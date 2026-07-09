from __future__ import annotations

import math
import urllib.parse as _up

from flask import request, session as flask_session

from codesandbox.features.organizations.service import get_org_for_user
from codesandbox.shared.session import build_nav, require_session
from codesandbox.web.blueprint import router
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx


def _set_active_workspace(org: dict) -> None:
    flask_session["active_workspace_slug"] = org["slug"]


def _load_org(slug: str, user_id: str) -> dict | None:
    return get_org_for_user(slug, user_id)


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

    if user_owns_org and request.args.get("mode") == "create":
        return {"_redirect": "/my/organizations"}

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
    session, redir = require_session()
    if redir:
        return redir
    tab = request.args.get("tab")
    if tab in ("members", "roles", "settings"):
        return {"_redirect": f"/my/organizations/{slug}/{tab}"}
    user = session.user
    org = _load_org(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    _set_active_workspace(org)
    nav = build_nav("/my/organizations", user, org)

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
    org = _load_org(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    _set_active_workspace(org)
    nav = build_nav("/my/organizations", user, org)

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
        "is_owner": org["is_owner"],
        "can_invite": org["can_invite"],
        "can_manage_members": org["can_manage_members"],
        "can_assign_roles": org["can_assign_roles"],
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
    org = _load_org(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    if not org["is_owner"] and not org["can_manage_roles"] and not org["can_assign_roles"]:
        return {"_redirect": f"/my/organizations/{slug}"}
    _set_active_workspace(org)
    nav = build_nav("/my/organizations", user, org)

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

    org_members_page_size = 20
    org_members_page = max(1, int(request.args.get("mpage", "1") or "1"))
    org_members_total = len(role_members)
    org_members_total_pages = max(1, math.ceil(org_members_total / org_members_page_size))
    org_members_page = min(org_members_page, org_members_total_pages)
    paged_role_members = role_members[
        (org_members_page - 1) * org_members_page_size : org_members_page * org_members_page_size
    ]

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
    org = _load_org(slug, user.id)
    if org is None:
        return {"_redirect": "/my/organizations"}
    if not org["can_edit_settings"]:
        return {"_redirect": f"/my/organizations/{slug}"}
    _set_active_workspace(org)
    nav = build_nav("/my/organizations", user, org)

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
