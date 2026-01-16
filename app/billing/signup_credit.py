from __future__ import annotations

import stripe
from flask import current_app

from ..extensions import db
from ..models import BillingCreditGrant
from .stripe_client import ensure_stripe_customer


SIGNUP_CREDIT_CENTS = 21240  # $212.40


def _init_stripe():
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key


def grant_signup_credit_once(user) -> bool:
    """
    Grants a one-time signup credit to the user's Stripe customer balance.
    Returns True if credit was granted now, False if it was already granted.
    """
    # Ensure Stripe customer exists
    ensure_stripe_customer(user)

    # Already granted? (DB is your guardrail)
    existing = (
        db.session.query(BillingCreditGrant)
        .filter_by(user_id=user.id, reason="signup_credit")
        .one_or_none()
    )
    if existing:
        return False

    _init_stripe()

    # Create Stripe balance credit (negative amount = credit)
    txn = stripe.Customer.create_balance_transaction(
        user.stripe_customer_id,
        amount=-SIGNUP_CREDIT_CENTS,
        currency="usd",
        description="Signup credit",
        metadata={"user_id": user.id, "reason": "signup_credit"},
    )

    # Record audit row
    db.session.add(
        BillingCreditGrant(
            user_id=user.id,
            months=0,  # not months-based; it's a flat credit
            amount_cents=SIGNUP_CREDIT_CENTS,
            reason="signup_credit",
            stripe_balance_txn_id=txn.id,
        )
    )
    db.session.commit()

    return True
