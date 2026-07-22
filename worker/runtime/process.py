from __future__ import annotations

import threading

from .base import RuntimeRunner


class RuntimeRegistry:
    def __init__(self) -> None:
        self._runners: dict[str, RuntimeRunner] = {}
        self._lock = threading.RLock()

    def register(self, instance_id: str, runner: RuntimeRunner) -> None:
        with self._lock:
            if instance_id in self._runners:
                raise RuntimeError("An active runtime already exists for this instance.")
            self._runners[instance_id] = runner

    def get(self, instance_id: str) -> RuntimeRunner | None:
        with self._lock:
            return self._runners.get(instance_id)

    def remove(self, instance_id: str, runner: RuntimeRunner | None = None) -> None:
        with self._lock:
            current = self._runners.get(instance_id)
            if runner is None or current is runner:
                self._runners.pop(instance_id, None)

    def all(self) -> list[tuple[str, RuntimeRunner]]:
        with self._lock:
            return list(self._runners.items())
