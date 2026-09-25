"""Onboarding assistant (Hydro Monitor plan 3.2, 4 steps 1-6).

The farmer describes the farm in their own words; the assistant (the most capable model we can reach) does the
structuring. Its tools: Open-Meteo for the location, the forecast and past weather; the crop file (knowledge.py);
crop suggestions (crop_advice.py) for farmers who haven't chosen a crop; and the department template below.
It produces the four things a person on the team reviews before anything is installed or run:

    1. farm profile   2. hardware plan   3. hard limits and starting ranges   4. the agent network (CrewAI definitions)

How a conversation stays on track:
  * Code picks the next question from QUESTIONS (clear, with examples, in English or Arabic); the model only
    acknowledges the farmer and structures what they said. So it never asks about something already known.
  * A slot is "" while unknown, a value when answered, and None (JSON null) when the farmer answered "none",
    "no" or "don't know": null counts as answered and is never asked again.
  * An answer the model didn't structure (an unexpected but correct one, e.g. water "from the river") is kept
    in the farmer's own words, and no slot is asked more than twice: after that it becomes null.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import llm
import weather
from agents.base import resolve_model
from knowledge import CANDIDATE_CROPS, CROP_FILE

REQUIRED = ["location", "fields", "water", "fertilizer", "power", "internet", "problems"]
NULLABLE = {"crop", "planted", "water", "fertilizer", "power", "internet", "problems", "goal"}   # may end up null
MAX_ASKS = 2

QUESTIONS = {   # slot -> (English, Arabic): one clear question each, with an example
    "location": ("Where is your farm? A town or area is enough, for example 'near Al Khor'.",
                 "أين تقع مزرعتك؟ يكفي اسم المدينة أو المنطقة، مثلاً «قرب الخور»."),
    "fields": ("How many beds or fields do you have, and roughly how big is each one (for example 10 by 20 metres)? "
               "Are they soil or hydroponic, in the open air or in a greenhouse?",
               "كم عدد الأحواض أو الحقول لديك، وما مساحة كل منها تقريباً (مثلاً 10 × 20 متر)؟ وهل هي تربة أم زراعة مائية، في الهواء الطلق أم في بيت محمي؟"),
    "crop": ("What are you growing in each bed? If you haven't decided yet, just say so and I'll suggest crops that suit your farm.",
             "ماذا تزرع في كل حوض؟ إذا لم تقرر بعد فقل ذلك وسأقترح عليك محاصيل تناسب مزرعتك."),
    "planted": ("When did you plant it? Roughly is fine, for example 'three weeks ago'. If it isn't planted yet, say so.",
                "متى زرعته؟ يكفي تقريباً، مثلاً «قبل ثلاثة أسابيع». وإذا لم تزرعه بعد فقل ذلك."),
    "goal": ("What matters most to you from the crop: income from selling at the market, food for your family, or using as little water as possible?",
             "ما الأهم بالنسبة لك: دخل من البيع في السوق، أم طعام لعائلتك، أم استهلاك أقل للماء؟"),
    "water": ("Where does your water come from (tank, well, municipal line…) and how does it reach the plants (hose, drip, sprinklers)?",
              "من أين يأتي الماء (خزان، بئر، شبكة البلدية…) وكيف يصل إلى النباتات (خرطوم، تنقيط، رشاشات)؟"),
    "fertilizer": ("Which fertilizer do you use, and how often? Say 'none' if you don't use any.",
                   "ما السماد الذي تستخدمه وكم مرة؟ قل «لا شيء» إذا لم تستخدم سماداً."),
    "power": ("Is there electricity near the beds, for example in a shed or from solar panels? Say 'none' if not.",
              "هل توجد كهرباء قرب الأحواض، مثلاً في مخزن أو من ألواح شمسية؟ قل «لا» إذا لم توجد."),
    "internet": ("Is there WiFi or mobile data at the farm?", "هل يوجد واي فاي أو بيانات جوال في المزرعة؟"),
    "problems": ("Last one: what goes wrong most often? For example pale leaves, pests, or not knowing how much to water.",
                 "سؤال أخير: ما المشكلة التي تتكرر أكثر؟ مثلاً اصفرار الأوراق أو الآفات أو عدم معرفة كمية الري."),
}
NONE_WORDS = re.compile(
    r"^\s*(no|none|nothing|nope|nah|n/?a|not really|no idea|i don'?t know|don'?t know|dont know|not sure|unknown|skip|"
    r"no problems?|not yet|لا|لا شيء|ما في|ماكو|لا أعرف|ما أعرف|مش عارف|غير معروف)\b[\s.!]*$", re.I)
UNDECIDED = re.compile(r"not (yet )?(decided|sure)|undecided|haven'?t decided|don'?t know what|suggest|no idea|لم أقرر|ما قررت|اقترح", re.I)

SYSTEM = (
    "You are the Hydro Monitor onboarding assistant for small farms in Qatar. The farmer describes the farm in their "
    "own words; you structure it. You do NOT ask questions: the system adds the next question after your text.\n"
    "Return JSON only: {\"ack\": \"one short sentence reacting to what the farmer just said. Be friendly but not "
    "flattering (no 'great choice'): add something useful when you can, e.g. the weather from the tool results or a quick "
    "practical tip; otherwise just 'Got it.'\", \"profile\": {the complete profile}}.\n"
    "Profile keys: location (text), fields (list of {size_m: [width, length] in metres or null, type, crop, planted}), "
    "water, fertilizer, power, internet (short texts), goal (text or null), problems (list of short texts).\n"
    "Rules: keep every value you were given. Put the farmer's answer in the slot the question was about, even if it is "
    "unusual (water 'from the river', power 'solar panels'). Use null when the farmer says none, no, not yet or don't "
    "know. Field types look like 'soil bed, open air', 'soil bed, greenhouse' or 'hydroponic, greenhouse'. Crop names in "
    "lower case; 'undecided' if they haven't chosen. Dates as YYYY-MM-DD using today's date. Leave anything the farmer "
    "hasn't talked about as it is ('' stays '')."
)


def empty_profile() -> dict[str, Any]:
    return {"location": "", "fields": [], "water": "", "fertilizer": "", "power": "", "internet": "", "problems": [],
            "_asked": {}}


def _undecided(profile: dict[str, Any]) -> bool:
    return any((f.get("crop") or "") == "undecided" for f in profile.get("fields") or [])


def missing(profile: dict[str, Any]) -> list[str]:
    """Slots still to ask, in order. '' / [] = unknown; None (null) = answered 'none / don't know'."""
    out = []
    if not profile.get("location"):
        out.append("location")
    fields = [f for f in profile.get("fields") or [] if isinstance(f, dict)]
    if not fields or any(f.get("size_m") == "" or f.get("type") in ("", None) for f in fields) \
            or any("size_m" not in f for f in fields):
        out.append("fields")
    if fields and any(f.get("crop") == "" for f in fields):
        out.append("crop")
    if fields and any(f.get("planted") == "" and f.get("crop") not in ("undecided", None, "") for f in fields):
        out.append("planted")
    if _undecided(profile) and profile.get("goal", "") == "":
        out.append("goal")
    for key in ("water", "fertilizer", "power", "internet"):
        if profile.get(key) == "":
            out.append(key)
    if profile.get("problems") == []:
        out.append("problems")
    return out


