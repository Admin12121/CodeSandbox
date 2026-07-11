from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from codesandbox.config import get_settings

from . import repository
from .queue import enqueue_job
from .runtime.metrics import runtime_seconds
from .service import _charge_completed_instance, make_worker_callback_token


def _stale(value: datetime | None, now: datetime, seconds: int) -> bool:
    return value is None or value <= now - timedelta(seconds=max(1, seconds))


def _settle(instance) -> None:
    if instance.started_at is None:
        repository.release_instance_reservation(str(instance.id))
        repository.transition_instance_status(
            str(instance.id),
            new_status=instance.status,
            actor="reconciler",
            expected_statuses=(instance.status,),
            billing_status="not_charged",
            charged_amount=0,
        )
        return
    _charge_completed_instance(instance)


def _finalize_at(instance, cutoff: datetime, status: str, reason: str) -> None:
    started_at = instance.started_at
    updated = repository.transition_instance_status(
        str(instance.id),
        new_status=status,
        actor="reconciler",
        expected_statuses=("provisioning", "running", "stopping", "cleanup"),
        stopped_at=cutoff,
        total_runtime_sec=runtime_seconds(started_at, cutoff),
        exit_reason=reason,
    )
    if updated is not None:
        _settle(updated)


def _queue_cleanup(instance, now: datetime, reason: str, final_status: str) -> bool:
    instance_id = str(instance.id)
    cutoff = instance.expires_at if final_status == "expired" and instance.expires_at else (
        instance.last_heartbeat_at or now
    )
    updated = repository.transition_instance_status(
        instance_id,
        new_status="cleanup",
        actor="reconciler",
        expected_statuses=("provisioning", "running", "stopping"),
        stopped_at=cutoff,
        total_runtime_sec=runtime_seconds(instance.started_at, cutoff),
        cleanup_started_at=now,
        exit_reason=reason,
    )
    if updated is None:
        return False
    _settle(updated)

    job_id = str(uuid.uuid4())
    callback_token = make_worker_callback_token(job_id, instance_id, "reconcile")
    repository.transition_instance_status(
        instance_id,
        new_status="cleanup",
        actor="reconciler",
        expected_statuses=("cleanup",),
        worker_job_id=job_id,
    )
    payload = {
        "job_id": job_id,
        "action": "reconcile",
        "instance_id": instance_id,
        "runtime_id": updated.runtime_id,
        "runtime_provider": updated.runtime_provider,
        "workspace_volume_id": updated.workspace_volume_id,
        "runtime_policy": json.loads(updated.runtime_policy or "{}"),
        "reason": reason,
        "final_status": final_status,
        "callback_url": (
            get_settings().control_plane_internal_url + "/internal/worker/callback"
        ),
        "callback_token": callback_token,
    }
    try:
        enqueue_job(payload)
    except Exception as exc:
        repository.log_instance_event(
            instance_id,
            "reconcile.queue_failed",
            actor="reconciler",
            detail=json.dumps({"error": str(exc), "reason": reason}),
        )
        _finalize_at(
            updated,
            cutoff,
            final_status if final_status == "expired" else "failed",
            f"{reason}; cleanup job could not be queued",
        )
        return False
    repository.log_instance_event(
        instance_id,
        "reconcile.cleanup_queued",
        actor="reconciler",
        detail=json.dumps({"reason": reason, "final_status": final_status}),
    )
    return True


def reconcile_stuck_instances(now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    counts = {"checked": 0, "queued": 0, "finalized": 0}

    for instance in repository.list_live_instances():
        counts["checked"] += 1
        instance_id = str(instance.id)
        status_changed_at = instance.status_changed_at or instance.created_at

        if instance.status == "provisioning" and _stale(
            status_changed_at, now, settings.sandbox_provisioning_stale_seconds
        ):
            if instance.runtime_id:
                counts["queued"] += int(_queue_cleanup(
                    instance, now, "Provisioning heartbeat timed out", "failed"
                ))
            else:
                repository.release_instance_reservation(instance_id)
                _finalize_at(instance, now, "failed", "Provisioning timed out")
                counts["finalized"] += 1
            continue

        if instance.status == "running":
            if instance.expires_at and instance.expires_at <= now:
                counts["queued"] += int(_queue_cleanup(
                    instance, now, "Maximum runtime exceeded", "expired"
                ))
                continue
            if _stale(
                instance.last_heartbeat_at,
                now,
                settings.sandbox_heartbeat_stale_seconds,
            ):
                counts["queued"] += int(_queue_cleanup(
                    instance, now, "Runtime heartbeat timed out", "failed"
                ))
                continue

        if instance.status == "stopping" and _stale(
            status_changed_at, now, settings.sandbox_stopping_stale_seconds
        ):
            if instance.runtime_id:
                counts["queued"] += int(_queue_cleanup(
                    instance, now, "Stop operation timed out", "stopped"
                ))
            else:
                _finalize_at(
                    instance,
                    instance.last_heartbeat_at or now,
                    "stopped",
                    "Runtime already absent during reconciliation",
                )
                counts["finalized"] += 1
            continue

        if instance.status == "cleanup" and _stale(
            instance.cleanup_started_at or status_changed_at,
            now,
            settings.sandbox_cleanup_stale_seconds,
        ):
            final_status = "expired" if "Maximum runtime" in (instance.exit_reason or "") else "failed"
            _finalize_at(
                instance,
                instance.stopped_at or instance.last_heartbeat_at or now,
                final_status,
                f"{instance.exit_reason or 'Cleanup'}; cleanup timed out",
            )
            counts["finalized"] += 1

    return counts
