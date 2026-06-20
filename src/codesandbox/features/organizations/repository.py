from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from nexorm.exceptions import DoesNotExist

from .models import (
    Organization,
    OrganizationInvitation,
    OrganizationMember,
    OrganizationMemberRole,
    OrganizationPermission,
    OrganizationRole,
    OrganizationRolePermission,
)


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80] or "org"


def list_organizations(
    *,
    search: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Organization], int]:
    qs = Organization.objects.filter()
    if status and status != "all":
        qs = qs.filter(status=status)
    all_orgs = qs.order_by("-created_at").all()
    if search:
        q = search.lower()
        all_orgs = [o for o in all_orgs if q in o.name.lower() or q in o.slug.lower()]
    total = len(all_orgs)
    offset = (page - 1) * page_size
    return all_orgs[offset : offset + page_size], total


def get_organization(org_id: str) -> Organization | None:
    try:
        return Organization.objects.get(id=org_id)
    except DoesNotExist:
        return None


def get_organization_by_slug(slug: str) -> Organization | None:
    return Organization.objects.filter(slug=slug).first()


def create_organization(
    *,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> Organization:
    slug = _slugify(name)
    base_slug = slug
    counter = 1
    while get_organization_by_slug(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1
    org = Organization(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        description=description,
        created_by=created_by,
    )
    org.save()
    if created_by:
        add_member(org_id=org.id, user_id=created_by)
    seed_org_roles(org.id)
    return org


def update_organization(org_id: str, **kwargs) -> Organization | None:
    org = get_organization(org_id)
    if org is None:
        return None
    for key, value in kwargs.items():
        setattr(org, key, value)
    org.updated_at = datetime.now(timezone.utc)
    org.save()
    return org


def get_member(org_id: str, user_id: str) -> OrganizationMember | None:
    return OrganizationMember.objects.filter(org_id=org_id, user_id=user_id).first()


def add_member(org_id: str, user_id: str) -> OrganizationMember:
    existing = get_member(org_id, user_id)
    if existing:
        return existing
    member = OrganizationMember(
        id=str(uuid.uuid4()),
        org_id=org_id,
        user_id=user_id,
    )
    member.save()
    owner_role = OrganizationRole.objects.filter(org_id=org_id, name="owner").first()
    if owner_role:
        mr = OrganizationMemberRole(
            id=str(uuid.uuid4()),
            member_id=member.id,
            role_id=owner_role.id,
        )
        mr.save()
    return member


def remove_member(org_id: str, user_id: str) -> None:
    member = get_member(org_id, user_id)
    if member:
        roles = OrganizationMemberRole.objects.filter(member_id=member.id).all()
        for r in roles:
            r.delete()
        member.delete()


def list_members(org_id: str) -> list[OrganizationMember]:
    return OrganizationMember.objects.filter(org_id=org_id).all()


def get_member_count(org_id: str) -> int:
    return OrganizationMember.objects.filter(org_id=org_id).count()


def create_invitation(
    org_id: str,
    email: str,
    invited_by: str | None = None,
) -> OrganizationInvitation:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    inv = OrganizationInvitation(
        id=str(uuid.uuid4()),
        org_id=org_id,
        email=email,
        token=token,
        invited_by=invited_by,
        expires_at=expires_at,
    )
    inv.save()
    return inv


def list_org_roles(org_id: str) -> list[OrganizationRole]:
    return OrganizationRole.objects.filter(org_id=org_id).order_by("position").all()


def seed_org_roles(org_id: str) -> None:
    defaults = [
        ("owner", "#ef4444", True),
        ("admin", "#f59e0b", True),
        ("member", "#6366f1", True),
    ]
    for name, color, is_system in defaults:
        if not OrganizationRole.objects.filter(org_id=org_id, name=name).first():
            role = OrganizationRole(
                id=str(uuid.uuid4()),
                org_id=org_id,
                name=name,
                color=color,
                is_system=is_system,
            )
            role.save()


# ── User-facing repository functions ─────────────────────────────────────────


def get_user_organizations(user_id: str) -> list[Organization]:
    """Return all organizations where the given user is a member."""
    memberships = OrganizationMember.objects.filter(user_id=user_id).all()
    orgs = []
    for m in memberships:
        org = get_organization(m.org_id)
        if org is not None:
            orgs.append(org)
    return orgs


def get_members_with_info(org_id: str) -> list[dict]:
    """Return members with user name/email and their role names."""
    from codesandbox.features.identity import repository as identity_repo

    members = OrganizationMember.objects.filter(org_id=org_id).all()
    result = []
    for m in members:
        user = identity_repo.find_user_by_id(m.user_id)
        if user is None:
            continue
        member_roles = OrganizationMemberRole.objects.filter(member_id=m.id).all()
        role_names = []
        role_colors = []
        for mr in member_roles:
            role = None
            try:
                role = OrganizationRole.objects.get(id=mr.role_id)
            except Exception:
                pass
            if role:
                role_names.append(role.name)
                role_colors.append(role.color)
        result.append({
            "id": m.id,
            "user_id": m.user_id,
            "name": user.name,
            "email": user.email,
            "roles": role_names,
            "role_colors": role_colors,
            "joined_at": m.joined_at,
        })
    return result


def find_invitation_by_token(token: str) -> OrganizationInvitation | None:
    return OrganizationInvitation.objects.filter(token=token).first()


def mark_invitation_accepted(invitation: OrganizationInvitation) -> None:
    invitation.status = "accepted"
    invitation.save()


def delete_member(member_id: str) -> None:
    """Delete OrganizationMemberRole rows then the member itself."""
    roles = OrganizationMemberRole.objects.filter(member_id=member_id).all()
    for r in roles:
        r.delete()
    try:
        member = OrganizationMember.objects.get(id=member_id)
        member.delete()
    except Exception:
        pass


def delete_organization(org_id: str) -> None:
    """Cascade-delete all org data then the org itself."""
    members = OrganizationMember.objects.filter(org_id=org_id).all()
    for m in members:
        for r in OrganizationMemberRole.objects.filter(member_id=m.id).all():
            r.delete()
        m.delete()
    for inv in OrganizationInvitation.objects.filter(org_id=org_id).all():
        inv.delete()
    for role in OrganizationRole.objects.filter(org_id=org_id).all():
        for p in OrganizationRolePermission.objects.filter(role_id=role.id).all():
            p.delete()
        role.delete()
    org = get_organization(org_id)
    if org:
        org.delete()
