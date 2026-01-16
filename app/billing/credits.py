# app/billing/credits.py
from __future__ import annotations

from dataclasses import dataclass

from .stripe_client import ensure_stripe_customer
from ..models import Plan
from typing import Optional

from .stripe_client import _init_stripe
from ..models import User, BillingConfig
from datetime import datetime
import stripe
from flask import current_app
from app.extensions import db
from app.models import User, BillingCreditGrant



def _init_stripe():
    key = current_app.config.get("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set")
    stripe.api_key = key


@dataclass(frozen=True)
class CreditGrantResult:
    customer_id: str
    amount_cents: int
    currency: str
    description: str
    stripe_balance_txn_id: str


def grant_customer_credit(
    *,
    user,
    amount_cents: int,
    currency: str = "usd",
    description: str,
) -> CreditGrantResult:
    """
    Adds a CREDIT to the Stripe customer's balance.
    Stripe convention: negative amount = credit, positive = debit.
    We accept a positive amount_cents and convert to negative.
    """
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")

    ensure_stripe_customer(user)
    _init_stripe()

    txn = stripe.Customer.create_balance_transaction(
        user.stripe_customer_id,
        amount=-amount_cents,
        currency=currency,
        description=description,
    )

    return CreditGrantResult(
        customer_id=user.stripe_customer_id,
        amount_cents=amount_cents,
        currency=currency,
        description=description,
        stripe_balance_txn_id=txn["id"],
    )



def estimate_monthly_amount_cents(plan_code: str) -> int:
    plan = Plan.query.filter_by(code=plan_code, active=True).first()
    if not plan:
        raise ValueError(f"Unknown or inactive plan_code: {plan_code}")
    return int(plan.monthly_amount_cents)



def grant_free_months(
    *,
    user,
    plan_code: str,
    months: int,
    reason: str,
    currency: str = "usd",
):
    plan = Plan.query.filter_by(code=plan_code, active=True).first()
    if not plan:
        raise ValueError("Invalid plan")

    amount_cents = plan.monthly_amount_cents * months

    result = grant_customer_credit(
        user=user,
        amount_cents=amount_cents,
        currency=currency,
        description=f"{reason}: {months} month(s) credit for plan={plan_code}",
    )

    # 🔒 Persist audit record
    db.session.add(
        BillingCreditGrant(
            user_id=user.id,
            months=months,
            amount_cents=amount_cents,
            reason=reason,
            stripe_balance_txn_id=result.stripe_balance_txn_id,
        )
    )
    db.session.commit()

    return result

# def get_credit_balance_cents(user_id: str) -> Optional[int]:
#     # Minimal v1: store a cached field on User, e.g. user.stripe_credit_balance_cents
#     user = db.session.get(User, user_id)
#     return getattr(user, "stripe_credit_balance_cents", None)

def get_credit_balance_cents(user_id: str) -> int:
    user = db.session.get(User, user_id)
    if not user:
        return 0
    # Stripe: negative balance = credit
    return max(0, -(user.stripe_balance_cents or 0))


def grant_signup_credit_once(
    *,
    user,
    plan_code: str = "base",
    months: int = 24,
    reason: str = "signup_credit",
) -> Optional[CreditGrantResult]:
    """
    One-time signup credit. Safe to call on every login.
    Uses BillingCreditGrant as the idempotency/audit source of truth.
    """
    if months <= 0:
        return None

    already = (
        db.session.query(BillingCreditGrant)
        .filter_by(user_id=user.id, reason=reason)
        .first()
    )
    if already:
        return None  # already granted

    return grant_free_months(
        user=user,
        plan_code=plan_code,
        months=months,
        reason=reason,
    )





def _get_billing_config() -> BillingConfig:
    cfg = db.session.get(BillingConfig, 1)
    if not cfg:
        cfg = BillingConfig(id=1)
        db.session.add(cfg)
        db.session.commit()
    return cfg



