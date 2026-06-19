from __future__ import annotations

from . import repository
from .models import Organization


def create_organization(
    *,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> Organization:
    return repository.create_organization(
        name=name,
        description=description,
        created_by=created_by,
    )


def get_platform_organizations(
    *,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    orgs, total = repository.list_organizations(
        search=search,
        status=status,
        page=page,
        page_size=page_size,
    )
    result = []
    for org in orgs:
        member_count = repository.get_member_count(org.id)
        result.append({
            "id": org.id,
            "name": org.name,
            "slug": org.slug,
            "description": org.description or "",
            "status": org.status,
            "member_count": member_count,
            "created_at": org.created_at,
        })
    return result, total


def update_organization_details(
    org_id: str,
    *,
    name: str,
    description: str | None = None,
) -> Organization | None:
    return repository.update_organization(org_id, name=name, description=description)


def update_organization_status(org_id: str, status: str) -> Organization | None:
    valid = {"active", "inactive", "suspended"}
    if status not in valid:
        return None
    return repository.update_organization(org_id, status=status)
