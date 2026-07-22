 """
CodeSandbox Test Runner
Feature-first test suite with interactive TUI.

Usage:
    cd /path/to/project
    uv run  tests/runner.py
"""
from __future__ import annotations

import importlib
import os
import select
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass, field
from typing import Callable

_TESTS_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_TESTS_DIR)
_SRC_DIR     = os.path.join(_PROJECT_DIR, "src")
for _p in (_SRC_DIR, _PROJECT_DIR):
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

_SUITE_MODULES = [
    ("identity",      "tests.identity.test_auth"),
    ("organizations", "tests.organizations.test_orgs"),
    ("org_rbac",      "tests.organizations.test_rbac"),
    ("system_rbac",   "tests.platform_admin.test_system_rbac"),
    ("platform_admin","tests.platform_admin.test_rbac"),
]


def _load_suites() -> dict[str, list]:
    result: dict[str, list] = {}
    errors: list[str] = []
    for suite_name, mod_path in _SUITE_MODULES:
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

def _selector(suites: dict[str, list]) -> str | None:
    counts = [(k, len(v)) for k, v in suites.items()]
    all_count = sum(c for _, c in counts)
    options: list[tuple[str, int]] = [("All", all_count)] + counts
    cursor = 0
    n_opts = len(options)
    _LINES = n_opts + 5

    _name_col = max(len(n) for n, _ in options) + 2

    def _draw(first: bool = False) -> None:
        if not first:
            sys.stdout.write(f"\033[{_LINES}A\033[J")
        print(f"  {_c(_BLD, 'Select a test suite:')}")
        print()
        for i, (name, count) in enumerate(options):
            dot       = _c(_CYN, "●") if i == cursor else _c(_DIM, "○")
            name_pad  = name.ljust(_name_col)        # pad before coloring
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

_NAME_W = 38

@dataclass
class _Result:
    global_n: int
    local_n: int
    name: str
    category: str
    passed: bool
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
        error=error,
    )

def _print_table(results: list[_Result]) -> None:
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
        if r.passed:
            s_text = "✔ pass"
            status = _c(_GRN, s_text) + " " * (_STATUS_W - len(s_text))
        else:
            s_text = "● failed"
            status = _c(_RED, s_text) + " " * (_STATUS_W - len(s_text))
        print(f"  {_c(_DIM, '│')} {cell:<{name_col_w}} {_c(_DIM, '│')} {status} {_c(_DIM, '│')}")

    print(f"  {_c(_DIM, bot)}")

    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    print()
    print(f"  {_c(_DIM, 'total tests  ')}{total}")
    print(f"  {_c(_GRN, 'passed       ')}{passed}")
    pcolor = _RED if failed else _DIM
    print(f"  {_c(pcolor, 'failed       ')}{failed}")
    print()

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

def main() -> None:
    _header()

    sys.stdout.write(f"  {_c(_DIM, 'Discovering tests...')}")
    sys.stdout.flush()
    suites = _load_suites()
    sys.stdout.write("\r\033[K")
    sys.stdout.flush()

    selected = _selector(suites)
    if selected is None:
        print(f"  {_c(_DIM, 'Cancelled.')}\n")
        return

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
        return

    print()
    if not _boot_app():
        return

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
        r = _run_one(local_n, global_n, tc)
        results.append(r)

    _print_table(results)


if __name__ == "__main__":
    main()
