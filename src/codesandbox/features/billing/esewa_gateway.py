"""eSewa ePay v2 topups: signed form POST + mandatory server-side verification.

eSewa has no webhook/IPN — the only confirmation channel is the browser
redirect to success_url, and that redirect's payload is fully
attacker-controllable (a user can forge the query string without ever
paying). It is NEVER trusted directly. The redirect is only used to learn
which transaction to check; the actual confirmation is always a fresh
server-to-server call to eSewa's status-check API, matched against our own
stored TopupIntent (not against whatever the redirect claims).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal

import requests

from codesandbox.config import get_settings
from codesandbox.features.billing import fx, repository as billing_repo
from codesandbox.features.sandbox.repository import add_balance_transaction

log = logging.getLogger(__name__)

MIN_TOPUP_NPR = Decimal("200")
_SIGNED_FIELDS = "total_amount,transaction_uuid,product_code"


class EsewaTopupError(Exception):
    pass


def _sign(total_amount: str, transaction_uuid: str, product_code: str) -> str:
    message = f"{total_amount},{transaction_uuid},{product_code}"
    settings = get_settings()
    digest = hmac.new(
        settings.esewa_secret_key.encode(), message.encode(), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode()


def build_payment_form(
    entity_type: str,
    entity_id: str,
    amount_npr: Decimal,
    success_url: str,
    failure_url: str,
) -> dict:
    """Creates a pending TopupIntent and returns the field dict the browser
    must POST (as a self-submitting form) to eSewa's payment page."""
    if amount_npr < MIN_TOPUP_NPR:
        raise EsewaTopupError(f"Minimum top-up is रु{MIN_TOPUP_NPR}.")

    settings = get_settings()
    product_code = settings.esewa_product_code
    transaction_uuid = str(uuid.uuid4())

    # No tax/service/delivery charges on a balance top-up — total equals the
    # base amount exactly, which keeps the signed-amount check on the
    # status-check response unambiguous.
    amount_str = f"{amount_npr:.2f}"
    signature = _sign(amount_str, transaction_uuid, product_code)

    billing_repo.create_topup_intent(
        entity_type=entity_type,
        entity_id=entity_id,
        gateway="esewa",
        charge_currency="NPR",
        charge_amount=amount_npr,
        external_ref=transaction_uuid,
    )

    return {
        "form_url": settings.esewa_form_url,
        "fields": {
            "amount": amount_str,
            "tax_amount": "0",
            "total_amount": amount_str,
            "transaction_uuid": transaction_uuid,
            "product_code": product_code,
            "product_service_charge": "0",
            "product_delivery_charge": "0",
            "success_url": success_url,
            "failure_url": failure_url,
            "signed_field_names": _SIGNED_FIELDS,
            "signature": signature,
        },
    }


def _check_status(product_code: str, total_amount: str, transaction_uuid: str) -> dict | None:
    settings = get_settings()
    try:
        resp = requests.get(
            settings.esewa_status_url,
            params={
                "product_code": product_code,
                "total_amount": total_amount,
                "transaction_uuid": transaction_uuid,
            },
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("eSewa status-check failed for %s: %s", transaction_uuid, exc)
        return None


def confirm_by_transaction_uuid(transaction_uuid: str) -> tuple[bool, str]:
    """Called from the success_url handler. Ignores whatever the redirect
    query string claims — re-derives product_code/total_amount from our own
    stored TopupIntent and re-verifies with eSewa directly.

    Returns (credited, message). Idempotent — calling this twice for an
    already-completed intent is a safe no-op.
    """
    intent = billing_repo.get_topup_intent_by_ref("esewa", transaction_uuid)
    if intent is None:
        return False, "Unknown transaction."
    if intent.status == "completed":
        return True, "Already credited."
    if intent.status == "failed":
        return False, "This transaction was already marked failed."

    settings = get_settings()
    total_amount_str = f"{Decimal(str(intent.charge_amount)):.2f}"
    status = _check_status(settings.esewa_product_code, total_amount_str, transaction_uuid)
    if status is None:
        return False, "Could not reach eSewa to verify payment — try again shortly."

    if status.get("status") != "COMPLETE":
        if status.get("status") in ("CANCELED", "NOT_FOUND"):
            billing_repo.mark_topup_failed(intent)
        return False, f"Payment not completed (status: {status.get('status', 'unknown')})."

    # Re-derive the credited amount from OUR stored intent, not from the
    # status-check response — the response is a secondary confirmation that
    # a real, completed payment for this transaction_uuid exists; the amount
    # entered by the user long ago (encoded in the intent) is what defines
    # what's credited.
    try:
        confirmed_amount = Decimal(str(status.get("total_amount")))
    except Exception:
        return False, "Unexpected response from eSewa."
    if confirmed_amount != Decimal(str(intent.charge_amount)):
        log.error(
            "eSewa amount mismatch for %s: intent=%s confirmed=%s",
            transaction_uuid, intent.charge_amount, confirmed_amount,
        )
        return False, "Amount mismatch during verification — contact support."

    amount_npr = Decimal(str(intent.charge_amount))
    rate = fx.get_gbp_npr_rate()
    amount_gbp = fx.npr_to_gbp(amount_npr)

    tx = add_balance_transaction(
        entity_type=intent.entity_type,
        entity_id=intent.entity_id,
        tx_type="topup",
        amount=amount_gbp,
        description=f"eSewa top-up (रु{amount_npr}, ref {status.get('ref_id', transaction_uuid)})",
    )
    billing_repo.mark_topup_completed(
        intent, credit_amount_gbp=amount_gbp, fx_rate=rate, balance_transaction_id=tx.id
    )
    return True, "Payment confirmed."
