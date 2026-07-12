from __future__ import annotations

import os
import sys

from tests._context import TestCase, TestContext


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", ".."))


def _install_fake_docker_sdk() -> None:
    """runtime/terminal.py and runtime/gui.py don't import `docker`
    themselves, but importing the `runtime` *package* runs
    runtime/__init__.py, which does (`from .docker_runner import
    DockerRunner`) — and the real `docker` SDK is only a dependency of the
    standalone worker/ service, not this app/test environment. Same
    workaround test_nats_auth_guard.py already uses for the same reason.
    Unconditional (not "only if missing") — a real `docker` package could in
    principle be importable but incomplete/mocked differently by whatever
    ran earlier in this shared test process, so this always (re)installs a
    known-complete fake rather than trusting an existing sys.modules entry."""
    import types

    docker_module = types.ModuleType("docker")
    errors_module = types.ModuleType("docker.errors")

    class ImageNotFound(Exception):
        pass

    class NotFound(Exception):
        pass

    class APIError(Exception):
        pass

    class Mount:
        def __init__(self, *a, **k):
            pass

    types_module = types.ModuleType("docker.types")
    types_module.Mount = Mount
    tls_module = types.ModuleType("docker.tls")
    tls_module.TLSConfig = lambda **k: None
    errors_module.ImageNotFound = ImageNotFound
    errors_module.NotFound = NotFound
    errors_module.APIError = APIError
    docker_module.errors = errors_module
    docker_module.types = types_module
    docker_module.tls = tls_module
    docker_module.from_env = lambda **k: None
    docker_module.DockerClient = lambda **k: None
    sys.modules["docker"] = docker_module
    sys.modules["docker.errors"] = errors_module
    sys.modules["docker.types"] = types_module
    sys.modules["docker.tls"] = tls_module


def test_terminal_and_gui_output_subjects_match_worker_publish_grant(ctx: TestContext) -> None:
    """Regression test for a real bug found during Phase 10.6 verification:
    docker/nats/nats-server.conf grants the worker publish rights to
    codesandbox.sandbox.> (and the control plane subscribe rights to the
    same) — but terminal.py's and gui.py's *output* subjects used to live
    under codesandbox.worker.>, which neither side is actually permissioned
    for in that direction. NATS silently drops denied publishes (the client
    library only logs an async error, it never raises to the caller), so
    this broke real-time terminal output with no loud failure — caught only
    by manual end-to-end verification (typing a command and checking the
    echo), not by any existing test. This pins the subject scheme so it
    can't regress silently again."""
    worker_dir = os.path.join(_repo_root(), "worker")
    if worker_dir not in sys.path:
        sys.path.insert(0, worker_dir)
    _install_fake_docker_sdk()

    from runtime.terminal import DockerTerminalManager
    from runtime.gui import DockerGuiProxy

    terminal_mgr = DockerTerminalManager(registry=None, publish=lambda *a, **k: None, worker_id="worker-1")
    gui_proxy = DockerGuiProxy(registry=None, publish=lambda *a, **k: None, worker_id="worker-1")

    terminal_subject = terminal_mgr._subject("instance-123")
    gui_subject = gui_proxy._subject("instance-123")

    for subject in (terminal_subject, gui_subject):
        assert subject.startswith("codesandbox.sandbox."), (
            f"Output subject {subject!r} must live under codesandbox.sandbox.> "
            "(the worker's actual NATS publish grant) — see docker/nats/nats-server.conf"
        )
        assert not subject.startswith("codesandbox.worker."), (
            f"Output subject {subject!r} must NOT live under codesandbox.worker.> "
            "— that namespace is the control plane's publish grant (ctl/input "
            "commands sent TO the worker), not the worker's own publish grant."
        )


def test_nats_config_grants_worker_publish_to_sandbox_namespace(ctx: TestContext) -> None:
    """Cross-checks the actual nats-server.conf permission grants against
    the subject namespace the code relies on, so the two can't silently
    drift apart again."""
    conf_path = os.path.join(_repo_root(), "docker", "nats", "nats-server.conf")
    text = open(conf_path, encoding="utf-8").read()
    assert '"codesandbox.sandbox.>"' in text, (
        "nats-server.conf must grant publish rights on codesandbox.sandbox.> "
        "to the worker user — that's where terminal.output/gui.output actually live"
    )


TESTS: list[TestCase] = [
    TestCase("terminal/gui output subjects match worker publish grant", "security", test_terminal_and_gui_output_subjects_match_worker_publish_grant),
    TestCase("nats config grants worker publish to sandbox namespace", "security", test_nats_config_grants_worker_publish_to_sandbox_namespace),
]
