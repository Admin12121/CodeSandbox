from __future__ import annotations

import hashlib
import logging
import os
import platform
import posixpath
import re
import threading
import time
import uuid
from typing import Any

from docker.errors import NotFound
from docker.types import Mount

from .artifacts import ArtifactCollector, ObjectStore, tar_bytes
from .base import RuntimeRunner
from .docker_client import DockerClientFactory
from .filesystem import DockerFilesystem
from .image_policy import ensure_image, normalize_image_reference
from .metrics import DockerMetrics

log = logging.getLogger("codesandbox-worker.docker")

_SUPPORTED_RUNTIME_CLASSES = {"container", "tool_job"}
_SUPPORTED_NETWORK_MODES = {"disabled", "restricted", "full_internet"}
_SUDO_USER_CAPABILITIES = {
    "AUDIT_WRITE", "CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID",
    "SETFCAP", "SETGID", "SETPCAP", "SETUID",
}
_ROOT_STUDY_CAPABILITIES = _SUDO_USER_CAPABILITIES | {
    "KILL", "MKNOD", "NET_BIND_SERVICE", "NET_RAW", "SYS_CHROOT", "SYS_PTRACE",
}
_USER_RE = re.compile(r"^(?:[0-9]+|[A-Za-z_][A-Za-z0-9_-]*)(?::(?:[0-9]+|[A-Za-z_][A-Za-z0-9_-]*))?$")
_WORKER_ID = os.environ.get("WORKER_ID", platform.node())

_LXCFS_BINDINGS = (
    ("proc/cpuinfo", "/proc/cpuinfo"),
    ("proc/diskstats", "/proc/diskstats"),
    ("proc/meminfo", "/proc/meminfo"),
    ("proc/stat", "/proc/stat"),
    ("proc/swaps", "/proc/swaps"),
    ("proc/uptime", "/proc/uptime"),
    ("proc/slabinfo", "/proc/slabinfo"),
    ("sys/devices/system/cpu", "/sys/devices/system/cpu"),
)


