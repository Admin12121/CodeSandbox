from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from app_router.responses import RedirectResult
from flask import current_app, g, request

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.identity.models import User


@dataclass
class CurrentSession:
    user: User
    token_hash: str


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_current_session() -> CurrentSession | None:
    if hasattr(g, "_cs_session"):
        return g._cs_session  # type: ignore[attr-defined]
    cookie_name = current_app.config.get("SESSION_COOKIE_NAME", "cs_session")
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    token_hash = _hash_token(token)
    session = identity_repo.find_active_session(token_hash)
    if session is None:
        return None
    user = identity_repo.find_user_by_id(session.user_id)
    if user is None or user.deleted_at is not None:
        return None
    cs_session = CurrentSession(user=user, token_hash=token_hash)
    g._cs_session = cs_session  # type: ignore[attr-defined]
    return cs_session


def require_session(next_path: str | None = None) -> tuple[CurrentSession | None, RedirectResult | None]:
    session = get_current_session()
    if not session:
        target = next_path or request.path
        return None, RedirectResult(url=f"/login?next={target}")
    return session, None


def require_platform_role(
    *roles: str,
    next_path: str | None = None,
) -> tuple[CurrentSession | None, RedirectResult | None]:
    session, redirect = require_session(next_path)
    if redirect:
        return None, redirect
    assert session is not None
    if session.user.platform_role not in roles:
        return None, RedirectResult(url="/dashboard")
    return session, None


def build_nav(current_path: str, user: User) -> dict[str, Any]:
    role = user.platform_role

    def item(label: str, href: str, permission: str | None = None) -> dict:
        return {
            "label": label,
            "href": href,
            "active": current_path == href or (href != "#help" and current_path.startswith(href + "/")),
            "permission": permission,
        }

    secondary_items = [
        item("Settings", "/settings"),
        item("Get Help", "#help"),
    ]

    if role in ("system_admin", "system_staff"):
        platform_items = [
            item("Dashboard", "/dashboard"),
            item("Users", "/platform/users", "platform.users.read"),
            item("Organizations", "/platform/organizations"),
            item("Application Staff", "/platform/staff", "platform.staff.read"),
            item("Staff Roles", "/platform/roles", "platform.roles.read"),
        ]
        if role != "system_admin":
            from codesandbox.features.platform_admin import repository as rbac_repo
            perms = rbac_repo.get_user_permission_keys(user.id)
            platform_items = [
                i for i in platform_items
                if not i["permission"] or i["permission"] in perms
            ]
        return {
            "sections": [{"label": "Platform", "items": platform_items}],
            "secondary": secondary_items,
        }

    user_items = [
        item("Dashboard", "/dashboard"),
    ]
    return {
        "sections": [{"label": "Workspace", "items": user_items}],
        "secondary": secondary_items,
    }


def format_role_label(role: str) -> str:
    mapping = {
        "system_admin": "System Admin",
        "system_staff": "App Staff",
        "user": "User",
    }
    return mapping.get(role, role.replace("_", " ").title())