def _set_null(profile: dict[str, Any], slot: str) -> None:
    """The farmer answered 'none / don't know' (or we asked twice): record null so it is never asked again."""
    if slot == "fields":
        fields = profile.get("fields") or [{"field_id": "F1", "crop": ""}]
        for f in fields:
            f["size_m"] = f.get("size_m") or None
            f["type"] = f.get("type") or "soil bed, open air"   # the plan's default setup
        profile["fields"] = fields
    elif slot == "crop":
        for f in profile.get("fields") or []:
            if f.get("crop") == "":
                f["crop"] = "undecided"
    elif slot == "planted":
        for f in profile.get("fields") or []:
            if f.get("planted") == "":
                f["planted"] = None
    elif slot == "location":
        profile["location"] = profile.get("location") or "Qatar"
    elif slot == "problems":
        profile["problems"] = None
    else:
        profile[slot] = None


def _keep_raw(profile: dict[str, Any], slot: str, text: str) -> None:
    """The model didn't structure an answer to a simple text slot: keep the farmer's own words."""
    text = text.strip()
    if not text or len(text) > 200:
        return
    if slot in ("water", "fertilizer", "power", "internet", "goal") and profile.get(slot) == "":
        profile[slot] = text
    elif slot == "problems" and profile.get("problems") == []:
        profile["problems"] = [p.strip() for p in re.split(r";|,| and |\n", text) if p.strip()][:5]
    elif slot == "location" and not profile.get("location"):
        profile["location"] = text


def _clean_size(size: Any) -> list[float] | None | str:
    if size is None:
        return None
    if isinstance(size, str):
        nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", size)][:2]
        return nums if len(nums) == 2 else ""
    if isinstance(size, (list, tuple)):
        nums = []
        for n in size:
            try:
                nums.append(float(n))
            except (TypeError, ValueError):
                pass
        return nums[:2] if len(nums) >= 2 else ""
    return ""


