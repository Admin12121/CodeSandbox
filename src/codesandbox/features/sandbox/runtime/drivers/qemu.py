from __future__ import annotations

from .base import RuntimeDriver, UnsupportedRuntimeError


class QemuRuntimeDriver(RuntimeDriver):
    provider = "qemu"

    def prepare(self, instance: dict, policy: dict) -> dict:
        raise UnsupportedRuntimeError("QEMU runtime is reserved for a future worker driver.")
