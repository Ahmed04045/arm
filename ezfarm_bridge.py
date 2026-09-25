"""EZFarm <-> agent network bridge (runs on the laptop next to the MQTT broker).

Listens to what the EZFarm ESP32 gateway already publishes, runs the department network for each
Nano node, and publishes the answers back over MQTT for the ESP32. The EZFarm firmware needs no
changes to *send* data; the ESP32 only subscribes to the answer topics (see ESP32_INTEGRATION.md).

    in   sensornet/<node>/reading/<sensor>   {"type","value","timestamp"}   (gateway, every ~10 s)
    in   sensornet/<node>/status             {"status":"green|yellow|red"}  (gateway)
    in   sensornet/agent/request             {"node":1} or empty            (re-run the rule checks now)
                                             {"briefing":true[,"node":1]}   (run a briefing now)
    in   sensornet/agent/ask                 {"id":"q1","node":1,"q":"..."} (farmer's question; plain text also works)
    out  sensornet/config/<sensor>           {"normalMin","normalMax"}      (crop thresholds, gateway already applies them)
    out  sensornet/agent/online              "1" / "0"                      (last will)
    out  sensornet/<node>/agent/status       small, fits the default 256-byte PubSubClient buffer
    out  sensornet/<node>/agent/departments  small
    out  sensornet/<node>/agent/action/<k>   small, k = 1..max_actions
    out  sensornet/<node>/agent/brief        briefing text, up to ~700 bytes (needs mqtt.setBufferSize(1024))
    out  sensornet/<node>/agent/insight/<k>  small, k = 1..3: crop, rotation, price and diversification suggestions
    out  sensornet/agent/answer              {"id","node","by","a"} up to ~900 bytes; {"id","thinking":true} first
    out  sensornet/<node>/agent/report       full JSON for laptops / servers (not for the ESP32)

Rule checks (status, departments, actions) run every interval_seconds and take milliseconds.
Briefings run at briefing_times (default 06:00 and 18:00) or on request, in a background thread: they add
the Market & Strategy department (prices, crop suitability, profitability, web research) and the LLM-written
briefing. Questions are answered one at a time in another background thread.

    python ezfarm_bridge.py                       # rules only, broker from farms/ezfarm_site.json
    python ezfarm_bridge.py --llm leaders         # department heads + director use their models
    python ezfarm_bridge.py --host 192.168.1.100 --interval 10
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

import llm
from controller import execute_network
from knowledge import SENSORS
from planner import validate_spec

ROOT = Path(__file__).parent
LED = {"STABLE": 0, "ATTENTION REQUIRED": 1, "CRITICAL": 2}           # = LedCommand in the EZFarm firmware
COLOR = {"STABLE": "green", "ATTENTION REQUIRED": "yellow", "CRITICAL": "red",
         "OK": "green", "WARNING": "yellow"}
SMALL = 200    # payload budget (bytes) for messages the ESP32 must receive with the default buffer
BRIEF = 700
ANSWER = 900
log = logging.getLogger("bridge")

Message = tuple[str, str, bool]   # topic, payload, retain


# ── calibration ──────────────────────────────────────────────────────────
def to_unit(raw: float, calibration: dict | None) -> float:
    if not calibration:
        return raw
    (r0, r1), (v0, v1) = calibration["raw"], calibration["value"]
    value = v0 + (raw - r0) * (v1 - v0) / (r1 - r0)
    return round(min(max(value, min(v0, v1)), max(v0, v1)), 2)


def to_raw(value: float, calibration: dict | None) -> float:
    if not calibration:
        return value
    (r0, r1), (v0, v1) = calibration["raw"], calibration["value"]
    return round(r0 + (value - v0) * (r1 - r0) / (v1 - v0), 1)


def _sentences(text: str, max_bytes: int) -> str:
    """Keep whole sentences that fit in max_bytes (falls back to a hard cut for one very long sentence)."""
    if len(text.encode()) <= max_bytes:
        return text
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        candidate = f"{kept} {sentence}".strip()
        if len(candidate.encode()) > max_bytes:
            break
        kept = candidate
    return kept or text.encode()[: max(8, max_bytes - 3)].decode(errors="ignore").rstrip() + "…"


def _fit(payload: dict[str, Any], budget: int, trim: list[str]) -> str:
    """Serialise compactly, shortening the given text keys until the payload fits the byte budget."""
    payload = dict(payload)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for key in trim:   # first drop whole trailing sentences, then shorten character by character
        if len(text.encode()) > budget and isinstance(payload.get(key), str):
            overflow = len(text.encode()) - budget
            payload[key] = _sentences(payload[key], max(16, len(payload[key].encode()) - overflow))
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    for key in trim:
        while len(text.encode()) > budget and len(payload.get(key, "")) > 8:
            payload[key] = payload[key][: max(8, len(payload[key]) - 12)].rstrip() + "…"
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return text


class Node:
    def __init__(self, node_id: int, name: str, max_rows: int):
        self.id, self.name = node_id, name
        self.pending: dict[str, list[float]] = {}
        self.last: dict[str, float] = {}
        self.capabilities: set[str] = set()
        self.rows: deque[dict[str, Any]] = deque(maxlen=max_rows)
        self.last_seen = 0.0
        self.last_run = 0.0
        self.gateway_status = None
        self.published_actions = -1   # unknown after a restart: the first run clears every action slot


class Bridge:
    """Transport-free core: feed it MQTT messages, call tick(), publish what it returns."""

    def __init__(self, site: dict[str, Any], spec: dict[str, Any], llm_mode: str = "off",
                 installed_models: list[str] | None = None, fallback_model: str = "qwen2.5:3b",
                 model_map: dict[str, str] | None = None):
        self.site, self.spec = site, spec
        self.prefix = site["mqtt"]["topic_prefix"]
        self.interval = float(site.get("interval_seconds", 60))
        self.window_rows = max(2, int(site.get("window_minutes", 60) * 60 / self.interval))
        self.max_rows = int(2 * 24 * 3600 / self.interval)   # two days, for the forecast agent
        self.llm_mode, self.fallback, self.model_map = llm_mode, fallback_model, model_map or {}
        self.installed = installed_models or []
        self.nodes: dict[int, Node] = {}
        self.requested: set[int] = set()
        self.specs: dict[frozenset, dict] = {}
        self.lock = threading.Lock()
        self.outbox: queue.Queue[Message] = queue.Queue()
        self.briefing_times = site.get("briefing_times", ["06:00", "18:00"])
        today = datetime.now().strftime("%Y-%m-%d")
        self.briefing_done = {f"{today} {t}" for t in self.briefing_times if datetime.now().strftime("%H:%M") >= t}
        self.briefing_requested: set[int] = set()
        self.briefing_thread: threading.Thread | None = None
        self.last_monitor: dict[int, dict[str, Any]] = {}
        self.last_briefing: dict[int, dict[str, Any]] = {}
        self.published_insights: dict[int, int] = {}
        self.questions: queue.Queue[tuple[int | None, str, str]] = queue.Queue()
        self.assistant_model = spec["director"].get("model")
        threading.Thread(target=self._question_worker, daemon=True).start()

    # ── inbound ──
    def on_message(self, topic: str, payload: bytes, now: float | None = None) -> None:
        now = time.time() if now is None else now
        parts = topic.split("/")
        if len(parts) < 3 or parts[0] != self.prefix:
            return
        if parts[1:] == ["agent", "request"]:
            try:
                body = json.loads(payload or b"{}")
            except json.JSONDecodeError:
                body = {}
            body = body if isinstance(body, dict) else {}
            with self.lock:
                nodes = {int(body["node"])} if "node" in body else set(self.nodes)
                if body.get("briefing"):
                    self.briefing_requested |= nodes
                else:
                    self.requested |= nodes
            return
        if parts[1:] == ["agent", "ask"]:
            text = (payload or b"").decode("utf-8", errors="replace").strip()
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = {"q": text}
            if isinstance(body, dict) and str(body.get("q", "")).strip():
                node = int(body["node"]) if str(body.get("node", "")).isdigit() else None
                self.questions.put((node, str(body.get("id", int(now))), str(body["q"]).strip()))
            return
        if not parts[1].isdigit():
            return
        node_id = int(parts[1])
        try:
            body = json.loads(payload)
        except json.JSONDecodeError:
            return
        with self.lock:
            node = self._node(node_id)
            if len(parts) == 4 and parts[2] == "reading":
                sensor = self.site["sensors"].get(parts[3])
                if sensor is None or not isinstance(body.get("value"), (int, float)):
                    return
                field = SENSORS[sensor["capability"]]["fields"][0]
                value = to_unit(float(body["value"]), sensor.get("calibration"))
                node.pending.setdefault(field, []).append(value)
                node.last[field] = value
                node.capabilities.add(sensor["capability"])
                node.last_seen = now
            elif len(parts) == 3 and parts[2] == "status":
                node.gateway_status = body.get("status")

    def _node(self, node_id: int) -> Node:
        if node_id not in self.nodes:
            name = self.site.get("nodes", {}).get(str(node_id), {}).get("name", f"Node {node_id}")
            self.nodes[node_id] = Node(node_id, name, self.max_rows)
            log.info("new node %s (%s)", node_id, name)
        return self.nodes[node_id]

    # ── periodic ──
    def tick(self, now: float | None = None) -> list[Message]:
        now = time.time() if now is None else now
        out: list[Message] = []
        with self.lock:
            due = []
            for node in self.nodes.values():
                fresh = now - node.last_seen <= self.site.get("stale_after_seconds", 120)
                if fresh and (node.id in self.requested or now - node.last_run >= self.interval) and node.last:
                    self._close_bucket(node, now)
                    node.last_run = now
                    due.append(node)
            self.requested.clear()
        for node in due:
            try:
                out += self.run_node(node)
            except Exception:
                log.exception("network run failed for node %s", node.id)
        self._maybe_start_briefing(now)
        while not self.outbox.empty():
            out.append(self.outbox.get())
        return out

    # ── briefings: twice a day or on request ──
    def _maybe_start_briefing(self, now: float) -> None:
        clock = datetime.fromtimestamp(now)
        wanted: set[int] = set()
        for t in self.briefing_times:
            slot = f"{clock:%Y-%m-%d} {t}"
            if clock.strftime("%H:%M") >= t and slot not in self.briefing_done:
                self.briefing_done.add(slot)
                wanted |= set(self.nodes)
                log.info("scheduled briefing %s", slot)
        with self.lock:
            wanted |= self.briefing_requested
            nodes = [self.nodes[n] for n in wanted if n in self.nodes and self.nodes[n].rows]
            busy = self.briefing_thread is not None and self.briefing_thread.is_alive()
            if not nodes or busy:
                return   # a busy worker leaves the requests queued for the next tick
            self.briefing_requested -= {n.id for n in nodes}
        self.briefing_thread = threading.Thread(target=self._briefing_worker, args=(nodes,), daemon=True)
        self.briefing_thread.start()

    def _briefing_worker(self, nodes: list[Node]) -> None:
        for node in nodes:
            try:
                started = time.perf_counter()
                spec, result = self._execute(node, self.llm_mode, purpose="briefing")
                self.last_briefing[node.id] = result
                for message in self.briefing_messages(node, spec, result):
                    self.outbox.put(message)
                log.info("node %s: briefing by %s (%.0fs) %s", node.id, result["assessment"]["model"].get("used") or "rules",
                         time.perf_counter() - started, llm.budget.usage() or "")
            except Exception:
                log.exception("briefing failed for node %s", node.id)

    def briefing_messages(self, node: Node, spec: dict[str, Any], result: dict[str, Any]) -> list[Message]:
        a, base = result["assessment"], f"{self.prefix}/{node.id}/agent"
        stamp = result["reading"].get("timestamp", "")
        out: list[Message] = [(f"{base}/brief", _fit({
            "by": a["model"].get("used") or "rules", "status": COLOR[a["status"]], "time": str(stamp)[:16].replace("T", " "),
            "text": a["summary"]}, BRIEF, ["text"]), True)]
        insights = a.get("insights", [])[:3]
        for k, item in enumerate(insights, 1):
            out.append((f"{base}/insight/{k}", _fit({
                "type": item["type"], "title": item["title"], "gain": f"{item['gain_pct']:+d}%" if item["gain_pct"] else "",
                "conf": item["confidence"], "detail": item["detail"]}, SMALL + 40, ["detail", "title"]), True))
        for k in range(len(insights) + 1, self.published_insights.get(node.id, 3) + 1):
            out.append((f"{base}/insight/{k}", "", True))
        self.published_insights[node.id] = len(insights)
        report = self._report(node, spec, result)
        report.update(insights=a.get("insights", []), research=a.get("research", []),
                      ranking=result["findings"].get("profit_agent", {}).get("ranking", []))
        out.append((f"{base}/report_ai", json.dumps(report, ensure_ascii=False, default=str), True))
        return out

    # ── questions ──
    def _question_worker(self) -> None:
        import assistant

        while True:
            node_id, qid, question = self.questions.get()
            try:
                with self.lock:
                    node = self.nodes.get(node_id) if node_id is not None else next(iter(self.nodes.values()), None)
                self.outbox.put((f"{self.prefix}/agent/answer", json.dumps({"id": qid, "thinking": True}), False))
                if node is None:
                    reply = {"text": "No farm data has arrived yet.", "by": "rules", "sources": []}
                else:
                    state = {"installed_models": self.installed, "fallback_model": self.fallback,
                             "model_map": self.model_map, "assistant_model": self.assistant_model}
                    briefing = self.last_briefing.get(node.id)
                    if briefing is None:   # no briefing yet: get the market / crop / profit numbers (rules only, fast)
                        _, briefing = self._execute(node, "off", purpose="briefing")
                    reply = assistant.answer(question, self.farm_for(node), self.last_monitor.get(node.id), briefing, state)
                log.info("Q[%s] %s -> answered by %s", qid, question[:60], reply["by"])
                self.outbox.put((f"{self.prefix}/agent/answer", _fit({
                    "id": qid, "node": node.id if node else None, "by": reply["by"], "a": reply["text"],
                    "src": reply["sources"][:2]}, ANSWER, ["a"]), True))
            except Exception:
                log.exception("question failed: %s", question)

    def _close_bucket(self, node: Node, now: float) -> None:
        row: dict[str, Any] = {"timestamp": datetime.fromtimestamp(now).isoformat(timespec="seconds")}
        for field, value in node.last.items():
            values = node.pending.get(field)
            row[field] = round(mean(values), 2) if values else value
        node.pending.clear()
        node.rows.append(row)

    def farm_for(self, node: Node) -> dict[str, Any]:
        farm = {k: v for k, v in self.site.items() if k not in ("mqtt", "sensors", "nodes")}
        farm["name"] = f"{self.site['name']} · {node.name}"
        farm["edge_devices"] = [{"id": f"NODE-{node.id}", "zone": node.name, "sensors": sorted(node.capabilities)}]
        return farm

    def _execute(self, node: Node, llm_mode: str, purpose: str = "monitor") -> tuple[dict[str, Any], dict[str, Any]]:
        farm = self.farm_for(node)
        with self.lock:
            key = frozenset(node.capabilities)
            if key not in self.specs:   # the network adapts to the sensors this node actually reports
                self.specs[key] = validate_spec(self.spec, farm)
            rows = list(node.rows)
        spec = self.specs[key]
        result = execute_network(
            spec, farm, rows[-self.window_rows:], rows, llm_mode=llm_mode,
            installed_models=self.installed, fallback_model=self.fallback, interval_minutes=self.interval / 60,
            model_map=self.model_map, purpose=purpose,
        )
        return spec, result

    def run_node(self, node: Node) -> list[Message]:
        """Rules-only run: fast and deterministic, so the ESP32 always has a fresh answer."""
        spec, result = self._execute(node, "off")
        self.last_monitor[node.id] = result
        log.info("node %s: %s, %d actions", node.id, result["assessment"]["status"], len(result["assessment"]["plan"]))
        return self.messages(node, spec, result)

    # ── outbound ──
    def messages(self, node: Node, spec: dict[str, Any], result: dict[str, Any]) -> list[Message]:
        base = f"{self.prefix}/{node.id}/agent"
        a = result["assessment"]
        codes = {d["id"]: d.get("code", d["id"][:4].upper()) for d in spec["departments"]}
        plan = a["plan"][: int(self.site.get("max_actions", 5))]
        stamp = node.rows[-1]["timestamp"] if node.rows else ""
        headline = plan[0]["reason"] if plan else "All departments report normal conditions"
        out: list[Message] = []

        out.append((f"{base}/status", _fit({
            "status": COLOR[a["status"]], "led": LED[a["status"]], "level": a["status"], "actions": len(plan),
            "gw": node.gateway_status or "", "time": stamp[11:19], "headline": headline,
        }, SMALL, ["headline"]), True))

        out.append((f"{base}/departments", json.dumps(
            {codes[d]: COLOR.get(result["reports"][d]["status"], "green") for d in codes if d in result["reports"]},
            separators=(",", ":")), True))

        for k, item in enumerate(plan, 1):
            out.append((f"{base}/action/{k}", _fit({
                "p": item["priority"], "sev": COLOR[item["severity"]], "cmd": item["cmd"],
                "dept": codes.get(item["department"], ""), "do": item["action"], "why": item["reason"],
            }, SMALL, ["why", "do"]), True))
        last = int(self.site.get("max_actions", 5)) if node.published_actions < 0 else node.published_actions
        for k in range(len(plan) + 1, last + 1):
            out.append((f"{base}/action/{k}", "", True))   # empty retained payload deletes stale actions
        node.published_actions = len(plan)

        out.append((f"{base}/report", json.dumps(self._report(node, spec, result), ensure_ascii=False, default=str), True))
        return out

    def _report(self, node: Node, spec: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        a = result["assessment"]
        codes = {d["id"]: d.get("code", d["id"][:4].upper()) for d in spec["departments"]}
        stamp = node.rows[-1]["timestamp"] if node.rows else ""
        return {
            "node": node.id, "name": node.name, "time": stamp, "status": a["status"], "summary": a["summary"],
            "departments": {codes[d]: {"status": result["reports"][d]["status"], "summary": result["reports"][d]["summary"]}
                            for d in codes if d in result["reports"]},
            "plan": a["plan"], "watch": a["watch"], "risks": a["risks"],
            "dormant": [x["name"] for x in spec.get("dormant", [])],
            "reading": result["reading"], "gateway_status": node.gateway_status,
            "ai_diagnosis": next((f.get("advice") for f in result["findings"].values() if f.get("advice")), None),
        }

    def threshold_messages(self) -> list[Message]:
        """Crop ranges in the gateway's own (raw) units, so its node LEDs follow the crop."""
        from knowledge import crop_profile

        ranges = crop_profile(self.site["crop"])["ranges"]
        out = []
        for name in self.site.get("publish_thresholds", []):
            sensor = self.site["sensors"].get(name)
            field = SENSORS[sensor["capability"]]["fields"][0] if sensor else None
            if field not in ranges:
                continue
            lo, hi = (to_raw(v, sensor.get("calibration")) for v in ranges[field])
            out.append((f"{self.prefix}/config/{name}", json.dumps({"normalMin": min(lo, hi), "normalMax": max(lo, hi)}), True))
        return out


