import sqlite3
import threading
import time
from contextlib import contextmanager
from urllib.parse import parse_qsl, unquote, urlparse

from nexorm.dialects.mysql import MySQLDialect
from nexorm.dialects.postgres import PostgresDialect
from nexorm.dialects.sqlite import SQLiteDialect
from nexorm.exceptions import ConfigurationError, PoolTimeoutError


_BACKEND_ALIASES = {
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "postgres": "postgresql",
    "postgresql": "postgresql",
    "psql": "postgresql",
    "mysql": "mysql",
    "mariadb": "mysql",
}

DEFAULT_POOL_SIZE = 10
DEFAULT_POOL_TIMEOUT = 30.0


class _ConnectionPool:
    """Bounded pool of live DB-API connections, shared across every thread
    that talks to one Database instance.

    Threads no longer permanently own a connection for the life of the
    process (the previous thread-local-forever design) — they borrow one via
    `acquire()` and give it back via `release()`/`invalidate()`, capped at
    `max_size` concurrent connections, with idle ones health-checked before
    reuse so a connection that's gone stale (server `wait_timeout`, an idle
    TCP drop) is transparently replaced instead of surfacing a "server has
    gone away" error on next use.
    """

    def __init__(self, create_conn, ping_conn, max_size, timeout):
        self._create_conn = create_conn
        self._ping_conn = ping_conn
        self.max_size = max(1, int(max_size))
        self.timeout = max(0.1, float(timeout))
        self._cond = threading.Condition()
        self._idle: list = []
        self._created = 0

    def acquire(self):
        deadline = time.monotonic() + self.timeout
        with self._cond:
            while True:
                while self._idle:
                    conn = self._idle.pop()
                    if self._is_alive(conn):
                        return conn
                    self._created -= 1
                    self._cond.notify()
                if self._created < self.max_size:
                    self._created += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PoolTimeoutError(
                        f"Timed out after {self.timeout}s waiting for a free "
                        f"database connection (pool_size={self.max_size})."
                    )
                self._cond.wait(remaining)
        try:
            return self._create_conn()
        except Exception:
            with self._cond:
                self._created -= 1
                self._cond.notify()
            raise

    def release(self, conn):
        with self._cond:
            self._idle.append(conn)
            self._cond.notify()

    def invalidate(self, conn):
        try:
            conn.close()
        except Exception:
            pass
        with self._cond:
            self._created = max(0, self._created - 1)
            self._cond.notify()

    def close_all(self):
        with self._cond:
            for conn in self._idle:
                try:
                    conn.close()
                except Exception:
                    pass
            self._idle.clear()
            self._created = 0
            self._cond.notify_all()

    def _is_alive(self, conn):
        try:
            return bool(self._ping_conn(conn))
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return False


