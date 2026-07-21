from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import repository
from .models import Organization, OrganizationInvitation, OrganizationMemberRole, OrganizationRole

_logger = logging.getLogger(__name__)


def _safe_url(url: str | None) -> str | None:
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
            "logo_url": org.logo_url or "",
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
    logo_url: str | None = None,
) -> Organization | None:
    kwargs: dict = {
        "name": name,
        "description": description,
        "website": _safe_url(website),
        "industry": industry,
        "size": size,
        "location": location,
        "contact_email": contact_email,
    }
    if logo_url is not None:
        kwargs["logo_url"] = logo_url
    return repository.update_organization(org_id, **kwargs)


def update_organization_status(org_id: str, status: str, reason: str | None = None) -> Organization | None:
    valid = {"active", "inactive", "suspended", "pending", "rejected"}
    if status not in valid:
        return None
    org = repository.update_organization(org_id, status=status)
    if org is None:
        return None

    # Notify org owner by email on significant status transitions
    if status in ("active", "rejected", "suspended") and org.owner_id:
        try:
            from codesandbox.config import get_settings
            from codesandbox.features.identity import repository as identity_repo
            from codesandbox.shared import email as mailer
            owner = identity_repo.find_user_by_id(str(org.owner_id))
            if owner:
                settings = get_settings()
                dashboard_url = f"{settings.app_url}/my/organizations/{org.slug}"
                support_url = f"{settings.app_url}/support"
                if status == "active":
                    mailer.send_org_approved(
                        to=owner.email,
                        org_name=org.name,
                        dashboard_url=dashboard_url,
                    )
                elif status == "rejected":
                    mailer.send_org_rejected(
                        to=owner.email,
                        org_name=org.name,
                        reason=reason,
                        support_url=support_url,
                    )
                elif status == "suspended":
                    mailer.send_org_suspended(
                        to=owner.email,
                        org_name=org.name,
                        reason=reason,
                        support_url=support_url,
                    )
        except Exception as exc:
            _logger.error("Org status-change notification failed for org %s: %s", org_id, exc)

    return org


# ── User-facing service functions ─────────────────────────────────────────────


def get_user_org_list(user_id: str) -> list[dict]:
    orgs = repository.get_user_organizations(user_id)
    result = []
    for org in orgs:
        member_count = repository.get_member_count(org.id)
        is_owner = repository.is_org_owner(org.id, user_id)
        member_permissions = [] if is_owner else repository.get_member_permissions(org.id, user_id)
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
            "is_owner": is_owner,
            "can_invite": is_owner or "org.members.invite" in member_permissions,
            "can_edit_settings": is_owner or "org.settings.edit" in member_permissions,
            "can_manage_members": is_owner or "org.members.remove" in member_permissions,
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
    # Backfill owner_id for pre-existing orgs
    if not org.owner_id:
        repository.ensure_creator_is_owner(org.id, str(org.created_by) if org.created_by else user_id)
        org = repository.get_organization(org.id)
    if not org:
        return None
    member = repository.get_member(org.id, user_id)
    if member is None:
        return None

    members = repository.get_members_with_info(org.id)
    is_owner = repository.is_org_owner(org.id, user_id)
    member_permissions = [] if is_owner else repository.get_member_permissions(org.id, user_id)

    roles = repository.list_org_roles(org.id)
    roles_list = []
    for r in roles:
        if r.name == "owner":
            continue  # exclude legacy owner role from listing
        cnt = OrganizationMemberRole.objects.filter(role_id=r.id).count()
        roles_list.append({
            "id": r.id,
            "name": r.name,
            "color": r.color or "#6366f1",
            "description": r.description or "",
            "is_system": bool(r.is_system),
            "position": r.position,
            "member_count": cnt,
            "permission_keys": repository.get_permissions_for_org_role(r.id),
        })

    actor_highest_position = repository.get_member_highest_position(org.id, user_id)

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
        "owner_id": org.owner_id,
        "members": members,
        "member_count": len(members),
        "is_owner": is_owner,
        "roles": roles_list,
        "member_permissions": member_permissions,
        "actor_highest_position": actor_highest_position,
        "can_invite": is_owner or "org.members.invite" in member_permissions,
        "can_edit_settings": is_owner or "org.settings.edit" in member_permissions,
        "can_manage_members": is_owner or "org.members.remove" in member_permissions,
        "can_manage_roles": is_owner or "org.roles.manage" in member_permissions,
        "can_assign_roles": is_owner or "org.roles.assign" in member_permissions,
    }


def invite_to_org(org_id: str, email: str, invited_by: str) -> tuple[OrganizationInvitation, str]:
    org = repository.get_organization(org_id)
    if org is None or org.status != "active":
        raise ValueError("This organization is not currently accepting invitations.")
    return repository.create_invitation(
        org_id=org_id,
        email=email,
        invited_by=invited_by,
    )


