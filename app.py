"""Streamlit demo: QU-ARS purple amaranth agent network.

Master device (farm state) -> network designed by the Agent Controller -> departments run -> Farm Director plan.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

import assistant
import llm
from controller import execute_network, to_dot
from knowledge import LABELS, fields_for
from planner import agent_count, load_network, models_used

ROOT = Path(__file__).parent
FARMS = ROOT / "farms"
INTERVAL_MIN = 15
ICON = {"OK": "🟢", "WARNING": "🟠", "CRITICAL": "🔴", "STABLE": "🟢", "ATTENTION REQUIRED": "🟠"}
LLM_MODES = {"Director only": "director", "Department heads + Director": "leaders", "Every agent": "all",
             "Off (rules only)": "off"}
ROUTES = llm.load_routes()

st.set_page_config(page_title="QU-ARS Agent Network", page_icon="🌱", layout="wide")
st.markdown(
    """<style>
    .kicker{font-family:monospace;color:#2d744d;font-size:.75rem;letter-spacing:.08em;text-transform:uppercase}
    .step{font-family:monospace;color:#7c8c82;font-size:.8rem;margin-top:1.6rem;border-top:1px solid #d9e3da;padding-top:.6rem}
    .model{font-family:monospace;font-size:.72rem;background:#eef3ee;border-radius:4px;padding:1px 6px;color:#2d744d}
    </style>""",
    unsafe_allow_html=True,
)


@st.cache_data
def load_farm() -> dict:
    return json.loads((FARMS / "quars_amaranth.json").read_text(encoding="utf-8"))


@st.cache_data
def load_readings(filename: str) -> pd.DataFrame:
    path = FARMS / filename
    if not path.exists():
        import generate_data

        generate_data.main()
    return pd.read_csv(path)


@st.cache_data(ttl=30)
def installed_models() -> list[str]:
    return llm.available_models()


def model_badge(info: dict | None) -> str:
    if not info:
        return ""
    assigned, used = info.get("assigned"), info.get("used")
    if used and used != assigned:
        return f'<span class="model">{assigned} → ran on {used}</span>'
    return f'<span class="model">{used or assigned}{" ✓" if used else ""}</span>'


farm = load_farm()
df = load_readings(farm["readings_file"])
spec = load_network(FARMS / farm["network_file"], farm)
models = installed_models()

# ── Sidebar ──────────────────────────────────────────────────────────────
st.sidebar.markdown("### 🌱 QU-ARS / Control")
timestamps = df["timestamp"].tolist()
ts = st.sidebar.select_slider("Master-device snapshot", timestamps, value=farm["demo_timestamp"])
window_h = st.sidebar.slider("Analysis window (hours)", 2, 24, 6)
st.sidebar.markdown("---")
llm_mode = LLM_MODES[st.sidebar.radio("Who writes in briefings", list(LLM_MODES),
                                      help="Rules always check the numbers every time; models only write during briefings and answer questions.")]
route_name = st.sidebar.selectbox("Where models run", list(ROUTES), help=" · ".join(f"{k}: {v['description']}" for k, v in ROUTES.items()))
route = ROUTES[route_name]
cloud_ready = any(llm.provider_ready(m) for m in [route["fallback"], *route["map"].values()])
fallback = route["fallback"]
if route_name == "local" and models:
    fallback = st.sidebar.selectbox("Fallback model (for assigned models not installed)", models)
elif not models and not cloud_ready:
    st.sidebar.caption("No Ollama and no cloud API key — rules-only mode.")
    llm_mode = "off"
if route_name != "local" and not cloud_ready:
    st.sidebar.warning("Set OPENROUTER_API_KEY (environment or arm/.env) to use this route.")
if llm.budget.usage():
    st.sidebar.caption("Cloud requests: " + ", ".join(f"{k} {v}" for k, v in llm.budget.usage().items()))
with st.sidebar.expander("Model roster"):
    for m in models_used(spec):
        target = route["map"].get(m, m)
        ready = llm.provider_ready(target) or target in models or f"{target}:latest" in models
        st.markdown(f"{'✅' if ready else '⬜'} `{m}`" + (f" → `{target}`" if target != m else ""))
    st.caption("Missing models run on the fallback. Install with `ollama pull <model>`.")

# ── Header ───────────────────────────────────────────────────────────────
st.markdown('<div class="kicker">Qatar University · Agricultural Research Station</div>', unsafe_allow_html=True)
st.title("Purple Amaranth Agent Network")
st.caption("A network of specialist AI departments designed by the Agent Controller. It reads farm state from the master device and returns one prioritised plan. Sensor data is synthetic, for demonstration only.")

# ── 1. Farm ──────────────────────────────────────────────────────────────
st.markdown('<div class="step">01 — FARM STATE (master device)</div>', unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns(4)
c1.metric("Crop", "Purple amaranth")
c2.metric("System", "Greenhouse · soil beds")
c3.metric("Area", f"{farm['size_hectares']} ha")
c4.metric("Edge devices", len(farm["edge_devices"]))
with st.expander(f"Device tree · {farm['master_device']['id']}"):
    for device in farm["edge_devices"]:
        st.markdown(f"**{device['id']}** — {device['zone']}  \n`" + "` `".join(fields_for(device["sensors"])) + "`")

# ── Run ──────────────────────────────────────────────────────────────────
i = timestamps.index(ts)
rows = df.to_dict("records")
history = rows[max(0, i - window_h * 60 // INTERVAL_MIN) : i + 1]
archive = rows[max(0, i - 2 * 24 * 60 // INTERVAL_MIN) : i + 1]
net_args = dict(llm_mode=llm_mode, installed_models=models, fallback_model=fallback,
                interval_minutes=INTERVAL_MIN, model_map=route["map"])
run_key = (ts, window_h)
if st.session_state.get("run_key") != run_key:   # rule checks: instant, every time the snapshot changes
    st.session_state["result"] = execute_network(spec, farm, history, archive, **{**net_args, "llm_mode": "off"})
    st.session_state["run_key"] = run_key
result = st.session_state["result"]
briefing = st.session_state.get("briefing") if st.session_state.get("briefing_key") == run_key else None

# ── 2. Network ───────────────────────────────────────────────────────────
st.markdown('<div class="step">02 — AGENT NETWORK DESIGNED BY THE CONTROLLER</div>', unsafe_allow_html=True)
left, right = st.columns([2.2, 1])
with left:
    st.graphviz_chart(to_dot(spec, result), width="stretch")
with right:
    n_specialists = sum(len(d["agents"]) for d in spec["departments"])
    a, b, c = st.columns(3)
    a.metric("Departments", len(spec["departments"]))
    b.metric("Agents", agent_count(spec), help=f"{n_specialists} specialists + {len(spec['departments'])} heads + director")
    c.metric("Models", len(models_used(spec)))
    st.markdown(f"**Designed by:** {spec['designed_by']} · `{spec['network_id']}`")
    st.info(spec["rationale"])
    for note in spec.get("notes", []):
        st.caption(f"⚙ {note}")
    st.download_button("⬇ Network spec (JSON)", json.dumps(spec, indent=2, ensure_ascii=False), f"{spec['network_id']}.json", "application/json")

# ── 3. Departments ───────────────────────────────────────────────────────
st.markdown(f'<div class="step">03 — DEPARTMENT REPORTS · snapshot {ts}</div>', unsafe_allow_html=True)
shown = briefing or result
depts = [d for d in spec["departments"] if d["id"] in shown["reports"]]
tabs = st.tabs([f"{ICON.get(shown['reports'][d['id']]['status'], '')} {d['name']}" for d in depts])
for tab, dept in zip(tabs, depts):
    report = shown["reports"][dept["id"]]
    with tab:
        st.markdown(f"**★ {dept['head']['name']}** {model_badge(report.get('model'))}  \n*Mandate: {dept['mandate']}*", unsafe_allow_html=True)
        st.write(report["summary"])
        if dept["depends_on"]:
            upstream = ", ".join(d["name"] for d in spec["departments"] if d["id"] in dept["depends_on"])
            st.caption(f"Reads reports from: {upstream}")
        cols = st.columns(len(dept["agents"]))
        for col, agent in zip(cols, dept["agents"]):
            f = shown["findings"][agent["id"]]
            with col.container(border=True):
                st.markdown(f"{ICON.get(f['status'], '')} **{agent['name']}**  \n{model_badge(f.get('model'))}", unsafe_allow_html=True)
                st.caption(agent["purpose"])
                st.write(f["summary"])
                if f.get("model", {}).get("used"):
                    with st.expander("Verified facts"):
                        st.write(f["facts"])

# ── 4. Director ──────────────────────────────────────────────────────────
assessment = result["assessment"]
st.markdown('<div class="step">04 — FARM DIRECTOR · ACTION PLAN FOR THE MASTER DEVICE</div>', unsafe_allow_html=True)
a, b = st.columns([1, 1.5])
with a:
    st.subheader(f"{ICON.get(assessment['status'], '')} {assessment['status']}")
    st.markdown(model_badge(assessment.get("model")), unsafe_allow_html=True)
    st.write(assessment["summary"])
    if assessment["watch"]:
        with st.expander(f"Early warnings & trends ({len(assessment['watch'])})", expanded=True):
            for item in assessment["watch"]:
                st.markdown(f"- {item}")
with b:
    if assessment["plan"]:
        st.dataframe(pd.DataFrame(assessment["plan"]), hide_index=True, width="stretch")
    else:
        st.success("No actions required — all departments report normal conditions.")
    st.download_button("⬇ Action plan (JSON)", json.dumps(assessment, indent=2, ensure_ascii=False), "action_plan.json", "application/json")
    with st.expander("Execution trace (LangGraph)"):
        st.dataframe(pd.DataFrame(result["trace"]), hide_index=True, width="stretch")

# ── 5. Briefing & insights ───────────────────────────────────────────────
st.markdown('<div class="step">05 — FARM BRIEFING & INSIGHTS (twice a day or on request)</div>', unsafe_allow_html=True)
st.caption("On the live system briefings run at 06:00 and 18:00, or when the farmer asks. They add the Market & Strategy "
           "department: Qatar prices, crop suitability by season, profitability and web research.")
if st.button("▶ Run briefing now", type="primary"):
    with st.spinner("Briefing: departments, market research and the Director's summary…"):
        st.session_state["briefing"] = execute_network(spec, farm, history, archive, purpose="briefing", **net_args)
        st.session_state["briefing_key"] = run_key
    st.rerun()

if briefing:
    ba = briefing["assessment"]
    st.markdown(f"**Farm Director** {model_badge(ba.get('model'))}", unsafe_allow_html=True)
    st.info(ba["summary"])
    if ba["insights"]:
        cards = st.columns(len(ba["insights"]))
        for card, item in zip(cards, ba["insights"]):
            with card.container(border=True):
                gain = f" · {item['gain_pct']:+d}%" if item["gain_pct"] else ""
                st.markdown(f"**{item['title']}**{gain}")
                st.write(item["detail"])
                st.caption(f"{item['type']} · price confidence: {item['confidence']}")
    f = briefing["findings"]
    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("**Gross revenue estimate by crop** (QR per m² per year, costs not included)")
        ranking = pd.DataFrame(f["profit_agent"]["ranking"]).rename(columns={
            "name": "Crop", "annual_qr_m2": "QR/m²/yr", "price": "QR/kg", "price_basis": "Price basis",
            "confidence": "Confidence", "window": "Fits"})
        st.dataframe(ranking, hide_index=True, width="stretch")
    with right:
        st.markdown("**Season fit in this greenhouse** (✓ ideal · ~ tolerated · blank = too hot or cold)")
        grid = pd.DataFrame({r["name"]: {m: {1.0: "✓", 0.5: "~"}.get(v, "") for m, v in r["fits"].items()}
                             for r in f["crop_fit_agent"]["table"]}).T
        st.dataframe(grid, width="stretch")
    with st.expander("Market data and research sources"):
        st.write(f["market_agent"]["summary"])
        st.caption(f"Official data fetched {f['market_agent'].get('data_age', '?')} · prices editable in farms/market_prices.json")
        st.write(f["research_agent"]["summary"])
        for note in ba.get("research", []):
            for src in note["sources"][:3]:
                st.markdown(f"- [{src['title'] or src['url']}]({src['url']})")
else:
    st.caption("No briefing for this snapshot yet. Press **Run briefing now**.")

# ── 6. Ask the farm ──────────────────────────────────────────────────────
st.markdown('<div class="step">06 — ASK THE FARM</div>', unsafe_allow_html=True)
chat = st.session_state.setdefault("chat", [])
for turn in chat:
    with st.chat_message(turn["role"]):
        st.write(turn["text"])
        if turn.get("by"):
            st.caption(f"answered by {turn['by']}" + (" · sources: " + ", ".join(turn["sources"]) if turn.get("sources") else ""))
question = st.chat_input("Ask about your crop, the readings, prices or what to grow next…")
if question:
    chat.append({"role": "user", "text": question})
    with st.spinner("Thinking…"):
        context = briefing or execute_network(spec, farm, history, archive, purpose="briefing", **{**net_args, "llm_mode": "off"})
        reply = assistant.answer(question, farm, result, context,
                                 {**net_args, "assistant_model": spec["director"].get("model")})
    chat.append({"role": "assistant", "text": reply["text"], "by": reply["by"], "sources": reply["sources"]})
    st.rerun()

# ── 7. Sensor data ───────────────────────────────────────────────────────
st.markdown('<div class="step">07 — SENSOR HISTORY SEEN BY THE NETWORK</div>', unsafe_allow_html=True)
window = pd.DataFrame(history).set_index("timestamp")
fields = [c for c in window.columns if c in LABELS]
chart_cols = st.columns(4)
for n, field in enumerate(fields):
    with chart_cols[n % 4]:
        st.caption(LABELS[field])
        st.line_chart(window[field], height=130)
