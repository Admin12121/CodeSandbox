from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field
from typing import Callable

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR   = os.path.join(_TESTS_DIR, "..", "src")
for _p in (_SRC_DIR, _TESTS_DIR, os.path.dirname(_TESTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@dataclass
class TestContext:
    _cleanups: list[Callable] = field(default_factory=list, repr=False)

    def defer(self, fn: Callable) -> None:
        self._cleanups.append(fn)

    def cleanup(self) -> None:
        for fn in reversed(self._cleanups):
            try:
                fn()
            except Exception:
                pass


@dataclass
class TestCase:
    name: str
    category: str
    fn: Callable[[TestContext], None]


class SkipTest(RuntimeError):
    """Mark a test as skipped without failing the suite."""


_flask_app = None
_flask_ctx = None


def boot() -> None:
    global _flask_app, _flask_ctx
    if _flask_app is not None:
        return
    os.environ.setdefault("SECRET_KEY", "test-secret-key-32chars-minimum-ok")
    os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
    from codesandbox.app import create_app
    _flask_app = create_app()
    _flask_ctx = _flask_app.app_context()
    _flask_ctx.push()


def unique(prefix: str = "t") -> str:
    return f"{prefix}_{secrets.token_hex(4)}"
