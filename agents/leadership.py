"""Department heads consolidate their team; the Farm Director turns every report into a plan."""

from __future__ import annotations

from typing import Any

import llm
from knowledge import ACTIONS

from .base import installed_sensors, narrate, resolve_model, worst

SEVERITY_RANK = {"CRITICAL": 0, "WARNING": 1}
WATCH_KINDS = {"trend", "anomaly", "forecast"}


def department_head(department: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    members = [(a, state["findings"].get(a["id"], {})) for a in department["agents"]]
    issues = [i for _, f in members for i in f.get("issues", [])]
    flagged = [a["name"] for a, f in members if f.get("status") not in (None, "OK")]
    if issues:
        summary = f"{len(flagged)}/{len(members)} agents raised {len(issues)} issue(s): " + "; ".join(i["message"] for i in issues[:4])
        summary += "." if len(issues) <= 4 else f"; +{len(issues) - 4} more."
    else:
        summary = f"All {len(members)} agents report normal conditions."
    report = {"status": worst([f.get("status", "OK") for _, f in members]), "summary": summary, "issues": issues}
    head = {**department["head"], "model": department["head"].get("model") or department.get("model")}
    return narrate(head, state, report,
                   f"As head of the {department['name']} ({department['mandate']}), brief the Farm Director in 2-3 sentences: "
                   "the department's verdict and its single most important concern.", leader=True)


def director(spec: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    reports = state["reports"]
    order = [d["id"] for d in spec["departments"]]
    issues = [dict(i, department=d) for d in order for i in reports.get(d, {}).get("issues", [])]
    act_on = [i for i in issues if i["kind"] not in WATCH_KINDS]
    act_on.sort(key=lambda i: (SEVERITY_RANK[i["severity"]], i["kind"] != "reading", order.index(i["department"])))

    plan, seen = [], set()
    for issue in act_on:
        action, cmd = issue.get("action"), issue.get("cmd")
        if not action and (issue["field"], issue["direction"]) in ACTIONS:
            action, cmd = ACTIONS[(issue["field"], issue["direction"])]
        if action and action not in seen:
            seen.add(action)
            plan.append({"priority": len(plan) + 1, "severity": issue["severity"], "cmd": cmd or "NOTIFY", "action": action,
                         "reason": issue["message"], "department": issue["department"],
                         "target_devices": ", ".join(issue.get("sources", [])) or "farm team"})

    statuses = [r.get("status") for r in reports.values()]
    status = "CRITICAL" if "CRITICAL" in statuses else "ATTENTION REQUIRED" if issues else "STABLE"
    names = {d["id"]: d["name"] for d in spec["departments"]}
    hot = [names[d] for d in order if reports.get(d, {}).get("status") not in (None, "OK")]
    summary = (f"{len(hot)} of {len(order)} departments raised concerns ({', '.join(hot)}). "
               f"{len(plan)} action(s) queued for the master device." if hot else "All departments report normal conditions.")

    assessment = {
        "status": status,
        "summary": summary,
        "plan": plan,
        "readings": [i["message"] for i in act_on if i["kind"] == "reading"],
        "risks": [i["message"] for i in act_on if i["kind"] == "risk"],
        "watch": [i["message"] for i in issues if i["kind"] in WATCH_KINDS],
    }
    assessment["insights"] = [s for f in state["findings"].values() for s in f.get("suggestions", [])]
    assessment["research"] = [n for f in state["findings"].values() for n in f.get("notes", [])]
    who = spec["director"]
    assessment["model"] = {"assigned": who.get("model"), "used": None}
    assessment["facts"] = summary
    if state.get("purpose") == "briefing" and state.get("llm_mode", "off") != "off":
        model = resolve_model(who.get("model"), state)
        briefs = "\n".join(f"- {names[d]}: {reports[d]['summary']}" for d in order if d in reports)
        strategy = ""
        if assessment["insights"]:
            strategy = ("Market & Strategy suggestions (gross-revenue estimates, costs excluded):\n"
                        + "\n".join(f"- {s['title']}: {s['gain_pct']:+d}% ({s['detail']})" for s in assessment["insights"]) + "\n")
            task = ("Write the farmer a briefing in two short parts, each starting on its own line.\n"
                    "'Today:' 2-3 sentences on the overall condition, the most urgent problem and what to do next. "
                    "If the status is STABLE, say so plainly and name what to keep watching; do not invent problems.\n"
                    "'Opportunity:' 2 sentences on the most useful market or crop suggestion above, with its numbers and "
                    "how reliable the price is. Gross revenue is not profit.\n")
        else:
            task = ("Write the station manager a 3-5 sentence briefing: overall condition, the most urgent problem and why, "
                    "and what the team must do in the next few hours. If the status is STABLE, say so plainly and name what "
                    "to keep watching; do not invent problems.\n")
        prompt = (f"Farm: {state['farm']['name']} — {state['farm']['crop']}. Overall status: {status}.\n"
                  f"{installed_sensors(state)}\n"
                  f"Department briefings:\n{briefs}\nAction plan: {[p['action'] for p in plan[:6]]}\n{strategy}"
                  f"{task}Use only these facts.")
        text = llm.chat(model, who.get("prompt", "You are the Farm Director."), prompt)
        if text:
            assessment["summary"] = text
            assessment["model"]["used"] = model
    return assessment
