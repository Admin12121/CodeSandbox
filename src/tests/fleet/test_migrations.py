from __future__ import annotations

import importlib
import os
from pathlib import Path

from tests._context import TestCase, TestContext


def _load_embedded_state(path: Path) -> dict:
    """Read the schema_state dict embedded at the end of a migration."""
    content = path.read_text(encoding="utf-8")
    idx = content.find("schema_state")
    assert idx >= 0, f"migration has no embedded schema_state: {path}"
    namespace: dict = {}
    exec(
        compile("from decimal import Decimal\n" + content[idx:], str(path), "exec"),
        namespace,
    )
    return namespace["schema_state"]


def test_models_have_no_unmigrated_changes(ctx: TestContext) -> None:
    """The live model state must match the latest committed migration."""
    from nexorm.migrations.autodetector import MigrationAutodetector
    from nexorm.migrations.engine import MigrationEngine
    from nexorm.migrations.state import model_state

    importlib.import_module("codesandbox.models")

    engine = MigrationEngine(migrations_dir=_migrations_dir())
    old_state = engine.project_state()
    new_state = model_state()
    ops = MigrationAutodetector(old_state, new_state).changes()
    assert not ops, (
        "models.py has changes with no committed migration - run "
        "`uv run nexorm makemigrations` and commit the result: "
        + ", ".join(op.describe() for op in ops)
    )


def test_latest_migration_is_applied(ctx: TestContext) -> None:
    """The Compose database must contain the latest committed migration."""
    from nexorm.migrations.engine import MigrationEngine

    migrations_dir = Path(_migrations_dir())
    migration_files = sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.py"))
    assert migration_files, "no migrations found"
    latest_migration = migration_files[-1]

    engine = MigrationEngine(migrations_dir=migrations_dir)
    applied = engine.applied()
    assert latest_migration.name in applied, (
        f"{latest_migration.name} is not applied; run `docker compose up` "
        "and let the bootstrap service finish"
    )
    assert engine.project_state() == _load_embedded_state(latest_migration)


def _migrations_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "migrations"))


TESTS: list[TestCase] = [
    TestCase(
        "models match latest migration",
        "worker",
        test_models_have_no_unmigrated_changes,
    ),
    TestCase(
        "latest migration is applied",
        "worker",
        test_latest_migration_is_applied,
    ),
]
