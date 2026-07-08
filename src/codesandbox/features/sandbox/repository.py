from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from .models import (
    Balance,
    BalanceTransaction,
    InstanceRequest,
    SandboxAuditLog,
    SandboxInstance,
    SandboxPlan,
    SandboxTemplate,
    SandboxTemplatePlan,
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


def get_templates_by_ids(ids: list[str]) -> list[SandboxTemplate]:
    """Batch-fetch templates by a list of IDs (one scan, no N+1)."""
    if not ids:
        return []
    id_set = set(ids)
    return [t for t in SandboxTemplate.objects.all() if str(t.id) in id_set]


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
    status: str = "maintenance",
) -> SandboxTemplate:
    # New templates never start "active" — they have no plans configured yet, and an
    # active-but-planless template is broken for real end users hitting the Hub.
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
        status=status,
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
    pending_requests = InstanceRequest.objects.filter(template_id=template_id).all()
    pending = [r for r in pending_requests if r.status == "pending"]
    if pending:
        return f"Cannot delete: {len(pending)} pending request(s) reference this template."
    t = get_template(template_id)
    if t:
        t.delete()
    return None


def count_active_instances_for_template(template_id: str) -> int:
    instances = SandboxInstance.objects.filter(template_id=template_id).all()
    return sum(
        1 for i in instances
        if i.status in ("idle", "provisioning", "running", "stopping") and i.workspace_type != "test"
    )


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


# ── SandboxInstance ───────────────────────────────────────────────────────────

def create_instance(
    template_id: str,
    plan_id: str,
    workspace_type: str,
    created_by_user_id: str,
    billing_entity: str,
    workspace_user_id: str | None = None,
    workspace_org_id: str | None = None,
    assigned_to_user_id: str | None = None,
    billed_user_id: str | None = None,
    billed_org_id: str | None = None,
    user_config: str | None = None,
) -> SandboxInstance:
    inst = SandboxInstance(
        id=str(uuid.uuid4()),
        template_id=template_id,
        plan_id=plan_id,
        workspace_type=workspace_type,
        workspace_user_id=workspace_user_id,
        workspace_org_id=workspace_org_id,
        assigned_to_user_id=assigned_to_user_id,
        created_by_user_id=created_by_user_id,
        status="idle",
        billing_entity=billing_entity,
        billed_user_id=billed_user_id,
        billed_org_id=billed_org_id,
        user_config=user_config,
        created_at=_now(),
    )
    inst.save()
    return inst


def get_instance(instance_id: str) -> SandboxInstance | None:
    return SandboxInstance.objects.filter(id=instance_id).first()


def list_instances_for_user(user_id: str) -> list[SandboxInstance]:
    return SandboxInstance.objects.filter(workspace_user_id=user_id, workspace_type="personal").all()


def list_instances_for_org(org_id: str) -> list[SandboxInstance]:
    return SandboxInstance.objects.filter(workspace_org_id=org_id, workspace_type="org").all()


def list_instances_assigned_to_user_in_org(user_id: str, org_id: str) -> list[SandboxInstance]:
    all_org = SandboxInstance.objects.filter(workspace_org_id=org_id, workspace_type="org").all()
    return [i for i in all_org if str(i.assigned_to_user_id) == str(user_id)]


# ── InstanceRequest ───────────────────────────────────────────────────────────

def create_instance_request(
    org_id: str,
    requested_by: str,
    template_id: str,
    plan_id: str,
    note: str | None = None,
) -> InstanceRequest:
    req = InstanceRequest(
        id=str(uuid.uuid4()),
        org_id=org_id,
        requested_by=requested_by,
        template_id=template_id,
        plan_id=plan_id,
        note=note,
        status="pending",
        created_at=_now(),
    )
    req.save()
    return req


def get_instance_request(request_id: str) -> InstanceRequest | None:
    return InstanceRequest.objects.filter(id=request_id).first()


def list_requests_for_org(org_id: str, status: str | None = None) -> list[InstanceRequest]:
    reqs = InstanceRequest.objects.filter(org_id=org_id).all()
    if status:
        reqs = [r for r in reqs if r.status == status]
    return sorted(reqs, key=lambda r: r.created_at, reverse=True)


def list_requests_by_user_in_org(user_id: str, org_id: str) -> list[InstanceRequest]:
    reqs = InstanceRequest.objects.filter(org_id=org_id, requested_by=user_id).all()
    return sorted(reqs, key=lambda r: r.created_at, reverse=True)


