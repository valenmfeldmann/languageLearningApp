# app/buddies/routes.py
from __future__ import annotations

from datetime import datetime
from flask import Blueprint, abort, jsonify, request
from flask_login import login_required, current_user
from ..models import User, BuddyLink

from ..extensions import db
from ..models import User, BuddyLink

bp = Blueprint("buddies", __name__, url_prefix="/buddies")


def _utcnow():
    return datetime.utcnow()


def _get_link_or_404(link_id: int) -> BuddyLink:
    link = db.session.get(BuddyLink, link_id)
    if not link:
        abort(404, description="BuddyLink not found")
    return link


def _is_party(link: BuddyLink, user_id: str) -> bool:
    return link.requester_id == user_id or link.addressee_id == user_id


@bp.post("/request/<other_user_id>")
@login_required
def request_buddy(other_user_id: str):
    me = current_user.id
    other = other_user_id

    if not other or other == me:
        abort(400, description="Invalid buddy target")

    # Ensure other user exists
    if not db.session.get(User, other):
        abort(404, description="User not found")

    # Prevent duplicates in either direction
    existing = (
        db.session.query(BuddyLink)
        .filter(
            ((BuddyLink.requester_id == me) & (BuddyLink.addressee_id == other)) |
            ((BuddyLink.requester_id == other) & (BuddyLink.addressee_id == me))
        )
        .filter(BuddyLink.status.in_(("pending", "accepted")))
        .first()
    )
    if existing:
        return jsonify({
            "ok": True,
            "message": "Link already exists",
            "link_id": existing.id,
            "status": existing.status,
        }), 200

    link = BuddyLink(
        requester_id=me,
        addressee_id=other,
        status="pending",
        created_at=_utcnow(),
    )
    db.session.add(link)
    db.session.commit()

    return jsonify({
        "ok": True,
        "link_id": link.id,
        "status": link.status,
    }), 201


@bp.post("/accept/<int:link_id>")
@login_required
def accept_buddy(link_id: int):
    me = current_user.id

    # Lock the link row to prevent double-accept races
    link = (
        db.session.query(BuddyLink)
        .filter(BuddyLink.id == link_id)
        .with_for_update()
        .one_or_none()
    )
    if not link:
        abort(404, description="BuddyLink not found")

    if not _is_party(link, me):
        abort(403, description="Not authorized")

    if link.status != "pending":
        abort(400, description=f"Cannot accept link in status '{link.status}'")

    # Policy: only addressee can accept
    if link.addressee_id != me:
        abort(403, description="Only the addressee can accept")

    # Lock both users to safely mutate counts
    requester = (
        db.session.query(User)
        .filter(User.id == link.requester_id)
        .with_for_update()
        .one()
    )
    addressee = (
        db.session.query(User)
        .filter(User.id == link.addressee_id)
        .with_for_update()
        .one()
    )

    # Enforce buddy cap (5)
    if requester.active_buddy_count >= 5:
        abort(400, description="Requester already has 5 buddies")
    if addressee.active_buddy_count >= 5:
        abort(400, description="Addressee already has 5 buddies")

    link.status = "accepted"
    link.accepted_at = _utcnow()

    requester.active_buddy_count += 1
    addressee.active_buddy_count += 1

    db.session.commit()

    return jsonify({
        "ok": True,
        "link_id": link.id,
        "status": link.status,
        "accepted_at": link.accepted_at.isoformat() if link.accepted_at else None,
        "requester_active_buddy_count": requester.active_buddy_count,
        "addressee_active_buddy_count": addressee.active_buddy_count,
    }), 200


@bp.post("/reject/<int:link_id>")
@login_required
def reject_buddy(link_id: int):
    me = current_user.id
    link = _get_link_or_404(link_id)

    if not _is_party(link, me):
        abort(403, description="Not authorized")

    if link.status != "pending":
        abort(400, description=f"Cannot reject link in status '{link.status}'")

    # Optional policy: only the addressee can reject
    if link.addressee_id != me:
        abort(403, description="Only the addressee can reject")

    link.status = "rejected"
    link.ended_at = _utcnow()
    db.session.commit()

    return jsonify({
        "ok": True,
        "link_id": link.id,
        "status": link.status,
    }), 200


@bp.post("/end/<int:link_id>")
@login_required
def end_buddy(link_id: int):
    me = current_user.id

    link = (
        db.session.query(BuddyLink)
        .filter(BuddyLink.id == link_id)
        .with_for_update()
        .one_or_none()
    )
    if not link:
        abort(404, description="BuddyLink not found")

    if not _is_party(link, me):
        abort(403, description="Not authorized")

    if link.status != "accepted":
        abort(400, description=f"Cannot end link in status '{link.status}'")

    requester = (
        db.session.query(User)
        .filter(User.id == link.requester_id)
        .with_for_update()
        .one()
    )
    addressee = (
        db.session.query(User)
        .filter(User.id == link.addressee_id)
        .with_for_update()
        .one()
    )

    link.status = "ended"
    link.ended_at = _utcnow()

    # Defensive: don't go negative
    requester.active_buddy_count = max(0, requester.active_buddy_count - 1)
    addressee.active_buddy_count = max(0, addressee.active_buddy_count - 1)

    db.session.commit()

    return jsonify({
        "ok": True,
        "link_id": link.id,
        "status": link.status,
        "ended_at": link.ended_at.isoformat() if link.ended_at else None,
        "requester_active_buddy_count": requester.active_buddy_count,
        "addressee_active_buddy_count": addressee.active_buddy_count,
    }), 200


@bp.get("/mine")
@login_required
def my_buddies():
    me = current_user.id

    links = (
        db.session.query(BuddyLink)
        .filter(
            (BuddyLink.requester_id == me) |
            (BuddyLink.addressee_id == me)
        )
        .order_by(BuddyLink.created_at.desc())
        .all()
    )

    return jsonify({
        "ok": True,
        "links": [
            {
                "id": l.id,
                "requester_id": l.requester_id,
                "addressee_id": l.addressee_id,
                "status": l.status,
                "created_at": l.created_at.isoformat() if l.created_at else None,
                "accepted_at": l.accepted_at.isoformat() if l.accepted_at else None,
                "ended_at": l.ended_at.isoformat() if l.ended_at else None,
            }
            for l in links
        ],
    })


@bp.post("/request_by_email")
@login_required
def request_buddy_by_email():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        abort(400, description="Missing email")

    other = db.session.query(User).filter(User.email.ilike(email)).one_or_none()
    if not other:
        abort(404, description="User not found")

    # reuse the existing logic
    return request_buddy(other.id)



@bp.post("/cancel/<int:link_id>")
@login_required
def cancel_buddy_request(link_id: int):
    me = current_user.id

    link = (
        db.session.query(BuddyLink)
        .filter(BuddyLink.id == link_id)
        .with_for_update()
        .one_or_none()
    )
    if not link:
        abort(404, description="BuddyLink not found")

    # Only requester can cancel
    if link.requester_id != me:
        abort(403, description="Not authorized")

    if link.status != "pending":
        abort(400, description=f"Cannot cancel link in status '{link.status}'")

    link.status = "rejected"
    link.ended_at = datetime.utcnow()

    db.session.commit()

    return jsonify({
        "ok": True,
        "link_id": link.id,
        "status": link.status,
    }), 200
