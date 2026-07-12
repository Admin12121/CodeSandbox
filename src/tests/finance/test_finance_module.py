from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tests._context import TestCase, TestContext, unique


def _id_repo():
    from codesandbox.features.identity import repository as identity_repo
    return identity_repo


def _platform_repo():
    from codesandbox.features.platform_admin import repository as platform_repo
    return platform_repo


def _make_user(ctx: TestContext, prefix: str = "fin"):
    from codesandbox.features.identity.service import sign_up

    email = unique(prefix) + "@test.local"
    result = sign_up(
        name=prefix,
        email=email,
        password="password123",
        ip_address=None,
        user_agent=None,
    )
    assert result.ok, result.message
    user = _id_repo().find_user_by_email(email)
    assert user is not None
    ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    return user


def _cleanup_user(user_id: str) -> None:
    user = _id_repo().find_user_by_id(user_id)
    if user:
        try:
            user.delete()
        except Exception:
            pass


def _admin_user():
    from codesandbox.features.identity.models import User

    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    assert admin is not None, "expected seed.py fixtures to exist"
    return admin


def _template():
    from codesandbox.features.sandbox import repository as sandbox_repo

    template = sandbox_repo.get_template_by_slug("reverse-decompile")
    assert template is not None, "expected seed.py fixtures to exist"
    return template


def _fixture_instance(ctx: TestContext, *, user, cost_hr: str = "1.0000", user_config: dict | None = None):
    from codesandbox.features.sandbox import repository as sandbox_repo
    from codesandbox.features.sandbox.models import BalanceTransaction
    from codesandbox.features.finance.models import UsageCharge

    template = _template()
    inst = sandbox_repo.create_instance(
        template_id=str(template.id),
        plan_id="general",
        workspace_type="personal",
        workspace_user_id=str(user.id),
        created_by_user_id=str(user.id),
        billing_entity="user",
        billed_user_id=str(user.id),
        user_config=json.dumps(user_config) if user_config else None,
    )
    inst.cost_hr_snapshot = Decimal(cost_hr)
    inst.billing_currency = "GBP"
    inst.min_billable_sec = 0
    inst.started_at = datetime.now(timezone.utc)
    inst.total_runtime_sec = 3600
    inst.save()

    def cleanup() -> None:
        for charge in UsageCharge.objects.filter(instance_id=str(inst.id)).all():
            try:
                charge.delete()
            except Exception:
                pass
        for tx in BalanceTransaction.objects.filter(instance_id=str(inst.id)).all():
            try:
                tx.delete()
            except Exception:
                pass
        try:
            inst.delete()
        except Exception:
            pass

    ctx.defer(cleanup)
    return inst


def _set_balance(user_id: str, amount: str) -> None:
    from codesandbox.features.sandbox import repository as sandbox_repo

    balance = sandbox_repo.get_or_create_balance("user", user_id)
    balance.amount = Decimal(amount)
    balance.reserved_amount = Decimal("0")
    balance.save()


def test_finance_permissions_seed_and_nav(ctx: TestContext) -> None:
    from codesandbox.shared.session import build_nav

    _platform_repo().seed_default_permissions()
    keys = {p.key for p in _platform_repo().list_permissions()}
    assert "platform.finance.read" in keys
    assert "platform.finance.refunds.manage" in keys
    from codesandbox.shared.permissions import get_registered_platform_permissions

    registered_keys = {key for key, _label, _group in get_registered_platform_permissions()}
    assert "platform.finance.pricing.manage" not in registered_keys

    staff = _make_user(ctx, "finstaff")
    _id_repo().update_user(str(staff.id), platform_role="system_staff")
    staff = _id_repo().find_user_by_id(str(staff.id))
    nav_without = build_nav("/platform/finance", staff)
    section_labels = [s["label"] for s in nav_without["sections"]]
    assert "Finance" not in section_labels

    role = _platform_repo().create_role(name=unique("finance-read"))
    ctx.defer(lambda rid=str(role.id): _platform_repo().delete_role(rid))
    finance_read = next(p for p in _platform_repo().list_permissions() if p.key == "platform.finance.read")
    _platform_repo().set_role_permissions(str(role.id), [str(finance_read.id)])
    _platform_repo().assign_role_to_user(str(staff.id), str(role.id))

    staff = _id_repo().find_user_by_id(str(staff.id))
    nav_with = build_nav("/platform/finance/revenue", staff)
    finance_section = next((s for s in nav_with["sections"] if s["label"] == "Finance"), None)
    assert finance_section is not None
    assert [item["label"] for item in finance_section["items"]] == ["Overview", "Revenue", "Ledger", "Promotions"]
    assert [item["href"] for item in finance_section["items"]] == [
        "/platform/finance",
        "/platform/finance/revenue",
        "/platform/finance/ledger",
        "/platform/finance/promotions",
    ]
    assert "Pricing" not in [item["label"] for item in finance_section["items"]]


