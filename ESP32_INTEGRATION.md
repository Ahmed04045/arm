# ESP32 integration: getting the agent network's answers

The agent network runs on the laptop (`ezfarm_bridge.py`). It reads what your gateway already publishes and sends its answers back over the **same MQTT broker**. You don't have to change anything for the network to *get* data. To *show* the answers, the gateway subscribes to a few extra topics.

```
Nano nodes --RF--> ESP32 gateway --MQTT--> broker <--MQTT--> laptop: ezfarm_bridge.py (agent network)
                        ^                                              |
                        +------------- answers (retained) -------------+
```

## What the bridge already uses from your firmware (no changes needed)

| Topic | Direction | Notes |
|---|---|---|
| `sensornet/<node>/reading/<sensor>` | gateway → bridge | `{"type","value","timestamp"}` exactly as `publishReading()` sends it |
| `sensornet/<node>/status` | gateway → bridge | your green/yellow/red plausibility check, echoed back as `gw` |
| `sensornet/config/<sensor>` | bridge → gateway | **Already handled by `mqttCallback()`.** When the bridge starts, it publishes red amaranth `normalMin`/`normalMax` (retained) for temperature, humidity, soil_moisture, water_level and air_quality, so the node LEDs follow the crop. `hardMin`/`hardMax` are left alone. To turn this off, run the bridge with `--no-thresholds`. |

The bridge turns the raw ADC values into % using the calibration in `farms/ezfarm_site.json` (soil moisture: dry 800 → 0 %, wet 350 → 100 %; water level: 0 → 0 %, 600 → 100 %). **Measure your own sensors and edit those numbers.** The threshold values sent to the gateway are converted back to raw units with the same calibration.

## Answer topics (new, all retained)

Every message is **retained**, so the ESP32 gets the latest answer as soon as it subscribes, even after a reboot.

| Topic | Max size | Payload |
|---|---|---|
| `sensornet/agent/online` | 1 B | `"1"` while the bridge runs, `"0"` otherwise (MQTT last will) |
| `sensornet/<node>/agent/status` | ≤ 200 B | `{"status":"red","led":2,"level":"CRITICAL","actions":2,"gw":"green","time":"14:35:27","headline":"Tank water level LOW: 8.09 % (ideal 30 %–100 %)"}` |
| `sensornet/<node>/agent/departments` | ≤ 80 B | `{"ENV":"green","SOIL":"red","CROP":"red","DATA":"yellow"}` |
| `sensornet/<node>/agent/action/<k>` | ≤ 200 B | `{"p":1,"sev":"red","cmd":"REFILL_TANK","dept":"SOIL","do":"Refill the water tank","why":"Tank water level LOW: 8.09 % (ideal 30 %–100 %)"}`, k = 1…5 in priority order. **An empty payload means that slot was cleared.** |
| `sensornet/<node>/agent/brief` | ≤ 700 B | `{"by":"qwen2.5:3b","status":"red","time":"2026-09-25 06:00","text":"Today: … Opportunity: …"}`. Written at 06:00 and 18:00 or on request. Needs `mqtt.setBufferSize(1024)`. |
| `sensornet/<node>/agent/insight/<k>` | ≤ 240 B | k = 1…3. `{"type":"price","title":"Confirm your purple amaranth price","gain":"","conf":"proxy","detail":"…below 2.52 QR/kg, Cucumber earns more…"}`. Types: `crop`, `rotation`, `price`, `diversify`. Empty payload = cleared. |
| `sensornet/agent/answer` | ≤ 900 B | Reply to a question: first `{"id":"q7","thinking":true}`, then `{"id":"q7","node":1,"by":"qwen2.5:3b","a":"…","src":["https://…"]}`. Needs `mqtt.setBufferSize(1024)`. |
| `sensornet/<node>/agent/report` | large | Full JSON for laptops and servers. **Don't subscribe on the ESP32.** |

