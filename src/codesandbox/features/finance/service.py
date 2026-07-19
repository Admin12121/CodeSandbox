from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
from urllib.parse import urlencode

from nexorm import transaction

from codesandbox.config import get_settings
from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.organizations import repository as org_repo
from codesandbox.features.sandbox import repository as sandbox_repo
from codesandbox.features.sandbox.models import (
    Balance,
    BalanceTransaction,
    SandboxAuditLog,
    SandboxInstance,
    TopupIntent,
)

from . import repository
from .models import Coupon, CreditGrant, FinanceAdjustment, UsageCharge


MONEY = Decimal("0.01")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _qid() -> str:
    return str(uuid.uuid4())


def _decimal(value, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value if value is not None else default))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _money(value) -> Decimal:
    return max(Decimal("0"), _decimal(value)).quantize(MONEY, rounding=ROUND_UP)


def _signed_money(value) -> Decimal:
    return _decimal(value).quantize(MONEY, rounding=ROUND_UP)


def _format_money(value, currency: str = "GBP") -> str:
    symbol = "£" if currency == "GBP" else f"{currency} "
    return f"{symbol}{_decimal(value):.2f}"


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand"), (100, "hundred")]


def _int_to_words(n: int) -> str:
    if n == 0:
        return "zero"
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, rest = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[rest]}" if rest else "")
    for scale, name in _SCALES:
        if n >= scale:
            whole, rest = divmod(n, scale)
            words = f"{_int_to_words(whole)} {name}"
            return f"{words} {_int_to_words(rest)}" if rest else words
    return str(n)


def _amount_in_words(value, currency: str = "GBP") -> str:
    amount = Decimal(f"{abs(_decimal(value)):.2f}")
    whole = int(amount)
    cents = int((amount - whole) * 100)
    words = _int_to_words(whole).capitalize()
    return f"{words} and {cents:02d}/100" if cents else words


def _safe_date(value: str | None) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _entity_id_for_instance(inst: SandboxInstance) -> str:
    if inst.billing_entity == "org" and inst.billed_org_id:
        return str(inst.billed_org_id)
    return str(inst.billed_user_id or "")


def _entity_label(entity_type: str, entity_id: str) -> str:
    if entity_type == "user":
        user = identity_repo.find_user_by_id(entity_id)
        return (user.email or user.name) if user else entity_id
    if entity_type == "org":
        org = org_repo.get_organization(entity_id)
        return org.name if org else entity_id
    return entity_id


def _entity_exists(entity_type: str, entity_id: str) -> bool:
    if entity_type == "user":
        user = identity_repo.find_user_by_id(entity_id)
        return bool(user and user.deleted_at is None)
    if entity_type == "org":
        return org_repo.get_organization(entity_id) is not None
    return False


def _instance_user_id(inst: SandboxInstance) -> str | None:
    if inst.billed_user_id:
        return str(inst.billed_user_id)
    if inst.workspace_user_id:
        return str(inst.workspace_user_id)
    if inst.assigned_to_user_id:
        return str(inst.assigned_to_user_id)
    return None


def _load_user_config(inst: SandboxInstance) -> dict:
    try:
        raw = json.loads(inst.user_config or "{}")
        return raw if isinstance(raw, dict) else {}
    except ValueError:
        return {}


def _coupon_code_for_instance(inst: SandboxInstance) -> str | None:
    cfg = _load_user_config(inst)
    code = cfg.get("coupon_code")
    billing = cfg.get("billing")
    if not code and isinstance(billing, dict):
        code = billing.get("coupon_code")
    code = str(code or "").strip().upper()
    return code or None


def _audit(
    event: str,
    *,
    actor_user_id: str | None = None,
    instance_id: str | None = None,
    detail: dict | None = None,
) -> None:
    sandbox_repo.log_instance_event(
        instance_id,
        event,
        actor=f"user:{actor_user_id}" if actor_user_id else "system",
        detail=json.dumps(detail or {}, separators=(",", ":")),
    )


def get_date_range(period: str = "30d", start: str | None = None, end: str | None = None) -> tuple[datetime, datetime]:
    now = _now()
    if period == "today":
        begin = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return begin, begin + timedelta(days=1)
    if period == "week":
        days_since_sunday = (now.weekday() + 1) % 7
        begin = (now - timedelta(days=days_since_sunday)).replace(hour=0, minute=0, second=0, microsecond=0)
        return begin, begin + timedelta(days=7)
    if period == "7d":
        return now - timedelta(days=7), now + timedelta(seconds=1)
    if period == "month":
        begin = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if begin.month == 12:
            finish = begin.replace(year=begin.year + 1, month=1)
        else:
            finish = begin.replace(month=begin.month + 1)
        return begin, finish
    if period == "year":
        begin = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return begin, begin.replace(year=begin.year + 1)
    if period == "custom":
        begin = _safe_date(start) or now - timedelta(days=30)
        finish = _safe_date(end)
        if finish:
            finish = finish + timedelta(days=1) if finish.hour == finish.minute == finish.second == 0 else finish
        return begin, finish or now + timedelta(seconds=1)
    return now - timedelta(days=30), now + timedelta(seconds=1)


def filter_query_args(**kwargs) -> str:
    return urlencode({k: v for k, v in kwargs.items() if v not in (None, "", "all")})


# ── Coupons / credit allocation ──────────────────────────────────────────────


def _coupon_is_scoped_to_instance(coupon: Coupon, inst: SandboxInstance, entity_type: str, entity_id: str) -> bool:
    if coupon.applies_to_template_id and str(coupon.applies_to_template_id) != str(inst.template_id):
        return False
    if coupon.applies_to_plan_id and str(coupon.applies_to_plan_id) != str(inst.plan_id):
        return False
    if coupon.applies_to_user_id:
        user_id = _instance_user_id(inst)
        if str(coupon.applies_to_user_id) != str(user_id or ""):
            return False
    if coupon.applies_to_org_id and str(coupon.applies_to_org_id) != str(inst.billed_org_id or inst.workspace_org_id or ""):
        return False
    if coupon.applies_to_user_id is None and coupon.applies_to_org_id is None:
        return True
    return True


def validate_coupon_for_instance(coupon: Coupon, inst: SandboxInstance, entity_type: str, entity_id: str) -> str | None:
    now = _now()
    if coupon.status != "active":
        return "Coupon is not active."
    starts_at = _as_utc(coupon.starts_at)
    expires_at = _as_utc(coupon.expires_at)
    if starts_at and starts_at > now:
        return "Coupon has not started."
    if expires_at and expires_at <= now:
        return "Coupon has expired."
    if not _coupon_is_scoped_to_instance(coupon, inst, entity_type, entity_id):
        return "Coupon does not apply to this sandbox."
    if coupon.max_redemptions is not None and repository.count_coupon_redemptions(str(coupon.id)) >= int(coupon.max_redemptions):
        return "Coupon redemption limit reached."
    if coupon.per_entity_limit is not None and repository.count_coupon_redemptions_for_entity(str(coupon.id), entity_type, entity_id) >= int(coupon.per_entity_limit):
        return "Coupon redemption limit reached for this account."
    return None


def _discount_for_coupon(coupon: Coupon, gross: Decimal, billable_seconds: int, cost_hr: Decimal) -> Decimal:
    value = _decimal(coupon.value)
    if gross <= 0 or value <= 0:
        return Decimal("0")
    if coupon.discount_type == "percent":
        return min(gross, (gross * value / Decimal(100)).quantize(MONEY, rounding=ROUND_UP))
    if coupon.discount_type in {"fixed", "free_credit"}:
        return min(gross, value.quantize(MONEY, rounding=ROUND_UP))
    if coupon.discount_type == "free_minutes":
        free_seconds = min(billable_seconds, int(value * Decimal(60)))
        return min(gross, (cost_hr * Decimal(free_seconds) / Decimal(3600)).quantize(MONEY, rounding=ROUND_UP))
    return Decimal("0")


def _consume_credit_grants(entity_type: str, entity_id: str, amount: Decimal) -> Decimal:
    remaining = max(Decimal("0"), amount)
    used = Decimal("0")
    now = _now()
    for grant in repository.list_credit_grants(entity_type=entity_type, entity_id=entity_id, status="active"):
        if remaining <= 0:
            break
        expires_at = _as_utc(grant.expires_at)
        if expires_at and expires_at <= now:
            grant.status = "expired"
            grant.updated_at = now
            grant.save()
            continue
        available = max(Decimal("0"), _decimal(grant.remaining_amount))
        if available <= 0:
            grant.status = "used"
            grant.updated_at = now
            grant.save()
            continue
        take = min(available, remaining)
        grant.remaining_amount = available - take
        if grant.remaining_amount <= 0:
            grant.status = "used"
        grant.updated_at = now
        grant.save()
        used += take
        remaining -= take
    return used.quantize(MONEY, rounding=ROUND_UP)


# ── Usage charge integration ─────────────────────────────────────────────────