def _env_true(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _lxcfs_enabled() -> bool:
    value = os.environ.get("SANDBOX_LXCFS_ENABLED")
    if value is None:
        return True
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    if normalized == "auto":
        state_file = os.environ.get(
            "SANDBOX_LXCFS_STATE_FILE",
            "/certs/client/codesandbox-lxcfs-enabled",
        )
        try:
            with open(state_file, encoding="utf-8") as handle:
                return handle.read().strip().lower() in {"1", "true", "yes", "on"}
        except OSError:
            return False
    return False


def _parse_cpu_list(value: str) -> list[int]:
    cpus: list[int] = []
    for part in str(value or "").strip().split(","):
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            try:
                cpus.extend(range(int(left), int(right) + 1))
            except ValueError:
                continue
        else:
            try:
                cpus.append(int(part))
            except ValueError:
                continue
    return sorted(set(cpu for cpu in cpus if cpu >= 0))


def _worker_allowed_cpus() -> list[int]:
    for path in (
        "/sys/fs/cgroup/cpuset.cpus.effective",
        "/sys/fs/cgroup/cpuset/cpuset.cpus",
    ):
        try:
            values = _parse_cpu_list(open(path, encoding="utf-8").read())
        except OSError:
            values = []
        if values:
            return values
    return list(range(max(1, os.cpu_count() or 1)))


class DockerRunner(RuntimeRunner):
    def __init__(self, job: dict, publish, store: ObjectStore | None = None) -> None:
        self.job = job
        self.instance_id = str(job["instance_id"])
        self.policy = dict(job.get("runtime_policy") or {})
        self.publish = publish
        self.client = DockerClientFactory.create()
        self.store = store or ObjectStore()
        disk_limit_bytes = max(1, int(self.policy.get("disk_gb") or 1)) * 1024**3
        global_artifact_limit = int(
            os.environ.get("SANDBOX_MAX_ARTIFACT_BYTES", str(500 * 1024 * 1024))
        )
        self.collector = ArtifactCollector(
            self.store, max_bytes=min(global_artifact_limit, disk_limit_bytes)
        )
        self.container = None
        self.workspace_volume = None
        self.input_volume = None
        self.network = None
        self.started_monotonic = time.monotonic()
        self.external_control = threading.Event()
        self._operation_lock = threading.RLock()
        self._cleaned = False
        self.filesystem = DockerFilesystem(self)
        self.metrics_reader = DockerMetrics(self)
        self.test_log_buffer = ""  # Test Launch only — see WorkerApp._stream_logs

    @property
    def is_running(self) -> bool:
        if self.container is None:
            return False
        try:
            self.container.reload()
            return self.container.status == "running"
        except Exception:
            return False

    def _cpuset_cpus(self) -> str:
        allowed = _worker_allowed_cpus()
        requested = max(1, int(self.policy.get("vcpu") or 1))
        if requested > len(allowed):
            raise RuntimeError(
                f"Worker exposes only {len(allowed)} CPU(s), but the selected plan requires {requested}."
            )
        if requested == len(allowed):
            selected = allowed
        else:
            seed = int(hashlib.sha256(self.instance_id.encode()).hexdigest()[:8], 16)
            start = seed % len(allowed)
            selected = [allowed[(start + offset) % len(allowed)] for offset in range(requested)]
        return ",".join(str(cpu) for cpu in sorted(selected))

    def _validate(self) -> None:
        uuid.UUID(self.instance_id)
        if int(self.policy.get("version") or 0) != 1:
            raise ValueError("Unsupported runtime policy version.")
        if self.policy.get("runtime_class") not in _SUPPORTED_RUNTIME_CLASSES:
            raise ValueError("Unsupported runtime class.")
        if self.policy.get("runtime_provider") != "docker":
            raise ValueError("Runtime provider must be Docker.")
        image = normalize_image_reference(
            str(self.policy.get("runtime_image") or self.policy.get("docker_image") or "")
        )
        self.policy["runtime_image"] = image
        self.policy["docker_image"] = image
        pull_policy = str(self.policy.get("image_pull_policy") or "if_not_present")
        if pull_policy not in {"always", "if_not_present", "never"}:
            raise ValueError("Unsupported image pull policy.")
        self.policy["image_pull_policy"] = pull_policy
        if self.policy.get("network_mode") not in _SUPPORTED_NETWORK_MODES:
            raise ValueError("Unsupported network mode.")
        if self.policy.get("network_mode") == "full_internet" and not self.policy.get(
            "full_internet_enabled"
        ):
            raise ValueError("Full internet was not explicitly enabled.")
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
        allow_sudo = bool(self.policy.get("allow_sudo"))
        security_profile = str(security.get("profile") or "restricted")
        cap_add_values = [str(value).upper() for value in security.get("cap_add") or []]
        cap_add = set(cap_add_values)
        if len(cap_add_values) != len(cap_add):
            raise ValueError("Runtime capability list contains duplicates.")
        if security_profile == "root_study":
            expected_cap_add = _ROOT_STUDY_CAPABILITIES
            if not self.policy.get("allow_root"):
                raise ValueError("root_study requires explicit root access.")
        elif allow_sudo:
            expected_cap_add = _SUDO_USER_CAPABILITIES
        elif security_profile == "restricted":
            expected_cap_add = set()
        else:
            raise ValueError("Unsupported runtime security profile.")
        if (
            security.get("cap_drop") != ["ALL"]
            or cap_add != expected_cap_add
            or security.get("privileged") is not False
            or security.get("host_mounts") is not False
            or security.get("docker_socket") is not False
            or (security.get("no_new_privileges") is not True and not allow_sudo)
        ):
            raise ValueError("Runtime security policy is not sufficiently restricted.")
        terminal_scope = str(self.policy.get("terminal_scope") or "container").strip().lower()
        if terminal_scope not in {"container", "workspace"}:
            raise ValueError("Unsupported terminal scope.")
        self.policy["terminal_scope"] = terminal_scope
        for field in ("run_as_user", "container_start_user", "terminal_user"):
            value = str(self.policy.get(field) or "")
            if value and not _USER_RE.fullmatch(value):
                raise ValueError(f"Invalid {field}.")
        if allow_sudo:
            if security.get("no_new_privileges") is not False:
                raise ValueError("Sudo-enabled templates must explicitly disable no-new-privileges.")
            if str(self.policy.get("container_start_user") or "") not in {"0", "0:0", "root", "root:root"}:
                raise ValueError("Sudo-enabled templates require a root-only bootstrap process.")
            terminal_user = str(self.policy.get("terminal_user") or "")
            if not terminal_user or terminal_user in {"0", "0:0", "root", "root:root"}:
                raise ValueError("Sudo-enabled templates require a non-root terminal user.")
        mount_paths: dict[str, str] = {}
        for path_name in ("working_dir", "input_mount_path", "output_mount_path"):
            value = str(self.policy.get(path_name) or "").strip()
            if path_name != "working_dir" and not value:
                self.policy[path_name] = None
                continue
            normalized_path = posixpath.normpath(value)
            if (
                not value.startswith("/")
                or normalized_path in {"/", "."}
                or ".." in value.split("/")
            ):
                raise ValueError(f"Invalid {path_name}.")
            self.policy[path_name] = normalized_path
            if path_name != "working_dir" or bool(self.policy.get("workspace_enabled", True)):
                mount_paths[path_name] = normalized_path

        if self.policy.get("input_required") and not self.policy.get("input_mount_path"):
            raise ValueError("Input is required but no input mount path is configured.")
        primary_input_alias = str(self.policy.get("primary_input_alias") or "").strip()
        if primary_input_alias and not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", primary_input_alias
        ):
            raise ValueError("Invalid primary input alias.")

        path_items = list(mount_paths.items())
        for index, (left_name, left_path) in enumerate(path_items):
            for right_name, right_path in path_items[index + 1 :]:
                if (
                    left_path == right_path
                    or left_path.startswith(right_path + "/")
                    or right_path.startswith(left_path + "/")
                ):
                    raise ValueError(
                        f"{left_name} and {right_name} must be separate, non-overlapping paths."
                    )

        unsupported_modes = set(self.policy.get("interface_modes") or []) & {
            "desktop_gui",
            "android_ui",
        }
        if unsupported_modes:
            raise ValueError(
                "The Docker container worker does not implement desktop_gui or android_ui. "
                "Use a worker/runtime driver that explicitly advertises those capabilities."
            )
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

    def _ensure_image(self, image: str, *, pull_policy: str | None = None) -> str:
        normalized = normalize_image_reference(image)
        policy = pull_policy or str(self.policy.get("image_pull_policy") or "if_not_present")
        log.info("ensuring image=%s pull_policy=%s", normalized, policy)
        ensure_image(self.client, normalized, pull_policy=policy)
        return normalized

    def _lxcfs_mounts(self) -> list[Mount]:
        if not _lxcfs_enabled():
            return []
        root = os.environ.get("SANDBOX_LXCFS_ROOT", "/var/lib/lxcfs").rstrip("/")
        if not root.startswith("/") or ".." in root.split("/"):
            raise RuntimeError("SANDBOX_LXCFS_ROOT must be an absolute normalized path.")
        return [
            Mount(
                target,
                f"{root}/{source_suffix}",
                type="bind",
                read_only=True,
            )
            for source_suffix, target in _LXCFS_BINDINGS
        ]

    def _verify_virtualized_resource_view(self, expected_cpuset: str) -> None:
        """Fail closed unless procfs/sysfs expose the plan, not the host.

        Docker cgroups are the enforcement boundary. LXCFS is an additional
        visibility layer so user tools such as htop, btop and free report the
        same CPU/RAM allocation. Both layers are verified independently.
        """
        if not _lxcfs_enabled():
            return
        if self.container is None:
            raise RuntimeError("Sandbox container is unavailable for LXCFS verification.")

        def read_file(path: str) -> str:
            result = self.container.exec_run(["cat", path])
            if int(result.exit_code) != 0:
                detail = bytes(result.output or b"").decode("utf-8", "replace").strip()
                raise RuntimeError(
                    f"Container-aware resource view could not read {path}: {detail or 'unknown error'}"
                )
            return bytes(result.output or b"").decode("utf-8", "replace")

        cpuinfo = read_file("/proc/cpuinfo")
        meminfo = read_file("/proc/meminfo")
        online = read_file("/sys/devices/system/cpu/online").strip()

        visible_cpu_count = sum(
            1 for line in cpuinfo.splitlines() if line.split(":", 1)[0].strip() == "processor"
        )
        expected_cpu_count = len(_parse_cpu_list(expected_cpuset))
        online_cpu_count = len(_parse_cpu_list(online))
        if visible_cpu_count != expected_cpu_count or online_cpu_count != expected_cpu_count:
            raise RuntimeError(
                "Container CPU visibility does not match the selected plan: "
                f"/proc/cpuinfo={visible_cpu_count}, sysfs={online_cpu_count}, "
                f"expected={expected_cpu_count}. LXCFS is missing or not cgroup-aware."
            )

        mem_total_kib = None
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                fields = line.split()
                if len(fields) >= 2:
                    try:
                        mem_total_kib = int(fields[1])
                    except ValueError:
                        pass
                break
        if mem_total_kib is None:
            raise RuntimeError("Container-aware /proc/meminfo has no valid MemTotal value.")

        visible_memory = mem_total_kib * 1024
        expected_memory = int(self.policy["ram_gb"]) * 1024**3
        tolerance = max(32 * 1024**2, expected_memory // 50)  # 32 MiB or 2%.
        if abs(visible_memory - expected_memory) > tolerance:
            raise RuntimeError(
                "Container memory visibility does not match the selected plan: "
                f"MemTotal={visible_memory} bytes, expected={expected_memory} bytes. "
                "LXCFS is missing or not cgroup-aware."
            )

    def _instance_network_name(self) -> str:
        return f"cs-net-{uuid.UUID(self.instance_id).hex}"

    def _instance_network(self):
        if self.network is not None:
            return self.network

        network_mode = self.policy["network_mode"]
        if network_mode == "disabled":
            return None

        name = self._instance_network_name()
        try:
            self.network = self.client.networks.get(name)
        except NotFound:
            self.network = self.client.networks.create(
                name,
                driver="bridge",
                internal=(network_mode == "restricted"),
                attachable=False,
                labels={
                    "com.codesandbox.managed": "true",
                    "com.codesandbox.instance_id": self.instance_id,
                    "com.codesandbox.worker_id": _WORKER_ID,
                },
                check_duplicate=True,
            )
        return self.network

    def _stage_volumes(self) -> None:
        inputs = list(self.policy.get("inputs") or [])
        disk_limit_bytes = max(1, int(self.policy.get("disk_gb") or 1)) * 1024**3
        declared_input_bytes = sum(max(0, int(item.get("size_bytes") or 0)) for item in inputs)
        if declared_input_bytes > disk_limit_bytes:
            raise ValueError("Input files exceed the disk allocation selected by the plan.")
        needs_workspace = self.workspace_volume is not None
        needs_input = self.input_volume is not None
        if not needs_workspace and not needs_input:
            if inputs:
                raise ValueError("Inputs were supplied but this template has no input mount.")
            return

        helper_image = self._ensure_image(
            os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36"),
            pull_policy="if_not_present",
        )
        mounts = []
        if needs_workspace:
            mounts.append(Mount("/workspace", self.workspace_volume.name, type="volume", no_copy=True))
        if needs_input:
            mounts.append(Mount("/input", self.input_volume.name, type="volume", no_copy=True))
        helper = self.client.containers.create(
            helper_image,
            command=["sh", "-c", "sleep 300"],
            network_mode="none",
            mounts=mounts,
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=1777"},
            cap_drop=["ALL"],
            cap_add=["CHOWN", "FOWNER", "DAC_OVERRIDE"],
            security_opt=["no-new-privileges:true"],
            pids_limit=64,
            mem_limit=128 * 1024 * 1024,
            labels={
                "com.codesandbox.managed": "true",
                "com.codesandbox.instance_id": self.instance_id,
                "com.codesandbox.role": "volume-init",
            },
        )
        try:
            helper.start()
            max_bytes = int(self.policy.get("max_upload_bytes") or 0)
            if inputs and not needs_input:
                raise ValueError("Inputs were supplied but this template has no input mount.")
            for index, item in enumerate(inputs):
                storage_key = str(item.get("storage_key") or "")
                data = self.store.get_input(self.instance_id, storage_key, max_bytes)
                expected_checksum = str(item.get("checksum") or "")
                if expected_checksum and hashlib.sha256(data).hexdigest() != expected_checksum:
                    raise ValueError("Input checksum mismatch.")
                name = str(item.get("name") or f"input-{index + 1}")
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".") or f"input-{index + 1}"
                helper.put_archive("/input", tar_bytes(safe_name, data, 0o444))
                alias = str(self.policy.get("primary_input_alias") or "").strip()
                if index == 0 and alias and alias != safe_name:
                    helper.put_archive("/input", tar_bytes(alias, data, 0o444))

            run_as_user = self._terminal_user()
            numeric = re.fullmatch(r"([0-9]+)(?::([0-9]+))?", run_as_user or "")
            if numeric:
                uid = numeric.group(1)
                gid = numeric.group(2) or uid
                commands = []
                if needs_workspace:
                    commands.append(f"chown -R {uid}:{gid} /workspace && chmod 0770 /workspace")
                if needs_input:
                    commands.append("chmod -R a-w /input")
                if commands:
                    ownership = helper.exec_run(["sh", "-c", " && ".join(commands)])
                    if ownership.exit_code != 0:
                        raise ValueError("Sandbox volume ownership could not be prepared.")
        finally:
            helper.remove(force=True)

    def _terminal_user(self) -> str | None:
        configured = str(
            self.policy.get("terminal_user")
            or self.policy.get("run_as_user")
            or ""
        )
        if configured:
            return configured
        if self.policy.get("allow_root"):
            return None
        return os.environ.get("SANDBOX_DEFAULT_USER", "65532:65532")

    def _container_user(self) -> str | None:
        configured = str(self.policy.get("container_start_user") or "")
        if configured:
            return configured
        return self._terminal_user()

    def prepare(self) -> None:
        self._validate()
        self.client.ping()
        image = str(self.policy["docker_image"])
        self.policy["docker_image"] = self._ensure_image(image)
        self.policy["runtime_image"] = self.policy["docker_image"]
        labels = {
            "com.codesandbox.managed": "true",
            "com.codesandbox.instance_id": self.instance_id,
        }

        needs_workspace_volume = bool(self.policy.get("workspace_enabled", True)) or bool(
            self.policy.get("output_mount_path")
        ) or bool(self.policy.get("artifact_paths"))
        if needs_workspace_volume:
            workspace_name = f"cs-workspace-{self.instance_id}"
            try:
                self.client.volumes.get(workspace_name).remove(force=True)
            except NotFound:
                pass
            self.workspace_volume = self.client.volumes.create(
                name=workspace_name, labels=labels
            )

        if self.policy.get("input_mount_path"):
            input_name = f"cs-input-{self.instance_id}"
            try:
                self.client.volumes.get(input_name).remove(force=True)
            except NotFound:
                pass
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
        # No platform-injected shell command: a template without an explicit
        # command uses the image's own CMD. This keeps arbitrary/distroless
        # images valid and leaves lifecycle behavior under admin control.
        return [str(value) for value in command] if command else None

    def _verify_resource_limits(self, expected_cpuset: str) -> None:
        """Fail closed when Docker ignores a plan-derived cgroup limit."""
        if self.container is None:
            raise RuntimeError("Sandbox container was not created.")
        self.container.reload()
        host = dict(self.container.attrs.get("HostConfig") or {})
        expected_nano = int(self.policy["vcpu"]) * 1_000_000_000
        expected_memory = int(self.policy["ram_gb"]) * 1024**3
        expected_pids = int(self.policy["pids_limit"])
        actual = {
            "NanoCpus": int(host.get("NanoCpus") or 0),
            "Memory": int(host.get("Memory") or 0),
            "MemorySwap": int(host.get("MemorySwap") or 0),
            "PidsLimit": int(host.get("PidsLimit") or 0),
            "CpusetCpus": str(host.get("CpusetCpus") or ""),
        }
        errors = []
        if actual["NanoCpus"] != expected_nano:
            errors.append(f"CPU quota {actual['NanoCpus']} != {expected_nano}")
        if actual["Memory"] != expected_memory:
            errors.append(f"memory {actual['Memory']} != {expected_memory}")
        if actual["MemorySwap"] != expected_memory:
            errors.append(f"memory+swap {actual['MemorySwap']} != {expected_memory}")
        if actual["PidsLimit"] != expected_pids:
            errors.append(f"PID limit {actual['PidsLimit']} != {expected_pids}")
        if _parse_cpu_list(actual["CpusetCpus"]) != _parse_cpu_list(expected_cpuset):
            errors.append(
                f"CPU set {actual['CpusetCpus'] or '<empty>'} != {expected_cpuset}"
            )
        if errors:
            raise RuntimeError(
                "Docker did not apply the selected plan limits: " + "; ".join(errors)
            )

    def _verify_network_policy(self) -> None:
        """Verify the exact per-instance bridge instead of trusting labels.

        Full Internet must reach the configured probe. Restricted bridges must
        not reach it. Disabled instances use Docker's ``none`` network and need
        no helper probe.
        """
        mode = str(self.policy.get("network_mode") or "disabled")
        if mode == "disabled":
            return
        network = self._instance_network()
        if network is None:
            raise RuntimeError(f"{mode} sandbox network was not created.")
        helper_image = self._ensure_image(
            os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36"),
            pull_policy="if_not_present",
        )
        url = os.environ.get("SANDBOX_RUNTIME_EGRESS_TEST_URL", "https://example.com/")
        timeout = max(2, min(30, int(os.environ.get("SANDBOX_RUNTIME_EGRESS_TEST_TIMEOUT", "8"))))
        helper = self.client.containers.create(
            helper_image,
            command=["sh", "-c", 'wget -q -T "$PROBE_TIMEOUT" -O /dev/null -- "$PROBE_URL"'],
            environment={"PROBE_URL": url, "PROBE_TIMEOUT": str(timeout)},
            network=network.name,
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=8m,mode=1777"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=32,
            mem_limit=64 * 1024 * 1024,
            labels={
                "com.codesandbox.managed": "true",
                "com.codesandbox.instance_id": self.instance_id,
                "com.codesandbox.role": "network-policy-verifier",
            },
        )
        try:
            helper.start()
            result = helper.wait(timeout=timeout + 5)
            code = int((result or {}).get("StatusCode", 1))
            detail = helper.logs(tail=20).decode("utf-8", errors="replace").strip()
            if mode == "full_internet" and code != 0:
                raise RuntimeError(
                    "Template requests Full Internet, but the runtime bridge cannot reach "
                    f"the configured egress probe{': ' + detail if detail else '.'}"
                )
            if mode == "restricted" and code == 0:
                raise RuntimeError(
                    "Restricted network isolation failed: the internal runtime bridge "
                    "unexpectedly reached the external egress probe."
                )
        finally:
            try:
                helper.remove(force=True)
            except Exception:
                pass

    def _open_workspace_terminal(self):
        """Open a non-root terminal in a real workspace-only chroot.

        The helper has no network and receives only the workspace volume. A
        short root bootstrap creates a disposable BusyBox jail, then ``su``
        drops the interactive shell to the template UID. The user cannot reach
        the analysis container, uploaded input mount, Docker daemon, or host.
        """
        if self.workspace_volume is None:
            raise RuntimeError("Workspace terminal requires a workspace volume.")
        helper_image = self._ensure_image(
            os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36"),
            pull_policy="if_not_present",
        )
        terminal_user = self._terminal_user() or "65532:65532"
        numeric = re.fullmatch(r"([0-9]+)(?::([0-9]+))?", terminal_user)
        if not numeric:
            raise RuntimeError("Workspace terminal requires a numeric UID:GID.")
        uid = numeric.group(1)
        gid = numeric.group(2) or uid
        bootstrap = (
            "set -eu; "
            "mkdir -p /jail/bin /jail/etc /jail/tmp /jail/workspace; "
            "cp /bin/busybox /jail/bin/busybox; "
            "/jail/bin/busybox --install /jail/bin; "
            f"printf 'sandbox:x:{uid}:{gid}:Sandbox User:/workspace:/bin/sh\\n' > /jail/etc/passwd; "
            f"printf 'sandbox:x:{gid}:\\n' > /jail/etc/group; "
            "chmod 0755 /jail /jail/bin /jail/etc /jail/tmp; "
            f"chown {uid}:{gid} /jail/workspace; chmod 0770 /jail/workspace; "
            "touch /jail/.ready; "
            "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"
        )
        helper = self.client.containers.create(
            helper_image,
            command=["sh", "-c", bootstrap],
            network_mode="none",
            mounts=[Mount(
                "/jail/workspace",
                self.workspace_volume.name,
                type="volume",
                no_copy=True,
            )],
            # Writable disposable helper root is required only to assemble the
            # jail. The interactive shell itself is chrooted and non-root.
            read_only=False,
            tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=1777"},
            cap_drop=["ALL"],
            cap_add=["CHOWN", "DAC_OVERRIDE", "SETGID", "SETUID", "SYS_CHROOT"],
            security_opt=["no-new-privileges:true"],
            pids_limit=min(128, int(self.policy.get("pids_limit") or 128)),
            mem_limit=min(256 * 1024 * 1024, int(self.policy["ram_gb"]) * 1024**3),
            nano_cpus=min(500_000_000, int(self.policy["vcpu"]) * 1_000_000_000),
            labels={
                "com.codesandbox.managed": "true",
                "com.codesandbox.instance_id": self.instance_id,
                "com.codesandbox.role": "workspace-terminal",
            },
        )
        try:
            helper.start()
            ready = None
            for _ in range(40):
                ready = helper.exec_run(["sh", "-c", "test -f /jail/.ready"])
                if ready.exit_code == 0:
                    break
                time.sleep(0.05)
            if ready is None or ready.exit_code != 0:
                raise RuntimeError("Workspace terminal jail could not be prepared.")
            created = self.client.api.exec_create(
                helper.id,
                ["/bin/chroot", "/jail", "/bin/su", "-", "sandbox"],
                stdin=True,
                tty=True,
                workdir="/",
                environment={"TERM": "xterm-256color"},
            )
            exec_id = created["Id"]
            wrapper = self.client.api.exec_start(exec_id, detach=False, tty=True, socket=True)
        except Exception:
            try:
                helper.remove(force=True)
            except Exception:
                pass
            raise

        def cleanup() -> None:
            try:
                helper.remove(force=True)
            except Exception:
                pass

        return exec_id, wrapper, cleanup

    def start(self) -> dict[str, Any]:
        mounts = []
        if self.workspace_volume is not None:
            if bool(self.policy.get("workspace_enabled", True)):
                mounts.append(Mount(
                    self.policy["working_dir"],
                    self.workspace_volume.name,
                    type="volume",
                    no_copy=True,
                ))
            output_mount = self.policy.get("output_mount_path")
            if output_mount:
                mounts.append(Mount(
                    output_mount,
                    self.workspace_volume.name,
                    type="volume",
                    no_copy=True,
                ))
        if self.input_volume is not None and self.policy.get("input_mount_path"):
            mounts.append(Mount(
                self.policy["input_mount_path"],
                self.input_volume.name,
                type="volume",
                read_only=True,
                no_copy=True,
            ))
        # Bind LXCFS-provided procfs/sysfs views into every user-facing runtime.
        # These mounts affect reporting only; cgroups remain the hard limit.
        mounts.extend(self._lxcfs_mounts())
        expected_cpuset = self._cpuset_cpus()
        kwargs: dict[str, Any] = {
            "image": self.policy["docker_image"],
            "command": self._container_command(),
            "entrypoint": self.policy.get("entrypoint") or None,
            "environment": dict(self.policy.get("environment") or {}),
            "name": f"cs-{self.instance_id}",
            "detach": True,
            "stdin_open": False,
            "tty": False,
            "working_dir": self.policy["working_dir"],
            "user": self._container_user(),
            "mounts": mounts,
            "read_only": bool(self.policy.get("read_only_root", True)),
            "tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=64m,mode=1777"},
            # Quota enforces CPU time and cpuset constrains scheduling affinity.
            # /proc-based tools such as htop may still display host topology;
            # post-start HostConfig verification below proves the actual limits.
            "nano_cpus": int(self.policy["vcpu"]) * 1_000_000_000,
            "cpuset_cpus": expected_cpuset,
            "mem_limit": int(self.policy["ram_gb"]) * 1024**3,
            "memswap_limit": int(self.policy["ram_gb"]) * 1024**3,
            "pids_limit": int(self.policy["pids_limit"]),
            "cap_drop": ["ALL"],
            "cap_add": list((self.policy.get("security") or {}).get("cap_add") or []),
            "security_opt": (["no-new-privileges:true"] if (self.policy.get("security") or {}).get("no_new_privileges", True) else []),
            "privileged": False,
            "init": True,
            "labels": self._container_labels(),
        }
        network_mode = self.policy["network_mode"]
        if network_mode == "disabled":
            kwargs["network_mode"] = "none"
        else:
            network = self._instance_network()
            if network is None:
                raise RuntimeError("Sandbox network could not be created.")
            kwargs["network"] = network.name

        self.container = self.client.containers.create(**kwargs)
        try:
            self.container.start()
            self._verify_resource_limits(expected_cpuset)
            self._verify_virtualized_resource_view(expected_cpuset)
            self._verify_network_policy()
        except Exception:
            try:
                self.container.remove(force=True)
            except Exception:
                pass
            raise
        self.started_monotonic = time.monotonic()
        return {
            "runtime_provider": "docker",
            "runtime_id": self.container.id,
            "runtime_node_id": os.environ.get("RUNTIME_NODE_ID", platform.node()),
            "workspace_volume_id": self.workspace_volume.name if self.workspace_volume is not None else None,
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
        if self.policy.get("terminal_scope") == "workspace":
            return self._open_workspace_terminal()
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
            user=self._terminal_user(),
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
        return exec_id, socket_wrapper, None

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
        artifact_paths = list(self.policy.get("artifact_paths") or [])
        if self.container is None or not artifact_paths:
            return []
        target = self.container
        helper = None
        if self.workspace_volume is not None:
            helper_image = self._ensure_image(
                os.environ.get("SANDBOX_VOLUME_INIT_IMAGE", "busybox:1.36")
            )
            helper = self.client.containers.create(
                helper_image,
                command=["sh", "-c", "sleep 300"],
                network_mode="none",
                mounts=(
                    [
                        Mount(
                            self.policy["working_dir"],
                            self.workspace_volume.name,
                            type="volume",
                            read_only=True,
                            no_copy=True,
                        )
                    ]
                    if bool(self.policy.get("workspace_enabled", True))
                    else []
                ) + (
                    [
                        Mount(
                            self.policy["output_mount_path"],
                            self.workspace_volume.name,
                            type="volume",
                            read_only=True,
                            no_copy=True,
                        )
                    ]
                    if self.policy.get("output_mount_path")
                    else []
                ),
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=1777"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                pids_limit=64,
                mem_limit=128 * 1024 * 1024,
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
                artifact_paths,
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
                    except Exception as exc:
                        log.warning("volume cleanup failed instance=%s error=%s", self.instance_id[:8], exc)
            if self.network is not None:
                try:
                    self.network.remove()
                except Exception as exc:
                    log.warning("network cleanup failed instance=%s error=%s", self.instance_id[:8], exc)

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
        if str(runner.policy.get("network_mode") or "disabled") != "disabled":
            try:
                runner.network = runner.client.networks.get(runner._instance_network_name())
            except NotFound:
                runner.network = None
        return runner
