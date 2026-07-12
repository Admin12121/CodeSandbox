from __future__ import annotations

import json

UI_WORKFLOW_MODES = ("terminal_only", "lab_ui", "background_run", "desktop_gui", "android_ui", "custom_page")
UI_WORKFLOW_CONDITIONS = ("success", "failure", "manual", "always")

# custom_page is a static admin-authored HTML screen (e.g. a "Workflow
# failed" landing page with a restart link) — it doesn't run in any
# sandbox runtime, so it's exempt from the runtime_class UI-mode
# compatibility checks the other 5 real modes go through.
UI_WORKFLOW_RUNTIME_BACKED_MODES = ("terminal_only", "lab_ui", "background_run", "desktop_gui", "android_ui")


def parse_ui_workflow_graph(graph_json: str | None) -> dict:
    """Template-scoped UI workflow graph — every node is a UI stage of the
    *same* sandbox template (no template_id per node), distinct from
    features/workflow/'s cross-template SandboxWorkflow.graph_json."""
    if not graph_json:
        return {"mode": "workflow", "start_node_id": None, "nodes": [], "edges": []}
    try:
        graph = json.loads(graph_json)
    except (TypeError, ValueError):
        return {"mode": "workflow", "start_node_id": None, "nodes": [], "edges": []}
    if not isinstance(graph, dict):
        return {"mode": "workflow", "start_node_id": None, "nodes": [], "edges": []}
    graph.setdefault("mode", "workflow")
    graph.setdefault("start_node_id", None)
    graph.setdefault("nodes", [])
    graph.setdefault("edges", [])
    # Nodes must never carry a template reference — this graph is always
    # scoped to the template it belongs to, injected or stale keys are
    # stripped defensively rather than trusted.
    for node in graph["nodes"]:
        if isinstance(node, dict):
            node.pop("template_id", None)
    return graph


def validate_ui_workflow_graph(graph: dict) -> str | None:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        return "Workflow Mode requires at least one node."
    if not isinstance(edges, list):
        return "Edges must be a list."

    ids = [str(n.get("id") or "") for n in nodes]
    if any(not i for i in ids):
        return "Every node needs an id."
    if len(set(ids)) != len(ids):
        return "Node ids must be unique."
    id_set = set(ids)

    for node in nodes:
        ui_mode = str(node.get("ui_mode") or "")
        if ui_mode not in UI_WORKFLOW_MODES:
            return f"Node '{node.get('label') or node.get('id')}' has an invalid ui_mode."

    start_node_id = str(graph.get("start_node_id") or "")
    if not start_node_id or start_node_id not in id_set:
        return "Workflow Mode requires one valid start node."

    nodes_by_id = {str(n.get("id")): n for n in nodes}
    adjacency: dict[str, list[str]] = {i: [] for i in ids}
    # Every source's outgoing targets must have pairwise-distinct ui_modes —
    # both a direct source==target match and two sibling branches landing
    # on the same mode are the same underlying mistake (an edge with
    # nothing to actually switch to, or a branch the user can't tell apart).
    seen_target_modes_by_source: dict[str, dict[str, str]] = {i: {} for i in ids}
    for edge in edges:
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if src not in id_set or dst not in id_set:
            return "An edge references a node that doesn't exist."
        condition = str(edge.get("condition") or "manual")
        if condition not in UI_WORKFLOW_CONDITIONS:
            return f"Edge '{edge.get('id') or ''}' has an invalid condition."
        src_mode = nodes_by_id[src].get("ui_mode")
        dst_mode = nodes_by_id[dst].get("ui_mode")
        if src_mode == dst_mode:
            return (
                f"'{nodes_by_id[src].get('label') or src}' and '{nodes_by_id[dst].get('label') or dst}' "
                "have the same UI mode — connect stages with different UI modes to branch between."
            )
        existing_dst = seen_target_modes_by_source[src].get(dst_mode)
        if existing_dst and existing_dst != dst:
            return (
                f"'{nodes_by_id[src].get('label') or src}' already branches to a '{dst_mode}' stage — "
                "each branch from one stage needs a distinct UI mode."
            )
        seen_target_modes_by_source[src][dst_mode] = dst
        adjacency[src].append(dst)

    if not graph.get("allow_cycles"):
        cycle = _find_cycle(adjacency)
        if cycle:
            return f"Workflow graph has a cycle ({' -> '.join(cycle)}) — cycles are disabled for now."

    return None


def _find_cycle(adjacency: dict[str, list[str]]) -> list[str] | None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in adjacency}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for neighbor in adjacency.get(node, []):
            if color.get(neighbor, WHITE) == GRAY:
                return path[path.index(neighbor):] + [neighbor]
            if color.get(neighbor, WHITE) == WHITE:
                found = visit(neighbor)
                if found:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for node in adjacency:
        if color[node] == WHITE:
            found = visit(node)
            if found:
                return found
    return None


def ui_workflow_node_ui_modes(graph: dict) -> list[str]:
    """Derives the effective allowed_ui_modes for a workflow-mode template —
    every distinct ui_mode any node in the graph actually uses."""
    modes: list[str] = []
    for node in graph.get("nodes") or []:
        mode = str(node.get("ui_mode") or "")
        if mode and mode not in modes:
            modes.append(mode)
    return modes


def ui_workflow_node_by_id(graph: dict, node_id: str | None) -> dict | None:
    if not node_id:
        return None
    for node in graph.get("nodes") or []:
        if str(node.get("id")) == str(node_id):
            return node
    return None


def ui_workflow_start_node(graph: dict) -> dict | None:
    node = ui_workflow_node_by_id(graph, graph.get("start_node_id"))
    if node is not None:
        return node
    nodes = graph.get("nodes") or []
    return nodes[0] if nodes else None


def ui_workflow_outgoing_edges(graph: dict, node_id: str) -> list[dict]:
    return [e for e in (graph.get("edges") or []) if str(e.get("source")) == str(node_id)]
