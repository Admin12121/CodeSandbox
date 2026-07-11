from __future__ import annotations

from codesandbox.features.sandbox.queue import verify_payload_signature

from . import repository
from .models import WorkerNode


def verify_worker_signature(payload: dict) -> bool:
    return verify_payload_signature(payload, field="signature")


def register_worker(payload: dict) -> WorkerNode:
    return repository.register_worker_node(
        worker_id=str(payload["worker_id"]),
        hostname=str(payload.get("hostname") or "") or None,
        capabilities_json=str(payload.get("capabilities_json") or "") or None,
        total_vcpu=int(payload.get("total_vcpu") or 0),
        total_ram_gb=int(payload.get("total_ram_gb") or 0),
        total_disk_gb=int(payload.get("total_disk_gb") or 0),
    )


def record_heartbeat(payload: dict) -> WorkerNode | None:
    return repository.heartbeat_worker_node(
        str(payload["worker_id"]),
        used_vcpu=payload.get("used_vcpu"),
        used_ram_gb=payload.get("used_ram_gb"),
        running_instances=payload.get("running_instances"),
    )


def select_worker_for_instance(required_vcpu: int, required_ram_gb: int) -> WorkerNode | None:
    return repository.select_worker_for_instance(required_vcpu, required_ram_gb)


def is_worker_online(worker_id: str | None) -> bool:
    if not worker_id:
        return False
    node = repository.get_worker_node(worker_id)
    return node is not None and node.status == "online"


def release_worker_capacity(worker_id: str | None, *, vcpu: int, ram_gb: int) -> None:
    if not worker_id:
        return
    repository.adjust_worker_load(
        worker_id, vcpu_delta=-vcpu, ram_gb_delta=-ram_gb, instance_delta=-1
    )


def reserve_worker_capacity(worker_id: str | None, *, vcpu: int, ram_gb: int) -> None:
    if not worker_id:
        return
    repository.adjust_worker_load(
        worker_id, vcpu_delta=vcpu, ram_gb_delta=ram_gb, instance_delta=1
    )
