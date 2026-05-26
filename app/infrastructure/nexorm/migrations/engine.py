import importlib.util
from pathlib import Path
from nexorm.database import default_db
from nexorm.migrations.state import read_state, write_state


class MigrationEngine:
    def __init__(self, migrations_dir="migrations", db=None, dialect=None):
        self.migrations_dir = Path(migrations_dir)
        self.db = db or default_db
        self.dialect = dialect or self.db.dialect

    def ensure_history(self):
        self.db.execute(self.dialect.migration_history_table_sql())
        self.db.commit()

    def migration_files(self):
        self.migrations_dir.mkdir(exist_ok=True)
        return sorted(self.migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.py"))

    def applied(self):
        self.ensure_history()
        return {row["name"] for row in self.db.fetchall(self._history_select_sql())}

    def load(self, path):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "operations", [])

    def load_module(self, path):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def project_state(self):
        latest_state = None
        for path in self.migration_files():
            state = getattr(self.load_module(path), "schema_state", None)
            if state is not None:
                latest_state = state
        return latest_state or read_state()

    def apply_pending(self):
        applied = self.applied()
        done = []
        for path in self.migration_files():
            if path.name in applied:
                continue
            module = self.load_module(path)
            operations = getattr(module, "operations", [])
            disable_fks = any(
                getattr(op, "requires_foreign_key_disable", False) for op in operations
            )
            try:
                if disable_fks:
                    for sql in self.dialect.disable_foreign_key_checks_sql():
                        self.db.execute(sql)
                    self.db.commit()
                with self.db.transaction():
                    for op in operations:
                        for sql in op.to_sql(self.dialect):
                            self.db.execute(sql)
                    self.db.execute(self._history_insert_sql(), [path.name])
            finally:
                if disable_fks:
                    for sql in self.dialect.enable_foreign_key_checks_sql():
                        self.db.execute(sql)
                    self.db.commit()
            state = getattr(module, "schema_state", None)
            if state is not None:
                write_state(state)
            done.append(path.name)
        return done

    def rollback_latest(self):
        self.ensure_history()
        row = self.db.fetchone(self._history_latest_sql())
        if not row:
            return None
        name = row["name"]
        path = self.migrations_dir / name
        operations = list(reversed(getattr(self.load_module(path), "operations", [])))
        disable_fks = any(
            getattr(op, "requires_foreign_key_disable", False) for op in operations
        )
        try:
            if disable_fks:
                for sql in self.dialect.disable_foreign_key_checks_sql():
                    self.db.execute(sql)
                self.db.commit()
            with self.db.transaction():
                for op in operations:
                    for sql in op.reverse_sql(self.dialect):
                        self.db.execute(sql)
                self.db.execute(self._history_delete_sql(), [name])
        finally:
            if disable_fks:
                for sql in self.dialect.enable_foreign_key_checks_sql():
                    self.db.execute(sql)
                self.db.commit()
        previous = None
        for migration_path, applied in self.status():
            if applied:
                state = getattr(
                    self.load_module(self.migrations_dir / migration_path), "schema_state", None
                )
                if state is not None:
                    previous = state
        write_state(previous or {"tables": {}})
        return name

    def status(self):
        applied = self.applied()
        return [(path.name, path.name in applied) for path in self.migration_files()]

    def sqlmigrate(self, name):
        path = self.migrations_dir / name
        return [
            sql
            for op in getattr(self.load_module(path), "operations", [])
            for sql in op.to_sql(self.dialect)
        ]

    def _history_select_sql(self):
        table = self.dialect.quote_identifier("nexorm_migrations")
        column = self.dialect.quote_identifier("name")
        return f"SELECT {column} FROM {table}"

    def _history_latest_sql(self):
        table = self.dialect.quote_identifier("nexorm_migrations")
        name = self.dialect.quote_identifier("name")
        pk = self.dialect.quote_identifier("id")
        return f"SELECT {name} FROM {table} ORDER BY {pk} DESC LIMIT 1"

    def _history_insert_sql(self):
        table = self.dialect.quote_identifier("nexorm_migrations")
        column = self.dialect.quote_identifier("name")
        return f"INSERT INTO {table} ({column}) VALUES ({self.dialect.placeholder})"

    def _history_delete_sql(self):
        table = self.dialect.quote_identifier("nexorm_migrations")
        column = self.dialect.quote_identifier("name")
        return f"DELETE FROM {table} WHERE {column} = {self.dialect.placeholder}"
