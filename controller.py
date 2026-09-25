"""Runtime: compile a department network spec into a LangGraph and execute it.

Graph shape
    START ─► agents of independent departments (parallel)
    agents ─► their department head (join)
    upstream heads ─► agents of dependent departments (join)
    all heads ─► Farm Director ─► END
"""

from __future__ import annotations

import operator
from graphlib import TopologicalSorter
from typing import Annotated, Any, TypedDict

from agents import agent_node, director_node, head_node

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # the demo still runs, sequentially, without LangGraph
    StateGraph = None


def _merge(left: dict, right: dict) -> dict:
    return {**left, **right}


class NetworkState(TypedDict, total=False):
    farm: dict[str, Any]
    reading: dict[str, Any]
    history: list[dict[str, Any]]      # analysis window
    archive: list[dict[str, Any]]      # longer look-back for forecasting
    interval_minutes: int
    llm_mode: str                      # off | director | leaders | all
    model_map: dict[str, str]          # spec model -> where it actually runs (farms/model_routes.json)
    purpose: str                       # monitor (every interval) | briefing (twice a day / on request)
    installed_models: list[str]
    fallback_model: str
    findings: Annotated[dict[str, Any], _merge]   # agents in parallel write here
    reports: Annotated[dict[str, Any], _merge]    # one per department head
    trace: Annotated[list[dict[str, Any]], operator.add]
    assessment: dict[str, Any]


def _edges(spec: dict[str, Any]) -> list[tuple[list[str], str]]:
    """(sources, target) pairs; a source list of 2+ nodes is a join."""
    heads = {d["id"]: d["head"]["id"] for d in spec["departments"]}
    edges: list[tuple[list[str], str]] = []
    for dept in spec["departments"]:
        upstream = [heads[d] for d in dept["depends_on"]]
        for agent in dept["agents"]:
            edges.append((upstream, agent["id"]))
        edges.append(([a["id"] for a in dept["agents"]], dept["head"]["id"]))
    edges.append((list(heads.values()), spec["director"]["id"]))
    return edges


def _nodes(spec: dict[str, Any]) -> dict[str, Any]:
    nodes = {}
    for dept in spec["departments"]:
        for agent in dept["agents"]:
            nodes[agent["id"]] = agent_node(agent)
        nodes[dept["head"]["id"]] = head_node(dept)
    nodes[spec["director"]["id"]] = director_node(spec)
    return nodes


def build_graph(spec: dict[str, Any]):
    graph = StateGraph(NetworkState)
    for node_id, fn in _nodes(spec).items():
        graph.add_node(node_id, fn)
    for sources, target in _edges(spec):
        if not sources:
            graph.add_edge(START, target)
        else:
            graph.add_edge(sources if len(sources) > 1 else sources[0], target)
    graph.add_edge(spec["director"]["id"], END)
    return graph.compile()


def for_purpose(spec: dict[str, Any], purpose: str) -> dict[str, Any]:
    """'monitor' (every interval) skips departments marked "runs": "briefing"; 'briefing' runs everything."""
    if purpose == "briefing":
        return spec
    return {**spec, "departments": [d for d in spec["departments"] if d.get("runs", "always") != "briefing"]}


def execute_network(
    spec: dict[str, Any],
    farm: dict[str, Any],
    history: list[dict[str, Any]],
    archive: list[dict[str, Any]] | None = None,
    llm_mode: str = "off",
    installed_models: list[str] | None = None,
    fallback_model: str = "qwen2.5:3b",
    interval_minutes: int = 15,
    model_map: dict[str, str] | None = None,
    purpose: str = "monitor",
) -> dict[str, Any]:
    spec = for_purpose(spec, purpose)
    initial: NetworkState = {
        "farm": farm, "reading": history[-1], "history": history, "archive": archive or history,
        "interval_minutes": interval_minutes, "llm_mode": llm_mode,
        "installed_models": installed_models or [], "fallback_model": fallback_model, "model_map": model_map or {},
        "purpose": purpose,
        "findings": {}, "reports": {}, "trace": [], "assessment": {},
    }
    if StateGraph is not None:
        return build_graph(spec).invoke(initial)

    state: dict[str, Any] = dict(initial)
    nodes = _nodes(spec)
    deps: dict[str, set[str]] = {}
    for sources, target in _edges(spec):
        deps.setdefault(target, set()).update(sources)
    for node_id in TopologicalSorter(deps).static_order():
        update = nodes[node_id](state)
        for key in ("findings", "reports"):
            state[key] = _merge(state[key], update.get(key, {}))
        state["trace"] = state["trace"] + update.get("trace", [])
        state["assessment"] = update.get("assessment", state["assessment"])
    return state


def to_dot(spec: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    """Graphviz DOT: one cluster per department, coloured by status."""
    fill = {"OK": "#d8efc9", "WARNING": "#fbe3b0", "CRITICAL": "#f6c3bb",
            "STABLE": "#d8efc9", "ATTENTION REQUIRED": "#fbe3b0"}
    findings = (result or {}).get("findings", {})
    reports = (result or {}).get("reports", {})
    status = (result or {}).get("assessment", {}).get("status")

    def color(state: str | None) -> str:
        return fill.get(state or "", "#ffffff")

    lines = [
        "digraph G {", 'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.55; compound=true;',
        'node [style="rounded,filled" shape=box fontname="Helvetica" fontsize=10 color="#2d744d" penwidth=1.1];',
        'edge [color="#8a978f" arrowsize=0.6];',
        'master [label="Master device\\n(sensors → state)" shape=cylinder fillcolor="#e8eef9"];',
    ]
    for dept in spec["departments"]:
        model = dept.get("model", "") + (" · briefings only" if dept.get("runs") == "briefing" else "")
        lines.append(f'subgraph "cluster_{dept["id"]}" {{ label="{dept["name"]}\\n{model}"; fontname="Helvetica-Bold"; fontsize=11; '
                     'style="rounded,dashed"; color="#7c8c82";')
        for agent in dept["agents"]:
            lines.append(f'"{agent["id"]}" [label="{agent["name"]}" fillcolor="{color(findings.get(agent["id"], {}).get("status"))}"];')
        head = dept["head"]
        lines.append(f'"{head["id"]}" [label="★ {head["name"]}" shape=box penwidth=2 fillcolor="{color(reports.get(dept["id"], {}).get("status"))}"];')
        lines.append("}")
    director = spec["director"]
    lines.append(f'"{director["id"]}" [label="{director["name"]}\\n{director.get("model", "")}" shape=doubleoctagon fillcolor="{color(status)}"];')
    for sources, target in _edges(spec):
        for source in sources or ["master"]:
            lines.append(f'"{source}" -> "{target}";')
    lines.append(f'"{director["id"]}" -> master [label=" action plan" style=dashed fontsize=9 constraint=false];')
    lines.append("}")
    return "\n".join(lines)
