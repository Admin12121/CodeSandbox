from __future__ import annotations

from flask import g

from codesandbox.features.identity.models import User

# ── Permission registries ─────────────────────────────────────────────────────
# Feature __init__.py files call register_*_permission() at import time.
# Seeder functions read from these lists to sync the DB.

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


# ── Per-request caches (flask.g) ─────────────────────────────────────────────
# One DB query per user/org per request. Subsequent checks hit the frozenset.

def _platform_perm_cache(user_id: str) -> frozenset[str]:
    from codesandbox.features.platform_admin import repository as rbac_repo
    cache: dict = g.setdefault("_pp", {})
    if user_id not in cache:
        cache[user_id] = frozenset(rbac_repo.get_user_permission_keys(user_id))
    return cache[user_id]


def _org_perm_cache(org_id: str, user_id: str) -> frozenset[str]:
    from codesandbox.features.organizations import repository as org_repo
    cache: dict = g.setdefault("_op", {})
    key = (org_id, user_id)
    if key not in cache:
        cache[key] = frozenset(org_repo.get_member_permissions(org_id, user_id))
    return cache[key]


# ── Resolvers ─────────────────────────────────────────────────────────────────

def has_platform_permission(user: User, permission_key: str) -> bool:
    if user.platform_role == "system_admin":
        return True
    return permission_key in _platform_perm_cache(str(user.id))


def has_any_platform_permission(user: User, *permission_keys: str) -> bool:
    return any(has_platform_permission(user, k) for k in permission_keys)


def has_org_permission(org_id: str, user: User, permission_key: str) -> bool:
    from codesandbox.features.organizations import repository as org_repo
    if org_repo.is_org_owner(org_id, str(user.id)):
        return True
    return permission_key in _org_perm_cache(org_id, str(user.id))


def is_platform_staff(user: User) -> bool:
    return user.platform_role in ("system_staff", "system_admin")


def is_platform_admin(user: User) -> bool:
    return user.platform_role == "system_admin"