# ── MQTT runtime ─────────────────────────────────────────────────────────
def main() -> None:
    import paho.mqtt.client as mqtt

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(ROOT / "farms" / "ezfarm_site.json"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--interval", type=float, help="seconds between network runs per node")
    parser.add_argument("--llm", choices=["off", "director", "leaders", "all"], default="director",
                        help="who writes in briefings (status and actions are always rule-checked). "
                             "LLM calls per briefing per node: director 1, leaders 7, all 22")
    parser.add_argument("--route", default="local", help="model route from farms/model_routes.json: local, openrouter, hybrid")
    parser.add_argument("--briefing-times", nargs="+", help='e.g. 06:00 18:00 (default from the site config)')
    parser.add_argument("--fallback-model", help="overrides the route's fallback model")
    parser.add_argument("--no-thresholds", action="store_true", help="don't push crop thresholds to the gateway")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    site = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.interval:
        site["interval_seconds"] = args.interval
    route = llm.load_routes()[args.route]
    if args.briefing_times:
        site["briefing_times"] = args.briefing_times
    cfg = site["mqtt"]
    host, port = args.host or cfg["host"], args.port or cfg["port"]
    spec = json.loads((Path(args.config).parent / site["network_file"]).read_text(encoding="utf-8"))
    models = llm.available_models() if args.llm != "off" else []
    bridge = Bridge(site, spec, args.llm, models, args.fallback_model or route["fallback"], route["map"])
    prefix = cfg["topic_prefix"]

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.get("client_id", "agent-network"))
    if cfg.get("username"):
        client.username_pw_set(cfg["username"], cfg.get("password") or None)
    client.will_set(f"{prefix}/agent/online", "0", retain=True)

    def on_connect(c, userdata, flags, reason_code, properties):
        log.info("connected to %s:%s (%s)", host, port, reason_code)
        c.subscribe([(f"{prefix}/+/reading/+", 0), (f"{prefix}/+/status", 0), (f"{prefix}/agent/request", 0),
                     (f"{prefix}/agent/ask", 0)])
        c.publish(f"{prefix}/agent/online", "1", retain=True)
        if not args.no_thresholds:
            for topic, payload, retain in bridge.threshold_messages():
                c.publish(topic, payload, retain=retain)
                log.info("threshold %s %s", topic, payload)

    client.on_connect = on_connect
    client.on_message = lambda c, u, msg: bridge.on_message(msg.topic, msg.payload)
    client.connect(host, port, keepalive=30)
    client.loop_start()
    log.info("bridge running: checks every %ss; briefings at %s or on request (LLM %s, route '%s'). Ctrl+C to stop.",
             site["interval_seconds"], ", ".join(site.get("briefing_times", ["06:00", "18:00"])), args.llm, args.route)
    try:
        while True:
            for topic, payload, retain in bridge.tick():
                client.publish(topic, payload, retain=retain)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(f"{prefix}/agent/online", "0", retain=True).wait_for_publish(2)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
