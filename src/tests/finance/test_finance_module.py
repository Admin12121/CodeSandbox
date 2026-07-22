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
    from seeds.sandbox_templates import GOD_TEAR_SLUG

    template = sandbox_repo.get_template_by_slug(GOD_TEAR_SLUG)
    assert template is not None, "expected seed.py fixtures to exist"
    return template


def _fixture_instance(ctx: TestContext, *, user, cost_hr: str = "1.00", user_config: dict | None = None):
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
    assert [item["label"] for item in finance_section["items"]] == ["Overview", "Usage & Margin", "Ledger", "Promotions"]
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
    assert "/platform/finance/ledger/preview" in rules
    assert "/platform/finance/ledger/export" in rules
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


def test_finance_console_shapes_and_ledger_preview(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.sandbox import repository as sandbox_repo
    from codesandbox.features.sandbox.models import BalanceTransaction

    user = _make_user(ctx, "finconsole")
    _id_repo().update_user(str(user.id), email_verified=True)
    _set_balance(str(user.id), "100.00")
    inst = _fixture_instance(ctx, user=user, cost_hr="3.00")
    charge, tx, revenue, status = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="console shape test",
    )
    assert charge is not None and tx is not None
    assert revenue == Decimal("3.00")
    assert status == "charged"

    isolated_offset = int(str(user.id).replace("-", "")[:8], 16) % 5000
    isolated_at = datetime(2080, 1, 1, 12, 0, tzinfo=timezone.utc) + timedelta(days=isolated_offset)
    charge.created_at = isolated_at
    charge.save()
    tx.created_at = isolated_at
    tx.save()

    topup_tx = sandbox_repo.add_balance_transaction(
        entity_type="user",
        entity_id=str(user.id),
        tx_type="topup",
        amount=Decimal("50.00"),
        provider="stripe",
        reference="test-topup",
        description="test top-up",
    )
    topup_tx.created_at = isolated_at + timedelta(minutes=1)
    topup_tx.save()

    grant, error = finance_service.grant_credit(
        entity_type="user",
        entity_id=str(user.id),
        amount="7.50",
        reason="test grant",
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert grant is not None
    grant_tx = BalanceTransaction.objects.filter(id=grant["balance_transaction_id"]).first()
    assert grant_tx is not None
    grant_tx.created_at = isolated_at + timedelta(minutes=2)
    grant_tx.save()

    isolated_day = isolated_at.date().isoformat()
    overview = finance_service.dashboard(period="custom", start=isolated_day, end=isolated_day)
    assert "health" in overview
    assert overview["health"]["net_revenue"]["raw"] == "3.00"
    assert overview["health"]["net_revenue"]["credits_used"]["raw"] == "0.00"
    assert overview["health"]["cash_liability"]["topups"]["raw"] == "50.00"
    assert overview["health"]["cash_liability"]["credit_issued"]["raw"] == "7.50"
    assert Decimal(overview["health"]["cash_liability"]["credit_outstanding"]["raw"]) >= Decimal("7.50")
    assert overview["health"]["margin_compute"]["credit_issued"]["raw"] == "7.50"
    assert Decimal(overview["health"]["margin_compute"]["credit_outstanding"]["raw"]) >= Decimal("7.50")
    assert overview["template_contribution"]
    assert overview["recent_activity"]
    usage_activity = next(row for row in overview["recent_activity"] if row["id"] == str(tx.id))
    assert usage_activity["amount"] == "3.00"
    assert usage_activity["is_negative"] is False
    assert usage_activity["platform_impact"] == "revenue"
    assert usage_activity["wallet_amount"] == "-3.00"

    topup_activity = next(row for row in overview["recent_activity"] if row["id"] == str(topup_tx.id))
    assert topup_activity["amount"] == "-50.00"
    assert topup_activity["is_negative"] is True
    assert topup_activity["platform_impact"] == "liability"
    assert topup_activity["wallet_amount"] == "50.00"

    grant_activity = next(row for row in overview["recent_activity"] if row["id"] == str(grant_tx.id))
    assert grant_activity["amount"] == "-7.50"
    assert grant_activity["is_negative"] is True
    assert grant_activity["platform_impact"] == "credit cost"
    assert grant_activity["wallet_amount"] == "7.50"

    month_start_dt = isolated_at.replace(day=1)
    month_end_dt = (month_start_dt.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    month_start = month_start_dt.date().isoformat()
    month_end = month_end_dt.date().isoformat()
    range_overview = finance_service.dashboard(period="custom", start=month_start, end=month_end)
    range_labels = [row["label"] for row in range_overview["revenue_cost_timeline"]]
    assert range_labels[0] == month_start
    assert isolated_day in range_labels
    assert range_labels[-1] == month_end

    report = finance_service.revenue_console(period="custom", start=isolated_day, end=isolated_day, page=1, page_size=1)
    assert [item["label"] for item in report["summary"]] == ["Net Usage Revenue", "Realized Margin", "Credit Exposure"]
    assert report["summary"][0]["raw"] == "3.00"
    assert report["summary"][0]["footer_value"] == "£3.00"
    assert report["summary"][2]["raw"] == "7.50"
    assert report["summary"][2]["footer_label"] == "Outstanding funded balance"
    assert report["usage_charges"]["total"] >= 1
    assert len(report["usage_charges"]["rows"]) == 1
    assert report["usage_charges"]["rows"][0]["template_name"]

    ledger = finance_service.ledger_console(start=isolated_day, end=isolated_day, page=1, page_size=10)
    assert ledger["selected_tx_id"] == ""
    assert ledger["selected_receipt"] is None
    usage_ledger_row = next(row for row in ledger["rows"] if row["id"] == str(tx.id))
    assert usage_ledger_row["amount"] == "3.00"
    assert usage_ledger_row["is_negative"] is False
    topup_ledger_row = next(row for row in ledger["rows"] if row["id"] == str(topup_tx.id))
    assert topup_ledger_row["amount"] == "-50.00"
    assert topup_ledger_row["is_negative"] is True
    grant_ledger_row = next(row for row in ledger["rows"] if row["id"] == str(grant_tx.id))
    assert grant_ledger_row["amount"] == "-7.50"
    assert grant_ledger_row["is_negative"] is True

    selected = finance_service.ledger_console(selected_id=str(tx.id), start=isolated_day, end=isolated_day, page=1, page_size=10)
    assert selected["selected_tx_id"] == str(tx.id)
    assert selected["selected_receipt"]["title"] == "Usage Invoice"

    document = finance_service.transaction_document(str(tx.id))
    assert document is not None
    assert document["title"] == "Usage Invoice"
    assert document["transaction_id"] == str(tx.id)


def test_finance_templates_split_and_no_internal_tabs(ctx: TestContext) -> None:
    from pathlib import Path

    import codesandbox
    from codesandbox.web.blueprint import router

    package_root = Path(next(iter(codesandbox.__path__))).resolve()
    base = package_root / "templates" / "(admin)" / "platform" / "finance"
    page_paths = [
        base / "page.html",
        base / "revenue" / "page.html",
        base / "ledger" / "page.html",
        base / "promotions" / "page.html",
    ]
    for path in page_paths:
        text = path.read_text()
        assert "finance_tabs" not in text
        assert "page_header" not in text
    # Usage & Margin renders period_toolbar() directly (not just the chart's
    # own Weekly/Monthly/Yearly pills) so Today/7 days/Custom range are
    # reachable as a real page-level filter. Overview intentionally relies
    # on the chart's own toggle only (snapshot-style page, no page.html
    # period_toolbar).
    assert "period_toolbar" in (base / "revenue" / "page.html").read_text()
    assert not (base / "overview.html").exists()
    assert not (base / "revenue.html").exists()
    assert not (base / "ledger.html").exists()
    assert not (base / "promotions.html").exists()
    assert (base / "_components").is_dir()
    assert (base / "revenue" / "_components").is_dir()
    assert (base / "ledger" / "_components").is_dir()
    assert (base / "promotions" / "_components").is_dir()
    assert '(admin)/platform/finance/ledger/_components/financial_document.html' in (
        (package_root / "features" / "finance" / "pages.py").read_text()
    )

    finance_routes = {
        "finance_dashboard",
        "finance_revenue",
        "finance_ledger",
        "finance_promotions",
    }
    for endpoint in finance_routes:
        route = router._page_routes[endpoint]
        assert route.template_explicit is False
        assert route.template.endswith("/page.html")


def test_finance_empty_periods_do_not_emit_fake_timelines(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service

    start = "2099-01-01"
    end = "2099-01-31"

    overview = finance_service.dashboard(period="custom", start=start, end=end)
    assert overview["revenue_cost_timeline"] == []
    assert overview["health"]["net_revenue"]["sparkline"] == []
    assert overview["health"]["cash_liability"]["sparkline"] == []
    assert overview["health"]["margin_compute"]["sparkline"] == []

    report = finance_service.revenue_console(period="custom", start=start, end=end)
    assert report["economics_timeline"] == []
    assert report["usage_charges"]["total"] == 0

    promotions = finance_service.promotions_console(period="custom", start=start, end=end)
    assert promotions["redemption_timeline"] == []
    assert promotions["has_redemptions"] is False


def test_usage_charge_is_idempotent(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import UsageCharge
    from codesandbox.features.sandbox import repository as sandbox_repo
    from codesandbox.features.sandbox.models import BalanceTransaction

    user = _admin_user()
    _set_balance(str(user.id), "100.00")
    inst = _fixture_instance(ctx, user=user, cost_hr="2.00")

    before = Decimal(str(sandbox_repo.get_balance("user", str(user.id)).amount))
    charge1, tx1, revenue1, status1 = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="finance usage test",
    )
    assert charge1 is not None and tx1 is not None
    assert revenue1 == Decimal("2.00")
    assert status1 == "charged"

    charge2, tx2, revenue2, status2 = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="finance usage retry",
    )
    assert charge2.id == charge1.id
    assert tx2.id == tx1.id
    assert revenue2 == Decimal("2.00")

    after = Decimal(str(sandbox_repo.get_balance("user", str(user.id)).amount))
    assert after == before - Decimal("2.00")
    assert len(UsageCharge.objects.filter(instance_id=str(inst.id)).all()) == 1
    assert len(BalanceTransaction.objects.filter(idempotency_key=f"usage:{inst.id}").all()) == 1


def test_finance_admin_mutations_validate_entity(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service

    result, error = finance_service.grant_credit(
        entity_type="user",
        entity_id="missing-user",
        amount="1.00",
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
    _set_balance(str(user.id), "100.00")
    admin_id = str(_admin_user().id)

    coupon, error = finance_service.create_coupon(
        code=unique("save"),
        name="Scoped save",
        discount_type="fixed",
        value="1.00",
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

    first = _fixture_instance(ctx, user=user, cost_hr="2.00", user_config={"coupon_code": coupon["code"]})
    charge1, _tx1, revenue1, _status1 = finance_service.create_usage_charge_for_instance(
        str(first.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="coupon first use",
    )
    assert charge1 is not None
    assert Decimal(str(charge1.discount_amount)) == Decimal("1.00")
    assert revenue1 == Decimal("1.00")

    second = _fixture_instance(ctx, user=user, cost_hr="2.00", user_config={"coupon_code": coupon["code"]})
    charge2, _tx2, revenue2, _status2 = finance_service.create_usage_charge_for_instance(
        str(second.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="coupon limit use",
    )
    assert charge2 is not None
    assert Decimal(str(charge2.discount_amount)) == Decimal("0")
    assert revenue2 == Decimal("2.00")

    expired, error = finance_service.create_coupon(
        code=unique("old"),
        name="Expired",
        discount_type="fixed",
        value="1.00",
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
    third = _fixture_instance(ctx, user=user, cost_hr="2.00", user_config={"coupon_code": expired["code"]})
    charge3, _tx3, revenue3, _status3 = finance_service.create_usage_charge_for_instance(
        str(third.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="expired coupon use",
    )
    assert charge3 is not None
    assert Decimal(str(charge3.discount_amount)) == Decimal("0")
    assert revenue3 == Decimal("2.00")


def test_refunds_and_invoice_totals(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import Invoice

    user = _make_user(ctx, "finrefund")
    _id_repo().update_user(str(user.id), email_verified=True)
    _set_balance(str(user.id), "100.00")
    inst = _fixture_instance(ctx, user=user, cost_hr="4.00")

    charge, _tx, revenue, _status = finance_service.create_usage_charge_for_instance(
        str(inst.id),
        runtime_seconds=3600,
        billable_seconds=3600,
        description="refund and invoice test",
    )
    assert charge is not None
    assert revenue == Decimal("4.00")

    refunded, error = finance_service.refund_usage_charge(
        charge_id=str(charge.id),
        amount="1.00",
        reason="partial",
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert refunded is not None
    assert Decimal(str(refunded["refunded_amount"])) == Decimal("1.00")

    over_refund, error = finance_service.refund_usage_charge(
        charge_id=str(charge.id),
        amount="4.00",
        reason="too much",
        actor_user_id=str(_admin_user().id),
    )
    assert over_refund is None
    assert error == "Refund amount exceeds remaining refundable amount."

    refunded, error = finance_service.refund_usage_charge(
        charge_id=str(charge.id),
        amount="3.00",
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
    assert Decimal(str(invoice["subtotal"])) == Decimal("4.00")
    assert Decimal(str(invoice["refund_total"])) == Decimal("4.00")
    assert Decimal(str(invoice["total"])) == Decimal("0.00")


def test_credit_grant_creates_ledger_transaction(ctx: TestContext) -> None:
    from codesandbox.features.finance import service as finance_service
    from codesandbox.features.finance.models import CreditGrant
    from codesandbox.features.sandbox import repository as sandbox_repo

    user = _make_user(ctx, "fincredit")
    _id_repo().update_user(str(user.id), email_verified=True)
    _set_balance(str(user.id), "0.00")

    grant, error = finance_service.grant_credit(
        entity_type="user",
        entity_id=str(user.id),
        amount="7.50",
        reason="test grant",
        actor_user_id=str(_admin_user().id),
    )
    assert error is None, error
    assert grant is not None
    ctx.defer(lambda gid=grant["id"]: CreditGrant.objects.filter(id=gid).first() and CreditGrant.objects.filter(id=gid).first().delete())

    balance = sandbox_repo.get_balance("user", str(user.id))
    assert Decimal(str(balance.amount)) == Decimal("7.50")
    txs = sandbox_repo.list_transactions("user", str(user.id), limit=10)
    assert any(tx.type == "credit_grant" and Decimal(str(tx.amount)) == Decimal("7.50") for tx in txs)


def test_csv_export_neutralizes_formula_injection(ctx: TestContext) -> None:
    from codesandbox.features.finance.pages import _csv_safe

    assert _csv_safe("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
    assert _csv_safe("+1+1") == "'+1+1"
    assert _csv_safe("-1+1") == "'-1+1"
    assert _csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_safe("\t=evil") == "'\t=evil"
    assert _csv_safe("\r=evil") == "'\r=evil"
    assert _csv_safe("CodeSandbox Platform") == "CodeSandbox Platform"
    assert _csv_safe("user@example.com") == "user@example.com"
    assert _csv_safe(None) == ""


TESTS: list[TestCase] = [
    TestCase("finance permissions seed and sidebar nav", "finance", test_finance_permissions_seed_and_nav),
    TestCase("finance console route contract", "finance", test_finance_console_route_contract),
    TestCase("finance console shapes and ledger preview", "finance", test_finance_console_shapes_and_ledger_preview),
    TestCase("finance templates split and no internal tabs", "finance", test_finance_templates_split_and_no_internal_tabs),
    TestCase("finance empty periods do not emit fake timelines", "finance", test_finance_empty_periods_do_not_emit_fake_timelines),
    TestCase("CSV export neutralizes formula injection", "finance", test_csv_export_neutralizes_formula_injection),
    TestCase("UsageCharge is idempotent", "finance", test_usage_charge_is_idempotent),
    TestCase("finance admin mutations validate entity", "finance", test_finance_admin_mutations_validate_entity),
    TestCase("coupon limit scope and expiry", "finance", test_coupon_limit_scope_and_expiry),
    TestCase("refunds and invoice totals", "finance", test_refunds_and_invoice_totals),
    TestCase("credit grant creates ledger transaction", "finance", test_credit_grant_creates_ledger_transaction),
]
