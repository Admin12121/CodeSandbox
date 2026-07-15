from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
from urllib.parse import quote_plus

from flask import abort, g, redirect, request, send_from_directory, session as flask_session

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.organizations import repository as org_repo
from codesandbox.features.sandbox.service import (
    archive_instance_for_user,
    archive_org_allocation,
    claim_org_allocation,
    create_org_allocations,
    create_personal_instance,
    get_active_hub_instance,
    get_hub_template_by_slug,
    get_hub_templates,
    get_instance_ui_context,
    get_live_balance_for_actor,
    get_org_allocation_edit_context,
    get_org_allocations_for_user,
    get_org_billing,
    get_org_instances,
    get_org_requests,
    group_org_allocations_for_display,
    get_template_plans_for_hub,
    get_user_assigned_instances,
    get_user_billing,
    get_user_instances,
    get_user_requests_in_org,
    review_instance_request,
    save_instance_note_for_view,
    set_org_allocation_group_status,
    start_instance,
    submit_instance_request,
    update_org_allocation_group,
    upload_instance_input,
)
from codesandbox.shared.session import build_nav, require_sandbox_user, require_session
from codesandbox.shared.guards import verified_email
from codesandbox.web.blueprint import router, web_bp
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx

_TEMPLATES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../templates"))
_PUBLIC_DIR = os.path.join(_TEMPLATES_DIR, "public")
_FAVICON = os.path.join(_TEMPLATES_DIR, "favicon.ico")
_ORG_INACTIVE_TOAST = "toast=org_inactive"


def _org_inactive_url(path: str) -> str:
    separator = "&" if "?" in path else "?"
    return f"{path}{separator}{_ORG_INACTIVE_TOAST}"


@web_bp.get("/favicon.ico")
def _favicon():
    if os.path.isfile(_FAVICON):
        return send_from_directory(_TEMPLATES_DIR, "favicon.ico", mimetype="image/x-icon")
    abort(404)


@web_bp.get("/<path:filename>")
def _public_static(filename):
    target = os.path.normpath(os.path.join(_PUBLIC_DIR, filename))
    if target.startswith(_PUBLIC_DIR) and os.path.isfile(target):
        return send_from_directory(_PUBLIC_DIR, filename)
    abort(404)


