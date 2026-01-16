# app/billing/sync.py
from __future__ import annotations

import stripe
from ..models import User
from ..extensions import db
from .stripe_client import _init_stripe


# app/billing/sync.py or similar
def clawback_user_command(user_id: str, fraction: float | None = None):
    from .credits import clawback_unsubscribe_credits
    cents = clawback_unsubscribe_credits(user_id, fraction=fraction)
    print(f"Clawed back {cents} cents from user {user_id}.")


def stripe_sync_command():
    """Reconcile DB-cached Stripe customer balances with Stripe."""
    _init_stripe()

    users = User.query.filter(User.stripe_customer_id.isnot(None)).all()
    updated = 0
    skipped = 0

    for user in users:
        if not user.stripe_customer_id:
            skipped += 1
            continue

        cust = stripe.Customer.retrieve(user.stripe_customer_id)
        stripe_balance = int(cust.get("balance") or 0)

        # Ensure we never store NULL
        if (user.stripe_balance_cents or 0) != stripe_balance:
            user.stripe_balance_cents = stripe_balance
            updated += 1

    db.session.commit()
    print(f"Stripe balance sync complete. Updated {updated} users. Skipped {skipped}.")
