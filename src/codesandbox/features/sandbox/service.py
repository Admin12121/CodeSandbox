from __future__ import annotations

import json
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
    parse_ui_workflow_graph,
    ui_workflow_node_by_id,
    ui_workflow_node_ui_modes,
    ui_workflow_outgoing_edges,
    ui_workflow_start_node,
    validate_ui_workflow_graph,
)

_WORKER_CALLBACK_SALT = "sandbox.worker-callback"


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:80] or "template"


def _parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


def _runtime_fields_changed(
    existing_t,
    values: dict,
    existing_runtime_config: dict,
    new_runtime_config: dict,
) -> bool:
    if any(getattr(existing_t, key, None) != value for key, value in values.items()):
        return True
    # GUI/Android connection config lives inside the free-form runtime_config
    # JSON blob, not a typed column, but changing it is just as dangerous as
    # changing runtime_class/network_mode — it points the browser at a
    # different VNC/emulator endpoint.
    for key in ("desktop_gui", "android_ui"):
        if _ui_feature_config(existing_runtime_config, key) != _ui_feature_config(new_runtime_config, key):
            return True
    return False


def _image_allowed(image: str) -> bool:
    settings = get_settings()
    if not settings.sandbox_allowed_images:
        return not settings.sandbox_require_image_allowlist
    return image in settings.sandbox_allowed_images


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
        start = ui_workflow_start_node(template_ui_workflow_graph(template_or_dict))
        if start and start.get("ui_mode"):
            return normalize_ui_mode(start.get("ui_mode"), "terminal_only")
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
        if require_publish_ready and not default_command.strip():
            return "Background Run requires a command before publishing."
        if require_publish_ready and not success_condition:
            return "Background Run requires a success_condition in runtime.json before publishing."

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

def _instance_dict(inst, template_cache: dict | None = None) -> dict:
    tid = str(inst.template_id)
    t = (template_cache or {}).get(tid) or repository.get_template(tid)
    allowed_ui_modes = template_allowed_ui_modes(t) if t else ["terminal_only"]
    default_ui_mode = template_default_ui_mode(t) if t else "terminal_only"
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
    _, plan_error = get_effective_plan(str(t.id), plan_id)
    if plan_error:
        return None, plan_error
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
    from codesandbox.features.organizations import repository as org_repo
    from codesandbox.features.identity.models import User

    actor = User.objects.filter(id=creator_user_id).first()
    if actor is None or actor.status != "active":
        return None, "Authenticated user is not active."
    org = org_repo.get_organization(org_id)
    if org is None or org.status != "active":
        return None, "Organization is not active."
    if org_repo.get_member(org_id, creator_user_id) is None:
        return None, "You are not a member of this organization."
    permissions = org_repo.get_member_permissions(org_id, creator_user_id)
    if not org_repo.is_org_owner(org_id, creator_user_id) and "sandbox.instances.create" not in permissions:
        return None, "You do not have permission to create organization instances."
    if assigned_to_user_id and org_repo.get_member(org_id, assigned_to_user_id) is None:
        return None, "Assigned user is not a member of this organization."
    t = repository.get_template_by_slug(template_slug)
    if not t or t.status != "active":
        return None, "Template not found or inactive."
    _, plan_error = get_effective_plan(str(t.id), plan_id)
    if plan_error:
        return None, plan_error
    inst = repository.create_instance(
        template_id=str(t.id),
        plan_id=plan_id,
        workspace_type="org",
        workspace_org_id=org_id,
        assigned_to_user_id=assigned_to_user_id,
        created_by_user_id=creator_user_id,
        billing_entity="org",
        billed_org_id=org_id,
    )
    return _instance_dict(inst), None


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


# ── InstanceRequest ───────────────────────────────────────────────────────────

def _request_dict(req, template_cache: dict | None = None) -> dict:
    tid = str(req.template_id)
    t = (template_cache or {}).get(tid) or repository.get_template(tid)
    return {
        "id": str(req.id),
        "org_id": str(req.org_id),
        "requested_by": str(req.requested_by),
        "template_id": str(req.template_id),
        "template_name": t.name if t else "Unknown",
        "template_slug": t.slug if t else "",
        "plan_id": req.plan_id,
        "note": req.note or "",
        "status": req.status,
        "reviewed_by": str(req.reviewed_by) if req.reviewed_by else None,
        "reviewed_at": req.reviewed_at,
        "review_note": req.review_note or "",
        "instance_id": str(req.instance_id) if req.instance_id else None,
        "created_at": req.created_at,
    }