@router.page("/dashboard")
def dashboard():
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    nav = build_nav("/dashboard", user, ws_ctx.get("active_workspace"))

    try:
        _, user_count = identity_repo.list_users()
    except Exception:
        user_count = 0
    try:
        _, org_count = org_repo.list_organizations()
    except Exception:
        org_count = 0

    metrics = [
        {"label": "Total Users",       "value": str(user_count), "change": "Platform accounts"},
        {"label": "Organizations",      "value": str(org_count),  "change": "Active tenants"},
        {"label": "Running Sandboxes",  "value": "0",             "change": "Runtime worker pending"},
        {"label": "Open Cases",         "value": "0",             "change": "Case workflow pending"},
    ]
    return {
        "_meta": {"title": "Dashboard — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Dashboard",
        "page_description": "Platform overview — users, orgs, runtime, cases",
        "metrics": metrics,
        **ws_ctx,
    }


# ── Hub ───────────────────────────────────────────────────────────────────────

@router.page("/hub")
def hub():
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    nav = build_nav("/hub", user, ws_ctx.get("active_workspace"))
    templates = get_hub_templates()

    org_ctx: dict = {}
    active_workspace = ws_ctx.get("active_workspace")
    hub_tab = request.args.get("tab", "private" if active_workspace else "catalog")
    if hub_tab not in {"private", "public", "catalog"}:
        hub_tab = "private" if active_workspace else "catalog"
    if active_workspace:
        org_id = str(active_workspace["id"])
        org_active = active_workspace.get("status") == "active"
        user_perms = set(org_repo.get_member_permissions(org_id, str(user.id)))
        is_owner = org_repo.is_org_owner(org_id, str(user.id))
        can_manage = org_active and (is_owner or "sandbox.allocations.prepare" in user_perms or "sandbox.allocations.manage" in user_perms)
        can_review = org_active and (is_owner or "sandbox.requests.review" in user_perms)
        can_request = org_active and (is_owner or "sandbox.requests.submit" in user_perms)
        public_tab_is_catalog = True
        if public_tab_is_catalog and hub_tab == "catalog":
            hub_tab = "public"
        org_allocations = get_org_allocations_for_user(org_id, str(user.id)) if org_active else []
        org_pool_allocations = [
            allocation
            for allocation in org_allocations
            if allocation.get("access_scope") == "pool"
        ]
        org_ctx = {
            "org_pool_instances": org_pool_allocations,
            "org_pool_groups": group_org_allocations_for_display(org_pool_allocations),
            "my_org_requests": get_user_requests_in_org(str(user.id), org_id),
            "pending_requests": get_org_requests(org_id, status="pending") if can_review else [],
            "can_manage_instances": can_manage,
            "can_review_requests": can_review,
            "can_request_instances": can_request,
            "hub_public_tab_is_catalog": public_tab_is_catalog,
            "org_active": org_active,
        }

    return {
        "_meta": {"title": "Hub — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Hub",
        "templates": templates,
        "hub_tab": hub_tab,
        "error": request.args.get("error"),
        **ws_ctx,
        **org_ctx,
    }


@router.page("/hub/<instance>")
def hub_template(instance: str):
    session, redir = require_session()
    if redir:
        return redir
    user = session.user

    template = get_hub_template_by_slug(instance)
    if template is None:
        return {"_redirect": "/hub"}

    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    is_org = active_workspace is not None
    plans = get_template_plans_for_hub(template["id"])

    can_start = True  # personal users can start immediately
    can_prepare = False
    can_request = False
    org_members = []
    if active_workspace:
        org_id = str(active_workspace["id"])
        user_perms = set(org_repo.get_member_permissions(org_id, str(user.id)))
        is_owner = org_repo.is_org_owner(org_id, str(user.id))
        can_prepare = active_workspace.get("status") == "active" and (is_owner or "sandbox.allocations.prepare" in user_perms)
        can_request = active_workspace.get("status") == "active" and (is_owner or "sandbox.requests.submit" in user_perms)
        can_start = can_prepare
        if can_prepare:
            org_members = org_repo.get_members_with_info(org_id)

    user_balance = None
    available_balance = None
    if user.platform_role == "user":
        if active_workspace:
            billing = get_org_billing(str(active_workspace["id"]))
        else:
            billing = get_user_billing(str(user.id))
        user_balance = billing["balance"]["available_amount_display"]
        try:
            available_balance = Decimal(str(billing["balance"]["available_amount"]))
        except (InvalidOperation, TypeError, ValueError):
            available_balance = Decimal("0")

    for plan in plans:
        required_key = (
            "org_minimum_start_amount"
            if is_org
            else "ind_minimum_start_amount"
        )
        required_display_key = (
            "org_minimum_start_amount_display"
            if is_org
            else "ind_minimum_start_amount_display"
        )
        required = Decimal(str(plan.get(required_key) or "0"))
        plan["minimum_start_amount"] = str(required)
        plan["minimum_start_amount_display"] = str(
            plan.get(required_display_key) or f"{required:.4f}"
        )
        plan["can_afford"] = available_balance is None or available_balance >= required

    requested_plan_id = str(request.args.get("plan") or "")
    selected_plan_id = (
        requested_plan_id
        if any(str(plan.get("id")) == requested_plan_id for plan in plans)
        else (str(plans[0].get("id")) if plans else "")
    )

    nav = build_nav("/hub", user, active_workspace)
    return {
        "_meta": {"title": f"{template['name']} — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "template": template,
        "plans": plans,
        "is_org": is_org,
        "can_start": can_start,
        "can_prepare": can_prepare,
        "can_request": can_request,
        "org_members": org_members,
        "selected_plan_id": selected_plan_id,
        "user_balance": user_balance,
        "error": request.args.get("error"),
        **ws_ctx,
    }


@router.page("/org-allocations/<allocation_id>/edit")
def org_allocation_edit_page(allocation_id: str):
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return {"_redirect": "/hub"}
    context, error = get_org_allocation_edit_context(
        allocation_id,
        str(active_workspace["id"]),
        str(user.id),
    )
    if error or context is None:
        return {"_redirect": f"/hub?tab=private&error={quote_plus(error or 'Allocation not found.')}"}
    nav = build_nav("/hub", user, active_workspace)
    return {
        "_meta": {"title": "Edit allocation — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Edit allocation",
        "error": request.args.get("error"),
        **context,
        **ws_ctx,
    }


@router.page("/hub/<instance>/<slug>")
def hub_sandbox(instance: str, slug: str):
    session, redir = require_session()
    if redir:
        return redir
    user = session.user

    template = get_hub_template_by_slug(instance)
    if template is None:
        return {"_redirect": "/hub"}

    plans = get_template_plans_for_hub(template["id"])
    plan = next((p for p in plans if p["id"] == slug), None)
    if plan is None:
        return {"_redirect": f"/hub/{instance}"}

    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    org_id = str(active_workspace["id"]) if active_workspace else None
    if active_workspace and active_workspace.get("status") != "active":
        return {"_redirect": _org_inactive_url(f"/hub/{instance}")}
    sandbox_instance = get_active_hub_instance(
        template["id"], slug, user_id=str(user.id), org_id=org_id
    )
    if sandbox_instance is None:
        # Nothing running for this user/template/plan — nothing for the IDE
        # to attach to (e.g. a stale bookmark after the instance stopped).
        return {"_redirect": f"/hub/{instance}?error=No+running+instance+for+this+plan.+Start+one+first."}

    return {"_redirect": f"/instances/{sandbox_instance['id']}"}


# ── My Instances ──────────────────────────────────────────────────────────────

@router.page("/my-instances")
def my_instances():
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if active_workspace:
        if active_workspace.get("status") != "active":
            return {"_redirect": _org_inactive_url("/dashboard")}
        org_id = str(active_workspace["id"])
        instances = get_user_assigned_instances(str(user.id), org_id)
        billing_live_url = f"/billing/live?org_id={org_id}"
    else:
        g._billing_workspace_override_set = True
        g._billing_workspace_override = None
        instances = get_user_instances(str(user.id))
        billing_live_url = "/billing/live?scope=personal"
    nav = build_nav("/my-instances", user, active_workspace)
    return {
        "_meta": {"title": "My Instances — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "My Instances",
        "instances": instances,
        "instance_scope_label": (
            f"{active_workspace['name']} sessions" if active_workspace else "Personal sessions"
        ),
        "error": request.args.get("error"),
        "billing_live_url": billing_live_url,
        **ws_ctx,
    }


@router.page("/instances/<instance_id>")
def instance_detail(instance_id: str):
    # Real Test Launches are opened by platform admins/staff, while ordinary
    # instances are opened by sandbox users. Authorization is enforced by
    # get_instance_ui_context/can_view_instance, so do not reject staff here.
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    ctx, error = get_instance_ui_context(
        instance_id,
        str(user.id),
        request.args.get("ui_mode") or request.args.get("mode"),
        request.args.get("node"),
    )
    if error or ctx is None:
        return {"_redirect": "/my-instances"}
    instance = ctx["instance"]
    base_ws_ctx = _workspaces_ctx(user)
    instance_org_id = str(instance.get("workspace_org_id") or "")
    if instance.get("workspace_type") == "org" and instance_org_id:
        contextual_workspace = next(
            (w for w in (base_ws_ctx.get("workspace_list") or []) if str(w.get("id")) == instance_org_id),
            None,
        )
        if contextual_workspace is None:
            return {"_redirect": "/my-instances"}
        ws_ctx = _workspaces_ctx(user, contextual_workspace)
        g._billing_workspace_override_set = True
        g._billing_workspace_override = contextual_workspace
        billing_live_url = f"/billing/live?org_id={instance_org_id}"
    elif instance.get("workspace_type") == "personal":
        ws_ctx = _workspaces_ctx(user, force_personal=True)
        g._billing_workspace_override_set = True
        g._billing_workspace_override = None
        billing_live_url = "/billing/live?scope=personal"
    else:
        ws_ctx = base_ws_ctx
        billing_live_url = "/billing/live"

    from codesandbox.features.workflow.service import get_workflow_run_context_for_instance

    workflow_run = get_workflow_run_context_for_instance(instance_id)

    nav_path = (
        "/platform/sandboxes"
        if instance.get("workspace_type") == "test"
        and user.platform_role in {"system_admin", "system_staff"}
        else "/my-instances"
    )

    return {
        "_meta": {"title": f"{instance['template_name']} - CodeSandbox"},
        "user": _user_ctx(user),
        "nav": build_nav(nav_path, user, ws_ctx.get("active_workspace")),
        "page_title": instance["template_name"],
        "error": request.args.get("error"),
        "workflow_run": workflow_run,
        "billing_live_url": billing_live_url,
        **ctx,
        **ws_ctx,
    }


@web_bp.post("/instances/<instance_id>/notes")
def instance_notes_save(instance_id: str):
    session, redir = require_session()
    if redir:
        abort(401)
    body = request.get_json(silent=True) or {}
    result, error = save_instance_note_for_view(
        instance_id,
        str(session.user.id),
        title=str(body.get("title") or ""),
        content=str(body.get("content") or ""),
    )
    if error:
        return {"ok": False, "error": error}, 403
    return {"ok": True, "notes": result}


# ── Private Instances (org-assigned) ─────────────────────────────────────────

@router.page("/private_instances")
def private_instances():
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")

    assigned = []
    if active_workspace:
        if active_workspace.get("status") != "active":
            return {"_redirect": _org_inactive_url("/dashboard")}
        org_id = str(active_workspace["id"])
        assigned = group_org_allocations_for_display(
            [
                allocation
                for allocation in get_org_allocations_for_user(org_id, str(user.id))
                if allocation.get("access_scope") == "private"
                and str(allocation.get("assigned_to_user_id") or "") == str(user.id)
                and allocation.get("status") in {"active", "in_use"}
            ]
        )

    nav = build_nav("/private_instances", user, active_workspace)
    return {
        "_meta": {"title": "Private Instances — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Private Instances",
        "instances": assigned,
        "error": request.args.get("error"),
        "billing_live_url": (f"/billing/live?org_id={active_workspace['id']}" if active_workspace else "/billing/live"),
        **ws_ctx,
    }


# ── Billing ───────────────────────────────────────────────────────────────────

@web_bp.get("/billing/live")
def billing_live():
    session, redir = require_session()
    if redir:
        return {"ok": False, "error": "Authentication required."}, 401
    requested_org_id = str(request.args.get("org_id") or "").strip()
    if requested_org_id:
        org = org_repo.get_organization(requested_org_id)
        if (
            org is None
            or org.status != "active"
            or org_repo.get_member(requested_org_id, str(session.user.id)) is None
        ):
            return {"ok": False, "error": "Organization workspace not available."}, 403
        org_id = requested_org_id
    elif request.args.get("scope") == "personal":
        org_id = None
    else:
        ws_ctx = _workspaces_ctx(session.user)
        active_workspace = ws_ctx.get("active_workspace")
        org_id = str(active_workspace["id"]) if active_workspace else None
    return {
        "ok": True,
        "balance": get_live_balance_for_actor(str(session.user.id), org_id),
    }


@router.page("/billing")
def billing():
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    try:
        tx_page = int(request.args.get("page", "1"))
    except (TypeError, ValueError):
        tx_page = 1
    tx_page_size = 20

    if active_workspace:
        org_id = str(active_workspace["id"])
        if active_workspace.get("status") != "active":
            return {"_redirect": _org_inactive_url("/dashboard")}
        billing_permissions = set(org_repo.get_member_permissions(org_id, str(user.id)))
        is_org_owner = org_repo.is_org_owner(org_id, str(user.id))
        can_view_billing = is_org_owner or "sandbox.billing.view" in billing_permissions
        can_topup_billing = is_org_owner or "sandbox.billing.topup" in billing_permissions
        if not (can_view_billing or can_topup_billing):
            return {"_redirect": "/dashboard"}
        billing_data = get_org_billing(org_id, page=tx_page, page_size=tx_page_size)
        billing_label = active_workspace.get("name", "Org")
    else:
        can_view_billing = True
        can_topup_billing = True
        billing_data = get_user_billing(str(user.id), page=tx_page, page_size=tx_page_size)
        billing_label = user.name or user.email

    from decimal import Decimal
    from codesandbox.config import get_settings
    from codesandbox.features.billing import esewa_gateway, fx, stripe_gateway

    npr_display = None
    try:
        npr_display = fx.gbp_to_npr(Decimal(str(billing_data["balance"]["amount"])))
    except Exception:
        pass

    nav = build_nav("/billing", user, active_workspace)
    return {
        "_meta": {"title": "Billing — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Billing",
        "billing_label": billing_label,
        "balance_npr": npr_display,
        "stripe_publishable_key": get_settings().stripe_publishable_key,
        "billing_dev_topup_enabled": get_settings().billing_dev_topup_enabled,
        "can_topup": can_topup_billing,
        "min_topup_gbp": stripe_gateway.MIN_TOPUP_GBP,
        "min_topup_npr": esewa_gateway.MIN_TOPUP_NPR,
        "error": request.args.get("error"),
        **billing_data,
        **ws_ctx,
    }


@web_bp.post("/billing/topup")
@verified_email("adding funds")
def billing_topup_action():
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    return redirect("/billing?error=Payment+gateway+not+configured", 303)


# ── Hub action routes (Phase 4a) ──────────────────────────────────────────────

@web_bp.post("/hub/<instance>/start")
def hub_start(instance: str):
    """Personal: start now. Organization: prepare allocations without starting."""
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    user = session.user
    plan_id = request.form.get("plan_id", "")
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if active_workspace:
        if active_workspace.get("status") != "active":
            return redirect(_org_inactive_url(f"/hub/{instance}"), 303)
        org_id = str(active_workspace["id"])
        user_perms = set(org_repo.get_member_permissions(org_id, str(user.id)))
        is_owner = org_repo.is_org_owner(org_id, str(user.id))
        if not is_owner and "sandbox.allocations.prepare" not in user_perms:
            return redirect(f"/hub/{instance}?error=Permission+denied.", 303)
        try:
            quantity = int(request.form.get("quantity", "1") or "1")
            max_session_hours = float(request.form.get("max_session_hours", "2") or "2")
            max_starts = int(request.form.get("max_starts_per_member", "1") or "1")
        except (TypeError, ValueError):
            return redirect(f"/hub/{instance}?error=Invalid+guardrail+values.", 303)
        scope = request.form.get("access_scope", "pool")
        assigned_user_id = request.form.get("assigned_to_user_id") or None
        rows, err = create_org_allocations(
            org_id=org_id,
            creator_user_id=str(user.id),
            template_slug=instance,
            plan_id=plan_id,
            access_scope=scope,
            assigned_to_user_id=assigned_user_id,
            quantity=quantity,
            max_session_minutes=max(1, int(max_session_hours * 60)),
            max_starts_per_member=max_starts,
        )
        if err:
            return redirect(f"/hub/{instance}?error={quote_plus(err)}", 303)
        return redirect("/hub?tab=private&prepared=1", 303)

    result, err = create_personal_instance(str(user.id), instance, plan_id)
    if err:
        return redirect(f"/hub/{instance}?error={quote_plus(err)}", 303)
    input_file = request.files.get("input_file")
    if input_file and input_file.filename:
        _, err = upload_instance_input(result["id"], str(user.id), input_file)
        if err:
            archive_instance_for_user(result["id"], str(user.id))
            return redirect(f"/hub/{instance}?error={quote_plus(err)}", 303)
    _, err = start_instance(result["id"], actor_user_id=str(user.id))
    if err:
        archive_instance_for_user(result["id"], str(user.id))
        return redirect(f"/hub/{instance}?error={quote_plus(err)}", 303)
    return redirect(f"/instances/{result['id']}", 303)


@web_bp.post("/hub/<instance>/request")
def hub_request(instance: str):
    """Submit an InstanceRequest (org members without sandbox.instances.create)."""
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    if active_workspace.get("status") != "active":
        return redirect(_org_inactive_url("/hub?tab=public"), 303)

    plan_id = request.form.get("plan_id", "")
    note = request.form.get("note", "").strip()
    org_id = str(active_workspace["id"])
    try:
        max_session_minutes = max(1, int(float(request.form.get("max_session_hours", "2") or "2") * 60))
        max_starts = max(1, int(request.form.get("max_starts", "1") or "1"))
    except (TypeError, ValueError):
        return redirect(f"/hub/{instance}?error=Invalid+request+guardrails.", 303)
    _, err = submit_instance_request(
        org_id, str(user.id), instance, plan_id, note or None,
        max_session_minutes=max_session_minutes, max_starts=max_starts,
    )
    if err:
        return redirect(f"/hub/{instance}?error={quote_plus(err)}", 303)
    return redirect("/hub?requested=1", 303)




@web_bp.post("/org-allocations/<allocation_id>/start")
def org_allocation_start(allocation_id: str):
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    result, err = claim_org_allocation(
        allocation_id,
        str(user.id),
        expected_org_id=str(active_workspace["id"]),
    )
    if err or result is None:
        return redirect(f"/hub?tab=private&error={quote_plus(err or 'Unable to claim allocation.')}", 303)
    input_file = request.files.get("input_file")
    if input_file and input_file.filename:
        _, err = upload_instance_input(result["id"], str(user.id), input_file)
        if err:
            archive_instance_for_user(result["id"], str(user.id))
            return redirect(f"/hub?tab=private&error={quote_plus(err)}", 303)
    _, err = start_instance(result["id"], actor_user_id=str(user.id))
    if err:
        archive_instance_for_user(result["id"], str(user.id))
        return redirect(f"/hub?tab=private&error={quote_plus(err)}", 303)
    return redirect(f"/instances/{result['id']}", 303)


@web_bp.post("/org-allocations/archive")
def org_allocations_bulk_archive():
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    ws_ctx = _workspaces_ctx(session.user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    allocation_ids = [
        str(value).strip()
        for value in request.form.getlist("allocation_id")
        if str(value).strip()
    ]
    if not allocation_ids:
        return redirect("/hub?tab=private&error=No+allocation+selected.", 303)

    _, err = set_org_allocation_group_status(
        allocation_ids,
        str(active_workspace["id"]),
        str(session.user.id),
        status="archived",
    )
    target = "/hub?tab=private"
    if err:
        target += "&error=" + quote_plus(err)
    return redirect(target, 303)


@web_bp.post("/org-allocations/status")
def org_allocations_status():
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    ws_ctx = _workspaces_ctx(session.user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    allocation_ids = [
        str(value).strip()
        for value in request.form.getlist("allocation_id")
        if str(value).strip()
    ]
    status = str(request.form.get("status") or "").strip().lower()
    _, err = set_org_allocation_group_status(
        allocation_ids,
        str(active_workspace["id"]),
        str(session.user.id),
        status=status,
    )
    target = "/hub?tab=private"
    if err:
        target += "&error=" + quote_plus(err)
    return redirect(target, 303)


@web_bp.post("/org-allocations/<allocation_id>/edit")
def org_allocation_edit_action(allocation_id: str):
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    ws_ctx = _workspaces_ctx(session.user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    try:
        quantity = int(request.form.get("quantity", "1") or "1")
        max_session_hours = float(request.form.get("max_session_hours", "2") or "2")
        max_starts = int(request.form.get("max_starts_per_member", "1") or "1")
    except (TypeError, ValueError):
        return redirect(f"/org-allocations/{allocation_id}/edit?error=Invalid+guardrail+values.", 303)
    _, err = update_org_allocation_group(
        allocation_id,
        str(active_workspace["id"]),
        str(session.user.id),
        access_scope=str(request.form.get("access_scope") or "pool"),
        assigned_to_user_id=request.form.get("assigned_to_user_id") or None,
        quantity=quantity,
        max_session_minutes=max(1, int(max_session_hours * 60)),
        max_starts_per_member=max_starts,
        status=str(request.form.get("status") or "active"),
    )
    if err:
        return redirect(f"/org-allocations/{allocation_id}/edit?error={quote_plus(err)}", 303)
    return redirect("/hub?tab=private", 303)


@web_bp.post("/org-allocations/<allocation_id>/archive")
def org_allocation_archive(allocation_id: str):
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    ws_ctx = _workspaces_ctx(session.user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    _, err = archive_org_allocation(
        allocation_id, str(active_workspace["id"]), str(session.user.id)
    )
    target = "/hub?tab=private"
    if err:
        target += "&error=" + quote_plus(err)
    return redirect(target, 303)

@web_bp.post("/hub/request/<request_id>/review")
def hub_review_request(request_id: str):
    """Approve or deny an InstanceRequest (requires sandbox.requests.review)."""
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")
    if not active_workspace:
        return redirect("/hub", 303)
    if active_workspace.get("status") != "active":
        return redirect(_org_inactive_url("/hub?tab=public"), 303)

    org_id = str(active_workspace["id"])
    user_perms = org_repo.get_member_permissions(org_id, str(user.id))
    is_owner = org_repo.is_org_owner(org_id, str(user.id))
    if not is_owner and "sandbox.requests.review" not in user_perms:
        return redirect("/hub", 303)

    action = request.form.get("action", "")
    review_note = request.form.get("review_note", "").strip()
    _, err = review_instance_request(request_id, org_id, str(user.id), action, review_note or None)
    if err:
        return redirect(f"/hub?tab=public&error={quote_plus(err)}", 303)
    return redirect("/hub", 303)
