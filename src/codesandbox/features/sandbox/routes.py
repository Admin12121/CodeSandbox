from __future__ import annotations

import os
import uuid as _uuid
from urllib.parse import quote

from flask import redirect, request

from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session
from codesandbox.web.blueprint import web_bp

import json as _json

from .service import (
    delete_template,
    save_plan,
    save_template,
    save_template_config,
    save_template_plan_configs,
    set_template_status,
    toggle_plan_active,
)

_PUBLIC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../templates/public"))
_THUMB_ALLOWED = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
_THUMB_EXTS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}


def _save_thumbnail(file_storage) -> str | None:
    if not file_storage or not file_storage.filename:
        return None
    mime = (file_storage.mimetype or "").split(";")[0].strip()
    if mime not in _THUMB_ALLOWED:
        return None
    data = file_storage.read()
    if not data or len(data) > 2 * 1024 * 1024:
        return None
    thumbs_dir = os.path.join(_PUBLIC_DIR, "thumbnails")
    os.makedirs(thumbs_dir, exist_ok=True)
    filename = f"{_uuid.uuid4().hex}.{_THUMB_EXTS[mime]}"
    with open(os.path.join(thumbs_dir, filename), "wb") as fh:
        fh.write(data)
    return f"/thumbnails/{filename}"


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

    icon_path = (_save_thumbnail(request.files.get("icon_file"))
                 or request.form.get("existing_icon_path", ""))

    result, error = save_template(
        template_id=template_id,
        name=request.form.get("name", ""),
        description=request.form.get("description", ""),
        icon_path=icon_path,
        docker_image=request.form.get("docker_image", ""),
        sandbox_type=request.form.get("sandbox_type", "interactive"),
        type_config=request.form.get("type_config", ""),
        created_by_id=str(cs.user.id),
        runtime_class=request.form.get("runtime_class", "container"),
        interface_mode=",".join(request.form.getlist("interface_mode")) or "terminal",
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


@web_bp.post("/platform/sandboxes/<template_id>/plans")
@platform_perm("platform.sandboxes.manage")
def save_template_plans_action(template_id: str):
    plans_json = request.form.get("plans_json", "[]")
    try:
        plan_data = _json.loads(plans_json)
    except Exception:
        return _sandboxes_redirect(template_id, "Invalid plans data.")
    save_template_plan_configs(template_id, plan_data)
    return redirect(f"/platform/sandboxes?template={template_id}&tab=plans", 303)


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
