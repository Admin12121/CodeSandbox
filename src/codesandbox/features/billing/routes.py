from __future__ import annotations

import html
import logging
import uuid
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import stripe
from flask import Response, abort, redirect, render_template, request

from codesandbox.config import get_settings
from codesandbox.features.billing import esewa_gateway, repository as billing_repo, stripe_gateway
from codesandbox.features.organizations import repository as org_repo
from codesandbox.shared.guards import verified_email
from codesandbox.shared.session import require_sandbox_user
from codesandbox.web._ctx import _workspaces_ctx
from codesandbox.web.blueprint import web_bp
from codesandbox.web.csrf import csrf_exempt

log = logging.getLogger(__name__)


def _resolve_billing_entity(
    user,
    *,
    permission: str = "sandbox.billing.topup",
) -> tuple[str, str] | tuple[None, None]:
    """Resolve the active workspace's independent billing account.

    Personal and organization ledgers never fall back to one another. The
    caller states the exact organization permission it needs: top-up routes
    require ``sandbox.billing.topup`` while receipt/read routes require
    ``sandbox.billing.view``. Organization owners are always authorized.
    """
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if active_workspace:
        org_id = str(active_workspace["id"])
        if not org_repo.is_org_owner(org_id, str(user.id)):
            permissions = set(org_repo.get_member_permissions(org_id, str(user.id)))
            if permission not in permissions:
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
        return {"ok": False, "error": "You do not have permission to add organization funds."}, 403

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
    except stripe.AuthenticationError:
        log.exception("Stripe credentials were rejected")
        return {"ok": False, "error": "Stripe credentials were rejected."}, 503
    except stripe.APIConnectionError:
        log.exception("Stripe API is unreachable")
        return {"ok": False, "error": "Stripe is temporarily unreachable."}, 503
    except stripe.StripeError:
        log.exception("Stripe rejected PaymentIntent creation")
        return {"ok": False, "error": "Stripe could not start the payment."}, 502
    except Exception:
        log.exception("Stripe payment intent creation failed")
        return {"ok": False, "error": "Could not start payment. Please try again."}, 500

    return {"ok": True, "client_secret": client_secret}


@web_bp.post("/billing/topup/stripe/finalize")
@verified_email("adding funds")
def billing_topup_stripe_finalize_action():
    """Server-side verification for the PaymentIntent returned by Stripe.js.

    The signed webhook remains the production source of asynchronous updates,
    while this route makes localhost development work without exposing the
    local webhook to Stripe. Both paths call the same idempotent completion.
    """
    session, redir = require_sandbox_user()
    if redir:
        return {"ok": False, "error": "Not authenticated."}, 401

    body = request.get_json(silent=True) or {}
    external_ref = str(body.get("payment_intent_id") or "")
    intent = billing_repo.get_topup_intent_by_ref("stripe", external_ref)
    if intent is None:
        return {"ok": False, "error": "Payment not found."}, 404

    entity_type, entity_id = _resolve_billing_entity(session.user)
    if (intent.entity_type, intent.entity_id) != (entity_type, entity_id):
        return {"ok": False, "error": "Payment not found."}, 404

    try:
        status = stripe_gateway.reconcile_payment_intent(external_ref)
    except stripe_gateway.StripeTopupError as exc:
        log.warning("Stripe PaymentIntent reconciliation rejected: %s", exc)
        return {"ok": False, "error": str(exc)}, 400
    except stripe.AuthenticationError:
        log.exception("Stripe credentials were rejected during reconciliation")
        return {"ok": False, "error": "Stripe credentials were rejected."}, 503
    except stripe.APIConnectionError:
        log.exception("Stripe API is unreachable during reconciliation")
        return {"ok": False, "error": "Stripe is temporarily unreachable."}, 503
    except stripe.StripeError:
        log.exception("Stripe reconciliation failed")
        return {"ok": False, "error": "Could not verify the Stripe payment."}, 502
    except Exception:
        log.exception("Stripe PaymentIntent reconciliation failed")
        return {"ok": False, "error": "Could not verify the Stripe payment."}, 500

    return {"ok": True, "status": status}


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


