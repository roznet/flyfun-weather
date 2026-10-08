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
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

logger = logging.getLogger(__name__)

#: Review log, one record per generation attempt (kept, reused or rejected).
#: Append-only and per flight, like ``live_history.jsonl``: ``live.json`` holds
#: only the newest highlight, so this is the only place a flight day can be
#: read back from afterwards.
LIVE_HIGHLIGHT_LOG = "live_highlights.jsonl"

#: Haiku 5.5 since #715. The A/B on the first flight day's 311 facts blocks:
#: ~7x cheaper per call than Haiku 4.5, and right on the cases 4.5 got wrong
#: (it led with the TS SIGMETs 4.5 dropped, read "130 NM along, 19 NM left"
#: correctly). It writes longer, hence the 60-word ceiling below.
DEFAULT_MODEL = "claude-haiku-5-5"
#: Thinking off, effort low (#715). Adaptive thinking at low effort added no
#: visible quality on the A/B and cost a 4 s p95, empty replies and
#: ``max_tokens`` stops; with it off, p50/p95 were 0.8/1.2 s.
THINKING = {"type": "disabled"}
EFFORT = "low"
#: Haiku's p95 on the busiest real tick is ~1.2 s; 5 s is a hung-connection
#: guard, not a latency budget. Nothing waits on this call, so one retry is
#: free — but two would let a flapping API hold a tick's worth of threads.
REQUEST_TIMEOUT_S = 5.0
MAX_RETRIES = 1
#: Hard ceiling (owner, 2026-10-08, #715; was 40 on Haiku 4.5). The prompt
#: asks for 35 so the model aims below it. On the A/B, Haiku 5.5's overruns of
#: 40 were almost all 41-50 words; 50 and 60 rejected the same 9 %.
MAX_WORDS = 60
#: Billed generations per facts state before giving up on it.
#:
#: The tick retries any flight with no stored highlight, and a rejection stores
#: nothing, so without a cap one unlucky facts block bought a rejected sentence
#: every tick for the whole live window — 33 billed calls on a single flight.
#: Zero retries would be wrong too: the model is stochastic (the same tick
#: comes back worded differently run to run), so a transient bad line would
#: cost that flight its highlight until the weather moved. Two attempts gives
#: a bad draw a second chance and caps a systematic failure at twice the price.
MAX_ATTEMPTS_PER_FACTS = 2
#: 60 words is ~110 output tokens on Haiku 5.5's tokenizer (~30 % more tokens
#: than 4.5's for the same text); 300 leaves room without paying for a runaway,
#: which the post-check rejects anyway.
MAX_TOKENS = 300

