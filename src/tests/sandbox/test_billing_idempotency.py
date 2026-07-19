from __future__ import annotations

from decimal import Decimal

from tests._context import TestCase, TestContext, unique


def _fixture_instance(ctx: TestContext):
    from codesandbox.features.identity.models import User
    from codesandbox.features.sandbox import repository as sandbox_repository

    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    template = sandbox_repository.get_template_by_slug("reverse-decompile")
    assert admin is not None and template is not None, "expected seed.py fixtures to exist"

    instance = sandbox_repository.create_instance(
        template_id=str(template.id),
        plan_id="__test__",
        workspace_type="personal",
        created_by_user_id=str(admin.id),
        billing_entity="user",
        workspace_user_id=str(admin.id),
        billed_user_id=str(admin.id),
    )
    ctx.defer(instance.delete)
    return admin, instance


def test_charge_instance_balance_is_idempotent(ctx: TestContext) -> None:
    """Reconciliation and worker-callback retries can call the same
    finalization path more than once for one instance — charging must
    never apply twice, relying on BalanceTransaction.idempotency_key
    (f"usage:{instance_id}") rather than caller-side de-duplication."""
    from codesandbox.features.sandbox import repository as sandbox_repository

    admin, instance = _fixture_instance(ctx)

    balance = sandbox_repository.get_or_create_balance("user", str(admin.id))
    balance.amount = Decimal("100.00")
    balance.updated_at = balance.updated_at
    balance.save()
    starting_amount = Decimal(str(sandbox_repository.get_balance("user", str(admin.id)).amount))

    tx1, charged1, status1 = sandbox_repository.charge_instance_balance(
        str(instance.id), Decimal("5.00"), "usage test"
    )
    assert tx1 is not None
    assert charged1 == Decimal("5.00")

    after_first = Decimal(str(sandbox_repository.get_balance("user", str(admin.id)).amount))
    assert after_first == starting_amount - Decimal("5.00")

    # Simulate a retried finalization callback for the exact same instance.
    tx2, charged2, status2 = sandbox_repository.charge_instance_balance(
        str(instance.id), Decimal("5.00"), "usage test retry"
    )
    assert tx2 is not None
    assert tx2.id == tx1.id  # same row returned, not a new charge

    after_second = Decimal(str(sandbox_repository.get_balance("user", str(admin.id)).amount))
    assert after_second == after_first, "a retried charge must not deduct twice"

    from codesandbox.features.sandbox.models import BalanceTransaction

    matching = BalanceTransaction.objects.filter(idempotency_key=f"usage:{instance.id}").all()
    assert len(matching) == 1


TESTS: list[TestCase] = [
    TestCase("charge_instance_balance is idempotent", "billing", test_charge_instance_balance_is_idempotent),
]
