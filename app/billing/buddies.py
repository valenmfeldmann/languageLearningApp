# app/billing/buddies.py
from ..extensions import db
from ..models import BuddyLink

def count_active_buddies(user_id: str) -> int:
    return (
        db.session.query(BuddyLink)
        .filter(BuddyLink.status == "accepted")
        .filter(
            (BuddyLink.requester_id == user_id) |
            (BuddyLink.addressee_id == user_id)
        )
        .count()
    )


def get_cached_active_buddy_count(user) -> int:
    return int(getattr(user, "active_buddy_count", 0) or 0)
