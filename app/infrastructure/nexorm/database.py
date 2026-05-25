import sqlite3
import threading
from contextlib import contextmanager
from nexorm.dialects.sqlite import SQLiteDialect


class Database:
    def __init__(self, path="db.sqlite3", dialect=None):
        self.path = path
        self.dialect = dialect or SQLiteDialect()
        self._local = threading.local()

    @property
    def connection(self):
        return getattr(self._local, "connection", None)

    @connection.setter
    def connection(self, value):
        self._local.connection = value

    @property
    def _atomic_depth(self):
        return getattr(self._local, "atomic_depth", 0)

    @_atomic_depth.setter
    def _atomic_depth(self, value):
        self._local.atomic_depth = value

    def connect(self):
        if self.connection is None:
            self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
        return self.connection

    def execute(self, sql, params=None):
        cursor = self.connect().execute(sql, params or [])
        return cursor

    def fetchone(self, sql, params=None):
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql, params=None):
        return self.execute(sql, params).fetchall()

    def commit(self):
        if self.connection is not None:
            self.connection.commit()

    def rollback(self):
        if self.connection is not None:
            self.connection.rollback()

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    @contextmanager
    def transaction(self):
        conn = self.connect()
        savepoint = f"nexorm_sp_{self._atomic_depth}"
        try:
            if self._atomic_depth == 0:
                conn.execute("BEGIN")
            else:
                conn.execute(f"SAVEPOINT {savepoint}")
            self._atomic_depth += 1
            yield self
            self._atomic_depth -= 1
            if self._atomic_depth == 0:
                conn.commit()
            else:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self._atomic_depth = max(0, self._atomic_depth - 1)
            if self._atomic_depth == 0:
                conn.rollback()
            else:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise

    @property
    def in_atomic(self):
        return self._atomic_depth > 0


default_db = Database()


def configure(path="db.sqlite3", dialect=None):
    default_db.close()
    default_db.path = path
    default_db.dialect = dialect or SQLiteDialect()
    return default_db
