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
from codesandbox.shared.guards import verified_email
from codesandbox.shared.limiter import limiter
from codesandbox.web.blueprint import web_bp

from . import repository
from .service import (
    confirm_totp_setup,
    create_session_for_user,
    disable_2fa,
    generate_totp_setup,
    get_user_passkeys,
    link_github_account,
    link_google_account,
    passkey_auth_begin,
    passkey_auth_complete,
    passkey_register_begin,
    passkey_register_complete,
    request_email_verification,
    request_login_email_challenge,
    request_password_reset,
    reset_password,
    record_second_factor_failure,
    sign_in,
    sign_in_with_github,
    sign_in_with_google,
    sign_out,
    sign_up,
    totp_qr_data_uri,
    unlink_account,
    verify_email,
    verify_login_email_challenge,
    verify_totp,
)


def _clear_pending_2fa() -> None:
    session.pop("_2fa_pending_user_id", None)
    session.pop("_2fa_pending_at", None)
    session.pop("_2fa_next", None)
    session.pop("_2fa_method", None)
    session.pop("_2fa_failures", None)
    session.pop("_2fa_passkey_challenge", None)


def _available_pending_2fa_methods(user_id: str) -> list[str]:
    user = repository.find_user_by_id(user_id)
    if user is None:
        return []
    methods: list[str] = []
    if repository.get_user_passkeys(user_id, enabled_only=True):
        methods.append("passkey")
    if user.two_factor_enabled:
        methods.append("totp")
    settings = get_settings()
    if settings.resend_api_key and user.email_verified:
        methods.append("email")
    return methods


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
@limiter.limit("10 per minute")
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
        session["_2fa_method"] = result.challenge_method or "totp"
        session["_2fa_failures"] = 0
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
    _clear_pending_2fa()
    response = redirect("/login", code=303)
    response.delete_cookie(cookie_name)
    return response


@web_bp.post("/logout/all")
def logout_all_action():
    from codesandbox.shared.session import get_current_session
    cookie_name = current_app.config["CS_AUTH_COOKIE"]
    cs = get_current_session()
    if cs:
        for s in repository.list_user_sessions(cs.user.id):
            try:
                s.delete()
            except Exception:
                pass
    _clear_pending_2fa()
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
@limiter.limit("5 per hour")
def resend_verification():
    from codesandbox.shared.session import get_current_session
    from codesandbox.shared.email import send_email_verification
    cs = get_current_session()
    if cs and not cs.user.email_verified:
        settings = get_settings()
        token = request_email_verification(cs.user.id, cs.user.email)
        verify_url = f"{settings.app_url}/verify-email?token={token}"
        if send_email_verification(to=cs.user.email, verify_url=verify_url):
            return redirect("/settings?info=Verification+email+sent.+Check+your+inbox.", code=303)
        return redirect("/settings?error=Couldn%27t+send+the+verification+email.+Try+again+shortly.", code=303)
    return redirect("/settings?info=Verification+email+sent.+Check+your+inbox.", code=303)


# ── Password reset ────────────────────────────────────────────────────────────

@web_bp.post("/forgot-password")
@limiter.limit("5 per hour")
def forgot_password_action():
    from codesandbox.shared.email import send_password_reset
    email = request.form.get("email", "").strip()
    found, raw_token = request_password_reset(email)
    if found:
        settings = get_settings()
        reset_url = f"{settings.app_url}/reset-password?token={raw_token}"
        send_password_reset(to=email, reset_url=reset_url)
    return redirect("/forgot-password?sent=1", code=303)


