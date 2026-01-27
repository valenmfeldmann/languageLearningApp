from app.author.routes import _require_curriculum_perm
from app.billing.access import has_access
from app.billing.access import access_status
from flask import Blueprint
from app.models import _uuid, AccessAccount, LessonSubject, CurriculumEditor, User
from app.access_ledger.service import (
    get_or_create_system_account,
    post_access_txn,
    EntrySpec,
)
from flask import render_template, abort
from sqlalchemy import case
from app.models import (
    Curriculum,
    CurriculumItem,
    Lesson,
    LessonCompletion,
    LessonBlock,
    LessonBlockProgress,
)
from flask import request, jsonify
from app.extensions import db
from app.models import LessonAttempt
from collections import defaultdict
from app.models import CurriculumOwner
from app.access_ledger.service import (
    AN_SCALE,
    get_an_asset,
    get_curriculum_share_asset,
    get_or_create_user_wallet,
)
from app.models import CurriculumOrder
from app.access_ledger.service import InsufficientFunds
from flask import has_request_context
from app.models import AnalyticsEvent
from sqlalchemy import func
from app.models import AccessBalance
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import session
from flask import redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import desc
import math, random
from datetime import datetime
from flask import abort, redirect, request, url_for
from flask_login import login_required, current_user
from app.extensions import db
from app.models import Curriculum, UserCurriculumStar



TREASURY_ACCOUNT_ID = "treasury"
MAX_AN = 10
A = 15  # minutes to reach half saturation

bp = Blueprint("main", __name__)


@bp.post("/app/lesson/<lesson_code>/complete")
@login_required
def complete_lesson(lesson_code: str):
    lesson = Lesson.query.filter_by(code=lesson_code).one_or_none()
    if not lesson:
        abort(404)

    attempt_id = request.form.get("attempt_id")
    if not attempt_id:
        abort(400)

    from app.models import LessonAttempt, LessonCompletion, LessonBlockProgress  # adjust import path

    attempt = LessonAttempt.query.filter_by(
        id=attempt_id,
        user_id=current_user.id,
        lesson_id=lesson.id,
    ).one_or_none()
    if attempt is None:
        abort(404)

    if attempt.completed_at is not None:
        # return jsonify({"ok": True, "already_completed": True})
        flash("Already completed.", "info")
        return redirect(url_for("main.lesson_page", lesson_code=lesson_code))

    # SERVER-SIDE gating (do it now; it prevents cheating)
    quiz_blocks = (LessonBlock.query
                   .filter_by(lesson_id=lesson.id, type="quiz_mcq")
                   .all())
    quiz_ids = [b.id for b in quiz_blocks]

    if quiz_ids:
        passed_count = (LessonBlockProgress.query
                        .filter(LessonBlockProgress.attempt_id == attempt.id)
                        .filter(LessonBlockProgress.lesson_block_id.in_(quiz_ids))
                        .filter(LessonBlockProgress.is_correct.is_(True))
                        .count())
        if passed_count != len(quiz_ids):
            # return jsonify({"ok": False, "error": "not_ready"}), 400
            flash("Complete all quizzes to finish this lesson.", "error")
            return redirect(url_for("main.lesson_page", lesson_code=lesson_code))

    # analytics: attempt completed (fires once because we return early if already completed)
    log_event(
        "lesson_attempt_completed",
        entity_type="lesson_attempt",
        entity_id=attempt.id,
        props={"lesson_id": lesson.id, "lesson_code": lesson.code},
    )

    # Mark attempt complete
    attempt.completed_at = datetime.utcnow()

    # Optional: keep LessonCompletion as an append-only history table (1 row per attempt)
    comp = LessonCompletion(
        user_id=current_user.id,
        lesson_id=lesson.id,
        attempt_id=attempt.id,
        completed_at=attempt.completed_at,
        curriculum_pos=attempt.curriculum_pos,
    )
    db.session.add(comp)
    db.session.commit()

    # Ledger reward (idempotency per attempt)
    # lesson_cfg = get_lesson(lesson_code) or {}
    # reward_ticks = 5000 #int(lesson_cfg.get("reward_notes", 0))

    time_spent_seconds = int(attempt.seconds_spent_total or 0)

    minutes = time_spent_seconds / 60.0
    user_payout = MAX_AN * minutes / (minutes + A) if minutes > 0 else 0.0
    reward_ticks = int(round(user_payout * AN_SCALE))

    if reward_ticks > 0:
        issuer = get_or_create_system_account("rewards_pool")
        user_wallet = get_or_create_user_wallet(current_user.id)

        # Standard curriculum: curriculum gets +10% extra (not taken from user)
        curriculum_bonus_ticks = 0
        curriculum_wallet_id = None

        if attempt.curriculum_id:
            curriculum = Curriculum.query.get(attempt.curriculum_id)
            if curriculum and curriculum.wallet_account_id:
                curriculum_wallet_id = curriculum.wallet_account_id
                curriculum_bonus_ticks = int(round(reward_ticks * 0.10))

        today = datetime.utcnow().date().isoformat()
        key = f"lesson_daily_reward:{current_user.id}:{lesson.id}:{today}"
        an_asset = get_an_asset()

        total_issuer_out = reward_ticks + curriculum_bonus_ticks

        entries = [
            EntrySpec(account_id=issuer.id, asset_id=an_asset.id, delta=-total_issuer_out, entry_type="mint"),
            EntrySpec(account_id=user_wallet.id, asset_id=an_asset.id, delta=+reward_ticks, entry_type="mint"),
        ]

        if curriculum_bonus_ticks > 0 and curriculum_wallet_id:
            entries.append(
                EntrySpec(account_id=curriculum_wallet_id, asset_id=an_asset.id, delta=+curriculum_bonus_ticks,
                          entry_type="mint")
            )

        post_access_txn(
            event_type="lesson_complete_reward",
            idempotency_key=key,
            actor_user_id=current_user.id,
            context_type="lesson_attempt",
            context_id=attempt.id,
            memo_json={
                "lesson_code": lesson_code,
                "lesson_id": lesson.id,
                "reward_notes": reward_ticks,
                "curriculum_bonus_notes": curriculum_bonus_ticks,
                "curriculum_id": attempt.curriculum_id,
            },
            entries=entries,
        )

        log_event(
            "lesson_daily_reward_issued",
            entity_type="lesson",
            entity_id=lesson.id,
            props={"reward_notes": reward_ticks, "idempotency_key": key, "attempt_id": attempt.id},
        )

    # # return jsonify({"ok": True, "already_completed": False, "rewarded": reward})
    # flash(f"Lesson completed! Reward: {reward} AN", "success")
    # return redirect(url_for("main.lesson_page", lesson_code=lesson_code))

    # Success message
    # flash(f"Lesson completed! Reward: {reward} AN", "success")

    flash(f"+{reward_ticks/AN_SCALE:g} AN awarded 🎉", "success")

    # Redirect: curriculum > lesson
    if attempt.curriculum_id:
        curriculum = Curriculum.query.get(attempt.curriculum_id)
        if curriculum:
            return redirect(url_for(
                "main.curriculum_view",
                curriculum_code=curriculum.code,
            ))

    return redirect(url_for("main.lesson_page", lesson_code=lesson_code))


@bp.get("/billing_status")
@login_required
def billing_status():
    info = access_status(current_user)
    return jsonify({
        "allowed": info.allowed,
        "reason": info.reason,
        "subscription_status": info.subscription_status,
        "current_period_end": info.current_period_end.isoformat() if info.current_period_end else None,
        "trial_end": info.trial_end.isoformat() if info.trial_end else None,
        "comped_until": info.comped_until.isoformat() if info.comped_until else None,
    })


@bp.get("/")
def home():
    return redirect(url_for("auth.login"))
    # return {"ok": True}



# @bp.get("/app")
# def app_home():
#     if not has_access(current_user):
#         return redirect(url_for("billing.pricing"))
#
#     # optional: find an in-progress lesson
#     attempt = (
#         LessonAttempt.query
#         .filter_by(user_id=current_user.id, completed_at=None)
#         .order_by(
#             desc(LessonAttempt.last_heartbeat_at),
#             desc(LessonAttempt.started_at),
#         )
#         .first()
#     )
#
#     lesson = Lesson.query.get(attempt.lesson_id) if attempt else None
#
#     return render_template(
#         "app/home.html",
#         continue_lesson=lesson,
#     )