def _merge(old: dict[str, Any], new: dict[str, Any] | None, slot: str | None = None) -> dict[str, Any]:
    """Keep what we knew; take the model's values. A null from the model counts only for the slot the farmer was
    answering: models like to fill every key they haven't heard about with null, which would skip those questions."""
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in old.items()}
    new = new or {}
    for key in ("location", "water", "fertilizer", "power", "internet", "goal"):
        if key in new and key in out and out[key] == "":        # never overwrite an earlier answer
            value = new[key]
            if value is None and key in NULLABLE and key == slot:
                out[key] = None
            elif isinstance(value, str) and value.strip():
                out[key] = value.strip()
        elif key == "goal" and "goal" not in out and isinstance(new.get("goal"), str) and new["goal"].strip():
            out["goal"] = new["goal"].strip()
    if out.get("problems") == []:
        value = new.get("problems")
        if isinstance(value, str) and value.strip():
            out["problems"] = [p.strip() for p in re.split(r";|\n", value) if p.strip()]
        elif isinstance(value, list) and [p for p in value if isinstance(p, str) and p.strip()]:
            out["problems"] = [p.strip() for p in value if isinstance(p, str) and p.strip()]

    before = [f for f in old.get("fields") or [] if isinstance(f, dict)]
    after = [f for f in new.get("fields") or [] if isinstance(f, dict)] if isinstance(new.get("fields"), list) else []
    count = max(len(before), len(after))
    fields = []
    for i in range(count):
        o = before[i] if i < len(before) else {}
        n = after[i] if i < len(after) else (after[0] if after and len(after) < len(before) else {})
        size = o.get("size_m", "")
        if size in ("", [], None) and o.get("size_m", "") == "":
            size = _clean_size(n.get("size_m", "")) if "size_m" in n else ""
        ftype = o.get("type") or (n.get("type") or "").strip()
        crop = o.get("crop") if o.get("crop") not in ("", None) else ((n.get("crop") or "").strip().lower())
        if crop in ("undecided", "unknown", "none", "null") and slot != "crop":
            crop = ""        # the model guessing 'undecided' before we asked: keep asking
        planted = o.get("planted", "")
        if planted == "":
            p = n.get("planted", "")
            planted = None if p is None and "planted" in n and slot == "planted" else (
                p if isinstance(p, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p) else "")
        fields.append({"field_id": f"F{i + 1}", "size_m": size, "type": ftype, "crop": crop, "planted": planted})
    out["fields"] = fields
    return out


NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "a couple of": 2, "a few": 3}


AR_NUMBERS = {"واحد": 1, "اثنين": 2, "اثنان": 2, "ثلاث": 3, "ثلاثة": 3, "أربع": 4, "اربع": 4, "أربعة": 4, "خمس": 5,
              "خمسة": 5, "ست": 6, "ستة": 6, "سبع": 7, "ثمان": 8, "تسع": 9, "عشر": 10, "عشرة": 10}
AR_UNITS = {"يوم": 1, "أيام": 1, "ايام": 1, "أسبوع": 7, "اسبوع": 7, "أسابيع": 7, "اسابيع": 7, "شهر": 30, "أشهر": 30, "اشهر": 30, "شهور": 30}
AR_DUALS = {"يومين": 2, "أسبوعين": 14, "اسبوعين": 14, "شهرين": 60}   # Arabic dual: 'two weeks' is one word


def _relative_date(text: str) -> str | None:
    """'about three weeks ago' / 'قبل أسبوعين' -> YYYY-MM-DD (code's date arithmetic beats the model's)."""
    days = None
    m = re.search(r"(\d+|a couple of|a few|an|a|one|two|three|four|five|six|seven|eight|nine|ten)\s+(day|week|month)s?\s+ago",
                  text.lower())
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1)]
        days = n * {"day": 1, "week": 7, "month": 30}[m.group(2)]
    elif "قبل" in text:
        after = text.split("قبل", 1)[1]
        dual = next((d for w, d in AR_DUALS.items() if w in after), None)
        m = re.search(r"(\d+|" + "|".join(AR_NUMBERS) + r")?\s*(" + "|".join(AR_UNITS) + r")", after)
        if dual:
            days = dual
        elif m:
            n = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else AR_NUMBERS.get(m.group(1) or "", 1)
            days = n * AR_UNITS[m.group(2)]
    if days is None:
        return None
    return date.fromordinal(date.today().toordinal() - days).isoformat()


AR_CROPS = {v["ar"]: v["name"].split(" (")[0].lower() for v in CANDIDATE_CROPS.values() if v.get("ar")}
AR_CROPS.update({"قطيفة": "purple amaranth", "ملوخيه": "mulukhiyah", "بندورة": "tomato", "فقوس": "snake cucumber",
                 "كوسة": "zucchini", "باذنجان": "eggplant", "خيار": "cucumber", "فلفل": "sweet pepper"})


def _crop_name(name: str) -> str:
    """Arabic crop names -> the English names the crop file uses ('خس' -> 'lettuce')."""
    name = (name or "").strip()
    for ar, en in sorted(AR_CROPS.items(), key=lambda kv: -len(kv[0])):
        if ar in name:
            return en
    return name.lower()


