from flask import Blueprint

bp = Blueprint("debug", __name__, url_prefix="/app/debug")

from . import routes  # noqa: E402,F401
