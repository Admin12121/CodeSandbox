from __future__ import annotations

import json

from tests._context import TestCase, TestContext, unique


def _fixture_template(ctx: TestContext, **overrides):
    from codesandbox.features.identity.models import User
    from codesandbox.features.sandbox import repository as sandbox_repository

    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    assert admin is not None, "expected seed.py fixture to exist"

    kwargs = dict(
        name=unique("test-launch-gating"),
        slug=unique("test-launch-gating"),
        description=None,
        icon_path=None,
        docker_image="busybox:1.36",
        sandbox_type="interactive",
        runtime_class="tool_job",
        interface_mode="background_run",
        allowed_ui_modes=json.dumps(["background_run"]),
        default_ui_mode="background_run",
        network_mode="disabled",
        allow_root=False,
        max_timeout_hr=1,
        runtime_config=None,
        created_by_id=str(admin.id),
    )
    kwargs.update(overrides)
    template = sandbox_repository.create_template(**kwargs)
    ctx.defer(template.delete)
    return template, admin


def test_start_test_instance_blocked_when_requires_input_and_no_file(ctx: TestContext) -> None:
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import start_test_instance

    runtime_config = json.dumps({
        "runtime.json": json.dumps({"test_config": {"requires_input": True, "success_condition": "exit_zero"}})
    })
    template, admin = _fixture_template(ctx, runtime_config=runtime_config)

    result, error = start_test_instance(str(template.id), actor_user_id=str(admin.id))
    assert result is None
    assert error is not None and "input file" in error.lower()

    # No instance should have been created for a request the gate rejected.
    live = [
        i for i in sandbox_repository.list_runtime_backed_instances_for_worker("worker-1")
        if str(i.template_id) == str(template.id)
    ]
    assert not live


def test_desktop_gui_publish_requires_gui_config(ctx: TestContext) -> None:
    """validate_ui_mode_config's require_publish_ready branch — a
    desktop_gui template can't be published without gui_url/novnc_url/
    gui_port configured; this is the check set_template_status relies on."""
    from codesandbox.features.sandbox.service import validate_ui_mode_config

    error = validate_ui_mode_config(
        runtime_class="container",
        allowed_ui_modes=["desktop_gui"],
        default_ui_mode="desktop_gui",
        default_command="",
        runtime_config={},
        require_publish_ready=True,
    )
    assert error is not None and "gui" in error.lower()

    ok = validate_ui_mode_config(
        runtime_class="container",
        allowed_ui_modes=["desktop_gui"],
        default_ui_mode="desktop_gui",
        default_command="",
        runtime_config={"ui": {"desktop_gui": {"internal_port": 5900}}},
        require_publish_ready=True,
    )
    assert ok is None


def test_android_ui_publish_requires_android_emulator_runtime_class(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import validate_ui_mode_config

    error = validate_ui_mode_config(
        runtime_class="container",
        allowed_ui_modes=["android_ui"],
        default_ui_mode="android_ui",
        default_command="",
        runtime_config={},
        require_publish_ready=False,
    )
    assert error is not None and "android_emulator" in error


def test_background_run_publish_requires_success_condition(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import validate_ui_mode_config

    error = validate_ui_mode_config(
        runtime_class="tool_job",
        allowed_ui_modes=["background_run"],
        default_ui_mode="background_run",
        default_command="decompile /input/sample",
        runtime_config={},
        require_publish_ready=True,
    )
    assert error is not None and "success_condition" in error


TESTS: list[TestCase] = [
    TestCase("start_test_instance blocked when requires_input and no file", "sandbox", test_start_test_instance_blocked_when_requires_input_and_no_file),
    TestCase("desktop_gui publish requires gui config", "sandbox", test_desktop_gui_publish_requires_gui_config),
    TestCase("android_ui publish requires android_emulator runtime class", "sandbox", test_android_ui_publish_requires_android_emulator_runtime_class),
    TestCase("background_run publish requires success_condition", "sandbox", test_background_run_publish_requires_success_condition),
]
