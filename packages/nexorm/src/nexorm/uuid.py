import os
import threading
import time
import uuid as _uuid


_last_timestamp_ms = 0
_last_random = 0
_lock = threading.Lock()


def uuid7():
    """Return a time-ordered UUIDv7 value."""
    global _last_random, _last_timestamp_ms

    timestamp_ms = time.time_ns() // 1_000_000
    with _lock:
        if timestamp_ms == _last_timestamp_ms:
            _last_random = (_last_random + 1) & ((1 << 74) - 1)
        else:
            _last_timestamp_ms = timestamp_ms
            _last_random = int.from_bytes(os.urandom(10), "big") & ((1 << 74) - 1)

        rand_a = (_last_random >> 62) & 0x0FFF
        rand_b = _last_random & ((1 << 62) - 1)

    value = (
        ((timestamp_ms & ((1 << 48) - 1)) << 80)
        | (7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return _uuid.UUID(int=value)
