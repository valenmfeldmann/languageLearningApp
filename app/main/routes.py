from flask import Blueprint

bp = Blueprint("main", __name__)

@bp.get("/")
def home():
    return {"ok": True}