def rule_fill(profile: dict[str, Any], farmer_text: str, slot: str | None) -> dict[str, Any]:
    """Code-side structuring the model may miss: bed count and size, bed type, planting date, 'none' answers."""
    text = farmer_text.lower()
    if slot and NONE_WORDS.match(farmer_text) and slot in NULLABLE | {"fields", "location"}:
        _set_null(profile, slot)
    if slot == "crop" and UNDECIDED.search(farmer_text):
        for f in profile.get("fields") or []:
            if f.get("crop") in ("", None):
                f["crop"] = "undecided"
    if slot == "planted" and re.search(r"not (planted|yet)|haven'?t planted|next (week|month)|لم أزرع", text):
        for f in profile.get("fields") or []:
            if f.get("planted") == "":
                f["planted"] = None
    planted = _relative_date(farmer_text) if (slot == "planted" or any(w in text for w in ("plant", "sow", "ago", "زرع"))) else None
    covered = any(w in text for w in ("greenhouse", "covered", "tunnel", "net house", "بيت محمي", "بيوت محمية", "صوبة", "دفيئة"))
    open_air = any(w in text for w in ("open air", "open-air", "outdoor", "outside", "in the open", "مكشوف", "الهواء الطلق", "في العراء"))
    for f in profile.get("fields") or []:   # Arabic bed types from the model -> the English terms the planner uses
        t = f.get("type") or ""
        if t and _arabic(t):
            base = "hydroponic" if "مائي" in t else "soil bed"
            where = ", greenhouse" if any(w in t for w in ("محمي", "صوبة", "دفيئة")) else ", open air" if any(
                w in t for w in ("الطلق", "مكشوف", "العراء")) else ""
            f["type"] = base + where
    dims = re.findall(r"\b(\d+(?:\.\d+)?)\s*(?:by|x|×|\*|في)\s*(\d+(?:\.\d+)?)\b", text)
    size = [float(dims[0][0]), float(dims[0][1])] if dims else None
    count = re.search(r"\b(\d+|one|two|three|four|five|six)\s+(?:\w+\s+)?(?:beds?|fields?|plots?|greenhouses?)\b", text)
    n = int(count.group(1)) if count and count.group(1).isdigit() else NUMBERS.get(count.group(1), 1) if count else None
    if n is None:   # Arabic: the dual 'حوضين' (two beds), or 'ثلاثة أحواض'
        if re.search(r"(حوضين|حقلين|بيتين|مزرعتين)", farmer_text):
            n = 2
        else:
            m = re.search(r"(\d+|" + "|".join(AR_NUMBERS) + r")\s*(أحواض|احواض|حقول|بيوت)", farmer_text)
            n = (int(m.group(1)) if m.group(1).isdigit() else AR_NUMBERS[m.group(1)]) if m else None
    fields = [f for f in profile.get("fields") or [] if isinstance(f, dict)]
    for f in fields:
        if f.get("crop"):
            f["crop"] = _crop_name(f["crop"])
    if n:
        base = fields[0] if fields else {"size_m": "", "type": "", "crop": "", "planted": ""}
        while len(fields) < min(n, 20):
            fields.append({**base, "field_id": f"F{len(fields) + 1}"})
    for f in fields:
        if planted and f.get("crop") not in ("undecided",):
            f["planted"] = planted
        if size and f.get("size_m") in ("", None):
            f["size_m"] = size
        kind = f.get("type") or ""
        if (covered or open_air) and not kind:
            kind = "hydroponic" if "hydro" in text else "soil bed"
        if open_air and "open" not in kind and "greenhouse" not in kind:
            kind = kind + ", open air"
        elif covered and "greenhouse" not in kind and "open" not in kind:
            kind = kind + ", greenhouse"
        f["type"] = kind
    profile["fields"] = fields
    return profile


ARABIC_PLACES = {"الخور": "Al Khor", "الوكرة": "Al Wakrah", "الدوحة": "Doha", "الريان": "Al Rayyan",
                 "أم صلال": "Umm Salal", "ام صلال": "Umm Salal", "الشمال": "Madinat ash Shamal", "الشحانية": "Al Shahaniya",
                 "دخان": "Dukhan", "مسيعيد": "Mesaieed", "الخيسة": "Al Kheesa", "سميسمة": "Simaisma", "الغويرية": "Al Ghuwariyah",
                 "الذخيرة": "Al Thakhira", "لوسيل": "Lusail", "الكرعانة": "Al Karaana"}


def _place_candidates(location: str) -> list[str]:
    """'near Al Khor, north of Doha' -> ['Al Khor', 'Doha', ...] for the geocoder (Arabic town names too)."""
    out = [en for ar, en in ARABIC_PLACES.items() if ar in location]
    location = re.sub(r"\bفي\b|\bقرب\b|\bشمال\b|\bجنوب\b", ",", location)
    for part in re.split(r",|\bnear\b|\bnorth of\b|\bsouth of\b|\beast of\b|\bwest of\b|\bin\b|\bclose to\b|\bقرب\b", location, flags=re.I):
        part = part.strip(" .")
        if part and part.lower() not in ("qatar", "the", "قطر") and part not in out:
            out.append(part)
            if not part.lower().startswith(("al ", "al-", "ال")):
                out.append(f"Al {part}")        # 'Wakrah' -> 'Al Wakrah', as the geocoder knows it
    return out or [location]


