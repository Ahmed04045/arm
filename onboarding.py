"""Onboarding assistant (Hydro Monitor plan 3.2, 4 steps 1-6).

The farmer describes the farm in their own words; the assistant (the most capable model we can reach) asks
follow-up questions until it understands every field, and does the structuring. Its tools: Open-Meteo for
the location, the forecast and past weather; the crop file (knowledge.py); and the department template below.
It produces the four things a person on the team reviews before anything is installed or run:

    1. farm profile   2. hardware plan   3. hard limits and starting ranges   4. the agent network (CrewAI definitions)
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import llm
import weather
from agents.base import resolve_model
from knowledge import CANDIDATE_CROPS, CROP_FILE

REQUIRED = ["location", "fields", "water", "fertilizer", "power", "internet", "problems"]
FIELD_KEYS = ["size_m", "type", "crop", "planted"]
QUESTIONS = {   # what each missing slot means, for the model's follow-up question
    "location": "where the farm is",
    "fields": "how many fields (beds) there are, their size, soil or hydroponic, open air or covered",
    "crop": "what grows in each field",
    "planted": "when each field was planted",
    "water": "the water source and how water reaches the plants",
    "fertilizer": "which fertilizer is used and how often",
    "power": "where there is electricity",
    "internet": "whether there is internet (WiFi or mobile data)",
    "problems": "what goes wrong most often",
}

SYSTEM = (
    "You are the Hydro Monitor onboarding assistant. A farmer describes their farm in their own words; you do the "
    "structuring. There is no form. Be warm and brief: acknowledge what they said in a few words, then ask ONE short "
    "follow-up question about the first thing still missing. Never ask about something already known. Use the tool "
    "results you are given (location, weather) naturally, e.g. mention the heat. Dates: convert 'three weeks ago' "
    "into YYYY-MM-DD using today's date. Field types look like 'soil bed, open air', 'soil bed, greenhouse' or "
    "'hydroponic, greenhouse'. Crop names in lower case (e.g. 'purple amaranth'); use 'undecided' if the farmer "
    "has not chosen a crop.\n"
    "Answer with JSON only: {\"reply\": \"what you say to the farmer\", \"profile\": {the complete profile so far}}. "
    "Profile keys: location (text), fields (list of {size_m: [width, length] in metres, type, crop, planted}), "
    "water, fertilizer, power, internet (texts), problems (list of short texts)."
)


def empty_profile() -> dict[str, Any]:
    return {"location": "", "fields": [], "water": "", "fertilizer": "", "power": "", "internet": "", "problems": []}


def missing(profile: dict[str, Any]) -> list[str]:
    """Slots still to ask about, in the order the plan's conversation asks them."""
    out = []
    if not profile.get("location"):
        out.append("location")
    fields = profile.get("fields") or []
    if not fields or any(not f.get("size_m") or not f.get("type") for f in fields):
        out.append("fields")
    if fields and any(not f.get("crop") for f in fields):
        out.append("crop")
    if fields and any(not f.get("planted") and f.get("crop") != "undecided" for f in fields):
        out.append("planted")
    out += [k for k in ("water", "fertilizer", "power", "internet", "problems") if not profile.get(k)]
    return out


def _merge(old: dict[str, Any], new: dict[str, Any] | None) -> dict[str, Any]:
    """Keep what we knew; take the model's non-empty values. Normalise the field list."""
    out = dict(old)
    for key, value in (new or {}).items():
        if key in REQUIRED and value not in ("", [], None):
            out[key] = value
    before, after = old.get("fields") or [], out.get("fields") or []
    if isinstance(after, list) and len(after) < len(before):   # the model dropped a bed: keep the ones it left out
        out["fields"] = [{**b, **{k: v for k, v in (after[i] if i < len(after) else {}).items() if v}}
                         for i, b in enumerate(before)]
    fields = []
    for i, f in enumerate(out.get("fields") or []):
        if not isinstance(f, dict):
            continue
        size = f.get("size_m")
        if isinstance(size, str):
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", size)][:2]
            size = nums if len(nums) == 2 else None
        planted = str(f.get("planted") or "")
        fields.append({"field_id": f"F{i + 1}", "size_m": size, "type": (f.get("type") or "").strip(),
                       "crop": (f.get("crop") or "").strip().lower(),
                       "planted": planted if re.fullmatch(r"\d{4}-\d{2}-\d{2}", planted) else ""})
    out["fields"] = fields
    if isinstance(out.get("problems"), str):
        out["problems"] = [p.strip() for p in re.split(r";|\n", out["problems"]) if p.strip()]
    for key in ("latitude", "longitude", "utc_offset", "place"):
        if key in old:
            out[key] = old[key]
    return out


NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "a couple of": 2, "a few": 3}


def _relative_date(text: str) -> str | None:
    """'about three weeks ago' -> YYYY-MM-DD (small models often leave this to us)."""
    m = re.search(r"(\d+|a couple of|a few|an|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|week|month)s?\s+ago",
                  text.lower())
    if not m:
        return None
    n = int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)]
    days = n * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    return date.fromordinal(date.today().toordinal() - days).isoformat()


def rule_fill(profile: dict[str, Any], farmer_text: str) -> dict[str, Any]:
    """Code-side structuring the model may miss: relative planting dates and open-air / covered beds."""
    text = farmer_text.lower()
    planted = _relative_date(text) if any(w in text for w in ("plant", "sow", "ago")) else None
    covered = any(w in text for w in ("greenhouse", "covered", "tunnel", "net house"))
    open_air = any(w in text for w in ("open air", "open-air", "outdoor", "outside", "in the open"))
    count = re.search(r"\b(\d+|two|three|four|five|six)\s+(?:\w+\s+)?(?:beds|fields|plots|greenhouses)\b", text)
    fields = profile.get("fields") or []
    if count and fields:   # "two soil beds": make sure there are two, like the first one
        n = int(count.group(1)) if count.group(1).isdigit() else NUMBERS[count.group(1)]
        while len(fields) < n:
            fields.append({**fields[0], "field_id": f"F{len(fields) + 1}"})
        profile["fields"] = fields
    for f in profile.get("fields", []):
        if planted:   # code's date arithmetic wins over the model's
            f["planted"] = planted
        kind = f.get("type") or ""
        if open_air and "open" not in kind and "greenhouse" not in kind:
            f["type"] = (kind or "soil bed") + ", open air"
        elif covered and "greenhouse" not in kind and "open" not in kind:
            f["type"] = (kind or "soil bed") + ", greenhouse"
    return profile


def _place_candidates(location: str) -> list[str]:
    """'near Al Khor, north of Doha' -> ['Al Khor', 'Doha', ...] for the geocoder."""
    out = []
    for part in re.split(r",|\bnear\b|\bnorth of\b|\bsouth of\b|\beast of\b|\bwest of\b|\bin\b|\bclose to\b", location, flags=re.I):
        part = part.strip(" .")
        if part and part.lower() not in ("qatar", "the") and part not in out:
            out.append(part)
    return out or [location]


def lookup(profile: dict[str, Any]) -> str | None:
    """Tool: Open-Meteo geocoding, the coming week and the past month. Adds coordinates to the profile."""
    place = next((p for p in (weather.geocode(c) for c in _place_candidates(profile["location"])) if p), None)
    if not place:
        return None
    profile.update(latitude=round(place["latitude"], 3), longitude=round(place["longitude"], 3),
                   place=f"{place['name']}, {place.get('country') or ''}".strip(", "),
                   utc_offset="+03:00" if place.get("timezone") in ("Asia/Qatar", "Asia/Riyadh", "Asia/Bahrain", "Asia/Kuwait") else "+03:00")
    bits = [f"{profile['place']} at {place['latitude']:.2f} N, {place['longitude']:.2f} E"]
    bits += [b for b in (weather.week_outlook(place["latitude"], place["longitude"]),
                         weather.past_month(place["latitude"], place["longitude"])) if b]
    return "; ".join(bits)


