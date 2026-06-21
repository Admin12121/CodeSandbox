from __future__ import annotations

import urllib.parse

from flask import redirect, request

from codesandbox.shared.session import require_platform_role, require_session
from codesandbox.shared.storage import upload_image_from_filestorage
from codesandbox.web.blueprint import web_bp

_ADMIN_ROLES = ("system_admin", "system_staff")


def _save_logo(file_storage) -> str | None:
    return upload_image_from_filestorage(file_storage, prefix="orgs")

from .service import (
    accept_org_invitation,
    assign_role_to_org_member,
    create_org_custom_role,
    create_organization,
    create_user_organization,
    delete_org_custom_role,
    delete_user_organization,
    invite_to_org,
    leave_org,
    remove_org_member,
    remove_role_from_org_member,
    toggle_org_role_permission,
    update_organization_details,
    update_organization_status,
)


# ── Platform admin actions ────────────────────────────────────────────────────


@web_bp.post("/platform/organizations/create")
def create_org_action():
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    if not name:
        return redirect("/platform/organizations?org=new", code=303)
    org = create_organization(name=name, description=description)
    return redirect(f"/platform/organizations?org={org.id}", code=303)


@web_bp.post("/platform/organizations/<org_id>/update")
def update_org_action(org_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(f"/platform/organizations?org={org_id}&error={urllib.parse.quote('Name is required.')}", code=303)
    update_organization_details(
        org_id,
        name=name,
        description=request.form.get("description", "").strip() or None,
        website=request.form.get("website", "").strip() or None,
        industry=request.form.get("industry", "").strip() or None,
        size=request.form.get("size", "").strip() or None,
        location=request.form.get("location", "").strip() or None,
        contact_email=request.form.get("contact_email", "").strip() or None,
    )
    return redirect(f"/platform/organizations?org={org_id}", code=303)


@web_bp.post("/platform/organizations/<org_id>/update-status")
def update_org_status_action(org_id: str):
    cs, redir = require_platform_role(*_ADMIN_ROLES)
    if redir:
        return redirect(redir.url, code=303)
    status = request.form.get("status", "")
    update_organization_status(org_id, status)
    return redirect(f"/platform/organizations?org={org_id}", code=303)


# ── User-facing actions ───────────────────────────────────────────────────────


@web_bp.post("/my/organizations/create")
def user_create_org_action():
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(f"/my/organizations?mode=create&error={urllib.parse.quote('Organization name is required.')}", code=303)
    logo_url = _save_logo(request.files.get("logo"))
    org = create_user_organization(
        name=name,
        description=request.form.get("description", "").strip() or None,
        website=request.form.get("website", "").strip() or None,
        industry=request.form.get("industry", "").strip() or None,
        size=request.form.get("size", "").strip() or None,
        location=request.form.get("location", "").strip() or None,
        contact_email=request.form.get("contact_email", "").strip() or None,
        logo_url=logo_url,
        created_by=session.user.id,
    )
    info = urllib.parse.quote(f"Organization \"{org.name}\" created and is pending admin approval.")
    return redirect(f"/my/organizations?info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/update-field")
def user_update_org_field_action(slug: str):
    from flask import jsonify
    session, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401

    data = request.get_json(silent=True) or {}
    field = str(data.get("field", "")).strip()
    value = str(data.get("value", "")).strip() or None

    _allowed = {"name", "description", "website", "industry", "size", "location", "contact_email"}
    if field not in _allowed:
        return jsonify({"ok": False, "error": "Invalid field."}), 400
    if field == "name" and not value:
        return jsonify({"ok": False, "error": "Name is required."}), 400

    from .service import get_org_for_user
    org_data = get_org_for_user(slug, session.user.id)
    if org_data is None:
        return jsonify({"ok": False, "error": "Organization not found."}), 404
    if not org_data["is_owner"]:
        return jsonify({"ok": False, "error": "Only owners can update organization details."}), 403

    kwargs = {
        "name": org_data["name"],
        "description": org_data["description"] or None,
        "website": org_data["website"] or None,
        "industry": org_data["industry"] or None,
        "size": org_data["size"] or None,
        "location": org_data["location"] or None,
        "contact_email": org_data["contact_email"] or None,
    }
    kwargs[field] = value
    update_organization_details(org_data["id"], **kwargs)

    new_slug = slug
    if field == "name":
        from .repository import get_organization
        updated = get_organization(org_data["id"])
        new_slug = updated.slug if updated else slug

    return jsonify({"ok": True, "slug": new_slug})


@web_bp.post("/my/organizations/<slug>/upload-logo")
def user_upload_org_logo_action(slug: str):
    from flask import jsonify
    from .service import get_org_for_user
    from .repository import update_organization
    session, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    org_data = get_org_for_user(slug, session.user.id)
    if org_data is None:
        return jsonify({"ok": False, "error": "Organization not found."}), 404
    if not org_data["is_owner"]:
        return jsonify({"ok": False, "error": "Only owners can upload a logo."}), 403
    logo_url = _save_logo(request.files.get("logo"))
    if logo_url is None:
        return jsonify({"ok": False, "error": "Invalid file. Use PNG, JPG, or WebP under 2 MB."}), 400
    update_organization(org_data["id"], logo_url=logo_url)
    return jsonify({"ok": True, "logo_url": logo_url})


@web_bp.post("/my/organizations/<slug>/update")
def user_update_org_action(slug: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, session.user.id)
    if org_data is None:
        return redirect("/my/organizations", code=303)
    if not org_data["is_owner"]:
        err = urllib.parse.quote("Only owners can update organization details.")
        return redirect(f"/my/organizations/{slug}?error={err}", code=303)
    name = request.form.get("name", "").strip()
    if not name:
        err = urllib.parse.quote("Name is required.")
        return redirect(f"/my/organizations/{slug}/settings?error={err}", code=303)
    update_organization_details(
        org_data["id"],
        name=name,
        description=request.form.get("description", "").strip() or None,
        website=request.form.get("website", "").strip() or None,
        industry=request.form.get("industry", "").strip() or None,
        size=request.form.get("size", "").strip() or None,
        location=request.form.get("location", "").strip() or None,
        contact_email=request.form.get("contact_email", "").strip() or None,
    )
    info = urllib.parse.quote("Organization updated.")
    from .repository import get_organization
    updated_org = get_organization(org_data["id"])
    new_slug = updated_org.slug if updated_org else slug
    return redirect(f"/my/organizations/{new_slug}/settings?info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/invite")
def user_invite_org_action(slug: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, session.user.id)
    if org_data is None:
        return redirect("/my/organizations", code=303)
    if not org_data["is_owner"]:
        err = urllib.parse.quote("Only owners can invite members.")
        return redirect(f"/my/organizations/{slug}/members?error={err}", code=303)
    email = request.form.get("email", "").strip()
    if not email:
        err = urllib.parse.quote("Email address is required.")
        return redirect(f"/my/organizations/{slug}/members?error={err}", code=303)

    invitation, raw_token = invite_to_org(org_id=org_data["id"], email=email, invited_by=session.user.id)

    from codesandbox.config import get_settings
    settings = get_settings()
    invite_url = f"{settings.app_url}/my/organizations/join/{raw_token}"

    # Try to send email; fall back to showing the link
    try:
        from codesandbox.shared.email import send_org_invitation
        sent = send_org_invitation(
            to=email,
            org_name=org_data["name"],
            invite_url=invite_url,
            invited_by_name=session.user.name,
        )
    except Exception:
        sent = False

    if sent:
        info = urllib.parse.quote(f"Invitation sent to {email}.")
        return redirect(f"/my/organizations/{slug}/members?info={info}", code=303)
    else:
        encoded_link = urllib.parse.quote(invite_url)
        info = urllib.parse.quote(f"Email delivery unavailable. Share this invite link manually.")
        return redirect(f"/my/organizations/{slug}/members?info={info}&invite_link={encoded_link}", code=303)


@web_bp.get("/my/organizations/join/<token>")
def join_org_action(token: str):
    session, redir = require_session(next_path=f"/my/organizations/join/{token}")
    if redir:
        return redirect(redir.url, code=303)
    ok, result = accept_org_invitation(token, session.user.id)
    if not ok:
        err = urllib.parse.quote(result)
        return redirect(f"/my/organizations?error={err}", code=303)
    # result is org_id; find slug
    from .repository import get_organization
    org = get_organization(result)
    if org:
        info = urllib.parse.quote(f"You have joined {org.name}.")
        return redirect(f"/my/organizations/{org.slug}?info={info}", code=303)
    return redirect("/my/organizations", code=303)


@web_bp.post("/my/organizations/<slug>/remove-member/<member_id>")
def user_remove_member_action(slug: str, member_id: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, session.user.id)
    if org_data is None:
        return redirect("/my/organizations", code=303)
    ok, msg = remove_org_member(
        org_id=org_data["id"],
        member_id=member_id,
        requesting_user_id=session.user.id,
    )
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/members?error={err}", code=303)
    info = urllib.parse.quote("Member removed.")
    return redirect(f"/my/organizations/{slug}/members?info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/delete")
def user_delete_org_action(slug: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    ok, result = delete_user_organization(slug, session.user.id)
    if not ok:
        err = urllib.parse.quote(result)
        return redirect(f"/my/organizations/{slug}/settings?error={err}", code=303)
    info = urllib.parse.quote(f'Organization "{result}" has been permanently deleted.')
    return redirect(f"/my/organizations?info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/roles/create")
def user_create_org_role_action(slug: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    name = request.form.get("name", "").strip()
    color = request.form.get("color", "#6366f1").strip() or "#6366f1"
    description = request.form.get("description", "").strip() or None
    ok, msg = create_org_custom_role(slug, name, color, description, session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/roles?error={err}", code=303)
    info = urllib.parse.quote(f'Role "{name}" created.')
    return redirect(f"/my/organizations/{slug}/roles?info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/update")
def user_update_org_role_action(slug: str, role_id: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    name = request.form.get("name", "").strip()
    color = request.form.get("color", "#6366f1").strip() or "#6366f1"
    description = request.form.get("description", "").strip() or None
    from .service import update_org_custom_role
    ok, msg = update_org_custom_role(slug, role_id, name, color, description, session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/roles?role={role_id}&error={err}", code=303)
    info = urllib.parse.quote("Role updated.")
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/delete")
def user_delete_org_role_action(slug: str, role_id: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    ok, msg = delete_org_custom_role(slug, role_id, session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/roles?error={err}", code=303)
    info = urllib.parse.quote("Role deleted.")
    return redirect(f"/my/organizations/{slug}/roles?info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/members/<member_id>/assign-role")
def user_assign_member_role_action(slug: str, member_id: str):
    from flask import jsonify
    session, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    role_id = str(data.get("role_id", "")).strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id required"}), 400
    ok, msg = assign_role_to_org_member(slug, member_id, role_id, session.user.id)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, session.user.id)
    member_info = next((m for m in (org_data["members"] if org_data else []) if m["id"] == member_id), None)
    return jsonify({
        "ok": True,
        "roles": [{"id": rid, "name": n, "color": c}
                  for rid, n, c in zip(
                      member_info["role_ids"] if member_info else [],
                      member_info["roles"] if member_info else [],
                      member_info["role_colors"] if member_info else [],
                  )] if member_info else [],
    })


@web_bp.post("/my/organizations/<slug>/members/<member_id>/remove-role")
def user_remove_member_role_action(slug: str, member_id: str):
    from flask import jsonify
    session, redir = require_session()
    if redir:
        return jsonify({"ok": False, "error": "Not authenticated"}), 401
    data = request.get_json(silent=True) or {}
    role_id = str(data.get("role_id", "")).strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id required"}), 400
    ok, msg = remove_role_from_org_member(slug, member_id, role_id, session.user.id)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, session.user.id)
    member_info = next((m for m in (org_data["members"] if org_data else []) if m["id"] == member_id), None)
    return jsonify({
        "ok": True,
        "roles": [{"id": rid, "name": n, "color": c}
                  for rid, n, c in zip(
                      member_info["role_ids"] if member_info else [],
                      member_info["roles"] if member_info else [],
                      member_info["role_colors"] if member_info else [],
                  )] if member_info else [],
    })


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/permission")
def user_org_role_permission_action(slug: str, role_id: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    key = request.form.get("key", "").strip()
    enabled = request.form.get("enabled") == "1"
    ok, msg = toggle_org_role_permission(slug, role_id, key, enabled, session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=permissions&error={err}", code=303)
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=permissions", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/members/add")
def user_org_role_add_member_action(slug: str, role_id: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    member_id = request.form.get("member_id", "").strip()
    if not member_id:
        err = urllib.parse.quote("Please select a member.")
        return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members&error={err}", code=303)
    ok, msg = assign_role_to_org_member(slug, member_id, role_id, session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members&error={err}", code=303)
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/members/<member_id>/remove")
def user_org_role_remove_member_action(slug: str, role_id: str, member_id: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    ok, msg = remove_role_from_org_member(slug, member_id, role_id, session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members&error={err}", code=303)
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members", code=303)


@web_bp.get("/my/workspace/personal")
def switch_to_personal_workspace():
    from flask import session as flask_session
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    flask_session.pop("active_workspace_slug", None)
    return redirect("/dashboard", code=303)


@web_bp.post("/my/organizations/<slug>/leave")
def user_leave_org_action(slug: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    from .repository import get_organization_by_slug
    org = get_organization_by_slug(slug)
    if org is None:
        return redirect("/my/organizations", code=303)
    ok, msg = leave_org(org_id=org.id, user_id=session.user.id)
    if not ok:
        err = urllib.parse.quote(msg)
        return redirect(f"/my/organizations/{slug}/settings?error={err}", code=303)
    info = urllib.parse.quote(f"You have left {org.name}.")
    return redirect(f"/my/organizations?info={info}", code=303)
