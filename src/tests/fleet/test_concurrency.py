from __future__ import annotations

import threading
import time

from nexorm.database import Database
from nexorm.exceptions import PoolTimeoutError

from tests._context import TestCase, TestContext


class _FakeConnection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _pooled_database(*, pool_size: int = 2, pool_timeout: float = 0.15) -> tuple[Database, list[_FakeConnection]]:
    created: list[_FakeConnection] = []
    lock = threading.Lock()
    db = Database(
        "mysql://user:pass@127.0.0.1/codesandbox",
        pool_size=pool_size,
        pool_timeout=pool_timeout,
    )

    def create_conn() -> _FakeConnection:
        with lock:
            conn = _FakeConnection(f"conn-{len(created) + 1}")
            created.append(conn)
            return conn

    db._connect_mysql = create_conn  # type: ignore[method-assign]
    db._ping_mysql = lambda conn: not conn.closed  # type: ignore[method-assign]
    return db, created


def test_pool_times_out_instead_of_exceeding_bound(ctx: TestContext) -> None:
    db, created = _pooled_database(pool_size=2, pool_timeout=0.1)
    release = threading.Event()
    acquired = threading.Barrier(3)
    errors: list[BaseException] = []

    def hold_connection() -> None:
        try:
            db.connect()
            acquired.wait(timeout=2)
            release.wait(timeout=2)
        except BaseException as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=hold_connection) for _ in range(2)]
    for thread in threads:
        thread.start()

    try:
        acquired.wait(timeout=2)
        try:
            db.connect()
        except PoolTimeoutError:
            pass
        else:
            raise AssertionError("pool allowed a third concurrent connection")
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=2)

    assert not errors
    assert len(created) == 2, f"expected exactly 2 connections, created {len(created)}"


def test_pool_reuses_released_connections_under_concurrency(ctx: TestContext) -> None:
    db, created = _pooled_database(pool_size=2, pool_timeout=0.5)
    errors: list[BaseException] = []
    start = threading.Barrier(9)

    def short_request() -> None:
        try:
            start.wait(timeout=2)
            db.connect()
            time.sleep(0.02)
        except BaseException as exc:
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=short_request) for _ in range(8)]
    for thread in threads:
        thread.start()

    start.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert len(created) <= 2, f"pool exceeded configured bound: {len(created)} connections"
    assert len(created) >= 1, "test did not exercise the pool"


TESTS = [
    TestCase("connection pool times out at bound", "load", test_pool_times_out_instead_of_exceeding_bound),
    TestCase("connection pool reuses released connections", "load", test_pool_reuses_released_connections_under_concurrency),
]
