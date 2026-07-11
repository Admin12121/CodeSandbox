from __future__ import annotations

import json as _json
from urllib.parse import quote

from flask import Response, abort, redirect, request, stream_with_context
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from codesandbox.config import get_settings
from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session
from codesandbox.shared.storage import get_private_object, upload_image_from_filestorage
from codesandbox.web.blueprint import web_bp
from codesandbox.web.csrf import csrf_exempt

from .service import (
    can_open_instance_channel,
    delete_template,
    get_artifact_for_download,
    get_instance_artifacts_for_view,
    handle_worker_callback,
    list_reconcile_candidates,
    make_worker_callback_token,
    save_plan,
    save_template,
    save_template_config,
    save_template_plan_configs,
    set_template_status,
    start_instance,
    start_test_instance,
    stop_instance,
    toggle_plan_active,
    toggle_template_plan_enabled,
    upload_instance_input,
)

# Must match the salt asgi.py verifies the token with.
_WS_TOKEN_SALT = "sandbox.monitor-ws"
_WORKER_CALLBACK_SALT = "sandbox.worker-callback"
_WORKER_ACTION_EVENTS = {
    "start": {"started", "heartbeat", "cleanup_started", "stopped", "expired", "failed", "escape_attempt", "artifact_ready"},
    "stop": {"heartbeat", "cleanup_started", "artifact_ready", "stopped", "expired", "failed"},
    "kill": {"cleanup_started", "artifact_ready", "killed", "failed"},
    "reconcile": {"started", "heartbeat", "cleanup_started", "artifact_ready", "stopped", "expired", "killed", "failed"},
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
        runtime_config=request.form.get("runtime_config", ""),
        created_by_id=str(cs.user.id),
        runtime_class=request.form.get("runtime_class", "container"),
        interface_mode=",".join(request.form.getlist("interface_mode")) or "terminal",
        network_mode=request.form.get("network_mode", "disabled"),
        allow_root=request.form.get("allow_root") == "1",
        max_timeout_hr=int(request.form.get("max_timeout_hr") or 2),
        default_command=request.form.get("default_command", ""),
        working_dir=request.form.get("working_dir", "/workspace"),
        input_mount_path=request.form.get("input_mount_path", "/input"),
        output_mount_path=request.form.get("output_mount_path", "/output"),
        artifact_paths=request.form.get("artifact_paths", ""),
        input_required=request.form.get("input_required") == "1",
        max_upload_mb=int(request.form.get("max_upload_mb") or 50),
        read_only_root=request.form.get("read_only_root") == "1",
        run_as_user=request.form.get("run_as_user", ""),
        pids_limit=int(request.form.get("pids_limit") or 256),
        allow_full_internet=request.form.get("allow_full_internet") == "1",
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
    if not isinstance(plan_data, list):
        return _sandboxes_redirect(template_id, "Invalid plans data.")
    error = save_template_plan_configs(template_id, plan_data)
    if error:
        return _sandboxes_redirect(template_id, error)
    return redirect(f"/platform/sandboxes?template={template_id}&tab=plans", 303)


@web_bp.post("/platform/sandboxes/<template_id>/plans/<plan_id>/toggle")
@platform_perm("platform.sandboxes.manage")
def toggle_template_plan_action(template_id: str, plan_id: str):
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    is_enabled = bool(data.get("is_enabled", True))
    error = toggle_template_plan_enabled(template_id, plan_id, is_enabled)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    return jsonify({"ok": True})


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
        min_billable_minutes=int(request.form.get("min_billable_minutes") or 0),
        allowed_network_modes=request.form.getlist("allowed_network_modes"),
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

@web_bp.post("/instances/<instance_id>/start")
def start_instance_action(instance_id: str):
    cs = get_current_session()
    if not cs:
        return redirect("/login", 303)
    _, err = start_instance(instance_id, actor_user_id=str(cs.user.id))
    if err:
        return redirect(f"/instances/{instance_id}?error={quote(err)}", 303)
    return redirect(f"/instances/{instance_id}", 303)


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
    purpose = request.args.get("purpose", "monitor")
    if purpose not in _WS_TOKEN_PURPOSES:
        abort(400)
    if not can_open_instance_channel(instance_id, str(cs.user.id), purpose):
        abort(403)
    token = URLSafeTimedSerializer(get_settings().secret_key, salt=_WS_TOKEN_SALT).dumps(
        {"instance_id": instance_id, "purpose": purpose}
    )
    return {"token": token}


@web_bp.post("/instances/<instance_id>/inputs")
def instance_input_upload(instance_id: str):
    cs = get_current_session()
    if not cs:
        abort(401)
    result, error = upload_instance_input(
        instance_id,
        str(cs.user.id),
        request.files.get("file"),
    )
    if error:
        return {"ok": False, "error": error}, 400
    return {"ok": True, "input": result}, 201


@web_bp.get("/instances/<instance_id>/artifacts")
def instance_artifact_list(instance_id: str):
    cs = get_current_session()
    if not cs:
        abort(401)
    artifacts, error = get_instance_artifacts_for_view(instance_id, str(cs.user.id))
    if error:
        abort(403)
    return {"ok": True, "artifacts": artifacts}


@web_bp.get("/artifacts/<artifact_id>/download")
def artifact_download(artifact_id: str):
    cs = get_current_session()
    if not cs:
        abort(401)
    artifact, error = get_artifact_for_download(artifact_id, str(cs.user.id))
    if error or artifact is None:
        abort(404 if error == "Artifact not found." else 403)
    try:
        obj = get_private_object(artifact.storage_key)
    except Exception:
        abort(404)

    filename = secure_filename(artifact.name.rsplit("/", 1)[-1]) or "artifact"
    response = Response(
        stream_with_context(obj["Body"].iter_chunks(chunk_size=64 * 1024)),
        mimetype=obj.get("ContentType") or "application/octet-stream",
        direct_passthrough=True,
    )
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Content-Length"] = str(int(artifact.size_bytes or 0))
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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

    result, err = handle_worker_callback(instance_id, job_id, event, data)
    if err:
        return {"ok": False, "error": err}, 400
    refreshed = make_worker_callback_token(
        job_id,
        instance_id,
        str(claims.get("action") or ""),
    )
    return {"ok": True, "callback_token": refreshed, **result}


@web_bp.post("/internal/worker/register")
@csrf_exempt
def worker_register():
    """Worker fleet registration — called once at worker boot.

    Not scoped to any one job, so it can't use the per-job callback token;
    instead it's signed with the same shared SANDBOX_JOB_SIGNING_KEY already
    used to sign/verify Redis job payloads.
    """
    from codesandbox.features.worker.service import register_worker, verify_worker_signature

    body = request.get_json(silent=True) or {}
    if not verify_worker_signature(body) or not str(body.get("worker_id") or ""):
        abort(401)
    node = register_worker(body)
    return {"ok": True, "worker_id": node.worker_id, "status": node.status}


@web_bp.post("/internal/worker/heartbeat")
@csrf_exempt
def worker_heartbeat():
    from codesandbox.features.worker.service import record_heartbeat, verify_worker_signature

    body = request.get_json(silent=True) or {}
    if not verify_worker_signature(body) or not str(body.get("worker_id") or ""):
        abort(401)
    node = record_heartbeat(body)
    if node is None:
        abort(404)
    return {"ok": True, "worker_id": node.worker_id, "status": node.status}


@web_bp.post("/internal/worker/instances")
@csrf_exempt
def worker_instances():
    """Called once at worker boot: everything the DB thinks this worker_id
    still owns, so it can rebuild its in-memory registry against whatever
    containers are still actually running (see docs/runtime-architecture.md,
    "worker restart recovery")."""
    from codesandbox.features.worker.service import verify_worker_signature

    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "")
    if not verify_worker_signature(body) or not worker_id:
        abort(401)
    return {"ok": True, "instances": list_reconcile_candidates(worker_id)}
