from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from . import repository


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")[:80] or "template"


def _parse_decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


# ── SandboxTemplate ───────────────────────────────────────────────────────────

SANDBOX_TYPES = ("interactive", "malware", "reverse_engineering", "android", "ctf")
RUNTIME_CLASSES = ("container", "microvm", "fullvm", "android_emulator")
INTERFACE_MODES = ("terminal", "full_ui", "background", "android_ui")
NETWORK_MODES = ("disabled", "isolated", "fake_internet", "controlled_proxy", "allowlist")
TEMPLATE_STATUSES = ("active", "maintenance", "disabled")


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


def _template_dict(t) -> dict:
    return {
        "id": str(t.id),
        "slug": t.slug,
        "name": t.name,
        "description": t.description or "",
        "icon_path": t.icon_path or "",
        "docker_image": t.docker_image,
        "sandbox_type": t.sandbox_type,
        "runtime_class": t.runtime_class,
        "interface_mode": t.interface_mode,
        "network_mode": t.network_mode,
        "allow_root": bool(t.allow_root),
        "max_timeout_hr": int(t.max_timeout_hr),
        "status": t.status,
        "type_config": t.type_config or "",
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
    type_config: str,
    created_by_id: str | None,
    slug: str = "",
    runtime_class: str = "container",
    interface_mode: str = "terminal",
    network_mode: str = "disabled",
    allow_root: bool = False,
    max_timeout_hr: int = 2,
) -> tuple[dict | None, str | None]:
    name = name.strip()
    if not name:
        return None, "Name is required."
    slug = (slug.strip() or _slugify(name))
    if sandbox_type not in SANDBOX_TYPES:
        return None, "Invalid sandbox type."
    if not docker_image.strip():
        return None, "Docker image is required."
    runtime_class = runtime_class if runtime_class in RUNTIME_CLASSES else "container"
    _modes = [m.strip() for m in interface_mode.split(",") if m.strip() in INTERFACE_MODES]
    interface_mode = ",".join(_modes) if _modes else "terminal"
    network_mode = network_mode if network_mode in NETWORK_MODES else "disabled"
    max_timeout_hr = max(1, min(72, int(max_timeout_hr or 2)))

    if template_id:
        t = repository.update_template(
            template_id,
            name=name, slug=slug, description=description or None,
            icon_path=icon_path or None, docker_image=docker_image.strip(),
            sandbox_type=sandbox_type, runtime_class=runtime_class,
            interface_mode=interface_mode, network_mode=network_mode,
            allow_root=allow_root, max_timeout_hr=max_timeout_hr,
            type_config=type_config.strip() or None,
        )
    else:
        existing = repository.get_template_by_slug(slug)
        if existing:
            return None, f"Slug '{slug}' is already taken."
        t = repository.create_template(
            name=name, slug=slug, description=description or None,
            icon_path=icon_path or None, docker_image=docker_image.strip(),
            sandbox_type=sandbox_type, runtime_class=runtime_class,
            interface_mode=interface_mode, network_mode=network_mode,
            allow_root=allow_root, max_timeout_hr=max_timeout_hr,
            type_config=type_config.strip() or None,
            created_by_id=created_by_id,
        )
    return _template_dict(t), None


def set_template_status(template_id: str, status: str) -> str | None:
    if status not in TEMPLATE_STATUSES:
        return "Invalid status."
    repository.update_template(template_id, status=status)
    return None


def save_template_config(template_id: str, config_json: str) -> None:
    repository.update_template(template_id, type_config=config_json.strip() or None)


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
        "org_vcpu": int(p.org_vcpu), "org_ram_gb": int(p.org_ram_gb), "org_disk_gb": int(p.org_disk_gb),
        "org_cost_hr": str(p.org_cost_hr),
        "is_active": bool(p.is_active),
        "updated_at": p.updated_at,
    }


def save_plan(
    plan_id: str,
    name: str,
    sort_order: int,
    ind_vcpu: int, ind_ram_gb: int, ind_disk_gb: int, ind_cost_hr: str,
    org_vcpu: int, org_ram_gb: int, org_disk_gb: int, org_cost_hr: str,
    updated_by_id: str | None,
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

    existing = repository.get_plan(plan_id)
    if existing:
        p = repository.update_plan(
            plan_id,
            name=name, sort_order=sort_order,
            ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost,
            org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost,
            updated_by=updated_by_id,
        )
    else:
        p = repository.create_plan(
            plan_id=plan_id, name=name, sort_order=sort_order,
            ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost,
            org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost,
            updated_by_id=updated_by_id,
        )
    return _plan_dict(p), None


def toggle_plan_active(plan_id: str, is_active: bool) -> None:
    repository.update_plan(plan_id, is_active=is_active)