class Database:
    def __init__(self, path="db.sqlite3", dialect=None, backend=None, **options):
        self._local = threading.local()
        self.configure(path, dialect=dialect, backend=backend, **options)

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

    def configure(self, path="db.sqlite3", dialect=None, backend=None, **options):
        if getattr(self, "_local", None) is not None:
            self._force_close()
            self._atomic_depth = 0
        if getattr(self, "_pool", None) is not None:
            self._pool.close_all()
        self._pool = None
        settings = normalize_settings(path, backend=backend, **options)
        self.settings = settings
        self.backend = settings["backend"]
        self.database = settings.get("database")
        self.path = self.database
        self.dsn = settings.get("dsn")
        self.dialect = dialect or dialect_for_backend(self.backend)
        self.pool_size = int(settings.get("pool_size") or DEFAULT_POOL_SIZE)
        self.pool_timeout = float(settings.get("pool_timeout") or DEFAULT_POOL_TIMEOUT)
        return self

    def _get_pool(self):
        if self._pool is None:
            if self.backend == "postgresql":
                create_conn, ping_conn = self._connect_postgresql, self._ping_postgresql
            elif self.backend == "mysql":
                create_conn, ping_conn = self._connect_mysql, self._ping_mysql
            else:
                raise ConfigurationError(f"Unsupported database backend: {self.backend}")
            self._pool = _ConnectionPool(
                create_conn, ping_conn, self.pool_size, self.pool_timeout
            )
        return self._pool

    def connect(self):
        if self.backend == "sqlite":
            # A single connection per thread is the idiomatic, correct model
            # for SQLite (file-locking semantics, no client/server pooling to
            # do) — only mysql/postgresql go through the bounded pool below.
            if self.connection is None:
                self.connection = self._connect_sqlite()
            return self.connection
        if self.connection is None:
            self.connection = self._get_pool().acquire()
        return self.connection

    def _ping_mysql(self, conn) -> bool:
        conn.ping(reconnect=False)
        return True

    def _ping_postgresql(self, conn) -> bool:
        if getattr(conn, "closed", False):
            return False
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
        finally:
            try:
                cursor.close()
            except Exception:
                pass
        return True

    def _connect_sqlite(self):
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _connect_postgresql(self):
        kwargs = self._driver_kwargs(dbname_key="dbname")
        try:
            import psycopg
            from psycopg.rows import dict_row

            if self.dsn:
                return psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)
            return psycopg.connect(**kwargs, row_factory=dict_row, autocommit=True)
        except ImportError:
            pass

        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:
            raise ConfigurationError(
                "PostgreSQL support requires psycopg. "
                "Install it with `pip install nexorm[postgres]`."
            ) from exc

        conn = (
            psycopg2.connect(self.dsn, cursor_factory=RealDictCursor)
            if self.dsn
            else psycopg2.connect(**kwargs, cursor_factory=RealDictCursor)
        )
        conn.autocommit = True
        return conn

    def _connect_mysql(self):
        try:
            import pymysql
            import pymysql.cursors
        except ImportError as exc:
            raise ConfigurationError(
                "MySQL support requires PyMySQL. Install it with `pip install nexorm[mysql]`."
            ) from exc

        kwargs = self._driver_kwargs(dbname_key="database")
        kwargs.setdefault("charset", "utf8mb4")
        kwargs.setdefault("autocommit", True)
        kwargs.setdefault("cursorclass", pymysql.cursors.DictCursor)
        return pymysql.connect(**kwargs)

    def _driver_kwargs(self, dbname_key):
        ignored = {"backend", "database", "dsn", "url", "driver", "pool_size", "pool_timeout"}
        kwargs = {dbname_key: self.database}
        for key, value in self.settings.items():
            if key in ignored or value is None:
                continue
            kwargs[key] = value
        return kwargs

    def execute(self, sql, params=None):
        conn = self.connect()
        cursor = conn.cursor()
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
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
        """Give this thread's connection back so another thread can use it.

        Call this at the end of a unit of work (a request, a job) — it's
        what actually makes pooling bounded rather than every thread
        permanently holding its own connection for the life of the process.
        Refuses to release mid-transaction (the transaction() context
        manager owns that lifecycle itself); nothing to do for sqlite, which
        isn't pooled.
        """
        if self.connection is None:
            return
        if self._atomic_depth > 0:
            return
        if self.backend == "sqlite":
            self.connection.close()
            self.connection = None
            return
        self._get_pool().release(self.connection)
        self.connection = None

    def _force_close(self) -> None:
        """Unconditionally close this thread's connection — used when
        reconfiguring to a different database/backend, never for routine
        per-request cleanup (use close() for that)."""
        if self.connection is not None:
            try:
                self.connection.close()
            except Exception:
                pass
            self.connection = None

    @contextmanager
    def transaction(self):
        conn = self.connect()
        savepoint = f"nexorm_sp_{self._atomic_depth}"
        try:
            if self._atomic_depth == 0:
                self.execute("BEGIN")
            else:
                self.execute(f"SAVEPOINT {savepoint}")
            self._atomic_depth += 1
            yield self
            self._atomic_depth -= 1
            if self._atomic_depth == 0:
                conn.commit()
            else:
                self.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            self._atomic_depth = max(0, self._atomic_depth - 1)
            if self._atomic_depth == 0:
                conn.rollback()
            else:
                self.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise

    @property
    def in_atomic(self):
        return self._atomic_depth > 0


