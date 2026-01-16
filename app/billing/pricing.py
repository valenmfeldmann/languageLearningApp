# app/billing/pricing.py

from typing import Optional
from ..models import Plan


def buddy_discount_multiplier(num_buddies: int) -> float:
    """
    Returns a multiplier applied to the base monthly cost.

    1.0  = no discount
    0.9  = 10% discount
    0.5  = maximum discount (cap)
    """
    if num_buddies <= 0:
        return 1.0

    # Example policy: 10% per buddy, capped at 50%
    return max(0.5, 1.0 - 0.1 * num_buddies)


def effective_monthly_cost_cents(
    *,
    plan: Plan,
    num_buddies: int,
) -> int:
    """
    Computes the user's effective monthly cost after discounts.
    """
    base = plan.monthly_amount_cents
    multiplier = buddy_discount_multiplier(num_buddies)
    return int(base * multiplier)
