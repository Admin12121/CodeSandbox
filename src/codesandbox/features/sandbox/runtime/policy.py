from __future__ import annotations

import json
import posixpath
import re
import shlex
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from ..image_refs import normalize_image_reference


POLICY_VERSION = 1
NETWORK_ALIASES = {
    "isolated": "restricted",
    "fake_internet": "restricted",
    "controlled_proxy": "restricted",
    "allowlist": "restricted",
}
SUPPORTED_NETWORK_MODES = {"disabled", "restricted", "full_internet"}
PROTECTED_MOUNT_PREFIXES = ("/dev", "/etc", "/proc", "/run", "/sys", "/var/run")

# The admin "Config" tab persists `SandboxTemplate.runtime_config` as a JSON
# map of virtual filename -> raw text content (the Config IDE is a generic
# multi-file editor, not tied to any one template). This one reserved
# filename is where a template's *validation* config lives — the generic,
# data-driven replacement for what used to be hardcoded per-slug checks on
# a template's required/forbidden command-line flags.
RUNTIME_CONFIG_FILE = "runtime.json"
WORKFLOW_CONFIG_FILE = "workflow.json"

UI_MODE_ALIASES = {
    "terminal": "terminal_only",
    "editor": "lab_ui",
    "full_ui": "lab_ui",
    "background": "background_run",
    "gui": "desktop_gui",
}
SUPPORTED_UI_MODES = {"terminal_only", "lab_ui", "background_run", "desktop_gui", "android_ui"}
SUPPORTED_IMAGE_PULL_POLICIES = {"always", "if_not_present", "never"}

# Fixed capability profiles are selected by the platform. Templates cannot
# provide arbitrary capability names. ``sudo_user`` is enough for the seeded
# non-root IDE bootstrap and package management; ``root_study`` additionally
# enables common debugging/network-study operations without granting
# privileged mode, host mounts, Docker access, SYS_ADMIN, or device access.
SUDO_USER_CAPABILITIES = [
    "AUDIT_WRITE",
    "CHOWN",
    "DAC_OVERRIDE",
    "FOWNER",
    "FSETID",
    "SETFCAP",
    "SETGID",
    "SETPCAP",
    "SETUID",
]
ROOT_STUDY_CAPABILITIES = [
    *SUDO_USER_CAPABILITIES,
    "KILL",
    "MKNOD",
    "NET_BIND_SERVICE",
    "NET_RAW",
    "SYS_CHROOT",
    "SYS_PTRACE",
]
SUPPORTED_SECURITY_PROFILES = {"restricted", "root_study"}
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_PLATFORM_ENVIRONMENT_NAMES = {
    "CODESANDBOX_USERNAME",
    "CODESANDBOX_INPUT_NAME",
    "USER",
    "LOGNAME",
}


def parse_runtime_config(runtime_config: Any) -> dict:
    """Extract the structured validation config (required_args,
    forbidden_args, allowed_file_types, max_input_size_mb) from a template's
    runtime_config files blob. Malformed/absent config fails open to "no
    extra restrictions" — it's admin-authored convenience validation, not a
    security boundary (those stay as fixed worker-side invariants)."""
    if not runtime_config:
        return {}
    try:
        files = json.loads(runtime_config) if isinstance(runtime_config, str) else runtime_config
        raw = files.get(RUNTIME_CONFIG_FILE) if isinstance(files, dict) else None
        parsed = json.loads(raw) if raw else {}
        parsed = parsed if isinstance(parsed, dict) else {}
        workflow_raw = files.get(WORKFLOW_CONFIG_FILE) if isinstance(files, dict) else None
        if workflow_raw and "workflow" not in parsed and "stage_graph_json" not in parsed:
            workflow = json.loads(workflow_raw)
            if isinstance(workflow, dict):
                parsed["workflow"] = workflow
        return parsed
    except (TypeError, ValueError):
        return {}


