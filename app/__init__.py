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



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

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
        if not getattr(current_user, "is_authenticated", False):
            return {}

        # Suppress banner on auth/billing/author pages (tune as you like)
        p = request.path or ""
        if p.startswith("/auth") or p.startswith("/billing") or p.startswith("/author"):
            return {}

        ticks = get_user_an_balance_ticks(current_user.id)
        an = ticks / AN_SCALE

        warn = an < 3*DAILY_TAX_AN
        critical = an < 1*DAILY_TAX_AN

        return {
            "an_balance_an": an,
            "an_warn": warn,
            "an_critical": critical,
        }


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

        return {
            "nav_links": {
                "author_lessons": safe_url("author.lesson_index"),
                "new_lesson": safe_url("author.lesson_new_form"),
                "new_curriculum": safe_url("author.curriculum_new_form"),
            }
        }

    return app



