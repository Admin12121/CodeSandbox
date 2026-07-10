from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from codesandbox.features.sandbox.models import TopupIntent


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_topup_intent(
    entity_type: str,
    entity_id: str,
    gateway: str,
    charge_currency: str,
    charge_amount: Decimal,
    external_ref: str,
) -> TopupIntent:
    intent = TopupIntent(
        id=str(uuid.uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        gateway=gateway,
        status="pending",
        charge_currency=charge_currency,
        charge_amount=charge_amount,
        external_ref=external_ref,
        created_at=_now(),
    )
    intent.save()
    return intent


def get_topup_intent(intent_id: str) -> TopupIntent | None:
    return TopupIntent.objects.filter(id=intent_id).first()


def get_topup_intent_by_ref(gateway: str, external_ref: str) -> TopupIntent | None:
    return TopupIntent.objects.filter(gateway=gateway, external_ref=external_ref).first()


def mark_topup_completed(
    intent: TopupIntent,
    credit_amount_gbp: Decimal,
    fx_rate: Decimal | None,
    balance_transaction_id: str,
) -> None:
    intent.status = "completed"
    intent.credit_amount_gbp = credit_amount_gbp
    intent.fx_rate = fx_rate
    intent.balance_transaction_id = balance_transaction_id
    intent.resolved_at = _now()
    intent.save()


def mark_topup_failed(intent: TopupIntent) -> None:
    intent.status = "failed"
    intent.resolved_at = _now()
    intent.save()