def update_instance_request(request_id: str, **kwargs) -> InstanceRequest | None:
    req = get_instance_request(request_id)
    if req is None:
        return None
    for k, v in kwargs.items():
        setattr(req, k, v)
    req.save()
    return req


# ── Balance ───────────────────────────────────────────────────────────────────

def get_balance(entity_type: str, entity_id: str) -> Balance | None:
    return Balance.objects.filter(entity_type=entity_type, entity_id=entity_id).first()


def get_or_create_balance(entity_type: str, entity_id: str) -> Balance:
    rows = Balance.objects.filter(entity_type=entity_type, entity_id=entity_id).all()
    if rows:
        # Deterministic winner if duplicates exist from a prior race
        return sorted(rows, key=lambda b: b.id)[0]
    b = Balance(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        amount=Decimal("0.00"),
        updated_at=_now(),
    )
    b.save()
    # Re-fetch after insert: if two processes raced, take the smallest UUID
    all_rows = Balance.objects.filter(entity_type=entity_type, entity_id=entity_id).all()
    return sorted(all_rows, key=lambda b: b.id)[0]


def add_balance_transaction(
    entity_type: str,
    entity_id: str,
    tx_type: str,
    amount: Decimal,
    description: str | None = None,
    instance_id: str | None = None,
) -> BalanceTransaction:
    b = get_or_create_balance(entity_type, entity_id)
    current = Decimal(str(b.amount or "0"))
    if tx_type in ("topup", "refund"):
        b.amount = current + amount
    elif tx_type == "deduction":
        b.amount = current - amount
    b.updated_at = _now()
    b.save()
    tx = BalanceTransaction(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        type=tx_type,
        amount=amount,
        instance_id=instance_id,
        description=description,
        created_at=_now(),
    )
    tx.save()
    return tx


def list_transactions(entity_type: str, entity_id: str, limit: int = 50) -> list:
    txs = BalanceTransaction.objects.filter(entity_type=entity_type, entity_id=entity_id).all()
    return sorted(txs, key=lambda t: t.created_at, reverse=True)[:limit]


# ── SandboxAuditLog ───────────────────────────────────────────────────────────

def log_instance_event(
    instance_id: str,
    event: str,
    old_status: str | None = None,
    new_status: str | None = None,
    actor: str | None = None,
    detail: str | None = None,
) -> SandboxAuditLog:
    entry = SandboxAuditLog(
        id=str(uuid.uuid4()),
        instance_id=instance_id,
        event=event,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        detail=detail,
        created_at=_now(),
    )
    entry.save()
    return entry


def list_instance_audit_log(instance_id: str, limit: int = 50) -> list[SandboxAuditLog]:
    rows = SandboxAuditLog.objects.filter(instance_id=instance_id).all()
    return sorted(rows, key=lambda r: r.created_at, reverse=True)[:limit]


def transition_instance_status(
    instance_id: str,
    new_status: str,
    actor: str | None = None,
    **extra_fields,
) -> SandboxInstance | None:
    inst = get_instance(instance_id)
    if inst is None:
        return None
    old_status = inst.status
    inst.status = new_status
    for k, v in extra_fields.items():
        setattr(inst, k, v)
    inst.save()
    log_instance_event(
        instance_id=instance_id,
        event=f"status:{old_status}->{new_status}",
        old_status=old_status,
        new_status=new_status,
        actor=actor,
    )
    return inst


# ── SandboxTemplatePlan ───────────────────────────────────────────────────────

def list_template_plans(template_id: str) -> list[SandboxTemplatePlan]:
    return SandboxTemplatePlan.objects.filter(template_id=template_id).all()


def get_template_plan(template_id: str, plan_id: str) -> SandboxTemplatePlan | None:
    rows = SandboxTemplatePlan.objects.filter(template_id=template_id, plan_id=plan_id).all()
    return rows[0] if rows else None


def upsert_template_plan(template_id: str, plan_id: str, **kwargs) -> SandboxTemplatePlan:
    tp = get_template_plan(template_id, plan_id)
    if tp is None:
        tp = SandboxTemplatePlan(
            id=str(uuid.uuid4()),
            template_id=template_id,
            plan_id=plan_id,
        )
    for k, v in kwargs.items():
        setattr(tp, k, v)
    tp.save()
    return tp


def delete_template_plan(template_id: str, plan_id: str) -> None:
    tp = get_template_plan(template_id, plan_id)
    if tp:
        tp.delete()
