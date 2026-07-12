from __future__ import annotations

from flask import request

from codesandbox.shared.permissions import has_platform_permission
from codesandbox.shared.session import build_nav, require_platform_role, require_sandbox_user
from codesandbox.web.blueprint import router
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx

from .service import (
    continue_workflow_run,
    get_published_workflow,
    get_workflow_detail,
    get_workflow_run_detail,
    list_platform_workflows,
    list_published_workflows,
    start_workflow_run,
)


def _available_templates() -> list[dict]:
    from codesandbox.features.sandbox.service import get_platform_templates

    templates, _total = get_platform_templates(status="active", page=1, page_size=200)
    return [
        {"id": t["id"], "name": t["name"], "ui_modes": t["allowed_ui_mode_values"]}
        for t in templates
    ]


# ── Platform admin ───────────────────────────────────────────────────────────

@router.page("/platform/workflows")
def platform_workflows():
    session, redirect = require_platform_role("system_admin", "system_staff")
    if redirect:
        return redirect
    user = session.user
    can_manage = user.platform_role == "system_admin" or has_platform_permission(
        user, "platform.workflows.manage"
    )
    if not can_manage:
        return {"_redirect": "/dashboard"}
    nav = build_nav("/platform/workflows", user)

    workflows = list_platform_workflows()
    workflow_param = request.args.get("workflow") or None
    selected_workflow = None
    if workflow_param == "new":
        selected_workflow = {
            "id": None, "name": "", "slug": "", "description": "",
            "status": "draft", "graph": {"stages": [], "edges": []}, "graph_json": "",
        }
    elif workflow_param:
        selected_workflow = get_workflow_detail(workflow_param)

    return {
        "_meta": {"title": "Workflows — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Workflows",
        "page_description": "Multi-stage sandbox workflows spanning templates and instances",
        **_workspaces_ctx(user),
        "workflows": workflows,
        "selected_workflow": selected_workflow,
        "can_manage": can_manage,
        "available_templates": _available_templates(),
        "error": request.args.get("error"),
    }


# ── User-facing ──────────────────────────────────────────────────────────────

@router.page("/workflows")
def workflows_list():
    session, redirect = require_sandbox_user()
    if redirect:
        return redirect
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    return {
        "_meta": {"title": "Workflows - CodeSandbox"},
        "user": _user_ctx(user),
        "nav": build_nav("/workflows", user, ws_ctx.get("active_workspace")),
        "page_title": "Workflows",
        "workflows": list_published_workflows(),
        **ws_ctx,
    }


@router.page("/workflows/<slug>")
def workflow_detail(slug: str):
    session, redirect = require_sandbox_user()
    if redirect:
        return redirect
    user = session.user
    wf = get_published_workflow(slug)
    if wf is None:
        return {"_redirect": "/workflows"}
    ws_ctx = _workspaces_ctx(user)
    return {
        "_meta": {"title": f"{wf['name']} - CodeSandbox"},
        "user": _user_ctx(user),
        "nav": build_nav("/workflows", user, ws_ctx.get("active_workspace")),
        "page_title": wf["name"],
        "workflow": wf,
        **ws_ctx,
    }


@router.page("/workflow-runs/<run_id>")
def workflow_run_detail(run_id: str):
    session, redirect = require_sandbox_user()
    if redirect:
        return redirect
    user = session.user
    detail, error = get_workflow_run_detail(run_id, str(user.id))
    if error or detail is None:
        return {"_redirect": "/workflows?error=" + (error or "Not found")}
    ws_ctx = _workspaces_ctx(user)
    return {
        "_meta": {"title": f"{detail['workflow_name']} run - CodeSandbox"},
        "user": _user_ctx(user),
        "nav": build_nav("/workflows", user, ws_ctx.get("active_workspace")),
        "page_title": detail["workflow_name"],
        "run": detail,
        **ws_ctx,
    }
