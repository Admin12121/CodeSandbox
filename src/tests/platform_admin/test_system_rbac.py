from __future__ import annotations

import uuid

from tests._context import TestCase, TestContext, unique


def _pa():
    from codesandbox.features.platform_admin import repository
    return repository


def _id_svc():
    from codesandbox.features.identity import service
    return service


def _id_repo():
    from codesandbox.features.identity import repository
    return repository


def _seed():
    from codesandbox.features.platform_admin.repository import seed_default_permissions
    seed_default_permissions()


def _cleanup_user(user_id: str) -> None:
    repo = _id_repo()
    user = repo.find_user_by_id(user_id)
    if not user:
        return
    for s in repo.list_user_sessions(user_id):
        try:
            s.delete()
        except Exception:
            pass
    try:
        user.delete()
    except Exception:
        pass


def _make_user(ctx: TestContext, prefix: str = "u") -> object:
    email = unique(prefix) + "@test.local"
    r = _id_svc().sign_up(
        name=prefix, email=email, password="password123",
        ip_address=None, user_agent=None,
    )
    assert r.ok, f"sign_up failed: {r.message}"
    user = _id_repo().find_user_by_email(email)
    assert user is not None
    ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    return user


def _make_role(ctx: TestContext, prefix: str = "r") -> object:
    role = _pa().create_role(name=unique(prefix))
    ctx.defer(lambda rid=str(role.id): _pa().delete_role(rid))
    return role


def _give_role(ctx: TestContext, user_id: str, role_id: str) -> object:
    from codesandbox.features.platform_admin.models import PlatformUserRole
    pur = PlatformUserRole(id=str(uuid.uuid4()), user_id=user_id, role_id=role_id)
    pur.save()
    ctx.defer(lambda p=pur: p.delete())
    return pur


"""Default user with no PlatformRole assigned has an empty permission keyset."""
def test_no_role_no_permissions(ctx: TestContext) -> None:
    user = _make_user(ctx, "nrnp")
    keys = _pa().get_user_permission_keys(str(user.id))
    assert len(keys) == 0, f"Expected empty, got {keys}"


"""User gets exactly the permissions seeded into their assigned role."""
def test_assigned_role_grants_its_permissions(ctx: TestContext) -> None:
    _seed()
    user = _make_user(ctx, "argp")
    role = _make_role(ctx, "argpr")
    perm = _pa().list_permissions()[0]

    _pa().set_role_permissions(str(role.id), [str(perm.id)])
    _give_role(ctx, str(user.id), str(role.id))

    keys = _pa().get_user_permission_keys(str(user.id))
    assert perm.key in keys


"""A role that has perm A does NOT grant perm B to its holder."""
def test_unassigned_permission_absent_from_keyset(ctx: TestContext) -> None:
    _seed()
    user = _make_user(ctx, "upak")
    role = _make_role(ctx, "upakr")
    perms = _pa().list_permissions()
    assert len(perms) >= 2, "Need at least 2 seeded permissions"

    perm_a, perm_b = perms[0], perms[1]
    _pa().set_role_permissions(str(role.id), [str(perm_a.id)])
    _give_role(ctx, str(user.id), str(role.id))

    keys = _pa().get_user_permission_keys(str(user.id))
    assert perm_a.key in keys,     "Assigned perm must be present"
    assert perm_b.key not in keys, "Unassigned perm must be absent"


"""Two users with different roles never see each other's permissions."""
def test_role_permissions_dont_bleed_across_users(ctx: TestContext) -> None:
    _seed()
    user_a = _make_user(ctx, "blda")
    user_b = _make_user(ctx, "bldb")
    role_a = _make_role(ctx, "bldr_a")
    role_b = _make_role(ctx, "bldr_b")
    perms  = _pa().list_permissions()
    assert len(perms) >= 2

    _pa().set_role_permissions(str(role_a.id), [str(perms[0].id)])
    _pa().set_role_permissions(str(role_b.id), [str(perms[1].id)])
    _give_role(ctx, str(user_a.id), str(role_a.id))
    _give_role(ctx, str(user_b.id), str(role_b.id))

    keys_a = _pa().get_user_permission_keys(str(user_a.id))
    keys_b = _pa().get_user_permission_keys(str(user_b.id))

    assert perms[1].key not in keys_a, "user_a must not see user_b's role permission"
    assert perms[0].key not in keys_b, "user_b must not see user_a's role permission"


