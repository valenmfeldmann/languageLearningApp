# app/billing/sync_subs.py
from __future__ import annotations

import stripe
from datetime import datetime, timezone

from ..extensions import db
from ..models import User, Subscription, Plan
from .stripe_client import _init_stripe


ACTIVEISH = {"active", "trialing", "past_due", "unpaid", "incomplete"}


def _to_dt(ts: int | None):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _choose_best_subscription(subs: list[dict]) -> dict | None:
    """
    Pick the most relevant subscription to represent in our 1-row-per-user table.
    Preference: active/trialing/etc, then newest by created.
    """
    if not subs:
        return None

    def rank(s: dict) -> tuple[int, int]:
        status = (s.get("status") or "").lower()
        status_rank = 1 if status in ACTIVEISH else 0
        created = int(s.get("created") or 0)
        return (status_rank, created)

    return sorted(subs, key=rank, reverse=True)[0]

## OLD
# def _infer_plan_code(sub: dict) -> str | None:
#     md = sub.get("metadata") or {}
#     if md.get("plan_code"):
#         return md["plan_code"]
#
#     # Fallback: infer by price id
#     items = (((sub.get("items") or {}).get("data")) or [])
#     if items:
#         price = (items[0].get("price") or {})
#         price_id = price.get("id")
#         if price_id:
#             plan = Plan.query.filter_by(stripe_price_id=price_id).first()
#             if plan:
#                 return plan.code
#     return None

def _infer_plan_code(sub: dict) -> str | None:
    md = sub.get("metadata") or {}
    if md.get("plan_code"):
        return md["plan_code"]

    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return None

    price_id = (items[0].get("price") or {}).get("id")
    if not price_id:
        return None

    plan = (
        Plan.query
        .filter_by(stripe_price_id=price_id, active=True)
        .first()
    )
    return plan.code if plan else None



def _ensure_subscription_row(user_id: str) -> Subscription:
    row = Subscription.query.filter_by(user_id=user_id).first()
    if row:
        return row
    # Your Subscription.id is a string PK; easiest is to use Stripe sub id once we have it.
    # For now, create a placeholder and fill later.
    row = Subscription(id=f"local-{user_id}", user_id=user_id, status="incomplete")
    db.session.add(row)
    return row


def stripe_sync_subscriptions_command():
    """Reconcile Subscription rows in DB with Stripe current subscription state."""
    _init_stripe()

    users = User.query.filter(User.stripe_customer_id.isnot(None)).all()
    updated = 0

    for user in users:
        cust_id = user.stripe_customer_id
        if not cust_id:
            continue

        # List all subs for this customer
        resp = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
        best = _choose_best_subscription(resp.data)

        if not best:
            # No subs in Stripe: mark DB row canceled if it exists
            row = Subscription.query.filter_by(user_id=user.id).first()
            if row and row.status != "canceled":
                row.status = "canceled"
                # row.cancel_at_period_end = False
                row.cancel_at = None # _to_dt(sub.get("cancel_at"))
                row.current_period_end = None
                row.trial_end = None
                updated += 1
            continue

        plan_code = _infer_plan_code(best)
        plan = Plan.query.filter_by(code=plan_code).first() if plan_code else None

        row = _ensure_subscription_row(user.id)

        stripe_sub_id = best.get("id")
        if stripe_sub_id and row.id != stripe_sub_id:
            # Make the row ID match Stripe subscription id (since it's your PK)
            # If you already have real rows keyed by Stripe id, you can simplify this later.
            row.id = stripe_sub_id

        row.stripe_subscription_id = stripe_sub_id
        row.status = (best.get("status") or "incomplete")
        # row.cancel_at_period_end = bool(best.get("cancel_at_period_end") or False)
        row.cancel_at = _to_dt(best.get("cancel_at"))
        row.current_period_end = _to_dt(best.get("current_period_end"))
        row.trial_end = _to_dt(best.get("trial_end"))
        row.plan_id = plan.id if plan else None

        updated += 1

        if best:
            sub_id = best.get("id")
            full = stripe.Subscription.retrieve(sub_id)

            print(
                "FULL",
                user.email,
                "id=", full.get("id"),
                "status=", full.get("status"),
                "cancel_at_period_end=", full.get("cancel_at_period_end"),
                "cancel_at=", full.get("cancel_at"),
                "canceled_at=", full.get("canceled_at"),
                "ended_at=", full.get("ended_at"),
                "current_period_end=", full.get("current_period_end"),
            )

    db.session.commit()
    print(f"Stripe subscription sync complete. Updated {updated} users.")
