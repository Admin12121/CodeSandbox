"""Stripe PaymentIntent top-ups with webhook and server-side reconciliation."""
from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import stripe

from codesandbox.config import get_settings
from codesandbox.features.billing import repository as billing_repo

log = logging.getLogger(__name__)

# Fail reasonably quickly when the app container cannot reach Stripe.
stripe.default_http_client = stripe.RequestsClient(timeout=10)

MIN_TOPUP_GBP = Decimal("1.00")
MAX_TOPUP_GBP = Decimal("10000.00")


class StripeTopupError(Exception):
    pass


def _configure_stripe() -> None:
    secret = get_settings().stripe_secret_key.strip()
    if not secret:
        raise StripeTopupError("Stripe is not configured.")
    stripe.api_key = secret


def _plain_stripe_value(value: Any) -> Any:
    """Convert StripeObject values into plain Python containers.

    StripeObject exposes mapping-like access, but it is not safely compatible
    with ``dict(value)``. Python falls back to sequence-style ``__getitem__(0)``
    and Stripe raises ``KeyError: 0``. Use Stripe's serializers or its internal
    data mapping instead.
    """
    if isinstance(value, dict):
        return {str(key): _plain_stripe_value(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_stripe_value(inner) for inner in value]

    for method_name in ("to_dict_recursive", "to_dict"):
        serializer = getattr(value, method_name, None)
        if callable(serializer):
            try:
                result = serializer()
            except Exception:
                result = None
            if isinstance(result, dict):
                return _plain_stripe_value(result)

    data = getattr(value, "_data", None)
    if isinstance(data, dict):
        return {str(key): _plain_stripe_value(inner) for key, inner in data.items()}
    return value


def _as_dict(value: Any) -> dict[str, Any]:
    result = _plain_stripe_value(value)
    if isinstance(result, dict):
        return result
    raise StripeTopupError("Stripe returned an invalid PaymentIntent.")


def _payment_intent_amount_gbp(payment_intent: dict[str, Any]) -> Decimal:
    raw_amount = payment_intent.get("amount_received")
    if raw_amount in (None, ""):
        raw_amount = payment_intent.get("amount")
    try:
        return (Decimal(str(raw_amount)) / 100).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise StripeTopupError("Stripe payment amount is invalid.") from exc


def create_payment_intent(
    entity_type: str,
    entity_id: str,
    amount_gbp: Decimal,
    request_key: str | None = None,
) -> str:
    try:
        amount_gbp = Decimal(str(amount_gbp)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise StripeTopupError("Invalid amount.") from exc
    if not amount_gbp.is_finite() or amount_gbp < MIN_TOPUP_GBP:
        raise StripeTopupError(f"Minimum top-up is GBP {MIN_TOPUP_GBP}.")
    if amount_gbp > MAX_TOPUP_GBP:
        raise StripeTopupError("Top-up amount exceeds the per-payment limit.")

    _configure_stripe()
    raw_key = request_key or str(uuid.uuid4())
    scoped_key = "stripe:" + hashlib.sha256(
        f"{entity_type}:{entity_id}:{raw_key}".encode("utf-8")
    ).hexdigest()
    existing = billing_repo.get_topup_intent_by_idempotency_key(scoped_key)
    if existing is not None:
        if existing.status in {"failed", "expired"}:
            raise StripeTopupError("This payment attempt is no longer usable. Please retry.")
        payment_intent = stripe.PaymentIntent.retrieve(existing.external_ref)
        if str(getattr(payment_intent, "status", "") or "") == "canceled":
            billing_repo.mark_topup_failed("stripe", existing.external_ref)
            raise StripeTopupError("This payment attempt expired. Please try again.")
        client_secret = getattr(payment_intent, "client_secret", None)
        if not client_secret:
            raise StripeTopupError("Stripe did not return a client secret.")
        return str(client_secret)

    intent_id = str(uuid.uuid4())
    payment_intent = stripe.PaymentIntent.create(
        amount=int(amount_gbp * 100),
        currency="gbp",
        payment_method_types=["card"],
        metadata={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "topup_intent_id": intent_id,
        },
        idempotency_key=scoped_key,
    )
    external_ref = str(getattr(payment_intent, "id", "") or "")
    client_secret = str(getattr(payment_intent, "client_secret", "") or "")
    if not external_ref or not client_secret:
        raise StripeTopupError("Stripe did not create a usable PaymentIntent.")

    stored = billing_repo.create_topup_intent(
        entity_type=entity_type,
        entity_id=entity_id,
        gateway="stripe",
        charge_currency="GBP",
        charge_amount=amount_gbp,
        external_ref=external_ref,
        intent_id=intent_id,
        idempotency_key=scoped_key,
    )
    if stored.external_ref != external_ref:
        payment_intent = stripe.PaymentIntent.retrieve(stored.external_ref)
        client_secret = str(getattr(payment_intent, "client_secret", "") or "")
        if not client_secret:
            raise StripeTopupError("Stripe did not return a client secret.")
    return client_secret


def _validate_and_complete(
    payment_intent_value: Any,
    *,
    provider_event_id: str | None,
) -> str:
    payment_intent = _as_dict(payment_intent_value)
    external_ref = str(payment_intent.get("id") or "")
    if not external_ref:
        raise StripeTopupError("Stripe PaymentIntent id is missing.")

    intent = billing_repo.get_topup_intent_by_ref("stripe", external_ref)
    if intent is None:
        raise StripeTopupError("Unknown Stripe PaymentIntent.")
    if intent.status == "completed":
        return "completed"
    if intent.status in {"failed", "expired"}:
        return "failed"

    status = str(payment_intent.get("status") or "")
    if status == "canceled":
        billing_repo.mark_topup_failed(
            "stripe", external_ref, provider_event_id=provider_event_id
        )
        return "failed"
    if status != "succeeded":
        # A failed card attempt usually leaves the PaymentIntent in
        # requires_payment_method and it can be retried. Do not permanently
        # fail our local intent on payment_intent.payment_failed.
        return status or "pending"

    currency = str(payment_intent.get("currency") or "").lower()
    if currency != "gbp":
        raise StripeTopupError("Stripe payment currency does not match the intent.")
    try:
        amount_gbp = _payment_intent_amount_gbp(payment_intent)
        expected_amount = Decimal(str(intent.charge_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise StripeTopupError("Stripe payment amount is invalid.") from exc
    if amount_gbp != expected_amount:
        raise StripeTopupError("Stripe payment amount does not match the intent.")

    metadata = payment_intent.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = dict(metadata)
    if (
        str(metadata.get("entity_type") or "") != intent.entity_type
        or str(metadata.get("entity_id") or "") != intent.entity_id
        or str(metadata.get("topup_intent_id") or "") != str(intent.id)
    ):
        raise StripeTopupError("Stripe payment metadata does not match the intent.")

    billing_repo.complete_topup(
        gateway="stripe",
        external_ref=external_ref,
        credit_amount_gbp=amount_gbp,
        fx_rate=None,
        provider_event_id=provider_event_id,
        provider_reference=external_ref,
        description=f"Stripe top-up ({external_ref})",
    )
    return "completed"


def reconcile_payment_intent(external_ref: str) -> str:
    """Retrieve a PaymentIntent from Stripe and reconcile it idempotently.

    This supports local development where Stripe cannot call a localhost
    webhook. Production should still configure the signed webhook endpoint.
    """
    if not external_ref.startswith("pi_") or len(external_ref) > 120:
        raise StripeTopupError("Invalid Stripe PaymentIntent reference.")
    _configure_stripe()
    payment_intent = stripe.PaymentIntent.retrieve(external_ref)
    return _validate_and_complete(payment_intent, provider_event_id=None)


def handle_webhook(payload: bytes, sig_header: str) -> None:
    settings = get_settings()
    webhook_secret = settings.stripe_webhook_secret.strip()
    if not webhook_secret:
        raise StripeTopupError("Stripe webhook secret is not configured.")
    _configure_stripe()
    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    event_type = str(event["type"])
    if event_type not in {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    }:
        return

    payment_intent = event["data"]["object"]
    external_ref = str(payment_intent.get("id") or "")
    if billing_repo.get_topup_intent_by_ref("stripe", external_ref) is None:
        log.warning("Stripe event for unknown PaymentIntent id=%s", external_ref)
        return

    # payment_failed is not terminal for a reusable PaymentIntent. The UI can
    # submit another card against the same intent; only canceled is terminal.
    if event_type == "payment_intent.payment_failed":
        return
    _validate_and_complete(
        payment_intent,
        provider_event_id=str(event["id"]),
    )
