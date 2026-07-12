from __future__ import annotations

from datetime import datetime, timezone

from nexorm.fields import DateTimeField, ForeignKey, StringField, TextField
from nexorm.model import Model

from codesandbox.features.identity.models import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SandboxWorkflow(Model):
    """Admin-authored graph of connected stages spanning templates/instances —
    distinct from a single template's own internal `runtime_config.workflow`
    JSON blob (see sandbox/service.py validate_workflow_config), which only
    describes stage transitions *within one instance's own lifecycle*.

    graph_json first (not separate stage/edge tables) per docs/plan.md Phase
    10.3 — schema designed so it can migrate to relational tables later:
    {"stages": [{stage_key, name, template_id, runtime_class, ui_mode,
    position_x, position_y, config_json, carry_artifacts, auto_start,
    continue_label}], "edges": [{from_stage_key, to_stage_key, condition, label}]}
    """

    id = StringField(primary_key=True, max_length=36)
    name = StringField(max_length=120)
    slug = StringField(max_length=80, unique=True)
    description = TextField(nullable=True)
    status = StringField(max_length=20, default="draft")  # draft|published|archived
    graph_json = TextField(nullable=True)
    created_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(default=_now)


class WorkflowRun(Model):
    id = StringField(primary_key=True, max_length=36)
    workflow_id = ForeignKey(to=SandboxWorkflow, on_delete="CASCADE")
    owner_type = StringField(max_length=20)  # user|org
    owner_id = StringField(max_length=36)
    status = StringField(max_length=20, default="running")  # running|completed|failed|cancelled
    current_stage_key = StringField(max_length=80, nullable=True)
    created_at = DateTimeField(default=_now)
    completed_at = DateTimeField(nullable=True)


class WorkflowStageRun(Model):
    id = StringField(primary_key=True, max_length=36)
    workflow_run_id = ForeignKey(to=WorkflowRun, on_delete="CASCADE")
    stage_key = StringField(max_length=80)
    # Loose reference (plain string, not FK) — mirrors SandboxInstance.plan_id's
    # existing "stored as plain string" convention, keeping this feature from
    # importing sandbox models top-down.
    instance_id = StringField(max_length=36, nullable=True)
    status = StringField(max_length=20, default="pending")  # pending|running|completed|failed|skipped
    started_at = DateTimeField(nullable=True)
    completed_at = DateTimeField(nullable=True)
    input_artifact_ids = TextField(nullable=True)  # JSON list
    output_artifact_ids = TextField(nullable=True)  # JSON list
    created_at = DateTimeField(default=_now)