def lookup(profile: dict[str, Any]) -> str | None:
    """Tool: Open-Meteo geocoding, the coming week and the past month. Adds coordinates to the profile."""
    place = next((p for p in (weather.geocode(c) for c in _place_candidates(profile["location"])) if p), None)
    if not place:
        profile["latitude"], profile["longitude"] = 25.29, 51.53   # Doha: good enough for Qatar-wide weather
        profile["place"] = f"{profile['location']} (weather from Doha)"
        return None
    profile.update(latitude=round(place["latitude"], 3), longitude=round(place["longitude"], 3),
                   place=f"{place['name']}, {place.get('country') or ''}".strip(", "), utc_offset="+03:00")
    bits = [f"{profile['place']} at {place['latitude']:.2f} N, {place['longitude']:.2f} E"]
    bits += [b for b in (weather.week_outlook(place["latitude"], place["longitude"]),
                         weather.past_month(place["latitude"], place["longitude"])) if b]
    return "; ".join(bits)


def onboarding_model(route: dict[str, Any]) -> str:
    """The most capable model we can reach: the route's 'onboarding' model if set, else the Director's model."""
    state = {"installed_models": llm.available_models(), "fallback_model": route["fallback"], "model_map": route["map"]}
    return resolve_model(route.get("onboarding") or "qwen2.5:7b", state)


