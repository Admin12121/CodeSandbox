from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from nexorm.fields import (
    BooleanField,
    DateTimeField,
    DecimalField,
    ForeignKey,
    IntegerField,
    StringField,
    TextField,
)
from nexorm.model import Model

from codesandbox.features.identity.models import User
from codesandbox.features.organizations.models import Organization


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SandboxTemplate(Model):
    id = StringField(primary_key=True, max_length=36)
    slug = StringField(max_length=80, unique=True)
    name = StringField(max_length=120)
    description = TextField(nullable=True)
    icon_path = StringField(max_length=500, nullable=True)

    docker_image = StringField(max_length=500)

    # Classification — drives policy builder logic
    sandbox_type = StringField(max_length=40)   # interactive|malware|reverse_engineering|android|ctf
    runtime_class = StringField(max_length=40)  # container|microvm|fullvm|android_emulator

    # Interface / network defaults
    interface_mode = StringField(max_length=40, default="terminal")   # terminal|full_ui|background|android_ui
    network_mode = StringField(max_length=40, default="disabled")     # disabled|isolated|fake_internet|controlled_proxy|allowlist

    # Security defaults
    allow_root = BooleanField(default=True)
    max_timeout_hr = IntegerField(default=2)

    # Visibility
    status = StringField(max_length=20, default="active")  # active|maintenance|disabled

    # Type-specific config JSON — admin editable, worker reads
    type_config = TextField(nullable=True)

    created_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "sandbox_templates"


class SandboxPlan(Model):
    id = StringField(primary_key=True, max_length=40)   # basic|general|optimized|premium
    name = StringField(max_length=80)
    sort_order = IntegerField(default=0)

    # Individual user tier
    ind_vcpu = IntegerField(default=1)
    ind_ram_gb = IntegerField(default=1)
    ind_disk_gb = IntegerField(default=10)
    ind_cost_hr = DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))

    # Org tier
    org_vcpu = IntegerField(default=2)
    org_ram_gb = IntegerField(default=2)
    org_disk_gb = IntegerField(default=20)
    org_cost_hr = DecimalField(max_digits=10, decimal_places=4, default=Decimal("0.00"))

    is_active = BooleanField(default=True)
    updated_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "sandbox_plans"


class SandboxInstance(Model):
    id = StringField(primary_key=True, max_length=36)
    template_id = ForeignKey(to=SandboxTemplate, on_delete="RESTRICT")
    plan_id = StringField(max_length=40)   # FK to SandboxPlan stored as plain string

    # Workspace context
    workspace_type = StringField(max_length=20)          # personal|org
    workspace_user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="personal_instances")
    workspace_org_id = ForeignKey(to=Organization, on_delete="SET NULL", nullable=True, related_name="org_instances")
    assigned_to_user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="assigned_instances")

    created_by_user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="created_instances")

    # Status lifecycle
    status = StringField(max_length=20, default="idle")  # idle|provisioning|running|stopping|stopped|failed|killed

    # Worker tracking
    worker_id = StringField(max_length=255, nullable=True)
    worker_job_id = StringField(max_length=255, nullable=True)

    # Billing
    billing_entity = StringField(max_length=10)          # user|org
    billed_user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="billed_instances")
    billed_org_id = ForeignKey(to=Organization, on_delete="SET NULL", nullable=True, related_name="billed_org_instances")

    # Config snapshot — frozen at start time
    user_config = TextField(nullable=True)
    runtime_policy = TextField(nullable=True)

    # Timing + billing
    started_at = DateTimeField(nullable=True)
    stopped_at = DateTimeField(nullable=True)
    total_runtime_sec = IntegerField(default=0)

    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "sandbox_instances"


class InstanceRequest(Model):
    id = StringField(primary_key=True, max_length=36)
    org_id = ForeignKey(to=Organization, on_delete="CASCADE")
    requested_by = ForeignKey(to=User, on_delete="CASCADE", related_name="sandbox_requests")
    template_id = ForeignKey(to=SandboxTemplate, on_delete="RESTRICT")
    plan_id = StringField(max_length=40)
    requested_config = TextField(nullable=True)
    note = TextField(nullable=True)

    status = StringField(max_length=20, default="pending")  # pending|approved|denied
    reviewed_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="reviewed_requests")
    reviewed_at = DateTimeField(nullable=True)
    review_note = TextField(nullable=True)

    instance_id = ForeignKey(to=SandboxInstance, on_delete="SET NULL", nullable=True)

    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "instance_requests"


class Balance(Model):
    id = StringField(primary_key=True, max_length=36)
    entity_type = StringField(max_length=10)  # user|org
    entity_id = StringField(max_length=36)
    amount = DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.00"))
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "balances"


class BalanceTransaction(Model):
    id = StringField(primary_key=True, max_length=36)
    entity_type = StringField(max_length=10)  # user|org
    entity_id = StringField(max_length=36)
    type = StringField(max_length=20)          # topup|deduction|refund
    amount = DecimalField(max_digits=12, decimal_places=4)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="SET NULL", nullable=True)
    description = StringField(max_length=500, nullable=True)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "balance_transactions"


class SandboxTemplatePlan(Model):
    """Per-template, per-plan resource and pricing overrides."""
    id = StringField(primary_key=True, max_length=36)
    template_id = ForeignKey(to=SandboxTemplate, on_delete="CASCADE")
    plan_id = StringField(max_length=40)  # references SandboxPlan.id

    # Individual tier overrides — None means "use SandboxPlan global default"
    ind_vcpu = IntegerField(nullable=True)
    ind_ram_gb = IntegerField(nullable=True)
    ind_disk_gb = IntegerField(nullable=True)
    ind_cost_hr = DecimalField(max_digits=10, decimal_places=4, nullable=True)

    # Org tier overrides
    org_vcpu = IntegerField(nullable=True)
    org_ram_gb = IntegerField(nullable=True)
    org_disk_gb = IntegerField(nullable=True)
    org_cost_hr = DecimalField(max_digits=10, decimal_places=4, nullable=True)

    is_enabled = BooleanField(default=True)
    sort_order = IntegerField(default=0)

    class Meta:
        table_name = "sandbox_template_plans"
