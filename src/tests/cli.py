from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


_COMPOSE_REQUIRED_SERVICES = {"app", "mysql", "redis", "minio", "nats"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _selected_suite(args: list[str]) -> str:
    return args[0] if args else os.environ.get("TEST_SUITE", "all")


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


def _run_in_app_container(suite: str) -> int:
    env = os.environ.copy()
    env["TEST_SUITE"] = suite
    try:
        completed = subprocess.run(
            [
                "docker", "compose", "exec", "-T",
                "-e", f"TEST_SUITE={suite}",
                "app",
                "/opt/codesandbox/.venv/bin/python", "-u",
                "src/tests/runner.py", suite,
            ],
            cwd=str(_repo_root()),
            env=env,
        )
    except FileNotFoundError:
        print("docker is not available; using host-side skips.", file=sys.stderr)
        return 127
    return int(completed.returncode)


def _suite_needs_compose_network(suite: str) -> bool:
    from tests.runner import _DOCKER_NETWORK_REQUIRED_SUITES, _SUITE_MODULES

    if suite == "all":
        return False
    known_suites = {name for name, _module in _SUITE_MODULES}
    return suite in known_suites and suite in _DOCKER_NETWORK_REQUIRED_SUITES


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--docker":
        args = args[1:]

    suite = _selected_suite(args)
    if _suite_needs_compose_network(suite) and _compose_services_are_running():
        raise SystemExit(_run_in_app_container(suite))

    from tests.runner import main as runner_main

    runner_main()


if __name__ == "__main__":
    main()