@bp.post("/app/curriculum/<curriculum_id>/star")
@login_required
def curriculum_star(curriculum_id: str):
    cur = Curriculum.query.get_or_404(curriculum_id)

    # Optional: prevent starring archived/unpublished if you want
    # if cur.is_archived or not cur.is_published:
    #     abort(404)

    exists = UserCurriculumStar.query.filter_by(
        user_id=current_user.id, curriculum_id=cur.id
    ).one_or_none()

    if not exists:
        db.session.add(UserCurriculumStar(user_id=current_user.id, curriculum_id=cur.id))
        db.session.commit()

    return redirect(request.referrer or url_for("main.app_home"))


@bp.post("/app/curriculum/<curriculum_id>/unstar")
@login_required
def curriculum_unstar(curriculum_id: str):
    cur = Curriculum.query.get_or_404(curriculum_id)

    UserCurriculumStar.query.filter_by(
        user_id=current_user.id, curriculum_id=cur.id
    ).delete(synchronize_session=False)
    db.session.commit()

    return redirect(request.referrer or url_for("main.app_home"))



from sqlalchemy import or_, case

@bp.get("/app")
def app_home():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    attempt = (
        LessonAttempt.query
        .filter_by(user_id=current_user.id, completed_at=None)
        .order_by(desc(LessonAttempt.last_heartbeat_at), desc(LessonAttempt.started_at))
        .first()
    )
    lesson = Lesson.query.get(attempt.lesson_id) if attempt else None

    q = (request.args.get("q") or "").strip()
    subject_code = (request.args.get("subject") or "").strip() or None
    school_code = (request.args.get("school") or "").strip() or None
    sort = (request.args.get("sort") or "new").strip()
    starred_only = (request.args.get("starred") or "").strip().lower() in ("1", "true", "yes", "on")

    subjects = (
        LessonSubject.query
        .filter(LessonSubject.active.is_(True))
        .order_by(LessonSubject.name.asc())
        .all()
    )
    subject_name_by_code = {
        s.code: s.name
        for s in subjects
    }

    school_rows = (
        db.session.query(Curriculum.school_code)
        .filter(Curriculum.school_code.isnot(None))
        .distinct()
        .order_by(Curriculum.school_code.asc())
        .all()
    )
    schools = [r[0] for r in school_rows if r[0]]

    school_name_by_code = {
        sc: sc.replace("_", " ").title()
        for sc in schools
    }

    # ---------- base query ----------
    base = (
        Curriculum.query
        .filter(Curriculum.is_archived.is_(False))
        .filter(Curriculum.is_published.is_(True))
    )

    if subject_code:
        base = base.filter(Curriculum.subject_code == subject_code)
    if school_code:
        base = base.filter(Curriculum.school_code == school_code)

    if q:
        like = f"%{q}%"
        base = base.filter(or_(
            Curriculum.title.ilike(like),
            Curriculum.description.ilike(like),
            Curriculum.code.ilike(like),
        ))

    # ---------- stats subquery ----------
    lesson_count_sq = (
        db.session.query(
            CurriculumItem.curriculum_id.label("cid"),
            func.count(CurriculumItem.id).label("lesson_count"),
        )
        .filter(CurriculumItem.item_type == "lesson")
        .filter(CurriculumItem.lesson_id.isnot(None))
        .group_by(CurriculumItem.curriculum_id)
        .subquery()
    )

    an_asset = get_an_asset()
    ab = AccessBalance
    s = UserCurriculumStar

    # joins
    base = (
        base
        .outerjoin(lesson_count_sq, lesson_count_sq.c.cid == Curriculum.id)
        .outerjoin(ab, (ab.account_id == Curriculum.wallet_account_id) & (ab.asset_id == an_asset.id))
        .outerjoin(s, (s.curriculum_id == Curriculum.id) & (s.user_id == current_user.id))
    )

    if starred_only:
        base = base.filter(s.user_id.isnot(None))

    # select once (keep is_starred!)
    base = base.with_entities(
        Curriculum,
        func.coalesce(lesson_count_sq.c.lesson_count, 0).label("lesson_count"),
        func.coalesce(ab.balance, 0).label("wallet_ticks"),
        case((s.user_id.isnot(None), True), else_=False).label("is_starred"),
    )

    # sorting
    if sort == "title":
        base = base.order_by(Curriculum.title.asc())
    elif sort == "market_cap":
        base = base.order_by(desc(func.coalesce(ab.balance, 0)), Curriculum.created_at.desc())
    else:
        base = base.order_by(Curriculum.created_at.desc())

    rows = base.limit(60).all()

    cards = []
    for cur, lesson_count, wallet_ticks, is_starred in rows:
        cards.append({
            "curriculum": cur,
            "lesson_count": int(lesson_count or 0),
            "market_cap_an": int(wallet_ticks or 0) / AN_SCALE,
            "wallet_ticks": int(wallet_ticks or 0),
            "is_starred": bool(is_starred),
        })

    return render_template(
        "app/home.html",
        continue_lesson=lesson,
        cards=cards,
        q=q,
        subject_code=subject_code,
        school_code=school_code,
        sort=sort,
        starred_only=starred_only,
        subjects=subjects,
        schools=schools,
        subject_name_by_code=subject_name_by_code,
        school_name_by_code=school_name_by_code,
    )




@bp.get("/pricing")
def pricing_shortcut():
    return redirect(url_for("billing.pricing"))




@bp.post("/app/lesson/<lesson_code>/quiz_answer")
@login_required
def quiz_answer(lesson_code):
    lesson = Lesson.query.filter_by(code=lesson_code).one_or_none()
    if not lesson:
        abort(404)

    block_id = request.form.get("quiz_block_id")
    answer_index_raw = request.form.get("choice_index")
    attempt_id = request.form.get("attempt_id")

    if not block_id or answer_index_raw is None or not attempt_id:
        flash("Missing answer.", "error")
        return redirect(url_for(
            "main.lesson_page",
            curriculum=request.args.get("curriculum"),
            pos=request.args.get("pos"),
            lesson_code=lesson_code,
            quiz_block_id=block_id or "",
            quiz_correct="0",
        ))

    block = LessonBlock.query.filter_by(id=block_id, lesson_id=lesson.id).one_or_none()
    if not block or block.type != "quiz_mcq":
        flash("Invalid quiz block.", "error")
        return redirect(url_for(
            "main.lesson_page",
            curriculum=request.args.get("curriculum"),
            pos=request.args.get("pos"),
            lesson_code=lesson_code,
            quiz_block_id=block_id,
            quiz_correct="0",
        ))

    from app.models import LessonAttempt, LessonBlockProgress  # adjust import path if needed

    attempt = LessonAttempt.query.filter_by(id=attempt_id, user_id=current_user.id, lesson_id=lesson.id).one_or_none()
    if attempt is None or attempt.completed_at is not None:
        flash("Invalid or closed attempt.", "error")
        return redirect(url_for("main.lesson_page", lesson_code=lesson_code))

    try:
        answer_index = int(answer_index_raw)
    except ValueError:
        flash("Invalid answer.", "error")
        return redirect(url_for(
            "main.lesson_page",
            curriculum=request.args.get("curriculum"),
            pos=request.args.get("pos"),
            lesson_code=lesson_code,
            quiz_block_id=str(block.id),
            quiz_correct="0",
        ))

    correct_index = int(block.payload_json.get("answer_index", -1))
    correct = (answer_index == correct_index)

    log_event(
        "quiz_answer_submitted",
        entity_type="lesson_attempt",
        entity_id=attempt.id,
        props={
            "lesson_id": lesson.id,
            "lesson_code": lesson.code,
            "lesson_block_id": block.id,
            "choice_index": answer_index,
            "is_correct": bool(correct),
        },
    )

    # Upsert progress by (attempt_id, lesson_block_id)
    prog = LessonBlockProgress.query.filter_by(
        attempt_id=attempt.id,
        lesson_block_id=block.id,
    ).one_or_none()

    if prog is None:
        prog = LessonBlockProgress(
            user_id=current_user.id,              # keep for convenience; redundant but fine
            attempt_id=attempt.id,
            lesson_block_id=block.id,
            last_choice_index=answer_index,
            is_correct=bool(correct),
            answered_at=datetime.utcnow(),
        )
        db.session.add(prog)
    else:
        prog.last_choice_index = answer_index
        prog.answered_at = datetime.utcnow()
        if correct:
            prog.is_correct = True  # success sticks for this attempt

    db.session.commit()

    return redirect(url_for(
        "main.lesson_page",
        curriculum=request.args.get("curriculum"),
        pos=request.args.get("pos"),
        lesson_code=lesson_code,
        quiz_block_id=str(block.id),
        quiz_correct=("1" if correct else "0"),
        _anchor=f"block-{block_id}",
    ))





