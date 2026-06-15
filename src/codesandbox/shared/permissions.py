from __future__ import annotations

from codesandbox.features.identity.models import User
from codesandbox.features.platform_admin import repository as rbac_repo


def has_platform_permission(user: User, permission_key: str) -> bool:
    if user.platform_role == "system_admin":
        return True
    keys = rbac_repo.get_user_permission_keys(user.id)
    return permission_key in keys


def has_any_platform_permission(user: User, *permission_keys: str) -> bool:
    return any(has_platform_permission(user, k) for k in permission_keys)


def is_platform_staff(user: User) -> bool:
    return user.platform_role in ("system_staff", "system_admin")


def is_platform_admin(user: User) -> bool:
    return user.platform_role == "system_admin"
