# app/billing/stripe_client.py
import stripe
from flask import current_app
from ..extensions import db


def _init_stripe():
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key


# def ensure_stripe_customer(user) -> None:
#     """
#     Idempotent: if the user already has a stripe_customer_id, do nothing.
#     Creates a Stripe Customer and stores the id on the user row.
#     """
#     if getattr(user, "stripe_customer_id", None):
#         return
#
#     _init_stripe()
#
#     customer = stripe.Customer.create(
#         email=user.email,
#         name=user.name,
#         metadata={"user_id": user.id},
#     )
#
#     user.stripe_customer_id = customer["id"]
#     db.session.commit()

def ensure_stripe_customer(user):
    _init_stripe()

    if user.stripe_customer_id:
        try:
            stripe.Customer.retrieve(user.stripe_customer_id)
            return
        except stripe.error.InvalidRequestError as e:
            if "No such customer" in str(e):
                user.stripe_customer_id = None
                db.session.commit()
            else:
                raise

    cust = stripe.Customer.create(email=user.email, name=user.name or None)
    user.stripe_customer_id = cust.id
    db.session.commit()

