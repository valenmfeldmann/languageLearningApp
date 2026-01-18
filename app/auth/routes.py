import uuid
from flask_login import login_user, logout_user, login_required
from flask import Blueprint, current_app

from ..billing.stripe_client import ensure_stripe_customer
from ..extensions import oauth, db
from ..models import User
from ..billing.signup_credit import grant_signup_credit_once
from app.models import Subscription
from app.extensions import db
from ..billing.credits import grant_signup_credit_once  # wherever this lives
from flask import redirect, url_for
from ..access_ledger.service import get_or_create_user_wallet
from flask import current_app, flash
from app.access_ledger.service import grant_signup_bonus_once

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.get("/login")
def login():
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.get("/google/callback")
def google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or oauth.google.parse_id_token(token)

    email = userinfo["email"]
    name = userinfo.get("name")
    picture = userinfo.get("picture")

    # 1) Upsert user first
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(id=str(uuid.uuid4()), email=email, name=name, image=picture)
        db.session.add(user)
    else:
        user.name = name
        user.image = picture

    db.session.commit()  # ensures user.id exists

    # 1.5) Ensure wallet exists (THIS is the fix)
    get_or_create_user_wallet(user.id, currency_code="access_note")
    # db.session.commit()   # <-- add this (important!)


    bonus_ticks = int(current_app.config.get("SIGNUP_BONUS_TICKS", 10000))
    if grant_signup_bonus_once(user_id=user.id, ticks=bonus_ticks):
        # This assumes your base.html reads flash categories like "reward:<ticks>" to run rewardFX(ticks)
        flash(f"Signup bonus: +{bonus_ticks // 1000} AN", f"reward:{bonus_ticks}")

    # 2) Ensure subscription row exists (DB bookkeeping)
    sub = Subscription.query.filter_by(user_id=user.id).one_or_none()
    if sub is None:
        sub = Subscription(
            id=str(uuid.uuid4()),
            user_id=user.id,
            status="none",  # treated as no-access in access.py
            cancel_at_period_end=False,
        )
        db.session.add(sub)
        db.session.commit()

    # 3) Stripe customer + one-time signup credit (don't block login if Stripe fails)
    try:
        ensure_stripe_customer(user)
        grant_signup_credit_once(user=user, plan_code="base", months=24)  # <-- keyword args
    except Exception:
        current_app.logger.exception("Stripe signup credit failed")

    # 4) Login + redirect
    login_user(user)
    return redirect("/app")

@bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")


