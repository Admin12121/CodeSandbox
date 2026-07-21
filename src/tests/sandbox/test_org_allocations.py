from __future__ import annotations

import json
from decimal import Decimal

from tests._context import TestCase, TestContext, unique


def _identity_service():
    from codesandbox.features.identity import service
    return service


def _identity_repo():
    from codesandbox.features.identity import repository
    return repository


def _org_repo():
    from codesandbox.features.organizations import repository
    return repository


def _sandbox_repo():
    from codesandbox.features.sandbox import repository
    return repository


def _sandbox_service():
    from codesandbox.features.sandbox import service
    return service


def _cleanup_user(user_id: str) -> None:
    repo = _identity_repo()
    user = repo.find_user_by_id(user_id)
    if user is None:
        return
    for row in repo.list_user_sessions(user_id):
        try:
            row.delete()
        except Exception:
            pass
    try:
        user.delete()
    except Exception:
        pass


def _make_user(ctx: TestContext, prefix: str):
    email = unique(prefix) + "@test.local"
    result = _identity_service().sign_up(
        name=prefix,
        email=email,
        password="password123",
        ip_address=None,
        user_agent=None,
    )
    assert result.ok, result.message
    user = _identity_repo().find_user_by_email(email)
    assert user is not None
    ctx.defer(lambda uid=str(user.id): _cleanup_user(uid))
    return user


def _grant_org_permissions(org_id: str, user_id: str, permissions: list[str]) -> None:
    org_repo = _org_repo()
    org_repo.ensure_org_permissions_seeded()
    role = org_repo.create_org_role(
        org_id=org_id,
        name=unique("sandbox-access"),
        color="#6366f1",
        position=10,
    )
    for permission in permissions:
        assert org_repo.set_org_role_permission(str(role.id), permission, True)
    member = org_repo.get_member(org_id, user_id)
    assert member is not None
    assert org_repo.assign_role_to_member(str(member.id), str(role.id))


def _fixture(ctx: TestContext):
    from codesandbox.features.sandbox.models import (
        InstanceRequest,
        OrganizationSandboxAllocation,
        SandboxInstance,
        SandboxTemplatePlan,
    )

    owner = _make_user(ctx, "orgalloc_owner")
    member = _make_user(ctx, "orgalloc_member")
    org = _org_repo().create_organization(name=unique("Allocation Org"), created_by=str(owner.id))
    ctx.defer(lambda oid=str(org.id): _org_repo().delete_organization(oid))
    _org_repo().add_member(str(org.id), str(member.id))
    _grant_org_permissions(
        str(org.id),
        str(member.id),
        [
            "sandbox.instances.use_pool",
            "sandbox.instances.use_assigned",
            "sandbox.requests.submit",
        ],
    )

    repo = _sandbox_repo()
    plan_id = unique("org-plan")[:40]
    plan = repo.create_plan(
        plan_id=plan_id,
        name="Org Allocation Test",
        sort_order=999,
        ind_vcpu=1,
        ind_ram_gb=1,
        ind_disk_gb=5,
        ind_cost_hr=Decimal("0"),
        org_vcpu=2,
        org_ram_gb=2,
        org_disk_gb=10,
        org_cost_hr=Decimal("0"),
        updated_by_id=str(owner.id),
        min_billable_minutes=1,
    )
    template = repo.create_template(
        name=unique("Org Allocation Template"),
        slug=unique("org-allocation-template"),
        description=None,
        icon_path=None,
        docker_image="busybox:1.36",
        sandbox_type="interactive",
        runtime_class="container",
        interface_mode="terminal_only",
        allowed_ui_modes=json.dumps(["terminal_only"]),
        default_ui_mode="terminal_only",
        network_mode="disabled",
        allow_root=False,
        max_timeout_hr=1,
        runtime_config=None,
        created_by_id=str(owner.id),
        status="active",
    )
    mapping = repo.upsert_template_plan(
        str(template.id), str(plan.id), is_enabled=True, sort_order=0,
        ind_vcpu=None, ind_ram_gb=None, ind_disk_gb=None, ind_cost_hr=None,
        org_vcpu=None, org_ram_gb=None, org_disk_gb=None, org_cost_hr=None,
        max_timeout_hr=None, network_mode=None, min_billable_minutes=None,
        full_internet_enabled=None,
    )

    def cleanup_resources() -> None:
        for row in SandboxInstance.objects.filter(template_id=str(template.id)).all():
            try:
                row.delete()
            except Exception:
                pass
        for row in InstanceRequest.objects.filter(template_id=str(template.id)).all():
            try:
                row.delete()
            except Exception:
                pass
        for row in OrganizationSandboxAllocation.objects.filter(template_id=str(template.id)).all():
            try:
                row.delete()
            except Exception:
                pass
        try:
            mapping.delete()
        except Exception:
            pass
        try:
            template.delete()
        except Exception:
            pass
        try:
            plan.delete()
        except Exception:
            pass

    ctx.defer(cleanup_resources)
    return owner, member, org, template, plan