@web_bp.post("/reset-password")
@limiter.limit("10 per hour")
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
@limiter.limit("10 per minute")
def two_factor_verify():
    pending_user_id = session.get("_2fa_pending_user_id")
    pending_at = session.get("_2fa_pending_at")
    next_path = session.pop("_2fa_next", "/dashboard")
    code = request.form.get("code", "").strip().replace(" ", "")

    if not pending_user_id or not isinstance(pending_at, int):
        return redirect("/login", code=303)

    if int(time.time()) - pending_at > 300:
        _clear_pending_2fa()
        return redirect("/login?error=Two-factor+verification+expired", code=303)

    method = str(session.get("_2fa_method") or "totp")
    if method == "passkey":
        session["_2fa_next"] = next_path
        return redirect("/two-factor?error=Use+your+passkey+to+continue+or+choose+another+option.", code=303)
    valid = (
        verify_login_email_challenge(str(pending_user_id), code)
        if method == "email"
        else verify_totp(str(pending_user_id), code)
    )
    if not valid:
        failures = int(session.get("_2fa_failures") or 0) + 1
        session["_2fa_failures"] = failures
        record_second_factor_failure(str(pending_user_id), ip_address=request.remote_addr)
        if failures >= 5:
            _clear_pending_2fa()
            return redirect("/login?error=Too+many+invalid+security+codes.+Sign+in+again+later.", code=303)
        session["_2fa_next"] = next_path
        return redirect("/two-factor?error=Invalid+or+expired+code", code=303)

    token = create_session_for_user(
        str(pending_user_id),
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
    )
    if not token:
        _clear_pending_2fa()
        return redirect("/login?error=Unable+to+create+session", code=303)

    _clear_pending_2fa()
    response = redirect(next_path, code=303)
    _set_session_cookie(response, token)
    return response


@web_bp.post("/two-factor/resend")
@limiter.limit("5 per hour")
def two_factor_resend():
    from flask import jsonify
    pending_user_id = session.get("_2fa_pending_user_id")
    method = str(session.get("_2fa_method") or "totp")
    if not pending_user_id or method != "email":
        return jsonify({"ok": False, "error": "Your session expired. Sign in again."}), 400
    if request_login_email_challenge(str(pending_user_id)):
        session["_2fa_pending_at"] = int(time.time())
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Couldn't send a new code. Try again shortly."}), 502


# ── TOTP setup (in settings) ──────────────────────────────────────────────────

@web_bp.post("/two-factor/method")
@limiter.limit("10 per minute")
def two_factor_method_action():
    pending_user_id = session.get("_2fa_pending_user_id")
    pending_at = session.get("_2fa_pending_at")
    next_path = _safe_next(str(session.get("_2fa_next") or "/dashboard"))
    method = str(request.form.get("method") or "").strip()
    if not pending_user_id or not isinstance(pending_at, int):
        return redirect("/login", code=303)
    if int(time.time()) - pending_at > 300:
        _clear_pending_2fa()
        return redirect("/login?error=Two-factor+verification+expired", code=303)

    available = _available_pending_2fa_methods(str(pending_user_id))
    if method not in available:
        session["_2fa_next"] = next_path
        return redirect("/two-factor?error=That+verification+option+is+not+available.", code=303)

    if method == "email" and not request_login_email_challenge(str(pending_user_id)):
        session["_2fa_next"] = next_path
        return redirect("/two-factor?error=Couldn%27t+send+an+email+code.+Choose+another+option.", code=303)

    session["_2fa_method"] = method
    session["_2fa_pending_at"] = int(time.time())
    session["_2fa_next"] = next_path
    session.pop("_2fa_passkey_challenge", None)
    labels = {
        "passkey": "Use your passkey to continue.",
        "email": "A security code was sent to your email.",
    }
    message = labels.get(method)
    if message:
        return redirect(f"/two-factor?info={urllib.parse.quote(message)}", code=303)
    return redirect("/two-factor", code=303)


@web_bp.post("/two-factor/passkey/begin")
@limiter.limit("10 per minute")
def two_factor_passkey_begin_action():
    from flask import jsonify

    pending_user_id = session.get("_2fa_pending_user_id")
    pending_at = session.get("_2fa_pending_at")
    if not pending_user_id or not isinstance(pending_at, int):
        return jsonify({"ok": False, "error": "Your verification session expired. Sign in again."}), 401
    if int(time.time()) - pending_at > 300:
        _clear_pending_2fa()
        return jsonify({"ok": False, "error": "Your verification session expired. Sign in again."}), 401
    if "passkey" not in _available_pending_2fa_methods(str(pending_user_id)):
        return jsonify({"ok": False, "error": "Passkey verification is not available."}), 400

    result = passkey_auth_begin(user_id=str(pending_user_id), ip_address=request.remote_addr)
    if not result:
        return jsonify({"ok": False, "error": "Unable to start passkey verification."}), 400
    session["_2fa_method"] = "passkey"
    session["_2fa_passkey_challenge"] = result["challenge"]
    session["_2fa_pending_at"] = int(time.time())
    return jsonify({"ok": True, "options": result["options"]})


