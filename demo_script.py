"""Scripted Hydro Monitor demo: the onboarding conversation and the farm's "AI team", with no AI calls.

Everything here follows the demo setup prompt word for word (see SETUP_PROMPT): friendly short messages, ONE
question per message and at most 3 questions, only about what the farmer hasn't said yet (the space, power and
WiFi, plants or seeds and their age, the main worry), then a 3-sentence design and "Shall I show you the details?",
and on "yes" exactly four blocks (FARM PROFILE, HARDWARE PLAN, HARD LIMITS & STARTING RANGES, AGENT NETWORK) and
"Ready for the Hydro Monitor team to review."

The dashboard's department insights are written by rules from simulated readings, so they always match the
numbers on screen. Used by demo_app.py; the real, agentic system is app.py.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from typing import Any

SETUP_PROMPT = "Hydro Monitor onboarding assistant (scripted demo): at most 3 questions, then a design, then 4 blocks."

# ── the kits, parts and crop file from the setup prompt ─────────────────
KITS = {
    "starter": {
        "name": "Starter Hydroponic Kit",
        "short": "two PVC pipes stacked on a wall, 2 net pots per pipe",
        "how": "Water is pumped to a funnel at the top and flows down through both pipes back to a tank.",
        "for": "small or indoor spaces; uses far less water than soil",
        "sensors": "DHT22 (air temperature, humidity), ultrasonic water level (tank lid), water-level strip (pipe), light sensor",
        "actuators": "relay1 → pump, relay2 → fan",
        "pots": 4,
    },
    "soil": {
        "name": "Soil Bed Kit",
        "short": "raised beds with drip lines",
        "how": "A pump feeds drip lines along each bed from a tank.",
        "for": "outdoor gardens",
        "sensors": "DHT22 (air temperature, humidity), ultrasonic water level (tank lid), light sensor",
        "actuators": "relay1 → pump",
        "pots": 0,
    },
    "field": {
        "name": "Open Field Kit",
        "short": "drip-irrigated field rows",
        "how": "Drip lines along the rows, fed by a pump from a tank.",
        "for": "large farms",
        "sensors": "DHT22 (air temperature, humidity), ultrasonic water level (tank lid), light sensor",
        "actuators": "relay1 → pump",
        "pots": 0,
    },
}
CROP = {"name": "red amaranth", "temp_start": (24, 30), "temp_alert": 32, "rh_start": (40, 70),
        "tank_alert": 20, "tank_block": 15, "pump_start": 120, "pump_max": 300, "cycle_days": 35}

QUESTIONS = {
    "space": "Where will your plants live: indoors or outdoors, and roughly how big is the space?",
    "power": "Is there a power socket and WiFi close to that spot?",
    "plants": "Do you already have plants or seeds? If so, how many, and how old are they?",
    "worry": "What worries you most about growing them?",
}
PLACES = ["Al Wakrah", "Al Khor", "Al Rayyan", "Umm Salal", "Al Shahaniya", "Lusail", "The Pearl", "West Bay", "Dukhan",
          "Mesaieed", "Al Daayen", "Simaisma", "Al Thakhira", "Madinat ash Shamal", "Doha"]
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "a couple of": 2, "a few": 3}


def new_profile() -> dict[str, Any]:
    return {"location": None, "kind": None, "size_text": None, "size_m2": None, "power": None, "wifi": None,
            "plants": None, "count": None, "age_days": None, "worry": None, "asked": [], "stage": "asking"}


def _num(token: str) -> float | None:
    token = token.lower()
    return float(token) if re.fullmatch(r"\d+(?:\.\d+)?", token) else WORDS.get(token)


def extract(p: dict[str, Any], text: str) -> dict[str, Any]:
    """Pick up whatever the farmer mentioned, in any message: that's what keeps the questions to 3 or fewer."""
    t = text.lower()
    if not p["location"]:
        p["location"] = next((pl for pl in PLACES if pl.lower() in t or pl.lower().replace("al ", "") in t), None)
    # the space
    if not p["kind"]:
        if re.search(r"\b(indoor|inside|apartment|flat|living room|kitchen|room|office|villa wall|wall)\b", t):
            p["kind"] = "indoor"
        elif re.search(r"\b(outdoor|outside|garden|yard|backyard|roof|rooftop|balcony|terrace|farm|field|land|hectare)\b", t):
            p["kind"] = "outdoor"
    if not p["size_m2"]:
        dims = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?\s*(?:by|x|×)\s*(\d+(?:\.\d+)?)", t)
        area = re.search(r"(\d+(?:\.\d+)?)\s*(square met|sq ?m|m2|m²|hectares?|ha\b|dunams?)", t)
        width = re.search(r"(\d+(?:\.\d+)?|one|two|three|four|five)\s*(?:m\b|metres?|meters?)\s*(?:wide|long)?", t)
        if dims:
            p["size_m2"] = float(dims.group(1)) * float(dims.group(2))
            p["size_text"] = f"{dims.group(1)} × {dims.group(2)} m"
        elif area:
            n = float(area.group(1))
            p["size_m2"] = n * (10000 if area.group(2).startswith(("hect", "ha")) else 1000 if area.group(2).startswith("dun") else 1)
            p["size_text"] = f"about {p['size_m2']:,.0f} m²"
        elif width:
            n = _num(width.group(1)) or 2
            p["size_m2"] = n * 1.0
            p["size_text"] = f"about {width.group(1)} m of wall" if p["kind"] == "indoor" else f"about {n:g} m²"
        elif re.search(r"\b(small|tiny|little|corner|a wall|one wall)\b", t):
            p["size_m2"], p["size_text"] = 2.0, "a small space"
        elif re.search(r"\b(big|large|huge)\b", t):
            p["size_m2"], p["size_text"] = 500.0, "a large space"
    # power and WiFi
    if p["power"] is None:
        if re.search(r"\b(no (power|electricity|socket|plug))\b", t):
            p["power"] = "no socket nearby (the kit runs from a small solar panel and battery)"
        elif re.search(r"\b(socket|plug|power|electricity|outlet)\b", t) or re.search(r"\byes\b", t) and "power" in p["asked"][-1:]:
            p["power"] = "wall socket nearby"
    if p["wifi"] is None:
        if re.search(r"\bno (wifi|wi-fi|internet)\b", t):
            p["wifi"] = "no WiFi (the master node keeps working and syncs later)"
        elif re.search(r"\b(wifi|wi-fi|internet|router|mobile data|4g|5g)\b", t) or re.search(r"\byes\b", t) and "power" in p["asked"][-1:]:
            p["wifi"] = "home WiFi" if "mobile" not in t else "mobile data"
    # plants or seeds
    if p["plants"] is None:
        count = re.search(r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten|a few|a couple of)\s+(seedlings?|plants?|seeds?|pots?)", t)
        if count:
            p["count"] = int(_num(count.group(1)) or 1)
            p["plants"] = "seeds" if count.group(2) in ("seed", "seeds") else "seedlings"
        elif re.search(r"\b(seeds?)\b", t):
            p["plants"], p["count"] = "seeds", None
        elif re.search(r"\b(seedlings?|plants?)\b", t):
            p["plants"] = "seedlings"
        elif re.search(r"\b(nothing yet|no plants|none yet|not yet|nothing)\b", t) and "plants" in p["asked"][-1:]:
            p["plants"], p["count"], p["age_days"] = "none yet (Hydro Monitor supplies red amaranth seeds)", 4, 0
    if p["age_days"] is None and p["plants"]:
        ago = re.search(r"(\d+|a|an|one|two|three|four|five|six|a couple of|a few)\s+(day|week|month)s?\s+(old|ago)", t)
        if ago:
            p["age_days"] = int((_num(ago.group(1)) or 1) * {"day": 1, "week": 7, "month": 30}[ago.group(2)])
        elif p["plants"] == "seeds" or re.search(r"\b(just|new|today|not planted)\b", t):
            p["age_days"] = 0
    # the main worry
    if p["worry"] is None:
        m = re.search(r"(?:worr(?:y|ied)(?: that| about)?|afraid(?: that| of)?|scared(?: that| of)?|concern(?:ed)?(?: about)?|"
                      r"problem is|my fear is)\s+(.+)", text, re.I)          # original text: keep the farmer's own words
        if m:
            p["worry"] = m.group(1).strip(" .!")
        elif "worry" in p["asked"][-1:] and text.strip():
            p["worry"] = text.strip(" .!")
    return p


