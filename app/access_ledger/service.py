# app/access_ledger/service.py
from __future__ import annotations
from app.billing.local_cancel import force_cancel_subscription_like_user_clicked
from dataclasses import dataclass
from datetime import datetime, date
from typing import Iterable, Optional, Dict, Any, List
import uuid
from sqlalchemy import func
from dataclasses import dataclass
from typing import Optional
from app.access_ledger.service import (
    post_access_txn,
    get_or_create_system_account,
    get_an_asset,
    get_or_create_user_wallet,
    EntrySpec,
)

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AccessAccount, AccessTxn, AccessEntry, AccessBalance, AccessAsset, User

AN_SCALE = 1000  # 1 AN = 1000 ticks
DAILY_TAX_AN = 1  # 1 AN = 1000 ticks
DAILY_TAX_TICKS = DAILY_TAX_AN*AN_SCALE

# ---- Velocity tax (debug-tunable) ----

DAILY_SPEND_TAX_ENABLED = True

DAILY_SPEND_TAX_THRESHOLD_AN = 100        # AN per UTC day
DAILY_SPEND_TAX_RATE = 0.50               # 50% marginal tax above threshold

# DAILY_SPEND_TAX_THRESHOLD_AN = 1    # Debug value
# DAILY_SPEND_TAX_RATE = 1.0          # Debug value


DAILY_SPEND_TAX_THRESHOLD_TICKS = int(DAILY_SPEND_TAX_THRESHOLD_AN * AN_SCALE)




def _uuid() -> str:
    return uuid.uuid4().hex

def get_user_an_balance_ticks(user_id: str) -> int:
    wallet = get_or_create_user_wallet(user_id)
    an = get_an_asset()
    return get_balance_ticks(wallet.id, an.id)



def charge_daily_tax_for_user(user_id: str, day_utc: date) -> bool:
    """
    Charges 1 AN per UTC day from user's wallet to the treasury.
    Idempotent via key: daily_tax:<YYYY-MM-DD>:<user_id>

    Returns True if charged (or already charged), False if skipped due to insufficient funds.
    """
    wallet = get_or_create_user_wallet(user_id)
    treasury = get_or_create_system_account("treasury")

    an = get_an_asset()

    mult = get_user_level_multiplier(user_id)
    tax_ticks = int(DAILY_TAX_TICKS * mult)

    # Optional precheck to avoid raising InsufficientFunds
    bal = get_balance_ticks(wallet.id, an.id)
    if bal < tax_ticks:
        force_cancel_subscription_like_user_clicked(
            user_id,
            anchor=f"daily_tax_insufficient:{day_utc.isoformat()}:{user_id}",
        )
        return False

    key = f"daily_tax:{day_utc.isoformat()}:{user_id}"

    try:
        post_access_txn(
            event_type="daily_tax",
            idempotency_key=key,
            actor_user_id=user_id,
            context_type="system",
            context_id=None,
            entries=[
                EntrySpec(account_id=wallet.id, asset_id=an.id, delta=-tax_ticks, entry_type="tax"),
                EntrySpec(account_id=treasury.id, asset_id=an.id, delta=+tax_ticks, entry_type="tax"),
            ],
            memo_json={
                "day_utc": day_utc.isoformat(),
                "base_ticks": DAILY_TAX_TICKS,
                "mult": mult,
                "tax_ticks": tax_ticks,
            },
            forbid_user_overdraft=True,
        )
        return True
    except InsufficientFunds:
        return False





def charge_daily_tax_for_all_users(day_utc: date) -> dict:
    """
    Attempts to charge all users. Safe to run repeatedly; idempotency prevents duplicates.
    Returns counts.
    """
    charged = 0
    skipped_insufficient = 0

    user_ids = [u.id for u in User.query.all()]
    for uid in user_ids:
        ok = charge_daily_tax_for_user(uid, day_utc)
        if ok:
            charged += 1
        else:
            skipped_insufficient += 1

    return {"charged": charged, "skipped_insufficient": skipped_insufficient}


def get_or_create_asset(code: str, asset_type: str, curriculum_id: str | None = None, scale: int = 1) -> AccessAsset:
    a = AccessAsset.query.filter_by(code=code).one_or_none()
    if a:
        return a
    a = AccessAsset(code=code, asset_type=asset_type, curriculum_id=curriculum_id, scale=scale)
    db.session.add(a)
    db.session.flush()
    return a


def get_an_asset() -> AccessAsset:
    # 1 AN = 1000 ticks stored in DB
    return get_or_create_asset(
        code="AN",
        asset_type="access_note",
        curriculum_id=None,
        scale=AN_SCALE
    )



def get_curriculum_share_asset(curriculum_id: str) -> AccessAsset:
    return get_or_create_asset(code=f"CURR_SHARE:{curriculum_id}", asset_type="curriculum_share", curriculum_id=curriculum_id, scale=1) # Scale is 1 because it is a share (not AN)