def create_usage_charge_for_instance(
    instance_id: str,
    *,
    runtime_seconds: int,
    billable_seconds: int,
    description: str,
) -> tuple[UsageCharge | None, BalanceTransaction | None, Decimal, str]:
    """Create the finance charge, ledger debit, and instance billing update atomically.

    The ledger debit reduces prepaid balance/liability. ``UsageCharge.final_amount``
    is revenue after discounts/free-credit allocation.
    """
    charge_key = f"usage-charge:{instance_id}"
    legacy_tx_key = f"usage:{instance_id}"

    with transaction.atomic():
        existing_charge = repository.get_usage_charge_by_idempotency_key(charge_key)
        existing_tx = BalanceTransaction.objects.filter(idempotency_key=legacy_tx_key).first()
        inst = sandbox_repo.get_instance_for_update(instance_id)
        if inst is None:
            return existing_charge, existing_tx, Decimal("0"), "missing"
        if existing_charge is not None:
            return existing_charge, existing_tx, _decimal(existing_charge.final_amount), inst.billing_status

        cost_hr = _decimal(inst.cost_hr_snapshot or "0")
        gross = _money(cost_hr * Decimal(max(0, billable_seconds)) / Decimal(3600))
        entity_type = inst.billing_entity
        entity_id = _entity_id_for_instance(inst)
        currency = inst.billing_currency or "GBP"

        if entity_type == "test" or gross <= 0:
            inst.billing_status = "not_charged" if entity_type == "test" else "charged"
            inst.charged_amount = Decimal("0")
            inst.billing_reserved_amount = Decimal("0")
            inst.cost_hr_snapshot = cost_hr
            inst.save()
            return None, None, Decimal("0"), inst.billing_status

        if entity_type not in {"user", "org"} or not entity_id:
            failed = repository.create_usage_charge(
                instance_id=instance_id,
                entity_type=entity_type or "user",
                entity_id=entity_id or "",
                user_id=_instance_user_id(inst),
                org_id=str(inst.billed_org_id) if inst.billed_org_id else None,
                template_id=str(inst.template_id) if inst.template_id else None,
                plan_id=str(inst.plan_id or "") or None,
                runtime_seconds=runtime_seconds,
                billable_seconds=billable_seconds,
                cost_hr_snapshot=cost_hr,
                gross_amount=gross,
                final_amount=Decimal("0"),
                currency=currency,
                status="failed",
                idempotency_key=charge_key,
                metadata_json=json.dumps({"error": "missing_billing_entity"}),
            )
            inst.billing_status = "failed"
            inst.save()
            return failed, None, Decimal("0"), "failed"

        if existing_tx is not None:
            charged = abs(_decimal(existing_tx.amount))
            status = inst.billing_status or ("charged" if charged > 0 else "not_charged")
            charge = repository.create_usage_charge(
                instance_id=instance_id,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=_instance_user_id(inst),
                org_id=str(inst.billed_org_id) if inst.billed_org_id else None,
                template_id=str(inst.template_id) if inst.template_id else None,
                plan_id=str(inst.plan_id or "") or None,
                runtime_seconds=runtime_seconds,
                billable_seconds=billable_seconds,
                cost_hr_snapshot=cost_hr,
                gross_amount=gross,
                discount_amount=Decimal("0"),
                credit_amount=Decimal("0"),
                final_amount=charged,
                currency=currency,
                status="charged" if charged > 0 else "failed",
                idempotency_key=charge_key,
                balance_transaction_id=str(existing_tx.id),
                charged_at=existing_tx.created_at or _now(),
                metadata_json=json.dumps({"adopted_legacy_balance_transaction": True, "billing_status": status}),
            )
            return charge, existing_tx, charged, status

        coupon = None
        discount = Decimal("0")
        coupon_code = _coupon_code_for_instance(inst)
        coupon_error = None
        if coupon_code:
            coupon = repository.get_coupon_by_code(coupon_code)
            coupon_error = "Coupon not found." if coupon is None else validate_coupon_for_instance(coupon, inst, entity_type, entity_id)
            if coupon is not None and coupon_error is None:
                discount = _discount_for_coupon(coupon, gross, billable_seconds, cost_hr)

        debit_due = max(Decimal("0"), gross - discount).quantize(MONEY, rounding=ROUND_UP)
        balance = sandbox_repo.get_balance_for_update(entity_type, entity_id)
        instance_reserved = _decimal(inst.billing_reserved_amount)
        total_reserved = _decimal(balance.reserved_amount)
        other_reserved = max(Decimal("0"), total_reserved - instance_reserved)
        spendable = max(Decimal("0"), _decimal(balance.amount) - other_reserved)
        ledger_debit = min(debit_due, spendable).quantize(MONEY, rounding=ROUND_UP)
        credit_used = _consume_credit_grants(entity_type, entity_id, ledger_debit)
        final_revenue = max(Decimal("0"), ledger_debit - credit_used).quantize(MONEY, rounding=ROUND_UP)

        balance.reserved_amount = other_reserved
        balance.amount = _decimal(balance.amount) - ledger_debit
        balance.updated_at = _now()
        balance.save()

        billing_status = "charged" if ledger_debit == debit_due else "partial"
        if debit_due > 0 and ledger_debit <= 0:
            billing_status = "partial"

        inst.billing_reserved_amount = Decimal("0")
        inst.charged_amount = ledger_debit
        inst.cost_hr_snapshot = cost_hr
        inst.billing_currency = currency
        inst.billing_status = billing_status
        inst.save()

        tx = None
        if ledger_debit > 0:
            tx = BalanceTransaction(
                id=_qid(),
                entity_type=entity_type,
                entity_id=entity_id,
                type="usage_charge",
                amount=-ledger_debit,
                instance_id=instance_id,
                provider="sandbox",
                reference=instance_id,
                idempotency_key=legacy_tx_key,
                description=description,
                created_at=_now(),
            )
            tx.save()

        metadata = {
            "billing_status": billing_status,
            "ledger_debit": str(ledger_debit),
            "coupon_code": coupon_code,
            "coupon_error": coupon_error,
        }
        charge_status = "charged" if ledger_debit > 0 or debit_due == 0 else "failed"
        charge = repository.create_usage_charge(
            instance_id=instance_id,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=_instance_user_id(inst),
            org_id=str(inst.billed_org_id) if inst.billed_org_id else None,
            template_id=str(inst.template_id) if inst.template_id else None,
            plan_id=str(inst.plan_id or "") or None,
            runtime_seconds=runtime_seconds,
            billable_seconds=billable_seconds,
            cost_hr_snapshot=cost_hr,
            gross_amount=gross,
            discount_amount=discount,
            credit_amount=credit_used,
            final_amount=final_revenue,
            currency=currency,
            status=charge_status,
            idempotency_key=charge_key,
            balance_transaction_id=str(tx.id) if tx else None,
            charged_at=_now() if tx or debit_due == 0 else None,
            metadata_json=json.dumps(metadata, separators=(",", ":")),
        )

        if coupon is not None and discount > 0:
            redemption = repository.create_coupon_redemption(
                coupon_id=str(coupon.id),
                entity_type=entity_type,
                entity_id=entity_id,
                usage_charge_id=str(charge.id),
                redeemed_amount=discount,
                currency=currency,
                metadata_json=json.dumps({"instance_id": instance_id, "coupon_code": coupon.code}),
            )
            charge.coupon_redemption_id = str(redemption.id)
            charge.save()

        _audit(
            "finance.usage_charge.created",
            instance_id=instance_id,
            detail={
                "usage_charge_id": str(charge.id),
                "gross": str(gross),
                "discount": str(discount),
                "credit": str(credit_used),
                "final_revenue": str(final_revenue),
                "ledger_debit": str(ledger_debit),
            },
        )
        return charge, tx, final_revenue, billing_status


# ── Admin mutations ──────────────────────────────────────────────────────────

def create_coupon(
    *,
    code: str,
    name: str,
    discount_type: str,
    value: str,
    actor_user_id: str,
    description: str | None = None,
    currency: str | None = "GBP",
    max_redemptions: str | None = None,
    per_entity_limit: str | None = None,
    starts_at: str | None = None,
    expires_at: str | None = None,
    applies_to_template_id: str | None = None,
    applies_to_plan_id: str | None = None,
    applies_to_user_id: str | None = None,
    applies_to_org_id: str | None = None,
) -> tuple[dict | None, str | None]:
    code = code.strip().upper()
    name = name.strip()
    if not code:
        return None, "Coupon code is required."
    if not name:
        return None, "Coupon name is required."
    if discount_type not in {"percent", "fixed", "free_minutes", "free_credit"}:
        return None, "Invalid discount type."
    amount = _decimal(value)
    if amount <= 0:
        return None, "Coupon value must be positive."
    if repository.get_coupon_by_code(code):
        return None, "Coupon code already exists."
    if applies_to_user_id and not _entity_exists("user", applies_to_user_id):
        return None, "Scoped user was not found."
    if applies_to_org_id and not _entity_exists("org", applies_to_org_id):
        return None, "Scoped organization was not found."
    coupon = repository.create_coupon(
        code=code,
        name=name,
        description=description or None,
        discount_type=discount_type,
        value=amount,
        currency=(currency or "GBP")[:3] if discount_type in {"fixed", "free_credit"} else None,
        max_redemptions=int(max_redemptions) if str(max_redemptions or "").strip() else None,
        per_entity_limit=int(per_entity_limit) if str(per_entity_limit or "").strip() else None,
        starts_at=_safe_date(starts_at),
        expires_at=_safe_date(expires_at),
        status="active",
        applies_to_template_id=applies_to_template_id or None,
        applies_to_plan_id=applies_to_plan_id or None,
        applies_to_user_id=applies_to_user_id or None,
        applies_to_org_id=applies_to_org_id or None,
        created_by=actor_user_id,
    )
    _audit("finance.coupon.created", actor_user_id=actor_user_id, detail={"coupon_id": str(coupon.id), "code": code})
    return coupon_dict(coupon), None