from jinja2 import TemplateNotFound
from flask import current_app

def _block_template_for(block_type: str) -> str:
    tpl = f"lessons/blocks/{block_type}.html"
    try:
        current_app.jinja_env.get_template(tpl)
        return tpl
    except TemplateNotFound:
        return "lessons/blocks/unknown.html"


@bp.get("/app/lesson/<lesson_code>")
@login_required
def lesson_page(lesson_code):
    lesson = Lesson.query.filter_by(code=lesson_code).first_or_404()
    blocks = (LessonBlock.query
              .filter_by(lesson_id=lesson.id)
              .order_by(LessonBlock.position.asc())
              .all())

    curriculum_code = request.args.get("curriculum")
    curriculum = None
    if curriculum_code:
        curriculum = Curriculum.query.filter_by(code=curriculum_code).one_or_none()

    pos_raw = request.args.get("pos")
    pos = int(pos_raw) if (pos_raw and pos_raw.isdigit()) else None

    src = request.args.get("src")
    if src:
        log_event("click", "lesson", lesson.id, {
            "src": src,
            "q": request.args.get("q"),
            "scope": request.args.get("scope"),
            "pos": request.args.get("pos"),
        })

    quiz_block_id = request.args.get("quiz_block_id")
    correct = request.args.get("quiz_correct")

    quiz_feedback = None
    if quiz_block_id is not None and correct is not None:
        quiz_feedback = {"block_id": quiz_block_id, "correct": (correct == "1")}

    from app.models import LessonAttempt, LessonBlockProgress  # adjust import path if needed

    # 1) Get or create an ACTIVE attempt for this user+lesson
    q = (LessonAttempt.query
         .filter_by(user_id=current_user.id, lesson_id=lesson.id, completed_at=None))
    if curriculum:
        q = q.filter(LessonAttempt.curriculum_id == curriculum.id)
    attempt = q.order_by(LessonAttempt.started_at.desc()).first()
    if attempt and curriculum and attempt.curriculum_pos is None and pos is not None:
        attempt.curriculum_pos = pos
        db.session.commit()

    if attempt is None:
        attempt = LessonAttempt(
            id=_uuid(),
            user_id=current_user.id,
            lesson_id=lesson.id,
            started_at=datetime.utcnow(),
            completed_at=None,
            curriculum_id=(curriculum.id if curriculum else None),
            curriculum_pos=pos,
        )
        db.session.add(attempt)
        db.session.commit()

        log_event(
            "lesson_attempt_started",
            entity_type="lesson_attempt",
            entity_id=attempt.id,
            props={"lesson_id": lesson.id, "lesson_code": lesson.code},
        )

    # 2) Completion gating FOR THIS ATTEMPT ONLY
    quiz_blocks = [b for b in blocks if b.type == "quiz_mcq"]
    total_quizzes = len(quiz_blocks)

    if total_quizzes == 0:
        can_complete = True
    else:
        quiz_ids = [b.id for b in quiz_blocks]
        passed_count = (LessonBlockProgress.query
                        .filter(LessonBlockProgress.attempt_id == attempt.id)
                        .filter(LessonBlockProgress.lesson_block_id.in_(quiz_ids))
                        .filter(LessonBlockProgress.is_correct.is_(True))
                        .count())
        can_complete = (passed_count == total_quizzes)

    block_views = [{"b": b, "tpl": _block_template_for(b.type)} for b in blocks]

    log_event(
        "lesson_page_view",
        entity_type="lesson",
        entity_id=lesson.id,
        props={"lesson_code": lesson.code, "attempt_id": attempt.id},
    )

    return render_template(
        "lessons/lesson_page.html",
        lesson=lesson,
        block_views=block_views,
        blocks=blocks,
        quiz_feedback=quiz_feedback,
        can_complete=can_complete,
        attempt=attempt,  # <-- critical for hidden inputs
    )



# @bp.get("/app/curriculum")
# @login_required
# def curriculum_index():
#     from app.models import Curriculum
#     from sqlalchemy import or_
#
#     q = (request.args.get("q") or "").strip()
#     scope = (request.args.get("scope") or "mine").strip().lower()
#
#     base = Curriculum.query
#
#     if scope == "published":
#         base = base.filter(Curriculum.is_published.is_(True))
#     elif scope == "all":
#         base = base.filter(or_(
#             Curriculum.created_by_user_id == current_user.id,
#             Curriculum.is_published.is_(True),
#         ))
#     else:
#         # default: mine = curriculums I have shares in
#         base = (base.join(CurriculumOwner, CurriculumOwner.curriculum_id == Curriculum.id)
#                 .filter(CurriculumOwner.user_id == current_user.id)
#                 .filter(CurriculumOwner.shares > 0))
#
#     if q:
#         like = f"%{q}%"
#         base = base.filter(or_(
#             Curriculum.title.ilike(like),
#             Curriculum.description.ilike(like),
#         ))
#
#     curriculums = base.order_by(Curriculum.title.asc()).all()
#
#     log_event(
#         "curriculum_index_impression",
#         entity_type="curriculum_list",
#         entity_id=None,
#         props={"q": q, "scope": scope, "count": len(curriculums)},
#     )
#
#     return render_template(
#         "curriculum/index.html",
#         curriculums=curriculums,
#         q=q,
#         scope=scope,
#     )


@bp.get("/app/curriculum")
@login_required
def curriculum_index():
    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "mine").strip()
    subject_code = (request.args.get("subject") or "").strip() or None

    subjects = (
        LessonSubject.query
        .filter(LessonSubject.active.is_(True))
        .order_by(LessonSubject.name.asc())
        .all()
    )

    query = Curriculum.query

    # ----- scope -----
    if scope == "mine":
        query = query.filter(Curriculum.created_by_user_id == current_user.id)
    elif scope == "published":
        query = query.filter(Curriculum.is_published.is_(True))
    elif scope == "all":
        query = query.filter(
            (Curriculum.created_by_user_id == current_user.id) |
            (Curriculum.is_published.is_(True))
        )

    # ----- subject -----
    if subject_code:
        query = query.filter(Curriculum.subject_code == subject_code)

    # ----- text search -----
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Curriculum.title.ilike(like)) |
            (Curriculum.description.ilike(like))
        )

    curriculums = (
        query
        .order_by(Curriculum.created_at.desc())
        .all()
    )

    return render_template(
        "curriculum/index.html",
        curriculums=curriculums,
        q=q,
        scope=scope,
        subject_code=subject_code,
        subjects=subjects,
    )






