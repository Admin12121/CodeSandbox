from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from codesandbox.config import get_settings

from . import repository


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    message: str
    token: str | None = None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sign_up(
    *,
    name: str,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> AuthResult:
    if len(password) < 8:
        return AuthResult(False, "Password must be at least 8 characters.")
    password_hash = generate_password_hash(password)
    existing = repository.find_user_by_email(email)
    if existing:
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="email_taken",
        )
        return AuthResult(False, "An account with that email already exists.")
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

    if user.status == "banned":
        repository.record_login_attempt(
            email=email,
            ip_address=ip_address,
            succeeded=False,
            failure_reason="banned",
        )
        return AuthResult(False, "This account has been suspended.")

    if not check_password_hash(user.password_hash, password):
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
        token_hash=hash_token(token),
        expires_at=expires_at,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    repository.update_user(user.id, last_login_at=datetime.now(timezone.utc))
    repository.record_login_attempt(
        email=email,
        ip_address=ip_address,
        succeeded=True,
    )
    return AuthResult(True, "Signed in.", token=token)


def sign_out(token: str) -> None:
    repository.delete_session(hash_token(token))
