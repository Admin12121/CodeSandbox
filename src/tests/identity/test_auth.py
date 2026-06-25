from __future__ import annotations

import hashlib
import secrets as _secrets
from datetime import datetime, timedelta, timezone

from tests._context import TestCase, TestContext, unique


def _svc():
    from codesandbox.features.identity import service
    return service


def _repo():
    from codesandbox.features.identity import repository
    return repository
    

def _cleanup_user(user_id: str) -> None:
    repo = _repo()
    user = repo.find_user_by_id(user_id)
    if not user:
        return
    for s in repo.list_user_sessions(user_id):
        try:
            s.delete()
        except Exception:
            pass
    try:
        user.delete()
    except Exception:
        pass


def _make_user(ctx: TestContext, prefix: str = "u") -> object:
    email = unique(prefix) + "@test.local"
    r = _svc().sign_up(
        name=prefix, email=email, password="password123",
        ip_address=None, user_agent=None,
    )
    assert r.ok, f"sign_up failed: {r.message}"
    user = _repo().find_user_by_email(email)
    assert user is not None
    ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    return user


"""Signing up with an email that already exists must fail."""
def test_signup_duplicate_email(ctx: TestContext) -> None:
    email = unique("dup") + "@test.local"
    r1 = _svc().sign_up(
        name="First", email=email, password="password123",
        ip_address=None, user_agent=None,
    )
    user = _repo().find_user_by_email(email)
    if user:
        ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    assert r1.ok

    r2 = _svc().sign_up(
        name="Second", email=email, password="password123",
        ip_address=None, user_agent=None,
    )
    assert not r2.ok, "Duplicate signup must fail"
    assert "already exists" in r2.message.lower()


"""Passwords under 8 characters are rejected before any DB write."""
def test_signup_short_password(ctx: TestContext) -> None:
    email = unique("short") + "@test.local"
    result = _svc().sign_up(
        name="Short", email=email, password="abc",
        ip_address=None, user_agent=None,
    )
    assert not result.ok, "Short password must be rejected"
    assert _repo().find_user_by_email(email) is None, "No user created for invalid password"


"""Incorrect password always returns the same generic error (no enumeration)."""
def test_signin_wrong_password(ctx: TestContext) -> None:
    user = _make_user(ctx, "wrong")
    result = _svc().sign_in(
        email=user.email, password="this_is_wrong",
        ip_address=None, user_agent=None,
    )
    assert not result.ok
    assert result.token is None
    # Generic message — must not confirm whether the email exists
    assert "invalid" in result.message.lower() or "incorrect" in result.message.lower()


"""A suspended account cannot sign in regardless of correct credentials."""
def test_signin_banned_user(ctx: TestContext) -> None:
    user = _make_user(ctx, "banned")
    _repo().update_user(str(user.id), status="banned")

    result = _svc().sign_in(
        email=user.email, password="password123",
        ip_address=None, user_agent=None,
    )
    assert not result.ok
    assert result.token is None
    msg = result.message.lower()
    assert "suspend" in msg or "ban" in msg


"""An already-expired session must not be returned as active."""
def test_session_expired(ctx: TestContext) -> None:
    user = _make_user(ctx, "expd")
    raw = _secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    _repo().create_session(
        user_id=str(user.id),
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        ip_address=None,
        user_agent=None,
    )
    assert _repo().find_active_session(token_hash) is None, "Expired session must be invisible"


"""Full reset flow: request → reset with new password → old password rejected."""
def test_password_reset_flow(ctx: TestContext) -> None:
    user = _make_user(ctx, "reset")
    found, raw_token = _svc().request_password_reset(user.email)
    assert found and raw_token

    reset_result = _svc().reset_password(raw_token, "newpass456")
    assert reset_result.ok, reset_result.message

    bad = _svc().sign_in(email=user.email, password="password123", ip_address=None, user_agent=None)
    assert not bad.ok, "Old password must be rejected after reset"

    good = _svc().sign_in(email=user.email, password="newpass456", ip_address=None, user_agent=None)
    assert good.ok, "New password must be accepted after reset"


