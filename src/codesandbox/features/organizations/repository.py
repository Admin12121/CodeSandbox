from __future__ import annotations

import hashlib
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

_CODE_CHARS = string.ascii_uppercase + string.digits


def _gen_invite_code() -> str:
    return "".join(secrets.choice(_CODE_CHARS) for _ in range(16))

from nexorm.exceptions import DoesNotExist, IntegrityError

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
    if not created_by:
        raise ValueError("Organization owner is required.")
    from codesandbox.features.identity import repository as identity_repo
    owner = identity_repo.find_user_by_id(str(created_by))
    if owner is None or owner.deleted_at is not None:
        raise ValueError("Organization owner must be an active user.")

    base_slug = _slugify(name)
    counter = 1
    slug = base_slug
    while get_organization_by_slug(slug):
        slug = f"{base_slug}-{counter}"
        counter += 1
    for attempt in range(5):
        candidate = slug if attempt == 0 else f"{base_slug}-{secrets.token_hex(3)}"
        org = Organization(
            id=str(uuid.uuid4()),
            name=name,
            slug=candidate,
            description=description,
            created_by=created_by,
            owner_id=created_by,
        )
        try:
            org.save()
            break
        except IntegrityError:
            if attempt == 4:
                raise
            continue
    if created_by:
        add_member(org_id=org.id, user_id=created_by)
    ensure_org_permissions_seeded()
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
) -> tuple[OrganizationInvitation, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    inv = OrganizationInvitation(
        id=str(uuid.uuid4()),
        org_id=org_id,
        email=email,
        token=token_hash,
        invited_by=invited_by,
        expires_at=expires_at,
    )
    inv.save()
    return inv, raw_token


def list_org_roles(org_id: str) -> list[OrganizationRole]:
    roles = OrganizationRole.objects.filter(org_id=org_id).all()
    return sorted(roles, key=lambda r: int(r.position or 0), reverse=True)


def seed_org_roles(org_id: str) -> None:
    defaults = [
        ("admin",  "#f59e0b", False, 80),
        ("member", "#6366f1", False, 10),
    ]
    for name, color, is_system, position in defaults:
        existing = OrganizationRole.objects.filter(org_id=org_id, name=name).first()
        if not existing:
            role = OrganizationRole(
                id=str(uuid.uuid4()),
                org_id=org_id,
                name=name,
                color=color,
                is_system=is_system,
                position=position,
            )
            role.save()
        elif existing.position == 0 or existing.is_system:
            existing.position = position if existing.position == 0 else existing.position
            existing.is_system = False
            existing.save()


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
    """Return members with user info, roles, and owner flag."""
    from codesandbox.features.identity import repository as identity_repo
    org = get_organization(org_id)
    owner_id = str(org.owner_id) if org and org.owner_id else None
    members = OrganizationMember.objects.filter(org_id=org_id).all()
    result = []
    for m in members:
        user = identity_repo.find_user_by_id(m.user_id)
        if user is None:
            continue
        member_roles = OrganizationMemberRole.objects.filter(member_id=m.id).all()
        role_names: list[str] = []
        role_colors: list[str] = []
        role_ids: list[str] = []
        for mr in member_roles:
            try:
                role = OrganizationRole.objects.get(id=mr.role_id)
                if role.name == "owner":
                    continue  # exclude legacy owner role from display
                role_names.append(role.name)
                role_colors.append(role.color)
                role_ids.append(role.id)
            except Exception:
                pass
        result.append({
            "id": m.id,
            "user_id": m.user_id,
            "name": user.name,
            "email": user.email,
            "avatar_url": getattr(user, "avatar_url", None),
            "roles": role_names,
            "role_colors": role_colors,
            "role_ids": role_ids,
            "is_owner": str(m.user_id) == owner_id,
            "joined_at": m.joined_at,
        })
    return result


def create_org_role(
    org_id: str,
    name: str,
    color: str,
    description: str | None = None,
    position: int = 1,
) -> OrganizationRole:
    role = OrganizationRole(
        id=str(uuid.uuid4()),
        org_id=org_id,
        name=name.strip(),
        color=color or "#6366f1",
        description=description,
        is_system=False,
        position=position,
    )
    role.save()
    return role


def update_org_role(
    role_id: str,
    name: str,
    color: str,
    description: str | None = None,
    position: int | None = None,
) -> bool:
    """Update a role. Returns False if not found."""
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False
    role.name = name.strip()
    role.color = color or "#6366f1"
    role.description = description
    if position is not None:
        role.position = position
    role.save()
    return True


def delete_org_role(role_id: str) -> bool:
    """Delete a role. Returns False if not found."""
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False
    for rp in OrganizationRolePermission.objects.filter(role_id=role_id).all():
        rp.delete()
    for mr in OrganizationMemberRole.objects.filter(role_id=role_id).all():
        mr.delete()
    role.delete()
    return True


def assign_role_to_member(member_id: str, role_id: str) -> bool:
    if OrganizationMemberRole.objects.filter(member_id=member_id, role_id=role_id).first():
        return True
    mr = OrganizationMemberRole(
        id=str(uuid.uuid4()),
        member_id=member_id,
        role_id=role_id,
    )
    mr.save()
    return True


def remove_role_from_member(member_id: str, role_id: str) -> None:
    mr = OrganizationMemberRole.objects.filter(member_id=member_id, role_id=role_id).first()
    if mr:
        mr.delete()


def find_invitation_by_token(raw_token: str) -> OrganizationInvitation | None:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return OrganizationInvitation.objects.filter(token=token_hash).first()


def mark_invitation_accepted(invitation: OrganizationInvitation) -> None:
    invitation.status = "accepted"
    invitation.save()


def get_or_create_org_invite_code(org_id: str) -> str:
    org = get_organization(org_id)
    if org is None:
        return ""
    if org.invite_code:
        return org.invite_code
    code = _gen_invite_code()
    update_organization(org_id, invite_code=code)
    return code


def regenerate_org_invite_code(org_id: str) -> str:
    code = _gen_invite_code()
    update_organization(org_id, invite_code=code)
    return code


def get_org_by_invite_code(code: str) -> Organization | None:
    return Organization.objects.filter(invite_code=code).first()


def ensure_creator_is_owner(org_id: str, user_id: str) -> None:
    """Backfill owner_id for orgs that existed before this field was added."""
    org = get_organization(org_id)
    if not org or org.owner_id:
        return
    # Try to find existing holder of the legacy 'owner' role first
    legacy_owner_role = OrganizationRole.objects.filter(org_id=org_id, name="owner").first()
    if legacy_owner_role:
        mr = OrganizationMemberRole.objects.filter(role_id=legacy_owner_role.id).first()
        if mr:
            try:
                member = OrganizationMember.objects.get(id=mr.member_id)
                update_organization(org_id, owner_id=member.user_id)
                return
            except Exception:
                pass
    # Fall back to created_by / the calling user
    if not get_member(org_id, user_id):
        m = OrganizationMember(id=str(uuid.uuid4()), org_id=org_id, user_id=user_id)
        m.save()
    update_organization(org_id, owner_id=user_id)


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



def ensure_org_permissions_seeded() -> None:
    # Permission registration is import-driven. Guarantee every organization
    # permission provider is loaded before synchronizing the database; otherwise
    # creating an organization from a narrow code path could incorrectly treat
    # valid sandbox permissions as stale and delete them.
    import codesandbox.features.organizations  # noqa: F401
    import codesandbox.features.sandbox  # noqa: F401
    from codesandbox.shared.permissions import get_registered_org_permissions

    registered = {key: (label, group) for key, label, group in get_registered_org_permissions()}

    for perm in OrganizationPermission.objects.all():
        if perm.key not in registered:
            OrganizationRolePermission.objects.filter(permission_id=perm.id).delete()
            perm.delete()

    for key, (label, group) in registered.items():
        row = OrganizationPermission.objects.filter(key=key).first()
        if row is None:
            row = OrganizationPermission(
                id=str(uuid.uuid4()), key=key, label=label, group=group,
            )
            row.save()
        elif row.label != label or row.group != group:
            row.label = label
            row.group = group
            row.save()

def get_all_org_permissions() -> list[OrganizationPermission]:
    return OrganizationPermission.objects.filter().all()


def get_permissions_for_org_role(role_id: str) -> list[str]:
    rps = OrganizationRolePermission.objects.filter(role_id=role_id).all()
    keys: list[str] = []
    for rp in rps:
        try:
            p = OrganizationPermission.objects.get(id=rp.permission_id)
            keys.append(p.key)
        except Exception:
            pass
    return keys


def set_org_role_permission(role_id: str, permission_key: str, enabled: bool) -> bool:
    perm = OrganizationPermission.objects.filter(key=permission_key).first()
    if not perm:
        return False
    existing = OrganizationRolePermission.objects.filter(
        role_id=role_id, permission_id=perm.id
    ).first()
    if enabled and not existing:
        rp = OrganizationRolePermission(
            id=str(uuid.uuid4()), role_id=role_id, permission_id=perm.id,
        )
        rp.save()
    elif not enabled and existing:
        existing.delete()
    return True


def is_org_owner(org_id: str, user_id: str) -> bool:
    """Return True if the user is the current owner of the org."""
    org = get_organization(org_id)
    if not org:
        return False
    return bool(org.owner_id) and str(org.owner_id) == str(user_id)


def get_member_permissions(org_id: str, user_id: str) -> list[str]:
    """Return all permission keys the user has via their org roles."""
    member = get_member(org_id, user_id)
    if not member:
        return []
    mrs = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    keys: set[str] = set()
    for mr in mrs:
        for key in get_permissions_for_org_role(mr.role_id):
            keys.add(key)
    return list(keys)


def get_member_highest_position(org_id: str, user_id: str) -> int:
    """Return the highest role position for a member. Owner returns sys.maxsize."""
    import sys
    if is_org_owner(org_id, user_id):
        return sys.maxsize
    member = get_member(org_id, user_id)
    if not member:
        return 0
    mrs = OrganizationMemberRole.objects.filter(member_id=member.id).all()
    positions: list[int] = []
    for mr in mrs:
        try:
            role = OrganizationRole.objects.get(id=mr.role_id)
            positions.append(role.position)
        except Exception:
            pass
    return max(positions) if positions else 0


def can_actor_manage_role(org_id: str, actor_id: str, role_id: str) -> bool:
    """True if actor's highest position is strictly greater than the target role's position."""
    if is_org_owner(org_id, actor_id):
        return True
    try:
        role = OrganizationRole.objects.get(id=role_id)
    except Exception:
        return False
    return get_member_highest_position(org_id, actor_id) > role.position


def can_actor_manage_member(org_id: str, actor_id: str, target_user_id: str) -> bool:
    """True if actor's highest position is strictly greater than target member's."""
    if is_org_owner(org_id, actor_id):
        return True
    if str(actor_id) == str(target_user_id):
        return False
    if is_org_owner(org_id, target_user_id):
        return False
    return get_member_highest_position(org_id, actor_id) > get_member_highest_position(org_id, target_user_id)


def reorder_org_roles(org_id: str, role_ids: list[str]) -> None:
    total = len(role_ids)
    for index, role_id in enumerate(role_ids):
        role = OrganizationRole.objects.get(id=role_id)
        if str(role.org_id) != str(org_id):
            continue
        role.position = (total - index) * 10
        role.save()


def transfer_ownership(org_id: str, current_owner_id: str, new_owner_id: str) -> tuple[bool, str]:
    """Transfer org ownership. new_owner_id must already be a member."""
    if not is_org_owner(org_id, current_owner_id):
        return False, "You are not the owner of this organization."
    if str(current_owner_id) == str(new_owner_id):
        return False, "You are already the owner."
    if not get_member(org_id, new_owner_id):
        return False, "New owner must be a current member of the organization."
    update_organization(org_id, owner_id=new_owner_id)
    return True, ""


def get_role_members(org_id: str, role_id: str) -> list[dict]:
    """Return user info for every member who holds role_id in org_id."""
    from codesandbox.features.identity import repository as identity_repo
    mrs = OrganizationMemberRole.objects.filter(role_id=role_id).all()
    result = []
    for mr in mrs:
        try:
            member = OrganizationMember.objects.get(id=mr.member_id)
        except Exception:
            continue
        if str(member.org_id) != str(org_id):
            continue
        user = identity_repo.find_user_by_id(member.user_id)
        if user is None:
            continue
        result.append({
            "id": member.id,
            "user_id": member.user_id,
            "name": user.name,
            "email": user.email,
            "joined_at": member.joined_at,
        })
    return result


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