def validate_command_args(
    command: str | list[str] | None, required_args: list[str], forbidden_args: list[str]
) -> str | None:
    """Generic required/forbidden-argument check for a template's
    default_command, sourced entirely from that template's own
    runtime_config — replaces what used to be a hardcoded per-slug special
    case with the same behavior driven by data instead of code."""
    if not required_args and not forbidden_args:
        return None
    text = " ".join(command) if isinstance(command, list) else str(command or "")
    missing = [arg for arg in required_args if arg not in text]
    if missing:
        return f"Default command must include: {', '.join(missing)}."
    present = [arg for arg in forbidden_args if arg in text]
    if present:
        return f"Default command must not include: {', '.join(present)}."
    return None


class RuntimePolicyError(ValueError):
    pass


def _value(source: Any, name: str, default=None):
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def normalize_network_mode(value: Any) -> str:
    mode = str(value or "disabled").strip().lower()
    return NETWORK_ALIASES.get(mode, mode)


def normalize_ui_mode(value: Any, default: str = "terminal_only") -> str:
    mode = str(value or "").strip().lower()
    mode = UI_MODE_ALIASES.get(mode, mode)
    return mode if mode in SUPPORTED_UI_MODES else default


def _json_list(value: Any, default: list[str] | None = None) -> list[str]:
    if value is None or value == "":
        return list(default or [])
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError):
        decoded = [line.strip() for line in str(value).replace(",", "\n").splitlines()]
    if not isinstance(decoded, list):
        raise RuntimePolicyError("Expected a JSON list.")
    return [str(item).strip() for item in decoded if str(item).strip()]


def _ui_modes(value: Any, default: list[str]) -> list[str]:
    modes = []
    for item in _json_list(value, default):
        mode = normalize_ui_mode(item, default="")
        if mode and mode not in modes:
            modes.append(mode)
    return modes or list(default)


def _absolute_container_path(value: Any, field: str) -> str:
    raw = str(value or "").strip()
    if not raw.startswith("/"):
        raise RuntimePolicyError(f"{field} must be an absolute container path.")
    normalized = posixpath.normpath(raw)
    if normalized == "/" or any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in PROTECTED_MOUNT_PREFIXES
    ):
        raise RuntimePolicyError(f"{field} points to a protected container path.")
    return normalized


def _optional_container_path(value: Any, field: str) -> str | None:
    raw = str(value or "").strip()
    return _absolute_container_path(raw, field) if raw else None


def _runtime_environment(value: Any) -> dict[str, str]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise RuntimePolicyError("Runtime environment must be a JSON object.")
    if len(value) > 128:
        raise RuntimePolicyError("Runtime environment may contain at most 128 variables.")
    result: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name)
        if not _ENV_NAME_RE.fullmatch(name):
            raise RuntimePolicyError(f"Invalid runtime environment variable name: {name}.")
        if name in _PLATFORM_ENVIRONMENT_NAMES:
            raise RuntimePolicyError(
                f"Runtime environment variable {name} is managed by the platform."
            )
        rendered = str(raw_value)
        if "\x00" in rendered or len(rendered) > 16384:
            raise RuntimePolicyError(f"Invalid value for runtime environment variable: {name}.")
        result[name] = rendered
    return result


def _image_pull_policy(value: Any) -> str:
    policy = str(value or "if_not_present").strip().lower()
    if policy not in SUPPORTED_IMAGE_PULL_POLICIES:
        raise RuntimePolicyError(
            "Image pull policy must be one of: always, if_not_present, never."
        )
    return policy


def _exposed_ports(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise RuntimePolicyError("Exposed ports must be a JSON list.")
    result: list[int] = []
    for raw in value:
        try:
            port = int(raw)
        except (TypeError, ValueError) as exc:
            raise RuntimePolicyError("Exposed ports must contain integers.") from exc
        if not 1 <= port <= 65535:
            raise RuntimePolicyError("Exposed ports must be between 1 and 65535.")
        if port not in result:
            result.append(port)
    return result


def _command(value: Any) -> list[str] | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        result = [str(part) for part in value]
    else:
        raw = str(value).strip()
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, list):
            result = [str(part) for part in decoded]
        else:
            try:
                result = shlex.split(raw, posix=True)
            except ValueError as exc:
                raise RuntimePolicyError("Default command has invalid quoting.") from exc
    if not result or any("\x00" in part for part in result):
        raise RuntimePolicyError("Default command is invalid.")
    return result


