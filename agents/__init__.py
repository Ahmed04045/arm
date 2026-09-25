"""Agent library: the skills a network spec can assign, and node factories for the graph."""

from __future__ import annotations

import time
from typing import Any, Callable

from .analytics import anomaly, forecast, trend
from .crop_science import crop_physiology, leaf_quality, plant_health
from .diagnosis import ai_diagnosis
from .leadership import department_head, director
from .monitors import range_monitor
from .strategy import crop_fit, market_watch, profitability, web_research

SKILLS: dict[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]] = {
    "range_monitor": range_monitor,
    "crop_physiology": crop_physiology,
    "leaf_quality": leaf_quality,
    "plant_health": plant_health,
    "ai_diagnosis": ai_diagnosis,
    "trend": trend,
    "anomaly": anomaly,
    "forecast": forecast,
    "market_watch": market_watch,
    "crop_fit": crop_fit,
    "profitability": profitability,
    "web_research": web_research,
}


def _timed(node_id: str, role: str, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def node(state: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        update = fn(state)
        update["trace"] = [{"node": node_id, "role": role, "ms": round((time.perf_counter() - started) * 1000)}]
        return update

    return node


def agent_node(agent: dict[str, Any]):
    return _timed(agent["id"], "agent", lambda s: {"findings": {agent["id"]: SKILLS[agent["skill"]](agent, s)}})


def head_node(department: dict[str, Any]):
    return _timed(department["head"]["id"], "head", lambda s: {"reports": {department["id"]: department_head(department, s)}})


def director_node(spec: dict[str, Any]):
    return _timed(spec["director"]["id"], "director", lambda s: {"assessment": director(spec, s)})
