# app/appui/__init__.py
from flask import Blueprint

bp = Blueprint("appui", __name__, url_prefix="/app")

from . import routes  # noqa: E402,F401
