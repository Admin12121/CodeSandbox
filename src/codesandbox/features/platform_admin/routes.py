from __future__ import annotations

import urllib.parse
from urllib.parse import quote

from flask import jsonify, redirect, request

from codesandbox.shared.guards import platform_perm
from codesandbox.shared.session import get_current_session
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


def _guard_user_mutation(cs, user_id: str, new_platform_role: str | None = None) -> str | None:
    """Business-rule guard: blocks staff from modifying admins or self-promoting."""
    from codesandbox.features.identity import repository as id_repo
    if cs.user.platform_role == "system_admin":
        return None
    target = id_repo.find_user_by_id(user_id)
    if target and target.platform_role == "system_admin":
        return "Staff cannot modify system admin accounts."
    if new_platform_role == "system_admin":
        return "Staff cannot promote users to system admin."
    return None


# ── Users ─────────────────────────────────────────────────────────────────────


@web_bp.post("/platform/users/<user_id>/update")
@platform_perm("platform.users.edit")
def update_user_action(user_id: str):
    from codesandbox.shared.permissions import has_platform_permission
    cs = get_current_session()
    platform_role = request.form.get("platform_role") or None
    status = request.form.get("status") or None
    name = request.form.get("name", "").strip() or None
    phone = request.form.get("phone", "").strip() or None

    # Field-level permission checks for elevated fields
    if platform_role and not has_platform_permission(cs.user, "platform.users.roles"):
        return redirect(f"/platform/users/{user_id}?error={urllib.parse.quote('Permission denied.')}", code=303)
    if status and not has_platform_permission(cs.user, "platform.users.status"):
        return redirect(f"/platform/users/{user_id}?error={urllib.parse.quote('Permission denied.')}", code=303)

    err = _guard_user_mutation(cs, user_id, new_platform_role=platform_role)
    if err:
        return redirect(f"/platform/users?error={urllib.parse.quote(err)}", code=303)

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
@platform_perm("platform.users.edit")
def platform_update_user_field_action(user_id: str):
    from codesandbox.features.identity import repository as id_repo
    from codesandbox.shared.permissions import has_platform_permission
    cs = get_current_session()
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

    # Field-level permission checks for elevated fields
    if field == "platform_role" and not has_platform_permission(cs.user, "platform.users.roles"):
        return jsonify({"ok": False, "error": "Permission denied."}), 403
    if field == "status" and not has_platform_permission(cs.user, "platform.users.status"):
        return jsonify({"ok": False, "error": "Permission denied."}), 403

    err = _guard_user_mutation(cs, user_id, new_platform_role=value if field == "platform_role" else None)
    if err:
        return jsonify({"ok": False, "error": err}), 403

    if field in ("platform_role", "status"):
        update_platform_user(user_id, **{field: value})
    else:
        id_repo.update_user(user_id, **{field: value})
    return jsonify({"ok": True})


@web_bp.post("/platform/users/<user_id>/upload-avatar")
@platform_perm("platform.users.edit")
def platform_upload_user_avatar_action(user_id: str):
    from codesandbox.features.identity import repository as id_repo
    from codesandbox.shared.storage import upload_image_from_filestorage
    avatar_url = upload_image_from_filestorage(request.files.get("logo"), prefix="avatars")
    if avatar_url is None:
        return jsonify({"ok": False, "error": "Invalid file. Use PNG, JPG, or WebP under 2 MB."}), 400
    id_repo.update_user(user_id, avatar_url=avatar_url)
    return jsonify({"ok": True, "url": avatar_url, "media_key": f"user:{user_id}"})


# ── Roles ─────────────────────────────────────────────────────────────────────


@web_bp.post("/platform/roles/create")
@platform_perm("platform.roles.manage")
def create_role_action():
    result, error = create_platform_role(
        name=request.form.get("name", ""),
        color=request.form.get("color", "#6366f1"),
        description=request.form.get("description") or None,
    )
    if error:
        return _roles_redirect("new", "display", error)
    return _roles_redirect(result["id"], "display")


@web_bp.post("/platform/roles/<role_id>/update")
@platform_perm("platform.roles.manage")
def update_role_action(role_id: str):
    error = update_platform_role(
        role_id,
        name=request.form.get("name"),
        color=request.form.get("color"),
        description=request.form.get("description", ""),
    )
    return _roles_redirect(role_id, "display", error)


@web_bp.post("/platform/roles/<role_id>/duplicate")
@platform_perm("platform.roles.manage")
def duplicate_role_action(role_id: str):
    new_id, error = duplicate_platform_role(role_id)
    if error:
        return _roles_redirect(None, error=error)
    return _roles_redirect(new_id, "display")


@web_bp.post("/platform/roles/<role_id>/delete")
@platform_perm("platform.roles.manage")
def delete_role_action(role_id: str):
    error = delete_platform_role(role_id)
    return _roles_redirect(None, error=error)


@web_bp.post("/platform/roles/<role_id>/permission")
@platform_perm("platform.roles.manage")
def toggle_permission_action(role_id: str):
    key = request.form.get("key", "")
    enabled = request.form.get("enabled") == "1"
    tab = request.form.get("tab", "permissions")
    error = toggle_role_permission(role_id, key, enabled)
    return _roles_redirect(role_id, tab, error)


@web_bp.get("/platform/roles/<role_id>/members/search")
@platform_perm("platform.roles.read")
def search_role_members_action(role_id: str):
    query = request.args.get("q", "")
    members = search_platform_role_member_candidates(role_id, query=query, limit=10)
    return jsonify({
        "ok": True,
        "items": [
            {
                "id": m["id"],
                "name": m["name"],
                "email": m["email"],
                "status": m["status"],
                "platform_role": m["platform_role"],
            }
            for m in members
        ],
    })


@web_bp.post("/platform/roles/<role_id>/members/add")
@platform_perm("platform.roles.manage")
def add_role_member_action(role_id: str):
    cs = get_current_session()
    user_id = request.form.get("user_id", "")
    error = add_role_member(role_id, user_id, granted_by=cs.user.id)
    return _roles_redirect(role_id, "members", error)


@web_bp.post("/platform/roles/<role_id>/members/<user_id>/remove")
@platform_perm("platform.roles.manage")
def remove_role_member_action(role_id: str, user_id: str):
    remove_role_member(role_id, user_id)
    return _roles_redirect(role_id, "members")


# ── Staff ─────────────────────────────────────────────────────────────────────


@web_bp.post("/platform/staff/save")
@platform_perm("platform.staff.manage")
def save_staff_action():
    cs = get_current_session()
    member_id = request.form.get("member_id") or None
    role_ids = [k[len("role_"):] for k in request.form if k.startswith("role_")]
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
        return redirect(f"/platform/staff?member={target}&error={quote(error)}", code=303)
    return redirect(f"/platform/staff?member={saved_id}", code=303)


# ── Platform org actions ──────────────────────────────────────────────────────
# (create was removed — platform does not create organizations)


@web_bp.post("/platform/organizations/<org_id>/update-status")
@platform_perm("platform.organizations.status")
def update_org_status_action(org_id: str):
    from codesandbox.features.organizations.service import update_organization_status
    status = request.form.get("status", "")
    update_organization_status(org_id, status)
    return redirect(f"/platform/organizations?org={org_id}", code=303)