def disable_coupon(coupon_id: str, actor_user_id: str) -> str | None:
    coupon = repository.update_coupon(coupon_id, status="inactive")
    if coupon is None:
        return "Coupon not found."
    _audit("finance.coupon.disabled", actor_user_id=actor_user_id, detail={"coupon_id": coupon_id})
    return None


def update_coupon(
    coupon_id: str,
    *,
    name: str,
    description: str | None,
    status: str,
    actor_user_id: str,
) -> tuple[dict | None, str | None]:
    if status not in {"active", "inactive", "archived"}:
        return None, "Invalid coupon status."
    coupon = repository.update_coupon(
        coupon_id,
        name=name.strip() or "Untitled Coupon",
        description=description or None,
        status=status,
    )
    if coupon is None:
        return None, "Coupon not found."
    _audit("finance.coupon.updated", actor_user_id=actor_user_id, detail={"coupon_id": coupon_id})
    return coupon_dict(coupon), None


def grant_credit(
    *,
    entity_type: str,
    entity_id: str,
    amount: str,
    reason: str,
    actor_user_id: str,
    expires_at: str | None = None,
) -> tuple[dict | None, str | None]:
    if entity_type not in {"user", "org"} or not entity_id:
        return None, "Select a valid user or organization."
    if not _entity_exists(entity_type, entity_id):
        return None, "Selected user or organization was not found."
    credit = _money(amount)
    if credit <= 0:
        return None, "Credit amount must be positive."
    with transaction.atomic():
        tx = sandbox_repo.add_balance_transaction(
            entity_type=entity_type,
            entity_id=entity_id,
            tx_type="credit_grant",
            amount=credit,
            provider="finance",
            reference=f"credit-grant:{uuid.uuid4()}",
            idempotency_key=None,
            description=reason or "Admin credit grant",
        )
        grant = repository.create_credit_grant(
            entity_type=entity_type,
            entity_id=entity_id,
            amount=credit,
            remaining_amount=credit,
            currency="GBP",
            reason=reason or None,
            granted_by=actor_user_id,
            expires_at=_safe_date(expires_at),
            status="active",
            balance_transaction_id=str(tx.id),
        )
    _audit("finance.credit_grant.created", actor_user_id=actor_user_id, detail={"credit_grant_id": str(grant.id), "amount": str(credit)})
    return credit_grant_dict(grant), None


def revoke_credit_grant(grant_id: str, actor_user_id: str) -> str | None:
    grant = repository.get_credit_grant(grant_id)
    if grant is None:
        return "Credit grant not found."
    unused = _decimal(grant.remaining_amount)
    if unused > 0 and grant.status == "active":
        sandbox_repo.add_balance_transaction(
            entity_type=grant.entity_type,
            entity_id=grant.entity_id,
            tx_type="adjustment",
            amount=-unused,
            provider="finance",
            reference=f"credit-grant-revoke:{grant_id}",
            idempotency_key=f"credit-grant-revoke:{grant_id}",
            description=f"Revoke unused credit grant: {grant.reason or grant_id}",
        )
    grant.status = "revoked"
    grant.remaining_amount = Decimal("0")
    grant.updated_at = _now()
    grant.save()
    _audit("finance.credit_grant.revoked", actor_user_id=actor_user_id, detail={"credit_grant_id": grant_id, "unused": str(unused)})
    return None


def refund_usage_charge(
    *,
    charge_id: str,
    amount: str,
    reason: str,
    actor_user_id: str,
    internal_note: str | None = None,
) -> tuple[dict | None, str | None]:
    charge = repository.get_usage_charge(charge_id)
    if charge is None:
        return None, "Usage charge not found."
    if charge.status in {"void", "failed"}:
        return None, "This usage charge cannot be refunded."
    ledger_amount = Decimal("0")
    tx = None
    if charge.balance_transaction_id:
        tx = BalanceTransaction.objects.filter(id=charge.balance_transaction_id).first()
        if tx:
            ledger_amount = abs(_decimal(tx.amount))
    if ledger_amount <= 0:
        ledger_amount = _decimal(charge.gross_amount) - _decimal(charge.discount_amount)
    already = _decimal(charge.refunded_amount)
    requested = _money(amount)
    if requested <= 0:
        return None, "Refund amount must be positive."
    if requested > ledger_amount - already:
        return None, "Refund amount exceeds remaining refundable amount."
    with transaction.atomic():
        refund_tx = sandbox_repo.add_balance_transaction(
            entity_type=charge.entity_type,
            entity_id=charge.entity_id,
            tx_type="refund",
            amount=requested,
            instance_id=str(charge.instance_id) if charge.instance_id else None,
            provider="finance",
            reference=f"usage-charge:{charge_id}",
            idempotency_key=f"refund:{charge_id}:{already + requested}",
            description=reason or "Usage charge refund",
        )
        charge.refunded_amount = already + requested
        charge.refunded_at = _now()
        if charge.refunded_amount >= ledger_amount:
            charge.status = "refunded"
        charge.save()
    _audit(
        "finance.refund.issued",
        actor_user_id=actor_user_id,
        instance_id=str(charge.instance_id) if charge.instance_id else None,
        detail={
            "usage_charge_id": charge_id,
            "amount": str(requested),
            "transaction_id": str(refund_tx.id),
            "internal_note": internal_note or None,
        },
    )
    return usage_charge_dict(charge), None


def create_adjustment(
    *,
    entity_type: str,
    entity_id: str,
    amount: str,
    reason: str,
    actor_user_id: str,
    internal_note: str | None = None,
) -> tuple[dict | None, str | None]:
    if entity_type not in {"user", "org"} or not entity_id:
        return None, "Select a valid user or organization."
    if not _entity_exists(entity_type, entity_id):
        return None, "Selected user or organization was not found."
    value = _signed_money(amount)
    if value == 0:
        return None, "Adjustment amount cannot be zero."
    with transaction.atomic():
        tx = sandbox_repo.add_balance_transaction(
            entity_type=entity_type,
            entity_id=entity_id,
            tx_type="adjustment",
            amount=value,
            provider="finance",
            reference=f"finance-adjustment:{uuid.uuid4()}",
            description=reason or "Finance adjustment",
        )
        adjustment = repository.create_adjustment(
            entity_type=entity_type,
            entity_id=entity_id,
            amount=value,
            currency="GBP",
            reason=reason or None,
            created_by=actor_user_id,
            balance_transaction_id=str(tx.id),
        )
    _audit(
        "finance.adjustment.created",
        actor_user_id=actor_user_id,
        detail={"adjustment_id": str(adjustment.id), "amount": str(value), "internal_note": internal_note or None},
    )
    return adjustment_dict(adjustment), None


def generate_invoice(
    *,
    entity_type: str,
    entity_id: str,
    period_start: str,
    period_end: str,
    actor_user_id: str,
) -> tuple[dict | None, str | None]:
    start = _safe_date(period_start)
    end = _safe_date(period_end)
    if entity_type not in {"user", "org"} or not entity_id or not start or not end:
        return None, "Valid entity and period are required."
    if not _entity_exists(entity_type, entity_id):
        return None, "Selected user or organization was not found."
    if end <= start:
        return None, "Invoice end date must be after start date."
    charges = repository.list_usage_charges(entity_type=entity_type, entity_id=entity_id, start=start, end=end)
    subtotal = repository.sum_decimal(c.gross_amount for c in charges)
    discount_total = repository.sum_decimal(c.discount_amount for c in charges)
    credit_total = repository.sum_decimal(c.credit_amount for c in charges)
    refund_total = repository.sum_decimal(c.refunded_amount for c in charges)
    total = max(Decimal("0"), repository.sum_decimal(c.final_amount for c in charges) - refund_total)
    invoice_no = f"INV-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    invoice = repository.create_invoice(
        invoice_no=invoice_no,
        entity_type=entity_type,
        entity_id=entity_id,
        period_start=start,
        period_end=end,
        subtotal=subtotal,
        discount_total=discount_total,
        credit_total=credit_total,
        refund_total=refund_total,
        total=total,
        currency="GBP",
        status="draft",
    )
    _audit("finance.invoice.generated", actor_user_id=actor_user_id, detail={"invoice_id": str(invoice.id), "invoice_no": invoice_no})
    return invoice_dict(invoice), None


# ── Reporting / DTOs ─────────────────────────────────────────────────────────

def _all_balance_transactions(start: datetime | None = None, end: datetime | None = None) -> list[BalanceTransaction]:
    rows = BalanceTransaction.objects.all()
    if start:
        rows = [r for r in rows if _as_utc(r.created_at) and _as_utc(r.created_at) >= _as_utc(start)]
    if end:
        rows = [r for r in rows if _as_utc(r.created_at) and _as_utc(r.created_at) < _as_utc(end)]
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


