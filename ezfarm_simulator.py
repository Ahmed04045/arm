"""Stand-in for the EZFarm ESP32 gateway, for testing without hardware.

Publishes readings exactly like esp32_gateway.cpp (sensornet/<node>/reading/<sensor>, raw ADC units)
every 10 s, and prints the agent answers the ESP32 would receive.

    python ezfarm_simulator.py                          # node 1, heat wave building over 3 minutes
    python ezfarm_simulator.py --nodes 1 2 --scenario dry --ramp 120
    python ezfarm_simulator.py --scenario normal --host 192.168.1.100
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from ezfarm_bridge import to_raw

ROOT = Path(__file__).parent


def reading(scenario: str, progress: float, rng: random.Random) -> dict[str, float]:
    """Values in real units; progress 0 -> 1 ramps the scenario in."""
    heat = progress if scenario == "heat" else 0.0
    dry = progress if scenario == "dry" else 0.0
    hour = time.localtime().tm_hour + time.localtime().tm_min / 60
    daylight = max(0.0, math.sin((hour - 5.5) / 13 * math.pi))
    return {
        "temperature": 26 + 15 * heat + rng.gauss(0, 0.3),
        "humidity": 65 - 32 * heat + rng.gauss(0, 1.0),
        "light_intensity": min(65535, 800 + 45000 * daylight * (1 + 0.4 * heat) + rng.gauss(0, 300)),
        "air_quality": 180 + 60 * heat + rng.gauss(0, 8),
        "soil_moisture": 65 - 45 * dry - 10 * heat + rng.gauss(0, 1.0),
        "water_level": 80 - 72 * dry + rng.gauss(0, 1.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "farms" / "ezfarm_site.json"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--nodes", type=int, nargs="+", default=[1])
    parser.add_argument("--scenario", choices=["normal", "heat", "dry"], default="heat")
    parser.add_argument("--ramp", type=float, default=180, help="seconds for the scenario to fully develop")
    parser.add_argument("--period", type=float, default=10, help="seconds between readings (the Nano uses 10)")
    parser.add_argument("--quiet", action="store_true", help="don't print agent answers")
    args = parser.parse_args()

    site = json.loads(Path(args.config).read_text(encoding="utf-8"))
    cfg, prefix = site["mqtt"], site["mqtt"]["topic_prefix"]
    rng = random.Random(1)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ezfarm-gateway-sim")

    def on_connect(c, userdata, flags, reason_code, properties):
        c.subscribe([(f"{prefix}/+/agent/+", 0), (f"{prefix}/+/agent/action/+", 0),
                     (f"{prefix}/config/+", 0), (f"{prefix}/agent/online", 0)])

    def on_message(c, userdata, msg):
        if args.quiet or msg.topic.endswith("/report"):
            return
        body = msg.payload.decode(errors="replace") or "(cleared)"
        print(f"  ESP32 <- {msg.topic}  [{len(msg.payload)} B]  {body}")

    client.on_connect, client.on_message = on_connect, on_message
    client.connect(args.host or cfg["host"], args.port or cfg["port"])
    client.loop_start()
    started = time.time()
    print(f"Simulating gateway for nodes {args.nodes}, scenario '{args.scenario}'. Ctrl+C to stop.")
    try:
        while True:
            progress = min(1.0, (time.time() - started) / args.ramp)
            for node in args.nodes:
                for name, value in reading(args.scenario, progress, rng).items():
                    raw = to_raw(value, site["sensors"][name].get("calibration"))
                    payload = {"type": name, "value": round(raw, 2), "timestamp": int((time.time() - started) * 1000)}
                    client.publish(f"{prefix}/{node}/reading/{name}", json.dumps(payload))
                client.publish(f"{prefix}/{node}/status", json.dumps({"status": "green"}))
            print(f"-> published node(s) {args.nodes} at {progress:.0%} of '{args.scenario}'")
            time.sleep(args.period)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