def onboarding_model(route: dict[str, Any]) -> str:
    """The most capable model we can reach: the route's 'onboarding' model if set, else the Director's model."""
    state = {"installed_models": llm.available_models(), "fallback_model": route["fallback"], "model_map": route["map"]}
    return resolve_model(route.get("onboarding") or "qwen2.5:7b", state)


def step(history: list[dict[str, str]], profile: dict[str, Any], model: str) -> dict[str, Any]:
    """One turn. history: [{role: farmer|assistant|tool, text}]. Returns {reply, profile, tool?, done}."""
    tool_text = None
    farmer_said = next((h["text"] for h in reversed(history) if h["role"] == "farmer"), "")
    profile = rule_fill(profile, farmer_said)   # so "still missing" below is already right for this turn
    if profile.get("location") and "latitude" not in profile:
        tool_text = lookup(profile)
    todo = missing(profile)
    transcript = "\n".join(f"{h['role'].upper()}: {h['text']}" for h in history[-12:])
    prompt = (f"Today is {date.today().isoformat()}.\nProfile so far: {json.dumps(profile, ensure_ascii=False)}\n"
              f"Still missing (ask about the first one): {', '.join(QUESTIONS[m] for m in todo) or 'nothing'}\n"
              + (f"Tool results: {tool_text}\n" if tool_text else "")
              + f"Conversation:\n{transcript}\n\nUpdate the profile from the farmer's last message and write your reply.")
    answer = llm.chat_json(model, SYSTEM, prompt, temperature=0.3) or {}
    updated = _merge(profile, answer.get("profile"))
    updated = rule_fill(updated, farmer_said)
    if updated.get("location") and "latitude" not in updated:
        tool_text = tool_text or lookup(updated)
    todo = missing(updated)
    reply = str(answer.get("reply") or "").strip()
    if not todo:
        reply = "Thanks, that's everything I need."   # the UI follows with the proposal (proposal_text)
    elif not reply:
        reply = f"Thanks. Could you tell me {QUESTIONS[todo[0]]}?"
    reply = reply[:1].upper() + reply[1:]
    return {"reply": reply, "profile": updated, "tool": tool_text, "done": not todo}


# ── design: the four outputs ─────────────────────────────────────────────
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def beds_text(n: int) -> str:
    return "bed" if n == 1 else f"{WORDS.get(n, n)} beds"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.split(",")[0].lower()) or "farm"


def _covered(field: dict[str, Any]) -> bool:
    return any(w in (field.get("type") or "").lower() for w in ("greenhouse", "covered", "tunnel", "net house", "indoor"))


def hardware_plan(profile: dict[str, Any]) -> dict[str, Any]:
    """Which sensor set and actuator set goes in each field (plan 6.2). The tank is measured once, on F1."""
    tank = "tank" in (profile.get("water") or "").lower()
    fields = []
    for i, f in enumerate(profile["fields"]):
        devices = [{"device_id": "dht11", "kinds": ["temp_air", "humidity"]},
                   {"device_id": "soil", "kinds": ["soil_moisture"]},
                   {"device_id": "light", "kinds": ["light"]}]
        if tank and i == 0:
            devices.append({"device_id": "level", "kinds": ["level"]})
        if _covered(f):
            devices.append({"device_id": "mq135", "kinds": ["air_quality"]})
        actuators = [{"device_id": "relay1", "kind": "pump", "what": "drip-line pump"}]
        if _covered(f):
            actuators.append({"device_id": "relay2", "kind": "fan", "what": "fan"})
        fields.append({"field_id": f["field_id"], "sensor_set": {"module_id": f"S{i + 1}", "devices": devices},
                       "actuator_set": {"module_id": f"A{i + 1}", "devices": actuators}})
    where = "shed" if "shed" in (profile.get("power") or "").lower() else "next to the power supply"
    notes = f"The master node and the farm computer go in the {where}."
    if tank and len(fields) > 1:
        notes += " Only S1 measures the tank, since the beds share it."
    return {"farm_id": None, "master": {"module_id": "M1", "hardware": "ESP32 + nRF24L01 + SD card", "location": where},
            "fields": fields, "notes": notes}


