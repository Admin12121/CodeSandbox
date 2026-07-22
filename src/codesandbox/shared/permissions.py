from __future__ import annotations

from codesandbox.features.identity.models import User
from codesandbox.features.platform_admin import repository as rbac_repo

# ── Permission registries ─────────────────────────────────────────────────────
# Feature modules call register_org_permission / register_platform_permission at
# import time. The repositories read from these lists to seed the DB.

_ORG_REGISTRY: list[tuple[str, str, str]] = []
_PLATFORM_REGISTRY: list[tuple[str, str, str]] = []


def register_org_permission(key: str, label: str, group: str) -> None:
    if not any(k == key for k, _, _ in _ORG_REGISTRY):
        _ORG_REGISTRY.append((key, label, group))


def get_registered_org_permissions() -> list[tuple[str, str, str]]:
    return list(_ORG_REGISTRY)


def register_platform_permission(key: str, label: str, group: str) -> None:
    if not any(k == key for k, _, _ in _PLATFORM_REGISTRY):
        _PLATFORM_REGISTRY.append((key, label, group))


def get_registered_platform_permissions() -> list[tuple[str, str, str]]:
    return list(_PLATFORM_REGISTRY)


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
