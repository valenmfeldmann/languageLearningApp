# app/billing/debug.py
from flask import current_app
from ..models import User, BillingCreditGrant, Subscription
from ..extensions import db
from .stripe_client import _init_stripe
import stripe

def print_user_billing(user_id: str):
    user = db.session.get(User, user_id)
    if not user:
        print("No such user")
        return

    print("USER", user.email, user.id)
    print("stripe_customer_id:", user.stripe_customer_id)
    print("cached stripe_balance_cents:", user.stripe_balance_cents)
    print("pending clawback:", user.unsubscribe_clawback_pending_cents, user.unsubscribe_clawback_pending_txn_id)

    sub = Subscription.query.filter_by(user_id=user_id).first()
    if sub:
        print("DB sub:", sub.status, sub.cancel_at_period_end, sub.current_period_end)
    else:
        print("DB sub: none")

    grants = BillingCreditGrant.query.filter_by(user_id=user_id).all()
    print("Grants:", len(grants))
    for g in grants:
        print(
            f"  grant#{g.id} amount={g.amount_cents} promo={g.is_promo} eligible={g.clawback_eligible} "
            f"clawed_back={g.clawed_back_cents} reason={g.reason}"
        )

    if user.stripe_customer_id:
        _init_stripe()
        cust = stripe.Customer.retrieve(user.stripe_customer_id)
        print("Stripe customer balance:", cust.get("balance"))
