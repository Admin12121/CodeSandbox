from __future__ import annotations

import urllib.parse

from flask import jsonify, redirect, request

from codesandbox.shared.guards import (
    authenticated,
    no_staff,
    org_member,
    org_owner,
    org_perm,
    platform_perm,
)
from codesandbox.shared.session import get_current_session
from codesandbox.shared.storage import upload_image_from_filestorage
from codesandbox.web.blueprint import web_bp

from .service import (
    accept_org_invitation,
    assign_role_to_org_member,
    batch_invite_to_org,
    create_org_custom_role,
    create_user_organization,
    delete_org_custom_role,
    delete_user_organization,
    get_org_invite_link_data,
    invite_to_org,
    join_by_invite_code,
    leave_org,
    regenerate_org_invite_link,
    remove_org_member,
    remove_role_from_org_member,
    toggle_org_role_permission,
    transfer_org_ownership,
    update_organization_details,
)


def _save_logo(file_storage) -> str | None:
    return upload_image_from_filestorage(file_storage, prefix="orgs")


def _has_upload(file_storage) -> bool:
    return bool(file_storage and getattr(file_storage, "filename", ""))


# ── Platform admin: org edit routes ──────────────────────────────────────────
# update-status is in platform_admin/routes.py; these cover edit-level actions.


@web_bp.post("/platform/organizations/<org_id>/update")
@platform_perm("platform.organizations.edit")
def update_org_action(org_id: str):
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(
            f"/platform/organizations?org={org_id}&error={urllib.parse.quote('Name is required.')}",
            code=303,
        )
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


@web_bp.post("/platform/organizations/<org_id>/update-field")
@platform_perm("platform.organizations.edit")
def platform_update_org_field_action(org_id: str):
    data = request.get_json(silent=True) or {}
    field = str(data.get("field", "")).strip()
    value = str(data.get("value", "")).strip() or None
    _allowed = {"name", "description", "website", "industry", "size", "location", "contact_email"}
    if field not in _allowed:
        return jsonify({"ok": False, "error": "Invalid field."}), 400
    if field == "name" and not value:
        return jsonify({"ok": False, "error": "Name is required."}), 400
    from .repository import get_organization
    org = get_organization(org_id)
    if org is None:
        return jsonify({"ok": False, "error": "Organization not found."}), 404
    kwargs = {
        "name": org.name,
        "description": org.description or None,
        "website": org.website or None,
        "industry": org.industry or None,
        "size": org.size or None,
        "location": org.location or None,
        "contact_email": org.contact_email or None,
    }
    kwargs[field] = value
    update_organization_details(org_id, **kwargs)
    return jsonify({"ok": True})


@web_bp.post("/platform/organizations/<org_id>/upload-logo")
@platform_perm("platform.organizations.edit")
def platform_upload_org_logo_action(org_id: str):
    from .repository import get_organization, update_organization
    org = get_organization(org_id)
    if org is None:
        return jsonify({"ok": False, "error": "Organization not found."}), 404
    logo_url = _save_logo(request.files.get("logo"))
    if logo_url is None:
        return jsonify({"ok": False, "error": "Invalid file. Use PNG, JPG, or WebP under 2 MB."}), 400
    update_organization(org_id, logo_url=logo_url)
    return jsonify({"ok": True, "logo_url": logo_url, "url": logo_url, "media_key": f"org:{org_id}", "entity_id": org_id})


# ── User-facing: create & join ────────────────────────────────────────────────
# @no_staff blocks platform staff from creating or joining orgs.


