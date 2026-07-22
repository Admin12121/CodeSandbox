from __future__ import annotations

import base64
import html
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import stripe
from flask import Response, redirect, request

from codesandbox.config import get_settings
from codesandbox.features.billing import esewa_gateway, repository as billing_repo, stripe_gateway
from codesandbox.features.organizations import repository as org_repo
from codesandbox.shared.guards import verified_email
from codesandbox.shared.session import require_sandbox_user
from codesandbox.web._ctx import _workspaces_ctx
from codesandbox.web.blueprint import web_bp
from codesandbox.web.csrf import csrf_exempt

log = logging.getLogger(__name__)


def _resolve_billing_entity(user) -> tuple[str, str] | tuple[None, None]:
    """Mirrors /billing's own entity resolution: the active org (owner only)
    or the personal balance. Only the org owner may spend org funds."""
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if active_workspace:
        org_id = str(active_workspace["id"])
        if not org_repo.is_org_owner(org_id, str(user.id)):
            return None, None
        return "org", org_id
    return "user", str(user.id)


# ── Stripe ──────────────────────────────────────────────────────────────────
# JSON, not a redirect: the card form stays inline (CardElement), confirmed
# client-side via Stripe.js against the client_secret this returns. That
# client-side result only drives UI feedback — see billing_topup_status
# below for the part that's actually trusted.

@web_bp.post("/billing/topup/stripe")
@verified_email("adding funds")
def billing_topup_stripe_action():
    session, redir = require_sandbox_user()
    if redir:
        return {"ok": False, "error": "Not authenticated."}, 401
    user = session.user
    entity_type, entity_id = _resolve_billing_entity(user)
    if entity_type is None:
        return {"ok": False, "error": "Only the organization owner can add funds."}, 403

    body = request.get_json(silent=True) or {}
    try:
        amount = Decimal(str(body.get("amount", "")))
    except InvalidOperation:
        return {"ok": False, "error": "Invalid amount."}, 400

    try:
        request_key = str(body.get("idempotency_key") or "")[:100]
        client_secret = stripe_gateway.create_payment_intent(
            entity_type,
            entity_id,
            amount,
            request_key=request_key or None,
        )
    except stripe_gateway.StripeTopupError as exc:
        return {"ok": False, "error": str(exc)}, 400
    except Exception:
        log.exception("Stripe payment intent creation failed")
        return {"ok": False, "error": "Could not start payment. Please try again."}, 500

    return {"ok": True, "client_secret": client_secret}


@web_bp.get("/billing/topup/status/<gateway>/<path:external_ref>")
def billing_topup_status_action(gateway: str, external_ref: str):
    """Polled by the frontend after a client-side Stripe confirmation (or
    while waiting on an eSewa redirect) to learn whether the webhook / status
    -check has actually landed — the source of truth, not the client result."""
    session, redir = require_sandbox_user()
    if redir:
        return {"ok": False}, 401
    user = session.user
    intent = billing_repo.get_topup_intent_by_ref(gateway, external_ref)
    if intent is None:
        return {"ok": False, "error": "Not found."}, 404

    entity_type, entity_id = _resolve_billing_entity(user)
    if (intent.entity_type, intent.entity_id) != (entity_type, entity_id):
        return {"ok": False, "error": "Not found."}, 404  # don't leak existence

    return {"ok": True, "status": intent.status}


@web_bp.post("/billing/topup/dev")
@verified_email("adding funds")
def billing_topup_dev_action():
    settings = get_settings()
    if not settings.billing_dev_topup_enabled:
        return {"ok": False, "error": "Development top-ups are disabled."}, 404
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    entity_type, entity_id = _resolve_billing_entity(session.user)
    if entity_type is None:
        return redirect("/billing?error=Only+the+organization+owner+can+add+funds.", 303)
    try:
        amount = Decimal(str(request.form.get("amount", ""))).quantize(Decimal("0.01"))
    except InvalidOperation:
        return redirect("/billing?error=Invalid+amount.", 303)
    if amount < Decimal("1") or amount > Decimal("10000"):
        return redirect("/billing?error=Amount+must+be+between+1+and+10000+GBP.", 303)
    external_ref = f"dev-{uuid.uuid4()}"
    billing_repo.create_topup_intent(
        entity_type=entity_type,
        entity_id=entity_id,
        gateway="dev",
        charge_currency="GBP",
        charge_amount=amount,
        external_ref=external_ref,
        idempotency_key=f"dev:{external_ref}",
    )
    billing_repo.complete_topup(
        gateway="dev",
        external_ref=external_ref,
        credit_amount_gbp=amount,
        fx_rate=None,
        provider_event_id=None,
        provider_reference=external_ref,
        description=f"Development top-up ({external_ref})",
    )
    return redirect("/billing?topup=success", 303)