- `status`, `departments` and `action/*` fit PubSubClient's default 256-byte buffer. Only `brief` needs the bigger buffer.
- `led` uses the same numbers as your `LedCommand` enum (0 = green OK, 1 = yellow warn, 2 = red error). If you want the node LEDs to show the network's verdict, pass it straight to `sendCommand(nodeId, (LedCommand)led)`.
- `status`, `departments` and `action/*` refresh every `interval_seconds` (60 s by default). They come from rule checks against the crop's ranges and are never written by an LLM.
- `brief` and `insight/*` come from **briefings**, which run twice a day (06:00 and 18:00, set `briefing_times` in `farms/ezfarm_site.json`) or when asked. A briefing adds the Market & Strategy department (Qatar prices, crop suitability by season, profitability, web research), and an LLM writes the text.
- Publish to `sensornet/agent/request`:
  - `{"node":1}` (or an empty payload for all nodes) re-runs the rule checks now.
  - `{"briefing":true,"node":1}` runs a full briefing now. A good fit for a "Brief me" button; it takes 10–60 s depending on the model.
- **Questions.** Publish to `sensornet/agent/ask`: `{"id":"q7","node":1,"q":"Should I plant parsley this winter?"}`. Plain text works too. The answer arrives on `sensornet/agent/answer` with the same `id`. Questions are answered one at a time. They need a local model or a cloud key; otherwise the reply says the assistant is offline and gives the current status.

### Department codes

| Code | Department | Agents |
|---|---|---|
| `ENV` | Agri-Environment | Climate, Light & Air Quality |
| `SOIL` | Soil & Water | Irrigation & Water (Soil Chemistry sleeps until a pH/NPK probe is added) |
| `CROP` | Crop Science | Crop Physiology, Leaf Quality & Pigment, Plant Health, AI Diagnosis (farm-qwen) |
| `DATA` | Data & Analytics | Trend, Anomaly, Forecast |

### Command codes (`cmd`)

The `cmd` field is machine-readable, and `do` is the human text. What you do with each code is your choice: show it, beep, or switch a relay. The design notes list which actuators fit the budget.

| `cmd` | Meaning | Possible actuator |
|---|---|---|
| `REFILL_TANK` | Tank below 30 % | top-up pump |
| `IRRIGATE` / `HOLD_IRRIGATION` | Soil too dry / too wet | irrigation pump |
| `VENTILATE` | Humid air, wet-rot risk, bad air quality | fan relay |
| `MIST` | Air too dry, spider mites | mister / fogger |
| `COOL` / `HEAT` | Air temperature out of range | person (or fan) |
| `SHADE` / `SHADE_OPEN` | Too much / too little light | person |
| `COOL_WATER` / `WARM_WATER` | Water temperature | person |
| `FERTILIZE` / `HOLD_FERTILIZER` / `ACIDIFY` / `LIME` / `LEACH` / `CO2_ON` | Only with pH/NPK/EC/CO₂ sensors | person |
| `NOTIFY` | Anything else | display only |

The design notes say *no automatic fertilising without an EC reading*. The network only raises nutrient commands when those sensors exist.

## Arduino snippet (ArduinoJson 6, same style as `esp32_gateway.cpp`)

These are additions for your own copy of the gateway. Nothing in `EZFarm_CMUQ-main` was changed.

