from __future__ import annotations

import secrets
import time
import urllib.parse
from urllib.parse import urlparse

from flask import current_app, redirect, request, session


def _safe_next(next_path: str, default: str = "/dashboard") -> str:
    """Reject absolute URLs to prevent open-redirect attacks."""
    parsed = urlparse(next_path)
    if parsed.scheme or parsed.netloc:
        return default
    return next_path or default

from codesandbox.config import get_settings
from codesandbox.web.blueprint import web_bp

from .service import (
    confirm_totp_setup,
    create_session_for_user,
    disable_2fa,
    generate_totp_setup,
    link_github_account,
    link_google_account,
    request_email_verification,
    request_password_reset,
    reset_password,
    sign_in,
    sign_in_with_github,
    sign_in_with_google,
    sign_out,
    sign_up,
    unlink_account,
    verify_email,
    verify_totp,
)


def _set_session_cookie(response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        current_app.config["CS_AUTH_COOKIE"],
        token,
        httponly=True,
        samesite="Lax",
        secure=settings.cookie_secure,
        max_age=current_app.config["SESSION_TTL_HOURS"] * 3600,
    )


@web_bp.post("/login")
def login_action():
    mode = request.form.get("mode", "signin")
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    name = request.form.get("name", "").strip() or email.split("@")[0]
    next_path = _safe_next(request.form.get("next", "/dashboard"))

    if mode == "signup":
        result = sign_up(
            name=name,
            email=email,
            password=password,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )
    else:
        result = sign_in(
            email=email,
            password=password,
            ip_address=request.remote_addr,
            user_agent=request.headers.get("User-Agent"),
        )

    if not result.ok:
        return redirect(f"/login?error={urllib.parse.quote(result.message)}&mode={mode}", code=303)

    if result.requires_2fa:
        if not result.user_id:
            return redirect("/login?error=Unable+to+start+two-factor+verification", code=303)
        session.clear()
        session["_2fa_pending_user_id"] = result.user_id
        session["_2fa_pending_at"] = int(time.time())
        session["_2fa_next"] = next_path
        return redirect("/two-factor", code=303)

    if not result.token:
        return redirect("/login?error=Unable+to+create+session", code=303)

    session.clear()
    response = redirect(next_path, code=303)
    _set_session_cookie(response, result.token)
    return response


@web_bp.post("/logout")
def logout_action():
    cookie_name = current_app.config["CS_AUTH_COOKIE"]
    token = request.cookies.get(cookie_name)
    if token:
        sign_out(token)
    session.pop("_2fa_pending_user_id", None)
    session.pop("_2fa_pending_at", None)
    session.pop("_2fa_next", None)
    response = redirect("/login", code=303)
    response.delete_cookie(cookie_name)
    return response


# ── Email verification ────────────────────────────────────────────────────────

@web_bp.get("/verify-email")
def verify_email_page():
    token = request.args.get("token", "")
    if token:
        result = verify_email(token)
        if result.ok:
            return redirect("/dashboard?verified=1", code=303)
        return redirect(f"/login?error={urllib.parse.quote(result.message)}", code=303)
    return redirect("/login", code=303)


@web_bp.post("/resend-verification")
def resend_verification():
    from codesandbox.shared.session import get_current_session
    from codesandbox.shared.email import send_email_verification
    cs = get_current_session()
    if cs and not cs.user.email_verified:
        settings = get_settings()
        token = request_email_verification(cs.user.id, cs.user.email)
        verify_url = f"{settings.app_url}/verify-email?token={token}"
        sent = send_email_verification(to=cs.user.email, verify_url=verify_url)
        if sent:
            return redirect("/settings?info=Verification+email+sent.+Check+your+inbox.", code=303)
        return redirect("/settings?error=Failed+to+send+verification+email.+Please+try+again+later.", code=303)
    return redirect("/settings", code=303)


# ── Password reset ────────────────────────────────────────────────────────────

@web_bp.post("/forgot-password")
def forgot_password_action():
    from codesandbox.shared.email import send_password_reset
    email = request.form.get("email", "").strip()
    found, raw_token = request_password_reset(email)
    if found:
        settings = get_settings()
        reset_url = f"{settings.app_url}/reset-password?token={raw_token}"
        sent = send_password_reset(to=email, reset_url=reset_url)
        if sent:
            return redirect("/forgot-password?sent=1", code=303)
        dev_path = f"/reset-password?token={raw_token}"
        return redirect(f"/forgot-password?sent=1&dev_url={urllib.parse.quote(dev_path)}", code=303)
    return redirect("/forgot-password?sent=1", code=303)


