from __future__ import annotations

import os

from flask import Flask

from codesandbox.config import get_settings
from codesandbox.web.blueprint import web_bp


def create_app() -> Flask:
    settings = get_settings()
    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["DATABASE_URL"] = settings.database_url
    app.config["SESSION_COOKIE_NAME"] = settings.session_cookie_name
    app.config["SESSION_TTL_HOURS"] = settings.session_ttl_hours
    from codesandbox.web import routes as _routes  # noqa: F401
    from codesandbox.modules.auth import routes as _auth_routes  # noqa: F401

    app.register_blueprint(web_bp)
    return app


app = create_app()
