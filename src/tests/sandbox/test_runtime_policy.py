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


def test_effective_plan_uses_global_plan_resources_only(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import resolve_effective_plan

    effective = resolve_effective_plan(
        _template(network_mode="restricted"),
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

    # SandboxTemplatePlan is availability-only. Resource limits and prices are
    # sourced from SandboxPlan so the client summary, scheduler, Docker limits,
    # and billing snapshot all use one authoritative set of values.
    assert effective.ind_vcpu == 1
    assert effective.ind_ram_gb == 1
    assert effective.ind_disk_gb == 10
    assert effective.ind_cost_hr == Decimal("0.1000")
    assert effective.org_vcpu == 2
    assert effective.org_ram_gb == 2
    assert effective.org_disk_gb == 20
    assert effective.org_cost_hr == Decimal("0.1500")
    assert effective.max_timeout_hr == 2
    assert effective.network_mode == "restricted"
    assert effective.min_billable_minutes == 1


def test_container_disk_limit_is_sparse_for_scheduler(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import _scheduler_disk_gb

    assert _scheduler_disk_gb("container", 30) == 1
    assert _scheduler_disk_gb("tool_job", 50) == 1
    assert _scheduler_disk_gb("qemu_vm", 30) == 30


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


def test_template_is_only_network_policy_source(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import resolve_effective_plan

    # A plan's legacy allowed_network_modes value cannot veto or grant network
    # access. Runtime network behavior comes only from template Config.
    effective = resolve_effective_plan(
        _template(
            sandbox_type="reverse_engineering",
            network_mode="full_internet",
            allow_full_internet=False,  # legacy duplicate column is ignored
        ),
        _plan(allowed_network_modes='["disabled"]'),
        _template_plan(full_internet_enabled=False),
    )
    assert effective.network_mode == "full_internet"
    assert effective.full_internet_enabled is True


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


def test_platform_environment_names_cannot_be_overridden(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import (
        PolicyBuilder,
        RuntimePolicyError,
        resolve_effective_plan,
    )

    effective = resolve_effective_plan(_template(), _plan())
    try:
        PolicyBuilder().build(
            _template(
                runtime_config=_runtime_config(
                    environment={"CODESANDBOX_USERNAME": "fake-user"}
                )
            ),
            effective,
        )
    except RuntimePolicyError as exc:
        assert "managed by the platform" in str(exc)
    else:
        raise AssertionError("Templates must not override platform-owned identity variables.")


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


def test_root_study_profile_uses_fixed_capabilities(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import (
        PolicyBuilder,
        ROOT_STUDY_CAPABILITIES,
        RuntimePolicyError,
        resolve_effective_plan,
    )

    template = _template(
        allow_root=True,
        read_only_root=False,
        run_as_user=None,
        runtime_config=_runtime_config(security_profile="root_study"),
    )
    policy = PolicyBuilder().build(template, resolve_effective_plan(template, _plan()))
    assert policy["security"]["cap_drop"] == ["ALL"]
    assert policy["security"]["cap_add"] == ROOT_STUDY_CAPABILITIES
    assert "SYS_ADMIN" not in policy["security"]["cap_add"]
    assert policy["security"]["privileged"] is False

    invalid = _template(
        allow_root=False,
        runtime_config=_runtime_config(security_profile="root_study"),
    )
    try:
        PolicyBuilder().build(invalid, resolve_effective_plan(invalid, _plan()))
    except RuntimePolicyError as exc:
        assert "requires explicit root access" in str(exc)
    else:
        raise AssertionError("root_study must require allow_root on the template.")


def test_sudo_ide_profile_is_non_root_but_bootstraps_as_root(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import (
        PolicyBuilder,
        SUDO_USER_CAPABILITIES,
        resolve_effective_plan,
    )

    template = _template(
        allow_root=False,
        read_only_root=False,
        run_as_user="1000:1000",
        runtime_config=_runtime_config(
            allow_sudo=True,
            container_start_user="0:0",
            terminal_user="1000:1000",
        ),
    )
    policy = PolicyBuilder().build(template, resolve_effective_plan(template, _plan()))
    assert policy["allow_root"] is False
    assert policy["container_start_user"] == "0:0"
    assert policy["terminal_user"] == "1000:1000"
    assert policy["security"]["cap_add"] == SUDO_USER_CAPABILITIES
    assert policy["security"]["no_new_privileges"] is False


def test_workspace_terminal_scope_is_carried_to_worker_policy(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.runtime.policy import PolicyBuilder, resolve_effective_plan

    effective = resolve_effective_plan(_template(network_mode="restricted"), _plan())
    policy = PolicyBuilder().build(
        _template(runtime_config=_runtime_config(terminal_scope="workspace")),
        effective,
    )
    assert policy["terminal_scope"] == "workspace"


TESTS: list[TestCase] = [
    TestCase("effective plan uses global plan resources only", "sandbox", test_effective_plan_uses_global_plan_resources_only),
    TestCase("container disk is sparse for scheduler", "sandbox", test_container_disk_limit_is_sparse_for_scheduler),
    TestCase("disabled template plan rejected", "sandbox", test_disabled_template_plan_rejected_by_service),
    TestCase("template controls network policy", "sandbox", test_template_is_only_network_policy_source),
    TestCase("required_args enforced generically", "sandbox", test_required_args_enforced_generically),
    TestCase("forbidden_args enforced generically", "sandbox", test_forbidden_args_enforced_generically),
    TestCase("platform environment names are reserved", "sandbox", test_platform_environment_names_cannot_be_overridden),
    TestCase("no hardcoded slug in policy builder", "sandbox", test_no_hardcoded_template_slug_in_policy_builder),
    TestCase("worker filesystem path confinement", "sandbox", test_worker_filesystem_paths_stay_in_workspace),
    TestCase("root study capability profile", "sandbox", test_root_study_profile_uses_fixed_capabilities),
    TestCase("sudo IDE capability profile", "sandbox", test_sudo_ide_profile_is_non_root_but_bootstraps_as_root),
    TestCase("workspace terminal scope", "sandbox", test_workspace_terminal_scope_is_carried_to_worker_policy),
]
