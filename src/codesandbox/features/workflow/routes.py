from __future__ import annotations

import json as _json

from flask import redirect, request

from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session, require_sandbox_user
from codesandbox.web._ctx import _workspaces_ctx
from codesandbox.web.blueprint import web_bp

from .service import (
    continue_workflow_run,
    delete_workflow,
    publish_workflow,
    save_workflow,
    save_workflow_graph,
    start_workflow_run,
    unpublish_workflow,
    validate_workflow_graph,
)


def _workflows_redirect(workflow_id: str | None = None, error: str | None = None):
    url = "/platform/workflows"
    params = []
    if workflow_id:
        params.append(f"workflow={workflow_id}")
    if error:
        from urllib.parse import quote
        params.append(f"error={quote(error)}")
    if params:
        url += "?" + "&".join(params)
    return redirect(url, code=303)


# ── Platform admin ───────────────────────────────────────────────────────────

@web_bp.post("/platform/workflows/save")
@platform_perm("platform.workflows.manage")
def save_workflow_action():
    cs = get_current_session()
    workflow_id = request.form.get("workflow_id") or None
    result, error = save_workflow(
        workflow_id=workflow_id,
        name=request.form.get("name", ""),
        slug=request.form.get("slug", ""),
        description=request.form.get("description", ""),
        created_by_id=str(cs.user.id),
    )
    if error:
        return _workflows_redirect(workflow_id or "new", error)
    return _workflows_redirect(result["id"])


@web_bp.post("/platform/workflows/<workflow_id>/graph")
@platform_perm("platform.workflows.manage")
def save_workflow_graph_action(workflow_id: str):
    body = request.get_json(silent=True) or {}
    graph = body.get("graph") or {}
    error = validate_workflow_graph(graph) if body.get("validate") else None
    if error and body.get("validate"):
        return {"ok": False, "error": error}, 400
    error = save_workflow_graph(workflow_id, graph)
    if error:
        return {"ok": False, "error": error}, 400
    return {"ok": True}


@web_bp.post("/platform/workflows/<workflow_id>/publish")
@platform_perm("platform.workflows.manage")
def publish_workflow_action(workflow_id: str):
    error = publish_workflow(workflow_id)
    if error:
        return {"ok": False, "error": error}, 400
    return {"ok": True}


@web_bp.post("/platform/workflows/<workflow_id>/unpublish")
@platform_perm("platform.workflows.manage")
def unpublish_workflow_action(workflow_id: str):
    error = unpublish_workflow(workflow_id)
    if error:
        return {"ok": False, "error": error}, 400
    return {"ok": True}


@web_bp.post("/platform/workflows/<workflow_id>/delete")
@platform_perm("platform.workflows.manage")
def delete_workflow_action(workflow_id: str):
    delete_workflow(workflow_id)
    return _workflows_redirect()


# ── User-facing ──────────────────────────────────────────────────────────────

@web_bp.post("/workflows/<slug>/start")
def start_workflow_action(slug: str):
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if active_workspace:
        # Cross-template workflows start runtimes immediately. Organization
        # workspaces intentionally use the prepare/request allocation flow so
        # ordinary members cannot bypass approval and spend shared funds.
        from urllib.parse import quote
        return redirect(
            f"/workflows/{slug}?error={quote('Organization workflows must be provisioned through the Public catalog and Private allocations.')}" ,
            code=303,
        )

    workspace_type = "personal"
    workspace_org_id = None

    result, error = start_workflow_run(
        slug,
        actor_user_id=str(user.id),
        workspace_type=workspace_type,
        workspace_org_id=workspace_org_id,
    )
    if error:
        from urllib.parse import quote
        return redirect(f"/workflows/{slug}?error={quote(error)}", code=303)
    return redirect(f"/instances/{result['instance_id']}?workflow_run={result['run_id']}", code=303)


@web_bp.post("/workflow-runs/<run_id>/continue")
def continue_workflow_run_action(run_id: str):
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    result, error = continue_workflow_run(run_id, str(user.id))
    if error:
        from urllib.parse import quote
        return redirect(f"/workflow-runs/{run_id}?error={quote(error)}", code=303)
    if result.get("completed"):
        return redirect(f"/workflow-runs/{run_id}", code=303)
    return redirect(f"/instances/{result['instance_id']}?workflow_run={run_id}", code=303)
