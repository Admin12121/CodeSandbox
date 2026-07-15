from __future__ import annotations

import hashlib
import json
import posixpath
import os
import re
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP

from itsdangerous import URLSafeTimedSerializer

from codesandbox.config import get_settings
from codesandbox.shared.storage import (
    delete_private_object,
    upload_private_filestorage,
)

from . import repository
from .image_refs import normalize_image_reference
from .runtime.policy import (
    EffectivePlan,
    PolicyBuilder,
    RuntimePolicyError,
    normalize_network_mode,
    parse_runtime_config,
    resolve_effective_plan,
    validate_command_args,
)
from .runtime.scheduler import get_runtime_driver
from .runtime.drivers.base import UnsupportedRuntimeError
from .runtime.artifacts import safe_artifact_name
from .runtime.metrics import runtime_seconds
from .ui_workflow import (
    UI_WORKFLOW_MODES,
    parse_ui_workflow_graph,
    ui_workflow_node_by_id,
    ui_workflow_node_ui_modes,
    ui_workflow_outgoing_edges,
    ui_workflow_start_node,
    validate_ui_workflow_graph,
)

_WORKER_CALLBACK_SALT = "sandbox.worker-callback"

_TEST_EVIDENCE_EVENT = "test.evidence"
_RUNTIME_EVIDENCE_EVENT = "runtime.evidence"
_SAFE_TEST_REQUIREMENT_RE = re.compile(r"^[^\x00\r\n]{1,240}$")
_BILLING_RESERVE_SECONDS = max(
    30, int(os.environ.get("SANDBOX_BILLING_RESERVE_SECONDS", "60"))
)
_LOW_BALANCE_WARNING_SECONDS = max(
    _BILLING_RESERVE_SECONDS,
    int(os.environ.get("SANDBOX_LOW_BALANCE_WARNING_SECONDS", "300")),
)


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:80] or "template"


def _parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _money(value) -> Decimal:
    try:
        return max(Decimal("0"), Decimal(str(value or "0"))).quantize(
            Decimal("0.0001"), rounding=ROUND_UP
        )
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.0000")


def _minimum_start_amount(effective_plan: EffectivePlan, workspace_type: str) -> Decimal:
    tier = effective_plan.tier(workspace_type)
    rate = _money(tier.get("cost_hr"))
    if rate <= 0:
        return Decimal("0.0000")
    seconds = max(
        _BILLING_RESERVE_SECONDS,
        int(effective_plan.min_billable_minutes or 0) * 60,
    )
    return _money(rate * Decimal(seconds) / Decimal(3600))


def _billing_account_for_workspace(
    workspace_type: str,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
) -> tuple[str, str] | None:
    if workspace_type == "org" and org_id:
        return "org", str(org_id)
    if workspace_type in {"personal", "user"} and user_id:
        return "user", str(user_id)
    return None


def _ensure_start_balance(
    effective_plan: EffectivePlan,
    workspace_type: str,
    *,
    user_id: str | None = None,
    org_id: str | None = None,
) -> str | None:
    required = _minimum_start_amount(effective_plan, workspace_type)
    if required <= 0:
        return None
    account = _billing_account_for_workspace(
        workspace_type, user_id=user_id, org_id=org_id
    )
    if account is None:
        return "Billing account is missing."
    balance = repository.get_or_create_balance(*account)
    available = max(Decimal("0"), repository.available_balance(balance))
    if available < required:
        return (
            "Insufficient balance. At least "
            f"£{required:.4f} is required to start this sandbox."
        )
    return None


def _scheduler_disk_gb(runtime_class: str, disk_limit_gb: int) -> int:
    """Capacity reservation used by the worker scheduler.

    Container workspace quotas are sparse limits, not preallocated disks. A
    30 GB plan should not consume 30 GB of scheduler capacity before the user
    writes any data. VM-style runtimes still reserve their full disk image.
    """
    disk_limit = max(1, int(disk_limit_gb or 1))
    if runtime_class in {"container", "tool_job"}:
        configured = max(
            1,
            int(os.environ.get("SANDBOX_CONTAINER_DISK_RESERVATION_GB", "1")),
        )
        return min(disk_limit, configured)
    return disk_limit


def _sandbox_username_for_user_id(user_id: str | None) -> str:
    """Return a safe display/login name without exposing the user's email."""
    from codesandbox.features.identity.models import User

    user = User.objects.filter(id=user_id).first() if user_id else None
    raw = (user.name if user else "") or ((user.email.split("@", 1)[0]) if user else "") or "student"
    value = re.sub(r"[^a-z0-9_-]+", "_", raw.strip().lower()).strip("_-")
    if not value or not value[0].isalpha():
        value = "user_" + value
    return value[:31] or "student"


_RUNTIME_EXECUTION_CONFIG_KEYS = (
    "image_pull_policy",
    "workspace_enabled",
    "entrypoint",
    "environment",
    "container_start_user",
    "terminal_user",
    "allow_sudo",
    "exposed_ports",
    "driver",
    "required_args",
    "forbidden_args",
    "allowed_file_types",
    "max_input_size_mb",
    "success_condition",
    "test_config",
    "ui",
    "workflow",
    "stage_graph_json",
)


def _runtime_execution_config(config: dict) -> dict:
    """Return only configuration that can change execution or test results.

    Notes and unrelated files in the Config IDE do not force a retest, but any
    runtime, driver, environment, security-adjacent UI, or validation change
    does. This prevents a published template from being tested with one policy
    and then silently executing another.
    """
    source = config if isinstance(config, dict) else {}
    return {key: source.get(key) for key in _RUNTIME_EXECUTION_CONFIG_KEYS}


def _runtime_fields_changed(
    existing_t,
    values: dict,
    existing_runtime_config: dict,
    new_runtime_config: dict,
) -> bool:
    if any(getattr(existing_t, key, None) != value for key, value in values.items()):
        return True
    return _runtime_execution_config(existing_runtime_config) != _runtime_execution_config(
        new_runtime_config
    )



# ── SandboxTemplate ───────────────────────────────────────────────────────────

SANDBOX_TYPES = ("interactive", "malware", "reverse_engineering", "android", "ctf")
RUNTIME_CLASSES = (
    "container",
    "tool_job",
    "microvm",
    "firecracker_microvm",
    "fullvm",
    "qemu_vm",
    "android_emulator",
)
UI_MODES = ("terminal_only", "lab_ui", "background_run", "desktop_gui", "android_ui")
UI_MODE_LABELS = {
    "terminal_only": "Terminal Only",
    "lab_ui": "Lab UI",
    "background_run": "Background Run",
    "desktop_gui": "Desktop GUI",
    "android_ui": "Android UI",
    "custom_page": "Custom Page",
}
UI_MODE_ALIASES = {
    "terminal": "terminal_only",
    "editor": "lab_ui",
    "full_ui": "lab_ui",
    "background": "background_run",
    "gui": "desktop_gui",
}
# Backwards-compatible export name used by existing admin page code.
INTERFACE_MODES = UI_MODES
NETWORK_MODES = (
    "disabled",
    "restricted",
    "full_internet",
    "isolated",
    "fake_internet",
    "controlled_proxy",
    "allowlist",
)
TEMPLATE_STATUSES = ("active", "maintenance", "disabled")


def normalize_ui_mode(value: object, default: str = "terminal_only") -> str:
    mode = str(value or "").strip().lower()
    mode = UI_MODE_ALIASES.get(mode, mode)
    return mode if mode in UI_MODES else default


def normalize_ui_modes(value: object, default: tuple[str, ...] = ("terminal_only",)) -> list[str]:
    if value is None or value == "":
        raw_values = list(default)
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw = str(value)
        try:
            decoded = json.loads(raw)
            raw_values = decoded if isinstance(decoded, list) else raw.replace(",", "\n").splitlines()
        except (TypeError, ValueError):
            raw_values = raw.replace(",", "\n").splitlines()
    modes = []
    for item in raw_values:
        mode = normalize_ui_mode(item, default="")
        if mode and mode not in modes:
            modes.append(mode)
    return modes or list(default)


def _runtime_default_ui_modes(runtime_class: str) -> list[str]:
    runtime_class = str(runtime_class or "container")
    if runtime_class == "tool_job":
        return ["background_run"]
    if runtime_class in {"fullvm", "qemu_vm"}:
        return ["desktop_gui", "background_run", "terminal_only"]
    if runtime_class == "android_emulator":
        return ["android_ui"]
    return ["terminal_only", "lab_ui", "background_run"]


def _tfield(template_or_dict, key, default=None):
    if isinstance(template_or_dict, dict):
        return template_or_dict.get(key, default)
    return getattr(template_or_dict, key, default)


def template_interface_behavior(template_or_dict) -> str:
    value = str(_tfield(template_or_dict, "interface_behavior") or "single")
    return value if value in ("single", "workflow") else "single"


def template_ui_workflow_graph(template_or_dict) -> dict:
    return parse_ui_workflow_graph(_tfield(template_or_dict, "ui_workflow_json"))


def template_allowed_ui_modes(template_or_dict) -> list[str]:
    runtime_class = _tfield(template_or_dict, "runtime_class")
    if template_interface_behavior(template_or_dict) == "workflow":
        modes = ui_workflow_node_ui_modes(template_ui_workflow_graph(template_or_dict))
        if modes:
            return modes
        # No nodes configured yet — fall back to the runtime's usual default
        # so the template still behaves sanely before the graph is built.
    explicit = _tfield(template_or_dict, "allowed_ui_modes")
    legacy = _tfield(template_or_dict, "interface_mode")
    return normalize_ui_modes(explicit or legacy, tuple(_runtime_default_ui_modes(str(runtime_class or "container"))[:1]))


def template_default_ui_mode(template_or_dict) -> str:
    if template_interface_behavior(template_or_dict) == "workflow":
        # Workflow-mode nodes aren't restricted to Single Mode's 5
        # runtime-backed UI_MODES (custom_page is workflow-only), so this
        # deliberately does NOT go through normalize_ui_mode.
        start = ui_workflow_start_node(template_ui_workflow_graph(template_or_dict))
        if start and start.get("ui_mode") in UI_WORKFLOW_MODES:
            return start["ui_mode"]
    allowed = template_allowed_ui_modes(template_or_dict)
    configured = getattr(template_or_dict, "default_ui_mode", None)
    if isinstance(template_or_dict, dict):
        configured = template_or_dict.get("default_ui_mode")
    default = normalize_ui_mode(configured, allowed[0])
    return default if default in allowed else allowed[0]


def _ui_mode_csv(modes: list[str]) -> str:
    return ",".join(modes)


def _ui_mode_json(modes: list[str]) -> str:
    return json.dumps(modes, separators=(",", ":"))


def _ui_feature_config(runtime_config: dict, key: str) -> dict:
    ui = runtime_config.get("ui") if isinstance(runtime_config.get("ui"), dict) else {}
    value = ui.get(key) if isinstance(ui, dict) else None
    return value if isinstance(value, dict) else {}


def validate_ui_mode_config(
    *,
    runtime_class: str,
    allowed_ui_modes: list[str],
    default_ui_mode: str,
    default_command: str,
    runtime_config: dict,
    require_publish_ready: bool = False,
) -> str | None:
    if default_ui_mode not in allowed_ui_modes:
        return "Default UI mode must be one of the allowed UI modes."

    if runtime_class in {"container", "tool_job"} and any(
        mode in allowed_ui_modes for mode in ("desktop_gui", "android_ui")
    ):
        return (
            "The current Docker worker supports terminal, lab, and background modes only; "
            "Desktop GUI and Android UI require a dedicated VM/emulator runtime driver."
        )

    if "android_ui" in allowed_ui_modes and runtime_class != "android_emulator":
        return "Android UI requires the android_emulator runtime class."
    if runtime_class == "android_emulator" and allowed_ui_modes != ["android_ui"]:
        return "Android emulator templates must use Android UI only."

    terminal_capable = runtime_class in {"container", "microvm", "firecracker_microvm", "fullvm", "qemu_vm"}
    if any(mode in allowed_ui_modes for mode in ("terminal_only", "lab_ui")) and not terminal_capable:
        return "Terminal Only and Lab UI require a runtime with shell/terminal support."

    if "lab_ui" in allowed_ui_modes and runtime_class == "tool_job":
        return "Lab UI requires filesystem, editor, and terminal support; use container/microvm/fullvm instead of tool_job."

    if "background_run" in allowed_ui_modes:
        bg = _ui_feature_config(runtime_config, "background_run")
        success_condition = str(bg.get("success_condition") or runtime_config.get("success_condition") or "").strip()
        test_config = runtime_config.get("test_config") if isinstance(runtime_config.get("test_config"), dict) else {}
        explicit_requirements = test_config.get("requirements") or []
        has_explicit_requirements = isinstance(explicit_requirements, list) and any(
            str(value or "").strip() for value in explicit_requirements
        )
        if require_publish_ready and not default_command.strip():
            return "Background Run requires a command before publishing."
        if require_publish_ready and not success_condition and not has_explicit_requirements:
            return "Background Run requires test_config.requirements or a success_condition in runtime.json before publishing."

    if "desktop_gui" in allowed_ui_modes:
        desktop = _ui_feature_config(runtime_config, "desktop_gui")
        has_gui = any(
            str(desktop.get(key) or runtime_config.get(key) or "").strip()
            for key in ("gui_url", "novnc_url", "gui_port", "internal_port")
        )
        if require_publish_ready and not has_gui:
            return "Desktop GUI requires internal_port (or gui_url/novnc_url/gui_port) in runtime.json before publishing."

    if "android_ui" in allowed_ui_modes:
        android = _ui_feature_config(runtime_config, "android_ui")
        has_android = any(str(android.get(key) or runtime_config.get(key) or "").strip() for key in ("emulator_target", "adb_serial", "device_url", "screen_url"))
        if require_publish_ready and not has_android:
            return "Android UI requires Android emulator config in runtime.json before publishing."

    return None


def make_worker_callback_token(job_id: str, instance_id: str, action: str) -> str:
    """Create a short-lived bearer token scoped to one worker job."""
    return URLSafeTimedSerializer(
        get_settings().secret_key,
        salt=_WORKER_CALLBACK_SALT,
    ).dumps({
        "job_id": job_id,
        "instance_id": instance_id,
        "action": action,
    })


def get_platform_templates(
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict], int]:
    templates, total = repository.list_templates(search=search, status=status, page=page, page_size=page_size)
    return [_template_dict(t) for t in templates], total


def get_template_detail(template_id: str) -> dict | None:
    t = repository.get_template(template_id)
    return _template_dict(t) if t else None


def get_hub_templates() -> list[dict]:
    templates, _ = repository.list_templates(status="active", page=1, page_size=200)
    return [_template_dict(t) for t in templates]


def get_hub_template_by_slug(slug: str) -> dict | None:
    t = repository.get_template_by_slug(slug)
    if t is None or t.status != "active":
        return None
    return _template_dict(t)


def get_hub_plans() -> list[dict]:
    return [p for p in get_platform_plans() if p["is_active"]]


# ── SandboxInstance ───────────────────────────────────────────────────────────

def _instance_billing_snapshot(inst) -> dict:
    rate = _money(inst.cost_hr_snapshot)
    now = datetime.now(timezone.utc)
    if inst.started_at and inst.status in {"provisioning", "running", "stopping", "cleanup"}:
        elapsed = runtime_seconds(inst.started_at, now)
    else:
        elapsed = int(inst.total_runtime_sec or 0)
    billable_seconds = max(elapsed, int(inst.min_billable_sec or 0)) if inst.started_at else 0
    estimated = _money(rate * Decimal(billable_seconds) / Decimal(3600))
    if inst.status in {"stopped", "failed", "expired", "killed"}:
        estimated = _money(inst.charged_amount)

    available = None
    remaining_seconds = None
    low_balance = False
    if inst.billing_entity in {"user", "org"}:
        entity_id = (
            str(inst.billed_org_id)
            if inst.billing_entity == "org" and inst.billed_org_id
            else str(inst.billed_user_id or "")
        )
        if entity_id:
            balance = repository.get_or_create_balance(inst.billing_entity, entity_id)
            available = max(Decimal("0"), repository.available_balance(balance))
            if rate > 0:
                remaining_seconds = max(0, int((available / rate * Decimal(3600))))
                low_balance = (
                    inst.status in {"provisioning", "running"}
                    and remaining_seconds <= _LOW_BALANCE_WARNING_SECONDS
                )

    return {
        "rate_per_hour": str(rate),
        "rate_per_hour_display": f"{rate:.4f}",
        "estimated_cost": str(estimated),
        "estimated_cost_display": f"{estimated:.4f}",
        "reserved_amount": str(_money(inst.billing_reserved_amount)),
        "reserved_amount_display": f"{_money(inst.billing_reserved_amount):.4f}",
        "available_balance": str(available) if available is not None else None,
        "available_balance_display": f"{available:.4f}" if available is not None else None,
        "remaining_seconds": remaining_seconds,
        "low_balance": low_balance,
        "runtime_seconds": elapsed,
        "billable_seconds": billable_seconds,
    }


