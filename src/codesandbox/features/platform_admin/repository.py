from __future__ import annotations

import uuid

from nexorm.exceptions import DoesNotExist

from codesandbox.features.identity.models import User
from codesandbox.features.identity.repository import find_user_by_id

from .models import PlatformPermission, PlatformRole, PlatformRolePermission, PlatformUserRole


def list_roles() -> list[PlatformRole]:
    return PlatformRole.objects.all()


def _role_position(role: PlatformRole | None) -> int:
    if role is None:
        return 0
    try:
        return int(role.position or "0")
    except (TypeError, ValueError):
        return 0


def get_role(role_id: str) -> PlatformRole | None:
    try:
        return PlatformRole.objects.get(id=role_id)
    except DoesNotExist:
        return None


def get_role_by_name(name: str) -> PlatformRole | None:
    return PlatformRole.objects.filter(name=name).first()


def create_role(
    *,
    name: str,
    color: str = "#6366f1",
    description: str | None = None,
    is_system: bool = False,
) -> PlatformRole:
    role = PlatformRole(
        id=str(uuid.uuid4()),
        name=name,
        color=color,
        description=description,
        is_system=is_system,
    )
    role.save()
    return role


def update_role_position(role_id: str, position: int) -> bool:
    role = get_role(role_id)
    if not role:
        return False
    role.position = str(position)
    role.save()
    return True


def delete_role(role_id: str) -> bool:
    role = get_role(role_id)
    if not role:
        return False
    role.delete()
    return True


def list_permissions() -> list[PlatformPermission]:
    return PlatformPermission.objects.all()


def get_permissions_for_role(role_id: str) -> list[PlatformPermission]:
    rp_list = PlatformRolePermission.objects.filter(role_id=role_id).all()
    perm_ids = [rp.permission_id for rp in rp_list]
    if not perm_ids:
        return []
    return [p for p in list_permissions() if p.id in perm_ids]


def set_role_permissions(role_id: str, permission_ids: list[str]) -> None:
    existing = PlatformRolePermission.objects.filter(role_id=role_id).all()
    for rp in existing:
        rp.delete()
    for perm_id in permission_ids:
        rp = PlatformRolePermission(
            id=str(uuid.uuid4()),
            role_id=role_id,
            permission_id=perm_id,
        )
        rp.save()


def get_user_platform_roles(user_id: str) -> list[PlatformRole]:
    user_roles = PlatformUserRole.objects.filter(user_id=user_id).all()
    role_ids = [ur.role_id for ur in user_roles]
    if not role_ids:
        return []
    all_roles = list_roles()
    return [r for r in all_roles if r.id in role_ids]


def get_user_highest_role_position(user_id: str) -> int:
    user = find_user_by_id(user_id)
    if not user:
        return 0
    if user.platform_role == "system_admin":
        import sys
        return sys.maxsize
    roles = get_user_platform_roles(user_id)
    return max((_role_position(r) for r in roles), default=0)


def can_actor_manage_role(actor_user_id: str, role_id: str) -> bool:
    actor = find_user_by_id(actor_user_id)
    if actor and actor.platform_role == "system_admin":
        return True
    role = get_role(role_id)
    if not role:
        return False
    return get_user_highest_role_position(actor_user_id) > _role_position(role)


def can_actor_manage_user(actor_user_id: str, target_user_id: str) -> bool:
    actor = find_user_by_id(actor_user_id)
    if actor and actor.platform_role == "system_admin":
        return True
    if str(actor_user_id) == str(target_user_id):
        return False
    return get_user_highest_role_position(actor_user_id) > get_user_highest_role_position(target_user_id)


def get_user_permission_keys(user_id: str) -> set[str]:
    user = find_user_by_id(user_id)
    if not user:
        return set()
    if user.platform_role == "system_admin":
        return {p.key for p in list_permissions()}
    roles = get_user_platform_roles(user_id)
    keys: set[str] = set()
    for role in roles:
        for perm in get_permissions_for_role(role.id):
            keys.add(perm.key)
    return keys


def assign_role_to_user(user_id: str, role_id: str, granted_by: str | None = None) -> None:
    existing = PlatformUserRole.objects.filter(user_id=user_id, role_id=role_id).first()
    if existing:
        return
    ur = PlatformUserRole(
        id=str(uuid.uuid4()),
        user_id=user_id,
        role_id=role_id,
        granted_by=granted_by,
    )
    ur.save()


def remove_role_from_user(user_id: str, role_id: str) -> None:
    ur = PlatformUserRole.objects.filter(user_id=user_id, role_id=role_id).first()
    if ur:
        ur.delete()


def get_role_members(role_id: str) -> list[User]:
    user_roles = PlatformUserRole.objects.filter(role_id=role_id).all()
    user_ids = [ur.user_id for ur in user_roles]
    if not user_ids:
        return []
    all_users = User.objects.filter(deleted_at__isnull=True).all()
    return [u for u in all_users if u.id in user_ids]


def seed_default_permissions() -> None:
    from codesandbox.shared.permissions import get_registered_platform_permissions
    registered = {key: (label, group) for key, label, group in get_registered_platform_permissions()}

    for perm in PlatformPermission.objects.all():
        if perm.key not in registered:
            PlatformRolePermission.objects.filter(permission_id=perm.id).delete()
            perm.delete()

    for key, (label, group) in registered.items():
        if not PlatformPermission.objects.filter(key=key).first():
            perm = PlatformPermission(
                id=str(uuid.uuid4()),
                key=key,
                label=label,
                group=group,
            )
            perm.save()


def seed_default_roles() -> None:
    # Application ownership and platform-staff status live on User.platform_role.
    # These legacy role rows duplicated that authority and made built-in roles
    # look non-deletable in the RBAC editor, so seed now only cleans them up.
    legacy_role_ids = {"system_admin", "system_staff"}
    for role in PlatformRole.objects.all():
        if role.name in legacy_role_ids:
            role.delete()
            continue
        if role.is_system:
            role.is_system = False
            role.save()


def get_application_owner() -> User | None:
    owners = [
        u for u in User.objects.filter(platform_role="system_admin", deleted_at__isnull=True).all()
        if u.status == "active"
    ]
    if not owners:
        owners = User.objects.filter(platform_role="system_admin", deleted_at__isnull=True).all()
    owners.sort(key=lambda u: u.created_at)
    return owners[0] if owners else None


def list_application_owner_candidates(current_owner_id: str) -> list[User]:
    users = [
        u for u in User.objects.filter(deleted_at__isnull=True).all()
        if str(u.id) != str(current_owner_id) and u.status == "active"
    ]
    users.sort(key=lambda u: (u.name.lower(), u.email.lower()))
    return users


def transfer_application_ownership(current_owner_id: str, new_owner_id: str) -> str | None:
    current_owner = find_user_by_id(current_owner_id)
    if not current_owner or current_owner.platform_role != "system_admin":
        return "Only the current application owner can transfer ownership."
    new_owner = find_user_by_id(new_owner_id)
    if not new_owner or new_owner.deleted_at is not None:
        return "New owner not found."
    if new_owner.status != "active":
        return "New owner must be an active user."
    if str(new_owner.id) == str(current_owner.id):
        return "Choose a different user as the new owner."

    for owner in User.objects.filter(platform_role="system_admin", deleted_at__isnull=True).all():
        if str(owner.id) != str(new_owner.id):
            owner.platform_role = "system_staff"
            owner.save()
    new_owner.platform_role = "system_admin"
    new_owner.save()
    return None
