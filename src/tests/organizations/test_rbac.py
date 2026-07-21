from __future__ import annotations

import uuid

from tests._context import TestCase, TestContext, unique


def _id_svc():
    from codesandbox.features.identity import service
    return service


def _id_repo():
    from codesandbox.features.identity import repository
    return repository


def _org():
    from codesandbox.features.organizations import repository
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


def _make_user(ctx: TestContext, prefix: str = "u") -> object:
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


def _make_org(ctx: TestContext, owner) -> object:
    org = _org().create_organization(name=unique("Org"), created_by=str(owner.id))
    _org().seed_org_roles(str(org.id))
    ctx.defer(lambda oid=str(org.id): _org().delete_organization(oid))
    return org


def _seed_perms() -> list:
    _org().ensure_org_permissions_seeded()
    return _org().get_all_org_permissions()



def test_assign_role_to_member(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "aro")
    member = _make_user(ctx, "arm")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(member.id))

    roles = _org().list_org_roles(str(org.id))
    admin_role = next(r for r in roles if r.name == "admin")
    m = _org().get_member(str(org.id), str(member.id))

    ok = _org().assign_role_to_member(str(m.id), str(admin_role.id))
    assert ok

    from codesandbox.features.organizations.models import OrganizationMemberRole
    assigned = OrganizationMemberRole.objects.filter(member_id=str(m.id), role_id=str(admin_role.id)).first()
    assert assigned is not None


def test_assign_role_idempotent(ctx: TestContext) -> None:
    owner = _make_user(ctx, "aidm")
    org   = _make_org(ctx, owner)
    roles = _org().list_org_roles(str(org.id))
    role  = roles[0]
    m     = _org().get_member(str(org.id), str(owner.id))

    r1 = _org().assign_role_to_member(str(m.id), str(role.id))
    r2 = _org().assign_role_to_member(str(m.id), str(role.id))
    assert r1 and r2, "Assigning same role twice must not fail"

    from codesandbox.features.organizations.models import OrganizationMemberRole
    rows = OrganizationMemberRole.objects.filter(member_id=str(m.id), role_id=str(role.id)).all()
    assert len(rows) == 1, "Must not create duplicate assignment rows"


def test_remove_role_from_member(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "rrm")
    member = _make_user(ctx, "rrmm")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(member.id))

    roles = _org().list_org_roles(str(org.id))
    role  = next(r for r in roles if r.name == "member")
    m     = _org().get_member(str(org.id), str(member.id))

    _org().assign_role_to_member(str(m.id), str(role.id))
    _org().remove_role_from_member(str(m.id), str(role.id))

    from codesandbox.features.organizations.models import OrganizationMemberRole
    row = OrganizationMemberRole.objects.filter(member_id=str(m.id), role_id=str(role.id)).first()
    assert row is None, "Role assignment should be gone after removal"


def test_set_org_role_permission_enable(ctx: TestContext) -> None:
    owner = _make_user(ctx, "srpe")
    org   = _make_org(ctx, owner)
    perms = _seed_perms()
    roles = _org().list_org_roles(str(org.id))
    role  = roles[0]
    pkey  = perms[0].key

    ok = _org().set_org_role_permission(str(role.id), pkey, enabled=True)
    assert ok

    assigned_keys = _org().get_permissions_for_org_role(str(role.id))
    assert pkey in assigned_keys


def test_set_org_role_permission_disable(ctx: TestContext) -> None:
    owner = _make_user(ctx, "srpd")
    org   = _make_org(ctx, owner)
    perms = _seed_perms()
    roles = _org().list_org_roles(str(org.id))
    role  = roles[0]
    pkey  = perms[0].key

    _org().set_org_role_permission(str(role.id), pkey, enabled=True)
    _org().set_org_role_permission(str(role.id), pkey, enabled=False)

    assigned_keys = _org().get_permissions_for_org_role(str(role.id))
    assert pkey not in assigned_keys


def test_get_member_permissions_via_role(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "gmpv")
    member = _make_user(ctx, "gmpm")
    org    = _make_org(ctx, owner)
    perms  = _seed_perms()
    _org().add_member(str(org.id), str(member.id))

    roles = _org().list_org_roles(str(org.id))
    role  = next(r for r in roles if r.name == "admin")
    pkey  = perms[0].key

    _org().set_org_role_permission(str(role.id), pkey, enabled=True)
    m = _org().get_member(str(org.id), str(member.id))
    _org().assign_role_to_member(str(m.id), str(role.id))

    keys = _org().get_member_permissions(str(org.id), str(member.id))
    assert pkey in keys


