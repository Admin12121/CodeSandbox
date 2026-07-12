from __future__ import annotations

from tests._context import TestCase, TestContext


def _two_stage_graph(**edge_overrides) -> dict:
    edge = {"from_stage_key": "scan", "to_stage_key": "lab", "condition": None, "label": ""}
    edge.update(edge_overrides)
    return {
        "stages": [
            {"stage_key": "scan", "name": "Background Scan", "template_id": "tpl-1", "ui_mode": "background_run"},
            {"stage_key": "lab", "name": "Manual Lab", "template_id": "tpl-2", "ui_mode": "lab_ui"},
        ],
        "edges": [edge],
    }


def test_valid_two_stage_graph_passes(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    assert validate_workflow_graph(_two_stage_graph()) is None


def test_empty_graph_rejected(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    error = validate_workflow_graph({"stages": [], "edges": []})
    assert error is not None
    assert "at least one stage" in error


def test_duplicate_stage_keys_rejected(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    graph = {
        "stages": [
            {"stage_key": "a", "name": "A", "template_id": "tpl-1", "ui_mode": "terminal_only"},
            {"stage_key": "a", "name": "A2", "template_id": "tpl-2", "ui_mode": "terminal_only"},
        ],
        "edges": [],
    }
    error = validate_workflow_graph(graph)
    assert error is not None and "unique" in error


def test_edge_referencing_missing_stage_rejected(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    graph = _two_stage_graph(to_stage_key="does-not-exist")
    error = validate_workflow_graph(graph)
    assert error is not None and "doesn't exist" in error


def test_stage_missing_template_id_rejected(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    graph = {
        "stages": [{"stage_key": "a", "name": "A", "template_id": "", "ui_mode": "terminal_only"}],
        "edges": [],
    }
    error = validate_workflow_graph(graph)
    assert error is not None and "template" in error


def test_cycle_rejected_by_default(ctx: TestContext) -> None:
    """Attack-simulation-style graphs (switching between attacker/target/
    monitor instances) can be intentionally cyclic, but that must be an
    explicit opt-in (`allow_cycles`), not the default — a cycle in a linear
    background_run -> lab_ui pipeline is almost certainly an authoring
    mistake the canvas should catch before publish."""
    from codesandbox.features.workflow.service import validate_workflow_graph

    graph = {
        "stages": [
            {"stage_key": "a", "name": "A", "template_id": "tpl-1", "ui_mode": "terminal_only"},
            {"stage_key": "b", "name": "B", "template_id": "tpl-2", "ui_mode": "terminal_only"},
        ],
        "edges": [
            {"from_stage_key": "a", "to_stage_key": "b", "condition": None, "label": ""},
            {"from_stage_key": "b", "to_stage_key": "a", "condition": None, "label": ""},
        ],
    }
    error = validate_workflow_graph(graph)
    assert error is not None and "cycle" in error.lower()


def test_cycle_allowed_with_explicit_opt_in(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    graph = {
        "allow_cycles": True,
        "stages": [
            {"stage_key": "a", "name": "Attacker", "template_id": "tpl-1", "ui_mode": "terminal_only"},
            {"stage_key": "b", "name": "Target", "template_id": "tpl-2", "ui_mode": "terminal_only"},
        ],
        "edges": [
            {"from_stage_key": "a", "to_stage_key": "b", "condition": None, "label": ""},
            {"from_stage_key": "b", "to_stage_key": "a", "condition": None, "label": ""},
        ],
    }
    assert validate_workflow_graph(graph) is None


def test_unknown_ui_mode_rejected(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import validate_workflow_graph

    graph = {
        "stages": [{"stage_key": "a", "name": "A", "template_id": "tpl-1", "ui_mode": "not_a_real_mode"}],
        "edges": [],
    }
    error = validate_workflow_graph(graph)
    assert error is not None and "ui_mode" in error


def test_parse_graph_round_trips_and_fails_open(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import parse_graph
    import json

    graph = _two_stage_graph()
    encoded = json.dumps(graph)
    decoded = parse_graph(encoded)
    assert decoded["stages"][0]["stage_key"] == "scan"
    assert decoded["edges"][0]["to_stage_key"] == "lab"

    # Malformed/absent graph_json fails open to an empty (but valid-shape) graph.
    assert parse_graph(None) == {"stages": [], "edges": []}
    assert parse_graph("not json") == {"stages": [], "edges": []}


def test_entry_stage_is_the_one_with_no_incoming_edge(ctx: TestContext) -> None:
    from codesandbox.features.workflow.service import _entry_stage, _next_stage

    graph = _two_stage_graph()
    entry = _entry_stage(graph)
    assert entry is not None and entry["stage_key"] == "scan"

    nxt = _next_stage(graph, "scan")
    assert nxt is not None and nxt["stage_key"] == "lab"
    assert _next_stage(graph, "lab") is None  # last stage — workflow finishes here


TESTS: list[TestCase] = [
    TestCase("valid two-stage graph passes", "workflow", test_valid_two_stage_graph_passes),
    TestCase("empty graph rejected", "workflow", test_empty_graph_rejected),
    TestCase("duplicate stage keys rejected", "workflow", test_duplicate_stage_keys_rejected),
    TestCase("edge referencing missing stage rejected", "workflow", test_edge_referencing_missing_stage_rejected),
    TestCase("stage missing template_id rejected", "workflow", test_stage_missing_template_id_rejected),
    TestCase("cycle rejected by default", "workflow", test_cycle_rejected_by_default),
    TestCase("cycle allowed with explicit opt-in", "workflow", test_cycle_allowed_with_explicit_opt_in),
    TestCase("unknown ui_mode rejected", "workflow", test_unknown_ui_mode_rejected),
    TestCase("parse_graph round-trips and fails open", "workflow", test_parse_graph_round_trips_and_fails_open),
    TestCase("entry stage has no incoming edge; next stage advances correctly", "workflow", test_entry_stage_is_the_one_with_no_incoming_edge),
]
