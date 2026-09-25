"""Domain knowledge shared by every agent.

SENSORS maps a sensor capability (what an edge device reports) to the reading fields it produces.
CROPS holds the crop profile: optimal ranges, agronomic impacts, and pest/disease risk rules.
ACTIONS are the corrective actions the Farm Director can send to the master device.
"""

from __future__ import annotations

SENSORS: dict[str, dict] = {
    "air_temperature":   {"fields": ["air_temperature"]},
    "humidity":          {"fields": ["humidity"]},
    "light":             {"fields": ["light"]},
    "air_composition":   {"fields": ["co2"]},
    "water_temperature": {"fields": ["water_temperature"]},
    "soil_ph":           {"fields": ["soil_ph"]},
    "soil_composition":  {"fields": ["nitrogen", "phosphorus", "potassium", "soil_ec"]},
    # EZFarm Nano node sensors (converted from raw ADC by the bridge, see farms/ezfarm_site.json)
    "soil_moisture":     {"fields": ["soil_moisture"]},
    "water_level":       {"fields": ["water_level"]},
    "air_quality":       {"fields": ["air_quality"]},
}

UNITS = {
    "air_temperature": "°C", "humidity": "%", "light": "lux", "co2": "ppm", "water_temperature": "°C",
    "soil_ph": "", "nitrogen": "mg/kg", "phosphorus": "mg/kg", "potassium": "mg/kg", "soil_ec": "dS/m",
    "soil_moisture": "%", "water_level": "%", "air_quality": "",
}

LABELS = {
    "air_temperature": "Air temperature", "humidity": "Humidity", "light": "Light", "co2": "CO₂",
    "water_temperature": "Irrigation water temperature", "soil_ph": "Soil pH", "nitrogen": "Soil nitrogen",
    "phosphorus": "Soil phosphorus", "potassium": "Soil potassium", "soil_ec": "Soil salinity (EC)",
    "soil_moisture": "Soil moisture", "water_level": "Tank water level", "air_quality": "Air quality (MQ-135 index)",
}

# Diurnal by design: excluded from trend / anomaly / forecast analysis.
DIURNAL = {"light"}


def _row(r: dict, field: str, default: float = 0.0) -> float:
    value = r.get(field)
    return float(value) if isinstance(value, (int, float)) else default


