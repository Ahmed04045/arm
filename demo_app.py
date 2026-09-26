"""Hydro Monitor farm setup and dashboard.

    streamlit run demo_app.py

Two pages:
    Set up a farm   the onboarding chat: at most 3 questions, a design, then the 4 blocks
    My farm         the farm dashboard for that design: live-looking readings, the AI team's insights (written by
                    rules from those readings), the big picture, to-dos, the kit drawing, parts and harvests, and a
                    question box with ready answers
Sidebar controls switch the day (normal, heat wave, tank running low, pump running) to show alerts on stage.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import demo_script as ds

st.set_page_config(page_title="Hydro Monitor", page_icon="🌱", layout="wide")
st.markdown(
    """<style>
    .kicker{font-family:monospace;color:#2d744d;font-size:.75rem;letter-spacing:.08em;text-transform:uppercase}
    .hero{background:linear-gradient(120deg,#1f7a4d 0%,#2e9e6a 45%,#2b8fb0 100%);color:#fff;border-radius:22px;
          padding:1.4rem 1.8rem 1.2rem;margin:.2rem 0 1rem 0;box-shadow:0 10px 30px rgba(31,122,77,.25)}
    .hero-kicker{font-family:monospace;font-size:.78rem;letter-spacing:.12em;text-transform:uppercase;opacity:.85}
    .hero-title{font-size:2.3rem;font-weight:800;line-height:1.15;margin:.2rem 0 .35rem}
    .hero-sub{font-size:1.02rem;opacity:.95}
    .kpi{border-radius:18px;padding:.9rem 1rem;background:rgba(46,158,106,.10);border:1px solid rgba(46,158,106,.25);
         min-height:128px;margin-bottom:.6rem}
    .kpi-icon{font-size:1.5rem}.kpi-value{font-size:1.45rem;font-weight:800;margin-top:.1rem;line-height:1.25}
    .kpi-label{font-weight:600;opacity:.9}.kpi-sub{font-size:.82rem;opacity:.7}
    .bed{border-radius:16px;padding:1rem 1.2rem;margin-bottom:.8rem;border:1px solid rgba(128,128,128,.25)}
    .bed.ok{background:rgba(46,160,90,.12);border-left:7px solid #2ea05a}
    .bed.watch{background:rgba(224,138,0,.13);border-left:7px solid #e08a00}
    .bed.act{background:rgba(215,60,60,.13);border-left:7px solid #d73c3c}
    .bed h3{margin:.1rem 0 .3rem 0;font-size:1.3rem}
    .bed .headline{font-size:1.2rem;font-weight:700;margin:.2rem 0}
    .reading{border-radius:14px;padding:.7rem .9rem;background:rgba(128,128,128,.07);border:1px solid rgba(128,128,128,.2);text-align:center}
    .reading .v{font-size:1.35rem;font-weight:800}.reading .l{font-size:.85rem;opacity:.75}
    .dept{border-radius:16px;padding:.85rem 1rem;margin-bottom:.8rem;border:1px solid rgba(128,128,128,.22);
          border-top:5px solid #2ea05a;background:rgba(128,128,128,.05);min-height:170px}
    .dept.WARNING{border-top-color:#e08a00}.dept.CRITICAL{border-top-color:#d73c3c}
    .dept-head{font-weight:700;font-size:1.05rem;margin-bottom:.35rem}
    .dept-text{font-size:.93rem;line-height:1.5}.dept-warn{font-size:.85rem;margin-top:.4rem;color:#e08a00}
    .overview{border-radius:18px;padding:1.1rem 1.3rem;margin:.4rem 0 1rem;
              background:linear-gradient(135deg,rgba(46,120,200,.13),rgba(46,158,106,.13));border:1px solid rgba(46,120,200,.3)}
    .overview-head{font-size:1.25rem;font-weight:800;margin-bottom:.4rem}.overview-text{font-size:1.12rem;line-height:1.7}
    .overview-stats{margin-top:.5rem;font-size:.95rem;opacity:.9}
    .todo{font-size:1.05rem;padding:.35rem 0}
    .muted{opacity:.75;font-size:.87rem}
    .flow{stroke-dasharray:10 8;animation:flow 1.1s linear infinite}
    @keyframes flow{to{stroke-dashoffset:-36}}
    </style>""",
    unsafe_allow_html=True,
)
DEPT_ICON = {"ENV": "🌤️", "SOIL": "💧", "CROP": "🌱", "DATA": "📈"}
S = st.session_state


def html(text: str, cls: str = "") -> None:
    st.markdown(f'<div class="{cls}">{text}</div>', unsafe_allow_html=True)


def kpi(col, icon: str, label: str, value: str, sub: str = "") -> None:
    col.markdown(f'<div class="kpi"><div class="kpi-icon">{icon}</div><div class="kpi-value">{value}</div>'
                 f'<div class="kpi-label">{label}</div><div class="kpi-sub">{sub}</div></div>', unsafe_allow_html=True)


def default_profile() -> dict:
    """Before anyone chats, the dashboard shows the rehearsal farmer's farm."""
    p = ds.new_profile()
    for line in ds.DEMO_LINES:
        ds.step(p, line)
    return p


def init() -> None:
    if "chat" not in S:
        S.chat = [{"role": "assistant", "text": "Hello! I'm the Hydro Monitor assistant. We design, install and run small smart "
                                                "farms in Qatar, and we supply the fertilizer. Tell me a little about where you'd like to grow."}]
        S.profile = ds.new_profile()
        S.farm = default_profile()
        S.blocks = ds.blocks(S.farm)
        S.todo_state = {}
        S.notes = []
        S.qa = []
        S.checked_at = datetime.now()


def sidebar() -> str:
    st.sidebar.markdown("### 🌱 Hydro Monitor")
    if st.sidebar.button("↺ Reset"):
        for key in list(S.keys()):
            del S[key]
        st.rerun()
    return "normal"


# ── Set up a farm ────────────────────────────────────────────────────────
def setup_page() -> None:
    init()
    sidebar()
    st.markdown('<div class="kicker">Hydro Monitor · Set up a farm</div>', unsafe_allow_html=True)
    st.title("Tell us about your farm")
    st.caption("Friendly, plain words: at most 3 questions, then a design for you to check.")
    for turn in S.chat:
        with st.chat_message("user" if turn["role"] == "farmer" else "assistant"):
            st.write(turn["text"])
            for title, body in turn.get("blocks", []):
                st.markdown(f"**{title}**")
                st.code(body, language=None, wrap_lines=True)
            if turn.get("closing"):
                st.write(turn["closing"])

    def say(text: str) -> None:
        S.chat.append({"role": "farmer", "text": text})
        with st.spinner("Thinking..."):
            time.sleep(2)
            out = ds.step(S.profile, text)
        S.chat.append({"role": "assistant", "text": out["reply"], "blocks": out.get("blocks", []), "closing": out.get("closing")})
        if out.get("done"):
            S.farm = dict(S.profile)
            S.blocks = out["blocks"]
            S.todo_state = {}

    done = S.profile["stage"] == "done"
    said = sum(1 for t in S.chat if t["role"] == "farmer")
    if not done and said < len(ds.DEMO_LINES):
        if st.button(f"💬 Continue setup {said + 1}/{len(ds.DEMO_LINES)}"):
            say(ds.DEMO_LINES[said])
            st.rerun()
    if done and st.button("✅ Team approves: install and open the farm dashboard", type="primary"):
        st.switch_page(PAGES["farm"])
    text = st.chat_input("Type your answer…", disabled=done)
    if text:
        say(text)
        st.rerun()


# ── My farm ──────────────────────────────────────────────────────────────
def kit_svg(r: dict, kit_key: str) -> str:
    """The Starter Hydroponic Kit on the wall: two pipes, 4 net pots, funnel, tank, pump, sensors."""
    if kit_key != "starter":
        return ('<svg viewBox="0 0 620 200" style="width:100%"><rect x="10" y="10" width="600" height="180" rx="16" fill="#7cc47f"/>'
                + "".join(f'<line x1="40" y1="{40 + 30 * i}" x2="580" y2="{40 + 30 * i}" stroke="#2f7a3a" stroke-width="4" stroke-dasharray="6 10"/>'
                          for i in range(5))
                + '<text x="310" y="115" text-anchor="middle" font-size="18" font-weight="700" fill="#16361b">Drip-irrigated beds</text></svg>')
    flowing = r["flowing"] > 0 and r["tank"] >= ds.CROP["tank_block"]
    water = max(0.0, min(1.0, r["tank"] / 100))
    wcls = ' class="flow"' if (flowing or r["pump_on"]) else ""
    pots = ""
    for top in (140, 260):                     # each pipe's top edge: two net pots sit on it
        for x in (230, 400):
            pots += (f'<path d="M{x} {top - 6} q -24 -22 -10 -40 q 14 16 10 40" fill="#b0245e"/>'
                     f'<path d="M{x} {top - 6} q 24 -24 8 -44 q -14 18 -8 44" fill="#8c1d4b"/>'
                     f'<path d="M{x} {top - 6} q -2 -30 1 -46" stroke="#6c9e3a" stroke-width="3" fill="none"/>'
                     f'<ellipse cx="{x}" cy="{top + 2}" rx="17" ry="9" fill="#3b2a24"/>')
    hot = r["temp"] > ds.CROP["temp_alert"]
    return f'''<svg viewBox="0 0 640 460" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;font-family:Segoe UI,Arial">
  <rect x="4" y="4" width="632" height="452" rx="18" fill="#efe8dc" stroke="#c9b99b" stroke-width="2"/>
  <text x="24" y="34" font-size="15" font-weight="700" fill="#4a3f30">Your wall · Starter Hydroponic Kit</text>
  <circle cx="604" cy="36" r="16" fill="#fff3c4" stroke="#e0b100" stroke-width="2"/>
  <text x="604" y="41" text-anchor="middle" font-size="14">☀</text>
  <text x="580" y="41" text-anchor="end" font-size="11" fill="#4a3f30">light sensor · {r['light']:,} lux</text>
  <path d="M345 412 L 600 412 L 600 70 L 131 70 L 131 84" fill="none" stroke="#9aa9b3" stroke-width="8"/>
  <path d="M345 412 L 600 412 L 600 70 L 131 70 L 131 84" fill="none" stroke="#3a8fd1" stroke-width="3"{' class="flow"' if r['pump_on'] else ''}/>
  <path d="M112 84 L150 84 L138 108 L124 108 Z" fill="#8fa3b3" stroke="#5f7383" stroke-width="2"/>
  <text x="160" y="100" font-size="11" fill="#4a3f30">funnel</text>
  <line x1="131" y1="108" x2="131" y2="142" stroke="#3a8fd1" stroke-width="4"{wcls}/>
  <rect x="100" y="140" width="440" height="36" rx="18" fill="#dfe6ea" stroke="#9aa9b3" stroke-width="3"/>
  <rect x="100" y="260" width="440" height="36" rx="18" fill="#dfe6ea" stroke="#9aa9b3" stroke-width="3"/>
  <line x1="140" y1="158" x2="530" y2="158" stroke="#3a8fd1" stroke-width="6"{wcls}/>
  <line x1="530" y1="278" x2="120" y2="278" stroke="#3a8fd1" stroke-width="6"{wcls}/>
  <path d="M540 158 Q 575 158 575 218 Q 575 278 540 278" fill="none" stroke="#9aa9b3" stroke-width="10"/>
  <path d="M540 158 Q 575 158 575 218 Q 575 278 540 278" fill="none" stroke="#3a8fd1" stroke-width="4"{wcls}/>
  <path d="M100 278 Q 76 278 76 320 L 76 352" fill="none" stroke="#9aa9b3" stroke-width="10"/>
  <path d="M100 278 Q 76 278 76 320 L 76 352" fill="none" stroke="#3a8fd1" stroke-width="4"{wcls}/>
  {pots}
  <text x="315" y="198" text-anchor="middle" font-size="11" fill="#4a3f30">pipe 1 · 2 net pots</text>
  <text x="315" y="318" text-anchor="middle" font-size="11" fill="#4a3f30">pipe 2 · 2 net pots</text>
  <rect x="452" y="296" width="8" height="26" rx="3" fill="#f0c75e"/>
  <text x="466" y="316" font-size="10" fill="#4a3f30">water-level strip</text>
  <circle cx="44" cy="150" r="19" fill="{'#3fa9f5' if hot else '#9aa4ad'}"/>
  <text x="44" y="156" text-anchor="middle" font-size="18">✣</text>
  <text x="44" y="186" text-anchor="middle" font-size="10" fill="#4a3f30">fan {'ON' if hot else 'off'}</text>
  <rect x="22" y="206" width="44" height="24" rx="6" fill="#ffffff" stroke="#5f7383" stroke-width="2"/>
  <text x="44" y="222" text-anchor="middle" font-size="10" fill="#1d3b52">DHT22</text>
  <text x="44" y="246" text-anchor="middle" font-size="10" fill="#4a3f30">{r['temp']}°C · {r['rh']}%</text>
  <rect x="40" y="352" width="230" height="84" rx="10" fill="#ffffff" stroke="#7fa8c4" stroke-width="3"/>
  <rect x="43" y="{352 + 81 * (1 - water):.0f}" width="224" height="{81 * water:.0f}" rx="8" fill="#6db3e8" opacity="0.85"/>
  <text x="155" y="400" text-anchor="middle" font-size="15" font-weight="700" fill="#1d3b52">Tank {r['tank']:.0f} %</text>
  <rect x="150" y="340" width="70" height="12" rx="4" fill="#5f7383"/>
  <text x="228" y="350" font-size="10" fill="#4a3f30">ultrasonic sensor</text>
  <rect x="285" y="390" width="60" height="44" rx="8" fill="{'#2ea05a' if r['pump_on'] else '#6b7780'}"/>
  <text x="315" y="417" text-anchor="middle" font-size="12" fill="#fff" font-weight="700">PUMP</text>
  <rect x="400" y="350" width="140" height="40" rx="8" fill="#b7a7d9"/>
  <text x="470" y="375" text-anchor="middle" font-size="11" fill="#222">📡 ESP32 master node</text>
</svg>'''


def farm_page() -> None:
    init()
    scenario = sidebar()
    live = st.sidebar.toggle("Live readings (every 5 s)", value=False)
    p = S.farm
    kit_key = ds.kit_for(p)
    kit = ds.KITS[kit_key]
    now = datetime.now()
    r = ds.readings(now, scenario)
    age = int(p.get("age_days") or 0)
    left = max(0, ds.CROP["cycle_days"] - age)

    title = f"{p['location']} {'wall garden' if kit_key == 'starter' else 'farm'}"
    st.markdown(f'<div class="hero"><div class="hero-kicker">Hydro Monitor · {kit["name"]}</div>'
                f'<div class="hero-title">🌱 {title}</div><div class="hero-sub">📍 {p["location"]}, Qatar &nbsp;·&nbsp; '
                f'🪴 {p.get("count") or kit["pots"] or 4} red amaranth plants, day {age} &nbsp;·&nbsp; 🔄 updated {now:%H:%M}</div></div>',
                unsafe_allow_html=True)
    level, headline, more = ds.status_of(r)
    k1, k2, k3, k4 = st.columns(4)
    kpi(k1, "🪴", "Plants doing well", "4/4" if level == "ok" else ("4/4 ⚠" if level == "watch" else "check now"), "right now")
    kpi(k2, "🌾", "Next harvest", f"in ~{left} days" if left else "ready now", f"planted {ds.planted_date(p):%d %b}")
    kpi(k3, "🛢️", "Water tank", f"{r['tank']:.0f} %", "pump paused" if r["tank"] < ds.CROP["tank_block"] else "pump ready")
    kpi(k4, "💧", "Water saved", "≈ 85 %", "vs growing in soil")

    tab_today, tab_kit, tab_talk = st.tabs(["🌿 Today", "🧰 My kit & harvests", "💬 Notes & questions"])
    with tab_today:
        if live:
            st.fragment(run_every=5)(today_section)(scenario, p)
        else:
            today_section(scenario, p)
        if st.button("🔄 Get fresh advice", type="primary"):
            with st.status("Your AI farm team is checking the farm…", expanded=True) as status:
                for step in ("🌤️ Agri-Environment: air, heat and light", "💧 Soil & Water: tank, pump and nutrients",
                             "🌱 Crop Science: how the plants are coping", "📈 Data & Analytics: trends and what's coming",
                             "🧭 Farm Director: one plan and your to-dos", "✅ Code check: every setting inside the safe limits"):
                    status.write(step)
                    time.sleep(0.6)
                status.update(label="Done: fresh advice below", state="complete")
            S.checked_at = datetime.now()
            S.todo_state = {}
            st.rerun()
    with tab_kit:
        kit_tab(r, p, kit_key)
    with tab_talk:
        talk_tab(r, p)


def today_section(scenario: str, p: dict) -> None:
    r = ds.readings(datetime.now(), scenario)
    level, headline, more = ds.status_of(r)
    icon = {"ok": "🟢", "watch": "🟠", "act": "🔴"}[level]
    html(f'<h3>{icon} F1 · wall garden · red amaranth</h3><div class="headline">{headline}</div>'
         + "".join(f"<div>{m}</div>" for m in more), f"bed {level}")
    cols = st.columns(6)
    vals = [("🌡️", f"{r['temp']} °C", "air"), ("💨", f"{r['rh']} %", "humidity"), ("☀️", f"{r['light']:,}", "light (lux)"),
            ("🛢️", f"{r['tank']:.0f} %", "tank"), ("🚰", "flowing" if r["flowing"] and r["tank"] >= ds.CROP["tank_block"] else "no flow", "pipes"),
            ("⚙️", "ON" if r["pump_on"] else "off", "pump")]
    for col, (i, v, label) in zip(cols, vals):
        col.markdown(f'<div class="reading"><div>{i}</div><div class="v">{v}</div><div class="l">{label}</div></div>', unsafe_allow_html=True)

    st.markdown("### 🤖 Your AI farm team")
    team = ds.team(r, p)
    cols = st.columns(2)
    for i, d in enumerate(team):
        with cols[i % 2]:
            html(f'<div class="dept-head">{DEPT_ICON[d["code"]]} {d["name"]}</div><div class="dept-text">{d["text"]}</div>'
                 + (f'<div class="dept-warn">⚠ {d["warn"]}</div>' if d["warn"] else ""), f"dept {d['status']}")
    message, todos = ds.overview(r, p)
    warnings = sum(1 for d in team if d["warn"])
    html(f'<div class="overview-head">🧭 The big picture</div><div class="overview-text">{message}</div>'
         f'<div class="overview-stats">🔎 {warnings} warning(s) · ✅ {len(todos)} to-do(s) · checked at {S.checked_at:%H:%M} · '
         f'all settings inside the safe limits</div>', "overview")
    st.markdown("### ✅ To do")
    for i, todo in enumerate(todos):
        state = S.todo_state.get(todo)
        a, b, c = st.columns([5.2, 1, 1.8])
        with a:
            html(("✅ " if state == "done" else "⬜ ") + todo + (f' <span class="muted">(you said: {state[4:]})</span>'
                                                                  if state and state.startswith("fix:") else ""), "todo")
        if b.button("Done", key=f"done{i}{todo[:8]}"):
            S.todo_state[todo] = "done"
            st.rerun()
        with c.popover("✏️ Correct it"):
            fix = st.text_input("What should it say or do?", key=f"fix{i}{todo[:8]}")
            if st.button("Save", key=f"save{i}{todo[:8]}") and fix:
                S.todo_state[todo] = f"fix:{fix}"
                st.rerun()


def kit_tab(r: dict, p: dict, kit_key: str) -> None:
    left, right = st.columns([1.3, 1])
    with left:
        st.markdown("#### 🗺️ Your kit")
        st.markdown(kit_svg(r, kit_key), unsafe_allow_html=True)
    with right:
        st.markdown("#### 🧾 Parts and cost (estimate)")
        parts = pd.DataFrame(ds.kit_cost(kit_key), columns=["part", "QR"])
        chart = alt.Chart(parts).mark_bar(cornerRadius=6, color="#8e6fd1").encode(
            x=alt.X("QR:Q"), y=alt.Y("part:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
            tooltip=["part", "QR"])
        st.altair_chart(chart, width="stretch")
        st.markdown(f"**Total ≈ {parts['QR'].sum():,} QR**, fertilizer supplied by Hydro Monitor.")
    st.markdown("#### 🌾 Harvests (red amaranth, every ~5 weeks)")
    start = ds.planted_date(p)
    rows = []
    for k in range(5):
        sow = start + timedelta(days=35 * k)
        rows.append({"cycle": f"Crop {k + 1}", "start": pd.Timestamp(sow), "end": pd.Timestamp(sow + timedelta(days=35)),
                     "harvest": (sow + timedelta(days=35)).strftime("%d %b"),
                     "label": "harvest " + (sow + timedelta(days=35)).strftime("%d %b")})
    df = pd.DataFrame(rows)
    today = pd.DataFrame([{"t": pd.Timestamp(date.today())}])
    bars = alt.Chart(df).mark_bar(cornerRadius=7, height=22, color="#b0245e").encode(
        x=alt.X("start:T", title=None), x2="end:T", y=alt.Y("cycle:N", title=None, sort=None),
        tooltip=["cycle", "harvest"])
    text = alt.Chart(df).mark_text(align="right", dx=-8, color="white", fontWeight="bold").encode(
        x="end:T", y=alt.Y("cycle:N", sort=None), text=alt.Text("label:N"))
    rule = alt.Chart(today).mark_rule(color="#f0c75e", strokeWidth=3).encode(x="t:T")
    st.altair_chart((bars + text + rule).properties(height=210), width="stretch")
    st.caption("Yellow line: today. Each bar ends with a harvest; cut the outer leaves and the plant keeps growing.")
    with st.expander("📋 What the onboarding assistant designed (the 4 blocks)"):
        for title, body in S.blocks:
            st.markdown(f"**{title}**")
            st.code(body, language=None, wrap_lines=True)
        st.write("Ready for the Hydro Monitor team to review.")


FAQ = [
    (("water", "watering", "thirsty", "dry"), lambda r, p: f"You don't need to water: the pump runs {ds.CROP['pump_start']} seconds at a "
     f"time and the water flows through both pipes back to the tank. Just keep the tank topped up (it's at {r['tank']:.0f} % now)."),
    (("harvest", "ready", "pick", "eat"), lambda r, p: f"Red amaranth is ready about {ds.CROP['cycle_days']} days after planting. "
     f"Yours are day {int(p.get('age_days') or 0)}. Cut the big outer leaves first and the plant keeps growing."),
    (("fertilizer", "fertiliser", "nutrient", "food", "feed"), lambda r, p: "Hydro Monitor sends you the fertilizer pack. Add one scoop "
     "to the tank every 2 weeks; the app reminds you. Your next pack is due in 9 days."),
    (("hot", "heat", "temperature", "summer"), lambda r, p: f"Red amaranth likes {ds.CROP['temp_start'][0]}–{ds.CROP['temp_start'][1]} °C. Above "
     f"{ds.CROP['temp_alert']} °C the fan switches on and you'll get a message. Right now it's {r['temp']} °C."),
    (("light", "sun", "lamp", "dark"), lambda r, p: f"It's getting {r['light']:,} lux now. A bright wall near a window is enough; strong "
     "light makes the leaves a deeper red."),
    (("tank", "refill", "top up", "empty"), lambda r, p: f"The tank is at {r['tank']:.0f} %. Top it up when you get the message "
     f"(below {ds.CROP['tank_alert']} %); below {ds.CROP['tank_block']} % the pump pauses to protect itself."),
    (("red", "color", "colour", "pale", "yellow", "leaves"), lambda r, p: "Pale or greener leaves usually mean too much heat or too little "
     "light. Keep it under 30 °C and in bright light, and the red comes back in a week."),
    (("travel", "holiday", "away", "vacation"), lambda r, p: "Going away is fine: fill the tank to the top before you leave and it lasts "
     "about 3 days. The system keeps watering and messages you if anything goes wrong."),
    (("cost", "price", "pay", "money"), lambda r, p: "The Starter Hydroponic Kit is about 1,440 QR installed, and Hydro Monitor supplies the "
     "fertilizer."),
]


def talk_tab(r: dict, p: dict) -> None:
    left, right = st.columns([1, 1.4])
    with left:
        st.markdown("### 📝 Tell us what you see")
        with st.form("note", clear_on_submit=True):
            text = st.text_input("Note", placeholder="e.g. the leaves on top look pale", label_visibility="collapsed")
            if st.form_submit_button("Send") and text:
                S.notes.insert(0, f"{datetime.now():%H:%M} · {text}")
                st.toast("Thanks, the team will look at it.")
        for n in S.notes[:4]:
            st.caption(n)
    with right:
        st.markdown("### 💬 Ask the farm assistant")
        st.caption("Try: When can I harvest? · Do I need to water? · It's very hot, is that okay?")
        for q, a in S.qa[-4:]:
            with st.chat_message("user"):
                st.write(q)
            with st.chat_message("assistant"):
                st.write(a)
        question = st.text_input("Ask anything about your plants…", key="ask_box")
        if st.button("Ask", key="ask_send") and question:
            ql = question.lower()
            answer = next((fn(r, p) for words, fn in FAQ if any(w in ql for w in words)),
                          "Good question! I can help with watering, harvest, fertilizer, heat, light and the tank. "
                          "For anything else, the Hydro Monitor team will get back to you today.")
            S.qa.append((question, answer))
            st.rerun()


PAGES = {"setup": st.Page(setup_page, title="Set up a farm", icon="💬", default=True),
         "farm": st.Page(farm_page, title="My farm", icon="🌱")}
st.navigation(list(PAGES.values())).run()
