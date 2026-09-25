# Hydro Monitor: the agentic part (hackathon MVP)

This is the AI side of **Hydro Monitor** (see `docs/Hydro Monitor_ Project Plan (2).pdf`). It's a sensing and control kit for small farms, looked after by a team of AI specialists built for each farm.
- Sensors report to a master node (ESP32), which logs every reading and switches pumps to keep conditions inside safe ranges.
- **A few times a day, a CrewAI agent network reads the farm's data and the weather forecast, sets new ranges, and writes plain-language advice for the farmer** in Arabic and English.
- Before that, an **onboarding assistant** talks with the farmer and designs the hardware and the agent network for that farm.

The demo farm is the plan's worked example: **two open-air soil beds of purple amaranth near Al Khor** (F1, F2).

```
 Sensor set (Nano) --radio--> MASTER (ESP32 + SD) --POST /log--> server.py (FastAPI) --> SQLite
                                    ^                                                     |
                                    +------------- GET /ranges (versioned) ---------------+
                                                                                          v
 every 6 h or "Run now":  SUMMARISER (code) -> code tools -> CrewAI departments -> FARM DIRECTOR -> CODE CHECK -> new version
                                                                                        '-> message + to-dos (AR/EN) -> dashboard
```

## Run it

```powershell
# CrewAI needs Python 3.10-3.13
python -m pip install uv; python -m uv venv --python 3.12 .venv
python -m uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\activate

ollama pull qwen2.5:3b          # departments (the fallback for llama3.2:3b, gemma2:2b, phi3.5 if not pulled)
ollama pull qwen2.5:7b          # Farm Director, translator and onboarding assistant

python seed_demo.py --reset     # the "heatwave day": 48 h of readings for F1 and F2 (plan M6)
python server.py                # farm computer: POST /log, GET /ranges, agent runs every 6 h
streamlit run app.py            # dashboard: Farm page and Onboarding page
python fake_master.py --check 20   # optional: a fake ESP32 master (type "dry F1" to pull the soil probe)
```

To run the agent network once in the terminal, use `python network.py`. It takes about 4–6 minutes on a laptop with local models; plan 5.3 suggests running it before the demo.

## One agent run (plan 3.4)

| Step | Where | What happens |
|---|---|---|
| 1. Trigger | `server.py`, `app.py` | Every 6 hours (00:00, 06:00, 12:00, 18:00), or **Run now** |
| 2. Summarise (code, no AI) | `summariser.py`, `weather.py` | For each field, over 24 h:<br>• now, min, max, avg and trend of every reading<br>• pump runs, and how fast the soil dries after 10:00<br>• yesterday's peak, the Open-Meteo forecast, the farmer's latest note |
| 3. Code tools | `agents/` | Each department's specialists count and check:<br>• readings against the ranges in force<br>• heat stress, leaf colour, pest rules<br>• trend, anomaly, 6 h projection, bed-to-bed comparison |
| 4. Departments (CrewAI) | `crew.py`, `network.py` | **Stage 1:** Agri-Environment, Soil & Water.<br>**Stage 2:** Crop Science (reads the stage 1 digest), Data & Analytics, Market & Strategy (extra).<br>Every report is fixed JSON: summary, warnings, to-dos, range proposals. |
| 5. Farm Director (CrewAI) | `crew.py` | Reads every report and writes one plan: **range changes**, a short message and to-dos. Then the Arabic translation. |
| 6. Code check | `checker.py` | Clamps every range inside the **hard limits** and flags anything clamped. Example: 400 s → 300 s. |
| 7. Save | `db.py` | A new version with `valid_from` = now + 5 min. The master picks it up with `GET /ranges`. |

**If any step fails, nothing changes:** the master keeps the last ranges, and the failed run is logged.

## The agent network for Al Khor

The network is in `farms/alkhor/network.json`, written as CrewAI agent definitions: `role`, `goal`, `backstory`, `llm`, `tools`.

| Department | Specialists (code tools) | Model | May change | Left out, and why |
|---|---|---|---|---|
| Agri-Environment | Climate, Light & Photosynthesis | llama3.2:3b | temp_air, humidity | Air quality: open-air beds, no sensor |
| Soil & Water | Irrigation, Nutrients & Fertilizer | gemma2:2b | soil_moisture, level, pump_seconds | Salinity: no EC sensor, so fertilizer advice only |
| Crop Science | Crop Physiology, Leaf Quality & Pigment, Plant Health | phi3.5 | none | |
| Data & Analytics | Trend, Anomaly, Forecast | qwen2.5:3b | none | |
| Market & Strategy *(extra, advice only)* | Market Price, Crop Suitability, Profitability, Web Research | qwen2.5:3b | never | |
| **Farm Director** | one agent over all departments | **qwen2.5:7b** | the plan | Crop Suggestion: the crop is already chosen |

