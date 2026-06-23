from __future__ import annotations

import urllib.parse
from urllib.parse import quote

from flask import jsonify, redirect, request

from codesandbox.shared.session import require_platform_role
from codesandbox.web.blueprint import web_bp

from .service import (
    add_role_member,
    create_platform_role,
    delete_platform_role,
    duplicate_platform_role,
    remove_role_member,
    save_staff_member,
    search_platform_role_member_candidates,
    toggle_role_permission,
    update_platform_role,
    update_platform_user,
)

_ADMIN_ROLES = ("system_admin", "system_staff")


def _roles_redirect(
    role_id: str | None = None, tab: str = "display", error: str | None = None
):
    url = "/platform/roles"
    params = []
    if role_id:
        params.append(f"role={role_id}")
        params.append(f"tab={tab}")
    if error:
        params.append(f"error={quote(error)}")
    if params:
        url += "?" + "&".join(params)
    return redirect(url, code=303)


@web_bp.post("/platform/users/<user_id>/update")
def update_user_action(user_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    platform_role = request.form.get("platform_role") or None
    status = request.form.get("status") or None
    name = request.form.get("name", "").strip() or None
    phone = request.form.get("phone", "").strip() or None

    if name is not None or phone is not None:
        from codesandbox.features.identity import repository as id_repo
        updates: dict = {}
        if name:
            updates["name"] = name
        if phone is not None:
            updates["phone"] = phone or None
        if updates:
            id_repo.update_user(user_id, **updates)

    if platform_role or status:
        update_platform_user(user_id, platform_role=platform_role, status=status)

    from_page = request.form.get("from") or ""
    if from_page == "detail":
        from codesandbox.features.identity import repository as id_repo
        target = id_repo.find_user_by_id(user_id)
        if target:
            return redirect(f"/platform/users/{urllib.parse.quote(target.email)}?info=User+updated.", code=303)
    return redirect("/platform/users", code=303)


@web_bp.post("/platform/users/<user_id>/update-field")
def platform_update_user_field_action(user_id: str):
    from flask import jsonify
    from codesandbox.features.identity import repository as id_repo
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    field = str(data.get("field", "")).strip()
    value = str(data.get("value", "")).strip() or None
    allowed = {"name", "phone", "platform_role", "status"}
    if field not in allowed:
        return jsonify({"ok": False, "error": "Invalid field."}), 400
    if field == "name" and not value:
        return jsonify({"ok": False, "error": "Name is required."}), 400
    if field == "platform_role" and value not in ("user", "system_staff", "system_admin"):
        return jsonify({"ok": False, "error": "Invalid role."}), 400
    if field == "status" and value not in ("active", "inactive", "banned"):
        return jsonify({"ok": False, "error": "Invalid status."}), 400
    if field in ("platform_role", "status"):
        update_platform_user(user_id, **{field: value})
    else:
        id_repo.update_user(user_id, **{field: value})
    return jsonify({"ok": True})


@web_bp.post("/platform/users/<user_id>/upload-avatar")
def platform_upload_user_avatar_action(user_id: str):
    from flask import jsonify
    from codesandbox.features.identity import repository as id_repo
    from codesandbox.shared.storage import upload_image_from_filestorage
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    avatar_url = upload_image_from_filestorage(request.files.get("logo"), prefix="avatars")
    if avatar_url is None:
        return jsonify({"ok": False, "error": "Invalid file. Use PNG, JPG, or WebP under 2 MB."}), 400
    id_repo.update_user(user_id, avatar_url=avatar_url)
    return jsonify({"ok": True, "url": avatar_url, "media_key": f"user:{user_id}"})


# ── Roles ─────────────────────────────────────────────────────────────────────


@web_bp.post("/platform/roles/create")
def create_role_action():
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    result, error = create_platform_role(
        name=request.form.get("name", ""),
        color=request.form.get("color", "#6366f1"),
        description=request.form.get("description") or None,
    )
    if error:
        return _roles_redirect("new", "display", error)
    return _roles_redirect(result["id"], "display")


@web_bp.post("/platform/roles/<role_id>/update")
def update_role_action(role_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    error = update_platform_role(
        role_id,
        name=request.form.get("name"),
        color=request.form.get("color"),
        description=request.form.get("description", ""),
    )
    return _roles_redirect(role_id, "display", error)


@web_bp.post("/platform/roles/<role_id>/duplicate")
def duplicate_role_action(role_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    new_id, error = duplicate_platform_role(role_id)
    if error:
        return _roles_redirect(None, error=error)
    return _roles_redirect(new_id, "display")


@web_bp.post("/platform/roles/<role_id>/delete")
def delete_role_action(role_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    error = delete_platform_role(role_id)
    return _roles_redirect(None, error=error)


@web_bp.post("/platform/roles/<role_id>/permission")
def toggle_permission_action(role_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    key = request.form.get("key", "")
    enabled = request.form.get("enabled") == "1"
    tab = request.form.get("tab", "permissions")
    error = toggle_role_permission(role_id, key, enabled)
    return _roles_redirect(role_id, tab, error)


@web_bp.get("/platform/roles/<role_id>/members/search")
def search_role_members_action(role_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return jsonify({"ok": False, "items": []}), 401
    query = request.args.get("q", "")
    members = search_platform_role_member_candidates(role_id, query=query, limit=10)
    return jsonify(
        {
            "ok": True,
            "items": [
                {
                    "id": member["id"],
                    "name": member["name"],
                    "email": member["email"],
                    "status": member["status"],
                    "platform_role": member["platform_role"],
                }
                for member in members
            ],
        }
    )


@web_bp.post("/platform/roles/<role_id>/members/add")
def add_role_member_action(role_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    user_id = request.form.get("user_id", "")
    error = add_role_member(role_id, user_id, granted_by=cs.user.id)
    return _roles_redirect(role_id, "members", error)


@web_bp.post("/platform/roles/<role_id>/members/<user_id>/remove")
def remove_role_member_action(role_id: str, user_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    remove_role_member(role_id, user_id)
    return _roles_redirect(role_id, "members")


# ── Staff ─────────────────────────────────────────────────────────────────────


@web_bp.post("/platform/staff/save")
def save_staff_action():
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    member_id = request.form.get("member_id") or None
    role_ids = [k[len("role_") :] for k in request.form if k.startswith("role_")]
    saved_id, error = save_staff_member(
        member_id=member_id,
        name=request.form.get("name", ""),
        email=request.form.get("email", ""),
        phone=request.form.get("phone") or None,
        role_ids=role_ids,
        granted_by=cs.user.id,
    )
    if error:
        target = member_id or "new"
        return redirect(
            f"/platform/staff?member={target}&error={quote(error)}", code=303
        )
    return redirect(f"/platform/staff?member={saved_id}", code=303)
