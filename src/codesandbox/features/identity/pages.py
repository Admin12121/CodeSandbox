from __future__ import annotations

import math
from datetime import datetime, timezone as _tz

from flask import request, session as flask_session

from codesandbox.config import get_settings
from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.identity.service import totp_qr_data_uri
from codesandbox.shared.session import build_nav, format_role_label, get_current_session, require_session
from codesandbox.web.blueprint import router
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx


@router.page("/")
def home():
    if get_current_session():
        return {"_redirect": "/dashboard"}
    return {"_meta": {"title": "CodeSandbox — Cloud sandboxes on demand"}}


@router.page("/login")
def login():
    if get_current_session():
        return {"_redirect": "/dashboard"}
    return {
        "_meta": {"title": "Sign in — CodeSandbox"},
        "mode": request.args.get("mode", "signin"),
        "error": request.args.get("error"),
        "info": request.args.get("info"),
        "next_path": request.args.get("next", "/dashboard"),
    }


@router.page("/forgot-password")
def forgot_password():
    if get_current_session():
        return {"_redirect": "/dashboard"}
    return {
        "_meta": {"title": "Forgot Password — CodeSandbox"},
        "sent": bool(request.args.get("sent")),
        "error": request.args.get("error"),
    }


@router.page("/reset-password")
def reset_password_page():
    if get_current_session():
        return {"_redirect": "/dashboard"}
    token = request.args.get("token", "")
    if not token:
        return {"_redirect": "/forgot-password"}
    return {
        "_meta": {"title": "Reset Password — CodeSandbox"},
        "token": token,
        "error": request.args.get("error"),
    }


@router.page("/two-factor")
def two_factor():
    if get_current_session():
        return {"_redirect": "/dashboard"}
    if not flask_session.get("_2fa_pending_user_id"):
        return {"_redirect": "/login"}
    pending_user_id = str(flask_session.get("_2fa_pending_user_id"))
    method = str(flask_session.get("_2fa_method") or "totp")
    user = identity_repo.find_user_by_id(pending_user_id)
    available_methods: list[str] = []
    if user is not None:
        if identity_repo.get_user_passkeys(pending_user_id, enabled_only=True):
            available_methods.append("passkey")
        if user.two_factor_enabled:
            available_methods.append("totp")
        settings = get_settings()
        if settings.resend_api_key and user.email_verified:
            available_methods.append("email")
    return {
        "_meta": {"title": "Security Verification — CodeSandbox"},
        "error": request.args.get("error"),
        "info": request.args.get("info"),
        "challenge_method": method,
        "available_methods": available_methods,
    }


@router.page("/settings")
def settings():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    nav = build_nav("/settings", user, ws_ctx.get("active_workspace"))

    totp = identity_repo.get_totp_method(user.id)
    accounts = identity_repo.get_user_auth_accounts(user.id)
    connected_providers = {a.provider for a in accounts}
    raw_sessions = identity_repo.list_user_sessions(user.id)

    current_session_id: str | None = None
    current_obj = identity_repo.find_active_session(session.token_hash)
    if current_obj:
        current_session_id = str(current_obj.id)

    now = datetime.now(_tz.utc)
    sessions_data = []
    for s in raw_sessions:
        expires = s.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=_tz.utc)
        sessions_data.append({
            "id": str(s.id),
            "ip_address": s.ip_address or "Unknown",
            "user_agent": s.user_agent or "",
            "created_at": s.created_at,
            "expires_at": s.expires_at,
            "is_current": str(s.id) == current_session_id,
            "is_expired": expires < now,
        })

    sess_page_size = 10
    sess_page = max(1, int(request.args.get("spage", "1") or "1"))
    sess_total = len(sessions_data)
    sess_total_pages = max(1, math.ceil(sess_total / sess_page_size))
    sess_page = min(sess_page, sess_total_pages)
    paged_sessions = sessions_data[(sess_page - 1) * sess_page_size : sess_page * sess_page_size]

    return {
        "_meta": {"title": "Settings — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Account Settings",
        "page_description": "Profile, sign-in, recovery, and active device controls.",
        "info": request.args.get("info"),
        "error": request.args.get("error"),
        "totp_enabled": totp.is_enabled if totp else False,
        "totp_verified": bool(totp.verified_at) if totp else False,
        "connected_providers": connected_providers,
        "auth_accounts": [
            {"provider": a.provider, "account_id": a.provider_account_id, "created_at": a.created_at}
            for a in accounts
        ],
        "has_password": bool(user.password_hash),
        "sessions": paged_sessions,
        "sessions_total": sess_total,
        "sessions_page": sess_page,
        "sessions_page_size": sess_page_size,
        "sessions_total_pages": sess_total_pages,
        "current_session_id": current_session_id,
        "settings_user": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "phone": user.phone or "",
            "avatar_url": getattr(user, "avatar_url", None) or "",
            "platform_role": user.platform_role,
            "role_label": format_role_label(user.platform_role),
            "status": user.status,
            "email_verified": user.email_verified,
            "two_factor_enabled": user.two_factor_enabled,
            "last_login_at": user.last_login_at,
            "created_at": user.created_at,
            "updated_at": user.updated_at,
        },
        **ws_ctx,
    }


@router.page("/settings/2fa")
def settings_2fa():
    cs, redir = require_session()
    if redir:
        return redir
    totp = identity_repo.get_totp_method(cs.user.id)
    ws_ctx = _workspaces_ctx(cs.user)
    nav = build_nav("/settings", cs.user, ws_ctx.get("active_workspace"))
    secret = flask_session.get("_2fa_setup_secret") if request.args.get("setup") else None
    uri = flask_session.get("_2fa_setup_uri") if request.args.get("setup") else None
    qr_url = totp_qr_data_uri(uri) if uri else None
    backup_raw = flask_session.pop("_2fa_backup_codes", "") if request.args.get("enabled") else ""
    backup = [c for c in backup_raw.split(",") if c] if backup_raw else []
    return {
        "_meta": {"title": "Two-Factor Auth — CodeSandbox"},
        "user": _user_ctx(cs.user),
        "nav": nav,
        "page_title": "Two-Factor Authentication",
        "secret": secret,
        "uri": uri,
        "qr_url": qr_url,
        "enabled": bool(request.args.get("enabled")),
        "backup": backup,
        "error": request.args.get("error"),
        "totp_enabled": totp.is_enabled if totp else False,
        **ws_ctx,
    }
