# app/appui/ledger_public.py
from __future__ import annotations

import hashlib
from typing import Dict, List, Optional

from app.extensions import db
from app.models import (
    AccessTxn,
    AccessEntry,
    AccessAccount,
    AccessAsset,
    User,
)
from app.access_ledger.service import AN_SCALE


# ---------- masking helpers ----------

_SALT = "public-ledger-v1"


def _mask_id(raw_id: str) -> str:
    h = hashlib.sha256(f"{_SALT}:{raw_id}".encode()).hexdigest()
    return f"acct_{h[:6]}"


def _display_account(acct: AccessAccount, viewer_user_id: Optional[str]) -> str:
    if acct.owner_user_id is None:
        # system accounts are named
        return acct.account_type
    if viewer_user_id and acct.owner_user_id == viewer_user_id:
        return "your_wallet"
    return _mask_id(acct.id)


# ---------- query ----------

def query_public_ledger(
    *,
    viewer_user_id: Optional[str],
    limit: int = 100,
    only_my_txns: bool = False,
    event_type: Optional[str] = None,
    context_type: Optional[str] = None,
    asset_code: Optional[str] = None,
) -> List[Dict]:
    """
    Returns recent ledger transactions with masked accounts.
    Supports basic filters for a dropdown-driven UI.
    """
    q = AccessTxn.query.order_by(AccessTxn.created_at.desc())

    if only_my_txns and viewer_user_id:
        q = q.filter(AccessTxn.actor_user_id == viewer_user_id)

    if event_type:
        q = q.filter(AccessTxn.event_type == event_type)

    if context_type:
        q = q.filter(AccessTxn.context_type == context_type)

    txns = q.limit(limit).all()
    if not txns:
        return []

    txn_ids = [t.id for t in txns]

    entries_q = AccessEntry.query.filter(AccessEntry.txn_id.in_(txn_ids))

    # Optional: filter by asset code (e.g. "AN", "CURR_SHARE:...")
    if asset_code:
        asset = AccessAsset.query.filter_by(code=asset_code).one_or_none()
        if not asset:
            return []
        entries_q = entries_q.filter(AccessEntry.asset_id == asset.id)

    entries = entries_q.all()

    # If asset_code filter is applied, we may now have txns with zero entries; prune them.
    kept_txn_ids = {e.txn_id for e in entries}
    txns = [t for t in txns if t.id in kept_txn_ids]
    txn_ids = [t.id for t in txns]

    accounts = {
        a.id: a
        for a in AccessAccount.query
        .filter(AccessAccount.id.in_({e.account_id for e in entries}))
        .all()
    }

    assets = {
        a.id: a
        for a in AccessAsset.query
        .filter(AccessAsset.id.in_({e.asset_id for e in entries}))
        .all()
    }

    entries_by_txn: Dict[str, List[Dict]] = {}
    for e in entries:
        acct = accounts[e.account_id]
        asset = assets[e.asset_id]

        entries_by_txn.setdefault(e.txn_id, []).append({
            "account": _display_account(acct, viewer_user_id),
            "asset": asset.code,
            "delta_ticks": int(e.delta),
            "delta_display": (f"{e.delta / AN_SCALE:,.3f}" if asset.code == "AN" else str(int(e.delta))),
            "entry_type": e.entry_type,
        })

    rows = []
    for t in txns:
        rows.append({
            "txn_id": t.id,
            "created_at": t.created_at,
            "event_type": t.event_type,
            "context_type": t.context_type,
            "context_id": t.context_id,
            "actor": ("you" if viewer_user_id and t.actor_user_id == viewer_user_id else "masked"),
            "entries": sorted(
                entries_by_txn.get(t.id, []),
                key=lambda x: (x["asset"], x["account"])
            ),
        })

    return rows


def ledger_filter_options() -> Dict[str, List[str]]:
    # Keep this cheap. If it grows, cache it.
    event_types = [r[0] for r in db.session.query(AccessTxn.event_type).distinct().order_by(AccessTxn.event_type).all()]
    context_types = [r[0] for r in db.session.query(AccessTxn.context_type).distinct().order_by(AccessTxn.context_type).all() if r[0]]
    assets = [r[0] for r in db.session.query(AccessAsset.code).distinct().order_by(AccessAsset.code).all()]
    return {
        "event_types": event_types,
        "context_types": context_types,
        "asset_codes": assets,
    }


def get_single_txn(txn_id: str, viewer_user_id: Optional[str]) -> Optional[Dict]:
    t = AccessTxn.query.get(txn_id)
    if not t:
        return None

    entries = AccessEntry.query.filter_by(txn_id=txn_id).all()
    accounts = {
        a.id: a
        for a in AccessAccount.query
        .filter(AccessAccount.id.in_({e.account_id for e in entries}))
        .all()
    }
    assets = {
        a.id: a
        for a in AccessAsset.query
        .filter(AccessAsset.id.in_({e.asset_id for e in entries}))
        .all()
    }

    return {
        "txn_id": t.id,
        "created_at": t.created_at,
        "event_type": t.event_type,
        "actor": ("you" if viewer_user_id and t.actor_user_id == viewer_user_id else "masked"),
        "entries": [
            {
                "account": _display_account(accounts[e.account_id], viewer_user_id),
                "asset": assets[e.asset_id].code,
                "delta": e.delta,
                "entry_type": e.entry_type,
            }
            for e in entries
        ],
    }