def get_org_invite_link_data(slug: str, user_id: str) -> dict | None:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return None
    if org.status != "active":
        return None
    if repository.get_member(org.id, user_id) is None:
        return None
    is_owner = repository.is_org_owner(org.id, user_id)
    if not is_owner and "org.members.invite" not in repository.get_member_permissions(org.id, user_id):
        return None
    code = repository.get_or_create_org_invite_code(org.id)
    from codesandbox.config import get_settings
    url = f"{get_settings().app_url}/my/organizations/join/code/{code}"
    return {"code": code, "url": url}


def regenerate_org_invite_link(slug: str, user_id: str) -> dict | None:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return None
    if org.status != "active":
        return None
    if repository.get_member(org.id, user_id) is None:
        return None
    is_owner = repository.is_org_owner(org.id, user_id)
    if not is_owner and "org.members.invite" not in repository.get_member_permissions(org.id, user_id):
        return None
    code = repository.regenerate_org_invite_code(org.id)
    from codesandbox.config import get_settings
    url = f"{get_settings().app_url}/my/organizations/join/code/{code}"
    return {"code": code, "url": url}


def batch_invite_to_org(
    slug: str, emails: list[str], invited_by: str, invited_by_name: str
) -> list[dict]:
    from codesandbox.config import get_settings
    from codesandbox.shared.email import send_org_invitation

    org = repository.get_organization_by_slug(slug)
    if org is None:
        return [{"email": e, "ok": False, "error": "org_not_found"} for e in emails]
    if org.status != "active":
        return [{"email": e, "ok": False, "error": "org_inactive"} for e in emails]
    settings = get_settings()
    results = []
    for email in emails[:5]:
        email = email.strip()
        if not email:
            continue
        try:
            invitation, raw_token = repository.create_invitation(
                org_id=org.id, email=email, invited_by=invited_by,
            )
            invite_url = f"{settings.app_url}/my/organizations/join/{raw_token}"
            try:
                sent = send_org_invitation(
                    to=email, org_name=org.name,
                    invite_url=invite_url, invited_by_name=invited_by_name,
                )
            except Exception:
                sent = False
            results.append({"email": email, "ok": True, "sent": sent, "url": invite_url})
        except Exception as exc:
            results.append({"email": email, "ok": False, "error": str(exc)})
    return results


def join_by_invite_code(code: str, user_id: str) -> tuple[bool, str]:
    org = repository.get_org_by_invite_code(code)
    if org is None:
        return False, "Invalid invite code."
    if org.status != "active":
        return False, "This organization is not currently accepting members."
    if repository.get_member(org.id, user_id):
        return True, org.id
    from nexorm.database import default_db
    with default_db.transaction():
        repository.add_member(org_id=org.id, user_id=user_id)
    return True, org.id


def accept_org_invitation(token: str, user_id: str) -> tuple[bool, str]:
    invitation = repository.find_invitation_by_token(token)
    if invitation is None:
        return False, "Invitation not found."
    org = repository.get_organization(invitation.org_id)
    if org is None or org.status != "active":
        return False, "This organization is not currently accepting members."
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
    requester_member = repository.get_member(org_id, requesting_user_id)
    if requester_member is None:
        return False, "You are not a member of this organization."

    is_owner = repository.is_org_owner(org_id, requesting_user_id)
    if not is_owner and "org.members.remove" not in repository.get_member_permissions(org_id, requesting_user_id):
        return False, "You don't have permission to remove members."

    from .models import OrganizationMember
    try:
        target = OrganizationMember.objects.get(id=member_id)
    except Exception:
        return False, "Member not found."

    # Rule 8: member must belong to this org
    if str(target.org_id) != str(org_id):
        return False, "Member not found."

    # Rule 5: cannot remove the owner
    if repository.is_org_owner(org_id, target.user_id):
        return False, "Cannot remove the organization owner."

    if str(target.user_id) == requesting_user_id:
        return False, "You cannot remove yourself. Use 'Leave organization' instead."
    if not is_owner and not repository.can_actor_manage_member(org_id, requesting_user_id, target.user_id):
        return False, "You cannot remove a member equal to or higher than your own position."

    repository.delete_member(member_id)
    return True, ""


def delete_user_organization(slug: str, user_id: str) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    if not repository.is_org_owner(org.id, user_id):
        return False, "Only the owner can delete this organization."
    org_name = org.name
    repository.delete_organization(org.id)
    return True, org_name


def delete_platform_organization(org_id: str) -> tuple[bool, str]:
    org = repository.get_organization(org_id)
    if org is None:
        return False, "Organization not found."
    org_name = org.name
    repository.delete_organization(org.id)
    return True, org_name


