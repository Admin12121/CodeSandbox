import argparse
import code
import importlib
from pathlib import Path
from nexorm.database import configure, default_db
from nexorm.migrations.autodetector import MigrationAutodetector
from nexorm.migrations.engine import MigrationEngine
from nexorm.migrations.state import model_state
from nexorm.migrations.writer import MigrationWriter


def load_models(module_name):
    importlib.import_module(module_name)


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
    print("Created migrations/" if not any(migrations_path.iterdir()) else "migrations/ already exists")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="nexorm")
    parser.add_argument("--database", default="db.sqlite3")
    parser.add_argument("--models", default="app.models")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--force", action="store_true")
    sub.add_parser("makemigrations")
    sub.add_parser("migrate")
    sub.add_parser("showmigrations")
    rollback = sub.add_parser("rollback")
    sqlmigrate = sub.add_parser("sqlmigrate")
    sqlmigrate.add_argument("name")
    sub.add_parser("dbshell")
    args = parser.parse_args(argv)

    if args.command == "init":
        init_project(args.force)
        return

    configure(args.database)
    if args.command != "dbshell":
        load_models(args.models)

    if args.command == "makemigrations":
        old = MigrationEngine().project_state()
        new = model_state()
        ops = MigrationAutodetector(old, new).changes()
        if not ops:
            print("No changes detected")
            return
        path = MigrationWriter(ops, new).write()
        print(f"Created {path}")
    elif args.command == "migrate":
        for name in MigrationEngine().apply_pending():
            print(f"Applied {name}")
    elif args.command == "showmigrations":
        for name, applied in MigrationEngine().status():
            print(f"[{'x' if applied else ' '}] {name}")
    elif args.command == "rollback":
        name = MigrationEngine().rollback_latest()
        print(f"Rolled back {name}" if name else "No migrations to rollback")
    elif args.command == "sqlmigrate":
        for sql in MigrationEngine().sqlmigrate(args.name):
            print(sql + ";")
    elif args.command == "dbshell":
        code.interact(local={"db": default_db})
