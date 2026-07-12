from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from .models import (
    Coupon,
    CouponRedemption,
    CreditGrant,
    FinanceAdjustment,
    Invoice,
    UsageCharge,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def get_usage_charge(charge_id: str) -> UsageCharge | None:
    return UsageCharge.objects.filter(id=charge_id).first()


def get_balance_transaction(tx_id: str):
    from codesandbox.features.sandbox.models import BalanceTransaction

    return BalanceTransaction.objects.filter(id=tx_id).first()


def get_usage_charge_by_instance(instance_id: str) -> UsageCharge | None:
    return UsageCharge.objects.filter(instance_id=instance_id).first()


def get_usage_charge_by_idempotency_key(key: str) -> UsageCharge | None:
    return UsageCharge.objects.filter(idempotency_key=key).first()


def create_usage_charge(**kwargs) -> UsageCharge:
    charge = UsageCharge(id=kwargs.pop("id", _new_id()), created_at=kwargs.pop("created_at", _now()), **kwargs)
    charge.save()
    return charge


def list_usage_charges(
    *,
    status: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
) -> list[UsageCharge]:
    rows = UsageCharge.objects.all()
    if status:
        rows = [r for r in rows if r.status == status]
    if entity_type:
        rows = [r for r in rows if r.entity_type == entity_type]
    if entity_id:
        rows = [r for r in rows if str(r.entity_id) == str(entity_id)]
    if start:
        rows = [r for r in rows if _as_utc(r.created_at) and _as_utc(r.created_at) >= _as_utc(start)]
    if end:
        rows = [r for r in rows if _as_utc(r.created_at) and _as_utc(r.created_at) < _as_utc(end)]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return rows[:limit] if limit else rows


def get_coupon(coupon_id: str) -> Coupon | None:
    return Coupon.objects.filter(id=coupon_id).first()


def get_coupon_by_code(code: str) -> Coupon | None:
    return Coupon.objects.filter(code=code.upper().strip()).first()


def create_coupon(**kwargs) -> Coupon:
    coupon = Coupon(id=kwargs.pop("id", _new_id()), created_at=kwargs.pop("created_at", _now()), **kwargs)
    coupon.save()
    return coupon


def update_coupon(coupon_id: str, **kwargs) -> Coupon | None:
    coupon = get_coupon(coupon_id)
    if coupon is None:
        return None
    for key, value in kwargs.items():
        setattr(coupon, key, value)
    coupon.updated_at = _now()
    coupon.save()
    return coupon


def list_coupons(status: str | None = None) -> list[Coupon]:
    rows = Coupon.objects.all()
    if status:
        rows = [r for r in rows if r.status == status]
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


def create_coupon_redemption(**kwargs) -> CouponRedemption:
    redemption = CouponRedemption(id=kwargs.pop("id", _new_id()), created_at=kwargs.pop("created_at", _now()), **kwargs)
    redemption.save()
    return redemption


def list_coupon_redemptions(coupon_id: str | None = None, *, limit: int | None = None) -> list[CouponRedemption]:
    rows = CouponRedemption.objects.all()
    if coupon_id:
        rows = [r for r in rows if str(r.coupon_id) == str(coupon_id)]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return rows[:limit] if limit else rows


def count_coupon_redemptions(coupon_id: str) -> int:
    return len(CouponRedemption.objects.filter(coupon_id=coupon_id).all())


def count_coupon_redemptions_for_entity(coupon_id: str, entity_type: str, entity_id: str) -> int:
    return len(
        CouponRedemption.objects.filter(
            coupon_id=coupon_id,
            entity_type=entity_type,
            entity_id=entity_id,
        ).all()
    )


def get_credit_grant(grant_id: str) -> CreditGrant | None:
    return CreditGrant.objects.filter(id=grant_id).first()


def create_credit_grant(**kwargs) -> CreditGrant:
    grant = CreditGrant(id=kwargs.pop("id", _new_id()), created_at=kwargs.pop("created_at", _now()), **kwargs)
    grant.save()
    return grant


def update_credit_grant(grant_id: str, **kwargs) -> CreditGrant | None:
    grant = get_credit_grant(grant_id)
    if grant is None:
        return None
    for key, value in kwargs.items():
        setattr(grant, key, value)
    grant.updated_at = _now()
    grant.save()
    return grant


def list_credit_grants(
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[CreditGrant]:
    rows = CreditGrant.objects.all()
    if entity_type:
        rows = [r for r in rows if r.entity_type == entity_type]
    if entity_id:
        rows = [r for r in rows if str(r.entity_id) == str(entity_id)]
    if status:
        rows = [r for r in rows if r.status == status]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return rows[:limit] if limit else rows


def get_invoice(invoice_id: str) -> Invoice | None:
    return Invoice.objects.filter(id=invoice_id).first()


def create_invoice(**kwargs) -> Invoice:
    invoice = Invoice(id=kwargs.pop("id", _new_id()), created_at=kwargs.pop("created_at", _now()), **kwargs)
    invoice.save()
    return invoice


def list_invoices(
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
) -> list[Invoice]:
    rows = Invoice.objects.all()
    if entity_type:
        rows = [r for r in rows if r.entity_type == entity_type]
    if entity_id:
        rows = [r for r in rows if str(r.entity_id) == str(entity_id)]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return rows[:limit] if limit else rows


def create_adjustment(**kwargs) -> FinanceAdjustment:
    adjustment = FinanceAdjustment(id=kwargs.pop("id", _new_id()), created_at=kwargs.pop("created_at", _now()), **kwargs)
    adjustment.save()
    return adjustment


def list_adjustments(
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int | None = None,
) -> list[FinanceAdjustment]:
    rows = FinanceAdjustment.objects.all()
    if entity_type:
        rows = [r for r in rows if r.entity_type == entity_type]
    if entity_id:
        rows = [r for r in rows if str(r.entity_id) == str(entity_id)]
    rows = sorted(rows, key=lambda r: r.created_at, reverse=True)
    return rows[:limit] if limit else rows


def sum_decimal(values) -> Decimal:
    total = Decimal("0")
    for value in values:
        total += Decimal(str(value or "0"))
    return total
