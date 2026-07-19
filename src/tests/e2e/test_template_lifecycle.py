from __future__ import annotations

import json
import time
from decimal import Decimal

from tests._context import TestCase, TestContext, unique
from tests.e2e._helpers import delete_rows, make_user


def _wait_for_instance(repository, instance_id: str, statuses: set[str], *, timeout: int):
    deadline = time.monotonic() + timeout
    instance = None
    while time.monotonic() < deadline:
        instance = repository.get_instance(instance_id)
        if instance is not None and instance.status in statuses:
            return instance
        time.sleep(0.25)
    status = instance.status if instance is not None else "missing"
    raise AssertionError(
        f"instance {instance_id} did not reach {sorted(statuses)}; last status={status}"
    )


def _cleanup_template_lifecycle(resources: dict) -> None:
    from codesandbox.features.finance.models import UsageCharge
    from codesandbox.features.sandbox.models import (
        Balance,
        BalanceTransaction,
        SandboxAuditLog,
        SandboxInstance,
        SandboxTemplate,
        SandboxTemplatePlan,
        SandboxPlan,
    )
    from codesandbox.features.sandbox import repository as sandbox_repo
    from codesandbox.features.sandbox import service as sandbox_service
    from codesandbox.features.worker.models import WorkerInstanceRuntime

    for instance_id in resources["instance_ids"]:
        instance = sandbox_repo.get_instance(instance_id)
        if instance is not None and instance.status in {"running", "provisioning"}:
            actor_user_id = resources["instance_actors"].get(instance_id)
            sandbox_service.stop_instance(instance_id, actor_user_id=actor_user_id)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                current = sandbox_repo.get_instance(instance_id)
                if current is None or current.status not in {
                    "running",
                    "provisioning",
                    "stopping",
                    "cleanup",
                }:
                    break
                time.sleep(0.25)
        delete_rows(UsageCharge, instance_id=instance_id)
        delete_rows(BalanceTransaction, instance_id=instance_id)
        delete_rows(WorkerInstanceRuntime, instance_id=instance_id)
        delete_rows(SandboxAuditLog, instance_id=instance_id)
        instance = SandboxInstance.objects.filter(id=instance_id).first()
        if instance is not None:
            try:
                instance.delete()
            except Exception:
                pass

    template_id = resources.get("template_id")
    if template_id:
        delete_rows(SandboxAuditLog, template_id=template_id)
        delete_rows(SandboxTemplatePlan, template_id=template_id)
        template = SandboxTemplate.objects.filter(id=template_id).first()
        if template is not None:
            try:
                template.delete()
            except Exception:
                pass

    plan_id = resources.get("plan_id")
    if plan_id:
        plan = SandboxPlan.objects.filter(id=plan_id).first()
        if plan is not None:
            try:
                plan.delete()
            except Exception:
                pass

    user_id = resources.get("user_id")
    if user_id:
        delete_rows(BalanceTransaction, entity_type="user", entity_id=user_id)
        delete_rows(Balance, entity_type="user", entity_id=user_id)