UNSURE = re.compile(r"^\s*(no|nope|not sure|don'?t know|dont know|idk|no idea|maybe|nothing|none|hello|hi)\b", re.I)
DEFAULTS = {"space": {"kind": "indoor", "size_m2": 2.0, "size_text": "a small wall (about 2 m²)"},
            "power": {"power": "wall socket nearby", "wifi": "home WiFi"},
            "plants": {"plants": "none yet (Hydro Monitor supplies red amaranth seeds)", "count": 4, "age_days": 0},
            "worry": {"worry": "not sure how much to water"}}


def settle(p: dict[str, Any], slot: str) -> None:
    """The farmer couldn't answer (or said no): use the sensible default for that slot and move on."""
    for key, value in DEFAULTS[slot].items():
        if not p.get(key):
            p[key] = value


def missing(p: dict[str, Any]) -> list[str]:
    out = []
    if not p["kind"] or not p["size_m2"]:
        out.append("space")
    if p["power"] is None or p["wifi"] is None:
        out.append("power")
    if p["plants"] is None or (p["plants"] in ("seedlings",) and p["age_days"] is None):
        out.append("plants")
    if p["worry"] is None:
        out.append("worry")
    return out


def kit_for(p: dict[str, Any]) -> str:
    size = p["size_m2"] or 2
    if size >= 1000:
        return "field"
    if p["kind"] == "indoor" or size <= 10:
        return "starter"
    return "soil"


