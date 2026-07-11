from __future__ import annotations

from decimal import Decimal
import sys
import types
from types import SimpleNamespace

from tests._context import TestCase, TestContext


def _template(**overrides):
    data = {
        "id": "tpl_1",
        "slug": "linux-lab",
        "name": "Linux Lab",
        "sandbox_type": "coding",
        "runtime_class": "container",
        "docker_image": "ubuntu:22.04",
        "interface_mode": "terminal",
        "network_mode": "disabled",
        "allow_full_internet": False,
        "default_command": "/bin/sh",
        "working_dir": "/workspace",
        "input_mount_path": "/input",
        "output_mount_path": "/output",
        "artifact_paths": '["/output"]',
        "input_required": False,
        "max_upload_mb": 50,
        "read_only_root": True,
        "allow_root": False,
        "run_as_user": "65532:65532",
        "pids_limit": 256,
        "max_timeout_hr": 2,
        "runtime_config": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _plan(**overrides):
    data = {
        "id": "basic",
        "name": "Basic",
        "ind_vcpu": 1,
        "ind_ram_gb": 1,
        "ind_disk_gb": 10,
        "ind_cost_hr": Decimal("0.1000"),
        "org_vcpu": 2,
        "org_ram_gb": 2,
        "org_disk_gb": 20,
        "org_cost_hr": Decimal("0.1500"),
        "min_billable_minutes": 1,
        "allowed_network_modes": '["disabled","restricted"]',
        "is_active": True,
        "sort_order": 0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _template_plan(**overrides):
    data = {
        "is_enabled": True,
        "ind_vcpu": None,
        "ind_ram_gb": None,
        "ind_disk_gb": None,
        "ind_cost_hr": None,
        "org_vcpu": None,
        "org_ram_gb": None,
        "org_disk_gb": None,
        "org_cost_hr": None,
        "max_timeout_hr": None,
        "network_mode": None,
        "min_billable_minutes": None,
        "full_internet_enabled": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_effective_plan_merges_template_overrides(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import resolve_effective_plan

    effective = resolve_effective_plan(
        _template(network_mode="disabled"),
        _plan(),
        _template_plan(
            ind_vcpu=3,
            ind_ram_gb=4,
            ind_disk_gb=30,
            ind_cost_hr=Decimal("0.2500"),
            org_vcpu=6,
            org_ram_gb=8,
            org_disk_gb=60,
            org_cost_hr=Decimal("0.4000"),
            max_timeout_hr=6,
            network_mode="restricted",
            min_billable_minutes=5,
        ),
    )

    assert effective.ind_vcpu == 3
    assert effective.ind_ram_gb == 4
    assert effective.ind_disk_gb == 30
    assert effective.ind_cost_hr == Decimal("0.2500")
    assert effective.org_vcpu == 6
    assert effective.org_ram_gb == 8
    assert effective.org_disk_gb == 60
    assert effective.org_cost_hr == Decimal("0.4000")
    assert effective.max_timeout_hr == 6
    assert effective.network_mode == "restricted"
    assert effective.min_billable_minutes == 5


def test_disabled_template_plan_rejected_by_service(ctx: TestContext) -> None:
    from codesandbox.features.sandbox import service

    original_repo = service.repository

    class FakeRepository:
        @staticmethod
        def get_template(template_id: str):
            return _template(id=template_id)

        @staticmethod
        def get_plan(plan_id: str):
            return _plan(id=plan_id)

        @staticmethod
        def get_template_plan(template_id: str, plan_id: str):
            return _template_plan(is_enabled=False)

    service.repository = FakeRepository()
    ctx.defer(lambda: setattr(service, "repository", original_repo))

    effective, error = service.get_effective_plan("tpl_1", "basic")

    assert effective is None
    assert error == "This plan is disabled for the selected template."


def test_full_internet_blocked_for_reverse_engineering(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import RuntimePolicyError, resolve_effective_plan

    try:
        resolve_effective_plan(
            _template(
                sandbox_type="reverse_engineering",
                network_mode="full_internet",
                allow_full_internet=True,
            ),
            _plan(allowed_network_modes='["disabled","restricted","full_internet"]'),
            _template_plan(full_internet_enabled=True),
        )
    except RuntimePolicyError as exc:
        assert "Full internet is not enabled" in str(exc)
    else:
        raise AssertionError("Reverse-engineering templates must not resolve full internet access.")


def _runtime_config(**settings) -> str:
    import json as _json

    return _json.dumps({"runtime.json": _json.dumps(settings)})


def test_required_args_enforced_generically(ctx: TestContext) -> None:
    """Any template can require a command-line flag via its own
    runtime_config — this used to be a hardcoded `slug == "reverse-decompile"`
    check; proving it works for an unrelated slug/template shows the
    behavior is now entirely data-driven, not special-cased in code."""
    from codesandbox.features.sandbox.runtime.policy import (
        PolicyBuilder,
        RuntimePolicyError,
        resolve_effective_plan,
    )

    effective = resolve_effective_plan(
        _template(slug="totally-unrelated-template", sandbox_type="reverse_engineering"),
        _plan(),
    )
    try:
        PolicyBuilder().build(
            _template(
                slug="totally-unrelated-template",
                sandbox_type="reverse_engineering",
                docker_image="docker.io/admin12121/decompile:stable",
                default_command="decompile /input/sample /output",
                runtime_config=_runtime_config(required_args=["--no-ai"]),
            ),
            effective,
        )
    except RuntimePolicyError as exc:
        assert "--no-ai" in str(exc)
    else:
        raise AssertionError("required_args from runtime_config must be enforced regardless of slug.")

    # And it must pass once the command actually includes the required flag —
    # again, driven purely by this template's own config, not its name.
    policy = PolicyBuilder().build(
        _template(
            slug="totally-unrelated-template",
            sandbox_type="reverse_engineering",
            docker_image="docker.io/admin12121/decompile:stable",
            default_command="decompile --no-ai /input/sample /output",
            runtime_config=_runtime_config(required_args=["--no-ai"]),
        ),
        effective,
    )
    assert policy["required_args"] == ["--no-ai"]


def test_forbidden_args_enforced_generically(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import (
        PolicyBuilder,
        RuntimePolicyError,
        resolve_effective_plan,
    )

    effective = resolve_effective_plan(_template(), _plan())
    try:
        PolicyBuilder().build(
            _template(
                default_command="/bin/sh --danger-flag",
                runtime_config=_runtime_config(forbidden_args=["--danger-flag"]),
            ),
            effective,
        )
    except RuntimePolicyError as exc:
        assert "--danger-flag" in str(exc)
    else:
        raise AssertionError("forbidden_args from runtime_config must be enforced.")


def test_no_hardcoded_template_slug_in_policy_builder(ctx: TestContext) -> None:
    """Regression guard: PolicyBuilder must never branch on a literal
    template slug/name again."""
    import inspect

    from codesandbox.features.sandbox.runtime import policy as policy_module

    source = inspect.getsource(policy_module)
    assert "reverse-decompile" not in source
    assert "reverse_decompile" not in source


def test_worker_filesystem_paths_stay_in_workspace(ctx: TestContext) -> None:
    docker_module = types.ModuleType("docker")
    errors_module = types.ModuleType("docker.errors")
    types_module = types.ModuleType("docker.types")

    class ImageNotFound(Exception):
        pass

    class NotFound(Exception):
        pass

    class Mount:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    errors_module.ImageNotFound = ImageNotFound
    errors_module.NotFound = NotFound
    types_module.Mount = Mount
    previous = {
        "docker": sys.modules.get("docker"),
        "docker.errors": sys.modules.get("docker.errors"),
        "docker.types": sys.modules.get("docker.types"),
    }
    sys.modules["docker"] = docker_module
    sys.modules["docker.errors"] = errors_module
    sys.modules["docker.types"] = types_module

    def restore_modules() -> None:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    ctx.defer(restore_modules)

    from worker.runtime.filesystem import DockerFilesystem, FilesystemError

    runner = SimpleNamespace(
        container=None,
        policy={"working_dir": "/workspace"},
    )
    filesystem = DockerFilesystem(runner)

    assert filesystem.resolve("../etc/passwd") == "/workspace/etc/passwd"
    assert filesystem.resolve("/absolute/path.txt") == "/workspace/absolute/path.txt"
    try:
        filesystem.resolve("bad\x00path")
    except FilesystemError:
        pass
    else:
        raise AssertionError("NUL bytes must be rejected in filesystem paths.")


TESTS: list[TestCase] = [
    TestCase("effective plan merges overrides", "sandbox", test_effective_plan_merges_template_overrides),
    TestCase("disabled template plan rejected", "sandbox", test_disabled_template_plan_rejected_by_service),
    TestCase("reverse engineering no internet", "sandbox", test_full_internet_blocked_for_reverse_engineering),
    TestCase("required_args enforced generically", "sandbox", test_required_args_enforced_generically),
    TestCase("forbidden_args enforced generically", "sandbox", test_forbidden_args_enforced_generically),
    TestCase("no hardcoded slug in policy builder", "sandbox", test_no_hardcoded_template_slug_in_policy_builder),
    TestCase("worker filesystem path confinement", "sandbox", test_worker_filesystem_paths_stay_in_workspace),
]
