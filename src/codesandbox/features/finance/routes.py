from __future__ import annotations

from urllib.parse import urlencode

from flask import redirect, request

from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session
from codesandbox.web.blueprint import web_bp

from . import service


def _redir(path: str, **params):
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    if clean:
        path += ("&" if "?" in path else "?") + urlencode(clean)
    return redirect(path, code=303)


@web_bp.post("/platform/finance/coupons/new")
@platform_perm("platform.finance.coupons.manage")
def finance_create_coupon_action():
    cs = get_current_session()
    result, error = service.create_coupon(
        code=request.form.get("code", ""),
        name=request.form.get("name", ""),
        description=request.form.get("description") or None,
        discount_type=request.form.get("discount_type", ""),
        value=request.form.get("value", ""),
        currency=request.form.get("currency") or "GBP",
        max_redemptions=request.form.get("max_redemptions"),
        per_entity_limit=request.form.get("per_entity_limit"),
        starts_at=request.form.get("starts_at"),
        expires_at=request.form.get("expires_at"),
        applies_to_template_id=request.form.get("applies_to_template_id") or None,
        applies_to_plan_id=request.form.get("applies_to_plan_id") or None,
        applies_to_user_id=request.form.get("applies_to_user_id") or None,
        applies_to_org_id=request.form.get("applies_to_org_id") or None,
        actor_user_id=str(cs.user.id),
    )
    if error:
        return _redir("/platform/finance/promotions", error=error, action="coupon")
    return _redir("/platform/finance/promotions", info=f"Coupon {result['code']} created.", action="coupon")


@web_bp.post("/platform/finance/coupons/<coupon_id>/disable")
@platform_perm("platform.finance.coupons.manage")
def finance_disable_coupon_action(coupon_id: str):
    cs = get_current_session()
    error = service.disable_coupon(coupon_id, actor_user_id=str(cs.user.id))
    return _redir(
        "/platform/finance/promotions",
        error=error,
        info=None if error else "Coupon disabled.",
    )


@web_bp.post("/platform/finance/credits/new")
@platform_perm("platform.finance.credits.manage")
def finance_grant_credit_action():
    cs = get_current_session()
    result, error = service.grant_credit(
        entity_type=request.form.get("entity_type", ""),
        entity_id=request.form.get("entity_id", ""),
        amount=request.form.get("amount", ""),
        reason=request.form.get("reason", ""),
        expires_at=request.form.get("expires_at"),
        actor_user_id=str(cs.user.id),
    )
    if error:
        return _redir("/platform/finance/promotions", error=error, action="credit")
    return _redir("/platform/finance/promotions", info=f"Credit grant {result['id'][:8]} created.")


@web_bp.post("/platform/finance/credits/<grant_id>/revoke")
@platform_perm("platform.finance.credits.manage")
def finance_revoke_credit_action(grant_id: str):
    cs = get_current_session()
    error = service.revoke_credit_grant(grant_id, actor_user_id=str(cs.user.id))
    return _redir(
        "/platform/finance/promotions",
        error=error,
        info=None if error else "Credit grant revoked.",
    )


@web_bp.post("/platform/finance/refunds")
@platform_perm("platform.finance.refunds.manage")
def finance_refund_action():
    cs = get_current_session()
    result, error = service.refund_usage_charge(
        charge_id=request.form.get("charge_id", ""),
        amount=request.form.get("amount", ""),
        reason=request.form.get("reason", ""),
        internal_note=request.form.get("internal_note") or None,
        actor_user_id=str(cs.user.id),
    )
    if error:
        return _redir("/platform/finance/ledger", error=error, action="refund", selected=request.form.get("selected_tx_id"))
    return _redir(
        "/platform/finance/ledger",
        selected=request.form.get("selected_tx_id"),
        info=f"Refund issued for charge {result['id']}.",
    )


@web_bp.post("/platform/finance/adjustments/new")
@platform_perm("platform.finance.manage")
def finance_adjustment_action():
    cs = get_current_session()
    direction = request.form.get("direction", "add")
    raw_amount = request.form.get("amount", "")
    amount = raw_amount
    if direction == "deduct" and raw_amount and not raw_amount.strip().startswith("-"):
        amount = "-" + raw_amount.strip()
    result, error = service.create_adjustment(
        entity_type=request.form.get("entity_type", ""),
        entity_id=request.form.get("entity_id", ""),
        amount=amount,
        reason=request.form.get("reason", ""),
        internal_note=request.form.get("internal_note") or None,
        actor_user_id=str(cs.user.id),
    )
    if error:
        return _redir("/platform/finance/ledger", error=error, action="adjustment")
    return _redir(
        "/platform/finance/ledger",
        selected=result.get("balance_transaction_id"),
        info="Adjustment created.",
    )
