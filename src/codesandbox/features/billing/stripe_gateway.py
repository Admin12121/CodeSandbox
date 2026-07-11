"""Stripe PaymentIntent top-ups credited only by signed webhook events."""

from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal

import stripe

from codesandbox.config import get_settings
from codesandbox.features.billing import repository as billing_repo

log = logging.getLogger(__name__)

MIN_TOPUP_GBP = Decimal("1.00")
MAX_TOPUP_GBP = Decimal("10000.00")


class StripeTopupError(Exception):
    pass


def create_payment_intent(
    entity_type: str,
    entity_id: str,
    amount_gbp: Decimal,
    request_key: str | None = None,
) -> str:
    amount_gbp = amount_gbp.quantize(Decimal("0.01"))
    if amount_gbp < MIN_TOPUP_GBP:
        raise StripeTopupError(f"Minimum top-up is GBP {MIN_TOPUP_GBP}.")
    if amount_gbp > MAX_TOPUP_GBP:
        raise StripeTopupError("Top-up amount exceeds the per-payment limit.")

    settings = get_settings()
    if not settings.stripe_secret_key:
        raise StripeTopupError("Stripe is not configured.")
    stripe.api_key = settings.stripe_secret_key

    raw_key = request_key or str(uuid.uuid4())
    scoped_key = "stripe:" + hashlib.sha256(
        f"{entity_type}:{entity_id}:{raw_key}".encode()
    ).hexdigest()
    existing = billing_repo.get_topup_intent_by_idempotency_key(scoped_key)
    if existing is not None:
        payment_intent = stripe.PaymentIntent.retrieve(existing.external_ref)
        return payment_intent.client_secret

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
    stored = billing_repo.create_topup_intent(
        entity_type=entity_type,
        entity_id=entity_id,
        gateway="stripe",
        charge_currency="GBP",
        charge_amount=amount_gbp,
        external_ref=payment_intent.id,
        intent_id=intent_id,
        idempotency_key=scoped_key,
    )
    if stored.external_ref != payment_intent.id:
        payment_intent = stripe.PaymentIntent.retrieve(stored.external_ref)
    return payment_intent.client_secret


def handle_webhook(payload: bytes, sig_header: str) -> None:
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise StripeTopupError("Stripe webhook secret is not configured.")
    event = stripe.Webhook.construct_event(
        payload, sig_header, settings.stripe_webhook_secret
    )
    event_type = str(event["type"])
    if event_type not in {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_intent.canceled",
    }:
        return

    payment_intent = event["data"]["object"].to_dict()
    external_ref = str(payment_intent["id"])
    intent = billing_repo.get_topup_intent_by_ref("stripe", external_ref)
    if intent is None:
        log.warning("Stripe event for unknown PaymentIntent id=%s", external_ref)
        return
    provider_event_id = str(event["id"])
    if event_type != "payment_intent.succeeded":
        billing_repo.mark_topup_failed(
            "stripe", external_ref, provider_event_id=provider_event_id
        )
        return

    amount_gbp = Decimal(str(payment_intent["amount_received"])) / 100
    if str(payment_intent.get("currency") or "").lower() != "gbp":
        raise StripeTopupError("Stripe payment currency does not match the intent.")
    if amount_gbp != Decimal(str(intent.charge_amount)):
        raise StripeTopupError("Stripe payment amount does not match the intent.")
    metadata = payment_intent.get("metadata") or {}
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
