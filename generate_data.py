"""Generate a synthetic-but-plausible sensor stream for the QU-ARS purple amaranth greenhouse.

3 days x 15-minute interval = 288 rows. Days 1-2 are normal September days in Qatar under
working fan-and-pad cooling. Day 3 (2026-09-24) contains scripted incidents:

  10:00  cooling-pad pump fails      -> heat, dry air, spider-mite / scorch risk, colour fading
  11:00  switch to brackish backup   -> soil salinity and pH climb, K and P lock-out
  12:00  sun-heats the header tank   -> warm irrigation water, damping-off risk

    python generate_data.py
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

FARMS = Path(__file__).parent / "farms"
START = datetime(2026, 9, 22)
STEPS = 3 * 24 * 4
INTERVAL = timedelta(minutes=15)


def diurnal(t: datetime, low: float, high: float, peak_hour: float = 14.0) -> float:
    """Smooth daily cycle between low and high, peaking at peak_hour."""
    hour = t.hour + t.minute / 60
    return low + (high - low) * (0.5 + 0.5 * math.cos((hour - peak_hour) / 24 * 2 * math.pi))


def daylight(t: datetime, peak: float) -> float:
    hour = t.hour + t.minute / 60
    return max(0.0, math.sin((hour - 5.5) / 13 * math.pi)) * peak  # sunrise ~05:30, sunset ~18:30


def ramp(t: datetime, start: str, hours: float) -> float:
    """0 -> 1 over `hours` starting at `start` (e.g. '2026-09-24 10:00')."""
    elapsed = (t - datetime.fromisoformat(start)).total_seconds() / 3600
    return min(max(elapsed / hours, 0.0), 1.0)


def amaranth(t: datetime, rng: random.Random) -> dict:
    heat = ramp(t, "2026-09-24 10:00", 3)
    brackish = ramp(t, "2026-09-24 11:00", 4)
    tank = ramp(t, "2026-09-24 12:00", 2.5)
    return {
        "air_temperature": diurnal(t, 24, 28.3) + 12.5 * heat + rng.gauss(0, 0.3),
        "humidity": diurnal(t, 68.5, 63) - 29 * heat + rng.gauss(0, 1.0),
        "light": daylight(t, 78000) * (1 + 0.18 * heat) + rng.gauss(0, 900),
        "co2": diurnal(t, 580, 480) - 20 * heat + rng.gauss(0, 10),
        "water_temperature": diurnal(t, 20.5, 22.3) + 7.5 * tank + rng.gauss(0, 0.2),
        "soil_ph": 6.4 + 1.3 * brackish + rng.gauss(0, 0.03),
        "soil_ec": 1.5 + 4.0 * brackish + rng.gauss(0, 0.05),
        "nitrogen": 68 - 6 * brackish + rng.gauss(0, 1.2),
        "phosphorus": 28 - 11 * brackish + rng.gauss(0, 0.6),
        "potassium": 190 - 80 * brackish + rng.gauss(0, 3),
    }


def main() -> None:
    rng = random.Random(7)
    rows = []
    for step in range(STEPS):
        t = START + step * INTERVAL
        row = {k: round(max(v, 0), 2) for k, v in amaranth(t, rng).items()}
        row["light"] = round(row["light"])
        rows.append({"timestamp": t.strftime("%Y-%m-%d %H:%M:%S"), **row})
    path = FARMS / "quars_amaranth_readings.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"wrote {path.name} ({STEPS} rows)")


if __name__ == "__main__":
    main()
