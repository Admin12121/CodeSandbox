from __future__ import annotations

from .base import RuntimeDriver


class DockerRuntimeDriver(RuntimeDriver):
    provider = "docker"

    def prepare(self, instance: dict, policy: dict) -> dict:
        if policy.get("runtime_class") not in {"container", "tool_job"}:
            raise ValueError("Docker driver only accepts container and tool_job policies.")
        if not (policy.get("runtime_image") or policy.get("docker_image")):
            raise ValueError("Docker image is required.")
        return {**policy, "runtime_provider": self.provider}
