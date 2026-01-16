# app/billing/discounts.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

import stripe
from flask import current_app

from app.extensions import db
from app.models import User, Plan, Subscription, BillingCreditGrant
from app.billing.pricing import effective_monthly_cost_cents
from app.billing.buddies import count_active_buddies


def _init_stripe():
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key


def apply_buddy_cashback_for_invoice(invoice: dict) -> int:
    """
    Returns cashback cents applied (0 if none).
    Idempotent per invoice.id.
    """

    invoice_id = invoice.get("id")
    customer_id = invoice.get("customer")
    if not invoice_id or not customer_id:
        return 0

    status = (invoice.get("status") or "").lower()
    if status in ("paid", "void"):
        return 0

    if not invoice.get("subscription"):
        return 0

    user = User.query.filter_by(stripe_customer_id=customer_id).one_or_none()
    if not user:
        return 0

    # Only apply once per invoice
    reason = f"buddy_cashback:{invoice_id}"
    already = (
        db.session.query(BillingCreditGrant)
        .filter_by(user_id=user.id, reason=reason)
        .first()
    )
    if already:
        return 0

    # Need plan to know base price
    stripe_sub_id = invoice.get("subscription")  # string id
    sub = Subscription.query.filter_by(stripe_subscription_id=stripe_sub_id).one_or_none()
    if not sub or not sub.plan_id:
        return 0

    plan = db.session.get(Plan, sub.plan_id)
    if not plan:
        return 0

    buddies = count_active_buddies(user.id)  # truth, not cached
    base = int(plan.monthly_amount_cents)
    effective = int(effective_monthly_cost_cents(plan=plan, num_buddies=buddies))


    cashback = max(0, base - effective)

    # Clamp to invoice amount due/subtotal to avoid over-crediting on proration/taxes.
    subtotal_excl_tax = int(invoice.get("subtotal_excluding_tax") or 0)
    subtotal = int(invoice.get("subtotal") or 0)
    cap = subtotal_excl_tax or subtotal
    if cap > 0:
        cashback = min(cashback, cap)

    if cashback <= 0:
        return 0

    _init_stripe()

    txn = stripe.Customer.create_balance_transaction(
        user.stripe_customer_id,
        amount=-cashback,  # negative = grant credit
        currency="usd",
        description=f"Buddy discount cashback for invoice {invoice_id} ({buddies} buddies)",
        metadata={
            "user_id": user.id,
            "kind": "buddy_cashback",
            "invoice_id": invoice_id,
            "buddies": str(buddies),
            "base_cents": str(base),
            "effective_cents": str(effective),
        },
    )

    db.session.add(
        BillingCreditGrant(
            user_id=user.id,
            months=0,                 # not really "months"
            amount_cents=cashback,    # audited amount
            reason=reason,            # idempotency key lives here
            stripe_balance_txn_id=txn["id"],
            is_promo=False,           # important: this is “earned”, not promo
            clawback_eligible=False,  # do not claw this back on unsubscribe
            created_at=datetime.utcnow(),
        )
    )

    return cashback
