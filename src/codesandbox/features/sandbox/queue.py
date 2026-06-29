from __future__ import annotations

import json
import os

import redis

_QUEUE_KEY = "codesandbox:sandbox-jobs"
_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def enqueue_job(payload: dict) -> None:
    """Push a job payload onto the left of the job queue (worker pops from right)."""
    _get_client().lpush(_QUEUE_KEY, json.dumps(payload))
