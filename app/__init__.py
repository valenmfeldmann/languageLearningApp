from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from .config import Config
from .extensions import db, migrate, login_manager, oauth
from .models import User
from .extensions import login_manager

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

    # import models so Alembic sees them
    from . import models  # noqa: F401

    # register blueprints
    from .main.routes import bp as main_bp
    app.register_blueprint(main_bp)

    return app