def _ack(p: dict[str, Any], before: dict[str, Any]) -> str:
    """One short friendly line reacting to what the farmer just told us."""
    if p["location"] and not before.get("location"):
        return f"Lovely, {p['location']} is a great place to grow greens."
    if p["kind"] and not before.get("kind"):
        return "Perfect, an indoor spot keeps the plants out of the harsh sun." if p["kind"] == "indoor" else \
            "Great, outdoors works well from October to April."
    if p["power"] and not before.get("power"):
        return "Got it, the kit can run on a small solar panel." if p["power"].startswith("no") else "Good, that makes setup easy."
    if p["plants"] and not before.get("plants"):
        return "Nice, we can work with that."
    if p["worry"] and not before.get("worry"):
        return "That's a very common worry, and it's exactly what the system is for."
    return "Hi!" if not before.get("asked") else "Okay, no problem."


def fill_defaults(p: dict[str, Any]) -> None:
    """After 3 questions we stop asking: sensible defaults for anything still unknown."""
    p["location"] = p["location"] or "Doha"
    p["kind"] = p["kind"] or "indoor"
    if not p["size_m2"]:
        p["size_m2"], p["size_text"] = 2.0, "a small wall (about 2 m²)"
    p["power"] = p["power"] or "wall socket nearby"
    p["wifi"] = p["wifi"] or "home WiFi"
    if p["plants"] is None:
        p["plants"], p["count"] = "none yet (Hydro Monitor supplies red amaranth seeds)", 4
    if p["age_days"] is None:
        p["age_days"] = 0
    p["worry"] = p["worry"] or "not sure how much to water"


def proposal(p: dict[str, Any]) -> str:
    key = kit_for(p)
    kit = KITS[key]
    if key == "starter":
        s1 = f"I suggest our {kit['name']}: {kit['short']}, holding {p['count'] or 4} red amaranth plants."
    else:
        s1 = f"I suggest our {kit['name']}: {kit['short']} for red amaranth, sized to your space ({p['size_text'] or 'as measured on site'})."
    s2 = (f"{kit['how']} A small sensor set watches the air, light and water, and "
          + ("the pump and a fan switch on by themselves." if "fan" in kit["actuators"] else "the pump switches on by itself."))
    s3 = (f"The system handles the watering and sends you a plain message when the plants need you, so "
          f"{_worry_phrase(p['worry'] or 'watering')} won't be a problem.")
    return f"{s1} {s2} {s3}\n\nShall I show you the details?"


def planted_date(p: dict[str, Any], today: date | None = None) -> date:
    return (today or date.today()) - timedelta(days=int(p["age_days"] or 0))