That's 10 specialists and a Farm Director for the two beds. Models that aren't installed run on the route's fallback (`farms/model_routes.json`). The `hybrid` route puts only the Director and onboarding on a free cloud model (it needs `OPENROUTER_API_KEY` in `.env`).

## The farm files: what the onboarding assistant produces (plan 3.2)

| File | Contents |
|---|---|
| `farms/<id>/profile.json` | Farm profile: location, fields (size, type, crop, planted), water, fertilizer, power, internet, problems |
| `farms/<id>/hardware.json` | Hardware plan / installation record: sensor and actuator sets per field with `field_id`, `module_id` and `device_id` |
| `farms/<id>/limits.json` | Hard limits (never crossed) and starting ranges (version 1) |
| `farms/<id>/network.json` | The agent network: departments, CrewAI definitions, specialists, left-out roles |

`farms/active.json` names the farm the system runs; it defaults to `alkhor`. On the **Onboarding** page:
1. The farmer chats. The assistant asks follow-ups, looks up the location, forecast and past weather on Open-Meteo, and structures the answers.
2. It then fills the department template: it keeps what the sensors can feed, drops the rest with a reason, and writes each role around the farm's crop and problems.
3. The team reviews the four outputs, then presses **Team approves** to save them and make that farm the active one.

The "Say the plan's example line" button replays the conversation from plan 6.1, for rehearsals.

## Dashboard (`streamlit run app.py`)

**Farm page:**
- live readings and the latest rows from the master;
- the agent network diagram;
- **Run now**, with live progress;
- the Director's plan:
  - the message in English and Arabic, and the to-dos, each with **Approve / Correct** (stored in the approval log, the future fine-tuning data);
  - the ranges table: before, the Director's value, and the saved value, with clamped values flagged;
  - department reports, and the summariser's numbers;
- farmer notes, and Ask the farm (an extra).

**Onboarding page:** the chat, then the four outputs for review.

## Files

| File | Role |
|---|---|
| `server.py` | FastAPI: `POST /log`, `GET /ranges`, `GET /ranges/version`, `POST /run`, `GET /status`; the 6-hour schedule |
| `db.py` | SQLite: log rows (plan 2.4 format), versioned plans, runs, approvals, notes |
| `summariser.py` / `weather.py` | Code summaries per field; Open-Meteo forecast, geocoding and past weather (cached for offline) |
| `network.py` | One agent run, steps 1–7 |
| `crew.py` | CrewAI agents, tasks and the fixed JSON formats |
| `checker.py` | Hard-limit clamp and flags |
| `onboarding.py` | The onboarding assistant and the department template |
| `farm.py` | Loads and validates a farm's four files |
| `agents/` | Code tools per specialist: `monitors`, `soil`, `crop_science`, `analytics`, `strategy` (market), `diagnosis` (the team's farm-qwen, not used on Al Khor) |
| `knowledge.py` | Crop file: amaranth ranges, pest rules, growth stages, hard limits and starting ranges |
| `seed_demo.py` / `fake_master.py` | The demo's heatwave day, and a fake ESP32 master over HTTP |
| `app.py` / `assistant.py` | Dashboard; Ask the farm |
| `MASTER_API.md` | **For the firmware:** the HTTP API, the CSV format, the ranges file, an Arduino outline |

## Honest limits (MVP)

- The seeded data and the plan's numbers are illustrative, not from a real farm. The forecast is real: Open-Meteo for Al Khor.
- The small local models:
  - write rough English;
  - sometimes propose nothing, or something the code check has to reject;
  - may fail to translate cleanly; the Arabic is then left out rather than shown garbled.
  
  The code check and the fallbacks keep that safe. A larger Director, via the `hybrid` route or `qwen2.5:7b` or bigger, writes much better plans.
- One laptop, one active farm, and one master: the database doesn't separate farms' log rows.
- Specialists aren't fine-tuned in the MVP (plan 3.5). The Approve / Correct log is the start of that data.
