"""Stripe topups: embedded card form (PaymentIntent + CardElement), confirmed
only via webhook.

The client confirms the PaymentIntent directly with Stripe.js
(`confirmCardPayment`) so the card form can stay inline instead of
redirecting to a hosted page — but that client-side result is UI feedback
only. It is never what credits the balance: a card confirmation result is
just what the browser was told, and the browser is not a trusted source of
truth. The only trusted confirmation is the signed `payment_intent.succeeded`
webhook event, verified with the Stripe webhook secret before any code here
trusts its contents. The frontend polls /billing/topup/status afterwards to
find out once that webhook has actually landed.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import stripe

from codesandbox.config import get_settings
from codesandbox.features.billing import repository as billing_repo
from codesandbox.features.sandbox.repository import add_balance_transaction

log = logging.getLogger(__name__)

MIN_TOPUP_GBP = Decimal("1.00")


class StripeTopupError(Exception):
    pass


def create_payment_intent(
    entity_type: str,
    entity_id: str,
    amount_gbp: Decimal,
) -> str:
    """Creates a pending TopupIntent + a Stripe PaymentIntent, returns the
    client_secret the frontend needs to confirm payment via Stripe.js."""
    if amount_gbp < MIN_TOPUP_GBP:
        raise StripeTopupError(f"Minimum top-up is £{MIN_TOPUP_GBP}.")

    settings = get_settings()
    if not settings.stripe_secret_key:
        raise StripeTopupError("Stripe is not configured.")
    stripe.api_key = settings.stripe_secret_key

    amount_pence = int((amount_gbp * 100).to_integral_value())

    # card-only, not automatic_payment_methods: the frontend calls
    # confirmCardPayment specifically (matches the embedded CardElement UI),
    # which only works against a card-compatible intent.
    intent = stripe.PaymentIntent.create(
        amount=amount_pence,
        currency="gbp",
        payment_method_types=["card"],
        metadata={"entity_type": entity_type, "entity_id": entity_id},
    )

    billing_repo.create_topup_intent(
        entity_type=entity_type,
        entity_id=entity_id,
        gateway="stripe",
        charge_currency="GBP",
        charge_amount=amount_gbp,
        external_ref=intent.id,
    )
    return intent.client_secret


def handle_webhook(payload: bytes, sig_header: str) -> None:
    """Verifies the Stripe signature and credits the balance on
    payment_intent.succeeded. Idempotent: replays of the same event (Stripe
    retries webhooks that don't 2xx quickly, and may deliver duplicates) are
    detected via the TopupIntent's status, which flips to "completed" exactly
    once."""
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise StripeTopupError("Stripe webhook secret is not configured.")

    event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)

    if event["type"] != "payment_intent.succeeded":
        return

    # event["data"]["object"] is a StripeObject, not a plain dict — it
    # supports __getitem__ but not .get(), and dict() on it misbehaves (it's
    # not a proper Mapping), so convert via its own to_dict() up front rather
    # than mixing subscript/attribute access throughout.
    payment_intent = event["data"]["object"].to_dict()

    intent = billing_repo.get_topup_intent_by_ref("stripe", payment_intent["id"])
    if intent is None:
        log.warning("Stripe webhook for unknown payment_intent id: %s", payment_intent.get("id"))
        return
    if intent.status == "completed":
        return  # already credited — duplicate delivery, no-op

    # amount_received (actually captured), not amount (what was requested) —
    # the field that reflects what Stripe actually took.
    amount_gbp = Decimal(payment_intent["amount_received"]) / 100

    tx = add_balance_transaction(
        entity_type=intent.entity_type,
        entity_id=intent.entity_id,
        tx_type="topup",
        amount=amount_gbp,
        description=f"Stripe top-up ({payment_intent['id']})",
    )
    billing_repo.mark_topup_completed(
        intent, credit_amount_gbp=amount_gbp, fx_rate=None, balance_transaction_id=tx.id
    )
