from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tests._context import TestCase, TestContext, unique


def _cleanup_worker(ctx: TestContext, worker_id: str) -> None:
    from codesandbox.features.worker.models import WorkerNode

    def _delete() -> None:
        node = WorkerNode.objects.filter(worker_id=worker_id).first()
        if node is not None:
            node.delete()

    ctx.defer(_delete)


def test_register_and_heartbeat_roundtrip(ctx: TestContext) -> None:
    from codesandbox.features.worker import repository, service

    worker_id = unique("worker")
    _cleanup_worker(ctx, worker_id)

    node = service.register_worker({
        "worker_id": worker_id,
        "hostname": "test-host",
        "total_vcpu": 8,
        "total_ram_gb": 16,
        "total_disk_gb": 100,
    })
    assert node.status == "online"
    assert node.total_vcpu == 8

    updated = service.record_heartbeat({
        "worker_id": worker_id,
        "used_vcpu": 2,
        "used_ram_gb": 4,
        "running_instances": 1,
    })
    assert updated is not None
    assert updated.used_vcpu == 2
    assert service.is_worker_online(worker_id)


def test_select_worker_respects_capacity(ctx: TestContext) -> None:
    """Other real workers may also be online in this shared dev environment,
    so this only asserts what's true regardless of fleet state: a worker
    with just enough capacity is a valid pick, and one with far too little
    capacity for an absurdly large request is never picked."""
    from codesandbox.features.worker import repository, service

    worker_id = unique("worker")
    _cleanup_worker(ctx, worker_id)
    repository.register_worker_node(
        worker_id=worker_id, hostname=None, capabilities_json=None,
        total_vcpu=2, total_ram_gb=2, total_disk_gb=10,
    )

    # Some worker with enough capacity must be found (at least this one).
    picked = service.select_worker_for_instance(2, 2)
    assert picked is not None

    # Nothing in a normal dev fleet has 999 vcpu/ram free — this worker in
    # particular must never be selected for a request that oversized.
    too_big = service.select_worker_for_instance(999, 999)
    assert too_big is None or too_big.worker_id != worker_id


def test_reserve_and_release_capacity(ctx: TestContext) -> None:
    from codesandbox.features.worker import repository, service

    worker_id = unique("worker")
    _cleanup_worker(ctx, worker_id)
    repository.register_worker_node(
        worker_id=worker_id, hostname=None, capabilities_json=None,
        total_vcpu=4, total_ram_gb=8, total_disk_gb=50,
    )

    service.reserve_worker_capacity(worker_id, vcpu=2, ram_gb=4, disk_gb=10)
    node = repository.get_worker_node(worker_id)
    assert (
        node.used_vcpu == 2
        and node.used_ram_gb == 4
        and node.used_disk_gb == 10
        and node.running_instances == 1
    )

    service.release_worker_capacity(worker_id, vcpu=2, ram_gb=4, disk_gb=10)
    node = repository.get_worker_node(worker_id)
    assert (
        node.used_vcpu == 0
        and node.used_ram_gb == 0
        and node.used_disk_gb == 0
        and node.running_instances == 0
    )


def test_stale_worker_marked_offline(ctx: TestContext) -> None:
    from codesandbox.features.worker import repository

    worker_id = unique("worker")
    _cleanup_worker(ctx, worker_id)
    node = repository.register_worker_node(
        worker_id=worker_id, hostname=None, capabilities_json=None,
        total_vcpu=1, total_ram_gb=1, total_disk_gb=1,
    )
    # Backdate the heartbeat well past any reasonable timeout.
    node.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(hours=1)
    node.save()

    stale = repository.mark_stale_workers_offline(timeout_seconds=30)
    assert any(w.worker_id == worker_id for w in stale)

    refreshed = repository.get_worker_node(worker_id)
    assert refreshed.status == "offline"
    # An offline worker must never be selected for new instances.
    assert not any(
        w.worker_id == worker_id for w in repository.list_online_workers()
    )


def test_worker_instance_runtime_upsert(ctx: TestContext) -> None:
    from codesandbox.features.identity.models import User
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.worker import repository
    from seeds.sandbox_templates import GOD_TEAR_SLUG

    # WorkerInstanceRuntime.instance_id is a real FK — needs a real
    # SandboxInstance row, so reuse the seeded admin user/template rather
    # than standing up a whole new template just for this.
    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    template = sandbox_repository.get_template_by_slug(GOD_TEAR_SLUG)
    assert admin is not None and template is not None, "expected seed.py fixtures to exist"

    instance = sandbox_repository.create_instance(
        template_id=str(template.id),
        plan_id="__test__",
        workspace_type="test",
        created_by_user_id=str(admin.id),
        billing_entity="test",
        workspace_user_id=str(admin.id),
    )
    ctx.defer(instance.delete)
    instance_id = str(instance.id)
    worker_id = unique("worker")

    runtime = repository.upsert_runtime(
        instance_id=instance_id,
        worker_id=worker_id,
        runtime_provider="docker",
        runtime_id="deadbeef",
        status="running",
    )
    ctx.defer(runtime.delete)
    assert runtime.status == "running"

    again = repository.upsert_runtime(
        instance_id=instance_id, worker_id=worker_id, status="stopped"
    )
    assert again.id == runtime.id  # same row, updated in place — not duplicated
    assert again.status == "stopped"


TESTS: list[TestCase] = [
    TestCase("register/heartbeat roundtrip", "worker_registry", test_register_and_heartbeat_roundtrip),
    TestCase("select worker respects capacity", "worker_registry", test_select_worker_respects_capacity),
    TestCase("reserve/release capacity", "worker_registry", test_reserve_and_release_capacity),
    TestCase("stale worker marked offline", "worker_registry", test_stale_worker_marked_offline),
    TestCase("worker instance runtime upsert", "worker_registry", test_worker_instance_runtime_upsert),
]
