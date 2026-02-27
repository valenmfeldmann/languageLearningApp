# app/appui/routes.py
from flask import render_template, url_for, abort, request, current_app
from flask_login import login_required, current_user

from . import bp  # 👈 THIS is the missing line

from app.models import Subscription, Plan, Curriculum, CurriculumItem
from app.billing.access import has_access, get_credit_balance_cents
from app.billing.pricing import buddy_discount_multiplier
from .portfolio import get_user_portfolio_view
from .ledger_public import query_public_ledger, get_single_txn, ledger_filter_options
from flask import redirect, flash, jsonify
from sqlalchemy import func
import random
from datetime import datetime
from flask import redirect, flash, request
from flask_login import current_user
from app.billing.access import has_access

from app.models import Lesson, LessonBlock, LessonSubject, TriviaAnswer, TriviaBadVote
from app.access_ledger.service import (
    get_or_create_system_account, get_or_create_user_wallet,
    get_an_asset, post_access_txn, EntrySpec,
    get_user_level_multiplier
)
from .. import db
from datetime import datetime, timedelta



@login_required
def _get_subscription():
    return Subscription.query.filter_by(user_id=current_user.id).one_or_none()


@bp.before_request
def require_paid_access_for_appui():
    # Allow logged-out users to see public pages (like public ledger).
    if not current_user.is_authenticated:
        return None

    # Allow these pages even without subscription:
    allowed_endpoints = {
        "appui.account",       # lets them see "Has access: NO"
        "appui.public_ledger", # if you want ledger public
        "appui.ledger_txn",    # public txn view
    }

    if request.endpoint in allowed_endpoints:
        return None

    if not has_access(current_user):
        flash("No active subscription. Please choose a plan.", "warning")
        return redirect(url_for("billing.pricing"))



@bp.get("/account")
@login_required
def account():
    # 1. Fetch the global requirement setting
    sub_required = current_app.config.get("REQUIRE_SUBSCRIPTION", True)

    sub = _get_subscription()

    buddy_count = int(getattr(current_user, "active_buddy_count", 0) or 0)
    mult = float(buddy_discount_multiplier(buddy_count))
    credit_balance_cents = int(get_credit_balance_cents(current_user.id) or 0)

    # If you want to show plan name:
    plan = None
    if sub and sub.plan_id:
        plan = Plan.query.get(sub.plan_id)

    from app.models import BuddyLink, User

    me = current_user.id

    outgoing_pending = (
        db.session.query(BuddyLink, User)
        .join(User, User.id == BuddyLink.addressee_id)
        .filter(
            BuddyLink.requester_id == me,
            BuddyLink.status == "pending",
        )
        .order_by(BuddyLink.created_at.desc())
        .all()
    )

    incoming_pending = (
        db.session.query(BuddyLink, User)
        .join(User, User.id == BuddyLink.requester_id)
        .filter(
            BuddyLink.addressee_id == me,
            BuddyLink.status == "pending",
        )
        .order_by(BuddyLink.created_at.desc())
        .all()
    )

    return render_template(
        "app/account.html",
        sub=sub,
        plan=plan,
        has_access=has_access(current_user),
        require_subscription=sub_required,  # Crucial for the template toggle
        buddy_count=buddy_count,
        multiplier=mult,
        credit_balance_cents=credit_balance_cents,
        outgoing_pending=outgoing_pending,
        incoming_pending=incoming_pending,
    )


@bp.get("/portfolio")
@login_required
def portfolio():
    pv = get_user_portfolio_view(current_user.id)
    return render_template("app/portfolio.html", **pv)



@bp.get("/ledger")
def public_ledger():
    viewer_id = current_user.id if current_user.is_authenticated else None

    only_mine = request.args.get("mine") == "1"
    event_type = request.args.get("event_type") or None
    context_type = request.args.get("context_type") or None
    asset_code = request.args.get("asset") or None

    # Basic sanity to avoid absurd limits
    try:
        limit = int(request.args.get("limit") or 100)
    except ValueError:
        limit = 100
    limit = max(10, min(500, limit))

    rows = query_public_ledger(
        viewer_user_id=viewer_id,
        limit=limit,
        only_my_txns=only_mine,
        event_type=event_type,
        context_type=context_type,
        asset_code=asset_code,
    )

    opts = ledger_filter_options()

    return render_template(
        "app/ledger.html",
        rows=rows,
        only_mine=only_mine,
        logged_in=current_user.is_authenticated,
        limit=limit,
        event_type=event_type,
        context_type=context_type,
        asset_code=asset_code,
        opts=opts,
    )


@bp.get("/ledger/txn/<txn_id>")
def ledger_txn(txn_id):
    viewer_id = current_user.id if current_user.is_authenticated else None
    txn = get_single_txn(txn_id, viewer_id)
    if not txn:
        abort(404)
    return render_template("app/ledger_txn.html", txn=txn)




