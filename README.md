# Hydro Monitor: an AI farm team for small farms in Qatar

Hydro Monitor is a sensing and control kit for small farms, looked after by a team of AI specialists built for each farm. This repository is the **agentic part**, the MVP for the Reboot the Earth hackathon. The full design is in [`docs/Hydro Monitor_ Project Plan (2).pdf`](docs/).

- **Sensors in every bed** report to a master node (ESP32). The master logs every reading and switches the pumps to keep conditions inside safe ranges, even when the laptop is off.
- **Every 6 hours**, a **CrewAI agent network** on the laptop reads the farm's data and the Open-Meteo forecast. It sets new ranges for the master, and tells the farmer, in Arabic or English, what's happening and what to do.
- **Before any of that**, an **onboarding assistant** talks with the farmer, looks up the location and the weather, and designs the sensors and the AI team for that farm. For a farmer who hasn't chosen a crop, it suggests crops that fit the season, their water and their goals.

```
 Sensor set (Nano) ──radio──► MASTER (ESP32 + SD) ──POST /log──► server.py (FastAPI) ──► SQLite (data/hydro.db)
                                     ▲                                                          │
                                     └──────────── GET /ranges (versioned, clamped) ◄───────────┤
                                                                                                ▼
 every 6 h or "Get fresh advice":  summariser (code) → code tools → CrewAI departments → Farm Director → code check
                                                                                              └─► farmer board (AR / EN)
```

## What the farmer gets (the "My farm" page)

- **How each bed is doing, in plain words:** 🟢 "Soil moisture is good", 🟠 "Drying: it will be watered soon", 🔴 "Dry: the tank is too low to water, refill it". Also shown: the crop, its day since planting and its expected harvest, and when the bed was last watered.
- **Today's advice** from the AI team. It's a short message plus a to-do list, for example:
  - shade cloth from 11:00 to 15:00;
  - check the drip emitters in bed 1;
  - send a photo of the pale leaves.
  
  Every item has ✅ **Done** and ✏️ **Correct**. Those answers are logged as future fine-tuning data.
- **What to plant, now and in the coming months:** for each crop, when it's ready, the money per harvest from *this* bed, the water need and the care level. For example: plant now mulukhiyah, okra or amaranth, which handle the heat; October snake cucumber; November zucchini; December radish.
- **Notes** ("tell us what you see"), which the AI team reads, and **Ask the farm assistant**, a chat in Arabic or English.
- **The whole board switches to Arabic** (right-to-left) with one click.

The **Developer** page has everything technical:
- live log rows;
- the agent-network diagram;
- runs with live progress;
- the Director's plan: ranges before, proposed and saved, with values clamped by the code check;
- every department report, and the summariser's numbers.

## Run it

```powershell
# one-time setup (CrewAI needs Python 3.10-3.13)
python -m pip install uv
python -m uv venv --python 3.12 .venv
python -m uv pip install --python .venv\Scripts\python.exe -r requirements.txt
copy .env.example .env          # add OPENROUTER_API_KEY (the default model route); optional TAVILY_API_KEY
.venv\Scripts\activate

python seed_demo.py --reset     # a 48-hour "heatwave day" for the demo farm (beds F1, F2)
python server.py                # farm computer: POST /log, GET /ranges, agent runs every 6 h
streamlit run app.py            # dashboard: My farm · Set up a farm · Developer
python fake_master.py --check 20   # optional: a fake ESP32 master (type "dry F1" to pull a soil probe)
```

- **Models:** OpenRouter is the default route (`farms/model_routes.json`, first entry). On a rate limit (HTTP 429), each task retries on the next free model in the fallback list.
- **Running offline:** pick `local` under *AI settings* in the sidebar, and install the models with `ollama pull qwen2.5:3b` and `ollama pull qwen2.5:7b`.
- **One run from the terminal:** `python network.py` (add `--route local` to use Ollama).

## The demo, in 5 minutes (plan 6.4)

1. **Set up a farm:** chat as the farmer, or press "Say the plan's example line". The assistant asks one clear question at a time, looks up the weather, and shows crop suggestions, the sensors, the hard limits and the AI team. The team approves.
2. **My farm:** the beds' status, the weather, what to plant. Switch to Arabic.
3. **Live hardware:** type `dry F1` in the fake master. The pump switches on, and the bed card and the log show it.
4. **Get fresh advice:** the AI team runs (about 3–5 minutes; run it before going on stage). You get today's message and to-dos. On the Developer page, the code check has clamped any unsafe range.
5. **Loop closed:** the master downloads the new version with `GET /ranges`.

## Documentation

| Document | For |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | **How the code works:** modules, one agent run step by step, onboarding, crop advice, **every table in `hydro.db`**, config files, how to extend |
| [MASTER_API.md](MASTER_API.md) | The firmware: the HTTP API, the CSV log format, the ranges file, an Arduino outline |
| [docs/flowchart.html](docs/flowchart.html) | Diagrams of the two parts, one agent run and onboarding (open it in a browser) |
| `docs/Hydro Monitor_ Project Plan (2).pdf` | The team's plan this code follows |

## Honest limits

- **The demo data is synthetic** (`seed_demo.py`). The forecast and the Qatar prices, yields and climate are real (Open-Meteo, Qatar Open Data).
- **Crop money is gross revenue** (yield × price, before costs). Most crops have no official farm-gate price, so they use Qatar's average vegetable value, and every price says so ("rough price guess").
- **Free cloud models** have daily limits, and small local models write rough English. Code checks everything they propose (hard limits, and a fixed format), and the farmer board never depends on a model to show the beds or crop suggestions.
- **One laptop, one active farm, one master.**
