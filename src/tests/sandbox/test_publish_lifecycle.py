from __future__ import annotations

import json

from tests._context import TestCase, TestContext, unique


def _fixture_template(ctx: TestContext, **overrides):
    from codesandbox.features.identity.models import User
    from codesandbox.features.sandbox import repository as sandbox_repository

    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    assert admin is not None, "expected seed.py fixture to exist"

    kwargs = dict(
        name=unique("publish-lifecycle"),
        slug=unique("publish-lifecycle"),
        description=None,
        icon_path=None,
        docker_image="busybox:1.36",
        sandbox_type="interactive",
        runtime_class="container",
        interface_mode="terminal_only",
        allowed_ui_modes=json.dumps(["terminal_only"]),
        default_ui_mode="terminal_only",
        network_mode="disabled",
        allow_root=False,
        max_timeout_hr=1,
        runtime_config=None,
        created_by_id=str(admin.id),
    )
    kwargs.update(overrides)
    template = sandbox_repository.create_template(**kwargs)
    ctx.defer(template.delete)
    return template


def test_publish_blocked_without_a_passed_test(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import set_template_status

    template = _fixture_template(ctx)
    assert template.last_test_status == "untested"

    error = set_template_status(str(template.id), "active")
    assert error is not None
    assert "test" in error.lower() or "activate" in error.lower()


def test_publish_succeeds_after_test_passes(ctx: TestContext) -> None:
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import set_template_status

    template = _fixture_template(ctx)
    sandbox_repository.update_template(str(template.id), last_test_status="passed")

    error = set_template_status(str(template.id), "active")
    assert error is None

    refreshed = sandbox_repository.get_template(str(template.id))
    assert refreshed.status == "active"


def test_identity_field_change_forces_retest_and_demotes_from_active(ctx: TestContext) -> None:
    """Task #24's guarantee ('Force deactivation + retest on Runtime field
    changes'), re-verified: changing docker_image on a published template
    must reset last_test_status and pull it back to maintenance — a
    published template's dangerous fields can't drift silently."""
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import save_template

    template = _fixture_template(ctx)
    sandbox_repository.update_template(str(template.id), last_test_status="passed", status="active")

    result, error = save_template(
        template_id=str(template.id),
        name=template.name,
        description="",
        icon_path="",
        docker_image="alpine:latest",  # dangerous field: different image
        sandbox_type=template.sandbox_type,
        runtime_config="",
        created_by_id=None,
        runtime_class=template.runtime_class,
        interface_mode=template.interface_mode,
        allowed_ui_modes=["terminal_only"],
        default_ui_mode="terminal_only",
        network_mode=template.network_mode,
        allow_root=False,
        max_timeout_hr=1,
        default_command="",
        working_dir="/workspace",
        input_mount_path="/input",
        output_mount_path="/output",
        artifact_paths="",
        input_required=False,
        max_upload_mb=50,
        read_only_root=True,
        run_as_user="",
        pids_limit=256,
        allow_full_internet=False,
    )
    assert error is None, error
    assert result["status"] == "maintenance"
    assert result["last_test_status"] == "untested"


def test_config_tab_change_to_desktop_gui_forces_retest(ctx: TestContext) -> None:
    """The Config tab (free-form runtime.json editor) is a *separate* save
    path from the Identity tab — this is the gap found and fixed during
    Phase 10.5 verification: editing desktop_gui/android_ui connection
    config there used to bypass the retest-forcing entirely."""
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import save_template_config

    template = _fixture_template(ctx, allowed_ui_modes=json.dumps(["desktop_gui"]), default_ui_mode="desktop_gui")
    sandbox_repository.update_template(str(template.id), last_test_status="passed", status="active")

    new_config = json.dumps({
        "runtime.json": json.dumps({"ui": {"desktop_gui": {"internal_port": 5900}}})
    })
    save_template_config(str(template.id), new_config)

    refreshed = sandbox_repository.get_template(str(template.id))
    assert refreshed.status == "maintenance"
    assert refreshed.last_test_status == "untested"


def test_config_tab_change_to_unrelated_field_does_not_force_retest(ctx: TestContext) -> None:
    """Only the specific dangerous keys (desktop_gui/android_ui) force a
    retest on the Config tab's autosave — everything else (workflow labels,
    notes) must autosave without silently unpublishing a live template."""
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import save_template_config

    template = _fixture_template(ctx)
    sandbox_repository.update_template(str(template.id), last_test_status="passed", status="active")

    new_config = json.dumps({"runtime.json": json.dumps({"required_args": ["--safe"]})})
    save_template_config(str(template.id), new_config)

    refreshed = sandbox_repository.get_template(str(template.id))
    assert refreshed.status == "active"
    assert refreshed.last_test_status == "passed"


TESTS: list[TestCase] = [
    TestCase("publish blocked without a passed test", "sandbox", test_publish_blocked_without_a_passed_test),
    TestCase("publish succeeds after test passes", "sandbox", test_publish_succeeds_after_test_passes),
    TestCase("identity field change forces retest + demotes from active", "sandbox", test_identity_field_change_forces_retest_and_demotes_from_active),
    TestCase("config tab desktop_gui change forces retest", "sandbox", test_config_tab_change_to_desktop_gui_forces_retest),
    TestCase("config tab unrelated change does not force retest", "sandbox", test_config_tab_change_to_unrelated_field_does_not_force_retest),
]