# @bp.get("/trivia")
# @login_required
# def trivia_page():
#     if not has_access(current_user):
#         return redirect(url_for("billing.pricing"))
#
#     search_text = (request.args.get("q") or "").strip()
#     subject_code = (request.args.get("subject") or "").strip() or None
#
#     # Fetch subjects for the filter dropdown
#     subjects = (LessonSubject.query
#                 .filter(LessonSubject.active.is_(True))
#                 .order_by(LessonSubject.name.asc())
#                 .all())
#
#     exclude_id = request.args.get("exclude")  # Capture the current question ID
#
#     # 1. Fetch all eligible blocks (ignoring randomization for a moment)
#     # We maintain your joins to ensure content is from published curricula
#     q = (
#         db.session.query(LessonBlock, Lesson)
#         .join(Lesson, Lesson.id == LessonBlock.lesson_id)
#         .join(CurriculumItem, CurriculumItem.lesson_id == Lesson.id)
#         .join(Curriculum, Curriculum.id == CurriculumItem.curriculum_id)
#         .filter(
#             LessonBlock.type == "quiz_mcq",
#             Curriculum.is_published == True
#         )
#     )
#
#     if exclude_id:
#         q = q.filter(LessonBlock.id != exclude_id)
#
#     if subject_code:
#         q = q.filter(Lesson.subject_code == subject_code)
#
#     if search_text:
#         like = f"%{search_text}%"
#         q = q.filter(
#             (Lesson.title.ilike(like)) |
#             (Lesson.description.ilike(like)) |
#             (Lesson.code.ilike(like))
#         )
#
#     all_eligible = q.all()
#
#     if not all_eligible:
#         flash("No trivia questions found for that filter.", "info")
#         return render_template("trivia/page.html", block=None, lesson=None, subjects=subjects,
#                                subject_code=subject_code, q=search_text)
#
#     # 2. Fetch user performance to calculate Mastery weights
#     # We get the user's answers to calculate how many times they've gotten each question right recently
#     user_answers = (TriviaAnswer.query
#                     .filter_by(user_id=current_user.id)
#                     .order_by(TriviaAnswer.created_at.desc())
#                     .all())
#
#     # Calculate streaks: consecutive correct answers from the most recent attempt
#     mastery_map = {}  # block_id -> streak_count
#     for a in user_answers:
#         if a.lesson_block_id not in mastery_map:
#             mastery_map[a.lesson_block_id] = {"count": 0, "reset": False}
#
#         if not mastery_map[a.lesson_block_id]["reset"]:
#             if a.is_correct:
#                 mastery_map[a.lesson_block_id]["count"] += 1
#             else:
#                 # User got it wrong at some point; stop counting the streak
#                 mastery_map[a.lesson_block_id]["reset"] = True
#
#     # 3. Incorporate your existing "Bad Question" protection
#     # We still want to down-weight questions the community has voted as "bad"
#     bad_counts_query = (
#         db.session.query(
#             TriviaBadVote.lesson_block_id,
#             func.count(TriviaBadVote.id).label("count")
#         )
#         .group_by(TriviaBadVote.lesson_block_id)
#         .all()
#     )
#     bad_map = {row.lesson_block_id: row.count for row in bad_counts_query}
#
#     # 4. Calculate Final Weights
#     population = []
#     weights = []
#     alpha = 0.7  # For bad votes
#
#     for block, lesson in all_eligible:
#
#         # Mastery Decay: 0.25^n (where n is the correct streak)
#         streak = mastery_map.get(block.id, {"count": 0})["count"]
#         mastery_weight = 0.25 ** streak
#
#
#         # Bad Vote Decay
#         bad_count = bad_map.get(block.id, 0)
#         bad_weight = max(current_app.config.get("MIN_WEIGHT", 1e-6), 2.718 ** (-alpha * bad_count))
#
#         # Combine weights
#         final_weight = mastery_weight * bad_weight
#
#         population.append((block, lesson))
#         weights.append(final_weight)
#
#         # Temporary Debug Logging
#         print(f"DEBUG: Question: {lesson.title[:20]}... | Correct Streak: {streak} | Weight: {final_weight:.4f}")
#
#
#     # 5. Perform Weighted Selection
#     import random
#     selected_row = random.choices(population, weights=weights, k=1)[0]
#     block, lesson = selected_row
#
#     return render_template("trivia/page.html", block=block, lesson=lesson,
#                            subjects=subjects, subject_code=subject_code, q=search_text)


