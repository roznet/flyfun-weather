"""The Observed tab's one-glance highlight (#697).

Code does the weather, the model phrases it. :func:`facts` reduces one live
tick (glance, ribbon bands, storms, SIGMETs, change rows) to a short facts
block; the model only chooses what leads and words it, under the product's
voice rules (no verdict, facts only, plain words). No weather analysis by the
model, so a highlight can never say something the tick did not already know.

**Off the critical path by construction.** :func:`ensure_highlight` runs *after*
``commit_live_update`` has written the layer, and patches ``glance.highlight``
in a second small write. The deterministic blocks are therefore servable ~1 s
earlier, a model failure can never roll back a tick, and the ↻ press returns
without waiting for a model call (measured on prod 2026-10-07: p50 1.04 s,
p95 1.19 s on the busiest real tick, against a 3–5 s ↻ refresh).

**Written but not displayed** (owner, 2026-10-07). Every live flight's
highlight is generated and logged so real flight days can be reviewed and the
prompt calibrated; no client renders it yet. The review log
(``live_highlights.jsonl``) keeps the facts alongside the text, because a line
that reads wrong is otherwise ambiguous between the model's phrasing and the
facts block feeding it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Review log, one record per generation attempt (kept, reused or rejected).
#: Append-only and per flight, like ``live_history.jsonl``: ``live.json`` holds
#: only the newest highlight, so this is the only place a flight day can be
#: read back from afterwards.
LIVE_HIGHLIGHT_LOG = "live_highlights.jsonl"

DEFAULT_MODEL = "claude-haiku-4-5"
#: Haiku's p95 on the busiest real tick is 1.19 s; 5 s is a hung-connection
#: guard, not a latency budget. Nothing waits on this call, so one retry is
#: free — but two would let a flapping API hold a tick's worth of threads.
REQUEST_TIMEOUT_S = 5.0
MAX_RETRIES = 1
#: Hard ceiling (owner, 2026-10-07). The prompt still asks for 25 words so the
#: model keeps leading hard; 40 is where we stop trusting it to have led.
MAX_WORDS = 40
#: ~70 output tokens covers 40 words with room for punctuation; a longer
#: generation is a prompt failure and the post-check rejects it anyway.
MAX_TOKENS = 200

SYSTEM = """You write the one-glance highlight at the top of a pilot's in-flight weather screen.

You get FACTS about one flight at one moment: where the flight is, the departure and destination conditions, the airports along the route (METAR now, TAF at the time the flight is abeam, their position against the route), SIGMETs, rain and convective cells near the route ahead (from radar) and how they move, and what changed since the briefing.

Write a highlight of at most two short sentences, 25 words in total at most, that tells the pilot what deserves their attention along the route ahead, most important first. Do not repeat the route or the airports' names as a title.

