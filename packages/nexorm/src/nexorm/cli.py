import argparse
import code
import importlib
import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from nexorm.database import configure, default_db
from nexorm.migrations.autodetector import MigrationAutodetector
from nexorm.migrations.engine import MigrationEngine
from nexorm.migrations.state import model_state
from nexorm.migrations.writer import MigrationWriter


def load_models(module_name):
    importlib.import_module(module_name)


def load_config(path=None):
    config_path = Path(path) if path else find_config()
    if config_path is None:
        return {}
    if not config_path.exists():
        raise ValueError(f"NexORM config file does not exist: {config_path}")
    try:
        data = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid NexORM config in {config_path}: {exc}") from exc

    if config_path.name == "pyproject.toml":
        data = data.get("tool", {}).get("nexorm", {})
    else:
        data = data.get("nexorm", data)
    if not isinstance(data, dict):
        raise ValueError(f"NexORM config in {config_path} must be a table")
    return data


def find_config():
    for directory in [Path.cwd(), *Path.cwd().parents]:
        pyproject = directory / "pyproject.toml"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text())
            except tomllib.TOMLDecodeError:
                data = {}
            if data.get("tool", {}).get("nexorm") is not None:
                return pyproject

        nexorm_toml = directory / "nexorm.toml"
        if nexorm_toml.exists():
            return nexorm_toml
    return None


def pick_config_value(config, key, default=None):
    value = config.get(key, default)
    return default if value is None else value


def configured_database(args, config):
    env_name = args.database_url_env or config.get("database_url_env")
    env_url = os.getenv(env_name) if env_name else None
    if (
        env_name
        and not env_url
        and not args.url
        and not args.database
        and not config.get("url")
        and not config.get("database")
    ):
        raise ValueError(f"NexORM database URL env var is not set: {env_name}")

    return (
        args.url
        or args.database
        or env_url
        or config.get("url")
        or config.get("database")
        or "db.sqlite3"
    )


def init_project(force=False):
    manage_path = Path("manage.py")
    migrations_path = Path("migrations")

    if manage_path.exists() and not force:
        print("manage.py already exists; use --force to overwrite it")
    else:
        manage_path.write_text(
            'from nexorm.cli import main\n\n\nif __name__ == "__main__":\n    main()\n'
        )
        print("Created manage.py")

    migrations_path.mkdir(exist_ok=True)
    print(
        "Created migrations/"
        if not any(migrations_path.iterdir())
        else "migrations/ already exists"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nexorm")
    parser.add_argument("--config")
    parser.add_argument(
        "--backend", choices=["sqlite", "postgresql", "postgres", "mysql"]
    )
    parser.add_argument("--database")
    parser.add_argument("--url")
    parser.add_argument("--database-url-env")
    parser.add_argument("--dsn")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--models")
    parser.add_argument("--migrations-dir")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    sub.add_parser("makemigrations")
    sub.add_parser("migrate")
    sub.add_parser("showmigrations")
    sub.add_parser("rollback")
    sqlmigrate = sub.add_parser("sqlmigrate")
    sqlmigrate.add_argument("name")
    sub.add_parser("dbshell")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser().resolve() if args.config else find_config()
    try:
        config = load_config(config_path)
    except ValueError as exc:
        parser.error(str(exc))

    project_root = config_path.parent if config_path is not None else Path.cwd()
    migrations_dir = Path(
        args.migrations_dir or pick_config_value(config, "migrations_dir", "migrations")
    ).expanduser()
    if not migrations_dir.is_absolute():
        migrations_dir = (project_root / migrations_dir).resolve()

    if args.command == "init":
        init_project(args.force)
        return

    db_options = {
        "dsn": args.dsn,
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
    }
    db_options = {
        key: value if value is not None else config.get(key)
        for key, value in db_options.items()
    }
    db_options = {key: value for key, value in db_options.items() if value is not None}
    backend = args.backend or pick_config_value(config, "backend", "sqlite")

    if args.command == "makemigrations":
        # Schema diffing does not need a live database. Avoid requiring
        # DATABASE_URL merely to generate migration files.
        configure(":memory:", backend="sqlite")
    else:
        try:
            database = configured_database(args, config)
        except ValueError as exc:
            parser.error(str(exc))
        configure(database, backend=backend, **db_options)
    if args.command != "dbshell":
        load_models(args.models or pick_config_value(config, "models", "app.models"))

    engine = MigrationEngine(migrations_dir=migrations_dir)

    if args.command == "makemigrations":
        old = engine.project_state()
        new = model_state()
        ops = MigrationAutodetector(old, new).changes()
        if not ops:
            print("No changes detected")
            return
        path = MigrationWriter(ops, new, migrations_dir=migrations_dir).write()
        print(f"Created {path}")
    elif args.command == "migrate":
        for name in engine.apply_pending():
            print(f"Applied {name}")
    elif args.command == "showmigrations":
        for name, applied in engine.status():
            print(f"[{'x' if applied else ' '}] {name}")
    elif args.command == "rollback":
        name = engine.rollback_latest()
        print(f"Rolled back {name}" if name else "No migrations to rollback")
    elif args.command == "sqlmigrate":
        for sql in engine.sqlmigrate(args.name):
            print(sql + ";")
    elif args.command == "dbshell":
        code.interact(local={"db": default_db})
