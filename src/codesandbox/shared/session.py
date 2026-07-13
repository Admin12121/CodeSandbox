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
    cookie_name = current_app.config.get("CS_AUTH_COOKIE", "cs_session")
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    token_hash = _hash_token(token)
    session = identity_repo.find_active_session(token_hash)
    if session is None:
        return None
    user = identity_repo.find_user_by_id(session.user_id)
    if user is None or user.deleted_at is not None or user.status == "banned":
        identity_repo.delete_session(token_hash)
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


def require_sandbox_user(
    next_path: str | None = None,
) -> tuple[CurrentSession | None, RedirectResult | None]:
    session, redirect = require_session(next_path)
    if redirect:
        return None, redirect
    assert session is not None
    if session.user.platform_role != "user":
        return None, RedirectResult(url="/dashboard")
    return session, None


def build_nav(current_path: str, user: User, active_workspace: dict | None = None) -> dict[str, Any]:
    role = user.platform_role

    def item(label: str, href: str, permission: str | None = None, *, exact: bool = False) -> dict:
        return {
            "label": label,
            "href": href,
            "active": current_path == href or (
                not exact and href != "#help" and current_path.startswith(href + "/")
            ),
            "permission": permission,
        }

    def separator() -> dict:
        return {"label": "", "href": "", "active": False, "permission": None, "separator": True}

    secondary_items = [
        item("Settings", "/settings"),
        item("Get Help", "#help"),
    ]

    if role in ("system_admin", "system_staff"):
        core_items = [
            item("Dashboard", "/dashboard"),
            item("Users", "/platform/users", "platform.users.read"),
            item("Organizations", "/platform/organizations", "platform.organizations.read"),
            item("Application Staff", "/platform/staff", "platform.staff.read"),
            item("Staff Roles", "/platform/roles", "platform.roles.read"),
        ]
        finance_items = [
            item("Overview", "/platform/finance", "platform.finance.read", exact=True),
            item("Usage & Margin", "/platform/finance/revenue", "platform.finance.read"),
            item("Ledger", "/platform/finance/ledger", "platform.finance.read"),
            item("Promotions", "/platform/finance/promotions", "platform.finance.read"),
        ]
        sandbox_items = [
            item("Sandboxes", "/platform/sandboxes", "platform.sandboxes.manage"),
            item("Sandbox Plans", "/platform/sandbox-plans", "platform.sandbox_plans.manage"),
            # "Workflows" (the cross-template SandboxWorkflow builder at
            # /platform/workflows) is intentionally not in the sidebar —
            # template UI workflows are now configured per-template from the
            # Identity tab's Workflow Mode, not from a separate global page.
            # The route/feature itself still exists for a possible future
            # advanced cross-template scenario builder.
        ]
        if role != "system_admin":
            from codesandbox.features.platform_admin import repository as rbac_repo
            perms = rbac_repo.get_user_permission_keys(user.id)
            def permitted(items: list[dict]) -> list[dict]:
                return [
                    i for i in items
                    if not i["permission"] or i["permission"] in perms
                ]
            core_items = permitted(core_items)
            finance_items = permitted(finance_items)
            sandbox_items = permitted(sandbox_items)
        sections = [{"label": "Platform", "items": core_items}]
        if sandbox_items:
            sections.append({"label": "Sandboxes", "items": sandbox_items})
        if finance_items:
            sections.append({"label": "Finance", "items": finance_items})
        return {
            "sections": sections,
            "secondary": secondary_items,
        }

    user_items = [
        item("Dashboard", "/dashboard"),
        item("Hub", "/hub"),
        item("My Instances", "/my-instances"),
    ]
    if active_workspace:
        # Only meaningful inside an org workspace — instances assigned to you
        # by an org. In personal space there's nothing this page could show.
        user_items.append(item("Private Instances", "/private_instances"))
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