@web_bp.post("/two-factor/passkey/verify")
@limiter.limit("10 per minute")
def two_factor_passkey_verify_action():
    from flask import jsonify
    import json

    pending_user_id = session.get("_2fa_pending_user_id")
    challenge_b64 = session.pop("_2fa_passkey_challenge", None)
    next_path = _safe_next(str(session.get("_2fa_next") or "/dashboard"))
    if not pending_user_id or not challenge_b64:
        return jsonify({"ok": False, "error": "No pending passkey verification. Try again."}), 400

    data = request.get_json(silent=True) or {}
    result = passkey_auth_complete(
        challenge_b64=str(challenge_b64),
        credential_json=json.dumps(data.get("credential", {})),
        expected_user_id=str(pending_user_id),
        ip_address=request.remote_addr,
        user_agent=request.headers.get("User-Agent"),
        create_session=True,
    )
    if not result.ok or not result.token:
        failures = int(session.get("_2fa_failures") or 0) + 1
        session["_2fa_failures"] = failures
        record_second_factor_failure(str(pending_user_id), ip_address=request.remote_addr)
        if failures >= 5:
            _clear_pending_2fa()
        return jsonify({"ok": False, "error": result.message}), 400

    _clear_pending_2fa()
    response = jsonify({"ok": True, "next": next_path})
    _set_session_cookie(response, result.token)
    return response


@web_bp.post("/settings/2fa/setup")
def totp_setup_action():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    data = generate_totp_setup(cs.user.id, cs.user.email)
    session["_2fa_setup_secret"] = data["secret"]
    session["_2fa_setup_uri"] = data["uri"]
    return redirect("/settings/2fa?setup=1", code=303)


@web_bp.post("/settings/2fa/setup-json")
def totp_setup_json_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = generate_totp_setup(cs.user.id, cs.user.email)
    session["_2fa_setup_secret"] = data["secret"]
    session["_2fa_setup_uri"] = data["uri"]
    qr_data = totp_qr_data_uri(data["uri"])
    if qr_data is None:
        return jsonify({"ok": False, "error": "Unable to generate QR code locally."}), 500
    return jsonify({"ok": True, "secret": data["secret"], "uri": data["uri"], "qr_url": qr_data})


@web_bp.post("/settings/2fa/confirm")
def totp_confirm_action():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    code = request.form.get("code", "").strip()
    result = confirm_totp_setup(cs.user.id, code)
    if result.ok:
        session["_2fa_backup_codes"] = result.token or ""
        session.pop("_2fa_setup_secret", None)
        session.pop("_2fa_setup_uri", None)
        return redirect("/settings/2fa?enabled=1", code=303)
    return redirect(f"/settings/2fa?error={urllib.parse.quote(result.message)}", code=303)


@web_bp.post("/settings/2fa/confirm-json")
@limiter.limit("10 per minute")
def totp_confirm_json_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    code = str(data.get("code", "")).strip()
    result = confirm_totp_setup(cs.user.id, code)
    if result.ok:
        codes = (result.token or "").split(",") if result.token else []
        session.pop("_2fa_setup_secret", None)
        session.pop("_2fa_setup_uri", None)
        return jsonify({"ok": True, "backup_codes": codes})
    return jsonify({"ok": False, "error": result.message})


