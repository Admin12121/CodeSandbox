from __future__ import annotations

import json
import hashlib
import hmac
import os
import time

import redis

from codesandbox.config import get_settings

_QUEUE_KEY = "codesandbox:sandbox-jobs"
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def enqueue_job(payload: dict) -> None:
    """Sign and enqueue a server-built runtime job."""
    job = dict(payload)
    job.setdefault("issued_at", int(time.time()))
    job["job_signature"] = sign_job(job)
    _get_client().lpush(_QUEUE_KEY, json.dumps(job, separators=(",", ":")))


def sign_job(payload: dict) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "job_signature"}
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
