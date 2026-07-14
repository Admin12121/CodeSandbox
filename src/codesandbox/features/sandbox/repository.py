from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from nexorm import transaction
from nexorm.database import default_db
from nexorm.exceptions import IntegrityError

from .models import (
    Balance,
    BalanceTransaction,
    InstanceRequest,
    SandboxAuditLog,
    SandboxArtifact,
    SandboxInput,
    SandboxInstanceNote,
    SandboxInstance,
    SandboxPlan,
    SandboxTemplate,
    SandboxTemplatePlan,
)


class InsufficientBalanceError(ValueError):
    pass


def _select_for_update(model, table: str, **where):
    dialect = default_db.dialect
    quoted_table = dialect.quote_identifier(table)
    clauses = []
    values = []
    for name, value in where.items():
        clauses.append(f"{dialect.quote_identifier(name)} = {dialect.placeholder}")
        values.append(value)
    suffix = "" if default_db.backend == "sqlite" else " FOR UPDATE"
    row = default_db.fetchone(
        f"SELECT * FROM {quoted_table} WHERE {' AND '.join(clauses)}{suffix}",
        values,
    )
    return model.from_row(row, db=default_db) if row else None


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
    allowed_ui_modes: str | None,
    default_ui_mode: str,
    network_mode: str,
    allow_root: bool,
    max_timeout_hr: int,
    runtime_config: str | None,
    created_by_id: str | None,
    status: str = "maintenance",
    default_command: str | None = None,
    working_dir: str = "/workspace",
    input_mount_path: str = "",
    output_mount_path: str = "",
    artifact_paths: str | None = None,
    input_required: bool = False,
    max_upload_mb: int = 50,
    read_only_root: bool = True,
    run_as_user: str | None = None,
    pids_limit: int = 256,
    allow_full_internet: bool = False,
    interface_behavior: str = "single",
    ui_workflow_json: str | None = None,
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
        default_command=default_command,
        working_dir=working_dir,
        input_mount_path=input_mount_path,
        output_mount_path=output_mount_path,
        artifact_paths=artifact_paths,
        input_required=input_required,
        max_upload_mb=max_upload_mb,
        sandbox_type=sandbox_type,
        runtime_class=runtime_class,
        interface_mode=interface_mode,
        allowed_ui_modes=allowed_ui_modes,
        default_ui_mode=default_ui_mode,
        interface_behavior=interface_behavior,
        ui_workflow_json=ui_workflow_json,
        network_mode=network_mode,
        allow_root=allow_root,
        read_only_root=read_only_root,
        run_as_user=run_as_user,
        pids_limit=pids_limit,
        allow_full_internet=allow_full_internet,
        max_timeout_hr=max_timeout_hr,
        runtime_config=runtime_config or None,
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
    blocked = [i for i in active if i.status in _LIVE_INSTANCE_STATUSES]
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
        if i.status in _LIVE_INSTANCE_STATUSES and i.workspace_type != "test"
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
    min_billable_minutes: int = 1,
    allowed_network_modes: str = '["disabled","restricted"]',
) -> SandboxPlan:
    p = SandboxPlan(
        id=plan_id,
        name=name,
        sort_order=sort_order,
        ind_vcpu=ind_vcpu, ind_ram_gb=ind_ram_gb, ind_disk_gb=ind_disk_gb, ind_cost_hr=ind_cost_hr,
        org_vcpu=org_vcpu, org_ram_gb=org_ram_gb, org_disk_gb=org_disk_gb, org_cost_hr=org_cost_hr,
        min_billable_minutes=min_billable_minutes,
        allowed_network_modes=allowed_network_modes,
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


def delete_plan(plan_id: str) -> str | None:
    """Delete an unused sandbox plan. Returns an error string when blocked."""
    p = get_plan(plan_id)
    if p is None:
        return "Plan not found."

    linked_templates = SandboxTemplatePlan.objects.filter(plan_id=plan_id).all()
    if linked_templates:
        return f"Cannot delete: {len(linked_templates)} template plan mapping(s) still use this plan."

    instances = SandboxInstance.objects.filter(plan_id=plan_id).all()
    if instances:
        return f"Cannot delete: {len(instances)} sandbox instance(s) reference this plan."

    requests = InstanceRequest.objects.filter(plan_id=plan_id).all()
    if requests:
        return f"Cannot delete: {len(requests)} sandbox request(s) reference this plan."

    p.delete()
    return None


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
    instance_id: str | None = None,
) -> SandboxInstance:
    now = _now()
    inst = SandboxInstance(
        id=instance_id or str(uuid.uuid4()),
        template_id=template_id,
        plan_id=plan_id,
        workspace_type=workspace_type,
        workspace_user_id=workspace_user_id,
        workspace_org_id=workspace_org_id,
        assigned_to_user_id=assigned_to_user_id,
        created_by_user_id=created_by_user_id,
        status="idle",
        status_changed_at=now,
        billing_entity=billing_entity,
        billed_user_id=billed_user_id,
        billed_org_id=billed_org_id,
        user_config=user_config,
        created_at=now,
    )
    inst.save()
    return inst