@dataclass(frozen=True)
class EffectivePlan:
    id: str
    name: str
    ind_vcpu: int
    ind_ram_gb: int
    ind_disk_gb: int
    ind_cost_hr: Decimal
    org_vcpu: int
    org_ram_gb: int
    org_disk_gb: int
    org_cost_hr: Decimal
    max_timeout_hr: int
    network_mode: str
    min_billable_minutes: int
    allowed_network_modes: tuple[str, ...]
    full_internet_enabled: bool
    is_active: bool
    is_enabled: bool
    sort_order: int

    def to_dict(self) -> dict:
        result = asdict(self)
        result["ind_cost_hr"] = str(self.ind_cost_hr)
        result["org_cost_hr"] = str(self.org_cost_hr)
        result["allowed_network_modes"] = list(self.allowed_network_modes)
        return result

    def tier(self, workspace_type: str) -> dict:
        is_org = workspace_type == "org"
        return {
            "vcpu": self.org_vcpu if is_org else self.ind_vcpu,
            "ram_gb": self.org_ram_gb if is_org else self.ind_ram_gb,
            "disk_gb": self.org_disk_gb if is_org else self.ind_disk_gb,
            "cost_hr": self.org_cost_hr if is_org else self.ind_cost_hr,
        }


def resolve_effective_plan(template: Any, global_plan: Any, template_plan: Any = None) -> EffectivePlan:
    """Resolve one template against one global plan.

    Resource and price fields are deliberately read only from SandboxPlan.
    SandboxTemplatePlan is now only an availability mapping (`is_enabled`).
    Legacy override columns remain readable for migration compatibility but are
    intentionally ignored here.
    """

    allowed = tuple(
        dict.fromkeys(
            normalize_network_mode(mode)
            for mode in _json_list(
                _value(global_plan, "allowed_network_modes"),
                ["disabled", "restricted"],
            )
            if normalize_network_mode(mode) in SUPPORTED_NETWORK_MODES
        )
    ) or ("disabled",)

    network_mode = normalize_network_mode(_value(template, "network_mode", "disabled"))
    if network_mode not in SUPPORTED_NETWORK_MODES:
        raise RuntimePolicyError(f"Unsupported network mode: {network_mode}.")
    if network_mode not in allowed:
        raise RuntimePolicyError(
            f"Network mode '{network_mode}' is not allowed by plan '{_value(global_plan, 'id', '')}'."
        )

    full_internet_enabled = bool(_value(template, "allow_full_internet", False))
    if network_mode == "full_internet" and not full_internet_enabled:
        raise RuntimePolicyError("Full internet must be explicitly enabled on the template.")

    max_timeout_hr = max(
        1, min(72, int(_value(template, "max_timeout_hr", 2) or 2))
    )
    min_billable_minutes = max(
        0, min(1440, int(_value(global_plan, "min_billable_minutes", 1) or 0))
    )

    return EffectivePlan(
        id=str(_value(global_plan, "id", "")),
        name=str(_value(global_plan, "name", "")),
        ind_vcpu=max(1, int(_value(global_plan, "ind_vcpu", 1))),
        ind_ram_gb=max(1, int(_value(global_plan, "ind_ram_gb", 1))),
        ind_disk_gb=max(1, int(_value(global_plan, "ind_disk_gb", 10))),
        ind_cost_hr=max(Decimal("0"), _decimal(_value(global_plan, "ind_cost_hr", 0))),
        org_vcpu=max(1, int(_value(global_plan, "org_vcpu", 2))),
        org_ram_gb=max(1, int(_value(global_plan, "org_ram_gb", 2))),
        org_disk_gb=max(1, int(_value(global_plan, "org_disk_gb", 20))),
        org_cost_hr=max(Decimal("0"), _decimal(_value(global_plan, "org_cost_hr", 0))),
        max_timeout_hr=max_timeout_hr,
        network_mode=network_mode,
        min_billable_minutes=min_billable_minutes,
        allowed_network_modes=allowed,
        full_internet_enabled=full_internet_enabled,
        is_active=bool(_value(global_plan, "is_active", True)),
        is_enabled=bool(_value(template_plan, "is_enabled", True)),
        sort_order=int(_value(global_plan, "sort_order", 0) or 0),
    )


