from __future__ import annotations

import json

from tests._context import TestCase, TestContext, unique


def _bg_lab_terminal_graph(**overrides) -> dict:
    graph = {
        "mode": "workflow",
        "start_node_id": "node-background",
        "nodes": [
            {"id": "node-background", "label": "Initial Analysis", "ui_mode": "background_run",
             "position": {"x": 120, "y": 180}, "carry_artifacts": True, "auto_start": False,
             "continue_label": "Choose next step"},
            {"id": "node-lab", "label": "Manual Lab", "ui_mode": "lab_ui",
             "position": {"x": 520, "y": 100}, "carry_artifacts": True, "auto_start": False, "continue_label": ""},
            {"id": "node-terminal", "label": "Terminal", "ui_mode": "terminal_only",
             "position": {"x": 520, "y": 420}, "carry_artifacts": True, "auto_start": False, "continue_label": ""},
        ],
        "edges": [
            {"id": "edge-bg-lab", "source": "node-background", "target": "node-lab", "condition": "manual", "label": "Open Lab UI"},
            {"id": "edge-bg-terminal", "source": "node-background", "target": "node-terminal", "condition": "manual", "label": "Open Terminal"},
        ],
    }
    graph.update(overrides)
    return graph


def _fixture_template(ctx: TestContext, **overrides):
    from codesandbox.features.identity.models import User
    from codesandbox.features.sandbox import repository as sandbox_repository

    admin = User.objects.filter(email="admin@codesandbox.dev").first()
    assert admin is not None, "expected seed.py fixture to exist"

    kwargs = dict(
        name=unique("ui-workflow"),
        slug=unique("ui-workflow"),
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
    return template, admin


def _fixture_instance(ctx: TestContext, template, admin, **overrides):
    from codesandbox.features.sandbox import repository as sandbox_repository

    kwargs = dict(
        template_id=str(template.id), plan_id="basic", workspace_type="user",
        workspace_user_id=str(admin.id), workspace_org_id=None, created_by_user_id=str(admin.id),
        billing_entity="user", billed_user_id=str(admin.id), billed_org_id=None,
    )
    kwargs.update(overrides)
    inst = sandbox_repository.create_instance(**kwargs)
    inst.status = "running"
    inst.runtime_id = "fake-runtime"
    inst.save()
    ctx.defer(inst.delete)
    return inst


# ── Graph validation ─────────────────────────────────────────────────────────

def test_valid_branching_graph_passes(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    assert validate_ui_workflow_graph(_bg_lab_terminal_graph()) is None


def test_empty_nodes_rejected(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    error = validate_ui_workflow_graph({"nodes": [], "edges": []})
    assert error is not None and "at least one node" in error.lower()


def test_missing_start_node_rejected(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    graph = _bg_lab_terminal_graph(start_node_id="does-not-exist")
    error = validate_ui_workflow_graph(graph)
    assert error is not None and "start node" in error.lower()


def test_invalid_ui_mode_rejected(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    graph = _bg_lab_terminal_graph()
    graph["nodes"][1]["ui_mode"] = "not_a_real_mode"
    error = validate_ui_workflow_graph(graph)
    assert error is not None and "ui_mode" in error.lower()


def test_edge_referencing_missing_node_rejected(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    graph = _bg_lab_terminal_graph()
    graph["edges"].append({"id": "e2", "source": "node-background", "target": "ghost", "condition": "manual"})
    error = validate_ui_workflow_graph(graph)
    assert error is not None and "doesn't exist" in error


def test_cycle_rejected_by_default_allowed_with_opt_in(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    graph = _bg_lab_terminal_graph()
    graph["edges"].append({"id": "e-back", "source": "node-lab", "target": "node-background", "condition": "manual"})
    error = validate_ui_workflow_graph(graph)
    assert error is not None and "cycle" in error.lower()

    graph["allow_cycles"] = True
    assert validate_ui_workflow_graph(graph) is None


def test_node_template_id_stripped_on_parse(ctx: TestContext) -> None:
    """Nodes must never carry a template reference — every node is a UI
    stage of the *same* template. A stray template_id (e.g. from a stale
    client) is defensively stripped on parse, not trusted."""
    from codesandbox.features.sandbox.ui_workflow import parse_ui_workflow_graph
    graph = _bg_lab_terminal_graph()
    graph["nodes"][0]["template_id"] = "some-other-template"
    parsed = parse_ui_workflow_graph(json.dumps(graph))
    assert all("template_id" not in n for n in parsed["nodes"])


def test_edge_between_same_ui_mode_nodes_rejected(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    graph = _bg_lab_terminal_graph()
    # Both node-lab and node-terminal are distinct ui_modes already (fixture
    # is valid); add a same-mode duplicate of node-lab and connect them.
    graph["nodes"].append({
        "id": "node-lab-2", "label": "Second Lab", "ui_mode": "lab_ui",
        "position": {"x": 800, "y": 100}, "carry_artifacts": True, "auto_start": False, "continue_label": "",
    })
    graph["edges"].append({"id": "e-same-mode", "source": "node-lab", "target": "node-lab-2", "condition": "manual"})
    error = validate_ui_workflow_graph(graph)
    assert error is not None and "same ui mode" in error.lower()


def test_source_branching_to_two_same_mode_siblings_rejected(ctx: TestContext) -> None:
    """Even when no single edge directly connects two same-mode nodes, one
    source branching to two DIFFERENT targets that happen to share a
    ui_mode is the same underlying problem — the branch choices become
    indistinguishable. Regression: this used to only be caught by editing
    an already-connected node's ui_mode client-side; the graph itself
    (e.g. built via two separate valid connects, each fine in isolation)
    was never re-checked as a whole."""
    from codesandbox.features.sandbox.ui_workflow import validate_ui_workflow_graph
    graph = _bg_lab_terminal_graph()
    # Add a second terminal_only node and connect node-background to it too —
    # node-background now branches to two "terminal_only" targets.
    graph["nodes"].append({
        "id": "node-terminal-2", "label": "Second Terminal", "ui_mode": "terminal_only",
        "position": {"x": 800, "y": 300}, "carry_artifacts": True, "auto_start": False, "continue_label": "",
    })
    graph["edges"].append({"id": "e-sibling", "source": "node-background", "target": "node-terminal-2", "condition": "manual"})
    error = validate_ui_workflow_graph(graph)
    assert error is not None and "already branches to" in error.lower()


# ── interface_behavior derivation ───────────────────────────────────────────

def test_single_mode_allowed_ui_modes_is_just_default(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import save_template
    template, admin = _fixture_template(ctx)
    result, error = save_template(
        template_id=str(template.id), name=template.name, description="", icon_path="",
        docker_image="busybox:1.36", sandbox_type="interactive", runtime_config="",
        created_by_id=str(admin.id), runtime_class="container", interface_mode="terminal_only",
        allowed_ui_modes=["terminal_only", "lab_ui"],  # ignored in single mode — only default_ui_mode counts
        default_ui_mode="lab_ui", interface_behavior="single", network_mode="disabled",
        allow_root=False, max_timeout_hr=1, default_command="", working_dir="/workspace",
        input_mount_path="/input", output_mount_path="/output", artifact_paths="", input_required=False,
        max_upload_mb=50, read_only_root=True, run_as_user="", pids_limit=256, allow_full_internet=False,
    )
    assert error is None, error
    assert result["allowed_ui_mode_values"] == ["lab_ui"]
    assert result["default_ui_mode"] == "lab_ui"


def test_workflow_mode_allowed_ui_modes_derived_from_graph(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import (
        save_template_ui_workflow,
        template_allowed_ui_modes,
        template_default_ui_mode,
    )
    template, admin = _fixture_template(ctx, interface_behavior="workflow")
    result, error = save_template_ui_workflow(str(template.id), _bg_lab_terminal_graph(), actor_user_id=str(admin.id))
    assert error is None, error

    from codesandbox.features.sandbox import repository as sandbox_repository
    refreshed = sandbox_repository.get_template(str(template.id))
    allowed = set(template_allowed_ui_modes(refreshed))
    assert allowed == {"background_run", "lab_ui", "terminal_only"}
    assert template_default_ui_mode(refreshed) == "background_run"  # start node


def test_workflow_graph_save_forces_retest(ctx: TestContext) -> None:
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import save_template_ui_workflow

    template, admin = _fixture_template(ctx, interface_behavior="workflow")
    sandbox_repository.update_template(str(template.id), last_test_status="passed", status="active")

    result, error = save_template_ui_workflow(str(template.id), _bg_lab_terminal_graph(), actor_user_id=str(admin.id))
    assert error is None, error
    assert result["last_test_status"] == "untested"
    assert result["status"] == "maintenance"


def test_invalid_graph_rejected_by_save(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import save_template_ui_workflow
    template, admin = _fixture_template(ctx, interface_behavior="workflow")
    result, error = save_template_ui_workflow(str(template.id), {"nodes": [], "edges": []}, actor_user_id=str(admin.id))
    assert result is None
    assert error is not None and "at least one node" in error.lower()


def test_publish_blocked_when_workflow_has_no_nodes(ctx: TestContext) -> None:
    from codesandbox.features.sandbox import repository as sandbox_repository
    from codesandbox.features.sandbox.service import set_template_status

    template, admin = _fixture_template(ctx, interface_behavior="workflow")
    sandbox_repository.update_template(str(template.id), last_test_status="passed")
    error = set_template_status(str(template.id), "active")
    assert error is not None and "node" in error.lower()


def test_workflow_mode_rejected_for_single_ui_mode_runtime_class(ctx: TestContext) -> None:
    """tool_job (and android_emulator) only ever have exactly one possible
    ui_mode — there's nothing to branch between, so Workflow Mode is
    rejected outright rather than silently falling back to a 1-node-only
    'workflow'. This also regression-guards the original bug: before this
    check existed, switching a tool_job template to Workflow Mode with an
    empty graph hardcoded the fallback ui_mode to terminal_only, which
    validate_ui_mode_config rejects for tool_job — save_template returned a
    confusing "Terminal Only and Lab UI require..." error instead of an
    honest "Workflow Mode isn't available for this runtime" one."""
    from codesandbox.features.sandbox.service import save_template

    template, admin = _fixture_template(
        ctx, runtime_class="tool_job", interface_mode="background_run",
        allowed_ui_modes=json.dumps(["background_run"]), default_ui_mode="background_run",
    )
    result, error = save_template(
        template_id=str(template.id), name=template.name, description="", icon_path="",
        docker_image="busybox:1.36", sandbox_type="interactive", runtime_config="",
        created_by_id=str(admin.id), runtime_class="tool_job", interface_mode="background_run",
        default_ui_mode="background_run", interface_behavior="workflow", network_mode="disabled",
        allow_root=False, max_timeout_hr=1, default_command="", working_dir="/workspace",
        input_mount_path="/input", output_mount_path="/output", artifact_paths="", input_required=False,
        max_upload_mb=50, read_only_root=True, run_as_user="", pids_limit=256, allow_full_internet=False,
    )
    assert result is None
    assert error is not None and "workflow mode" in error.lower() and "tool_job" in error.lower()


def test_workflow_mode_allowed_for_multi_ui_mode_runtime_with_empty_graph(ctx: TestContext) -> None:
    """A runtime class with more than one available ui_mode (container) can
    switch to Workflow Mode before any graph exists — falls back to that
    runtime's own default mode, not a hardcoded one."""
    from codesandbox.features.sandbox.service import save_template

    template, admin = _fixture_template(ctx, runtime_class="container")
    result, error = save_template(
        template_id=str(template.id), name=template.name, description="", icon_path="",
        docker_image="busybox:1.36", sandbox_type="interactive", runtime_config="",
        created_by_id=str(admin.id), runtime_class="container", interface_mode="terminal_only",
        default_ui_mode="terminal_only", interface_behavior="workflow", network_mode="disabled",
        allow_root=False, max_timeout_hr=1, default_command="", working_dir="/workspace",
        input_mount_path="/input", output_mount_path="/output", artifact_paths="", input_required=False,
        max_upload_mb=50, read_only_root=True, run_as_user="", pids_limit=256, allow_full_internet=False,
    )
    assert error is None, error
    assert result["interface_behavior"] == "workflow"
    assert result["default_ui_mode"] in ("terminal_only", "lab_ui", "background_run")


# ── Execution: branching + condition filtering ──────────────────────────────

def test_start_node_offers_branching_choices(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import get_instance_ui_context

    template, admin = _fixture_template(
        ctx, interface_behavior="workflow",
        ui_workflow_json=json.dumps(_bg_lab_terminal_graph()),
    )
    inst = _fixture_instance(ctx, template, admin)

    ctx_dict, error = get_instance_ui_context(str(inst.id), str(admin.id))
    assert error is None, error
    assert ctx_dict["ui_mode"] == "background_run"
    target_modes = {c["target_ui_mode"] for c in ctx_dict["ui_workflow_choices"]}
    assert target_modes == {"lab_ui", "terminal_only"}


def test_following_edge_switches_same_instance(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import get_instance_ui_context

    template, admin = _fixture_template(
        ctx, interface_behavior="workflow",
        ui_workflow_json=json.dumps(_bg_lab_terminal_graph()),
    )
    inst = _fixture_instance(ctx, template, admin)

    ctx_dict, error = get_instance_ui_context(str(inst.id), str(admin.id), requested_node_id="node-lab")
    assert error is None, error
    assert ctx_dict["ui_mode"] == "lab_ui"
    assert ctx_dict["instance"]["id"] == str(inst.id)
    # node-lab is a dead end in this fixture graph — no further choices.
    assert ctx_dict["ui_workflow_choices"] == []


def test_success_failure_conditions_gated_by_exit_code(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import get_instance_ui_context

    graph = _bg_lab_terminal_graph(edges=[
        {"id": "e-ok", "source": "node-background", "target": "node-lab", "condition": "success", "label": "On success"},
        {"id": "e-fail", "source": "node-background", "target": "node-terminal", "condition": "failure", "label": "On failure"},
    ])
    template, admin = _fixture_template(ctx, interface_behavior="workflow", ui_workflow_json=json.dumps(graph))
    inst = _fixture_instance(ctx, template, admin)

    # Outcome not yet known (exit_code is None) — neither conditional edge shown.
    ctx_dict, _ = get_instance_ui_context(str(inst.id), str(admin.id))
    assert ctx_dict["ui_workflow_choices"] == []

    inst.exit_code = 0
    inst.save()
    ctx_dict, _ = get_instance_ui_context(str(inst.id), str(admin.id))
    assert [c["target_ui_mode"] for c in ctx_dict["ui_workflow_choices"]] == ["lab_ui"]

    inst.exit_code = 1
    inst.save()
    ctx_dict, _ = get_instance_ui_context(str(inst.id), str(admin.id))
    assert [c["target_ui_mode"] for c in ctx_dict["ui_workflow_choices"]] == ["terminal_only"]


def test_single_mode_instance_has_no_workflow_choices(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import get_instance_ui_context
    template, admin = _fixture_template(ctx)  # interface_behavior defaults to "single"
    inst = _fixture_instance(ctx, template, admin)
    ctx_dict, error = get_instance_ui_context(str(inst.id), str(admin.id))
    assert error is None, error
    assert ctx_dict["ui_mode"] == "terminal_only"
    assert ctx_dict["ui_workflow_choices"] == []


# ── Custom Page node type ────────────────────────────────────────────────────

def _bg_to_custom_page_graph() -> dict:
    return {
        "mode": "workflow",
        "start_node_id": "node-background",
        "nodes": [
            {"id": "node-background", "label": "Analysis", "ui_mode": "background_run",
             "position": {"x": 40, "y": 40}, "carry_artifacts": True, "auto_start": False, "continue_label": ""},
            {"id": "node-result", "label": "Result", "ui_mode": "custom_page",
             "position": {"x": 340, "y": 40}, "carry_artifacts": True, "auto_start": False, "continue_label": "",
             "custom_html": "<h1 id='marker'>Done</h1>"},
        ],
        "edges": [
            {"id": "e1", "source": "node-background", "target": "node-result", "condition": "manual", "label": ""},
        ],
    }


def test_custom_page_graph_passes_validation_without_runtime_capability_checks(ctx: TestContext) -> None:
    """custom_page doesn't run in any sandbox runtime, so it must be usable
    regardless of the template's runtime_class — unlike the 5 real modes."""
    from codesandbox.features.sandbox.service import save_template_ui_workflow
    template, admin = _fixture_template(ctx, runtime_class="tool_job", interface_behavior="workflow")
    result, error = save_template_ui_workflow(str(template.id), _bg_to_custom_page_graph(), actor_user_id=str(admin.id))
    assert error is None, error


def test_custom_page_node_resolves_as_ui_mode_bypassing_single_mode_normalize(ctx: TestContext) -> None:
    """Regression: template_default_ui_mode/get_instance_ui_context used to
    run workflow-derived ui_mode values through normalize_ui_mode, which is
    scoped to Single Mode's 5 runtime-backed UI_MODES — silently downgrading
    any custom_page node back to terminal_only."""
    from codesandbox.features.sandbox.service import get_instance_ui_context, template_default_ui_mode
    template, admin = _fixture_template(
        ctx, interface_behavior="workflow", ui_workflow_json=json.dumps(_bg_to_custom_page_graph()),
    )
    inst = _fixture_instance(ctx, template, admin)

    assert template_default_ui_mode(template) == "background_run"

    ctx_dict, error = get_instance_ui_context(str(inst.id), str(admin.id), requested_node_id="node-result")
    assert error is None, error
    assert ctx_dict["ui_mode"] == "custom_page"
    assert ctx_dict["ui_workflow_node"]["custom_html"] == "<h1 id='marker'>Done</h1>"


def test_custom_page_restart_url_points_to_start_node(ctx: TestContext) -> None:
    from codesandbox.features.sandbox.service import get_instance_ui_context
    template, admin = _fixture_template(
        ctx, interface_behavior="workflow", ui_workflow_json=json.dumps(_bg_to_custom_page_graph()),
    )
    inst = _fixture_instance(ctx, template, admin)

    ctx_dict, error = get_instance_ui_context(str(inst.id), str(admin.id), requested_node_id="node-result")
    assert error is None, error
    assert ctx_dict["ui_workflow_restart_url"] is not None
    assert "node=node-background" in ctx_dict["ui_workflow_restart_url"]
    assert "ui_mode=background_run" in ctx_dict["ui_workflow_restart_url"]

    # At the start node itself, there's nothing to restart to.
    ctx_dict2, _ = get_instance_ui_context(str(inst.id), str(admin.id), requested_node_id="node-background")
    assert ctx_dict2["ui_workflow_restart_url"] is None


TESTS: list[TestCase] = [
    TestCase("valid branching graph passes", "sandbox", test_valid_branching_graph_passes),
    TestCase("empty nodes rejected", "sandbox", test_empty_nodes_rejected),
    TestCase("missing start node rejected", "sandbox", test_missing_start_node_rejected),
    TestCase("invalid ui_mode rejected", "sandbox", test_invalid_ui_mode_rejected),
    TestCase("edge referencing missing node rejected", "sandbox", test_edge_referencing_missing_node_rejected),
    TestCase("cycle rejected by default, allowed with opt-in", "sandbox", test_cycle_rejected_by_default_allowed_with_opt_in),
    TestCase("node template_id stripped on parse", "sandbox", test_node_template_id_stripped_on_parse),
    TestCase("edge between same ui_mode nodes rejected", "sandbox", test_edge_between_same_ui_mode_nodes_rejected),
    TestCase("source branching to two same-mode siblings rejected", "sandbox", test_source_branching_to_two_same_mode_siblings_rejected),
    TestCase("single mode allowed_ui_modes is just default", "sandbox", test_single_mode_allowed_ui_modes_is_just_default),
    TestCase("workflow mode allowed_ui_modes derived from graph", "sandbox", test_workflow_mode_allowed_ui_modes_derived_from_graph),
    TestCase("workflow graph save forces retest", "sandbox", test_workflow_graph_save_forces_retest),
    TestCase("invalid graph rejected by save", "sandbox", test_invalid_graph_rejected_by_save),
    TestCase("publish blocked when workflow has no nodes", "sandbox", test_publish_blocked_when_workflow_has_no_nodes),
    TestCase("workflow mode rejected for single-ui-mode runtime class", "sandbox", test_workflow_mode_rejected_for_single_ui_mode_runtime_class),
    TestCase("workflow mode allowed for multi-ui-mode runtime with empty graph", "sandbox", test_workflow_mode_allowed_for_multi_ui_mode_runtime_with_empty_graph),
    TestCase("start node offers branching choices", "sandbox", test_start_node_offers_branching_choices),
    TestCase("following edge switches same instance", "sandbox", test_following_edge_switches_same_instance),
    TestCase("success/failure conditions gated by exit_code", "sandbox", test_success_failure_conditions_gated_by_exit_code),
    TestCase("single mode instance has no workflow choices", "sandbox", test_single_mode_instance_has_no_workflow_choices),
    TestCase("custom_page graph passes validation without runtime capability checks", "sandbox", test_custom_page_graph_passes_validation_without_runtime_capability_checks),
    TestCase("custom_page node resolves as ui_mode bypassing Single Mode normalize", "sandbox", test_custom_page_node_resolves_as_ui_mode_bypassing_single_mode_normalize),
    TestCase("custom_page restart url points to start node", "sandbox", test_custom_page_restart_url_points_to_start_node),
]
