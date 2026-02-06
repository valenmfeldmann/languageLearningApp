import mistune
from dotenv import load_dotenv


load_dotenv()

from app import models
from .config import Config
from .extensions import db, migrate, login_manager, oauth
from .models import User
from .extensions import login_manager
from .auth.routes import bp as auth_bp

# app/__init__.py (inside create_app)
from flask import Flask
from .models import User

from werkzeug.routing import BuildError
from flask import url_for

from flask import request, redirect, url_for, flash
from flask_login import current_user
from app.billing.access import has_access




@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

def create_app():
    app = Flask(__name__)

    # FIX: Tell Oauthlib that it is okay to use HTTP for local dev
    import os
    if os.environ.get('FLASK_ENV') == 'development':
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    app.config.from_object(Config)


    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    oauth.init_app(app)

    from app.lessons.routes import bp as lessons_bp
    app.register_blueprint(lessons_bp)

    from app.author.routes import bp as author_bp
    app.register_blueprint(author_bp)

    from app.appui import bp as appui_bp
    app.register_blueprint(appui_bp)

    from .debug import bp as debug_bp
    app.register_blueprint(debug_bp)

    from .access_ledger.cli import register_cli as register_access_ledger_cli
    register_access_ledger_cli(app)

    from .billing.import_stripe_users import stripe_import_users_command
    import click

    @app.cli.command("stripe-import-users")
    @click.option("--limit", default=100, type=int)
    def stripe_import_users(limit: int):
        """Import Stripe customers into DB User rows (dev helper)."""
        stripe_import_users_command(limit=limit)

    import click
    from .billing.debug import print_user_billing

    @app.cli.command("billing-debug")
    @click.argument("user_id")
    def billing_debug(user_id):
        print_user_billing(user_id)

    from .billing.sync import stripe_sync_command
    from .billing.sync_subs import stripe_sync_subscriptions_command

    @app.cli.command("stripe-sync-all")
    def stripe_sync_all():
        """Sync subscriptions + balances from Stripe into DB."""
        stripe_sync_subscriptions_command()
        stripe_sync_command()

    from .billing.sync_subs import stripe_sync_subscriptions_command

    @app.cli.command("stripe-sync-subs")
    def stripe_sync_subs():
        """Reconcile DB subscription rows with Stripe."""
        stripe_sync_subscriptions_command()

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, user_id)

    from .billing.sync import stripe_sync_command

    @app.cli.command("stripe-sync")
    def stripe_sync():
        """Reconcile DB state with Stripe."""
        stripe_sync_command()

    from .buddies.routes import bp as buddies_bp
    app.register_blueprint(buddies_bp)

    from .billing.routes import bp as billing_bp
    app.register_blueprint(billing_bp)

    from .auth.oauth import register_google
    register_google(app)

    from .auth.routes import bp as auth_bp
    app.register_blueprint(auth_bp)

    from .main.routes import bp as main_bp
    app.register_blueprint(main_bp)

    from flask_login import current_user
    from flask import request
    from app.access_ledger.service import get_user_an_balance_ticks, AN_SCALE
    from .access_ledger.service import DAILY_TAX_AN

    @app.context_processor
    def inject_wallet_banner():
        # 1. If not logged in, provide safe defaults to avoid UndefinedErrors
        if not getattr(current_user, "is_authenticated", False):
            return {
                "an_balance_an": 0,
                "an_warn": False,
                "an_critical": False
            }

        # 2. Always fetch the actual balance for the User Pill
        from app.access_ledger.service import get_user_an_balance_ticks, AN_SCALE, DAILY_TAX_AN
        ticks = get_user_an_balance_ticks(current_user.id)
        an = ticks / AN_SCALE

        # 3. Determine if we SHOULD show the warning banner
        # We suppress the VISUAL banner on auth/billing/author pages
        p = request.path or ""
        suppress_pages = ("/auth", "/billing", "/author")

        show_warning_logic = not p.startswith(suppress_pages)

        # 4. Set the flags
        # If we are on an author page, an_warn becomes False, hiding the banner
        # but an_balance_an still exists for the navigation bar.
        warn = (an < 3 * DAILY_TAX_AN) if show_warning_logic else False
        critical = (an < 1 * DAILY_TAX_AN) if show_warning_logic else False

        return {
            "an_balance_an": an,
            "an_warn": warn,
            "an_critical": critical,
        }


    # Define the markdown filter
    @app.template_filter('markdown')
    def markdown_filter(text):
        if not text:
            return ""
        # Initialize the renderer
        markdown = mistune.create_markdown(escape=False)
        return markdown(text)



    from flask import session

    @app.context_processor
    def inject_last_curriculum():
        return {
            "last_curriculum_code": session.get("last_curriculum_code")
        }

    @app.context_processor
    def inject_nav_links():
        def safe_url(endpoint, **values):
            try:
                return url_for(endpoint, **values)
            except BuildError:
                return None

        # Check if subscriptions are required
        sub_req = current_app.config.get("REQUIRE_SUBSCRIPTION", True)

        return {
            "require_subscription": sub_req,  # Pass this to templates
            "nav_links": {
                "author_lessons": safe_url("author.lesson_index"),
                "new_lesson": safe_url("author.lesson_new_form"),
                "new_curriculum": safe_url("author.curriculum_new_form"),
                "author_curricula": url_for('author.curricula_index'),
            }
        }

    import os
    from flask import current_app

    @app.context_processor
    def inject_announcement():
        # Use the instance folder so it's easy to update on the server
        file_path = os.path.join(current_app.instance_path, 'announcement.txt')

        announcement_text = None
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:  # Only set if the file isn't empty
                        announcement_text = content
            except Exception:
                pass  # Fail silently so the app doesn't crash if the file is locked

        return dict(global_announcement=announcement_text)

    @app.before_request
    def require_subscription_everywhere():
        # 1. Check the global toggle first
        if not current_app.config.get("REQUIRE_SUBSCRIPTION", True):
            return None

        # Always allow static files
        if request.endpoint == "static":
            return None

        if request.path == "/favicon.ico":
            return None

        # Let unauthenticated users proceed (your routes can redirect to login)
        if not current_user.is_authenticated:
            return None

        # Allowlist: ONLY things visible without subscription
        allowed_endpoints = {
            # public-ish pages you want accessible without subscription
            "main.home",  # GET /
            "main.app_home",  # GET /app
            "appui.account",  # GET /app/account

            # pricing pages
            "billing.pricing",  # GET /billing/pricing
            "main.pricing_shortcut",  # GET /pricing (your shortcut route)

            # stripe webhook must always work
            "billing.stripe_webhook",  # POST /billing/webhook

            # auth flow
            "auth.login",
            "auth.google_callback",
            "auth.logout",

            # billing portal
            "billing.portal",  # GET /billing/portal
            "billing.checkout",  # GET/POST /billing/checkout (whatever you named it)
            "billing.checkout_success",  # if you have it
            "billing.checkout_cancel",  # if you have it

        }

        # If you want "/app" home or "/" home accessible, add the real endpoint name(s) here.
        # We'll add it after you run the grep below.
        # allowed_endpoints.add("main.index")

        if request.endpoint in allowed_endpoints:
            return None

        # If not subscribed: block everything else
        if not has_access(current_user):
            # For POST/PUT/etc, don't redirect (prevents weird form submits)
            if request.method != "GET":
                return ("Subscription required", 403)

            flash("No active subscription. Please choose a plan.", "warning")
            return redirect(url_for("billing.pricing"))

        return None


    return app



