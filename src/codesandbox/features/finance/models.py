from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from nexorm.fields import (
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
from codesandbox.features.sandbox.models import (
    BalanceTransaction,
    SandboxInstance,
    SandboxTemplate,
    TopupIntent,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Coupon(Model):
    id = StringField(primary_key=True, max_length=36)
    code = StringField(max_length=80, unique=True)
    name = StringField(max_length=120)
    description = TextField(nullable=True)
    discount_type = StringField(max_length=30)  # percent|fixed|free_minutes|free_credit
    value = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = StringField(max_length=3, nullable=True)
    max_redemptions = IntegerField(nullable=True)
    per_entity_limit = IntegerField(nullable=True)
    starts_at = DateTimeField(nullable=True)
    expires_at = DateTimeField(nullable=True)
    status = StringField(max_length=20, default="active")  # active|inactive|expired|archived
    applies_to_template_id = ForeignKey(to=SandboxTemplate, on_delete="SET NULL", nullable=True)
    applies_to_plan_id = StringField(max_length=40, nullable=True)
    applies_to_user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="finance_coupons")
    applies_to_org_id = ForeignKey(to=Organization, on_delete="SET NULL", nullable=True)
    created_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="created_coupons")
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "finance_coupons"


class UsageCharge(Model):
    id = StringField(primary_key=True, max_length=36)
    instance_id = ForeignKey(to=SandboxInstance, on_delete="CASCADE", unique=True)
    entity_type = StringField(max_length=10)  # user|org
    entity_id = StringField(max_length=36)
    user_id = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="usage_charges")
    org_id = ForeignKey(to=Organization, on_delete="SET NULL", nullable=True, related_name="usage_charges")
    template_id = ForeignKey(to=SandboxTemplate, on_delete="SET NULL", nullable=True)
    plan_id = StringField(max_length=40, nullable=True)
    runtime_seconds = IntegerField(default=0)
    billable_seconds = IntegerField(default=0)
    cost_hr_snapshot = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    gross_amount = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_amount = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    credit_amount = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    final_amount = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    refunded_amount = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = StringField(max_length=3, default="GBP")
    status = StringField(max_length=20, default="pending")  # pending|charged|refunded|failed|void
    idempotency_key = StringField(max_length=255, unique=True)
    balance_transaction_id = ForeignKey(to=BalanceTransaction, on_delete="SET NULL", nullable=True)
    coupon_redemption_id = StringField(max_length=36, nullable=True)
    created_at = DateTimeField(default=_now)
    charged_at = DateTimeField(nullable=True)
    refunded_at = DateTimeField(nullable=True)
    metadata_json = TextField(nullable=True)

    class Meta:
        table_name = "usage_charges"
        indexes = [
            {
                "name": "idx_usage_charges_entity_created",
                "fields": ["entity_type", "entity_id", "created_at"],
                "unique": False,
            },
            {
                "name": "idx_usage_charges_status_created",
                "fields": ["status", "created_at"],
                "unique": False,
            },
        ]


class CouponRedemption(Model):
    id = StringField(primary_key=True, max_length=36)
    coupon_id = ForeignKey(to=Coupon, on_delete="CASCADE")
    entity_type = StringField(max_length=10)
    entity_id = StringField(max_length=36)
    usage_charge_id = ForeignKey(to=UsageCharge, on_delete="SET NULL", nullable=True)
    topup_intent_id = ForeignKey(to=TopupIntent, on_delete="SET NULL", nullable=True)
    redeemed_amount = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = StringField(max_length=3, default="GBP")
    created_at = DateTimeField(default=_now)
    metadata_json = TextField(nullable=True)

    class Meta:
        table_name = "coupon_redemptions"
        indexes = [
            {
                "name": "idx_coupon_redemptions_coupon_entity",
                "fields": ["coupon_id", "entity_type", "entity_id"],
                "unique": False,
            }
        ]


class CreditGrant(Model):
    id = StringField(primary_key=True, max_length=36)
    entity_type = StringField(max_length=10)
    entity_id = StringField(max_length=36)
    amount = DecimalField(max_digits=12, decimal_places=2)
    remaining_amount = DecimalField(max_digits=12, decimal_places=2)
    currency = StringField(max_length=3, default="GBP")
    reason = TextField(nullable=True)
    granted_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="credit_grants_made")
    expires_at = DateTimeField(nullable=True)
    status = StringField(max_length=20, default="active")  # active|used|expired|reversed|revoked
    balance_transaction_id = ForeignKey(to=BalanceTransaction, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)
    updated_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "credit_grants"
        indexes = [
            {
                "name": "idx_credit_grants_entity_status",
                "fields": ["entity_type", "entity_id", "status"],
                "unique": False,
            }
        ]


class Invoice(Model):
    id = StringField(primary_key=True, max_length=36)
    invoice_no = StringField(max_length=80, unique=True)
    entity_type = StringField(max_length=10)
    entity_id = StringField(max_length=36)
    period_start = DateTimeField()
    period_end = DateTimeField()
    subtotal = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    credit_total = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    refund_total = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    currency = StringField(max_length=3, default="GBP")
    status = StringField(max_length=20, default="draft")  # draft|final|void
    pdf_storage_key = StringField(max_length=700, nullable=True)
    created_at = DateTimeField(default=_now)
    finalized_at = DateTimeField(nullable=True)

    class Meta:
        table_name = "finance_invoices"
        indexes = [
            {
                "name": "idx_finance_invoices_entity_period",
                "fields": ["entity_type", "entity_id", "period_start"],
                "unique": False,
            }
        ]


class FinanceAdjustment(Model):
    id = StringField(primary_key=True, max_length=36)
    entity_type = StringField(max_length=10)
    entity_id = StringField(max_length=36)
    amount = DecimalField(max_digits=12, decimal_places=2)
    currency = StringField(max_length=3, default="GBP")
    reason = TextField(nullable=True)
    created_by = ForeignKey(to=User, on_delete="SET NULL", nullable=True, related_name="finance_adjustments_made")
    balance_transaction_id = ForeignKey(to=BalanceTransaction, on_delete="SET NULL", nullable=True)
    created_at = DateTimeField(default=_now)

    class Meta:
        table_name = "finance_adjustments"
        indexes = [
            {
                "name": "idx_finance_adjustments_entity_created",
                "fields": ["entity_type", "entity_id", "created_at"],
                "unique": False,
            }
        ]
