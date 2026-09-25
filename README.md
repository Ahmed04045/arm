# QU-ARS Red Amaranth Agent Network

Hackathon project: an **Agent Controller** designs a network of specialist AI **departments** for an existing red/purple amaranth (*Amaranthus cruentus*) farm at the Qatar University Agricultural Research Station. The network reads farm state from the master device (the **EZFarm** ESP32 gateway). The farmer gets three things:

1. **Continuous checks** every minute, rules only: status, department colours and a ranked action plan for the ESP32.
2. **Briefings** twice a day (06:00, 18:00) or on request. These cover today's condition and advice, plus **market insights**: which crops fit this greenhouse by season, and which changes could raise revenue at current Qatar prices. They are backed by official data and web research.
3. **Questions.** The farmer can ask anything, from the ESP32 or the app, and gets an answer grounded in the network's findings.

*The other scenario, a new empty farm where the Controller proposes a network plan for approval, is outside this demo.*

```
 EZFarm Nano nodes --RF--> ESP32 gateway --MQTT--> ezfarm_bridge.py (laptop) --MQTT--> ESP32 (answers)

 every minute (rules)                                          briefings (06:00 · 18:00 · on request)
 ┌──────────────┬───────────────┬──────────────┐             ┌──────────────────────────────────────┐
 AGRI-ENV (ENV)  SOIL & WATER    DATA & ANALYTICS             MARKET & STRATEGY (MKT)
 Climate         Irrigation      Trend · Anomaly              Market Price · Crop Suitability
 Light & Air     Soil Chemistry  Forecast                     Profitability · Web Research
       └───────┬───────┘ (dormant w/o probe)                    (Qatar Open Data + price table + Tavily)
         CROP SCIENCE (CROP): Physiology · Leaf Quality · Plant Health · AI Diagnosis (farm-qwen)
                         └──────────────────────┬───────────────────────┘
                                         FARM DIRECTOR
          status · actions (every minute)   │   "Today" + "Opportunity" briefing, insights (briefings)
                                  FARM ASSISTANT answers questions any time
```

There are 20 specialist agents in 5 departments, plus the department heads and the Director. They run as a **LangGraph** graph: agents inside a department run in parallel, each head waits for its team, Crop Science waits for the Environment and Soil & Water reports, and the Director waits for every head. Agents whose sensors a site doesn't have go **dormant**, so one network design fits both the research greenhouse and a small EZFarm node.

## Run it

### Live with EZFarm hardware (or the simulator)

```powershell
python -m pip install -r requirements.txt
copy .env.example .env                           # optional keys: TAVILY_API_KEY (web research), OPENROUTER_API_KEY
mosquitto -v                                     # MQTT broker; the gateway's MQTT_BROKER = this laptop's IP
python ezfarm_bridge.py --host localhost         # checks every 60 s; briefings 06:00/18:00 or on request
python ezfarm_simulator.py --nodes 1 2 --scenario heat   # optional fake gateway
```

The bridge listens to the gateway's existing `sensornet/<node>/reading/<sensor>` topics and publishes compact, retained answers to `sensornet/<node>/agent/...`. It also pushes red amaranth thresholds to `sensornet/config/<sensor>`, which the gateway firmware already applies. From the ESP32, the farmer can publish `{"briefing":true}` to `sensornet/agent/request` for a briefing, or a question to `sensornet/agent/ask`. **See [ESP32_INTEGRATION.md](ESP32_INTEGRATION.md) for every topic, payload and an Arduino snippet.**

Configure the site in `farms/ezfarm_site.json`: broker, node names, check interval, `briefing_times`, and the **raw-ADC calibration for soil moisture and water level (measure your sensors)**.

### Research-greenhouse demo (Streamlit)

```powershell
streamlit run app.py
```

It uses a full sensor kit and 3 days of synthetic data with scripted incidents.

1. **Normal day.** At `2026-09-23 12:00` every department reports STABLE.
2. **Early warning.** At `2026-09-24 10:30` the Forecast and Anomaly agents flag the cooling-pad failure before anything is out of range.
3. **Incident.** At `2026-09-24 14:30` the problems cascade: heat above 40 °C, dry air, a brackish backup well raising soil pH and salinity, and warm irrigation water. The Director ranks it all into one plan.
4. **Briefing.** Press **Run briefing now** to see:
   - the Director's *Today* and *Opportunity* briefing;
   - suggestion cards, such as "Confirm your amaranth price: below 2.5 QR/kg, cucumber earns more" or "Diversify with parsley Sep–May";
   - a gross-revenue ranking and a season-fit calendar.
5. **Ask.** Type questions such as "Which crop would earn more than amaranth this winter?" or "Why is the status red and what should I do first?".

## Market insights: data and method

