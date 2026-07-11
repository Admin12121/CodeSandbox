from __future__ import annotations

from datetime import datetime, timezone

from nexorm.fields import (
    BooleanField,
    DateTimeField,
    ForeignKey,
    IntegerField,
    StringField,
    TextField,
)
from nexorm.model import Model

from codesandbox.features.sandbox.models import SandboxInstance


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkerNode(Model):
    """Persistent registry of worker fleet members.

    Replaces the in-memory-only runner registry: a worker's identity and
    capacity survive its own process restarts, so the control plane always
    knows which worker (if any) owns a running instance.
    """
    id = StringField(primary_key=True, max_length=36)
    worker_id = StringField(max_length=255, unique=True)
    hostname = StringField(max_length=255, nullable=True)
    status = StringField(max_length=20, default="offline")  # online|offline|draining

    capabilities_json = TextField(nullable=True)

    total_vcpu = IntegerField(default=0)
    total_ram_gb = IntegerField(default=0)
    total_disk_gb = IntegerField(default=0)
    used_vcpu = IntegerField(default=0)
    used_ram_gb = IntegerField(default=0)
    running_instances = IntegerField(default=0)

    last_heartbeat_at = DateTimeField(nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "worker_nodes"


class WorkerInstanceRuntime(Model):
    """Durable record of which worker/container backs a given sandbox instance.

    Rebuilt on worker boot from Docker container labels + this table, so a
    worker restart doesn't strand terminal/filesystem attachment or billing
    state for containers that are still running.
    """
    id = StringField(primary_key=True, max_length=36)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="CASCADE")
    worker_id = StringField(max_length=255)
    runtime_provider = StringField(max_length=40, nullable=True)
    runtime_id = StringField(max_length=255, nullable=True)
    container_name = StringField(max_length=255, nullable=True)
    workspace_volume_id = StringField(max_length=255, nullable=True)
    input_volume_id = StringField(max_length=255, nullable=True)
    output_volume_id = StringField(max_length=255, nullable=True)
    status = StringField(max_length=20, default="starting")  # starting|running|stopped|failed|orphaned
    started_at = DateTimeField(nullable=True)
    last_seen_at = DateTimeField(nullable=True)
    metadata_json = TextField(nullable=True)

    class Meta:
        table_name = "worker_instance_runtimes"
        indexes = [
            {
                "name": "idx_worker_instance_runtimes_worker_status",
                "fields": ["worker_id", "status"],
                "unique": False,
            }
        ]
