"""eSewa ePay v2 top-ups with signed requests and verified callbacks."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import Mapping

import requests

from codesandbox.config import get_settings
from codesandbox.features.billing import fx, repository as billing_repo

log = logging.getLogger(__name__)

MIN_TOPUP_NPR = Decimal("200")
_REQUEST_SIGNED_FIELDS = (
    "total_amount",
    "transaction_uuid",
    "product_code",
)
_RESPONSE_SIGNED_FIELDS = (
    "transaction_code",
    "status",
    "total_amount",
    "transaction_uuid",
    "product_code",
    "signed_field_names",
)


class EsewaTopupError(Exception):
    pass


def _signature_message(payload: Mapping[str, object], field_names: tuple[str, ...]) -> bytes:
    if not field_names or len(set(field_names)) != len(field_names):
        raise EsewaTopupError("Invalid eSewa signed field list.")

    parts: list[str] = []
    for name in field_names:
        if name not in payload or payload[name] is None:
            raise EsewaTopupError(f"Missing eSewa signed field: {name}.")
        parts.append(f"{name}={payload[name]}")
    return ",".join(parts).encode("utf-8")


def _generate_signature(payload: Mapping[str, object], field_names: tuple[str, ...]) -> str:
    secret = get_settings().esewa_secret_key.strip()
    if not secret:
        raise EsewaTopupError("eSewa secret key is not configured.")
    digest = hmac.new(
        secret.encode("utf-8"),
        _signature_message(payload, field_names),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def _decode_base64_json(raw: str) -> dict[str, object]:
    if not raw:
        raise EsewaTopupError("Missing eSewa response payload.")

    # Query-string parsing can turn an unescaped '+' into a space. eSewa's
    # response is standard Base64, so normalize that before strict decoding.
    normalized = raw.strip().replace(" ", "+")
    normalized += "=" * (-len(normalized) % 4)
    try:
        decoded = base64.b64decode(normalized, validate=True).decode("utf-8")
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EsewaTopupError("Invalid eSewa response payload.") from exc
    if not isinstance(payload, dict):
        raise EsewaTopupError("Invalid eSewa response payload.")
    return payload


def decode_and_verify_success_payload(raw: str) -> dict[str, object]:
    """Decode eSewa's Base64 callback and verify its HMAC signature."""
    payload = _decode_base64_json(raw)
    signed_names = str(payload.get("signed_field_names") or "")
    field_names = tuple(part.strip() for part in signed_names.split(",") if part.strip())
    if field_names != _RESPONSE_SIGNED_FIELDS:
        raise EsewaTopupError("Unexpected eSewa response signed fields.")

    supplied_signature = str(payload.get("signature") or "")
    if not supplied_signature:
        raise EsewaTopupError("Missing eSewa response signature.")
    expected_signature = _generate_signature(payload, field_names)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise EsewaTopupError("Invalid eSewa response signature.")

    settings = get_settings()
    if str(payload.get("product_code") or "") != settings.esewa_product_code:
        raise EsewaTopupError("eSewa product code does not match.")
    return payload


def build_payment_form(
    entity_type: str,
    entity_id: str,
    amount_npr: Decimal,
    success_url: str,
    failure_url: str,
) -> dict:
    """Create a pending intent and return the browser POST fields for eSewa."""
    try:
        amount_npr = Decimal(str(amount_npr)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise EsewaTopupError("Invalid amount.") from exc
    if not amount_npr.is_finite() or amount_npr < MIN_TOPUP_NPR:
        raise EsewaTopupError(f"Minimum top-up is रु{MIN_TOPUP_NPR}.")

    settings = get_settings()
    product_code = settings.esewa_product_code.strip()
    if not product_code or not settings.esewa_form_url.strip():
        raise EsewaTopupError("eSewa is not configured.")

    transaction_uuid = str(uuid.uuid4())
    amount_str = f"{amount_npr:.2f}"
    signature_payload = {
        "total_amount": amount_str,
        "transaction_uuid": transaction_uuid,
        "product_code": product_code,
    }
    signature = _generate_signature(signature_payload, _REQUEST_SIGNED_FIELDS)

    billing_repo.create_topup_intent(
        entity_type=entity_type,
        entity_id=entity_id,
        gateway="esewa",
        charge_currency="NPR",
        charge_amount=amount_npr,
        external_ref=transaction_uuid,
        idempotency_key=f"esewa:{transaction_uuid}",
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
            "signed_field_names": ",".join(_REQUEST_SIGNED_FIELDS),
            "signature": signature,
        },
    }


def _check_status(product_code: str, total_amount: str, transaction_uuid: str) -> dict | None:
    settings = get_settings()
    if not settings.esewa_status_url.strip():
        return None
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
        payload = resp.json()
        return payload if isinstance(payload, dict) else None
    except (requests.RequestException, ValueError) as exc:
        log.warning("eSewa status-check failed for %s: %s", transaction_uuid, exc)
        return None


def confirm_by_transaction_uuid(transaction_uuid: str) -> tuple[bool, str]:
    """Verify a stored eSewa intent through eSewa's server-side status API."""
    intent = billing_repo.get_topup_intent_by_ref("esewa", transaction_uuid)
    if intent is None:
        return False, "Unknown transaction."
    if intent.status == "completed":
        return True, "Already credited."
    if intent.status in {"failed", "expired"}:
        return False, "This transaction is no longer payable."

    settings = get_settings()
    total_amount_str = f"{Decimal(str(intent.charge_amount)):.2f}"
    status = _check_status(settings.esewa_product_code, total_amount_str, transaction_uuid)
    if status is None:
        return False, "Could not reach eSewa to verify payment — try again shortly."

    returned_product_code = str(status.get("product_code") or "")
    returned_uuid = str(status.get("transaction_uuid") or "")
    if returned_product_code != settings.esewa_product_code or returned_uuid != transaction_uuid:
        log.error("eSewa status identity mismatch for %s", transaction_uuid)
        return False, "eSewa verification response did not match the transaction."

    provider_status = str(status.get("status") or "").upper()
    if provider_status != "COMPLETE":
        if provider_status in {"CANCELED", "NOT_FOUND", "FULL_REFUND"}:
            billing_repo.mark_topup_failed("esewa", transaction_uuid)
        return False, f"Payment not completed (status: {provider_status or 'unknown'})."

    try:
        confirmed_amount = Decimal(str(status.get("total_amount"))).quantize(Decimal("0.01"))
        stored_amount = Decimal(str(intent.charge_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return False, "Unexpected response from eSewa."
    if confirmed_amount != stored_amount:
        log.error(
            "eSewa amount mismatch for %s: intent=%s confirmed=%s",
            transaction_uuid,
            intent.charge_amount,
            confirmed_amount,
        )
        return False, "Amount mismatch during verification — contact support."

    amount_npr = stored_amount
    rate = fx.get_gbp_npr_rate()
    amount_gbp = fx.npr_to_gbp(amount_npr)
    billing_repo.complete_topup(
        gateway="esewa",
        external_ref=transaction_uuid,
        credit_amount_gbp=amount_gbp,
        fx_rate=rate,
        provider_event_id=None,
        provider_reference=str(status.get("ref_id") or transaction_uuid),
        description=f"eSewa top-up (NPR {amount_npr}, ref {status.get('ref_id', transaction_uuid)})",
    )
    return True, "Payment confirmed."