@bp.get("/trivia")
@login_required
def trivia_page():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    # 1. Grab parameters from the URL (The Launcher sends these)
    search_text = (request.args.get("q") or "").strip()
    subject_code = (request.args.get("subject") or "").strip() or None
    back_to_lesson = request.args.get("from_lesson")
    exclude_id = request.args.get("exclude")

    # RESTORED: Fetch subjects for the filter dropdown
    subjects = (LessonSubject.query
                .filter(LessonSubject.active.is_(True))
                .order_by(LessonSubject.name.asc())
                .all())

    # 2. Build the base query for published MCQs
    base_query = (
        db.session.query(LessonBlock, Lesson)
        .join(Lesson, Lesson.id == LessonBlock.lesson_id)
        .join(CurriculumItem, CurriculumItem.lesson_id == Lesson.id)
        .join(Curriculum, Curriculum.id == CurriculumItem.curriculum_id)
        .filter(LessonBlock.type == "quiz_mcq", Curriculum.is_published == True)
    )

    if exclude_id:
        base_query = base_query.filter(LessonBlock.id != exclude_id)

    # 3. Build the query
    active_query = base_query

    if search_text:
        # If we have a search string, let's be flexible with the subject
        active_query = active_query.filter(
            (Lesson.title.ilike(f"%{search_text}%")) |
            (Lesson.code.ilike(f"%{search_text}%"))
        )

        # Only filter by subject if it's a valid code we recognize
        if subject_code:
            valid_subject = LessonSubject.query.filter_by(code=subject_code).first()
            if valid_subject:
                active_query = active_query.filter(Lesson.subject_code == subject_code)
            # If not valid, we just ignore the subject and rely on the search_text
    elif subject_code:
        # If ONLY a subject is provided, it must be valid
        active_query = active_query.filter(Lesson.subject_code == subject_code)

    all_eligible = active_query.all()




    # 4. TRIPLE-CHECK Fallback: Only flash if we REALLY have nothing
    if not all_eligible:
        if search_text or subject_code:
            # Broaden search to just the subject before failing entirely
            all_eligible = base_query.filter(Lesson.subject_code == subject_code).limit(50).all()

            if all_eligible:
                # Don't flash an error, just a helpful info message
                flash(f"Showing all {subject_code} questions.", "info")
            else:
                flash("No specific matches found. Showing general trivia.", "info")
                all_eligible = base_query.limit(50).all()




    # 5. Safety Valve: If the database is completely empty, prevent a crash
    if not all_eligible:
        return render_template("trivia/page.html",
                               block=None, lesson=None, subjects=subjects,
                               subject_code=subject_code, q=search_text,
                               back_to_lesson=back_to_lesson)

    # 6. Fetch user performance to calculate Mastery weights
    user_answers = (TriviaAnswer.query
                    .filter_by(user_id=current_user.id)
                    .order_by(TriviaAnswer.created_at.desc())
                    .all())

    mastery_map = {}
    for a in user_answers:
        if a.lesson_block_id not in mastery_map:
            mastery_map[a.lesson_block_id] = {"count": 0, "reset": False}
        if not mastery_map[a.lesson_block_id]["reset"]:
            if a.is_correct:
                mastery_map[a.lesson_block_id]["count"] += 1
            else:
                mastery_map[a.lesson_block_id]["reset"] = True

    # 7. Bad Question Protection
    bad_counts_query = (
        db.session.query(TriviaBadVote.lesson_block_id, func.count(TriviaBadVote.id).label("count"))
        .group_by(TriviaBadVote.lesson_block_id).all()
    )
    bad_map = {row.lesson_block_id: row.count for row in bad_counts_query}

    # 8. Calculate Final Weights
    population = []
    weights = []
    alpha = 0.7

    for block, lesson in all_eligible:
        streak = mastery_map.get(block.id, {"count": 0})["count"]
        mastery_weight = 0.25 ** streak
        bad_count = bad_map.get(block.id, 0)
        bad_weight = max(current_app.config.get("MIN_WEIGHT", 1e-6), 2.718 ** (-alpha * bad_count))

        final_weight = mastery_weight * bad_weight
        population.append((block, lesson))
        weights.append(final_weight)

        print(f"DEBUG: Question: {lesson.title[:20]}... | Streak: {streak} | Weight: {final_weight:.4f}")

    # 9. Perform Weighted Selection
    selected_row = random.choices(population, weights=weights, k=1)[0]
    block, lesson = selected_row

    return render_template("trivia/page.html",
                           block=block, lesson=lesson, subjects=subjects,
                           subject_code=subject_code, q=search_text,
                           back_to_lesson=back_to_lesson)





