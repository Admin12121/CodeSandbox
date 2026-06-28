from flask import Blueprint
from app_router import AppRouter

from codesandbox.web.csrf import install_csrf_protection

web_bp = Blueprint(
    "web",
    __name__,
    template_folder="../templates",
)


install_csrf_protection(web_bp)

TAILWIND_CDN_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data:; "
    "font-src 'self' https://cdn.jsdelivr.net; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

router = AppRouter(web_bp, csp=TAILWIND_CDN_CSP)