def test_get_member_permissions_revoked_after_role_removal(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "gmpr")
    member = _make_user(ctx, "gmpru")
    org    = _make_org(ctx, owner)
    perms  = _seed_perms()
    _org().add_member(str(org.id), str(member.id))

    roles = _org().list_org_roles(str(org.id))
    role  = next(r for r in roles if r.name == "admin")
    pkey  = perms[0].key

    _org().set_org_role_permission(str(role.id), pkey, enabled=True)
    m = _org().get_member(str(org.id), str(member.id))
    _org().assign_role_to_member(str(m.id), str(role.id))

    # Confirm permission is active
    assert pkey in _org().get_member_permissions(str(org.id), str(member.id))

    # Remove role → permission should disappear
    _org().remove_role_from_member(str(m.id), str(role.id))
    keys_after = _org().get_member_permissions(str(org.id), str(member.id))
    assert pkey not in keys_after


def test_get_member_highest_position(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "gmhp")
    member = _make_user(ctx, "gmhpm")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(member.id))

    roles      = _org().list_org_roles(str(org.id))
    admin_role = next(r for r in roles if r.name == "admin")   # position 80
    member_role= next(r for r in roles if r.name == "member")  # position 10
    m = _org().get_member(str(org.id), str(member.id))

    _org().assign_role_to_member(str(m.id), str(member_role.id))
    pos_after_member = _org().get_member_highest_position(str(org.id), str(member.id))
    assert pos_after_member == 10

    _org().assign_role_to_member(str(m.id), str(admin_role.id))
    pos_after_admin = _org().get_member_highest_position(str(org.id), str(member.id))
    assert pos_after_admin == 80, "Should return the highest position across all roles"


def test_owner_highest_position_is_maxsize(ctx: TestContext) -> None:
    import sys as _sys
    owner = _make_user(ctx, "ohpm")
    org   = _make_org(ctx, owner)
    pos   = _org().get_member_highest_position(str(org.id), str(owner.id))
    assert pos == _sys.maxsize, "Owner must have maxsize position"


def test_can_actor_manage_role_owner(ctx: TestContext) -> None:
    owner = _make_user(ctx, "camro")
    org   = _make_org(ctx, owner)
    roles = _org().list_org_roles(str(org.id))
    role  = roles[0]
    assert _org().can_actor_manage_role(str(org.id), str(owner.id), str(role.id))


def test_can_actor_manage_role_higher_position(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "camhp")
    actor  = _make_user(ctx, "camha")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(actor.id))

    roles       = _org().list_org_roles(str(org.id))
    admin_role  = next(r for r in roles if r.name == "admin")   # position 80
    member_role = next(r for r in roles if r.name == "member")  # position 10

    # Give actor the admin role (position 80) → can manage member role (position 10)
    m = _org().get_member(str(org.id), str(actor.id))
    _org().assign_role_to_member(str(m.id), str(admin_role.id))

    assert _org().can_actor_manage_role(str(org.id), str(actor.id), str(member_role.id))


def test_can_actor_manage_role_lower_position_blocked(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "camlb")
    actor  = _make_user(ctx, "camla")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(actor.id))

    roles       = _org().list_org_roles(str(org.id))
    admin_role  = next(r for r in roles if r.name == "admin")   # position 80
    member_role = next(r for r in roles if r.name == "member")  # position 10

    # Give actor the member role (position 10) → cannot manage admin role (position 80)
    m = _org().get_member(str(org.id), str(actor.id))
    _org().assign_role_to_member(str(m.id), str(member_role.id))

    assert not _org().can_actor_manage_role(str(org.id), str(actor.id), str(admin_role.id))


def test_is_org_owner(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "isoo")
    other  = _make_user(ctx, "isou")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(other.id))

    assert _org().is_org_owner(str(org.id), str(owner.id))
    assert not _org().is_org_owner(str(org.id), str(other.id))