def test_configured_member_permissions_are_least_privilege(ctx: TestContext) -> None:
    owner, member, org, _template, _plan = _fixture(ctx)
    permissions = set(_org_repo().get_member_permissions(str(org.id), str(member.id)))
    assert "sandbox.instances.use_pool" in permissions
    assert "sandbox.instances.use_assigned" in permissions
    assert "sandbox.requests.submit" in permissions
    assert "sandbox.allocations.prepare" not in permissions
    assert "sandbox.requests.review" not in permissions
    assert "sandbox.billing.topup" not in permissions


def test_prepare_allocation_never_starts_or_bills_runtime(ctx: TestContext) -> None:
    owner, member, org, template, plan = _fixture(ctx)
    rows, error = _sandbox_service().create_org_allocations(
        org_id=str(org.id),
        creator_user_id=str(owner.id),
        template_slug=template.slug,
        plan_id=str(plan.id),
        access_scope="pool",
        quantity=2,
        max_session_minutes=60,
        max_starts_per_member=2,
    )
    assert error is None, error
    assert rows is not None and len(rows) == 2
    assert _sandbox_repo().list_instances_for_org(str(org.id)) == []
    assert _sandbox_repo().get_balance("org", str(org.id)) is None

    denied, denied_error = _sandbox_service().create_org_allocations(
        org_id=str(org.id),
        creator_user_id=str(member.id),
        template_slug=template.slug,
        plan_id=str(plan.id),
    )
    assert denied is None
    assert denied_error and "permission" in denied_error.lower()


def test_claimed_allocation_uses_only_org_billing_scope(ctx: TestContext) -> None:
    owner, member, org, template, plan = _fixture(ctx)
    rows, error = _sandbox_service().create_org_allocations(
        org_id=str(org.id),
        creator_user_id=str(owner.id),
        template_slug=template.slug,
        plan_id=str(plan.id),
        access_scope="pool",
        quantity=1,
        max_session_minutes=60,
        max_starts_per_member=1,
    )
    assert error is None and rows

    claimed, error = _sandbox_service().claim_org_allocation(
        rows[0]["id"], str(member.id), expected_org_id=str(org.id)
    )
    assert error is None, error
    assert claimed is not None
    instance = _sandbox_repo().get_instance(claimed["id"])
    assert instance is not None
    assert instance.workspace_type == "org"
    assert str(instance.workspace_org_id) == str(org.id)
    assert instance.workspace_user_id is None
    assert instance.billing_entity == "org"
    assert str(instance.billed_org_id) == str(org.id)
    assert instance.billed_user_id is None

    # A completed/deleted historical run still consumes the member guardrail.
    instance.status = "stopped"
    instance.save()
    _sandbox_repo().archive_instance(str(instance.id))
    assert _sandbox_repo().count_allocation_starts_by_user(rows[0]["id"], str(member.id)) == 1
    second, second_error = _sandbox_service().claim_org_allocation(
        rows[0]["id"], str(member.id), expected_org_id=str(org.id)
    )
    assert second is None
    assert second_error and "start limit" in second_error.lower()


