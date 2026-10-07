"""Experiment (#690 follow-up): a one-line "highlight" of a live tick, written
by a small model from facts the code has already computed.

    python scripts/live_highlight_experiment.py <live.json or /live body> [...]
        [--model claude-haiku-4-5] [--facts-only]

Code does the weather: it reduces the tick (glance, ribbon bands, storms,
SIGMETs, change rows) to a short facts block. The model only chooses what
leads and phrases it, under the product's voice rules (no verdict, facts
only, plain words). Not wired into the app.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

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
- Plain cockpit words. Places as distance along the route ("mid-route", "last 50 NM", "near LFMD") or ICAO codes. Times in Z.
- Keep every condition at the place the facts give it. A route airport's METAR is that airport's, never the destination's.
- Airports along the route matter when they show something notable (non-VFR, showers, thunderstorms, a worse TAF at your time); don't list VFR ones.
- "Cell" for a radar core; say "thunderstorm" only when the facts give lightning for it.
- Say how weather moves relative to the route when the facts give it (crossing it, moving away, closing).
- Prefer the big picture over a list: one rain area crossing the route matters more than its parts.
- When nothing notable is ahead, say so plainly in one short sentence (for example "Quiet route ahead: VFR at both ends, no cells near the track.").
- Output only the highlight text, no preamble, no quotes, no markdown."""


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
        corridor = r.get("weather_corridor_nm") or 30
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


def _at(live: dict, flown: float | None) -> dict:
    """The tick as if ``flown`` NM were flown: storms behind that point drop
    out of "ahead"."""
    if flown is None:
        return live
    live = json.loads(json.dumps(live))
    r = live.get("ribbon") or {}
    r["flown_nm"] = flown
    for s in (live.get("storms") or {}).get("storms") or []:
        s["ahead"] = (s.get("along_nm") or 0) >= flown
    return live


def highlight(client, model: str, f: dict) -> tuple[str, dict, float]:
    t = time.time()
    extra = {} if model.startswith("claude-haiku") else {"output_config": {"effort": "low"}}
    resp = client.messages.create(
        model=model, max_tokens=2000, system=SYSTEM, **extra,
        messages=[{"role": "user", "content": "FACTS:\n" + json.dumps(f, indent=1, ensure_ascii=False)}],
    )
    text = next((b.text for b in resp.content if b.type == "text"), "").strip()
    return text, resp.usage, time.time() - t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--facts-only", action="store_true")
    ap.add_argument("--show-facts", action="store_true")
    ap.add_argument("--flown", type=float, action="append",
                    help="replay the tick as if this many NM were flown (repeatable)")
    args = ap.parse_args()

    client = None
    if not args.facts_only:
        import anthropic
        from dotenv import load_dotenv

        load_dotenv()
        client = anthropic.Anthropic()

    cases = []
    for p in args.files:
        live = json.loads(p.read_text())
        live = live.get("body", live)
        for flown in args.flown or [None]:
            cases.append((p, _at(live, flown)))

    total_in = total_out = 0
    for p, live in cases:
        f = facts(live)
        print(f"\n=== {p.name}  ({f['route']}, {f['now']}, {f['flight']})")
        g = live.get("glance") or {}
        if g:
            print("nutshell:", g.get("headline"))
        if args.facts_only or args.show_facts:
            print(json.dumps(f, indent=1, ensure_ascii=False))
        if client is None:
            continue
        text, usage, dt = highlight(client, args.model, f)
        total_in += usage.input_tokens
        total_out += usage.output_tokens
        print(f"HIGHLIGHT ({args.model}, {dt:.1f}s, {usage.input_tokens} in / {usage.output_tokens} out):\n  {text}")
    if client is not None:
        price = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5-5": (2.0, 10.0)}.get(args.model, (4.0, 20.0))
        cost = total_in / 1e6 * price[0] + total_out / 1e6 * price[1]
        print(f"\n{len(cases)} calls, {total_in} in / {total_out} out tokens, ${cost:.4f}"
              f" (${cost / len(cases):.5f} per tick)")


if __name__ == "__main__":
    sys.exit(main())
