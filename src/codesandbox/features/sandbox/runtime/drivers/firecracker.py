from __future__ import annotations

from .base import RuntimeDriver, UnsupportedRuntimeError


class FirecrackerRuntimeDriver(RuntimeDriver):
    provider = "firecracker"

    def prepare(self, instance: dict, policy: dict) -> dict:
        raise UnsupportedRuntimeError("Firecracker runtime is reserved for a future worker driver.")
