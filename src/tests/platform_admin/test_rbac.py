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


def _make_user(ctx: TestContext, prefix: str = "rbac") -> object:
    email = unique(prefix) + "@test.local"
    r = _id_svc().sign_up(
        name=prefix, email=email, password="password123",
        ip_address=None, user_agent=None,
    )
    assert r.ok, f"Failed to create user: {r.message}"
    user = _id_repo().find_user_by_email(email)
    assert user is not None
    ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    return user


def _seed():
    from codesandbox.features.platform_admin.repository import seed_default_permissions
    seed_default_permissions()



"""create_role produces a persisted role that appears in list_roles."""
def test_create_platform_role(ctx: TestContext) -> None:
    role_name = unique("role")
    role = _pa().create_role(name=role_name, color="#ff0000")
    ctx.defer(lambda rid=str(role.id): _pa().delete_role(rid))
    assert role is not None
    assert role.name == role_name
    assert any(str(r.id) == str(role.id) for r in _pa().list_roles())


"""set_role_permissions assigns exactly the given permissions to a role."""
def test_set_role_permissions(ctx: TestContext) -> None:
    role = _pa().create_role(name=unique("permrole"))
    ctx.defer(lambda rid=str(role.id): _pa().delete_role(rid))
    _seed()
    all_perms = _pa().list_permissions()
    assert len(all_perms) > 0

    _pa().set_role_permissions(str(role.id), [all_perms[0].id])
    assigned = _pa().get_permissions_for_role(str(role.id))
    assert len(assigned) == 1
    assert str(assigned[0].id) == str(all_perms[0].id)


"""platform_role='system_admin' grants every seeded permission — no gaps."""
def test_system_admin_all_permissions(ctx: TestContext) -> None:
    user = _make_user(ctx, "sysadm")
    _id_repo().update_user(str(user.id), platform_role="system_admin")
    _seed()
    all_perms = _pa().list_permissions()
    keys = _pa().get_user_permission_keys(str(user.id))
    assert len(keys) == len(all_perms), (
        f"system_admin should get all {len(all_perms)} permissions, got {len(keys)}"
    )


"""A user with a custom role receives only the keys assigned to that role."""
def test_custom_role_assigned_keys(ctx: TestContext) -> None:
    from codesandbox.features.platform_admin.models import PlatformUserRole

    user = _make_user(ctx, "custrl")
    role = _pa().create_role(name=unique("customrole"))
    ctx.defer(lambda rid=str(role.id): _pa().delete_role(rid))
    _seed()
    all_perms = _pa().list_permissions()
    assert len(all_perms) >= 2

    selected_ids = [all_perms[0].id, all_perms[1].id]
    _pa().set_role_permissions(str(role.id), selected_ids)

    pur = PlatformUserRole(id=str(uuid.uuid4()), user_id=str(user.id), role_id=str(role.id))
    pur.save()
    ctx.defer(lambda p=pur: p.delete())

    keys = _pa().get_user_permission_keys(str(user.id))
    assert all_perms[0].key in keys
    assert all_perms[1].key in keys


"""delete_role removes the role row and returns True."""
def test_delete_role(ctx: TestContext) -> None:
    role = _pa().create_role(name=unique("delrole"))
    role_id = str(role.id)
    ok = _pa().delete_role(role_id)
    assert ok
    assert _pa().get_role(role_id) is None


"""seed_default_permissions cleans up any permissions not in the canonical set."""
def test_seed_removes_orphaned_permissions(ctx: TestContext) -> None:
    from codesandbox.features.platform_admin.models import PlatformPermission

    orphan_key = unique("orphan_perm")
    orphan = PlatformPermission(
        id=str(uuid.uuid4()),
        key=orphan_key,
        label="Orphan Test Perm",
        group="test",
    )
    orphan.save()
    orphan_id = str(orphan.id)

    _seed()

    remaining = _pa().list_permissions()
    assert all(str(p.id) != orphan_id for p in remaining), (
        "seed_default_permissions must remove orphaned permissions"
    )


"""set_role_permissions is a replace — calling it again removes prior assignments."""
def test_set_role_permissions_replaces_previous(ctx: TestContext) -> None:
    role = _pa().create_role(name=unique("replrole"))
    ctx.defer(lambda rid=str(role.id): _pa().delete_role(rid))
    _seed()
    perms = _pa().list_permissions()
    assert len(perms) >= 2

    # Assign perm[0] first
    _pa().set_role_permissions(str(role.id), [str(perms[0].id)])
    keys_first = {p.key for p in _pa().get_permissions_for_role(str(role.id))}
    assert perms[0].key in keys_first

    # Replace with perm[1] only
    _pa().set_role_permissions(str(role.id), [str(perms[1].id)])
    keys_second = {p.key for p in _pa().get_permissions_for_role(str(role.id))}

    assert perms[1].key in keys_second, "New permission must be present after replace"
    assert perms[0].key not in keys_second, "Old permission must be gone after replace"


"""Deleting a role leaves no orphaned permission assignments behind."""
def test_role_permissions_cleaned_on_role_delete(ctx: TestContext) -> None:
    role = _pa().create_role(name=unique("pclean"))
    _seed()
    perms = _pa().list_permissions()
    assert len(perms) > 0

    _pa().set_role_permissions(str(role.id), [str(perms[0].id)])
    assert len(_pa().get_permissions_for_role(str(role.id))) > 0

    role_id = str(role.id)
    _pa().delete_role(role_id)

    assert _pa().get_role(role_id) is None
    assert len(_pa().get_permissions_for_role(role_id)) == 0, (
        "Deleted role must have no residual permission assignments"
    )


TESTS: list[TestCase] = [
    TestCase("create platform role",                  "platform_admin", test_create_platform_role),
    TestCase("set role permissions",                  "platform_admin", test_set_role_permissions),
    TestCase("system admin all permissions",          "platform_admin", test_system_admin_all_permissions),
    TestCase("custom role assigned keys",             "platform_admin", test_custom_role_assigned_keys),
    TestCase("delete role",                           "platform_admin", test_delete_role),
    TestCase("seed removes orphaned perms",           "platform_admin", test_seed_removes_orphaned_permissions),
    TestCase("set permissions replaces previous",     "platform_admin", test_set_role_permissions_replaces_previous),
    TestCase("perms cleaned on role delete",          "platform_admin", test_role_permissions_cleaned_on_role_delete),
]
