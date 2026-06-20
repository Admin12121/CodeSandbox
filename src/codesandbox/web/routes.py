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
    if not flask_session.get("_2fa_pending_token"):
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
        "total_pages": total_pages,
        "search": search,
        "role_filter": role,
        "status_filter": status,
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
        selected_org = {"id": None, "name": "", "slug": "", "description": "", "website": "", "industry": "", "size": "", "location": "", "contact_email": "", "status": "active", "member_count": 0}
    elif org_param:
        from codesandbox.features.organizations.repository import get_organization, get_member_count
        _org = get_organization(org_param)
        if _org:
            selected_org = {
                "id": _org.id,
                "name": _org.name,
                "slug": _org.slug,
                "description": _org.description or "",
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
        visible_staff = [
            m for m in staff
            if q in m["name"].lower() or q in m["email"].lower()
            or any(q in r["name"].lower() for r in m["roles"])
        ]
    else:
        visible_staff = staff

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
        visible_roles = [
            r for r in rbac["roles"]
            if q in r["display_name"].lower() or q in r["description"].lower()
        ]
    else:
        visible_roles = rbac["roles"]

    member_ids = {m["id"] for m in (selected_role["members"] if selected_role else [])}
    available_members = [m for m in staff if m["id"] not in member_ids]

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
        "available_members": available_members,
        "error": request.args.get("error"),
    }


# ── Settings ──────────────────────────────────────────────────────────────────


@router.page("/settings")
def settings():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/settings", user)
    from codesandbox.features.identity.repository import get_totp_method
    totp = get_totp_method(user.id)
    return {
        "_meta": {"title": "Settings — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Account Settings",
        "page_description": "Manage your account and security",
        "info": request.args.get("info"),
        "totp_enabled": totp.is_enabled if totp else False,
        **_workspaces_ctx(user),
    }


@router.page("/settings/2fa")
def settings_2fa():
    cs, redir = require_session()
    if redir:
        return redir
    from codesandbox.features.identity.repository import get_totp_method
    totp = get_totp_method(cs.user.id)
    nav = build_nav("/settings", cs.user)
    return {
        "_meta": {"title": "Two-Factor Auth — CodeSandbox"},
        "user": _user_ctx(cs.user),
        "nav": nav,
        "page_title": "Two-Factor Authentication",
        "secret": request.args.get("secret"),
        "uri": request.args.get("uri"),
        "enabled": bool(request.args.get("enabled")),
        "backup": request.args.get("backup", "").split(",") if request.args.get("backup") else [],
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


@router.page("/my/organizations/<slug>")
def my_organization_detail(slug: str):
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/my/organizations", user)

    from codesandbox.features.organizations.service import get_org_for_user
    org = get_org_for_user(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}

    tab = request.args.get("tab", "members")
    if tab not in ("members", "settings"):
        tab = "members"

    invite_link_raw = request.args.get("invite_link")
    invite_link = None
    if invite_link_raw:
        import urllib.parse
        invite_link = urllib.parse.unquote(invite_link_raw)

    return {
        "_meta": {"title": f"{org['name']} — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": org["name"],
        "org": org,
        "active_tab": tab,
        "invite_link": invite_link,
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
        return {"workspace_list": None, "active_workspace": None}
    from flask import g
    if not hasattr(g, "_workspace_list"):
        from codesandbox.features.organizations.service import get_user_org_list
        g._workspace_list = get_user_org_list(user.id)
    return {
        "workspace_list": g._workspace_list,
        "active_workspace": active_workspace,
    }
