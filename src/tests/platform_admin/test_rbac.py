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


"""platform_role='system_admin' marks the application owner and grants every permission."""
def test_application_owner_all_permissions(ctx: TestContext) -> None:
    user = _make_user(ctx, "sysadm")
    _id_repo().update_user(str(user.id), platform_role="system_admin")
    _seed()
    all_perms = _pa().list_permissions()
    keys = _pa().get_user_permission_keys(str(user.id))
    assert len(keys) == len(all_perms), (
        f"application owner should get all {len(all_perms)} permissions, got {len(keys)}"
    )


"""seed_default_roles removes legacy built-in role rows instead of recreating them."""
def test_seed_removes_legacy_default_roles(ctx: TestContext) -> None:
    _pa().seed_default_roles()
    admin = _pa().create_role(name="system_admin", color="#ef4444", is_system=True)
    staff = _pa().create_role(name="system_staff", color="#f59e0b", is_system=True)
    custom = _pa().create_role(name=unique("mutable_system"), is_system=True)
    ctx.defer(lambda rid=str(admin.id): _pa().delete_role(rid))
    ctx.defer(lambda rid=str(staff.id): _pa().delete_role(rid))
    ctx.defer(lambda rid=str(custom.id): _pa().delete_role(rid))

    _pa().seed_default_roles()

    assert _pa().get_role(str(admin.id)) is None
    assert _pa().get_role(str(staff.id)) is None
    custom_after = _pa().get_role(str(custom.id))
    assert custom_after is not None
    assert custom_after.is_system is False


"""Application ownership transfer moves the owner flag and permission bypass."""
def test_transfer_application_ownership(ctx: TestContext) -> None:
    from codesandbox.features.identity.models import User
    from codesandbox.features.platform_admin import service

    previous_owner_ids = [
        str(u.id)
        for u in User.objects.filter(platform_role="system_admin", deleted_at__isnull=True).all()
    ]
    ctx.defer(lambda ids=previous_owner_ids: [
        _id_repo().update_user(uid, platform_role="system_admin")
        for uid in ids
        if _id_repo().find_user_by_id(uid)
    ])

    owner = _make_user(ctx, "appowner")
    target = _make_user(ctx, "newowner")
    _id_repo().update_user(str(owner.id), platform_role="system_admin")
    _id_repo().update_user(str(target.id), platform_role="user")
    _seed()

    err = service.transfer_application_ownership(str(owner.id), str(target.id))
    assert err is None

    owner_after = _id_repo().find_user_by_id(str(owner.id))
    target_after = _id_repo().find_user_by_id(str(target.id))
    assert owner_after is not None and owner_after.platform_role == "system_staff"
    assert target_after is not None and target_after.platform_role == "system_admin"
    all_keys = {p.key for p in _pa().list_permissions()}
    assert _pa().get_user_permission_keys(str(target.id)) == all_keys
    assert _pa().get_user_permission_keys(str(owner.id)) == set()

    staff_rows = service.get_platform_staff()
    target_row = next((row for row in staff_rows if str(row["id"]) == str(target.id)), None)
    assert target_row is not None
    assert target_row["is_application_owner"] is True
    assert target_row["role_label"] == "Application Owner"


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


def test_staff_cannot_assign_equal_or_higher_platform_role(ctx: TestContext) -> None:
    from codesandbox.features.platform_admin import service

    actor = _make_user(ctx, "ph_actor")
    target = _make_user(ctx, "ph_target")
    _id_repo().update_user(str(actor.id), platform_role="system_staff")
    _id_repo().update_user(str(target.id), platform_role="system_staff")

    actor_role = _pa().create_role(name=unique("actor_high"))
    peer_role = _pa().create_role(name=unique("peer_high"))
    ctx.defer(lambda rid=str(actor_role.id): _pa().delete_role(rid))
    ctx.defer(lambda rid=str(peer_role.id): _pa().delete_role(rid))
    actor_role.position = "80"
    actor_role.save()
    peer_role.position = "80"
    peer_role.save()

    _pa().assign_role_to_user(str(actor.id), str(actor_role.id), granted_by=None)

    err = service.add_role_member(str(peer_role.id), str(target.id), granted_by=str(actor.id))
    assert err is not None
    assert "equal to or higher" in err


def test_staff_cannot_manage_equal_platform_member(ctx: TestContext) -> None:
    from codesandbox.features.platform_admin import service

    actor = _make_user(ctx, "pm_actor")
    target = _make_user(ctx, "pm_target")
    _id_repo().update_user(str(actor.id), platform_role="system_staff")
    _id_repo().update_user(str(target.id), platform_role="system_staff")

    high_role = _pa().create_role(name=unique("highrole"))
    low_role = _pa().create_role(name=unique("lowrole"))
    ctx.defer(lambda rid=str(high_role.id): _pa().delete_role(rid))
    ctx.defer(lambda rid=str(low_role.id): _pa().delete_role(rid))
    high_role.position = "80"
    high_role.save()
    low_role.position = "10"
    low_role.save()

    _pa().assign_role_to_user(str(actor.id), str(high_role.id), granted_by=None)
    _pa().assign_role_to_user(str(target.id), str(high_role.id), granted_by=None)
    _pa().assign_role_to_user(str(target.id), str(low_role.id), granted_by=None)

    err = service.remove_role_member(str(low_role.id), str(target.id), actor_user_id=str(actor.id))
    assert err is not None
    assert "staff member equal to or higher" in err


TESTS: list[TestCase] = [
    TestCase("create platform role",                  "platform_admin", test_create_platform_role),
    TestCase("set role permissions",                  "platform_admin", test_set_role_permissions),
    TestCase("application owner all permissions",     "platform_admin", test_application_owner_all_permissions),
    TestCase("seed removes legacy default roles",     "platform_admin", test_seed_removes_legacy_default_roles),
    TestCase("transfer application ownership",        "platform_admin", test_transfer_application_ownership),
    TestCase("custom role assigned keys",             "platform_admin", test_custom_role_assigned_keys),
    TestCase("delete role",                           "platform_admin", test_delete_role),
    TestCase("seed removes orphaned perms",           "platform_admin", test_seed_removes_orphaned_permissions),
    TestCase("set permissions replaces previous",     "platform_admin", test_set_role_permissions_replaces_previous),
    TestCase("perms cleaned on role delete",          "platform_admin", test_role_permissions_cleaned_on_role_delete),
    TestCase("cannot assign peer platform role",      "platform_admin", test_staff_cannot_assign_equal_or_higher_platform_role),
    TestCase("cannot manage peer platform member",    "platform_admin", test_staff_cannot_manage_equal_platform_member),
]
