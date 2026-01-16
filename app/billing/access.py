# app/billing/access.py
"""
Centralized, DB-only access control.

- Answers: "Should this user have access right now?"
- Robust to missing rows / NULLs / partially populated fields.
- MUST NOT call Stripe. Stripe state only enters via webhooks/sync into DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

# from .subscriptions import get_subscription_for_user
from ..extensions import db
from ..models import Subscription, Plan

from .pricing import effective_monthly_cost_cents
from .buddies import count_active_buddies
from .credits import get_credit_balance_cents  # DB-cached Stripe balance


ACTIVE_STATUSES = {"active"}
TRIAL_STATUSES = {"trialing"}
SOFT_STATUSES = {"past_due", "unpaid", "incomplete", "incomplete_expired"}



def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _canceled_due_to_daily_tax(user) -> bool:
    # We set event_id like: "daily_tax_insufficient:YYYY-MM-DD:<user_id>"
    ev = getattr(user, "unsubscribe_clawback_last_event_id", None) or ""
    return isinstance(ev, str) and ev.startswith("daily_tax_insufficient:")


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class AccessResult:
    allowed: bool
    reason: str

    subscription_status: Optional[str] = None
    current_period_end: Optional[datetime] = None
    trial_end: Optional[datetime] = None

    comped_until: Optional[datetime] = None

    # For debugging/UI
    plan_code: Optional[str] = None
    base_monthly_cents: Optional[int] = None
    effective_monthly_cents: Optional[int] = None
    buddies: Optional[int] = None
    credit_balance_cents: Optional[int] = None


def get_subscription_for_user(user_id: str) -> Optional[Subscription]:
    return db.session.query(Subscription).filter_by(user_id=user_id).one_or_none()


def _subscription_allows_access(sub: Subscription, at: datetime) -> Tuple[bool, str]:
    status = (sub.status or "").lower()

    if status in ACTIVE_STATUSES:
        return True, "subscription_active"

    if status in TRIAL_STATUSES:
        trial_end = _as_utc(getattr(sub, "trial_end", None))
        if trial_end is None:
            # Stripe sometimes omits trial_end depending on creation path
            return True, "subscription_trialing_no_trial_end"
        if trial_end > at:
            return True, "subscription_trialing"
        return False, "trial_expired"

    if status in SOFT_STATUSES:
        return False, f"subscription_{status}"

    if status:
        return False, f"subscription_{status}"
    return False, "subscription_status_missing"


def _comped_allows_access(user, at: datetime) -> Tuple[bool, Optional[datetime]]:
    comped_until = _as_utc(getattr(user, "comped_until", None))
    if comped_until and comped_until > at:
        return True, comped_until
    return False, comped_until


def _credits_allow_access(
    *,
    user_id: str,
    plan: Optional[Plan],
    at: datetime,
) -> Tuple[bool, str, dict]:
    """
    DB-only credit-based access.

    We interpret "credits" as: user has a Stripe-cached balance that can cover
    at least one *effective* monthly charge. Buddy discount reduces burn rate,
    extending the free period.
    """
    if plan is None:
        return False, "no_plan", {}

    credit_cents = get_credit_balance_cents(user_id)  # you cache this from webhooks
    buddies = count_active_buddies(user_id)

    eff_monthly = effective_monthly_cost_cents(plan=plan, num_buddies=buddies)

    debug = {
        "credit_balance_cents": credit_cents,
        "buddies": buddies,
        "base_monthly_cents": plan.monthly_amount_cents,
        "effective_monthly_cents": eff_monthly,
        "plan_code": plan.code,
    }

    # Minimal, conservative rule:
    # if they have enough credit to cover at least one effective month, allow access.
    # (You can replace with "comped_until" style expiry later.)
    if credit_cents is not None and credit_cents >= eff_monthly and eff_monthly > 0:
        return True, "credit_balance_covers_month", debug

    return False, "insufficient_credit_balance", debug


def has_access(user, at: Optional[datetime] = None) -> bool:
    return access_status(user, at=at).allowed


def access_status(user, at: Optional[datetime] = None) -> AccessResult:
    at = _as_utc(at) or now_utc()

    user_id = getattr(user, "id", None)
    if not user_id:
        return AccessResult(False, "no_user_id")

    # 1) Admin comp override
    comp_ok, comped_until = _comped_allows_access(user, at)
    if comp_ok:
        return AccessResult(True, "comped", comped_until=comped_until)

    # 2) Subscription row
    sub = get_subscription_for_user(user_id)
    if not sub:
        return AccessResult(False, "no_subscription_row")

    # 3) Subscription allows access?
    # sub = _get_subscription(user)
    sub = get_subscription_for_user(user.id)

    if sub:
        allowed, reason = _subscription_allows_access(sub, at)
        if allowed:
            return AccessResult(
                True,
                reason,
                subscription_status=sub.status,
                trial_end=_as_utc(getattr(sub, "trial_end", None)),
                comped_until=comped_until,
            )

    # 4) Subscription does NOT grant access: deny
    if sub:
        reason = f"subscription_{(sub.status or '').lower()}"
        if (sub.status or "").lower() == "canceled" and _canceled_due_to_daily_tax(user):
            reason = "canceled_daily_tax_insufficient"
    else:
        reason = "subscription_missing"

    return AccessResult(
        False,
        reason,
        subscription_status=sub.status if sub else None,
        trial_end=_as_utc(getattr(sub, "trial_end", None)) if sub else None,
        comped_until=comped_until,
    )
