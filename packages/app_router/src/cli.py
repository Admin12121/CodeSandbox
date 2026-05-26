"""Command-line helpers for app-router."""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Any

from flask import Flask

from .router import AppRouter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app-router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build route and asset manifests.")
    build_parser.add_argument(
        "--app",
        help=(
            "Optional Flask app import path, e.g. app:app or app:create_app(). "
            "When omitted, app-router tries FLASK_APP and common app modules."
        ),
    )
    build_parser.add_argument(
        "--output",
        default=".app-router",
        help="Build output directory.",
    )
    build_parser.add_argument(
        "--router-index",
        type=int,
        default=0,
        help="Router index when app has many.",
    )

    args = parser.parse_args(argv)
    if args.command == "build":
        app = _load_app(args.app)
        router = _router_from_app(app, args.router_index)
        result = router.build(app, output_dir=Path(args.output))
        print(f"Built app-router metadata in {result['output_dir']}")
        return 0
    return 1


def _load_app(import_path: str | None = None) -> Flask:
    _ensure_cwd_on_path()

    if import_path:
        return _load_app_from_import_path(import_path)

    flask_app = os.environ.get("FLASK_APP")
    if flask_app:
        return _load_app_from_import_path(flask_app)

    for module_name in ("app", "wsgi", "main", "application"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        app = _app_from_module(module)
        if app is not None:
            return app

    raise SystemExit(
        "Could not auto-detect a Flask app. Define app or create_app() in "
        "app.py, wsgi.py, main.py, or set FLASK_APP=module:app."
    )


def _ensure_cwd_on_path() -> None:
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)


def _load_app_from_import_path(import_path: str) -> Flask:
    module_name, separator, attr = import_path.partition(":")
    if not separator:
        module = importlib.import_module(import_path)
        app = _app_from_module(module)
        if app is None:
            raise SystemExit(f"{import_path!r} does not expose a Flask app.")
        return app

    if not module_name or not attr:
        raise SystemExit("--app must look like module:app or module:create_app().")

    module = importlib.import_module(module_name)
    if attr.endswith("()"):
        factory_name = attr[:-2]
        factory = getattr(module, factory_name)
        app = factory()
    else:
        app = getattr(module, attr)

    if not isinstance(app, Flask):
        raise SystemExit("--app did not resolve to a Flask application.")
    return app


def _app_from_module(module: Any) -> Flask | None:
    for attr in ("app", "application"):
        candidate = getattr(module, attr, None)
        if isinstance(candidate, Flask):
            return candidate

    for attr in ("create_app", "make_app"):
        factory = getattr(module, attr, None)
        if not callable(factory):
            continue
        try:
            candidate = factory()
        except TypeError:
            continue
        if isinstance(candidate, Flask):
            return candidate

    return None


def _router_from_app(app: Flask, router_index: int) -> AppRouter:
    state: dict[str, Any] | None = app.extensions.get("app_router")
    if not state:
        raise SystemExit("No app-router extension is registered on this app.")

    routers = state.get("routers", [])
    try:
        router = routers[router_index]
    except IndexError as exc:
        raise SystemExit(f"No router exists at index {router_index}.") from exc

    if not isinstance(router, AppRouter):
        raise SystemExit(f"Extension router at index {router_index} is invalid.")
    return router


if __name__ == "__main__":
    raise SystemExit(main())