def blocks(p: dict[str, Any], today: date | None = None) -> list[tuple[str, str]]:
    """The four blocks, exactly as the setup prompt asks."""
    kit_key = kit_for(p)
    kit = KITS[kit_key]
    kind = p["plants"] if p["plants"] in ("seeds", "seedlings") else "plants"
    count = p["count"] or (kit["pots"] if kind != "seeds" else None)
    crop_line = (f"red amaranth, {count} {kind}" if count else f"red amaranth {kind}") + f", planted {planted_date(p, today).isoformat()}"
    water = ("recirculating tank under the pipes, topped up from the tap; Hydro Monitor supplies the fertilizer"
             if kit_key == "starter" else "tank feeding the drip lines; Hydro Monitor supplies the fertilizer")
    profile = "\n".join([
        f"location: {p['location']}",
        f"space: {p['kind']}, {p['size_text']}",
        f"system: {kit['name']} ({kit['short']})",
        f"crop: {crop_line}",
        f"water: {water}",
        f"power: {p['power']}",
        f"internet: {p['wifi']}",
        f"problems: {p['worry']}",
    ])
    home = "indoors next to the power socket and WiFi router" if p["kind"] == "indoor" else "in a shaded, dry box near the power and WiFi"
    hardware = (f"F1 · Sensor S1: {kit['sensors']}\n"
                f"     Actuator A1: {kit['actuators']}\n"
                f"The master node (ESP32 + SD card) and the farm computer sit {home}.")
    c = CROP
    limits = "\n".join([
        f"Air temperature: hard limit alert above {c['temp_alert']} °C · start {c['temp_start'][0]}–{c['temp_start'][1]} °C",
        f"Humidity: hard limit none in the crop file · start {c['rh_start'][0]}–{c['rh_start'][1]} %",
        f"Tank level: hard limit block the pump below {c['tank_block']} % · start alert below {c['tank_alert']} %",
        f"Pump run: hard limit never more than {c['pump_max']} s · start {c['pump_start']} s per run",
    ])
    network = "\n".join([
        "Agri-Environment: Climate, Light ✓",
        "   (left out: Air Quality, the kit has no air-quality sensor)",
        "Soil & Water: Irrigation, Nutrients ✓",
        "   (left out: Salinity, no EC sensor, so fertilizer advice only)",
        "Crop Science: Physiology, Leaf, Health ✓",
        "Data & Analytics: Trend, Anomaly, Forecast ✓",
        "10 specialists + 1 Farm Director",
    ])
    return [("FARM PROFILE", profile), ("HARDWARE PLAN", hardware), ("HARD LIMITS & STARTING RANGES", limits),
            ("AGENT NETWORK", network)]


YES = re.compile(r"^\s*(yes|yeah|yep|sure|ok|okay|please|show|go ahead|do it|y)\b", re.I)
NO = re.compile(r"^\s*(no|not yet|wait|change)\b", re.I)


def step(p: dict[str, Any], text: str) -> dict[str, Any]:
    """One farmer message in, the assistant's reply out: {'reply', 'blocks'?, 'done'}."""
    if p["stage"] == "proposed":
        if YES.search(text):
            fill_defaults(p)
            p["stage"] = "done"
            return {"reply": "Here are the details:", "blocks": blocks(p), "done": True,
                    "closing": "Ready for the Hydro Monitor team to review."}
        if NO.search(text) or text.strip():
            before = dict(p)
            extract(p, text)
            if p != before:
                return {"reply": "Got it, I've updated the plan. " + proposal(p), "done": False}
            return {"reply": "No problem. Tell me what you'd like to change, or say yes to see the details.", "done": False}
    before = {**p, "asked": list(p["asked"])}
    extract(p, text)
    last = p["asked"][-1] if p["asked"] else None
    ack = None
    if last == "power" and last in missing(p) and re.match(r"^\s*(no|nope|none|nothing)\b", text, re.I):
        p["power"] = p["power"] or "no socket nearby (the kit runs from a small solar panel and battery)"
        p["wifi"] = p["wifi"] or "no WiFi (the master node keeps working and syncs later)"
        ack = "Got it, the kit can run on a small solar panel and work offline."
    elif last and last in missing(p) and UNSURE.search(text):
        settle(p, last)                      # "not sure": never ask the same thing twice
        ack = f"No problem, I'll assume {ASSUME[last]}."
    todo = [slot for slot in missing(p) if slot not in p["asked"]]
    ack = ack or _ack(p, before)
    if todo and len(p["asked"]) < 3:
        nxt = todo[0]
        p["asked"].append(nxt)
        return {"reply": f"{ack} {QUESTIONS[nxt]}", "done": False}
    fill_defaults(p)
    p["stage"] = "proposed"
    return {"reply": f"{ack} {proposal(p)}", "done": False}