def clawback_unsubscribe_credits(user_id: str, fraction: float | None = None, *, anchor: str | None = None) -> int:
    """
    Claw back a fraction of remaining *promo* Stripe credits for a user.

    Returns clawed_back_cents (positive integer).

    Stripe customer balance convention:
    - Negative balance => customer has credit
    - To REMOVE credit, create a POSITIVE balance transaction.
    """

    _init_stripe()

    user = db.session.get(User, user_id)
    if not user or not user.stripe_customer_id:
        return 0

    cfg = _get_billing_config()
    rate = float(fraction if fraction is not None else cfg.unsubscribe_credit_clawback_rate)

    if rate <= 0:
        return 0
    rate = min(rate, 1.0)

    # Remaining promo credits eligible for clawback
    grants = (
        BillingCreditGrant.query
        .filter_by(user_id=user_id)
        .filter(BillingCreditGrant.is_promo.is_(True))
        .filter(BillingCreditGrant.clawback_eligible.is_(True))
        .all()
    )

    remaining = 0
    for g in grants:
        remaining += max(0, int(g.amount_cents) - int(g.clawed_back_cents or 0))

    if remaining <= 0:
        return 0

    clawback_cents = int(round(remaining * rate))
    if clawback_cents <= 0:
        return 0

    # ---- Idempotency ----
    # anchor should be something stable per “cancel event”, e.g. cancel_at timestamp as a string.
    # If you don’t have one, use a deterministic key based on (user_id, clawback_cents, rate, remaining).
    # Better: pass anchor= str(cancel_at) from the webhook.
    if anchor is None:
        anchor = f"remaining={remaining}"

    idempotency_key = f"unsubscribe_clawback:{user.id}:{anchor}:{rate:.4f}:{clawback_cents}"

    # REMOVE credits -> POSITIVE amount
    txn = stripe.Customer.create_balance_transaction(
        user.stripe_customer_id,
        amount=clawback_cents,
        currency="usd",
        description=f"Unsubscribe clawback ({rate:.2f} of promo credits)",
        metadata={
            "user_id": user.id,
            "kind": "unsubscribe_clawback",
            "rate": str(rate),
            "anchor": anchor,
        },
        idempotency_key=idempotency_key,
    )

    txn_id = txn["id"]

    # Allocate clawback across grants (oldest-first)
    remaining_to_allocate = clawback_cents
    for g in sorted(grants, key=lambda x: x.created_at):
        if remaining_to_allocate <= 0:
            break
        g_remaining = max(0, int(g.amount_cents) - int(g.clawed_back_cents or 0))
        if g_remaining <= 0:
            continue

        take = min(g_remaining, remaining_to_allocate)
        g.clawed_back_cents = int(g.clawed_back_cents or 0) + take
        g.clawed_back_at = datetime.utcnow()

        # if you store txn id on grants, use dict access
        if not g.clawback_stripe_balance_txn_id:
            g.clawback_stripe_balance_txn_id = txn_id

        remaining_to_allocate -= take

    # Refresh cached balance from Stripe so DB matches Stripe
    cust = stripe.Customer.retrieve(user.stripe_customer_id)
    user.stripe_balance_cents = int(cust.get("balance") or 0)

    db.session.commit()
    return clawback_cents