def _owned_billing_transaction_receipt(transaction_id: str | None):
    session, redir = require_sandbox_user()
    if redir:
        abort(401)
    entity_type, entity_id = _resolve_billing_entity(
        session.user,
        permission="sandbox.billing.view",
    )
    if entity_type is None:
        abort(403)
    if not transaction_id:
        abort(404)

    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.sandbox.models import BalanceTransaction

    tx = BalanceTransaction.objects.filter(id=str(transaction_id)).first()
    if tx is None or (tx.entity_type, str(tx.entity_id)) != (entity_type, entity_id):
        abort(404)
    receipt = finance_service.transaction_receipt_dict(tx)
    if receipt is None:
        abort(404)
    return receipt


@web_bp.get("/billing/transactions/receipt")
def billing_transaction_receipt_action():
    receipt = _owned_billing_transaction_receipt(request.args.get("transaction"))
    return Response(
        render_template(
            "(admin)/platform/finance/ledger/_components/financial_document.html",
            receipt=receipt,
            can_manage_refunds=False,
        ),
        mimetype="text/html",
    )


@web_bp.get("/billing/transactions/receipt/download")
def billing_transaction_receipt_download_action():
    receipt = _owned_billing_transaction_receipt(request.args.get("transaction"))
    filename = f"{receipt['number']}.html"
    return Response(
        render_template(
            "(admin)/platform/finance/ledger/_components/document_download.html",
            receipt=receipt,
        ),
        mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        return redirect("/billing?error=You+do+not+have+permission+to+add+organization+funds.", 303)
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
    except stripe.SignatureVerificationError:
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
    wants_json = request.headers.get("X-Requested-With") == "fetch"

    def fail(message: str):
        if wants_json:
            return {"ok": False, "error": message}, 400
        return redirect(f"/billing?error={quote(message)}", 303)

    session, redir = require_sandbox_user()
    if redir:
        if wants_json:
            return {"ok": False, "error": "Not authenticated."}, 401
        return redirect("/login", 303)
    user = session.user
    entity_type, entity_id = _resolve_billing_entity(user)
    if entity_type is None:
        return fail("You do not have permission to add organization funds.")

    try:
        amount = Decimal(request.form.get("amount", ""))
    except InvalidOperation:
        return fail("Invalid amount.")

    settings = get_settings()
    try:
        form = esewa_gateway.build_payment_form(
            entity_type, entity_id, amount,
            success_url=f"{settings.app_url}/billing/topup/esewa/success",
            failure_url=f"{settings.app_url}/billing/topup/esewa/failure",
        )
    except esewa_gateway.EsewaTopupError as exc:
        return fail(str(exc))
    except Exception:
        log.exception("eSewa form build failed")
        return fail("Could not start eSewa payment. Please try again.")

    # eSewa requires an actual browser POST to their payment page — a plain
    # redirect can't carry a POST body. The billing page JS fetches this as
    # JSON and submits a dynamically-built form, so the browser navigates
    # straight to eSewa with no intermediate page.
    if wants_json:
        return {"ok": True, "form_url": form["form_url"], "fields": form["fields"]}

    # Non-fetch fallback: a visible continue button, NOT an inline onload
    # auto-submit — the app's CSP has no 'unsafe-inline' in script-src, so an
    # inline handler silently never runs (this page then sat at "Redirecting
    # to eSewa…" forever, which is exactly the bug this replaced).
    inputs = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in form["fields"].items()
    )
    body = f"""<!doctype html>
<html><body>
<form method="POST" action="{html.escape(form["form_url"])}">
{inputs}
<button type="submit">Continue to eSewa</button>
</form>
</body></html>"""
    return Response(body, mimetype="text/html")


@web_bp.get("/billing/topup/esewa/success")
def billing_topup_esewa_success():
    raw = request.args.get("data", "")
    try:
        decoded = esewa_gateway.decode_and_verify_success_payload(raw)
    except esewa_gateway.EsewaTopupError as exc:
        log.warning("Rejected eSewa success callback: %s", exc)
        return redirect(f"/billing?error={quote(str(exc))}", 303)

    transaction_uuid = str(decoded.get("transaction_uuid") or "")
    if not transaction_uuid:
        return redirect("/billing?error=Missing+transaction+reference.", 303)
    if str(decoded.get("status") or "").upper() != "COMPLETE":
        return redirect("/billing?error=Payment+was+not+completed.", 303)

    credited, message = esewa_gateway.confirm_by_transaction_uuid(transaction_uuid)
    if credited:
        return redirect("/billing?topup=success", 303)
    return redirect(f"/billing?error={quote(message)}", 303)


@web_bp.get("/billing/topup/esewa/failure")
def billing_topup_esewa_failure():
    return redirect("/billing?topup=cancelled", 303)
