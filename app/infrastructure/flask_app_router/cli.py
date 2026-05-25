"""Command-line helpers for Flask App Router."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any

from flask import Flask

from .router import AppRouter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="flask-app-router")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser("build", help="Build route and asset manifests.")
    build_parser.add_argument(
        "--app",
        required=True,
        help="Import path, e.g. app:app or app:create_app().",
    )
    build_parser.add_argument(
        "--output",
        default=".flask-app-router",
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
        print(f"Built Flask App Router metadata in {result['output_dir']}")
        return 0
    return 1


def _load_app(import_path: str) -> Flask:
    module_name, separator, attr = import_path.partition(":")
    if not separator or not module_name or not attr:
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


def _router_from_app(app: Flask, router_index: int) -> AppRouter:
    state: dict[str, Any] | None = app.extensions.get("flask_app_router")
    if not state:
        raise SystemExit("No Flask App Router extension is registered on this app.")

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
