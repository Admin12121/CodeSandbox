from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from . import repository
from .models import Organization, OrganizationInvitation, OrganizationMemberRole, OrganizationRole


def _safe_url(url: str | None) -> str | None:
    """Reject URLs with non-http(s) schemes to prevent javascript:/data: injection."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None
    return url


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
        website=_safe_url(website),
        industry=industry,
        size=size,
        location=location,
        contact_email=contact_email,
    )


def update_organization_status(org_id: str, status: str) -> Organization | None:
    valid = {"active", "inactive", "suspended", "pending", "rejected"}
    if status not in valid:
        return None
    org = repository.update_organization(org_id, status=status)
    if status == "active" and org is not None:
        # Provision per-org database if not already ready
        existing = repository.get_org_database(org_id)
        if existing is None or existing.status not in ("ready", "provisioning"):
            import threading
            t = threading.Thread(target=repository.provision_org_database, args=(org_id,), daemon=True)
            t.start()
    return org


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
            "logo_url": org.logo_url or "",
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
    logo_url: str | None = None,
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
        website=_safe_url(website),
        industry=industry,
        size=size,
        location=location,
        contact_email=contact_email,
        logo_url=logo_url,
    )
    return org


def get_org_for_user(slug: str, user_id: str) -> dict | None:
    """Return org detail dict if the user is a member, else None."""
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return None
    if org.created_by == user_id:
        repository.ensure_creator_is_owner(org.id, user_id)
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

    roles = repository.list_org_roles(org.id)
    roles_list = []
    for r in roles:
        cnt = OrganizationMemberRole.objects.filter(role_id=r.id).count()
        roles_list.append({
            "id": r.id,
            "name": r.name,
            "color": r.color or "#6366f1",
            "description": r.description or "",
            "is_system": bool(r.is_system),
            "member_count": cnt,
            "permission_keys": repository.get_permissions_for_org_role(r.id),
        })

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
        "logo_url": org.logo_url or "",
        "status": org.status,
        "created_at": org.created_at,
        "members": members,
        "member_count": len(members),
        "is_owner": is_owner,
        "owner_count": owner_count,
        "roles": roles_list,
    }


def invite_to_org(org_id: str, email: str, invited_by: str) -> tuple[OrganizationInvitation, str]:
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
    from nexorm.database import default_db
    with default_db.transaction():
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


def create_org_custom_role(
    slug: str,
    name: str,
    color: str,
    description: str | None,
    requesting_user_id: str,
) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    member = repository.get_member(org.id, requesting_user_id)
    if member is None:
        return False, "Not a member."
    user_roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None
        and OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in user_roles
    )
    if not is_owner:
        return False, "Only owners can create roles."
    name = name.strip()
    if not name:
        return False, "Role name is required."
    if OrganizationRole.objects.filter(org_id=org.id, name=name).first():
        return False, f'A role named "{name}" already exists.'
    repository.create_org_role(org.id, name, color, description)
    return True, ""


def update_org_custom_role(
    slug: str,
    role_id: str,
    name: str,
    color: str,
    description: str | None,
    requesting_user_id: str,
) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    member = repository.get_member(org.id, requesting_user_id)
    if member is None:
        return False, "Not a member."
    user_roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None
        and OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in user_roles
    )
    if not is_owner:
        return False, "Only owners can update roles."
    name = name.strip()
    if not name:
        return False, "Role name is required."
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."
    if str(role.org_id) != str(org.id):
        return False, "Role not found."
    if role.is_system:
        return False, "System roles cannot be edited."
    existing = OrganizationRole.objects.filter(org_id=org.id, name=name).first()
    if existing and str(existing.id) != str(role_id):
        return False, f'A role named "{name}" already exists.'
    repository.update_org_role(role_id, name, color, description)
    return True, ""


def delete_org_custom_role(
    slug: str,
    role_id: str,
    requesting_user_id: str,
) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    member = repository.get_member(org.id, requesting_user_id)
    if member is None:
        return False, "Not a member."
    user_roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None
        and OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in user_roles
    )
    if not is_owner:
        return False, "Only owners can delete roles."
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."
    if str(role.org_id) != str(org.id):
        return False, "Role not found."
    if role.is_system:
        return False, "System roles cannot be deleted."
    repository.delete_org_role(role_id)
    return True, ""


def assign_role_to_org_member(
    slug: str,
    member_id: str,
    role_id: str,
    requesting_user_id: str,
) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    requester = repository.get_member(org.id, requesting_user_id)
    if requester is None:
        return False, "Not a member."
    req_roles = OrganizationMemberRole.objects.filter(member_id=requester.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None
        and OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in req_roles
    )
    if not is_owner:
        return False, "Only owners can assign roles."
    try:
        from .models import OrganizationMember
        target = OrganizationMember.objects.get(id=member_id)
    except Exception:
        return False, "Member not found."
    if str(target.org_id) != str(org.id):
        return False, "Member not found."
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."
    if str(role.org_id) != str(org.id):
        return False, "Role not found."
    repository.assign_role_to_member(member_id, role_id)
    return True, ""


def remove_role_from_org_member(
    slug: str,
    member_id: str,
    role_id: str,
    requesting_user_id: str,
) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    requester = repository.get_member(org.id, requesting_user_id)
    if requester is None:
        return False, "Not a member."
    req_roles = OrganizationMemberRole.objects.filter(member_id=requester.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None
        and OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in req_roles
    )
    if not is_owner:
        return False, "Only owners can remove roles."
    try:
        from .models import OrganizationMember
        target = OrganizationMember.objects.get(id=member_id)
    except Exception:
        return False, "Member not found."
    if str(target.org_id) != str(org.id):
        return False, "Member not found."
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."
    if str(role.org_id) != str(org.id):
        return False, "Role not found."
    # Prevent removing owner role if this is the last owner
    if role.name == "owner":
        all_members = repository.list_members(org.id)
        owner_count = 0
        for m in all_members:
            m_roles = OrganizationMemberRole.objects.filter(member_id=m.id).all()
            for mr in m_roles:
                try:
                    r = OrganizationRole.objects.get(id=mr.role_id)
                    if r.name == "owner":
                        owner_count += 1
                        break
                except Exception:
                    pass
        if owner_count <= 1:
            return False, "Cannot remove the last owner's owner role."
    repository.remove_role_from_member(member_id, role_id)
    return True, ""


def toggle_org_role_permission(
    slug: str,
    role_id: str,
    permission_key: str,
    enabled: bool,
    requesting_user_id: str,
) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    member = repository.get_member(org.id, requesting_user_id)
    if member is None:
        return False, "Not a member."
    req_roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    is_owner = any(
        OrganizationRole.objects.filter(id=mr.role_id).first() is not None
        and OrganizationRole.objects.filter(id=mr.role_id).first().name == "owner"
        for mr in req_roles
    )
    if not is_owner:
        return False, "Only owners can manage role permissions."
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."
    if str(role.org_id) != str(org.id):
        return False, "Role not found."
    repository.set_org_role_permission(role_id, permission_key, enabled)
    return True, ""


def get_role_members_for_org(org_id: str, role_id: str) -> list[dict]:
    return repository.get_role_members(org_id, role_id)


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
