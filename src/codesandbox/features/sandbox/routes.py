from __future__ import annotations

import json as _json
from urllib.parse import quote

from flask import abort, redirect, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from codesandbox.config import get_settings
from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session
from codesandbox.shared.storage import upload_image_from_filestorage
from codesandbox.web.blueprint import web_bp
from codesandbox.web.csrf import csrf_exempt

from .service import (
    can_view_instance,
    delete_template,
    handle_worker_callback,
    save_plan,
    save_template,
    save_template_config,
    save_template_plan_configs,
    set_template_status,
    start_test_instance,
    stop_instance,
    toggle_plan_active,
)

# Must match the salt asgi.py verifies the token with.
_WS_TOKEN_SALT = "sandbox.monitor-ws"
_WORKER_CALLBACK_SALT = "sandbox.worker-callback"
_WORKER_ACTION_EVENTS = {
    "start": {"started", "stopped", "failed", "escape_attempt", "artifact_ready"},
    "stop": {"stopped", "failed"},
    "kill": {"killed", "failed"},
}

def _save_thumbnail(file_storage) -> str | None:
    # SVG intentionally excluded: shared/storage.py's allowlist excludes it too
    # (an uploaded SVG can embed <script>, a stored-XSS vector when served back).
    return upload_image_from_filestorage(file_storage, prefix="sandbox-templates")


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
    body = request.get_json(silent=True) or {}
    config_json = _json.dumps(body.get("files", {}))
    save_template_config(template_id, config_json)
    return {"ok": True}


@web_bp.post("/platform/sandboxes/<template_id>/status")
@platform_perm("platform.sandboxes.manage")
def set_template_status_action(template_id: str):
    body = request.get_json(silent=True) or {}
    status = body.get("status", "")
    error = set_template_status(template_id, status)
    if error:
        return {"ok": False, "error": error}, 400
    return {"ok": True}


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


# ── Admin: test-run a template ────────────────────────────────────────────────

@web_bp.post("/platform/sandboxes/<template_id>/test-run")
@platform_perm("platform.sandboxes.manage")
def test_run_action(template_id: str):
    cs = get_current_session()
    result, err = start_test_instance(template_id, actor_user_id=str(cs.user.id))
    if err:
        return {"ok": False, "error": err}, 400
    return {"ok": True, "instance_id": result["id"]}


# ── Instance stop (user-facing) ───────────────────────────────────────────────

@web_bp.post("/instances/<instance_id>/stop")
def stop_instance_action(instance_id: str):
    cs = get_current_session()
    if not cs:
        return redirect("/login", 303)
    _, err = stop_instance(instance_id, actor_user_id=str(cs.user.id))
    if err:
        return redirect(f"/my-instances?error={quote(err)}", 303)
    return redirect("/my-instances", 303)


# ── Instance monitor / terminal / fs token ─────────────────────────────────────

_WS_TOKEN_PURPOSES = {"monitor", "terminal", "fs"}


@web_bp.get("/instances/<instance_id>/monitor-token")
def instance_monitor_token(instance_id: str):
    """Issues a short-lived signed token gating a real-time WebSocket (monitor or terminal)
    or the filesystem REST API (fs) — see asgi.py for where each purpose is verified.

    Those WS routes live on the Starlette layer, outside this blueprint, so they
    never see the session cookie — this token is how the session's authorization
    decision (made here, with full access to it) is carried over to that layer.
    The token is scoped to both instance_id and purpose so a monitor token can't
    be replayed against the terminal route (or vice versa).
    """
    cs = get_current_session()
    if not cs:
        abort(401)
    if not can_view_instance(instance_id, str(cs.user.id)):
        abort(403)
    purpose = request.args.get("purpose", "monitor")
    if purpose not in _WS_TOKEN_PURPOSES:
        abort(400)
    token = URLSafeTimedSerializer(get_settings().secret_key, salt=_WS_TOKEN_SALT).dumps(
        {"instance_id": instance_id, "purpose": purpose}
    )
    return {"token": token}


# ── Internal worker callback ──────────────────────────────────────────────────

@web_bp.post("/internal/worker/callback")
@csrf_exempt
def worker_callback():
    """Receives status updates from the worker plane.

    Exempt from CSRF: the worker has no browser session/cookie, it authenticates
    with a short-lived bearer token scoped to the queued job.
    """
    settings = get_settings()
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401)
    raw_token = auth.removeprefix("Bearer ").strip()

    body = request.get_json(silent=True) or {}
    instance_id = body.get("instance_id", "")
    job_id = body.get("job_id", "")
    event = body.get("event", "")
    data = body.get("data") or {}

    if not instance_id or not job_id or not event:
        abort(400)
    try:
        claims = URLSafeTimedSerializer(
            settings.secret_key,
            salt=_WORKER_CALLBACK_SALT,
        ).loads(raw_token, max_age=settings.worker_callback_token_ttl_seconds)
    except SignatureExpired:
        abort(401)
    except BadSignature:
        abort(401)

    if claims.get("instance_id") != instance_id or claims.get("job_id") != job_id:
        abort(401)
    allowed_events = _WORKER_ACTION_EVENTS.get(str(claims.get("action") or ""), set())
    if event not in allowed_events:
        abort(401)

    err = handle_worker_callback(instance_id, job_id, event, data)
    if err:
        return {"ok": False, "error": err}, 400
    return {"ok": True}