def get_instance(instance_id: str) -> SandboxInstance | None:
    return SandboxInstance.objects.filter(id=instance_id).first()


def get_instance_for_update(instance_id: str) -> SandboxInstance | None:
    return _select_for_update(SandboxInstance, "sandbox_instances", id=instance_id)


def list_instances_for_user(user_id: str) -> list[SandboxInstance]:
    return SandboxInstance.objects.filter(workspace_user_id=user_id, workspace_type="personal").all()


def list_instances_for_org(org_id: str) -> list[SandboxInstance]:
    return SandboxInstance.objects.filter(workspace_org_id=org_id, workspace_type="org").all()


def list_instances_assigned_to_user_in_org(user_id: str, org_id: str) -> list[SandboxInstance]:
    all_org = SandboxInstance.objects.filter(workspace_org_id=org_id, workspace_type="org").all()
    return [i for i in all_org if str(i.assigned_to_user_id) == str(user_id)]


_LIVE_INSTANCE_STATUSES = ("idle", "provisioning", "running", "stopping", "cleanup")


def find_active_test_instance(
    template_id: str,
    *,
    actor_user_id: str | None = None,
) -> SandboxInstance | None:
    """Return the newest non-terminal Test Launch for this template.

    Test state is intentionally represented by the real SandboxInstance row,
    rather than browser memory. This lets the Config page and the dedicated
    test tab recover the same run after navigation or refresh.
    """
    rows = SandboxInstance.objects.filter(
        template_id=template_id, workspace_type="test"
    ).all()
    candidates = [row for row in rows if row.status in _LIVE_INSTANCE_STATUSES]
    if actor_user_id is not None:
        candidates = [
            row for row in candidates
            if str(row.created_by_user_id or "") == str(actor_user_id)
        ]
    return max(candidates, key=lambda row: row.created_at or _now(), default=None)


def update_instance_user_config(instance_id: str, user_config: str | None) -> SandboxInstance | None:
    inst = get_instance(instance_id)
    if inst is None:
        return None
    inst.user_config = user_config
    inst.save()
    return inst


