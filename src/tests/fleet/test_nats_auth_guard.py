from __future__ import annotations

import os
import subprocess
import sys

from tests._context import TestCase, TestContext


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", ".."))


def test_control_plane_refuses_production_without_nats_creds(ctx: TestContext) -> None:
    """asgi.py's fail-closed guard runs at import time — verified in a fresh
    subprocess so it can't be masked by codesandbox.asgi already being
    imported (and cached) earlier in this same test process."""
    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": "production",
        "SECRET_KEY": "x" * 32,
        "DATABASE_URL": env.get("DATABASE_URL", "mysql://codesandbox:codesandbox@mysql:3306/codesandbox"),
        "NATS_USER": "",
        "NATS_PASSWORD": "",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import codesandbox.asgi"],
        cwd=os.path.join(_repo_root(), "src"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "NATS_USER/NATS_PASSWORD are required" in result.stderr


def test_control_plane_starts_with_nats_creds_in_production(ctx: TestContext) -> None:
    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": "production",
        "SECRET_KEY": "x" * 32,
        "DATABASE_URL": env.get("DATABASE_URL", "mysql://codesandbox:codesandbox@mysql:3306/codesandbox"),
        "NATS_USER": "control-plane",
        "NATS_PASSWORD": "some-password",
    })
    result = subprocess.run(
        [sys.executable, "-c", "import codesandbox.asgi; print('IMPORT_OK')"],
        cwd=os.path.join(_repo_root(), "src"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert "IMPORT_OK" in result.stdout, result.stderr


_FAKE_DOCKER_SDK = """
import sys, types
docker_module = types.ModuleType("docker")
errors_module = types.ModuleType("docker.errors")
types_module = types.ModuleType("docker.types")
tls_module = types.ModuleType("docker.tls")
class ImageNotFound(Exception): pass
class NotFound(Exception): pass
class Mount:
    def __init__(self, *a, **k): pass
class TLSConfig:
    def __init__(self, **k): pass
errors_module.ImageNotFound = ImageNotFound
errors_module.NotFound = NotFound
types_module.Mount = Mount
tls_module.TLSConfig = TLSConfig
docker_module.errors = errors_module
docker_module.types = types_module
docker_module.tls = tls_module
docker_module.from_env = lambda **k: None
docker_module.DockerClient = lambda **k: None
sys.modules["docker"] = docker_module
sys.modules["docker.errors"] = errors_module
sys.modules["docker.types"] = types_module
sys.modules["docker.tls"] = tls_module
"""


def test_worker_refuses_production_without_nats_creds(ctx: TestContext) -> None:
    """worker/main.py's own internal imports (`from runtime.X import Y`) are
    written for how it's actually run in production — with /app/worker
    itself (not the repo root) as the sys.path entry — so that's what this
    test adds, isolated to this test rather than the global test-runner path.
    The real `docker` SDK is only a dependency of the standalone worker/
    service (not this app/test environment), so it's faked the same way
    test_runtime_policy.py's worker filesystem test already does."""
    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": "production",
        "NATS_USER": "",
        "NATS_PASSWORD": "",
    })
    worker_dir = os.path.join(_repo_root(), "worker")
    script = _FAKE_DOCKER_SDK + "\nimport main\nmain._require_nats_auth_in_production()\n"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=worker_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "NATS_USER/NATS_PASSWORD are required" in result.stderr


TESTS: list[TestCase] = [
    TestCase("control plane refuses prod without NATS creds", "security", test_control_plane_refuses_production_without_nats_creds),
    TestCase("control plane starts with NATS creds in prod", "security", test_control_plane_starts_with_nats_creds_in_production),
    TestCase("worker refuses prod without NATS creds", "security", test_worker_refuses_production_without_nats_creds),
]
