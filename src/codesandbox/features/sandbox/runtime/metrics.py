from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def runtime_seconds(started_at: datetime | None, ended_at: datetime | None = None) -> int:
    started = ensure_utc(started_at)
    if started is None:
        return 0
    ended = ensure_utc(ended_at) or utc_now()
    return max(0, int((ended - started).total_seconds()))