def _topup_intents(start: datetime | None = None, end: datetime | None = None) -> list[TopupIntent]:
    rows = TopupIntent.objects.all()
    if start:
        rows = [r for r in rows if _as_utc(r.created_at) and _as_utc(r.created_at) >= _as_utc(start)]
    if end:
        rows = [r for r in rows if _as_utc(r.created_at) and _as_utc(r.created_at) < _as_utc(end)]
    return sorted(rows, key=lambda r: r.created_at, reverse=True)


def _estimated_compute_cost_for_charge(charge: UsageCharge) -> Decimal:
    settings = get_settings()
    vcpu_cost = _decimal(settings.finance_cost_per_vcpu_hour)
    ram_cost = _decimal(settings.finance_cost_per_ram_gb_hour)
    disk_cost = _decimal(settings.finance_cost_per_disk_gb_hour)
    inst = SandboxInstance.objects.filter(id=charge.instance_id).first() if charge.instance_id else None
    if inst is None:
        return Decimal("0")
    hours = Decimal(int(charge.billable_seconds or 0)) / Decimal(3600)
    return (
        Decimal(int(inst.allocated_vcpu or 0)) * hours * vcpu_cost
        + Decimal(int(inst.allocated_ram_gb or 0)) * hours * ram_cost
        + Decimal(int(inst.allocated_disk_gb or 0)) * hours * disk_cost
    ).quantize(MONEY, rounding=ROUND_UP)


def _display_end(finish: datetime) -> datetime:
    return finish - timedelta(seconds=1)


def _period_context(period: str = "30d", start: str | None = None, end: str | None = None) -> dict:
    begin, finish = get_date_range(period, start, end)
    duration = finish - begin
    if duration.total_seconds() <= 0:
        duration = timedelta(days=1)
        finish = begin + duration
    previous_begin = begin - duration
    previous_finish = begin
    labels = {
        "today": "Today",
        "week": "This week",
        "7d": "Last 7 days",
        "30d": "Last 30 days",
        "month": "This month",
        "year": "This year",
        "custom": "Custom range",
    }
    return {
        "key": period or "30d",
        "label": labels.get(period or "30d", "Last 30 days"),
        "start": begin.date().isoformat(),
        "end": _display_end(finish).date().isoformat(),
        "begin": begin,
        "finish": finish,
        "previous_begin": previous_begin,
        "previous_finish": previous_finish,
        "is_custom": period == "custom",
    }


def _percent(value: Decimal, total: Decimal) -> Decimal | None:
    if total == 0:
        return None
    return (value / total * Decimal("100")).quantize(Decimal("0.01"))


def _delta(current: Decimal, previous: Decimal, currency: str = "GBP") -> dict:
    amount = current - previous
    pct = None if previous == 0 else ((amount / abs(previous)) * Decimal("100")).quantize(Decimal("0.01"))
    if pct is None:
        text = "No previous period" if current == 0 else "New activity"
    else:
        sign = "+" if pct > 0 else ""
        text = f"{sign}{pct:.2f}% vs previous period"
    return {
        "amount": f"{amount:.2f}",
        "amount_display": _format_money(amount, currency),
        "percent": None if pct is None else f"{pct:.2f}",
        "text": text,
        "direction": "up" if amount > 0 else "down" if amount < 0 else "flat",
    }


def _amount(value: Decimal, currency: str = "GBP") -> dict:
    return {"raw": f"{value:.2f}", "display": _format_money(value, currency)}


def _mini_bar_series(values: list[str | Decimal | int | float]) -> list[dict]:
    decimals = [_decimal(value) for value in values]
    if not decimals:
        decimals = [Decimal("0")]
    max_abs = max(abs(value) for value in decimals)
    bars = []
    for value in decimals:
        if max_abs == 0:
            height = 2
        else:
            height = int((abs(value) / max_abs * Decimal("22")).to_integral_value(rounding=ROUND_UP))
            height = max(2, min(22, height))
        bars.append({
            "value": f"{value:.2f}",
            "height": height,
            "negative": value < 0,
            "zero": value == 0,
        })
    return bars


# Every period (today/week/month/year/custom) slices into this many
# equal-width real time buckets for the mini sparklines — a fixed target
# density rather than a fixed semantic unit (was: 24 hourly buckets for
# "today", 7 daily for "week", ~30 daily for "month", 12 monthly for
# "year"), so "week" and "year" no longer render visibly sparser than
# "month" just because their calendar-aligned unit was coarser. Each
# bucket still sums real events within its exact sub-range — no mock data,
# just a consistent number of real sub-totals.
_TARGET_BAR_COUNT = 30