@bp.get("/app/curriculum/<curriculum_code>")
@login_required
def curriculum_view(curriculum_code: str):
    from collections import defaultdict
    from sqlalchemy import func
    from app.models import Curriculum, CurriculumItem, Lesson, LessonCompletion
    from app.models import CurriculumOrder, AccessBalance

    cur = Curriculum.query.filter_by(code=curriculum_code).one_or_none()
    if not cur:
        abort(404)

    session["last_curriculum_code"] = cur.code

    items = (CurriculumItem.query
             .filter_by(curriculum_id=cur.id)
             .order_by(CurriculumItem.position.asc())
             .all())

    # completion counts per lesson for this user
    rows = (db.session.query(LessonCompletion.lesson_id, func.count(LessonCompletion.id))
            .filter(LessonCompletion.user_id == current_user.id)
            .group_by(LessonCompletion.lesson_id)
            .all())
    completion_count = {lesson_id: int(cnt) for lesson_id, cnt in rows}

    # Track how many times each lesson has appeared so far in this curriculum
    seen = defaultdict(int)

    # Attach display info
    display = []
    lesson_ids = [it.lesson_id for it in items if it.item_type == "lesson" and it.lesson_id]
    lessons = {}
    if lesson_ids:
        for L in Lesson.query.filter(Lesson.id.in_(lesson_ids)).all():
            lessons[L.id] = L

    for it in items:
        if it.item_type == "phase":
            display.append({"kind": "phase", "title": it.phase_title or "Phase"})
            continue

        if it.item_type == "lesson" and it.lesson_id:
            seen[it.lesson_id] += 1
            n_completed = completion_count.get(it.lesson_id, 0)
            is_done = seen[it.lesson_id] <= n_completed
            display.append({
                "kind": "lesson",
                "lesson": lessons.get(it.lesson_id),
                "is_done": is_done,
                "occurrence": seen[it.lesson_id],
                "note": it.note,
            })
            continue

        # fallback
        display.append({"kind": "unknown"})

    log_event(
        "curriculum_view",
        entity_type="curriculum",
        entity_id=cur.id,
        props={"curriculum_code": cur.code},
    )

    my_row, _, _ = _curr_owner_ctx(cur.id)

    can_edit = bool(
        (my_row and my_row.can_edit)
        or CurriculumEditor.query.filter_by(
            curriculum_id=cur.id,
            user_id=current_user.id,
            can_edit=True,
        ).first()
    )

    can_view_analytics = bool(my_row and my_row.shares > 0 and my_row.can_view_analytics)
    can_manage = bool(my_row and my_row.shares > 0 and my_row.can_manage_ownership)

    # ----------------------------
    # TRADING CONTEXT (NEW)
    # ----------------------------
    an = get_an_asset()
    sh = get_curriculum_share_asset(cur.id)

    my_wallet = get_or_create_user_wallet(current_user.id)

    an_bal_row = AccessBalance.query.filter_by(account_id=my_wallet.id, asset_id=an.id).one_or_none()
    sh_bal_row = AccessBalance.query.filter_by(account_id=my_wallet.id, asset_id=sh.id).one_or_none()

    my_an_ticks = int(an_bal_row.balance) if an_bal_row else 0
    my_shares = int(sh_bal_row.balance) if sh_bal_row else 0

    bids = (CurriculumOrder.query
            .filter_by(curriculum_id=cur.id, side="bid", status="open")
            .filter(CurriculumOrder.qty_remaining > 0)
            .order_by(CurriculumOrder.price_ticks.desc(), CurriculumOrder.created_at.asc())
            .limit(50)
            .all())

    asks = (CurriculumOrder.query
            .filter_by(curriculum_id=cur.id, side="ask", status="open")
            .filter(CurriculumOrder.qty_remaining > 0)
            .order_by(CurriculumOrder.price_ticks.asc(), CurriculumOrder.created_at.asc())
            .limit(50)
            .all())

    my_open_orders = (CurriculumOrder.query
                      .filter_by(curriculum_id=cur.id, user_id=current_user.id, status="open")
                      .filter(CurriculumOrder.qty_remaining > 0)
                      .order_by(CurriculumOrder.created_at.desc())
                      .limit(50)
                      .all())


    # -------------------- Fix “Your shares” and “Your balance” on the curriculum page --------------------

    # --- Trading UI state ---
    wallet = get_or_create_user_wallet(current_user.id)

    an_asset = get_an_asset()
    share_asset = get_curriculum_share_asset(cur.id)

    my_an_ticks = (
            AccessBalance.query.filter_by(account_id=wallet.id, asset_id=an_asset.id)
            .with_entities(AccessBalance.balance)
            .scalar()
            or 0
    )

    my_shares = (
            AccessBalance.query.filter_by(account_id=wallet.id, asset_id=share_asset.id)
            .with_entities(AccessBalance.balance)
            .scalar()
            or 0
    )

    my_open_orders = (
        CurriculumOrder.query
        .filter_by(curriculum_id=cur.id, user_id=current_user.id, status="open")
        .order_by(CurriculumOrder.created_at.desc())
        .all()
    )

    bids = (
        CurriculumOrder.query
        .filter_by(curriculum_id=cur.id, side="bid", status="open")
        .order_by(CurriculumOrder.price_ticks.desc(), CurriculumOrder.created_at.asc())
        .limit(20)
        .all()
    )

    asks = (
        CurriculumOrder.query
        .filter_by(curriculum_id=cur.id, side="ask", status="open")
        .order_by(CurriculumOrder.price_ticks.asc(), CurriculumOrder.created_at.asc())
        .limit(20)
        .all()
    )

    # -----------------------------------------------------------------------------------------------------
    from app.access_ledger.service import get_balance_ticks

    an = get_an_asset()
    AN_ASSET_ID = an.id

    curriculum_an_ticks = get_balance_ticks(cur.wallet_account_id, AN_ASSET_ID)

    # curriculum_an_ticks = 0
    # if cur.wallet_account_id:
    #     bal = AccessBalance.query.filter_by(
    #         account_id=cur.wallet_account_id,
    #         asset_id=AN_ASSET_ID,
    #     ).one_or_none()
    #     curriculum_an_ticks = int(bal.balance) if bal else 0


    treasury_share_bal = get_balance_ticks(TREASURY_ACCOUNT_ID, share_asset.id)  # this will be negative, e.g. -100
    total_shares_outstanding = max(0, -treasury_share_bal)

    total_shares_outstanding = int(total_shares_outstanding or 0)
    my_shares = int(my_shares or 0)

    ownership_pct = (100.0 * my_shares / total_shares_outstanding) if total_shares_outstanding > 0 else 0.0

    return render_template(
        "curriculum/view.html",
        curriculum=cur,
        items=display,
        can_edit=can_edit,
        can_view_analytics=can_view_analytics,
        can_manage=can_manage,



        # trading UI
        AN_SCALE=AN_SCALE,
        my_an_ticks=my_an_ticks,
        my_shares=my_shares,
        my_open_orders=my_open_orders,
        bids=bids,
        asks=asks,
        curriculum_an_ticks=curriculum_an_ticks,  # <— add this
        total_shares_outstanding=total_shares_outstanding,
        ownership_pct=ownership_pct,

    )



@bp.post("/author/curriculum/<curriculum_code>/editor/add")
@login_required
def curriculum_editor_add(curriculum_code):
    from app.models import Curriculum, CurriculumEditor, User

    cur = Curriculum.query.filter_by(code=curriculum_code).first_or_404()

    _require_curriculum_perm(cur.id, need_manage=True)

    email = request.form.get("email", "").strip().lower()
    if not email:
        abort(400)

    user = User.query.filter(func.lower(User.email) == email).first()
    if not user:
        flash("User not found", "error")
        return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))

    # prevent duplicates
    exists = CurriculumEditor.query.filter_by(
        curriculum_id=cur.id,
        user_id=user.id,
    ).first()
    if exists:
        flash("User already has access", "info")
        return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))

    db.session.add(CurriculumEditor(
        curriculum_id=cur.id,          # ✅ ID, not code
        user_id=user.id,
        can_edit=True,
        invited_by_user_id=current_user.id,
    ))
    db.session.commit()               # ✅ REQUIRED

    flash("Editor added", "success")
    return redirect(url_for("author.curriculum_edit", curriculum_id=cur.id))



@bp.post("/app/curriculum/<curriculum_code>/editors/remove")
@login_required
def curriculum_editor_remove(curriculum_code):
    cur = Curriculum.query.filter_by(code=curriculum_code).one_or_none()
    if not cur:
        abort(404)

    _require_curriculum_manage(cur.id)

    user_id = request.form.get("user_id")
    if not user_id:
        abort(400)

    CurriculumEditor.query.filter_by(
        curriculum_id=cur.id,
        user_id=user_id,
    ).delete()

    db.session.commit()
    flash("Editor removed.", "info")
    return redirect(url_for("author.curriculum_edit", curriculum_code=cur.code))





@bp.get("/app/lessons")
@login_required
def lessons_index():
    from app.models import Lesson
    from sqlalchemy import or_

    q = (request.args.get("q") or "").strip()
    scope = (request.args.get("scope") or "mine").strip().lower()

    base = Lesson.query

    if scope == "public":
        base = base.filter(Lesson.visibility == "public")
    elif scope == "all":
        base = base.filter(or_(
            Lesson.created_by_user_id == current_user.id,
            Lesson.visibility == "public",
        ))
    else:
        # default: mine
        base = base.filter(Lesson.created_by_user_id == current_user.id)

    if q:
        like = f"%{q}%"
        base = base.filter(or_(
            Lesson.title.ilike(like),
            Lesson.code.ilike(like),
            Lesson.description.ilike(like),
        ))

    lessons = base.order_by(Lesson.title.asc()).all()

    log_event(
        "lessons_index_impression",
        entity_type="lesson_list",
        entity_id=None,
        props={"q": q, "scope": scope, "count": len(lessons)},
    )

    return render_template("lessons/index.html", lessons=lessons, q=q, scope=scope)