@web_bp.post("/reset-password")
def reset_password_action():
    token = request.form.get("token", "")
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", "")
    if password != confirm:
        return redirect(f"/reset-password?token={urllib.parse.quote(token)}&error=Passwords+do+not+match", code=303)
    result = reset_password(token, password)
    if result.ok:
        return redirect("/login?info=Password+reset+successfully.+You+can+now+sign+in.", code=303)
    return redirect(f"/reset-password?token={urllib.parse.quote(token)}&error={urllib.parse.quote(result.message)}", code=303)


# ── 2FA verification at login ─────────────────────────────────────────────────

@web_bp.post("/two-factor/verify")
def two_factor_verify():
    pending_user_id = session.get("_2fa_pending_user_id")
    pending_at = session.get("_2fa_pending_at")
    next_path = session.pop("_2fa_next", "/dashboard")
    code = request.form.get("code", "").strip().replace(" ", "")

    if not pending_user_id or not isinstance(pending_at, int):
        return redirect("/login", code=303)

    if int(time.time()) - pending_at > 300:
        session.pop("_2fa_pending_user_id", None)
        session.pop("_2fa_pending_at", None)
        return redirect("/login?error=Two-factor+verification+expired", code=303)

    if not verify_totp(str(pending_user_id), code):
        session["_2fa_next"] = next_path
        return redirect("/two-factor?error=Invalid+code", code=303)

    token = create_session_for_user(
        str(pending_user_id),
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    if not token:
        session.pop("_2fa_pending_user_id", None)
        session.pop("_2fa_pending_at", None)
        return redirect("/login?error=Unable+to+create+session", code=303)

    session.pop("_2fa_pending_user_id", None)
    session.pop("_2fa_pending_at", None)
    response = redirect(next_path, code=303)
    _set_session_cookie(response, token)
    return response


# ── TOTP setup (in settings) ──────────────────────────────────────────────────

@web_bp.post("/settings/2fa/setup")
def totp_setup_action():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    data = generate_totp_setup(cs.user.id, cs.user.email)
    # Store secret in server-side session — never expose in URL/logs
    session["_2fa_setup_secret"] = data["secret"]
    session["_2fa_setup_uri"] = data["uri"]
    return redirect("/settings/2fa?setup=1", code=303)


@web_bp.post("/settings/2fa/confirm")
def totp_confirm_action():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    code = request.form.get("code", "").strip()
    result = confirm_totp_setup(cs.user.id, code)
    if result.ok:
        # Store backup codes in session for one-time display, then clear
        session["_2fa_backup_codes"] = result.token or ""
        session.pop("_2fa_setup_secret", None)
        session.pop("_2fa_setup_uri", None)
        return redirect("/settings/2fa?enabled=1", code=303)
    return redirect(f"/settings/2fa?error={urllib.parse.quote(result.message)}", code=303)


@web_bp.post("/settings/2fa/disable")
def totp_disable_action():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    disable_2fa(cs.user.id)
    return redirect("/settings?info=Two-factor+authentication+disabled.", code=303)


# ── GitHub OAuth ──────────────────────────────────────────────────────────────

@web_bp.get("/auth/github")
def github_authorize():
    settings = get_settings()
    if not settings.github_client_id:
        return redirect("/login?error=GitHub+OAuth+not+configured", code=303)
    state = secrets.token_urlsafe(16)
    session["_oauth_state"] = state
    session["_oauth_next"] = _safe_next(request.args.get("next", "/dashboard"))
    params = urllib.parse.urlencode({
        "client_id": settings.github_client_id,
        "redirect_uri": f"{settings.app_url}/auth/github/callback",
        "scope": "user:email",
        "state": state,
    })
    return redirect(f"https://github.com/login/oauth/authorize?{params}", code=302)


@web_bp.get("/auth/github/callback")
def github_callback():
    returned_state = request.args.get("state", "")
    expected_state = session.pop("_oauth_state", None)
    next_path = session.pop("_oauth_next", "/dashboard")
    if not expected_state or not secrets.compare_digest(returned_state, expected_state):
        return redirect("/login?error=Invalid+OAuth+state.+Please+try+again.", code=303)
    code = request.args.get("code", "")
    if not code:
        return redirect("/login?error=GitHub+authorization+cancelled", code=303)
    result = sign_in_with_github(
        code=code,
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    if not result.ok or not result.token:
        return redirect(f"/login?error={urllib.parse.quote(result.message)}", code=303)
    session.clear()
    response = redirect(next_path, code=303)
    _set_session_cookie(response, result.token)
    return response


# ── Google OAuth ──────────────────────────────────────────────────────────────

@web_bp.get("/auth/google")
def google_authorize():
    settings = get_settings()
    if not settings.google_client_id:
        return redirect("/login?error=Google+OAuth+not+configured", code=303)
    state = secrets.token_urlsafe(16)
    session["_oauth_state"] = state
    session["_oauth_next"] = _safe_next(request.args.get("next", "/dashboard"))
    params = urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.app_url}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", code=302)


@web_bp.get("/auth/google/callback")
def google_callback():
    returned_state = request.args.get("state", "")
    expected_state = session.pop("_oauth_state", None)
    next_path = session.pop("_oauth_next", "/dashboard")
    if not expected_state or not secrets.compare_digest(returned_state, expected_state):
        return redirect("/login?error=Invalid+OAuth+state.+Please+try+again.", code=303)
    code = request.args.get("code", "")
    if not code:
        return redirect("/login?error=Google+authorization+cancelled", code=303)
    settings = get_settings()
    result = sign_in_with_google(
        code=code,
        redirect_uri=f"{settings.app_url}/auth/google/callback",
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    if not result.ok or not result.token:
        return redirect(f"/login?error={urllib.parse.quote(result.message)}", code=303)
    session.clear()
    response = redirect(next_path, code=303)
    _set_session_cookie(response, result.token)
    return response


# ── Social account linking (settings) ────────────────────────────────────────

@web_bp.get("/auth/google/connect")
def google_connect_authorize():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    settings = get_settings()
    if not settings.google_client_id:
        return redirect("/settings?tab=security&error=Google+OAuth+not+configured", code=303)
    state = secrets.token_urlsafe(16)
    session["_connect_state"] = state
    params = urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.app_url}/auth/google/connect/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state,
    })
    return redirect(f"https://accounts.google.com/o/oauth2/v2/auth?{params}", code=302)


