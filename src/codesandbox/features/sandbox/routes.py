from __future__ import annotations

from urllib.parse import quote

from flask import jsonify, redirect, request

from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session
from codesandbox.web.blueprint import web_bp

from .service import delete_template, save_plan, save_template, save_template_config, set_template_status, toggle_plan_active


def _sandboxes_redirect(template_id: str | None = None, error: str | None = None):
    url = "/platform/sandboxes"
    params = []
    if template_id:
        params.append(f"template={template_id}")
    if error:
        params.append(f"error={quote(error)}")
    if params:
        url += "?" + "&".join(params)
    return redirect(url, code=303)


def _plans_redirect(plan_id: str | None = None, error: str | None = None):
    url = "/platform/sandbox-plans"
    params = []
    if plan_id:
        params.append(f"plan={plan_id}")
    if error:
        params.append(f"error={quote(error)}")
    if params:
        url += "?" + "&".join(params)
    return redirect(url, code=303)


# ── Sandbox Templates ─────────────────────────────────────────────────────────

@web_bp.post("/platform/sandboxes/save")
@platform_perm("platform.sandboxes.manage")
def save_template_action():
    cs = get_current_session()
    template_id = request.form.get("template_id") or None

    result, error = save_template(
        template_id=template_id,
        name=request.form.get("name", ""),
        description=request.form.get("description", ""),
        icon_path="",
        docker_image=request.form.get("docker_image", ""),
        sandbox_type=request.form.get("sandbox_type", "interactive"),
        type_config=request.form.get("type_config", ""),
        created_by_id=str(cs.user.id),
        runtime_class=request.form.get("runtime_class", "container"),
        interface_mode=request.form.get("interface_mode", "terminal"),
        network_mode=request.form.get("network_mode", "disabled"),
        allow_root=request.form.get("allow_root") == "1",
        max_timeout_hr=int(request.form.get("max_timeout_hr") or 2),
    )
    if error:
        return _sandboxes_redirect(template_id or "new", error)
    return _sandboxes_redirect(result["id"])


@web_bp.post("/platform/sandboxes/<template_id>/config")
@platform_perm("platform.sandboxes.manage")
def save_template_config_action(template_id: str):
    config_json = request.form.get("config_json", "")
    save_template_config(template_id, config_json)
    return redirect(f"/platform/sandboxes?template={template_id}&tab=config", 303)


@web_bp.post("/platform/sandboxes/<template_id>/status")
@platform_perm("platform.sandboxes.manage")
def set_template_status_action(template_id: str):
    status = request.form.get("status", "")
    error = set_template_status(template_id, status)
    return _sandboxes_redirect(template_id, error)


@web_bp.post("/platform/sandboxes/<template_id>/delete")
@platform_perm("platform.sandboxes.manage")
def delete_template_action(template_id: str):
    error = delete_template(template_id)
    if error:
        return _sandboxes_redirect(template_id, error)
    return _sandboxes_redirect()


# ── Sandbox Plans ─────────────────────────────────────────────────────────────

@web_bp.post("/platform/sandbox-plans/save")
@platform_perm("platform.sandbox_plans.manage")
def save_plan_action():
    cs = get_current_session()

    result, error = save_plan(
        plan_id=request.form.get("plan_id", ""),
        name=request.form.get("name", ""),
        sort_order=int(request.form.get("sort_order") or 0),
        ind_vcpu=int(request.form.get("ind_vcpu") or 1),
        ind_ram_gb=int(request.form.get("ind_ram_gb") or 1),
        ind_disk_gb=int(request.form.get("ind_disk_gb") or 10),
        ind_cost_hr=request.form.get("ind_cost_hr", "0"),
        org_vcpu=int(request.form.get("org_vcpu") or 2),
        org_ram_gb=int(request.form.get("org_ram_gb") or 2),
        org_disk_gb=int(request.form.get("org_disk_gb") or 20),
        org_cost_hr=request.form.get("org_cost_hr", "0"),
        updated_by_id=str(cs.user.id),
    )
    if error:
        return _plans_redirect(request.form.get("plan_id") or "new", error)
    return _plans_redirect(result["id"])


@web_bp.post("/platform/sandbox-plans/<plan_id>/toggle")
@platform_perm("platform.sandbox_plans.manage")
def toggle_plan_action(plan_id: str):
    is_active = request.form.get("is_active") == "1"
    toggle_plan_active(plan_id, is_active)
    return _plans_redirect(plan_id)