def _bucketed_values(period_info: dict, events: list[tuple[datetime | None, Decimal]]) -> list[Decimal]:
    if not events:
        return []
    begin = _as_utc(period_info["begin"]) or period_info["begin"]
    finish = _as_utc(period_info["finish"]) or period_info["finish"]
    total_seconds = max(1.0, (finish - begin).total_seconds())
    bucket_seconds = total_seconds / _TARGET_BAR_COUNT
    values = [Decimal("0") for _ in range(_TARGET_BAR_COUNT)]
    for when, amount in events:
        when_utc = _as_utc(when)
        if when_utc is None or when_utc < begin or when_utc >= finish:
            continue
        index = int((when_utc - begin).total_seconds() // bucket_seconds)
        index = max(0, min(_TARGET_BAR_COUNT - 1, index))
        values[index] += _decimal(amount)
    return values


def _duration_display(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


def _date_label(value: datetime | None) -> str:
    if not value:
        return ""
    value = _as_utc(value) or value
    return value.strftime("%d %b %Y %H:%M")


def _compute_stats(charges: list[UsageCharge]) -> tuple[dict[str, dict], dict]:
    settings = get_settings()
    vcpu_cost = _decimal(settings.finance_cost_per_vcpu_hour)
    ram_cost = _decimal(settings.finance_cost_per_ram_gb_hour)
    disk_cost = _decimal(settings.finance_cost_per_disk_gb_hour)
    by_charge: dict[str, dict] = {}
    totals = {
        "compute_cost": Decimal("0"),
        "vcpu_hours": Decimal("0"),
        "ram_gb_hours": Decimal("0"),
        "disk_gb_hours": Decimal("0"),
    }
    instance_ids = sorted({str(c.instance_id) for c in charges if c.instance_id})
    instances: dict[str, SandboxInstance] = {}
    if instance_ids:
        instances = {str(i.id): i for i in SandboxInstance.objects.filter(id__in=instance_ids).all()}
    for charge in charges:
        inst = instances.get(str(charge.instance_id)) if charge.instance_id else None
        hours = Decimal(int(charge.billable_seconds or 0)) / Decimal(3600)
        vcpu_hours = Decimal(int(inst.allocated_vcpu or 0)) * hours if inst else Decimal("0")
        ram_gb_hours = Decimal(int(inst.allocated_ram_gb or 0)) * hours if inst else Decimal("0")
        disk_gb_hours = Decimal(int(inst.allocated_disk_gb or 0)) * hours if inst else Decimal("0")
        compute_cost = (
            vcpu_hours * vcpu_cost
            + ram_gb_hours * ram_cost
            + disk_gb_hours * disk_cost
        ).quantize(MONEY, rounding=ROUND_UP)
        by_charge[str(charge.id)] = {
            "instance": inst,
            "compute_cost": compute_cost,
            "vcpu_hours": vcpu_hours,
            "ram_gb_hours": ram_gb_hours,
            "disk_gb_hours": disk_gb_hours,
        }
        totals["compute_cost"] += compute_cost
        totals["vcpu_hours"] += vcpu_hours
        totals["ram_gb_hours"] += ram_gb_hours
        totals["disk_gb_hours"] += disk_gb_hours
    return by_charge, totals


def _period_financials(begin: datetime, finish: datetime) -> dict:
    charges = repository.list_usage_charges(start=begin, end=finish)
    txs = _all_balance_transactions(begin, finish)
    revenue_charges = [c for c in charges if c.status in {"charged", "refunded"}]
    refunds = [t for t in txs if t.type == "refund"]
    topups = [t for t in txs if t.type == "topup"]
    adjustments = [t for t in txs if t.type in {"adjustment", "credit_grant"}]
    compute_by_charge, compute_totals = _compute_stats(revenue_charges)
    gross = repository.sum_decimal(c.gross_amount for c in revenue_charges)
    discounts = repository.sum_decimal(c.discount_amount for c in revenue_charges)
    credits = repository.sum_decimal(c.credit_amount for c in revenue_charges)
    usage_revenue = repository.sum_decimal(c.final_amount for c in revenue_charges)
    refund_total = repository.sum_decimal(t.amount for t in refunds)
    net_revenue = usage_revenue - refund_total
    compute_cost = compute_totals["compute_cost"]
    billable_seconds = sum(int(c.billable_seconds or 0) for c in revenue_charges)
    runtime_seconds = sum(int(c.runtime_seconds or 0) for c in revenue_charges)
    return {
        "charges": charges,
        "revenue_charges": revenue_charges,
        "transactions": txs,
        "topups": topups,
        "refunds": refunds,
        "adjustments": adjustments,
        "compute_by_charge": compute_by_charge,
        "compute_totals": compute_totals,
        "gross": gross,
        "discounts": discounts,
        "credits": credits,
        "usage_revenue": usage_revenue,
        "refund_total": refund_total,
        "net_revenue": net_revenue,
        "compute_cost": compute_cost,
        "profit": net_revenue - compute_cost,
        "topups_total": repository.sum_decimal(t.amount for t in topups),
        "adjustment_total": repository.sum_decimal(t.amount for t in adjustments),
        "billable_seconds": billable_seconds,
        "runtime_seconds": runtime_seconds,
    }


def _balance_split() -> dict:
    balances = Balance.objects.all()
    user_balance = repository.sum_decimal(b.amount for b in balances if b.entity_type == "user")
    org_balance = repository.sum_decimal(b.amount for b in balances if b.entity_type == "org")
    total = user_balance + org_balance
    return {
        "total": _amount(total),
        "user": _amount(user_balance),
        "org": _amount(org_balance),
        "user_share": "0" if total == 0 else f"{(user_balance / total * Decimal('100')):.2f}",
        "org_share": "0" if total == 0 else f"{(org_balance / total * Decimal('100')):.2f}",
    }


def _timeline(begin: datetime, finish: datetime, stats: dict) -> list[dict]:
    charges = stats["revenue_charges"]
    refunds = stats["refunds"]
    by_charge = stats["compute_by_charge"]
    buckets: dict[str, dict[str, Decimal]] = {}
    for charge in charges:
        if not charge.created_at:
            continue
        key = (_as_utc(charge.created_at) or charge.created_at).date().isoformat()
        bucket = buckets.setdefault(key, {"gross": Decimal("0"), "net": Decimal("0"), "compute_cost": Decimal("0"), "refunds": Decimal("0")})
        bucket["gross"] += _decimal(charge.gross_amount)
        bucket["net"] += _decimal(charge.final_amount)
        bucket["compute_cost"] += by_charge.get(str(charge.id), {}).get("compute_cost", Decimal("0"))
    for refund in refunds:
        if not refund.created_at:
            continue
        key = (_as_utc(refund.created_at) or refund.created_at).date().isoformat()
        bucket = buckets.setdefault(key, {"gross": Decimal("0"), "net": Decimal("0"), "compute_cost": Decimal("0"), "refunds": Decimal("0")})
        bucket["refunds"] += _decimal(refund.amount)
        bucket["net"] -= _decimal(refund.amount)
    return [
        {
            "label": key,
            "gross": f"{values['gross']:.2f}",
            "net": f"{values['net']:.2f}",
            "compute_cost": f"{values['compute_cost']:.2f}",
            "refunds": f"{values['refunds']:.2f}",
        }
        for key, values in sorted(buckets.items())
    ]


def _transaction_timeline(begin: datetime, finish: datetime, transactions: list[BalanceTransaction]) -> list[dict]:
    buckets: dict[str, Decimal] = {}
    for tx in transactions:
        if not tx.created_at:
            continue
        key = (_as_utc(tx.created_at) or tx.created_at).date().isoformat()
        buckets[key] = buckets.get(key, Decimal("0")) + _decimal(tx.amount)
    return [
        {"label": key, "value": f"{value:.2f}"}
        for key, value in sorted(buckets.items())
    ]


def _ranked_templates(charges: list[UsageCharge], compute_by_charge: dict[str, dict], limit: int = 8) -> list[dict]:
    rows: dict[str, dict] = {}
    for charge in charges:
        key = str(charge.template_id or "unknown")
        row = rows.setdefault(key, {"id": key, "label": _template_label(None if key == "unknown" else key), "revenue": Decimal("0"), "compute_cost": Decimal("0")})
        row["revenue"] += _decimal(charge.final_amount)
        row["compute_cost"] += compute_by_charge.get(str(charge.id), {}).get("compute_cost", Decimal("0"))
    total = repository.sum_decimal(row["revenue"] for row in rows.values())
    ranked = sorted(rows.values(), key=lambda row: row["revenue"], reverse=True)[:limit]
    result = []
    for row in ranked:
        margin = row["revenue"] - row["compute_cost"]
        margin_pct = _percent(margin, row["revenue"])
        share = _percent(row["revenue"], total)
        result.append({
            "id": row["id"],
            "label": row["label"],
            "revenue": f"{row['revenue']:.2f}",
            "revenue_display": _format_money(row["revenue"]),
            "compute_cost": f"{row['compute_cost']:.2f}",
            "compute_cost_display": _format_money(row["compute_cost"]),
            "margin": f"{margin:.2f}",
            "margin_display": _format_money(margin),
            "margin_percent": None if margin_pct is None else f"{margin_pct:.2f}",
            "share": "0" if share is None else f"{share:.2f}",
        })
    return result


def _breakdown_rows(charges: list[UsageCharge], compute_by_charge: dict[str, dict], key: str, limit: int = 12) -> list[dict]:
    rows: dict[str, dict] = {}
    for charge in charges:
        if key == "entity":
            row_key = f"{charge.entity_type}:{charge.entity_id}"
            label = _entity_label(charge.entity_type, str(charge.entity_id))
            meta = charge.entity_type.title()
        elif key == "plan":
            row_key = str(charge.plan_id or "default")
            label = _plan_label(None if row_key == "default" else row_key)
            meta = "Sandbox plan"
        else:
            row_key = str(charge.template_id or "unknown")
            label = _template_label(None if row_key == "unknown" else row_key)
            meta = "Sandbox template"
        row = rows.setdefault(row_key, {"id": row_key, "label": label, "meta": meta, "revenue": Decimal("0"), "compute_cost": Decimal("0"), "count": 0})
        row["revenue"] += _decimal(charge.final_amount)
        row["compute_cost"] += compute_by_charge.get(str(charge.id), {}).get("compute_cost", Decimal("0"))
        row["count"] += 1
    result = []
    for row in sorted(rows.values(), key=lambda item: item["revenue"], reverse=True)[:limit]:
        margin = row["revenue"] - row["compute_cost"]
        margin_pct = _percent(margin, row["revenue"])
        result.append({
            "id": row["id"],
            "label": row["label"],
            "meta": row["meta"],
            "count": row["count"],
            "revenue": f"{row['revenue']:.2f}",
            "revenue_display": _format_money(row["revenue"]),
            "compute_cost": f"{row['compute_cost']:.2f}",
            "compute_cost_display": _format_money(row["compute_cost"]),
            "margin_display": _format_money(margin),
            "margin_percent": None if margin_pct is None else f"{margin_pct:.2f}",
        })
    return result


def _activity_item_from_tx(tx: BalanceTransaction) -> dict:
    amount = _decimal(tx.amount)
    labels = {
        "usage_charge": "Usage charge",
        "topup": "Top-up",
        "refund": "Refund",
        "adjustment": "Adjustment",
        "credit_grant": "Credit grant",
        "failed_payment": "Failed payment",
    }
    return {
        "id": str(tx.id),
        "type": tx.type,
        "label": labels.get(tx.type, tx.type.replace("_", " ").title()),
        "entity_label": _entity_label(tx.entity_type, str(tx.entity_id)),
        "entity_type": tx.entity_type,
        "reference": tx.reference or str(tx.id)[:8],
        "provider": tx.provider or "internal",
        "amount": f"{amount:.2f}",
        "amount_display": _format_money(amount),
        "is_negative": amount < 0,
        "status": "completed" if tx.type != "failed_payment" else "failed",
        "created_at": tx.created_at,
        "created_label": _date_label(tx.created_at),
    }


def _usage_charge_row(charge: UsageCharge, compute_by_charge: dict[str, dict]) -> dict:
    row = usage_charge_dict(charge)
    compute = compute_by_charge.get(str(charge.id), {})
    revenue = _decimal(charge.final_amount)
    compute_cost = _decimal(compute.get("compute_cost", "0"))
    row.update({
        "template_name": _template_label(str(charge.template_id) if charge.template_id else None),
        "plan_name": _plan_label(charge.plan_id),
        "billable_runtime": _duration_display(int(charge.billable_seconds or 0)),
        "runtime": _duration_display(int(charge.runtime_seconds or 0)),
        "cost_hr_display": _format_money(charge.cost_hr_snapshot),
        "gross_display": _format_money(charge.gross_amount),
        "discounts_display": _format_money(_decimal(charge.discount_amount) + _decimal(charge.credit_amount)),
        "final_display": _format_money(revenue),
        "compute_cost_display": _format_money(compute_cost),
        "margin_display": _format_money(revenue - compute_cost),
    })
    return row


def _usage_efficiency(stats: dict, begin: datetime, finish: datetime) -> dict:
    instances = [
        inst for inst in SandboxInstance.objects.all()
        if _as_utc(inst.created_at) and _as_utc(begin) <= _as_utc(inst.created_at) < _as_utc(finish)
    ]
    started = len([inst for inst in instances if inst.started_at])
    failed = len([inst for inst in instances if inst.status == "failed"])
    completed = len([inst for inst in instances if inst.status in {"stopped", "expired", "killed"}])
    total_terminal = completed + failed
    completion_rate = Decimal("0") if total_terminal == 0 else (Decimal(completed) / Decimal(total_terminal) * Decimal("100")).quantize(Decimal("0.01"))
    billable_hours = Decimal(stats["billable_seconds"]) / Decimal(3600)
    runtime_hours = Decimal(stats["runtime_seconds"]) / Decimal(3600)
    avg_runtime = Decimal(stats["runtime_seconds"]) / Decimal(max(1, len(stats["revenue_charges"])))
    compute_hours = stats["compute_totals"]["vcpu_hours"]
    revenue_per_compute_hour = Decimal("0") if compute_hours == 0 else (stats["net_revenue"] / compute_hours).quantize(MONEY, rounding=ROUND_UP)
    return {
        "instances_started": started,
        "instances_failed": failed,
        "completion_rate": f"{completion_rate:.2f}",
        "runtime_hours": f"{runtime_hours:.2f}",
        "billable_hours": f"{billable_hours:.2f}",
        "average_runtime": _duration_display(int(avg_runtime)),
        "vcpu_hours": f"{stats['compute_totals']['vcpu_hours']:.2f}",
        "ram_gb_hours": f"{stats['compute_totals']['ram_gb_hours']:.2f}",
        "disk_gb_hours": f"{stats['compute_totals']['disk_gb_hours']:.2f}",
        "revenue_per_compute_hour": _format_money(revenue_per_compute_hour),
    }


def overview_console(period: str = "30d", start: str | None = None, end: str | None = None) -> dict:
    period_info = _period_context(period, start, end)
    current = _period_financials(period_info["begin"], period_info["finish"])
    previous = _period_financials(period_info["previous_begin"], period_info["previous_finish"])
    balances = _balance_split()
    margin_pct = _percent(current["profit"], current["net_revenue"])
    timeline = _timeline(period_info["begin"], period_info["finish"], current)
    net_revenue_series = _bucketed_values(
        period_info,
        [(charge.created_at, _decimal(charge.final_amount)) for charge in current["revenue_charges"]]
        + [(refund.created_at, -_decimal(refund.amount)) for refund in current["refunds"]],
    )
    balance_movement_series = _bucketed_values(
        period_info,
        [(tx.created_at, _decimal(tx.amount)) for tx in current["transactions"]],
    )
    balance_movement_total = repository.sum_decimal(_decimal(tx.amount) for tx in current["transactions"])
    compute_cost_series = _bucketed_values(
        period_info,
        [
            (
                charge.created_at,
                current["compute_by_charge"].get(str(charge.id), {}).get("compute_cost", Decimal("0")),
            )
            for charge in current["revenue_charges"]
        ],
    )
    return {
        "period": period_info,
        "health": {
            "net_revenue": {
                **_amount(current["net_revenue"]),
                "delta": _delta(current["net_revenue"], previous["net_revenue"]),
                "gross": _amount(current["gross"]),
                "refunds": _amount(current["refund_total"]),
                "discounts": _amount(current["discounts"] + current["credits"]),
                "sparkline": net_revenue_series,
                "bars": _mini_bar_series(net_revenue_series),
            },
            "cash_liability": {
                "topups": _amount(current["topups_total"]),
                "liability": balances["total"],
                "user_balance": balances["user"],
                "org_balance": balances["org"],
                "user_share": balances["user_share"],
                "org_share": balances["org_share"],
                "movement": _amount(balance_movement_total),
                "sparkline": balance_movement_series,
                "bars": _mini_bar_series(balance_movement_series),
            },
            "margin_compute": {
                "profit": _amount(current["profit"]),
                "compute_cost": _amount(current["compute_cost"]),
                "margin_percent": None if margin_pct is None else f"{margin_pct:.2f}",
                "revenue": _amount(current["net_revenue"]),
                "runtime_hours": f"{Decimal(current['billable_seconds']) / Decimal(3600):.2f}",
                "sparkline": compute_cost_series,
                "bars": _mini_bar_series(compute_cost_series),
            },
        },
        "revenue_cost_timeline": timeline,
        "template_contribution": _ranked_templates(current["revenue_charges"], current["compute_by_charge"]),
        "recent_activity": [_activity_item_from_tx(tx) for tx in current["transactions"][:8]],
        "has_activity": bool(current["revenue_charges"] or current["transactions"]),
    }


def dashboard(period: str = "30d", start: str | None = None, end: str | None = None) -> dict:
    return overview_console(period, start, end)


def usage_margin_console(
    period: str = "30d",
    start: str | None = None,
    end: str | None = None,
    *,
    page: int = 1,
    page_size: int = 25,
    breakdown: str = "plan",
) -> dict:
    period_info = _period_context(period, start, end)
    current = _period_financials(period_info["begin"], period_info["finish"])
    previous = _period_financials(period_info["previous_begin"], period_info["previous_finish"])
    margin_pct = _percent(current["profit"], current["net_revenue"])
    charges = current["revenue_charges"]
    total = len(charges)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, total_pages))
    start_index = (page - 1) * page_size
    page_rows = charges[start_index:start_index + page_size]
    breakdown = breakdown if breakdown in {"plan", "entity", "template"} else "plan"
    return {
        "period": period_info,
        "summary": [
            {"label": "Gross usage", **_amount(current["gross"]), "delta": _delta(current["gross"], previous["gross"])},
            {"label": "Discounts and credits", **_amount(current["discounts"] + current["credits"]), "delta": _delta(current["discounts"] + current["credits"], previous["discounts"] + previous["credits"])},
            {"label": "Net revenue", **_amount(current["net_revenue"]), "delta": _delta(current["net_revenue"], previous["net_revenue"])},
            {"label": "Estimated compute cost", **_amount(current["compute_cost"]), "delta": _delta(current["compute_cost"], previous["compute_cost"])},
            {"label": "Estimated margin", **_amount(current["profit"]), "suffix": None if margin_pct is None else f"{margin_pct:.2f}%"},
        ],
        "economics_timeline": _timeline(period_info["begin"], period_info["finish"], current),
        "template_unit_economics": _ranked_templates(charges, current["compute_by_charge"]),
        "usage_efficiency": _usage_efficiency(current, period_info["begin"], period_info["finish"]),
        "breakdown": {
            "selected": breakdown,
            "rows": _breakdown_rows(charges, current["compute_by_charge"], breakdown),
            "options": [
                {"value": "plan", "label": "Plan"},
                {"value": "entity", "label": "User/Organization"},
                {"value": "template", "label": "Template"},
            ],
        },
        "usage_charges": {
            "rows": [_usage_charge_row(c, current["compute_by_charge"]) for c in page_rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "base_url": "/platform/finance/revenue?" + filter_query_args(
                period=period_info["key"],
                start=period_info["start"] if period_info["is_custom"] else None,
                end=period_info["end"] if period_info["is_custom"] else None,
            ),
        },
        "has_activity": bool(charges),
    }


def revenue_report(period: str = "30d", start: str | None = None, end: str | None = None) -> dict:
    return usage_margin_console(period, start, end)


def usage_report(period: str = "30d", start: str | None = None, end: str | None = None) -> dict:
    return usage_margin_console(period, start, end)


def revenue_console(
    period: str = "30d",
    start: str | None = None,
    end: str | None = None,
    *,
    page: int = 1,
    page_size: int = 25,
    breakdown: str = "plan",
) -> dict:
    return usage_margin_console(period, start, end, page=page, page_size=page_size, breakdown=breakdown)


def _template_label(template_id: str | None) -> str:
    if not template_id:
        return "Sandbox"
    template = sandbox_repo.get_template(str(template_id))
    return template.name if template else str(template_id)


def _plan_label(plan_id: str | None) -> str:
    if not plan_id:
        return "Default plan"
    plan = sandbox_repo.get_plan(str(plan_id))
    return plan.name if plan else str(plan_id)


def _usage_charge_for_transaction(tx: BalanceTransaction) -> UsageCharge | None:
    charge = UsageCharge.objects.filter(balance_transaction_id=str(tx.id)).first()
    if charge:
        return charge
    if tx.instance_id:
        return repository.get_usage_charge_by_instance(str(tx.instance_id))
    return None


def _adjustment_for_transaction(tx: BalanceTransaction) -> FinanceAdjustment | None:
    return FinanceAdjustment.objects.filter(balance_transaction_id=str(tx.id)).first()


def _topup_for_transaction(tx: BalanceTransaction) -> TopupIntent | None:
    if not tx.topup_intent_id:
        return None
    return TopupIntent.objects.filter(id=tx.topup_intent_id).first()


def _credit_grant_for_transaction(tx: BalanceTransaction) -> CreditGrant | None:
    return CreditGrant.objects.filter(balance_transaction_id=str(tx.id)).first()


def _user_label(user_id) -> str | None:
    if not user_id:
        return None
    user = identity_repo.find_user_by_id(str(user_id))
    return (user.email or user.name) if user else None


def _refund_actor_label(tx: BalanceTransaction) -> str | None:
    # A refund's BalanceTransaction row doesn't store who issued it — only
    # the audit trail does: refund_usage_charge logs finance.refund.issued
    # with actor="user:<id>" and this transaction's id inside the compact
    # JSON detail payload.
    needle = f'"transaction_id":"{tx.id}"'
    for entry in SandboxAuditLog.objects.filter(event="finance.refund.issued").all():
        actor = str(entry.actor or "")
        if needle in (entry.detail or "") and actor.startswith("user:"):
            return _user_label(actor[len("user:"):])
    return None


def transaction_receipt_dict(tx: BalanceTransaction | None) -> dict | None:
    if tx is None:
        return None
    amount = _decimal(tx.amount)
    absolute = abs(amount)
    receipt = {
        "transaction_id": str(tx.id),
        "title": "Ledger Receipt",
        "number": f"RCPT-{str(tx.id)[:8].upper()}",
        "date": tx.created_at,
        "currency": "GBP",
        "billed_by": "CodeSandbox Platform",
        "billed_to": _entity_label(tx.entity_type, str(tx.entity_id)),
        "entity_type": tx.entity_type,
        "entity_id": str(tx.entity_id),
        "status": "completed",
        "provider": tx.provider or "internal",
        "provider_reference": tx.reference or "",
        "description": tx.description or "",
        "items": [],
        "subtotal": str(absolute),
        "discount": "0",
        "credit": "0",
        "refund": "0",
        "total": str(absolute),
        "meta": [],
        "refundable": False,
        "refundable_charge_id": "",
        "refundable_remaining": "0",
    }
    if tx.type == "usage_charge":
        charge = _usage_charge_for_transaction(tx)
        if charge:
            template_name = _template_label(str(charge.template_id) if charge.template_id else None)
            plan_name = _plan_label(charge.plan_id)
            remaining_refund = max(Decimal("0"), _decimal(charge.final_amount) - _decimal(charge.refunded_amount))
            receipt.update({
                "title": "Usage Invoice",
                "number": f"USG-{str(charge.id)[:8].upper()}",
                "currency": charge.currency or "GBP",
                "status": charge.status,
                "subtotal": str(charge.gross_amount or "0"),
                "discount": str(charge.discount_amount or "0"),
                "credit": str(charge.credit_amount or "0"),
                "refund": str(charge.refunded_amount or "0"),
                "total": str(charge.final_amount or "0"),
                "items": [{
                    "label": f"{template_name} · {plan_name}",
                    "quantity": f"{int(charge.billable_seconds or 0)}s billable runtime",
                    "rate": str(charge.cost_hr_snapshot or "0"),
                    "amount": str(charge.final_amount or "0"),
                }],
                "meta": [
                    ("Instance", str(charge.instance_id) if charge.instance_id else ""),
                    ("Template", template_name),
                    ("Plan", plan_name),
                    ("Runtime", f"{int(charge.runtime_seconds or 0)}s"),
                    ("Billing status", charge.status),
                ],
                "refundable": charge.status not in {"failed", "void"} and remaining_refund > 0,
                "refundable_charge_id": str(charge.id),
                "refundable_remaining": str(remaining_refund),
            })
        else:
            receipt["title"] = "Usage Invoice"
            receipt["items"] = [{"label": "Sandbox usage", "quantity": "1", "rate": str(absolute), "amount": str(absolute)}]
    elif tx.type == "topup":
        topup = _topup_for_transaction(tx)
        topup_provider = (topup.gateway if topup else tx.provider) or "payment"
        topup_reference = tx.reference or (topup.external_ref if topup else "") or ""
        receipt.update({
            "title": "Payment Receipt",
            "number": f"PAY-{str(tx.id)[:8].upper()}",
            "status": topup.status if topup else "completed",
            "provider": topup_provider,
            "provider_reference": topup_reference,
            "items": [{"label": "Balance top-up", "quantity": "1", "rate": str(absolute), "amount": str(absolute)}],
            "meta": [("Payment provider", topup_provider), ("Provider reference", topup_reference)],
        })
    elif tx.type == "refund":
        receipt.update({
            "title": "Refund Receipt",
            "number": f"REF-{str(tx.id)[:8].upper()}",
            "refund": str(absolute),
            "total": str(absolute),
            "items": [{"label": "Usage charge refund", "quantity": "1", "rate": str(absolute), "amount": str(absolute)}],
            "meta": [
                ("Original transaction reference", tx.reference or ""),
                ("Reason", tx.description or ""),
                ("Refunded by", _refund_actor_label(tx) or tx.provider or "finance"),
            ],
        })
    elif tx.type in {"adjustment", "credit_grant"}:
        adjustment = _adjustment_for_transaction(tx)
        grant = _credit_grant_for_transaction(tx) if tx.type == "credit_grant" else None
        actor_id = grant.granted_by if grant else (adjustment.created_by if adjustment else None)
        direction = "Add credit" if amount >= 0 else "Deduct balance"
        receipt.update({
            "title": "Internal Adjustment Note" if tx.type == "adjustment" else "Credit Grant Receipt",
            "number": f"ADJ-{str(tx.id)[:8].upper()}",
            "items": [{"label": direction, "quantity": "1", "rate": str(absolute), "amount": str(absolute)}],
            "meta": [
                ("Direction", direction),
                ("Reason", ((grant.reason if grant else None) or (adjustment.reason if adjustment else None) or tx.description) or ""),
                ("Adjusted by", _user_label(actor_id) or tx.provider or "finance"),
            ],
        })
    elif tx.type == "failed_payment":
        receipt.update({
            "title": "Failed Payment Record",
            "number": f"FAIL-{str(tx.id)[:8].upper()}",
            "status": "failed",
            "items": [{"label": "Failed payment", "quantity": "1", "rate": str(absolute), "amount": str(absolute)}],
        })
    else:
        receipt["items"] = [{"label": tx.type.replace("_", " ").title(), "quantity": "1", "rate": str(absolute), "amount": str(absolute)}]
    for key in ("subtotal", "discount", "credit", "refund", "total", "refundable_remaining"):
        receipt[f"{key}_display"] = f"{_decimal(receipt.get(key)):.2f}"
    for item in receipt["items"]:
        item["rate_display"] = f"{_decimal(item.get('rate')):.2f}"
        item["amount_display"] = f"{_decimal(item.get('amount')):.2f}"
    receipt["total_in_words"] = _amount_in_words(receipt["total"], receipt["currency"])
    return receipt


def ledger_console(
    *,
    selected_id: str | None = None,
    tx_type: str | None = None,
    search: str | None = None,
    start: str | None = None,
    end: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    begin = _safe_date(start)
    finish = _safe_date(end)
    if finish:
        finish = finish + timedelta(days=1) if finish.hour == finish.minute == finish.second == 0 else finish
    rows = _all_balance_transactions(begin, finish)
    if tx_type and tx_type != "all":
        rows = [r for r in rows if r.type == tx_type]
    if search:
        q = search.lower().strip()
        # One label lookup per distinct entity, not per transaction — this
        # branch walks every transaction in range, and _entity_label is a DB
        # query each time it's called.
        label_cache: dict[tuple[str, str], str] = {}

        def cached_label(r) -> str:
            key = (r.entity_type, str(r.entity_id))
            if key not in label_cache:
                label_cache[key] = _entity_label(*key).lower()
            return label_cache[key]

        rows = [
            r for r in rows
            if q in r.type.lower()
            or q in cached_label(r)
            or q in str(r.reference or "").lower()
            or q in str(r.description or "").lower()
        ]
    total = len(rows)
    total_pages = max(1, math.ceil(total / page_size))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * page_size
    page_rows = rows[offset:offset + page_size]
    selected = repository.get_balance_transaction(selected_id) if selected_id else None
    selected_id = str(selected.id) if selected else ""
    return {
        "rows": [transaction_dict(r) | _activity_item_from_tx(r) | {"selected": str(r.id) == selected_id} for r in page_rows],
        "selected_receipt": transaction_receipt_dict(selected),
        "selected_tx_id": selected_id,
        "tx_type": tx_type or "all",
        "search": search or "",
        "start": start or "",
        "end": end or "",
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "base_url": "/platform/finance/ledger?" + filter_query_args(type=tx_type or "all", search=search or "", start=start or "", end=end or ""),
    }


def transaction_document(transaction_id: str | None) -> dict | None:
    if not transaction_id:
        return None
    tx = repository.get_balance_transaction(transaction_id)
    return transaction_receipt_dict(tx)


def coupon_redemption_dict(redemption) -> dict:
    coupon = repository.get_coupon(str(redemption.coupon_id)) if redemption.coupon_id else None
    redeemed_amount = _decimal(redemption.redeemed_amount)
    return {
        "id": str(redemption.id),
        "coupon_code": coupon.code if coupon else str(redemption.coupon_id),
        "entity_type": redemption.entity_type,
        "entity_id": str(redemption.entity_id),
        "entity_label": _entity_label(redemption.entity_type, str(redemption.entity_id)),
        "redeemed_amount": str(redeemed_amount),
        "redeemed_amount_display": f"{redeemed_amount:.2f}",
        "currency": redemption.currency or "GBP",
        "created_at": redemption.created_at,
    }


def entity_options(limit: int = 30, search: str | None = None) -> list[dict]:
    users, _ = identity_repo.list_users(search=search, page=1, page_size=limit)
    orgs, _ = org_repo.list_organizations(search=search, page=1, page_size=limit)
    options = [
        {"type": "user", "id": str(u.id), "label": u.email or u.name, "description": u.name or "User"}
        for u in users
    ]
    options.extend(
        {"type": "org", "id": str(o.id), "label": o.name, "description": o.slug}
        for o in orgs
    )
    return options


def _redemption_timeline(redemptions: list, begin: datetime, finish: datetime) -> list[dict]:
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for redemption in redemptions:
        if not redemption.created_at:
            continue
        created_at = _as_utc(redemption.created_at) or redemption.created_at
        if created_at < begin or created_at >= finish:
            continue
        key = created_at.date().isoformat()
        bucket = buckets.setdefault(key, {"amount": Decimal("0"), "count": 0})
        bucket["amount"] = _decimal(bucket["amount"]) + _decimal(redemption.redeemed_amount)
        bucket["count"] = int(bucket["count"]) + 1
    return [
        {"label": key, "net": f"{_decimal(values['amount']):.2f}", "compute_cost": "0", "count": int(values["count"])}
        for key, values in sorted(buckets.items())
    ]


def promotions_console(period: str = "30d", start: str | None = None, end: str | None = None) -> dict:
    period_info = _period_context(period, start, end)
    coupons = list_coupon_dicts()
    credits = list_credit_grant_dicts()
    all_redemptions = repository.list_coupon_redemptions(limit=200)
    period_redemptions = [
        r for r in all_redemptions
        if _as_utc(r.created_at) and period_info["begin"] <= _as_utc(r.created_at) < period_info["finish"]
    ]
    period_charges = repository.list_usage_charges(start=period_info["begin"], end=period_info["finish"])
    return {
        "period": period_info,
        "summary": {
            "active_coupons": len([c for c in coupons if c["status"] == "active"]),
            "credit_outstanding": _amount(repository.sum_decimal(c["remaining_amount"] for c in credits if c["status"] == "active")),
            "redemptions_this_period": len(period_redemptions),
            "discounts_this_period": _amount(repository.sum_decimal(_decimal(c.discount_amount) + _decimal(c.credit_amount) for c in period_charges)),
        },
        "coupons": coupons,
        "credits": credits,
        "redemptions": [coupon_redemption_dict(r) for r in all_redemptions[:30]],
        "redemption_timeline": _redemption_timeline(all_redemptions, period_info["begin"], period_info["finish"]),
        "has_redemptions": bool(period_redemptions),
        "entity_options": entity_options(),
    }


def usage_charge_dict(c: UsageCharge) -> dict:
    return {
        "id": str(c.id),
        "instance_id": str(c.instance_id) if c.instance_id else "",
        "entity_type": c.entity_type,
        "entity_id": str(c.entity_id),
        "entity_label": _entity_label(c.entity_type, str(c.entity_id)),
        "template_id": str(c.template_id) if c.template_id else "",
        "plan_id": c.plan_id or "",
        "runtime_seconds": int(c.runtime_seconds or 0),
        "billable_seconds": int(c.billable_seconds or 0),
        "cost_hr_snapshot": str(c.cost_hr_snapshot or "0"),
        "gross_amount": str(c.gross_amount or "0"),
        "discount_amount": str(c.discount_amount or "0"),
        "credit_amount": str(c.credit_amount or "0"),
        "final_amount": str(c.final_amount or "0"),
        "refunded_amount": str(c.refunded_amount or "0"),
        "currency": c.currency or "GBP",
        "status": c.status,
        "created_at": c.created_at,
        "charged_at": c.charged_at,
    }


def transaction_dict(tx: BalanceTransaction) -> dict:
    amount = _decimal(tx.amount)
    return {
        "id": str(tx.id),
        "entity_type": tx.entity_type,
        "entity_id": str(tx.entity_id),
        "entity_label": _entity_label(tx.entity_type, str(tx.entity_id)),
        "type": tx.type,
        "amount": str(amount),
        "absolute_amount": str(abs(amount)),
        "absolute_amount_display": f"{abs(amount):.2f}",
        "provider": tx.provider or "",
        "reference": tx.reference or "",
        "description": tx.description or "",
        "created_at": tx.created_at,
    }


def coupon_dict(c: Coupon) -> dict:
    return {
        "id": str(c.id),
        "code": c.code,
        "name": c.name,
        "description": c.description or "",
        "discount_type": c.discount_type,
        "value": str(c.value or "0"),
        "value_display": f"{_decimal(c.value):.2f}",
        "currency": c.currency or "",
        "max_redemptions": c.max_redemptions,
        "per_entity_limit": c.per_entity_limit,
        "starts_at": c.starts_at,
        "expires_at": c.expires_at,
        "status": c.status,
        "redemptions": repository.count_coupon_redemptions(str(c.id)),
        "applies_to_template_id": str(c.applies_to_template_id) if c.applies_to_template_id else "",
        "applies_to_plan_id": c.applies_to_plan_id or "",
        "applies_to_user_id": str(c.applies_to_user_id) if c.applies_to_user_id else "",
        "applies_to_org_id": str(c.applies_to_org_id) if c.applies_to_org_id else "",
        "created_at": c.created_at,
    }


def credit_grant_dict(g: CreditGrant) -> dict:
    amount = _decimal(g.amount)
    remaining_amount = _decimal(g.remaining_amount)
    return {
        "id": str(g.id),
        "entity_type": g.entity_type,
        "entity_id": str(g.entity_id),
        "entity_label": _entity_label(g.entity_type, str(g.entity_id)),
        "amount": str(amount),
        "amount_display": f"{amount:.2f}",
        "remaining_amount": str(remaining_amount),
        "remaining_amount_display": f"{remaining_amount:.2f}",
        "currency": g.currency or "GBP",
        "reason": g.reason or "",
        "status": g.status,
        "expires_at": g.expires_at,
        "created_at": g.created_at,
        "balance_transaction_id": str(g.balance_transaction_id) if g.balance_transaction_id else "",
    }


def invoice_dict(inv) -> dict:
    return {
        "id": str(inv.id),
        "invoice_no": inv.invoice_no,
        "entity_type": inv.entity_type,
        "entity_id": str(inv.entity_id),
        "entity_label": _entity_label(inv.entity_type, str(inv.entity_id)),
        "period_start": inv.period_start,
        "period_end": inv.period_end,
        "subtotal": str(inv.subtotal or "0"),
        "discount_total": str(inv.discount_total or "0"),
        "credit_total": str(inv.credit_total or "0"),
        "refund_total": str(inv.refund_total or "0"),
        "total": str(inv.total or "0"),
        "currency": inv.currency or "GBP",
        "status": inv.status,
        "created_at": inv.created_at,
    }


def adjustment_dict(adj) -> dict:
    return {
        "id": str(adj.id),
        "entity_type": adj.entity_type,
        "entity_id": str(adj.entity_id),
        "entity_label": _entity_label(adj.entity_type, str(adj.entity_id)),
        "amount": str(adj.amount or "0"),
        "currency": adj.currency or "GBP",
        "reason": adj.reason or "",
        "created_at": adj.created_at,
        "balance_transaction_id": str(adj.balance_transaction_id) if adj.balance_transaction_id else "",
    }


def list_coupon_dicts(status: str | None = None) -> list[dict]:
    return [coupon_dict(c) for c in repository.list_coupons(status=status)]


def list_credit_grant_dicts(**kwargs) -> list[dict]:
    return [credit_grant_dict(g) for g in repository.list_credit_grants(**kwargs)]


def list_invoice_dicts(**kwargs) -> list[dict]:
    return [invoice_dict(i) for i in repository.list_invoices(**kwargs)]


def list_transaction_dicts(limit: int = 100) -> list[dict]:
    return [transaction_dict(t) for t in _all_balance_transactions()[:limit]]


def list_topup_dicts(limit: int = 100) -> list[dict]:
    return [transaction_dict(t) for t in _all_balance_transactions() if t.type == "topup"][:limit]


def list_usage_charge_dicts(limit: int = 100) -> list[dict]:
    return [usage_charge_dict(c) for c in repository.list_usage_charges(limit=limit)]


def finance_detail_for_entity(entity_type: str, entity_id: str) -> dict | None:
    if entity_type not in {"user", "org"}:
        return None
    balance = sandbox_repo.get_or_create_balance(entity_type, entity_id)
    txs = sandbox_repo.list_transactions(entity_type, entity_id, limit=25)
    charges = repository.list_usage_charges(entity_type=entity_type, entity_id=entity_id, limit=25)
    refunds = [t for t in txs if t.type == "refund"]
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_label": _entity_label(entity_type, entity_id),
        "balance": {
            "amount": str(balance.amount or "0"),
            "reserved_amount": str(balance.reserved_amount or "0"),
            "available_amount": str(max(Decimal("0"), _decimal(balance.amount) - _decimal(balance.reserved_amount))),
        },
        "totals": [
            {"label": "Top-ups", "value": _format_money(repository.sum_decimal(t.amount for t in txs if t.type == "topup"))},
            {"label": "Usage revenue", "value": _format_money(repository.sum_decimal(c.final_amount for c in charges))},
            {"label": "Coupons used", "value": _format_money(repository.sum_decimal(c.discount_amount for c in charges))},
            {"label": "Credits granted", "value": _format_money(repository.sum_decimal(g.amount for g in repository.list_credit_grants(entity_type=entity_type, entity_id=entity_id)))},
            {"label": "Refunds", "value": _format_money(repository.sum_decimal(t.amount for t in refunds))},
        ],
        "charges": [usage_charge_dict(c) for c in charges],
        "transactions": [transaction_dict(t) for t in txs],
        "credits": list_credit_grant_dicts(entity_type=entity_type, entity_id=entity_id),
        "invoices": list_invoice_dicts(entity_type=entity_type, entity_id=entity_id),
    }