def grant_signup_bonus_once(*, user_id: str, ticks: int) -> bool:
    """
    Give user +ticks of AN exactly once.
    Returns True if granted now, False if already granted before.
    """
    ticks = int(ticks)
    if ticks <= 0:
        return False

    idem = f"signup_bonus:{user_id}"

    # If already posted, do nothing (idempotent)
    existing = AccessTxn.query.filter_by(idempotency_key=idem).one_or_none()
    if existing:
        return False

    an = get_an_asset()
    wallet = get_or_create_user_wallet(user_id, currency_code="access_note")

    # Source of funds (can go negative unless you forbid it for system accts)
    treasury = get_or_create_system_account("treasury", currency_code="access_note")

    post_access_txn(
        event_type="signup_bonus",
        idempotency_key=idem,
        actor_user_id=user_id,
        context_type="user",
        context_id=user_id,
        entries=[
            EntrySpec(account_id=treasury.id, asset_id=an.id, delta=-ticks, entry_type="signup_bonus"),
            EntrySpec(account_id=wallet.id,   asset_id=an.id, delta=+ticks, entry_type="signup_bonus"),
        ],
        forbid_user_overdraft=True,  # only blocks *user* wallets from going negative; treasury can go negative
    )

    return True




@dataclass(frozen=True)
class EntrySpec:
    account_id: str
    asset_id: str
    delta: int
    entry_type: str = "principal"

class InsufficientFunds(Exception):
    pass

def ensure_balance_row(account_id: str, asset_id: str) -> AccessBalance:
    row = AccessBalance.query.filter_by(account_id=account_id, asset_id=asset_id).one_or_none()
    if row:
        return row
    row = AccessBalance(account_id=account_id, asset_id=asset_id, balance=0, updated_at=datetime.utcnow())
    db.session.add(row)
    db.session.flush()
    return row


def get_or_create_system_account(account_type: str, currency_code: str = "access_note") -> AccessAccount:
    acct = AccessAccount.query.filter_by(
        owner_user_id=None, account_type=account_type, currency_code=currency_code
    ).one_or_none()
    if acct:
        return acct

    acct = AccessAccount(owner_user_id=None, account_type=account_type, currency_code=currency_code)
    db.session.add(acct)
    db.session.flush()

    # ensure AN balance exists (default asset for "brokerage" accounts)
    an = get_an_asset()
    ensure_balance_row(acct.id, an.id)

    return acct


def get_or_create_user_wallet(user_id: str, currency_code: str = "access_note") -> AccessAccount:
    acct = AccessAccount.query.filter_by(
        owner_user_id=user_id, account_type="user_wallet", currency_code=currency_code
    ).one_or_none()
    if acct:
        return acct

    acct = AccessAccount(owner_user_id=user_id, account_type="user_wallet", currency_code=currency_code)
    db.session.add(acct)
    db.session.flush()

    an = get_an_asset()
    ensure_balance_row(acct.id, an.id)

    return acct

from sqlalchemy import tuple_