def test_request_approval_creates_dedicated_allocation_not_runtime(ctx: TestContext) -> None:
    owner, member, org, template, plan = _fixture(ctx)
    request_row, error = _sandbox_service().submit_instance_request(
        str(org.id),
        str(member.id),
        template.slug,
        str(plan.id),
        "Need a dedicated environment",
        max_session_minutes=90,
        max_starts=3,
    )
    assert error is None and request_row

    reviewed, error = _sandbox_service().review_instance_request(
        request_row["id"], str(org.id), str(owner.id), "approved"
    )
    assert error is None, error
    assert reviewed is not None and reviewed["allocation_id"]
    assert reviewed["instance_id"] is None
    assert _sandbox_repo().list_instances_for_org(str(org.id)) == []

    allocation = _sandbox_repo().get_org_allocation(reviewed["allocation_id"])
    assert allocation is not None
    assert allocation.access_scope == "private"
    assert str(allocation.assigned_to_user_id) == str(member.id)
    assert int(allocation.max_session_minutes) == 90
    assert int(allocation.max_starts_per_member) == 3



def test_pool_member_cannot_open_another_members_live_instance(ctx: TestContext) -> None:
    owner, member, org, template, plan = _fixture(ctx)
    other_member = _make_user(ctx, "orgalloc_other")
    _org_repo().add_member(str(org.id), str(other_member.id))
    _grant_org_permissions(
        str(org.id),
        str(other_member.id),
        [
            "sandbox.instances.use_pool",
            "sandbox.instances.use_assigned",
            "sandbox.requests.submit",
        ],
    )

    rows, error = _sandbox_service().create_org_allocations(
        org_id=str(org.id),
        creator_user_id=str(owner.id),
        template_slug=template.slug,
        plan_id=str(plan.id),
        access_scope="pool",
        quantity=1,
        max_session_minutes=60,
        max_starts_per_member=2,
    )
    assert error is None and rows
    claimed, error = _sandbox_service().claim_org_allocation(
        rows[0]["id"], str(member.id), expected_org_id=str(org.id)
    )
    assert error is None and claimed

    other_view = _sandbox_service().get_org_allocations_for_user(
        str(org.id), str(other_member.id)
    )
    allocation = next(row for row in other_view if row["id"] == rows[0]["id"])
    assert allocation["status"] == "in_use"
    assert allocation["live_instance"] is None
    assert allocation["can_start"] is False

    owner_view = _sandbox_service().get_org_allocations_for_user(
        str(org.id), str(owner.id)
    )
    managed = next(row for row in owner_view if row["id"] == rows[0]["id"])
    assert managed["live_instance"] is not None

def test_direct_org_runtime_creation_is_disabled(ctx: TestContext) -> None:
    owner, _member, org, template, plan = _fixture(ctx)
    result, error = _sandbox_service().create_org_instance(
        str(org.id), str(owner.id), template.slug, str(plan.id)
    )
    assert result is None
    assert error and "disabled" in error.lower()


TESTS: list[TestCase] = [
    TestCase("configured org member sandbox permissions", "org_sandbox", test_configured_member_permissions_are_least_privilege),
    TestCase("org allocation prepare is idle", "org_sandbox", test_prepare_allocation_never_starts_or_bills_runtime),
    TestCase("org claim billing isolation", "org_sandbox", test_claimed_allocation_uses_only_org_billing_scope),
    TestCase("org request approval allocation", "org_sandbox", test_request_approval_creates_dedicated_allocation_not_runtime),
    TestCase("org pool live instance privacy", "org_sandbox", test_pool_member_cannot_open_another_members_live_instance),
    TestCase("direct org runtime disabled", "org_sandbox", test_direct_org_runtime_creation_is_disabled),
]