def test_finance_console_route_contract(ctx: TestContext) -> None:
    from flask import current_app

    rules = {str(rule.rule) for rule in current_app.url_map.iter_rules()}
    assert "/platform/finance" in rules
    assert "/platform/finance/revenue" in rules
    assert "/platform/finance/ledger" in rules
    assert "/platform/finance/promotions" in rules
    assert "/platform/finance/transactions/<id>" not in rules
    assert "/platform/finance/transactions/<transaction_id>" not in rules
    assert "/platform/finance/pricing" not in rules
    assert "/platform/finance/pricing-overrides" not in rules
    assert "/platform/finance/pricing-overrides/<path:_unused>" not in rules
    assert "/platform/finance/transactions" in rules
    assert "/platform/finance/topups" in rules
    assert "/platform/finance/coupons" in rules
    assert "/platform/finance/credits" in rules
    assert "/platform/finance/usage" in rules
    assert "/platform/finance/costs" in rules


def test_usage_charge_is_idempotent(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import UsageCharge
    from codesandbox.features.sandbox import repository as sandbox_repo
    from codesandbox.features.sandbox.models import BalanceTransaction

    user = _admin_user()
    _set_balance(str(user.id), "100.0000")
    inst = _fixture_instance(ctx, user=user, cost_hr="2.0000")

    before = Decimal(str(sandbox_repo.get_balance("user", str(user.id)).amount))
    charge1, tx1, revenue1, status1 = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="finance usage test",
    )
    assert charge1 is not None and tx1 is not None
    assert revenue1 == Decimal("2.0000")
    assert status1 == "charged"

    charge2, tx2, revenue2, status2 = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="finance usage retry",
    )
    assert charge2.id == charge1.id
    assert tx2.id == tx1.id
    assert revenue2 == Decimal("2.0000")

    after = Decimal(str(sandbox_repo.get_balance("user", str(user.id)).amount))
    assert after == before - Decimal("2.0000")
    assert len(UsageCharge.objects.filter(instance_id=str(inst.id)).all()) == 1
    assert len(BalanceTransaction.objects.filter(idempotency_key=f"usage:{inst.id}").all()) == 1


