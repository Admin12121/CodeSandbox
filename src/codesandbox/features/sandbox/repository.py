from __future__ import annotations

import uuid
from datetime import datetime, timezone

from .models import (
    InstanceRequest,
    SandboxInstance,
    SandboxPlan,
    SandboxTemplate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── SandboxTemplate ───────────────────────────────────────────────────────────

def list_templates(
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[SandboxTemplate], int]:
    qs = SandboxTemplate.objects.all()
    if search:
        q = search.lower()
        qs = [t for t in qs if q in t.name.lower() or q in t.slug.lower()]
    if status:
        qs = [t for t in qs if t.status == status]
    total = len(qs)
    start = (page - 1) * page_size
    return qs[start : start + page_size], total


def get_template(template_id: str) -> SandboxTemplate | None:
    return SandboxTemplate.objects.filter(id=template_id).first()


def get_template_by_slug(slug: str) -> SandboxTemplate | None:
    return SandboxTemplate.objects.filter(slug=slug).first()


def create_template(
    name: str,
    slug: str,
    description: str | None,
    icon_path: str | None,
    docker_image: str,
    sandbox_type: str,
    runtime_class: str,
    interface_mode: str,
    network_mode: str,
    allow_root: bool,
    max_timeout_hr: int,
    type_config: str | None,
    created_by_id: str | None,
) -> SandboxTemplate:
    t = SandboxTemplate(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        description=description or None,
        icon_path=icon_path or None,
        docker_image=docker_image,
        sandbox_type=sandbox_type,
        runtime_class=runtime_class,
        interface_mode=interface_mode,
        network_mode=network_mode,
        allow_root=allow_root,
        max_timeout_hr=max_timeout_hr,
        type_config=type_config or None,
        created_by=created_by_id,
        created_at=_now(),
    )
    t.save()
    return t


def update_template(template_id: str, **kwargs) -> SandboxTemplate | None:
    t = get_template(template_id)
    if t is None:
        return None
    for k, v in kwargs.items():
        setattr(t, k, v)
    t.updated_at = _now()
    t.save()
    return t


def delete_template(template_id: str) -> str | None:
    """Returns error string if delete is blocked, else None."""
    active = SandboxInstance.objects.filter(template_id=template_id).all()
    blocked = [i for i in active if i.status in ("idle", "provisioning", "running", "stopping")]
    if blocked:
        return f"Cannot delete: {len(blocked)} active/idle instance(s) exist."
    t = get_template(template_id)
    if t:
        t.delete()
    return None


def count_active_instances_for_template(template_id: str) -> int:
    instances = SandboxInstance.objects.filter(template_id=template_id).all()
    return sum(1 for i in instances if i.status in ("idle", "provisioning", "running", "stopping"))


# ── SandboxPlan ───────────────────────────────────────────────────────────────

def list_plans() -> list[SandboxPlan]:
    plans = SandboxPlan.objects.all()
    return sorted(plans, key=lambda p: p.sort_order)


def get_plan(plan_id: str) -> SandboxPlan | None:
    return SandboxPlan.objects.filter(id=plan_id).first()


def create_plan(
    plan_id: str,
    name: str,
    sort_order: int,
    ind_vcpu: int, ind_ram_gb: int, ind_disk_gb: int, ind_cost_hr,
    org_vcpu: int, org_ram_gb: int, org_disk_gb: int, org_cost_hr,
    updated_by_id: str | None,
) -> SandboxPlan:
    p = SandboxPlan(
        id=plan_id,
        name=name,
        sort_order=sort_order,
        ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost_hr,
        org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost_hr,
        is_active=True,
        updated_by=updated_by_id,
        updated_at=_now(),
    )
    p.save()
    return p


def update_plan(plan_id: str, **kwargs) -> SandboxPlan | None:
    p = get_plan(plan_id)
    if p is None:
        return None
    for k, v in kwargs.items():
        setattr(p, k, v)
    p.updated_at = _now()
    p.save()
    return p
