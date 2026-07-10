from __future__ import annotations

from .base import RuntimeDriver, UnsupportedRuntimeError


class AndroidRuntimeDriver(RuntimeDriver):
    provider = "android"

    def prepare(self, instance: dict, policy: dict) -> dict:
        raise UnsupportedRuntimeError("Android runtime is reserved for a future worker driver.")