def limits_for(profile: dict[str, Any], hardware: dict[str, Any]) -> dict[str, Any]:
    """Hard limits and starting ranges from the crop file; crops without an entry use their temperature profile."""
    crop = next((f["crop"] for f in profile["fields"] if f.get("crop") and f["crop"] != "undecided"), "undecided")
    key = crop.replace(" ", "_")
    if key in CROP_FILE:
        entry = CROP_FILE[key]
        hard, start, notes = entry["hard"], entry["start"], entry["notes"]
    else:
        air = CANDIDATE_CROPS.get(key, {}).get("air", (18, 30, 10, 35))
        rh = CANDIDATE_CROPS.get(key, {}).get("rh", (50, 75, 35, 85))
        hard = {"soil_moisture": [25, 55], "temp_air": [air[2], air[3]], "humidity": [rh[2], rh[3]],
                "level": [15, 100], "pump_seconds": [10, 300]}
        start = {"soil_moisture": [30, 45], "temp_air": [air[0], air[1]], "humidity": [rh[0], rh[1]],
                 "level": [20, 100], "pump_seconds": 120}
        notes = {"crop": f"no crop-file entry for '{crop}': general values, to be checked by the team"}
    per_field = {}
    for f in hardware["fields"]:
        kinds = {k for d in f["sensor_set"]["devices"] for k in d["kinds"]}
        per_field[f["field_id"]] = {k: v for k, v in start.items() if k in kinds or k == "pump_seconds"}
    return {"farm_id": None, "crop": crop, "hard": hard, "start": per_field, "notes": notes}


TEMPLATE = [   # the department template (plan 3.3); needs = sensor kinds a role can't work without
    {"id": "agri_environment", "code": "ENV", "name": "Agri-Environment", "llm": "ollama/llama3.2:3b",
     "sets": ["temp_air", "humidity"], "depends_on": [], "tools": ["field_summary", "forecast"],
     "goal": "Keep the beds out of heat and dry-air stress, and warn early when the forecast turns hot",
     "specialists": [
         {"id": "climate", "name": "Climate", "skill": "range_monitor", "inputs": ["temp_air", "humidity"], "needs": ["temp_air"]},
         {"id": "light", "name": "Light & Photosynthesis", "skill": "range_monitor", "inputs": ["light"], "needs": ["light"]},
         {"id": "air_quality", "name": "Air quality", "skill": "range_monitor", "inputs": ["air_quality"], "needs": ["air_quality"],
          "why_not": "open-air beds, no sensor"}]},
    {"id": "soil_water", "code": "SOIL", "name": "Soil & Water", "llm": "ollama/gemma2:2b",
     "sets": ["soil_moisture", "level", "pump_seconds"], "depends_on": [], "tools": ["field_summary", "forecast", "pump_log"],
     "goal": "Keep soil moisture in range with as little water as possible, and never let the pump run the tank dry",
     "specialists": [
         {"id": "irrigation", "name": "Irrigation", "skill": "irrigation", "inputs": ["soil_moisture", "level"], "needs": ["soil_moisture"]},
         {"id": "nutrients", "name": "Nutrients & Fertilizer", "skill": "fertilizer", "inputs": [], "needs": []},
         {"id": "salinity", "name": "Salinity", "skill": "range_monitor", "inputs": ["soil_ec"], "needs": ["soil_ec"],
          "why_not": "no EC sensor, so fertilizer advice only"}]},
    {"id": "crop_science", "code": "CROP", "name": "Crop Science", "llm": "ollama/phi3.5",
     "sets": [], "depends_on": ["agri_environment", "soil_water"], "tools": ["department_reports", "growth_stage", "farmer_notes"],
     "goal": "Judge how the plants are coping at their growth stage, and warn about disease, pests and leaf quality",
     "specialists": [
         {"id": "physiology", "name": "Crop Physiology", "skill": "crop_physiology", "inputs": ["temp_air"], "needs": ["temp_air"]},
         {"id": "leaf_quality", "name": "Leaf Quality & Pigment", "skill": "leaf_quality", "inputs": ["temp_air", "light"], "needs": ["temp_air"]},
         {"id": "plant_health", "name": "Plant Health", "skill": "plant_health", "inputs": ["temp_air", "humidity", "light", "soil_moisture"], "needs": ["temp_air"]}]},
    {"id": "data_analytics", "code": "DATA", "name": "Data & Analytics", "llm": "ollama/qwen2.5:3b",
     "sets": [], "depends_on": [], "tools": ["trend", "anomaly", "forecast_6h", "field_compare"],
     "goal": "Say what changed, what is unusual and what is coming, with numbers",
     "specialists": [
         {"id": "trend", "name": "Trend", "skill": "trend", "inputs": ["*"], "needs": []},
         {"id": "anomaly", "name": "Anomaly", "skill": "anomaly", "inputs": ["*"], "needs": []},
         {"id": "forecast", "name": "Forecast", "skill": "forecast", "inputs": ["*"], "needs": []}]},
]


