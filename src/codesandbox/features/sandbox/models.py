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
    default_command = TextField(nullable=True)
    working_dir = StringField(max_length=255, default="/workspace")
    input_mount_path = StringField(max_length=255, default="/input")
    output_mount_path = StringField(max_length=255, default="/output")
    artifact_paths = TextField(nullable=True)
    input_required = BooleanField(default=False)
    max_upload_mb = IntegerField(default=50)

    # Classification — drives policy builder logic
    sandbox_type = StringField(max_length=40)   # interactive|malware|reverse_engineering|android|ctf
    runtime_class = StringField(max_length=40)  # container|microvm|fullvm|android_emulator

    # Interface / network defaults
    interface_mode = StringField(max_length=40, default="terminal")   # terminal|full_ui|background|android_ui
    network_mode = StringField(max_length=40, default="disabled")     # disabled|isolated|fake_internet|controlled_proxy|allowlist

    # Security defaults
    allow_root = BooleanField(default=False)
    read_only_root = BooleanField(default=True)
    run_as_user = StringField(max_length=80, nullable=True)
    pids_limit = IntegerField(default=256)
    allow_full_internet = BooleanField(default=False)
    max_timeout_hr = IntegerField(default=2)

    # Visibility
    status = StringField(max_length=20, default="active")  # active|maintenance|disabled

    # Test gate — a template can only be activated after a Test Launch has
    # passed. Runtime-affecting edits reset this back to "untested".
    last_test_status = StringField(max_length=20, default="untested")  # untested|passed|failed
    last_tested_at = DateTimeField(nullable=True)

    # Type-specific config JSON — admin editable, worker reads
    runtime_config = TextField(nullable=True)

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

    min_billable_minutes = IntegerField(default=1)
    allowed_network_modes = TextField(default='["disabled","restricted"]')

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
    status = StringField(max_length=20, default="idle")  # idle|provisioning|running|stopping|cleanup|stopped|failed|expired|killed
    status_changed_at = DateTimeField(nullable=True)

    # Worker tracking
    worker_id = StringField(max_length=255, nullable=True)
    worker_job_id = StringField(max_length=255, nullable=True)
    runtime_provider = StringField(max_length=40, nullable=True)
    runtime_id = StringField(max_length=255, nullable=True)
    runtime_node_id = StringField(max_length=255, nullable=True)
    workspace_volume_id = StringField(max_length=255, nullable=True)
    artifact_prefix = StringField(max_length=500, nullable=True)

    # Billing
    billing_entity = StringField(max_length=10)          # user|org
    billed_user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="billed_instances")
    billed_org_id = ForeignKey(to=Organization, on_delete="SET NULL", nullable=True, related_name="billed_org_instances")

    # Config snapshot — frozen at start time
    user_config = TextField(nullable=True)
    runtime_policy = TextField(nullable=True)

    allocated_vcpu = IntegerField(nullable=True)
    allocated_ram_gb = IntegerField(nullable=True)
    allocated_disk_gb = IntegerField(nullable=True)
    effective_network_mode = StringField(max_length=40, nullable=True)
    cost_hr_snapshot = DecimalField(max_digits=12, decimal_places=4, nullable=True)
    billing_currency = StringField(max_length=3, default="GBP")
    billing_status = StringField(max_length=20, default="unbilled")
    charged_amount = DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.00"))
    billing_reserved_amount = DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.00"))
    min_billable_sec = IntegerField(default=0)

    # Timing + billing
    started_at = DateTimeField(nullable=True)
    stopped_at = DateTimeField(nullable=True)
    total_runtime_sec = IntegerField(default=0)
    last_heartbeat_at = DateTimeField(nullable=True)
    expires_at = DateTimeField(nullable=True)
    cleanup_started_at = DateTimeField(nullable=True)
    exit_code = IntegerField(nullable=True)
    exit_reason = StringField(max_length=500, nullable=True)

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
    reserved_amount = DecimalField(max_digits=12, decimal_places=4, default=Decimal("0.00"))
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "balances"
        unique_together = [("entity_type", "entity_id")]


