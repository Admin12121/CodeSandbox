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


def get_hub_templates() -> list[dict]:
    templates, _ = repository.list_templates(status="active", page=1, page_size=200)
    return [_template_dict(t) for t in templates]


def get_hub_template_by_slug(slug: str) -> dict | None:
    t = repository.get_template_by_slug(slug)
    if t is None or t.status != "active":
        return None
    return _template_dict(t)


def get_hub_plans() -> list[dict]:
    return [p for p in (get_platform_plans()) if p["is_active"]]


# ── SandboxInstance ───────────────────────────────────────────────────────────

def _instance_dict(inst, template_cache: dict | None = None) -> dict:
    tid = str(inst.template_id)
    t = (template_cache or {}).get(tid) or repository.get_template(tid)
    return {
        "id": str(inst.id),
        "template_id": tid,
        "template_name": t.name if t else "Unknown",
        "template_slug": t.slug if t else "",
        "template_icon": t.icon_path or "" if t else "",
        "plan_id": inst.plan_id,
        "workspace_type": inst.workspace_type,
        "workspace_user_id": str(inst.workspace_user_id) if inst.workspace_user_id else None,
        "workspace_org_id": str(inst.workspace_org_id) if inst.workspace_org_id else None,
        "assigned_to_user_id": str(inst.assigned_to_user_id) if inst.assigned_to_user_id else None,
        "status": inst.status,
        "billing_entity": inst.billing_entity,
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
    t = repository.get_template_by_slug(template_slug)
    if not t or t.status != "active":
        return None, "Template not found or inactive."
    p = repository.get_plan(plan_id)
    if not p or not p.is_active:
        return None, "Plan not found or inactive."
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
    t = repository.get_template_by_slug(template_slug)
    if not t or t.status != "active":
        return None, "Template not found or inactive."
    p = repository.get_plan(plan_id)
    if not p or not p.is_active:
        return None, "Plan not found or inactive."
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
    t = repository.get_template_by_slug(template_slug)
    if not t or t.status != "active":
        return None, "Template not found or inactive."
    p = repository.get_plan(plan_id)
    if not p or not p.is_active:
        return None, "Plan not found or inactive."
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
    reviewer_id: str,
    action: str,
    review_note: str | None = None,
) -> tuple[dict | None, str | None]:
    from datetime import datetime, timezone
    req = repository.get_instance_request(request_id)
    if not req:
        return None, "Request not found."
    if req.status != "pending":
        return None, "Request already reviewed."
    if action not in ("approved", "denied"):
        return None, "Invalid action."

    instance_id = None
    if action == "approved":
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


# ── Balance / Billing ─────────────────────────────────────────────────────────

def _balance_dict(b) -> dict:
    return {
        "entity_type": b.entity_type,
        "entity_id": str(b.entity_id),
        "amount": str(b.amount),
        "updated_at": b.updated_at,
    }


def _transaction_dict(tx) -> dict:
    return {
        "id": str(tx.id),
        "type": tx.type,
        "amount": str(tx.amount),
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
