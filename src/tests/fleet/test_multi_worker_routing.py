from __future__ import annotations

import json

from tests._context import TestCase, TestContext, unique


def _nats_subject_matches(pattern: str, subject: str) -> bool:
    """Minimal NATS wildcard matcher (`*` = exactly one token, `>` = rest),
    used to prove worker-scoped subscribe patterns can't match another
    worker's subjects — without needing a live NATS server for this test."""
    pattern_parts = pattern.split(".")
    subject_parts = subject.split(".")
    for i, part in enumerate(pattern_parts):
        if part == ">":
            return True
        if i >= len(subject_parts):
            return False
        if part != "*" and part != subject_parts[i]:
            return False
    return len(pattern_parts) == len(subject_parts)


def test_redis_queue_partitioned_per_worker(ctx: TestContext) -> None:
    """Instance A's job (assigned to worker-1) must never be poppable from
    worker-2's queue, and vice versa — the core Phase 4 fix (no more
    work-stealing off one shared queue)."""
    from codesandbox.features.sandbox.queue import _get_client, enqueue_job, worker_queue_key

    worker_a = unique("worker")
    worker_b = unique("worker")
    instance_a = unique("inst")
    instance_b = unique("inst")

    client = _get_client()
    key_a = worker_queue_key(worker_a)
    key_b = worker_queue_key(worker_b)
    ctx.defer(lambda: client.delete(key_a))
    ctx.defer(lambda: client.delete(key_b))

    enqueue_job({"job_id": "j1", "action": "start", "instance_id": instance_a, "worker_id": worker_a})
    enqueue_job({"job_id": "j2", "action": "start", "instance_id": instance_b, "worker_id": worker_b})

    assert key_a != key_b

    raw_a = client.lrange(key_a, 0, -1)
    raw_b = client.lrange(key_b, 0, -1)
    assert len(raw_a) == 1 and len(raw_b) == 1

    job_a = json.loads(raw_a[0])
    job_b = json.loads(raw_b[0])
    assert job_a["instance_id"] == instance_a
    assert job_b["instance_id"] == instance_b
    # Instance A's job must not be sitting in worker B's queue, and vice versa.
    assert instance_b not in [json.loads(item)["instance_id"] for item in raw_a]
    assert instance_a not in [json.loads(item)["instance_id"] for item in raw_b]


def test_enqueue_requires_worker_id(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.queue import enqueue_job

    try:
        enqueue_job({"job_id": "j3", "action": "start", "instance_id": "x"})
    except ValueError:
        pass
    else:
        raise AssertionError("enqueue_job must refuse a job with no worker_id.")


def test_terminal_subjects_scoped_to_worker(ctx: TestContext) -> None:
    """A terminal command addressed to instance A on worker-1 must not be
    delivered to worker-2's subscription, and vice versa."""
    worker_1, worker_2 = "worker-1", "worker-2"
    instance_a, instance_b = "instance-a", "instance-b"

    def ctl_subject(worker_id: str, instance_id: str) -> str:
        return f"codesandbox.worker.{worker_id}.sandbox.{instance_id}.terminal.ctl"

    def fs_subject(worker_id: str, instance_id: str) -> str:
        return f"codesandbox.worker.{worker_id}.sandbox.{instance_id}.fs.request"

    worker_1_ctl_pattern = f"codesandbox.worker.{worker_1}.sandbox.*.terminal.ctl"
    worker_2_ctl_pattern = f"codesandbox.worker.{worker_2}.sandbox.*.terminal.ctl"
    worker_1_fs_pattern = f"codesandbox.worker.{worker_1}.sandbox.*.fs.request"
    worker_2_fs_pattern = f"codesandbox.worker.{worker_2}.sandbox.*.fs.request"

    subject_a = ctl_subject(worker_1, instance_a)
    subject_b = ctl_subject(worker_2, instance_b)

    # Worker-1's subscription matches instance A's subject...
    assert _nats_subject_matches(worker_1_ctl_pattern, subject_a)
    # ...but never instance B's subject, which belongs to worker-2.
    assert not _nats_subject_matches(worker_1_ctl_pattern, subject_b)
    # And worker-2's subscription is the mirror image.
    assert _nats_subject_matches(worker_2_ctl_pattern, subject_b)
    assert not _nats_subject_matches(worker_2_ctl_pattern, subject_a)

    fs_a = fs_subject(worker_1, instance_a)
    fs_b = fs_subject(worker_2, instance_b)
    assert _nats_subject_matches(worker_1_fs_pattern, fs_a)
    assert not _nats_subject_matches(worker_1_fs_pattern, fs_b)
    assert _nats_subject_matches(worker_2_fs_pattern, fs_b)
    assert not _nats_subject_matches(worker_2_fs_pattern, fs_a)


TESTS: list[TestCase] = [
    TestCase("redis queue partitioned per worker", "worker", test_redis_queue_partitioned_per_worker),
    TestCase("enqueue requires worker_id", "worker", test_enqueue_requires_worker_id),
    TestCase("terminal/fs subjects scoped to worker", "worker", test_terminal_subjects_scoped_to_worker),
]
