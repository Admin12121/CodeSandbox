from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .models import SandboxWorkflow, WorkflowRun, WorkflowStageRun


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── SandboxWorkflow ─────────────────────────────────────────────────────────

def list_workflows(status: str | None = None) -> list[SandboxWorkflow]:
    items = list(
        SandboxWorkflow.objects.filter(status=status).all()
        if status
        else SandboxWorkflow.objects.all()
    )
    items.sort(key=lambda w: w.created_at, reverse=True)
    return items


def get_workflow(workflow_id: str) -> SandboxWorkflow | None:
    return SandboxWorkflow.objects.filter(id=workflow_id).first()


def get_workflow_by_slug(slug: str) -> SandboxWorkflow | None:
    return SandboxWorkflow.objects.filter(slug=slug).first()


def create_workflow(
    *, name: str, slug: str, description: str | None, graph_json: str | None, created_by_id: str | None
) -> SandboxWorkflow:
    wf = SandboxWorkflow(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        description=description,
        status="draft",
        graph_json=graph_json,
        created_by=created_by_id,
        created_at=_now(),
        updated_at=_now(),
    )
    wf.save()
    return wf


def update_workflow(workflow_id: str, **kwargs) -> SandboxWorkflow | None:
    wf = get_workflow(workflow_id)
    if wf is None:
        return None
    for key, value in kwargs.items():
        setattr(wf, key, value)
    wf.updated_at = _now()
    wf.save()
    return wf


def delete_workflow(workflow_id: str) -> None:
    wf = get_workflow(workflow_id)
    if wf is not None:
        wf.delete()


# ── WorkflowRun ──────────────────────────────────────────────────────────────

def create_workflow_run(
    *, workflow_id: str, owner_type: str, owner_id: str, current_stage_key: str
) -> WorkflowRun:
    run = WorkflowRun(
        id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        owner_type=owner_type,
        owner_id=owner_id,
        status="running",
        current_stage_key=current_stage_key,
        created_at=_now(),
    )
    run.save()
    return run


def get_workflow_run(run_id: str) -> WorkflowRun | None:
    return WorkflowRun.objects.filter(id=run_id).first()


def update_workflow_run(run_id: str, **kwargs) -> WorkflowRun | None:
    run = get_workflow_run(run_id)
    if run is None:
        return None
    for key, value in kwargs.items():
        setattr(run, key, value)
    run.save()
    return run


def list_workflow_runs_for_owner(owner_type: str, owner_id: str) -> list[WorkflowRun]:
    return list(
        WorkflowRun.objects.filter(owner_type=owner_type, owner_id=owner_id)
        .order_by("-created_at")
        .all()
    )


# ── WorkflowStageRun ─────────────────────────────────────────────────────────

def create_stage_run(
    *,
    workflow_run_id: str,
    stage_key: str,
    instance_id: str | None,
    status: str = "pending",
    input_artifact_ids: str | None = None,
) -> WorkflowStageRun:
    stage_run = WorkflowStageRun(
        id=str(uuid.uuid4()),
        workflow_run_id=workflow_run_id,
        stage_key=stage_key,
        instance_id=instance_id,
        status=status,
        started_at=_now() if status == "running" else None,
        input_artifact_ids=input_artifact_ids,
        created_at=_now(),
    )
    stage_run.save()
    return stage_run


def get_stage_run(stage_run_id: str) -> WorkflowStageRun | None:
    return WorkflowStageRun.objects.filter(id=stage_run_id).first()


def get_stage_run_by_instance(instance_id: str) -> WorkflowStageRun | None:
    """Most recent stage run for this instance — an instance can only ever
    be the current stage of at most one active run, but stays queryable
    after the run moves on for the "this run has finished" banner state."""
    return (
        WorkflowStageRun.objects.filter(instance_id=instance_id)
        .order_by("-created_at")
        .first()
    )


def get_latest_stage_run(workflow_run_id: str, stage_key: str) -> WorkflowStageRun | None:
    return (
        WorkflowStageRun.objects.filter(workflow_run_id=workflow_run_id, stage_key=stage_key)
        .order_by("-created_at")
        .first()
    )


def list_stage_runs(workflow_run_id: str) -> list[WorkflowStageRun]:
    return list(
        WorkflowStageRun.objects.filter(workflow_run_id=workflow_run_id).order_by("created_at").all()
    )


def update_stage_run(stage_run_id: str, **kwargs) -> WorkflowStageRun | None:
    stage_run = get_stage_run(stage_run_id)
    if stage_run is None:
        return None
    for key, value in kwargs.items():
        setattr(stage_run, key, value)
    stage_run.save()
    return stage_run
