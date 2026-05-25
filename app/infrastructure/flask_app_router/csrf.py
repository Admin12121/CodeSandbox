"""Minimal CSRF helpers for server-rendered Flask forms and APIs."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from flask import current_app, request, session
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import Markup, escape

from .exceptions import CSRFError

SESSION_KEY = "_flask_app_router_csrf_seed"
FIELD_NAME = "_csrf_token"
HEADER_NAMES = ("X-CSRF-Token", "X-CSRFToken")
DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 8


def _serializer() -> URLSafeTimedSerializer:
    secret = current_app.secret_key or current_app.config.get("SECRET_KEY")
    if not secret:
        raise RuntimeError("Flask App Router CSRF requires Flask SECRET_KEY to be set.")
    return URLSafeTimedSerializer(secret_key=secret, salt="flask-app-router.csrf")


def _session_seed() -> str:
    seed = session.get(SESSION_KEY)
    if not isinstance(seed, str):
        seed = secrets.token_urlsafe(32)
        session[SESSION_KEY] = seed
    return seed


def csrf_token(name: str = "default") -> str:
    """Return a signed CSRF token tied to the current Flask session."""

    payload = {"seed": _session_seed(), "name": name}
    return _serializer().dumps(payload)


def csrf_input(name: str = "default", field_name: str = FIELD_NAME) -> Markup:
    """Render a hidden CSRF input for normal HTML forms."""

    return Markup(
        f'<input type="hidden" name="{escape(field_name)}" '
        f'value="{escape(csrf_token(name))}">'
    )


def _request_token(field_name: str = FIELD_NAME) -> str | None:
    for header in HEADER_NAMES:
        token = request.headers.get(header)
        if token:
            return token
    token = request.form.get(field_name)
    return token if token else None


def validate_csrf(
    token: str | None = None,
    *,
    name: str = "default",
    max_age: int | None = None,
    raise_error: bool = False,
) -> bool:
    """Validate a signed CSRF token."""

    token = token or _request_token()
    if not token:
        if raise_error:
            raise CSRFError("CSRF token is missing.")
        return False

    if max_age is None:
        max_age = int(
            current_app.config.get(
                "FLASK_APP_ROUTER_CSRF_MAX_AGE",
                DEFAULT_MAX_AGE_SECONDS,
            )
        )

    try:
        payload: Any = _serializer().loads(token, max_age=max_age)
    except SignatureExpired as exc:
        if raise_error:
            raise CSRFError("CSRF token has expired.") from exc
        return False
    except BadSignature as exc:
        if raise_error:
            raise CSRFError("CSRF token is invalid.") from exc
        return False

    valid = (
        isinstance(payload, dict)
        and payload.get("name") == name
        and isinstance(payload.get("seed"), str)
        and isinstance(session.get(SESSION_KEY), str)
        and hmac.compare_digest(payload["seed"], session[SESSION_KEY])
    )
    if not valid and raise_error:
        raise CSRFError("CSRF token is invalid.")
    return valid