| What | Source | Confidence shown to the farmer |
|---|---|---|
| Retail prices of local produce (tomato 3.0, parsley 22.5 QR/kg; 2023) | Qatar Open Data, *annual average consumer prices* | official |
| Vegetable farm-gate value, 2.68 QR/kg (2023) | Qatar Open Data, *value* ÷ *quantity of agricultural production* | official |
| Vegetable self-sufficiency, 19.7 % (2023): most is imported | Qatar Open Data, *production and self-sufficiency* | official |
| Yields per crop (e.g. lettuce 22.6, cucumber 64.9 t/ha) | Qatar Open Data, *production, area and yield* (2025) and *greenhouse crops* (2025) | official |
| Monthly climate normals (2020–2024) | Qatar Open Data, *monthly temperature and humidity* | official |
| Farm-gate estimates for lettuce, spinach, cucumber, tomato | Selina Wamucii (modelled data, 2023) | estimate |
| Amaranth price | **none published**, priced like spinach until the farmer enters their buyer's price | proxy |
| Recent prices from the web | Tavily search (free key, 1,000 searches/month), cached 12 h | unverified |

The official data is fetched by `python market.py` (the briefing refreshes it weekly) and cached in `farms/market_cache.json`, so briefings also work offline. The prices the model uses are in **`farms/market_prices.json`: edit it with your real buyer prices** (confidence `farmer`).

**Method.**
- **Crop fit:** each candidate crop's temperature ranges (from the team's fine-tuning crop profiles) are checked against a rough model of this greenhouse's climate per month. The model is Qatar's monthly normals plus the cooling this greenhouse has shown.
- **Revenue:** yield per m² ÷ cycle length × farm-gate price × season fit.
- **Suggestions:** better crops, a two-season rotation, a break-even price check when the current price is unconfirmed, and a diversification crop backed by an official price.
- **All figures are gross revenue.** Seed, labour, energy and water costs are not included, and the app says so.

## Models

```powershell
ollama pull qwen2.5:3b                               # minimum: fallback for every agent
ollama pull llama3.2:3b gemma2:2b phi3.5 qwen2.5:7b  # each department on its own model (~10 GB)
ollama create farm-qwen -f Modelfile                 # the team's fine-tune (../finetuning, farm-qwen-gguf/)
```

- **LLMs only write** in briefings and answers. The every-minute checks never call a model.
- **How many models write:** in briefings, `--llm` chooses who writes. `director` is 1 call per node, `leaders` 7, `all` 22.
- **Where they run:** `--route` picks where models run (`farms/model_routes.json`): `local` (Ollama), `openrouter` (a free cloud model per department) or `hybrid` (only the Director in the cloud).
- **Cloud limits:** OpenRouter's free tier allows 50 requests/day (1,000 after $10 of credits). Twice-daily briefings in `director` mode use about 2 per node per day plus questions. `llm.py` enforces the daily cap and falls back to rule-based text.

## Files

| File | Role |
|---|---|
| `farms/quars_amaranth_network.json` | **The network spec**: departments, agents, skills, models, prompts; `"runs": "briefing"` marks Market & Strategy |
| `farms/ezfarm_site.json` | EZFarm site: MQTT, nodes, sensor mapping, calibration, check interval, briefing times |
| `farms/market_prices.json` | Price table with source and confidence per crop. **Edit with your real prices** |
| `farms/market_cache.json` | Cached official Qatar Open Data (prices, yields, self-sufficiency, climate) |
| `farms/model_routes.json` | Where each model runs (local / OpenRouter / hybrid) |
| `ezfarm_bridge.py` | MQTT bridge: checks, scheduled briefings, questions |
| `ezfarm_simulator.py` | Fake gateway for testing without hardware |
| `assistant.py` | Answers the farmer's questions from the network's findings |
| `market.py` | Downloads and caches the official data |
| `agents/` | Skills: `monitors`, `crop_science`, `diagnosis`, `analytics`, `strategy`, `leadership` |
| `knowledge.py` | Amaranth profile (team research), candidate crops, actions and command codes |
| `llm.py` | Ollama and OpenAI-compatible cloud routers, with rate limits |
| `app.py` | Streamlit demo |

## Design choices worth explaining to judges

- **Science sets the safe ranges; code checks them.** Status, LEDs and commands come only from rule checks against the team's *A. cruentus* ranges.
- **Models explain; they don't decide.** LLMs write briefings and answers from verified facts, and only mention sensors that are installed. Insights are advice, never commands.
- **Every price says how much to trust it.** Official, estimate, proxy, farmer or unverified web. The break-even check tells the farmer which number matters most to confirm.
- **Each layer degrades gracefully.** The ESP32 keeps its own threshold checks without the laptop. Briefings work offline from the cache. Questions fall back to status and top action when no model is available.

## Limitations

- The research demo's sensor data is synthetic. On EZFarm, soil moisture and water level need calibration, and the MQ-135 reading is an uncalibrated index.
- Retail price data stops at 2023, and no official price exists for amaranth, lettuce or spinach. The market advice is only as good as `market_prices.json`.
- The greenhouse climate model is rough, and the amaranth yield is a literature estimate.
- The 3B local model occasionally muddles comparisons. A larger model (the `hybrid` route) answers questions better.
