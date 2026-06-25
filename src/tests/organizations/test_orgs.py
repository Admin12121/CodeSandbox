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


def _org_svc():
    from codesandbox.features.organizations import service
    return service


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
    ctx.defer(lambda oid=str(org.id): _org().delete_organization(oid))
    return org



"""Creating an org sets owner_id to the creator and generates a slug."""
def test_create_organization(ctx: TestContext) -> None:
    user = _make_user(ctx, "orgown")
    org = _make_org(ctx, user)
    assert org.slug
    assert org.owner_id is not None
    assert str(org.owner_id) == str(user.id)


"""Every new org must have at least admin and member system roles seeded."""
def test_create_org_seeds_roles(ctx: TestContext) -> None:
    user = _make_user(ctx, "seedown")
    org  = _make_org(ctx, user)
    role_names = [r.name for r in _org().list_org_roles(str(org.id))]
    assert "admin"  in role_names, f"Expected 'admin' role, got {role_names}"
    assert "member" in role_names, f"Expected 'member' role, got {role_names}"


"""A member can be removed; the record must be fully gone afterwards."""
def test_remove_member(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "rmown")
    member = _make_user(ctx, "rmmem")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(member.id))
    _org().remove_member(str(org.id), str(member.id))
    assert _org().get_member(str(org.id), str(member.id)) is None, "Member must be removed"


"""Owner can transfer ownership to any current member."""
def test_transfer_ownership_success(ctx: TestContext) -> None:
    owner     = _make_user(ctx, "toown")
    new_owner = _make_user(ctx, "tonew")
    org       = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(new_owner.id))

    ok, msg = _org().transfer_ownership(str(org.id), str(owner.id), str(new_owner.id))
    assert ok, msg
    assert str(_org().get_organization(str(org.id)).owner_id) == str(new_owner.id)


"""Non-owner cannot transfer ownership regardless of membership."""
def test_transfer_ownership_not_owner(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "notown")
    other  = _make_user(ctx, "notno")
    target = _make_user(ctx, "nottg")
    org    = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(other.id))
    _org().add_member(str(org.id), str(target.id))

    ok, _ = _org().transfer_ownership(str(org.id), str(other.id), str(target.id))
    assert not ok, "Non-owner must not transfer ownership"


"""delete_org_role must refuse roles with is_system=True."""
def test_delete_system_org_role_blocked(ctx: TestContext) -> None:
    from codesandbox.features.organizations.models import OrganizationRole

    user = _make_user(ctx, "sysrl")
    org  = _make_org(ctx, user)
    role = OrganizationRole(
        id=str(uuid.uuid4()),
        org_id=str(org.id),
        name="protected",
        color="#ff0000",
        is_system=True,
        position=99,
    )
    role.save()

    def _force_delete():
        role.is_system = False
        role.save()
        _org().delete_org_role(str(role.id))

    ctx.defer(_force_delete)

    ok = _org().delete_org_role(str(role.id))
    assert not ok, "System roles must not be deletable"


"""Deleted org is no longer retrievable."""
def test_delete_organization(ctx: TestContext) -> None:
    owner  = _make_user(ctx, "delown")
    org    = _org().create_organization(name=unique("DelOrg"), created_by=str(owner.id))
    org_id = str(org.id)
    _org().delete_organization(org_id)
    assert _org().get_organization(org_id) is None



"""IDOR: owner of Org B cannot remove a member from Org A using their member_id."""
def test_idor_cross_org_member_remove(ctx: TestContext) -> None:
    owner_a = _make_user(ctx, "idra_o")
    owner_b = _make_user(ctx, "idrb_o")
    victim  = _make_user(ctx, "idrv")

    org_a = _make_org(ctx, owner_a)
    org_b = _make_org(ctx, owner_b)

    _org().add_member(str(org_a.id), str(victim.id))
    victim_member_a = _org().get_member(str(org_a.id), str(victim.id))
    assert victim_member_a is not None

    # owner_b passes org_b.id but victim's member_id from org_a
    from codesandbox.features.organizations.service import remove_org_member
    ok, _ = remove_org_member(
        org_id=str(org_b.id),
        member_id=str(victim_member_a.id),
        requesting_user_id=str(owner_b.id),
    )
    assert not ok, "Cross-org member removal must be blocked"
    assert _org().get_member(str(org_a.id), str(victim.id)) is not None, "Victim must still be in org_a"


