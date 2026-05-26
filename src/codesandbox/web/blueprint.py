from flask import Blueprint
from app_router import AppRouter

web_bp = Blueprint(
    "web",
    __name__,
)

router = AppRouter(web_bp)