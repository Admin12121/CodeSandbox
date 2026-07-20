from __future__ import annotations

import json
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
    env_group = os.environ.get("TEST_GROUP", "").strip()
    env_suite = os.environ.get("TEST_SUITE", "").strip()
    if env_suite and env_suite != "all":
        return env_suite
    if env_group == "e2e":
        return "e2e"
    return "all"


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


def _is_integration_all(args: list[str]) -> bool:
    if args:
        if args[0] == "all":
            return True
        return args[0] == "integration" and (
            len(args) == 1 or args[1] == "all"
        )

    env_group = os.environ.get("TEST_GROUP", "").strip().lower()
    env_suite = os.environ.get("TEST_SUITE", "").strip().lower()
    if env_group:
        return env_group == "integration" and (not env_suite or env_suite == "all")
    return not env_suite or env_suite == "all"


def _run_reported_suite(
    suite: str,
    *,
    in_container: bool,
) -> subprocess.CompletedProcess[str]:
    runner_args = ["integration", suite, "--result-json"]
    if in_container:
        command = [
            "docker", "compose", "exec", "-T",
            "app",
            "/opt/codesandbox/.venv/bin/python", "-u",
            "src/tests/runner.py", *runner_args,
        ]
    else:
        command = [
            sys.executable,
            "-u",
            str(_repo_root() / "src" / "tests" / "runner.py"),
            *runner_args,
        ]
    return subprocess.run(
        command,
        cwd=str(_repo_root()),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _reported_results(output: str, marker: str) -> list[dict] | None:
    marker_index = output.rfind(marker)
    if marker_index < 0:
        return None
    raw = output[marker_index + len(marker):].splitlines()[0]
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, list) else None


def _run_integration_all() -> int:
    from tests.runner import (
        _DOCKER_NETWORK_REQUIRED_SUITES,
        _INTEGRATION_SUITE_MODULES,
        _RESULT_JSON_MARKER,
        _Result,
        _load_suites,
        _print_table,
    )

    suites = _load_suites(_INTEGRATION_SUITE_MODULES)
    results: list[_Result] = []
    total_suites = len(_INTEGRATION_SUITE_MODULES)

    print("\n  Running all integration tests in their required environments.\n")
    for suite_index, (suite_name, _module) in enumerate(
        _INTEGRATION_SUITE_MODULES,
        start=1,
    ):
        tests = suites[suite_name]
        in_container = suite_name in _DOCKER_NETWORK_REQUIRED_SUITES
        environment = "Docker" if in_container else "host"
        print(
            f"  [{suite_index:>2}/{total_suites}] "
            f"{suite_name:<26} {environment:<6} ",
            end="",
            flush=True,
        )

        completed = _run_reported_suite(suite_name, in_container=in_container)
        payload = _reported_results(completed.stdout, _RESULT_JSON_MARKER)
        suite_results: list[_Result] = []
        if payload is None:
            diagnostic = (completed.stderr or completed.stdout).strip()
            if len(diagnostic) > 500:
                diagnostic = diagnostic[-500:]
            diagnostic = diagnostic or (
                f"test runner exited with code {completed.returncode} without a report"
            )
            suite_results = [
                _Result(
                    global_n=0,
                    local_n=local_n,
                    name=test_case.name,
                    category=suite_name,
                    passed=False,
                    error=diagnostic,
                )
                for local_n, test_case in enumerate(tests, start=1)
            ]
        else:
            suite_results = [
                _Result(
                    global_n=0,
                    local_n=int(item.get("local_n", local_n)),
                    name=str(item.get("name", test_case.name)),
                    category=str(item.get("category", suite_name)),
                    passed=bool(item.get("passed", False)),
                    skipped=bool(item.get("skipped", False)),
                    error=item.get("error"),
                )
                for local_n, (item, test_case) in enumerate(
                    zip(payload, tests, strict=False),
                    start=1,
                )
            ]
            if len(suite_results) != len(tests):
                diagnostic = (
                    f"test runner reported {len(suite_results)} of {len(tests)} results"
                )
                for local_n, test_case in enumerate(
                    tests[len(suite_results):],
                    start=len(suite_results) + 1,
                ):
                    suite_results.append(
                        _Result(
                            global_n=0,
                            local_n=local_n,
                            name=test_case.name,
                            category=suite_name,
                            passed=False,
                            error=diagnostic,
                        )
                    )

        for result in suite_results:
            result.global_n = len(results) + 1
            results.append(result)

        passed = sum(result.passed for result in suite_results)
        skipped = sum(result.skipped for result in suite_results)
        failed = len(suite_results) - passed - skipped
        status = "PASS" if not failed and not skipped else "FAIL" if failed else "SKIP"
        print(f"{status} (passed {passed}, skipped {skipped}, failed {failed})")
        for result in suite_results:
            if result.passed or result.skipped:
                continue
            error = (result.error or "unknown failure").replace("\n", " ").strip()
            if len(error) > 180:
                error = error[:177] + "..."
            print(f"        {result.name}: {error}")

    return 1 if _print_table(results) else 0


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
    if _is_integration_all(args) and _compose_services_are_running():
        raise SystemExit(_run_integration_all())
    if _suite_needs_compose_network(suite) and _compose_services_are_running():
        raise SystemExit(_run_in_app_container(args or [suite]))

    from tests.runner import main as runner_main

    if args:
        sys.argv = [sys.argv[0], *args]
    raise SystemExit(runner_main())


if __name__ == "__main__":
    main()