ASSUME = {"space": "a small indoor wall", "power": "a socket and WiFi nearby", "plants": "we start you with our red amaranth seeds",
          "worry": "watering is the main thing to take care of"}


def _worry_phrase(worry: str) -> str:
    """'I'll forget to water them' -> 'forgetting to water them'; 'the heat' stays 'the heat'."""
    w = worry.strip().rstrip(".!").lower()
    for prefix in ("i'll ", "i will ", "i might ", "i may ", "that i'll ", "that i will ", "i ", "that "):
        if w.startswith(prefix):
            rest = w[len(prefix):]
            verb, _, tail = rest.partition(" ")
            irregular = {"forget": "forgetting", "get": "getting", "run": "running", "put": "putting", "let": "letting",
                         "be": "being", "die": "dying", "lose": "losing", "set": "setting"}
            ing = irregular.get(verb) or (verb[:-1] + "ing" if verb.endswith("e") and not verb.endswith("ee") else verb + "ing")
            return f"{ing} {tail}".strip()
    return w


DEMO_LINES = [   # a ready-made farmer for rehearsals: the design comes out after 3 questions
    "Hi! I live in Al Wakrah and I'd like to grow some greens at home.",
    "Indoors, on a wall in my living room, about 2 metres wide.",
    "Yes, there's a socket right there and good WiFi.",
    "I have 4 seedlings, about two weeks old. I'm worried I'll forget to water them.",
    "Yes please!",
]


# ── the farm dashboard: simulated readings and rule-written insights ─────
SCENARIOS = {"normal": "Normal day", "heat": "Heat wave", "tank": "Tank running low", "pump": "Pump running now"}


def readings(now: datetime, scenario: str = "normal") -> dict[str, float]:
    """A believable indoor day for the wall kit; the scenario pushes one thing out of range for the demo."""
    hour = now.hour + now.minute / 60
    wobble = math.sin(now.minute / 60 * 2 * math.pi) * 0.3
    temp = 26.2 + 2.2 * math.cos((hour - 15) / 24 * 2 * math.pi) + wobble
    rh = 56 - 6 * math.cos((hour - 15) / 24 * 2 * math.pi)
    light = max(900.0, 11000 * math.sin(max(0.0, (hour - 6)) / 12 * math.pi)) if 6 <= hour <= 18 else 60.0
    tank = 72 - (hour % 24) * 0.9
    strip = 1.0
    pump_on = False
    if scenario == "heat":
        temp, rh = 33.4 + wobble, 36.0
    elif scenario == "tank":
        tank = 13.5
        strip = 0.0
    elif scenario == "pump":
        pump_on, strip = True, 1.0
    return {"temp": round(temp, 1), "rh": round(rh), "light": round(light), "tank": round(tank, 1),
            "flowing": strip, "pump_on": pump_on}


def status_of(r: dict[str, float]) -> tuple[str, str, list[str]]:
    """Traffic light, headline and more lines for the wall garden card."""
    c = CROP
    lines = []
    level = "ok"
    if r["tank"] < c["tank_block"]:
        level = "act"
        headline = f"Tank almost empty ({r['tank']:.0f} %): the pump is paused. Please top it up."
    elif r["tank"] < c["tank_alert"]:
        level = "watch"
        headline = f"Tank getting low ({r['tank']:.0f} %): top it up today."
    elif r["temp"] > c["temp_alert"]:
        level = "act"
        headline = f"Too hot ({r['temp']:.0f} °C): the fan is on. Close the curtain on that wall."
    elif r["pump_on"]:
        headline = "Watering now: water is flowing through both pipes."
    else:
        headline = "All good: water, air and light are just right."
    if level == "ok" and not c["rh_start"][0] <= r["rh"] <= c["rh_start"][1]:
        level = "watch"
        lines.append(f"Air is {'dry' if r['rh'] < c['rh_start'][0] else 'humid'} ({r['rh']} %).")
    return level, headline, lines


