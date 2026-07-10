from __future__ import annotations

from abc import ABC, abstractmethod


class UnsupportedRuntimeError(RuntimeError):
    pass


class RuntimeDriver(ABC):
    provider = "unknown"

    @abstractmethod
    def prepare(self, instance: dict, policy: dict) -> dict:
        raise NotImplementedError

    def start(self):
        raise UnsupportedRuntimeError(f"{self.provider} starts in the worker plane")

    def stop(self):
        raise UnsupportedRuntimeError(f"{self.provider} stops in the worker plane")

    def kill(self):
        raise UnsupportedRuntimeError(f"{self.provider} kills in the worker plane")

    def exec(self, command):
        raise UnsupportedRuntimeError(f"{self.provider} exec runs in the worker plane")

    def open_terminal(self):
        raise UnsupportedRuntimeError(f"{self.provider} terminals run in the worker plane")

    def stats(self):
        raise UnsupportedRuntimeError(f"{self.provider} stats run in the worker plane")

    def collect_artifacts(self):
        raise UnsupportedRuntimeError(f"{self.provider} artifacts run in the worker plane")

    def cleanup(self):
        raise UnsupportedRuntimeError(f"{self.provider} cleanup runs in the worker plane")
