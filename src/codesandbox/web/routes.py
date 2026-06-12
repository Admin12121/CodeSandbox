from __future__ import annotations

import math

from flask import request

from codesandbox.features.organizations.service import get_platform_organizations
from codesandbox.features.platform_admin.service import (
    get_platform_rbac,
    get_platform_staff,
    get_platform_users,
)
from codesandbox.features.identity import repository as identity_repo
from codesandbox.shared.session import build_nav, format_role_label, require_session, require_platform_role
from codesandbox.web.blueprint import router


# ── Auth pages ──────────────────────────────────────────────────────────────


@router.page("/")
def home():
    return {"_redirect": "/dashboard"}


@router.page("/login")
def login():
    return {
        "_meta": {"title": "Sign in — CodeSandbox"},
        "mode": "signin",
        "error": None,
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

    return {
        "_meta": {"title": "Organizations — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Organizations",
        "page_description": "All tenant organizations on the platform",
        "organizations": orgs,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "search": search,
        "status_filter": status,
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
    return {
        "_meta": {"title": "Application Staff — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Application Staff",
        "page_description": "Platform admins and staff members",
        "staff": staff,
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
    return {
        "_meta": {"title": "Roles — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Application Roles",
        "page_description": "Platform-level roles and permission assignments",
        "roles": rbac["roles"],
        "permission_groups": rbac["permission_groups"],
    }


# ── Settings ──────────────────────────────────────────────────────────────────


@router.page("/settings")
def settings():
    session, redirect = require_session()
    if redirect:
        return redirect
    user = session.user
    nav = build_nav("/settings", user)
    return {
        "_meta": {"title": "Settings — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Account Settings",
        "page_description": "Manage your account and security",
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
