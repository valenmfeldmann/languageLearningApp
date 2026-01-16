from __future__ import annotations

from flask import render_template
from flask_login import login_required, current_user

from app.extensions import db
from app.models import (
    User,
    Subscription,
    Plan,
    BuddyLink,
    BillingCreditGrant,
    AccessAccount,
    AccessBalance,
    AccessTxn,
    AccessEntry,
)
from app.billing.buddies import count_active_buddies
from app.billing.pricing import effective_monthly_cost_cents
from app.billing.credits import get_credit_balance_cents

from . import bp


@bp.get("")
@login_required
def debug_home():
    u: User = db.session.get(User, current_user.id)

    # ---- Subscription / Plan ----
    sub = Subscription.query.filter_by(user_id=u.id).one_or_none()
    plan = db.session.get(Plan, sub.plan_id) if (sub and sub.plan_id) else None

    # ---- Buddies ----
    cached_buddies = int(getattr(u, "active_buddy_count", 0) or 0)
    computed_buddies = int(count_active_buddies(u.id))

    links = (
        BuddyLink.query
        .filter((BuddyLink.requester_id == u.id) | (BuddyLink.addressee_id == u.id))
        .order_by(BuddyLink.created_at.desc())
        .limit(50)
        .all()
    )

    # ---- Credits (Stripe-cached) ----
    credit_balance_cents = int(get_credit_balance_cents(u.id) or 0)

    # ---- Effective monthly (buddy discount) ----
    effective_cents = None
    base_cents = None
    if plan:
        base_cents = int(plan.monthly_amount_cents)
        effective_cents = int(effective_monthly_cost_cents(plan=plan, num_buddies=cached_buddies))

    # ---- Billing credit grants history ----
    grants = (
        BillingCreditGrant.query
        .filter_by(user_id=u.id)
        .order_by(BillingCreditGrant.created_at.desc())
        .limit(30)
        .all()
    )

    # ---- Access Notes ledger ----
    # wallet account (if it exists)
    wallet = (
        AccessAccount.query
        .filter_by(owner_user_id=u.id, account_type="user_wallet", currency_code="access_note")
        .one_or_none()
    )
    wallet_balance = db.session.get(AccessBalance, wallet.id).balance if wallet else None

    # recent txns involving wallet
    wallet_txns = []
    wallet_entries = []
    if wallet:
        # entries for wallet, newest first
        wallet_entries = (
            AccessEntry.query
            .filter_by(account_id=wallet.id)
            .order_by(AccessEntry.created_at.desc())
            .limit(50)
            .all()
        )
        txn_ids = [e.txn_id for e in wallet_entries]
        if txn_ids:
            wallet_txns = (
                AccessTxn.query
                .filter(AccessTxn.id.in_(txn_ids))
                .order_by(AccessTxn.created_at.desc())
                .limit(25)
                .all()
            )

    return render_template(
        "debug/home.html",
        user=u,
        sub=sub,
        plan=plan,
        cached_buddies=cached_buddies,
        computed_buddies=computed_buddies,
        buddy_links=links,
        credit_balance_cents=credit_balance_cents,
        base_cents=base_cents,
        effective_cents=effective_cents,
        grants=grants,
        wallet=wallet,
        wallet_balance=wallet_balance,
        wallet_txns=wallet_txns,
        wallet_entries=wallet_entries,
    )