@bp.get("/app/curriculum/<curriculum_code>/analytics")
@login_required
def curriculum_analytics(curriculum_code: str):
    curriculum = Curriculum.query.filter_by(code=curriculum_code).one_or_none()
    if not curriculum:
        abort(404)

    # owners with shares can view analytics (paid owners)
    _require_curriculum_analytics(curriculum.id)

    items = (CurriculumItem.query
             .filter_by(curriculum_id=curriculum.id)
             .order_by(CurriculumItem.position.asc())
             .all())

    lesson_items = [it for it in items if (it.item_type or "").strip().lower() == "lesson" and it.lesson_id]
    lesson_ids = [it.lesson_id for it in lesson_items]

    if not lesson_ids:
        return render_template(
            "curriculum/analytics.html",
            curriculum=curriculum,
            rows=[],
            summary={"lessons_in_curriculum": 0, "unique_lessons": 0},
            has_attempt_time=False,
        )

    lessons = Lesson.query.filter(Lesson.id.in_(lesson_ids)).all()
    lesson_by_id = {L.id: L for L in lessons}

    # starts = number of attempts (all users)
    starts = dict(
        db.session.query(LessonAttempt.lesson_id, func.count(LessonAttempt.id))
        .filter(LessonAttempt.lesson_id.in_(lesson_ids))
        .group_by(LessonAttempt.lesson_id)
        .all()
    )

    # completions = number of completion rows (all users)
    completions = dict(
        db.session.query(LessonCompletion.lesson_id, func.count(LessonCompletion.id))
        .filter(LessonCompletion.lesson_id.in_(lesson_ids))
        .group_by(LessonCompletion.lesson_id)
        .all()
    )

    # avg time per lesson (only if you add LessonAttempt.seconds_spent_total later)
    has_attempt_time = hasattr(LessonAttempt, "seconds_spent_total")
    avg_seconds = {}
    if has_attempt_time:
        avg_seconds = dict(
            db.session.query(LessonAttempt.lesson_id, func.avg(LessonAttempt.seconds_spent_total))
            .filter(LessonAttempt.lesson_id.in_(lesson_ids))
            .group_by(LessonAttempt.lesson_id)
            .all()
        )

    # quiz blocks per lesson
    quiz_blocks = (LessonBlock.query
                   .filter(LessonBlock.lesson_id.in_(lesson_ids),
                           LessonBlock.type == "quiz_mcq")
                   .all())
    quiz_blocks_per_lesson = defaultdict(int)
    for b in quiz_blocks:
        quiz_blocks_per_lesson[b.lesson_id] += 1

    # quiz accuracy per lesson: correct / answered rows (all users)
    quiz_total_answered = defaultdict(int)
    quiz_correct_counts = defaultdict(int)

    if quiz_blocks:
        q = (db.session.query(
                LessonBlock.lesson_id.label("lesson_id"),
                func.count(LessonBlockProgress.id).label("answered"),
                func.sum(case((LessonBlockProgress.is_correct.is_(True), 1), else_=0)).label("correct"),
            )
            .join(LessonBlockProgress, LessonBlockProgress.lesson_block_id == LessonBlock.id)
            .filter(LessonBlock.type == "quiz_mcq")
            .filter(LessonBlock.lesson_id.in_(lesson_ids))
            .group_by(LessonBlock.lesson_id))

        for lesson_id, answered, correct in q.all():
            quiz_total_answered[lesson_id] = int(answered or 0)
            quiz_correct_counts[lesson_id] = int(correct or 0)

    # build rows in curriculum order (including repeats)
    rows = []
    for slot, it in enumerate(lesson_items, start=1):
        L = lesson_by_id.get(it.lesson_id)
        if not L:
            continue

        s = int(starts.get(L.id, 0))
        c = int(completions.get(L.id, 0))
        rate = (c / s) if s > 0 else None

        avg_s = None
        if has_attempt_time:
            v = avg_seconds.get(L.id, None)
            avg_s = float(v) if v is not None else None

        qb = int(quiz_blocks_per_lesson.get(L.id, 0))
        answered = int(quiz_total_answered.get(L.id, 0))
        correct = int(quiz_correct_counts.get(L.id, 0))
        quiz_acc = (correct / answered) if answered > 0 else None

        rows.append({
            "slot": slot,
            "lesson_code": L.code,
            "lesson_title": L.title,
            "starts": s,
            "completions": c,
            "completion_rate": rate,
            "avg_seconds": avg_s,
            "quiz_blocks": qb,
            "quiz_answered_rows": answered,
            "quiz_accuracy": quiz_acc,
            "note": it.note,
        })

    summary = {
        "lessons_in_curriculum": len(lesson_items),
        "unique_lessons": len(set(lesson_ids)),
    }


    # -------------------- Retention graph additions --------------------
    # WINDOW_DAYS = 30
    # ACTIVE_SECONDS = 60
    # CONSISTENCY_PCT = 0.90

    window_days = max(1, min(int(request.args.get("window_days", 30)), 365))
    active_seconds = max(1, min(int(request.args.get("active_seconds", 60)), 60 * 60))
    consistency_pct = float(request.args.get("consistency", 0.90))
    consistency_pct = max(0.0, min(consistency_pct, 1.0))

    from datetime import timedelta
    from app.models import CurriculumDayActivity
    from statistics import median

    # today = date.today()
    today = datetime.utcnow().date()
    start_day = today - timedelta(days=window_days - 1)

    rows_daily = (
        CurriculumDayActivity.query
        .filter(CurriculumDayActivity.curriculum_id == curriculum.id)
        .filter(CurriculumDayActivity.day >= start_day)
        .all()
    )


    active_days_by_user = defaultdict(set)

    for r in rows_daily:
        if r.seconds_spent_total >= active_seconds:
            active_days_by_user[r.user_id].add(r.day)

    active_day_counts = [len(days) for days in active_days_by_user.values()]
    median_active_days = median(active_day_counts) if active_day_counts else 0

    retention_curve = []

    user_count = len(active_days_by_user)

    # headline stats (users with >=1 active day in window slices)
    def active_users_in_last(k: int) -> int:
        start = today - timedelta(days=k - 1)
        return sum(1 for days in active_days_by_user.values() if any(d >= start for d in days))

    active_today = active_users_in_last(1)
    active_7d = active_users_in_last(min(7, window_days))
    active_30d = active_users_in_last(min(30, window_days))

    # loose retention curve: >= 1 active day in last n days
    loose_curve = []
    for n in range(1, window_days + 1):
        start = today - timedelta(days=n - 1)
        retained_any = sum(
            1 for days in active_days_by_user.values()
            if any(d >= start for d in days)
        )
        loose_curve.append(retained_any / user_count if user_count else 0.0)

    for n in range(1, window_days + 1):
        cutoff = int((n * consistency_pct) + 0.999)  # ceil
        retained = sum(
            1 for days in active_days_by_user.values()
            if len([d for d in days if d >= today - timedelta(days=n - 1)]) >= cutoff
        )
        retention_curve.append(retained / user_count if user_count else 0.0)

    # -------------------------------------------------------------------

    # -------------------------- Compute funnel --------------------------

    cohort_users = set(active_days_by_user.keys())
    slot_count = len(lesson_items)
    den = len(cohort_users) or 1

    # Max slot STARTED per user (within curriculum + time window)
    attempt_rows = (
        db.session.query(LessonAttempt.user_id, func.max(LessonAttempt.curriculum_pos))
        .filter(LessonAttempt.curriculum_id == curriculum.id)
        .filter(LessonAttempt.curriculum_pos.isnot(None))
        .filter(LessonAttempt.user_id.in_(cohort_users))
        .filter(LessonAttempt.started_at >= datetime.utcnow() - timedelta(days=window_days))
        .group_by(LessonAttempt.user_id)
        .all()
    )
    max_started = {uid: int(m or 0) for uid, m in attempt_rows}

    # Max slot COMPLETED per user (join completions -> attempts to enforce curriculum_id)
    comp_rows = (
        db.session.query(LessonCompletion.user_id, func.max(LessonAttempt.curriculum_pos))
        .join(LessonAttempt, LessonAttempt.id == LessonCompletion.attempt_id)
        .filter(LessonAttempt.curriculum_id == curriculum.id)
        .filter(LessonAttempt.curriculum_pos.isnot(None))
        .filter(LessonCompletion.user_id.in_(cohort_users))
        .filter(LessonCompletion.completed_at >= datetime.utcnow() - timedelta(days=window_days))
        .group_by(LessonCompletion.user_id)
        .all()
    )
    max_completed = {uid: int(m or 0) for uid, m in comp_rows}

    reached = []
    completed = []
    dropoff = []

    for k in range(1, slot_count + 1):
        r = sum(1 for u in cohort_users if max_started.get(u, 0) >= k) / den
        c = sum(1 for u in cohort_users if max_completed.get(u, 0) >= k) / den
        reached.append(r)
        completed.append(c)

    for k in range(1, slot_count):
        dropoff.append(reached[k - 1] - reached[k])
    dropoff.append(None)

    funnel = {
        "slots": slot_count,
        "denominator": len(cohort_users),
        "reached": reached,
        "completed": completed,
        "dropoff": dropoff,
        "window_days": window_days,
    }

    # --------------------------------------------------------------------

    return render_template(
        "curriculum/analytics.html",
        curriculum=curriculum,
        rows=rows,
        summary=summary,
        has_attempt_time=has_attempt_time,
        retention={
            "window_days": window_days,
            "active_seconds": active_seconds,
            "consistency": consistency_pct,
            "curve": retention_curve,  # strict (your current one)
            "loose_curve": loose_curve,  # NEW
            "median_active_days": median_active_days,
            "active_today": active_today,  # NEW
            "active_7d": active_7d,  # NEW
            "active_30d": active_30d,  # NEW
            "user_count": user_count,  # NEW (nice to show denominator)
        },
        funnel=funnel
    )