@web_bp.post("/my/organizations/create")
@no_staff
def user_create_org_action():
    cs = get_current_session()
    from codesandbox.features.organizations.service import get_user_org_list
    existing = get_user_org_list(cs.user.id)
    if any(o.get("created_by") == cs.user.id for o in existing):
        return redirect(
            f"/my/organizations?error={urllib.parse.quote('You have already created an organization.')}",
            code=303,
        )
    name = request.form.get("name", "").strip()
    if not name:
        return redirect(
            f"/my/organizations?mode=create&error={urllib.parse.quote('Organization name is required.')}",
            code=303,
        )
    logo_file = request.files.get("logo")
    logo_url = _save_logo(logo_file) if _has_upload(logo_file) else None
    if _has_upload(logo_file) and logo_url is None:
        return redirect(
            f"/my/organizations?mode=create&error={urllib.parse.quote('Invalid logo. Use PNG, JPG, or WebP under 2 MB.')}",
            code=303,
        )
    org = create_user_organization(
        name=name,
        description=request.form.get("description", "").strip() or None,
        website=request.form.get("website", "").strip() or None,
        industry=request.form.get("industry", "").strip() or None,
        size=request.form.get("size", "").strip() or None,
        location=request.form.get("location", "").strip() or None,
        contact_email=request.form.get("contact_email", "").strip() or None,
        logo_url=logo_url,
        created_by=cs.user.id,
    )
    info = urllib.parse.quote(f'Organization "{org.name}" created and is pending admin approval.')
    return redirect(f"/my/organizations?info={info}", code=303)


@web_bp.get("/my/organizations/join/<token>")
@no_staff
def join_org_action(token: str):
    cs = get_current_session()
    ok, result = accept_org_invitation(token, cs.user.id)
    if not ok:
        return redirect(f"/my/organizations?error={urllib.parse.quote(result)}", code=303)
    from .repository import get_organization
    org = get_organization(result)
    if org:
        info = urllib.parse.quote(f"You have joined {org.name}.")
        return redirect(f"/my/organizations/{org.slug}?info={info}", code=303)
    return redirect("/my/organizations", code=303)


@web_bp.get("/my/organizations/join/code/<code>")
@no_staff
def join_org_by_code(code: str):
    cs = get_current_session()
    ok, result = join_by_invite_code(code, cs.user.id)
    if not ok:
        return redirect(f"/my/organizations?error={urllib.parse.quote(result)}", code=303)
    from .repository import get_organization
    org = get_organization(result)
    if org:
        return redirect(
            f"/my/organizations/{org.slug}?info={urllib.parse.quote('You have joined ' + org.name + '.')}",
            code=303,
        )
    return redirect("/my/organizations", code=303)


# ── User-facing: org settings / details ──────────────────────────────────────


@web_bp.post("/my/organizations/<slug>/update-field")
@org_owner
def user_update_org_field_action(slug: str):
    cs = get_current_session()
    data = request.get_json(silent=True) or {}
    field = str(data.get("field", "")).strip()
    value = str(data.get("value", "")).strip() or None
    _allowed = {"name", "description", "website", "industry", "size", "location", "contact_email"}
    if field not in _allowed:
        return jsonify({"ok": False, "error": "Invalid field."}), 400
    if field == "name" and not value:
        return jsonify({"ok": False, "error": "Name is required."}), 400
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return jsonify({"ok": False, "error": "Organization not found."}), 404
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
@org_owner
def user_upload_org_logo_action(slug: str):
    cs = get_current_session()
    from .service import get_org_for_user
    from .repository import update_organization
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return jsonify({"ok": False, "error": "Organization not found."}), 404
    logo_url = _save_logo(request.files.get("logo"))
    if logo_url is None:
        return jsonify({"ok": False, "error": "Invalid file. Use PNG, JPG, or WebP under 2 MB."}), 400
    update_organization(org_data["id"], logo_url=logo_url)
    return jsonify({"ok": True, "logo_url": logo_url, "url": logo_url, "media_key": f"org:{org_data['id']}", "entity_id": org_data["id"]})


@web_bp.post("/my/organizations/<slug>/update")
@org_owner
def user_update_org_action(slug: str):
    cs = get_current_session()
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return redirect("/my/organizations", code=303)
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


