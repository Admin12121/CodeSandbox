from __future__ import annotations

import os

from flask import abort, redirect, request, send_from_directory, session as flask_session

from codesandbox.features.identity import repository as identity_repo
from codesandbox.features.organizations import repository as org_repo
from codesandbox.features.sandbox.service import (
    create_personal_instance,
    get_hub_template_by_slug,
    get_hub_templates,
    get_org_billing,
    get_org_instances,
    get_org_requests,
    get_template_plans_for_hub,
    get_user_assigned_instances,
    get_user_billing,
    get_user_instances,
    get_user_requests_in_org,
    review_instance_request,
    start_instance,
    submit_instance_request,
)
from codesandbox.shared.session import build_nav, require_sandbox_user, require_session
from codesandbox.web.blueprint import router, web_bp
from codesandbox.web._ctx import _user_ctx, _workspaces_ctx

_TEMPLATES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../templates"))
_PUBLIC_DIR = os.path.join(_TEMPLATES_DIR, "public")
_FAVICON = os.path.join(_TEMPLATES_DIR, "favicon.ico")


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
    if active_workspace:
        org_id = str(active_workspace["id"])
        user_perms = org_repo.get_member_permissions(org_id, str(user.id))
        is_owner = org_repo.is_org_owner(org_id, str(user.id))
        can_manage = is_owner or "sandbox.instances.create" in user_perms
        can_review = is_owner or "sandbox.requests.review" in user_perms
        org_ctx = {
            "org_pool_instances": get_org_instances(org_id) if (can_manage or "sandbox.instances.view_all" in user_perms or is_owner) else [],
            "my_org_requests": get_user_requests_in_org(str(user.id), org_id),
            "pending_requests": get_org_requests(org_id, status="pending") if can_review else [],
            "can_manage_instances": can_manage,
            "can_review_requests": can_review,
        }

    return {
        "_meta": {"title": "Hub — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Hub",
        "templates": templates,
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

    can_start = True  # personal users can always start
    if active_workspace:
        org_id = str(active_workspace["id"])
        user_perms = org_repo.get_member_permissions(org_id, str(user.id))
        is_owner = org_repo.is_org_owner(org_id, str(user.id))
        can_start = is_owner or "sandbox.instances.create" in user_perms

    user_balance = None
    if user.platform_role == "user":
        if active_workspace:
            billing = get_org_billing(str(active_workspace["id"]))
        else:
            billing = get_user_billing(str(user.id))
        user_balance = billing["balance"]["amount"]

    nav = build_nav("/hub", user, active_workspace)
    return {
        "_meta": {"title": f"{template['name']} — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "template": template,
        "plans": plans,
        "is_org": is_org,
        "can_start": can_start,
        "user_balance": user_balance,
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
    nav = build_nav("/hub", user, ws_ctx.get("active_workspace"))
    return {
        "_meta": {"title": f"{template['name']} Sandbox — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "template": template,
        "plan": plan,
        **ws_ctx,
    }


# ── My Instances ──────────────────────────────────────────────────────────────

@router.page("/my-instances")
def my_instances():
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    nav = build_nav("/my-instances", user, ws_ctx.get("active_workspace"))
    instances = get_user_instances(str(user.id))
    return {
        "_meta": {"title": "My Instances — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "My Instances",
        "instances": instances,
        **ws_ctx,
    }


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
        org_id = str(active_workspace["id"])
        assigned = get_user_assigned_instances(str(user.id), org_id)

    nav = build_nav("/private_instances", user, active_workspace)
    return {
        "_meta": {"title": "Private Instances — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Private Instances",
        "instances": assigned,
        **ws_ctx,
    }


# ── Billing ───────────────────────────────────────────────────────────────────

@router.page("/billing")
def billing():
    session, redir = require_sandbox_user()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    active_workspace = ws_ctx.get("active_workspace")

    if active_workspace:
        org_id = str(active_workspace["id"])
        if not org_repo.is_org_owner(org_id, str(user.id)):
            return {"_redirect": "/dashboard"}
        billing_data = get_org_billing(org_id)
        billing_label = active_workspace.get("name", "Org")
    else:
        billing_data = get_user_billing(str(user.id))
        billing_label = user.name or user.email

    nav = build_nav("/billing", user, active_workspace)
    return {
        "_meta": {"title": "Billing — CodeSandbox"},
        "user": _user_ctx(user),
        "nav": nav,
        "page_title": "Billing",
        "billing_label": billing_label,
        **billing_data,
        **ws_ctx,
    }


# ── Hub action routes (Phase 4a) ──────────────────────────────────────────────

@web_bp.post("/hub/<instance>/start")
def hub_start(instance: str):
    """Create a personal SandboxInstance and redirect to the sandbox IDE."""
    session, redir = require_sandbox_user()
    if redir:
        return redirect("/login", 303)
    user = session.user
    plan_id = request.form.get("plan_id", "")
    result, err = create_personal_instance(str(user.id), instance, plan_id)
    if err:
        return redirect(f"/hub/{instance}?error={err}", 303)
    _, err = start_instance(result["id"], actor_user_id=str(user.id))
    if err:
        return redirect(f"/hub/{instance}?error={err}", 303)
    return redirect(f"/hub/{instance}/{plan_id}", 303)


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

    plan_id = request.form.get("plan_id", "")
    note = request.form.get("note", "").strip()
    org_id = str(active_workspace["id"])
    _, err = submit_instance_request(org_id, str(user.id), instance, plan_id, note or None)
    if err:
        return redirect(f"/hub/{instance}?error={err}", 303)
    return redirect("/hub?requested=1", 303)


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

    org_id = str(active_workspace["id"])
    user_perms = org_repo.get_member_permissions(org_id, str(user.id))
    is_owner = org_repo.is_org_owner(org_id, str(user.id))
    if not is_owner and "sandbox.requests.review" not in user_perms:
        return redirect("/hub", 303)

    action = request.form.get("action", "")
    review_note = request.form.get("review_note", "").strip()
    _, err = review_instance_request(request_id, org_id, str(user.id), action, review_note or None)
    if err:
        return redirect(f"/hub?error={err}", 303)
    return redirect("/hub", 303)


# ── Laboratory (internal testing) ────────────────────────────────────────────

@router.page("/laboratory/sandbox")
def sandbox():
    session, redir = require_session()
    if redir:
        return redir
    user = session.user
    ws_ctx = _workspaces_ctx(user)
    nav = build_nav("/laboratory/sandbox", user, ws_ctx.get("active_workspace"))

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