def test_finance_admin_mutations_validate_entity(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service

    result, error = finance_service.grant_credit(
        entity_type="user",
        entity_id="missing-user",
        amount="1.0000",
        reason="should fail",
        actor_user_id=str(_admin_user().id),
    )
    assert result is None
    assert error == "Selected user or organization was not found."


def test_coupon_limit_scope_and_expiry(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import Coupon, CouponRedemption

    user = _make_user(ctx, "fincoupon")
    _id_repo().update_user(str(user.id), email_verified=True)
    _set_balance(str(user.id), "100.0000")
    admin_id = str(_admin_user().id)

    coupon, error = finance_service.create_coupon(
        code=unique("save"),
        name="Scoped save",
        discount_type="fixed",
        value="1.0000",
        max_redemptions=None,
        per_entity_limit="1",
        applies_to_user_id=str(user.id),
        actor_user_id=admin_id,
    )
    assert error is None, error
    assert coupon is not None

    def cleanup_coupon(cid=coupon["id"]) -> None:
        for redemption in CouponRedemption.objects.filter(coupon_id=cid).all():
            try:
                redemption.delete()
            except Exception:
                pass
        row = Coupon.objects.filter(id=cid).first()
        if row:
            try:
                row.delete()
            except Exception:
                pass

    ctx.defer(cleanup_coupon)

    first = _fixture_instance(ctx, user=user, cost_hr="2.0000", user_config={"coupon_code": coupon["code"]})
    charge1, _tx1, revenue1, _status1 = finance_service.create_usage_charge_for_instance(
        str(first.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="coupon first use",
    )
    assert charge1 is not None
    assert Decimal(str(charge1.discount_amount)) == Decimal("1.0000")
    assert revenue1 == Decimal("1.0000")

    second = _fixture_instance(ctx, user=user, cost_hr="2.0000", user_config={"coupon_code": coupon["code"]})
    charge2, _tx2, revenue2, _status2 = finance_service.create_usage_charge_for_instance(
        str(second.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="coupon limit use",
    )
    assert charge2 is not None
    assert Decimal(str(charge2.discount_amount)) == Decimal("0")
    assert revenue2 == Decimal("2.0000")

    expired, error = finance_service.create_coupon(
        code=unique("old"),
        name="Expired",
        discount_type="fixed",
        value="1.0000",
        expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat(),
        actor_user_id=admin_id,
    )
    assert error is None, error
    assert expired is not None

    def cleanup_expired(cid=expired["id"]) -> None:
        for redemption in CouponRedemption.objects.filter(coupon_id=cid).all():
            try:
                redemption.delete()
            except Exception:
                pass
        row = Coupon.objects.filter(id=cid).first()
        if row:
            try:
                row.delete()
            except Exception:
                pass

    ctx.defer(cleanup_expired)
    third = _fixture_instance(ctx, user=user, cost_hr="2.0000", user_config={"coupon_code": expired["code"]})
    charge3, _tx3, revenue3, _status3 = finance_service.create_usage_charge_for_instance(
        str(third.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="expired coupon use",
    )
    assert charge3 is not None
    assert Decimal(str(charge3.discount_amount)) == Decimal("0")
    assert revenue3 == Decimal("2.0000")


def test_refunds_and_invoice_totals(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import Invoice

    user = _make_user(ctx, "finrefund")
    _id_repo().update_user(str(user.id), email_verified=True)
    _set_balance(str(user.id), "100.0000")
    inst = _fixture_instance(ctx, user=user, cost_hr="4.0000")

    charge, _tx, revenue, _status = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="refund and invoice test",
    )
    assert charge is not None
    assert revenue == Decimal("4.0000")

    refunded, error = finance_service.refund_usage_charge(
        charge_id=str(charge.id),
        amount="1.0000",
        reason="partial",
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert refunded is not None
    assert Decimal(str(refunded["refunded_amount"])) == Decimal("1.0000")

    over_refund, error = finance_service.refund_usage_charge(
        charge_id=str(charge.id),
        amount="4.0000",
        reason="too much",
        actor_user_id=str(_admin_user().id),
    )
    assert over_refund is None
    assert error == "Refund amount exceeds remaining refundable amount."

    refunded, error = finance_service.refund_usage_charge(
        charge_id=str(charge.id),
        amount="3.0000",
        reason="rest",
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert refunded is not None
    assert refunded["status"] == "refunded"

    start = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
    invoice, error = finance_service.generate_invoice(
        entity_type="user",
        entity_id=str(user.id),
        period_start=start,
        period_end=end,
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert invoice is not None
    ctx.defer(lambda iid=invoice["id"]: Invoice.objects.filter(id=iid).first() and Invoice.objects.filter(id=iid).first().delete())
    assert Decimal(str(invoice["subtotal"])) == Decimal("4.0000")
    assert Decimal(str(invoice["refund_total"])) == Decimal("4.0000")
    assert Decimal(str(invoice["total"])) == Decimal("0.0000")


def test_credit_grant_creates_ledger_transaction(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import CreditGrant
    from codesandbox.features.sandbox import repository as sandbox_repo

    user = _make_user(ctx, "fincredit")
    _id_repo().update_user(str(user.id), email_verified=True)
    _set_balance(str(user.id), "0.0000")

    grant, error = finance_service.grant_credit(
        entity_type="user",
        entity_id=str(user.id),
        amount="7.5000",
        reason="test grant",
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert grant is not None
    ctx.defer(lambda gid=grant["id"]: CreditGrant.objects.filter(id=gid).first() and CreditGrant.objects.filter(id=gid).first().delete())

    balance = sandbox_repo.get_balance("user", str(user.id))
    assert Decimal(str(balance.amount)) == Decimal("7.5000")
    txs = sandbox_repo.list_transactions("user", str(user.id), limit=10)
    assert any(tx.type == "credit_grant" and Decimal(str(tx.amount)) == Decimal("7.5000") for tx in txs)


TESTS: list[TestCase] = [
    TestCase("finance permissions seed and sidebar nav", "finance", test_finance_permissions_seed_and_nav),
    TestCase("finance console route contract", "finance", test_finance_console_route_contract),
    TestCase("UsageCharge is idempotent", "finance", test_usage_charge_is_idempotent),
    TestCase("finance admin mutations validate entity", "finance", test_finance_admin_mutations_validate_entity),
    TestCase("coupon limit scope and expiry", "finance", test_coupon_limit_scope_and_expiry),
    TestCase("refunds and invoice totals", "finance", test_refunds_and_invoice_totals),
    TestCase("credit grant creates ledger transaction", "finance", test_credit_grant_creates_ledger_transaction),
]
