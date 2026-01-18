# app/lessons/routes.py
from flask import Blueprint, render_template

from app.models import Lesson, LessonBlock, LessonAsset
from flask import request, jsonify
from datetime import datetime
from app.extensions import db
from app.models import LessonAttempt, LessonBlockProgress
from flask_login import login_required, current_user
from flask import send_file, abort
from flask import send_file, abort
import mimetypes
from pathlib import Path
from pathlib import Path
from flask import abort, current_app
from flask import redirect, url_for, flash
from app.billing.access import has_access


bp = Blueprint("lessons", __name__, url_prefix="/lessons")

@bp.before_request
def require_paid_access_for_lessons():
    # Let login_required handle unauthenticated users on each route.
    if not current_user.is_authenticated:
        return None

    if not has_access(current_user):
        flash("No active subscription. Please choose a plan.", "warning")
        return redirect(url_for("billing.pricing"))



@bp.get("/<lesson_id>/preview")
@login_required
def preview(lesson_id: str):
    lesson = Lesson.query.get_or_404(lesson_id)
    blocks = (LessonBlock.query.filter_by(lesson_id=lesson_id)
              .order_by(LessonBlock.position.asc())
              .all())
    assets = LessonAsset.query.filter_by(lesson_id=lesson_id).all()

    return render_template("lessons/preview.html", lesson=lesson, blocks=blocks, assets=assets)



@bp.post("/heartbeat")
@login_required
def attempt_heartbeat():
    data = request.get_json(force=True) or {}
    attempt_id = data.get("attempt_id")
    block_id = data.get("lesson_block_id")
    delta = int(data.get("seconds_delta", 0) or 0)

    # clamp delta (protects against weird client spam)
    delta = max(0, min(delta, 30))
    if not attempt_id or delta <= 0:
        return jsonify(ok=True)

    attempt = LessonAttempt.query.get_or_404(attempt_id)

    # security / integrity checks
    if attempt.user_id != current_user.id:
        return jsonify(ok=True)
    if attempt.completed_at:
        return jsonify(ok=True)

    now = datetime.utcnow()

    # ✅ attempt-level time (what curriculum analytics should average)
    attempt.seconds_spent_total = (attempt.seconds_spent_total or 0) + delta
    attempt.last_heartbeat_at = now

    # ✅ optional per-block time
    if block_id:
        prog = LessonBlockProgress.query.filter_by(
            user_id=current_user.id,
            attempt_id=attempt.id,
            lesson_block_id=block_id,
        ).one_or_none()

        if not prog:
            prog = LessonBlockProgress(
                user_id=current_user.id,
                attempt_id=attempt.id,
                lesson_block_id=block_id,
                seconds_spent_total=0,
                last_seen_at=None,
            )
            db.session.add(prog)

        prog.seconds_spent_total = (prog.seconds_spent_total or 0) + delta
        prog.last_seen_at = now

    db.session.commit()
    return jsonify(ok=True)



# @bp.get("/<lesson_id>/asset/<path:ref>")
# @login_required
# def serve_asset(lesson_id: str, ref: str):
#     # ref will look like "assets/foo.png"
#     lesson = Lesson.query.get_or_404(lesson_id)
#
#     a = LessonAsset.query.filter_by(lesson_id=lesson_id, ref=ref).one_or_none()
#     if not a:
#         abort(404)
#
#     return send_file(a.storage_path, mimetype=a.content_type or "application/octet-stream")


@bp.get("/<lesson_id>/asset/<path:ref>")
@login_required
def asset(lesson_id: str, ref: str):
    # Only allow refs inside assets/ namespace
    if not ref.startswith("assets/"):
        abort(404)

    a = LessonAsset.query.filter_by(lesson_id=lesson_id, ref=ref).one_or_none()
    if not a or not a.storage_path:
        abort(404)

    p = Path(a.storage_path)
    if not p.exists():
        abort(404)

    mime = a.content_type or mimetypes.guess_type(str(p))[0] or "application/octet-stream"


    p = Path(a.storage_path or "")
    if not a.storage_path or not p.exists() or p.is_dir():
        current_app.logger.error(
            "Bad asset storage_path lesson_id=%s ref=%s storage_path=%r",
            lesson_id, ref, a.storage_path
        )
        abort(404)

    return send_file(str(p), mimetype=mime)

