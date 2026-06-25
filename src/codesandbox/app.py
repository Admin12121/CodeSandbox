from __future__ import annotations

import logging
import warnings

from flask import Flask

from codesandbox.config import get_settings
from codesandbox.infrastructure.nexorm import configure_db
from codesandbox.shared.limiter import init_limiter
from codesandbox.web.blueprint import web_bp

_WEAK_KEYS = {"dev-secret-change-in-production", "secret", "changeme", ""}


def create_app() -> Flask:
    settings = get_settings()
    if settings.secret_key in _WEAK_KEYS or len(settings.secret_key) < 32:
        warnings.warn(
            "SECRET_KEY is weak or default — set a strong random value in production.",
            stacklevel=1,
        )
        logging.getLogger(__name__).warning(
            "SECURITY: SECRET_KEY is weak or default. Generate one with: "
            "python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    configure_db(settings.database_url)

    app = Flask(__name__, template_folder="templates")
    app.config["SECRET_KEY"] = settings.secret_key
    app.config["DATABASE_URL"] = settings.database_url
    app.config["SESSION_TTL_HOURS"] = settings.session_ttl_hours
    # Auth token cookie — read/written by identity code via CS_AUTH_COOKIE key
    app.config["CS_AUTH_COOKIE"] = settings.session_cookie_name
    # Flask's built-in session cookie must use a DIFFERENT name to avoid
    # overwriting the auth token when flask.session is modified (e.g. 2FA, workspace slug)
    app.config["SESSION_COOKIE_NAME"] = "_cs_sess"

    import codesandbox.models  # noqa: F401 — registers all NexORM models

    from codesandbox.web import routes as _web_routes  # noqa: F401
    from codesandbox.features.identity import routes as _identity_routes  # noqa: F401
    from codesandbox.features.identity import pages as _identity_pages  # noqa: F401
    from codesandbox.features.platform_admin import routes as _platform_admin_routes  # noqa: F401
    from codesandbox.features.platform_admin import pages as _platform_pages  # noqa: F401
    from codesandbox.features.organizations import routes as _org_routes  # noqa: F401
    from codesandbox.features.organizations import pages as _org_pages  # noqa: F401

    app.register_blueprint(web_bp)
    init_limiter(app, settings.redis_url)
    app.jinja_env.auto_reload = True

    # In debug mode, disable the Jinja2 LRU cache so every request reloads
    # templates from disk. This makes template edits visible immediately without
    # a process restart, fixing the mtime-resolution issue on Docker overlayfs.
    if app.debug:
        app.jinja_env.cache = None  # type: ignore[assignment]

    return app


app = create_app()