class BalanceTransaction(Model):
    id = StringField(primary_key=True, max_length=36)
    entity_type = StringField(max_length=10)  # user|org
    entity_id = StringField(max_length=36)
    type = StringField(max_length=30)          # topup|usage_charge|refund|adjustment|failed_payment
    amount = DecimalField(max_digits=12, decimal_places=4)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="SET NULL", nullable=True)
    topup_intent_id = ForeignKey(to="TopupIntent", on_delete="SET NULL", nullable=True)
    provider = StringField(max_length=30, nullable=True)
    reference = StringField(max_length=255, nullable=True)
    idempotency_key = StringField(max_length=255, nullable=True, unique=True)
    description = StringField(max_length=500, nullable=True)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "balance_transactions"


class TopupIntent(Model):
    """Tracks a gateway payment from creation through confirmation.

    BalanceTransaction has no status field and represents money already
    settled — it must only ever be created once a payment is confirmed.
    This model is the pending/in-flight side: created before redirecting
    the user to the gateway, and resolved (completed/failed) by the
    webhook (Stripe) or the mandatory status-check call (eSewa) — never by
    trusting a client-supplied redirect alone.
    """
    id = StringField(primary_key=True, max_length=36)
    entity_type = StringField(max_length=10)   # user|org
    entity_id = StringField(max_length=36)
    gateway = StringField(max_length=20)        # stripe|esewa
    status = StringField(max_length=20, default="pending")  # pending|processing|completed|failed|expired
    # Amount actually charged, in the gateway's own currency (GBP for
    # Stripe, NPR for eSewa) — kept alongside the converted GBP credit
    # amount so the ledger entry and the receipt agree with what the user
    # was actually charged.
    charge_currency = StringField(max_length=3)   # GBP|NPR
    charge_amount = DecimalField(max_digits=12, decimal_places=4)
    credit_amount_gbp = DecimalField(max_digits=12, decimal_places=4, nullable=True)
    fx_rate = DecimalField(max_digits=12, decimal_places=6, nullable=True)
    external_ref = StringField(max_length=120, unique=True)  # Stripe session id | eSewa transaction_uuid
    idempotency_key = StringField(max_length=255, nullable=True, unique=True)
    provider_event_id = StringField(max_length=255, nullable=True, unique=True)
    balance_transaction_id = ForeignKey(to=BalanceTransaction, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)
    expires_at = DateTimeField(nullable=True)
    resolved_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "topup_intents"


class SandboxAuditLog(Model):
    """Immutable record of every status transition on a SandboxInstance."""
    id = StringField(primary_key=True, max_length=36)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="CASCADE", nullable=True)
    template_id = ForeignKey(to=SandboxTemplate, on_delete="SET NULL", nullable=True)
    event = StringField(max_length=60)    # queued|started|stopped|failed|killed|escape_attempt
    old_status = StringField(max_length=20, nullable=True)
    new_status = StringField(max_length=20, nullable=True)
    actor = StringField(max_length=80, nullable=True)   # user:<id> | worker | system
    detail = TextField(nullable=True)                   # JSON extra data
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "sandbox_audit_logs"


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

    max_timeout_hr = IntegerField(nullable=True)
    network_mode = StringField(max_length=40, nullable=True)
    min_billable_minutes = IntegerField(nullable=True)
    full_internet_enabled = BooleanField(nullable=True)

    is_enabled = BooleanField(default=True)
    sort_order = IntegerField(default=0)

    class Meta:
        table_name = "sandbox_template_plans"
        unique_together = [("template_id", "plan_id")]


class SandboxInput(Model):
    id = StringField(primary_key=True, max_length=36)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="CASCADE")
    name = StringField(max_length=255)
    storage_key = StringField(max_length=700, unique=True)
    size_bytes = IntegerField(default=0)
    checksum = StringField(max_length=64)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "sandbox_inputs"


class SandboxArtifact(Model):
    id = StringField(primary_key=True, max_length=36)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="CASCADE")
    name = StringField(max_length=500)
    artifact_type = StringField(max_length=80, default="file")
    storage_key = StringField(max_length=700, unique=True)
    size_bytes = IntegerField(default=0)
    checksum = StringField(max_length=64)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "sandbox_artifacts"
        indexes = [
            {
                "name": "idx_sandbox_artifacts_instance_created",
                "fields": ["instance_id", "created_at"],
                "unique": False,
            }
        ]