def _focus_lines(profile: dict[str, Any], model: str | None) -> dict[str, str]:
    """One sentence per department on what to watch most on this farm, written by the assistant (optional)."""
    if not model:
        return {}
    prompt = (f"Farm profile: {json.dumps(profile, ensure_ascii=False)}\nFor each department id "
              f"({', '.join(d['id'] for d in TEMPLATE)}), write one sentence telling that department what to watch most "
              "on this farm, based on its crop, setup and problems. JSON only: {\"agri_environment\": \"...\", ...}")
    out = llm.chat_json(model, "You design farm agent networks. Be specific and brief.", prompt, temperature=0.3) or {}
    return {k: str(v) for k, v in out.items() if isinstance(v, str)}


def network_for(profile: dict[str, Any], hardware: dict[str, Any], limits: dict[str, Any], model: str | None) -> dict[str, Any]:
    """Fill the department template: keep what the sensors can feed, drop the rest with the reason, write each
    role around the farm's crop and problems (plan 3.2)."""
    kinds = {k for f in hardware["fields"] for d in f["sensor_set"]["devices"] for k in d["kinds"]}
    crop = limits["crop"]
    n = len(profile["fields"])
    beds = " and ".join(f["field_id"] for f in profile["fields"])
    types = ", ".join(sorted({f["type"] for f in profile["fields"] if f.get("type")}))
    place = profile.get("place") or profile.get("location")
    problems = "; ".join(profile.get("problems") or []) or "none reported"
    hard = limits["hard"]
    focus = _focus_lines(profile, model)
    departments = []
    for t in TEMPLATE:
        keep = [s for s in t["specialists"] if all(k in kinds for k in s["needs"])]
        left_out = [{"role": s["name"], "why": s.get("why_not", "no sensor for it")} for s in t["specialists"] if s not in keep]
        sets = [s for s in t["sets"] if s in kinds or s == "pump_seconds"]
        backstory = (f"You look after {n} {types} {'bed' if n == 1 else 'beds'} ({beds}) of {crop} near {place}. "
                     f"The farmer's problems: {problems}. Hard limits: "
                     + ", ".join(f"{s} {hard[s][0]:g}-{hard[s][1]:g}" for s in (sets or ["temp_air"]) if s in hard)
                     + ". You get a 24-hour summary and the forecast. " + (focus.get(t["id"], "") + " " if focus.get(t["id"]) else "")
                     + "Reply only in the department JSON format.")
        departments.append({
            "id": t["id"], "code": t["code"], "name": t["name"], "depends_on": t["depends_on"], "sets": sets,
            "agent": {"role": f"{t['name']} agent for {beds} ({crop}, {types}, {place})", "goal": t["goal"],
                      "backstory": backstory, "llm": t["llm"], "tools": t["tools"]},
            "specialists": [{k: v for k, v in s.items() if k not in ("needs", "why_not")} for s in keep],
            "left_out": left_out})
    specialists = sum(len(d["specialists"]) for d in departments)
    undecided = crop == "undecided"
    extra_name = "Crop Suggestion" if undecided else "Market & Strategy"
    extras = [{
        "id": "market_strategy", "code": "MKT", "name": extra_name, "advice_only": True, "depends_on": [], "sets": [],
        "agent": {"role": f"{extra_name} adviser for a small farm near {place}",
                  "goal": "Propose crops and a rotation plan" if undecided else
                          "Point out the most useful crop or price opportunity, with its numbers and how reliable they are",
                  "backstory": "You think like a farm business adviser in Qatar. You always say how reliable a price is and never "
                               "call gross revenue profit. Your advice never changes ranges. Reply only in the department JSON format.",
                  "llm": "ollama/qwen2.5:3b", "tools": ["qatar_open_data", "price_table", "web_search"]},
        "specialists": [{"id": "market", "name": "Market Price", "skill": "market_watch", "inputs": []},
                        {"id": "crop_fit", "name": "Crop Suitability", "skill": "crop_fit", "inputs": []},
                        {"id": "profit", "name": "Profitability", "skill": "profitability", "inputs": []}],
        "left_out": []}]
    return {
        "farm_id": None, "designed_by": "onboarding assistant, awaiting team review", "designed_on": date.today().isoformat(),
        "process": "sequential",
        "summary": f"{specialists} specialists and a Farm Director will look after your {beds_text(n)}.",
        "departments": departments, "extras": extras,
        "director": {"id": "farm_director", "code": "DIR", "name": "Farm Director", "languages": ["en", "ar"],
                     "agent": {"role": f"Farm Director for the {crop} beds near {place}",
                               "goal": "Turn the department reports into one plan: ranges for each field, a short message for the farmer, and to-dos that need a person",
                               "backstory": "You are the one larger model over all departments. You read short reports, not the raw data. "
                                            "You set ranges only inside the hard limits, change them only when the reports give a reason, and keep "
                                            "the farmer's message short, plain and kind. The farmer reads it in Arabic and English.",
                               "llm": "ollama/qwen2.5:7b", "tools": ["department_reports", "ranges_in_force", "hard_limits"]}}}


