from __future__ import annotations

import hashlib
import logging
import os
import platform
import re
import threading
import time
import uuid
from typing import Any

import docker
from docker.errors import ImageNotFound, NotFound
from docker.types import Mount

from .artifacts import ArtifactCollector, ObjectStore, tar_bytes
from .base import RuntimeRunner
from .docker_client import DockerClientFactory
from .filesystem import DockerFilesystem
from .metrics import DockerMetrics

log = logging.getLogger("codesandbox-worker.docker")

_SUPPORTED_RUNTIME_CLASSES = {"container", "tool_job"}
_SUPPORTED_NETWORK_MODES = {"disabled", "restricted", "full_internet"}
_USER_RE = re.compile(r"^(?:[0-9]+|[A-Za-z_][A-Za-z0-9_-]*)(?::(?:[0-9]+|[A-Za-z_][A-Za-z0-9_-]*))?$")
_WORKER_ID = os.environ.get("WORKER_ID", platform.node())


class DockerRunner(RuntimeRunner):
    def __init__(self, job: dict, publish, store: ObjectStore | None = None) -> None:
        self.job = job
        self.instance_id = str(job["instance_id"])
        self.policy = dict(job.get("runtime_policy") or {})
        self.publish = publish
        self.client = DockerClientFactory.create()
        self.store = store or ObjectStore()
        self.collector = ArtifactCollector(self.store)
        self.container = None
        self.workspace_volume = None
        self.input_volume = None
        self.started_monotonic = time.monotonic()
        self.external_control = threading.Event()
        self._operation_lock = threading.RLock()
        self._cleaned = False
        self.filesystem = DockerFilesystem(self)
        self.metrics_reader = DockerMetrics(self)

    @property
    def is_running(self) -> bool:
        if self.container is None:
            return False
        try:
            self.container.reload()
            return self.container.status == "running"
        except Exception:
            return False

    def _validate(self) -> None:
        uuid.UUID(self.instance_id)
        if int(self.policy.get("version") or 0) != 1:
            raise ValueError("Unsupported runtime policy version.")
        if self.policy.get("runtime_class") not in _SUPPORTED_RUNTIME_CLASSES:
            raise ValueError("Unsupported runtime class.")
        if self.policy.get("runtime_provider") != "docker":
            raise ValueError("Runtime provider must be Docker.")
        image = str(self.policy.get("docker_image") or "").strip()
        if not image or any(char.isspace() for char in image):
            raise ValueError("Invalid Docker image reference.")
        allowed = {
            value.strip()
            for value in os.environ.get("SANDBOX_ALLOWED_IMAGES", "").split(",")
            if value.strip()
        }
        require_allowlist = os.environ.get(
            "SANDBOX_REQUIRE_IMAGE_ALLOWLIST", "false"
        ).lower() in {"1", "true", "yes", "on"}
        if (allowed and image not in allowed) or (require_allowlist and not allowed):
            raise ValueError("Docker image is not approved by this worker.")
        if self.policy.get("network_mode") not in _SUPPORTED_NETWORK_MODES:
            raise ValueError("Unsupported network mode.")
        if self.policy.get("network_mode") == "full_internet" and not self.policy.get(
            "full_internet_enabled"
        ):
            raise ValueError("Full internet was not explicitly enabled.")
        if self.policy.get("sandbox_type") in {"malware", "reverse_engineering"} and self.policy.get(
            "network_mode"
        ) == "full_internet":
            raise ValueError("Full internet is prohibited for this sandbox type.")
        for name, minimum, maximum in (
            ("vcpu", 1, 128),
            ("ram_gb", 1, 1024),
            ("disk_gb", 1, 4096),
            ("pids_limit", 32, 4096),
            ("max_timeout_sec", 1, 72 * 3600),
        ):
            value = int(self.policy.get(name) or 0)
            if not minimum <= value <= maximum:
                raise ValueError(f"Runtime policy {name} is outside worker limits.")
        security = self.policy.get("security") or {}
        if (
            security.get("no_new_privileges") is not True
            or security.get("cap_drop") != ["ALL"]
            or security.get("privileged") is not False
            or security.get("host_mounts") is not False
            or security.get("docker_socket") is not False
        ):
            raise ValueError("Runtime security policy is not sufficiently restricted.")
        run_as_user = str(self.policy.get("run_as_user") or "")
        if run_as_user and not _USER_RE.fullmatch(run_as_user):
            raise ValueError("Invalid container user.")
        for path_name in (
            "working_dir", "input_mount_path", "output_mount_path"
        ):
            value = str(self.policy.get(path_name) or "")
            if not value.startswith("/") or ".." in value.split("/"):
                raise ValueError(f"Invalid {path_name}.")
        # Generic (not slug-specific) required/forbidden-argument re-check —
        # sourced entirely from this template's own runtime_config, same as
        # the control-plane-side check in runtime/policy.py.
        required_args = [str(a) for a in (self.policy.get("required_args") or [])]
        forbidden_args = [str(a) for a in (self.policy.get("forbidden_args") or [])]
        if required_args or forbidden_args:
            command_text = " ".join(self.policy.get("default_command") or [])
            missing = [arg for arg in required_args if arg not in command_text]
            if missing:
                raise ValueError(f"Default command must include: {', '.join(missing)}.")
            present = [arg for arg in forbidden_args if arg in command_text]
            if present:
                raise ValueError(f"Default command must not include: {', '.join(present)}.")

    def _ensure_image(self, image: str) -> None:
        try:
            self.client.images.get(image)
        except ImageNotFound:
            log.info("pulling image=%s", image)
            self.client.images.pull(image)

    def _restricted_network(self) -> str:
        name = os.environ.get("SANDBOX_RESTRICTED_NETWORK", "codesandbox-restricted")
        try:
            self.client.networks.get(name)
        except NotFound:
            self.client.networks.create(
                name,
                driver="bridge",
                internal=True,
                attachable=False,
                labels={"com.codesandbox.managed": "true"},
            )
        return name

    def _stage_volumes(self) -> None:
        helper_image = os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36")
        self._ensure_image(helper_image)
        mounts = [
            Mount("/workspace", self.workspace_volume.name, type="volume", no_copy=True),
            Mount("/output", self.workspace_volume.name, type="volume", no_copy=True),
            Mount("/input", self.input_volume.name, type="volume", no_copy=True),
        ]
        helper = self.client.containers.create(
            helper_image,
            command=["sh", "-c", "sleep 300"],
            network_mode="none",
            mounts=mounts,
            labels={
                "com.codesandbox.managed": "true",
                "com.codesandbox.instance_id": self.instance_id,
                "com.codesandbox.role": "volume-init",
            },
        )
        try:
            helper.start()
            max_bytes = int(self.policy.get("max_upload_bytes") or 0)
            for index, item in enumerate(self.policy.get("inputs") or []):
                storage_key = str(item.get("storage_key") or "")
                data = self.store.get_input(self.instance_id, storage_key, max_bytes)
                expected_checksum = str(item.get("checksum") or "")
                if expected_checksum and hashlib.sha256(data).hexdigest() != expected_checksum:
                    raise ValueError("Input checksum mismatch.")
                name = str(item.get("name") or f"input-{index + 1}")
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".") or f"input-{index + 1}"
                helper.put_archive("/input", tar_bytes(safe_name, data, 0o444))
                if index == 0 and safe_name != "sample":
                    helper.put_archive("/input", tar_bytes("sample", data, 0o444))

            run_as_user = self._container_user()
            numeric = re.fullmatch(r"([0-9]+)(?::([0-9]+))?", run_as_user or "")
            if numeric:
                uid = numeric.group(1)
                gid = numeric.group(2) or uid
                ownership = helper.exec_run([
                    "sh", "-c",
                    (
                        f"chown -R {uid}:{gid} /workspace /output "
                        f"&& chmod 0770 /workspace /output "
                        "&& chmod -R a-w /input"
                    ),
                ])
                if ownership.exit_code != 0:
                    raise ValueError("Workspace ownership could not be prepared.")
        finally:
            helper.remove(force=True)

    def _container_user(self) -> str | None:
        configured = str(self.policy.get("run_as_user") or "")
        if configured:
            return configured
        if self.policy.get("allow_root"):
            return None
        return os.environ.get("SANDBOX_DEFAULT_USER", "65532:65532")

    def prepare(self) -> None:
        self._validate()
        self.client.ping()
        image = str(self.policy["docker_image"])
        self._ensure_image(image)
        labels = {
            "com.codesandbox.managed": "true",
            "com.codesandbox.instance_id": self.instance_id,
        }
        workspace_name = f"cs-workspace-{self.instance_id}"
        input_name = f"cs-input-{self.instance_id}"
        for volume_name in (workspace_name, input_name):
            try:
                self.client.volumes.get(volume_name).remove(force=True)
            except NotFound:
                pass
        self.workspace_volume = self.client.volumes.create(
            name=workspace_name, labels=labels
        )
        self.input_volume = self.client.volumes.create(name=input_name, labels=labels)
        self._stage_volumes()

    def _container_labels(self) -> dict[str, str]:
        meta = self.job.get("labels") or {}
        return {
            "com.codesandbox.managed": "true",
            "codesandbox.instance_id": self.instance_id,
            "codesandbox.worker_id": _WORKER_ID,
            "codesandbox.template_id": str(meta.get("template_id") or ""),
            "codesandbox.plan_id": str(meta.get("plan_id") or ""),
            "codesandbox.owner_type": str(meta.get("owner_type") or ""),
            "codesandbox.owner_id": str(meta.get("owner_id") or ""),
        }

    def _container_command(self) -> list[str] | None:
        command = self.policy.get("default_command")
        if command:
            return [str(value) for value in command]
        modes = set(self.policy.get("interface_modes") or [])
        if modes.intersection({"terminal", "editor"}):
            return ["/bin/sh", "-c", "trap : TERM INT; sleep infinity & wait"]
        return None

    def start(self) -> dict[str, Any]:
        mounts = [
            Mount(
                self.policy["working_dir"],
                self.workspace_volume.name,
                type="volume",
                no_copy=True,
            ),
            Mount(
                self.policy["output_mount_path"],
                self.workspace_volume.name,
                type="volume",
                no_copy=True,
            ),
            Mount(
                self.policy["input_mount_path"],
                self.input_volume.name,
                type="volume",
                read_only=True,
                no_copy=True,
            ),
        ]
        kwargs: dict[str, Any] = {
            "image": self.policy["docker_image"],
            "command": self._container_command(),
            "name": f"cs-{self.instance_id}",
            "detach": True,
            "stdin_open": False,
            "tty": False,
            "working_dir": self.policy["working_dir"],
            "user": self._container_user(),
            "mounts": mounts,
            "read_only": bool(self.policy.get("read_only_root", True)),
            "tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=64m,mode=1777"},
            "nano_cpus": int(self.policy["vcpu"]) * 1_000_000_000,
            "mem_limit": int(self.policy["ram_gb"]) * 1024**3,
            "memswap_limit": int(self.policy["ram_gb"]) * 1024**3,
            "pids_limit": int(self.policy["pids_limit"]),
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "privileged": False,
            "init": True,
            "labels": self._container_labels(),
        }
        network_mode = self.policy["network_mode"]
        if network_mode == "disabled":
            kwargs["network_mode"] = "none"
        elif network_mode == "restricted":
            kwargs["network"] = self._restricted_network()
        else:
            kwargs["network_mode"] = "bridge"
        self.container = self.client.containers.create(**kwargs)
        self.container.start()
        self.started_monotonic = time.monotonic()
        return {
            "runtime_provider": "docker",
            "runtime_id": self.container.id,
            "runtime_node_id": os.environ.get("RUNTIME_NODE_ID", platform.node()),
            "workspace_volume_id": self.workspace_volume.name,
            "worker_id": _WORKER_ID,
        }

    def exec(self, command: list[str]) -> tuple[int, bytes]:
        if not self.is_running:
            raise RuntimeError("Sandbox is not running.")
        result = self.container.exec_run(command)
        return int(result.exit_code), bytes(result.output)

    def open_terminal(self):
        if not self.is_running:
            raise RuntimeError("Sandbox is not running.")
        shell = None
        for candidate in ("/bin/bash", "/bin/sh"):
            probe = self.container.exec_run([candidate, "-c", "exit 0"])
            if probe.exit_code == 0:
                shell = candidate
                break
        if shell is None:
            raise RuntimeError("No supported shell is installed in the container.")
        created = self.client.api.exec_create(
            self.container.id,
            [shell, "-i"],
            stdin=True,
            tty=True,
            user=self._container_user(),
            workdir=self.policy["working_dir"],
            environment={"TERM": "xterm-256color"},
        )
        exec_id = created["Id"]
        socket_wrapper = self.client.api.exec_start(
            exec_id,
            detach=False,
            tty=True,
            socket=True,
        )
        return exec_id, socket_wrapper

    def stats(self) -> dict[str, Any]:
        return self.metrics_reader.snapshot()

    def stop(self) -> dict[str, Any]:
        with self._operation_lock:
            if self.container is None:
                return {"exit_code": None}
            try:
                self.container.reload()
                if self.container.status == "running":
                    self.container.stop(timeout=10)
                self.container.reload()
            except NotFound:
                return {"exit_code": None}
            state = self.container.attrs.get("State", {})
            return {"exit_code": state.get("ExitCode"), "reason": "stopped"}

    def kill(self) -> dict[str, Any]:
        with self._operation_lock:
            if self.container is None:
                return {"exit_code": None}
            try:
                self.container.kill()
                self.container.reload()
            except NotFound:
                return {"exit_code": None}
            state = self.container.attrs.get("State", {})
            return {"exit_code": state.get("ExitCode"), "reason": "killed"}

    def collect_artifacts(self) -> list[dict[str, Any]]:
        if self.container is None:
            return []
        target = self.container
        helper = None
        if not self.is_running and self.workspace_volume is not None:
            helper_image = os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36")
            helper = self.client.containers.create(
                helper_image,
                command=["sh", "-c", "sleep 300"],
                network_mode="none",
                mounts=[
                    Mount(
                        self.policy["working_dir"],
                        self.workspace_volume.name,
                        type="volume",
                        read_only=True,
                        no_copy=True,
                    ),
                    Mount(
                        self.policy["output_mount_path"],
                        self.workspace_volume.name,
                        type="volume",
                        read_only=True,
                        no_copy=True,
                    ),
                ],
                labels={
                    "com.codesandbox.managed": "true",
                    "com.codesandbox.instance_id": self.instance_id,
                    "com.codesandbox.role": "artifact-collector",
                },
            )
            helper.start()
            target = helper
        try:
            return self.collector.collect(
                target,
                list(self.policy.get("artifact_paths") or []),
                str(self.policy.get("artifact_prefix") or f"sandboxes/{self.instance_id}/artifacts"),
            )
        finally:
            if helper is not None:
                helper.remove(force=True)

    def cleanup(self) -> None:
        with self._operation_lock:
            if self._cleaned:
                return
            self._cleaned = True
            if self.container is not None:
                try:
                    self.container.remove(force=True)
                except NotFound:
                    pass
            for volume in (self.input_volume, self.workspace_volume):
                if volume is not None:
                    try:
                        volume.remove(force=True)
                    except (NotFound, Exception) as exc:
                        log.warning("volume cleanup failed instance=%s error=%s", self.instance_id[:8], exc)

    @classmethod
    def recover(cls, job: dict, publish, store: ObjectStore | None = None):
        runner = cls(job, publish, store)
        runtime_id = str(job.get("runtime_id") or "")
        if not runtime_id:
            raise ValueError("Runtime ID is required for recovery.")
        runner.container = runner.client.containers.get(runtime_id)
        workspace_name = str(job.get("workspace_volume_id") or f"cs-workspace-{runner.instance_id}")
        try:
            runner.workspace_volume = runner.client.volumes.get(workspace_name)
        except NotFound:
            runner.workspace_volume = None
        try:
            runner.input_volume = runner.client.volumes.get(f"cs-input-{runner.instance_id}")
        except NotFound:
            runner.input_volume = None
        return runner
