# app/billing/local_cancel.py
from __future__ import annotations

from datetime import datetime
from app.extensions import db
from app.models import Subscription
from app.billing.credits import apply_unsubscribe_clawback_if_needed
from datetime import datetime
from app.models import Subscription
from app.extensions import db


def _reason_from_anchor(anchor: str) -> str:
    if not anchor:
        return "unknown"
    if anchor.startswith("daily_tax_insufficient"):
        return "daily_tax_insufficient"
    if anchor.startswith("user_cancel"):
        return "user_cancel"
    if anchor.startswith("stripe_cancel"):
        return "stripe_cancel"
    return "unknown"



def force_cancel_subscription_like_user_clicked(user_id: str, *, anchor: str) -> None:
    """
    Make the app behave as if the user canceled their subscription.

    - Set DB subscription status to 'canceled'
    - Apply unsubscribe clawback if eligible (idempotent inside credits.py)
    """
    sub = Subscription.query.filter_by(user_id=user_id).one_or_none()

    if not sub:
        # mirror your auth/routes.py behavior: create a local row if missing
        sub = Subscription(
            id=f"local-{user_id}",
            user_id=user_id,
            stripe_subscription_id=None,
            status="canceled",
            trial_end=None,
            cancel_at_period_end=False,
            cancel_at=datetime.utcnow(),
        )
        db.session.add(sub)
    else:
        sub.status = "canceled"
        sub.cancel_at_period_end = False
        sub.cancel_at = datetime.utcnow()
        sub.trial_end = None

    # record why access was revoked (SAFE: sub exists now)
    sub.access_revoked_reason = _reason_from_anchor(anchor)
    sub.access_revoked_anchor = anchor
    sub.access_revoked_at = datetime.utcnow()

    db.session.commit()

    # This function is already idempotent and tracks processed cancel events.
    # We don't have Stripe cancel_at, so we pass event_id as a stable anchor.
    apply_unsubscribe_clawback_if_needed(user_id, event_id=anchor)
