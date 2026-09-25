"""Hydro Monitor dashboard (plan 5.2: Streamlit on the laptop, reading SQLite). Three pages:

    My farm          the farmer board, in Arabic or English: how each bed is doing in plain words, today's advice
                     and to-dos (Done / Correct), what to plant now and in the coming months, notes, and a chat
    Set up a farm    the onboarding assistant: a chat that produces the farm profile, hardware plan, hard limits,
                     agent network and crop suggestions for the team to approve
    Developer        everything technical: live rows, the agent network, Run now with progress, the Director's plan
                     (ranges before / proposed / saved, clamped values), department reports, the summariser's numbers

    streamlit run app.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import assistant
import crop_advice
import db
import farm as farms
import farmer_view as fv
import llm
import network
import onboarding
import weather
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
    .bed{border-radius:14px;padding:1rem 1.1rem;margin-bottom:.6rem;border:1px solid rgba(128,128,128,.25)}
    .bed.ok{background:rgba(46,160,90,.12);border-left:6px solid #2ea05a}
    .bed.watch{background:rgba(224,138,0,.13);border-left:6px solid #e08a00}
    .bed.act{background:rgba(215,60,60,.13);border-left:6px solid #d73c3c}
    .bed.none{background:rgba(128,128,128,.10);border-left:6px solid #888}
    .bed h3{margin:.1rem 0 .3rem 0;font-size:1.25rem}
    .bed .big{font-size:1.15rem;font-weight:600;margin:.2rem 0}
    .bed .facts{opacity:.85;font-size:.95rem}
    .advice{font-size:1.2rem;line-height:1.7;padding:.8rem 1rem;border-radius:12px;background:rgba(46,120,200,.10)}
    .crop{border-radius:12px;padding:.8rem 1rem;border:1px solid rgba(128,128,128,.25);height:100%}
    .crop .name{font-size:1.2rem;font-weight:700}
    .crop .money{font-size:1.1rem;font-weight:600;color:#2ea05a}
    .ar, .ar *{direction:rtl;text-align:right}
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


def sidebar(show_models: bool = True) -> dict:
    """The model route (OpenRouter first, the default). Farmers never need to touch it."""
    st.sidebar.markdown("### 🌱 Hydro Monitor")
    box = st.sidebar.expander("AI settings", expanded=show_models) if not show_models else st.sidebar
    name = box.selectbox("Where models run", list(ROUTES), key="route",
                         help=" · ".join(f"{k}: {v['description']}" for k, v in ROUTES.items()))
    route = ROUTES[name]
    if show_models:
        models = installed_models()
        box.caption("Ollama models: " + (", ".join(models) or "none (start Ollama)"))
    if name != "local" and not any(llm.provider_ready(m) for m in [route["fallback"], *route["map"].values()]):
        box.warning("Set OPENROUTER_API_KEY in arm/.env to use this route.")
    if llm.budget.usage():
        box.caption("Cloud requests: " + ", ".join(f"{k} {v}" for k, v in llm.budget.usage().items()))
    return {"name": name, **route}


def language() -> bool:
    """True for Arabic. Remembered across pages."""
    choice = st.sidebar.radio("Language · اللغة", ["English", "العربية"], key="lang", horizontal=True)
    return choice == "العربية"


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
    tips = fv.market_tips(plan)
    if tips:
        st.markdown("**Market & Strategy advice** (extra, never changes a range)")
        cards = st.columns(len(tips))
        for card, item in zip(cards, tips):
            with card.container(border=True):
                st.markdown(f"**{item['title']}**")
                st.write(item["detail"])
                st.caption(f"price confidence: {item['confidence']}")
    if plan.get("summaries"):
        with st.expander("What the summariser gave the departments (code, no AI)"):
            st.json(plan["summaries"], expanded=False)


def dev_page() -> None:
    route = sidebar()
    con = connection()
    farm = farms.load_farm()
    net = farm["network"]
    plan = db.latest_plan(con, farm["id"])
    profile = farm["profile"]
    auto = st.sidebar.toggle("Live refresh (10 s)", value=False)

    st.markdown('<div class="kicker">Hydro Monitor · Developer board</div>', unsafe_allow_html=True)
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


# ── Farmer board ─────────────────────────────────────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)
def forecast_today(lat: float, lon: float) -> dict | None:
    from datetime import date

    return weather.day_summary(weather.forecast(lat, lon), date.today())


@st.cache_data(ttl=3600, show_spinner=False)
def crops_for(farm_id: str) -> dict:
    return crop_advice.suggest(farms.load_farm(farm_id))


def _html(text: str, ar: bool, cls: str = "") -> None:
    st.markdown(f'<div class="{cls} {"ar" if ar else ""}">{text}</div>', unsafe_allow_html=True)


WATER_ICON = {"low": "💧", "medium": "💧💧", "high": "💧💧💧"}


def crop_card(r: dict, ar: bool, area: int) -> None:
    name = r["ar"] if ar else r["name"]
    money = f"≈ {r['qr_per_harvest']:,} QR" if area else f"≈ {r['qr_m2']} QR/m²"
    reasons = r.get("reasons_ar" if ar else "reasons") or r["reasons"]
    conf = fv.t(r["confidence"], ar) if r["confidence"] in ("official", "estimate", "proxy", "farmer") else r["confidence"]
    _html(f'<div class="name">{name}</div>'
          f'<div>{fv.t("ready_in", ar)} {r["days"]} {fv.t("days", ar)}</div>'
          f'<div class="money">{money}</div><div style="opacity:.75;font-size:.85rem">{fv.t("per_harvest", ar) if area else ""} · {conf}</div>'
          f'<div>{fv.t("water_need", ar)}: {WATER_ICON[r["water"]]} · {fv.t("care", ar)}: {fv.t(r["care"], ar)}</div>'
          f'<div style="opacity:.85;font-size:.9rem;margin-top:.3rem">' + "<br>".join(f"• {x}" for x in reasons[:3]) + "</div>",
          ar, "crop")


def farmer_page() -> None:
    ar = language()
    route = sidebar(show_models=False)
    con = connection()
    farm = farms.load_farm()
    profile = farm["profile"]
    plan = db.latest_plan(con, farm["id"])
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731

    updated = fv.updated_at(con, farm)
    _html(f'<div class="kicker">Hydro Monitor</div>', ar)
    st.title(f"🌱 {profile['name']}")
    fc = forecast_today(profile.get("latitude") or 25.29, profile.get("longitude") or 51.53)
    bits = [f"{profile['location']}"]
    if fc:
        bits.append(f"☀️ {t('weather')}: {t('up_to')} {fc['temp_max']:.0f} °C, {t('hottest')} {fc['hottest_hour']}")
    if updated:
        bits.append(f"{t('updated')} {updated}")
    _html(" · ".join(bits), ar)

    # 1. the beds, in plain words
    st.subheader(t("beds"))
    cards = fv.bed_cards(con, farm, plan, ar)
    icon = {"ok": "🟢", "watch": "🟠", "act": "🔴", "none": "⚪"}
    cols = st.columns(min(3, len(cards)) or 1)
    for i, c in enumerate(cards):
        with cols[i % len(cols)]:
            crop_line = " · ".join(x for x in (c["crop"], c["day"], c["timing"]) if x)
            facts = []
            if c["soil"] is not None:
                facts.append(f"🌱 {t('soil')} {c['soil']:.0f}%")
            if c["temp"] is not None:
                facts.append(f"🌡️ {t('air')} {c['temp']:.0f} °C")
            if c["tank"] is not None:
                facts.append(f"🛢️ {t('tank')} {c['tank']:.0f}%")
            facts.append(f"🚿 {t('watered')}: {c['watered']}")
            _html(f'<h3>{icon[c["level"]]} {c["field_id"]} · {crop_line}</h3>'
                  f'<div class="big">{c["headline"]}</div>'
                  + "".join(f"<div>{m}</div>" for m in c["more"])
                  + f'<div class="facts">{" · ".join(facts)}</div>', ar, f"bed {c['level']}")

    # 2. today's advice from the AI team
    st.subheader(t("advice"))
    if plan and plan.get("reports"):
        message = plan.get("message_ar") if ar and plan.get("message_ar") else plan.get("message_en") or ""
        _html(message, ar, "advice")
        decisions = db.approvals(con, plan["version"])
        m1, m2, _ = st.columns([1.1, 1.5, 3.4])
        if m1.button(f"👍 {t('helpful')}", key="msg_ok"):
            db.add_approval(con, plan["version"], "message", "approve")
            st.toast(t("thanks"))
        with m2.popover(f"✏️ {t('correct')}"):
            fix = st.text_input(t("correct_q"), key="msg_fix")
            if st.button(t("save"), key="msg_save") and fix:
                db.add_approval(con, plan["version"], "message", "correct", fix)
                st.toast(t("thanks"))
        todos = plan.get("todos") or []
        todos_ar = plan.get("todos_ar") or []
        if todos:
            st.markdown(f"**{t('todo')}**")
        for i, todo in enumerate(todos):
            text = todos_ar[i] if ar and i < len(todos_ar) else todo
            item = f"todo:{i + 1}"
            state = decisions.get(item)
            a, b, c = st.columns([6, 1, 1.3])
            with a:
                _html(("✅ " if state and state["decision"] == "done" else "⬜ ") + text, ar)
            if b.button(t("done"), key=f"done{i}"):
                db.add_approval(con, plan["version"], item, "done")
                st.rerun()
            with c.popover(f"✏️ {t('correct')}"):
                fix = st.text_input(t("correct_q"), key=f"fix{i}")
                if st.button(t("save"), key=f"save{i}") and fix:
                    db.add_approval(con, plan["version"], item, "correct", fix)
                    st.rerun()
    else:
        st.info(t("no_plan"))
    if st.button(f"🔄 {t('refresh')}", help=t("refresh_help")):
        with st.status(t("refresh_help"), expanded=False) as status:
            result = network.run(con, trigger="button", route=route["name"], progress=status.write)
            status.update(state="complete" if result["status"] == "ok" else "error",
                          label="✓" if result["status"] == "ok" else result.get("error", "failed"))
        if result["status"] == "ok":
            st.rerun()

    # 3. what to plant
    st.subheader(f"🌾 {t('plant')}")
    advice = crops_for(farm["id"])
    groups: dict[str, list[str]] = {}
    for field_id, f in advice["fields"].items():   # beds with the same suggestions are shown once
        sig = json.dumps([r["crop"] for r in f["now"]] + [r["crop"] for r in f["later"]]) + str(f["covered"])
        groups.setdefault(sig, []).append(field_id)
    for fields in groups.values():
        f = advice["fields"][fields[0]]
        label = " · ".join(fields) + (f" ({f['area_m2']:,} m²)" if len(fields) == 1 and f["area_m2"] else "")
        st.markdown(f"**{label}** — {t('plant_now')}")
        if f["now"]:
            cols = st.columns(len(f["now"]))
            for col, r in zip(cols, f["now"]):
                with col:
                    crop_card(r, ar, f["area_m2"])
        if f["later"]:
            later = " · ".join(f"**{fv.month_name(r['plant_from_month'], ar)}**: {r['ar'] if ar else r['name']}" for r in f["later"])
            _html(f"📅 {t('later')}: " + later.replace("**", ""), ar)
    st.caption(t("gross"))

    tips = fv.market_tips(plan)
    if tips:
        with st.expander(f"💰 {t('market')}"):
            for tip in tips:
                st.markdown(f"**{tip['title']}** — {tip['detail']}")

    # 4. notes and questions
    left, right = st.columns([1, 1.4])
    with left:
        st.subheader(f"📝 {t('notes')}")
        with st.form("note", clear_on_submit=True):
            field = st.selectbox("Bed", [f["field_id"] for f in profile["fields"]], label_visibility="collapsed")
            text = st.text_input("Note", placeholder=t("note_ph"), label_visibility="collapsed")
            if st.form_submit_button(t("add")) and text:
                db.add_note(con, field, text)
                st.toast(t("thanks"))
        for n in db.notes(con, limit=3):
            st.caption(f"{n['field_id']} · {n['text']}")
    with right:
        st.subheader(f"💬 {t('ask')}")
        chat = st.session_state.setdefault("farmer_chat", [])
        for turn in chat[-6:]:
            with st.chat_message(turn["role"]):
                st.write(turn["text"])
        question = st.chat_input(t("ask_ph"))
        if question:
            chat.append({"role": "user", "text": question})
            with st.spinner("…"):
                reply = assistant.answer(question, farm, plan, route)
            chat.append({"role": "assistant", "text": reply["text"]})
            st.rerun()


# ── Onboarding page ──────────────────────────────────────────────────────
EXAMPLE = [   # the plan's worked example (6.1), for rehearsing the demo
    "Near Al Khor, north of Doha.",
    "Two soil beds, each about 10 by 20 metres, in the open air. Sometimes I put up shade cloth.",
    "Purple amaranth.",
    "About three weeks ago.",
    "From a tank, I water with a hose.",
    "Granular NPK once a month.",
    "Electricity in the shed, about 30 metres away.",
    "Mobile data works.",
    "In the heat the leaves go pale and burn at the edges, and I never know if I'm watering too much.",
]


def onboarding_page() -> None:
    ar = language()
    route = sidebar(show_models=False)
    model = onboarding.onboarding_model(route)
    s = st.session_state
    if "ob_history" not in s or st.sidebar.button("Start over · ابدأ من جديد"):
        opening, profile = onboarding.first_question()
        if ar:
            opening = "مرحباً! سأساعدك في إعداد نظام Hydro Monitor لمزرعتك. " + onboarding.QUESTIONS["location"][1]
        s.ob_history = [{"role": "assistant", "text": opening}]
        s.ob_profile, s.ob_done, s.ob_parts = profile, False, None

    st.markdown('<div class="kicker">Hydro Monitor · Set up a farm</div>', unsafe_allow_html=True)
    st.title("أخبرنا عن مزرعتك" if ar else "Tell us about your farm")
    st.caption(f"The assistant ({model}) asks one question at a time, looks up your area's weather, and designs the sensors "
               "and the AI team for your farm. Answer in your own words, in Arabic or English; 'none' or 'don't know' is "
               "fine. A person from our team checks everything before anything is installed.")

    for turn in s.ob_history:
        if turn["role"] == "tool":
            st.caption(f"🔎 {turn['text']}")
        else:
            with st.chat_message("user" if turn["role"] == "farmer" else "assistant"):
                st.write(turn["text"])

    def say(text: str) -> None:
        s.ob_history.append({"role": "farmer", "text": text})
        with st.spinner("…"):
            out = onboarding.step(s.ob_history, s.ob_profile, model)
        s.ob_profile = out["profile"]
        if out["tool"]:
            s.ob_history.append({"role": "tool", "text": f"Looked up {out['tool']}"})
        s.ob_history.append({"role": "assistant", "text": out["reply"]})
        if out["done"] and not s.ob_done:
            s.ob_done = True
            with st.spinner("Designing the sensors, the AI team and crop suggestions…"):
                public = onboarding.public_profile(s.ob_profile)
                s.ob_parts = onboarding.design(public, onboarding.slug(public.get("place") or public["location"]), model)
            s.ob_history.append({"role": "assistant", "text": onboarding.proposal_text(s.ob_parts)})

    if not s.ob_done:
        farmer_turns = sum(1 for turn in s.ob_history if turn["role"] == "farmer")
        if farmer_turns < len(EXAMPLE) and st.button(f"Say the plan's example line {farmer_turns + 1}/{len(EXAMPLE)}",
                                                     help=EXAMPLE[farmer_turns]):
            say(EXAMPLE[farmer_turns])
            st.rerun()
        text = st.chat_input("اكتب هنا…" if ar else "Type your answer…")
        if text:
            say(text)
            st.rerun()
        with st.sidebar.expander("Profile so far"):
            st.json(onboarding.public_profile(s.ob_profile))
            st.caption("Still to ask: " + (", ".join(onboarding.missing(s.ob_profile)) or "nothing")
                       + " · null = answered 'none / don't know'")
        return

    parts = s.ob_parts
    st.markdown('<div class="step">WHAT THE ASSISTANT PRODUCED · FOR TEAM REVIEW</div>', unsafe_allow_html=True)
    t0, t1, t2, t3, t4 = st.tabs(["🌾 Crop suggestions", "1 · Farm profile", "2 · Hardware plan", "3 · Hard limits", "4 · Agent network"])
    with t0:
        for field_id, f in parts["crops"]["fields"].items():
            st.markdown(f"**{field_id}** ({f['area_m2']:,} m², {'greenhouse' if f['covered'] else 'open air'}) — {fv.t('plant_now', ar)}")
            cols = st.columns(max(1, len(f["now"])))
            for col, r in zip(cols, f["now"]):
                with col:
                    crop_card(r, ar, f["area_m2"])
            if f["later"]:
                st.caption("Later: " + " · ".join(f"{r['plant_from_month']}: {r['name']}" for r in f["later"]))
            break   # beds share the climate: one set of cards is enough here
        st.caption(" ".join(parts["crops"]["notes"]))
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
    if (farms.FARMS / farm_id / "profile.json").exists():
        st.warning(f"farms/{farm_id} already exists: approving replaces it.")
    if st.button("✅ Team approves: save and go live", type="primary"):
        for key in farms.PARTS:
            parts[key]["farm_id"] = farm_id
        parts["network"]["designed_by"] = "onboarding assistant, approved by the team"
        farms.save_farm(farm_id, parts)
        farms.set_active(farm_id)
        con = connection()
        db.save_plan(con, farm_id, farms.local_now(farms.load_farm(farm_id)).isoformat(timespec="seconds"),
                     parts["limits"]["start"], made_by="onboarding (starting ranges)", trigger="onboarding",
                     message_en="Starting ranges from onboarding.", todos=[], flags=[])
        crops_for.clear()
        st.success(f"Saved farms/{farm_id} and made it the active farm. Open 'My farm' to see it.")


st.navigation([st.Page(farmer_page, title="My farm", icon="🌱", default=True),
               st.Page(onboarding_page, title="Set up a farm", icon="💬"),
               st.Page(dev_page, title="Developer", icon="🛠️")]).run()