SYSTEM = """You write the one-glance highlight at the top of a pilot's in-flight weather screen.

You get FACTS about one flight at one moment: where the flight is, the departure and destination conditions, the airports along the route (METAR now, TAF at the time the flight is abeam, their position against the route), SIGMETs, rain and convective cells near the route ahead (from radar) and how they move, and what changed since the briefing.

Write a highlight of at most two short sentences, 35 words in total at most, that tells the pilot what deserves their attention along the route ahead, most important first. Do not repeat the route or the airports' names as a title.

60 words is a hard limit: a longer highlight is discarded and the pilot sees nothing. When the facts hold more than fits, leave the least important item out entirely rather than shortening every clause — one item said properly beats three said in fragments.

What leads, in this order:
1. Hazards on the track ahead: an active SIGMET covering the route (always mention it, with where it covers), cells within 10 NM of the track (lightning and closing ones first), rain lying over the track.
2. The destination or an alternate that is non-VFR or getting worse, at the time the flight gets there.
3. A notable airport along the route.
When a hazard family is covered by a SIGMET and by radar, say it once (the SIGMET's span) and add only what radar adds (a cell with lightning, its motion).
Mention a missing source (radar, METAR) only when leaving it out would make the route sound clear.

Rules:
- Use only the facts given. Never add weather, causes, forecasts or numbers that are not in them.
- No verdict and no advice: never say safe, unsafe, go, no-go, avoid, divert, recommend, should, watch or monitor. Point at the thing; the pilot decides.
- Say what is there, not what is absent: don't list missing hazards ("no SIGMETs", "no lightning", "no cells"). Absence belongs only in the quiet-route sentence below.
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

    Mirrors ``DISABLE_LIVE_LAYER``: the kill switch is the thing you set. Note
    what that means for a deploy — **on is the default**, so an environment
    that means to run without the highlight and forgets
    ``DISABLE_LIVE_HIGHLIGHT`` generates and is billed for it. The safe
    direction is the key: no ``ANTHROPIC_API_KEY`` and nothing is called, which
    is how a dev worktree and the test suite run the whole tick for free
    (``conftest`` deletes the key so a developer's shell cannot bill the
    suite).
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


#: Present-weather families the highlight names (listed sorted). The facts give an airport's weather as these words, not as the METAR's
#: codes: "-SHRA" → "SHRA" → "-SHRA" is noise a pilot would not read as a
#: change, and the gate (:func:`gate_changes`) compares exactly what the facts
#: show (#706). Mist, haze, smoke and dust are left out on purpose: they come
#: and go with a few hundred metres of visibility, which the flight category
#: already carries when it matters. Thunderstorms come through
#: :func:`convective_tags` (TS / CB / TCU), not from here.
_WX_FAMILIES = (
    ("FZ", "freezing precipitation"),
    ("GR", "hail"),
    ("GS", "small hail"),
    ("SQ", "squall"),
    ("FC", "funnel cloud"),
    ("SN", "snow"),
    ("SH", "showers"),
    ("RA", "rain"),
    ("DZ", "drizzle"),
    ("FG", "fog"),
)
#: Gust at which an airport with no wind advisory (no runway data) is shown
#: as gusty. The issue's replay used the same threshold (#706).
GUST_NOTABLE_KT = 25


def _weather_families(obs) -> list[str]:
    """The present weather as family words, heavy ones marked."""
    from weatherbrief.tasks.live_significance import _present_weather

    out: list[str] = []
    for code in _present_weather(obs):
        if "TS" in code:
            continue  # convective, said once as TS
        body = code.lstrip("+-")
        nearby = body.startswith("VC")
        for token, word in _WX_FAMILIES:
            if token not in body:
                continue
            if word in ("rain", "drizzle") and "SH" in body:
                continue  # "showers" already says it
            if token == "FZ" and "FG" in body:
                word = "freezing fog"
            elif token == "FG" and "FZ" in body:
                continue  # said as "freezing fog"
            if code.startswith("+"):
                word = f"heavy {word}"
            elif nearby:
                word = f"{word} nearby"
            if word not in out:
                out.append(word)
    return sorted(out)


def _category_drivers(obs) -> list[str]:
    """What sets a non-VFR category: "low ceiling", "low visibility" or both.

    Words, never the figure (#706 item 5): a carried-forward highlight that
    quoted "ceiling 2400 ft" would go stale silently when the ceiling drifts
    to 2900 ft inside the same category, because the gate does not regenerate
    on that.
    """
    from weatherbrief.analysis.airport_conditions import classify_flight_category

    cat = obs.metar_flight_category
    if not cat or cat == "VFR":
        return []
    out = []
    if obs.metar_ceiling_ft is not None and classify_flight_category(obs.metar_ceiling_ft, None).value == cat:
        out.append("low ceiling")
    if obs.metar_visibility_m is not None and classify_flight_category(None, obs.metar_visibility_m / 1609.344).value == cat:
        out.append("low visibility")
    return out


def _metar_state(a: dict) -> dict:
    """One airport's METAR as the significance classifier reads it.

    The same interpretation ``live_significance._airport_metar_changes``
    compares: flight category, convective level (TCU < CB < TS), significant
    weather (as families) and the wind-advisory band. Observation time, exact
    ceiling / visibility / gust figures and the runway are deliberately
    absent: none of them is a change a pilot would read (#706).
    """
    from weatherbrief.models.observations import AirportObservation
    from weatherbrief.tasks.live_significance import (
        _CONVECTIVE_RANK,
        _convective_level,
        convective_tags,
    )

    try:
        obs = AirportObservation.model_validate(a)
    except ValidationError:
        # One malformed row must not drop the whole flight's highlight.
        logger.warning("Live highlight: unreadable METAR row for %s", a.get("icao", "?"), exc_info=True)
        return {"category": "METAR unreadable", "unreadable": True}
    tags = convective_tags(obs)
    level = _convective_level(tags)
    state: dict = {"category": obs.metar_flight_category or "category unknown"}
    drivers = _category_drivers(obs)
    if drivers:
        state["drivers"] = drivers
    top = sorted(t for t in tags if _CONVECTIVE_RANK[t] == level)
    if top:
        state["convective"] = top
    wx = _weather_families(obs)
    if wx:
        state["weather"] = wx
    if obs.metar_wind_advisory in ("amber", "red"):
        state["wind"] = f"wind advisory {obs.metar_wind_advisory}"
    elif obs.metar_wind_advisory is None and (obs.metar_wind_gust_kt or 0) >= GUST_NOTABLE_KT:
        state["wind"] = "gusts"
    return state


def _metar_words(a: dict) -> str:
    """"IFR (low ceiling), CB, showers, wind advisory amber"."""
    s = _metar_state(a)
    head = s["category"]
    if s.get("drivers"):
        head += f" ({', '.join(s['drivers'])})"
    bits = [head, *s.get("convective", []), *s.get("weather", [])]
    if s.get("wind"):
        bits.append(s["wind"])
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
    """Worth naming: non-VFR, convective, notable weather or wind, or a worse
    TAF at ETA. Judged on the interpretation the facts show, so an airport
    is never listed for something the facts then leave out (plain mist)."""
    if a.get("has_metar"):
        s = _metar_state(a)
        if s.get("unreadable"):
            pass
        elif s["category"] != "VFR" or s.get("convective") or s.get("weather") or s.get("wind"):
            return True
    for k in ("taf_prevailing_category_at_eta", "taf_temporary_category_at_eta"):
        if a.get(k) and a[k] != "VFR":
            return True
    return bool(a.get("taf_significant_weather"))


#: Notable airports the facts list; the gate tracks them all (#706 item 7).
MAX_NOTABLE_AIRPORTS = 12
#: An airport this far behind the aircraft is "passed" and leaves the list.
PASSED_MARGIN_NM = 5


def _airports_along(live: dict, flown: float) -> tuple[dict, dict]:
    """The facts' route-airport block, and the gate's view of it.

    The gate's view is every notable airport ahead, **uncapped**, with its
    position: passing one is not a change, and neither is the next one
    sliding into the 12-entry cap (#706 item 7) — :func:`gate_changes` needs
    both to tell those apart from an airport becoming notable or quiet.
    """
    r = live.get("ribbon") or {}
    by_icao = {a["icao"]: a for a in ((live.get("route_observations") or {}).get("airports") or [])}
    rows, quiet = [], 0
    for st in r.get("stations") or []:
        if st.get("role") in ("departure", "destination"):
            continue
        along = st.get("along_nm")
        if along is not None and along < flown - PASSED_MARGIN_NM:
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
            rows.append((0, along or 0, line, along))
        else:
            quiet += 1
    rows.sort(key=lambda x: (x[0], x[1]))
    notable = [(line, along) for _rank, _k, line, along in rows]
    shown = [line for line, _along in notable][:MAX_NOTABLE_AIRPORTS]
    gate = {line["icao"]: {"along": along, **{k: v for k, v in line.items() if k not in ("icao", "where")}}
            for line, along in notable}
    # No count: it falls by one each time an airport is passed, which is not a
    # change, and a kept highlight quoting "five other airports" would go stale.
    return ({"notable": shown or "none", "other_airports_ahead": "all VFR" if quiet else "none"}, gate)


#: Rain over the track is gated at this resolution (#706 item 4): a stretch
#: edge moving within a bin is radar-frame noise, not a change.
RAIN_BIN_NM = 25


def _coverage_band(pct: float) -> str:
    """Rain within 10 NM of the track as a band (#706 item 9): the exact
    percentage drifts with progress (its denominator is the route ahead), but
    flank rain going from 5 % to 80 % of the route must regenerate."""
    if pct <= 0:
        return "none of the route ahead"
    if pct < 25:
        return "under 25% of the route ahead"
    if pct < 50:
        return "25 to 50% of the route ahead"
    return "50% or more of the route ahead"


def _coarse_stretches(bins: set[float], route_nm: float, flown: float) -> list[str]:
    """Rain-over-track 5 NM bins as stretches on a :data:`RAIN_BIN_NM` grid.

    The first stretch says "from the aircraft" rather than a figure behind it
    once airborne, so its start does not move with the clock.
    """
    coarse = sorted({int(a // RAIN_BIN_NM) for a in bins})
    spans: list[list[int]] = []
    for b in coarse:
        if spans and b == spans[-1][1]:
            spans[-1][1] = b + 1
        else:
            spans.append([b, b + 1])
    out = []
    for lo, hi in spans:
        lo_nm, hi_nm = lo * RAIN_BIN_NM, min(_f(route_nm), hi * RAIN_BIN_NM)
        start = "from the aircraft" if flown > 0 and lo_nm <= flown else f"from {lo_nm} NM"
        out.append(f"{start} to {hi_nm} NM")
    return out


#: How close the nearest cell ahead is to the track, as the gate sees it.
_CELL_BANDS = ((3, "within 3 NM of the track"), (10, "within 10 NM of the track"))


def _cells_ahead(st: dict) -> str | dict:
    """Radar cells ahead as flags (#706 item 4).

    Exact positions, dBZ, abeam times and motion speeds move with every radar
    frame, so the gate cannot follow them — and so the facts don't show them
    either (item 5): a kept highlight quoting "42 dBZ at 102 NM" would go
    stale silently. What is left is what a pilot acts on: how close the
    nearest one is, whether any has lightning, whether one near the track is
    closing on it.
    """
    corridor = _f(st.get("corridor_nm") or 30)
    ahead = [s for s in st.get("storms") or [] if s.get("ahead")]
    if not ahead:
        return f"none within {corridor} NM of the track"
    nearest = min((s.get("offtrack_nm") if s.get("offtrack_nm") is not None else 99) for s in ahead)
    band = next((words for limit, words in _CELL_BANDS if nearest <= limit), f"within {corridor} NM of the track")
    out: dict = {"nearest": band}
    if any(s.get("flashes") for s in ahead):
        # Only present when true: ``check_grounding`` reads this key as the
        # licence for "thunderstorm".
        out["lightning_flashes"] = "in at least one cell"
    if any(s.get("relative_motion") == "closing" and (s["offtrack_nm"] if s.get("offtrack_nm") is not None else 99) <= 10 for s in ahead):
        out["closing_on_track"] = "at least one cell within 10 NM"
    if any(s.get("trend") == "developing" and (s["offtrack_nm"] if s.get("offtrack_nm") is not None else 99) <= 10 for s in ahead):
        out["developing"] = "at least one cell within 10 NM"
    return out


#: Change rows the facts carry. Radar storm / ring rows are left out: they
#: flicker present ↔ absent tick to tick with figures in their text, and
#: ``cells_ahead`` is the radar source (#706 item 3).
_FACT_CHANGE_KINDS = frozenset({
    "metar_category", "metar_convective", "metar_weather", "metar_wind",
    "taf_category", "sigmet_issued", "sigmet_cancelled",
})


def _change_words(c: dict) -> str:
    """The row's text without figures the gate does not follow: the wind row's
    crosswind / gust detail moves every METAR inside the same band."""
    if c.get("kind") == "metar_wind" and c.get("icao"):
        return f"{c['icao']} wind advisory: {c.get('from_value')} → {c.get('to_value')}"
    return c["message"]


def _phase(r: dict, flown: float, route_nm: float) -> tuple[str, str]:
    """(facts wording, gate key) of the flight's phase."""
    if flown <= 0:
        return f"before departure (departure planned {_hhmm(r.get('departure_at'))})", "before departure"
    if flown >= route_nm - 1:
        return "arrived (at plan)", "arrived"
    return (f"en route, about {_f(flown)} of {_f(route_nm)} NM flown, arrival planned {_hhmm(r.get('arrival_at'))}",
            "en route")


def facts_and_gate(live: dict) -> tuple[dict, dict]:
    """The facts block for one tick, and the gate state behind it.

    The facts are what the model reads. The gate state (#706) is what
    regeneration is decided on — compared with the state at the last
    *generation* by :func:`gate_changes`, not with the previous tick. Both are
    built here from one pass so they cannot disagree, and the rule that ties
    them is item 5: **what the gate ignores, the facts don't show** — no
    observation time, no exact ceiling, no cell position — or a carried-
    forward highlight would quote a figure that has since moved.

    Keep the block this size — the experiment found the model drops items once
    the block grows (issue #697, lesson 2), and every condition carries its
    place because without that it relocated a route airport's LIFR onto the
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

    phase, phase_key = _phase(r, flown, route_nm)
    out: dict = {"now": _hhmm(now), "route": f"{dep} to {dest}, {_f(route_nm)} NM", "flight": phase}
    gate: dict = {"route": out["route"], "phase": phase_key, "_flown_nm": flown}

    stations = {s.get("role"): s for s in r.get("stations") or [] if s.get("role") in ("departure", "destination")}
    obs_by_icao = {x["icao"]: x for x in ((live.get("route_observations") or {}).get("airports") or [])}
    for role, icao in (("departure", dep), ("destination", dest)):
        s = stations.get(role) or {}
        obs = obs_by_icao.get(icao, {})
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
        gate[role] = a

    out["airports_along_route_ahead"], gate["airports"] = _airports_along(live, flown)

    # SIGMETs touching the route ahead. Gated exactly, id included (#706
    # item 10): the highlight quotes ids, so a reissue must regenerate.
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
    out["sigmets_ahead"] = gate["sigmets"] = sig or "none"

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
            # Only the part of a rain area still ahead (#706 item 8): rain
            # behind the aircraft once read as "moving toward the route from
            # behind" with no rain ahead at all.
            ahead_len = (b.get("to_nm") or 0) - max(b.get("from_nm") or 0, flown)
            if b.get("tier") == "rain" and b.get("motion_rel_deg") is not None and ahead_len > 0:
                rain_motion.append((ahead_len, b["motion_rel_deg"]))
        ahead_nm = max(route_nm - flown, 1)
        rain = {
            "stretches_where_radar_rain_lies_over_the_track_itself": _coarse_stretches(on_track, route_nm, flown) or "none",
            "rain_within_10_NM_either_side": "on " + _coverage_band(len(near) * 5 / ahead_nm * 100),
        }
        gate["rain"] = dict(rain)
        if rain_motion:
            # Shown, not gated (#706 item 8): it flips null ↔ "moving along"
            # as small areas come and go, and it is a direction word, not a
            # figure that could go stale in a kept highlight.
            rain["main_rain_area"] = _rel_words(max(rain_motion)[1])
        out["rain_ahead"] = rain
    else:
        segs = [s for s in r.get("segments") or [] if (s.get("to_nm") or 0) >= flown]
        wet = [s for s in segs if (s.get("radar_max_dbz") or 0) >= 20]
        out["rain_ahead"] = ({"radar_rain_within_10_NM_of_track_nm": [[_f(s["from_nm"]), _f(s["to_nm"])] for s in wet]}
                             if wet else "none within 10 NM of the track") if segs else "radar unavailable"
        gate["rain"] = out["rain_ahead"]

    out["cells_ahead"] = gate["cells"] = (
        _cells_ahead(st) if st.get("status") == "available" else "radar cell tracking unavailable"
    )

    rows = [c for c in (live.get("changes") or {}).get("changes") or [] if c.get("kind") in _FACT_CHANGE_KINDS]

    def place(c):
        role = c.get("role") or "route"
        icao = c.get("icao")
        if role in ("departure", "destination", "alternate") and icao:
            return f"{role} {icao}"
        if icao:
            d = c.get("enroute_distance_nm")
            return f"route airport {icao}" + (f" at {_f(d)} NM along" if d is not None else "")
        # A SIGMET row: its span is in ``sigmets_ahead``; the row's own
        # distance moves with the geometry and is not gated.
        return "en route"

    worse_rows = [c for c in rows if c.get("direction") == "worse" and c.get("tier") in ("alert", "highlight")]
    worse = [{"where": place(c), "what": _change_words(c)} for c in worse_rows]
    better = any(c.get("direction") == "better" for c in rows)
    # Improvements as a flag, not a count, and out of the gate (#706 item 8):
    # the count ticks 0 → 1 → 2 with nothing to say about the route ahead.
    out["changes_since_briefing"] = {"worse": worse[:4] or "none",
                                     "improved_since_briefing": "some" if better else "none"}
    # By identity, not message (#706 item 3): the same row re-worded (a
    # SIGMET's "from 08:35Z" dropping once valid) is not a change.
    gate["changes"] = sorted({f"{c.get('key')}|{c.get('direction')}|{c.get('to_value')}" for c in worse_rows})
    return out, gate


def facts(live: dict) -> dict:
    """The facts block for one tick, from a serialized live layer."""
    return facts_and_gate(live)[0]


def arrived(gate: dict) -> bool:
    """At or past the planned arrival (#706 item 6). The flown figure is
    interpolated from the plan, so this is the plan's arrival, not a landing:
    after it the "ahead" cells have abeam times in the past and every figure
    "ahead" is about a route the aircraft has (on paper) finished."""
    return gate.get("phase") == "arrived"


def gate_hash(gate: dict) -> str:
    """Stable hash of a gate state — the cheap first check: identical hash ⇒
    nothing to compare. Keys starting with ``_`` (the flown figure) are
    context for :func:`gate_changes`, not state."""
    view = {k: v for k, v in gate.items() if not k.startswith("_")}
    return hashlib.sha256(json.dumps(view, sort_keys=True, default=str).encode()).hexdigest()[:16]


def gate_changes(previous: dict, current: dict) -> list[str]:
    """What changed significantly between the state at the last generation and
    now (#706). Empty means the stored highlight still holds.

    Comparing with the last *generated* state, not the previous tick, is what
    stops slow drift slipping through one small step at a time — and since
    the state holds interpretations (category, convective level, weather
    families, wind band, 25 NM rain bins, cell flags), a fixed bucket cannot
    flap a regeneration per tick either: it regenerates once, when the state
    the highlight was written from no longer holds.

    One rule is not plain equality: a notable airport present at the last
    generation and gone now is **passed** when it is now behind the aircraft,
    and that is not a change (#706 item 7). One appearing that was not
    notable before is — the uncapped list means one merely sliding into the
    12-entry cap is not "new".
    """
    reasons = []
    for key in sorted((set(previous) | set(current)) - {"airports"}):
        if key.startswith("_"):
            continue
        if previous.get(key) != current.get(key):
            reasons.append(key)
    prev_ap, cur_ap = previous.get("airports") or {}, current.get("airports") or {}
    flown = current.get("_flown_nm") or 0
    for icao in sorted(set(prev_ap) | set(cur_ap)):
        p, c = prev_ap.get(icao), cur_ap.get(icao)
        if p is not None and c is not None:
            if {k: v for k, v in p.items() if k != "along"} != {k: v for k, v in c.items() if k != "along"}:
                reasons.append(f"airport {icao}")
        elif c is None:
            along = p.get("along")
            if along is None or along >= flown - PASSED_MARGIN_NM:
                reasons.append(f"airport {icao} no longer notable")
        else:
            reasons.append(f"airport {icao} now notable")
    return reasons


# --- Grounding --------------------------------------------------------------

#: Words the product voice never uses: the highlight points at the thing, the
#: pilot decides (``feedback_not_go_nogo``, meteorology-decisions §41).
VERDICT_WORDS = frozenset(
    "safe unsafe safely dangerous hazardous go no-go nogo avoid divert diverting "
    "recommend recommended should must advise advisable unflyable "
    "caution careful suggest suggested consider "
    # #715: Haiku 5.5 wrote "Watch LFAC…" on 19 of 311 A/B lines — advice.
    "watch watching monitor monitoring".split()
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
    # "wind" because the facts give an airport's wind as its advisory band
    # ("wind advisory amber"), never a gust figure (#706).
    "gusts": frozenset({"gusts", "gusting", "gust", "wind"}),
    "ceiling": frozenset({"ceiling", "overcast", "broken"}),
    "visibility": frozenset({"visibility", "vis"}),
}

#: A clause carrying one of these is not making a bindable claim about an
#: aerodrome's own conditions, so the place-binding rule skips it rather than
#: guess:
#:
#: - a negative or comparative ("no cell near LFMD", "LFMD better than
#:   briefed") — the rule cannot read a negation;
#: - a **SIGMET**, which describes a region and names an aerodrome only as the
#:   edge of it. Measured: "embedded thunderstorms from 235 NM to destination
#:   (LEMI)" is accurate — the span ends at LEMI, LEMI itself is VFR — and the
#:   rule rejected it until "sigmet" was listed here.
_HEDGES = frozenset(
    "no none not never without nothing clear quiet improving improved better "
    "easing clearing lifting sigmet sigmets".split()
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
#: The model narrating its own drafting. With thinking off (#715), naming a
#: banned word in the prompt made Haiku 5.5 write "Wait, that contains
#: 'watch'… Corrected:" into the highlight, or dump its reasoning about the
#: facts. Every leak on the replay had a blank line, so a line break alone
#: rejects; the words catch a one-paragraph leak.
_META_RE = re.compile(
    r"\n|\b(?:instructions?|corrected|the facts|highlight|wait|I was told|I'll|let me)\b", re.IGNORECASE)
#: "no lightning", "without thunderstorms": a stated absence, not a claim of
#: one. Haiku 5.5 writes "(no lightning)" after a cell, which the thunderstorm
#: rule read as the word itself (7 of 9 thunderstorm rejections on the #715 A/B).
_NEGATED_TS_RE = re.compile(
    r"\b(?:no|without|nor)\s+(?:lightning|thunderstorms?|ts)\b", re.IGNORECASE)
#: An aerodrome named only as the reference point of a distance — "85-125 NM
#: from EGBJ", "10 NM past LFMD" — is not the subject of the clause's weather.
#: Binding "rain" to it rejected correct lines on the #715 A/B.
_ANCHOR_ICAO_RE = re.compile(r"\bNM\b[^,;.]*?\b(?:from|after|past|beyond|before|of)\s+([A-Z]{4})\b")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_CLAUSE_RE = re.compile(r"[;,.]|\band\b|\bbut\b|\bwhile\b|\bwith\b")


def _icaos(text: str) -> set[str]:
    """Aerodrome codes in a piece of text, weather codes excluded."""
    return {c for c in _ICAO_RE.findall(text) if c not in _NOT_ICAO}


def _words(text: str) -> list[str]:
    """Lower-case tokens, keeping ``:`` *inside* a token but not on its edges.

    The colon has to survive so "09:47z" stays one token and a time is not
    read as two numbers. Stripping it at the edges matters just as much:
    without that, "SIGMETs:" tokenises as ``sigmets:`` and never matches the
    hedge list, which is how a correct SIGMET-span line kept being rejected.
    """
    out = []
    for raw in re.split(r"[^A-Za-z0-9+\-/:]+", text.lower()):
        w = raw.strip(":")
        if w:
            out.append(w)
    return out


def _conditions_in(text: str) -> set[str]:
    """Which airport conditions a piece of text claims.

    "Low IFR" is LIFR spelled out (Haiku 5.5 writes it); read word by word it
    claimed plain IFR at an LIFR airport and failed the binding (#715)."""
    toks = set(_words(re.sub(r"\blow\s+ifr\b", "lifr", text, flags=re.IGNORECASE)))
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

    Returns ``None`` when it passes, else a short reason. Before the rules,
    the line must be one paragraph of highlight, not the model's drafting
    (#715: with thinking off it sometimes narrates its own corrections). Five
    rules, each one a failure seen in the experiment or a voice rule from the
    issue:

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
    bound (ambiguous attribution), a clause hedged with "no"/"better" is
    skipped (rule 3 cannot read a negation), and an aerodrome that only anchors
    a distance ("NM from EGBJ") is not bound to the clause's weather. A stated
    absence ("no lightning") does not count as saying thunderstorm (rule 5). Both let a wrong line through
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

    meta = _META_RE.search(text)
    if meta:
        return f"not a highlight (drafting text: {meta.group(0).strip() or 'line break'!r})"

    blob = json.dumps(f, default=str)

    unknown = sorted({c for c in _icaos(text) if c not in blob})
    if unknown:
        return f"ICAO not in facts: {', '.join(unknown)}"

    fact_numbers = set(_NUM_RE.findall(blob))
    invented = sorted({n for n in _NUM_RE.findall(text) if n not in fact_numbers})
    if invented:
        return f"figure not in facts: {', '.join(invented)}"

    said = set(_words(text))
    asserted = set(_words(_NEGATED_TS_RE.sub(" ", text)))
    verdict = sorted(said & VERDICT_WORDS)
    if verdict:
        return f"verdict word: {', '.join(verdict)}"

    if asserted & AIRPORT_CONDITIONS["thunderstorm"]:
        # Two sources can license the word, and they license different claims
        # (§41: a radar core is a "cell"; only observed electrification or a
        # TS hazard earns "thunderstorm").
        observed = "lightning_flashes" in blob or bool(re.search(r"\bTS\b|TSRA|VCTS|TSGR", blob))
        if not observed:
            return "says thunderstorm without lightning in the facts"
        # An *airport* reporting TSRA does not make the cell at 180 NM a
        # thunderstorm. A clause that says thunderstorm about a position
        # rather than an aerodrome needs the cells or a SIGMET to carry it —
        # otherwise the station's TS has been moved onto a radar core, which
        # the place-binding rule below cannot see because there is no ICAO in
        # the clause to bind to.
        from_cells = "lightning_flashes" in blob
        from_sigmet = any(
            re.search(r"\bTS\b|TSRA|TSGR", str(s.get("what") or ""))
            for s in (f.get("sigmets_ahead") or [])
            if isinstance(s, dict)
        )
        if not (from_cells or from_sigmet):
            for clause in _CLAUSE_RE.split(_NEGATED_TS_RE.sub(" ", text)):
                words_here = set(_words(clause))
                if not (words_here & AIRPORT_CONDITIONS["thunderstorm"]):
                    continue
                if _icaos(clause):
                    continue  # about an aerodrome: rule 3 binds it
                if _NUM_RE.search(clause):
                    return "says thunderstorm at a position, but only a station reports TS"

    for clause in _CLAUSE_RE.split(text):
        icaos = _icaos(clause) - set(_ANCHOR_ICAO_RE.findall(clause))
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


_client = None
_client_lock = threading.Lock()


def _anthropic_client():
    """One client for the process, built on first use.

    The tick fans out up to ``_HIGHLIGHT_WORKERS`` threads, and a client per
    call meant a new HTTP connection pool per flight per tick. The SDK client
    is safe to share across threads; building it is what needs the lock.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import anthropic

                _client = anthropic.Anthropic(
                    timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES,
                )
    return _client


class HighlightRefused(Exception):
    """The model declined (``stop_reason == "refusal"``). Billed, unlike a
    transport failure, so the caller logs it as a rejection: it counts toward
    ``MAX_ATTEMPTS_PER_FACTS`` instead of being retried every tick. Haiku 5.5
    runs safety classifiers and has no server-side fallback (#715)."""

    def __init__(self, category: str | None, usage: dict, latency_ms: int):
        super().__init__(f"refusal ({category or 'no category'})")
        self.usage, self.latency_ms = usage, latency_ms


def generate(f: dict, model: str = DEFAULT_MODEL) -> tuple[str, dict, int]:
    """One highlight from one facts block: ``(text, usage, latency_ms)``.

    Raises :class:`HighlightRefused` on a refusal, and any other exception on
    an API failure — every caller treats both as "no highlight this tick" and
    keeps the previous one.
    """
    client = _anthropic_client()
    t = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        thinking=THINKING,
        output_config={"effort": EFFORT},
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
    if resp.stop_reason == "refusal":
        details = getattr(resp, "stop_details", None)
        raise HighlightRefused(getattr(details, "category", None), usage, latency_ms)
    return text, usage, latency_ms


# --- Orchestration ----------------------------------------------------------


def facts_and_gate_for(layer) -> tuple[dict, dict]:
    """The facts block and gate state for a committed :class:`LiveLayer`.

    Goes through the serialized form on purpose: the experiment script feeds
    :func:`facts` a ``live.json`` / ``/live`` body, and prompt v3 was
    calibrated against that exact shape. One code path, one input shape.
    """
    return facts_and_gate(layer.model_dump(mode="json"))


def carry_forward(stored, layer) -> bool:
    """Keep the previous highlight unless the weather moved significantly.

    ``build_glance`` rebuilds the whole glance every tick, so without this the
    previous highlight is destroyed on every commit and every tick pays for a
    new one. Returns True when one was carried over. Never raises: a highlight
    problem must not fail a tick.

    The comparison is against the gate state stored **with the highlight**
    — the state it was written from — not the previous tick (#706). A carried
    highlight keeps that baseline, so drift is measured from where the text
    was true. After the planned arrival nothing is carried (item 6): the
    "ahead" a kept line talks about is a route already flown, so the layer
    falls back to the nutshell headline.

    Runs *inside* the commit because the glance it attaches to is written
    there. Pure computation — no model call, no network.
    """
    try:
        previous = stored.glance.highlight if stored is not None and stored.glance else None
        if previous is None or layer.glance is None:
            return False
        _f_block, gate = facts_and_gate_for(layer)
        if arrived(gate):
            return False
        if previous.gate is None:
            # Written before #706: its hash is of the old facts shape, so
            # nothing to compare against. Regenerate once, then gate.
            return False
        if previous.facts_hash != gate_hash(gate):
            reasons = gate_changes(previous.gate, gate)
            if reasons:
                logger.info("Live highlight regenerates for %s: %s",
                            getattr(layer, "flight_id", "?"), ", ".join(reasons))
                return False
        layer.glance.highlight = previous
        return True
    except Exception:
        logger.warning("Live highlight carry-forward failed for %s — regenerating", getattr(layer, "flight_id", "?"), exc_info=True)
        return False


def call_cost(usage: dict | None) -> float | None:
    """USD for one call, or None when nothing was billed.

    Pure pricing (``compute_call_cost``, no session), so the review log can
    carry the cost of every attempt — including a rejected one, which is
    billed and otherwise invisible. The ledger row is the accounting record;
    this is what makes a calibration review self-contained, since the admin
    cost views filter on ``category == "briefing"`` and do not show these.
    """
    if not usage or not usage.get("model"):
        return None
    try:
        from weatherbrief.costs import compute_call_cost

        return compute_call_cost(
            usage["model"],
            input_tokens=usage.get("input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            cache_read_tokens=usage.get("cache_read_tokens") or 0,
            cache_write_tokens=usage.get("cache_write_tokens") or 0,
        )
    except Exception:
        # An unpriced model raises rather than guess; a missing cost must not
        # cost us the highlight.
        logger.warning("Live highlight cost pricing failed", exc_info=True)
        return None


def rejected_attempts(flight_dir: Path | str, digest: str) -> int:
    """How many billed generations this gate state has already thrown away.

    Counted from the review log, which is already per flight and append-only —
    no extra state to carry across ticks. Only ``rejected`` counts: a
    ``written`` one is carried forward anyway, and a ``call_failed`` one is a
    timeout or an outage that cost nothing and is right to retry.
    """
    path = Path(flight_dir) / LIVE_HIGHLIGHT_LOG
    if not path.exists():
        return 0
    n = 0
    try:
        for line in path.read_text().splitlines():
            # Cheap reject first: this runs every tick for every flight with no
            # highlight, and all but one or two lines of the log belong to
            # other facts states. Parsing each one as JSON to find that out is
            # per-tick work that grows with the flight.
            if digest not in line or '"rejected"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a write cut short; never fail a tick over the log
            if rec.get("facts_hash") == digest and rec.get("outcome") == "rejected":
                n += 1
    except OSError:
        logger.warning("Unreadable highlight log %s — retrying generation", path, exc_info=True)
        return 0
    return n


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

    No-ops when nothing significant changed (``carry_forward`` already
    attached the previous highlight during the commit), which is most ticks,
    and after the planned arrival.

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
        f, gate = facts_and_gate_for(layer)
    except Exception:
        logger.exception("LIVE_HIGHLIGHT_FAILED flight=%s — could not build facts", layer.flight_id)
        return HighlightOutcome("skipped")
    if arrived(gate):
        # #706 item 6: 34 of a flight day's 311 attempts came after the planned
        # arrival, about "ahead" cells already abeam in the past.
        return HighlightOutcome("skipped", reason="after planned arrival")

    # The retry cap counts per gate state, so a rejected state is not retried
    # on a change the gate ignores (a rain-motion word, an airport passed).
    digest = gate_hash(gate)
    already = rejected_attempts(flight_dir, digest)
    if already >= MAX_ATTEMPTS_PER_FACTS:
        # Logged without the facts block: a one-line marker keeps the
        # frequency visible for the calibration review without paying for a
        # third identical rejection or repeating a 1.5 kB block every tick.
        _log_attempt(flight_dir, {
            "at": datetime.now(timezone.utc), "flight_id": layer.flight_id,
            "facts_hash": digest, "outcome": "skipped_rejected", "attempts": already,
        })
        return HighlightOutcome("skipped", reason=f"{already} rejected attempts for these facts")

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
    except HighlightRefused as exc:
        logger.warning("LIVE_HIGHLIGHT_REJECTED flight=%s reason=%s", layer.flight_id, exc)
        _log_attempt(flight_dir, {**base, "outcome": "rejected", "reason": str(exc), "text": "",
                                  "usage": exc.usage, "latency_ms": exc.latency_ms,
                                  "cost_usd": call_cost(exc.usage)})
        return HighlightOutcome("rejected", text="", usage=exc.usage, reason=str(exc))
    except Exception as exc:
        # Expected failure mode (timeout, rate limit, outage). One line, not a
        # traceback per tick: the highlight is optional and the tick is intact.
        logger.warning("Live highlight call failed for %s: %s", layer.flight_id, exc)
        # Without the facts block. There is no text to judge against them, so
        # they would add nothing to the review — and a sustained outage retries
        # every tick (deliberately: a timeout costs nothing and is right to
        # retry), which with the block attached wrote ~1.8 kB per flight per
        # tick for as long as it lasted.
        _log_attempt(flight_dir, {
            k: v for k, v in base.items() if k != "facts"
        } | {"outcome": "call_failed", "error": str(exc)})
        return HighlightOutcome("call_failed", reason=str(exc))

    reason = check_grounding(text, f)
    if reason is not None:
        # Loud: a systematic rejection means the prompt drifted, and the
        # fallback (the nutshell headline) hides it from every surface.
        logger.warning(
            "LIVE_HIGHLIGHT_REJECTED flight=%s reason=%s text=%r", layer.flight_id, reason, text,
        )
        _log_attempt(flight_dir, {**base, "outcome": "rejected", "reason": reason,
                                  "text": text, "usage": usage, "latency_ms": latency_ms,
                                  "cost_usd": call_cost(usage)})
        return HighlightOutcome("rejected", text=text, usage=usage, reason=reason)

    written = patch_highlight(
        flight_dir,
        LiveHighlight(text=text, model=model, facts_hash=digest, gate=gate,
                      generated_at=datetime.now(timezone.utc), latency_ms=latency_ms),
        pack_timestamp=layer.pack_timestamp,
        as_of=layer.glance.as_of,
    )
    _log_attempt(flight_dir, {**base, "outcome": "written" if written else "superseded",
                              "text": text, "usage": usage, "latency_ms": latency_ms,
                              "cost_usd": call_cost(usage)})
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
