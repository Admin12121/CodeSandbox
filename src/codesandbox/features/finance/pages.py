from __future__ import annotations

import csv
import io

from flask import Response, abort, redirect, render_template, request

from codesandbox.features.sandbox.service import get_platform_plans, get_platform_templates
from codesandbox.shared.guards import platform_perm
from codesandbox.shared.permissions import has_platform_permission
from codesandbox.shared.session import build_nav, get_current_session
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx
from codesandbox.web.blueprint import router

from . import service


# Formula-triggering leading characters per OWASP CSV injection guidance —
# a cell starting with one of these can be interpreted as a spreadsheet
# formula by Excel/Sheets/LibreOffice when the exported file is opened.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


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
        "page_title": title,
        "page_description": description,
        "user": _user_ctx(user),
        "nav": build_nav(request.path, user),
        **_workspaces_ctx(user),
        "finance_section": section,
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


@router.page("/platform/finance")
@platform_perm("platform.finance.read")
def finance_dashboard():
    data = service.dashboard(
        period=request.args.get("period", "month"),
        start=request.args.get("start"),
        end=request.args.get("end"),
    )
    return _base(
        "overview",
        "Finance",
        "High-level finance dashboard. Top-ups are cash received; usage charges are revenue; balances are liability.",
        **data,
    )


@router.page("/platform/finance/revenue")
@platform_perm("platform.finance.read")
def finance_revenue():
    report = service.revenue_console(
        period=request.args.get("period", "month"),
        start=request.args.get("start"),
        end=request.args.get("end"),
        page=_int_arg("page", 1),
        page_size=25,
    )
    return _base(
        "revenue",
        "Usage & Margin",
        "Usage revenue, compute cost, resource consumption, and estimated margin.",
        report=report,
    )


@router.page("/platform/finance/ledger")
@platform_perm("platform.finance.read")
def finance_ledger():
    ledger = service.ledger_console(
        selected_id=request.args.get("selected"),
        tx_type=request.args.get("type", "all"),
        search=request.args.get("search"),
        start=request.args.get("start"),
        end=request.args.get("end"),
        page=_int_arg("page", 1),
        page_size=20,
    )
    return _base(
        "ledger",
        "Ledger",
        "Financial transaction console for balance ledger entries, receipts, refunds, and manual adjustments.",
        ledger=ledger,
        entity_options=service.entity_options(),
    )


@router.page("/platform/finance/promotions")
@platform_perm("platform.finance.read")
def finance_promotions():
    return _base(
        "promotions",
        "Promotions",
        "Discounts, coupons, free credits, credit grants, and redemptions.",
        **service.promotions_console(
            period=request.args.get("period", "30d"),
            start=request.args.get("start"),
            end=request.args.get("end"),
        ),
        templates=_template_options(),
        plans=_plan_options(),
    )


@router.api("/platform/finance/ledger/preview")
@platform_perm("platform.finance.read")
def finance_ledger_preview():
    receipt = service.transaction_document(request.args.get("transaction"))
    if receipt is None:
        abort(404)
    cs = get_current_session()
    assert cs is not None
    return Response(
        render_template(
            "(admin)/platform/finance/ledger/_components/financial_document.html",
            receipt=receipt,
            can_manage_refunds=has_platform_permission(cs.user, "platform.finance.refunds.manage"),
        ),
        mimetype="text/html",
    )


@router.api("/platform/finance/entities")
@platform_perm("platform.finance.read")
def finance_entity_search():
    q = (request.args.get("q") or "").strip()
    return {"options": service.entity_options(limit=20, search=q or None)}


@router.api("/platform/finance/ledger/download")
@platform_perm("platform.finance.read")
def finance_ledger_download():
    receipt = service.transaction_document(request.args.get("transaction"))
    if receipt is None:
        abort(404)
    filename = f"{receipt['number']}.html"
    return Response(
        render_template(
            "(admin)/platform/finance/ledger/_components/document_download.html",
            receipt=receipt,
        ),
        mimetype="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.api("/platform/finance/ledger/export")
@platform_perm("platform.finance.read")
def finance_ledger_export():
    ledger = service.ledger_console(
        tx_type=request.args.get("type", "all"),
        search=request.args.get("search"),
        start=request.args.get("start"),
        end=request.args.get("end"),
        page=1,
        page_size=10000,
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "type", "entity_type", "entity", "reference", "amount", "provider", "status", "created_at"])
    for row in ledger["rows"]:
        writer.writerow([
            row["id"],
            row["type"],
            row["entity_type"],
            _csv_safe(row["entity_label"]),
            _csv_safe(row["reference"]),
            row["amount"],
            _csv_safe(row["provider"]),
            row["status"],
            row["created_label"],
        ])
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=finance-ledger.csv"},
    )


def _legacy(path: str):
    return redirect(path, code=302)


@router.api("/platform/finance/transactions", endpoint="finance_legacy_ledger_transactions")
@router.api("/platform/finance/topups", endpoint="finance_legacy_ledger_topups")
@router.api("/platform/finance/refunds", endpoint="finance_legacy_ledger_refunds")
@router.api("/platform/finance/invoices", endpoint="finance_legacy_ledger_invoices")
@router.api("/platform/finance/invoices/<path:_unused>", endpoint="finance_legacy_ledger_invoice_detail")
@router.api("/platform/finance/users/<path:_unused>", endpoint="finance_legacy_ledger_user_detail")
@router.api("/platform/finance/orgs/<path:_unused>", endpoint="finance_legacy_ledger_org_detail")
@platform_perm("platform.finance.read")
def finance_legacy_ledger(_unused: str | None = None):
    return _legacy("/platform/finance/ledger")


@router.api("/platform/finance/coupons", endpoint="finance_legacy_promotions_coupons")
@router.api("/platform/finance/coupons/<path:_unused>", endpoint="finance_legacy_promotions_coupon_detail")
@router.api("/platform/finance/credits", endpoint="finance_legacy_promotions_credits")
@router.api("/platform/finance/credits/<path:_unused>", endpoint="finance_legacy_promotions_credit_detail")
@platform_perm("platform.finance.read")
def finance_legacy_promotions(_unused: str | None = None):
    return _legacy("/platform/finance/promotions")


@router.api("/platform/finance/usage", endpoint="finance_legacy_revenue_usage")
@router.api("/platform/finance/costs", endpoint="finance_legacy_revenue_costs")
@platform_perm("platform.finance.read")
def finance_legacy_revenue():
    return _legacy("/platform/finance/revenue")
