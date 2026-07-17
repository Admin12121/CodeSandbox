from __future__ import annotations

import logging
import math
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
    configure_db(
        settings.database_url,
        pool_size=settings.database_pool_size,
        pool_timeout=settings.database_pool_timeout,
    )

    app = Flask(__name__, template_folder="templates")
    app.debug = settings.debug
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
    from codesandbox.features.laboratory import pages as _laboratory_pages  # noqa: F401
    from codesandbox.features.sandbox import routes as _sandbox_routes  # noqa: F401
    from codesandbox.features.sandbox import pages as _sandbox_pages  # noqa: F401
    from codesandbox.features.billing import routes as _billing_routes  # noqa: F401
    from codesandbox.features.finance import routes as _finance_routes  # noqa: F401
    from codesandbox.features.finance import pages as _finance_pages  # noqa: F401
    from codesandbox.features.workflow import routes as _workflow_routes  # noqa: F401
    from codesandbox.features.workflow import pages as _workflow_pages  # noqa: F401

    app.register_blueprint(web_bp)
    init_limiter(app, settings.redis_url)
    app.jinja_env.auto_reload = not settings.window

    @app.teardown_appcontext
    def _release_db_connection(_exc: BaseException | None) -> None:
        # Give this thread's connection back to the pool at the end of every
        # request instead of holding it for the life of the process — what
        # actually makes DATABASE_POOL_SIZE a real bound rather than every
        # request-serving thread permanently owning its own connection.
        from codesandbox.infrastructure.nexorm import get_db
        get_db().close()

    # In debug mode, disable the Jinja2 LRU cache so every request reloads
    # templates from disk. This makes template edits visible immediately without
    # a process restart, fixing the mtime-resolution issue on Docker overlayfs.
    if app.debug:
        app.jinja_env.cache = None  # type: ignore[assignment]

    def _header_balance_global():
        # A Jinja global function, not a Flask @app.context_processor: this
        # app's router (app_router.AppRouter) renders templates via
        # `jinja_env.get_template(...).render(dict(context))` directly,
        # bypassing flask.render_template — so Flask context processors,
        # which only hook into render_template, never fire here. Jinja
        # globals are merged into every Template.render() call by Jinja
        # itself regardless of how render() was reached, so this is the
        # mechanism that actually reaches every page without threading it
        # through every route's individual context dict.
        from codesandbox.shared.session import get_current_session
        session = get_current_session()
        if not session:
            return None
        from flask import g
        from codesandbox.features.billing.service import get_header_balance
        from codesandbox.web._ctx import _workspaces_ctx
        if getattr(g, "_billing_workspace_override_set", False):
            active_workspace = getattr(g, "_billing_workspace_override", None)
        else:
            active_workspace = _workspaces_ctx(session.user).get("active_workspace")
        return get_header_balance(session.user, active_workspace)

    app.jinja_env.globals["header_balance"] = _header_balance_global
    app.jinja_env.globals["sin"] = math.sin
    app.jinja_env.globals["cos"] = math.cos
    app.jinja_env.globals["pi"] = math.pi
    app.jinja_env.globals["use_built_tailwind"] = settings.use_built_tailwind

    return app


app = create_app()
