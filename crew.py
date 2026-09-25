"""The farm agent network as CrewAI crews (Hydro Monitor plan 3.3-3.4).

One CrewAI agent per department, built from its definition in network.json (role, goal, backstory, llm).
Every task replies in a fixed JSON format (a pydantic model CrewAI enforces). network.py runs three stages,
each a sequential CrewAI crew, and writes a short digest of the earlier reports into the next stage's tasks
(small models copy raw reports they get as context, so they get the digest instead):

    stage 1  Agri-Environment, Soil & Water
    stage 2  Crop Science (reads the stage-1 digest), Data & Analytics, Market & Strategy (extra)
    stage 3  Farm Director (reads every report's digest), then the Arabic translation
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, Field

import llm

os.environ.setdefault("CREWAI_TELEMETRY_OPT_OUT", "true")   # nothing leaves the laptop except model calls
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

from crewai import LLM, Agent, Crew, Process, Task   # noqa: E402  (after the telemetry switches)

log = logging.getLogger("crew")


# ── the fixed JSON formats ───────────────────────────────────────────────
class RangeProposal(BaseModel):
    field_id: str = Field(description="F1, F2, ...")
    kind: str = Field(description="soil_moisture, temp_air, humidity or level")
    min: float
    max: float
    reason: str


class PumpProposal(BaseModel):
    field_id: str
    seconds: int
    reason: str


class DepartmentReport(BaseModel):
    summary: str = Field(description="2-3 sentences with the key numbers")
    warnings: list[str] = Field(default_factory=list, description="risks, most serious first, at most 3")
    todos: list[str] = Field(default_factory=list, description="things a person must do, at most 3")
    ranges: list[RangeProposal] = Field(default_factory=list, description="only settings this department may change; empty if no change")
    pump_seconds: list[PumpProposal] = Field(default_factory=list, description="only if this department may change pump_seconds")


class Change(BaseModel):
    field_id: str
    setting: str = Field(description="soil_moisture, temp_air, humidity, level or pump_seconds")
    min: float | None = Field(default=None, description="new minimum (ranges only)")
    max: float | None = Field(default=None, description="new maximum (ranges only)")
    seconds: int | None = Field(default=None, description="new pump run (pump_seconds only)")
    reason: str


class DirectorPlan(BaseModel):
    changes: list[Change] = Field(default_factory=list, description="only the settings that should change; empty keeps every range")
    message: str = Field(description="to the farmer: at most 4 short, plain sentences")
    todos: list[str] = Field(description="concrete tasks for the farmer, most urgent first, at most 5")


class Translation(BaseModel):
    message_ar: str
    todos_ar: list[str]


REPORT_RULES = (
    "Rules: use only the numbers given here and never invent readings. Propose a change only when the data gives a "
    "reason, and only inside the hard limits. Mention only sensors that exist on these beds."
)


def crew_llm(model: str, temperature: float = 0.2):
    """Our model names (llm.py) -> a CrewAI LLM. Every provider, Ollama included, speaks the OpenAI API."""
    provider, name = llm.split(model)
    if provider == "ollama":
        base_url, key = f"{OLLAMA_URL}/v1", "ollama"
    else:
        cfg = llm.PROVIDERS[provider]
        base_url, key = cfg["base_url"], os.environ.get(cfg["key_env"] or "", "ollama")
    return LLM(model=f"openai/{name}", base_url=base_url, api_key=key, temperature=temperature, max_tokens=900)


def _agent(definition: dict[str, Any], model: str, temperature: float = 0.2) -> Agent:
    return Agent(role=definition["role"], goal=definition["goal"], backstory=definition["backstory"],
                 llm=crew_llm(model, temperature), allow_delegation=False, max_iter=2, verbose=False)


def _safe(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")   # CrewAI treats {name} in task text as a template slot


def _kickoff(tasks: list[Task]) -> str | None:
    try:
        Crew(agents=[t.agent for t in tasks], tasks=tasks, process=Process.sequential, verbose=False).kickoff()
        return None
    except Exception as err:   # keep whatever finished; the caller decides what a missing result means
        log.warning("crew stopped: %s", err)
        return f"{type(err).__name__}: {err}"


def _parsed(task: Task | None) -> dict[str, Any] | None:
    out = task.output if task else None
    return out.pydantic.model_dump() if out is not None and out.pydantic is not None else None


def run_departments(departments: list[dict[str, Any]], models: dict[str, str]) -> tuple[dict[str, Any], str | None]:
    """departments: [{id, agent (definition), brief (task text), sets}]. One crew, one task each."""
    tasks = {}
    for dept in departments:
        sets = dept["sets"]
        expected = ("JSON department report. " + (f"You may change only: {', '.join(sets)}." if sets else
                                                  "You change no settings: leave 'ranges' and 'pump_seconds' empty."))
        # context=[]: in a sequential crew CrewAI otherwise hands every task the previous task's output, and small
        # models then copy that report instead of writing their own
        tasks[dept["id"]] = Task(description=_safe(dept["brief"] + "\n\n" + REPORT_RULES), expected_output=expected,
                                 agent=_agent(dept["agent"], models[dept["id"]]), output_pydantic=DepartmentReport,
                                 context=[])
    error = _kickoff(list(tasks.values()))
    return {d: _parsed(t) for d, t in tasks.items()}, error


def run_director(director: dict[str, Any], model: str, brief: str) -> tuple[dict[str, Any] | None, str | None]:
    plan_task = Task(description=_safe(brief), expected_output="JSON plan: changes, message, todos.",
                     agent=_agent(director["agent"], model), output_pydantic=DirectorPlan, context=[])
    error = _kickoff([plan_task])
    return _parsed(plan_task), error


def _clean_arabic(text: str) -> bool:
    """Small Qwen models sometimes slip Chinese characters into other languages."""
    return not any("぀" <= ch <= "鿿" or "가" <= ch <= "힯" for ch in text)


def translate(message: str, todos: list[str], model: str, attempts: int = 2) -> dict[str, Any] | None:
    """The farmer's message and to-dos in Modern Standard Arabic, or None if no clean translation came back."""
    source = "Message: " + message + "\nTo-dos:\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(todos))
    for attempt in range(attempts):
        task = Task(
            description=_safe("Translate this farm advice into simple Modern Standard Arabic for the farmer. Write Arabic "
                              "script only. Keep every number, unit, time and field name (F1, F2) exactly as written.\n\n" + source),
            expected_output="JSON with message_ar and todos_ar (same order as the to-dos).",
            agent=Agent(role="Translator for the farm messages", goal="Faithful, simple Arabic the farmer understands",
                        backstory="You translate farm advice from English into clear Modern Standard Arabic.",
                        llm=crew_llm(model, 0.1 if attempt == 0 else 0.0), allow_delegation=False, max_iter=2, verbose=False),
            output_pydantic=Translation, context=[])
        _kickoff([task])
        out = _parsed(task)
        if out and _clean_arabic(out["message_ar"] + "".join(out["todos_ar"])):
            return out
    return None