@web_bp.post("/settings/2fa/disable")
@limiter.limit("5 per minute")
def totp_disable_action():
    from codesandbox.shared.session import require_session
    from werkzeug.security import check_password_hash
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    current_password = request.form.get("current_password", "")
    code = request.form.get("code", "").strip().replace(" ", "")
    password_ok = bool(
        cs.user.password_hash
        and current_password
        and check_password_hash(cs.user.password_hash, current_password)
    )
    totp_ok = bool(code and verify_totp(cs.user.id, code))
    if not password_ok and not totp_ok:
        return redirect(
            "/settings?tab=security&error=Confirm+with+your+password+or+2FA+code+to+disable+two-factor+authentication.",
            code=303,
        )
    disable_2fa(cs.user.id)
    return redirect("/settings?tab=security&info=Two-factor+authentication+disabled.", code=303)


@web_bp.post("/settings/change-password")
@limiter.limit("5 per minute")
def change_password_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    from werkzeug.security import check_password_hash, generate_password_hash
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    current = str(data.get("current_password", "")).strip()
    new_pw = str(data.get("new_password", "")).strip()
    confirm = str(data.get("confirm_password", "")).strip()
    if not new_pw or len(new_pw) < 8:
        return jsonify({"ok": False, "error": "Password must be at least 8 characters."}), 400
    if new_pw != confirm:
        return jsonify({"ok": False, "error": "Passwords do not match."}), 400
    if cs.user.password_hash:
        if not current:
            return jsonify({"ok": False, "error": "Current password is required."}), 400
        if not check_password_hash(cs.user.password_hash, current):
            return jsonify({"ok": False, "error": "Current password is incorrect."}), 400
    pw_hash = generate_password_hash(new_pw)
    repository.update_user(cs.user.id, password_hash=pw_hash)
    repository.delete_user_sessions(cs.user.id, except_token_hash=cs.token_hash)
    return jsonify({"ok": True})


# ── GitHub OAuth ──────────────────────────────────────────────────────────────

@web_bp.get("/auth/github")
def github_authorize():
    from codesandbox.shared.session import get_current_session
    if get_current_session():
        return redirect("/dashboard", code=303)
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
    from codesandbox.shared.session import get_current_session
    if get_current_session():
        return redirect("/dashboard", code=303)
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
@verified_email("linking social accounts")
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
@verified_email("linking social accounts")
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


@web_bp.post("/settings/profile")
def update_profile_action():
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip() or None
    if not name:
        return redirect("/settings?error=Name+cannot+be+empty.", code=303)
    repository.update_user(cs.user.id, name=name, phone=phone)
    return redirect("/settings?info=Profile+updated+successfully.", code=303)


@web_bp.post("/settings/update-field")
def settings_update_field_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    field = str(data.get("field", "")).strip()
    value = str(data.get("value", "")).strip() or None
    allowed = {"name", "phone"}
    if field not in allowed:
        return jsonify({"ok": False, "error": "Invalid field."}), 400
    if field == "name" and not value:
        return jsonify({"ok": False, "error": "Name is required."}), 400
    repository.update_user(cs.user.id, **{field: value})
    return jsonify({"ok": True})


@web_bp.post("/settings/update-email")
@limiter.limit("5 per hour")
def settings_update_email_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    from codesandbox.shared.email import send_email_verification
    import re
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    new_email = str(data.get("email", "")).strip().lower()
    if not new_email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", new_email):
        return jsonify({"ok": False, "error": "Enter a valid email address."}), 400
    if new_email == cs.user.email:
        return jsonify({"ok": False, "error": "That's already your email address."}), 400
    existing = repository.find_user_by_email(new_email)
    if existing and str(existing.id) != str(cs.user.id):
        return jsonify({"ok": False, "error": "That email is already in use."}), 400
    repository.update_user(cs.user.id, email=new_email, email_verified=False)
    settings = get_settings()
    token = request_email_verification(cs.user.id, new_email)
    verify_url = f"{settings.app_url}/verify-email?token={token}"
    send_email_verification(to=new_email, verify_url=verify_url)
    return jsonify({"ok": True})


