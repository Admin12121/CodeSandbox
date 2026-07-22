from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from nexorm.database import default_db
from nexorm.exceptions import IntegrityError
from nexorm.transaction import transaction

from codesandbox.features.sandbox.models import BalanceTransaction, TopupIntent
from codesandbox.features.sandbox.repository import (
    add_balance_transaction,
    log_instance_event,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _intent_for_update(gateway: str, external_ref: str) -> TopupIntent | None:
    placeholder = "?" if default_db.backend == "sqlite" else "%s"
    suffix = "" if default_db.backend == "sqlite" else " FOR UPDATE"
    row = default_db.fetchone(
        f"SELECT * FROM topup_intents WHERE gateway = {placeholder} "
        f"AND external_ref = {placeholder}{suffix}",
        [gateway, external_ref],
    )
    return TopupIntent.from_row(row, db=default_db) if row else None


def create_topup_intent(
    entity_type: str,
    entity_id: str,
    gateway: str,
    charge_currency: str,
    charge_amount: Decimal,
    external_ref: str,
    *,
    intent_id: str | None = None,
    idempotency_key: str | None = None,
) -> TopupIntent:
    intent = TopupIntent(
        id=intent_id or str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        gateway=gateway,
        status="pending",
        charge_currency=charge_currency,
        charge_amount=charge_amount,
        external_ref=external_ref,
        idempotency_key=idempotency_key,
        created_at=_now(),
        updated_at=_now(),
        expires_at=_now() + timedelta(hours=24),
    )
    try:
        intent.save()
    except IntegrityError:
        existing = (
            get_topup_intent_by_idempotency_key(idempotency_key)
            if idempotency_key
            else get_topup_intent_by_ref(gateway, external_ref)
        )
        if existing is None:
            raise
        return existing
    return intent


def get_topup_intent(intent_id: str) -> TopupIntent | None:
    return TopupIntent.objects.filter(id=intent_id).first()


def get_topup_intent_by_ref(gateway: str, external_ref: str) -> TopupIntent | None:
    return TopupIntent.objects.filter(gateway=gateway, external_ref=external_ref).first()


def get_topup_intent_by_idempotency_key(key: str | None) -> TopupIntent | None:
    if not key:
        return None
    return TopupIntent.objects.filter(idempotency_key=key).first()


def complete_topup(
    *,
    gateway: str,
    external_ref: str,
    credit_amount_gbp: Decimal,
    fx_rate: Decimal | None,
    provider_event_id: str | None,
    provider_reference: str | None,
    description: str,
) -> tuple[BalanceTransaction | None, bool]:
    amount = Decimal(str(credit_amount_gbp))
    if amount <= 0:
        raise ValueError("Top-up credit must be positive.")
    with transaction.atomic():
        intent = _intent_for_update(gateway, external_ref)
        if intent is None:
            return None, False
        if intent.status == "completed":
            tx = (
                BalanceTransaction.objects.filter(id=intent.balance_transaction_id).first()
                if intent.balance_transaction_id
                else None
            )
            return tx, False
        if intent.status in {"failed", "expired"}:
            return None, False

        intent.status = "processing"
        intent.updated_at = _now()
        intent.provider_event_id = provider_event_id or intent.provider_event_id
        intent.save()

        tx = add_balance_transaction(
            entity_type=intent.entity_type,
            entity_id=intent.entity_id,
            tx_type="topup",
            amount=amount,
            topup_intent_id=str(intent.id),
            provider=gateway,
            reference=provider_reference or external_ref,
            idempotency_key=f"topup:{gateway}:{external_ref}",
            description=description,
        )
        intent.status = "completed"
        intent.credit_amount_gbp = amount
        intent.fx_rate = fx_rate
        intent.balance_transaction_id = str(tx.id)
        intent.updated_at = _now()
        intent.resolved_at = _now()
        intent.save()
        log_instance_event(
            None,
            "topup.completed",
            actor="payment_provider",
            detail=json.dumps({
                "gateway": gateway,
                "external_ref": external_ref,
                "transaction_id": str(tx.id),
                "amount_gbp": str(amount),
            }),
        )
        return tx, True


def mark_topup_failed(
    gateway: str,
    external_ref: str,
    provider_event_id: str | None = None,
) -> bool:
    with transaction.atomic():
        intent = _intent_for_update(gateway, external_ref)
        if intent is None or intent.status in {"completed", "failed", "expired"}:
            return False
        intent.status = "failed"
        intent.provider_event_id = provider_event_id or intent.provider_event_id
        intent.updated_at = _now()
        intent.resolved_at = _now()
        intent.save()
        return True