CROPS: dict[str, dict] = {
    "purple_amaranth": {
        "display": "Red/purple amaranth (Amaranthus cruentus)",
        "source": "Team research on A. cruentus (Red Amaranth Growing Research, 24 Sep 2026); "
                  "N-P-K, soil moisture, tank and air-quality bands are general guides",
        # Ideal band. Outside it -> WARNING.
        "ranges": {
            "air_temperature": (22, 30), "humidity": (58, 72), "light": (30000, 100000), "co2": (400, 700),
            "water_temperature": (18, 24), "soil_ph": (5.5, 7.0),
            "nitrogen": (40, 100), "phosphorus": (15, 50), "potassium": (120, 300), "soil_ec": (0.4, 4.0),
            "soil_moisture": (50, 80), "water_level": (30, 100), "air_quality": (0, 500),
        },
        # Tolerated band. Outside it -> CRITICAL. Fields without one use a distance rule.
        "acceptable": {
            "air_temperature": (10, 40), "humidity": (37, 78), "water_temperature": (15, 26),
            "soil_ph": (4.3, 7.5), "soil_ec": (0, 10), "soil_moisture": (30, 90), "water_level": (14, 100),
            "air_quality": (0, 700),
        },
        "impacts": {
            "air_temperature": {"high": "heat stress; above ~40 °C leaf temperature photosynthesis is permanently damaged",
                                "low": "cold nights stunt amaranth growth"},
            "humidity": {"high": "Choanephora wet rot takes hold above ~78 %",
                         "low": "high transpiration, wilting and spider-mite pressure"},
            "light": {"high": "leaf scorch when combined with heat"},
            "co2": {"low": "slower shoot growth (it grew better at 700 than 400 ppm)"},
            "water_temperature": {"high": "warm root water holds less oxygen: root stress and Pythium risk",
                                  "low": "slowed root uptake"},
            "soil_ph": {"high": "iron and manganese lock-out: interveinal chlorosis and dull leaf colour",
                        "low": "manganese / aluminium toxicity"},
            "nitrogen": {"low": "small, pale leaves and weaker red colour",
                         "high": "leaf nitrate accumulation (food-safety limit)"},
            "phosphorus": {"low": "stunted roots and slow establishment"},
            "potassium": {"low": "leaf-margin necrosis and poor water regulation"},
            "soil_ec": {"high": "salt stress: growth falls and betalain (red pigment) is lost as sodium builds up"},
            "soil_moisture": {"low": "wilting and slower leaf growth", "high": "waterlogged roots stunt growth"},
            "water_level": {"low": "irrigation pump may run dry"},
            "air_quality": {"high": "poor ventilation or fumes (heater exhaust can signal crop-damaging ethylene)"},
        },
        "pest_disease": [
            {"id": "choanephora", "name": "Choanephora wet rot",
             "when": lambda r: _row(r, "air_temperature") >= 25 and _row(r, "humidity") >= 78,
             "action": "Increase ventilation, stop overhead irrigation, remove infected shoot tips", "cmd": "VENTILATE"},
            {"id": "damping_off", "name": "Pythium damping-off",
             "when": lambda r: _row(r, "water_temperature") >= 28 or _row(r, "soil_moisture") >= 92,
             "action": "Cool the irrigation water and let beds drain; drench seedling beds with Trichoderma", "cmd": "COOL_WATER"},
            {"id": "spider_mites", "name": "Two-spotted spider mite outbreak",
             "when": lambda r: _row(r, "air_temperature") >= 32 and _row(r, "humidity", 100) <= 40,
             "action": "Raise humidity with misting; scout leaf undersides; release Phytoseiulus predatory mites", "cmd": "MIST"},
            {"id": "leaf_scorch", "name": "Leaf scorch",
             "when": lambda r: _row(r, "air_temperature") >= 36 and _row(r, "light") >= 60000,
             "action": "Deploy 30–40% shade net over the beds during 11:00–15:00", "cmd": "SHADE"},
        ],
    },
}

# (field, direction) -> (action text for people, command code for the master device)
ACTIONS = {
    ("air_temperature", "high"): ("Restore fan-and-pad cooling; deploy shade net; move irrigation to early morning", "COOL"),
    ("air_temperature", "low"): ("Close vents and enable heating", "HEAT"),
    ("humidity", "low"): ("Run fogging/misting cycles during the hottest hours", "MIST"),
    ("humidity", "high"): ("Increase ventilation to cut wet-rot pressure", "VENTILATE"),
    ("light", "high"): ("Deploy 30–40% shade screens during the midday peak", "SHADE"),
    ("co2", "high"): ("Increase ventilation; check the CO₂ dosing controller", "VENTILATE"),
    ("co2", "low"): ("Enable CO₂ enrichment during daylight hours", "CO2_ON"),
    ("air_quality", "high"): ("Ventilate now and check for heater exhaust or ammonia", "VENTILATE"),
    ("water_temperature", "high"): ("Shade/insulate the water tank; add a frozen bottle or switch to the chilled line", "COOL_WATER"),
    ("water_temperature", "low"): ("Warm the irrigation water", "WARM_WATER"),
    ("water_level", "low"): ("Refill the water tank", "REFILL_TANK"),
    ("soil_moisture", "low"): ("Irrigate the beds now", "IRRIGATE"),
    ("soil_moisture", "high"): ("Pause irrigation and check drainage", "HOLD_IRRIGATION"),
    ("soil_ph", "high"): ("Acidify irrigation water; apply elemental sulphur to the beds", "ACIDIFY"),
    ("soil_ph", "low"): ("Apply agricultural lime", "LIME"),
    ("nitrogen", "low"): ("Fertigate with nitrogen (split doses, not before harvest)", "FERTILIZE"),
    ("nitrogen", "high"): ("Pause nitrogen fertigation; delay harvest to let leaf nitrate fall", "HOLD_FERTILIZER"),
    ("phosphorus", "low"): ("Apply phosphorus fertiliser", "FERTILIZE"),
    ("potassium", "low"): ("Fertigate with potassium sulphate", "FERTILIZE"),
    ("soil_ec", "high"): ("Leach the beds with low-salinity water; stop using the brackish well", "LEACH"),
}