"""A member's role has perm P1 — they must not receive perm P2 from the same org."""
def test_unassigned_org_perm_absent_from_member(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "uopam")
    member = _make_user(ctx, "uopamm")
    org    = _make_org(ctx, owner)
    perms  = _seed_perms()
    assert len(perms) >= 2, "Need at least 2 org permissions"
    _org().add_member(str(org.id), str(member.id))

    roles = _org().list_org_roles(str(org.id))
    role  = next(r for r in roles if r.name == "admin")
    # Assign only perms[0] to the role
    _org().set_org_role_permission(str(role.id), perms[0].key, enabled=True)
    _org().set_org_role_permission(str(role.id), perms[1].key, enabled=False)

    m = _org().get_member(str(org.id), str(member.id))
    _org().assign_role_to_member(str(m.id), str(role.id))

    keys = _org().get_member_permissions(str(org.id), str(member.id))
    assert perms[0].key in keys,     "Assigned perm must be present"
    assert perms[1].key not in keys, "Unassigned perm must be absent"


"""Member A's role permissions never appear in member B's keyset and vice-versa."""
def test_permission_isolation_between_members(ctx: TestContext) -> None:
    owner    = _make_user(ctx, "pibm_o")
    member_a = _make_user(ctx, "pibm_a")
    member_b = _make_user(ctx, "pibm_b")
    org      = _make_org(ctx, owner)
    perms    = _seed_perms()
    assert len(perms) >= 2

    _org().add_member(str(org.id), str(member_a.id))
    _org().add_member(str(org.id), str(member_b.id))

    roles       = _org().list_org_roles(str(org.id))
    admin_role  = next(r for r in roles if r.name == "admin")
    member_role = next(r for r in roles if r.name == "member")

    # Give admin role perm[0], member role perm[1]
    _org().set_org_role_permission(str(admin_role.id),  perms[0].key, enabled=True)
    _org().set_org_role_permission(str(member_role.id), perms[1].key, enabled=True)

    ma = _org().get_member(str(org.id), str(member_a.id))
    mb = _org().get_member(str(org.id), str(member_b.id))
    # add_member assigns the default member role. Remove it from member A so
    # this test compares two disjoint role assignments.
    _org().remove_role_from_member(str(ma.id), str(member_role.id))
    _org().assign_role_to_member(str(ma.id), str(admin_role.id))
    _org().assign_role_to_member(str(mb.id), str(member_role.id))

    keys_a = _org().get_member_permissions(str(org.id), str(member_a.id))
    keys_b = _org().get_member_permissions(str(org.id), str(member_b.id))

    assert perms[1].key not in keys_a, "member_a must not receive member_b's permission"
    assert perms[0].key not in keys_b, "member_b must not receive member_a's permission"


"""Same user in two orgs: each org's permissions are independent."""
def test_permissions_scoped_to_org(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "psto")
    shared = _make_user(ctx, "psts")
    org_a  = _make_org(ctx, owner)
    org_b  = _make_org(ctx, owner)
    perms  = _seed_perms()
    assert len(perms) >= 2

    _org().add_member(str(org_a.id), str(shared.id))
    _org().add_member(str(org_b.id), str(shared.id))

    roles_a = _org().list_org_roles(str(org_a.id))
    roles_b = _org().list_org_roles(str(org_b.id))
    role_a  = next(r for r in roles_a if r.name == "admin")
    role_b  = next(r for r in roles_b if r.name == "member")

    # Org A grants perm[0], org B grants perm[1]
    _org().set_org_role_permission(str(role_a.id), perms[0].key, enabled=True)
    _org().set_org_role_permission(str(role_b.id), perms[1].key, enabled=True)

    ma = _org().get_member(str(org_a.id), str(shared.id))
    mb = _org().get_member(str(org_b.id), str(shared.id))
    _org().assign_role_to_member(str(ma.id), str(role_a.id))
    _org().assign_role_to_member(str(mb.id), str(role_b.id))

    keys_in_a = _org().get_member_permissions(str(org_a.id), str(shared.id))
    keys_in_b = _org().get_member_permissions(str(org_b.id), str(shared.id))

    assert perms[0].key in keys_in_a
    assert perms[1].key not in keys_in_a, "Org B's permission must not appear in org A's check"

    assert perms[1].key in keys_in_b
    assert perms[0].key not in keys_in_b, "Org A's permission must not appear in org B's check"


"""A user who is not a member of the org has no permissions in that org."""
def test_non_member_gets_no_permissions(ctx: TestContext) -> None:
    owner    = _make_user(ctx, "nmgnp_o")
    outsider = _make_user(ctx, "nmgnp_x")
    org      = _make_org(ctx, owner)
    _seed_perms()

    keys = _org().get_member_permissions(str(org.id), str(outsider.id))
    assert keys == [], f"Non-member must have no permissions, got {keys}"