def design(profile: dict[str, Any], farm_id: str, model: str | None = None) -> dict[str, Any]:
    """The four outputs, ready for review."""
    full = {"farm_id": farm_id, "name": f"{(profile.get('place') or profile['location']).split(',')[0]} farm",
            "location": profile.get("place") or profile["location"], "latitude": profile.get("latitude"),
            "longitude": profile.get("longitude"), "utc_offset": profile.get("utc_offset", "+03:00"),
            "crop": next((f["crop"] for f in profile["fields"] if f.get("crop")), "undecided"),
            **{k: profile[k] for k in ("fields", "water", "fertilizer", "power", "internet", "problems")}}
    hardware = hardware_plan(profile)
    limits = limits_for(profile, hardware)
    network = network_for(profile, hardware, limits, model)
    for part in (hardware, limits, network):
        part["farm_id"] = farm_id
    return {"profile": full, "hardware": hardware, "limits": limits, "network": network}


def proposal_text(parts: dict[str, Any]) -> str:
    """The assistant's closing suggestion, in the plan's words (6.1)."""
    hw, net = parts["hardware"], parts["network"]
    n = len(hw["fields"])
    pumps = "a small pump on a drip line" + (" and a fan" if any(len(f["actuator_set"]["devices"]) > 1 for f in hw["fields"]) else "")
    left = [f"you don't need {'an' if lo['role'][0].lower() in 'aeiou' else 'a'} {lo['role'].lower()} sensor ({lo['why']})"
            for d in net["departments"] for lo in d["left_out"] if lo["why"].startswith("open-air")]
    text = (f"Here's what I suggest. {'Each bed' if n > 1 else 'The bed'} gets a sensor set and {pumps}, and the master goes in the "
            f"{hw['master']['location']}. ")
    if left:
        text += f"The beds are in the open air, so {left[0].split(' (')[0]}. "
    count = sum(len(d["specialists"]) for d in net["departments"])
    return text + (f"I'll also set up {count} AI specialists and a Farm Director for your {beds_text(n)}. "
                   "Shall I show you the details?")
