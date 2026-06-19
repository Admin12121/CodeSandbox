from __future__ import annotations

from flask import redirect, request

from codesandbox.web.blueprint import web_bp

from .service import create_organization, update_organization_details, update_organization_status


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
    description = request.form.get("description", "").strip() or None
    if not name:
        import urllib.parse
        return redirect(f"/platform/organizations?org={org_id}&error={urllib.parse.quote('Name is required.')}", code=303)
    update_organization_details(org_id, name=name, description=description)
    return redirect(f"/platform/organizations?org={org_id}", code=303)


@web_bp.post("/platform/organizations/<org_id>/update-status")
def update_org_status_action(org_id: str):
    status = request.form.get("status", "")
    update_organization_status(org_id, status)
    return redirect(f"/platform/organizations?org={org_id}", code=303)
