# app/appui/routes.py
from flask import render_template, url_for, abort, request
from flask_login import login_required, current_user

from . import bp  # 👈 THIS is the missing line

from app.models import Subscription, Plan
from app.billing.access import has_access, get_credit_balance_cents
from app.billing.pricing import buddy_discount_multiplier
from .portfolio import get_user_portfolio_view
from .ledger_public import query_public_ledger, get_single_txn, ledger_filter_options
from flask import redirect, flash, jsonify
from sqlalchemy import func
import random
from datetime import datetime

from app.models import Lesson, LessonBlock, LessonSubject, TriviaAnswer, TriviaBadVote
from app.access_ledger.service import (
    get_or_create_system_account, get_or_create_user_wallet,
    get_an_asset, post_access_txn, EntrySpec,
    get_user_level_multiplier
)
from .. import db


@login_required
def _get_subscription():
    return Subscription.query.filter_by(user_id=current_user.id).one_or_none()

@bp.get("/account")
@login_required
def account():
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




@bp.get("/trivia")
@login_required
def trivia_page():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    search_text = (request.args.get("q") or "").strip()
    subject_code = (request.args.get("subject") or "").strip() or None

    subjects = (LessonSubject.query
                .filter(LessonSubject.active.is_(True))
                .order_by(LessonSubject.name.asc())
                .all())

    from sqlalchemy import func

    alpha = 0.7  # tune: bigger => bad questions disappear faster

    bad_counts = (
        db.session.query(
            TriviaBadVote.lesson_block_id.label("block_id"),
            func.count(TriviaBadVote.id).label("bad_count"),
        )
        .group_by(TriviaBadVote.lesson_block_id)
        .subquery()
    )

    bad_count = func.coalesce(bad_counts.c.bad_count, 0)
    weight = func.exp(-alpha * bad_count)
    weight_safe = func.greatest(weight, 1e-6)
    score = (-func.ln(func.random())) / weight_safe  # smaller score wins

    q = (
        db.session.query(LessonBlock, Lesson)
        .join(Lesson, Lesson.id == LessonBlock.lesson_id)
        .outerjoin(bad_counts, bad_counts.c.block_id == LessonBlock.id)
        .filter(LessonBlock.type == "quiz_mcq")
    )

    if subject_code:
        q = q.filter(Lesson.subject_code == subject_code)

    if search_text:
        like = f"%{search_text}%"
        q = q.filter(
            (Lesson.title.ilike(like)) |
            (Lesson.description.ilike(like)) |
            (Lesson.code.ilike(like))
        )

    row = q.order_by(score).first()

    if not row:
        flash("No trivia questions found for that filter.", "info")
        return render_template(
            "trivia/page.html",
            block=None,
            lesson=None,
            subjects=subjects,
            subject_code=subject_code,
            q=search_text,
        )

    block, lesson = row
    return render_template("trivia/page.html", block=block, lesson=lesson,
                           subjects=subjects, subject_code=subject_code, q=search_text)


@bp.post("/trivia/answer")
@login_required
def trivia_answer():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    block_id = (request.form.get("block_id") or "").strip()
    choice_raw = request.form.get("choice_index")
    subject_code = (request.form.get("subject") or "").strip() or None
    search_text = (request.form.get("q") or "").strip() or None

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

    base_ticks = 0
    if is_correct:
        base_ticks = 1 + int(random.expovariate(1 / 10.0))

    mult = get_user_level_multiplier(current_user.id)
    payout_ticks = int(round(base_ticks * mult))

    # Keep “correct” always rewarding at least 1 tick if base > 0
    if base_ticks > 0 and payout_ticks <= 0:
        payout_ticks = 1

    # record attempt
    ans = TriviaAnswer(
        user_id=current_user.id,
        lesson_block_id=block.id,
        chosen_index=choice_index,
        is_correct=bool(is_correct),
        payout_ticks=int(payout_ticks),
        subject_code=subject_code,
        search_text=search_text,
    )
    db.session.add(ans)
    db.session.flush()  # so ans.id exists

    # mint reward idempotently per trivia answer row
    if payout_ticks > 0:
        issuer = get_or_create_system_account("rewards_pool")
        user_wallet = get_or_create_user_wallet(current_user.id)
        an = get_an_asset()

        post_access_txn(
            event_type="trivia_correct_reward",
            idempotency_key=f"trivia_answer_reward:{ans.id}",
            actor_user_id=current_user.id,
            context_type="trivia_answer",
            context_id=str(ans.id),
            memo_json={
                "base_ticks": base_ticks,
                "mult": mult,
                "payout_ticks": payout_ticks,
                "lesson_block_id": block.id
            },
            entries=[
                EntrySpec(account_id=issuer.id, asset_id=an.id, delta=-payout_ticks, entry_type="mint"),
                EntrySpec(account_id=user_wallet.id, asset_id=an.id, delta=+payout_ticks, entry_type="mint"),
            ],
        )

    db.session.commit()


    # Normal correctness feedback
    if is_correct:
        flash("Correct!", "success")
        if payout_ticks > 0:
            # special category format: "reward:<ticks>"
            flash(f"+{payout_ticks} ticks", f"reward:{payout_ticks}")
    else:
        flash("Nope.", "error")

    return redirect(url_for("appui.trivia_page", subject=subject_code or "", q=search_text or ""))


@bp.post("/trivia/bad")
@login_required
def trivia_bad_vote():
    if not has_access(current_user):
        return redirect(url_for("billing.pricing"))

    block_id = (request.form.get("block_id") or "").strip()
    subject_code = (request.form.get("subject") or "").strip() or None
    search_text = (request.form.get("q") or "").strip() or None

    if not block_id:
        abort(400)

    # insert-if-not-exists (one vote per user per block)
    existing = TriviaBadVote.query.filter_by(user_id=current_user.id, lesson_block_id=block_id).one_or_none()
    if not existing:
        db.session.add(TriviaBadVote(
            user_id=current_user.id,
            lesson_block_id=block_id,
            subject_code=subject_code,
        ))
        db.session.commit()

    flash("Logged bad-question vote.", "info")
    return redirect(url_for("appui.trivia_page", subject=subject_code or "", q=search_text or ""))
