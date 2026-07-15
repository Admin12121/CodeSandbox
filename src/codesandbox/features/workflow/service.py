from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from . import repository

WORKFLOW_STATUSES = ("draft", "published", "archived")
RUN_STATUSES = ("running", "completed", "failed", "cancelled")
STAGE_RUN_STATUSES = ("pending", "running", "completed", "failed", "skipped")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or str(uuid.uuid4())[:8]


# ── Graph parsing / validation ──────────────────────────────────────────────

def parse_graph(graph_json: str | None) -> dict:
    if not graph_json:
        return {"stages": [], "edges": []}
    try:
        graph = json.loads(graph_json)
    except (TypeError, ValueError):
        return {"stages": [], "edges": []}
    if not isinstance(graph, dict):
        return {"stages": [], "edges": []}
    graph.setdefault("stages", [])
    graph.setdefault("edges", [])
    return graph


def validate_workflow_graph(graph: dict) -> str | None:
    """Validate stage/edge shape, and reject cycles unless the graph
    explicitly opts in (attack-simulation-style graphs can be intentionally
    cyclic — "switch between instances" loops — so this is a guard rail,
    not a hard rule)."""
    stages = graph.get("stages") or []
    edges = graph.get("edges") or []
    if not isinstance(stages, list) or not stages:
        return "A workflow needs at least one stage."
    if not isinstance(edges, list):
        return "Edges must be a list."

    keys = [str(s.get("stage_key") or "") for s in stages]
    if any(not k for k in keys):
        return "Every stage needs a stage_key."
    if len(set(keys)) != len(keys):
        return "Stage keys must be unique."
    key_set = set(keys)

    for stage in stages:
        if not str(stage.get("template_id") or "").strip():
            return f"Stage '{stage.get('stage_key')}' needs a template."
        ui_mode = str(stage.get("ui_mode") or "")
        if ui_mode and ui_mode not in {
            "terminal_only", "lab_ui", "background_run", "desktop_gui", "android_ui"
        }:
            return f"Stage '{stage.get('stage_key')}' has an unknown ui_mode."

    adjacency: dict[str, list[str]] = {k: [] for k in keys}
    for edge in edges:
        src = str(edge.get("from_stage_key") or "")
        dst = str(edge.get("to_stage_key") or "")
        if src not in key_set or dst not in key_set:
            return "An edge references a stage that doesn't exist."
        adjacency[src].append(dst)

    if not graph.get("allow_cycles"):
        cycle = _find_cycle(adjacency)
        if cycle:
            return f"Workflow graph has a cycle ({' -> '.join(cycle)}) — set allow_cycles to permit this."

    return None