def _idle_instance_start_state(inst, template) -> tuple[bool, str | None]:
    if inst.status != "idle":
        return False, None
    if inst.workspace_type == "test":
        return True, None
    if template is None or template.status != "active":
        return False, "Template is not active."
    plan = repository.get_plan(str(inst.plan_id or ""))
    template_plan = repository.get_template_plan(str(template.id), str(inst.plan_id or ""))
    if plan is None or not plan.is_active:
        return False, "Plan is not active."
    if template_plan is not None and not template_plan.is_enabled:
        return False, "This plan is disabled for the template."
    try:
        effective = resolve_effective_plan(template, plan, template_plan)
    except RuntimePolicyError as exc:
        return False, str(exc)
    error = _ensure_start_balance(
        effective,
        inst.workspace_type or "personal",
        user_id=str(inst.billed_user_id or inst.workspace_user_id or "") or None,
        org_id=str(inst.billed_org_id or inst.workspace_org_id or "") or None,
    )
    return error is None, error

def _restart_instance_state(inst, template) -> tuple[bool, str | None]:
    """Whether a completed customer instance may be started again as a fresh run.

    A restart creates a new SandboxInstance row. This deliberately preserves the
    old run's immutable billing/audit history and avoids reusing a deleted Docker
    volume or an already-charged billing idempotency key.
    """
    if inst.status not in {"stopped", "failed", "expired", "killed"}:
        return False, None
    if inst.workspace_type == "test":
        return False, None
    if template is None or template.status != "active":
        return False, "Template is not active."
    if getattr(inst, "allocation_id", None):
        return False, "Start a new session from the organization Private tab."
    if bool(getattr(template, "input_required", False)):
        return False, "This template requires a new input upload. Start it again from the Hub."
    plan = repository.get_plan(str(inst.plan_id or ""))
    mapping = repository.get_template_plan(str(template.id), str(inst.plan_id or ""))
    if plan is None or not plan.is_active:
        return False, "Plan is not active."
    if mapping is not None and not mapping.is_enabled:
        return False, "This plan is disabled for the template."
    try:
        effective = resolve_effective_plan(template, plan, mapping)
    except RuntimePolicyError as exc:
        return False, str(exc)
    error = _ensure_start_balance(
        effective,
        inst.workspace_type or "personal",
        user_id=str(inst.billed_user_id or inst.workspace_user_id or "") or None,
        org_id=str(inst.billed_org_id or inst.workspace_org_id or "") or None,
    )
    return error is None, error


def _instance_dict(inst, template_cache: dict | None = None) -> dict:
    tid = str(inst.template_id)
    t = (template_cache or {}).get(tid) or repository.get_template(tid)
    allowed_ui_modes = template_allowed_ui_modes(t) if t else ["terminal_only"]
    default_ui_mode = template_default_ui_mode(t) if t else "terminal_only"
    can_start, start_error = _idle_instance_start_state(inst, t)
    can_restart, restart_error = _restart_instance_state(inst, t)
    return {
        "id": str(inst.id),
        "template_id": tid,
        "template_name": t.name if t else "Unknown",
        "template_slug": t.slug if t else "",
        "template_icon": t.icon_path or "" if t else "",
        "runtime_class": t.runtime_class if t else "",
        "allowed_ui_modes": allowed_ui_modes,
        "default_ui_mode": default_ui_mode,
        "plan_id": inst.plan_id,
        "workspace_type": inst.workspace_type,
        "user_config": inst.user_config or "",
        "workspace_user_id": str(inst.workspace_user_id) if inst.workspace_user_id else None,
        "workspace_org_id": str(inst.workspace_org_id) if inst.workspace_org_id else None,
        "assigned_to_user_id": str(inst.assigned_to_user_id) if inst.assigned_to_user_id else None,
        "status": inst.status,
        "billing_entity": inst.billing_entity,
        "runtime_provider": inst.runtime_provider,
        "runtime_id": inst.runtime_id,
        "runtime_node_id": inst.runtime_node_id,
        "workspace_volume_id": inst.workspace_volume_id,
        "allocated_vcpu": inst.allocated_vcpu,
        "allocated_ram_gb": inst.allocated_ram_gb,
        "allocated_disk_gb": inst.allocated_disk_gb,
        "effective_network_mode": inst.effective_network_mode,
        "cost_hr_snapshot": str(inst.cost_hr_snapshot or "0"),
        "billing_currency": inst.billing_currency or "GBP",
        "billing_status": inst.billing_status or "unbilled",
        "charged_amount": str(inst.charged_amount or "0"),
        "total_runtime_sec": int(inst.total_runtime_sec or 0),
        "last_heartbeat_at": inst.last_heartbeat_at,
        "exit_code": inst.exit_code,
        "exit_reason": inst.exit_reason or "",
        "created_at": inst.created_at,
        "started_at": inst.started_at,
        "stopped_at": inst.stopped_at,
        "deleted_at": getattr(inst, "deleted_at", None),
        "can_start": can_start,
        "start_error": start_error or "",
        "can_restart": can_restart,
        "restart_error": restart_error or "",
        "can_open": inst.status in {"provisioning", "running", "stopping", "cleanup"},
        "can_stop": inst.status in {"provisioning", "running"},
        "can_delete": inst.status in {"idle", "stopped", "failed", "expired", "killed"},
        "billing": _instance_billing_snapshot(inst),
    }


def _template_cache_for(items) -> dict:
    """Build a {template_id: SandboxTemplate} cache to avoid N+1 queries."""
    if not items:
        return {}
    ids = list({str(i.template_id) for i in items})
    return {str(t.id): t for t in repository.get_templates_by_ids(ids)}


def create_personal_instance(
    user_id: str,
    template_slug: str,
    plan_id: str,
) -> tuple[dict | None, str | None]:
    from codesandbox.features.identity.models import User

    actor = User.objects.filter(id=user_id).first()
    if actor is None or actor.status != "active":
        return None, "Authenticated user is not active."
    t = repository.get_template_by_slug(template_slug)
    if not t or t.status != "active":
        return None, "Template not found or inactive."
    effective_plan, plan_error = get_effective_plan(str(t.id), plan_id)
    if plan_error or effective_plan is None:
        return None, plan_error
    balance_error = _ensure_start_balance(
        effective_plan,
        "personal",
        user_id=user_id,
    )
    if balance_error:
        return None, balance_error
    inst = repository.create_instance(
        template_id=str(t.id),
        plan_id=plan_id,
        workspace_type="personal",
        workspace_user_id=user_id,
        created_by_user_id=user_id,
        billing_entity="user",
        billed_user_id=user_id,
    )
    return _instance_dict(inst), None