class PolicyBuilder:
    """Build the immutable, server-authoritative runtime policy snapshot."""

    def build(
        self,
        template: Any,
        plan: EffectivePlan | dict,
        workspace_type: str = "personal",
        user_config: dict | None = None,
    ) -> dict:
        effective = plan if isinstance(plan, EffectivePlan) else self._from_resolved_dict(plan)
        tier = effective.tier(workspace_type)
        runtime_class = str(_value(template, "runtime_class", "container"))
        raw_image = str(_value(template, "docker_image", "")).strip()
        if not raw_image:
            raise RuntimePolicyError("A runtime image or target is required.")

        if runtime_class in {"container", "tool_job"}:
            try:
                runtime_image = normalize_image_reference(raw_image)
            except ValueError as exc:
                raise RuntimePolicyError(str(exc)) from exc
            runtime_provider = "docker"
        else:
            if any(char.isspace() for char in raw_image):
                raise RuntimePolicyError("Runtime image or target must not contain whitespace.")
            runtime_image = raw_image
            runtime_provider = {
                "microvm": "firecracker",
                "firecracker_microvm": "firecracker",
                "fullvm": "qemu",
                "qemu_vm": "qemu",
                "android": "android",
                "android_emulator": "android",
            }.get(runtime_class, runtime_class)

        interface_modes = _ui_modes(
            _value(template, "allowed_ui_modes")
            or _value(template, "interface_mode", "terminal_only"),
            ["terminal_only"],
        )
        requested_ui_mode = None
        if user_config:
            requested_ui_mode = user_config.get("ui_mode") or user_config.get("interface_mode")
        if requested_ui_mode and normalize_ui_mode(requested_ui_mode, default="") in interface_modes:
            interface_modes = [normalize_ui_mode(requested_ui_mode)]
        default_ui_mode = normalize_ui_mode(
            _value(template, "default_ui_mode", interface_modes[0]), interface_modes[0]
        )
        if default_ui_mode not in interface_modes:
            default_ui_mode = interface_modes[0]

        template_runtime_config = parse_runtime_config(_value(template, "runtime_config"))
        command = _command(_value(template, "default_command"))
        entrypoint = _command(template_runtime_config.get("entrypoint"))
        environment = _runtime_environment(template_runtime_config.get("environment"))
        image_pull_policy = _image_pull_policy(template_runtime_config.get("image_pull_policy"))
        exposed_ports = _exposed_ports(template_runtime_config.get("exposed_ports"))
        container_start_user = str(template_runtime_config.get("container_start_user") or "").strip()
        terminal_user = str(template_runtime_config.get("terminal_user") or "").strip()
        allow_sudo = bool(template_runtime_config.get("allow_sudo", False))
        security_profile = str(
            template_runtime_config.get("security_profile") or "restricted"
        ).strip().lower()
        if security_profile not in SUPPORTED_SECURITY_PROFILES:
            raise RuntimePolicyError("Unsupported sandbox security profile.")
        template_allows_root = bool(_value(template, "allow_root", False))
        if security_profile == "root_study" and not template_allows_root:
            raise RuntimePolicyError(
                "The root_study security profile requires explicit root access."
            )
        if security_profile == "root_study":
            capability_add = list(ROOT_STUDY_CAPABILITIES)
        elif allow_sudo:
            capability_add = list(SUDO_USER_CAPABILITIES)
        else:
            capability_add = []
        driver_config = template_runtime_config.get("driver")
        driver_config = driver_config if isinstance(driver_config, dict) else {}

        required_args = [str(a) for a in template_runtime_config.get("required_args") or []]
        forbidden_args = [str(a) for a in template_runtime_config.get("forbidden_args") or []]
        command_error = validate_command_args(command, required_args, forbidden_args)
        if command_error:
            raise RuntimePolicyError(command_error)

        working_dir = _absolute_container_path(
            _value(template, "working_dir", "/workspace") or "/workspace",
            "Working directory",
        )
        workspace_enabled = bool(template_runtime_config.get("workspace_enabled", True))
        input_mount = _optional_container_path(
            _value(template, "input_mount_path", ""), "Input mount"
        )
        output_mount = _optional_container_path(
            _value(template, "output_mount_path", ""), "Output mount"
        )
        input_required = bool(_value(template, "input_required", False))
        if input_required and not input_mount:
            raise RuntimePolicyError("Input is required but no input mount path is configured.")

        named_paths = []
        if workspace_enabled:
            named_paths.append(("Working directory", working_dir))
        if input_mount:
            named_paths.append(("Input mount", input_mount))
        if output_mount:
            named_paths.append(("Output mount", output_mount))
        for index, (left_name, left_path) in enumerate(named_paths):
            for right_name, right_path in named_paths[index + 1:]:
                if (
                    left_path == right_path
                    or left_path.startswith(right_path + "/")
                    or right_path.startswith(left_path + "/")
                ):
                    raise RuntimePolicyError(
                        f"{left_name} and {right_name} must be separate, non-overlapping paths."
                    )

        artifact_paths = [
            _absolute_container_path(path, "Artifact path")
            for path in _json_list(_value(template, "artifact_paths"), [])
        ]
        artifact_roots = []
        if workspace_enabled:
            artifact_roots.append(working_dir)
        if output_mount:
            artifact_roots.append(output_mount)
        if artifact_paths and not artifact_roots:
            raise RuntimePolicyError(
                "Artifact paths require a workspace or output mount."
            )
        if any(
            not any(path == root or path.startswith(root + "/") for root in artifact_roots)
            for path in artifact_paths
        ):
            raise RuntimePolicyError(
                "Artifact paths must be inside the workspace or output mount."
            )

        primary_input_alias = str(
            template_runtime_config.get("primary_input_alias") or ""
        ).strip()
        if primary_input_alias and not _SAFE_FILENAME_RE.fullmatch(primary_input_alias):
            raise RuntimePolicyError(
                "Primary input alias must be a safe filename without path separators."
            )

        allowed_file_types = [
            str(t).lower() for t in template_runtime_config.get("allowed_file_types") or []
        ]
        max_input_size_mb = template_runtime_config.get("max_input_size_mb")
        test_config_raw = template_runtime_config.get("test_config")
        test_config = test_config_raw if isinstance(test_config_raw, dict) else {}
        ui_feature_config = template_runtime_config.get("ui")
        ui_feature_config = ui_feature_config if isinstance(ui_feature_config, dict) else {}
        desktop_gui_raw = ui_feature_config.get("desktop_gui")
        desktop_gui_config = desktop_gui_raw if isinstance(desktop_gui_raw, dict) else {}
        android_ui_raw = ui_feature_config.get("android_ui")
        android_ui_config = android_ui_raw if isinstance(android_ui_raw, dict) else {}

        runtime_evidence_logs: list[str] = []
        for value in test_config.get("log_contains") or []:
            pattern = str(value or "").strip()
            if pattern and pattern not in runtime_evidence_logs:
                runtime_evidence_logs.append(pattern)
        for mode_config in ui_feature_config.values():
            if not isinstance(mode_config, dict):
                continue
            completion = mode_config.get("completion_log")
            values = completion if isinstance(completion, list) else [completion]
            for value in values:
                pattern = str(value or "").strip()
                if pattern and pattern not in runtime_evidence_logs:
                    runtime_evidence_logs.append(pattern)
        try:
            workflow_graph = json.loads(str(_value(template, "ui_workflow_json", "") or "{}"))
        except (TypeError, ValueError):
            workflow_graph = {}
        for node in workflow_graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            for requirement in node.get("completion_requirements") or []:
                value = str(requirement or "").strip()
                if value.startswith("log:"):
                    pattern = value.removeprefix("log:").strip()
                    if pattern and pattern not in runtime_evidence_logs:
                        runtime_evidence_logs.append(pattern)

        return {
            "version": POLICY_VERSION,
            "runtime_class": runtime_class,
            "runtime_provider": runtime_provider,
            "template_id": str(_value(template, "id", "")),
            "template_slug": str(_value(template, "slug", "")),
            "sandbox_type": str(_value(template, "sandbox_type", "interactive")),
            "runtime_image": runtime_image,
            "docker_image": runtime_image if runtime_provider == "docker" else "",
            "image_pull_policy": image_pull_policy,
            "entrypoint": entrypoint,
            "default_command": command,
            "environment": environment,
            "exposed_ports": exposed_ports,
            "driver_config": driver_config,
            "interface_modes": interface_modes,
            "default_ui_mode": default_ui_mode,
            "network_mode": effective.network_mode,
            "full_internet_enabled": effective.full_internet_enabled,
            # These values come only from SandboxPlan. Template config and
            # template-plan mappings cannot override them.
            "vcpu": tier["vcpu"],
            "ram_gb": tier["ram_gb"],
            "disk_gb": tier["disk_gb"],
            "cost_hr": str(tier["cost_hr"]),
            "currency": "GBP",
            "min_billable_sec": effective.min_billable_minutes * 60,
            "max_timeout_sec": effective.max_timeout_hr * 3600,
            "working_dir": working_dir,
            "workspace_enabled": workspace_enabled,
            "input_mount_path": input_mount,
            "output_mount_path": output_mount,
            "artifact_paths": artifact_paths,
            "input_required": input_required,
            "primary_input_alias": primary_input_alias or None,
            "max_upload_bytes": max(
                1, int(max_input_size_mb or _value(template, "max_upload_mb", 50) or 50)
            ) * 1024 * 1024,
            "allowed_file_types": allowed_file_types,
            "required_args": required_args,
            "forbidden_args": forbidden_args,
            "test_config": test_config,
            "runtime_evidence_logs": runtime_evidence_logs,
            "desktop_gui": desktop_gui_config,
            "android_ui": android_ui_config,
            "allow_root": template_allows_root,
            "read_only_root": bool(_value(template, "read_only_root", True)),
            "run_as_user": str(_value(template, "run_as_user", "") or ""),
            "container_start_user": container_start_user,
            "terminal_user": terminal_user,
            "allow_sudo": allow_sudo,
            "pids_limit": max(32, min(4096, int(_value(template, "pids_limit", 256) or 256))),
            "security": {
                "profile": security_profile,
                "no_new_privileges": not allow_sudo,
                "cap_drop": ["ALL"],
                "cap_add": capability_add,
                "privileged": False,
                "host_mounts": False,
                "docker_socket": False,
            },
        }

    @staticmethod
    def _from_resolved_dict(plan: dict) -> EffectivePlan:
        return EffectivePlan(
            id=str(plan.get("id", "")),
            name=str(plan.get("name", "")),
            ind_vcpu=int(plan.get("ind_vcpu", 1)),
            ind_ram_gb=int(plan.get("ind_ram_gb", 1)),
            ind_disk_gb=int(plan.get("ind_disk_gb", 10)),
            ind_cost_hr=_decimal(plan.get("ind_cost_hr", 0)),
            org_vcpu=int(plan.get("org_vcpu", 2)),
            org_ram_gb=int(plan.get("org_ram_gb", 2)),
            org_disk_gb=int(plan.get("org_disk_gb", 20)),
            org_cost_hr=_decimal(plan.get("org_cost_hr", 0)),
            max_timeout_hr=int(plan.get("max_timeout_hr", 2)),
            network_mode=normalize_network_mode(plan.get("network_mode", "disabled")),
            min_billable_minutes=int(plan.get("min_billable_minutes", 1)),
            allowed_network_modes=tuple(plan.get("allowed_network_modes", ["disabled", "restricted"])),
            full_internet_enabled=bool(plan.get("full_internet_enabled", False)),
            is_active=bool(plan.get("is_active", True)),
            is_enabled=bool(plan.get("is_enabled", True)),
            sort_order=int(plan.get("sort_order", 0)),
        )