What leads, in this order:
1. Hazards on the track ahead: an active SIGMET covering the route (always mention it, with where it covers), cells within 10 NM of the track (lightning and closing ones first), rain lying over the track.
2. The destination or an alternate that is non-VFR or getting worse, at the time the flight gets there.
3. A notable airport along the route.
When a hazard family is covered by a SIGMET and by radar, say it once (the SIGMET's span) and add only what radar adds (a cell with lightning, its motion).
Mention a missing source (radar, METAR) only when leaving it out would make the route sound clear.

Rules:
- Use only the facts given. Never add weather, causes, forecasts or numbers that are not in them.
- No verdict and no advice: never say safe, unsafe, go, no-go, avoid, divert, recommend or should. Point at the thing; the pilot decides.
- Plain cockpit words. Places as distance along the route ("mid-route", "near LFMD") or ICAO codes. Times in Z.
- Give every distance, time and figure exactly as the facts give it. Do not round it, convert it, or work out a span, total or difference of your own: say "from 235 NM to the destination" when the facts say 235, never "the last 41 NM".
- Keep every condition at the place the facts give it. A route airport's METAR is that airport's, never the destination's.
- Airports along the route matter when they show something notable (non-VFR, showers, thunderstorms, a worse TAF at your time); don't list VFR ones.
- "Cell" for a radar core; say "thunderstorm" only when the facts give lightning for it.
- Say how weather moves relative to the route when the facts give it (crossing it, moving away, closing).
- Prefer the big picture over a list: one rain area crossing the route matters more than its parts.
- When nothing notable is ahead, say so plainly in one short sentence (for example "Quiet route ahead: VFR at both ends, no cells near the track.").
- Output only the highlight text, no preamble, no quotes, no markdown."""


def highlight_enabled() -> bool:
    """Generation is on unless switched off, and dark without a key.

    Mirrors ``DISABLE_LIVE_LAYER``: the kill switch is the thing you set, so a
    deploy that forgets a variable degrades to no highlight rather than to an
    unbilled surprise. No key is not an error — it is how a dev worktree and
    the test suite run the whole tick without calling anything.
    """
    if os.environ.get("DISABLE_LIVE_HIGHLIGHT", "").strip() in ("1", "true", "yes"):
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --- Facts ------------------------------------------------------------------
# Built from the serialized layer (``live.json`` / a ``/live`` body), not from
# the model objects, so the experiment script and the tick feed the prompt
# byte-identical input — prompt v3 was calibrated against this exact shape.


def _f(x, n=0):
    return None if x is None else round(float(x), n) if n else int(round(float(x)))


def _hhmm(iso):
    if not iso:
        return None
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%H:%MZ")


def _rel_words(deg):
    if deg is None:
        return None
    a = abs(deg)
    if a <= 35:
        return "moving along the route (same direction as the flight)"
    if a >= 145:
        return "moving down the route toward the flight"
    return f"moving across the route toward its {'right' if deg > 0 else 'left'} side"


def _stretches(bins, route_nm):
    """Merge [along, ...] bins (5 NM) into [from, to] stretches."""
    out = []
    for a in sorted(bins):
        lo, hi = a - 2.5, a + 2.5
        if out and lo <= out[-1][1] + 0.1:
            out[-1][1] = hi
        else:
            out.append([lo, hi])
    return [[max(0, _f(lo)), min(_f(route_nm), _f(hi))] for lo, hi in out]


def _metar_words(a: dict) -> str:
    bits = [a.get("metar_flight_category") or "category unknown"]
    if a.get("metar_time"):
        bits[0] += f" at {_hhmm(a['metar_time'])}"
    vis = a.get("metar_visibility_m")
    if vis is not None and vis < 8000:
        bits.append(f"visibility {_f(vis)} m")
    ceil = a.get("metar_ceiling_ft")
    if ceil is not None and ceil < 5000:
        bits.append(f"ceiling {_f(ceil)} ft")
    if a.get("metar_weather"):
        bits.append(" ".join(a["metar_weather"]))
    if a.get("metar_wind_gust_kt"):
        bits.append(f"gusts {_f(a['metar_wind_gust_kt'])} kt")
    return ", ".join(bits)


def _taf_words(a: dict, st: dict) -> str | None:
    if not a.get("has_taf") or not a.get("taf_valid_at_eta"):
        return None
    cat = a.get("taf_prevailing_category_at_eta") or a.get("taf_flight_category_at_eta")
    out = f"{cat}" if cat else "category unknown"
    if a.get("taf_temporary_type"):
        out += f", {a['taf_temporary_type']} {a.get('taf_temporary_category') or ''}".rstrip()
    if a.get("taf_significant_weather"):
        out += " " + " ".join(a["taf_significant_weather"])
    eta = _hhmm(st.get("eta")) if st.get("eta") else None
    return f"at {eta}: {out}" if eta else out


def _notable(a: dict) -> bool:
    if (a.get("metar_flight_category") or "VFR") != "VFR":
        return True
    if a.get("metar_weather"):
        return True
    for k in ("taf_prevailing_category_at_eta", "taf_temporary_category_at_eta"):
        if a.get(k) and a[k] != "VFR":
            return True
    return bool(a.get("taf_significant_weather"))


def _airports_along(live: dict, flown: float) -> dict:
    r = live.get("ribbon") or {}
    by_icao = {a["icao"]: a for a in ((live.get("route_observations") or {}).get("airports") or [])}
    rows, quiet = [], 0
    for st in r.get("stations") or []:
        if st.get("role") in ("departure", "destination"):
            continue
        along = st.get("along_nm")
        if along is not None and along < flown - 5:
            continue
        a = by_icao.get(st["icao"], {})
        cross = st.get("cross_nm")
        where = f"{_f(along)} NM along" if along is not None else "position unknown"
        if cross is not None:
            where += ", on track" if abs(cross) < 1 else f", {_f(abs(cross))} NM {'right' if cross > 0 else 'left'} of track"
        line = {"icao": st["icao"], "role": st.get("role") or "route", "where": where,
                "metar_now": _metar_words(a) if a.get("has_metar") else "no METAR"}
        taf = _taf_words(a, st)
        if taf:
            line["taf_at_eta"] = taf
        if _notable(a) or st.get("role") == "alternate":
            rows.append((0, along or 0, line))
        else:
            quiet += 1
            rows.append((1, along or 0, line))
    rows.sort(key=lambda x: (x[0], x[1]))
    shown = [line for rank, _along, line in rows if rank == 0][:12]
    return {"notable": shown or "none", "other_airports_ahead_all_VFR": quiet}


def facts(live: dict) -> dict:
    """The facts block for one tick, from a serialized live layer.

    Keep it this size — the experiment found the model drops items once the
    block grows (issue #697, lesson 2), and every condition carries its place
    because without that it relocated a route airport's LIFR onto the
    destination (lesson 1).
    """
    g = live.get("glance") or {}
    r = live.get("ribbon") or {}
    st = live.get("storms") or {}
    route_nm = r.get("route_nm") or 0
    flown = r.get("flown_nm") or 0.0
    wps = r.get("waypoints") or []
    dep, dest = (wps[0]["icao"], wps[-1]["icao"]) if wps else ("DEP", "DEST")
    now = g.get("as_of") or live.get("live_updated_at")

    if flown <= 0:
        phase = f"before departure (departure planned {_hhmm(r.get('departure_at'))})"
    elif flown >= route_nm - 1:
        phase = "arrived (at plan)"
    else:
        phase = f"en route, about {_f(flown)} of {_f(route_nm)} NM flown, arrival planned {_hhmm(r.get('arrival_at'))}"

    out: dict = {"now": _hhmm(now), "route": f"{dep} to {dest}, {_f(route_nm)} NM", "flight": phase}

    stations = {s.get("role"): s for s in r.get("stations") or [] if s.get("role") in ("departure", "destination")}
    for role, icao in (("departure", dep), ("destination", dest)):
        s = stations.get(role) or {}
        obs = {x["icao"]: x for x in ((live.get("route_observations") or {}).get("airports") or [])}.get(icao, {})
        a = {"icao": icao, "metar_now": _metar_words(obs) if obs.get("has_metar") else (s.get("metar_category") or "unavailable")}
        if s.get("convective"):
            a["reported_convection"] = s["convective"]
        if role == "destination":
            taf = s.get("taf_category_at_eta")
            a["taf_at_eta"] = taf or "none"
            if s.get("taf_temporary_type"):
                a["taf_at_eta"] += f", {s['taf_temporary_type']} {s.get('taf_temporary_category')}"
                if s.get("taf_weather"):
                    a["taf_at_eta"] += " " + " ".join(s["taf_weather"])
        if role == "departure" and flown > 0:
            continue
        out[role] = a

    out["airports_along_route_ahead"] = _airports_along(live, flown)

    # SIGMETs touching the route ahead.
    sig = []
    for s in r.get("sigmets") or []:
        lo, hi = s.get("from_nm"), s.get("to_nm")
        if hi is not None and hi < flown:
            continue
        item = {"what": " ".join(p for p in (s.get("qualifier"), s.get("hazard")) if p) or "SIGMET",
                "id": (s.get("label") or "").split(":")[0]}
        if lo is not None and hi is not None:
            item["covers_route_nm"] = [_f(lo), _f(hi)]
        if s.get("new"):
            item["new_since_briefing"] = True
        if s.get("pending"):
            item["not_yet_valid"] = True
        if s.get("motion") in ("toward", "away"):
            item["moving"] = f"{s['motion']} the route"
        sig.append(item)
    out["sigmets_ahead"] = sig or "none"

    cells_ok = st.get("status") == "available"
    weather = r.get("weather") if r.get("weather_status") == "available" else None
    if weather is not None:
        on_track, near = set(), set()
        rain_motion = []
        for b in weather:
            for a, lo, hi in b.get("profile") or []:
                if a < flown:
                    continue
                if lo <= 0 <= hi:
                    on_track.add(a)
                if min(abs(lo), abs(hi)) <= 10 or lo <= 0 <= hi:
                    near.add(a)
            if b.get("tier") == "rain" and b.get("motion_rel_deg") is not None:
                rain_motion.append((b["to_nm"] - b["from_nm"], b["motion_rel_deg"]))
        ahead_nm = max(route_nm - flown, 1)
        rain = {
            "stretches_where_radar_rain_lies_over_the_track_itself_nm": _stretches(on_track, route_nm) or "none",
            "rain_within_10_NM_either_side": f"on {min(100, _f(len(near) * 5 / ahead_nm * 100))}% of the route ahead",
        }
        if rain_motion:
            rain["main_rain_area"] = _rel_words(max(rain_motion)[1])
        out["rain_ahead"] = rain
    else:
        segs = [s for s in r.get("segments") or [] if (s.get("to_nm") or 0) >= flown]
        wet = [s for s in segs if (s.get("radar_max_dbz") or 0) >= 20]
        out["rain_ahead"] = ({"radar_rain_within_10_NM_of_track_nm": [[_f(s["from_nm"]), _f(s["to_nm"])] for s in wet]}
                             if wet else "none within 10 NM of the track") if segs else "radar unavailable"

    if not cells_ok:
        out["cells_ahead"] = "radar cell tracking unavailable"
    else:
        ahead = [s for s in st.get("storms") or [] if s.get("ahead")]
        if not ahead:
            out["cells_ahead"] = f"none within {_f(st.get('corridor_nm') or 30)} NM of the track"
        else:
            def cell(s):
                c = {"peak_dBZ": _f(s.get("peak_dbz")), "at_route_nm": _f(s.get("along_nm")),
                     "off_track_nm": _f(s.get("offtrack_nm")), "side": s.get("side") or s.get("end_bearing") or "on track"}
                if s.get("abeam_eta"):
                    c["abeam_at"] = _hhmm(s["abeam_eta"])
                if s.get("flashes"):
                    c["lightning_flashes"] = s["flashes"]
                rm = s.get("relative_motion")
                if rm == "closing" and s.get("closing_kt") is not None:
                    c["motion"] = f"closing on the track at {_f(s['closing_kt'])} kt"
                elif rm == "moving_away":
                    c["motion"] = "moving away from the track"
                elif rm == "parallel":
                    c["motion"] = "moving along the track"
                if s.get("trend") in ("developing", "decaying"):
                    c["trend"] = s["trend"]
                return c
            listed = sorted(ahead, key=lambda s: s.get("offtrack_nm") or 0)[:5]
            out["cells_ahead"] = {
                "count": len(ahead),
                "with_lightning": sum(1 for s in ahead if s.get("flashes")),
                "closing_on_track": sum(1 for s in ahead if s.get("relative_motion") == "closing"),
                "within_10_NM_of_track": sum(1 for s in ahead if (s.get("offtrack_nm") or 99) <= 10),
                "nearest_to_track": [cell(s) for s in listed],
            }

    rows = (live.get("changes") or {}).get("changes") or []

    def place(c):
        role = c.get("role") or "route"
        icao = c.get("icao")
        if role in ("departure", "destination", "alternate") and icao:
            return f"{role} {icao}"
        if icao:
            d = c.get("enroute_distance_nm")
            return f"route airport {icao}" + (f" at {_f(d)} NM along" if d is not None else "")
        d = c.get("enroute_distance_nm")
        return f"en route at {_f(d)} NM" if d is not None else "en route"

    worse = [{"where": place(c), "what": c["message"]} for c in rows
             if c.get("direction") == "worse" and c.get("tier") in ("alert", "highlight")]
    better = sum(1 for c in rows if c.get("direction") == "better")
    out["changes_since_briefing"] = {"worse": worse[:4] or "none", "improved_count": better}
    return out


def facts_hash(f: dict) -> str:
    """Stable hash of a facts block, ignoring the wall clock.

    ``now`` moves every tick while nothing the pilot would read has changed,
    so hashing it would pay for an identical highlight six times an hour.
    Everything else counts — a METAR time, a cell's abeam time and a changed
    percentage are all real movement.
    """
    body = {k: v for k, v in f.items() if k != "now"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --- Grounding --------------------------------------------------------------

#: Words the product voice never uses: the highlight points at the thing, the
#: pilot decides (``feedback_not_go_nogo``, meteorology-decisions §41).
VERDICT_WORDS = frozenset(
    "safe unsafe safely dangerous hazardous go no-go nogo avoid divert diverting "
    "recommend recommended should must advise advisable unflyable "
    "caution careful suggest suggested consider".split()
)

#: Conditions an *airport* can be in, each with the surface forms the facts and
#: the model may use for it. Used only to bind a condition to the place the
#: model attributed it to — the failure that motivated the check was a route
#: airport's LIFR appearing on the destination (#697, lesson 1).
#:
#: Deliberately airport-scoped. Route-level words (cell, rain, SIGMET) are
#: checked by the global rules below, not bound to a place: "no cell within
#: 20 NM of LFMD" is a true statement about LFMD that mentions no condition of
#: LFMD's, and binding it would reject good output.
AIRPORT_CONDITIONS: dict[str, frozenset[str]] = {
    "LIFR": frozenset({"lifr"}),
    "IFR": frozenset({"ifr"}),
    "MVFR": frozenset({"mvfr"}),
    "VFR": frozenset({"vfr"}),
    "thunderstorm": frozenset({"ts", "tsra", "+tsra", "-tsra", "vcts", "tsgr",
                               "thunderstorm", "thunderstorms", "lightning"}),
    "convection": frozenset({"cb", "tcu", "cumulonimbus", "towering"}),
    "rain": frozenset({"ra", "-ra", "+ra", "shra", "-shra", "+shra", "vcsh", "dz", "-dz",
                       "rain", "showers", "shower", "drizzle"}),
    "snow": frozenset({"sn", "-sn", "+sn", "shsn", "snow", "sleet", "gs", "gr", "hail"}),
    "obscuration": frozenset({"fg", "bcfg", "mifg", "br", "hz", "fu", "fog", "mist", "haze", "smoke"}),
    "gusts": frozenset({"gusts", "gusting", "gust"}),
    "ceiling": frozenset({"ceiling", "overcast", "broken"}),
    "visibility": frozenset({"visibility", "vis"}),
}

#: A clause carrying one of these is making a negative or comparative claim
#: ("no cell near LFMD", "LFMD better than briefed"), which the binding rule
#: cannot read. Skipped rather than guessed at.
_HEDGES = frozenset(
    "no none not never without nothing clear quiet improving improved better "
    "easing clearing lifting".split()
)

#: Four-letter upper-case words that are *not* ICAO codes. Without this,
#: ``LIFR`` reads as an airport: rule 1 then rejects it as an unknown aerodrome,
#: and worse, the place-binding rule sees two "ICAOs" in "LEMI reporting LIFR"
#: and skips the clause — which is exactly the misattribution it exists to
#: catch. Weather codes, cloud groups and SIGMET qualifiers all collide.
_NOT_ICAO = frozenset(
    """LIFR MVFR TSRA VCTS TSGR SHRA SHSN BCFG MIFG DRSN BLSN FZRA FZDZ FZFG
    EMBD ISOL OCNL FRQT SQLN LYRS BKNL OVCL CAVU NOSIG METR TEMP PROB BECM
    GRID WIND TEMPO NSC""".split()
)

_ICAO_RE = re.compile(r"\b[A-Z]{4}\b")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_CLAUSE_RE = re.compile(r"[;,.]|\band\b|\bbut\b|\bwhile\b|\bwith\b")


def _icaos(text: str) -> set[str]:
    """Aerodrome codes in a piece of text, weather codes excluded."""
    return {c for c in _ICAO_RE.findall(text) if c not in _NOT_ICAO}


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^A-Za-z0-9+\-/:]+", text.lower()) if w]


def _conditions_in(text: str) -> set[str]:
    """Which airport conditions a piece of text claims."""
    toks = set(_words(text))
    return {name for name, forms in AIRPORT_CONDITIONS.items() if toks & forms}


def _facts_for_icao(f: dict, icao: str) -> str:
    """Everything the facts say about one ICAO, as text.

    Walks the whole block rather than the known shapes: an ICAO turns up as a
    departure, a destination, a route airport, an alternate and inside a change
    row's ``where``, and a binding check that missed one of those would reject
    a correct highlight.
    """
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            blob = json.dumps(node, default=str)
            if icao in blob:
                # The entry that names it directly, not an ancestor holding
                # every airport: recurse first and keep the narrowest.
                children = [v for v in node.values() if isinstance(v, (dict, list))]
                if not any(icao in json.dumps(c, default=str) for c in children):
                    found.append(blob)
                    return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and icao in node:
            found.append(node)

    walk(f)
    return " ".join(found)


def check_grounding(text: str, f: dict) -> str | None:
    """Reject a highlight that is not carried by the facts.

    Returns ``None`` when it passes, else a short reason. Five rules, each one
    a failure seen in the experiment or a voice rule from the issue:

    1. **ICAOs** — every 4-letter code must appear in the facts.
    2. **Figures** — every number must appear in the facts. Catches an invented
       distance, time or dBZ, which is the one error a pilot cannot spot.
    3. **Place binding** — in a clause naming exactly one airport, every
       *airport condition* claimed must be one the facts give for that airport.
       This is the rule the plain "appears somewhere in the facts" check misses:
       moving LECH's LIFR onto LFMD passes rule 1 and 2 and fails here.
    4. **Verdict words** — the product never says go/no-go.
    5. **"Thunderstorm" needs lightning** — §41: a radar core is a "cell"; only
       observed lightning or a TS hazard earns the word.

    Known limits, accepted deliberately: a clause naming two airports is not
    bound (ambiguous attribution), and a clause hedged with "no"/"better" is
    skipped (rule 3 cannot read a negation). Both let a wrong line through
    rather than reject a right one — the replay set and the review log are the
    backstop, and every rejection is logged with its text so a false one is
    visible rather than silent.
    """
    text = (text or "").strip()
    if not text:
        return "empty"

    words = text.split()
    if len(words) > MAX_WORDS:
        return f"too long ({len(words)} words > {MAX_WORDS})"

    blob = json.dumps(f, default=str)

    unknown = sorted({c for c in _icaos(text) if c not in blob})
    if unknown:
        return f"ICAO not in facts: {', '.join(unknown)}"

    fact_numbers = set(_NUM_RE.findall(blob))
    invented = sorted({n for n in _NUM_RE.findall(text) if n not in fact_numbers})
    if invented:
        return f"figure not in facts: {', '.join(invented)}"

    said = set(_words(text))
    verdict = sorted(said & VERDICT_WORDS)
    if verdict:
        return f"verdict word: {', '.join(verdict)}"

    if said & AIRPORT_CONDITIONS["thunderstorm"]:
        lightning = "lightning_flashes" in blob or re.search(r"\bTS\b|TSRA|VCTS", blob)
        if not lightning:
            return "says thunderstorm without lightning in the facts"

    for clause in _CLAUSE_RE.split(text):
        icaos = _icaos(clause)
        if len(icaos) != 1:
            continue
        if set(_words(clause)) & _HEDGES:
            continue
        icao = icaos.pop()
        claimed = _conditions_in(clause)
        if not claimed:
            continue
        supported = _conditions_in(_facts_for_icao(f, icao))
        wrong = sorted(claimed - supported)
        if wrong:
            return f"{icao} not given as {', '.join(wrong)} in the facts"

    return None


# --- Model call -------------------------------------------------------------


def generate(f: dict, model: str = DEFAULT_MODEL) -> tuple[str, dict, int]:
    """One highlight from one facts block: ``(text, usage, latency_ms)``.

    Raises on an API failure — every caller treats that as "no highlight this
    tick" and keeps the previous one.
    """
    import anthropic

    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES)
    t = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        system=SYSTEM,
        messages=[{"role": "user", "content": "FACTS:\n" + json.dumps(f, indent=1, ensure_ascii=False)}],
    )
    latency_ms = int((time.perf_counter() - t) * 1000)
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    usage = {
        "model": model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        "cache_write_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
    }
    return text, usage, latency_ms


# --- Orchestration ----------------------------------------------------------


def facts_for(layer) -> dict:
    """The facts block for a committed :class:`LiveLayer`.

    Goes through the serialized form on purpose: the experiment script feeds
    :func:`facts` a ``live.json`` / ``/live`` body, and prompt v3 was
    calibrated against that exact shape. One code path, one input shape.
    """
    return facts(layer.model_dump(mode="json"))


def carry_forward(stored, layer) -> bool:
    """Keep the previous highlight when this tick's facts are unchanged.

    ``build_glance`` rebuilds the whole glance every tick, so without this the
    previous highlight is destroyed on every commit and every tick pays for a
    new one. Returns True when one was carried over. Never raises: a highlight
    problem must not fail a tick.

    Runs *inside* the commit because the glance it attaches to is written
    there. Pure computation — no model call, no network.
    """
    try:
        previous = stored.glance.highlight if stored is not None and stored.glance else None
        if previous is None or layer.glance is None:
            return False
        if previous.facts_hash != facts_hash(facts_for(layer)):
            return False
        layer.glance.highlight = previous
        return True
    except Exception:
        logger.warning("Live highlight carry-forward failed for %s — regenerating", getattr(layer, "flight_id", "?"), exc_info=True)
        return False


def _log_attempt(flight_dir: Path, record: dict) -> None:
    """Append one attempt to the review log. Never raises.

    The facts go in with the text: a line that reads wrong is otherwise
    ambiguous between the model's phrasing and the facts block feeding it,
    which is the whole point of the calibration period (owner, 2026-10-07).
    """
    try:
        path = Path(flight_dir) / LIVE_HIGHLIGHT_LOG
        with open(path, "a") as fh:
            fh.write(json.dumps(record, separators=(",", ":"), default=str) + "\n")
    except OSError:
        logger.warning("Live highlight log write failed for %s", flight_dir, exc_info=True)


@dataclass(frozen=True)
class HighlightOutcome:
    """What one :func:`ensure_highlight` call did.

    ``usage`` is present whenever the model was billed — including a rejected
    generation, because a billed-but-unusable call is still a real cost and an
    invisible cost line is how a small cost becomes an unexplained one.
    """

    outcome: str  # written | reused | rejected | call_failed | skipped | superseded
    text: str | None = None
    usage: dict | None = None
    reason: str | None = None

    @property
    def written(self) -> bool:
        return self.outcome == "written"


def ensure_highlight(
    flight_dir: Path | str,
    layer,
    *,
    model: str = DEFAULT_MODEL,
) -> HighlightOutcome:
    """Generate this tick's highlight and patch it in.

    Call **after** ``commit_live_update`` has returned: the layer is already on
    disk and servable, so nothing a pilot reads waits on the model. Never
    raises — every failure path leaves the layer exactly as the tick wrote it,
    which is a glance with no highlight, and clients fall back to its
    ``headline``.

    No-ops when the facts are unchanged (``carry_forward`` already attached the
    previous highlight during the commit), which is most ticks.

    Does **not** charge the ledger: it is called from a worker thread and a
    SQLAlchemy ``Session`` is not thread-safe. The caller charges
    ``outcome.usage`` through :func:`charge_highlight` on its own thread.
    """
    flight_dir = Path(flight_dir)
    if not highlight_enabled() or layer is None or layer.glance is None:
        return HighlightOutcome("skipped")
    if layer.ribbon is None:
        # Without the ribbon there is no route, no station lane and no weather
        # band, and ``facts`` degenerates to "DEP to DEST, 0 NM" — a block with
        # nothing in it to lead with. A layer written before #695, or by a tick
        # whose glance build failed, must not be paid for or logged as a
        # highlight that "came out wrong".
        return HighlightOutcome("skipped")
    if layer.glance.highlight is not None:
        # Carried forward during the commit: unchanged facts, nothing to pay.
        return HighlightOutcome("reused", text=layer.glance.highlight.text)

    from weatherbrief.models.live import LiveHighlight
    from weatherbrief.tasks.live_layer import patch_highlight

    try:
        f = facts_for(layer)
    except Exception:
        logger.exception("LIVE_HIGHLIGHT_FAILED flight=%s — could not build facts", layer.flight_id)
        return HighlightOutcome("skipped")

    digest = facts_hash(f)
    base = {
        "at": datetime.now(timezone.utc),
        "flight_id": layer.flight_id,
        "pack_timestamp": layer.pack_timestamp,
        "as_of": layer.glance.as_of,
        "facts_hash": digest,
        "facts": f,
        "model": model,
    }

    try:
        text, usage, latency_ms = generate(f, model=model)
    except Exception as exc:
        # Expected failure mode (timeout, rate limit, outage). One line, not a
        # traceback per tick: the highlight is optional and the tick is intact.
        logger.warning("Live highlight call failed for %s: %s", layer.flight_id, exc)
        _log_attempt(flight_dir, {**base, "outcome": "call_failed", "error": str(exc)})
        return HighlightOutcome("call_failed", reason=str(exc))

    reason = check_grounding(text, f)
    if reason is not None:
        # Loud: a systematic rejection means the prompt drifted, and the
        # fallback (the nutshell headline) hides it from every surface.
        logger.warning(
            "LIVE_HIGHLIGHT_REJECTED flight=%s reason=%s text=%r", layer.flight_id, reason, text,
        )
        _log_attempt(flight_dir, {**base, "outcome": "rejected", "reason": reason,
                                  "text": text, "usage": usage, "latency_ms": latency_ms})
        return HighlightOutcome("rejected", text=text, usage=usage, reason=reason)

    written = patch_highlight(
        flight_dir,
        LiveHighlight(text=text, model=model, facts_hash=digest,
                      generated_at=datetime.now(timezone.utc), latency_ms=latency_ms),
        pack_timestamp=layer.pack_timestamp,
        as_of=layer.glance.as_of,
    )
    _log_attempt(flight_dir, {**base, "outcome": "written" if written else "superseded",
                              "text": text, "usage": usage, "latency_ms": latency_ms})
    return HighlightOutcome("written" if written else "superseded", text=text, usage=usage)


def charge_highlight(db, user_id: str | None, flight_id: str, usage: dict | None) -> None:
    """Put the (tiny) cost through the shared ledger. Never blocks.

    Priced by ``compute_call_cost``, not the per-briefing ``compute_cost``:
    that one amortises droplet and subscription share over *briefings*, and
    charging it per side call is what billed a sub-cent paragraph at ~$0.62
    (see ``digest/trip_summary._charge``). ~$0.0016 a call here, several times
    an hour per live flight — small, and recorded, because an invisible cost
    line is how a small cost becomes an unexplained one.
    """
    if db is None or not user_id or not usage or not usage.get("model"):
        return
    try:
        from flyfun_common.costs import record_cost

        from weatherbrief.api.credits import SERVICE
        from weatherbrief.costs import compute_call_cost

        cost = compute_call_cost(
            usage["model"],
            input_tokens=usage.get("input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            cache_read_tokens=usage.get("cache_read_tokens") or 0,
            cache_write_tokens=usage.get("cache_write_tokens") or 0,
        )
        record_cost(
            db, user_id,
            service=SERVICE,
            action="live_highlight",
            cost=cost,
            category="live_highlight",
            description=f"Observed highlight (${cost:.4f})",
            metadata={"model": usage["model"],
                      "input_tokens": usage.get("input_tokens") or 0,
                      "output_tokens": usage.get("output_tokens") or 0},
            reference_id=flight_id,
        )
    except Exception:
        logger.warning("Live highlight cost charge failed for %s", flight_id, exc_info=True)
