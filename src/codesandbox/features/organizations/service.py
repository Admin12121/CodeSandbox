from __future__ import annotations

from datetime import datetime, timezone

from . import repository
from .models import Organization, OrganizationInvitation


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
            "website": org.website or "",
            "industry": org.industry or "",
            "size": org.size or "",
            "location": org.location or "",
            "contact_email": org.contact_email or "",
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
    website: str | None = None,
    industry: str | None = None,
    size: str | None = None,
    location: str | None = None,
    contact_email: str | None = None,
) -> Organization | None:
    return repository.update_organization(
        org_id,
        name=name,
        description=description,
        website=website,
        industry=industry,
        size=size,
        location=location,
        contact_email=contact_email,
    )


def update_organization_status(org_id: str, status: str) -> Organization | None:
    valid = {"active", "inactive", "suspended", "pending", "rejected"}
    if status not in valid:
        return None
    return repository.update_organization(org_id, status=status)


# ── User-facing service functions ─────────────────────────────────────────────


def get_user_org_list(user_id: str) -> list[dict]:
    """Return all orgs the user belongs to, enriched with member_count."""
    orgs = repository.get_user_organizations(user_id)
    result = []
    for org in orgs:
        member_count = repository.get_member_count(org.id)
        result.append({
            "id": org.id,
            "name": org.name,
            "slug": org.slug,
            "description": org.description or "",
            "website": org.website or "",
            "industry": org.industry or "",
            "size": org.size or "",
            "location": org.location or "",
            "contact_email": org.contact_email or "",
            "status": org.status,
            "member_count": member_count,
            "created_at": org.created_at,
            "created_by": org.created_by,
        })
    return result


def create_user_organization(
    *,
    name: str,
    description: str | None = None,
    website: str | None = None,
    industry: str | None = None,
    size: str | None = None,
    location: str | None = None,
    contact_email: str | None = None,
    created_by: str,
) -> Organization:
    """Create an org with pending status for the given user."""
    org = repository.create_organization(
        name=name,
        description=description,
        created_by=created_by,
    )
    repository.update_organization(
        org.id,
        status="pending",
        website=website,
        industry=industry,
        size=size,
        location=location,
        contact_email=contact_email,
    )
    return org


def get_org_for_user(slug: str, user_id: str) -> dict | None:
    """Return org detail dict if the user is a member, else None."""
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return None
    member = repository.get_member(org.id, user_id)
    if member is None:
        return None

    members = repository.get_members_with_info(org.id)

    # Determine if requesting user is owner
    is_owner = False
    for m in members:
        if m["user_id"] == user_id and "owner" in m["roles"]:
            is_owner = True
            break

    # Count owners
    owner_count = sum(1 for m in members if "owner" in m["roles"])

    return {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "description": org.description or "",
        "website": org.website or "",
        "industry": org.industry or "",
        "size": org.size or "",
        "location": org.location or "",
        "contact_email": org.contact_email or "",
        "status": org.status,
        "created_at": org.created_at,
        "members": members,
        "member_count": len(members),
        "is_owner": is_owner,
        "owner_count": owner_count,
    }


def invite_to_org(org_id: str, email: str, invited_by: str) -> OrganizationInvitation:
    return repository.create_invitation(
        org_id=org_id,
        email=email,
        invited_by=invited_by,
    )


def accept_org_invitation(token: str, user_id: str) -> tuple[bool, str]:
    """Accept an invitation. Returns (True, org_id) or (False, error_msg)."""
    invitation = repository.find_invitation_by_token(token)
    if invitation is None:
        return False, "Invitation not found."
    if invitation.status != "pending":
        return False, "Invitation has already been used."
    if invitation.expires_at:
        expires = invitation.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < datetime.now(timezone.utc):
            return False, "Invitation has expired."
    repository.add_member(org_id=invitation.org_id, user_id=user_id)
    repository.mark_invitation_accepted(invitation)
    return True, invitation.org_id


def remove_org_member(
    org_id: str,
    member_id: str,
    requesting_user_id: str,
) -> tuple[bool, str]:
    """Remove a member. Checks requesting user is owner and not removing self."""
    requester_member = repository.get_member(org_id, requesting_user_id)
    if requester_member is None:
        return False, "You are not a member of this organization."

    # Check requester is owner
    from .models import OrganizationMemberRole, OrganizationRole
    req_roles = OrganizationMemberRole.objects.filter(member_id=requester_member.id).all()
    is_owner = False
    for mr in req_roles:
        try:
            role = OrganizationRole.objects.get(id=mr.role_id)
            if role.name == "owner":
                is_owner = True
                break
        except Exception:
            pass
    if not is_owner:
        return False, "Only owners can remove members."

    # Prevent removing self
    from .models import OrganizationMember
    try:
        from nexorm.exceptions import DoesNotExist
        target = OrganizationMember.objects.get(id=member_id)
    except Exception:
        return False, "Member not found."
    if target.user_id == requesting_user_id:
        return False, "You cannot remove yourself. Use 'Leave organization' instead."

    repository.delete_member(member_id)
    return True, ""


def delete_user_organization(slug: str, user_id: str) -> tuple[bool, str]:
    """Delete an org entirely. Only owners may do this."""
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    member = repository.get_member(org.id, user_id)
    if member is None:
        return False, "You are not a member of this organization."
    from .models import OrganizationMemberRole, OrganizationRole
    user_roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None and
        OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in user_roles
    )
    if not is_owner:
        return False, "Only owners can delete an organization."
    org_name = org.name
    repository.delete_organization(org.id)
    return True, org_name


def leave_org(org_id: str, user_id: str) -> tuple[bool, str]:
    """Leave an org. Prevents last owner from leaving."""
    member = repository.get_member(org_id, user_id)
    if member is None:
        return False, "You are not a member of this organization."

    from .models import OrganizationMemberRole, OrganizationRole
    user_roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    is_owner = False
    for mr in user_roles:
        try:
            role = OrganizationRole.objects.get(id=mr.role_id)
            if role.name == "owner":
                is_owner = True
                break
        except Exception:
            pass

    if is_owner:
        # Count total owners
        all_members = repository.list_members(org_id)
        owner_count = 0
        for m in all_members:
            m_roles = OrganizationMemberRole.objects.filter(member_id=m.id).all()
            for mr in m_roles:
                try:
                    role = OrganizationRole.objects.get(id=mr.role_id)
                    if role.name == "owner":
                        owner_count += 1
                        break
                except Exception:
                    pass
        if owner_count <= 1:
            return False, "You are the last owner. Transfer ownership before leaving."

    repository.delete_member(member.id)
    return True, ""