def create_org_instance(
    org_id: str,
    creator_user_id: str,
    template_slug: str,
    plan_id: str,
    assigned_to_user_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Deprecated: org runtimes must originate from prepared allocations.

    Keeping this explicit failure prevents old routes or extensions from
    bypassing the organization approval/guardrail workflow and charging the
    shared balance immediately.
    """
    return None, (
        "Direct organization instance creation is disabled. "
        "Prepare an allocation from the organization Public catalog instead."
    )



def _allocation_dict(row, *, viewer_user_id: str | None = None, manager: bool = False) -> dict:
    template = repository.get_template(str(row.template_id))
    plan = repository.get_plan(str(row.plan_id))
    live = repository.find_live_instance_for_allocation(str(row.id))
    start_count = (
        repository.count_allocation_starts_by_user(str(row.id), viewer_user_id)
        if viewer_user_id
        else 0
    )
    limit = int(row.max_starts_per_member or 0)
    assigned = str(row.assigned_to_user_id) if row.assigned_to_user_id else None
    can_start_scope = bool(
        viewer_user_id
        and (
            row.access_scope == "pool"
            or assigned == str(viewer_user_id)
        )
    )
    can_start = bool(
        row.status == "active"
        and template is not None
        and template.status == "active"
        and plan is not None
        and plan.is_active
        and live is None
        and can_start_scope
        and (limit <= 0 or start_count < limit)
    )
    # A shared pool allocation may be visible to every member, but the live
    # instance belongs only to the member who claimed it. Do not leak an
    # instance ID or an Open link to other members; managers may still inspect
    # it for support/administration.
    live_visible = bool(
        live is not None
        and (
            manager
            or str(live.assigned_to_user_id or "") == str(viewer_user_id or "")
        )
    )
    return {
        "id": str(row.id),
        "org_id": str(row.org_id),
        "template_id": str(row.template_id),
        "template_name": template.name if template else "Unknown template",
        "template_slug": template.slug if template else "",
        "template_icon": (template.icon_path or "") if template else "",
        "input_required": bool(template.input_required) if template else False,
        "max_upload_mb": int(template.max_upload_mb or 0) if template else 0,
        "plan_id": str(row.plan_id),
        "plan_name": plan.name if plan else str(row.plan_id),
        "access_scope": row.access_scope,
        "assigned_to_user_id": assigned,
        "max_session_minutes": int(row.max_session_minutes or 0),
        "max_starts_per_member": limit,
        "member_start_count": start_count,
        "status": row.status,
        "live_instance": _instance_dict(live) if live_visible else None,
        "in_use": live is not None or row.status == "in_use",
        "can_start": can_start,
        "can_manage": manager,
        "created_at": row.created_at,
    }


def create_org_allocations(
    *,
    org_id: str,
    creator_user_id: str,
    template_slug: str,
    plan_id: str,
    access_scope: str = "pool",
    assigned_to_user_id: str | None = None,
    quantity: int = 1,
    max_session_minutes: int | None = None,
    max_starts_per_member: int | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Prepare org allocations without starting or charging any runtime."""
    from codesandbox.features.organizations import repository as org_repo
    from codesandbox.features.identity.models import User

    actor = User.objects.filter(id=creator_user_id).first()
    org = org_repo.get_organization(org_id)
    if actor is None or actor.status != "active":
        return None, "Authenticated user is not active."
    if org is None or org.status != "active":
        return None, "Organization is not active."
    if org_repo.get_member(org_id, creator_user_id) is None:
        return None, "You are not a member of this organization."
    perms = set(org_repo.get_member_permissions(org_id, creator_user_id))
    if not org_repo.is_org_owner(org_id, creator_user_id) and "sandbox.allocations.prepare" not in perms:
        return None, "You do not have permission to prepare organization sandboxes."

    access_scope = str(access_scope or "pool").strip().lower()
    if access_scope not in {"pool", "private"}:
        return None, "Invalid allocation scope."
    if access_scope == "private":
        if not assigned_to_user_id or org_repo.get_member(org_id, assigned_to_user_id) is None:
            return None, "A private allocation must be assigned to an organization member."
    else:
        assigned_to_user_id = None

    quantity = max(1, min(int(quantity or 1), 50))
    max_session_minutes = max(1, min(int(max_session_minutes or 120), 72 * 60))
    max_starts_per_member = max(1, min(int(max_starts_per_member or 1), 1000))

    template = repository.get_template_by_slug(template_slug)
    if template is None or template.status != "active":
        return None, "Template not found or inactive."
    _, plan_error = get_effective_plan(str(template.id), plan_id)
    if plan_error:
        return None, plan_error

    rows = []
    for _ in range(quantity):
        row = repository.create_org_allocation(
            org_id=org_id,
            template_id=str(template.id),
            plan_id=plan_id,
            access_scope=access_scope,
            assigned_to_user_id=assigned_to_user_id,
            max_session_minutes=max_session_minutes,
            max_starts_per_member=max_starts_per_member,
            created_by_user_id=creator_user_id,
        )
        rows.append(_allocation_dict(row, viewer_user_id=creator_user_id, manager=True))
    return rows, None


def get_org_allocations_for_user(org_id: str, user_id: str) -> list[dict]:
    from codesandbox.features.organizations import repository as org_repo
    if org_repo.get_member(org_id, user_id) is None:
        return []
    perms = set(org_repo.get_member_permissions(org_id, user_id))
    manager = org_repo.is_org_owner(org_id, user_id) or bool(
        {"sandbox.allocations.manage", "sandbox.allocations.view_all"} & perms
    )
    rows = (
        repository.list_org_allocations(org_id)
        if manager
        else repository.list_allocations_for_member(org_id, user_id)
    )
    return [_allocation_dict(row, viewer_user_id=user_id, manager=manager) for row in rows]


def group_org_allocations_for_display(allocations: list[dict]) -> list[dict]:
    """Collapse identical prepared allocation slots into one UI card.

    Creating a shared pool with quantity=5 stores five allocation rows because
    each row is an independently claimable runtime slot. Showing those rows
    one-for-one makes the Hub look duplicated, so the UI receives a grouped
    view while start/archive actions still target the underlying slot IDs.
    """
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for allocation in allocations:
        status_bucket = "disabled" if allocation.get("status") == "disabled" else "available"
        key = (
            str(allocation.get("template_id") or ""),
            str(allocation.get("plan_id") or ""),
            str(allocation.get("access_scope") or ""),
            str(allocation.get("assigned_to_user_id") or ""),
            int(allocation.get("max_session_minutes") or 0),
            int(allocation.get("max_starts_per_member") or 0),
            bool(allocation.get("input_required")),
            status_bucket,
        )
        group = groups.get(key)
        if group is None:
            group = dict(allocation)
            group.update(
                group_id=f"allocation-group-{len(order) + 1}",
                allocation_ids=[],
                archive_allocation_ids=[],
                live_instances=[],
                quantity=0,
                active_count=0,
                in_use_count=0,
                unavailable_count=0,
                total_member_start_count=0,
                total_max_starts_per_member=0,
                start_allocation_id=None,
                can_archive=False,
            )
            groups[key] = group
            order.append(key)

        allocation_id = str(allocation.get("id") or "")
        group["allocation_ids"].append(allocation_id)
        group["quantity"] += 1
        group["total_member_start_count"] += int(allocation.get("member_start_count") or 0)
        group["total_max_starts_per_member"] += int(allocation.get("max_starts_per_member") or 0)

        if allocation.get("live_instance"):
            group["live_instances"].append(allocation["live_instance"])
            if not group.get("live_instance"):
                group["live_instance"] = allocation["live_instance"]

        if allocation.get("can_start") and not group.get("start_allocation_id"):
            group["start_allocation_id"] = allocation_id
            group["id"] = allocation_id
            group["can_start"] = True

        if allocation.get("in_use") or allocation.get("status") == "in_use":
            group["in_use_count"] += 1
        elif allocation.get("status") == "active":
            group["active_count"] += 1
        elif allocation.get("status") == "disabled":
            group["disabled_count"] = int(group.get("disabled_count") or 0) + 1
        else:
            group["unavailable_count"] += 1

        if allocation.get("can_manage") and not allocation.get("in_use") and not allocation.get("live_instance"):
            group["archive_allocation_ids"].append(allocation_id)
            group["can_archive"] = True

        group["can_manage"] = bool(group.get("can_manage") or allocation.get("can_manage"))

    result = [groups[key] for key in order]
    for group in result:
        if group["active_count"]:
            group["status"] = "active"
        elif group["in_use_count"]:
            group["status"] = "in_use"
        elif group.get("disabled_count"):
            group["status"] = "disabled"
        group["can_start"] = bool(group.get("start_allocation_id"))
        if group["quantity"] > 1:
            group["member_start_count"] = group["total_member_start_count"]
            group["max_starts_per_member"] = group["total_max_starts_per_member"]
    return result


def _can_manage_org_allocations(org_id: str, user_id: str) -> bool:
    from codesandbox.features.organizations import repository as org_repo

    if org_repo.get_member(org_id, user_id) is None:
        return False
    perms = set(org_repo.get_member_permissions(org_id, user_id))
    return org_repo.is_org_owner(org_id, user_id) or "sandbox.allocations.manage" in perms


def _allocation_status_bucket(status: str | None) -> str:
    return "disabled" if str(status or "") == "disabled" else "available"


def _allocation_matches_group(candidate, anchor) -> bool:
    return (
        str(candidate.org_id) == str(anchor.org_id)
        and str(candidate.template_id) == str(anchor.template_id)
        and str(candidate.plan_id) == str(anchor.plan_id)
        and str(candidate.access_scope) == str(anchor.access_scope)
        and str(candidate.assigned_to_user_id or "") == str(anchor.assigned_to_user_id or "")
        and int(candidate.max_session_minutes or 0) == int(anchor.max_session_minutes or 0)
        and int(candidate.max_starts_per_member or 0) == int(anchor.max_starts_per_member or 0)
        and _allocation_status_bucket(candidate.status) == _allocation_status_bucket(anchor.status)
    )


def _allocation_group_rows(anchor):
    return [
        row
        for row in repository.list_org_allocations(str(anchor.org_id))
        if _allocation_matches_group(row, anchor)
    ]


def get_org_allocation_edit_context(
    allocation_id: str,
    org_id: str,
    actor_user_id: str,
) -> tuple[dict | None, str | None]:
    from codesandbox.features.organizations import repository as org_repo
    from codesandbox.features.identity.models import User

    if not _can_manage_org_allocations(org_id, actor_user_id):
        return None, "You do not have permission to manage allocations."
    anchor = repository.get_org_allocation(allocation_id)
    if anchor is None or str(anchor.org_id) != str(org_id) or anchor.status == "archived":
        return None, "Allocation not found."
    rows = _allocation_group_rows(anchor)
    allocations = [
        _allocation_dict(row, viewer_user_id=actor_user_id, manager=True)
        for row in rows
    ]
    groups = group_org_allocations_for_display(allocations)
    group = groups[0] if groups else _allocation_dict(anchor, viewer_user_id=actor_user_id, manager=True)
    template = repository.get_template(str(anchor.template_id))
    plan = repository.get_plan(str(anchor.plan_id))
    members = org_repo.get_members_with_info(org_id)
    return {
        "allocation": group,
        "allocation_ids": [str(row.id) for row in rows],
        "editable_count": sum(1 for row in rows if row.status in {"active", "disabled"}),
        "in_use_count": sum(1 for row in rows if row.status == "in_use"),
        "template": _template_dict(template) if template else None,
        "plan": _plan_dict(plan) if plan else None,
        "org_members": members,
        "assigned_user": (
            User.objects.filter(id=str(anchor.assigned_to_user_id)).first()
            if anchor.assigned_to_user_id
            else None
        ),
    }, None


def update_org_allocation_group(
    allocation_id: str,
    org_id: str,
    actor_user_id: str,
    *,
    access_scope: str,
    assigned_to_user_id: str | None,
    quantity: int,
    max_session_minutes: int,
    max_starts_per_member: int,
    status: str,
) -> tuple[dict | None, str | None]:
    from codesandbox.features.organizations import repository as org_repo

    if not _can_manage_org_allocations(org_id, actor_user_id):
        return None, "You do not have permission to manage allocations."
    anchor = repository.get_org_allocation(allocation_id)
    if anchor is None or str(anchor.org_id) != str(org_id) or anchor.status == "archived":
        return None, "Allocation not found."
    if status not in {"active", "disabled"}:
        return None, "Invalid allocation status."
    access_scope = str(access_scope or "pool").strip().lower()
    if access_scope not in {"pool", "private"}:
        return None, "Invalid allocation scope."
    if access_scope == "private":
        if not assigned_to_user_id or org_repo.get_member(org_id, assigned_to_user_id) is None:
            return None, "A private allocation must be assigned to an organization member."
    else:
        assigned_to_user_id = None

    quantity = max(1, min(int(quantity or 1), 50))
    max_session_minutes = max(1, min(int(max_session_minutes or 120), 72 * 60))
    max_starts_per_member = max(1, min(int(max_starts_per_member or 1), 1000))

    rows = _allocation_group_rows(anchor)
    in_use = [row for row in rows if row.status == "in_use"]
    editable = [row for row in rows if row.status in {"active", "disabled"}]
    if quantity < len(in_use):
        return None, "Quantity cannot be lower than active sessions in this allocation group."

    keep_editable_count = quantity - len(in_use)
    if keep_editable_count < len(editable):
        remove_count = len(editable) - keep_editable_count
        for row in editable[-remove_count:]:
            repository.update_org_allocation(str(row.id), status="archived")
        editable = editable[:-remove_count]

    for row in editable:
        repository.update_org_allocation(
            str(row.id),
            access_scope=access_scope,
            assigned_to_user_id=assigned_to_user_id,
            max_session_minutes=max_session_minutes,
            max_starts_per_member=max_starts_per_member,
            status=status,
        )

    add_count = keep_editable_count - len(editable)
    for _ in range(max(0, add_count)):
        created = repository.create_org_allocation(
            org_id=org_id,
            template_id=str(anchor.template_id),
            plan_id=str(anchor.plan_id),
            access_scope=access_scope,
            assigned_to_user_id=assigned_to_user_id,
            max_session_minutes=max_session_minutes,
            max_starts_per_member=max_starts_per_member,
            created_by_user_id=actor_user_id,
        )
        if status == "disabled":
            repository.update_org_allocation(str(created.id), status="disabled")

    context, error = get_org_allocation_edit_context(allocation_id, org_id, actor_user_id)
    return (context.get("allocation") if context else None), error


def set_org_allocation_group_status(
    allocation_ids: list[str],
    org_id: str,
    actor_user_id: str,
    *,
    status: str,
) -> tuple[int, str | None]:
    if not _can_manage_org_allocations(org_id, actor_user_id):
        return 0, "You do not have permission to manage allocations."
    if status not in {"active", "disabled", "archived"}:
        return 0, "Invalid allocation status."
    changed = 0
    for allocation_id in allocation_ids[:50]:
        row = repository.get_org_allocation(str(allocation_id))
        if row is None or str(row.org_id) != str(org_id):
            continue
        if row.status == "in_use" or repository.find_live_instance_for_allocation(str(row.id)):
            if status in {"disabled", "archived"}:
                continue
        if row.status != "archived":
            repository.update_org_allocation(str(row.id), status=status)
            changed += 1
    return changed, None


def claim_org_allocation(
    allocation_id: str,
    user_id: str,
    *,
    expected_org_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Create one idle runtime row from a prepared allocation.

    Charging still begins only in start_instance(). Keeping the claim and start
    separate lets the route attach a required upload before any worker job exists.
    """
    from codesandbox.features.organizations import repository as org_repo
    from codesandbox.features.identity.models import User

    row = repository.get_org_allocation(allocation_id)
    if row is None or row.status != "active":
        return None, "Allocation is not available."
    org_id = str(row.org_id)
    if expected_org_id and str(expected_org_id) != org_id:
        return None, "Allocation does not belong to the active organization workspace."
    org = org_repo.get_organization(org_id)
    actor = User.objects.filter(id=user_id).first()
    if actor is None or actor.status != "active" or org is None or org.status != "active":
        return None, "Organization or user is not active."
    if org_repo.get_member(org_id, user_id) is None:
        return None, "You are not a member of this organization."

    perms = set(org_repo.get_member_permissions(org_id, user_id))
    is_owner = org_repo.is_org_owner(org_id, user_id)
    if row.access_scope == "private":
        if str(row.assigned_to_user_id or "") != str(user_id):
            return None, "This private sandbox is assigned to another member."
        if not is_owner and "sandbox.instances.use_assigned" not in perms:
            return None, "You do not have permission to use assigned sandboxes."
    elif not is_owner and "sandbox.instances.use_pool" not in perms:
        return None, "You do not have permission to use the organization pool."

    if row.status != "active" or repository.find_live_instance_for_allocation(allocation_id) is not None:
        return None, "This allocation already has an active session or is unavailable."
    max_starts = int(row.max_starts_per_member or 0)
    if (
        max_starts > 0
        and repository.count_allocation_starts_by_user(allocation_id, user_id) >= max_starts
    ):
        return None, "You have reached the start limit for this allocation."

    template = repository.get_template(str(row.template_id))
    if template is None or template.status != "active":
        return None, "Template is not active."
    effective, plan_error = get_effective_plan(str(template.id), str(row.plan_id))
    if plan_error or effective is None:
        return None, plan_error
    balance_error = _ensure_start_balance(effective, "org", org_id=org_id)
    if balance_error:
        return None, balance_error

    inst, claim_error = repository.claim_org_allocation_instance(
        allocation_id=allocation_id,
        user_id=user_id,
        org_id=org_id,
    )
    if claim_error or inst is None:
        return None, claim_error or "Unable to claim this allocation."
    return _instance_dict(inst), None


def archive_org_allocation(
    allocation_id: str, org_id: str, actor_user_id: str
) -> tuple[dict | None, str | None]:
    from codesandbox.features.organizations import repository as org_repo
    row = repository.get_org_allocation(allocation_id)
    if row is None or str(row.org_id) != str(org_id):
        return None, "Allocation not found."
    perms = set(org_repo.get_member_permissions(org_id, actor_user_id))
    if not org_repo.is_org_owner(org_id, actor_user_id) and "sandbox.allocations.manage" not in perms:
        return None, "You do not have permission to manage allocations."
    if row.status == "in_use" or repository.find_live_instance_for_allocation(allocation_id):
        return None, "Stop the active session before archiving this allocation."
    row = repository.update_org_allocation(allocation_id, status="archived")
    return (_allocation_dict(row, viewer_user_id=actor_user_id, manager=True), None) if row else (None, "Allocation not found.")

def get_user_instances(user_id: str) -> list[dict]:
    instances = repository.list_instances_for_user(user_id)
    cache = _template_cache_for(instances)
    return [_instance_dict(i, cache) for i in instances]


def get_org_instances(org_id: str) -> list[dict]:
    instances = repository.list_instances_for_org(org_id)
    cache = _template_cache_for(instances)
    return [_instance_dict(i, cache) for i in instances]


def get_user_assigned_instances(user_id: str, org_id: str) -> list[dict]:
    instances = repository.list_instances_assigned_to_user_in_org(user_id, org_id)
    cache = _template_cache_for(instances)
    return [_instance_dict(i, cache) for i in instances]


def get_active_hub_instance(
    template_id: str,
    plan_id: str,
    *,
    user_id: str,
    org_id: str | None = None,
) -> dict | None:
    """The instance the /hub/<template>/<plan> IDE page should attach to, if any."""
    inst = repository.find_hub_instance(template_id, plan_id, user_id=user_id, org_id=org_id)
    return _instance_dict(inst) if inst else None


def get_instance_live_status(
    instance_id: str, actor_user_id: str | None
) -> tuple[dict | None, str | None]:
    if not can_view_instance(instance_id, actor_user_id):
        return None, "You do not have permission to view this instance."
    inst = repository.get_instance(instance_id)
    if inst is None or getattr(inst, "deleted_at", None) is not None:
        return None, "Instance not found."
    data = _instance_dict(inst)
    return {
        "id": data["id"],
        "status": data["status"],
        "exit_reason": data["exit_reason"],
        "started_at": data["started_at"],
        "stopped_at": data["stopped_at"],
        "allocated_vcpu": data["allocated_vcpu"],
        "allocated_ram_gb": data["allocated_ram_gb"],
        "allocated_disk_gb": data["allocated_disk_gb"],
        "billing": data["billing"],
        "can_start": data["can_start"],
        "start_error": data["start_error"],
        "can_restart": data["can_restart"],
        "restart_error": data["restart_error"],
        "can_open": data["can_open"],
        "can_stop": data["can_stop"],
        "can_delete": data["can_delete"],
    }, None


def archive_instance_for_user(
    instance_id: str, actor_user_id: str | None
) -> tuple[dict | None, str | None]:
    inst = repository.get_instance(instance_id)
    if inst is None or getattr(inst, "deleted_at", None) is not None:
        return None, "Instance not found."
    # An idle row created by a successful allocation claim may need cleanup
    # after upload/start validation fails. Permit the claimant to discard that
    # idle row using the same authorization required to start it. Terminal
    # runs still require the explicit stop/manage permission.
    authorized = (
        can_start_instance(inst, actor_user_id)
        if inst.status == "idle"
        else can_manage_instance(inst, actor_user_id)
    )
    if not authorized:
        return None, "You do not have permission to delete this instance."
    if inst.status not in {"idle", "stopped", "failed", "expired", "killed"}:
        return None, "Stop the instance before deleting it."

    # Delete private object payloads, but keep the relational usage/audit rows.
    for item in repository.list_instance_inputs(instance_id):
        try:
            delete_private_object(item.storage_key)
        except Exception:
            pass
    for item in repository.list_instance_artifacts(instance_id):
        try:
            delete_private_object(item.storage_key)
        except Exception:
            pass
    archived = repository.archive_instance(instance_id)
    if archived is None:
        return None, "Instance not found."
    repository.log_instance_event(
        instance_id, "instance.archived", actor=f"user:{actor_user_id}"
    )
    return _instance_dict(archived), None


def get_live_balance_for_actor(
    user_id: str, org_id: str | None = None
) -> dict:
    entity_type, entity_id = ("org", org_id) if org_id else ("user", user_id)
    balance = repository.get_or_create_balance(entity_type, str(entity_id))
    amount = _money(balance.amount)
    reserved = _money(balance.reserved_amount)
    available = max(Decimal("0"), amount - reserved)
    return {
        "currency": "GBP",
        "amount": str(amount),
        "amount_display": f"{amount:.4f}",
        "reserved_amount": str(reserved),
        "reserved_amount_display": f"{reserved:.4f}",
        "available_amount": str(available),
        "available_amount_display": f"{available:.4f}",
    }


# ── InstanceRequest ───────────────────────────────────────────────────────────

def _request_dict(req, template_cache: dict | None = None) -> dict:
    from codesandbox.features.identity import repository as identity_repo

    tid = str(req.template_id)
    t = (template_cache or {}).get(tid) or repository.get_template(tid)
    requester = identity_repo.find_user_by_id(str(req.requested_by))
    try:
        requested = json.loads(req.requested_config or "{}")
        requested = requested if isinstance(requested, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        requested = {}
    return {
        "id": str(req.id),
        "org_id": str(req.org_id),
        "requested_by": str(req.requested_by),
        "requester_name": requester.name if requester else "Unknown member",
        "requester_email": requester.email if requester else "",
        "template_id": str(req.template_id),
        "template_name": t.name if t else "Unknown",
        "template_slug": t.slug if t else "",
        "plan_id": req.plan_id,
        "note": req.note or "",
        "max_session_minutes": int(requested.get("max_session_minutes") or 120),
        "max_starts": int(requested.get("max_starts") or 1),
        "status": req.status,
        "reviewed_by": str(req.reviewed_by) if req.reviewed_by else None,
        "reviewed_at": req.reviewed_at,
        "review_note": req.review_note or "",
        "instance_id": str(req.instance_id) if req.instance_id else None,
        "allocation_id": str(req.allocation_id) if getattr(req, "allocation_id", None) else None,
        "created_at": req.created_at,
    }


def submit_instance_request(
    org_id: str,
    user_id: str,
    template_slug: str,
    plan_id: str,
    note: str | None = None,
    *,
    max_session_minutes: int = 120,
    max_starts: int = 1,
) -> tuple[dict | None, str | None]:
    from codesandbox.features.organizations import repository as org_repo
    org = org_repo.get_organization(org_id)
    if org is None or org.status != "active":
        return None, "Organization is not active."
    if org_repo.get_member(org_id, user_id) is None:
        return None, "You are not a member of this organization."
    if (
        not org_repo.is_org_owner(org_id, user_id)
        and "sandbox.requests.submit" not in org_repo.get_member_permissions(org_id, user_id)
    ):
        return None, "You do not have permission to request a sandbox."
    t = repository.get_template_by_slug(template_slug)
    if not t or t.status != "active":
        return None, "Template not found or inactive."
    _, plan_error = get_effective_plan(str(t.id), plan_id)
    if plan_error:
        return None, plan_error
    req = repository.create_instance_request(
        org_id=org_id,
        requested_by=user_id,
        template_id=str(t.id),
        plan_id=plan_id,
        note=note,
        requested_config=json.dumps({
            "max_session_minutes": max(1, min(int(max_session_minutes or 120), 72 * 60)),
            "max_starts": max(1, min(int(max_starts or 1), 1000)),
        }),
    )
    return _request_dict(req), None


def review_instance_request(
    request_id: str,
    org_id: str,
    reviewer_id: str,
    action: str,
    review_note: str | None = None,
) -> tuple[dict | None, str | None]:
    from nexorm import transaction
    from codesandbox.features.organizations import repository as org_repo

    if (
        not org_repo.is_org_owner(org_id, reviewer_id)
        and "sandbox.requests.review" not in org_repo.get_member_permissions(org_id, reviewer_id)
    ):
        return None, "You do not have permission to review sandbox requests."
    if action not in ("approved", "denied"):
        return None, "Invalid action."

    with transaction.atomic():
        req = repository.get_instance_request_for_update(request_id)
        if not req or str(req.org_id) != str(org_id):
            return None, "Request not found in this organization."
        if req.status != "pending":
            return None, "Request already reviewed."

        allocation_id = None
        if action == "approved":
            org = org_repo.get_organization(str(req.org_id))
            if org is None or org.status != "active":
                return None, "Organization is not active."
            tmpl = repository.get_template(str(req.template_id))
            if not tmpl or tmpl.status != "active":
                return None, "Template is no longer active; cannot approve."
            try:
                requested = json.loads(req.requested_config or "{}")
                requested = requested if isinstance(requested, dict) else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                requested = {}
            # Revalidate the plan at approval time; availability may have changed
            # since the member submitted the request.
            _, plan_error = get_effective_plan(str(tmpl.id), str(req.plan_id))
            if plan_error:
                return None, plan_error
            allocation = repository.create_org_allocation(
                org_id=str(req.org_id),
                template_id=str(tmpl.id),
                plan_id=str(req.plan_id),
                access_scope="private",
                assigned_to_user_id=str(req.requested_by),
                max_session_minutes=max(1, min(int(requested.get("max_session_minutes") or 120), 72 * 60)),
                max_starts_per_member=max(1, min(int(requested.get("max_starts") or 1), 1000)),
                created_by_user_id=reviewer_id,
            )
            allocation_id = str(allocation.id)

        req.status = action
        req.reviewed_by = reviewer_id
        req.reviewed_at = datetime.now(timezone.utc)
        req.review_note = review_note
        req.allocation_id = allocation_id
        req.instance_id = None
        req.save()
        return _request_dict(req), None


def get_org_requests(org_id: str, status: str | None = None) -> list[dict]:
    reqs = repository.list_requests_for_org(org_id, status)
    cache = _template_cache_for(reqs)
    return [_request_dict(r, cache) for r in reqs]


def get_user_requests_in_org(user_id: str, org_id: str) -> list[dict]:
    reqs = repository.list_requests_by_user_in_org(user_id, org_id)
    cache = _template_cache_for(reqs)
    return [_request_dict(r, cache) for r in reqs]


def _template_dict(t) -> dict:
    allowed_ui_modes = template_allowed_ui_modes(t)
    default_ui_mode = template_default_ui_mode(t)
    return {
        "id": str(t.id),
        "slug": t.slug,
        "name": t.name,
        "description": t.description or "",
        "icon_path": t.icon_path or "",
        "docker_image": t.docker_image,
        "default_command": t.default_command or "",
        "working_dir": t.working_dir or "",
        "input_mount_path": t.input_mount_path or "",
        "output_mount_path": t.output_mount_path or "",
        "artifact_paths": t.artifact_paths or "",
        "input_required": bool(t.input_required),
        "test_input_required": bool(t.input_required or _test_config_for_template(t).get("requires_input")),
        "max_upload_mb": int(t.max_upload_mb or 50),
        "sandbox_type": t.sandbox_type,
        "runtime_class": t.runtime_class,
        "interface_mode": t.interface_mode,
        "allowed_ui_modes": _ui_mode_csv(allowed_ui_modes),
        "allowed_ui_mode_values": allowed_ui_modes,
        "default_ui_mode": default_ui_mode,
        "interface_behavior": template_interface_behavior(t),
        "ui_workflow_json": t.ui_workflow_json or "",
        "ui_workflow": template_ui_workflow_graph(t),
        "network_mode": t.network_mode,
        "allow_root": bool(t.allow_root),
        "read_only_root": bool(t.read_only_root),
        "run_as_user": t.run_as_user or "",
        "pids_limit": int(t.pids_limit or 256),
        "allow_full_internet": bool(t.allow_full_internet),
        "max_timeout_hr": int(t.max_timeout_hr),
        "status": t.status,
        "last_test_status": t.last_test_status or "untested",
        "last_tested_at": t.last_tested_at,
        "last_test_error": t.last_test_error or "",
        "runtime_config": t.runtime_config or "",
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "active_instances": repository.count_active_instances_for_template(str(t.id)),
    }


def save_template(
    template_id: str | None,
    name: str,
    description: str,
    icon_path: str,
    docker_image: str,
    sandbox_type: str,
    runtime_config: str,
    created_by_id: str | None,
    slug: str = "",
    runtime_class: str = "container",
    interface_mode: str = "terminal",
    allowed_ui_modes: str | list[str] | None = None,
    default_ui_mode: str = "terminal_only",
    interface_behavior: str = "single",
    network_mode: str = "disabled",
    allow_root: bool = False,
    max_timeout_hr: int = 2,
    default_command: str = "",
    working_dir: str = "/workspace",
    input_mount_path: str = "",
    output_mount_path: str = "",
    artifact_paths: str | list[str] = "",
    input_required: bool = False,
    max_upload_mb: int = 50,
    read_only_root: bool = True,
    run_as_user: str = "",
    pids_limit: int = 256,
    allow_full_internet: bool = False,
) -> tuple[dict | None, str | None]:
    name = name.strip()
    if not name:
        return None, "Name is required."
    if sandbox_type not in SANDBOX_TYPES:
        return None, "Invalid sandbox type."
    if runtime_class not in RUNTIME_CLASSES:
        return None, "Invalid runtime class."
    docker_image = docker_image.strip()
    if not docker_image:
        return None, "A runtime image or target is required."
    if runtime_class in {"container", "tool_job"}:
        try:
            docker_image = normalize_image_reference(docker_image)
        except ValueError as exc:
            return None, str(exc)
    elif any(char.isspace() for char in docker_image):
        return None, "Runtime image or target must not contain whitespace."
    interface_behavior = interface_behavior if interface_behavior in ("single", "workflow") else "single"

    existing_t = repository.get_template(template_id) if template_id else None
    if template_id and existing_t is None:
        return None, "Template not found."

    if interface_behavior == "workflow" and len(_runtime_default_ui_modes(runtime_class)) <= 1:
        # tool_job and android_emulator each only ever have exactly one
        # possible ui_mode — there's nothing to branch between, so Workflow
        # Mode (multiple UI stages) is meaningless for them.
        return None, (
            f"Workflow Mode requires a runtime with more than one available UI mode — "
            f"{runtime_class} only supports {_runtime_default_ui_modes(runtime_class)[0]}. Use Single Mode instead."
        )

    if interface_behavior == "workflow":
        # The Identity form doesn't submit allowed_ui_modes/default_ui_mode
        # in Workflow Mode — they're derived from whatever graph is already
        # saved (edited separately via the canvas route). A template that
        # hasn't had its workflow configured yet just falls back to
        # whatever this runtime_class's own default mode is (not a hardcoded
        # terminal_only — that's invalid for e.g. tool_job) until the admin
        # builds the graph.
        existing_graph = template_ui_workflow_graph(existing_t) if existing_t else {"nodes": []}
        _modes = ui_workflow_node_ui_modes(existing_graph) or _runtime_default_ui_modes(runtime_class)[:1]
        default_ui_mode = _modes[0]
        graph_start = ui_workflow_start_node(existing_graph)
        if graph_start and graph_start.get("ui_mode") in _modes:
            default_ui_mode = graph_start["ui_mode"]
    else:
        # Single Mode: the template opens in exactly one UI mode — no
        # multi-select, just whatever the admin picked as Default UI Mode.
        default_ui_mode = normalize_ui_mode(default_ui_mode, "terminal_only")
        _modes = [default_ui_mode]
    allowed_ui_modes_json = _ui_mode_json(_modes)
    interface_mode = _ui_mode_csv(_modes)
    network_mode = normalize_network_mode(
        network_mode if network_mode in NETWORK_MODES else "disabled"
    )
    max_timeout_hr = max(1, min(72, int(max_timeout_hr or 2)))
    max_upload_mb = max(1, min(1024, int(max_upload_mb or 50)))
    pids_limit = max(32, min(4096, int(pids_limit or 256)))
    working_dir = working_dir.strip() or "/workspace"
    input_mount_path = input_mount_path.strip()
    output_mount_path = output_mount_path.strip()
    if input_required and not input_mount_path:
        return None, "Require Input needs an input mount path."
    default_command = default_command.strip()
    parsed_runtime_config = parse_runtime_config(runtime_config)
    ui_error = validate_ui_mode_config(
        runtime_class=runtime_class,
        allowed_ui_modes=_modes,
        default_ui_mode=default_ui_mode,
        default_command=default_command,
        runtime_config=parsed_runtime_config,
        require_publish_ready=False,
    )
    if ui_error:
        return None, ui_error
    command_error = validate_command_args(
        default_command,
        [str(a) for a in parsed_runtime_config.get("required_args") or []],
        [str(a) for a in parsed_runtime_config.get("forbidden_args") or []],
    )
    if command_error:
        return None, command_error
    # Input/output mounts are optional. Only configured paths participate in
    # validation; the working directory remains required for every runtime.
    workspace_enabled = bool(parsed_runtime_config.get("workspace_enabled", True))
    configured_paths = []
    if workspace_enabled:
        configured_paths.append(("Working directory", working_dir.strip()))
    elif not working_dir.strip().startswith("/"):
        return None, "Working directory must be an absolute container path."
    if input_mount_path:
        configured_paths.append(("Input mount", input_mount_path))
    if output_mount_path:
        configured_paths.append(("Output mount", output_mount_path))
    for label, path in configured_paths:
        if not path.startswith("/"):
            return None, f"{label} must be an absolute container path."
    for index, (left_label, left_path) in enumerate(configured_paths):
        for right_label, right_path in configured_paths[index + 1:]:
            left = posixpath.normpath(left_path)
            right = posixpath.normpath(right_path)
            if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
                return None, f"{left_label} and {right_label} must be separate, non-overlapping paths."

    if isinstance(artifact_paths, str):
        raw_artifacts = artifact_paths.strip()
        try:
            artifact_values = json.loads(raw_artifacts) if raw_artifacts else []
        except ValueError:
            artifact_values = [line.strip() for line in raw_artifacts.splitlines() if line.strip()]
    else:
        artifact_values = artifact_paths or []
    if not isinstance(artifact_values, list) or any(
        not str(path).strip().startswith("/") for path in artifact_values
    ):
        return None, "Artifact paths must be a JSON list (or one path per line) of absolute container paths."
    artifact_paths_json = json.dumps(
        [str(path).strip() for path in artifact_values], separators=(",", ":")
    )

    runtime_values = dict(
        docker_image=docker_image,
        default_command=default_command or None,
        working_dir=working_dir.strip(),
        input_mount_path=input_mount_path.strip(),
        output_mount_path=output_mount_path.strip(),
        artifact_paths=artifact_paths_json,
        input_required=bool(input_required),
        max_upload_mb=max_upload_mb,
        sandbox_type=sandbox_type,
        runtime_class=runtime_class,
        interface_mode=interface_mode,
        allowed_ui_modes=allowed_ui_modes_json,
        default_ui_mode=default_ui_mode,
        interface_behavior=interface_behavior,
        network_mode=network_mode,
        allow_root=allow_root,
        read_only_root=read_only_root,
        run_as_user=run_as_user.strip() or None,
        pids_limit=pids_limit,
        allow_full_internet=(network_mode == "full_internet"),
        max_timeout_hr=max_timeout_hr,
    )

    if template_id:
        # existing_t was already fetched above (needed to derive Workflow
        # Mode's allowed_ui_modes/default_ui_mode before validation ran).
        slug = existing_t.slug if existing_t else (slug.strip() or _slugify(name))

        update_kwargs = dict(
            name=name, slug=slug, description=description or None,
            icon_path=icon_path or None,
            runtime_config=runtime_config.strip() or None,
            **runtime_values,
        )

        existing_runtime_config = parse_runtime_config(existing_t.runtime_config)
        if _runtime_fields_changed(existing_t, runtime_values, existing_runtime_config, parsed_runtime_config):
            # A Runtime-affecting edit invalidates any prior test pass. If the
            # template was live, take it out of rotation until it's retested.
            update_kwargs["last_test_status"] = "untested"
            update_kwargs["last_tested_at"] = None
            if existing_t.status == "active":
                update_kwargs["status"] = "maintenance"

        t = repository.update_template(template_id, **update_kwargs)
    else:
        slug = slug.strip() or _slugify(name)
        existing = repository.get_template_by_slug(slug)
        if existing:
            return None, f"Slug '{slug}' is already taken."
        t = repository.create_template(
            name=name, slug=slug, description=description or None,
            icon_path=icon_path or None,
            runtime_config=runtime_config.strip() or None,
            created_by_id=created_by_id,
            **runtime_values,
        )
    repository.log_instance_event(
        None,
        "template.runtime_updated",
        actor=f"user:{created_by_id}" if created_by_id else "system",
        detail=json.dumps({
            "runtime_class": runtime_class,
            "allowed_ui_modes": _modes,
            "default_ui_mode": default_ui_mode,
            "interface_behavior": interface_behavior,
            "network_mode": network_mode,
            "allow_full_internet": network_mode == "full_internet",
        }),
        template_id=str(t.id),
    )
    return _template_dict(t), None


def set_template_status(template_id: str, status: str, actor_user_id: str | None = None) -> str | None:
    if status not in TEMPLATE_STATUSES:
        return "Invalid status."
    if status == "active":
        if not get_template_plans_for_hub(template_id):
            return "Cannot activate: no plans available for this template. Enable at least one plan in the Plans tab first."
        t = repository.get_template(template_id)
        if t is None:
            return "Template not found."
        if (t.last_test_status or "untested") != "passed":
            return "Cannot activate: run a successful Test Launch first."
        if template_interface_behavior(t) == "workflow":
            graph_error = validate_ui_workflow_graph(template_ui_workflow_graph(t))
            if graph_error:
                return f"Cannot activate: {graph_error}"
        runtime_config = parse_runtime_config(t.runtime_config)
        ui_error = validate_ui_mode_config(
            runtime_class=t.runtime_class,
            allowed_ui_modes=template_allowed_ui_modes(t),
            default_ui_mode=template_default_ui_mode(t),
            default_command=t.default_command or "",
            runtime_config=runtime_config,
            require_publish_ready=True,
        )
        if ui_error:
            return f"Cannot activate: {ui_error}"
    existing = repository.get_template(template_id)
    previous_status = existing.status if existing else None
    repository.update_template(template_id, status=status)
    # Publish/unpublish is a user-visibility change (Draft/Maintenance ->
    # visible in Hub, or back) — audit logged regardless of direction.
    if status == "active":
        event = "template.published"
    elif previous_status == "active":
        event = "template.unpublished"
    else:
        event = "template.status_changed"
    repository.log_instance_event(
        None,
        event,
        old_status=previous_status,
        new_status=status,
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        template_id=template_id,
    )
    return None


def get_template_test_status(
    template_id: str,
    actor_user_id: str | None = None,
) -> dict | None:
    t = repository.get_template(template_id)
    if t is None:
        return None
    active = repository.find_active_test_instance(
        template_id, actor_user_id=actor_user_id
    )
    active_payload = None
    if active is not None:
        progress = _evaluate_test_progress(active)
        current_node_id = None
        try:
            user_config = json.loads(active.user_config or "{}")
            current_node_id = (user_config.get("_ui_state") or {}).get("current_node_id")
        except (TypeError, ValueError):
            pass
        query = f"?node={current_node_id}" if current_node_id else ""
        active_payload = {
            "instance_id": str(active.id),
            "template_id": str(active.template_id),
            "status": active.status,
            "url": f"/instances/{active.id}{query}",
            "current_node_id": current_node_id,
            **progress,
        }
    return {
        "last_test_status": t.last_test_status or "untested",
        "last_test_error": t.last_test_error or "",
        "active_test": active_payload,
        "requirements": _test_requirements_for_template(t),
    }


def save_template_config(template_id: str, config_json: str, actor_user_id: str | None = None) -> None:
    existing_t = repository.get_template(template_id)
    new_config = config_json.strip() or None
    update_kwargs: dict = {"runtime_config": new_config}
    if existing_t is not None:
        # The Config tab autosaves, but execution-affecting changes must never
        # inherit a Test Launch result from an older policy. Notes and unrelated
        # IDE files may change without taking a live template out of rotation.
        old_cfg = parse_runtime_config(existing_t.runtime_config)
        new_cfg = parse_runtime_config(new_config)
        old_execution_cfg = _runtime_execution_config(old_cfg)
        new_execution_cfg = _runtime_execution_config(new_cfg)
        changed_keys = [
            key for key in _RUNTIME_EXECUTION_CONFIG_KEYS
            if old_execution_cfg.get(key) != new_execution_cfg.get(key)
        ]
        if changed_keys:
            update_kwargs["last_test_status"] = "untested"
            update_kwargs["last_tested_at"] = None
            if existing_t.status == "active":
                update_kwargs["status"] = "maintenance"
            repository.log_instance_event(
                None,
                "template.dangerous_config_changed",
                actor=f"user:{actor_user_id}" if actor_user_id else "system",
                detail=json.dumps({"changed_keys": changed_keys, "forced_retest": True}),
                template_id=template_id,
            )
    repository.update_template(template_id, **update_kwargs)


def save_template_ui_workflow(
    template_id: str, graph: dict, actor_user_id: str | None = None
) -> tuple[dict | None, str | None]:
    """Persists this template's own UI-stage graph (Workflow Mode). Any
    change here alters what UI mode(s) the template opens with and how
    instances transition between them — always dangerous enough to force a
    retest, same tier as an Identity-tab runtime field edit."""
    existing_t = repository.get_template(template_id)
    if existing_t is None:
        return None, "Template not found."
    error = validate_ui_workflow_graph(graph)
    if error:
        return None, error
    node_modes = ui_workflow_node_ui_modes(graph)
    runtime_config = parse_runtime_config(existing_t.runtime_config)
    ui_error = validate_ui_mode_config(
        runtime_class=existing_t.runtime_class,
        allowed_ui_modes=node_modes,
        default_ui_mode=node_modes[0],
        default_command=existing_t.default_command or "",
        runtime_config=runtime_config,
        require_publish_ready=False,
    )
    if ui_error:
        return None, ui_error

    graph_json = json.dumps(graph, separators=(",", ":"))
    update_kwargs: dict = {"ui_workflow_json": graph_json}
    if existing_t.ui_workflow_json != graph_json:
        update_kwargs["last_test_status"] = "untested"
        update_kwargs["last_tested_at"] = None
        if existing_t.status == "active":
            update_kwargs["status"] = "maintenance"
    t = repository.update_template(template_id, **update_kwargs)
    repository.log_instance_event(
        None,
        "template.ui_workflow_updated",
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        detail=json.dumps({
            "node_count": len(graph.get("nodes") or []),
            "edge_count": len(graph.get("edges") or []),
            "ui_modes": node_modes,
            "forced_retest": True,
        }),
        template_id=template_id,
    )
    return _template_dict(t), None


def get_template_ui_workflow(template_id: str) -> dict | None:
    t = repository.get_template(template_id)
    if t is None:
        return None
    return template_ui_workflow_graph(t)


def delete_template(template_id: str) -> str | None:
    return repository.delete_template(template_id)


# ── SandboxPlan ───────────────────────────────────────────────────────────────

def get_platform_plans() -> list[dict]:
    return [_plan_dict(p) for p in repository.list_plans()]


def _plan_dict(p) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "sort_order": int(p.sort_order),
        "ind_vcpu": int(p.ind_vcpu), "ind_ram_gb": int(p.ind_ram_gb), "ind_disk_gb": int(p.ind_disk_gb),
        "ind_cost_hr": str(p.ind_cost_hr),
        "ind_cost_hr_display": f"{_money(p.ind_cost_hr):.4f}",
        "org_vcpu": int(p.org_vcpu), "org_ram_gb": int(p.org_ram_gb), "org_disk_gb": int(p.org_disk_gb),
        "org_cost_hr": str(p.org_cost_hr),
        "org_cost_hr_display": f"{_money(p.org_cost_hr):.4f}",
        "min_billable_minutes": int(p.min_billable_minutes or 0),
        "is_active": bool(p.is_active),
        "updated_at": p.updated_at,
    }


def save_plan(
    plan_id: str,
    name: str,
    ind_vcpu: int, ind_ram_gb: int, ind_disk_gb: int, ind_cost_hr: str,
    org_vcpu: int, org_ram_gb: int, org_disk_gb: int, org_cost_hr: str,
    updated_by_id: str | None,
    min_billable_minutes: int = 1,
) -> tuple[dict | None, str | None]:
    name = name.strip()
    if not name:
        return None, "Name is required."
    plan_id = plan_id.strip()
    if not re.match(r'^[a-z0-9_-]{1,40}$', plan_id):
        return None, "Plan ID must be lowercase letters, digits, hyphens, or underscores."

    resource_values = {
        "Individual vCPU": (ind_vcpu, 1, 128),
        "Individual RAM": (ind_ram_gb, 1, 1024),
        "Individual disk": (ind_disk_gb, 1, 4096),
        "Organization vCPU": (org_vcpu, 1, 128),
        "Organization RAM": (org_ram_gb, 1, 1024),
        "Organization disk": (org_disk_gb, 1, 4096),
    }
    for label, (value, minimum, maximum) in resource_values.items():
        if not minimum <= int(value) <= maximum:
            return None, f"{label} must be between {minimum} and {maximum}."

    ind_cost = _parse_decimal(ind_cost_hr)
    org_cost = _parse_decimal(org_cost_hr)
    if ind_cost is None or org_cost is None or ind_cost < 0 or org_cost < 0:
        return None, "Cost per hour must be a non-negative decimal value."
    min_billable_minutes = max(0, min(1440, int(min_billable_minutes or 0)))
    existing = repository.get_plan(plan_id)
    if existing:
        p = repository.update_plan(
            plan_id,
            name=name,
            ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost,
            org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost,
            min_billable_minutes=min_billable_minutes,
            updated_by=updated_by_id,
        )
    else:
        sort_order = len(repository.list_plans())
        p = repository.create_plan(
            plan_id=plan_id, name=name, sort_order=sort_order,
            ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost,
            org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost,
            min_billable_minutes=min_billable_minutes,
            updated_by_id=updated_by_id,
        )
    return _plan_dict(p), None


def toggle_plan_active(plan_id: str, is_active: bool) -> None:
    repository.update_plan(plan_id, is_active=is_active)


def delete_plan(plan_id: str) -> str | None:
    return repository.delete_plan(plan_id)


# ── SandboxTemplatePlan ───────────────────────────────────────────────────────

def _int_or_none(v) -> int | None:
    s = str(v).strip() if v is not None else ""
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _resolve_plan_specs(template, global_plan, template_plan) -> dict:
    data = resolve_effective_plan(template, global_plan, template_plan).to_dict()
    data["ind_cost_hr_display"] = f"{_money(data.get('ind_cost_hr')):.4f}"
    data["org_cost_hr_display"] = f"{_money(data.get('org_cost_hr')):.4f}"
    reserve_seconds = max(
        _BILLING_RESERVE_SECONDS,
        int(data.get("min_billable_minutes") or 0) * 60,
    )
    for prefix in ("ind", "org"):
        rate = _money(data.get(f"{prefix}_cost_hr"))
        required = _money(rate * Decimal(reserve_seconds) / Decimal(3600))
        data[f"{prefix}_minimum_start_amount"] = str(required)
        data[f"{prefix}_minimum_start_amount_display"] = f"{required:.4f}"
    return data


def get_effective_plan(
    template_id: str,
    plan_id: str,
    *,
    require_active: bool = True,
) -> tuple[EffectivePlan | None, str | None]:
    template = repository.get_template(template_id)
    if template is None:
        return None, "Template not found."
    plan = repository.get_plan(plan_id)
    if plan is None or (require_active and not plan.is_active):
        return None, "Plan not found or inactive."
    template_plan = repository.get_template_plan(template_id, plan_id)
    if template_plan is not None and not template_plan.is_enabled:
        return None, "This plan is disabled for the selected template."
    try:
        return resolve_effective_plan(template, plan, template_plan), None
    except RuntimePolicyError as exc:
        return None, str(exc)


def get_template_plans_for_hub(template_id: str) -> list[dict]:
    """Active global plans enabled for this template, in sort order."""
    template = repository.get_template(template_id)
    if template is None:
        return []
    global_plans = [p for p in repository.list_plans() if p.is_active]
    tp_rows = repository.list_template_plans(template_id)
    tp_by_plan = {str(tp.plan_id): tp for tp in tp_rows}
    result = []
    for gp in global_plans:
        tp = tp_by_plan.get(str(gp.id))
        if tp is not None and not tp.is_enabled:
            continue
        try:
            result.append(_resolve_plan_specs(template, gp, tp))
        except RuntimePolicyError:
            continue
    return result


def get_template_plan_configs(template_id: str) -> list[dict]:
    """All global plans and whether each one is enabled for this template.

    CPU, RAM, disk and prices are always inherited from SandboxPlan.
    """
    global_plans = get_platform_plans()
    tp_rows = repository.list_template_plans(template_id)
    tp_by_plan = {str(tp.plan_id): tp for tp in tp_rows}
    return [
        {
            "global": gp,
            "is_enabled": bool(tp_by_plan[gp["id"]].is_enabled)
            if gp["id"] in tp_by_plan
            else True,
        }
        for gp in global_plans
    ]


def toggle_template_plan_enabled(template_id: str, plan_id: str, is_enabled: bool) -> str | None:
    if not repository.get_template(template_id):
        return "Template not found."
    if not repository.get_plan(plan_id):
        return "Plan not found."
    repository.upsert_template_plan(
        template_id=template_id,
        plan_id=plan_id,
        is_enabled=is_enabled,
        # Clear deprecated per-template resource overrides so the database
        # cannot silently retain a second source of truth.
        ind_vcpu=None, ind_ram_gb=None, ind_disk_gb=None, ind_cost_hr=None,
        org_vcpu=None, org_ram_gb=None, org_disk_gb=None, org_cost_hr=None,
        max_timeout_hr=None, network_mode=None, min_billable_minutes=None,
        full_internet_enabled=None,
    )
    return None


def save_template_plan_configs(template_id: str, plan_data: list[dict]) -> str | None:
    """Compatibility endpoint: only plan availability is accepted.

    Resource/pricing overrides from older clients are ignored and cleared.
    """
    if repository.get_template(template_id) is None:
        return "Template not found."
    for row in plan_data:
        plan_id = str(row.get("plan_id", "")).strip()
        if not plan_id or not repository.get_plan(plan_id):
            return "A selected plan no longer exists."
        error = toggle_template_plan_enabled(
            template_id, plan_id, bool(row.get("is_enabled", True))
        )
        if error:
            return error
    return None


# ── Instance lifecycle (Phase 5) ──────────────────────────────────────────────

_policy_builder = PolicyBuilder()


def can_start_instance(inst, actor_user_id: str | None) -> bool:
    """Return whether the actor may start this already-created idle run."""
    if not actor_user_id or getattr(inst, "deleted_at", None) is not None:
        return False
    from codesandbox.features.identity.models import User
    from codesandbox.shared.permissions import has_org_permission, is_platform_staff

    if inst.workspace_type == "personal":
        return bool(inst.workspace_user_id and str(inst.workspace_user_id) == str(actor_user_id))
    if inst.workspace_type == "test":
        user = User.objects.filter(id=actor_user_id).first()
        return bool(user and is_platform_staff(user))
    if inst.workspace_type == "org" and inst.workspace_org_id:
        org_id = str(inst.workspace_org_id)
        from codesandbox.features.organizations import repository as org_repo
        if org_repo.is_org_owner(org_id, actor_user_id):
            return True
        if inst.assigned_to_user_id and str(inst.assigned_to_user_id) == str(actor_user_id):
            # Claiming the allocation already verified pool/assigned usage rights.
            return True
        user = User.objects.filter(id=actor_user_id).first()
        return bool(user and has_org_permission(org_id, user, "sandbox.allocations.manage"))
    return False


def can_manage_instance(inst, actor_user_id: str | None) -> bool:
    """Owner, assignee, org managers, and platform staff may act on an instance."""
    if not actor_user_id or getattr(inst, "deleted_at", None) is not None:
        return False

    from codesandbox.features.identity.models import User
    from codesandbox.shared.permissions import has_org_permission, is_platform_staff

    if inst.workspace_user_id and str(inst.workspace_user_id) == actor_user_id:
        return True
    if inst.workspace_type == "org" and inst.assigned_to_user_id and str(inst.assigned_to_user_id) == actor_user_id:
        from codesandbox.features.organizations import repository as org_repo
        org_id = str(inst.workspace_org_id or "")
        return org_repo.is_org_owner(org_id, actor_user_id) or "sandbox.instances.stop_own" in org_repo.get_member_permissions(org_id, actor_user_id)

    user = User.objects.filter(id=actor_user_id).first()
    if user is None:
        return False
    if is_platform_staff(user):
        return True
    if inst.workspace_type == "org" and inst.workspace_org_id:
        return has_org_permission(str(inst.workspace_org_id), user, "sandbox.allocations.manage")
    return False


def can_view_instance(instance_id: str, actor_user_id: str | None) -> bool:
    inst = repository.get_instance(instance_id)
    if (
        inst is None
        or not actor_user_id
        or getattr(inst, "deleted_at", None) is not None
    ):
        return False

    from codesandbox.features.identity.models import User
    from codesandbox.shared.permissions import has_org_permission, is_platform_staff

    if inst.workspace_user_id and str(inst.workspace_user_id) == actor_user_id:
        return True
    if inst.workspace_type == "org" and inst.assigned_to_user_id and str(inst.assigned_to_user_id) == actor_user_id:
        return True
    user = User.objects.filter(id=actor_user_id).first()
    if user is None:
        return False
    if is_platform_staff(user):
        return True
    if inst.workspace_type == "org" and inst.workspace_org_id:
        from codesandbox.features.organizations import repository as org_repo
        org_id = str(inst.workspace_org_id)
        org = org_repo.get_organization(org_id)
        if org is None or org.status != "active":
            return False
        return (
            has_org_permission(org_id, user, "sandbox.allocations.view_all")
            or has_org_permission(org_id, user, "sandbox.allocations.manage")
        )
    return False


def get_instance_for_view(
    instance_id: str,
    actor_user_id: str | None,
) -> tuple[dict | None, str | None]:
    if not can_view_instance(instance_id, actor_user_id):
        return None, "You do not have permission to view this instance."
    inst = repository.get_instance(instance_id)
    return (_instance_dict(inst), None) if inst else (None, "Instance not found.")


def can_open_instance_channel(
    instance_id: str,
    actor_user_id: str | None,
    purpose: str,
) -> bool:
    if not can_view_instance(instance_id, actor_user_id):
        return False
    inst = repository.get_instance(instance_id)
    if inst is None:
        return False
    if purpose in {"terminal", "fs", "gui", "android"}:
        if inst.status != "running" or not bool(inst.runtime_id):
            return False
        template = repository.get_template(str(inst.template_id))
        if template is None:
            return False
        allowed = set(template_allowed_ui_modes(template))
        runtime_config = parse_runtime_config(template.runtime_config)
        bg = _ui_feature_config(runtime_config, "background_run")
        if purpose == "terminal":
            return bool({"terminal_only", "lab_ui"} & allowed) or bool(bg.get("allow_terminal"))
        if purpose == "fs":
            return "lab_ui" in allowed or bool(bg.get("allow_filesystem"))
        if purpose == "gui":
            return "desktop_gui" in allowed
        if purpose == "android":
            return "android_ui" in allowed
    return purpose == "monitor"


def log_channel_token_issued(instance_id: str, purpose: str, actor_user_id: str | None = None) -> None:
    """High-risk channels (GUI/Android connection tokens) are audit logged
    individually — everything else (monitor/terminal/fs) is already implicit
    in the instance's own started/stopped/artifact event trail."""
    repository.log_instance_event(
        instance_id,
        f"instance.{purpose}_token_issued",
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        detail=json.dumps({"purpose": purpose}),
    )


def upload_instance_input(
    instance_id: str,
    actor_user_id: str | None,
    file_storage,
) -> tuple[dict | None, str | None]:
    """Store a start-time input without ever writing it to the web host."""
    inst = repository.get_instance(instance_id)
    if inst is None:
        return None, "Instance not found."
    # Upload is a pre-start action. A member who successfully claimed an
    # allocation is allowed to attach its required input even when their role
    # intentionally lacks the broader stop/delete permission.
    if not can_start_instance(inst, actor_user_id):
        return None, "You do not have permission to upload to this instance."
    if inst.status != "idle":
        return None, "Inputs can only be uploaded before the instance starts."

    template = repository.get_template(str(inst.template_id))
    if template is None:
        return None, "Template not found."
    template_runtime_config = parse_runtime_config(template.runtime_config)
    allowed_file_types = [
        str(t).lower().lstrip(".") for t in template_runtime_config.get("allowed_file_types") or []
    ]
    allow_extensionless = bool(template_runtime_config.get("allow_extensionless_input"))
    if allowed_file_types and "*" not in allowed_file_types:
        filename = str(getattr(file_storage, "filename", "") or "")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if not extension and not allow_extensionless:
            return None, "This template requires a supported file extension."
        if extension and extension not in allowed_file_types:
            return None, f"This sandbox only accepts: {', '.join(allowed_file_types)}."
    configured_mb = int(template_runtime_config.get("max_input_size_mb") or template.max_upload_mb or 50)
    max_upload_bytes = min(
        configured_mb * 1024 * 1024,
        get_settings().sandbox_max_upload_bytes,
    )
    uploaded = upload_private_filestorage(
        file_storage,
        prefix=f"sandboxes/{instance_id}/inputs",
        max_bytes=max_upload_bytes,
    )
    if uploaded is None:
        return None, f"Select a non-empty file no larger than {max_upload_bytes // (1024 * 1024)} MB."

    try:
        item = repository.create_instance_input(
            instance_id=instance_id,
            name=uploaded["name"],
            storage_key=uploaded["storage_key"],
            size_bytes=uploaded["size_bytes"],
            checksum=uploaded["checksum"],
        )
    except Exception:
        delete_private_object(uploaded["storage_key"])
        raise

    repository.log_instance_event(
        instance_id,
        "input.uploaded",
        actor=f"user:{actor_user_id}",
        detail=json.dumps({
            "name": item.name,
            "size_bytes": int(item.size_bytes),
            "checksum": item.checksum,
        }),
    )
    return {
        "id": str(item.id),
        "name": item.name,
        "size_bytes": int(item.size_bytes),
        "checksum": item.checksum,
        "created_at": item.created_at,
    }, None


def get_instance_artifacts_for_view(
    instance_id: str,
    actor_user_id: str | None,
) -> tuple[list[dict] | None, str | None]:
    if not can_view_instance(instance_id, actor_user_id):
        return None, "You do not have permission to view this instance."
    return [
        {
            "id": str(item.id),
            "name": item.name,
            "artifact_type": item.artifact_type,
            "size_bytes": int(item.size_bytes),
            "checksum": item.checksum,
            "created_at": item.created_at,
        }
        for item in repository.list_instance_artifacts(instance_id)
    ], None


def _audit_event_dict(row) -> dict:
    detail = {}
    if row.detail:
        try:
            detail = json.loads(row.detail)
        except ValueError:
            detail = {"message": row.detail}
    return {
        "id": str(row.id),
        "event": row.event,
        "old_status": row.old_status,
        "new_status": row.new_status,
        "actor": row.actor or "",
        "detail": detail,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def get_instance_events_for_view(
    instance_id: str,
    actor_user_id: str | None,
    limit: int = 80,
) -> tuple[list[dict] | None, str | None]:
    if not can_view_instance(instance_id, actor_user_id):
        return None, "You do not have permission to view this instance."
    events = repository.list_instance_audit_log(instance_id, limit=limit)
    return [_audit_event_dict(row) for row in reversed(events)], None


def get_instance_note_for_view(
    instance_id: str,
    actor_user_id: str | None,
) -> tuple[dict | None, str | None]:
    if not can_view_instance(instance_id, actor_user_id):
        return None, "You do not have permission to view this instance."
    note = repository.get_instance_note(instance_id)
    if note is None:
        return {
            "title": "Untitled Investigation",
            "content": "",
            "updated_at": None,
        }, None
    return {
        "title": note.title or "Untitled Investigation",
        "content": note.content or "",
        "updated_at": note.updated_at,
    }, None


def save_instance_note_for_view(
    instance_id: str,
    actor_user_id: str | None,
    *,
    title: str,
    content: str,
) -> tuple[dict | None, str | None]:
    if not can_view_instance(instance_id, actor_user_id):
        return None, "You do not have permission to edit notes for this instance."
    note = repository.upsert_instance_note(
        instance_id,
        title=title.strip() or "Untitled Investigation",
        content=content,
        updated_by=actor_user_id,
    )
    return {
        "title": note.title,
        "content": note.content or "",
        "updated_at": note.updated_at,
    }, None


def select_instance_ui_mode(
    instance: dict,
    requested_ui_mode: str | None = None,
) -> tuple[str, list[str]]:
    allowed = normalize_ui_modes(instance.get("allowed_ui_modes"), ("terminal_only",))
    default = normalize_ui_mode(instance.get("default_ui_mode"), allowed[0])
    if default not in allowed:
        default = allowed[0]
    requested = normalize_ui_mode(requested_ui_mode, default) if requested_ui_mode else default
    return (requested if requested in allowed else default), allowed


def _ui_workflow_choices(
    graph: dict,
    node: dict | None,
    instance: dict,
    evidence: set[str] | None = None,
) -> list[dict]:
    """Outgoing choices from the current node, gated by real completion evidence."""
    if not node:
        return []
    required = [
        value for value in (
            _normalize_test_requirement(item)
            for item in (node.get("completion_requirements") or [])
        ) if value
    ]
    available = evidence or set()
    if required and any(item not in available for item in required):
        return []
    exit_code = instance.get("exit_code")
    choices = []
    for edge in ui_workflow_outgoing_edges(graph, str(node.get("id"))):
        condition = str(edge.get("condition") or "manual")
        if condition == "success" and exit_code != 0:
            continue
        if condition == "failure" and (exit_code is None or exit_code == 0):
            continue
        target = ui_workflow_node_by_id(graph, edge.get("target"))
        if target is None:
            continue
        choices.append({
            "edge_id": edge.get("id") or "",
            "label": edge.get("label") or node.get("continue_label") or f"Open {UI_MODE_LABELS.get(target.get('ui_mode'), target.get('ui_mode'))}",
            "condition": condition,
            "target_node_id": target.get("id"),
            "target_ui_mode": target.get("ui_mode"),
            "target_label": target.get("label") or UI_MODE_LABELS.get(target.get("ui_mode"), target.get("ui_mode")),
            "target_auto_start": bool(target.get("auto_start")),
            "url": f"/instances/{instance.get('id')}?ui_mode={target.get('ui_mode')}&node={target.get('id')}",
        })
    return choices


def get_instance_ui_context(
    instance_id: str,
    actor_user_id: str | None,
    requested_ui_mode: str | None = None,
    requested_node_id: str | None = None,
) -> tuple[dict | None, str | None]:
    instance, error = get_instance_for_view(instance_id, actor_user_id)
    if error or instance is None:
        return None, error or "Instance not found."
    artifacts, artifacts_error = get_instance_artifacts_for_view(instance_id, actor_user_id)
    if artifacts_error:
        return None, artifacts_error
    events, events_error = get_instance_events_for_view(instance_id, actor_user_id)
    if events_error:
        return None, events_error
    notes, notes_error = get_instance_note_for_view(instance_id, actor_user_id)
    if notes_error:
        return None, notes_error
    template = repository.get_template(instance["template_id"])
    plan_row = repository.get_plan(instance["plan_id"])
    runtime_config = parse_runtime_config(template.runtime_config if template else None)

    ui_workflow_node = None
    ui_workflow_choices: list[dict] = []
    ui_workflow_restart_url = None
    evidence = _instance_evidence(instance_id)
    if template is not None and template_interface_behavior(template) == "workflow":
        graph = template_ui_workflow_graph(template)
        persisted_node_id = None
        try:
            persisted_config = json.loads(instance.get("user_config") or "{}")
            persisted_node_id = (persisted_config.get("_ui_state") or {}).get("current_node_id")
        except (TypeError, ValueError):
            persisted_config = {}
        start_node = ui_workflow_start_node(graph)
        current_node = ui_workflow_node_by_id(graph, persisted_node_id) or start_node
        ui_workflow_node = current_node
        if requested_node_id and str(requested_node_id) != str((current_node or {}).get("id") or ""):
            permitted_targets = {
                str(choice.get("target_node_id") or "")
                for choice in _ui_workflow_choices(graph, current_node, instance, evidence)
            }
            requested = ui_workflow_node_by_id(graph, requested_node_id)
            if requested is not None and str(requested.get("id")) in permitted_targets:
                ui_workflow_node = requested
        if ui_workflow_node and str(ui_workflow_node.get("id")) != str(persisted_node_id or ""):
            state = dict(persisted_config.get("_ui_state") or {})
            state["current_node_id"] = str(ui_workflow_node.get("id"))
            persisted_config["_ui_state"] = state
            repository.update_instance_user_config(
                instance_id, json.dumps(persisted_config, separators=(",", ":"))
            )
            instance["user_config"] = json.dumps(persisted_config, separators=(",", ":"))
        # Workflow-mode nodes aren't restricted to Single Mode's UI_MODES
        # (custom_page is workflow-only) — the graph was already validated.
        ui_mode = (ui_workflow_node or {}).get("ui_mode") or "terminal_only"
        allowed_ui_modes = ui_workflow_node_ui_modes(graph) or [ui_mode]
        ui_workflow_choices = _ui_workflow_choices(
            graph, ui_workflow_node, instance, evidence
        )
        if start_node and start_node.get("id") != (ui_workflow_node or {}).get("id"):
            ui_workflow_restart_url = f"/instances/{instance.get('id')}?ui_mode={start_node.get('ui_mode')}&node={start_node.get('id')}"
    else:
        ui_mode, allowed_ui_modes = select_instance_ui_mode(instance, requested_ui_mode)

    return {
        "instance": instance,
        "template": _template_dict(template) if template else None,
        "plan": _plan_dict(plan_row) if plan_row else {
            "id": instance.get("plan_id", ""),
            "name": instance.get("plan_id", ""),
            "ind_vcpu": instance.get("allocated_vcpu") or 0,
            "ind_ram_gb": instance.get("allocated_ram_gb") or 0,
            "ind_disk_gb": instance.get("allocated_disk_gb") or 0,
        },
        "ui_mode": ui_mode,
        "ui_mode_label": UI_MODE_LABELS.get(ui_mode, ui_mode),
        "allowed_ui_modes": allowed_ui_modes,
        "ui_mode_labels": UI_MODE_LABELS,
        "runtime_config": runtime_config,
        "ui_config": runtime_config.get("ui") if isinstance(runtime_config.get("ui"), dict) else {},
        "ui_workflow_node": ui_workflow_node,
        "ui_workflow_choices": ui_workflow_choices,
        "ui_workflow_restart_url": ui_workflow_restart_url,
        "test_context": (
            {
                "is_test": True,
                **_evaluate_test_progress(repository.get_instance(instance_id)),
            }
            if instance.get("workspace_type") == "test" else None
        ),
        "artifacts": artifacts or [],
        "events": events or [],
        "notes": notes or {},
    }, None


def get_artifact_for_download(
    artifact_id: str,
    actor_user_id: str | None,
):
    artifact = repository.get_artifact(artifact_id)
    if artifact is None:
        return None, "Artifact not found."
    if not can_view_instance(str(artifact.instance_id), actor_user_id):
        return None, "You do not have permission to download this artifact."
    return artifact, None


def _test_effective_plan(template) -> EffectivePlan:
    """Build an admin-only, zero-cost test profile independent of Hub plans.

    Test Launch is a platform validation tool, not a customer purchase. It must
    therefore keep working when a template has no published plan mapping, when
    a plan is disabled, or when the customer's selected plan is too large for
    a small local worker. Templates may opt into larger test resources through
    runtime_config.test_resources.
    """
    runtime = parse_runtime_config(template.runtime_config)
    resources = runtime.get("test_resources")
    resources = resources if isinstance(resources, dict) else {}

    def _bounded(name: str, default: int, maximum: int) -> int:
        try:
            value = int(resources.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(1, min(maximum, value))

    vcpu = _bounded("vcpu", 1, 8)
    ram_gb = _bounded("ram_gb", 2, 32)
    disk_gb = _bounded("disk_gb", 5, 100)
    timeout_hr = _bounded("max_timeout_hr", 1, 4)
    network_mode = normalize_network_mode(template.network_mode or "disabled")
    full_internet = network_mode == "full_internet"

    return EffectivePlan(
        id="__test__",
        name="Admin Test Launch",
        ind_vcpu=vcpu,
        ind_ram_gb=ram_gb,
        ind_disk_gb=disk_gb,
        ind_cost_hr=Decimal("0"),
        org_vcpu=vcpu,
        org_ram_gb=ram_gb,
        org_disk_gb=disk_gb,
        org_cost_hr=Decimal("0"),
        max_timeout_hr=min(timeout_hr, max(1, int(template.max_timeout_hr or 1))),
        network_mode=network_mode,
        min_billable_minutes=0,
        full_internet_enabled=full_internet,
        is_active=True,
        is_enabled=True,
        sort_order=-1,
    )


def start_instance(
    instance_id: str,
    actor_user_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Build runtime policy, enqueue start job, transition idle→provisioning."""
    from .queue import enqueue_job

    inst = repository.get_instance(instance_id)
    if inst is None:
        return None, "Instance not found."
    if not can_start_instance(inst, actor_user_id):
        return None, "You do not have permission to start this instance."
    if inst.status != "idle":
        return None, f"Cannot start an instance in '{inst.status}' state."
    if inst.workspace_type == "org" and inst.workspace_org_id:
        from codesandbox.features.organizations import repository as org_repo
        org = org_repo.get_organization(str(inst.workspace_org_id))
        if org is None or org.status != "active":
            return None, "Organization is not active."
        if (
            inst.billing_entity != "org"
            or str(inst.billed_org_id or "") != str(inst.workspace_org_id)
            or inst.billed_user_id
        ):
            return None, "Organization instance billing scope is invalid."
    elif inst.workspace_type == "personal":
        if (
            inst.billing_entity != "user"
            or str(inst.billed_user_id or "") != str(inst.workspace_user_id or "")
            or inst.billed_org_id
        ):
            return None, "Personal instance billing scope is invalid."
    elif inst.workspace_type == "test" and inst.billing_entity != "test":
        return None, "Test instance billing scope is invalid."

    t = repository.get_template(str(inst.template_id))
    if not t:
        return None, "Template no longer exists."
    if inst.workspace_type != "test" and t.status != "active":
        return None, "Template is not active."
    if inst.workspace_type == "test":
        try:
            effective_plan = _test_effective_plan(t)
        except RuntimePolicyError as exc:
            return None, str(exc)
    else:
        effective_plan, plan_error = get_effective_plan(str(t.id), inst.plan_id)
        if plan_error or effective_plan is None:
            return None, plan_error or "Plan is unavailable."

    if inst.workspace_type != "test":
        balance_error = _ensure_start_balance(
            effective_plan,
            inst.workspace_type or "personal",
            user_id=str(inst.billed_user_id or inst.workspace_user_id or "") or None,
            org_id=str(inst.billed_org_id or inst.workspace_org_id or "") or None,
        )
        if balance_error:
            return None, balance_error

    workspace_type = inst.workspace_type or "personal"
    template_dict = _template_dict(t)
    try:
        user_config = json.loads(inst.user_config) if inst.user_config else None
        runtime_policy = _policy_builder.build(
            template_dict,
            effective_plan,
            workspace_type,
            user_config,
        )
        runtime_policy = get_runtime_driver(runtime_policy["runtime_class"]).prepare(
            _instance_dict(inst), runtime_policy
        )
        identity_user_id = str(inst.workspace_user_id or inst.assigned_to_user_id or actor_user_id or "") or None
        sandbox_username = _sandbox_username_for_user_id(identity_user_id)
        runtime_policy["sandbox_username"] = sandbox_username
        runtime_policy["template_test_revision"] = _template_test_revision(t)
        environment = dict(runtime_policy.get("environment") or {})
        environment.setdefault("CODESANDBOX_USERNAME", sandbox_username)
        environment.setdefault("USER", sandbox_username)
        environment.setdefault("LOGNAME", sandbox_username)
        environment.setdefault("CODESANDBOX_VCPU_LIMIT", str(runtime_policy["vcpu"]))
        environment.setdefault("CODESANDBOX_RAM_LIMIT_GB", str(runtime_policy["ram_gb"]))
        environment.setdefault("CODESANDBOX_DISK_LIMIT_GB", str(runtime_policy["disk_gb"]))
        runtime_policy["environment"] = environment
        runtime_policy["scheduler_disk_gb"] = _scheduler_disk_gb(
            str(runtime_policy["runtime_class"]), int(runtime_policy["disk_gb"])
        )
    except (RuntimePolicyError, UnsupportedRuntimeError, ValueError, TypeError) as exc:
        return None, str(exc)

    if getattr(inst, "allocation_id", None):
        allocation = repository.get_org_allocation(str(inst.allocation_id))
        if allocation and int(allocation.max_session_minutes or 0) > 0:
            allocation_timeout = int(allocation.max_session_minutes) * 60
            configured_timeout = int(runtime_policy.get("max_timeout_sec") or allocation_timeout)
            runtime_policy["max_timeout_sec"] = min(configured_timeout, allocation_timeout)

    inputs = repository.list_instance_inputs(instance_id)
    if runtime_policy.get("input_required") and not inputs:
        return None, "This sandbox requires an input file before it can start."
    runtime_policy["inputs"] = [
        {
            "name": item.name,
            "storage_key": item.storage_key,
            "size_bytes": int(item.size_bytes),
            "checksum": item.checksum,
        }
        for item in inputs
    ]
    if inputs:
        environment = dict(runtime_policy.get("environment") or {})
        environment.setdefault("CODESANDBOX_INPUT_NAME", str(inputs[0].name or "input")[:255])
        runtime_policy["environment"] = environment
    artifact_prefix = f"sandboxes/{instance_id}/artifacts"
    runtime_policy["artifact_prefix"] = artifact_prefix

    actor = f"user:{actor_user_id}" if actor_user_id else "system"
    settings = get_settings()
    callback_url = settings.control_plane_internal_url + "/internal/worker/callback"
    job_id = str(_uuid.uuid4())
    callback_token = make_worker_callback_token(job_id, instance_id, "start")

    requested_vcpu = int(runtime_policy["vcpu"])
    requested_ram_gb = int(runtime_policy["ram_gb"])
    requested_disk_gb = int(runtime_policy["disk_gb"])
    scheduler_disk_gb = int(runtime_policy["scheduler_disk_gb"])

    from codesandbox.features.worker.service import (
        release_worker_capacity,
        reserve_worker_capacity,
        select_worker_for_instance,
    )

    worker_node = select_worker_for_instance(
        requested_vcpu,
        requested_ram_gb,
        required_disk_gb=scheduler_disk_gb,
        runtime_class=str(runtime_policy["runtime_class"]),
    )
    if worker_node is None:
        return None, (
            "No online worker currently has enough free capacity for "
            f"{requested_vcpu} vCPU, {requested_ram_gb} GB RAM and "
            f"{scheduler_disk_gb} GB scheduled disk. Stop another instance "
            "or select a smaller plan."
        )
    worker_id = worker_node.worker_id

    job_payload = {
        "job_id": job_id,
        "action": "start",
        "instance_id": instance_id,
        "worker_id": worker_id,
        "runtime_policy": runtime_policy,
        "callback_url": callback_url,
        "callback_token": callback_token,
        # Container label metadata only (Phase 5 fleet reconciliation) — not
        # runtime-policy-affecting, so it lives on the job, not the policy.
        "labels": {
            "template_id": str(inst.template_id),
            "plan_id": str(inst.plan_id or ""),
            "owner_type": workspace_type,
            "owner_id": str(inst.workspace_org_id or inst.workspace_user_id or ""),
        },
    }

    billing_reserve_sec = max(
        int(runtime_policy["min_billable_sec"]),
        _BILLING_RESERVE_SECONDS
        if inst.workspace_type != "test"
        and Decimal(str(runtime_policy["cost_hr"])) > 0
        else 0,
    )
    minimum_required = (
        Decimal(str(runtime_policy["cost_hr"]))
        * Decimal(billing_reserve_sec)
        / Decimal(3600)
    ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    now = datetime.now(timezone.utc)
    reserve_worker_capacity(
        worker_id,
        vcpu=requested_vcpu,
        ram_gb=requested_ram_gb,
        disk_gb=scheduler_disk_gb,
    )
    inst, begin_error = repository.begin_instance_start(
        instance_id,
        actor=actor,
        worker_id=worker_id,
        worker_job_id=job_id,
        runtime_policy=json.dumps(runtime_policy, separators=(",", ":")),
        runtime_provider=str(runtime_policy["runtime_provider"]),
        artifact_prefix=artifact_prefix,
        allocated_vcpu=requested_vcpu,
        allocated_ram_gb=requested_ram_gb,
        allocated_disk_gb=requested_disk_gb,
        effective_network_mode=str(runtime_policy["network_mode"]),
        cost_hr_snapshot=Decimal(str(runtime_policy["cost_hr"])),
        billing_currency=str(runtime_policy["currency"]),
        min_billable_sec=int(runtime_policy["min_billable_sec"]),
        expires_at=now + timedelta(seconds=int(runtime_policy["max_timeout_sec"])),
        minimum_required=minimum_required,
    )
    if begin_error or inst is None:
        release_worker_capacity(
            worker_id,
            vcpu=requested_vcpu,
            ram_gb=requested_ram_gb,
            disk_gb=scheduler_disk_gb,
        )
        return None, begin_error or "Could not start instance."
    try:
        enqueue_job(job_payload)
    except Exception:
        release_worker_capacity(
            worker_id,
            vcpu=requested_vcpu,
            ram_gb=requested_ram_gb,
            disk_gb=scheduler_disk_gb,
        )
        repository.release_instance_reservation(instance_id)
        repository.transition_instance_status(
            instance_id,
            new_status="failed",
            actor="system",
            expected_statuses=("provisioning",),
            exit_reason="queue_unavailable",
            billing_status="not_charged",
            charged_amount=Decimal("0"),
        )
        return None, "Runtime queue is unavailable. Try again shortly."
    return _instance_dict(inst), None


def restart_instance(
    instance_id: str,
    actor_user_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Start a fresh billable run with the same template, plan and owner.

    The old instance remains as immutable usage history. Workspace contents are
    intentionally not reused because normal worker cleanup removes that volume;
    input-requiring templates must be launched from the Hub with a new upload.
    """
    previous = repository.get_instance(instance_id)
    if previous is None or getattr(previous, "deleted_at", None) is not None:
        return None, "Instance not found."
    if not can_manage_instance(previous, actor_user_id):
        return None, "You do not have permission to restart this instance."
    template = repository.get_template(str(previous.template_id))
    allowed, error = _restart_instance_state(previous, template)
    if not allowed:
        return None, error or f"Cannot restart an instance in '{previous.status}' state."
    created_by = str(actor_user_id or previous.created_by_user_id or "")
    if not created_by:
        return None, "A user identity is required to restart this instance."
    fresh_user_config = previous.user_config
    try:
        parsed_user_config = json.loads(previous.user_config or "{}")
        if isinstance(parsed_user_config, dict):
            parsed_user_config.pop("_ui_state", None)
            fresh_user_config = json.dumps(parsed_user_config, separators=(",", ":"))
    except (TypeError, ValueError):
        pass
    fresh = repository.create_instance(
        template_id=str(previous.template_id),
        plan_id=str(previous.plan_id),
        workspace_type=str(previous.workspace_type or "personal"),
        workspace_user_id=(str(previous.workspace_user_id) if previous.workspace_user_id else None),
        workspace_org_id=(str(previous.workspace_org_id) if previous.workspace_org_id else None),
        assigned_to_user_id=(str(previous.assigned_to_user_id) if previous.assigned_to_user_id else None),
        created_by_user_id=created_by,
        billing_entity=str(previous.billing_entity or "user"),
        billed_user_id=(str(previous.billed_user_id) if previous.billed_user_id else None),
        billed_org_id=(str(previous.billed_org_id) if previous.billed_org_id else None),
        user_config=fresh_user_config,
    )
    started, start_error = start_instance(str(fresh.id), actor_user_id=actor_user_id)
    if start_error:
        repository.archive_instance(str(fresh.id))
        return None, start_error
    repository.archive_instance(str(previous.id))
    repository.log_instance_event(
        str(fresh.id),
        "instance.restarted",
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        detail=json.dumps({"previous_instance_id": str(previous.id)}),
    )
    return started, None


def stop_instance(
    instance_id: str,
    actor_user_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Enqueue stop job, transition running→stopping."""
    from .queue import enqueue_job

    inst = repository.get_instance(instance_id)
    if inst is None:
        return None, "Instance not found."
    if not can_manage_instance(inst, actor_user_id):
        return None, "You do not have permission to stop this instance."
    if inst.status not in ("running", "provisioning"):
        return None, f"Cannot stop an instance in '{inst.status}' state."
    if not inst.worker_id:
        return None, "This instance has no assigned worker yet — try again shortly."

    from codesandbox.features.worker.service import is_worker_online

    if not is_worker_online(inst.worker_id):
        return None, f"Worker '{inst.worker_id}' is offline — it cannot be reached to stop this instance."

    actor = f"user:{actor_user_id}" if actor_user_id else "system"
    callback_url = get_settings().control_plane_internal_url + "/internal/worker/callback"
    job_id = str(_uuid.uuid4())
    callback_token = make_worker_callback_token(job_id, instance_id, "stop")

    transitioned = repository.transition_instance_status(
        instance_id,
        new_status="stopping",
        actor=actor,
        expected_statuses=("running", "provisioning"),
        worker_job_id=job_id,
    )
    if transitioned is None:
        return None, "Instance state changed before it could be stopped."
    try:
        enqueue_job({
            "job_id": job_id,
            "action": "stop",
            "instance_id": instance_id,
            "worker_id": transitioned.worker_id,
            "reason": "user_stop",
            "runtime_id": transitioned.runtime_id,
            "runtime_provider": transitioned.runtime_provider,
            "workspace_volume_id": transitioned.workspace_volume_id,
            "runtime_policy": json.loads(transitioned.runtime_policy or "{}"),
            "callback_url": callback_url,
            "callback_token": callback_token,
        })
    except Exception:
        repository.log_instance_event(
            instance_id,
            "stop.queue_failed",
            actor="system",
            detail=json.dumps({"job_id": job_id}),
        )
        return None, "Runtime queue is unavailable. Reconciliation will retry cleanup."
    return _instance_dict(transitioned), None


def list_reconcile_candidates(worker_id: str) -> list[dict]:
    """Everything this worker_id might still have a live container for,
    with a fresh callback token per instance — called once at worker boot
    so it can reattach to containers that outlived its own process restart."""
    result = []
    for inst in repository.list_runtime_backed_instances_for_worker(worker_id):
        job_id = str(inst.worker_job_id or "")
        if not job_id or not inst.runtime_id:
            continue
        result.append({
            "instance_id": str(inst.id),
            "job_id": job_id,
            "runtime_id": inst.runtime_id,
            "runtime_provider": inst.runtime_provider,
            "workspace_volume_id": inst.workspace_volume_id,
            "runtime_policy": json.loads(inst.runtime_policy or "{}"),
            "callback_token": make_worker_callback_token(job_id, str(inst.id), "start"),
        })
    return result


def _template_test_revision(template) -> str:
    """Hash execution-affecting template state for stale-test protection."""
    payload = {
        "docker_image": str(template.docker_image or ""),
        "default_command": str(template.default_command or ""),
        "working_dir": str(template.working_dir or ""),
        "input_mount_path": str(template.input_mount_path or ""),
        "output_mount_path": str(template.output_mount_path or ""),
        "artifact_paths": str(template.artifact_paths or ""),
        "input_required": bool(template.input_required),
        "sandbox_type": str(template.sandbox_type or ""),
        "runtime_class": str(template.runtime_class or ""),
        "allowed_ui_modes": str(template.allowed_ui_modes or ""),
        "default_ui_mode": str(template.default_ui_mode or ""),
        "interface_behavior": str(template.interface_behavior or ""),
        "ui_workflow_json": str(template.ui_workflow_json or ""),
        "network_mode": str(template.network_mode or ""),
        "allow_root": bool(template.allow_root),
        "read_only_root": bool(template.read_only_root),
        "run_as_user": str(template.run_as_user or ""),
        "pids_limit": int(template.pids_limit or 0),
        "max_timeout_hr": int(template.max_timeout_hr or 0),
        "runtime_config": _runtime_execution_config(parse_runtime_config(template.runtime_config)),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _test_config_for_template(template) -> dict:
    runtime_config = parse_runtime_config(template.runtime_config)
    raw = runtime_config.get("test_config")
    return raw if isinstance(raw, dict) else {}


def _normalize_test_requirement(value: object) -> str | None:
    requirement = str(value or "").strip()
    if not requirement or not _SAFE_TEST_REQUIREMENT_RE.fullmatch(requirement):
        return None
    return requirement


def _test_requirements_for_template(template) -> list[str]:
    """Resolve the minimum evidence required for this template's Test Launch.

    Explicit `test_config.requirements` wins. Legacy success_condition fields
    remain supported so existing templates do not silently change behavior.
    """
    test_config = _test_config_for_template(template)
    explicit = test_config.get("requirements")
    requirements: list[str] = []
    if isinstance(explicit, list):
        for item in explicit:
            normalized = _normalize_test_requirement(item)
            if normalized and normalized not in requirements:
                requirements.append(normalized)
    if requirements:
        return requirements

    requirements.append("runtime_started")
    success_condition = str(test_config.get("success_condition") or "").strip()
    if success_condition == "exit_zero":
        requirements.append("exit_zero")
    elif success_condition == "log_contains":
        for pattern in test_config.get("log_contains") or []:
            pattern = str(pattern or "").strip()
            normalized = _normalize_test_requirement(f"log:{pattern}")
            if normalized:
                requirements.append(normalized)
    elif success_condition == "artifact_exists":
        for path in test_config.get("required_artifacts") or []:
            path = str(path or "").strip().lstrip("/")
            normalized = _normalize_test_requirement(f"artifact:{path}")
            if normalized:
                requirements.append(normalized)
    elif success_condition == "healthcheck":
        requirements.append("healthcheck")

    if template_interface_behavior(template) == "workflow":
        modes = set(ui_workflow_node_ui_modes(template_ui_workflow_graph(template)))
    else:
        modes = {template_default_ui_mode(template)}
    if "terminal_only" in modes:
        requirements.append("terminal_ready")
    if "lab_ui" in modes:
        requirements.extend(["terminal_ready", "filesystem_ready"])
    if "custom_page" in modes:
        requirements.append("custom_page_ready")

    deduped: list[str] = []
    for requirement in requirements:
        if requirement not in deduped:
            deduped.append(requirement)
    return deduped


def _instance_evidence(instance_id: str) -> set[str]:
    evidence: set[str] = set()
    for row in repository.list_instance_audit_log(instance_id, limit=500):
        if row.event not in {_TEST_EVIDENCE_EVENT, _RUNTIME_EVIDENCE_EVENT}:
            continue
        try:
            detail = json.loads(row.detail or "{}")
        except ValueError:
            continue
        requirement = _normalize_test_requirement(detail.get("requirement"))
        if requirement:
            evidence.add(requirement)
    return evidence


def _has_instance_event(instance_id: str, event_name: str) -> bool:
    return any(
        row.event == event_name
        for row in repository.list_instance_audit_log(instance_id, limit=500)
    )


def _record_runtime_evidence(
    inst,
    requirement: str,
    *,
    actor: str,
    detail: dict | None = None,
) -> bool:
    normalized = _normalize_test_requirement(requirement)
    if not normalized or normalized in _instance_evidence(str(inst.id)):
        return False
    payload = {"requirement": normalized, **(detail or {})}
    repository.log_instance_event(
        str(inst.id),
        _RUNTIME_EVIDENCE_EVENT,
        actor=actor,
        detail=json.dumps(payload, separators=(",", ":")),
    )
    if inst.workspace_type == "test":
        repository.log_instance_event(
            str(inst.id),
            _TEST_EVIDENCE_EVENT,
            actor=actor,
            detail=json.dumps(payload, separators=(",", ":")),
        )
    return True


def _mark_template_test_result(inst, status: str, reason: str | None = None) -> None:
    """Record a Test Launch result without allowing stale runs to publish.

    A run started against an older template revision is retained for audit, but
    cannot overwrite the current template's test gate after an administrator
    edits the template while the run is open.
    """
    if inst.workspace_type != "test":
        return
    template = repository.get_template(str(inst.template_id))
    if template is None:
        return
    try:
        policy = json.loads(inst.runtime_policy or "{}")
    except ValueError:
        policy = {}
    started_revision = str(policy.get("template_test_revision") or "")
    current_revision = _template_test_revision(template)
    if started_revision and started_revision != current_revision:
        repository.log_instance_event(
            str(inst.id),
            "test.stale",
            actor="system",
            detail=json.dumps({
                "reason": "Template configuration changed after this Test Launch started.",
                "started_revision": started_revision,
                "current_revision": current_revision,
            }, separators=(",", ":")),
        )
        return
    repository.update_template(
        str(inst.template_id),
        last_test_status=status,
        last_tested_at=datetime.now(timezone.utc),
        last_test_error=reason if status == "failed" else None,
    )
    event = "test.passed" if status == "passed" else "test.failed"
    if not _has_instance_event(str(inst.id), event):
        repository.log_instance_event(
            str(inst.id),
            event,
            actor="system",
            detail=json.dumps({"reason": reason or ""}, separators=(",", ":")),
        )


def _evaluate_test_progress(inst) -> dict:
    template = repository.get_template(str(inst.template_id))
    if template is None:
        return {"requirements": [], "evidence": [], "missing": [], "passed": False}
    requirements = _test_requirements_for_template(template)
    evidence = _instance_evidence(str(inst.id))
    try:
        policy = json.loads(inst.runtime_policy or "{}")
    except (TypeError, ValueError):
        policy = {}
    started_revision = str(policy.get("template_test_revision") or "")
    current_revision = _template_test_revision(template)
    stale = bool(started_revision and started_revision != current_revision)
    if stale:
        return {
            "requirements": requirements,
            "evidence": sorted(evidence),
            "missing": ["template_configuration_changed"],
            "passed": False,
            "stale": True,
        }
    missing = [requirement for requirement in requirements if requirement not in evidence]
    passed = not missing and bool(requirements)
    if passed and not _has_instance_event(str(inst.id), "test.passed"):
        _mark_template_test_result(inst, "passed")
    return {
        "requirements": requirements,
        "evidence": sorted(evidence),
        "missing": missing,
        "passed": passed,
        "stale": False,
    }


def record_instance_ui_evidence(
    instance_id: str,
    actor_user_id: str,
    requirement: str,
) -> tuple[dict | None, str | None]:
    """Record evidence emitted by the real instance UI for tests and workflows."""
    inst = repository.get_instance(instance_id)
    if inst is None:
        return None, "Instance not found."
    if not can_manage_instance(inst, actor_user_id):
        return None, "You do not have permission to update this instance."
    allowed = {"terminal_ready", "filesystem_ready", "custom_page_ready", "healthcheck"}
    if requirement not in allowed:
        return None, "Unsupported UI evidence."
    if inst.status != "running":
        return None, "The sandbox is not running."
    recorded = _record_runtime_evidence(
        inst,
        requirement,
        actor=f"user:{actor_user_id}",
        detail={"source": "instance_ui"},
    )
    result = {
        "recorded": recorded,
        "evidence": sorted(_instance_evidence(instance_id)),
    }
    if inst.workspace_type == "test":
        result.update(_evaluate_test_progress(inst))
    return result, None


def record_test_ui_evidence(
    instance_id: str,
    actor_user_id: str,
    requirement: str,
) -> tuple[dict | None, str | None]:
    """Backward-compatible wrapper used by existing test code."""
    inst = repository.get_instance(instance_id)
    if inst is None or inst.workspace_type != "test":
        return None, "Test instance not found."
    return record_instance_ui_evidence(instance_id, actor_user_id, requirement)


def _safe_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _upsert_worker_runtime(inst, *, status: str) -> None:
    if not inst.worker_id:
        return
    from codesandbox.features.worker.repository import upsert_runtime

    upsert_runtime(
        instance_id=str(inst.id),
        worker_id=str(inst.worker_id),
        runtime_provider=inst.runtime_provider,
        runtime_id=inst.runtime_id,
        workspace_volume_id=inst.workspace_volume_id,
        status=status,
    )


def _release_worker_capacity_for(inst) -> None:
    if not inst.worker_id:
        return
    from codesandbox.features.worker.service import release_worker_capacity

    scheduler_disk_gb = int(inst.allocated_disk_gb or 0)
    try:
        policy = json.loads(inst.runtime_policy or "{}")
        scheduler_disk_gb = int(
            policy.get("scheduler_disk_gb") or scheduler_disk_gb
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    release_worker_capacity(
        inst.worker_id,
        vcpu=int(inst.allocated_vcpu or 0),
        ram_gb=int(inst.allocated_ram_gb or 0),
        disk_gb=scheduler_disk_gb,
    )


def _charge_completed_instance(inst) -> None:
    actual_runtime_sec = int(inst.total_runtime_sec or 0)
    billable_sec = max(actual_runtime_sec, int(inst.min_billable_sec or 0))
    from codesandbox.features.finance.service import create_usage_charge_for_instance

    charge, tx, revenue, status = create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=actual_runtime_sec,
        billable_seconds=billable_sec,
        description=f"Sandbox usage ({billable_sec}s at {inst.billing_currency or 'GBP'} {inst.cost_hr_snapshot or 0}/hr)",
    )
    if charge is not None:
        repository.log_instance_event(
            str(inst.id),
            "usage_charged",
            actor="system",
            detail=json.dumps({
                "usage_charge_id": str(charge.id),
                "gross": str(charge.gross_amount or "0"),
                "discount": str(charge.discount_amount or "0"),
                "credit": str(charge.credit_amount or "0"),
                "revenue": str(revenue),
                "balance_transaction_id": str(tx.id) if tx else None,
                "billing_status": status,
            }),
        )


def handle_worker_callback(
    instance_id: str,
    job_id: str,
    event: str,
    data: dict | None = None,
) -> tuple[dict, str | None]:
    """Process an authenticated worker event and return worker directives."""
    inst = repository.get_instance(instance_id)
    if inst is None:
        return {}, "Instance not found."
    if str(inst.worker_job_id or "") != job_id:
        return {}, "Callback job does not match the active worker job."

    data = data or {}
    now = datetime.now(timezone.utc)
    detail = json.dumps(data) if data else None
    response: dict = {}

    if event == "started":
        runtime_id = str(data.get("runtime_id") or "").strip()
        if not runtime_id:
            return {}, "Worker did not provide a runtime ID."
        updated = repository.transition_instance_status(
            instance_id,
            new_status="running",
            actor="worker",
            expected_statuses=("provisioning", "running"),
            started_at=inst.started_at or now,
            last_heartbeat_at=now,
            runtime_provider=str(data.get("runtime_provider") or inst.runtime_provider or "docker"),
            runtime_id=runtime_id,
            runtime_node_id=str(data.get("runtime_node_id") or "") or None,
            worker_id=str(data.get("worker_id") or "") or None,
            workspace_volume_id=str(data.get("workspace_volume_id") or "") or None,
        )
        if updated is None:
            return {}, f"Cannot accept started event in '{inst.status}' state."
        repository.log_instance_event(instance_id, "started", actor="worker", detail=detail)
        _record_runtime_evidence(
            updated, "runtime_started", actor="worker", detail={"runtime_id": runtime_id}
        )
        if updated.workspace_type == "test":
            _evaluate_test_progress(updated)
        _upsert_worker_runtime(updated, status="running")

    elif event == "runtime_evidence":
        requirement = str(data.get("requirement") or "").strip()
        if not requirement:
            return {}, "Runtime evidence did not include a requirement."
        _record_runtime_evidence(
            inst, requirement, actor="worker", detail={
                "kind": data.get("kind"),
                "value": data.get("value"),
            },
        )
        repository.log_instance_event(
            instance_id, "runtime_evidence_received", actor="worker", detail=detail
        )
        if inst.workspace_type == "test":
            response["test_progress"] = _evaluate_test_progress(inst)

    elif event == "heartbeat":
        updated = repository.transition_instance_status(
            instance_id,
            new_status=inst.status,
            actor="worker",
            expected_statuses=("provisioning", "running", "stopping", "cleanup"),
            last_heartbeat_at=now,
            runtime_id=str(data.get("runtime_id") or inst.runtime_id or "") or None,
            runtime_node_id=str(data.get("runtime_node_id") or inst.runtime_node_id or "") or None,
            worker_id=str(data.get("worker_id") or inst.worker_id or "") or None,
        )
        if updated is None:
            return {}, f"Cannot accept heartbeat in '{inst.status}' state."
        if inst.status == "running" and inst.billing_entity != "test":
            elapsed = runtime_seconds(inst.started_at, now)
            reserve_seconds = max(
                int(inst.min_billable_sec or 0),
                elapsed + _BILLING_RESERVE_SECONDS,
            )
            desired = (
                Decimal(str(inst.cost_hr_snapshot or "0"))
                * Decimal(reserve_seconds)
                / Decimal(3600)
            ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
            if not repository.reserve_instance_balance(instance_id, desired):
                repository.transition_instance_status(
                    instance_id,
                    new_status="stopping",
                    actor="system",
                    expected_statuses=("running",),
                    exit_reason="insufficient_balance",
                )
                response["command"] = "stop"
                response["reason"] = "insufficient_balance"

    elif event == "cleanup_started":
        updated = repository.transition_instance_status(
            instance_id,
            new_status="cleanup",
            actor="worker",
            expected_statuses=("provisioning", "running", "stopping", "failed", "expired", "killed", "cleanup"),
            cleanup_started_at=now,
            last_heartbeat_at=now,
        )
        if updated is None:
            return {}, f"Cannot begin cleanup in '{inst.status}' state."
        repository.log_instance_event(instance_id, "cleanup_started", actor="worker", detail=detail)

    elif event in {"stopped", "expired", "killed"}:
        current = repository.get_instance(instance_id)
        if current and current.status not in {"cleanup", "stopping"}:
            repository.transition_instance_status(
                instance_id,
                new_status="cleanup",
                actor="worker",
                expected_statuses=("provisioning", "running", "failed", "expired", "killed"),
                cleanup_started_at=now,
            )
        current = repository.get_instance(instance_id)
        final_status = "expired" if event == "expired" or data.get("reason") == "timeout" else event
        if final_status not in {"stopped", "expired", "killed"}:
            final_status = "stopped"
        elapsed = runtime_seconds(current.started_at if current else inst.started_at, now)
        updated = repository.transition_instance_status(
            instance_id,
            new_status=final_status,
            actor="worker",
            expected_statuses=("stopping", "cleanup", final_status),
            stopped_at=now,
            total_runtime_sec=elapsed,
            last_heartbeat_at=now,
            exit_code=_safe_int(data.get("exit_code")),
            exit_reason=str(data.get("reason") or final_status)[:500],
        )
        if updated is None:
            return {}, f"Cannot finalize instance in '{current.status if current else inst.status}' state."
        _charge_completed_instance(updated)
        repository.release_org_allocation(getattr(updated, "allocation_id", None))
        _release_worker_capacity_for(updated)
        _upsert_worker_runtime(updated, status=final_status)
        repository.log_instance_event(instance_id, final_status, actor="worker", detail=detail)
        exit_code = _safe_int(data.get("exit_code"))
        if exit_code == 0:
            _record_runtime_evidence(updated, "exit_zero", actor="worker")
        if inst.workspace_type == "test":
            progress = _evaluate_test_progress(updated)
            if not progress["passed"]:
                reason = str(data.get("test_reason") or data.get("reason") or final_status)
                missing = ", ".join(progress["missing"])
                if missing:
                    reason = f"{reason}; missing test requirements: {missing}"
                _mark_template_test_result(updated, "failed", reason=reason)

    elif event == "failed":
        elapsed = runtime_seconds(inst.started_at, now)
        updated = repository.transition_instance_status(
            instance_id,
            new_status="failed",
            actor="worker",
            expected_statuses=("provisioning", "running", "stopping", "cleanup", "failed"),
            stopped_at=now if inst.started_at else None,
            total_runtime_sec=elapsed,
            last_heartbeat_at=now,
            exit_code=_safe_int(data.get("exit_code")),
            exit_reason=str(data.get("reason") or data.get("error") or "runtime_failed")[:500],
        )
        if updated is None:
            return {}, f"Cannot fail instance in '{inst.status}' state."
        if inst.started_at:
            _charge_completed_instance(updated)
        else:
            repository.release_instance_reservation(instance_id)
            updated.billing_reserved_amount = Decimal("0")
            updated.billing_status = "not_charged"
            updated.charged_amount = Decimal("0")
            updated.save()
        repository.release_org_allocation(getattr(updated, "allocation_id", None))
        _release_worker_capacity_for(updated)
        _upsert_worker_runtime(updated, status="failed")
        repository.log_instance_event(instance_id, "failed", actor="worker", detail=detail)
        if inst.workspace_type == "test":
            failure_reason = str(data.get("reason") or "runtime_failed").strip()
            failure_error = str(data.get("error") or "").strip()
            if failure_error and failure_error != failure_reason:
                failure_reason = f"{failure_reason}: {failure_error}"
            _mark_template_test_result(
                updated,
                "failed",
                reason=failure_reason[:500],
            )

    elif event == "escape_attempt":
        repository.log_instance_event(instance_id, "escape_attempt", actor="worker", detail=detail)
        from .queue import enqueue_job
        kill_job_id = str(_uuid.uuid4())
        callback_token = make_worker_callback_token(kill_job_id, instance_id, "kill")
        transitioned = repository.transition_instance_status(
            instance_id,
            new_status="stopping",
            actor="system",
            expected_statuses=("running", "provisioning"),
            worker_job_id=kill_job_id,
            exit_reason="escape_attempt",
        )
        if transitioned is None:
            return {}, f"Cannot kill instance in '{inst.status}' state."
        enqueue_job({
            "job_id": kill_job_id,
            "action": "kill",
            "instance_id": instance_id,
            "worker_id": transitioned.worker_id,
            "reason": "escape_attempt",
            "callback_url": get_settings().control_plane_internal_url + "/internal/worker/callback",
            "callback_token": callback_token,
        })

    elif event == "artifact_ready":
        storage_key = str(data.get("storage_key") or "")
        prefix = str(inst.artifact_prefix or "")
        if not storage_key or not prefix or not storage_key.startswith(prefix + "/"):
            return {}, "Artifact key is outside this instance prefix."
        try:
            name = safe_artifact_name(str(data.get("name") or "artifact"))
        except ValueError as exc:
            return {}, str(exc)
        repository.create_or_get_artifact(
            instance_id=instance_id,
            name=name,
            artifact_type=str(data.get("artifact_type") or "file")[:80],
            storage_key=storage_key,
            size_bytes=max(0, int(data.get("size_bytes") or 0)),
            checksum=str(data.get("checksum") or "")[:64],
        )
        repository.log_instance_event(instance_id, "artifact_ready", actor="worker", detail=detail)
        _record_runtime_evidence(
            inst, f"artifact:{name.lstrip('/')}", actor="worker", detail={"name": name}
        )
        if inst.workspace_type == "test":
            _evaluate_test_progress(inst)

    else:
        return {}, f"Unknown event: {event}"

    return response, None


# ── Admin Test Run ────────────────────────────────────────────────────────────

def start_test_instance(
    template_id: str,
    actor_user_id: str,
    test_input_file=None,
) -> tuple[dict | None, str | None]:
    """Create and start a persistent, real Test Launch instance.

    Required input is validated and uploaded before the instance row exists, so
    a missing/invalid file never creates or starts a test. If the same admin
    already has a live test for this template, return it for resumption.
    """
    t = repository.get_template(template_id)
    if t is None:
        return None, "Template not found."

    active = repository.find_active_test_instance(
        template_id, actor_user_id=actor_user_id
    )
    if active is not None:
        return {
            **_instance_dict(active),
            "instance_url": f"/instances/{active.id}",
            "resumed": True,
        }, None

    runtime_config = parse_runtime_config(t.runtime_config)
    test_config = runtime_config.get("test_config")
    test_config = test_config if isinstance(test_config, dict) else {}
    requires_input = bool(t.input_required or test_config.get("requires_input"))
    if requires_input and test_input_file is None:
        return None, "This template requires an input file before Test Launch can start."

    try:
        test_plan = _test_effective_plan(t)
    except RuntimePolicyError as exc:
        return None, str(exc)

    staged_upload = None
    instance_id = str(_uuid.uuid4())
    if test_input_file is not None:
        filename = str(getattr(test_input_file, "filename", "") or "")
        allowed_file_types = [
            str(value).lower().lstrip(".")
            for value in runtime_config.get("allowed_file_types") or []
        ]
        allow_extensionless = bool(runtime_config.get("allow_extensionless_input"))
        if allowed_file_types and "*" not in allowed_file_types:
            extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if not extension and not allow_extensionless:
                return None, "This template requires a supported file extension."
            if extension and extension not in allowed_file_types:
                return None, f"This sandbox only accepts: {', '.join(allowed_file_types)}."
        configured_mb = int(runtime_config.get("max_input_size_mb") or t.max_upload_mb or 50)
        max_upload_bytes = min(
            configured_mb * 1024 * 1024,
            get_settings().sandbox_max_upload_bytes,
        )
        staged_upload = upload_private_filestorage(
            test_input_file,
            prefix=f"sandboxes/{instance_id}/inputs",
            max_bytes=max_upload_bytes,
        )
        if staged_upload is None:
            return None, (
                f"Select a non-empty file no larger than "
                f"{max_upload_bytes // (1024 * 1024)} MB."
            )

    inst = None
    try:
        inst = repository.create_instance(
            instance_id=instance_id,
            template_id=template_id,
            plan_id=test_plan.id,
            workspace_type="test",
            workspace_user_id=actor_user_id,
            created_by_user_id=actor_user_id,
            billing_entity="test",
        )
        if staged_upload is not None:
            repository.create_instance_input(
                instance_id=str(inst.id),
                name=staged_upload["name"],
                storage_key=staged_upload["storage_key"],
                size_bytes=staged_upload["size_bytes"],
                checksum=staged_upload["checksum"],
            )
            repository.log_instance_event(
                str(inst.id),
                "input.uploaded",
                actor=f"user:{actor_user_id}",
                detail=json.dumps({
                    "name": staged_upload["name"],
                    "size_bytes": staged_upload["size_bytes"],
                    "checksum": staged_upload["checksum"],
                }, separators=(",", ":")),
            )
        repository.update_template(
            template_id,
            last_test_status="untested",
            last_tested_at=None,
            last_test_error=None,
        )
        repository.log_instance_event(
            str(inst.id),
            "test.started",
            actor=f"user:{actor_user_id}",
            detail=json.dumps({
                "requirements": _test_requirements_for_template(t),
                "template_revision": _template_test_revision(t),
            }, separators=(",", ":")),
        )
        result, error = start_instance(str(inst.id), actor_user_id=actor_user_id)
        if error:
            raise RuntimeError(error)
        return {
            **(result or _instance_dict(inst)),
            "instance_url": f"/instances/{inst.id}",
            "resumed": False,
        }, None
    except Exception as exc:
        current = repository.get_instance(str(inst.id)) if inst is not None else None
        safe_to_remove = current is None or current.status == "idle"
        if staged_upload is not None and safe_to_remove:
            try:
                delete_private_object(staged_upload["storage_key"])
            except Exception:
                pass
        if current is not None and current.status == "idle":
            try:
                current.delete()
            except Exception:
                pass
        return None, str(exc)


# ── Balance / Billing ─────────────────────────────────────────────────────────

def _balance_dict(b) -> dict:
    amount = Decimal(str(b.amount or "0"))
    reserved = Decimal(str(b.reserved_amount or "0"))
    available = max(Decimal("0"), amount - reserved)
    return {
        "entity_type": b.entity_type,
        "entity_id": str(b.entity_id),
        "amount": str(amount),
        "amount_display": f"{_money(amount):.4f}",
        "reserved_amount": str(reserved),
        "reserved_amount_display": f"{_money(reserved):.4f}",
        "available_amount": str(available),
        "available_amount_display": f"{_money(available):.4f}",
        "updated_at": b.updated_at,
    }


def _transaction_dict(tx) -> dict:
    amount = Decimal(str(tx.amount or "0"))
    return {
        "id": str(tx.id),
        "type": tx.type,
        "type_label": (tx.type or "").replace("_", " ").title(),
        "amount": str(amount),
        "absolute_amount": str(abs(amount)),
        "is_credit": amount >= 0,
        "description": tx.description or "",
        "reference": tx.reference or "",
        "provider": tx.provider or "internal",
        "status": "failed" if tx.type == "failed_payment" else "completed",
        "instance_id": str(tx.instance_id) if tx.instance_id else None,
        "created_at": tx.created_at,
    }


def get_user_billing(user_id: str, *, page: int = 1, page_size: int = 20) -> dict:
    b = repository.get_or_create_balance("user", user_id)
    txs, total, page, total_pages = repository.list_transactions_paginated(
        "user",
        user_id,
        page=page,
        page_size=page_size,
    )
    return {
        "balance": _balance_dict(b),
        "transactions": [_transaction_dict(t) for t in txs],
        "transactions_total": total,
        "transactions_page": page,
        "transactions_total_pages": total_pages,
        "transactions_page_size": page_size,
    }


def get_org_billing(org_id: str, *, page: int = 1, page_size: int = 20) -> dict:
    b = repository.get_or_create_balance("org", org_id)
    txs, total, page, total_pages = repository.list_transactions_paginated(
        "org",
        org_id,
        page=page,
        page_size=page_size,
    )
    return {
        "balance": _balance_dict(b),
        "transactions": [_transaction_dict(t) for t in txs],
        "transactions_total": total,
        "transactions_page": page,
        "transactions_total_pages": total_pages,
        "transactions_page_size": page_size,
    }