# ── User-facing: invites ──────────────────────────────────────────────────────


@web_bp.post("/my/organizations/<slug>/invite")
@org_perm("org.members.invite")
def user_invite_org_action(slug: str):
    cs = get_current_session()
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return redirect("/my/organizations", code=303)
    email = request.form.get("email", "").strip()
    if not email:
        err = urllib.parse.quote("Email address is required.")
        return redirect(f"/my/organizations/{slug}/members?error={err}", code=303)
    invitation, raw_token = invite_to_org(org_id=org_data["id"], email=email, invited_by=cs.user.id)
    from codesandbox.config import get_settings
    settings = get_settings()
    invite_url = f"{settings.app_url}/my/organizations/join/{raw_token}"
    try:
        from codesandbox.shared.email import send_org_invitation
        sent = send_org_invitation(
            to=email,
            org_name=org_data["name"],
            invite_url=invite_url,
            invited_by_name=cs.user.name,
        )
    except Exception:
        sent = False
    if sent:
        info = urllib.parse.quote(f"Invitation sent to {email}.")
        return redirect(f"/my/organizations/{slug}/members?info={info}", code=303)
    encoded_link = urllib.parse.quote(invite_url)
    info = urllib.parse.quote("Email delivery unavailable. Share this invite link manually.")
    return redirect(f"/my/organizations/{slug}/members?info={info}&invite_link={encoded_link}", code=303)


@web_bp.get("/my/organizations/<slug>/invite-link-data")
@org_perm("org.members.invite")
def user_org_invite_link_data(slug: str):
    cs = get_current_session()
    data = get_org_invite_link_data(slug, cs.user.id)
    if data is None:
        return jsonify({"ok": False}), 403
    return jsonify({"ok": True, **data})


@web_bp.post("/my/organizations/<slug>/invite-link/regenerate")
@org_perm("org.members.invite")
def user_org_invite_link_regenerate(slug: str):
    cs = get_current_session()
    data = regenerate_org_invite_link(slug, cs.user.id)
    if data is None:
        return jsonify({"ok": False, "error": "Unauthorized or not found"}), 403
    return jsonify({"ok": True, **data})


@web_bp.post("/my/organizations/<slug>/invite-batch")
@org_perm("org.members.invite")
def user_org_invite_batch(slug: str):
    cs = get_current_session()
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return jsonify({"ok": False, "error": "Not authorized"}), 403
    body = request.get_json(silent=True) or {}
    emails = body.get("emails", [])
    if not isinstance(emails, list):
        return jsonify({"ok": False, "error": "emails must be a list"}), 400
    results = batch_invite_to_org(slug, emails[:5], cs.user.id, cs.user.name)
    return jsonify({"ok": True, "results": results})


@web_bp.get("/my/organizations/<slug>/invite-qr")
@org_perm("org.members.invite")
def user_org_invite_qr(slug: str):
    from flask import Response
    cs = get_current_session()
    data = get_org_invite_link_data(slug, cs.user.id)
    if data is None:
        return Response("", status=403)
    try:
        import io
        import qrcode
        import qrcode.image.svg
        qr = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6, border=2,
        )
        qr.add_data(data["url"])
        qr.make(fit=True)
        img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
        buf = io.BytesIO()
        img.save(buf)
        return Response(buf.getvalue(), mimetype="image/svg+xml",
                        headers={"Cache-Control": "no-store"})
    except ImportError:
        return Response("QR library not installed", status=501)


# ── User-facing: member management ───────────────────────────────────────────


@web_bp.post("/my/organizations/<slug>/remove-member/<member_id>")
@org_perm("org.members.remove")
def user_remove_member_action(slug: str, member_id: str):
    cs = get_current_session()
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return redirect("/my/organizations", code=303)
    ok, msg = remove_org_member(
        org_id=org_data["id"],
        member_id=member_id,
        requesting_user_id=cs.user.id,
    )
    if not ok:
        return redirect(f"/my/organizations/{slug}/members?error={urllib.parse.quote(msg)}", code=303)
    return redirect(f"/my/organizations/{slug}/members?info={urllib.parse.quote('Member removed.')}", code=303)


