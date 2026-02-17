import uuid
from flask_login import login_user, logout_user, login_required
from flask import Blueprint, current_app

from ..billing.stripe_client import ensure_stripe_customer
from ..extensions import oauth, db
from ..companion.service import create_new_dog
from ..models import User, Character
from ..billing.signup_credit import grant_signup_credit_once
from app.models import Subscription
from app.extensions import db
from ..billing.credits import grant_signup_credit_once  # wherever this lives
from flask import redirect, url_for
from ..access_ledger.service import get_or_create_user_wallet
from flask import current_app, flash
from app.access_ledger.service import grant_signup_bonus_once
import threading
from flask import current_app




bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.get("/login")
def login():
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)




def background_user_setup(app_context, user_id):
    """Heavy lifting moved out of the request-response cycle."""
    with app_context:
        user = User.query.get(user_id)
        if not user:
            return

        try:
            # 1. Initialize Wallet
            get_or_create_user_wallet(user.id, currency_code="access_note")

            # 2. Grant Bonus
            bonus_ticks = int(current_app.config.get("SIGNUP_BONUS_TICKS", 10000))
            grant_signup_bonus_once(user_id=user.id, ticks=bonus_ticks)

            # 3. Stripe & Credits
            ensure_stripe_customer(user)
            grant_signup_credit_once(user=user, plan_code="base", months=24)

            db.session.commit()
        except Exception:
            current_app.logger.exception(f"Background setup failed for user {user_id}")


@bp.get("/google/callback")
def google_callback():
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo") or oauth.google.parse_id_token(token)

    email = userinfo["email"]
    name = userinfo.get("name")
    picture = userinfo.get("picture")

    # 1) UPSERT USER (Keep this synchronous so they can login)
    user = User.query.filter_by(email=email).first()
    is_new = False
    if not user:
        is_new = True
        user = User(id=str(uuid.uuid4()), email=email, name=name, image=picture)
        db.session.add(user)
    else:
        user.name = name
        user.image = picture

    db.session.commit()  # Critical: Ensure the user exists before threading

    # 2) LOGIN IMMEDIATELY
    login_user(user)

    # 3) DEFER HEAVY SETUP IF NEW
    if is_new:
        # Pass the app context so the thread can access the DB and config
        ctx = current_app.app_context()
        threading.Thread(target=background_user_setup, args=(ctx, user.id)).start()

    # 4) COMPANION CHECK: Ensure they have a live dog
    live_dog = Character.query.filter_by(user_id=user.id, is_alive=True).first()
    if not live_dog:
        create_new_dog(user)

    # 5) SMART REDIRECT
    if not user.has_seen_intro:
        return redirect(url_for("companion.welcome_explainer"))

    return redirect(url_for("companion.greet"))

    # # 4) REDIRECT NOW (No more black screen!)
    # return redirect("/app")




# @bp.get("/google/callback")
# def google_callback():
#     token = oauth.google.authorize_access_token()
#     userinfo = token.get("userinfo") or oauth.google.parse_id_token(token)
#
#     email = userinfo["email"]
#     name = userinfo.get("name")
#     picture = userinfo.get("picture")
#
#     # 1) Upsert user first
#     user = User.query.filter_by(email=email).first()
#     if not user:
#         user = User(id=str(uuid.uuid4()), email=email, name=name, image=picture)
#         db.session.add(user)
#     else:
#         user.name = name
#         user.image = picture
#
#     db.session.commit()  # ensures user.id exists
#
#     # 1.5) Ensure wallet exists (THIS is the fix)
#     get_or_create_user_wallet(user.id, currency_code="access_note")
#     # db.session.commit()   # <-- add this (important!)
#
#
#     bonus_ticks = int(current_app.config.get("SIGNUP_BONUS_TICKS", 10000))
#     if grant_signup_bonus_once(user_id=user.id, ticks=bonus_ticks):
#         # This assumes your base.html reads flash categories like "reward:<ticks>" to run rewardFX(ticks)
#         flash(f"Signup bonus: +{bonus_ticks // 1000} AN", f"reward:{bonus_ticks}")
#
#     # 2) Ensure subscription row exists (DB bookkeeping)
#     sub = Subscription.query.filter_by(user_id=user.id).one_or_none()
#     if sub is None:
#         sub = Subscription(
#             id=str(uuid.uuid4()),
#             user_id=user.id,
#             status="none",  # treated as no-access in access.py
#             cancel_at_period_end=False,
#         )
#         db.session.add(sub)
#         db.session.commit()
#
#     # 3) Stripe customer + one-time signup credit (don't block login if Stripe fails)
#     try:
#         ensure_stripe_customer(user)
#         grant_signup_credit_once(user=user, plan_code="base", months=24)  # <-- keyword args
#     except Exception:
#         current_app.logger.exception("Stripe signup credit failed")
#
#     # 4) Login + redirect
#     login_user(user)
#     return redirect("/app")




@bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/")


