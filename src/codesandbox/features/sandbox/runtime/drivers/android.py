from __future__ import annotations

from .base import RuntimeDriver


class AndroidRuntimeDriver(RuntimeDriver):
    provider = "android"

    def prepare(self, instance: dict, policy: dict) -> dict:
        if policy.get("runtime_class") not in {'android', 'android_emulator'}:
            raise ValueError("android driver received an incompatible runtime class.")
        if not policy.get("runtime_image"):
            raise ValueError("A runtime image or target is required.")
        # Provisioning is performed by a worker that advertises this runtime
        # class. The control plane only signs and routes the immutable policy.
        return {**policy, "runtime_provider": self.provider}