```cpp
// ---- agent network answers -------------------------------------------
struct AgentAction { char cmd[20]; char sev[8]; char text[96]; };
struct AgentView {
  char status[8] = "";      // green | yellow | red
  uint8_t led = 0;          // same values as LedCommand
  uint8_t actionCount = 0;
  char headline[96] = "";
  char brief[400] = "";
  AgentAction actions[5];
};
AgentView agentView[MAX_NODES + 1];   // indexed by nodeId (1..MAX_NODES)
bool agentOnline = false;

// setup(): before the first connect
//   mqtt.setBufferSize(1024);            // only needed for .../agent/brief

// reconnectMQTT(): after mqtt.subscribe(CONFIG_TOPIC_FILTER);
//   mqtt.subscribe("sensornet/+/agent/status");
//   mqtt.subscribe("sensornet/+/agent/departments");
//   mqtt.subscribe("sensornet/+/agent/action/+");
//   mqtt.subscribe("sensornet/+/agent/brief");
//   mqtt.subscribe("sensornet/+/agent/insight/+");
//   mqtt.subscribe("sensornet/agent/answer");
//   mqtt.subscribe("sensornet/agent/online");

// mqttCallback(): first line
//   if (handleAgentMessage(topic, payload, length)) return;

bool handleAgentMessage(char *topic, byte *payload, unsigned int length) {
  if (strcmp(topic, "sensornet/agent/online") == 0) {
    agentOnline = length > 0 && payload[0] == '1';
    return true;
  }
  unsigned nodeId = 0, slot = 0;
  char kind[16] = "";
  int n = sscanf(topic, "sensornet/%u/agent/%15[^/]/%u", &nodeId, kind, &slot);
  if (n < 2) return false;                       // not an agent topic
  if (nodeId == 0 || nodeId > MAX_NODES) return true;
  AgentView &v = agentView[nodeId];

  if (strcmp(kind, "action") == 0 && slot >= 1 && slot <= 5 && length == 0) {
    v.actions[slot - 1].cmd[0] = '\0';           // slot cleared by the bridge
    return true;
  }

  DynamicJsonDocument doc(1024);
  if (deserializeJson(doc, payload, length)) return true;

  if (strcmp(kind, "status") == 0) {
    strlcpy(v.status, doc["status"] | "", sizeof(v.status));
    v.led = doc["led"] | 0;
    v.actionCount = doc["actions"] | 0;
    strlcpy(v.headline, doc["headline"] | "", sizeof(v.headline));
    // optional: let the network drive the node LED
    // sendCommand(nodeId, (LedCommand)v.led);
  } else if (strcmp(kind, "action") == 0 && slot >= 1 && slot <= 5) {
    AgentAction &a = v.actions[slot - 1];
    strlcpy(a.cmd, doc["cmd"] | "", sizeof(a.cmd));
    strlcpy(a.sev, doc["sev"] | "", sizeof(a.sev));
    strlcpy(a.text, doc["do"] | "", sizeof(a.text));
  } else if (strcmp(kind, "brief") == 0) {
    strlcpy(v.brief, doc["text"] | "", sizeof(v.brief));
  }
  // "departments": iterate doc.as<JsonObject>() if you want the department colours
  // "insight": same pattern as "action" (slot 1..3, fields title / gain / detail)
  return true;
}

// Answers arrive on "sensornet/agent/answer" (no node number in the topic): handle it before the sscanf,
// e.g.  if (strcmp(topic, "sensornet/agent/answer") == 0) { ... doc["thinking"] ... doc["a"] ... }

// Asking a question from the ESP (e.g. typed on a keypad or chosen from a preset list):
void askAgent(uint8_t nodeId, const char *id, const char *question) {
  StaticJsonDocument<256> doc;
  doc["id"] = id;
  doc["node"] = nodeId;
  doc["q"] = question;
  char payload[256];
  size_t len = serializeJson(doc, payload);
  mqtt.publish("sensornet/agent/ask", (uint8_t *)payload, len);
}

// "Brief me" button:
//   mqtt.publish("sensornet/agent/request", "{\"briefing\":true,\"node\":1}");
```

## Testing without hardware

```powershell
# 1. MQTT broker on the laptop (the gateway's MQTT_BROKER must point at this laptop's IP)
winget install EclipseFoundation.Mosquitto      # then: mosquitto -v   (on newer versions, add a config with "listener 1883" and "allow_anonymous true" so the ESP32 can connect over the LAN)

# 2. the agent network
python ezfarm_bridge.py --interval 10            # add --llm leaders for LLM briefings

# 3. a fake gateway (publishes readings, prints what the ESP32 would receive)
python ezfarm_simulator.py --nodes 1 2 --scenario heat     # or: dry, normal
```
