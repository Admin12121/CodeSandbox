from __future__ import annotations

import importlib
import os
import tempfile

from tests._context import TestCase, TestContext


def _load_embedded_state(path: str) -> dict:
    """Read the `schema_state` dict a migration file embeds at its tail."""
    content = open(path, encoding="utf-8").read()
    idx = content.find("schema_state")
    ns: dict = {}
    exec(compile("from decimal import Decimal\n" + content[idx:], path, "exec"), ns)
    return ns["schema_state"]


def test_models_have_no_unmigrated_changes(ctx: TestContext) -> None:
    """The live model state must exactly match the latest committed migration's
    embedded schema snapshot — i.e. nobody edited models.py without generating
    and committing a migration for it (the exact drift class that produced the
    stale schema_state.json this phase fixed)."""
    from nexorm.migrations.autodetector import MigrationAutodetector
    from nexorm.migrations.engine import MigrationEngine
    from nexorm.migrations.state import model_state

    importlib.import_module("codesandbox.models")

    engine = MigrationEngine(migrations_dir=_migrations_dir())
    old_state = engine.project_state()
    new_state = model_state()
    ops = MigrationAutodetector(old_state, new_state).changes()
    assert not ops, (
        "models.py has changes with no committed migration — run "
        "`uv run nexorm makemigrations` and commit the result: "
        + ", ".join(op.describe() for op in ops)
    )


def test_new_migration_applies_to_existing_database(ctx: TestContext) -> None:
    """Upgrade-path test: seed a fresh SQLite DB with exactly the schema the
    *previous* migration (0016) already committed in production, mark
    migrations up to and including it as already applied, then run the
    engine's normal `apply_pending()` — it must apply only the new
    WorkerNode/WorkerInstanceRuntime migration (0017) and nothing else,
    proving the new migration lands cleanly on top of an already-migrated
    database without touching unrelated tables.

    This deliberately does not replay the full historical migration chain
    from empty on SQLite: migration 0016 mixes an AlterColumn (which SQLite
    can only apply via a full table rebuild using the column list embedded
    at generation time) with later AddColumn ops for the same table, which is
    a pre-existing nexorm engine limitation specific to rebuild-based
    dialects. It does not affect this project, since MySQL is the only
    backend actually used in dev/production, and a full fresh install has
    been manually verified there end-to-end.
    """
    from nexorm.database import Database
    from nexorm.migrations.engine import MigrationEngine
    from nexorm.migrations.operations import CreateTable

    importlib.import_module("codesandbox.models")

    migrations_dir = _migrations_dir()
    baseline = _load_embedded_state(
        os.path.join(migrations_dir, "0016_add_column_default_command_to_sandbox_templates.py")
    )

    fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.remove(db_path)
    ctx.defer(lambda: os.path.exists(db_path) and os.remove(db_path))

    # A dedicated Database instance (not nexorm.database.configure(), which
    # would repoint the process-wide default connection every other test
    # relies on) so this stays isolated from the shared app DB.
    db = Database(db_path, backend="sqlite")
    ctx.defer(db.close)
    engine = MigrationEngine(migrations_dir=migrations_dir, db=db)

    for table in baseline["tables"].values():
        for sql in CreateTable(
            table["name"],
            list(table["columns"].values()),
            table.get("foreign_keys", []),
            table.get("indexes", []),
        ).to_sql(engine.dialect):
            engine.db.execute(sql)
    engine.db.commit()

    engine.ensure_history()
    baseline_cutoff = "0016_add_column_default_command_to_sandbox_templates.py"
    already_applied = [
        path.name for path in engine.migration_files() if path.name <= baseline_cutoff
    ]
    expected_pending = [
        path.name for path in engine.migration_files() if path.name > baseline_cutoff
    ]
    for name in already_applied:
        engine.db.execute(engine._history_insert_sql(), [name])
    engine.db.commit()

    applied = engine.apply_pending()
    assert applied == expected_pending, applied

    row = engine.db.fetchone(
        f"SELECT * FROM {engine.dialect.quote_identifier('worker_nodes')} LIMIT 1"
    )
    assert row is None  # table exists and is queryable, just empty
    row = engine.db.fetchone(
        f"SELECT * FROM {engine.dialect.quote_identifier('worker_instance_runtimes')} LIMIT 1"
    )
    assert row is None


def _migrations_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "..", "migrations"))


TESTS: list[TestCase] = [
    TestCase("models match latest migration", "worker", test_models_have_no_unmigrated_changes),
    TestCase("new migration upgrades existing db", "worker", test_new_migration_applies_to_existing_database),
]
