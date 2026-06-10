from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

from codesandbox.config import get_settings
from codesandbox.modules.auth import repository


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    message: str
    token: str | None = None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_in(
    *,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    user = repository.find_user_by_email(email)
    if not user or not user.password_hash:
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="invalid_credentials",
        )
        return AuthResult(False, "Invalid email or password.")

    if user.status != "active" or not check_password_hash(user.password_hash, password):
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="invalid_credentials",
        )
        return AuthResult(False, "Invalid email or password.")

    settings = get_settings()
    token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours)
    repository.create_session(
        user_id=user.id,
        token_hash=_hash_token(token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    repository.record_login_attempt(
        email=email,
        ip_address=ip_address,
        succeeded=True,
    )
    return AuthResult(True, "Signed in.", token=token)


def sign_up(
    *,
    name: str,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    password_hash = generate_password_hash(password)
    try:
        repository.create_user(email=email, name=name, password_hash=password_hash)
    except Exception:
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="signup_failed",
        )
        return AuthResult(False, "Unable to create account.")

    return sign_in(
        email=email,
        password=password,
        ip_address=ip_address,
        user_agent=user_agent,
    )