# WAS IN MAIN!
@bp.post("/app/trivia/answer")
@login_required
def trivia_answer():
    back_to_lesson = request.form.get("from_lesson")
    subject_code = request.form.get("subject")
    search_text = request.form.get("q")

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

    # --- NEW: Record the performance for Weighted Sampling ---
    ans = TriviaAnswer(
        user_id=current_user.id,
        lesson_block_id=block.id,
        chosen_index=choice_index,
        is_correct=bool(is_correct)
    )
    db.session.add(ans)

    payout_ticks = 0
    if is_correct:
        update_user_streak(current_user)
        db.session.add(current_user)  # Ensure the user object is marked for saving

        if is_correct:
            # 1. Detect if it's a mobile device
            user_agent = request.headers.get('User-Agent', '').lower()
            is_mobile = any(word in user_agent for word in ['mobile', 'android', 'iphone'])

            # 2. Logic: Same expected value (~11 ticks), but capped for UI safety
            if is_mobile:
                # We use a tighter distribution that still skews high occasionally
                # Capping at 20 ticks prevents layout breaks in the userpill
                raw_extra = int(random.expovariate(1 / 10.0))
                payout_ticks = 1 + min(19, raw_extra)
            else:
                # Standard uncapped desktop logic
                extra = int(random.expovariate(1 / 10.0))
                payout_ticks = 1 + max(0, extra)


        # today = datetime.utcnow().date().isoformat()
        # key = f"trivia_reward:{current_user.id}:{block.id}:{today}"

        import uuid
        key = f"trivia_reward:{current_user.id}:{block.id}:{uuid.uuid4().hex}"

        # import time
        # # Add a timestamp so the key is always unique for testing
        # key = f"trivia_reward:{current_user.id}:{block.id}:{today}:{time.time()}"


        issuer = get_or_create_system_account("rewards_pool")
        user_wallet = get_or_create_user_wallet(current_user.id)
        an = get_an_asset()

        # # 4. FLASH MESSAGES (The Confetti Trigger)
        # flash("Correct!", "success")
        # # This category "reward:..." is what triggers the celebration in base.html
        # flash(f"+{payout_ticks} ticks", f"reward:{payout_ticks}")

        flash("Correct! " + f"+{payout_ticks} ticks" if payout_ticks else "", f"reward:{payout_ticks}")

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
    else:
        flash("Nope. Try again!", "error")
        # flash(("Nope. ") + (f"+{payout_ticks} ticks" if payout_ticks else ""))

    db.session.commit()


    # FORCE a fresh read of the balance after the commit
    from app.access_ledger.service import get_user_an_balance_ticks, AN_SCALE
    new_ticks = get_user_an_balance_ticks(current_user.id)  #
    new_an = new_ticks / AN_SCALE


    # If it's an AJAX request (XHR)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({
            "is_correct": bool(is_correct),
            "payout": payout_ticks,
            "new_balance_an": f"{new_an:.3f}",
            "current_streak": current_user.current_streak,
        })


    # # flash(("Correct! " if is_correct else "Nope. ") + (f"+{payout_ticks} ticks" if payout_ticks else ""), "success" if is_correct else "error")
    # return redirect(url_for("appui.trivia_page", from_lesson=back_to_lesson))

    # Redir with ALL context preserved
    return redirect(url_for("appui.trivia_page",
                            from_lesson=back_to_lesson,
                            subject=subject_code,
                            q=search_text))





# WAS IN MAIN!!
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
    return redirect(url_for("appui.trivia_page"))








from flask_login import current_user
from app.models import AnalyticsEvent
from app.extensions import db

def log_event(event_name, entity_type=None, entity_id=None, props=None):
    """Utility to log an analytics event with user context."""
    evt = AnalyticsEvent(
        event_type=event_name,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=current_user.id if current_user.is_authenticated else None, # Links the user
        props_json=props or {}
    )
    db.session.add(evt)
    return evt





# @bp.post("/trivia/bad")
# @login_required
# def trivia_bad_vote():
#     if not has_access(current_user):
#         return redirect(url_for("billing.pricing"))
#
#     block_id = (request.form.get("block_id") or "").strip()
#     subject_code = (request.form.get("subject") or "").strip() or None
#     search_text = (request.form.get("q") or "").strip() or None
#
#     if not block_id:
#         abort(400)
#
#     # insert-if-not-exists (one vote per user per block)
#     existing = TriviaBadVote.query.filter_by(user_id=current_user.id, lesson_block_id=block_id).one_or_none()
#     if not existing:
#         db.session.add(TriviaBadVote(
#             user_id=current_user.id,
#             lesson_block_id=block_id,
#             subject_code=subject_code,
#         ))
#         db.session.commit()
#
#     flash("Logged bad-question vote.", "info")
#     return redirect(url_for("appui.trivia_page", subject=subject_code or "", q=search_text or ""))




def update_user_streak(user):
    """Updates the streak based on the current UTC date."""
    today = datetime.utcnow().date()

    # If they already did something today, do nothing
    if user.last_activity_date == today:
        return

        # If their last activity was yesterday, increment the streak
    if user.last_activity_date == today - timedelta(days=1):
        user.current_streak += 1
    else:
        # If they missed a day (or it's their first time), reset to 1
        user.current_streak = 1

    user.last_activity_date = today