def _arabic(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" for ch in text)


def _clean_ack(ack: str, ar: bool) -> str:
    """One short sentence in the farmer's language, or nothing. Small models drift into other scripts or ask
    their own questions; the code's question follows anyway."""
    ack = ack.strip()
    if not ack or any("\u3040" <= ch <= "\u9fff" or "\uac00" <= ch <= "\ud7af" for ch in ack) or _arabic(ack) != ar:
        return ""
    first = re.split(r"(?<=[.!؟])\s+", ack)[0]
    if first.rstrip().endswith(("?", "؟")) or len(first) > 220:
        return ""
    return first


def step(history: list[dict[str, str]], profile: dict[str, Any], model: str) -> dict[str, Any]:
    """One turn. history: [{role: farmer|assistant|tool, text}]. Returns {reply, profile, tool?, done, asking}."""
    profile = {**empty_profile(), **profile}
    asked: dict[str, int] = dict(profile.get("_asked") or {})
    slot = profile.get("_last_asked")                      # what the farmer's message is answering
    farmer_said = next((h["text"] for h in reversed(history) if h["role"] == "farmer"), "")
    ar = _arabic(farmer_said)
    profile = rule_fill(profile, farmer_said, slot)
    tool_text = None
    if profile.get("location") and "latitude" not in profile:
        tool_text = lookup(profile)

    transcript = "\n".join(f"{h['role'].upper()}: {h['text']}" for h in history[-10:])
    prompt = (f"Today is {date.today().isoformat()}.\nProfile so far: "
              f"{json.dumps({k: v for k, v in profile.items() if not k.startswith('_')}, ensure_ascii=False)}\n"
              + (f"The farmer's last message answers the question about: {slot}.\n" if slot else "")
              + (f"Tool results: {tool_text}\n" if tool_text else "")
              + f"Conversation:\n{transcript}\n\nUpdate the profile from the farmer's last message and write your ack "
              + ("in Arabic." if ar else "in English."))
    answer = llm.chat_json(model, SYSTEM, prompt, temperature=0.3) or {}
    updated = _merge(profile, answer.get("profile"), slot)
    updated = rule_fill(updated, farmer_said, slot)
    if slot and slot in missing(updated):
        _keep_raw(updated, slot, farmer_said)             # a correct but unexpected answer: keep their words
    if updated.get("location") and "latitude" not in updated:
        tool_text = tool_text or lookup(updated)
    for s in missing(updated):                            # asked twice already: record null and move on
        if asked.get(s, 0) >= MAX_ASKS:
            _set_null(updated, s)
    todo = missing(updated)
    ack = _clean_ack(str(answer.get("ack") or answer.get("reply") or ""), ar)
    if not todo:
        reply = (ack + " " if ack else "") + ("شكراً، هذا كل ما أحتاجه." if ar else "Thanks, that's everything I need.")
        updated["_last_asked"] = None
    else:
        nxt = todo[0]
        asked[nxt] = asked.get(nxt, 0) + 1
        question = QUESTIONS[nxt][1 if ar else 0]
        if asked[nxt] > 1:
            question = ("لم أفهم تماماً: " if ar else "Sorry, I didn't quite get that. ") + question
        reply = (ack + " " if ack else "") + question
        updated["_last_asked"] = nxt
    updated["_asked"] = asked
    reply = reply[:1].upper() + reply[1:]
    return {"reply": reply, "profile": updated, "tool": tool_text, "done": not todo, "asking": updated.get("_last_asked")}


def first_question(profile: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    """The opening message; marks 'location' as the slot being asked."""
    profile = profile or empty_profile()
    profile["_last_asked"] = "location"
    profile["_asked"] = {"location": 1}
    return "Hello! I'll help set up Hydro Monitor for your farm. " + QUESTIONS["location"][0], profile


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile without the conversation's bookkeeping keys."""
    return {k: v for k, v in profile.items() if not k.startswith("_")}


# ── design: the four outputs ─────────────────────────────────────────────
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def beds_text(n: int) -> str:
    return "bed" if n == 1 else f"{WORDS.get(n, n)} beds"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.split(",")[0].lower()) or "farm"


def _covered(field: dict[str, Any]) -> bool:
    return any(w in (field.get("type") or "").lower() for w in ("greenhouse", "covered", "tunnel", "net house", "indoor"))


def hardware_plan(profile: dict[str, Any]) -> dict[str, Any]:
    """Which sensor set and actuator set goes in each field (plan 6.2). The tank is measured once, on F1."""
    water = (profile.get("water") or "").lower()
    tank = "tank" in water or "خزان" in water
    fields = []
    for i, f in enumerate(profile["fields"]):
        devices = [{"device_id": "dht11", "kinds": ["temp_air", "humidity"]},
                   {"device_id": "soil", "kinds": ["soil_moisture"]},
                   {"device_id": "light", "kinds": ["light"]}]
        if tank and i == 0:
            devices.append({"device_id": "level", "kinds": ["level"]})
        if _covered(f):
            devices.append({"device_id": "mq135", "kinds": ["air_quality"]})
        actuators = [{"device_id": "relay1", "kind": "pump", "what": "drip-line pump"}]
        if _covered(f):
            actuators.append({"device_id": "relay2", "kind": "fan", "what": "fan"})
        fields.append({"field_id": f["field_id"], "sensor_set": {"module_id": f"S{i + 1}", "devices": devices},
                       "actuator_set": {"module_id": f"A{i + 1}", "devices": actuators}})
    power = (profile.get("power") or "").lower()
    where = "shed" if "shed" in power else "solar box by the beds" if "solar" in power else "place with power and shade"
    notes = f"The master node and the farm computer go in the {where}."
    if not profile.get("power"):
        notes += " There is no mains power, so the master needs a solar panel and battery."
    if tank and len(fields) > 1:
        notes += " Only S1 measures the tank, since the beds share it."
    return {"farm_id": None, "master": {"module_id": "M1", "hardware": "ESP32 + nRF24L01 + SD card", "location": where},
            "fields": fields, "notes": notes}


def limits_for(profile: dict[str, Any], hardware: dict[str, Any]) -> dict[str, Any]:
    """Hard limits and starting ranges from the crop file; crops without an entry use their temperature profile."""
    crop = next((f["crop"] for f in profile["fields"] if f.get("crop") and f["crop"] != "undecided"), "undecided")
    key = crop.replace(" ", "_")
    if key in CROP_FILE:
        entry = CROP_FILE[key]
        hard, start, notes = entry["hard"], entry["start"], entry["notes"]
    else:
        air = CANDIDATE_CROPS.get(key, {}).get("air", (18, 30, 10, 35))
        rh = CANDIDATE_CROPS.get(key, {}).get("rh", (50, 75, 35, 85))
        hard = {"soil_moisture": [25, 55], "temp_air": [air[2], air[3]], "humidity": [rh[2], rh[3]],
                "level": [15, 100], "pump_seconds": [10, 300]}
        start = {"soil_moisture": [30, 45], "temp_air": [air[0], air[1]], "humidity": [rh[0], rh[1]],
                 "level": [20, 100], "pump_seconds": 120}
        notes = {"crop": f"no crop-file entry for '{crop}': general values, to be checked by the team"}
    per_field = {}
    for f in hardware["fields"]:
        kinds = {k for d in f["sensor_set"]["devices"] for k in d["kinds"]}
        per_field[f["field_id"]] = {k: v for k, v in start.items() if k in kinds or k == "pump_seconds"}
    return {"farm_id": None, "crop": crop, "hard": hard, "start": per_field, "notes": notes}


TEMPLATE = [   # the department template (plan 3.3); needs = sensor kinds a role can't work without
    {"id": "agri_environment", "code": "ENV", "name": "Agri-Environment", "llm": "ollama/llama3.2:3b",
     "sets": ["temp_air", "humidity"], "depends_on": [], "tools": ["field_summary", "forecast"],
     "goal": "Keep the beds out of heat and dry-air stress, and warn early when the forecast turns hot",
     "specialists": [
         {"id": "climate", "name": "Climate", "skill": "range_monitor", "inputs": ["temp_air", "humidity"], "needs": ["temp_air"]},
         {"id": "light", "name": "Light & Photosynthesis", "skill": "range_monitor", "inputs": ["light"], "needs": ["light"]},
         {"id": "air_quality", "name": "Air quality", "skill": "range_monitor", "inputs": ["air_quality"], "needs": ["air_quality"],
          "why_not": "open-air beds, no sensor"}]},
    {"id": "soil_water", "code": "SOIL", "name": "Soil & Water", "llm": "ollama/gemma2:2b",
     "sets": ["soil_moisture", "level", "pump_seconds"], "depends_on": [], "tools": ["field_summary", "forecast", "pump_log"],
     "goal": "Keep soil moisture in range with as little water as possible, and never let the pump run the tank dry",
     "specialists": [
         {"id": "irrigation", "name": "Irrigation", "skill": "irrigation", "inputs": ["soil_moisture", "level"], "needs": ["soil_moisture"]},
         {"id": "nutrients", "name": "Nutrients & Fertilizer", "skill": "fertilizer", "inputs": [], "needs": []},
         {"id": "salinity", "name": "Salinity", "skill": "range_monitor", "inputs": ["soil_ec"], "needs": ["soil_ec"],
          "why_not": "no EC sensor, so fertilizer advice only"}]},
    {"id": "crop_science", "code": "CROP", "name": "Crop Science", "llm": "ollama/phi3.5",
     "sets": [], "depends_on": ["agri_environment", "soil_water"], "tools": ["department_reports", "growth_stage", "farmer_notes"],
     "goal": "Judge how the plants are coping at their growth stage, and warn about disease, pests and leaf quality",
     "specialists": [
         {"id": "physiology", "name": "Crop Physiology", "skill": "crop_physiology", "inputs": ["temp_air"], "needs": ["temp_air"]},
         {"id": "leaf_quality", "name": "Leaf Quality & Pigment", "skill": "leaf_quality", "inputs": ["temp_air", "light"], "needs": ["temp_air"]},
         {"id": "plant_health", "name": "Plant Health", "skill": "plant_health", "inputs": ["temp_air", "humidity", "light", "soil_moisture"], "needs": ["temp_air"]}]},
    {"id": "data_analytics", "code": "DATA", "name": "Data & Analytics", "llm": "ollama/qwen2.5:3b",
     "sets": [], "depends_on": [], "tools": ["trend", "anomaly", "forecast_6h", "field_compare"],
     "goal": "Say what changed, what is unusual and what is coming, with numbers",
     "specialists": [
         {"id": "trend", "name": "Trend", "skill": "trend", "inputs": ["*"], "needs": []},
         {"id": "anomaly", "name": "Anomaly", "skill": "anomaly", "inputs": ["*"], "needs": []},
         {"id": "forecast", "name": "Forecast", "skill": "forecast", "inputs": ["*"], "needs": []}]},
]


def _focus_lines(profile: dict[str, Any], model: str | None) -> dict[str, str]:
    """One sentence per department on what to watch most on this farm, written by the assistant (optional)."""
    if not model:
        return {}
    prompt = (f"Farm profile: {json.dumps(profile, ensure_ascii=False)}\nFor each department id "
              f"({', '.join(d['id'] for d in TEMPLATE)}), write one sentence telling that department what to watch most "
              "on this farm, based on its crop, setup and problems. JSON only: {\"agri_environment\": \"...\", ...}")
    out = llm.chat_json(model, "You design farm agent networks. Be specific and brief.", prompt, temperature=0.3) or {}
    return {k: str(v) for k, v in out.items() if isinstance(v, str)}


def network_for(profile: dict[str, Any], hardware: dict[str, Any], limits: dict[str, Any], model: str | None) -> dict[str, Any]:
    """Fill the department template: keep what the sensors can feed, drop the rest with the reason, write each
    role around the farm's crop and problems (plan 3.2)."""
    kinds = {k for f in hardware["fields"] for d in f["sensor_set"]["devices"] for k in d["kinds"]}
    crop = limits["crop"]
    n = len(profile["fields"])
    beds = " and ".join(f["field_id"] for f in profile["fields"])
    types = ", ".join(sorted({f["type"] for f in profile["fields"] if f.get("type")})) or "soil bed"
    place = profile.get("place") or profile.get("location")
    problems = "; ".join(profile.get("problems") or []) or "none reported"
    hard = limits["hard"]
    focus = _focus_lines(profile, model)
    departments = []
    for t in TEMPLATE:
        keep = [s for s in t["specialists"] if all(k in kinds for k in s["needs"])]
        left_out = [{"role": s["name"], "why": s.get("why_not", "no sensor for it")} for s in t["specialists"] if s not in keep]
        sets = [s for s in t["sets"] if s in kinds or s == "pump_seconds"]
        backstory = (f"You look after {n} {types} {'bed' if n == 1 else 'beds'} ({beds}) of {crop} near {place}. "
                     f"The farmer's problems: {problems}. Hard limits: "
                     + ", ".join(f"{s} {hard[s][0]:g}-{hard[s][1]:g}" for s in (sets or ["temp_air"]) if s in hard)
                     + ". You get a 24-hour summary and the forecast. " + (focus.get(t["id"], "") + " " if focus.get(t["id"]) else "")
                     + "Reply only in the department JSON format.")
        departments.append({
            "id": t["id"], "code": t["code"], "name": t["name"], "depends_on": t["depends_on"], "sets": sets,
            "agent": {"role": f"{t['name']} agent for {beds} ({crop}, {types}, {place})", "goal": t["goal"],
                      "backstory": backstory, "llm": t["llm"], "tools": t["tools"]},
            "specialists": [{k: v for k, v in s.items() if k not in ("needs", "why_not")} for s in keep],
            "left_out": left_out})
    specialists = sum(len(d["specialists"]) for d in departments)
    undecided = crop == "undecided"
    extra_name = "Crop Suggestion" if undecided else "Market & Strategy"
    extras = [{
        "id": "market_strategy", "code": "MKT", "name": extra_name, "advice_only": True, "depends_on": [], "sets": [],
        "agent": {"role": f"{extra_name} adviser for a small farm near {place}",
                  "goal": "Recommend which crop to plant in each bed now and in the coming months, with when it is ready, "
                          "how much it could bring in and how much water it needs" if undecided else
                          "Point out the most useful crop or price opportunity, with its numbers and how reliable they are",
                  "backstory": "You think like a farm business adviser in Qatar. You always say how reliable a price is and never "
                               "call gross revenue profit. Your advice never changes ranges. Reply only in the department JSON format.",
                  "llm": "ollama/qwen2.5:3b", "tools": ["qatar_open_data", "price_table", "web_search"]},
        "specialists": [{"id": "crop_suggest", "name": "Crop Suggestion", "skill": "crop_suggest", "inputs": []},
                        {"id": "market", "name": "Market Price", "skill": "market_watch", "inputs": []},
                        {"id": "profit", "name": "Profitability", "skill": "profitability", "inputs": []}],
        "left_out": []}]
    return {
        "farm_id": None, "designed_by": "onboarding assistant, awaiting team review", "designed_on": date.today().isoformat(),
        "process": "sequential",
        "summary": f"{specialists} specialists and a Farm Director will look after your {beds_text(n)}.",
        "departments": departments, "extras": extras,
        "director": {"id": "farm_director", "code": "DIR", "name": "Farm Director", "languages": ["en", "ar"],
                     "agent": {"role": f"Farm Director for the {crop} beds near {place}",
                               "goal": "Turn the department reports into one plan: ranges for each field, a short message for the farmer, and to-dos that need a person",
                               "backstory": "You are the one larger model over all departments. You read short reports, not the raw data. "
                                            "You set ranges only inside the hard limits, change them only when the reports give a reason, and keep "
                                            "the farmer's message short, plain and kind. The farmer reads it in Arabic and English.",
                               "llm": "ollama/qwen2.5:7b", "tools": ["department_reports", "ranges_in_force", "hard_limits"]}}}


def design(profile: dict[str, Any], farm_id: str, model: str | None = None) -> dict[str, Any]:
    """The four outputs, ready for review."""
    full = {"farm_id": farm_id, "name": f"{(profile.get('place') or profile['location']).split(',')[0]} farm",
            "location": profile.get("place") or profile["location"], "latitude": profile.get("latitude"),
            "longitude": profile.get("longitude"), "utc_offset": profile.get("utc_offset", "+03:00"),
            "crop": next((f["crop"] for f in profile["fields"] if f.get("crop")), "undecided"),
            **{k: profile.get(k) for k in ("fields", "water", "fertilizer", "power", "internet", "problems", "goal")}}
    hardware = hardware_plan(profile)
    limits = limits_for(profile, hardware)
    network = network_for(profile, hardware, limits, model)
    for part in (hardware, limits, network):
        part["farm_id"] = farm_id
    import crop_advice

    return {"profile": full, "hardware": hardware, "limits": limits, "network": network,
            "crops": crop_advice.suggest({"profile": full})}


def proposal_text(parts: dict[str, Any]) -> str:
    """The assistant's closing suggestion, in the plan's words (6.1)."""
    hw, net = parts["hardware"], parts["network"]
    n = len(hw["fields"])
    pumps = "a small pump on a drip line" + (" and a fan" if any(len(f["actuator_set"]["devices"]) > 1 for f in hw["fields"]) else "")
    left = [f"you don't need {'an' if lo['role'][0].lower() in 'aeiou' else 'a'} {lo['role'].lower()} sensor ({lo['why']})"
            for d in net["departments"] for lo in d["left_out"] if lo["why"].startswith("open-air")]
    text = (f"Here's what I suggest. {'Each bed' if n > 1 else 'The bed'} gets a sensor set and {pumps}, and the master goes in the "
            f"{hw['master']['location']}. ")
    if left:
        text += f"The beds are in the open air, so {left[0].split(' (')[0]}. "
    count = sum(len(d["specialists"]) for d in net["departments"])
    text += f"I'll also set up {count} AI specialists and a Farm Director for your {beds_text(n)}. "
    crops = parts.get("crops", {}).get("fields", {})
    first = next(iter(crops.values()), {})
    if parts["profile"].get("crop") == "undecided" and first.get("now"):
        best = first["now"][:2]
        text += ("You haven't chosen a crop yet: right now " + " or ".join(r["name"].split(" (")[0].lower() for r in best)
                 + f" would suit your beds ({best[0]['reasons'][0]}). ")
    return text + "Shall I show you the details?"