def submit_instance_request(
    org_id: str,
    user_id: str,
    template_slug: str,
    plan_id: str,
    note: str | None = None,
) -> tuple[dict | None, str | None]:
    from codesandbox.features.organizations import repository as org_repo
    org = org_repo.get_organization(org_id)
    if org is None or org.status != "active":
        return None, "Organization is not active."
    if org_repo.get_member(org_id, user_id) is None:
        return None, "You are not a member of this organization."
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
    )
    return _request_dict(req), None


def review_instance_request(
    request_id: str,
    org_id: str,
    reviewer_id: str,
    action: str,
    review_note: str | None = None,
) -> tuple[dict | None, str | None]:
    from datetime import datetime, timezone
    from codesandbox.features.organizations import repository as org_repo

    if not org_repo.is_org_owner(org_id, reviewer_id) and "sandbox.requests.review" not in org_repo.get_member_permissions(org_id, reviewer_id):
        return None, "You do not have permission to review sandbox requests."
    req = repository.get_instance_request(request_id)
    if not req:
        return None, "Request not found."
    if str(req.org_id) != org_id:
        return None, "Request not found in this organization."
    if req.status != "pending":
        return None, "Request already reviewed."
    if action not in ("approved", "denied"):
        return None, "Invalid action."

    instance_id = None
    if action == "approved":
        org = org_repo.get_organization(str(req.org_id))
        if org is None or org.status != "active":
            return None, "Organization is not active."
        tmpl = repository.get_template(str(req.template_id))
        if not tmpl:
            return None, "Template no longer exists; cannot approve."
        inst, err = create_org_instance(
            org_id=str(req.org_id),
            creator_user_id=reviewer_id,
            template_slug=tmpl.slug,
            plan_id=req.plan_id,
            assigned_to_user_id=str(req.requested_by),
        )
        if err:
            return None, err
        instance_id = inst["id"]

    req = repository.update_instance_request(
        request_id,
        status=action,
        reviewed_by=reviewer_id,
        reviewed_at=datetime.now(timezone.utc),
        review_note=review_note,
        instance_id=instance_id,
    )
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
        "working_dir": t.working_dir or "/workspace",
        "input_mount_path": t.input_mount_path or "/input",
        "output_mount_path": t.output_mount_path or "/output",
        "artifact_paths": t.artifact_paths or "",
        "input_required": bool(t.input_required),
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
    input_mount_path: str = "/input",
    output_mount_path: str = "/output",
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
    docker_image = docker_image.strip()
    if not docker_image:
        return None, "Docker image is required."
    if not _image_allowed(docker_image):
        return None, "Docker image is not in the configured runtime allowlist."
    runtime_class = runtime_class if runtime_class in RUNTIME_CLASSES else "container"
    interface_behavior = interface_behavior if interface_behavior in ("single", "workflow") else "single"

    existing_t = repository.get_template(template_id) if template_id else None
    if template_id and existing_t is None:
        return None, "Template not found."

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
    if network_mode == "full_internet" and not allow_full_internet:
        return None, "Enable full internet explicitly before selecting that network mode."
    if sandbox_type in {"malware", "reverse_engineering"} and network_mode == "full_internet":
        return None, "Full internet is disabled for malware and reverse-engineering templates."
    max_timeout_hr = max(1, min(72, int(max_timeout_hr or 2)))
    max_upload_mb = max(1, min(1024, int(max_upload_mb or 50)))
    pids_limit = max(32, min(4096, int(pids_limit or 256)))
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
    mounts = [working_dir.strip(), input_mount_path.strip(), output_mount_path.strip()]
    if any(not path.startswith("/") for path in mounts) or len(set(mounts)) != 3:
        return None, "Workspace, input, and output mounts must be distinct absolute paths."
    if isinstance(artifact_paths, str):
        raw_artifacts = artifact_paths.strip()
        try:
            artifact_values = json.loads(raw_artifacts) if raw_artifacts else [output_mount_path]
        except ValueError:
            artifact_values = [line.strip() for line in raw_artifacts.splitlines() if line.strip()]
    else:
        artifact_values = artifact_paths
    if not isinstance(artifact_values, list) or any(
        not str(path).startswith("/") for path in artifact_values
    ):
        return None, "Artifact paths must be absolute container paths."
    artifact_paths_json = json.dumps(
        [str(path) for path in artifact_values], separators=(",", ":")
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
        allow_full_internet=allow_full_internet,
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
            "allow_full_internet": allow_full_internet,
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


def get_template_test_status(template_id: str) -> dict | None:
    t = repository.get_template(template_id)
    if t is None:
        return None
    return {
        "last_test_status": t.last_test_status or "untested",
        "last_test_error": t.last_test_error or "",
    }


def save_template_config(template_id: str, config_json: str, actor_user_id: str | None = None) -> None:
    existing_t = repository.get_template(template_id)
    new_config = config_json.strip() or None
    update_kwargs: dict = {"runtime_config": new_config}
    if existing_t is not None:
        # The Config tab autosaves on every edit, so only the fields that are
        # actually dangerous (GUI/Android connection config — same list as
        # _runtime_fields_changed) force a retest; everything else (notes,
        # workflow labels, etc.) autosaves without touching publish status.
        old_cfg = parse_runtime_config(existing_t.runtime_config)
        new_cfg = parse_runtime_config(new_config)
        changed_keys = [
            key for key in ("desktop_gui", "android_ui")
            if _ui_feature_config(old_cfg, key) != _ui_feature_config(new_cfg, key)
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
    try:
        allowed_network_modes = json.loads(
            p.allowed_network_modes or '["disabled","restricted"]'
        )
    except (TypeError, ValueError):
        allowed_network_modes = ["disabled", "restricted"]
    return {
        "id": p.id,
        "name": p.name,
        "sort_order": int(p.sort_order),
        "ind_vcpu": int(p.ind_vcpu), "ind_ram_gb": int(p.ind_ram_gb), "ind_disk_gb": int(p.ind_disk_gb),
        "ind_cost_hr": str(p.ind_cost_hr),
        "org_vcpu": int(p.org_vcpu), "org_ram_gb": int(p.org_ram_gb), "org_disk_gb": int(p.org_disk_gb),
        "org_cost_hr": str(p.org_cost_hr),
        "min_billable_minutes": int(p.min_billable_minutes or 0),
        "allowed_network_modes": p.allowed_network_modes or '["disabled","restricted"]',
        "allowed_network_mode_values": allowed_network_modes,
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
    allowed_network_modes: list[str] | str | None = None,
) -> tuple[dict | None, str | None]:
    name = name.strip()
    if not name:
        return None, "Name is required."
    plan_id = plan_id.strip()
    if not re.match(r'^[a-z0-9_-]{1,40}$', plan_id):
        return None, "Plan ID must be lowercase letters, digits, hyphens, or underscores."

    ind_cost = _parse_decimal(ind_cost_hr)
    org_cost = _parse_decimal(org_cost_hr)
    if ind_cost is None or org_cost is None:
        return None, "Invalid cost per hour value."
    min_billable_minutes = max(0, min(1440, int(min_billable_minutes or 0)))
    if isinstance(allowed_network_modes, str):
        try:
            allowed_values = json.loads(allowed_network_modes)
        except ValueError:
            allowed_values = allowed_network_modes.split(",")
    else:
        allowed_values = allowed_network_modes or ["disabled", "restricted"]
    allowed_values = list(
        dict.fromkeys(normalize_network_mode(value) for value in allowed_values)
    )
    if not allowed_values or any(
        value not in {"disabled", "restricted", "full_internet"}
        for value in allowed_values
    ):
        return None, "Select at least one valid network mode."
    allowed_network_json = json.dumps(allowed_values, separators=(",", ":"))

    existing = repository.get_plan(plan_id)
    if existing:
        p = repository.update_plan(
            plan_id,
            name=name,
            ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost,
            org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost,
            min_billable_minutes=min_billable_minutes,
            allowed_network_modes=allowed_network_json,
            updated_by=updated_by_id,
        )
    else:
        sort_order = len(repository.list_plans())
        p = repository.create_plan(
            plan_id=plan_id, name=name, sort_order=sort_order,
            ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost,
            org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost,
            min_billable_minutes=min_billable_minutes,
            allowed_network_modes=allowed_network_json,
            updated_by_id=updated_by_id,
        )
    return _plan_dict(p), None


def toggle_plan_active(plan_id: str, is_active: bool) -> None:
    repository.update_plan(plan_id, is_active=is_active)


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
    return resolve_effective_plan(template, global_plan, template_plan).to_dict()


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
    """Active global plans merged with template-level overrides, in sort order."""
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
    """All global plans + their template overrides, for the admin Plans tab."""
    global_plans = get_platform_plans()
    tp_rows = repository.list_template_plans(template_id)
    tp_by_plan = {str(tp.plan_id): tp for tp in tp_rows}
    result = []
    for gp in global_plans:
        tp = tp_by_plan.get(gp["id"])
        result.append({
            "global": gp,
            "is_enabled": bool(tp.is_enabled) if tp is not None else True,
            "ind_vcpu": int(tp.ind_vcpu) if (tp is not None and tp.ind_vcpu is not None) else None,
            "ind_ram_gb": int(tp.ind_ram_gb) if (tp is not None and tp.ind_ram_gb is not None) else None,
            "ind_disk_gb": int(tp.ind_disk_gb) if (tp is not None and tp.ind_disk_gb is not None) else None,
            "ind_cost_hr": str(tp.ind_cost_hr) if (tp is not None and tp.ind_cost_hr is not None) else None,
            "org_vcpu": int(tp.org_vcpu) if (tp is not None and tp.org_vcpu is not None) else None,
            "org_ram_gb": int(tp.org_ram_gb) if (tp is not None and tp.org_ram_gb is not None) else None,
            "org_disk_gb": int(tp.org_disk_gb) if (tp is not None and tp.org_disk_gb is not None) else None,
            "org_cost_hr": str(tp.org_cost_hr) if (tp is not None and tp.org_cost_hr is not None) else None,
            "max_timeout_hr": int(tp.max_timeout_hr) if (tp is not None and tp.max_timeout_hr is not None) else None,
            "network_mode": tp.network_mode if tp is not None else None,
            "min_billable_minutes": int(tp.min_billable_minutes) if (tp is not None and tp.min_billable_minutes is not None) else None,
            "full_internet_enabled": bool(tp.full_internet_enabled) if (tp is not None and tp.full_internet_enabled is not None) else None,
        })
    return result


def toggle_template_plan_enabled(template_id: str, plan_id: str, is_enabled: bool) -> str | None:
    """Flip whether a global plan is available on this template. Returns an
    error message, or None on success. Only touches is_enabled — any existing
    per-template overrides (ind_vcpu, etc.) are left untouched."""
    if not repository.get_plan(plan_id):
        return "Plan not found."
    repository.upsert_template_plan(template_id=template_id, plan_id=plan_id, is_enabled=is_enabled)
    return None


def save_template_plan_configs(template_id: str, plan_data: list[dict]) -> str | None:
    """Upsert per-template plan overrides from the admin Plans tab."""
    template = repository.get_template(template_id)
    if template is None:
        return "Template not found."
    normalized_rows: list[dict] = []
    for row in plan_data:
        plan_id = str(row.get("plan_id", "")).strip()
        if not plan_id or not repository.get_plan(plan_id):
            return "A selected plan no longer exists."
        normalized = {
            "plan_id": plan_id,
            "is_enabled": bool(row.get("is_enabled", True)),
            "ind_vcpu": _int_or_none(row.get("ind_vcpu")),
            "ind_ram_gb": _int_or_none(row.get("ind_ram_gb")),
            "ind_disk_gb": _int_or_none(row.get("ind_disk_gb")),
            "ind_cost_hr": _parse_decimal(str(row["ind_cost_hr"]).strip()) if row.get("ind_cost_hr") else None,
            "org_vcpu": _int_or_none(row.get("org_vcpu")),
            "org_ram_gb": _int_or_none(row.get("org_ram_gb")),
            "org_disk_gb": _int_or_none(row.get("org_disk_gb")),
            "org_cost_hr": _parse_decimal(str(row["org_cost_hr"]).strip()) if row.get("org_cost_hr") else None,
            "max_timeout_hr": _int_or_none(row.get("max_timeout_hr")),
            "network_mode": normalize_network_mode(row.get("network_mode")) if row.get("network_mode") else None,
            "min_billable_minutes": _int_or_none(row.get("min_billable_minutes")),
            "full_internet_enabled": row.get("full_internet_enabled"),
        }
        if any(
            row.get(field) not in (None, "") and normalized[field] is None
            for field in ("ind_cost_hr", "org_cost_hr")
        ):
            return "Cost overrides must be valid decimal values."
        if normalized["full_internet_enabled"] not in (None, True, False):
            return "Invalid full internet override."
        resource_fields = (
            "ind_vcpu", "ind_ram_gb", "ind_disk_gb",
            "org_vcpu", "org_ram_gb", "org_disk_gb",
        )
        if any(normalized[field] is not None and normalized[field] <= 0 for field in resource_fields):
            return "Resource overrides must be positive values."
        if any(
            normalized[field] is not None and normalized[field] < 0
            for field in ("ind_cost_hr", "org_cost_hr")
        ):
            return "Cost overrides cannot be negative."
        if normalized["max_timeout_hr"] is not None and not 1 <= normalized["max_timeout_hr"] <= 72:
            return "Timeout overrides must be between 1 and 72 hours."
        if normalized["min_billable_minutes"] is not None and not 0 <= normalized["min_billable_minutes"] <= 1440:
            return "Minimum billing must be between 0 and 1440 minutes."
        if normalized["network_mode"] not in {None, "disabled", "restricted", "full_internet"}:
            return "Invalid network override."
        requests_internet = (
            normalized["network_mode"] == "full_internet"
            or normalized["full_internet_enabled"] is True
        )
        if requests_internet and (
            not template.allow_full_internet
            or template.sandbox_type in {"malware", "reverse_engineering"}
        ):
            return "Full internet is not permitted for this template."
        normalized_rows.append(normalized)
    repository.save_template_plan_overrides(template_id, normalized_rows)
    return None


# ── Instance lifecycle (Phase 5) ──────────────────────────────────────────────

_policy_builder = PolicyBuilder()


def can_manage_instance(inst, actor_user_id: str | None) -> bool:
    """Owner, assignee, org managers (sandbox.instances.create), and platform staff may act on an instance."""
    if not actor_user_id:
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
        return has_org_permission(str(inst.workspace_org_id), user, "sandbox.instances.create")
    return False


def can_view_instance(instance_id: str, actor_user_id: str | None) -> bool:
    inst = repository.get_instance(instance_id)
    if inst is None or not actor_user_id:
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
            has_org_permission(org_id, user, "sandbox.instances.view_all")
            or has_org_permission(org_id, user, "sandbox.instances.create")
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
    if not can_manage_instance(inst, actor_user_id):
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
    if allowed_file_types:
        filename = str(getattr(file_storage, "filename", "") or "")
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in allowed_file_types:
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


def _ui_workflow_choices(graph: dict, node: dict | None, instance: dict) -> list[dict]:
    """Outgoing-edge choices from the current node, filtered by condition —
    success/failure only appear once the instance's real exit_code makes the
    outcome known; manual/always are always offered."""
    if not node:
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
    if template is not None and template_interface_behavior(template) == "workflow":
        graph = template_ui_workflow_graph(template)
        ui_workflow_node = ui_workflow_node_by_id(graph, requested_node_id) or ui_workflow_start_node(graph)
        ui_mode = normalize_ui_mode((ui_workflow_node or {}).get("ui_mode"), "terminal_only")
        allowed_ui_modes = ui_workflow_node_ui_modes(graph) or [ui_mode]
        ui_workflow_choices = _ui_workflow_choices(graph, ui_workflow_node, instance)
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


# Admin test launches are a no-billing preview run — they don't need a real
# SandboxPlan to exist, so a minimal fixed resource envelope stands in for one.
_TEST_PLAN = {
    "id": "__test__", "name": "Test",
    "ind_vcpu": 1, "ind_ram_gb": 1, "ind_disk_gb": 5, "ind_cost_hr": "0",
    "org_vcpu": 1, "org_ram_gb": 1, "org_disk_gb": 5, "org_cost_hr": "0",
    "min_billable_minutes": 0,
    "allowed_network_modes": '["disabled","restricted","full_internet"]',
    "is_active": True,
}


def start_instance(
    instance_id: str,
    actor_user_id: str | None = None,
) -> tuple[dict | None, str | None]:
    """Build runtime policy, enqueue start job, transition idle→provisioning."""
    from .queue import enqueue_job

    inst = repository.get_instance(instance_id)
    if inst is None:
        return None, "Instance not found."
    if not can_manage_instance(inst, actor_user_id):
        return None, "You do not have permission to start this instance."
    if inst.status != "idle":
        return None, f"Cannot start an instance in '{inst.status}' state."
    if inst.workspace_type == "org" and inst.workspace_org_id:
        from codesandbox.features.organizations import repository as org_repo
        org = org_repo.get_organization(str(inst.workspace_org_id))
        if org is None or org.status != "active":
            return None, "Organization is not active."

    t = repository.get_template(str(inst.template_id))
    if not t:
        return None, "Template no longer exists."
    if inst.workspace_type != "test" and t.status != "active":
        return None, "Template is not active."
    if not _image_allowed(t.docker_image):
        return None, "Docker image is not in the configured runtime allowlist."

    if inst.workspace_type == "test":
        try:
            effective_plan = resolve_effective_plan(t, _TEST_PLAN, None)
        except RuntimePolicyError as exc:
            return None, str(exc)
    else:
        effective_plan, plan_error = get_effective_plan(str(t.id), inst.plan_id)
        if plan_error or effective_plan is None:
            return None, plan_error or "Plan is unavailable."

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
    except (RuntimePolicyError, UnsupportedRuntimeError, ValueError, TypeError) as exc:
        return None, str(exc)

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
    artifact_prefix = f"sandboxes/{instance_id}/artifacts"
    runtime_policy["artifact_prefix"] = artifact_prefix

    actor = f"user:{actor_user_id}" if actor_user_id else "system"
    settings = get_settings()
    callback_url = settings.control_plane_internal_url + "/internal/worker/callback"
    job_id = str(_uuid.uuid4())
    callback_token = make_worker_callback_token(job_id, instance_id, "start")

    tier = effective_plan.tier(workspace_type)

    from codesandbox.features.worker.service import (
        release_worker_capacity,
        reserve_worker_capacity,
        select_worker_for_instance,
    )

    worker_node = select_worker_for_instance(int(tier["vcpu"]), int(tier["ram_gb"]))
    if worker_node is None:
        return None, "No worker capacity is currently available. Try again shortly."
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

    minimum_required = (
        Decimal(str(tier["cost_hr"]))
        * Decimal(int(runtime_policy["min_billable_sec"]))
        / Decimal(3600)
    ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    now = datetime.now(timezone.utc)
    reserve_worker_capacity(worker_id, vcpu=int(tier["vcpu"]), ram_gb=int(tier["ram_gb"]))
    inst, begin_error = repository.begin_instance_start(
        instance_id,
        actor=actor,
        worker_id=worker_id,
        worker_job_id=job_id,
        runtime_policy=json.dumps(runtime_policy, separators=(",", ":")),
        runtime_provider=str(runtime_policy["runtime_provider"]),
        artifact_prefix=artifact_prefix,
        allocated_vcpu=int(tier["vcpu"]),
        allocated_ram_gb=int(tier["ram_gb"]),
        allocated_disk_gb=int(tier["disk_gb"]),
        effective_network_mode=str(runtime_policy["network_mode"]),
        cost_hr_snapshot=Decimal(str(tier["cost_hr"])),
        billing_currency=str(runtime_policy["currency"]),
        min_billable_sec=int(runtime_policy["min_billable_sec"]),
        expires_at=now + timedelta(seconds=int(runtime_policy["max_timeout_sec"])),
        minimum_required=minimum_required,
    )
    if begin_error or inst is None:
        release_worker_capacity(worker_id, vcpu=int(tier["vcpu"]), ram_gb=int(tier["ram_gb"]))
        return None, begin_error or "Could not start instance."
    try:
        enqueue_job(job_payload)
    except Exception:
        release_worker_capacity(worker_id, vcpu=int(tier["vcpu"]), ram_gb=int(tier["ram_gb"]))
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


def _mark_template_test_result(inst, status: str, reason: str | None = None) -> None:
    """If this instance was a Test Launch, record the pass/fail outcome on its template."""
    if inst.workspace_type != "test":
        return
    repository.update_template(
        str(inst.template_id),
        last_test_status=status,
        last_tested_at=datetime.now(timezone.utc),
        last_test_error=reason if status == "failed" else None,
    )


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

    release_worker_capacity(
        inst.worker_id,
        vcpu=int(inst.allocated_vcpu or 0),
        ram_gb=int(inst.allocated_ram_gb or 0),
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
        if inst.workspace_type == "test":
            # terminal_only/lab_ui: a real container starting, reachable
            # through the same terminal/filesystem code path production
            # instances use, is the honest pass signal for those modes.
            # background_run/desktop_gui/android_ui need more than "it
            # started" (see docs/plan.md Phase 10.4) — those wait for the
            # worker's own success_condition evaluation at finish time
            # (_evaluate_test_success on the worker, "test_success" in the
            # stopped/failed callback below).
            test_template = repository.get_template(str(inst.template_id))
            ui_mode = template_default_ui_mode(test_template) if test_template else "terminal_only"
            if ui_mode in ("terminal_only", "lab_ui"):
                _mark_template_test_result(inst, "passed")
        _upsert_worker_runtime(updated, status="running")

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
            reserve_seconds = max(int(inst.min_billable_sec or 0), elapsed + 60)
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
        _release_worker_capacity_for(updated)
        _upsert_worker_runtime(updated, status=final_status)
        repository.log_instance_event(instance_id, final_status, actor="worker", detail=detail)
        if inst.workspace_type == "test":
            if "test_success" in data:
                # Worker evaluated a configured success_condition against the
                # real exit code/artifacts/logs — trust it over the generic
                # "did it stop cleanly" heuristic below.
                _mark_template_test_result(
                    inst,
                    "passed" if data.get("test_success") else "failed",
                    reason=data.get("test_reason"),
                )
            elif final_status != "stopped":
                _mark_template_test_result(inst, "failed", reason=str(data.get("reason") or final_status))

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
        _release_worker_capacity_for(updated)
        _upsert_worker_runtime(updated, status="failed")
        repository.log_instance_event(instance_id, "failed", actor="worker", detail=detail)
        _mark_template_test_result(
            inst, "failed", reason=str(data.get("reason") or data.get("error") or "runtime_failed")
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

    else:
        return {}, f"Unknown event: {event}"

    return response, None


# ── Admin Test Run ────────────────────────────────────────────────────────────

def start_test_instance(
    template_id: str,
    actor_user_id: str,
    test_input_file=None,
) -> tuple[dict | None, str | None]:
    """Admin: create + immediately start a single-use test instance of a template.

    No-billing preview run — doesn't require a SandboxPlan to exist (see _TEST_PLAN).
    """
    t = repository.get_template(template_id)
    if t is None:
        return None, "Template not found."

    runtime_config = parse_runtime_config(t.runtime_config)
    test_config = runtime_config.get("test_config")
    test_config = test_config if isinstance(test_config, dict) else {}
    if test_config.get("requires_input") and test_input_file is None:
        return None, "This template's test config requires an input file — upload one to run the test."

    inst = repository.create_instance(
        template_id=template_id,
        plan_id=_TEST_PLAN["id"],
        workspace_type="test",
        workspace_user_id=actor_user_id,
        created_by_user_id=actor_user_id,
        billing_entity="test",
    )
    if test_input_file is not None:
        _, upload_error = upload_instance_input(str(inst.id), actor_user_id, test_input_file)
        if upload_error:
            return None, upload_error
    return start_instance(str(inst.id), actor_user_id=actor_user_id)


# ── Balance / Billing ─────────────────────────────────────────────────────────

def _balance_dict(b) -> dict:
    amount = Decimal(str(b.amount or "0"))
    reserved = Decimal(str(b.reserved_amount or "0"))
    return {
        "entity_type": b.entity_type,
        "entity_id": str(b.entity_id),
        "amount": str(amount),
        "reserved_amount": str(reserved),
        "available_amount": str(max(Decimal("0"), amount - reserved)),
        "updated_at": b.updated_at,
    }


def _transaction_dict(tx) -> dict:
    amount = Decimal(str(tx.amount or "0"))
    return {
        "id": str(tx.id),
        "type": tx.type,
        "amount": str(amount),
        "absolute_amount": str(abs(amount)),
        "is_credit": amount >= 0,
        "description": tx.description or "",
        "instance_id": str(tx.instance_id) if tx.instance_id else None,
        "created_at": tx.created_at,
    }


def get_user_billing(user_id: str) -> dict:
    b = repository.get_or_create_balance("user", user_id)
    txs = repository.list_transactions("user", user_id)
    return {
        "balance": _balance_dict(b),
        "transactions": [_transaction_dict(t) for t in txs],
    }


def get_org_billing(org_id: str) -> dict:
    b = repository.get_or_create_balance("org", org_id)
    txs = repository.list_transactions("org", org_id)
    return {
        "balance": _balance_dict(b),
        "transactions": [_transaction_dict(t) for t in txs],
    }