def configure(path="db.sqlite3", dialect=None, alias="default", backend=None, **options):
    if alias == "default":
        default_db.configure(path, dialect=dialect, backend=backend, **options)
        connections["default"] = default_db
        return default_db

    db = connections.get(alias)
    if db is None:
        db = Database(path, dialect=dialect, backend=backend, **options)
        connections[alias] = db
        return db

    db.configure(path, dialect=dialect, backend=backend, **options)
    return db


def get_connection(alias="default"):
    try:
        return connections[alias]
    except KeyError as exc:
        raise KeyError(f"Unknown NexORM database connection: {alias}") from exc


def normalize_settings(path="db.sqlite3", backend=None, **options):
    config = {}
    if isinstance(path, dict):
        config.update(path)
        path = (
            config.pop("path", None)
            or config.pop("database", None)
            or config.pop("NAME", None)
            or "db.sqlite3"
        )
        backend = backend or config.pop("backend", None) or config.pop("ENGINE", None)
    config.update(options)
    url = config.pop("url", None)
    dsn = config.pop("dsn", None)
    if url:
        parsed = _settings_from_url(url)
    elif dsn and _looks_like_url(dsn):
        parsed = _settings_from_url(dsn)
    elif isinstance(path, str) and _looks_like_url(path):
        parsed = _settings_from_url(path)
    else:
        backend_name = normalize_backend(backend or "sqlite")
        parsed = {"backend": backend_name, "database": path}
        if dsn:
            parsed["dsn"] = dsn
    parsed.update(_normalize_option_keys(config))
    parsed["backend"] = normalize_backend(parsed["backend"])
    if not parsed.get("database") and parsed["backend"] == "sqlite":
        parsed["database"] = "db.sqlite3"
    return parsed


def normalize_backend(backend):
    if backend is None:
        return "sqlite"
    name = str(backend).lower().strip()
    if "postgres" in name:
        return "postgresql"
    if "mysql" in name or "mariadb" in name:
        return "mysql"
    if "sqlite" in name:
        return "sqlite"
    try:
        return _BACKEND_ALIASES[name]
    except KeyError as exc:
        raise ConfigurationError(f"Unsupported database backend: {backend}") from exc


def dialect_for_backend(backend):
    backend = normalize_backend(backend)
    if backend == "sqlite":
        return SQLiteDialect()
    if backend == "postgresql":
        return PostgresDialect()
    if backend == "mysql":
        return MySQLDialect()
    raise ConfigurationError(f"Unsupported database backend: {backend}")


def _looks_like_url(value):
    return isinstance(value, str) and "://" in value


def _settings_from_url(url):
    if url.startswith("sqlite:///"):
        database = url[len("sqlite:///") :]
        return {"backend": "sqlite", "database": database or ":memory:", "dsn": url}
    parsed = urlparse(url)
    backend = normalize_backend(parsed.scheme)
    settings = {"backend": backend, "dsn": url}
    if parsed.path and parsed.path != "/":
        settings["database"] = unquote(parsed.path.lstrip("/"))
    if parsed.username:
        settings["user"] = unquote(parsed.username)
    if parsed.password:
        settings["password"] = unquote(parsed.password)
    if parsed.hostname:
        settings["host"] = parsed.hostname
    if parsed.port:
        settings["port"] = parsed.port
    settings.update(dict(parse_qsl(parsed.query)))
    return settings


def _normalize_option_keys(options):
    mapping = {
        "NAME": "database",
        "USER": "user",
        "PASSWORD": "password",
        "HOST": "host",
        "PORT": "port",
        "ENGINE": "backend",
    }
    normalized = {}
    for key, value in options.items():
        normalized[mapping.get(key, key)] = value
    return normalized


default_db = Database()
connections = {"default": default_db}