def apply_unsubscribe_clawback_if_needed(user_id: str, *, stripe_sub: dict | None = None, event_id: str | None = None) -> int:
    u = db.session.get(User, user_id)
    if not u or not u.stripe_customer_id:
        return 0

    # If we've already applied a clawback that hasn't been reversed, do nothing.
    if int(getattr(u, "unsubscribe_clawback_pending_cents", 0) or 0) > 0:
        return 0

    cancel_at = None
    if stripe_sub:
        cancel_at = stripe_sub.get("cancel_at") or (stripe_sub.get("canceled_at"))
    cancel_at = int(cancel_at or 0)

    # ✅ Idempotency: if we already processed this exact cancel_at, do nothing
    if cancel_at and getattr(u, "unsubscribe_clawback_last_cancel_at", None) == cancel_at:
        return 0

    # Always pull fresh customer balance from Stripe (source of truth)
    _init_stripe()
    cust = stripe.Customer.retrieve(u.stripe_customer_id)
    bal = int(cust.get("balance") or 0)   # Stripe: negative means credit

    # If no credit, nothing to claw back
    if bal >= 0:
        u.unsubscribe_clawback_last_cancel_at = cancel_at or None
        if event_id:
            u.unsubscribe_clawback_last_event_id = event_id
        return 0

    cfg = BillingConfig.query.get(1)
    fraction = float(getattr(cfg, "unsubscribe_credit_clawback_rate", 0.5) or 0.5)

    credit_available = -bal
    clawback = int(round(credit_available * fraction))
    if clawback <= 0:
        u.unsubscribe_clawback_last_cancel_at = cancel_at or None
        if event_id:
            u.unsubscribe_clawback_last_event_id = event_id
        return 0

    # ✅ Stripe-side idempotency key (prevents duplicates even if webhook retries)
    # idempotency_key = f"unsubscribe_clawback:{u.id}:{cancel_at or 'nocancelat'}"
    # idempotency_key = f"unsubscribe_clawback:{user_id}:{cancel_at}:{clawback}"
    cust_id = u.stripe_customer_id
    idempotency_key = f"unsubscribe_clawback:{cust_id}:{user_id}:{cancel_at or 'capend'}:{clawback}"

    txn = stripe.Customer.create_balance_transaction(
        u.stripe_customer_id,
        amount=clawback,  # positive reduces credit
        currency="usd",
        description=f"Unsubscribe clawback ({fraction:.2f} of credit)",
        metadata={"user_id": u.id, "reason": "unsubscribe_clawback", "cancel_at": str(cancel_at)},
        idempotency_key=idempotency_key,
    )

    # Refresh again and store exactly what Stripe says
    cust2 = stripe.Customer.retrieve(u.stripe_customer_id)
    u.stripe_balance_cents = int(cust2.get("balance") or 0)

    # Record that this cancel_at has been processed
    u.unsubscribe_clawback_last_cancel_at = cancel_at or None
    if event_id:
        u.unsubscribe_clawback_last_event_id = event_id

    # Optionally keep txn id for audit
    u.unsubscribe_clawback_pending_txn_id = txn["id"]
    u.unsubscribe_clawback_pending_cents = clawback  # rename later if you want

    return clawback




# app/billing/credits.py
def reverse_unsubscribe_clawback_if_needed(user_id: str, anchor: str | None = None) -> int:
    """
    If a cancel-scheduled clawback was applied and user resumes, restore exactly that amount.
    Returns restored cents.
    """
    _init_stripe()

    user = db.session.get(User, user_id)
    if not user or not user.stripe_customer_id:
        return 0

    pending = int(getattr(user, "unsubscribe_clawback_pending_cents", 0) or 0)
    if pending <= 0:
        return 0

    # Make idempotency stable for this specific "resume" action
    # If you stored the cancel anchor when you applied clawback, use that same anchor here.
    # Fallback: just use "unknown".
    anchor = anchor or str(getattr(user, "unsubscribe_clawback_anchor", None) or "unknown")

    txn = stripe.Customer.create_balance_transaction(
        user.stripe_customer_id,
        amount=-pending,  # negative adds credit back
        currency="usd",
        description="Unsubscribe clawback reversal (subscription resumed)",
        metadata={"user_id": user_id, "kind": "unsubscribe_clawback_reversal", "anchor": anchor},
        idempotency_key=f"unsubscribe_clawback_reversal:{user_id}:{anchor}:{pending}",
    )

    # Clear pending (and store reversal txn id if you want)
    user.unsubscribe_clawback_pending_cents = 0
    user.unsubscribe_clawback_pending_txn_id = txn["id"]  # or a separate field like *_reversal_txn_id

    # refresh cached balance from Stripe so DB matches Stripe truth
    try:
        cust = stripe.Customer.retrieve(user.stripe_customer_id)
        user.stripe_balance_cents = int(cust.get("balance") or 0)
    except Exception:
        pass

    db.session.commit()
    return pending