# def log_event(event_type, entity_type=None, entity_id=None, props=None):
#     from app.models import AnalyticsEvent
#     ev = AnalyticsEvent(
#         user_id=current_user.id if current_user.is_authenticated else None,
#         event_type=event_type,
#         entity_type=entity_type,
#         entity_id=entity_id,
#         props_json=props or None,
#     )
#     db.session.add(ev)
#     db.session.commit()



def log_event(name: str, entity_type: str | None = None, entity_id: str | None = None, props: dict | None = None):
    props = props or {}

    user_id = None
    try:
        if has_request_context() and getattr(current_user, "is_authenticated", False):
            user_id = current_user.id
    except Exception:
        user_id = None

    ev = AnalyticsEvent(
        event_type=name,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=entity_id,
        props_json=props,
        created_at=datetime.utcnow(),
    )
    db.session.add(ev)



def _curr_owner_ctx(curriculum_id: str):
    """
    Returns:
      my_row: CurriculumOwner row for current_user (or None)
      owner_user_id: user_id of the largest shareholder (or None)
      is_owner: bool (current_user is largest shareholder)
    Tie-break: highest shares, then earliest created_at, then lowest id.
    """
    my_row = (CurriculumOwner.query
              .filter_by(curriculum_id=curriculum_id, user_id=current_user.id)
              .one_or_none())

    top = (CurriculumOwner.query
           .filter_by(curriculum_id=curriculum_id)
           .order_by(CurriculumOwner.shares.desc(),
                     CurriculumOwner.created_at.asc(),
                     CurriculumOwner.id.asc())
           .first())

    owner_user_id = top.user_id if top else None
    is_owner = bool(owner_user_id and owner_user_id == current_user.id)

    return my_row, owner_user_id, is_owner


def _require_curriculum_analytics(curriculum_id: str):
    my_row, _, _ = _curr_owner_ctx(curriculum_id)
    if not my_row or my_row.shares <= 0 or not my_row.can_view_analytics:
        abort(403)
    return my_row


def _require_curriculum_edit(curriculum_id: str):
    my_row, _, _ = _curr_owner_ctx(curriculum_id)
    if not my_row or my_row.shares <= 0 or not my_row.can_edit:
        abort(403)
    return my_row


def _require_curriculum_manage(curriculum_id: str):
    """
    Manage = current largest shareholder.
    """
    my_row, _, is_owner = _curr_owner_ctx(curriculum_id)
    if not my_row or my_row.shares <= 0 or not is_owner:
        abort(403)
    return my_row

def _exchange_escrow_account() -> AccessAccount:
    # Single system escrow for the exchange
    return get_or_create_system_account("exchange_escrow")


def _mint_curriculum_shares(curriculum_id: str, to_user_id: str, qty: int):
    if qty <= 0:
        raise ValueError("qty must be positive")

    sh = get_curriculum_share_asset(curriculum_id)
    treasury = get_or_create_system_account("treasury")
    to_wallet = get_or_create_user_wallet(to_user_id)

    # Move shares from treasury -> user (mint source is treasury)
    post_access_txn(
        event_type="mint_curriculum_shares",
        idempotency_key=f"mint:{curriculum_id}:{to_user_id}:{qty}",
        actor_user_id=to_user_id,
        context_type="curriculum",
        context_id=curriculum_id,
        memo_json={"qty": qty},
        entries=[
            EntrySpec(account_id=treasury.id, asset_id=sh.id, delta=-qty, entry_type="mint"),
            EntrySpec(account_id=to_wallet.id, asset_id=sh.id, delta=+qty, entry_type="mint"),
        ],
        forbid_user_overdraft=False,  # treasury can go negative if you treat it as “issuer”
    )

    # cache
    _bump_curr_owner(curriculum_id, to_user_id, +qty)


@bp.post("/app/curriculum/<curriculum_code>/shares/mint")
@login_required
def mint_curriculum_shares(curriculum_code: str):
    cur = Curriculum.query.filter_by(code=curriculum_code).one_or_none()
    if not cur:
        abort(404)

    _require_curriculum_manage(cur.id)

    to_user_id = (request.form.get("user_id") or "").strip()
    qty = int(request.form.get("qty") or 0)
    if not to_user_id or qty <= 0:
        abort(400)

    _mint_curriculum_shares(cur.id, to_user_id, qty)
    db.session.commit()
    return redirect(url_for("main.curriculum_view", curriculum_code=cur.code))



def _bump_curr_owner(curriculum_id: str, user_id: str, delta_shares: int):
    row = CurriculumOwner.query.filter_by(curriculum_id=curriculum_id, user_id=user_id).one_or_none()
    if row is None:
        row = CurriculumOwner(
            curriculum_id=curriculum_id,
            user_id=user_id,
            shares=0,
            can_view_analytics=False,
            can_edit=False,
            can_manage_ownership=False,
        )
        db.session.add(row)
        db.session.flush()

    row.shares = int(row.shares) + int(delta_shares)
    if row.shares < 0:
        # should never happen if ledger + escrow are correct
        raise ValueError("CurriculumOwner shares would go negative")

    # Optional: if someone now has shares>0, you might want defaults
    # (personally I'd leave perms false unless explicitly granted)

    db.session.flush()


def _payout_curriculum_wallet(curriculum_id: str, *, max_ticks: int | None = None):
    """
    Distributes AN from the curriculum wallet to shareholders pro-rata.

    max_ticks: optional cap so a single run doesn't drain everything (nice for testing).
    """
    cur = Curriculum.query.get(curriculum_id)
    if not cur or not cur.wallet_account_id:
        return 0

    an = get_an_asset()

    # Current wallet balance (ticks)
    wallet_ticks = (
        AccessBalance.query
        .filter_by(account_id=cur.wallet_account_id, asset_id=an.id)
        .with_entities(AccessBalance.balance)
        .scalar()
        or 0
    )
    wallet_ticks = int(wallet_ticks)
    if wallet_ticks <= 0:
        return 0

    if max_ticks is not None:
        wallet_ticks = min(wallet_ticks, int(max_ticks))
        if wallet_ticks <= 0:
            return 0

    owners = (CurriculumOwner.query
              .filter_by(curriculum_id=curriculum_id)
              .filter(CurriculumOwner.shares > 0)
              .all())

    total_shares = sum(int(o.shares) for o in owners)
    if total_shares <= 0:
        return 0

    # Pro-rata allocation with remainder left in wallet (avoids rounding hell)
    entries = []
    distributed = 0

    for o in owners:
        user_wallet = get_or_create_user_wallet(o.user_id)
        amt = (wallet_ticks * int(o.shares)) // total_shares
        if amt <= 0:
            continue
        distributed += amt
        entries.append(EntrySpec(account_id=user_wallet.id, asset_id=an.id, delta=+amt, entry_type="payout"))

    if distributed <= 0:
        return 0

    # Wallet is the source
    entries.insert(0, EntrySpec(
        account_id=cur.wallet_account_id,
        asset_id=an.id,
        delta=-distributed,
        entry_type="payout",
    ))

    # idempotency: one payout per curriculum per minute (simple + safe)
    # (If you want “per hour/day”, change the key granularity.)
    # key = f"payout:{curriculum_id}:{datetime.utcnow().strftime('%Y%m%d%H%M')}"


    today_local = datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    key = f"lesson_daily_reward:{current_user.id}:{curriculum_id}:{today_local}"

    post_access_txn(
        event_type="curriculum_wallet_payout",
        idempotency_key=key,
        actor_user_id=None,
        context_type="curriculum",
        context_id=curriculum_id,
        memo_json={"wallet_ticks_before": int(wallet_ticks), "distributed_ticks": int(distributed)},
        entries=entries,
        forbid_user_overdraft=False,  # wallet should have funds, but keep consistent with system accounts
    )

    log_event(
        "curriculum_wallet_payout",
        entity_type="curriculum",
        entity_id=curriculum_id,
        props={"distributed_ticks": distributed, "distributed_an": distributed / AN_SCALE},
    )

    db.session.commit()
    return distributed