def transfer_org_ownership(slug: str, requesting_user_id: str, new_owner_id: str) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    return repository.transfer_ownership(org.id, requesting_user_id, new_owner_id)


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

    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.manage" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to create roles."

    name = name.strip()
    if not name:
        return False, "Role name is required."
    if OrganizationRole.objects.filter(org_id=org.id, name=name).first():
        return False, f'A role named "{name}" already exists.'

    # New roles default to position=1 (below all standard roles)
    repository.create_org_role(org.id, name, color, description, position=1)
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

    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.manage" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to update roles."

    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."

    # Rule 8: role must belong to this org
    if str(role.org_id) != str(org.id):
        return False, "Role not found."

    # Rule 2: cannot edit role >= own highest position
    if not repository.can_actor_manage_role(org.id, requesting_user_id, role_id):
        return False, "You cannot edit a role equal to or higher than your own position."

    name = name.strip()
    if not name:
        return False, "Role name is required."
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

    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.manage" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to delete roles."

    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."

    # Rule 8: role must belong to this org
    if str(role.org_id) != str(org.id):
        return False, "Role not found."

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

    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.assign" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to assign roles."

    # Rule 8: verify member and role belong to this org
    from .models import OrganizationMember
    try:
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

    if not is_owner:
        actor_pos = repository.get_member_highest_position(org.id, requesting_user_id)
        if not repository.can_actor_manage_member(org.id, requesting_user_id, target.user_id):
            return False, "You cannot manage a member equal to or higher than your own position."
        # Rule 1: cannot assign role >= own highest position
        if role.position >= actor_pos:
            return False, "You cannot assign a role equal to or higher than your own position."
        # Rule 7: cannot self-assign a role that raises own position
        if str(target.user_id) == requesting_user_id:
            if role.position >= actor_pos:
                return False, "You cannot assign yourself a role equal to or higher than your current position."

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

    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.assign" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to remove roles."

    # Rule 8: verify member and role belong to this org
    from .models import OrganizationMember
    try:
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

    # Rule 1: cannot remove a role that is >= own position (unless owner)
    if not is_owner:
        actor_pos = repository.get_member_highest_position(org.id, requesting_user_id)
        if not repository.can_actor_manage_member(org.id, requesting_user_id, target.user_id):
            return False, "You cannot manage a member equal to or higher than your own position."
        if role.position >= actor_pos:
            return False, "You cannot remove a role equal to or higher than your own position."

    repository.remove_role_from_member(member_id, role_id)
    return True, ""


def toggle_org_role_permission(
    slug: str,
    role_id: str,
    permission_key: str,
    enabled: bool,
    requesting_user_id: str,
) -> tuple[bool, str]:
    from codesandbox.shared.permissions import get_registered_org_permissions
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    member = repository.get_member(org.id, requesting_user_id)
    if member is None:
        return False, "Not a member."

    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.manage" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to manage role permissions."

    # Validate permission key is registered (prevents injection)
    registered_keys = {k for k, _, _ in get_registered_org_permissions()}
    if permission_key not in registered_keys:
        return False, "Invalid permission key."

    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False, "Role not found."
    if str(role.org_id) != str(org.id):
        return False, "Role not found."

    # Rule 2: cannot manage role >= own position
    if not repository.can_actor_manage_role(org.id, requesting_user_id, role_id):
        return False, "You cannot manage a role equal to or higher than your own position."

    if enabled and not is_owner:
        # Rule 3: cannot grant a permission you don't hold
        actor_perms = repository.get_member_permissions(org.id, requesting_user_id)
        if permission_key not in actor_perms:
            return False, "You cannot grant a permission you don't hold."
        # Rule 4: org.roles.manage can only be granted by owner
        if permission_key == "org.roles.manage":
            return False, "Only the owner can grant the roles management permission."

    repository.set_org_role_permission(role_id, permission_key, enabled)
    return True, ""


def get_role_members_for_org(org_id: str, role_id: str) -> list[dict]:
    return repository.get_role_members(org_id, role_id)


def reorder_org_role_positions(slug: str, role_ids: list[str], requesting_user_id: str) -> tuple[bool, str]:
    org = repository.get_organization_by_slug(slug)
    if org is None:
        return False, "Organization not found."
    if repository.get_member(org.id, requesting_user_id) is None:
        return False, "Not a member."
    is_owner = repository.is_org_owner(org.id, requesting_user_id)
    if not is_owner and "org.roles.manage" not in repository.get_member_permissions(org.id, requesting_user_id):
        return False, "You don't have permission to reorder roles."

    roles = {str(r.id): r for r in repository.list_org_roles(org.id) if r.name != "owner"}
    ordered_ids = [str(rid) for rid in role_ids if str(rid) in roles]
    if set(ordered_ids) != set(roles):
        return False, "Role order must include every role."
    if not is_owner:
        for role_id in ordered_ids:
            if not repository.can_actor_manage_role(org.id, requesting_user_id, role_id):
                return False, "You cannot reorder a role equal to or higher than your own position."
    repository.reorder_org_roles(org.id, ordered_ids)
    return True, ""


def leave_org(org_id: str, user_id: str) -> tuple[bool, str]:
    member = repository.get_member(org_id, user_id)
    if member is None:
        return False, "You are not a member of this organization."
    if repository.is_org_owner(org_id, user_id):
        return False, "You are the owner. Transfer ownership before leaving."
    repository.delete_member(member.id)
    return True, ""
