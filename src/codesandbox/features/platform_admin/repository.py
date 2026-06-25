from __future__ import annotations

import uuid

from nexorm.exceptions import DoesNotExist

from codesandbox.features.identity.models import User
from codesandbox.features.identity.repository import find_user_by_id

from .models import PlatformPermission, PlatformRole, PlatformRolePermission, PlatformUserRole


def list_roles() -> list[PlatformRole]:
    return PlatformRole.objects.all()


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
    for key, label, group in get_registered_platform_permissions():
        if not PlatformPermission.objects.filter(key=key).first():
            perm = PlatformPermission(
                id=str(uuid.uuid4()),
                key=key,
                label=label,
                group=group,
            )
            perm.save()


def seed_default_roles() -> None:
    # Fix any pre-existing locked roles so they become fully editable
    for role in PlatformRole.objects.filter(is_system=True).all():
        role.is_system = False
        role.save()

    if not get_role_by_name("system_admin"):
        create_role(name="system_admin", color="#ef4444", description="Full platform access")
    if not get_role_by_name("system_staff"):
        create_role(name="system_staff", color="#f59e0b", description="Platform staff access")