@bp.post("/app/curriculum/<curriculum_code>/payout")
@login_required
def payout_curriculum(curriculum_code: str):
    cur = Curriculum.query.filter_by(code=curriculum_code).one_or_none()
    if not cur:
        abort(404)

    # Optional: restrict to managers/owners:
    _require_curriculum_manage(cur.id)

    _payout_curriculum_wallet(cur.id)
    flash("Payout executed.", "success")
    return redirect(url_for("main.curriculum_view", curriculum_code=curriculum_code))




@bp.post("/app/curriculum/<curriculum_code>/orders/place")
@login_required
def place_curriculum_order(curriculum_code: str):
    cur = Curriculum.query.filter_by(code=curriculum_code).one_or_none()
    if not cur:
        abort(404)

    side = (request.form.get("side") or "").strip().lower()   # bid / ask
    price_an = float(request.form.get("price_an") or 0)       # human AN
    qty = int(request.form.get("qty") or 0)

    if side not in ("bid", "ask") or qty <= 0 or price_an <= 0:
        abort(400)

    an = get_an_asset()
    sh = get_curriculum_share_asset(cur.id)

    price_ticks = int(round(price_an * AN_SCALE))  # ticks per 1 share
    escrow = _exchange_escrow_account()
    me = get_or_create_user_wallet(current_user.id)

    # escrow cost upfront so orders are always “funded”
    if side == "bid":
        # lock AN = price * qty
        cost_ticks = price_ticks * qty
        try:
            post_access_txn(
                event_type="order_escrow",
                idempotency_key=f"escrow:bid:{current_user.id}:{cur.id}:{_uuid()}",
                actor_user_id=current_user.id,
                context_type="curriculum",
                context_id=cur.id,
                memo_json={"side": "bid", "price_ticks": price_ticks, "qty": qty},
                entries=[
                    EntrySpec(account_id=me.id, asset_id=an.id, delta=-cost_ticks, entry_type="escrow"),
                    EntrySpec(account_id=escrow.id, asset_id=an.id, delta=+cost_ticks, entry_type="escrow"),
                ],
            )
        except InsufficientFunds as e:
            db.session.rollback()
            flash(str(e), "error")
            return redirect(url_for("main.curriculum_view", curriculum_code=curriculum_code))

    else:
        # lock shares = qty
        post_access_txn(
            event_type="order_escrow",
            idempotency_key=f"escrow:ask:{current_user.id}:{cur.id}:{_uuid()}",
            actor_user_id=current_user.id,
            context_type="curriculum",
            context_id=cur.id,
            memo_json={"side": "ask", "price_ticks": price_ticks, "qty": qty},
            entries=[
                EntrySpec(account_id=me.id, asset_id=sh.id, delta=-qty, entry_type="escrow"),
                EntrySpec(account_id=escrow.id, asset_id=sh.id, delta=+qty, entry_type="escrow"),
            ],
        )

        # ownership cache: seller shares decrease immediately (shares moved to escrow)
        _bump_curr_owner(cur.id, current_user.id, -qty)

    o = CurriculumOrder(
        id=_uuid(),
        curriculum_id=cur.id,
        user_id=current_user.id,
        side=side,
        price_ticks=price_ticks,
        qty_initial=qty,
        qty_remaining=qty,
        status="open",
    )
    db.session.add(o)
    db.session.commit()

    _try_match_orders(cur.id)  # best-effort matching
    return redirect(url_for("main.curriculum_view", curriculum_code=cur.code))


@bp.post("/app/orders/<order_id>/cancel")
@login_required
def cancel_order(order_id: str):
    o = CurriculumOrder.query.filter_by(id=order_id).one_or_none()
    if not o:
        abort(404)
    if o.user_id != current_user.id:
        abort(403)
    if o.status != "open" or o.qty_remaining <= 0:
        return redirect(url_for("main.curriculum_view", curriculum_code=Curriculum.query.get(o.curriculum_id).code))

    an = get_an_asset()
    sh = get_curriculum_share_asset(o.curriculum_id)
    escrow = _exchange_escrow_account()
    me = get_or_create_user_wallet(current_user.id)

    rem = int(o.qty_remaining)

    # release remaining escrow
    if o.side == "bid":
        refund_ticks = int(o.price_ticks) * rem
        post_access_txn(
            event_type="order_cancel_refund",
            idempotency_key=f"cancel:bid:{o.id}:{_uuid()}",
            actor_user_id=current_user.id,
            context_type="order",
            context_id=o.id,
            memo_json={"side": "bid", "price_ticks": o.price_ticks, "qty": rem},
            entries=[
                EntrySpec(account_id=escrow.id, asset_id=an.id, delta=-refund_ticks, entry_type="escrow_release"),
                EntrySpec(account_id=me.id, asset_id=an.id, delta=+refund_ticks, entry_type="escrow_release"),
            ],
        )
    else:
        post_access_txn(
            event_type="order_cancel_refund",
            idempotency_key=f"cancel:ask:{o.id}:{_uuid()}",
            actor_user_id=current_user.id,
            context_type="order",
            context_id=o.id,
            memo_json={"side": "ask", "price_ticks": o.price_ticks, "qty": rem},
            entries=[
                EntrySpec(account_id=escrow.id, asset_id=sh.id, delta=-rem, entry_type="escrow_release"),
                EntrySpec(account_id=me.id, asset_id=sh.id, delta=+rem, entry_type="escrow_release"),
            ],
        )
        # shares returned from escrow back to seller
        _bump_curr_owner(o.curriculum_id, current_user.id, +rem)

    o.qty_remaining = 0
    o.status = "canceled"
    o.canceled_at = datetime.utcnow()
    db.session.commit()

    cur = Curriculum.query.get(o.curriculum_id)
    return redirect(url_for("main.curriculum_view", curriculum_code=cur.code))


def _try_match_orders(curriculum_id: str, max_fills: int = 50):
    an = get_an_asset()
    sh = get_curriculum_share_asset(curriculum_id)
    escrow = _exchange_escrow_account()

    fills = 0
    while fills < max_fills:
        best_bid = (CurriculumOrder.query
                    .filter_by(curriculum_id=curriculum_id, side="bid", status="open")
                    .filter(CurriculumOrder.qty_remaining > 0)
                    .order_by(CurriculumOrder.price_ticks.desc(), CurriculumOrder.created_at.asc())
                    .first())

        best_ask = (CurriculumOrder.query
                    .filter_by(curriculum_id=curriculum_id, side="ask", status="open")
                    .filter(CurriculumOrder.qty_remaining > 0)
                    .order_by(CurriculumOrder.price_ticks.asc(), CurriculumOrder.created_at.asc())
                    .first())

        if not best_bid or not best_ask:
            break

        # cross?
        if int(best_bid.price_ticks) < int(best_ask.price_ticks):
            break

        qty = min(int(best_bid.qty_remaining), int(best_ask.qty_remaining))

        buyer_wallet = get_or_create_user_wallet(best_bid.user_id)
        seller_wallet = get_or_create_user_wallet(best_ask.user_id)

        # Choose execution price. Simplest: take the older order’s price (maker-taker-ish).
        # Here: execute at ask price.
        exec_price_ticks = int(best_ask.price_ticks)
        cost_ticks = exec_price_ticks * qty

        # Settlement:
        # - shares: escrow -> buyer
        # - AN: escrow -> seller
        post_access_txn(
            event_type="order_fill",
            idempotency_key=f"fill:{best_bid.id}:{best_ask.id}:{fills}",
            actor_user_id=None,
            context_type="curriculum",
            context_id=curriculum_id,
            memo_json={
                "bid_id": best_bid.id,
                "ask_id": best_ask.id,
                "qty": qty,
                "exec_price_ticks": exec_price_ticks,
            },
            entries=[
                EntrySpec(account_id=escrow.id, asset_id=sh.id, delta=-qty, entry_type="settle"),
                EntrySpec(account_id=buyer_wallet.id, asset_id=sh.id, delta=+qty, entry_type="settle"),

                EntrySpec(account_id=escrow.id, asset_id=an.id, delta=-cost_ticks, entry_type="settle"),
                EntrySpec(account_id=seller_wallet.id, asset_id=an.id, delta=+cost_ticks, entry_type="settle"),
            ],
        )

        log_event(
            "curriculum_trade_fill",
            entity_type="curriculum",
            entity_id=curriculum_id,
            props={
                "bid_id": best_bid.id,
                "ask_id": best_ask.id,
                "qty": qty,
                "exec_price_ticks": exec_price_ticks,
                "exec_price_an": exec_price_ticks / AN_SCALE,
            },
        )


        # Ownership cache follows the shares leaving escrow:
        _bump_curr_owner(curriculum_id, best_bid.user_id, +qty)
        # seller already lost shares when placing ask (to escrow), so no further decrement here

        best_bid.qty_remaining -= qty
        best_ask.qty_remaining -= qty

        if best_bid.qty_remaining == 0:
            best_bid.status = "filled"
            best_bid.filled_at = datetime.utcnow()

            # refund any over-escrow if bid price > exec price
            if int(best_bid.price_ticks) > exec_price_ticks:
                over = (int(best_bid.price_ticks) - exec_price_ticks) * int(best_bid.qty_initial)
                if over > 0:
                    bw = get_or_create_user_wallet(best_bid.user_id)
                    post_access_txn(
                        event_type="order_bid_over_refund",
                        idempotency_key=f"refund_over:{best_bid.id}:{_uuid()}",
                        actor_user_id=best_bid.user_id,
                        context_type="order",
                        context_id=best_bid.id,
                        memo_json={"over_ticks": over},
                        entries=[
                            EntrySpec(account_id=escrow.id, asset_id=an.id, delta=-over, entry_type="escrow_release"),
                            EntrySpec(account_id=bw.id, asset_id=an.id, delta=+over, entry_type="escrow_release"),
                        ],
                    )

        if best_ask.qty_remaining == 0:
            best_ask.status = "filled"
            best_ask.filled_at = datetime.utcnow()

        db.session.commit()
        fills += 1




