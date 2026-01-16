# app/buddies/cli.py
import click
from app.extensions import db
from app.models import User, BuddyLink

@click.command("recalc-buddy-counts")
def recalc_buddy_counts():
    # reset to 0
    db.session.query(User).update({User.active_buddy_count: 0})

    # count accepted links per user (both requester and addressee)
    rows = (
        db.session.query(BuddyLink.requester_id, BuddyLink.addressee_id)
        .filter(BuddyLink.status == "accepted")
        .all()
    )

    counts = {}
    for r, a in rows:
        counts[r] = counts.get(r, 0) + 1
        counts[a] = counts.get(a, 0) + 1

    for user_id, c in counts.items():
        db.session.query(User).filter(User.id == user_id).update({User.active_buddy_count: c})

    db.session.commit()
    click.echo("OK: buddy counts recomputed")