"""A member whose highest position equals a role's position cannot manage that role."""
def test_same_position_cannot_manage_peer(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "spcmp_o")
    actor  = _make_user(ctx, "spcmp_a")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(actor.id))

    # Create a custom role at position 50
    custom_role = _org().create_org_role(str(org.id), name=unique("peer"), color="#aaaaaa", position=50)
    # Give actor that same role (position 50)
    m = _org().get_member(str(org.id), str(actor.id))
    _org().assign_role_to_member(str(m.id), str(custom_role.id))

    # Actor's highest position == 50, role's position == 50 → 50 > 50 is False
    result = _org().can_actor_manage_role(str(org.id), str(actor.id), str(custom_role.id))
    assert not result, "Same-position actor must not be able to manage a peer role"


"""toggle_org_role_permission rejects keys not in the registered permission set."""
def test_permission_key_injection_rejected(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import toggle_org_role_permission

    owner = _make_user(ctx, "pkir")
    org   = _make_org(ctx, owner)
    roles = _org().list_org_roles(str(org.id))
    role  = roles[0]

    ok, msg = toggle_org_role_permission(org.slug, str(role.id), "injected.fake.key", True, str(owner.id))
    assert not ok, "Unregistered permission key must be rejected"
    assert "invalid" in msg.lower()


"""Rule 3: a non-owner cannot enable a permission they don't currently hold."""
def test_member_cannot_grant_perm_they_dont_hold(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import toggle_org_role_permission

    owner = _make_user(ctx, "mcgp_o")
    actor = _make_user(ctx, "mcgp_a")
    org   = _make_org(ctx, owner)
    perms = _seed_perms()
    assert len(perms) >= 2

    _org().add_member(str(org.id), str(actor.id))

    roles       = _org().list_org_roles(str(org.id))
    actor_role  = next(r for r in roles if r.name == "admin")
    target_role = next(r for r in roles if r.name == "member")

    # Give actor org.roles.manage so they can call the endpoint, but NOT perms[1]
    _org().set_org_role_permission(str(actor_role.id), "org.roles.manage", enabled=True)
    m = _org().get_member(str(org.id), str(actor.id))
    _org().assign_role_to_member(str(m.id), str(actor_role.id))

    actor_keys = _org().get_member_permissions(str(org.id), str(actor.id))
    missing_perm = next((p for p in perms if p.key not in actor_keys), None)
    if missing_perm is None:
        return  # actor holds everything; scenario not constructible

    ok, _ = toggle_org_role_permission(
        org.slug, str(target_role.id), missing_perm.key, True, str(actor.id),
    )
    assert not ok, "Non-owner must not grant a permission they don't hold"


"""Rule 4: org.roles.manage is only grantable by the owner — never delegatable."""
def test_member_cannot_grant_org_roles_manage(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import toggle_org_role_permission

    owner = _make_user(ctx, "mcgrm_o")
    actor = _make_user(ctx, "mcgrm_a")
    org   = _make_org(ctx, owner)
    _seed_perms()

    _org().add_member(str(org.id), str(actor.id))

    roles       = _org().list_org_roles(str(org.id))
    actor_role  = next(r for r in roles if r.name == "admin")
    target_role = next(r for r in roles if r.name == "member")

    # Directly seed org.roles.manage onto actor's role (bypassing service rules for setup)
    _org().set_org_role_permission(str(actor_role.id), "org.roles.manage", enabled=True)
    m = _org().get_member(str(org.id), str(actor.id))
    _org().assign_role_to_member(str(m.id), str(actor_role.id))

    # Actor holds org.roles.manage themselves — still must not be able to grant it
    ok, msg = toggle_org_role_permission(
        org.slug, str(target_role.id), "org.roles.manage", True, str(actor.id),
    )
    assert not ok, "Non-owner must never be able to grant org.roles.manage"
    assert "owner" in msg.lower() or "grant" in msg.lower()


def test_member_cannot_remove_equal_position_member(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import remove_org_member

    owner = _make_user(ctx, "mcrp_o")
    actor = _make_user(ctx, "mcrp_a")
    target = _make_user(ctx, "mcrp_t")
    org = _make_org(ctx, owner)
    _seed_perms()
    _org().add_member(str(org.id), str(actor.id))
    _org().add_member(str(org.id), str(target.id))

    admin_role = next(r for r in _org().list_org_roles(str(org.id)) if r.name == "admin")
    _org().set_org_role_permission(str(admin_role.id), "org.members.remove", enabled=True)
    actor_member = _org().get_member(str(org.id), str(actor.id))
    target_member = _org().get_member(str(org.id), str(target.id))
    _org().assign_role_to_member(str(actor_member.id), str(admin_role.id))
    _org().assign_role_to_member(str(target_member.id), str(admin_role.id))

    ok, msg = remove_org_member(str(org.id), str(target_member.id), str(actor.id))
    assert not ok
    assert "equal to or higher" in msg


def test_member_cannot_remove_low_role_from_equal_position_member(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import remove_role_from_org_member

    owner = _make_user(ctx, "mcrl_o")
    actor = _make_user(ctx, "mcrl_a")
    target = _make_user(ctx, "mcrl_t")
    org = _make_org(ctx, owner)
    _seed_perms()
    _org().add_member(str(org.id), str(actor.id))
    _org().add_member(str(org.id), str(target.id))

    roles = _org().list_org_roles(str(org.id))
    admin_role = next(r for r in roles if r.name == "admin")
    member_role = next(r for r in roles if r.name == "member")
    _org().set_org_role_permission(str(admin_role.id), "org.roles.assign", enabled=True)
    actor_member = _org().get_member(str(org.id), str(actor.id))
    target_member = _org().get_member(str(org.id), str(target.id))
    _org().assign_role_to_member(str(actor_member.id), str(admin_role.id))
    _org().assign_role_to_member(str(target_member.id), str(admin_role.id))
    _org().assign_role_to_member(str(target_member.id), str(member_role.id))

    ok, msg = remove_role_from_org_member(org.slug, str(target_member.id), str(member_role.id), str(actor.id))
    assert not ok
    assert "equal to or higher" in msg


TESTS: list[TestCase] = [
    TestCase("assign role to member",              "org_rbac", test_assign_role_to_member),
    TestCase("assign role idempotent",             "org_rbac", test_assign_role_idempotent),
    TestCase("remove role from member",            "org_rbac", test_remove_role_from_member),
    TestCase("set role permission enable",         "org_rbac", test_set_org_role_permission_enable),
    TestCase("set role permission disable",        "org_rbac", test_set_org_role_permission_disable),
    TestCase("member permissions via role",        "org_rbac", test_get_member_permissions_via_role),
    TestCase("permissions revoked after removal",  "org_rbac", test_get_member_permissions_revoked_after_role_removal),
    TestCase("member highest position",            "org_rbac", test_get_member_highest_position),
    TestCase("owner position is maxsize",          "org_rbac", test_owner_highest_position_is_maxsize),
    TestCase("can actor manage role — owner",      "org_rbac", test_can_actor_manage_role_owner),
    TestCase("can actor manage role — higher pos", "org_rbac", test_can_actor_manage_role_higher_position),
    TestCase("can actor manage role — lower pos",  "org_rbac", test_can_actor_manage_role_lower_position_blocked),
    TestCase("is org owner",                       "org_rbac", test_is_org_owner),
    
    # enforcement 

    TestCase("unassigned perm absent from member", "org_rbac", test_unassigned_org_perm_absent_from_member),
    TestCase("perm isolation between members",     "org_rbac", test_permission_isolation_between_members),
    TestCase("permissions scoped to org",          "org_rbac", test_permissions_scoped_to_org),
    TestCase("non-member gets no permissions",     "org_rbac", test_non_member_gets_no_permissions),
    TestCase("same position cannot manage peer",   "org_rbac", test_same_position_cannot_manage_peer),
    
    # input security

    TestCase("perm key injection rejected",        "org_rbac", test_permission_key_injection_rejected),
    TestCase("cannot grant perm you don't hold",   "org_rbac", test_member_cannot_grant_perm_they_dont_hold),
    TestCase("cannot grant org.roles.manage",      "org_rbac", test_member_cannot_grant_org_roles_manage),
    TestCase("cannot remove equal member",         "org_rbac", test_member_cannot_remove_equal_position_member),
    TestCase("cannot remove low role from peer",   "org_rbac", test_member_cannot_remove_low_role_from_equal_position_member),
]
