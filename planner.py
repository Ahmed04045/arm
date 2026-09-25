"""Load and validate a department network spec written by the Agent Controller.

Spec shape (see farms/quars_amaranth_network.json):
    departments[]: id, name, mandate, model, depends_on[dept ids], head{id,name,prompt}, agents[]
    agents[]:      id, name, skill, purpose, inputs[fields | "*"], model?, prompt
    director:      id, name, model, prompt

validate_spec() rejects specs the runtime cannot build and fills in derived fields
(model per agent, the edge devices each agent listens to, upstream departments).
"""

from __future__ import annotations

import copy
import json
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any

from agents import SKILLS
from knowledge import fields_for

RESERVED = {"farm", "reading", "history", "archive", "findings", "reports", "assessment", "trace", "master"}


def available_fields(farm: dict[str, Any]) -> list[str]:
    return fields_for([s for d in farm.get("edge_devices", []) for s in d.get("sensors", [])])


def _sources(fields: list[str], farm: dict[str, Any]) -> list[str]:
    wanted = set(fields)
    return [d["id"] for d in farm.get("edge_devices", []) if wanted & set(fields_for(d.get("sensors", [])))]


def validate_spec(spec: dict[str, Any], farm: dict[str, Any]) -> dict[str, Any]:
    """Return a runnable copy of the spec for this farm's sensors.

    Agents whose sensors this farm doesn't have go dormant (listed in spec["dormant"]) instead of failing,
    so one network design fits both the full research greenhouse and a small EZFarm node.
    """
    spec = copy.deepcopy(spec)
    spec["dormant"] = []
    available = available_fields(farm)
    _prune_dormant(spec, available)
    errors: list[str] = []
    notes: list[str] = []
    ids: set[str] = set()

    def claim(node_id: str) -> None:
        if node_id in ids or node_id in RESERVED:
            errors.append(f"Duplicate or reserved id '{node_id}'.")
        ids.add(node_id)

    dept_ids = [d["id"] for d in spec.get("departments", [])]
    monitored: set[str] = set()
    for dept in spec.get("departments", []):
        claim(dept["id"])
        claim(dept["head"]["id"])
        dept.setdefault("depends_on", [])
        for dep in dept["depends_on"]:
            if dep not in dept_ids:
                errors.append(f"{dept['name']} depends on unknown department '{dep}'.")
        for agent in dept.get("agents", []):
            claim(agent["id"])
            if agent.get("skill") not in SKILLS:
                errors.append(f"{agent['name']}: unknown skill '{agent.get('skill')}'.")
            inputs = available if agent.get("inputs") == ["*"] else agent.get("inputs", [])
            agent["inputs"] = [f for f in inputs if f in available]
            agent.setdefault("model", dept.get("model"))
            agent["sources"] = _sources(agent["inputs"], farm)
            agent["reads_from"] = dept["depends_on"]
            if agent.get("skill") == "range_monitor":
                if not agent["inputs"]:
                    errors.append(f"{agent['name']}: a monitor needs at least one available field.")
                monitored.update(agent["inputs"])
        if not dept.get("agents"):
            errors.append(f"{dept['name']} has no agents.")

    unmonitored = [f for f in available if f not in monitored]
    if unmonitored:
        notes.append(f"No monitor owns: {', '.join(unmonitored)}.")
    if "director" not in spec:
        errors.append("Spec has no director.")
    else:
        claim(spec["director"]["id"])
    try:
        TopologicalSorter({d["id"]: d["depends_on"] for d in spec.get("departments", [])}).prepare()
    except CycleError:
        errors.append("Department dependencies contain a cycle.")

    if errors:
        raise ValueError("Invalid network spec:\n- " + "\n- ".join(errors))
    spec["notes"] = notes
    return spec


def _prune_dormant(spec: dict[str, Any], available: list[str]) -> None:
    """Drop agents that declare sensor inputs none of which exist, then departments left empty."""
    for dept in spec.get("departments", []):
        active = []
        for agent in dept.get("agents", []):
            declared = agent.get("inputs", [])
            if declared and declared != ["*"] and not set(declared) & set(available):
                spec["dormant"].append({"id": agent["id"], "name": agent["name"], "department": dept["id"],
                                        "needs": declared})
            else:
                active.append(agent)
        dept["agents"] = active
    gone = {d["id"] for d in spec.get("departments", []) if not d["agents"]}
    spec["departments"] = [d for d in spec.get("departments", []) if d["id"] not in gone]
    for dept in spec["departments"]:
        dept["depends_on"] = [d for d in dept.get("depends_on", []) if d not in gone]


def load_network(path: Path, farm: dict[str, Any]) -> dict[str, Any]:
    return validate_spec(json.loads(path.read_text(encoding="utf-8")), farm)


def agent_count(spec: dict[str, Any]) -> int:
    """Specialists + department heads + director."""
    return sum(len(d["agents"]) + 1 for d in spec["departments"]) + 1


def models_used(spec: dict[str, Any]) -> list[str]:
    models = [spec["director"].get("model")]
    for dept in spec["departments"]:
        models += [dept.get("model")] + [a.get("model") for a in dept["agents"]]
    return list(dict.fromkeys(m for m in models if m))
