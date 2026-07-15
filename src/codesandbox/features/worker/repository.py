from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .models import WorkerInstanceRuntime, WorkerNode


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_worker_node(worker_id: str) -> WorkerNode | None:
    return WorkerNode.objects.filter(worker_id=worker_id).first()


def list_online_workers() -> list[WorkerNode]:
    return WorkerNode.objects.filter(status="online").all()


def register_worker_node(
    *,
    worker_id: str,
    hostname: str | None,
    capabilities_json: str | None,
    total_vcpu: int,
    total_ram_gb: int,
    total_disk_gb: int,
) -> WorkerNode:
    node = get_worker_node(worker_id)
    now = _now()
    if node is None:
        node = WorkerNode(
            id=str(uuid.uuid4()),
            worker_id=worker_id,
            created_at=now,
        )
    node.hostname = hostname
    node.capabilities_json = capabilities_json
    node.total_vcpu = total_vcpu
    node.total_ram_gb = total_ram_gb
    node.total_disk_gb = total_disk_gb
    node.status = "online"
    node.last_heartbeat_at = now
    node.updated_at = now
    node.save()
    return node


def heartbeat_worker_node(
    worker_id: str,
    *,
    used_vcpu: int | None = None,
    used_ram_gb: int | None = None,
    used_disk_gb: int | None = None,
    running_instances: int | None = None,
) -> WorkerNode | None:
    node = get_worker_node(worker_id)
    if node is None:
        return None
    node.status = "online"
    node.last_heartbeat_at = _now()
    node.updated_at = _now()
    if used_vcpu is not None:
        node.used_vcpu = used_vcpu
    if used_ram_gb is not None:
        node.used_ram_gb = used_ram_gb
    if used_disk_gb is not None:
        node.used_disk_gb = used_disk_gb
    if running_instances is not None:
        node.running_instances = running_instances
    node.save()
    return node


def mark_stale_workers_offline(timeout_seconds: int) -> list[WorkerNode]:
    """Flip any online worker whose heartbeat is overdue to offline.

    Returns the workers just marked offline, so callers (the reconciler) can
    flag their in-flight instances as at-risk.
    """
    cutoff = _now().timestamp() - max(1, timeout_seconds)
    stale = []
    for node in list_online_workers():
        last = node.last_heartbeat_at
        if last is None or last.timestamp() <= cutoff:
            node.status = "offline"
            node.updated_at = _now()
            node.save()
            stale.append(node)
    return stale


def _worker_runtime_classes(node: WorkerNode) -> set[str]:
    try:
        capabilities = json.loads(node.capabilities_json or "{}")
    except (TypeError, ValueError):
        return set()
    values = capabilities.get("runtime_class") or capabilities.get("runtime_classes") or []
    return {str(value) for value in values}


def select_worker_for_instance(
    required_vcpu: int,
    required_ram_gb: int,
    *,
    required_disk_gb: int = 0,
    runtime_class: str | None = None,
) -> WorkerNode | None:
    """Pick the least-loaded compatible worker with enough plan capacity."""
    candidates = [
        node
        for node in list_online_workers()
        if (node.total_vcpu - node.used_vcpu) >= required_vcpu
        and (node.total_ram_gb - node.used_ram_gb) >= required_ram_gb
        and (int(node.total_disk_gb or 0) - int(node.used_disk_gb or 0)) >= required_disk_gb
        and (not runtime_class or runtime_class in _worker_runtime_classes(node))
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda node: (
            node.running_instances,
            node.used_vcpu,
            node.used_ram_gb,
            node.used_disk_gb,
        ),
    )


def adjust_worker_load(
    worker_id: str,
    *,
    vcpu_delta: int,
    ram_gb_delta: int,
    disk_gb_delta: int,
    instance_delta: int,
) -> None:
    node = get_worker_node(worker_id)
    if node is None:
        return
    node.used_vcpu = max(0, int(node.used_vcpu or 0) + vcpu_delta)
    node.used_ram_gb = max(0, int(node.used_ram_gb or 0) + ram_gb_delta)
    node.used_disk_gb = max(0, int(node.used_disk_gb or 0) + disk_gb_delta)
    node.running_instances = max(0, int(node.running_instances or 0) + instance_delta)
    node.updated_at = _now()
    node.save()


# ── WorkerInstanceRuntime ──────────────────────────────────────────────────────


def get_runtime_for_instance(instance_id: str) -> WorkerInstanceRuntime | None:
    return WorkerInstanceRuntime.objects.filter(instance_id=instance_id).first()


def upsert_runtime(
    *,
    instance_id: str,
    worker_id: str,
    runtime_provider: str | None = None,
    runtime_id: str | None = None,
    container_name: str | None = None,
    workspace_volume_id: str | None = None,
    input_volume_id: str | None = None,
    output_volume_id: str | None = None,
    status: str = "starting",
    metadata_json: str | None = None,
) -> WorkerInstanceRuntime:
    runtime = get_runtime_for_instance(instance_id)
    now = _now()
    if runtime is None:
        runtime = WorkerInstanceRuntime(
            id=str(uuid.uuid4()),
            instance_id=instance_id,
            worker_id=worker_id,
            started_at=now,
        )
    runtime.worker_id = worker_id
    if runtime_provider is not None:
        runtime.runtime_provider = runtime_provider
    if runtime_id is not None:
        runtime.runtime_id = runtime_id
    if container_name is not None:
        runtime.container_name = container_name
    if workspace_volume_id is not None:
        runtime.workspace_volume_id = workspace_volume_id
    if input_volume_id is not None:
        runtime.input_volume_id = input_volume_id
    if output_volume_id is not None:
        runtime.output_volume_id = output_volume_id
    runtime.status = status
    runtime.last_seen_at = now
    if metadata_json is not None:
        runtime.metadata_json = metadata_json
    runtime.save()
    return runtime


def list_runtimes_for_worker(worker_id: str, status: str | None = None) -> list[WorkerInstanceRuntime]:
    qs = WorkerInstanceRuntime.objects.filter(worker_id=worker_id)
    if status:
        qs = qs.filter(status=status)
    return qs.all()
