from __future__ import annotations

import os

from tests._context import TestCase, TestContext


def _templates_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "codesandbox", "templates"))


def test_lab_ui_does_not_include_old_hub_template(ctx: TestContext) -> None:
    """Phase 10.2's core guarantee: /instances/<id> must be fully
    self-contained and never fall back to the old hub/[instance]/[slug]
    IDE template it used to be a 2-line {% include %} of."""
    path = os.path.join(
        _templates_root(), "(admin)", "instances", "[instance_id]", "_components", "lab_ui.html"
    )
    text = open(path, encoding="utf-8").read()
    # Check the actual Jinja directives, not just any mention of the path —
    # an explanatory comment ("does not depend on hub/[instance]") would
    # otherwise trip a naive substring check.
    for directive in ('{% include "(admin)/hub/[instance]', '{% import "(admin)/hub/[instance]'):
        assert directive not in text, (
            f"lab_ui.html must not include/import anything from the old hub/[instance] "
            f"template tree — found {directive!r} in {path}"
        )


def test_desktop_gui_and_android_ui_never_expose_raw_internal_url(ctx: TestContext) -> None:
    """These pages used to be `<iframe src="{{ gui_url }}">` pointed at a
    raw admin-supplied internal URL — the real implementation must route
    through the tokenized WebSocket proxy instead, never render a
    container-internal URL/port directly into the page."""
    root = _templates_root()
    for filename in ("desktop_gui.html", "android_ui.html"):
        path = os.path.join(root, "(admin)", "instances", "[instance_id]", "_components", filename)
        text = open(path, encoding="utf-8").read()
        assert "<iframe" not in text, f"{filename} must not render a raw iframe to an internal GUI URL"
        assert "gui_url" not in text and "screen_url" not in text, (
            f"{filename} must not reference a raw internal gui_url/screen_url directly"
        )


def test_desktop_gui_uses_tokenized_ws_route(ctx: TestContext) -> None:
    path = os.path.join(
        _templates_root(), "(admin)", "instances", "[instance_id]", "_components", "desktop_gui.html"
    )
    text = open(path, encoding="utf-8").read()
    assert "monitor-token?purpose=gui" in text
    assert "/ws/sandbox/" in text and "/gui?token=" in text


def test_android_ui_uses_tokenized_ws_routes(ctx: TestContext) -> None:
    path = os.path.join(
        _templates_root(), "(admin)", "instances", "[instance_id]", "_components", "android_ui.html"
    )
    text = open(path, encoding="utf-8").read()
    assert "monitor-token?purpose=android" in text
    assert "android-screen" in text and "android-logcat" in text


def test_platform_sandboxes_admin_ui_says_publish_not_activate(ctx: TestContext) -> None:
    path = os.path.join(
        _templates_root(), "(admin)", "platform", "sandboxes", "_components", "table.html"
    )
    text = open(path, encoding="utf-8").read()
    assert ">Publish<" in text or "Publish" in text
    assert ">Unpublish<" in text or "Unpublish" in text
    assert "Cannot publish" in text
    # The old wording must not still be the button label anywhere.
    assert '"Deactivate" if selected_template.status == "active" else "Activate"' not in text


def test_background_run_has_no_card_wrapper_around_log_stream(ctx: TestContext) -> None:
    """Phase 10.1: the log panel itself must not be wrapped in the
    rounded-xl border bg-black/20 'card' box it used to have — the mask-fade
    log stream should sit directly on the page background."""
    path = os.path.join(
        _templates_root(), "(admin)", "instances", "[instance_id]", "_components", "background_run.html"
    )
    text = open(path, encoding="utf-8").read()
    assert 'class="logs-explorer"' in text  # the mask-fade container itself still exists
    assert 'bg-black/20' not in text, "the old card wrapper around the log stream must be gone"


TESTS: list[TestCase] = [
    TestCase("lab_ui does not include old hub template", "sandbox", test_lab_ui_does_not_include_old_hub_template),
    TestCase("desktop_gui/android_ui never expose raw internal URL", "sandbox", test_desktop_gui_and_android_ui_never_expose_raw_internal_url),
    TestCase("desktop_gui uses tokenized WS route", "sandbox", test_desktop_gui_uses_tokenized_ws_route),
    TestCase("android_ui uses tokenized WS routes", "sandbox", test_android_ui_uses_tokenized_ws_routes),
    TestCase("platform sandboxes admin UI says Publish not Activate", "sandbox", test_platform_sandboxes_admin_ui_says_publish_not_activate),
    TestCase("background_run has no card wrapper around log stream", "sandbox", test_background_run_has_no_card_wrapper_around_log_stream),
]
