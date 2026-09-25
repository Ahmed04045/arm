"""Farm assistant: answers the farmer's questions from the network's latest findings.

The answer is grounded in what the network already knows: the latest rule-checked status and plan,
the last briefing (market, crop fit, profitability, research) and the crop's ranges. Price and market
questions also trigger a web search when a TAVILY_API_KEY is set.
"""

from __future__ import annotations

import re
from typing import Any

import llm
from agents.base import resolve_model
from agents.strategy import research
from knowledge import LABELS, crop_profile, fmt

SYSTEM = (
    "You are the farm assistant for a greenhouse at the Qatar University Agricultural Research Station. "
    "A network of specialist agents has already analysed the farm; their findings are given to you. "
    "Answer the farmer's question in plain language, in at most 5 sentences. Use only the facts provided: "
    "quote numbers exactly, say how reliable a price is (official, estimate, proxy, farmer, or unverified web), "
    "and never call gross revenue profit. If the facts don't answer the question, say what data is missing."
)
MARKET_WORDS = re.compile(r"price|market|sell|buy|profit|revenue|cost|qr|riyal|demand|crop|grow|plant", re.I)


def build_context(farm: dict[str, Any], monitor: dict[str, Any] | None, briefing: dict[str, Any] | None) -> str:
    lines = [f"Farm: {farm['name']}; crop {farm['crop']}; {farm.get('system', '')}; {farm.get('location', '')}."]
    ranges = crop_profile(farm["crop"])["ranges"]
    if monitor:
        a = monitor["assessment"]
        reading = monitor.get("reading", {})
        lines.append(f"Latest reading ({reading.get('timestamp')}): "
                     + ", ".join(f"{LABELS.get(k, k)} {fmt(k, v)}" for k, v in reading.items()
                                 if k != "timestamp" and isinstance(v, (int, float))) + ".")
        lines.append("Ideal ranges: " + ", ".join(f"{LABELS.get(k, k)} {fmt(k, lo)}–{fmt(k, hi)}"
                                                  for k, (lo, hi) in ranges.items() if k in reading) + ".")
        lines.append(f"Status: {a['status']}. Out of range: {'; '.join(a['readings']) or 'nothing'}.")
        if a["risks"]:
            lines.append("Crop risks: " + "; ".join(a["risks"][:5]) + ".")
        if a["watch"]:
            lines.append("Trends: " + "; ".join(a["watch"][:4]) + ".")
        if a["plan"]:
            lines.append("Action plan: " + "; ".join(f"{p['priority']}. {p['action']}" for p in a["plan"][:5]) + ".")
    if briefing:
        f = briefing["findings"]
        for agent in ("market_agent", "crop_fit_agent", "profit_agent", "research_agent"):
            if agent in f:
                lines.append(f"{agent.replace('_', ' ').title()}: {f[agent].get('facts') or f[agent]['summary']}")
        profit = f.get("profit_agent", {})
        if profit.get("seasons"):
            lines.append("Best earner per season (precomputed, gross revenue): " + "; ".join(profit["seasons"]) + ".")
            beats = profit.get("beats_current_yearly", [])
            lines.append(f"Over a whole year, crops earning more than {farm['crop']}: {', '.join(beats) or 'none'}.")
        ranking = profit.get("ranking", [])
        if ranking:
            lines.append("Gross revenue ranking (QR/m²/yr): " + "; ".join(
                f"{r['name']} {r['annual_qr_m2']} (price {r['price']} QR/kg, {r['confidence']}, fits {r['window']})" for r in ranking) + ".")
        lines.append(f"Last briefing: {briefing['assessment']['summary']}")
    return "\n".join(lines)


def answer(question: str, farm: dict[str, Any], monitor: dict[str, Any] | None, briefing: dict[str, Any] | None,
           state: dict[str, Any]) -> dict[str, Any]:
    """state needs installed_models, fallback_model, model_map (as in the network state). Returns {text, by, sources}."""
    context = build_context(farm, monitor, briefing)
    sources: list[str] = []
    if MARKET_WORDS.search(question):
        for note in research([f"{question} Qatar"]):
            context += f"\nWeb search (unverified) for '{note['query']}': {note['answer']}"
            sources += [s["url"] for s in note["sources"][:3]]
    model = resolve_model(state.get("assistant_model", "qwen2.5:7b"), state)
    text = llm.chat(model, SYSTEM, f"FACTS\n{context}\n\nFARMER'S QUESTION\n{question}", temperature=0.3)
    if text:
        return {"text": text, "by": model, "sources": sources}
    status = monitor["assessment"]["status"] if monitor else "unknown"
    top = monitor["assessment"]["plan"][0]["action"] if monitor and monitor["assessment"]["plan"] else "no action needed"
    return {"text": f"The AI assistant is offline (no local model or cloud key), so I can't answer free questions. "
                    f"Current status: {status}. Top action: {top}.", "by": "rules", "sources": []}
