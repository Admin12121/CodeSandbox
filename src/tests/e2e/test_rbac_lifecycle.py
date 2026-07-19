from __future__ import annotations

from tests._context import TestCase, TestContext, unique
from tests.e2e._helpers import make_user


def test_rbac_lifecycle(ctx: TestContext) -> None:
    from codesandbox.features.identity import repository as identity_repo
    from codesandbox.features.organizations import repository as org_repo
    from codesandbox.features.organizations import service as org_service
    from codesandbox.features.platform_admin import repository as platform_repo
    from codesandbox.features.platform_admin import service as platform_service
    from codesandbox.features.platform_admin.models import PlatformUserRole

    platform_admin, _ = make_user(ctx, "e2e-platform-admin")
    staff, _ = make_user(ctx, "e2e-platform-staff")
    senior_staff, _ = make_user(ctx, "e2e-platform-senior")
    identity_repo.update_user(str(platform_admin.id), platform_role="system_admin")
    identity_repo.update_user(str(staff.id), platform_role="system_staff")
    identity_repo.update_user(str(senior_staff.id), platform_role="system_staff")

    platform_repo.seed_default_permissions()
    registered_platform_permissions = {p.key for p in platform_repo.list_permissions()}
    assert "platform.users.read" in registered_platform_permissions

    junior_name = unique("e2e_junior_role")
    junior_data, error = platform_service.create_platform_role(
        name=junior_name,
        color="#2563eb",
        description="E2E junior application role",
    )
    assert error is None and junior_data is not None
    junior_role_id = str(junior_data["id"])
    ctx.defer(lambda: platform_service.delete_platform_role(junior_role_id))
    platform_repo.update_role_position(junior_role_id, 20)

    senior_name = unique("e2e_senior_role")
    senior_data, error = platform_service.create_platform_role(
        name=senior_name,
        color="#dc2626",
        description="E2E senior application role",
    )
    assert error is None and senior_data is not None
    senior_role_id = str(senior_data["id"])
    ctx.defer(lambda: platform_service.delete_platform_role(senior_role_id))
    platform_repo.update_role_position(senior_role_id, 70)

    assert platform_service.add_role_member(
        junior_role_id,
        str(staff.id),
        granted_by=str(platform_admin.id),
    ) is None
    assert platform_service.add_role_member(
        senior_role_id,
        str(senior_staff.id),
        granted_by=str(platform_admin.id),
    ) is None
    assert platform_service.toggle_role_permission(
        junior_role_id,
        "platform.users.read",
        True,
        actor_user_id=str(platform_admin.id),
    ) is None
    assert "platform.users.read" in platform_repo.get_user_permission_keys(str(staff.id))

    unknown_error = platform_service.toggle_role_permission(
        junior_role_id,
        "platform.injected.permission",
        True,
        actor_user_id=str(platform_admin.id),
    )
    assert unknown_error == "Unknown permission."

    _, duplicate_error = platform_service.create_platform_role(name=junior_name)
    assert duplicate_error is not None and "already exists" in duplicate_error

    hierarchy_error = platform_service.add_role_member(
        senior_role_id,
        str(staff.id),
        granted_by=str(staff.id),
    )
    assert hierarchy_error is not None and "higher" in hierarchy_error
    assert not platform_repo.can_actor_manage_user(str(staff.id), str(senior_staff.id))

    assigned_rows = PlatformUserRole.objects.filter(
        user_id=str(staff.id), role_id=junior_role_id
    ).all()
    assert len(assigned_rows) == 1, "application role membership must be unique"
    assert {
        permission.key for permission in platform_repo.get_permissions_for_role(junior_role_id)
    } <= registered_platform_permissions

    owner, _ = make_user(ctx, "e2e-org-owner")
    manager, _ = make_user(ctx, "e2e-org-manager")
    member, _ = make_user(ctx, "e2e-org-member")
    outsider, _ = make_user(ctx, "e2e-org-outsider")

    org = org_service.create_user_organization(
        name=unique("E2E Organization"),
        description="RBAC lifecycle organization",
        created_by=str(owner.id),
    )
    ctx.defer(lambda: org_repo.delete_organization(str(org.id)))
    manager_member = org_repo.add_member(str(org.id), str(manager.id))
    member_member = org_repo.add_member(str(org.id), str(member.id))

    ok, error = org_service.create_org_custom_role(
        org.slug,
        "E2E Manager",
        "#0f766e",
        "Delegated organization manager",
        str(owner.id),
    )
    assert ok, error
    manager_role = next(
        role for role in org_repo.list_org_roles(str(org.id)) if role.name == "E2E Manager"
    )
    org_repo.update_org_role(
        str(manager_role.id),
        manager_role.name,
        manager_role.color,
        manager_role.description,
        position=60,
    )

    ok, error = org_service.create_org_custom_role(
        org.slug,
        "E2E Junior",
        "#7c3aed",
        "Delegated organization member",
        str(owner.id),
    )
    assert ok, error
    junior_org_role = next(
        role for role in org_repo.list_org_roles(str(org.id)) if role.name == "E2E Junior"
    )
    org_repo.update_org_role(
        str(junior_org_role.id),
        junior_org_role.name,
        junior_org_role.color,
        junior_org_role.description,
        position=20,
    )

    for permission_key in ("org.roles.manage", "org.roles.assign"):
        ok, error = org_service.toggle_org_role_permission(
            org.slug,
            str(manager_role.id),
            permission_key,
            True,
            str(owner.id),
        )
        assert ok, error

    ok, error = org_service.assign_role_to_org_member(
        org.slug,
        str(manager_member.id),
        str(manager_role.id),
        str(owner.id),
    )
    assert ok, error
    ok, error = org_service.assign_role_to_org_member(
        org.slug,
        str(member_member.id),
        str(junior_org_role.id),
        str(manager.id),
    )
    assert ok, error
    assert "org.roles.assign" in org_repo.get_member_permissions(str(org.id), str(manager.id))

    peer_ok, peer_error = org_service.assign_role_to_org_member(
        org.slug,
        str(member_member.id),
        str(manager_role.id),
        str(manager.id),
    )
    assert not peer_ok and "higher" in peer_error

    unheld_key = next(
        key
        for key in (permission.key for permission in org_repo.get_all_org_permissions())
        if key not in {"org.roles.manage", "org.roles.assign"}
    )
    grant_ok, grant_error = org_service.toggle_org_role_permission(
        org.slug,
        str(junior_org_role.id),
        unheld_key,
        True,
        str(manager.id),
    )
    assert not grant_ok and "don't hold" in grant_error

    injection_ok, injection_error = org_service.toggle_org_role_permission(
        org.slug,
        str(junior_org_role.id),
        "org.injected.permission",
        True,
        str(owner.id),
    )
    assert not injection_ok and injection_error == "Invalid permission key."

    other_org = org_service.create_user_organization(
        name=unique("E2E Other Organization"),
        created_by=str(outsider.id),
    )
    ctx.defer(lambda: org_repo.delete_organization(str(other_org.id)))
    foreign_role = org_repo.create_org_role(
        str(other_org.id), "Foreign Role", "#64748b", position=5
    )
    cross_ok, cross_error = org_service.assign_role_to_org_member(
        org.slug,
        str(member_member.id),
        str(foreign_role.id),
        str(owner.id),
    )
    assert not cross_ok and cross_error == "Role not found."

    assert not org_repo.can_actor_manage_role(
        str(org.id), str(manager.id), str(manager_role.id)
    )
    assert org_repo.can_actor_manage_role(
        str(org.id), str(manager.id), str(junior_org_role.id)
    )
    assert org_repo.get_member_permissions(str(org.id), str(outsider.id)) == []


TESTS: list[TestCase] = [
    TestCase("RBAC lifecycle", "e2e_rbac_lifecycle", test_rbac_lifecycle),
]