@web_bp.post("/settings/upload-avatar")
def settings_upload_avatar_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    from codesandbox.shared.storage import upload_image_from_filestorage
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    avatar_url = upload_image_from_filestorage(request.files.get("logo"), prefix="avatars")
    if avatar_url is None:
        return jsonify({"ok": False, "error": "Invalid file. Use PNG, JPG, or WebP under 2 MB."}), 400
    repository.update_user(cs.user.id, avatar_url=avatar_url)
    return jsonify({"ok": True, "url": avatar_url, "media_key": f"user:{cs.user.id}"})


@web_bp.post("/settings/sessions/<session_id>/revoke")
def revoke_session_action(session_id: str):
    from codesandbox.shared.session import require_session, get_current_session
    cs, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    target = repository.find_session_by_id(session_id)
    if target is None or str(target.user_id) != str(cs.user.id):
        return redirect("/settings?tab=sessions&error=Session+not+found.", code=303)
    current = get_current_session()
    if current:
        current_obj = repository.find_active_session(current.token_hash)
        if current_obj and str(current_obj.id) == session_id:
            return redirect("/settings?tab=sessions&error=Use+Sign+Out+to+end+your+current+session.", code=303)
    repository.delete_session_by_id(session_id)
    return redirect("/settings?tab=sessions&info=Session+revoked.", code=303)


@web_bp.get("/settings/passkeys")
def list_passkeys_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    passkeys = get_user_passkeys(cs.user.id)
    return jsonify({"ok": True, "passkeys": passkeys})


@web_bp.post("/settings/passkeys/register/begin")
@limiter.limit("10 per minute")
def passkey_register_begin_action():
    from flask import jsonify, session as flask_session
    from codesandbox.shared.session import require_session
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    attachment = data.get("attachment") or None
    result = passkey_register_begin(cs.user.id, attachment=attachment)
    if not result:
        return jsonify({"ok": False, "error": "Unable to start passkey registration."}), 500
    flask_session["_passkey_challenge"] = result["challenge"]
    return jsonify({"ok": True, "options": result["options"]})


@web_bp.post("/settings/passkeys/register/complete")
@limiter.limit("10 per minute")
def passkey_register_complete_action():
    from flask import jsonify, session as flask_session
    from codesandbox.shared.session import require_session
    import json
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    challenge_b64 = flask_session.pop("_passkey_challenge", None)
    if not challenge_b64:
        return jsonify({"ok": False, "error": "No pending registration. Please start again."}), 400
    data = request.get_json(silent=True) or {}
    credential_json = json.dumps(data.get("credential", {}))
    passkey_name = str(data.get("name", "")).strip() or None
    result = passkey_register_complete(cs.user.id, challenge_b64, credential_json, passkey_name)
    if not result.ok:
        return jsonify({"ok": False, "error": result.message}), 400
    return jsonify({"ok": True, "message": result.message})


@web_bp.post("/settings/passkeys/<passkey_id>/delete")
def delete_passkey_action(passkey_id: str):
    from flask import jsonify
    from codesandbox.shared.session import require_session
    from . import repository
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    deleted = repository.delete_passkey(passkey_id, cs.user.id)
    if not deleted:
        return jsonify({"ok": False, "error": "Passkey not found."}), 404
    return jsonify({"ok": True})


@web_bp.post("/settings/passkeys/<passkey_id>/status")
def update_passkey_status_action(passkey_id: str):
    from flask import jsonify
    from codesandbox.shared.session import require_session
    from . import repository
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"ok": False, "error": "Missing passkey status."}), 400
    updated = repository.set_passkey_enabled(passkey_id, cs.user.id, bool(data.get("enabled")))
    if not updated:
        return jsonify({"ok": False, "error": "Passkey not found."}), 404
    return jsonify({"ok": True})


@web_bp.post("/settings/passkeys/status")
def update_account_passkey_status_action():
    from flask import jsonify
    from codesandbox.shared.session import require_session
    from . import repository
    cs, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"ok": False, "error": "Missing passkey status."}), 400
    updated = repository.set_user_passkeys_enabled(cs.user.id, bool(data.get("enabled")))
    if updated == 0:
        return jsonify({"ok": False, "error": "No passkeys found."}), 404
    return jsonify({"ok": True, "updated": updated})


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
