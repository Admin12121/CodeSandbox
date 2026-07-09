from __future__ import annotations

from codesandbox.shared.session import format_role_label


def _user_ctx(user) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "platform_role": user.platform_role,
        "role_label": format_role_label(user.platform_role),
        "status": user.status,
        "email_verified": user.email_verified,
    }


def _workspaces_ctx(user, active_workspace=None) -> dict:
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
    user_owns_org = any(w.get("created_by") == user.id for w in workspace_list)
    return {
        "workspace_list": workspace_list,
        "active_workspace": active_workspace,
        "user_owns_org": user_owns_org,
        "_layout_state": {
            "role": user.platform_role,
            "active_workspace": _workspace_state_item(active_workspace),
            "workspaces": [_workspace_state_item(w) for w in workspace_list],
            "user_owns_org": user_owns_org,
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
