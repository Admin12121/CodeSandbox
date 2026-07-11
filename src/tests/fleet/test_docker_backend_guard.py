from __future__ import annotations

import os
import sys
import types

from tests._context import TestCase, TestContext


def _fake_docker_module(ctx: TestContext) -> types.ModuleType:
    """The `docker` SDK isn't a dependency of the app/test process (only of
    the standalone `worker/` service) — inject a minimal fake, same pattern
    already used by test_runtime_policy.py's filesystem test. `worker/runtime/
    __init__.py` eagerly imports docker_runner (for DockerRunner), so
    reaching docker_client via `worker.runtime.*` pulls in docker.errors/
    docker.types too, not just docker.tls."""
    docker_module = types.ModuleType("docker")
    errors_module = types.ModuleType("docker.errors")
    types_module = types.ModuleType("docker.types")
    tls_module = types.ModuleType("docker.tls")

    class ImageNotFound(Exception):
        pass

    class NotFound(Exception):
        pass

    class Mount:
        def __init__(self, *args, **kwargs):
            pass

    class TLSConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    errors_module.ImageNotFound = ImageNotFound
    errors_module.NotFound = NotFound
    types_module.Mount = Mount
    tls_module.TLSConfig = TLSConfig
    docker_module.errors = errors_module
    docker_module.types = types_module
    docker_module.tls = tls_module
    docker_module.from_env = lambda **kwargs: SimpleNamespaceClient("local")
    docker_module.DockerClient = lambda **kwargs: SimpleNamespaceClient("remote")

    names = ("docker", "docker.errors", "docker.types", "docker.tls")
    previous = {name: sys.modules.get(name) for name in names}
    sys.modules["docker"] = docker_module
    sys.modules["docker.errors"] = errors_module
    sys.modules["docker.types"] = types_module
    sys.modules["docker.tls"] = tls_module

    def restore() -> None:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    ctx.defer(restore)
    return docker_module


class SimpleNamespaceClient:
    def __init__(self, kind: str) -> None:
        self.kind = kind


def _docker_client_module():
    # DockerClientFactory reads os.environ fresh on every call (not cached at
    # import time), so a plain import is enough — no reload gymnastics needed,
    # and reload() actively breaks on a module fetched via the parent
    # package's cached attribute rather than a fresh sys.modules entry.
    from worker.runtime import docker_client as module

    return module


def _clear_env(*names: str, ctx: TestContext) -> None:
    for name in names:
        previous = os.environ.pop(name, None)
        if previous is not None:
            ctx.defer(lambda n=name, v=previous: os.environ.__setitem__(n, v))


def test_local_socket_refused_in_production_without_override(ctx: TestContext) -> None:
    _fake_docker_module(ctx)
    module = _docker_client_module()

    _clear_env("ALLOW_UNSAFE_DOCKER_SOCKET", ctx=ctx)
    os.environ["ENVIRONMENT"] = "production"
    os.environ["SANDBOX_DOCKER_BACKEND"] = "local_socket"
    ctx.defer(lambda: os.environ.pop("ENVIRONMENT", None))
    ctx.defer(lambda: os.environ.pop("SANDBOX_DOCKER_BACKEND", None))

    try:
        module.DockerClientFactory.validate_production_safety()
    except module.DockerBackendError:
        pass
    else:
        raise AssertionError("local_socket must be refused in production without the override flag.")


def test_local_socket_allowed_with_explicit_override(ctx: TestContext) -> None:
    _fake_docker_module(ctx)
    module = _docker_client_module()

    os.environ["ENVIRONMENT"] = "production"
    os.environ["SANDBOX_DOCKER_BACKEND"] = "local_socket"
    os.environ["ALLOW_UNSAFE_DOCKER_SOCKET"] = "true"
    ctx.defer(lambda: os.environ.pop("ENVIRONMENT", None))
    ctx.defer(lambda: os.environ.pop("SANDBOX_DOCKER_BACKEND", None))
    ctx.defer(lambda: os.environ.pop("ALLOW_UNSAFE_DOCKER_SOCKET", None))

    module.DockerClientFactory.validate_production_safety()  # must not raise


def test_local_socket_allowed_in_development(ctx: TestContext) -> None:
    _fake_docker_module(ctx)
    module = _docker_client_module()

    os.environ["ENVIRONMENT"] = "development"
    os.environ["SANDBOX_DOCKER_BACKEND"] = "local_socket"
    ctx.defer(lambda: os.environ.pop("ENVIRONMENT", None))
    ctx.defer(lambda: os.environ.pop("SANDBOX_DOCKER_BACKEND", None))

    module.DockerClientFactory.validate_production_safety()  # must not raise


def test_remote_tls_requires_docker_host(ctx: TestContext) -> None:
    _fake_docker_module(ctx)
    module = _docker_client_module()

    _clear_env("DOCKER_HOST", ctx=ctx)
    os.environ["SANDBOX_DOCKER_BACKEND"] = "remote_tls"
    ctx.defer(lambda: os.environ.pop("SANDBOX_DOCKER_BACKEND", None))

    try:
        module.DockerClientFactory.create()
    except module.DockerBackendError as exc:
        assert "DOCKER_HOST" in str(exc)
    else:
        raise AssertionError("remote_tls without DOCKER_HOST must raise DockerBackendError.")


TESTS: list[TestCase] = [
    TestCase("local_socket refused in production", "security", test_local_socket_refused_in_production_without_override),
    TestCase("local_socket allowed with override flag", "security", test_local_socket_allowed_with_explicit_override),
    TestCase("local_socket allowed in development", "security", test_local_socket_allowed_in_development),
    TestCase("remote_tls requires DOCKER_HOST", "security", test_remote_tls_requires_docker_host),
]