@web_bp.post("/webhooks/stripe")
@csrf_exempt
def stripe_webhook_action():
    """Stripe has no session/cookie — auth is the signature on the payload,
    not CSRF (there is no browser involved to carry a CSRF token)."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        stripe_gateway.handle_webhook(payload, sig_header)
    except stripe.error.SignatureVerificationError:
        log.warning("Stripe webhook signature verification failed")
        return {"ok": False}, 400
    except stripe_gateway.StripeTopupError as exc:
        log.error("Stripe webhook config error: %s", exc)
        return {"ok": False}, 500
    except Exception:
        log.exception("Stripe webhook handling failed")
        return {"ok": False}, 500
    return {"ok": True}, 200


# ── eSewa ─────────────────────────────────────────────────────────────────────

@web_bp.post("/billing/topup/esewa")
@verified_email("adding funds")
def billing_topup_esewa_action():
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    user = session.user
    entity_type, entity_id = _resolve_billing_entity(user)
    if entity_type is None:
        return redirect("/billing?error=Only+the+organization+owner+can+add+funds.", 303)

    try:
        amount = Decimal(request.form.get("amount", ""))
    except InvalidOperation:
        return redirect("/billing?error=Invalid+amount.", 303)

    settings = get_settings()
    try:
        form = esewa_gateway.build_payment_form(
            entity_type, entity_id, amount,
            success_url=f"{settings.app_url}/billing/topup/esewa/success",
            failure_url=f"{settings.app_url}/billing/topup/esewa/failure",
        )
    except esewa_gateway.EsewaTopupError as exc:
        return redirect(f"/billing?error={quote(str(exc))}", 303)
    except Exception:
        log.exception("eSewa form build failed")
        return redirect("/billing?error=Could+not+start+eSewa+payment.+Please+try+again.", 303)

    # eSewa requires an actual browser POST to their payment page — a plain
    # redirect can't carry a POST body, so this returns a minimal
    # self-submitting form instead. All interpolated values are
    # server-generated (amount/uuid/product code/our own URLs), still HTML
    # -escaped as defense in depth.
    inputs = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in form["fields"].items()
    )
    body = f"""<!doctype html>
<html><body onload="document.forms[0].submit()">
<form method="POST" action="{html.escape(form["form_url"])}">
{inputs}
</form>
<p>Redirecting to eSewa…</p>
</body></html>"""
    return Response(body, mimetype="text/html")


@web_bp.get("/billing/topup/esewa/success")
def billing_topup_esewa_success():
    # The redirect's own payload is never trusted for confirmation (see
    # esewa_gateway module docstring) — it's only used to extract which
    # transaction to re-verify server-to-server.
    raw = request.args.get("data", "")
    transaction_uuid = None
    try:
        decoded = json.loads(base64.b64decode(raw).decode())
        transaction_uuid = decoded.get("transaction_uuid")
    except Exception:
        pass

    if not transaction_uuid:
        return redirect("/billing?error=Missing+transaction+reference.", 303)

    credited, message = esewa_gateway.confirm_by_transaction_uuid(transaction_uuid)
    if credited:
        return redirect("/billing?topup=success", 303)
    return redirect(f"/billing?error={quote(message)}", 303)


@web_bp.get("/billing/topup/esewa/failure")
def billing_topup_esewa_failure():
    return redirect("/billing?topup=cancelled", 303)
