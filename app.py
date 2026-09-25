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
from checker import KIND_LABEL
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
    .hero{background:linear-gradient(120deg,#1f7a4d 0%,#2e9e6a 45%,#2b8fb0 100%);color:#fff;border-radius:22px;
          padding:1.4rem 1.8rem 1.2rem;margin:.2rem 0 1rem 0;box-shadow:0 10px 30px rgba(31,122,77,.25)}
    .hero-kicker{font-family:monospace;font-size:.78rem;letter-spacing:.12em;text-transform:uppercase;opacity:.85}
    .hero-title{font-size:2.3rem;font-weight:800;line-height:1.15;margin:.2rem 0 .35rem}
    .hero-sub{font-size:1.02rem;opacity:.95}
    .kpi{border-radius:18px;padding:.9rem 1rem;background:rgba(46,158,106,.10);border:1px solid rgba(46,158,106,.25);
         min-height:128px;margin-bottom:.6rem}
    .kpi-icon{font-size:1.5rem}.kpi-value{font-size:1.45rem;font-weight:800;margin-top:.1rem;line-height:1.25}
    .kpi-label{font-weight:600;opacity:.9}.kpi-sub{font-size:.82rem;opacity:.7}
    .dept{border-radius:16px;padding:.85rem 1rem;margin-bottom:.8rem;border:1px solid rgba(128,128,128,.22);
          border-top:5px solid #2ea05a;background:rgba(128,128,128,.05);min-height:170px}
    .dept.watch{border-top-color:#e08a00}.dept.act{border-top-color:#d73c3c}
    .dept-head{font-weight:700;font-size:1.05rem;margin-bottom:.35rem}
    .dept-text{font-size:.93rem;line-height:1.5}.dept-warn{font-size:.85rem;margin-top:.4rem;color:#e08a00}
    .pill{font-size:.7rem;font-weight:600;background:rgba(46,120,200,.15);color:#2e78c8;border-radius:10px;padding:1px 8px;margin-left:.3rem}
    .overview{border-radius:18px;padding:1.1rem 1.3rem;margin:.4rem 0 1rem;background:linear-gradient(135deg,rgba(46,120,200,.13),rgba(46,158,106,.13));
         border:1px solid rgba(46,120,200,.3)}
    .big-head{font-size:1.25rem;font-weight:800;margin-bottom:.4rem}.big-text{font-size:1.12rem;line-height:1.7}
    .big-stats{margin-top:.5rem;font-size:.95rem;opacity:.9}
    .todo{font-size:1.05rem;padding:.35rem 0}
    .story{border-radius:14px;padding:.8rem 1rem;background:rgba(240,199,94,.14);border-left:5px solid #f0c75e;margin:.4rem 0 .8rem}
    .muted{opacity:.75;font-size:.87rem}
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
    rows = [r for r in db.read_log(con, since, farm_id=farm["id"]) if r["type"] == "reading"]
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
        st.dataframe(pd.DataFrame(db.latest_rows(con, 14, farm["id"])), hide_index=True, height=260, width="stretch")


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
        c1.markdown(text + ("  \n:green[✓ approved]" if done and done["decision"] == "approve" else
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
    d.metric("Rows logged", f"{con.execute('SELECT COUNT(*) FROM log WHERE farm_id = ?', (farm['id'],)).fetchone()[0]:,}")

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
            db.add_note(con, field, text, farm["id"])
            st.rerun()
    notes = db.notes(con, limit=6, farm_id=farm["id"])
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


@st.cache_data(ttl=3600, show_spinner=False)
def farm_plan_for(farm_id: str) -> dict:
    """The onboarding's saved plan (bare land) or a fresh one for a working farm."""
    import farm_plan

    f = farms.load_farm(farm_id)
    return f.get("plan") or farm_plan.plan(f["profile"])


def _html(text: str, ar: bool, cls: str = "") -> None:
    st.markdown(f'<div class="{cls} {"ar" if ar else ""}">{text}</div>', unsafe_allow_html=True)


WATER_ICON = {"low": "💧", "medium": "💧💧", "high": "💧💧💧"}
DEPT_ICON = {"ENV": "🌤️", "SOIL": "💧", "CROP": "🌱", "DATA": "📈", "MKT": "🛒", "FIN": "💰", "DIR": "🧭"}
DEPT_AR = {"ENV": "البيئة الزراعية", "SOIL": "التربة والماء", "CROP": "علوم المحاصيل", "DATA": "البيانات والتحليل",
           "MKT": "السوق والمحاصيل", "FIN": "المالية"}
STATUS_CLS = {"OK": "ok", "WARNING": "watch", "CRITICAL": "act"}


def qr(n: float | int | None) -> str:
    return "–" if n is None else f"{n:,.0f} QR"


def crop_card(r: dict, ar: bool, area: int) -> None:
    name = r["ar"] if ar else r["name"]
    money = f"≈ {r['qr_per_harvest']:,} QR" if area else f"≈ {r['qr_m2']} QR/m²"
    reasons = r.get("reasons_ar" if ar else "reasons") or r["reasons"]
    conf = fv.t(r["confidence"], ar) if r["confidence"] in ("official", "estimate", "proxy", "farmer") else r["confidence"]
    _html(f'<div class="name">{name}</div>'
          f'<div>{fv.t("ready_in", ar)} {r["days"]} {fv.t("days", ar)}</div>'
          f'<div class="money">{money}</div><div class="muted">{fv.t("per_harvest", ar) if area else ""} · {conf}</div>'
          f'<div>{fv.t("water_need", ar)}: {WATER_ICON[r["water"]]} · {fv.t("care", ar)}: {fv.t(r["care"], ar)}</div>'
          f'<div class="muted" style="margin-top:.3rem">' + "<br>".join(f"• {x}" for x in reasons[:3]) + "</div>",
          ar, "crop")


def kpi(col, icon: str, label: str, value: str, sub: str = "") -> None:
    col.markdown(f'<div class="kpi"><div class="kpi-icon">{icon}</div><div class="kpi-value">{value}</div>'
                 f'<div class="kpi-label">{label}</div><div class="kpi-sub">{sub}</div></div>', unsafe_allow_html=True)


def hero(farm: dict, fc: dict | None, updated: str | None, ar: bool) -> None:
    p = farm["profile"]
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731
    bits = [f"📍 {p['location']}"]
    if fc:
        bits.append(f"☀️ {t('up_to')} {fc['temp_max']:.0f} °C · {t('hottest')} {fc['hottest_hour']}")
    if updated:
        bits.append(f"🔄 {t('updated')} {updated}")
    stage = t("stage_land") if p.get("stage") == "land" else t("stage_farm")
    st.markdown(f'<div class="hero {"ar" if ar else ""}"><div class="hero-kicker">Hydro Monitor · {stage}</div>'
                f'<div class="hero-title">🌱 {p["name"]}</div><div class="hero-sub">{" &nbsp;·&nbsp; ".join(bits)}</div></div>',
                unsafe_allow_html=True)


def team_section(plan: dict | None, money: dict, ar: bool) -> None:
    """Each department's insight, then the combined overview (the Farm Director)."""
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731
    reports = {k: v for k, v in ((plan or {}).get("reports") or {}).items() if not k.startswith("_")}
    st.markdown(f"### 🤖 {t('team')}")
    if not reports:
        st.info(t("no_plan"))
        return
    cols = st.columns(3)
    for i, r in enumerate(reports.values()):
        code = r.get("code") or ""
        name = DEPT_AR.get(code, r["name"]) if ar else r["name"]
        text = r.get("summary_ar") if ar and r.get("summary_ar") else r["summary"]
        warn = (r.get("warnings") or [None])[0]
        with cols[i % 3]:
            _html(f'<div class="dept-head">{DEPT_ICON.get(code, "🧩")} {name}'
                  f'{" <span class=pill>" + t("advice_only") + "</span>" if r.get("advice_only") else ""}</div>'
                  f'<div class="dept-text">{text}</div>'
                  + (f'<div class="dept-warn">⚠ {warn}</div>' if warn and not ar else ""),
                  ar, f"dept {STATUS_CLS.get(r.get('status'), 'ok')}")
    warnings = sum(len(r.get("warnings") or []) for r in reports.values())
    changes = len((plan.get("reports") or {}).get("_changes") or [])
    flags = len(plan.get("flags") or [])
    message = plan.get("message_ar") if ar and plan.get("message_ar") else plan.get("message_en") or ""
    best = money["recommended"]
    finance = (f"💰 {t('profit_year')}: <b>{qr(best['profit'])}</b> · {t('setup')}: {qr(best['capex_total'])}"
               + (f" · {t('payback')}: {best['payback_years']} {t('years')}" if best["payback_years"] else ""))
    _html(f'<div class="big-head">🧭 {t("big_picture")}</div><div class="big-text">{message}</div>'
          f'<div class="big-stats">🔎 {warnings} {t("n_warnings")} · 🎛️ {changes} {t("n_changes")}'
          f'{" (" + str(flags) + " " + t("n_checked") + ")" if flags else ""} · ✅ {len(plan.get("todos") or [])} {t("n_todos")}</div>'
          f'<div class="big-stats">{finance}</div>', ar, "overview")


def todo_section(con, plan: dict | None, ar: bool) -> None:
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731
    if not plan or not plan.get("todos"):
        return
    st.markdown(f"### ✅ {t('todo')}")
    decisions = db.approvals(con, plan["version"])
    todos_ar = plan.get("todos_ar") or []
    for i, todo in enumerate(plan["todos"]):
        text = todos_ar[i] if ar and i < len(todos_ar) else todo
        item = f"todo:{i + 1}"
        state = decisions.get(item)
        a, b, c = st.columns([5.2, 1, 1.8])
        with a:
            _html(("✅ " if state and state["decision"] == "done" else "⬜ ") + text, ar, "todo")
        if b.button(t("done"), key=f"done{i}"):
            db.add_approval(con, plan["version"], item, "done")
            st.rerun()
        with c.popover(f"✏️ {t('correct')}"):
            fix = st.text_input(t("correct_q"), key=f"fix{i}")
            if st.button(t("save"), key=f"save{i}") and fix:
                db.add_approval(con, plan["version"], item, "correct", fix)
                st.rerun()
    m1, m2, _ = st.columns([1.1, 1.5, 3.4])
    if m1.button(f"👍 {t('helpful')}", key="msg_ok"):
        db.add_approval(con, plan["version"], "message", "approve")
        st.toast(t("thanks"))
    with m2.popover(f"✏️ {t('correct')}"):
        fix = st.text_input(t("correct_q"), key="msg_fix")
        if st.button(t("save"), key="msg_save") and fix:
            db.add_approval(con, plan["version"], "message", "correct", fix)
            st.toast(t("thanks"))


def beds_section(con, farm: dict, plan: dict | None, ar: bool) -> None:
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731
    st.markdown(f"### 🪴 {t('beds')}")
    cards = fv.bed_cards(con, farm, plan, ar)
    icon = {"ok": "🟢", "watch": "🟠", "act": "🔴", "none": "⚪"}
    planned = farm["profile"].get("stage") == "land"
    cols = st.columns(min(4, len(cards)) or 1)
    for i, c in enumerate(cards):
        with cols[i % len(cols)]:
            crop_line = " · ".join(x for x in (c["crop"], c["day"], c["timing"]) if x)
            facts = []
            if c["soil"] is not None:
                facts.append(f"🌱 {c['soil']:.0f}%")
            if c["temp"] is not None:
                facts.append(f"🌡️ {c['temp']:.0f} °C")
            if c["tank"] is not None:
                facts.append(f"🛢️ {c['tank']:.0f}%")
            if c["soil"] is not None:
                facts.append(f"🚿 {c['watered']}")
            headline = t("planned_bed") if planned and c["level"] == "none" else c["headline"]
            _html(f'<h3>{icon[c["level"]] if not planned or c["level"] != "none" else "📐"} {c["field_id"]} · {crop_line}</h3>'
                  f'<div class="big">{headline}</div>' + "".join(f"<div>{m}</div>" for m in c["more"])
                  + (f'<div class="facts">{" · ".join(facts)}</div>' if facts else ""), ar, f"bed {c['level']}")


def calendar_chart(money: dict, ar: bool):
    """The crop combination as a year calendar: one labelled row per zone and crop, cool season then hot season."""
    import altair as alt

    rows = []
    for z in money["recommended"]["zones"]:
        cover = farm_plan_mod().COVER_LABEL[z["cover"]][1 if ar else 0]
        for c in z["crops"]:
            start, end = (0, 7) if c["season"] == "cool" else (7, 12)
            name = c["ar"] if ar else c["name"].split(" (")[0]
            rows.append({"row": f"{z['zone']} {cover} · {name}", "crop": name, "start": start, "end": end,
                         "label": f"{name} · {c['share']:.0%} · {c['area_m2']:,} m²" if c["crop"] else name,
                         "sales": c["sales"], "order": f"{z['zone']}{start:02d}"})
    df = pd.DataFrame(rows)
    months = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]
    label_expr = "[" + ",".join(f"'{fv.month_name(m, ar)[:3] if not ar else fv.month_name(m, ar)}'" for m in months) + "][datum.value]"
    order = list(dict.fromkeys(df.sort_values(["order"])["row"]))
    base = alt.Chart(df).encode(
        y=alt.Y("row:N", title=None, sort=order, axis=alt.Axis(labelLimit=260)),
        x=alt.X("start:Q", title=None, scale=alt.Scale(domain=[0, 12]),
                axis=alt.Axis(values=list(range(13)), labelExpr=label_expr, labelAngle=0, grid=True)),
        x2="end:Q", tooltip=["row", "label", alt.Tooltip("sales:Q", format=",.0f", title="QR / season")])
    bars = base.mark_bar(cornerRadius=7, height=22).encode(color=alt.Color("crop:N", legend=None, scale=alt.Scale(scheme="tableau20")))
    text = base.mark_text(align="left", dx=8, color="white", fontWeight="bold").encode(text="label:N")
    return (bars + text).properties(height=34 * len(order) + 30)


def finance_charts(money: dict, ar: bool):
    import altair as alt

    best = money["recommended"]
    pl = pd.DataFrame([{"what": fv.t("sales", ar), "QR": best["sales"], "kind": "in"},
                       *[{"what": k, "QR": v, "kind": "out"} for k, v in best["opex"].items() if v],
                       {"what": fv.t("profit_year", ar), "QR": best["profit"], "kind": "profit"}])
    colours = alt.Scale(domain=["in", "out", "profit"], range=["#2ea05a", "#e08a00", "#2e78c8"])
    bars = alt.Chart(pl).mark_bar(cornerRadius=6).encode(
        x=alt.X("QR:Q", title="QR / year"), y=alt.Y("what:N", sort=None, title=None),
        color=alt.Color("kind:N", scale=colours, legend=None), tooltip=["what", alt.Tooltip("QR:Q", format=",.0f")])
    capex = pd.DataFrame(best["capex"]).sort_values("total", ascending=False)
    capex["item"] = capex["item"].str.replace(r" \((.*)\)", r" · \1", regex=True)
    setup = alt.Chart(capex).mark_bar(cornerRadius=6, color="#8e6fd1").encode(
        x=alt.X("total:Q", title="QR"), y=alt.Y("item:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240)),
        tooltip=["item", alt.Tooltip("total:Q", format=",.0f", title="QR"), "what"])
    setup_text = alt.Chart(capex).mark_text(align="left", dx=4, color="#9a9a9a").encode(
        x="total:Q", y=alt.Y("item:N", sort="-x"), text=alt.Text("total:Q", format=",.0f"))
    return bars.properties(height=220), (setup + setup_text).properties(height=28 * len(capex) + 20)


def farm_plan_mod():
    import farm_plan

    return farm_plan


def plan_tab(farm: dict, money: dict, ar: bool) -> None:
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731
    fp = farm_plan_mod()
    best = money["recommended"]
    c1, c2, c3, c4 = st.columns(4)
    kpi(c1, "🏗️", t("setup"), qr(best["capex_total"]),
        (f"{t('budget')}: {qr(money['budget_qr'])}" if money.get("budget_qr") else t("no_budget")))
    kpi(c2, "🧺", t("sales"), qr(best["sales"]), t("per_year"))
    kpi(c3, "💰", t("profit_year"), qr(best["profit"]), f"{best['margin_pct']}% {t('of_sales')}")
    kpi(c4, "⏳", t("payback"), f"{best['payback_years']} {t('years')}" if best["payback_years"] else "–",
        best["label"][1 if ar else 0])
    if money.get("story") and not ar:
        _html(f"🧑‍🌾 {money['story']}", ar, "story")
    for w in money.get("warnings") or []:
        st.warning(w)
    left, right = st.columns([1.25, 1])
    with left:
        st.markdown(f"#### 🗺️ {t('site')}")
        st.markdown(fp.site_svg(money, ar), unsafe_allow_html=True)
    with right:
        st.markdown(f"#### 🧾 {t('setup_items')}")
        _, setup = finance_charts(money, ar)
        st.altair_chart(setup, width="stretch")
    st.markdown(f"#### 🌾 {t('combo')}")
    st.altair_chart(calendar_chart(money, ar), width="stretch")
    st.caption(t("combo_note"))
    st.markdown(f"#### 📊 {t('money')}")
    bars, _ = finance_charts(money, ar)
    st.altair_chart(bars, width="stretch")
    options = pd.DataFrame([{t("option"): o["label"][1 if ar else 0], t("setup"): qr(o["capex_total"]),
                             t("profit_year"): qr(o["profit"]),
                             t("payback"): f"{o['payback_years']} {t('years')}" if o["payback_years"] else "–",
                             t("fits_budget"): "✅" if not money.get("budget_qr") or o["capex_total"] <= money["budget_qr"] else "❌"}
                            for o in money["options"].values()])
    st.markdown(f"#### ⚖️ {t('options')}")
    st.dataframe(options, hide_index=True, width="stretch")
    if money.get("current"):
        cur, mix = money["current"], money["options"]["starter"]
        st.info(f"{t('today_crop')}: {qr(cur['profit'])} / {t('per_year')} → {t('best_mix')}: {qr(mix['profit'])} "
                f"({mix['profit'] - cur['profit']:+,} QR)")
    st.caption(money.get("assumptions", ""))


def plant_tab(farm: dict, ar: bool) -> None:
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731
    advice = crops_for(farm["id"])
    groups: dict[str, list[str]] = {}
    for field_id, f in advice["fields"].items():   # beds with the same suggestions are shown once
        sig = json.dumps([r["crop"] for r in f["now"]] + [r["crop"] for r in f["later"]]) + str(f["covered"])
        groups.setdefault(sig, []).append(field_id)
    for fields in groups.values():
        f = advice["fields"][fields[0]]
        label = " · ".join(fields) + (f" ({f['area_m2']:,} m²)" if len(fields) == 1 and f["area_m2"] else "")
        st.markdown(f"#### {label} — {t('plant_now')}")
        if f["now"]:
            cols = st.columns(len(f["now"]))
            for col, r in zip(cols, f["now"]):
                with col:
                    crop_card(r, ar, f["area_m2"])
        if f["later"]:
            later = " · ".join(f"{fv.month_name(r['plant_from_month'], ar)}: {r['ar'] if ar else r['name']}" for r in f["later"])
            _html(f"📅 {t('later')}: {later}", ar, "story")
    st.caption(t("gross"))


def farmer_page() -> None:
    ar = language()
    farm_switcher()
    route = sidebar(show_models=False)
    con = connection()
    farm = farms.load_farm()
    profile = farm["profile"]
    plan = db.latest_plan(con, farm["id"])
    money = farm_plan_for(farm["id"])
    t = lambda key, **kw: fv.t(key, ar, **kw)   # noqa: E731

    fc = forecast_today(profile.get("latitude") or 25.29, profile.get("longitude") or 51.53)
    hero(farm, fc, fv.updated_at(con, farm), ar)
    cards = fv.bed_cards(con, farm, plan, ar)
    ok = sum(1 for c in cards if c["level"] == "ok")
    soon = [c["timing"] for c in cards if c.get("timing")]
    k1, k2, k3, k4 = st.columns(4)
    kpi(k1, "🪴", t("kpi_beds"), f"{ok}/{len(cards)}" if any(c["level"] != "none" for c in cards) else f"{len(cards)}",
        t("kpi_beds_sub") if any(c["level"] != "none" for c in cards) else t("planned_bed"))
    kpi(k2, "💰", t("profit_year"), qr(money["recommended"]["profit"]), t("estimate_word"))
    kpi(k3, "🌾", t("kpi_harvest"), soon[0] if soon else "–", cards[0]["crop"] if cards else "")
    kpi(k4, "💧", t("kpi_water"), f"{money['recommended']['water_m3_year']:,} m³", t("per_year"))

    tab_today, tab_plan, tab_plant, tab_talk = st.tabs(
        [f"🌿 {t('tab_today')}", f"🗺️ {t('tab_plan')}", f"🌾 {t('plant')}", f"💬 {t('tab_talk')}"])
    with tab_today:
        beds_section(con, farm, plan, ar)
        team_section(plan, money, ar)
        todo_section(con, plan, ar)
        waiting_for_sensors = profile.get("stage") == "land" and not fv.updated_at(con, farm)
        if not waiting_for_sensors and st.button(f"🔄 {t('refresh')}", help=t("refresh_help"), type="primary"):
            with st.status(t("refresh_help"), expanded=False) as status:
                result = network.run(con, trigger="button", route=route["name"], progress=status.write)
                status.update(state="complete" if result["status"] == "ok" else "error",
                              label="✓" if result["status"] == "ok" else result.get("error", "failed"))
            if result["status"] == "ok":
                st.rerun()
        if waiting_for_sensors:
            st.info(t("land_note"))
    with tab_plan:
        plan_tab(farm, money, ar)
    with tab_plant:
        plant_tab(farm, ar)
        tips = fv.market_tips(plan)
        if tips:
            with st.expander(f"🛒 {t('market')}"):
                for tip in tips:
                    st.markdown(f"**{tip['title']}** — {tip['detail']}")
    with tab_talk:
        left, right = st.columns([1, 1.4])
        with left:
            st.markdown(f"### 📝 {t('notes')}")
            with st.form("note", clear_on_submit=True):
                field = st.selectbox("Bed", [f["field_id"] for f in profile["fields"]], label_visibility="collapsed")
                text = st.text_input("Note", placeholder=t("note_ph"), label_visibility="collapsed")
                if st.form_submit_button(t("add")) and text:
                    db.add_note(con, field, text, farm["id"])
                    st.toast(t("thanks"))
            for n in db.notes(con, limit=3, farm_id=farm["id"]):
                st.caption(f"{n['field_id']} · {n['text']}")
        with right:
            st.markdown(f"### 💬 {t('ask')}")
            chat = st.session_state.setdefault("farmer_chat", [])
            for turn in chat[-6:]:
                with st.chat_message(turn["role"]):
                    st.write(turn["text"])
            question = st.text_input(t("ask_ph"), key="ask_box")
            if st.button(t("add"), key="ask_send") and question:
                chat.append({"role": "user", "text": question})
                with st.spinner("…"):
                    reply = assistant.answer(question, farm, plan, route)
                chat.append({"role": "assistant", "text": reply["text"]})
                st.rerun()


def farm_switcher() -> None:
    """Pick which farm the board shows (the demo has a working farm and a bare-land plan)."""
    names = farms.list_farms()
    current = farms.active_farm_id()
    if len(names) > 1:
        choice = st.sidebar.selectbox("🏡 Farm", names, index=names.index(current) if current in names else 0)
        if choice != current:
            farms.set_active(choice)
            st.cache_data.clear()
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
    tp, t0, t1, t2, t3, t4 = st.tabs(["💰 Farm plan & money", "🌾 Crop suggestions", "1 · Farm profile", "2 · Hardware plan",
                                      "3 · Hard limits", "4 · Agent network"])
    with tp:
        plan_tab(None, parts["plan"], ar)
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
        farm_plan_for.clear()
        st.success(f"Saved farms/{farm_id} and made it the active farm. Open 'My farm' to see it.")


st.navigation([st.Page(farmer_page, title="My farm", icon="🌱", default=True),
               st.Page(onboarding_page, title="Set up a farm", icon="💬"),
               st.Page(dev_page, title="Developer", icon="🛠️")]).run()
