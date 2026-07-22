from __future__ import annotations

import math

from flask import request

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.organizations.service import get_platform_organizations
from codesandbox.features.platform_admin import repository as platform_admin_repo
from codesandbox.features.platform_admin.service import (
    get_platform_rbac,
    get_platform_staff,
    get_platform_users,
)
from codesandbox.shared.permissions import has_platform_permission
from codesandbox.shared.session import build_nav, format_role_label, require_platform_role
from codesandbox.web.blueprint import router
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx


@router.page("/platform/users")
def platform_users():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    if user.platform_role == "system_staff" and not has_platform_permission(user, "platform.users.read"):
        return {"_redirect": "/dashboard"}
    can_edit = has_platform_permission(user, "platform.users.edit")
    can_change_status = has_platform_permission(user, "platform.users.status")
    can_change_roles = has_platform_permission(user, "platform.users.roles")
    can_manage = can_edit or can_change_status or can_change_roles
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
        **_workspaces_ctx(user),
        "users": users_data,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "search": search,
        "role_filter": role,
        "status_filter": status,
        "can_manage": can_manage,
        "can_edit": can_edit,
        "can_change_status": can_change_status,
        "can_change_roles": can_change_roles,
    }


@router.page("/platform/users/<username>")
def platform_user_detail(username: str):
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    current_user = session.user
    if current_user.platform_role == "system_staff" and not has_platform_permission(current_user, "platform.users.read"):
        return {"_redirect": "/dashboard"}
    can_edit = has_platform_permission(current_user, "platform.users.edit")
    can_change_status = has_platform_permission(current_user, "platform.users.status")
    can_change_roles = has_platform_permission(current_user, "platform.users.roles")
    can_manage = can_edit or can_change_status or can_change_roles
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
        "can_manage": can_manage,
        "can_edit": can_edit,
        "can_change_status": can_change_status,
        "can_change_roles": can_change_roles,
    }


@router.page("/platform/organizations")
def platform_organizations():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    if user.platform_role == "system_staff" and not has_platform_permission(user, "platform.organizations.read"):
        return {"_redirect": "/dashboard"}
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
        selected_org = {
            "id": None, "name": "", "slug": "", "description": "",
            "logo_url": "", "website": "", "industry": "", "size": "",
            "location": "", "contact_email": "", "status": "active", "member_count": 0,
        }
    elif org_param:
        from codesandbox.features.organizations.repository import get_member_count, get_organization
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
        "page_description": "All tenant organizations on the platform",
        **_workspaces_ctx(user),
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


@router.page("/platform/staff")
def platform_staff():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    if user.platform_role == "system_staff" and not has_platform_permission(user, "platform.staff.read"):
        return {"_redirect": "/dashboard"}
    is_super_admin = user.platform_role == "system_admin"
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
        "page_description": "Platform admins and staff members",
        **_workspaces_ctx(user),
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
        "is_super_admin": is_super_admin,
    }


@router.page("/platform/roles")
def platform_roles():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    if user.platform_role == "system_staff" and not has_platform_permission(user, "platform.roles.read"):
        return {"_redirect": "/dashboard"}
    nav = build_nav("/platform/roles", user)
    platform_admin_repo.seed_default_permissions()
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
        "page_description": "Platform-level roles and permission assignments",
        **_workspaces_ctx(user),
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