@bp.post("/lesson/heartbeat")
@login_required
def lesson_heartbeat():
    data = request.get_json(force=True) or {}
    attempt_id = data.get("attempt_id")
    block_id = data.get("lesson_block_id")
    delta = int(data.get("seconds_delta") or 0)

    # clamp delta so clients can’t spam gigantic time
    delta = max(0, min(delta, 30))
    if not attempt_id or delta <= 0:
        return jsonify(ok=True)

    attempt = LessonAttempt.query.get_or_404(attempt_id)

    # security
    if attempt.user_id != current_user.id:
        return jsonify(ok=True)

    # no time accrual after completion
    if attempt.completed_at:
        return jsonify(ok=True)

    now = datetime.utcnow()
    attempt.last_heartbeat_at = now
    attempt.seconds_spent_total = (attempt.seconds_spent_total or 0) + delta

    from app.models import CurriculumDayActivity

    today = datetime.utcnow().date()

    if attempt.curriculum_id:
        row = (CurriculumDayActivity.query
               .filter_by(curriculum_id=attempt.curriculum_id, user_id=current_user.id, day=today)
               .one_or_none())
        if row is None:
            row = CurriculumDayActivity(
                curriculum_id=attempt.curriculum_id,
                user_id=current_user.id,
                day=today,
                seconds_spent_total=0,
            )
            db.session.add(row)

        row.seconds_spent_total += delta

    # OPTIONAL: sample an analytics event occasionally (keep it cheap)
    # Here: only log when block_id exists AND about once per minute
    if block_id and now.second < delta:
        # log_event(
        #     event_type="lesson_time_bucket",
        #     entity_type="lesson_attempt",
        #     entity_id=attempt.id,
        #     props={"lesson_block_id": block_id, "seconds_delta": delta},
        # )
        log_event(
            "lesson_time_bucket",
            entity_type="lesson_attempt",
            entity_id=attempt.id,
            props={"lesson_block_id": block_id, "seconds_delta": delta},
        )

    db.session.commit()
    return jsonify(ok=True)




@bp.get("/app/lessons/continue")
@login_required
def lessons_continue():
    from app.models import LessonAttempt, Lesson

    # Most recent *in-progress* attempt wins
    attempt = (
        LessonAttempt.query
        .filter_by(user_id=current_user.id)
        .filter(LessonAttempt.completed_at.is_(None))
        .order_by(
            desc(LessonAttempt.last_heartbeat_at),
            desc(LessonAttempt.started_at),
        )
        .first()
    )

    if attempt:
        lesson = Lesson.query.get(attempt.lesson_id)
        if lesson:
            return redirect(url_for("main.lesson_page", lesson_code=lesson.code, src="continue"))

    flash("No active lesson to continue. Pick a lesson to start.", "info")
    return redirect(url_for("main.lessons_index"))





@bp.get("/app/trivia")
@login_required
def trivia_page():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    term = (request.args.get("q") or "").strip()

    # Base: random quiz block
    q = (
        db.session.query(LessonBlock, Lesson)
        .join(Lesson, Lesson.id == LessonBlock.lesson_id)
        .filter(LessonBlock.type == "quiz_mcq")
        # pick your discovery rule:
        # .filter(Lesson.visibility == "public")
    )

    # "Subject" v1: just search lesson fields
    if term:
        like = f"%{term}%"
        q = q.filter(
            (Lesson.title.ilike(like)) |
            (Lesson.description.ilike(like)) |
            (Lesson.code.ilike(like))
        )

    row = q.order_by(func.random()).first()

    if not row:
        flash("No trivia questions found for that search.", "info")
        return render_template("trivia/page.html", block=None, lesson=None, q=term)

    block, lesson = row

    log_event("trivia_question_served", "lesson_block", block.id, {
        "lesson_id": lesson.id,
        "lesson_code": lesson.code,
        "q": term,
    })
    db.session.commit()

    return render_template("trivia/page.html", block=block, lesson=lesson, q=term)


@bp.post("/app/trivia/answer")
@login_required
def trivia_answer():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    block_id = (request.form.get("block_id") or "").strip()
    choice_raw = request.form.get("choice_index")

    if not block_id or choice_raw is None:
        abort(400)

    block = LessonBlock.query.filter_by(id=block_id, type="quiz_mcq").one_or_none()
    if not block:
        abort(404)

    try:
        choice_index = int(choice_raw)
    except ValueError:
        abort(400)

    correct_index = int(block.payload_json.get("answer_index", -1))
    is_correct = (choice_index == correct_index)

    # log either way
    log_event("trivia_answer_submitted", "lesson_block", block.id, {
        "choice_index": choice_index,
        "is_correct": bool(is_correct),
    })

    payout_ticks = 0
    if is_correct:
        # Pays at least 1 tick, plus Exp(mean=10 ticks)
        extra = int(random.expovariate(1 / 10.0))  # expected ~10, can be 0
        payout_ticks = 1 + max(0, extra)

        today = datetime.utcnow().date().isoformat()
        key = f"trivia_reward:{current_user.id}:{block.id}:{today}"

        issuer = get_or_create_system_account("rewards_pool")
        user_wallet = get_or_create_user_wallet(current_user.id)
        an = get_an_asset()

        post_access_txn(
            event_type="trivia_correct_reward",
            idempotency_key=key,
            actor_user_id=current_user.id,
            context_type="lesson_block",
            context_id=block.id,
            memo_json={"payout_ticks": payout_ticks},
            entries=[
                EntrySpec(account_id=issuer.id, asset_id=an.id, delta=-payout_ticks, entry_type="mint"),
                EntrySpec(account_id=user_wallet.id, asset_id=an.id, delta=+payout_ticks, entry_type="mint"),
            ],
        )

    db.session.commit()

    flash(("Correct! " if is_correct else "Nope. ") + (f"+{payout_ticks} ticks" if payout_ticks else ""), "success" if is_correct else "error")
    return redirect(url_for("main.trivia_page"))


@bp.post("/app/trivia/bad")
@login_required
def trivia_bad_vote():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    block_id = (request.form.get("block_id") or "").strip()
    if not block_id:
        abort(400)

    # Phase 1: store as an analytics event (no schema)
    log_event("trivia_bad_question_voted", "lesson_block", block_id, {})
    db.session.commit()

    flash("Logged. Thank you for protecting the public from cursed trivia.", "info")
    return redirect(url_for("main.trivia_page"))