@web_bp.post("/my/organizations/<slug>/leave")
@org_member
def user_leave_org_action(slug: str):
    cs = get_current_session()
    from .repository import get_organization_by_slug
    org = get_organization_by_slug(slug)
    if org is None:
        return redirect("/my/organizations", code=303)
    ok, msg = leave_org(org_id=org.id, user_id=cs.user.id)
    if not ok:
        return redirect(f"/my/organizations/{slug}/settings?error={urllib.parse.quote(msg)}", code=303)
    return redirect(f"/my/organizations?info={urllib.parse.quote('You have left ' + org.name + '.')}", code=303)


# ── User-facing: role management ─────────────────────────────────────────────


@web_bp.post("/my/organizations/<slug>/roles/create")
@org_perm("org.roles.manage")
def user_create_org_role_action(slug: str):
    cs = get_current_session()
    name = request.form.get("name", "").strip()
    color = request.form.get("color", "#6366f1").strip() or "#6366f1"
    description = request.form.get("description", "").strip() or None
    ok, msg = create_org_custom_role(slug, name, color, description, cs.user.id)
    if not ok:
        return redirect(f"/my/organizations/{slug}/roles?error={urllib.parse.quote(msg)}", code=303)
    return redirect(f"/my/organizations/{slug}/roles?info={urllib.parse.quote('Role \"' + name + '\" created.')}", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/update")
@org_perm("org.roles.manage")
def user_update_org_role_action(slug: str, role_id: str):
    cs = get_current_session()
    name = request.form.get("name", "").strip()
    color = request.form.get("color", "#6366f1").strip() or "#6366f1"
    description = request.form.get("description", "").strip() or None
    from .service import update_org_custom_role
    ok, msg = update_org_custom_role(slug, role_id, name, color, description, cs.user.id)
    if not ok:
        return redirect(f"/my/organizations/{slug}/roles?role={role_id}&error={urllib.parse.quote(msg)}", code=303)
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&info={urllib.parse.quote('Role updated.')}", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/delete")
@org_perm("org.roles.manage")
def user_delete_org_role_action(slug: str, role_id: str):
    cs = get_current_session()
    ok, msg = delete_org_custom_role(slug, role_id, cs.user.id)
    if not ok:
        return redirect(f"/my/organizations/{slug}/roles?error={urllib.parse.quote(msg)}", code=303)
    return redirect(f"/my/organizations/{slug}/roles?info={urllib.parse.quote('Role deleted.')}", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/permission")
@org_perm("org.roles.manage")
def user_org_role_permission_action(slug: str, role_id: str):
    cs = get_current_session()
    key = request.form.get("key", "").strip()
    enabled = request.form.get("enabled") == "1"
    ok, msg = toggle_org_role_permission(slug, role_id, key, enabled, cs.user.id)
    if not ok:
        return redirect(
            f"/my/organizations/{slug}/roles?role={role_id}&tab=permissions&error={urllib.parse.quote(msg)}",
            code=303,
        )
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=permissions", code=303)


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/members/add")
@org_perm("org.roles.assign")
def user_org_role_add_member_action(slug: str, role_id: str):
    cs = get_current_session()
    member_id = request.form.get("member_id", "").strip()
    if not member_id:
        return redirect(
            f"/my/organizations/{slug}/roles?role={role_id}&tab=members&error={urllib.parse.quote('Please select a member.')}",
            code=303,
        )
    ok, msg = assign_role_to_org_member(slug, member_id, role_id, cs.user.id)
    if not ok:
        return redirect(
            f"/my/organizations/{slug}/roles?role={role_id}&tab=members&error={urllib.parse.quote(msg)}",
            code=303,
        )
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members", code=303)


@web_bp.get("/my/organizations/<slug>/roles/<role_id>/members/search")
@org_perm("org.roles.assign")
def user_org_role_member_search(slug: str, role_id: str):
    cs = get_current_session()
    q = request.args.get("q", "").strip().lower()
    from .service import get_org_for_user, get_role_members_for_org
    org_data = get_org_for_user(slug, cs.user.id)
    if org_data is None:
        return jsonify({"items": []}), 403
    role_members = get_role_members_for_org(org_data["id"], role_id)
    in_role_ids = {m["id"] for m in role_members}
    results = [
        {"id": m["id"], "name": m["name"], "email": m["email"]}
        for m in org_data["members"]
        if m["id"] not in in_role_ids
        and (not q or q in (m["name"] or "").lower() or q in (m["email"] or "").lower())
    ]
    return jsonify({"items": results[:20]})


@web_bp.post("/my/organizations/<slug>/roles/<role_id>/members/<member_id>/remove")
@org_perm("org.roles.assign")
def user_org_role_remove_member_action(slug: str, role_id: str, member_id: str):
    cs = get_current_session()
    ok, msg = remove_role_from_org_member(slug, member_id, role_id, cs.user.id)
    if not ok:
        return redirect(
            f"/my/organizations/{slug}/roles?role={role_id}&tab=members&error={urllib.parse.quote(msg)}",
            code=303,
        )
    return redirect(f"/my/organizations/{slug}/roles?role={role_id}&tab=members", code=303)


@web_bp.post("/my/organizations/<slug>/members/<member_id>/assign-role")
@org_perm("org.roles.assign")
def user_assign_member_role_action(slug: str, member_id: str):
    cs = get_current_session()
    data = request.get_json(silent=True) or {}
    role_id = str(data.get("role_id", "")).strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id required"}), 400
    ok, msg = assign_role_to_org_member(slug, member_id, role_id, cs.user.id)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
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
@org_perm("org.roles.assign")
def user_remove_member_role_action(slug: str, member_id: str):
    cs = get_current_session()
    data = request.get_json(silent=True) or {}
    role_id = str(data.get("role_id", "")).strip()
    if not role_id:
        return jsonify({"ok": False, "error": "role_id required"}), 400
    ok, msg = remove_role_from_org_member(slug, member_id, role_id, cs.user.id)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    from .service import get_org_for_user
    org_data = get_org_for_user(slug, cs.user.id)
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


# ── User-facing: delete / transfer ownership ─────────────────────────────────


@web_bp.post("/my/organizations/<slug>/delete")
@org_owner
def user_delete_org_action(slug: str):
    cs = get_current_session()
    ok, result = delete_user_organization(slug, cs.user.id)
    if not ok:
        return redirect(f"/my/organizations/{slug}/settings?error={urllib.parse.quote(result)}", code=303)
    return redirect(
        f"/my/organizations?info={urllib.parse.quote('Organization \"' + result + '\" has been permanently deleted.')}",
        code=303,
    )


@web_bp.post("/my/organizations/<slug>/transfer-ownership")
@org_owner
def user_transfer_org_ownership_action(slug: str):
    cs = get_current_session()
    data = request.get_json(silent=True) or {}
    new_owner_id = str(data.get("new_owner_id", "")).strip()
    if not new_owner_id:
        return jsonify({"ok": False, "error": "new_owner_id is required."}), 400
    ok, msg = transfer_org_ownership(slug, cs.user.id, new_owner_id)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


# ── Workspace switcher ────────────────────────────────────────────────────────


@web_bp.get("/my/workspace/personal")
@authenticated
def switch_to_personal_workspace():
    from flask import session as flask_session
    flask_session.pop("active_workspace_slug", None)
    return redirect("/dashboard", code=303)