def post_access_txn(
    *,
    event_type: str,
    idempotency_key: str,
    entries: Iterable[EntrySpec],
    actor_user_id: Optional[str] = None,
    context_type: Optional[str] = None,
    context_id: Optional[str] = None,
    memo_json: Optional[Dict[str, Any]] = None,
    forbid_user_overdraft: bool = True,
) -> str:
    """
    Atomic, idempotent posting function.

    Rules (multi-asset):
    - For EACH asset_id: sum(deltas) must be 0
    - idempotency_key must be unique (retries return existing txn id)
    - updates AccessBalance rows keyed by (account_id, asset_id) in same DB transaction
    - optionally forbids negative balances for user_wallet accounts
    """
    entries_list: List[EntrySpec] = list(entries)
    if not entries_list:
        raise ValueError("entries must be non-empty")

    # Multi-asset invariant: per-asset sums must net to 0
    per_asset_totals: Dict[str, int] = {}
    for e in entries_list:
        per_asset_totals[e.asset_id] = per_asset_totals.get(e.asset_id, 0) + int(e.delta)
    bad = {aid: tot for aid, tot in per_asset_totals.items() if tot != 0}
    if bad:
        raise ValueError(f"Ledger invariant violated: per-asset sum(delta) must be 0. Bad={bad}")

    # Idempotency check
    existing = AccessTxn.query.filter_by(idempotency_key=idempotency_key).one_or_none()
    if existing:
        return existing.id

    txn = AccessTxn(
        id=_uuid(),
        created_at=datetime.utcnow(),
        event_type=event_type,
        actor_user_id=actor_user_id,
        context_type=context_type,
        context_id=context_id,
        idempotency_key=idempotency_key,
        memo_json=memo_json,
    )
    db.session.add(txn)
    db.session.flush()

    # Ensure all needed balance rows exist BEFORE locking
    pairs = {(e.account_id, e.asset_id) for e in entries_list}
    for account_id, asset_id in pairs:
        ensure_balance_row(account_id, asset_id)

    # Lock only the relevant balance rows
    account_ids = sorted({a for a, _ in pairs})
    asset_ids = sorted({s for _, s in pairs})

    balances = (
        AccessBalance.query
        .filter(AccessBalance.account_id.in_(account_ids))
        .filter(AccessBalance.asset_id.in_(asset_ids))
        .with_for_update()
        .all()
    )
    balance_map = {(b.account_id, b.asset_id): b for b in balances}

    # Optional overdraft prevention (user_wallet only)
    acct_rows = AccessAccount.query.filter(AccessAccount.id.in_(account_ids)).all()
    acct_type_by_id = {a.id: a.account_type for a in acct_rows}


    # -------------------------------
    # Velocity tax (daily spend cap)
    # -------------------------------
    if DAILY_SPEND_TAX_ENABLED:
        today = datetime.utcnow().date()
        treasury = get_or_create_system_account("treasury")
        an = get_an_asset()

        extra_tax_entries: List[EntrySpec] = []

        for e in entries_list:
            # Only tax outgoing AN from user wallets
            if e.delta >= 0:
                continue
            if e.asset_id != an.id:
                continue

            acct = acct_type_by_id.get(e.account_id)
            if acct != "user_wallet":
                continue

            spend_amount = -int(e.delta)

            spent_before = get_an_spent_today_ticks(e.account_id, today)
            spent_after = spent_before + spend_amount

            # Marginal portion above threshold
            taxable = max(
                0,
                min(spent_after - DAILY_SPEND_TAX_THRESHOLD_TICKS, spend_amount)
            )

            if taxable <= 0:
                continue

            tax_ticks = int(taxable * DAILY_SPEND_TAX_RATE)
            if tax_ticks <= 0:
                continue

            extra_tax_entries.append(EntrySpec(
                account_id=e.account_id,
                asset_id=an.id,
                delta=-tax_ticks,
                entry_type="velocity_tax",
            ))
            extra_tax_entries.append(EntrySpec(
                account_id=treasury.id,
                asset_id=an.id,
                delta=+tax_ticks,
                entry_type="velocity_tax",
            ))

        if extra_tax_entries:
            entries_list.extend(extra_tax_entries)



    # Apply entries
    for e in entries_list:
        b = balance_map.get((e.account_id, e.asset_id))
        if b is None:
            # should not happen because ensure_balance_row created them
            b = ensure_balance_row(e.account_id, e.asset_id)
            balance_map[(e.account_id, e.asset_id)] = b

        new_bal = int(b.balance) + int(e.delta)

        if forbid_user_overdraft and acct_type_by_id.get(e.account_id) == "user_wallet":
            if new_bal < 0:
                raise InsufficientFunds(f"Insufficient funds for account={e.account_id}, asset={e.asset_id}")

        b.balance = new_bal
        b.updated_at = datetime.utcnow()

        db.session.add(AccessEntry(
            txn_id=txn.id,
            account_id=e.account_id,
            asset_id=e.asset_id,
            delta=int(e.delta),
            entry_type=e.entry_type,
        ))

    db.session.commit()
    return txn.id


def get_balance_ticks(account_id: str, asset_id: str) -> int:
    """Return current balance for (account, asset). If missing row, treat as 0."""
    from app.models import AccessBalance
    row = AccessBalance.query.filter_by(account_id=account_id, asset_id=asset_id).one_or_none()
    return int(row.balance) if row else 0



def get_an_spent_today_ticks(account_id: str, day_utc: date) -> int:
    """
    Total AN ticks spent (outgoing) from this account on a given UTC day.
    Only counts negative deltas from user_wallet.
    """
    an = get_an_asset()

    day_start = datetime.combine(day_utc, datetime.min.time())
    day_end = datetime.combine(day_utc, datetime.max.time())

    total = (
        db.session.query(func.coalesce(func.sum(-AccessEntry.delta), 0))
        .join(AccessAccount, AccessAccount.id == AccessEntry.account_id)
        .filter(AccessEntry.account_id == account_id)
        .filter(AccessEntry.asset_id == an.id)
        .filter(AccessEntry.delta < 0)
        .filter(AccessAccount.account_type == "user_wallet")
        .filter(AccessEntry.created_at >= day_start)
        .filter(AccessEntry.created_at <= day_end)
        .scalar()
    )

    return int(total or 0)



def get_user_level_multiplier(user_id: str) -> float:
    u = User.query.get(user_id)
    if not u:
        return 1.0
    m = float(getattr(u, "access_level_mult", 1.0) or 1.0)

    # sanity clamps
    if m < 0:
        m = 0.0
    if m > 10:
        m = 10.0
    return m

