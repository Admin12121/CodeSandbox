from __future__ import annotations

import urllib.parse

from flask import redirect, request

from codesandbox.shared.session import require_session
from codesandbox.web.blueprint import web_bp

from .service import (
    accept_org_invitation,
    create_organization,
    create_user_organization,
    delete_user_organization,
    invite_to_org,
    leave_org,
    remove_org_member,
    update_organization_details,
    update_organization_status,
)


# ── Platform admin actions ────────────────────────────────────────────────────


@web_bp.post("/platform/organizations/create")
def create_org_action():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    if not name:
        return redirect("/platform/organizations?org=new", code=303)
    org = create_organization(name=name, description=description)
    return redirect(f"/platform/organizations?org={org.id}", code=303)


@web_bp.post("/platform/organizations/<org_id>/update")
def update_org_action(org_id: str):
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
    description = request.form.get("description", "").strip() or None
    if not name:
        return redirect(f"/my/organizations?mode=create&error={urllib.parse.quote('Organization name is required.')}", code=303)
    org = create_user_organization(
        name=name,
        description=request.form.get("description", "").strip() or None,
        website=request.form.get("website", "").strip() or None,
        industry=request.form.get("industry", "").strip() or None,
        size=request.form.get("size", "").strip() or None,
        location=request.form.get("location", "").strip() or None,
        contact_email=request.form.get("contact_email", "").strip() or None,
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
        return redirect(f"/my/organizations/{slug}?tab=settings&error={err}", code=303)
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
    # Slug may have changed; re-fetch to get updated slug
    from .repository import get_organization
    updated_org = get_organization(org_data["id"])
    new_slug = updated_org.slug if updated_org else slug
    return redirect(f"/my/organizations/{new_slug}?tab=settings&info={info}", code=303)


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
        return redirect(f"/my/organizations/{slug}?tab=members&error={err}", code=303)
    email = request.form.get("email", "").strip()
    if not email:
        err = urllib.parse.quote("Email address is required.")
        return redirect(f"/my/organizations/{slug}?tab=members&error={err}", code=303)

    invitation = invite_to_org(org_id=org_data["id"], email=email, invited_by=session.user.id)

    from codesandbox.config import get_settings
    settings = get_settings()
    invite_url = f"{settings.app_url}/my/organizations/join/{invitation.token}"

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
        return redirect(f"/my/organizations/{slug}?tab=members&info={info}", code=303)
    else:
        encoded_link = urllib.parse.quote(invite_url)
        info = urllib.parse.quote(f"Email delivery unavailable. Share this invite link manually.")
        return redirect(f"/my/organizations/{slug}?tab=members&info={info}&invite_link={encoded_link}", code=303)


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
        return redirect(f"/my/organizations/{slug}?tab=members&error={err}", code=303)
    info = urllib.parse.quote("Member removed.")
    return redirect(f"/my/organizations/{slug}?tab=members&info={info}", code=303)


@web_bp.post("/my/organizations/<slug>/delete")
def user_delete_org_action(slug: str):
    session, redir = require_session()
    if redir:
        return redirect(redir.url, code=303)
    ok, result = delete_user_organization(slug, session.user.id)
    if not ok:
        err = urllib.parse.quote(result)
        return redirect(f"/my/organizations/{slug}?tab=settings&error={err}", code=303)
    info = urllib.parse.quote(f'Organization "{result}" has been permanently deleted.')
    return redirect(f"/my/organizations?info={info}", code=303)


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
        return redirect(f"/my/organizations/{slug}?tab=settings&error={err}", code=303)
    info = urllib.parse.quote(f"You have left {org.name}.")
    return redirect(f"/my/organizations?info={info}", code=303)
