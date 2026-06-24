from __future__ import annotations

import math
import os

from flask import abort, request, send_from_directory

from codesandbox.features.organizations.service import get_platform_organizations
from codesandbox.features.platform_admin.service import (
    get_platform_rbac,
    get_platform_staff,
    get_platform_users,
)
from codesandbox.features.identity import repository as identity_repo
from codesandbox.shared.session import build_nav, format_role_label, require_session, require_platform_role
from codesandbox.web.blueprint import router, web_bp

_TEMPLATES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../templates"))
_PUBLIC_DIR = os.path.join(_TEMPLATES_DIR, "public")
_FAVICON = os.path.join(_TEMPLATES_DIR, "favicon.ico")


@web_bp.get("/favicon.ico")
def _favicon():
    if os.path.isfile(_FAVICON):
        return send_from_directory(_TEMPLATES_DIR, "favicon.ico", mimetype="image/x-icon")
    abort(404)


@web_bp.get("/<path:filename>")
def _public_static(filename):
    target = os.path.normpath(os.path.join(_PUBLIC_DIR, filename))
    if target.startswith(_PUBLIC_DIR) and os.path.isfile(target):
        return send_from_directory(_PUBLIC_DIR, filename)
    abort(404)


# ── Auth pages ──────────────────────────────────────────────────────────────


@router.page("/")
def home():
    return {"_redirect": "/dashboard"}


@router.page("/login")
def login():
    mode = request.args.get("mode", "signin")
    return {
        "_meta": {"title": "Sign in — CodeSandbox"},
        "mode": mode,
        "error": request.args.get("error"),
        "info": request.args.get("info"),
        "next_path": request.args.get("next", "/dashboard"),
    }


@router.page("/forgot-password")
def forgot_password():
    return {
        "_meta": {"title": "Forgot Password — CodeSandbox"},
        "sent": bool(request.args.get("sent")),
        "dev_url": request.args.get("dev_url"),
        "error": request.args.get("error"),
    }


@router.page("/reset-password")
def reset_password_page():
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
    from flask import session as flask_session
    if not flask_session.get("_2fa_pending_user_id"):
        return {"_redirect": "/login"}
    return {
        "_meta": {"title": "Two-Factor Auth — CodeSandbox"},
        "error": request.args.get("error"),
    }




# ── Platform admin dashboard ─────────────────────────────────────────────────