def test_template_lifecycle(ctx: TestContext) -> None:
    from codesandbox.features.finance import repository as finance_repo
    from codesandbox.features.finance.models import UsageCharge
    from codesandbox.features.identity.models import User
    from codesandbox.features.sandbox import repository as sandbox_repo
    from codesandbox.features.sandbox import service as sandbox_service
    from codesandbox.features.sandbox.models import BalanceTransaction
    from codesandbox.features.worker import repository as worker_repo

    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    assert admin is not None, "expected seed.py fixtures to exist"
    user, _ = make_user(ctx, "e2e-template-user")
    user_id = str(user.id)

    resources = {
        "user_id": user_id,
        "plan_id": None,
        "template_id": None,
        "instance_ids": [],
        "instance_actors": {},
    }
    ctx.defer(lambda: _cleanup_template_lifecycle(resources))

    balance = sandbox_repo.get_or_create_balance("user", user_id)
    balance.amount = Decimal("10.00")
    balance.reserved_amount = Decimal("0.00")
    balance.save()
    starting_balance = Decimal("10.00")

    plan_id = unique("e2e_plan")
    resources["plan_id"] = plan_id
    plan, error = sandbox_service.save_plan(
        plan_id=plan_id,
        name="E2E Lifecycle Plan",
        ind_vcpu=1,
        ind_ram_gb=1,
        ind_disk_gb=2,
        ind_cost_hr="3.60",
        org_vcpu=2,
        org_ram_gb=2,
        org_disk_gb=4,
        org_cost_hr="7.20",
        updated_by_id=str(admin.id),
        min_billable_minutes=1,
    )
    assert error is None and plan is not None

    runtime_config = json.dumps({
        "runtime.json": json.dumps({
            "success_condition": "exit_zero",
            "test_config": {"success_condition": "exit_zero"},
        })
    })
    slug = unique("e2e-template")
    template_data, error = sandbox_service.save_template(
        template_id=None,
        name="E2E Template Lifecycle",
        description="Created by the end-to-end lifecycle test",
        icon_path="",
        docker_image="busybox:1.36",
        sandbox_type="interactive",
        runtime_config=runtime_config,
        created_by_id=str(admin.id),
        slug=slug,
        runtime_class="tool_job",
        default_ui_mode="background_run",
        interface_behavior="single",
        network_mode="disabled",
        default_command="sh -c 'echo codesandbox-e2e; sleep 8'",
        working_dir="/workspace",
        artifact_paths=[],
        read_only_root=True,
        run_as_user="65532:65532",
    )
    assert error is None and template_data is not None
    template_id = str(template_data["id"])
    resources["template_id"] = template_id

    plan_error = sandbox_service.toggle_template_plan_enabled(template_id, plan_id, True)
    assert plan_error is None
    enabled_plan_ids = {
        row["id"] for row in sandbox_service.get_template_plans_for_hub(template_id)
    }
    assert plan_id in enabled_plan_ids

    online_tool_workers = [
        worker
        for worker in worker_repo.list_online_workers()
        if "tool_job" in json.loads(worker.capabilities_json or "{}").get(
            "runtime_class", []
        )
    ]
    assert online_tool_workers, "a live tool_job worker is required for E2E tests"

    test_run, error = sandbox_service.start_test_instance(
        template_id,
        actor_user_id=str(admin.id),
    )
    assert error is None and test_run is not None
    test_instance_id = str(test_run["id"])
    resources["instance_ids"].append(test_instance_id)
    resources["instance_actors"][test_instance_id] = str(admin.id)
    test_instance = _wait_for_instance(
        sandbox_repo,
        test_instance_id,
        {"stopped", "failed"},
        timeout=60,
    )
    test_policy = json.loads(test_instance.runtime_policy or "{}")
    assert "codesandbox-e2e" in str(test_policy["default_command"])
    assert test_instance.status == "stopped", test_instance.failure_reason
    assert test_instance.exit_code == 0

    tested_template = sandbox_repo.get_template(template_id)
    assert tested_template is not None
    assert tested_template.last_test_status == "passed"
    publish_error = sandbox_service.set_template_status(
        template_id,
        "active",
        actor_user_id=str(admin.id),
    )
    assert publish_error is None, publish_error
    assert sandbox_repo.get_template(template_id).status == "active"

    personal, error = sandbox_service.create_personal_instance(user_id, slug, plan_id)
    assert error is None and personal is not None
    instance_id = str(personal["id"])
    resources["instance_ids"].append(instance_id)
    resources["instance_actors"][instance_id] = user_id

    started, error = sandbox_service.start_instance(instance_id, actor_user_id=user_id)
    assert error is None and started is not None
    assert started["status"] == "provisioning"
    running = _wait_for_instance(
        sandbox_repo,
        instance_id,
        {"running", "failed", "stopped"},
        timeout=45,
    )
    assert running.status == "running", running.failure_reason
    runtime_policy = json.loads(running.runtime_policy or "{}")
    assert "codesandbox-e2e" in str(runtime_policy["default_command"])
    assert Decimal(str(runtime_policy["cost_hr"])) == Decimal("3.60")

    stopping, error = sandbox_service.stop_instance(instance_id, actor_user_id=user_id)
    assert error is None and stopping is not None
    completed = _wait_for_instance(
        sandbox_repo,
        instance_id,
        {"stopped", "failed"},
        timeout=45,
    )
    assert completed is not None and completed.status == "stopped"
    assert completed.min_billable_sec == 60
    assert Decimal(str(completed.charged_amount)) == Decimal("0.06")
    assert completed.billing_status == "charged"

    charge = finance_repo.get_usage_charge_by_instance(instance_id)
    assert charge is not None
    assert charge.status == "charged"
    assert charge.billable_seconds == 60
    assert Decimal(str(charge.gross_amount)) == Decimal("0.06")
    assert Decimal(str(charge.final_amount)) == Decimal("0.06")
    assert len(UsageCharge.objects.filter(instance_id=instance_id).all()) == 1
    assert len(BalanceTransaction.objects.filter(
        idempotency_key=f"usage:{instance_id}"
    ).all()) == 1

    ending_balance = Decimal(str(sandbox_repo.get_balance("user", user_id).amount))
    assert ending_balance == starting_balance - Decimal("0.06")

    events = sandbox_repo.list_instance_audit_log(instance_id, limit=100)
    event_names = {event.event for event in events}
    expected_events = {
        "status:idle->provisioning",
        "started",
        "status:provisioning->running",
        "status:running->stopping",
        "cleanup_started",
        "status:stopping->cleanup",
        "stopped",
        "status:cleanup->stopped",
        "usage_charged",
    }
    assert expected_events <= event_names, sorted(event_names)

    archived, error = sandbox_service.archive_instance_for_user(instance_id, user_id)
    assert error is None and archived is not None
    assert archived["deleted_at"] is not None
    assert all(
        str(row["id"]) != instance_id
        for row in sandbox_service.get_user_instances(user_id)
    )


TESTS: list[TestCase] = [
    TestCase("template lifecycle", "e2e_template_lifecycle", test_template_lifecycle),
]
