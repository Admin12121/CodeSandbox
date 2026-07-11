from __future__ import annotations

import json
import hashlib
import hmac
import os
import time

import redis

from codesandbox.config import get_settings

_QUEUE_KEY_PREFIX = "codesandbox:sandbox-jobs"
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def worker_queue_key(worker_id: str) -> str:
    """Every job (start/stop/kill/reconcile) is enqueued onto the exact
    worker that owns (or, for `start`, was just assigned) the instance —
    there is no shared/broadcast queue, so a job can never be work-stolen by
    a worker other than the one the control plane picked."""
    return f"{_QUEUE_KEY_PREFIX}:{worker_id}"


def enqueue_job(payload: dict) -> None:
    """Sign and enqueue a server-built runtime job onto its worker_id's queue.

    `worker_id` must already be set on the payload — the scheduler assigns it
    up front (see sandbox/service.py:start_instance), and stop/kill/reconcile
    jobs reuse the worker_id already recorded on the instance.
    """
    worker_id = str(payload.get("worker_id") or "")
    if not worker_id:
        raise ValueError("Cannot enqueue a runtime job without a worker_id.")
    job = dict(payload)
    job.setdefault("issued_at", int(time.time()))
    job["job_signature"] = sign_job(job)
    _get_client().lpush(worker_queue_key(worker_id), json.dumps(job, separators=(",", ":")))


def sign_job(payload: dict) -> str:
    return sign_payload(payload, exclude="job_signature")


def sign_payload(payload: dict, *, exclude: str = "signature") -> str:
    unsigned = {key: value for key, value in payload.items() if key != exclude}
    encoded = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hmac.new(
        get_settings().sandbox_job_signing_key.encode(),
        encoded,
        hashlib.sha256,
    ).hexdigest()


def verify_payload_signature(payload: dict, *, field: str = "signature") -> bool:
    """Verify a worker->control-plane payload signed with the same shared
    SANDBOX_JOB_SIGNING_KEY used for Redis job dispatch (worker registration
    and heartbeats aren't scoped to one job, so they use this generic form
    instead of the per-job `sign_job`/`verify_job` pair)."""
    provided = str(payload.get(field) or "")
    if not provided:
        return False
    expected = sign_payload(payload, exclude=field)
    return hmac.compare_digest(provided, expected)