"""A used reset token must be rejected on second use — no replays."""
def test_password_reset_token_replay(ctx: TestContext) -> None:
    user = _make_user(ctx, "replay")
    _, raw_token = _svc().request_password_reset(user.email)

    first = _svc().reset_password(raw_token, "newpass001")
    assert first.ok, first.message

    second = _svc().reset_password(raw_token, "newpass002")
    assert not second.ok, "Replayed reset token must be rejected"

    # Password must NOT have changed to the second value
    valid = _svc().sign_in(email=user.email, password="newpass001", ip_address=None, user_agent=None)
    assert valid.ok, "First reset password must still be the correct one"


"""An email-verify token must not be accepted as a password reset token."""
def test_reset_token_cross_purpose(ctx: TestContext) -> None:
    user = _make_user(ctx, "crosspurp")
    email_verify_token = _svc().request_email_verification(str(user.id), user.email)

    result = _svc().reset_password(email_verify_token, "hijackpass1")
    assert not result.ok, "Email-verify token must not reset a password"

    # Password must be unchanged — original credentials still work
    valid = _svc().sign_in(email=user.email, password="password123", ip_address=None, user_agent=None)
    assert valid.ok, "Original password must still work after cross-purpose attack attempt"


"""After sign_out, the same token must not resolve to an active session."""
def test_session_invalidated_after_signout(ctx: TestContext) -> None:
    user = _make_user(ctx, "sias")
    result = _svc().sign_in(
        email=user.email, password="password123",
        ip_address=None, user_agent=None,
    )
    assert result.ok and result.token

    token_hash = hashlib.sha256(result.token.encode()).hexdigest()
    assert _repo().find_active_session(token_hash) is not None, "Session should be active before sign-out"

    _svc().sign_out(result.token)
    assert _repo().find_active_session(token_hash) is None, "Session must be gone after sign-out"


"""create_session_for_user returns None for a banned account."""
def test_banned_user_cannot_create_session(ctx: TestContext) -> None:
    user = _make_user(ctx, "bucs")
    _repo().update_user(str(user.id), status="banned")

    token = _svc().create_session_for_user(
        str(user.id), ip_address=None, user_agent=None,
    )
    assert token is None, "Banned user must not receive a new session token"


"""sign_up AuthResult must not expose the raw password in any field."""
def test_password_not_in_auth_result(ctx: TestContext) -> None:
    pw = "super_secret_password_xyz_9182"
    email = unique("pnar") + "@test.local"
    result = _svc().sign_up(
        name="PNARTest", email=email, password=pw,
        ip_address=None, user_agent=None,
    )
    user = _repo().find_user_by_email(email)
    if user:
        ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    assert result.ok

    # Concatenate all result fields and verify the raw password never appears
    leaked = " ".join(
        str(v) for v in [result.message, result.token, result.user_id] if v is not None
    )
    assert pw not in leaked, "Raw password must never appear in AuthResult fields"


TESTS: list[TestCase] = [
    TestCase("signup duplicate email",          "identity", test_signup_duplicate_email),
    TestCase("signup short password",           "identity", test_signup_short_password),
    TestCase("signin wrong password",           "identity", test_signin_wrong_password),
    TestCase("signin banned user",              "identity", test_signin_banned_user),
    TestCase("session expired",                 "identity", test_session_expired),
    TestCase("password reset flow",             "identity", test_password_reset_flow),
    TestCase("password reset token replay",     "identity", test_password_reset_token_replay),
    TestCase("reset token cross-purpose",       "identity", test_reset_token_cross_purpose),
    TestCase("session invalidated after signout","identity", test_session_invalidated_after_signout),
    TestCase("banned user cannot create session","identity", test_banned_user_cannot_create_session),
    TestCase("password not in auth result",     "identity", test_password_not_in_auth_result),
]
