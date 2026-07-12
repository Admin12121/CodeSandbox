from __future__ import annotations

from flask import redirect, request

from codesandbox.features.sandbox.service import get_platform_plans, get_platform_templates
from codesandbox.shared.guards import platform_perm
from codesandbox.shared.permissions import has_platform_permission
from codesandbox.shared.session import build_nav, get_current_session
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx
from codesandbox.web.blueprint import router, web_bp

from . import service


FINANCE_TEMPLATE = "(admin)/platform/finance/page.html"


def _int_arg(name: str, default: int) -> int:
    try:
        return max(1, int(request.args.get(name, str(default)) or default))
    except ValueError:
        return default


def _template_options() -> list[dict]:
    templates, _ = get_platform_templates(page=1, page_size=500)
    return [{"id": t["id"], "name": t["name"], "slug": t["slug"]} for t in templates]


def _plan_options() -> list[dict]:
    return [{"id": p["id"], "name": p["name"]} for p in get_platform_plans()]


def _base(section: str, title: str, description: str, **extra):
    cs = get_current_session()
    assert cs is not None
    user = cs.user
    return {
        "_meta": {"title": f"{title} — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": build_nav(request.path, user),
        "page_title": title,
        "page_description": description,
        **_workspaces_ctx(user),
        "finance_section": section,
        "finance_nav": [
            ("overview", "Overview", "/platform/finance"),
            ("revenue", "Revenue", "/platform/finance/revenue"),
            ("ledger", "Ledger", "/platform/finance/ledger"),
            ("promotions", "Promotions", "/platform/finance/promotions"),
        ],
        "can_manage_finance": has_platform_permission(user, "platform.finance.manage"),
        "can_manage_coupons": has_platform_permission(user, "platform.finance.coupons.manage"),
        "can_manage_credits": has_platform_permission(user, "platform.finance.credits.manage"),
        "can_manage_refunds": has_platform_permission(user, "platform.finance.refunds.manage"),
        "can_export_ledger": has_platform_permission(user, "platform.finance.read"),
        "error": request.args.get("error"),
        "info": request.args.get("info"),
        "action": request.args.get("action", ""),
        **extra,
    }


@router.page("/platform/finance", template=FINANCE_TEMPLATE)
@platform_perm("platform.finance.read")
def finance_dashboard():
    data = service.dashboard(
        period=request.args.get("period", "30d"),
        start=request.args.get("start"),
        end=request.args.get("end"),
    )
    return _base(
        "overview",
        "Finance",
        "High-level finance dashboard. Top-ups are cash received; usage charges are revenue; balances are liability.",
        **data,
    )


@router.page("/platform/finance/revenue", template=FINANCE_TEMPLATE)
@platform_perm("platform.finance.read")
def finance_revenue():
    report = service.revenue_console(
        period=request.args.get("period", "30d"),
        start=request.args.get("start"),
        end=request.args.get("end"),
    )
    return _base(
        "revenue",
        "Revenue",
        "Usage revenue and compute analysis from usage charges, runtime, resource hours, and cost estimates.",
        report=report,
    )


@router.page("/platform/finance/ledger", template=FINANCE_TEMPLATE)
@platform_perm("platform.finance.read")
def finance_ledger():
    ledger = service.ledger_console(
        selected_id=request.args.get("selected"),
        tx_type=request.args.get("type", "all"),
        search=request.args.get("search"),
        page=_int_arg("page", 1),
        page_size=25,
    )
    return _base(
        "ledger",
        "Ledger",
        "Financial transaction console for balance ledger entries, receipts, refunds, and manual adjustments.",
        ledger=ledger,
        entity_options=service.entity_options(),
    )


@router.page("/platform/finance/promotions", template=FINANCE_TEMPLATE)
@platform_perm("platform.finance.read")
def finance_promotions():
    return _base(
        "promotions",
        "Promotions",
        "Discounts, coupons, free credits, credit grants, and redemptions.",
        **service.promotions_console(),
        templates=_template_options(),
        plans=_plan_options(),
    )


def _legacy(path: str):
    return redirect(path, code=302)


@web_bp.get("/platform/finance/transactions")
@web_bp.get("/platform/finance/topups")
@web_bp.get("/platform/finance/refunds")
@web_bp.get("/platform/finance/invoices")
@web_bp.get("/platform/finance/invoices/<path:_unused>")
@web_bp.get("/platform/finance/users/<path:_unused>")
@web_bp.get("/platform/finance/orgs/<path:_unused>")
@platform_perm("platform.finance.read")
def finance_legacy_ledger(_unused: str | None = None):
    return _legacy("/platform/finance/ledger")


@web_bp.get("/platform/finance/coupons")
@web_bp.get("/platform/finance/coupons/<path:_unused>")
@web_bp.get("/platform/finance/credits")
@web_bp.get("/platform/finance/credits/<path:_unused>")
@platform_perm("platform.finance.read")
def finance_legacy_promotions(_unused: str | None = None):
    return _legacy("/platform/finance/promotions")


@web_bp.get("/platform/finance/usage")
@web_bp.get("/platform/finance/costs")
@platform_perm("platform.finance.read")
def finance_legacy_revenue():
    return _legacy("/platform/finance/revenue")

