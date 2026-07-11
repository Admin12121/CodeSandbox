from __future__ import annotations

import os

from tests._context import TestCase, TestContext

_FORBIDDEN = ("reverse-decompile", "reverse_decompile")

# Only seed.py (creating the example template) and test files themselves are
# allowed to name it — everywhere else, template behavior must be driven by
# SandboxTemplate/runtime_config data, never a literal slug/name comparison.
_ALLOWED_PATHS = ("seed.py", os.path.join("src", "tests"))


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", ".."))


def _scan(*dirs: str) -> list[str]:
    root = _repo_root()
    offenders = []
    for rel_dir in dirs:
        base = os.path.join(root, rel_dir)
        for dirpath, _dirnames, filenames in os.walk(base):
            if "__pycache__" in dirpath or ".venv" in dirpath:
                continue
            for filename in filenames:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(dirpath, filename)
                rel = os.path.relpath(path, root)
                if any(rel.startswith(allowed) for allowed in _ALLOWED_PATHS):
                    continue
                text = open(path, encoding="utf-8", errors="ignore").read().lower()
                if any(term in text for term in _FORBIDDEN):
                    offenders.append(rel)
    return offenders


def test_no_hardcoded_reverse_decompile_outside_seed(ctx: TestContext) -> None:
    offenders = _scan("src/codesandbox", "worker")
    assert not offenders, (
        "Found hardcoded reverse-decompile references outside seed.py/tests "
        f"— template behavior must come from data, not code: {offenders}"
    )


TESTS: list[TestCase] = [
    TestCase("no hardcoded reverse-decompile outside seed", "security", test_no_hardcoded_reverse_decompile_outside_seed),
]
