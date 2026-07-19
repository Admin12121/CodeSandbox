from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_COMPOSE_REQUIRED_SERVICES = {"app", "mysql", "redis", "minio", "nats"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _selected_suite(args: list[str]) -> str:
    if args:
        if args[0] == "e2e":
            return "e2e"
        if args[0] == "integration" and len(args) > 1:
            return args[1]
        return args[0]
    return os.environ.get("TEST_GROUP", "").strip() or os.environ.get("TEST_SUITE", "all")


def _compose_services_are_running() -> bool:
    try:
        completed = subprocess.run(
            ["docker", "compose", "ps", "--services", "--filter", "status=running"],
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    running = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    return _COMPOSE_REQUIRED_SERVICES.issubset(running)


def _run_in_app_container(args: list[str]) -> int:
    target_args = args or ["all"]
    env = os.environ.copy()
    try:
        completed = subprocess.run(
            [
                "docker", "compose", "exec", "-T",
                "app",
                "/opt/codesandbox/.venv/bin/python", "-u",
                "src/tests/runner.py", *target_args,
            ],
            cwd=str(_repo_root()),
            env=env,
        )
    except FileNotFoundError:
        print("docker is not available; using host-side skips.", file=sys.stderr)
        return 127
    return int(completed.returncode)


def _suite_needs_compose_network(suite: str) -> bool:
    from tests.runner import _ALL_SUITE_MODULES, _DOCKER_NETWORK_REQUIRED_SUITES

    if suite == "e2e":
        return True
    if suite in {"all", "integration"}:
        return False
    known_suites = {name for name, _module in _ALL_SUITE_MODULES}
    return suite in known_suites and suite in _DOCKER_NETWORK_REQUIRED_SUITES


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--docker":
        args = args[1:]

    has_env_selection = bool(
        os.environ.get("TEST_GROUP", "").strip()
        or os.environ.get("TEST_SUITE", "").strip()
    )
    if not args and not has_env_selection:
        from tests.runner import (
            _E2E_SUITE_MODULES,
            _INTEGRATION_SUITE_MODULES,
            _group_selector,
            _load_suites,
            _selector,
        )

        suite_groups = {
            "e2e": _load_suites(_E2E_SUITE_MODULES),
            "integration": _load_suites(_INTEGRATION_SUITE_MODULES),
        }
        group = _group_selector(suite_groups)
        if group is None:
            return
        selected = _selector(
            suite_groups[group],
            group_label="E2E" if group == "e2e" else "Integration",
        )
        if selected is None:
            return
        args = [group, selected]

    suite = _selected_suite(args)
    if _suite_needs_compose_network(suite) and _compose_services_are_running():
        raise SystemExit(_run_in_app_container(args or [suite]))

    from tests.runner import main as runner_main

    if args:
        sys.argv = [sys.argv[0], *args]
    raise SystemExit(runner_main())


if __name__ == "__main__":
    main()
