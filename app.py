"""Hydro Monitor dashboard (plan 5.2: Streamlit on the laptop, reading SQLite).

    Farm page        live readings, the agent network, "Run now", the Director's plan (ranges, clamped values,
                     the farmer's message in Arabic and English, to-dos with Approve / Correct), farmer notes
    Onboarding page  the onboarding assistant: a chat that produces the farm profile, hardware plan, hard limits
                     and agent network for the team to approve

    streamlit run app.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import assistant
import db
import farm as farms
import llm
import network
import onboarding
from checker import KIND_LABEL, settings_for
from knowledge import KIND_UNIT

st.set_page_config(page_title="Hydro Monitor", page_icon="🌱", layout="wide")
st.markdown(
    """<style>
    .kicker{font-family:monospace;color:#2d744d;font-size:.75rem;letter-spacing:.08em;text-transform:uppercase}
    .step{font-family:monospace;color:#7c8c82;font-size:.8rem;margin-top:1.4rem;border-top:1px solid #d9e3da;padding-top:.6rem}
    .model{font-family:monospace;font-size:.72rem;background:#eef3ee;border-radius:4px;padding:1px 6px;color:#2d744d}
    .rtl{direction:rtl;text-align:right;font-size:1.05rem;line-height:1.8}
    .flag{background:rgba(224,138,0,.14);color:inherit;border-left:3px solid #e08a00;padding:.35rem .7rem;margin:.25rem 0;border-radius:3px}
    </style>""",
    unsafe_allow_html=True,
)
ICON = {"OK": "🟢", "WARNING": "🟠", "CRITICAL": "🔴"}
SHOW_UNIT = {**KIND_UNIT, "temp_air": "°C"}   # the log format writes "C"
ROUTES = llm.load_routes()


@st.cache_resource
def connection():
    return db.connect()


@st.cache_data(ttl=30)
def installed_models() -> list[str]:
    return llm.available_models()


def sidebar() -> dict:
    st.sidebar.markdown("### 🌱 Hydro Monitor")
    name = st.sidebar.selectbox("Where models run", list(ROUTES), key="route",
                                help=" · ".join(f"{k}: {v['description']}" for k, v in ROUTES.items()))
    route = ROUTES[name]
    models = installed_models()
    st.sidebar.caption("Ollama models: " + (", ".join(models) or "none (start Ollama)"))
    if name != "local" and not any(llm.provider_ready(m) for m in [route["fallback"], *route["map"].values()]):
        st.sidebar.warning("Set OPENROUTER_API_KEY in arm/.env to use this route.")
    return {"name": name, **route}


# ── Farm page ────────────────────────────────────────────────────────────
def network_dot(net: dict) -> str:
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; nodesep=0.2; ranksep=0.45;',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=10 fillcolor="#ffffff" color="#2d744d"];',
             'edge [color="#8a978f" arrowsize=0.6];',
             'db [label="Database + forecast" shape=cylinder fillcolor="#e8eef9"];',
             'sum [label="Summariser\\n(code, no AI)" fillcolor="#eef3ee"];', "db -> sum;"]
    for d in net["departments"] + net.get("extras", []):
        roles = "\\n".join(s["name"] for s in d["specialists"])
        style = ' style="rounded,dashed,filled"' if d.get("advice_only") else ""
        lines.append(f'{d["id"]} [label="{d["name"].upper()}\\n{roles}\\n({d["agent"]["llm"].replace("ollama/", "")})"{style}];')
        lines.append(f'sum -> {d["id"]};' if not d["depends_on"] else "")
        for u in d["depends_on"]:
            lines.append(f"{u} -> {d['id']};")
        lines.append(f'{d["id"]} -> director{" [style=dashed]" if d.get("advice_only") else ""};')
    llm_name = net["director"]["agent"]["llm"].replace("ollama/", "")
    lines += [f'director [label="FARM DIRECTOR\\n{llm_name}\\none plan" fillcolor="#f8e4ee" color="#a3195b"];',
              'check [label="Code check\\nhard limits" fillcolor="#eef3ee"];', 'master [label="Master\\nGET /ranges" fillcolor="#e3f0e7"];',
              'dash [label="Message + to-dos\\n(AR · EN)" fillcolor="#e3f0e7"];',
              "director -> check -> master; director -> dash; }"]
    return "\n".join(lines)


def live_section(con, farm: dict, plan: dict | None) -> None:
    kinds = farms.field_kinds(farm["hardware"])
    since = (datetime.now() - timedelta(hours=24)).timestamp()
    rows = [r for r in db.read_log(con, since) if r["type"] == "reading"]
    if not rows:
        st.info("No readings yet. Start the master (or `python fake_master.py`), or seed the demo with `python seed_demo.py`.")
        return
    df = pd.DataFrame(rows)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["time"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(farms.tz_of(farm))
    last = df.sort_values("ts").groupby(["field_id", "kind"]).tail(1).set_index(["field_id", "kind"])["value"]
    cols = st.columns(len(kinds))
    for col, (field, field_kinds) in zip(cols, kinds.items()):
        with col.container(border=True):
            st.markdown(f"**{field}** · {farms.field_info(farm, field).get('stage', '')}")
            inner = st.columns(len(field_kinds))
            for c, kind in zip(inner, field_kinds):
                value = last.get((field, kind))
                band = (plan["ranges"].get(field, {}).get(kind) if plan else None)
                label = KIND_LABEL.get(kind, (kind, ""))[0].capitalize()
                c.metric(label, "–" if value is None else f"{value:,.0f} {SHOW_UNIT.get(kind, '')}" if kind == "light"
                         else f"{value:.1f} {SHOW_UNIT.get(kind, '')}", help=f"range in force: {band}" if band else None)
    left, right = st.columns([1.4, 1])
    with left:
        pick = st.radio("Chart", ["soil_moisture", "temp_air", "humidity", "light", "level"], horizontal=True,
                        format_func=lambda k: KIND_LABEL.get(k, (k,))[0].capitalize(), label_visibility="collapsed")
        chart = df[df["kind"] == pick].copy()
        if not chart.empty:
            chart["t"] = chart["time"].dt.floor("5min")
            st.line_chart(chart.pivot_table(index="t", columns="field_id", values="value"), height=230)
    with right:
        st.caption("Latest rows from the master (plan 2.4 log format)")
        st.dataframe(pd.DataFrame(db.latest_rows(con, 14)), hide_index=True, height=260, width="stretch")


def show(value) -> str:
    """[24.0, 32.0] -> '24–32', 120 -> '120 s'."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "–".join(f"{float(v):g}" if v is not None else "?" for v in value)
    return f"{float(value):g} s" if isinstance(value, (int, float)) else str(value)


def plan_section(con, farm: dict, plan: dict) -> None:
    history = db.plan_history(con, farm["id"], 2)
    before = history[1]["ranges"] if len(history) > 1 and history[0]["version"] == plan["version"] else None
    st.markdown(f"**Version {plan['version']}** · valid from {plan['valid_from']} · made by "
                f'<span class="model">{plan["made_by"]}</span> · trigger {plan["trigger"]}', unsafe_allow_html=True)
    decisions = db.approvals(con, plan["version"])

    def review(item: str, text: str, key: str) -> None:
        c1, c2, c3 = st.columns([6, 1, 1.3])
        done = decisions.get(item)
        c1.markdown(text + (f"  \n:green[✓ approved]" if done and done["decision"] == "approve" else
                            f"  \n:orange[✎ corrected: {done['correction']}]" if done else ""), unsafe_allow_html=True)
        if c2.button("Approve", key=f"ap{key}"):
            db.add_approval(con, plan["version"], item, "approve")
            st.rerun()
        with c3.popover("Correct"):
            fix = st.text_input("What should it say or do?", key=f"fx{key}")
            if st.button("Save", key=f"sv{key}") and fix:
                db.add_approval(con, plan["version"], item, "correct", fix)
                st.rerun()

    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("**Message to the farmer**")
        review("message", plan["message_en"] or "", "msg")
        if plan.get("message_ar"):
            st.markdown(f'<div class="rtl">{plan["message_ar"]}</div>', unsafe_allow_html=True)
        else:
            st.caption("Arabic translation unavailable for this version.")
        st.markdown("**To-dos**")
        for i, todo in enumerate(plan.get("todos") or []):
            ar = (plan.get("todos_ar") or [None] * 10)[i] if plan.get("todos_ar") and i < len(plan["todos_ar"]) else None
            review(f"todo:{i + 1}", f"**{i + 1}.** {todo}" + (f'<div class="rtl">{ar}</div>' if ar else ""), f"td{i}")
    with right:
        st.markdown("**Ranges for the master** (after the code check)")
        table = []
        flags = {(f["field"], f["setting"]): f for f in plan.get("flags") or []}
        for field, values in plan["ranges"].items():
            if not isinstance(values, dict):
                continue
            for setting, value in values.items():
                old = (before or {}).get(field, {}).get(setting)
                prop = (plan.get("proposed") or {}).get(field, {}).get(setting)
                flag = flags.get((field, setting))
                table.append({"field": field, "setting": setting, "before": show(old),
                              "Director": show(prop), "saved": show(value),
                              "check": ("⚠ " + flag["type"]) if flag else ("changed" if old is not None and old != value else "")})
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        for f in plan.get("flags") or []:
            st.markdown(f'<div class="flag">⚠ <b>{f["field"]} {f["setting"]}</b>: {f["why"]} → saved {show(f["used"])}</div>',
                        unsafe_allow_html=True)
        changes = (plan.get("reports") or {}).get("_changes") or []
        if changes:
            with st.expander(f"The Director's reasons ({len(changes)} change(s))"):
                for c in changes:
                    st.markdown(f"- **{c['field_id']} {c['setting']}**: {c['reason']}")

    reports = {k: v for k, v in (plan.get("reports") or {}).items() if not k.startswith("_")}
    if reports:
        st.markdown("**Department reports**")
        tabs = st.tabs([f"{ICON.get(r.get('status'), '')} {r['name']}{' (extra)' if r.get('advice_only') else ''}" for r in reports.values()])
        for tab, r in zip(tabs, reports.values()):
            with tab:
                st.markdown(f'<span class="model">{r.get("by")}</span>', unsafe_allow_html=True)
                st.write(r["summary"])
                a, b, c = st.columns(3)
                a.markdown("**Warnings**\n" + "\n".join(f"- {w}" for w in r.get("warnings", [])) if r.get("warnings") else "")
                b.markdown("**To-dos**\n" + "\n".join(f"- {t}" for t in r.get("todos", [])) if r.get("todos") else "")
                props = [f"- {p['field_id']} {p['kind']} {p['min']:g}–{p['max']:g}: {p['reason']}" for p in r.get("ranges", [])]
                props += [f"- {p['field_id']} pump {p['seconds']} s: {p['reason']}" for p in r.get("pump_seconds", [])]
                c.markdown("**Proposes**\n" + "\n".join(props) if props else "")
    if plan.get("advice"):
        st.markdown("**Market & Strategy advice** (extra, never changes a range)")
        cards = st.columns(len(plan["advice"]))
        for card, item in zip(cards, plan["advice"]):
            with card.container(border=True):
                st.markdown(f"**{item['title']}**")
                st.write(item["detail"])
                st.caption(f"price confidence: {item['confidence']}")
    if plan.get("summaries"):
        with st.expander("What the summariser gave the departments (code, no AI)"):
            st.json(plan["summaries"], expanded=False)


def farm_page() -> None:
    route = sidebar()
    con = connection()
    farm = farms.load_farm()
    net = farm["network"]
    plan = db.latest_plan(con, farm["id"])
    profile = farm["profile"]
    auto = st.sidebar.toggle("Live refresh (10 s)", value=False)

    st.markdown('<div class="kicker">Hydro Monitor · Reboot the Earth · Doha</div>', unsafe_allow_html=True)
    st.title(profile["name"])
    st.caption(f"{profile['location']} · {profile['crop']} · " + ", ".join(
        f"{f['field_id']} {f['type']} {f['size_m'][0]:g}×{f['size_m'][1]:g} m" for f in profile["fields"]))
    runs = db.runs(con, 5)
    a, b, c, d = st.columns(4)
    a.metric("Ranges in force", f"v{plan['version']}" if plan else "none", help=plan["valid_from"] if plan else None)
    if runs:
        b.metric("Last agent run", runs[0]["status"], help=f"{runs[0]['trigger']} · {runs[0]['started_at']}")
    b.caption("Runs every 6 h (server.py) or on Run now")
    c.metric("Specialists", farms.specialist_count(net), help=f"+ {farms.specialist_count(net, True) - farms.specialist_count(net)} in the extras")
    d.metric("Rows logged", f"{con.execute('SELECT COUNT(*) FROM log').fetchone()[0]:,}")

    st.markdown('<div class="step">01 · LIVE READINGS FROM THE MASTER</div>', unsafe_allow_html=True)
    if auto:
        st.fragment(run_every=10)(live_section)(con, farm, plan)
    else:
        live_section(con, farm, plan)

    st.markdown('<div class="step">02 · THE FARM AGENT NETWORK (CrewAI)</div>', unsafe_allow_html=True)
    left, right = st.columns([2, 1])
    left.graphviz_chart(network_dot(net), width="stretch")
    with right:
        st.info(net.get("summary", ""))
        for d in net["departments"] + net.get("extras", []):
            with st.expander(f"{d['name']} · {d['agent']['llm'].replace('ollama/', '')}"):
                st.markdown(f"**Role:** {d['agent']['role']}  \n**Goal:** {d['agent']['goal']}")
                st.caption(d["agent"]["backstory"])
                st.markdown("**Specialists:** " + ", ".join(s["name"] for s in d["specialists"]))
                if d.get("sets"):
                    st.markdown("**May change:** " + ", ".join(d["sets"]))
                for lo in d.get("left_out", []):
                    st.markdown(f"~~{lo['role']}~~: {lo['why']}")
        st.download_button("⬇ CrewAI agent definitions (network.json)", json.dumps(farm["network"], indent=2, ensure_ascii=False),
                           "network.json", "application/json")

    st.markdown('<div class="step">03 · RUN THE AGENTS</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1, 3])
    if c1.button("▶ Run now", type="primary"):
        with c2.status("Agent run", expanded=True) as status:
            result = network.run(con, trigger="button", route=route["name"], progress=status.write)
            status.update(label=f"Agent run: {result['status']}" + (f" · v{result['plan']['version']}" if result["status"] == "ok" else ""),
                          state="complete" if result["status"] == "ok" else "error")
            if result["status"] != "ok":
                st.error(result["error"])
        st.rerun()
    if runs:
        c2.dataframe(pd.DataFrame(runs)[["id", "trigger", "started_at", "status", "version", "error"]], hide_index=True, width="stretch")

    st.markdown('<div class="step">04 · THE FARM DIRECTOR\'S PLAN</div>', unsafe_allow_html=True)
    if plan and plan.get("reports"):
        plan_section(con, farm, plan)
    elif plan:
        st.caption(f"Version {plan['version']}: {plan.get('message_en') or 'starting ranges'}. Press Run now for the first agent plan.")
        st.json(plan["ranges"])

    st.markdown('<div class="step">05 · FARMER NOTES</div>', unsafe_allow_html=True)
    n1, n2 = st.columns([1, 2])
    with n1.form("note", clear_on_submit=True):
        field = st.selectbox("Field", [f["field_id"] for f in profile["fields"]])
        text = st.text_input("Note", placeholder="e.g. leaves on the west edge look pale")
        if st.form_submit_button("Add note") and text:
            db.add_note(con, field, text)
            st.rerun()
    notes = db.notes(con, limit=6)
    n2.dataframe(pd.DataFrame(notes)[["field_id", "text", "created_at"]] if notes else pd.DataFrame(), hide_index=True, width="stretch")

    st.markdown('<div class="step">06 · ASK THE FARM (extra)</div>', unsafe_allow_html=True)
    chat = st.session_state.setdefault("chat", [])
    for turn in chat:
        with st.chat_message(turn["role"]):
            st.write(turn["text"])
            if turn.get("by"):
                st.caption(f"answered by {turn['by']}" + (" · " + ", ".join(turn["sources"]) if turn.get("sources") else ""))
    question = st.chat_input("Ask about the beds, the plan, prices or what to grow…")
    if question:
        chat.append({"role": "user", "text": question})
        with st.spinner("Thinking…"):
            reply = assistant.answer(question, farm, plan, route)
        chat.append({"role": "assistant", "text": reply["text"], "by": reply["by"], "sources": reply["sources"]})
        st.rerun()


# ── Onboarding page ──────────────────────────────────────────────────────
EXAMPLE = [   # the plan's worked example (6.1), for rehearsing the demo
    "Near Al Khor, north of Doha.",
    "Purple amaranth, in two soil beds, each about 10 by 20 metres. I water with a hose from a tank.",
    "Open air. Sometimes I put up shade cloth.",
    "About three weeks ago. Granular NPK once a month.",
    "Electricity in the shed, about 30 metres away. Mobile data works.",
    "In the heat the leaves go pale and burn at the edges, and I never know if I'm watering too much.",
]


def onboarding_page() -> None:
    route = sidebar()
    model = onboarding.onboarding_model(route)
    s = st.session_state
    if "ob_history" not in s or st.sidebar.button("Start over"):
        s.ob_history = [{"role": "assistant", "text": "Hello! I'll help set up the system for your farm. Where is it?"}]
        s.ob_profile, s.ob_done, s.ob_parts = onboarding.empty_profile(), False, None

    st.markdown('<div class="kicker">Hydro Monitor · Onboarding assistant</div>', unsafe_allow_html=True)
    st.title("Tell us about your farm")
    st.caption(f"The farmer describes the farm in their own words; the assistant ({model}) asks follow-ups and does the "
               "structuring. It looks up the location and weather (Open-Meteo), and designs the hardware and the agent network. "
               "A person on the team reviews everything before it is installed or run.")

    for turn in s.ob_history:
        if turn["role"] == "tool":
            st.caption(f"🔎 (tools) {turn['text']}")
        else:
            with st.chat_message("user" if turn["role"] == "farmer" else "assistant"):
                st.write(turn["text"])

    def say(text: str) -> None:
        s.ob_history.append({"role": "farmer", "text": text})
        with st.spinner("The assistant is thinking…"):
            out = onboarding.step(s.ob_history, s.ob_profile, model)
        s.ob_profile = out["profile"]
        if out["tool"]:
            s.ob_history.append({"role": "tool", "text": f"Looked up {out['tool']}"})
        s.ob_history.append({"role": "assistant", "text": out["reply"]})
        if out["done"] and not s.ob_done:
            s.ob_done = True
            with st.spinner("Designing the hardware and the agent network…"):
                farm_id = onboarding.slug(s.ob_profile.get("place") or s.ob_profile["location"])
                s.ob_parts = onboarding.design(s.ob_profile, farm_id, model)
            s.ob_history.append({"role": "assistant", "text": onboarding.proposal_text(s.ob_parts)})

    if not s.ob_done:
        farmer_turns = sum(1 for t in s.ob_history if t["role"] == "farmer")
        if farmer_turns < len(EXAMPLE) and st.button(f"Say the plan's example line {farmer_turns + 1}/{len(EXAMPLE)}",
                                                     help=EXAMPLE[farmer_turns]):
            say(EXAMPLE[farmer_turns])
            st.rerun()
        text = st.chat_input("Describe your farm…")
        if text:
            say(text)
            st.rerun()
        with st.sidebar.expander("Profile so far"):
            st.json(s.ob_profile)
            st.caption("Still missing: " + (", ".join(onboarding.missing(s.ob_profile)) or "nothing"))
        return

    parts = s.ob_parts
    st.markdown('<div class="step">WHAT THE ASSISTANT PRODUCED · FOR TEAM REVIEW</div>', unsafe_allow_html=True)
    t1, t2, t3, t4 = st.tabs(["1 · Farm profile", "2 · Hardware plan", "3 · Hard limits", "4 · Agent network"])
    with t1:
        st.json(parts["profile"])
    with t2:
        st.dataframe(pd.DataFrame([{
            "field": f["field_id"], "sensor set": f["sensor_set"]["module_id"],
            "sensors": ", ".join(d["device_id"] for d in f["sensor_set"]["devices"]),
            "actuator set": f["actuator_set"]["module_id"],
            "actuators": ", ".join(f"{d['what']} ({d['device_id']})" for d in f["actuator_set"]["devices"])}
            for f in parts["hardware"]["fields"]]), hide_index=True, width="stretch")
        st.caption(parts["hardware"]["notes"])
    with t3:
        lim = parts["limits"]
        st.dataframe(pd.DataFrame([{"setting": k, "hard limit": f"{v[0]:g}–{v[1]:g}",
                                    "starting value": str(next(iter(lim["start"].values())).get(k, "")),
                                    "note": lim["notes"].get(k, "")} for k, v in lim["hard"].items()]),
                     hide_index=True, width="stretch")
    with t4:
        net = parts["network"]
        st.info(net["summary"])
        st.dataframe(pd.DataFrame([{"department": d["name"], "roles created": ", ".join(sp["name"] for sp in d["specialists"]),
                                    "left out, and why": "; ".join(f"{lo['role']}: {lo['why']}" for lo in d["left_out"]),
                                    "model": d["agent"]["llm"]} for d in net["departments"] + net["extras"]]
                                  + [{"department": "Farm Director", "roles created": "One", "model": net["director"]["agent"]["llm"],
                                      "left out, and why": "" if net["extras"][0]["name"] != "Market & Strategy" else "Crop Suggestion: the crop is already chosen"}]),
                     hide_index=True, width="stretch")
        with st.expander("CrewAI agent definitions"):
            st.json([d["agent"] for d in net["departments"]], expanded=False)

    farm_id = st.text_input("Farm id (folder name)", parts["profile"]["farm_id"])
    exists = (farms.FARMS / farm_id / "profile.json").exists()
    if exists:
        st.warning(f"farms/{farm_id} already exists: approving replaces it.")
    if st.button("✅ Team approves: save and go live", type="primary"):
        for part in parts.values():
            part["farm_id"] = farm_id
        parts["network"]["designed_by"] = "onboarding assistant, approved by the team"
        farms.save_farm(farm_id, parts)
        farms.set_active(farm_id)
        con = connection()
        db.save_plan(con, farm_id, farms.local_now(farms.load_farm(farm_id)).isoformat(timespec="seconds"),
                     parts["limits"]["start"], made_by="onboarding (starting ranges)", trigger="onboarding",
                     message_en="Starting ranges from onboarding.", todos=[], flags=[])
        st.success(f"Saved farms/{farm_id} and made it the active farm. The master can fetch the starting ranges now; "
                   "open the Farm page and press Run now for the first agent run.")


st.navigation([st.Page(farm_page, title="Farm", icon="🌱", default=True),
               st.Page(onboarding_page, title="Onboarding", icon="💬")]).run()