@web_bp.get("/auth/google/connect/callback")
def google_connect_callback():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    returned_state = request.args.get("state", "")
    expected_state = session.pop("_connect_state", None)
    if not expected_state or not secrets.compare_digest(returned_state, expected_state):
        return redirect("/settings?tab=security&error=Invalid+OAuth+state.+Please+try+again.", code=303)
    code = request.args.get("code", "")
    if not code:
        return redirect("/settings?tab=security&error=Google+authorization+cancelled", code=303)
    settings = get_settings()
    result = link_google_account(
        user_id=cs.user.id,
        code=code,
        redirect_uri=f"{settings.app_url}/auth/google/connect/callback",
    )
    if not result.ok:
        return redirect(f"/settings?tab=security&error={urllib.parse.quote(result.message)}", code=303)
    return redirect("/settings?tab=security&info=Google+account+connected.", code=303)


@web_bp.get("/auth/github/connect")
def github_connect_authorize():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    settings = get_settings()
    if not settings.github_client_id:
        return redirect("/settings?tab=security&error=GitHub+OAuth+not+configured", code=303)
    state = secrets.token_urlsafe(16)
    session["_connect_state"] = state
    params = urllib.parse.urlencode({
        "client_id": settings.github_client_id,
        "redirect_uri": f"{settings.app_url}/auth/github/connect/callback",
        "scope": "user:email",
        "state": state,
    })
    return redirect(f"https://github.com/login/oauth/authorize?{params}", code=302)


@web_bp.get("/auth/github/connect/callback")
def github_connect_callback():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    returned_state = request.args.get("state", "")
    expected_state = session.pop("_connect_state", None)
    if not expected_state or not secrets.compare_digest(returned_state, expected_state):
        return redirect("/settings?tab=security&error=Invalid+OAuth+state.+Please+try+again.", code=303)
    code = request.args.get("code", "")
    if not code:
        return redirect("/settings?tab=security&error=GitHub+authorization+cancelled", code=303)
    result = link_github_account(user_id=cs.user.id, code=code)
    if not result.ok:
        return redirect(f"/settings?tab=security&error={urllib.parse.quote(result.message)}", code=303)
    return redirect("/settings?tab=security&info=GitHub+account+connected.", code=303)


@web_bp.post("/settings/connected-accounts/<provider>/disconnect")
def disconnect_account_action(provider: str):
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    if provider not in ("google", "github"):
        return redirect("/settings?tab=security", code=303)
    ok, err = unlink_account(cs.user.id, provider)
    if not ok:
        return redirect(f"/settings?tab=security&error={urllib.parse.quote(err)}", code=303)
    label = "Google" if provider == "google" else "GitHub"
    return redirect(f"/settings?tab=security&info={urllib.parse.quote(label + ' account disconnected.')}", code=303)