def find_hub_instance(
    template_id: str,
    plan_id: str,
    *,
    user_id: str,
    org_id: str | None = None,
) -> SandboxInstance | None:
    """Most recent non-terminal instance for this user + template + plan.

    Personal workspace: instance owned by the user. Org workspace: instance
    assigned to the user within that org (the pool instance a manager handed
    them, or one they created for themselves if they have create rights).
    """
    if org_id:
        candidates = [
            i for i in list_instances_for_org(org_id)
            if str(i.assigned_to_user_id) == str(user_id)
        ]
    else:
        candidates = list_instances_for_user(user_id)

    live = [
        i for i in candidates
        if str(i.template_id) == str(template_id)
        and i.plan_id == plan_id
        and i.status in _LIVE_INSTANCE_STATUSES
    ]
    if not live:
        return None
    return max(live, key=lambda i: i.created_at)


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
    if entity_type not in {"user", "org"} or not entity_id:
        raise ValueError("Invalid billing entity.")
    with transaction.atomic():
        balance = _select_for_update(
            Balance,
            "balances",
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if balance is not None:
            return balance
        balance = Balance(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=entity_id,
            amount=Decimal("0.00"),
            reserved_amount=Decimal("0.00"),
            updated_at=_now(),
        )
        try:
            balance.save()
        except IntegrityError:
            balance = _select_for_update(
                Balance,
                "balances",
                entity_type=entity_type,
                entity_id=entity_id,
            )
            if balance is None:
                raise
        return balance


def get_balance_for_update(entity_type: str, entity_id: str) -> Balance:
    balance = _select_for_update(
        Balance,
        "balances",
        entity_type=entity_type,
        entity_id=entity_id,
    )
    if balance is not None:
        return balance
    return get_or_create_balance(entity_type, entity_id)


def available_balance(balance: Balance) -> Decimal:
    return Decimal(str(balance.amount or "0")) - Decimal(
        str(balance.reserved_amount or "0")
    )


def add_balance_transaction(
    entity_type: str,
    entity_id: str,
    tx_type: str,
    amount: Decimal,
    description: str | None = None,
    instance_id: str | None = None,
    topup_intent_id: str | None = None,
    provider: str | None = None,
    reference: str | None = None,
    idempotency_key: str | None = None,
) -> BalanceTransaction:
    signed_amount = Decimal(str(amount))
    if tx_type == "deduction":
        tx_type = "usage_charge"
        signed_amount = -abs(signed_amount)
    elif tx_type in {"usage_charge", "failed_payment"}:
        signed_amount = -abs(signed_amount)
    elif tx_type in {"topup", "refund"}:
        signed_amount = abs(signed_amount)

    with transaction.atomic():
        if idempotency_key:
            existing = BalanceTransaction.objects.filter(
                idempotency_key=idempotency_key
            ).first()
            if existing is not None:
                return existing
        balance = get_balance_for_update(entity_type, entity_id)
        next_amount = Decimal(str(balance.amount or "0")) + signed_amount
        if next_amount < 0:
            raise InsufficientBalanceError("Balance cannot become negative.")
        if signed_amount < 0 and next_amount < Decimal(
            str(balance.reserved_amount or "0")
        ):
            raise InsufficientBalanceError("Funds are reserved by running sandboxes.")
        balance.amount = next_amount
        balance.updated_at = _now()
        balance.save()
        tx = BalanceTransaction(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=entity_id,
            type=tx_type,
            amount=signed_amount,
            instance_id=instance_id,
            topup_intent_id=topup_intent_id,
            provider=provider,
            reference=reference,
            idempotency_key=idempotency_key,
            description=description,
            created_at=_now(),
        )
        tx.save()
        return tx


def reserve_instance_balance(instance_id: str, required_amount: Decimal) -> bool:
    required = max(Decimal("0"), Decimal(str(required_amount)))
    with transaction.atomic():
        inst = get_instance_for_update(instance_id)
        if inst is None or inst.billing_entity not in {"user", "org"}:
            return inst is not None and inst.billing_entity == "test"
        entity_id = (
            str(inst.billed_org_id)
            if inst.billing_entity == "org" and inst.billed_org_id
            else str(inst.billed_user_id or "")
        )
        if not entity_id:
            return False
        balance = get_balance_for_update(inst.billing_entity, entity_id)
        current_reservation = Decimal(str(inst.billing_reserved_amount or "0"))
        delta = max(Decimal("0"), required - current_reservation)
        if available_balance(balance) < delta:
            return False
        balance.reserved_amount = Decimal(str(balance.reserved_amount or "0")) + delta
        balance.updated_at = _now()
        balance.save()
        inst.billing_reserved_amount = current_reservation + delta
        inst.save()
        return True


def begin_instance_start(
    instance_id: str,
    *,
    actor: str,
    worker_id: str,
    worker_job_id: str,
    runtime_policy: str,
    runtime_provider: str,
    artifact_prefix: str,
    allocated_vcpu: int,
    allocated_ram_gb: int,
    allocated_disk_gb: int,
    effective_network_mode: str,
    cost_hr_snapshot: Decimal,
    billing_currency: str,
    min_billable_sec: int,
    expires_at: datetime,
    minimum_required: Decimal,
) -> tuple[SandboxInstance | None, str | None]:
    with transaction.atomic():
        inst = get_instance_for_update(instance_id)
        if inst is None:
            return None, "Instance not found."
        if inst.status != "idle":
            return None, f"Cannot start an instance in '{inst.status}' state."

        reserved = Decimal("0")
        if inst.billing_entity in {"user", "org"}:
            entity_id = (
                str(inst.billed_org_id)
                if inst.billing_entity == "org" and inst.billed_org_id
                else str(inst.billed_user_id or "")
            )
            if not entity_id:
                return None, "Billing account is missing."
            balance = get_balance_for_update(inst.billing_entity, entity_id)
            required = max(Decimal("0"), Decimal(str(minimum_required)))
            if available_balance(balance) < required:
                return None, "Insufficient balance for the minimum billable runtime."
            balance.reserved_amount = Decimal(str(balance.reserved_amount or "0")) + required
            balance.updated_at = _now()
            balance.save()
            reserved = required

        old_status = inst.status
        inst.status = "provisioning"
        inst.status_changed_at = _now()
        inst.worker_id = worker_id
        inst.worker_job_id = worker_job_id
        inst.runtime_policy = runtime_policy
        inst.runtime_provider = runtime_provider
        inst.artifact_prefix = artifact_prefix
        inst.allocated_vcpu = allocated_vcpu
        inst.allocated_ram_gb = allocated_ram_gb
        inst.allocated_disk_gb = allocated_disk_gb
        inst.effective_network_mode = effective_network_mode
        inst.cost_hr_snapshot = cost_hr_snapshot
        inst.billing_currency = billing_currency
        inst.billing_status = "metering" if inst.billing_entity != "test" else "not_charged"
        inst.billing_reserved_amount = reserved
        inst.min_billable_sec = min_billable_sec
        inst.expires_at = expires_at
        inst.last_heartbeat_at = _now()
        inst.save()
        log_instance_event(
            instance_id=instance_id,
            event="status:idle->provisioning",
            old_status=old_status,
            new_status="provisioning",
            actor=actor,
        )
        return inst, None


def release_instance_reservation(instance_id: str) -> None:
    with transaction.atomic():
        inst = get_instance_for_update(instance_id)
        if inst is None:
            return
        reserved = Decimal(str(inst.billing_reserved_amount or "0"))
        if reserved <= 0 or inst.billing_entity not in {"user", "org"}:
            inst.billing_reserved_amount = Decimal("0")
            inst.save()
            return
        entity_id = (
            str(inst.billed_org_id)
            if inst.billing_entity == "org" and inst.billed_org_id
            else str(inst.billed_user_id or "")
        )
        if entity_id:
            balance = get_balance_for_update(inst.billing_entity, entity_id)
            balance.reserved_amount = max(
                Decimal("0"),
                Decimal(str(balance.reserved_amount or "0")) - reserved,
            )
            balance.updated_at = _now()
            balance.save()
        inst.billing_reserved_amount = Decimal("0")
        inst.save()


def charge_instance_balance(
    instance_id: str,
    amount: Decimal,
    description: str,
) -> tuple[BalanceTransaction | None, Decimal, str]:
    due = max(Decimal("0"), Decimal(str(amount)))
    idempotency_key = f"usage:{instance_id}"
    with transaction.atomic():
        existing = BalanceTransaction.objects.filter(
            idempotency_key=idempotency_key
        ).first()
        inst = get_instance_for_update(instance_id)
        if inst is None:
            return existing, Decimal("0"), "missing"
        if existing is not None:
            return existing, abs(Decimal(str(existing.amount))), inst.billing_status
        if inst.billing_entity == "test" or due == 0:
            inst.billing_status = "not_charged" if inst.billing_entity == "test" else "charged"
            inst.charged_amount = Decimal("0")
            inst.billing_reserved_amount = Decimal("0")
            inst.save()
            return None, Decimal("0"), inst.billing_status

        entity_id = (
            str(inst.billed_org_id)
            if inst.billing_entity == "org" and inst.billed_org_id
            else str(inst.billed_user_id or "")
        )
        if not entity_id:
            inst.billing_status = "failed"
            inst.save()
            return None, Decimal("0"), "failed"

        balance = get_balance_for_update(inst.billing_entity, entity_id)
        instance_reserved = Decimal(str(inst.billing_reserved_amount or "0"))
        total_reserved = Decimal(str(balance.reserved_amount or "0"))
        other_reserved = max(Decimal("0"), total_reserved - instance_reserved)
        spendable = max(Decimal("0"), Decimal(str(balance.amount or "0")) - other_reserved)
        charged = min(due, spendable)

        balance.reserved_amount = other_reserved
        balance.amount = Decimal(str(balance.amount or "0")) - charged
        balance.updated_at = _now()
        balance.save()

        status = "charged" if charged == due else "partial"
        inst.billing_reserved_amount = Decimal("0")
        inst.charged_amount = charged
        inst.billing_status = status
        inst.save()

        tx = None
        if charged > 0:
            tx = BalanceTransaction(
                id=str(uuid.uuid4()),
                entity_type=inst.billing_entity,
                entity_id=entity_id,
                type="usage_charge",
                amount=-charged,
                instance_id=instance_id,
                provider="sandbox",
                reference=instance_id,
                idempotency_key=idempotency_key,
                description=description,
                created_at=_now(),
            )
            tx.save()
        return tx, charged, status


def list_transactions(entity_type: str, entity_id: str, limit: int = 50) -> list:
    txs = BalanceTransaction.objects.filter(entity_type=entity_type, entity_id=entity_id).all()
    return sorted(txs, key=lambda t: t.created_at, reverse=True)[:limit]


def list_transactions_paginated(
    entity_type: str,
    entity_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[BalanceTransaction], int, int, int]:
    txs = BalanceTransaction.objects.filter(entity_type=entity_type, entity_id=entity_id).all()
    ordered = sorted(txs, key=lambda t: t.created_at, reverse=True)
    total = len(ordered)
    page_size = max(1, min(int(page_size), 100))
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * page_size
    return ordered[start:start + page_size], total, page, total_pages


# ── SandboxAuditLog ───────────────────────────────────────────────────────────

def log_instance_event(
    instance_id: str | None,
    event: str,
    old_status: str | None = None,
    new_status: str | None = None,
    actor: str | None = None,
    detail: str | None = None,
    template_id: str | None = None,
) -> SandboxAuditLog:
    entry = SandboxAuditLog(
        id=str(uuid.uuid4()),
        instance_id=instance_id,
        template_id=template_id,
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


def get_instance_note(instance_id: str) -> SandboxInstanceNote | None:
    return SandboxInstanceNote.objects.filter(instance_id=instance_id).first()


def upsert_instance_note(
    instance_id: str,
    *,
    title: str,
    content: str,
    updated_by: str | None = None,
) -> SandboxInstanceNote:
    note = get_instance_note(instance_id)
    now = _now()
    if note is None:
        note = SandboxInstanceNote(
            id=str(uuid.uuid4()),
            instance_id=instance_id,
            created_at=now,
        )
    note.title = title[:255] or "Untitled Investigation"
    note.content = content
    note.updated_by = updated_by
    note.updated_at = now
    note.save()
    return note


_ALLOWED_INSTANCE_TRANSITIONS = {
    "idle": {"provisioning", "failed"},
    "provisioning": {"running", "stopping", "cleanup", "failed", "expired"},
    "running": {"stopping", "cleanup", "failed", "expired", "killed"},
    "stopping": {"cleanup", "stopped", "failed", "expired", "killed"},
    "cleanup": {"stopped", "failed", "expired", "killed"},
    "stopped": set(),
    "failed": {"cleanup"},
    "expired": {"cleanup"},
    "killed": {"cleanup"},
}


def transition_instance_status(
    instance_id: str,
    new_status: str,
    actor: str | None = None,
    expected_statuses: tuple[str, ...] | list[str] | set[str] | None = None,
    **extra_fields,
) -> SandboxInstance | None:
    with transaction.atomic():
        inst = get_instance_for_update(instance_id)
        if inst is None:
            return None
        old_status = inst.status
        if expected_statuses is not None and old_status not in expected_statuses:
            return None
        if old_status != new_status and new_status not in _ALLOWED_INSTANCE_TRANSITIONS.get(
            old_status, set()
        ):
            return None
        if old_status != new_status:
            inst.status = new_status
            inst.status_changed_at = _now()
        for key, value in extra_fields.items():
            setattr(inst, key, value)
        inst.save()
        if old_status != new_status:
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
    with transaction.atomic():
        tp = _select_for_update(
            SandboxTemplatePlan,
            "sandbox_template_plans",
            template_id=template_id,
            plan_id=plan_id,
        )
        if tp is None:
            tp = SandboxTemplatePlan(
                id=str(uuid.uuid4()),
                template_id=template_id,
                plan_id=plan_id,
            )
        for key, value in kwargs.items():
            setattr(tp, key, value)
        tp.save()
        return tp


def delete_template_plan(template_id: str, plan_id: str) -> None:
    tp = get_template_plan(template_id, plan_id)
    if tp:
        tp.delete()


# Sandbox inputs and artifacts

def create_instance_input(
    instance_id: str,
    name: str,
    storage_key: str,
    size_bytes: int,
    checksum: str,
) -> SandboxInput:
    item = SandboxInput(
        id=str(uuid.uuid4()),
        instance_id=instance_id,
        name=name,
        storage_key=storage_key,
        size_bytes=size_bytes,
        checksum=checksum,
        created_at=_now(),
    )
    item.save()
    return item


def list_instance_inputs(instance_id: str) -> list[SandboxInput]:
    return SandboxInput.objects.filter(instance_id=instance_id).order_by("created_at").all()


def create_or_get_artifact(
    instance_id: str,
    name: str,
    artifact_type: str,
    storage_key: str,
    size_bytes: int,
    checksum: str,
) -> SandboxArtifact:
    existing = SandboxArtifact.objects.filter(storage_key=storage_key).first()
    if existing is not None:
        return existing
    artifact = SandboxArtifact(
        id=str(uuid.uuid4()),
        instance_id=instance_id,
        name=name,
        artifact_type=artifact_type,
        storage_key=storage_key,
        size_bytes=size_bytes,
        checksum=checksum,
        created_at=_now(),
    )
    try:
        artifact.save()
    except IntegrityError:
        existing = SandboxArtifact.objects.filter(storage_key=storage_key).first()
        if existing is None:
            raise
        return existing
    return artifact


def get_artifact(artifact_id: str) -> SandboxArtifact | None:
    return SandboxArtifact.objects.filter(id=artifact_id).first()


def list_instance_artifacts(instance_id: str) -> list[SandboxArtifact]:
    return SandboxArtifact.objects.filter(instance_id=instance_id).order_by("created_at").all()


def list_live_instances() -> list[SandboxInstance]:
    rows = SandboxInstance.objects.all()
    return [row for row in rows if row.status in _LIVE_INSTANCE_STATUSES]


_RUNTIME_BACKED_STATUSES = ("provisioning", "running", "stopping", "cleanup")


def list_runtime_backed_instances_for_worker(worker_id: str) -> list[SandboxInstance]:
    """Instances this worker_id might still have a live container for —
    used at worker boot to rebuild the in-memory registry after a restart."""
    rows = SandboxInstance.objects.filter(worker_id=worker_id).all()
    return [row for row in rows if row.status in _RUNTIME_BACKED_STATUSES]
