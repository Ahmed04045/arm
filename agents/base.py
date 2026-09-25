"""Helpers shared by every agent: range checks, status roll-up, and LLM narration."""

from __future__ import annotations

from typing import Any

import llm
from knowledge import LABELS, fmt

CRITICAL_FRACTION = 0.35  # no tolerated band known: a deviation beyond this share of the ideal band is critical


def check_field(field: str, value: float, bounds: tuple[float, float],
                acceptable: tuple[float, float] | None = None) -> dict[str, Any] | None:
    """Outside the ideal band -> WARNING; outside the tolerated band -> CRITICAL."""
    low, high = bounds
    if low <= value <= high:
        return None
    direction = "high" if value > high else "low"
    if acceptable:
        critical = not acceptable[0] <= value <= acceptable[1]
    else:
        deviation = value - high if direction == "high" else low - value
        critical = deviation / (high - low) > CRITICAL_FRACTION
    return {
        "kind": "reading",
        "field": field,
        "value": value,
        "direction": direction,
        "severity": "CRITICAL" if critical else "WARNING",
        "message": f"{LABELS.get(field, field)} {direction.upper()}: {fmt(field, value)} (ideal {fmt(field, low)}–{fmt(field, high)})",
    }


def worst(statuses: list[str]) -> str:
    for level in ("CRITICAL", "WARNING"):
        if level in statuses:
            return level
    return "OK"


def finding(agent: dict[str, Any], issues: list[dict[str, Any]], ok_summary: str, **extra: Any) -> dict[str, Any]:
    for issue in issues:
        issue.setdefault("agent", agent["id"])
        issue.setdefault("sources", agent.get("sources", []))
    summary = "; ".join(i["message"] for i in issues) + "." if issues else ok_summary
    return {"status": worst([i["severity"] for i in issues]), "summary": summary, "issues": issues, **extra}


def installed_sensors(state: dict[str, Any]) -> str:
    fields = [LABELS.get(k, k) for k, v in state["reading"].items() if k != "timestamp" and isinstance(v, (int, float))]
    return f"Sensors installed: {', '.join(fields)}. Do not mention or advise on any other measurement."


def resolve_model(requested: str | None, state: dict[str, Any]) -> str:
    """Where the assigned model runs: its route (cloud if the key is set, or local if installed),
    then the assigned model itself if installed, otherwise the fallback model."""
    installed = state.get("installed_models", [])

    def usable(model: str | None) -> bool:
        return bool(model) and (llm.provider_ready(model) or model in installed or f"{model}:latest" in installed)

    routed = state.get("model_map", {}).get(requested or "")
    for candidate in (routed, requested, state["fallback_model"]):
        if usable(candidate):
            return candidate
    return installed[0] if installed else state["fallback_model"]   # e.g. cloud route chosen but no API key


def narrate(who: dict[str, Any], state: dict[str, Any], result: dict[str, Any], task: str, leader: bool = False) -> dict[str, Any]:
    """Let the agent's own model explain rule-verified facts. It never supplies new numbers."""
    mode = state.get("llm_mode", "off")
    assigned = who.get("model")
    result["model"] = {"assigned": assigned, "used": None}
    result["facts"] = result["summary"]
    if state.get("purpose") != "briefing" or mode in ("off", "director") or (mode == "leaders" and not leader):
        return result
    model = resolve_model(assigned, state)
    farm = state["farm"]
    prompt = (
        f"Farm: {farm['name']} — {farm['crop']} ({farm['system']}), {farm['location']}.\n"
        f"Snapshot: {state['reading'].get('timestamp')}\n"
        f"{installed_sensors(state)}\n"
        f"Verified facts (do not invent numbers):\n{result['summary']}\n\n{task}"
    )
    text = llm.chat(model, who.get("prompt") or f"You are the {who['name']}.", prompt)
    if text:
        result["summary"] = text
        result["model"]["used"] = model
    return result