def crop_profile(crop: str | None) -> dict:
    key = (crop or "").lower().replace(" ", "_")
    return CROPS.get(key, {"ranges": {}, "acceptable": {}, "impacts": {}, "pest_disease": []})


def fields_for(sensors: list[str]) -> list[str]:
    fields: list[str] = []
    for sensor in sensors:
        for field in SENSORS.get(sensor, {}).get("fields", []):
            if field not in fields:
                fields.append(field)
    return fields


def fmt(field: str, value: float) -> str:
    unit = UNITS.get(field, "")
    text = f"{value:,.0f}" if abs(value) >= 1000 else f"{value:g}"
    return f"{text} {unit}".strip()


# ── Candidate crops for the Market & Strategy department ─────────────────
# air / rh: (ideal low, ideal high, acceptable low, acceptable high), copied from the team's
# finetuning.py crop profiles (red amaranth from the team's A. cruentus research; the rest are
# general hydroponic grower-guide values). yield_t_ha: Qatar Open Data per-season yields where
# published (open field 2025 or greenhouse 2025); cycle_days: general grower guide.
CANDIDATE_CROPS: dict[str, dict] = {
    "purple_amaranth": {"name": "Purple amaranth", "air": (22, 30, 10, 40), "rh": (58, 72, 37, 78),
                        "yield_t_ha": 20.0, "yield_source": "estimate (leaf amaranth literature 15-30 t/ha); no Qatar data",
                        "cycle_days": 35},
    "lettuce": {"name": "Lettuce", "air": (16, 24, 7, 29), "rh": (50, 70, 40, 80),
                "yield_t_ha": 22.6, "yield_source": "Qatar Open Data, open field 2025", "cycle_days": 55},
    "spinach": {"name": "Spinach", "air": (15, 22, 7, 27), "rh": (50, 70, 40, 80),
                "yield_t_ha": 21.8, "yield_source": "Qatar Open Data, open field 2025", "cycle_days": 45},
    "parsley": {"name": "Parsley", "air": (16, 24, 7, 29), "rh": (50, 70, 40, 80),
                "yield_t_ha": 16.5, "yield_source": "Qatar Open Data, open field 2025", "cycle_days": 75},
    "swiss_chard": {"name": "Swiss chard", "air": (16, 24, 7, 29), "rh": (50, 70, 40, 80),
                    "yield_t_ha": 21.9, "yield_source": "Qatar Open Data, open field 2025 (chard)", "cycle_days": 60},
    "tomato": {"name": "Tomato", "air": (21, 27, 13, 32), "rh": (60, 80, 50, 85),
               "yield_t_ha": 96.5, "yield_source": "Qatar Open Data, greenhouse 2025", "cycle_days": 150},
    "cucumber": {"name": "Cucumber", "air": (22, 28, 16, 33), "rh": (60, 80, 50, 85),
                 "yield_t_ha": 64.9, "yield_source": "Qatar Open Data, greenhouse 2025", "cycle_days": 90},
    "sweet_pepper": {"name": "Sweet pepper", "air": (21, 27, 15, 32), "rh": (60, 75, 50, 85),
                     "yield_t_ha": 49.2, "yield_source": "Qatar Open Data, greenhouse 2025", "cycle_days": 150},
}
