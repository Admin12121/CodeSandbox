from __future__ import annotations

from flask import redirect, request

from codesandbox.web.blueprint import web_bp

from .service import create_organization, update_organization_status


@web_bp.post("/platform/organizations/create")
def create_org_action():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip() or None
    if name:
        create_organization(name=name, description=description)
    return redirect("/platform/organizations", code=303)


@web_bp.post("/platform/organizations/<org_id>/update-status")
def update_org_status_action(org_id: str):
    status = request.form.get("status", "")
    update_organization_status(org_id, status)
    return redirect("/platform/organizations", code=303)