"""IDOR: owner of Org B cannot delete a custom role that belongs to Org A."""
def test_idor_cross_org_role_delete(ctx: TestContext) -> None:
    from codesandbox.features.organizations.models import OrganizationRole
    from codesandbox.features.organizations.service import delete_org_custom_role

    owner_a = _make_user(ctx, "idra_ro")
    owner_b = _make_user(ctx, "idrb_ro")

    org_a = _make_org(ctx, owner_a)
    org_b = _make_org(ctx, owner_b)

    # Create a custom (non-system) role in org_a
    role_a = OrganizationRole(
        id=str(uuid.uuid4()),
        org_id=str(org_a.id),
        name=unique("custom"),
        color="#aabbcc",
        is_system=False,
        position=1,
    )
    role_a.save()
    ctx.defer(lambda rid=str(role_a.id): _org().delete_org_role(rid))

    # owner_b uses org_b's slug but role_a's id → must be blocked
    ok, _ = delete_org_custom_role(org_b.slug, str(role_a.id), str(owner_b.id))
    assert not ok, "Cross-org role deletion must be blocked"

    still = OrganizationRole.objects.filter(id=str(role_a.id)).first()
    assert still is not None, "Role in org_a must survive the cross-org attack"


"""A member (pos 10) cannot assign themselves the admin role (pos 80)."""
def test_self_assign_higher_position_blocked(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import assign_role_to_org_member

    owner = _make_user(ctx, "sahpb_o")
    actor = _make_user(ctx, "sahpb_a")
    org   = _make_org(ctx, owner)
    _org().add_member(str(org.id), str(actor.id))

    roles       = _org().list_org_roles(str(org.id))
    admin_role  = next(r for r in roles if r.name == "admin")   # position 80
    member_role = next(r for r in roles if r.name == "member")  # position 10

    m = _org().get_member(str(org.id), str(actor.id))
    _org().assign_role_to_member(str(m.id), str(member_role.id))

    ok, msg = assign_role_to_org_member(
        org.slug, str(m.id), str(admin_role.id), str(actor.id),
    )
    assert not ok, "Member at pos 10 must not self-escalate to pos 80"
    assert "position" in msg.lower() or "permission" in msg.lower()


def test_transfer_to_non_member_blocked(ctx: TestContext) -> None:
    """Transfer ownership to a user who is not a member must fail."""
    owner    = _make_user(ctx, "ttnmb_o")
    outsider = _make_user(ctx, "ttnmb_x")
    org      = _make_org(ctx, owner)

    ok, msg = _org().transfer_ownership(str(org.id), str(owner.id), str(outsider.id))
    assert not ok, "Transfer to non-member must be rejected"
    assert "member" in msg.lower()


"""Owner cannot leave the org without transferring ownership first."""
def test_leave_org_owner_blocked(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import leave_org

    owner = _make_user(ctx, "loob")
    org   = _make_org(ctx, owner)

    ok, msg = leave_org(str(org.id), str(owner.id))
    assert not ok, "Owner must not be able to leave without transferring"
    assert "transfer" in msg.lower() or "owner" in msg.lower()


"""A user who is not a member cannot assign roles in the org."""
def test_non_member_role_assign_rejected(ctx: TestContext) -> None:
    from codesandbox.features.organizations.service import assign_role_to_org_member

    owner    = _make_user(ctx, "nmrar_o")
    outsider = _make_user(ctx, "nmrar_x")
    victim   = _make_user(ctx, "nmrar_v")
    org      = _make_org(ctx, owner)

    _org().add_member(str(org.id), str(victim.id))
    m     = _org().get_member(str(org.id), str(victim.id))
    roles = _org().list_org_roles(str(org.id))
    role  = roles[0]

    ok, _ = assign_role_to_org_member(org.slug, str(m.id), str(role.id), str(outsider.id))
    assert not ok, "Non-member must not be able to assign roles"


TESTS: list[TestCase] = [
    TestCase("create organization",              "organizations", test_create_organization),
    TestCase("create org seeds roles",           "organizations", test_create_org_seeds_roles),
    TestCase("remove member",                    "organizations", test_remove_member),
    TestCase("transfer ownership success",       "organizations", test_transfer_ownership_success),
    TestCase("transfer ownership not owner",     "organizations", test_transfer_ownership_not_owner),
    TestCase("delete system org role blocked",   "organizations", test_delete_system_org_role_blocked),
    TestCase("delete organization",              "organizations", test_delete_organization),
    TestCase("IDOR cross-org member remove",     "organizations", test_idor_cross_org_member_remove),
    TestCase("IDOR cross-org role delete",       "organizations", test_idor_cross_org_role_delete),
    TestCase("self-assign higher position",      "organizations", test_self_assign_higher_position_blocked),
    TestCase("transfer to non-member blocked",   "organizations", test_transfer_to_non_member_blocked),
    TestCase("leave org owner blocked",          "organizations", test_leave_org_owner_blocked),
    TestCase("non-member role assign rejected",  "organizations", test_non_member_role_assign_rejected),
]