"""User with two roles gets exactly the union — no permissions beyond those two roles."""
def test_multiple_roles_union_no_extra(ctx: TestContext) -> None:
    _seed()
    user   = _make_user(ctx, "mrun")
    role_a = _make_role(ctx, "mrun_a")
    role_b = _make_role(ctx, "mrun_b")
    perms  = _pa().list_permissions()
    assert len(perms) >= 3

    _pa().set_role_permissions(str(role_a.id), [str(perms[0].id)])
    _pa().set_role_permissions(str(role_b.id), [str(perms[1].id)])
    _give_role(ctx, str(user.id), str(role_a.id))
    _give_role(ctx, str(user.id), str(role_b.id))

    keys = _pa().get_user_permission_keys(str(user.id))
    assert perms[0].key in keys, "perm from role_a must be present"
    assert perms[1].key in keys, "perm from role_b must be present"
    assert perms[2].key not in keys, "perm not in either role must be absent"


"""Removing a permission from a role immediately removes it from the user's keyset."""
def test_revoke_permission_from_role_removes_access(ctx: TestContext) -> None:
    _seed()
    user = _make_user(ctx, "rprm")
    role = _make_role(ctx, "rprmr")
    perm = _pa().list_permissions()[0]

    _pa().set_role_permissions(str(role.id), [str(perm.id)])
    _give_role(ctx, str(user.id), str(role.id))
    assert perm.key in _pa().get_user_permission_keys(str(user.id))

    _pa().set_role_permissions(str(role.id), [])   # remove all perms from role
    keys_after = _pa().get_user_permission_keys(str(user.id))
    assert perm.key not in keys_after, "Revoked perm must not persist in user's keyset"


"""Removing the PlatformUserRole row immediately loses that role's permissions."""
def test_revoke_role_from_user_removes_permissions(ctx: TestContext) -> None:
    _seed()
    user = _make_user(ctx, "rrup")
    role = _make_role(ctx, "rrupr")
    perm = _pa().list_permissions()[0]
    _pa().set_role_permissions(str(role.id), [str(perm.id)])

    from codesandbox.features.platform_admin.models import PlatformUserRole
    pur = PlatformUserRole(id=str(uuid.uuid4()), user_id=str(user.id), role_id=str(role.id))
    pur.save()
    assert perm.key in _pa().get_user_permission_keys(str(user.id))

    pur.delete()
    assert perm.key not in _pa().get_user_permission_keys(str(user.id)), (
        "User must lose permissions when their role is revoked"
    )


"""User with platform_role='system_admin' receives every seeded permission."""
def test_system_admin_field_grants_all_permissions(ctx: TestContext) -> None:
    _seed()
    user = _make_user(ctx, "safg")
    _id_repo().update_user(str(user.id), platform_role="system_admin")
    all_perms = _pa().list_permissions()
    keys = _pa().get_user_permission_keys(str(user.id))
    missing = [p.key for p in all_perms if p.key not in keys]
    assert not missing, f"system_admin missing: {missing}"


"""A regular user with one assigned permission gets exactly that one — no escalation."""
def test_non_admin_cannot_exceed_role_perms(ctx: TestContext) -> None:
    _seed()
    user  = _make_user(ctx, "nace")
    role  = _make_role(ctx, "nacer")
    perms = _pa().list_permissions()
    assert len(perms) >= 2

    _pa().set_role_permissions(str(role.id), [str(perms[0].id)])
    _give_role(ctx, str(user.id), str(role.id))

    keys = _pa().get_user_permission_keys(str(user.id))
    # Exactly {perms[0].key} — no other platform permission crept in
    assert keys == {perms[0].key}, (
        f"Expected exactly {{{perms[0].key}}}, got {keys}"
    )


TESTS: list[TestCase] = [
    TestCase("no role — no permissions",               "system_rbac", test_no_role_no_permissions),
    TestCase("assigned role grants its permissions",   "system_rbac", test_assigned_role_grants_its_permissions),
    TestCase("unassigned perm absent from keyset",     "system_rbac", test_unassigned_permission_absent_from_keyset),
    TestCase("permissions don't bleed across users",   "system_rbac", test_role_permissions_dont_bleed_across_users),
    TestCase("multiple roles — exact union no extra",  "system_rbac", test_multiple_roles_union_no_extra),
    TestCase("revoke perm from role — loses access",   "system_rbac", test_revoke_permission_from_role_removes_access),
    TestCase("revoke role from user — loses perms",    "system_rbac", test_revoke_role_from_user_removes_permissions),
    TestCase("system_admin field grants all perms",    "system_rbac", test_system_admin_field_grants_all_permissions),
    TestCase("non-admin cannot exceed role perms",     "system_rbac", test_non_admin_cannot_exceed_role_perms),
]
