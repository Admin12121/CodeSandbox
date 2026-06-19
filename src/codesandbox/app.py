from __future__ import annotations

from flask import Flask

from codesandbox.config import get_settings
from codesandbox.infrastructure.nexorm import configure_db
from codesandbox.web.blueprint import web_bp


def create_app() -> Flask:
    settings = get_settings()
    configure_db(settings.database_url)

    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["DATABASE_URL"] = settings.database_url
    app.config["SESSION_COOKIE_NAME"] = settings.session_cookie_name
    app.config["SESSION_TTL_HOURS"] = settings.session_ttl_hours

    import codesandbox.models  # noqa: F401 — registers all NexORM models

    from codesandbox.web import routes as _web_routes  # noqa: F401
    from codesandbox.features.identity import routes as _identity_routes  # noqa: F401
    from codesandbox.features.platform_admin import routes as _platform_admin_routes  # noqa: F401
    from codesandbox.features.organizations import routes as _org_routes  # noqa: F401

    app.register_blueprint(web_bp)
    return app


app = create_app()
