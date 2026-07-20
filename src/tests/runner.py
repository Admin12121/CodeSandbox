"""
CodeSandbox Test Runner
Feature-first test suite with interactive TUI.

Usage:
    cd /path/to/project
    uv run  tests/runner.py
"""
from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse
from typing import Callable

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty

_TESTS_DIR   = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR     = os.path.dirname(_TESTS_DIR)
_REPO_ROOT   = os.path.dirname(_SRC_DIR)
# _REPO_ROOT is what lets tests import the standalone `worker/` package
# (worker/runtime/docker_client.py etc.) — it has its own pyproject.toml
# and isn't part of the `codesandbox` package under src/.
for _p in (_SRC_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_RST = "\033[0m"
_BLD = "\033[1m"
_DIM = "\033[2m"
_GRN = "\033[32m"
_RED = "\033[31m"
_CYN = "\033[36m"
_YLW = "\033[33m"

def _c(code: str, text: str) -> str:
    return f"{code}{text}{_RST}"

_BAR_W = 20
_FILL  = "█"
_EMPTY = "░"
_SPIN  = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

def _bar(pct: float, color: str = "") -> str:
    n = int(_BAR_W * pct / 100)
    filled = _FILL * n
    empty  = _EMPTY * (_BAR_W - n)
    if color:
        return f"{color}{filled}{_RST}{empty}"
    return filled + empty

def _getch() -> str:
    if os.name == "nt":
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            key = msvcrt.getwch()
            if key == "H":
                return "UP"
            if key == "P":
                return "DOWN"
            return "ESC"
        return ch

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = os.read(fd, 1)
        if ch == b"\x1b":
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                seq = os.read(fd, 2)
                if seq == b"[A": return "UP"
                if seq == b"[B": return "DOWN"
            return "ESC"
        return ch.decode("utf-8", errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

_INTEGRATION_SUITE_MODULES = [
    ("identity",      "tests.identity.test_auth"),
    ("organizations", "tests.organizations.test_orgs"),
    ("org_rbac",      "tests.organizations.test_rbac"),
    ("system_rbac",   "tests.platform_admin.test_system_rbac"),
    ("platform_admin","tests.platform_admin.test_rbac"),
    ("sandbox",       "tests.sandbox.test_runtime_policy"),
    ("org_sandbox",   "tests.sandbox.test_org_allocations"),
    ("sandbox_templates", "tests.sandbox.test_dynamic_templates"),
    ("billing",       "tests.sandbox.test_billing_idempotency"),
    ("sandbox_publish", "tests.sandbox.test_publish_lifecycle"),
    ("sandbox_test_launch", "tests.sandbox.test_launch_gating"),
    ("sandbox_content_guards", "tests.sandbox.test_template_content_guards"),
    ("sandbox_ui_workflow", "tests.sandbox.test_ui_workflow"),
    ("workflow",      "tests.workflow.test_workflow_graph"),
    ("finance",       "tests.finance.test_finance_module"),
    ("worker",        "tests.fleet.test_migrations"),
    ("worker_routing", "tests.fleet.test_multi_worker_routing"),
    ("worker_registry", "tests.fleet.test_registry"),
    ("security_slugs", "tests.fleet.test_no_hardcoded_slugs"),
    ("security_docker", "tests.fleet.test_docker_backend_guard"),
    ("security_nats", "tests.fleet.test_nats_auth_guard"),
    ("security_nats_subjects", "tests.fleet.test_nats_subject_grants"),
]

_E2E_SUITE_MODULES = [
    ("user_lifecycle", "tests.e2e.test_user_lifecycle"),
    ("rbac_lifecycle", "tests.e2e.test_rbac_lifecycle"),
    ("template_lifecycle", "tests.e2e.test_template_lifecycle"),
]

# Existing callers use this name for the integration collection.
_SUITE_MODULES = _INTEGRATION_SUITE_MODULES
_ALL_SUITE_MODULES = [*_E2E_SUITE_MODULES, *_INTEGRATION_SUITE_MODULES]
_SUITE_LABELS = {
    "user_lifecycle": "User lifecycle",
    "rbac_lifecycle": "RBAC lifecycle",
    "template_lifecycle": "Template lifecycle",
}

_DOCKER_NETWORK_REQUIRED_SUITES = {
    "user_lifecycle",
    "rbac_lifecycle",
    "template_lifecycle",
    "identity",
    "organizations",
    "org_rbac",
    "system_rbac",
    "platform_admin",
    "org_sandbox",
    "billing",
    "sandbox_publish",
    "sandbox_test_launch",
    "sandbox_ui_workflow",
    "finance",
    "worker",
    "worker_routing",
    "worker_registry",
}

_APP_CONTEXT_REQUIRED_SUITES = set(_DOCKER_NETWORK_REQUIRED_SUITES)
_RESULT_JSON_FLAG = "--result-json"
_RESULT_JSON_MARKER = "__CODESANDBOX_TEST_REPORT__="


def _load_suites(modules: list[tuple[str, str]] | None = None) -> dict[str, list]:
    result: dict[str, list] = {}
    errors: list[str] = []
    for suite_name, mod_path in modules or _SUITE_MODULES:
        try:
            mod = importlib.import_module(mod_path)
            result[suite_name] = getattr(mod, "TESTS", [])
        except Exception as exc:
            errors.append(f"{suite_name}: {exc}")
            result[suite_name] = []
    if errors:
        print()
        for err in errors:
            print(f"  {_c(_RED, '✗')} Load error — {err}", file=sys.stderr)
    return result

def _header() -> None:
    print()
    print(f"  {_c(_BLD + _CYN, 'CodeSandbox Test Runner')}")
    print()

def _selector(suites: dict[str, list], *, group_label: str) -> str | None:
    counts = [(k, len(v)) for k, v in suites.items()]
    all_count = sum(c for _, c in counts)
    options: list[tuple[str, int]] = [("All", all_count)] + counts
    if not sys.stdin.isatty():
        for index, (name, count) in enumerate(options):
            print(f"  {index}. {_SUITE_LABELS.get(name, name)} [{count} tests]")
        try:
            raw = input("  Select a test suite number: ").strip()
        except EOFError:
            raw = "0"
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        try:
            selected_index = int(raw)
        except ValueError:
            selected_index = 0
        if selected_index < 0 or selected_index >= len(options):
            selected_index = 0
        return "all" if selected_index == 0 else options[selected_index][0]

    cursor = 0
    n_opts = len(options)
    _LINES = n_opts + 5

    _name_col = max(len(_SUITE_LABELS.get(n, n)) for n, _ in options) + 2

    def _draw(first: bool = False) -> None:
        if not first:
            sys.stdout.write(f"\033[{_LINES}A\033[J")
        print(f"  {_c(_BLD, f'Select a {group_label} test suite:')}")
        print()
        for i, (name, count) in enumerate(options):
            dot       = _c(_CYN, "●") if i == cursor else _c(_DIM, "○")
            name_pad  = _SUITE_LABELS.get(name, name).ljust(_name_col)
            label     = _c(_BLD + _CYN, name_pad) if i == cursor else name_pad
            count_str = _c(_DIM, f"[{count:>3} tests]")
            print(f"  {dot} {label}{count_str}")
            if i == 0:
                print(f"  {_c(_DIM, '─' * (_name_col + 14))}")
        print()
        print(f"  {_c(_DIM, '↑↓ navigate   Enter run   q quit')}")
        sys.stdout.flush()

    _draw(first=True)

    while True:
        key = _getch()
        if key == "UP":
            cursor = (cursor - 1) % n_opts
            _draw()
        elif key == "DOWN":
            cursor = (cursor + 1) % n_opts
            _draw()
        elif key in ("\r", "\n"):
            sys.stdout.write(f"\033[{_LINES}A\033[J")
            sys.stdout.flush()
            return "all" if cursor == 0 else options[cursor][0]
        elif key in ("q", "\x03", "ESC"):
            sys.stdout.write(f"\033[{_LINES}A\033[J")
            sys.stdout.flush()
            return None


def _group_selector(suite_groups: dict[str, dict[str, list]]) -> str | None:
    options = [
        ("e2e", "E2E", sum(len(tests) for tests in suite_groups["e2e"].values())),
        (
            "integration",
            "Integration tests",
            sum(len(tests) for tests in suite_groups["integration"].values()),
        ),
    ]
    if not sys.stdin.isatty():
        for index, (_key, label, count) in enumerate(options):
            print(f"  {index}. {label} [{count} tests]")
        try:
            raw = input("  Select a test type number: ").strip()
        except EOFError:
            raw = "0"
        if raw.lower() in {"q", "quit", "exit"}:
            return None
        try:
            selected_index = int(raw)
        except ValueError:
            selected_index = 0
        if selected_index < 0 or selected_index >= len(options):
            selected_index = 0
        return options[selected_index][0]

    cursor = 0
    lines = len(options) + 4

    def draw(first: bool = False) -> None:
        if not first:
            sys.stdout.write(f"\033[{lines}A\033[J")
        print(f"  {_c(_BLD, 'Select a test type:')}")
        print()
        for index, (_key, label, count) in enumerate(options):
            dot = _c(_CYN, "●") if index == cursor else _c(_DIM, "○")
            display = _c(_BLD + _CYN, label) if index == cursor else label
            print(f"  {dot} {display:<24}{_c(_DIM, f'[{count:>3} tests]')}")
        print()
        print(f"  {_c(_DIM, '↑↓ navigate   Enter select   q quit')}")
        sys.stdout.flush()

    draw(first=True)
    while True:
        key = _getch()
        if key == "UP":
            cursor = (cursor - 1) % len(options)
            draw()
        elif key == "DOWN":
            cursor = (cursor + 1) % len(options)
            draw()
        elif key in ("\r", "\n"):
            sys.stdout.write(f"\033[{lines}A\033[J")
            sys.stdout.flush()
            return options[cursor][0]
        elif key in ("q", "\x03", "ESC"):
            sys.stdout.write(f"\033[{lines}A\033[J")
            sys.stdout.flush()
            return None

_NAME_W = 38

@dataclass
class _Result:
    global_n: int
    local_n: int
    name: str
    category: str
    passed: bool
    skipped: bool = False
    error: str | None = None


def _run_one(local_n: int, global_n: int, test_case) -> _Result:
    from tests._context import TestContext

    name     = test_case.name
    name_pad = (name[:_NAME_W - 1] + "…") if len(name) >= _NAME_W else name
    name_pad = name_pad.ljust(_NAME_W)

    stop    = threading.Event()
    spin_i  = [0]

    def _spin() -> None:
        while not stop.is_set():
            frame = _c(_YLW, _SPIN[spin_i[0] % len(_SPIN)])
            sys.stdout.write(
                f"\r  test-{local_n:<3}  {name_pad}  {_bar(0)}    0%  {frame}"
            )
            sys.stdout.flush()
            time.sleep(0.08)
            spin_i[0] += 1

    t = threading.Thread(target=_spin, daemon=True)
    t.start()

    ctx = TestContext()
    passed = False
    error: str | None = None
    try:
        test_case.fn(ctx)
        passed = True
    except Exception as exc:
        error = str(exc) or type(exc).__name__
    finally:
        ctx.cleanup()
        stop.set()
        t.join()

    if passed:
        bar_str = _bar(100, _GRN)
        mark    = _c(_GRN, "✔ pass  ")
    else:
        bar_str = _bar(100, _RED)
        mark    = _c(_RED, "✗ fail  ")

    sys.stdout.write(
        f"\r  test-{local_n:<3}  {name_pad}  {bar_str}  100%  {mark}\n"
    )
    if not passed and error:
        short = error.replace("\n", " ").strip()[:90]
        sys.stdout.write(f"            {_c(_DIM, '↳ ' + short)}\n")
    sys.stdout.flush()

    return _Result(
        global_n=global_n,
        local_n=local_n,
        name=test_case.name,
        category=test_case.category,
        passed=passed,
        skipped=False,
        error=error,
    )

def _skip_one(local_n: int, global_n: int, suite_name: str, test_case, reason: str) -> _Result:
    name_pad = (test_case.name[:_NAME_W - 1] + "…") if len(test_case.name) >= _NAME_W else test_case.name
    name_pad = name_pad.ljust(_NAME_W)
    sys.stdout.write(
        f"  test-{local_n:<3}  {name_pad}  {_bar(100, _YLW)}  100%  {_c(_YLW, '○ skip  ')}\n"
    )
    sys.stdout.write(f"            {_c(_DIM, '↳ ' + reason)}\n")
    sys.stdout.flush()
    return _Result(
        global_n=global_n,
        local_n=local_n,
        name=test_case.name,
        category=suite_name,
        passed=False,
        skipped=True,
        error=reason,
    )

def _print_table(results: list[_Result]) -> int:
    name_col_w = max((len(r.name) for r in results), default=20) + 6
    name_col_w = max(name_col_w, 32)

    top = "┌" + "─" * (name_col_w + 2) + "┬──────────────┐"
    mid = "├" + "─" * (name_col_w + 2) + "┼──────────────┤"
    bot = "└" + "─" * (name_col_w + 2) + "┴──────────────┘"

    _STATUS_W = 12

    print()
    print(f"  {_c(_DIM, top)}")
    hdr_name   = _c(_BLD, f"{'Test':<{name_col_w}}")
    hdr_status = _c(_BLD, "Status") + " " * (_STATUS_W - len("Status"))
    print(f"  {_c(_DIM, '│')} {hdr_name} {_c(_DIM, '│')} {hdr_status} {_c(_DIM, '│')}")
    print(f"  {_c(_DIM, mid)}")

    for r in results:
        cell = f"{r.global_n:<2}  {r.name}"
        if r.skipped:
            s_text = "○ skip"
            status = _c(_YLW, s_text) + " " * (_STATUS_W - len(s_text))
        elif r.passed:
            s_text = "✔ pass"
            status = _c(_GRN, s_text) + " " * (_STATUS_W - len(s_text))
        else:
            s_text = "● failed"
            status = _c(_RED, s_text) + " " * (_STATUS_W - len(s_text))
        print(f"  {_c(_DIM, '│')} {cell:<{name_col_w}} {_c(_DIM, '│')} {status} {_c(_DIM, '│')}")

    print(f"  {_c(_DIM, bot)}")

    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    skipped = sum(1 for r in results if r.skipped)
    failed = total - passed - skipped

    print()
    print(f"  {_c(_DIM, 'total tests  ')}{total}")
    print(f"  {_c(_GRN, 'passed       ')}{passed}")
    print(f"  {_c(_YLW, 'skipped      ')}{skipped}")
    pcolor = _RED if failed else _DIM
    print(f"  {_c(pcolor, 'failed       ')}{failed}")
    print()
    return failed

def _service_target_from_url(raw_url: str, default_port: int) -> tuple[str, int] | None:
    try:
        parsed = urlparse(raw_url)
    except Exception:
        return None
    if not parsed.hostname:
        return None
    return parsed.hostname, int(parsed.port or default_port)

def _tcp_reachable(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False

def _docker_network_ready() -> tuple[bool, str]:
    """Return whether DB/queue-backed tests can reach internal Compose services.

    MySQL is intentionally no longer exposed to the host. When DATABASE_URL
    points at the Compose-only hostname ``mysql``, host-side test runs cannot
    execute DB-backed suites. Running through the docker-compose ``test``
    service puts the runner on the same bridge network, where this probe passes.
    """
    database_url = os.environ.get(
        "DATABASE_URL",
        "mysql://codesandbox:codesandbox@127.0.0.1:3306/codesandbox",
    )
    redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

    targets: list[tuple[str, int, str]] = []
    db_target = _service_target_from_url(database_url, 3306)
    if db_target:
        targets.append((*db_target, "mysql"))
    redis_target = _service_target_from_url(redis_url, 6379)
    if redis_target:
        targets.append((*redis_target, "redis"))

    for host, port, label in targets:
        if not _tcp_reachable(host, port):
            return False, (
                f"{label} service {host}:{port} is not reachable from this process; "
                "DB-backed tests are skipped on the host. Start the Compose stack "
                "and run `uv run test <suite>` to execute them inside Docker."
            )
    return True, "docker network services are reachable"

def _boot_app() -> bool:
    frames = _SPIN
    stop   = threading.Event()

    def _spin() -> None:
        i = 0
        while not stop.is_set():
            sys.stdout.write(
                f"\r  {_c(_YLW, frames[i % len(frames)])}  "
                f"{_c(_DIM, 'Initialising app context...')}"
            )
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    t = threading.Thread(target=_spin, daemon=True)
    t.start()
    try:
        from tests._context import boot
        boot()
        ok = True
    except Exception as exc:
        ok = False
        err = str(exc)
    finally:
        stop.set()
        t.join()
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    if ok:
        print(f"  {_c(_GRN, '✔')}  {_c(_DIM, 'App context ready.')}")
    else:
        print(f"  {_c(_RED, '✗')}  App context failed: {err}")

    return ok

def _selected_from_argv_or_env(
    suite_groups: dict[str, dict[str, list]],
) -> tuple[str, str] | None:
    args = [arg.strip() for arg in sys.argv[1:] if arg.strip()]
    env_group = os.environ.get("TEST_GROUP", "").strip().lower()
    env_suite = os.environ.get("TEST_SUITE", "").strip()
    if not args and not env_group and not env_suite:
        return None

    raw_group = ""
    raw_suite = ""
    if args and args[0].lower() in suite_groups:
        raw_group = args[0].lower()
        raw_suite = args[1] if len(args) > 1 else "all"
    elif args:
        raw_suite = args[0]
    else:
        raw_group = env_group
        raw_suite = env_suite or "all"

    if raw_suite.lower() in {"q", "quit", "exit"}:
        return ("cancel", "cancel")
    if raw_group:
        if raw_group not in suite_groups:
            raise SystemExit("Unknown test type. Valid values: e2e, integration")
        if raw_suite.lower() == "all":
            return raw_group, "all"
        if raw_suite not in suite_groups[raw_group]:
            valid = ", ".join(["all", *suite_groups[raw_group].keys()])
            raise SystemExit(
                f"Unknown {raw_group} test suite {raw_suite!r}. Valid values: {valid}"
            )
        return raw_group, raw_suite

    if raw_suite.lower() == "all":
        return "integration", "all"
    for group_name, suites in suite_groups.items():
        if raw_suite in suites:
            return group_name, raw_suite
    valid = ", ".join(
        ["e2e", "integration", "all", *suite_groups["e2e"], *suite_groups["integration"]]
    )
    raise SystemExit(f"Unknown test suite {raw_suite!r}. Valid values: {valid}")

def main() -> int:
    result_json = _RESULT_JSON_FLAG in sys.argv[1:]
    if result_json:
        sys.argv = [
            sys.argv[0],
            *(arg for arg in sys.argv[1:] if arg != _RESULT_JSON_FLAG),
        ]

    _header()

    sys.stdout.write(f"  {_c(_DIM, 'Discovering tests...')}")
    sys.stdout.flush()
    suite_groups = {
        "e2e": _load_suites(_E2E_SUITE_MODULES),
        "integration": _load_suites(_INTEGRATION_SUITE_MODULES),
    }
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()

    explicit = _selected_from_argv_or_env(suite_groups)
    if explicit == ("cancel", "cancel"):
        print(f"  {_c(_DIM, 'Cancelled.')}\n")
        return 0
    if explicit is None:
        group_name = _group_selector(suite_groups)
        if group_name is None:
            print(f"  {_c(_DIM, 'Cancelled.')}\n")
            return 0
        selected = _selector(
            suite_groups[group_name],
            group_label="E2E" if group_name == "e2e" else "Integration",
        )
    else:
        group_name, selected = explicit
    if selected is None:
        print(f"  {_c(_DIM, 'Cancelled.')}\n")
        return 0
    suites = suite_groups[group_name]

    to_run: list[tuple[str, object]] = []
    if selected == "all":
        for suite_name, tests in suites.items():
            for tc in tests:
                to_run.append((suite_name, tc))
    else:
        for tc in suites.get(selected, []):
            to_run.append((selected, tc))

    if not to_run:
        print(f"  {_c(_YLW, 'No tests found for selection.')} ({selected})\n")
        return 1

    print()
    needs_docker_network = any(suite_name in _DOCKER_NETWORK_REQUIRED_SUITES for suite_name, _tc in to_run)
    docker_network_ok = True
    docker_network_reason = ""
    if needs_docker_network:
        docker_network_ok, docker_network_reason = _docker_network_ready()
        if docker_network_ok:
            print(f"  {_c(_GRN, '✔')}  {_c(_DIM, docker_network_reason)}")
        else:
            print(f"  {_c(_YLW, '○')}  {_c(_DIM, docker_network_reason)}")

    needs_app_context = any(
        suite_name in _APP_CONTEXT_REQUIRED_SUITES
        for suite_name, _tc in to_run
        if suite_name not in _DOCKER_NETWORK_REQUIRED_SUITES or docker_network_ok
    )
    if needs_app_context and not _boot_app():
        return 1

    results: list[_Result] = []
    current_cat: str | None = None
    global_n = 0
    local_n  = 0

    for suite_name, tc in to_run:
        global_n += 1
        if suite_name != current_cat:
            current_cat = suite_name
            local_n = 0
            print()
            print(f"  {_c(_BLD + _CYN, suite_name)}")
            print(f"  {_c(_DIM, '─' * 68)}")

        local_n += 1
        if suite_name in _DOCKER_NETWORK_REQUIRED_SUITES and not docker_network_ok:
            r = _skip_one(local_n, global_n, suite_name, tc, docker_network_reason)
        else:
            r = _run_one(local_n, global_n, tc)
        results.append(r)

    failed = _print_table(results)
    if result_json:
        payload = [
            {
                "global_n": result.global_n,
                "local_n": result.local_n,
                "name": result.name,
                "category": result.category,
                "passed": result.passed,
                "skipped": result.skipped,
                "error": result.error,
            }
            for result in results
        ]
        print(_RESULT_JSON_MARKER + json.dumps(payload, ensure_ascii=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