def team(r: dict[str, float], p: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """What each department would report, written by rules from the same readings shown on the page."""
    c = CROP
    age = int(p.get("age_days") or 0)
    left = max(0, c["cycle_days"] - age)
    hot = r["temp"] > c["temp_alert"]
    low_tank = r["tank"] < c["tank_alert"]
    return [
        {"code": "ENV", "name": "Agri-Environment", "status": "CRITICAL" if hot else "OK",
         "text": (f"Air is {r['temp']} °C, above the {c['temp_alert']} °C alert for red amaranth: the fan is running. "
                  f"Humidity {r['rh']} % is low for this heat." if hot else
                  f"Air {r['temp']} °C and humidity {r['rh']} % are inside the red amaranth range "
                  f"({c['temp_start'][0]}–{c['temp_start'][1]} °C, {c['rh_start'][0]}–{c['rh_start'][1]} %). Light is {r['light']:,} lux."),
         "warn": "Heat above 32 °C fades the red leaf colour." if hot else None},
        {"code": "SOIL", "name": "Soil & Water", "status": "CRITICAL" if r["tank"] < c["tank_block"] else "WARNING" if low_tank else "OK",
         "text": (f"The tank is at {r['tank']:.0f} %, below the {c['tank_block']} % safety line, so the pump is blocked to protect it. "
                  "The water-level strip shows no flow in the pipes." if r["tank"] < c["tank_block"] else
                  f"The tank is at {r['tank']:.0f} %; the pump runs {c['pump_start']} s at a time and water reached both pipes. "
                  "Your next fertilizer pack from Hydro Monitor is due in 9 days."),
         "warn": "Top up the tank today." if low_tank else None},
        {"code": "CROP", "name": "Crop Science", "status": "WARNING" if hot else "OK",
         "text": (f"Your {p.get('count') or 4} red amaranth plants are {age} days old "
                  + (f"and should be ready to harvest in about {left} days. " if left else "and ready to harvest. ")
                  + ("The heat may make the leaves greener and less red." if hot else "Leaves should be a deep red at this light.")),
         "warn": None},
        {"code": "DATA", "name": "Data & Analytics", "status": "WARNING" if low_tank else "OK",
         "text": ("The tank dropped faster than usual today: check that the pipe end isn't leaking." if low_tank else
                  f"The tank drops about 0.9 % per hour, so it will need a top-up in about {max(1, int((r['tank'] - c['tank_alert']) / 0.9))} hours. "
                  "No unusual readings."),
         "warn": None},
    ]


def overview(r: dict[str, float], p: dict[str, Any]) -> tuple[str, list[str]]:
    """The Farm Director's combined view: one message and the to-dos."""
    c = CROP
    todos = []
    if r["tank"] < c["tank_alert"]:
        todos.append("Top up the water tank (about 5 litres).")
    if r["temp"] > c["temp_alert"]:
        todos.append("Close the curtain or blind on that wall until the evening.")
    todos.append("Check the net pots once: the roots should be white, not brown.")
    age = int(p.get("age_days") or 0)
    if age >= 28:
        todos.append("Harvest the biggest outer leaves first; the plant keeps growing.")
    if r["tank"] < c["tank_block"]:
        msg = "Your wall garden needs water: the tank is almost empty, so the pump has paused to protect itself. Top it up and it restarts on its own."
    elif r["temp"] > c["temp_alert"]:
        msg = "It's hot on your wall today. The fan is on; closing the curtain for a few hours will keep the leaves red."
    else:
        msg = "Your plants are doing well. The system is watering them on its own; nothing urgent today."
    return msg, todos


def kit_cost(kit_key: str) -> list[tuple[str, int]]:
    """Rough demo prices (QR) for the kit's parts."""
    if kit_key == "starter":
        return [("PVC pipes, funnel and 4 net pots", 180), ("Pump and tubing", 120), ("Water tank (20 L)", 60),
                ("Fan", 80), ("Sensor set (Nano, DHT22, ultrasonic, strip, light)", 250), ("Actuator set (Nano, relays)", 200),
                ("Master node (ESP32, SD card)", 300), ("Installation", 250)]
    return [("Beds or rows, drip lines", 1200), ("Pump and tubing", 800), ("Water tank", 1500),
            ("Sensor set", 250), ("Actuator set", 200), ("Master node", 300), ("Installation", 800)]