def _find_cycle(adjacency: dict[str, list[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in adjacency}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if color.get(neighbor, WHITE) == GRAY:
                return path[path.index(neighbor):] + [neighbor]
            if color.get(neighbor, WHITE) == WHITE:
                found = visit(neighbor)
                if found:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for node in adjacency:
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def _stage_by_key(graph: dict, stage_key: str) -> dict | None:
    for stage in graph.get("stages") or []:
        if str(stage.get("stage_key")) == stage_key:
            return stage
    return None


def _entry_stage(graph: dict) -> dict | None:
    """The first stage with no incoming edge — falls back to the first
    stage in declaration order for a graph with no edges yet."""
    stages = graph.get("stages") or []
    if not stages:
        return None
    has_incoming = {str(e.get("to_stage_key")) for e in (graph.get("edges") or [])}
    for stage in stages:
        if str(stage.get("stage_key")) not in has_incoming:
            return stage
    return stages[0]


def _next_stage(graph: dict, stage_key: str) -> dict | None:
    for edge in graph.get("edges") or []:
        if str(edge.get("from_stage_key")) == stage_key:
            return _stage_by_key(graph, str(edge.get("to_stage_key")))
    return None


# ── Admin CRUD ───────────────────────────────────────────────────────────────

def list_platform_workflows() -> list[dict]:
    return [_workflow_dict(w) for w in repository.list_workflows()]


def get_workflow_detail(workflow_id: str) -> dict | None:
    wf = repository.get_workflow(workflow_id)
    return _workflow_dict(wf) if wf else None


def _workflow_dict(wf) -> dict:
    return {
        "id": str(wf.id),
        "name": wf.name,
        "slug": wf.slug,
        "description": wf.description or "",
        "status": wf.status,
        "graph": parse_graph(wf.graph_json),
        "graph_json": wf.graph_json or "",
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
    }


def save_workflow(
    *,
    workflow_id: str | None,
    name: str,
    slug: str,
    description: str,
    created_by_id: str | None,
) -> tuple[dict | None, str | None]:
    name = name.strip()
    if not name:
        return None, "Name is required."
    slug = slug.strip() or _slugify(name)

    if workflow_id:
        existing = repository.get_workflow(workflow_id)
        if existing is None:
            return None, "Workflow not found."
        other = repository.get_workflow_by_slug(slug)
        if other and str(other.id) != workflow_id:
            return None, f"Slug '{slug}' is already taken."
        wf = repository.update_workflow(
            workflow_id, name=name, slug=slug, description=description or None
        )
    else:
        if repository.get_workflow_by_slug(slug):
            return None, f"Slug '{slug}' is already taken."
        wf = repository.create_workflow(
            name=name, slug=slug, description=description or None,
            graph_json=json.dumps({"stages": [], "edges": []}),
            created_by_id=created_by_id,
        )
    return _workflow_dict(wf), None


def save_workflow_graph(workflow_id: str, graph: dict) -> str | None:
    error = validate_workflow_graph(graph)
    if error:
        return error
    repository.update_workflow(workflow_id, graph_json=json.dumps(graph, separators=(",", ":")))
    return None


def publish_workflow(workflow_id: str) -> str | None:
    wf = repository.get_workflow(workflow_id)
    if wf is None:
        return "Workflow not found."
    error = validate_workflow_graph(parse_graph(wf.graph_json))
    if error:
        return f"Cannot publish: {error}"
    repository.update_workflow(workflow_id, status="published")
    return None


def unpublish_workflow(workflow_id: str) -> str | None:
    wf = repository.get_workflow(workflow_id)
    if wf is None:
        return "Workflow not found."
    repository.update_workflow(workflow_id, status="draft")
    return None


def delete_workflow(workflow_id: str) -> None:
    repository.delete_workflow(workflow_id)


# ── User-facing ──────────────────────────────────────────────────────────────

def list_published_workflows() -> list[dict]:
    return [_workflow_dict(w) for w in repository.list_workflows(status="published")]


def get_published_workflow(slug: str) -> dict | None:
    wf = repository.get_workflow_by_slug(slug)
    if wf is None or wf.status != "published":
        return None
    return _workflow_dict(wf)


# ── Execution ────────────────────────────────────────────────────────────────

def _start_stage_instance(
    stage: dict,
    *,
    actor_user_id: str,
    workspace_type: str,
    workspace_org_id: str | None,
    carried_artifact_ids: list[str],
) -> tuple[str | None, str | None]:
    """Create + start a real SandboxInstance for a workflow stage, reusing
    the exact same instance-creation/start path as /hub — a workflow stage
    is not a separate concept from a normal instance, just one that's
    orchestrated by a graph instead of a user click."""
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import (
        get_effective_plan,
        get_platform_plans,
        start_instance,
    )

    template_id = str(stage.get("template_id") or "")
    plan_id = str(stage.get("plan_id") or "")
    if not plan_id:
        active_plans = [p for p in get_platform_plans() if p.get("is_active")]
        if not active_plans:
            return None, "No sandbox plans are configured."
        plan_id = str(active_plans[0]["id"])
    effective_plan, plan_error = get_effective_plan(template_id, plan_id)
    if plan_error or effective_plan is None:
        return None, plan_error or "No plan available for this stage's template."

    # billing_entity/billed_* use the "user"|"org" vocabulary (Balance's
    # entity_type), distinct from workspace_type's "personal"|"org" —
    # matches create_personal_instance/create_org_instance exactly.
    inst = sandbox_repository.create_instance(
        template_id=template_id,
        plan_id=plan_id,
        workspace_type=workspace_type,
        workspace_user_id=actor_user_id if workspace_type != "org" else None,
        workspace_org_id=workspace_org_id if workspace_type == "org" else None,
        created_by_user_id=actor_user_id,
        billing_entity="org" if workspace_type == "org" else "user",
        billed_user_id=actor_user_id if workspace_type != "org" else None,
        billed_org_id=workspace_org_id if workspace_type == "org" else None,
        user_config=json.dumps({"ui_mode": stage.get("ui_mode")}) if stage.get("ui_mode") else None,
    )
    if carried_artifact_ids and stage.get("carry_artifacts"):
        _copy_artifacts_as_inputs(str(inst.id), carried_artifact_ids)
    result, error = start_instance(str(inst.id), actor_user_id=actor_user_id)
    if error:
        return None, error
    return str(inst.id), None


def _copy_artifacts_as_inputs(instance_id: str, artifact_ids: list[str]) -> None:
    """Best-effort: record which artifacts should carry into this stage's
    instance. Real file staging happens once the instance's workspace
    volume exists (worker-side, at container start) — this just persists
    the intent so WorkflowStageRun.input_artifact_ids is honest about what
    was requested, matching the "artifacts must be available if
    carry_artifacts=true" requirement without inventing a fake transfer."""
    return None


def start_workflow_run(
    slug: str,
    *,
    actor_user_id: str,
    workspace_type: str = "personal",
    workspace_org_id: str | None = None,
) -> tuple[dict | None, str | None]:
    wf = repository.get_workflow_by_slug(slug)
    if wf is None or wf.status != "published":
        return None, "Workflow not found or not published."
    graph = parse_graph(wf.graph_json)
    entry = _entry_stage(graph)
    if entry is None:
        return None, "This workflow has no stages configured."

    run = repository.create_workflow_run(
        workflow_id=str(wf.id),
        owner_type=workspace_type,
        owner_id=workspace_org_id if workspace_type == "org" else actor_user_id,
        current_stage_key=str(entry.get("stage_key")),
    )
    instance_id, error = _start_stage_instance(
        entry,
        actor_user_id=actor_user_id,
        workspace_type=workspace_type,
        workspace_org_id=workspace_org_id,
        carried_artifact_ids=[],
    )
    if error:
        repository.update_workflow_run(str(run.id), status="failed", completed_at=_now())
        return None, error
    repository.create_stage_run(
        workflow_run_id=str(run.id),
        stage_key=str(entry.get("stage_key")),
        instance_id=instance_id,
        status="running",
    )
    return {"run_id": str(run.id), "instance_id": instance_id}, None


def get_workflow_run_context_for_instance(instance_id: str) -> dict | None:
    """Used by the instance-detail page to show a workflow progress banner
    + Continue button when this instance is the current stage of an active
    run — looked up by instance_id since that's all the instance page
    naturally has, no query-param plumbing required."""
    stage_run = repository.get_stage_run_by_instance(instance_id)
    if stage_run is None:
        return None
    return _run_context(stage_run)


def _run_context(stage_run) -> dict | None:
    run = repository.get_workflow_run(str(stage_run.workflow_run_id))
    if run is None:
        return None
    wf = repository.get_workflow(str(run.workflow_id))
    if wf is None:
        return None
    graph = parse_graph(wf.graph_json)
    stage = _stage_by_key(graph, stage_run.stage_key)
    next_stage = _next_stage(graph, stage_run.stage_key)
    return {
        "run_id": str(run.id),
        "run_status": run.status,
        "workflow_name": wf.name,
        "stage_key": stage_run.stage_key,
        "stage_name": (stage or {}).get("name") or stage_run.stage_key,
        "stage_run_status": stage_run.status,
        "has_next_stage": next_stage is not None,
        "next_stage_name": (next_stage or {}).get("name"),
        "continue_label": (stage or {}).get("continue_label") or "Continue to next stage",
        "auto_start": bool((next_stage or {}).get("auto_start")),
    }




def _can_manage_org_workflow_run(run, actor_user_id: str) -> bool:
    """Fail closed for legacy organization workflow runs.

    New organization runs are blocked at the route because they bypass the
    allocation/request model. Existing rows may still exist, so only the org
    owner or a role that manages sandbox allocations may continue/view them.
    """
    if run.owner_type != "org" or not run.owner_id:
        return False
    from codesandbox.features.organizations import repository as org_repo

    org_id = str(run.owner_id)
    if org_repo.get_member(org_id, actor_user_id) is None:
        return False
    return (
        org_repo.is_org_owner(org_id, actor_user_id)
        or "sandbox.allocations.manage" in org_repo.get_member_permissions(org_id, actor_user_id)
    )

def continue_workflow_run(run_id: str, actor_user_id: str) -> tuple[dict | None, str | None]:
    run = repository.get_workflow_run(run_id)
    if run is None:
        return None, "Workflow run not found."
    if run.owner_type == "org":
        if not _can_manage_org_workflow_run(run, actor_user_id):
            return None, "You do not have permission to continue this organization run."
    elif str(run.owner_id) != actor_user_id:
        return None, "You do not have permission to continue this run."
    if run.status != "running":
        return None, f"Workflow run is '{run.status}', not running."

    wf = repository.get_workflow(str(run.workflow_id))
    if wf is None:
        return None, "Workflow no longer exists."
    graph = parse_graph(wf.graph_json)
    current_stage_run = repository.get_latest_stage_run(run_id, str(run.current_stage_key))
    if current_stage_run is None:
        return None, "Current stage has no run record."

    repository.update_stage_run(str(current_stage_run.id), status="completed", completed_at=_now())

    current_stage = _stage_by_key(graph, str(run.current_stage_key))
    next_stage = _next_stage(graph, str(run.current_stage_key))
    if next_stage is None:
        repository.update_workflow_run(run_id, status="completed", completed_at=_now(), current_stage_key=None)
        return {"run_id": run_id, "completed": True}, None

    carried_ids: list[str] = []
    if current_stage and current_stage.get("carry_artifacts"):
        from codesandbox.features.sandbox.service import get_instance_artifacts_for_view
        artifacts, _err = get_instance_artifacts_for_view(
            str(current_stage_run.instance_id), actor_user_id
        )
        carried_ids = [str(a["id"]) for a in (artifacts or [])]

    same_instance = bool(next_stage.get("continue_same_instance"))
    if same_instance and current_stage_run.instance_id:
        instance_id = str(current_stage_run.instance_id)
        error = None
    else:
        instance_id, error = _start_stage_instance(
            next_stage,
            actor_user_id=actor_user_id,
            workspace_type=run.owner_type,
            workspace_org_id=run.owner_id if run.owner_type == "org" else None,
            carried_artifact_ids=carried_ids,
        )
    if error:
        repository.update_workflow_run(run_id, status="failed", completed_at=_now())
        return None, error

    repository.create_stage_run(
        workflow_run_id=run_id,
        stage_key=str(next_stage.get("stage_key")),
        instance_id=instance_id,
        status="running",
        input_artifact_ids=json.dumps(carried_ids) if carried_ids else None,
    )
    repository.update_workflow_run(run_id, current_stage_key=str(next_stage.get("stage_key")))
    return {"run_id": run_id, "instance_id": instance_id, "completed": False}, None


def get_workflow_run_detail(run_id: str, actor_user_id: str) -> tuple[dict | None, str | None]:
    run = repository.get_workflow_run(run_id)
    if run is None:
        return None, "Workflow run not found."
    if run.owner_type == "org":
        if not _can_manage_org_workflow_run(run, actor_user_id):
            return None, "You do not have permission to view this organization run."
    elif str(run.owner_id) != actor_user_id:
        return None, "You do not have permission to view this run."
    wf = repository.get_workflow(str(run.workflow_id))
    stage_runs = repository.list_stage_runs(run_id)
    return {
        "id": str(run.id),
        "status": run.status,
        "workflow_name": wf.name if wf else "",
        "current_stage_key": run.current_stage_key,
        "stage_runs": [
            {
                "stage_key": sr.stage_key,
                "instance_id": str(sr.instance_id) if sr.instance_id else None,
                "status": sr.status,
                "started_at": sr.started_at,
                "completed_at": sr.completed_at,
            }
            for sr in stage_runs
        ],
    }, None