@router.page("/dashboard")
def dashboard():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/dashboard", user)

    try:
        user_count = identity_repo.list_users()[1]
    except Exception:
        user_count = 0
    try:
        from codesandbox.features.organizations.repository import list_organizations
        _, org_count = list_organizations()
    except Exception:
        org_count = 0

    metrics = [
        {"label": "Total Users", "value": str(user_count), "change": "Platform accounts"},
        {"label": "Organizations", "value": str(org_count), "change": "Active tenants"},
        {"label": "Running Sandboxes", "value": "0", "change": "Runtime worker pending"},
        {"label": "Open Cases", "value": "0", "change": "Case workflow pending"},
    ]
    return {
        "_meta": {"title": "Dashboard — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Dashboard",
        "page_description": "Platform overview — users, orgs, runtime, cases",
        "metrics": metrics,
        **_workspaces_ctx(user),
    }


# ── Platform users ────────────────────────────────────────────────────────────


@router.page("/platform/users")
def platform_users():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/platform/users", user)

    search = request.args.get("search", "").strip()
    role = request.args.get("role", "all")
    status = request.args.get("status", "all")
    page = max(1, int(request.args.get("page", "1") or "1"))
    page_size = 25

    users, total = get_platform_users(
        search=search or None,
        role=role if role != "all" else None,
        status=status if status != "all" else None,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, math.ceil(total / page_size))

    users_data = [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "platform_role": u.platform_role,
            "role_label": format_role_label(u.platform_role),
            "status": u.status,
            "email_verified": u.email_verified,
            "last_login_at": u.last_login_at,
            "created_at": u.created_at,
        }
        for u in users
    ]

    return {
        "_meta": {"title": "Users — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Platform Users",
        **_workspaces_ctx(user),
        "page_description": "All accounts — admins, staff, and regular users",
        "users": users_data,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "search": search,
        "role_filter": role,
        "status_filter": status,
    }


# ── Platform user detail ─────────────────────────────────────────────────────


@router.page("/platform/users/<username>")
def platform_user_detail(username: str):
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    current_user = session.user
    nav = build_nav("/platform/users", current_user)

    target_user = identity_repo.find_user_by_email(username) or identity_repo.find_user_by_id(username)
    if target_user is None:
        return {"_redirect": "/platform/users"}

    auth_accounts = identity_repo.get_user_auth_accounts(target_user.id)
    totp = identity_repo.get_totp_method(target_user.id)

    from codesandbox.features.organizations.service import get_user_org_list
    user_orgs = get_user_org_list(target_user.id)
    owned_orgs = [o for o in user_orgs if o.get("created_by") == target_user.id]
    member_orgs = [o for o in user_orgs if o.get("created_by") != target_user.id]

    return {
        "_meta": {"title": f"{target_user.name} — CodeSandbox"},
        "user": _user_ctx(current_user),
        "nav": nav,
        "page_title": target_user.name,
        "page_description": target_user.email,
        **_workspaces_ctx(current_user),
        "target_user": {
            "id": str(target_user.id),
            "name": target_user.name,
            "email": target_user.email,
            "phone": target_user.phone or "",
            "avatar_url": getattr(target_user, "avatar_url", None) or "",
            "platform_role": target_user.platform_role,
            "role_label": format_role_label(target_user.platform_role),
            "status": target_user.status,
            "email_verified": target_user.email_verified,
            "two_factor_enabled": target_user.two_factor_enabled,
            "last_login_at": target_user.last_login_at,
            "created_at": target_user.created_at,
            "updated_at": target_user.updated_at,
            "has_password": bool(target_user.password_hash),
        },
        "totp": {
            "enabled": totp.is_enabled if totp else False,
            "verified": bool(totp.verified_at) if totp else False,
        },
        "auth_accounts": [
            {
                "provider": a.provider,
                "account_id": a.provider_account_id,
                "created_at": a.created_at,
            }
            for a in auth_accounts
        ],
        "owned_orgs": owned_orgs,
        "member_orgs": member_orgs,
        "error": request.args.get("error"),
        "info": request.args.get("info"),
    }


# ── Platform organizations ────────────────────────────────────────────────────


@router.page("/platform/organizations")
def platform_organizations():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/platform/organizations", user)

    search = request.args.get("search", "").strip()
    status = request.args.get("status", "all")
    page = max(1, int(request.args.get("page", "1") or "1"))
    page_size = 25

    orgs, total = get_platform_organizations(
        search=search or None,
        status=status if status != "all" else None,
        page=page,
        page_size=page_size,
    )
    total_pages = max(1, math.ceil(total / page_size))

    org_param = request.args.get("org") or None
    selected_org = None
    if org_param == "new":
        selected_org = {"id": None, "name": "", "slug": "", "description": "", "logo_url": "", "website": "", "industry": "", "size": "", "location": "", "contact_email": "", "status": "active", "member_count": 0}
    elif org_param:
        from codesandbox.features.organizations.repository import get_organization, get_member_count
        _org = get_organization(org_param)
        if _org:
            selected_org = {
                "id": _org.id,
                "name": _org.name,
                "slug": _org.slug,
                "description": _org.description or "",
                "logo_url": _org.logo_url or "",
                "website": _org.website or "",
                "industry": _org.industry or "",
                "size": _org.size or "",
                "location": _org.location or "",
                "contact_email": _org.contact_email or "",
                "status": _org.status,
                "member_count": get_member_count(_org.id),
            }

    return {
        "_meta": {"title": "Organizations — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Organizations",
        **_workspaces_ctx(user),
        "page_description": "All tenant organizations on the platform",
        "organizations": orgs,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "search": search,
        "status_filter": status,
        "selected_org": selected_org,
        "error": request.args.get("error"),
    }


# ── Platform staff ────────────────────────────────────────────────────────────


@router.page("/platform/staff")
def platform_staff():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/platform/staff", user)
    staff = get_platform_staff()
    rbac = get_platform_rbac()

    member_param = request.args.get("member") or None
    search = request.args.get("search", "").strip()
    page = max(1, int(request.args.get("page", "1") or "1"))
    page_size = 20
    selected_member = None
    if member_param == "new":
        selected_member = {
            "id": None, "name": "", "email": "", "phone": "",
            "status": "active", "role_ids": [], "roles": [],
        }
    elif member_param:
        selected_member = next((m for m in staff if str(m["id"]) == member_param), None)

    if search:
        q = search.lower()
        filtered_staff = [
            m for m in staff
            if q in m["name"].lower() or q in m["email"].lower()
            or any(q in r["name"].lower() for r in m["roles"])
        ]
    else:
        filtered_staff = staff

    total_staff = len(filtered_staff)
    total_pages_staff = max(1, math.ceil(total_staff / page_size))
    page = min(page, total_pages_staff)
    visible_staff = filtered_staff[(page - 1) * page_size : page * page_size]

    return {
        "_meta": {"title": "Application Staff — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Application Staff",
        **_workspaces_ctx(user),
        "page_description": "Platform admins and staff members",
        "staff": staff,
        "visible_staff": visible_staff,
        "roles": rbac["roles"],
        "selected_member": selected_member,
        "search": search,
        "page": page,
        "page_size": page_size,
        "total": total_staff,
        "total_pages": total_pages_staff,
        "error": request.args.get("error"),
    }


# ── Platform roles ────────────────────────────────────────────────────────────


@router.page("/platform/roles")
def platform_roles():
    session, redirect = require_platform_role("system_admin")
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/platform/roles", user)
    rbac = get_platform_rbac()
    staff = get_platform_staff()

    role_param = request.args.get("role") or None
    tab = request.args.get("tab", "display")
    if tab not in ("display", "permissions", "sidebar", "members"):
        tab = "display"
    search = request.args.get("search", "").strip()
    page = max(1, int(request.args.get("page", "1") or "1"))
    page_size = 20

    selected_role = None
    if role_param == "new":
        selected_role = {
            "id": None, "name": "", "display_name": "New role",
            "color": "#6366f1", "description": "", "is_system": False,
            "is_mutable": True, "position": 0, "permission_keys": [],
            "member_count": 0, "members": [],
        }
    elif role_param:
        selected_role = next((r for r in rbac["roles"] if str(r["id"]) == role_param), None)

    if search:
        q = search.lower()
        filtered_roles = [
            r for r in rbac["roles"]
            if q in r["display_name"].lower() or q in r["description"].lower()
        ]
    else:
        filtered_roles = rbac["roles"]

    total_roles = len(filtered_roles)
    total_pages_roles = max(1, math.ceil(total_roles / page_size))
    page = min(page, total_pages_roles)
    visible_roles = filtered_roles[(page - 1) * page_size : page * page_size]

    member_ids = {m["id"] for m in (selected_role["members"] if selected_role else [])}
    available_members = [m for m in staff if m["id"] not in member_ids]

    # Members-tab pagination
    members_page_size = 20
    members_page = max(1, int(request.args.get("mpage", "1") or "1"))
    all_members = selected_role["members"] if selected_role else []
    members_total = len(all_members)
    members_total_pages = max(1, math.ceil(members_total / members_page_size))
    members_page = min(members_page, members_total_pages)
    paged_members = all_members[(members_page - 1) * members_page_size : members_page * members_page_size]
    if selected_role:
        selected_role = {**selected_role, "members": paged_members}

    return {
        "_meta": {"title": "Staff Roles — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Staff Roles",
        **_workspaces_ctx(user),
        "page_description": "Platform-level roles and permission assignments",
        "roles": rbac["roles"],
        "visible_roles": visible_roles,
        "permission_groups": rbac["permission_groups"],
        "nav_matrix": rbac["nav_matrix"],
        "selected_role": selected_role,
        "editor_tab": tab,
        "search": search,
        "page": page,
        "page_size": page_size,
        "total": total_roles,
        "total_pages": total_pages_roles,
        "available_members": available_members,
        "members_page": members_page,
        "members_page_size": members_page_size,
        "members_total": members_total,
        "members_total_pages": members_total_pages,
        "error": request.args.get("error"),
    }


# ── Settings ──────────────────────────────────────────────────────────────────


@router.page("/settings")
def settings():
    from datetime import datetime, timezone as _tz
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/settings", user)

    totp = identity_repo.get_totp_method(user.id)
    accounts = identity_repo.get_user_auth_accounts(user.id)
    connected_providers = {a.provider for a in accounts}
    raw_sessions = identity_repo.list_user_sessions(user.id)

    # Identify current session by token_hash stored on CurrentSession
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
        "dev_url": request.args.get("dev_url"),
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
        **_workspaces_ctx(user),
    }


@router.page("/settings/2fa")
def settings_2fa():
    from flask import session as flask_session
    cs, redir = require_session()
    if redir:
        return redir
    from codesandbox.features.identity.repository import get_totp_method
    totp = get_totp_method(cs.user.id)
    nav = build_nav("/settings", cs.user)
    # Read setup data from server-side session (never from URL for security)
    secret = flask_session.get("_2fa_setup_secret") if request.args.get("setup") else None
    uri = flask_session.get("_2fa_setup_uri") if request.args.get("setup") else None
    # One-time backup codes display then clear
    backup_raw = flask_session.pop("_2fa_backup_codes", "") if request.args.get("enabled") else ""
    backup = [c for c in backup_raw.split(",") if c] if backup_raw else []
    return {
        "_meta": {"title": "Two-Factor Auth — CodeSandbox"},
        "user": _user_ctx(cs.user),
        "nav": nav,
        "page_title": "Two-Factor Authentication",
        "secret": secret,
        "uri": uri,
        "enabled": bool(request.args.get("enabled")),
        "backup": backup,
        "error": request.args.get("error"),
        "totp_enabled": totp.is_enabled if totp else False,
        **_workspaces_ctx(cs.user),
    }


# ── My Organizations ──────────────────────────────────────────────────────────


@router.page("/my/organizations")
def my_organizations():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    if user.platform_role in ("system_admin", "system_staff"):
        return {"_redirect": "/dashboard"}
    nav = build_nav("/my/organizations", user)

    from codesandbox.features.organizations.service import get_user_org_list
    organizations = get_user_org_list(user.id)
    user_owns_org = any(o.get("created_by") == user.id for o in organizations)

    return {
        "_meta": {"title": "My Organizations — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "My Organizations",
        "page_description": "Organizations you belong to",
        "organizations": organizations,
        "user_owns_org": user_owns_org,
        "info": request.args.get("info"),
        "error": request.args.get("error"),
        **_workspaces_ctx(user),
    }


def _set_active_workspace(org: dict) -> None:
    from flask import session as flask_session
    flask_session["active_workspace_slug"] = org["slug"]


def _load_org_or_redirect(slug: str, user_id: str):
    from codesandbox.features.organizations.service import get_org_for_user
    return get_org_for_user(slug, user_id)


@router.page("/my/organizations/<slug>")
def my_organization_detail(slug: str):
    session, redir = require_session()
    if redir:
        return redir
    # Redirect old tab= links to sub-pages
    tab = request.args.get("tab")
    if tab in ("members", "roles", "settings"):
        return {"_redirect": f"/my/organizations/{slug}/{tab}"}

    user = session.user
    nav = build_nav("/my/organizations", user)
    org = _load_org_or_redirect(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    _set_active_workspace(org)

    return {
        "_meta": {"title": f"{org['name']} — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": org["name"],
        "org": org,
        "info": request.args.get("info"),
        "error": request.args.get("error"),
        **_workspaces_ctx(user, active_workspace=org),
    }


@router.page("/my/organizations/<slug>/members")
def my_organization_members(slug: str):
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    nav = build_nav("/my/organizations", user)
    org = _load_org_or_redirect(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    if not org["can_manage_members"] and not org["can_invite"]:
        return {"_redirect": f"/my/organizations/{slug}"}
    _set_active_workspace(org)

    search = request.args.get("search", "").strip()
    role_filter = request.args.get("role", "all")
    page = max(1, int(request.args.get("page", "1") or "1"))
    page_size = 20

    all_members = list(org["members"])
    if search:
        q = search.lower()
        all_members = [m for m in all_members if q in m["name"].lower() or q in m["email"].lower()]
    if role_filter and role_filter != "all":
        all_members = [m for m in all_members if role_filter in m["roles"]]

    total = len(all_members)
    total_pages = max(1, math.ceil(total / page_size))
    page = min(page, total_pages)
    members = all_members[(page - 1) * page_size : page * page_size]

    import urllib.parse as _up
    invite_link_raw = request.args.get("invite_link")
    invite_link = _up.unquote(invite_link_raw) if invite_link_raw else None

    return {
        "_meta": {"title": f"Members — {org['name']}"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": org["name"],
        "org": org,
        "members": members,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "search": search,
        "role_filter": role_filter,
        "invite_link": invite_link,
        "info": request.args.get("info"),
        "error": request.args.get("error"),
        **_workspaces_ctx(user, active_workspace=org),
    }


@router.page("/my/organizations/<slug>/roles")
def my_organization_roles(slug: str):
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    nav = build_nav("/my/organizations", user)
    org = _load_org_or_redirect(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    if not org["is_owner"]:
        return {"_redirect": f"/my/organizations/{slug}"}
    _set_active_workspace(org)

    from codesandbox.features.organizations.repository import (
        ensure_org_permissions_seeded,
        get_all_org_permissions,
    )
    ensure_org_permissions_seeded()
    all_perms = get_all_org_permissions()
    org_permissions = [
        {"id": p.id, "key": p.key, "label": p.label, "group": p.group}
        for p in all_perms
    ]

    role_id = request.args.get("role")
    editor_tab = request.args.get("tab", "display")
    if editor_tab not in ("display", "permissions", "members"):
        editor_tab = "display"

    selected_role = None
    is_create = False
    role_members: list = []
    available_members: list = []

    if role_id == "new":
        is_create = True
        selected_role = {
            "id": None, "name": "", "color": "#6366f1",
            "description": "", "is_system": False,
            "member_count": 0, "permission_keys": [],
        }
    elif role_id:
        selected_role = next((r for r in org["roles"] if r["id"] == role_id), None)
        if selected_role:
            from codesandbox.features.organizations.service import get_role_members_for_org
            role_members = get_role_members_for_org(org["id"], role_id)
            in_role_ids = {m["id"] for m in role_members}
            available_members = [m for m in org["members"] if m["id"] not in in_role_ids]

    # Members-tab pagination
    org_members_page_size = 20
    org_members_page = max(1, int(request.args.get("mpage", "1") or "1"))
    org_members_total = len(role_members)
    org_members_total_pages = max(1, math.ceil(org_members_total / org_members_page_size))
    org_members_page = min(org_members_page, org_members_total_pages)
    paged_role_members = role_members[(org_members_page - 1) * org_members_page_size : org_members_page * org_members_page_size]

    return {
        "_meta": {"title": f"Roles — {org['name']}"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": org["name"],
        "org": org,
        "selected_role": selected_role,
        "is_create": is_create,
        "role_id": role_id,
        "editor_tab": editor_tab,
        "org_permissions": org_permissions,
        "role_members": paged_role_members,
        "available_members": available_members,
        "org_members_page": org_members_page,
        "org_members_page_size": org_members_page_size,
        "org_members_total": org_members_total,
        "org_members_total_pages": org_members_total_pages,
        "info": request.args.get("info"),
        "error": request.args.get("error"),
        **_workspaces_ctx(user, active_workspace=org),
    }


@router.page("/my/organizations/<slug>/settings")
def my_organization_settings(slug: str):
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    nav = build_nav("/my/organizations", user)
    org = _load_org_or_redirect(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    if not org["can_edit_settings"]:
        return {"_redirect": f"/my/organizations/{slug}"}
    _set_active_workspace(org)

    return {
        "_meta": {"title": f"Settings — {org['name']}"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": org["name"],
        "org": org,
        "info": request.args.get("info"),
        "error": request.args.get("error"),
        **_workspaces_ctx(user, active_workspace=org),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _user_ctx(user) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "platform_role": user.platform_role,
        "role_label": format_role_label(user.platform_role),
        "status": user.status,
    }


def _workspaces_ctx(user, active_workspace=None) -> dict:
    """Workspace switcher context. Returns None values for admin/staff users."""
    if user.platform_role in ("system_admin", "system_staff"):
        return {
            "workspace_list": None,
            "active_workspace": None,
            "_layout_state": {
                "role": user.platform_role,
                "active_workspace": None,
                "workspaces": [],
            },
        }
    from flask import g, session as flask_session
    if not hasattr(g, "_workspace_list"):
        from codesandbox.features.organizations.service import get_user_org_list
        g._workspace_list = get_user_org_list(user.id)
    workspace_list = g._workspace_list
    if active_workspace is None:
        persisted_slug = flask_session.get("active_workspace_slug")
        if persisted_slug:
            active_workspace = next(
                (w for w in workspace_list if w["slug"] == persisted_slug), None
            )
    return {
        "workspace_list": workspace_list,
        "active_workspace": active_workspace,
        "_layout_state": {
            "role": user.platform_role,
            "active_workspace": _workspace_state_item(active_workspace),
            "workspaces": [_workspace_state_item(workspace) for workspace in workspace_list],
        },
    }


def _workspace_state_item(workspace) -> dict | None:
    if not workspace:
        return None
    return {
        "id": workspace.get("id"),
        "slug": workspace.get("slug"),
        "name": workspace.get("name"),
        "logo_url": workspace.get("logo_url") or "",
        "status": workspace.get("status"),
        "member_count": workspace.get("member_count"),
        "is_owner": bool(workspace.get("is_owner")),
    }
