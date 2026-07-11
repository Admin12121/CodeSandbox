from __future__ import annotations

import json
import posixpath
import shlex
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


POLICY_VERSION = 1
NETWORK_ALIASES = {
    "isolated": "restricted",
    "fake_internet": "restricted",
    "controlled_proxy": "restricted",
    "allowlist": "restricted",
}
SUPPORTED_NETWORK_MODES = {"disabled", "restricted", "full_internet"}
PROTECTED_MOUNT_PREFIXES = ("/dev", "/etc", "/proc", "/run", "/sys", "/var/run")


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
    def override(name: str, fallback):
        value = _value(template_plan, name)
        return fallback if value is None else value

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

    network_mode = normalize_network_mode(
        override("network_mode", _value(template, "network_mode", "disabled"))
    )
    if network_mode not in SUPPORTED_NETWORK_MODES:
        raise RuntimePolicyError(f"Unsupported network mode: {network_mode}.")

    template_allows_internet = bool(_value(template, "allow_full_internet", False))
    full_internet_enabled = template_allows_internet and bool(
        override("full_internet_enabled", template_allows_internet)
    )
    if str(_value(template, "sandbox_type", "interactive")) in {
        "malware",
        "reverse_engineering",
    }:
        full_internet_enabled = False
    if network_mode == "full_internet" and not full_internet_enabled:
        raise RuntimePolicyError("Full internet is not enabled for this template and plan.")
    if network_mode not in allowed:
        raise RuntimePolicyError(
            f"Network mode '{network_mode}' is not allowed by plan '{_value(global_plan, 'id', '')}'."
        )

    max_timeout_hr = max(
        1,
        min(72, int(override("max_timeout_hr", _value(template, "max_timeout_hr", 2)) or 2)),
    )
    min_billable_minutes = max(
        0,
        min(
            1440,
            int(
                override(
                    "min_billable_minutes",
                    _value(global_plan, "min_billable_minutes", 1),
                )
                or 0
            ),
        ),
    )

    return EffectivePlan(
        id=str(_value(global_plan, "id", "")),
        name=str(_value(global_plan, "name", "")),
        ind_vcpu=max(1, int(override("ind_vcpu", _value(global_plan, "ind_vcpu", 1)))),
        ind_ram_gb=max(1, int(override("ind_ram_gb", _value(global_plan, "ind_ram_gb", 1)))),
        ind_disk_gb=max(1, int(override("ind_disk_gb", _value(global_plan, "ind_disk_gb", 10)))),
        ind_cost_hr=max(Decimal("0"), _decimal(override("ind_cost_hr", _value(global_plan, "ind_cost_hr", 0)))),
        org_vcpu=max(1, int(override("org_vcpu", _value(global_plan, "org_vcpu", 2)))),
        org_ram_gb=max(1, int(override("org_ram_gb", _value(global_plan, "org_ram_gb", 2)))),
        org_disk_gb=max(1, int(override("org_disk_gb", _value(global_plan, "org_disk_gb", 20)))),
        org_cost_hr=max(Decimal("0"), _decimal(override("org_cost_hr", _value(global_plan, "org_cost_hr", 0)))),
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
        image = str(_value(template, "docker_image", "")).strip()
        if runtime_class in {"container", "tool_job"} and not image:
            raise RuntimePolicyError("Docker image is required for a container runtime.")

        working_dir = _absolute_container_path(
            _value(template, "working_dir", "/workspace"), "Working directory"
        )
        input_mount = _absolute_container_path(
            _value(template, "input_mount_path", "/input"), "Input mount"
        )
        output_mount = _absolute_container_path(
            _value(template, "output_mount_path", "/output"), "Output mount"
        )
        if len({working_dir, input_mount, output_mount}) != 3:
            raise RuntimePolicyError("Workspace, input, and output mounts must be distinct.")

        artifact_paths = [
            _absolute_container_path(path, "Artifact path")
            for path in _json_list(_value(template, "artifact_paths"), [output_mount])
        ]
        if any(
            path != working_dir
            and not path.startswith(working_dir + "/")
            and path != output_mount
            and not path.startswith(output_mount + "/")
            for path in artifact_paths
        ):
            raise RuntimePolicyError("Artifact paths must be inside workspace or output mounts.")
        interface_modes = [
            mode.strip()
            for mode in str(_value(template, "interface_mode", "terminal")).split(",")
            if mode.strip()
        ] or ["terminal"]
        if user_config and user_config.get("interface_mode") in interface_modes:
            interface_modes = [str(user_config["interface_mode"])]

        command = _command(_value(template, "default_command"))
        slug = str(_value(template, "slug", ""))
        if slug == "reverse-decompile" and command and "--no-ai" not in command:
            raise RuntimePolicyError("The reverse-decompile command must include --no-ai.")

        return {
            "version": POLICY_VERSION,
            "runtime_class": runtime_class,
            "runtime_provider": "docker" if runtime_class in {"container", "tool_job"} else runtime_class,
            "template_id": str(_value(template, "id", "")),
            "template_slug": slug,
            "sandbox_type": str(_value(template, "sandbox_type", "interactive")),
            "docker_image": image,
            "default_command": command,
            "interface_modes": interface_modes,
            "network_mode": effective.network_mode,
            "full_internet_enabled": effective.full_internet_enabled,
            "vcpu": tier["vcpu"],
            "ram_gb": tier["ram_gb"],
            "disk_gb": tier["disk_gb"],
            "cost_hr": str(tier["cost_hr"]),
            "currency": "GBP",
            "min_billable_sec": effective.min_billable_minutes * 60,
            "max_timeout_sec": effective.max_timeout_hr * 3600,
            "working_dir": working_dir,
            "input_mount_path": input_mount,
            "output_mount_path": output_mount,
            "artifact_paths": artifact_paths,
            "input_required": bool(_value(template, "input_required", False)),
            "max_upload_bytes": max(1, int(_value(template, "max_upload_mb", 50))) * 1024 * 1024,
            "allow_root": bool(_value(template, "allow_root", False)),
            "read_only_root": bool(_value(template, "read_only_root", True)),
            "run_as_user": str(_value(template, "run_as_user", "") or ""),
            "pids_limit": max(32, min(4096, int(_value(template, "pids_limit", 256) or 256))),
            "security": {
                "no_new_privileges": True,
                "cap_drop": ["ALL"],
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
