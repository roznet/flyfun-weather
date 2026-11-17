"""Debrief report, phase 0 prototype (debrief brainstorm, 2026-11-17).

NOT FOR MERGE as is. A prototype to try the post-flight report on real
flights imported into a dev server (``scripts/ops/import_flight.py``), so the
rules and wording can be iterated before the real thing is planned.

The report is built on the server from what already exists:

- the observed recap (``tasks/live_recap.recap_for_flight``): per segment what
  radar saw when the aircraft was there, the alerts, the time base;
- the forecast the pilot flew with: the recap's default pack
  ``route_advisories.json`` (per-model ribbons for the strip, per-model
  highlight *regions* with base/top for the questions);
- the live history: METAR/TAF records for the airports at actual times, and
  the app's position fixes for the flown altitude;
- the route locator, for "between Lydd and Abbeville" wording.

It answers four things:

1. ``summary``: one deterministic line, what matched and what did not;
2. ``strip``: forecast / observed / you lanes per recap segment;
3. ``airports``: departure and arrival METAR at the actual times vs the TAF
   the pilot had;
4. ``questions``: one to three one-tap questions, only on what the
   instruments cannot see (icing, cloud at your level, turbulence, showers
   where radar had no coverage).

Answers are kept in ``debrief_answers.json`` in the flight directory: no
table, no migration, since this is a prototype. ``interest`` says whether
the flight would have earned a push.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

REPORT_VERSION = "phase0-1"
ANSWERS_FILE = "debrief_answers.json"
MAX_QUESTIONS = 3

ICING_ADVISORIES = ("aircraft_icing", "fiki_icing", "icing_escape", "freezing_precip")
CLOUD_ADVISORIES = ("vmc_cruise", "cloud_top")
TURBULENCE_ADVISORIES = ("turbulence", "mountain_wind")
CONVECTIVE_ADVISORIES = ("convective", "convective_character", "enroute_precip")
#: The forecast lane of the strip: the same set as the web recap's
#: ``ROUTE_WEATHER_ADVISORIES`` (web/ts/helpers/debrief-replay.ts).
ROUTE_WEATHER_ADVISORIES = ICING_ADVISORIES + CLOUD_ADVISORIES + TURBULENCE_ADVISORIES + CONVECTIVE_ADVISORIES

HAZARD_OF = {
    **{a: "icing" for a in ICING_ADVISORIES},
    **{a: "cloud" for a in CLOUD_ADVISORIES},
    **{a: "turbulence" for a in TURBULENCE_ADVISORIES},
    **{a: "showers" for a in CONVECTIVE_ADVISORIES},
}

#: An icing / cloud / turbulence region counts as "at your level" when the
#: flown altitude is inside it or within this margin of it.
LEVEL_MARGIN_FT = 1000
#: Regions closer than this along the route merge into one stretch.
STRETCH_GAP_NM = 5.0
#: Radar thresholds for the observed lane (VIP 3 "heavy", as the live layer).
HEAVY_DBZ = 41.0
RAIN_DBZ = 20.0
#: METAR at the departure / arrival: the report nearest the time, within this.
METAR_WINDOW = timedelta(minutes=60)

SEV_RANK = {"green": 0, "amber": 1, "red": 2}
CATEGORY_RANK = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}

QUESTION_OPTIONS = {
    "icing": [
        {"id": "none", "label": "None"},
        {"id": "light", "label": "Light"},
        {"id": "moderate_plus", "label": "Moderate or more"},
        {"id": "not_in_cloud", "label": "Not in cloud"},
    ],
    "cloud": [
        {"id": "in_cloud", "label": "In cloud"},
        {"id": "in_and_out", "label": "In and out"},
        {"id": "on_top", "label": "On top"},
        {"id": "below", "label": "Below it"},
        {"id": "clear", "label": "No cloud"},
    ],
    "turbulence": [
        {"id": "none", "label": "Smooth"},
        {"id": "light", "label": "Light"},
        {"id": "moderate_plus", "label": "Moderate or more"},
    ],
    "showers": [
        {"id": "none", "label": "Nothing"},
        {"id": "showers", "label": "Showers"},
        {"id": "build_ups", "label": "Build-ups or CB"},
    ],
}


# --- Small helpers --------------------------------------------------------------


def _utc(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hhmmz(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).strftime("%H:%MZ") if t else None


def _ft(v: float | None) -> str:
    if v is None:
        return "?"
    v = int(round(v / 100.0) * 100)
    return f"FL{v // 100:03d}" if v >= 10000 else f"{v:,} ft"


def _combine(sevs: list[str], aggregation: str) -> str | None:
    """``AdvisoryStatus.worst`` / ``.majority`` on severity strings."""
    from weatherbrief.models.advisories import AdvisoryStatus

    valid = [AdvisoryStatus(s) for s in sevs if s in SEV_RANK]
    if not valid:
        return None
    out = AdvisoryStatus.worst(valid) if aggregation == "worst" else AdvisoryStatus.majority(valid)
    return out.value


def _band_at(ribbon: list, along: float) -> str | None:
    """Severity of the ribbon band containing ``along`` (lane format
    ``[from, to, severity]``)."""
    for b in ribbon or []:
        if len(b) >= 3 and b[0] <= along <= b[1]:
            return b[2]
    return None


# --- Forecast inputs ------------------------------------------------------------


def load_manifest(pack_dir: Path | None) -> dict | None:
    """The pack's ``route_advisories.json`` as a plain dict (survives T1)."""
    if pack_dir is None:
        return None
    path = Path(pack_dir) / "route_advisories.json"
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except Exception:
        logger.warning("Unreadable route_advisories.json in %s", pack_dir, exc_info=True)
        return None


def forecast_at(lane: dict | None, along: float) -> dict:
    """Worst route-weather severity at ``along`` across advisories, each
    advisory's models combined with the pack's own aggregation, with the
    hazards that reach it."""
    if not lane:
        return {"severity": None, "hazards": []}
    agg = lane.get("aggregation") or "majority"
    worst, hazards = None, set()
    for adv in lane.get("advisories") or []:
        aid = adv.get("advisory_id")
        if aid not in ROUTE_WEATHER_ADVISORIES:
            continue
        sevs = [s for m in adv.get("per_model") or [] if (s := _band_at(m.get("ribbon"), along))]
        sev = _combine(sevs, agg)
        if sev is None:
            continue
        if worst is None or SEV_RANK[sev] > SEV_RANK[worst]:
            worst = sev
        if sev in ("amber", "red"):
            hazards.add(HAZARD_OF[aid])
    return {"severity": worst, "hazards": sorted(hazards)}


def hazard_stretches(manifest: dict | None, advisories: tuple[str, ...], flown_ft: float | None) -> list[dict]:
    """Flagged (amber/red) highlight regions of ``advisories`` across models,
    merged along the route into stretches. Each stretch keeps the envelope of
    base/top, the worst severity, the models that flagged it and whether the
    flown altitude is inside it (``at_level``)."""
    if not manifest:
        return []
    regions = []
    models_total = set()
    for adv in manifest.get("advisories") or []:
        if adv.get("advisory_id") not in advisories:
            continue
        for m in adv.get("per_model") or []:
            if m.get("status") == "unavailable":
                continue
            models_total.add(m.get("model"))
            for r in ((m.get("highlights") or {}).get("regions") or []):
                if r.get("severity") not in ("amber", "red"):
                    continue
                regions.append({**r, "model": m.get("model"), "advisory_id": adv.get("advisory_id")})
    regions.sort(key=lambda r: r["dist_from_nm"])
    stretches: list[dict] = []
    for r in regions:
        cur = stretches[-1] if stretches else None
        if cur is not None and r["dist_from_nm"] <= cur["to_nm"] + STRETCH_GAP_NM:
            cur["to_nm"] = max(cur["to_nm"], r["dist_to_nm"])
            cur["regions"].append(r)
        else:
            stretches.append({"from_nm": r["dist_from_nm"], "to_nm": r["dist_to_nm"], "regions": [r]})
    out = []
    for s in stretches:
        rs = s.pop("regions")
        bases = [r["base_ft"] for r in rs if r.get("base_ft") is not None]
        tops = [r["top_ft"] for r in rs if r.get("top_ft") is not None]
        full_column = any(r.get("base_ft") is None and r.get("top_ft") is None for r in rs)
        at_level = None
        if flown_ft is not None:
            at_level = full_column or any(
                (r.get("base_ft") if r.get("base_ft") is not None else -1e9) - LEVEL_MARGIN_FT
                <= flown_ft
                <= (r.get("top_ft") if r.get("top_ft") is not None else 1e9) + LEVEL_MARGIN_FT
                for r in rs
            )
        out.append({
            **s,
            "base_ft": min(bases) if bases else None,
            "top_ft": max(tops) if tops else None,
            "severity": "red" if any(r["severity"] == "red" for r in rs) else "amber",
            "models": sorted({r["model"] for r in rs}),
            "models_total": len(models_total),
            "advisories": sorted({r["advisory_id"] for r in rs}),
            "kinds": sorted({r.get("kind") for r in rs if r.get("kind")}),
            "at_level": at_level,
        })
    return out


# --- Observed inputs ------------------------------------------------------------


def observed_class(seg: dict) -> str:
    """One word per recap segment for the observed lane. Radar sees
    precipitation and convection only; icing and cloud are not observed."""
    status = seg.get("status")
    if status in ("not_reached", "before_recording", "untimed"):
        return "not_recorded"
    if seg.get("radar_status") != "measured":
        return "no_data"
    if seg.get("lightning"):
        return "heavy"
    dbz = seg.get("radar_max_dbz")
    if dbz is None:
        return "clear"
    if dbz >= HEAVY_DBZ:
        return "heavy"
    if dbz >= RAIN_DBZ:
        return "rain"
    return "clear"


def flown_altitude(history: list[dict], route_nm: float | None, planned_ft: float | None) -> dict:
    """The cruise altitude: median of the app's airborne fixes in the middle
    of the route when there are any, else the planned cruise."""
    alts = []
    for r in history:
        if r.get("type") != "progress" or r.get("event") != "fix":
            continue
        alt, along = r.get("alt_ft"), r.get("along_nm")
        terrain = r.get("terrain_ft")
        if alt is None:
            continue
        if terrain is not None and alt - terrain < 1000:
            continue  # on the ground or in the circuit
        if route_nm and along is not None and not (0.2 * route_nm <= along <= 0.8 * route_nm):
            continue
        alts.append(float(alt))
    if len(alts) >= 2:
        return {"ft": round(median(alts), -2), "source": "fixes", "fixes": len(alts)}
    return {"ft": planned_ft, "source": "planned", "fixes": len(alts)}


def airport_at(history: list[dict], icao: str, at: datetime | None, taf_before: datetime | None) -> dict:
    """METAR nearest ``at`` and the TAF the pilot had (latest issued before
    ``taf_before``) read at ``at``; ``match`` compares their categories."""
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.analysis.taf_reading import read_taf_at

    out: dict = {"icao": icao, "at": at.isoformat() if at else None}
    if at is None:
        out["match"] = "untimed"
        return out
    metars = [r for r in history if r.get("type") == "report" and r.get("kind") == "metar"
              and (r.get("icao") or "").upper() == icao and r.get("raw")]
    best = None
    for r in metars:
        t = _utc(r.get("observed_at"))
        if t is None:
            continue
        d = abs(t - at)
        if d <= METAR_WINDOW and (best is None or d < best[0]):
            best = (d, r, t)
    if best is not None:
        _, r, t = best
        out["metar_raw"] = r["raw"]
        out["metar_time"] = t.isoformat()
        cat = r.get("flight_category")
        if cat is None:
            rep = WeatherParser.parse_metar(r["raw"], source="live_history", reference=t)
            cat = rep.flight_category.value if rep is not None and rep.flight_category is not None else None
        out["metar_category"] = cat
    tafs = [r for r in history if r.get("type") == "report" and r.get("kind") == "taf"
            and (r.get("icao") or "").upper() == icao and r.get("raw")]
    cutoff = taf_before or at
    had = [r for r in tafs if (_utc(r.get("issued_at")) or _utc(r.get("tick_at")) or cutoff) <= cutoff]
    taf_rec = max(had or tafs, key=lambda r: r.get("issued_at") or "", default=None)
    if taf_rec is not None:
        taf = WeatherParser.parse_taf(taf_rec["raw"], source="live_history",
                                      reference=_utc(taf_rec.get("issued_at")) or at)
        reading = read_taf_at(taf, at) if taf is not None else None
        out["taf_issued_at"] = taf_rec.get("issued_at")
        if reading is not None:
            out["taf_category"] = reading.prevailing_category
            out["taf_temporary_category"] = reading.temporary_category
            out["taf_temporary_type"] = reading.temporary_type
    m = out.get("metar_category")
    cats = [c for c in (out.get("taf_category"), out.get("taf_temporary_category")) if c in CATEGORY_RANK]
    if m not in CATEGORY_RANK or not cats:
        out["match"] = "unverifiable"
    elif m in cats:
        out["match"] = "as_forecast"
    elif CATEGORY_RANK[m] > max(CATEGORY_RANK[c] for c in cats):
        out["match"] = "lower"
    else:
        out["match"] = "better"
    return out


# --- Questions -----------------------------------------------------------------


def _qid(kind: str, s: dict) -> str:
    return f"{kind}-{int(s['from_nm'])}-{int(s['to_nm'])}"


def _span(locator, s: dict) -> str:
    if locator is not None:
        try:
            return locator.span(s["from_nm"], s["to_nm"])
        except Exception:
            logger.debug("locator span failed", exc_info=True)
    return f"from {s['from_nm']:.0f} to {s['to_nm']:.0f} NM"


def _models_phrase(s: dict) -> str:
    n, total = len(s["models"]), s.get("models_total") or len(s["models"])
    return "all models" if n == total else f"{n} of {total} models"


def build_questions(manifest, segments, flown, progress, locator) -> list[dict]:
    """One question per hazard stretch the instruments could not verify, at
    the flown level, ranked icing first then by severity and length."""
    flown_ft = flown.get("ft")
    level_txt = f"You were at {_ft(flown_ft)}" + (" (planned)" if flown.get("source") == "planned" else "")
    candidates: list[tuple[tuple, dict]] = []

    def eta(s):
        t = progress.eta((s["from_nm"] + s["to_nm"]) / 2) if progress is not None else None
        return _hhmmz(t)

    for kind, advisories, rank in (
        ("icing", ICING_ADVISORIES, 0), ("cloud", CLOUD_ADVISORIES, 2), ("turbulence", TURBULENCE_ADVISORIES, 1),
    ):
        for s in hazard_stretches(manifest, advisories, flown_ft):
            if s["at_level"] is False:
                continue
            band = (f"between {_ft(s['base_ft'])} and {_ft(s['top_ft'])}"
                    if s["base_ft"] is not None and s["top_ft"] is not None else "")
            when = eta(s)
            where = _span(locator, s)
            if kind == "icing":
                text = (f"Icing was forecast {band} {where}".replace("  ", " ")
                        + (f", around {when}" if when else "")
                        + f" ({_models_phrase(s)}). {level_txt}. What did you get?")
            elif kind == "cloud":
                text = (f"The forecast had you in cloud {where}" + (f" around {when}" if when else "")
                        + f" ({_models_phrase(s)}). {level_txt}. Were you?")
            else:
                text = (f"Turbulence was forecast {band} {where}".replace("  ", " ")
                        + (f", around {when}" if when else "")
                        + f" ({_models_phrase(s)}). {level_txt}. How was it?")
            q = {
                "id": _qid(kind, s), "kind": kind, "text": text,
                "options": QUESTION_OPTIONS[kind], "avoidable": kind in ("icing", "turbulence"),
                "stretch": {k: s[k] for k in ("from_nm", "to_nm", "base_ft", "top_ft", "severity",
                                              "models", "models_total", "advisories", "kinds")},
                "at": when, "flown_ft": flown_ft, "flown_source": flown.get("source"),
            }
            key = (rank, -SEV_RANK[s["severity"]], -len(s["models"]), -(s["to_nm"] - s["from_nm"]))
            candidates.append((key, q))

    # Showers only where radar could not see a forecast convective stretch.
    for s in hazard_stretches(manifest, CONVECTIVE_ADVISORIES, None):
        segs = [g for g in segments if g.get("to_nm", 0) > s["from_nm"] and g.get("from_nm", 0) < s["to_nm"]]
        if segs and any(observed_class(g) not in ("no_data", "not_recorded") for g in segs):
            continue
        when = eta(s)
        q = {
            "id": _qid("showers", s), "kind": "showers",
            "text": (f"Showers were forecast {_span(locator, s)}" + (f" around {when}" if when else "")
                     + " and radar had no coverage there. What did you see?"),
            "options": QUESTION_OPTIONS["showers"], "avoidable": True,
            "stretch": {k: s[k] for k in ("from_nm", "to_nm", "severity", "models", "models_total", "advisories")},
            "at": when, "flown_ft": flown_ft, "flown_source": flown.get("source"),
        }
        candidates.append(((3, -SEV_RANK[s["severity"]], 0, 0), q))

    candidates.sort(key=lambda kq: kq[0])
    seen_kinds: dict[str, int] = {}
    out = []
    for _, q in candidates:
        # At most one question per kind first, then fill up.
        if seen_kinds.get(q["kind"]):
            continue
        seen_kinds[q["kind"]] = 1
        out.append(q)
        if len(out) == MAX_QUESTIONS:
            break
    return out


# --- Summary and interest --------------------------------------------------------


def build_summary(strip: list[dict], airports: list[dict], alerts: list[dict], locator) -> dict:
    """One deterministic line, plus the parts it was built from."""
    parts, diffs, checked = [], 0, 0
    heavy = [s for s in strip if s["observed"] == "heavy"]
    if heavy:
        lo, hi = heavy[0]["from_nm"], heavy[-1]["to_nm"]
        forecast = any("showers" in s["forecast_hazards"] for s in heavy)
        where = _span(locator, {"from_nm": lo, "to_nm": hi})
        checked += 1
        if forecast:
            parts.append(f"Heavy showers {where}, as forecast.")
        else:
            diffs += 1
            parts.append(f"Heavy showers {where} that the forecast did not have.")
    else:
        quiet = [s for s in strip if "showers" in s["forecast_hazards"] and s["observed"] in ("clear", "rain")]
        if quiet:
            checked += 1
            where = _span(locator, {"from_nm": quiet[0]["from_nm"], "to_nm": quiet[-1]["to_nm"]})
            parts.append(f"Showers were forecast {where}; radar saw nothing heavy.")
    names = {"departure": "Departure", "arrival": "Arrival"}
    for a in airports:
        label = f"{names[a['role']]} {a['icao']}"
        if a.get("match") in ("as_forecast", "lower", "better"):
            checked += 1
        if a.get("match") == "as_forecast":
            parts.append(f"{label} as the TAF said.")
        elif a.get("match") == "lower":
            diffs += 1
            parts.append(f"{label} came in lower than the TAF ({a.get('metar_category')} against {a.get('taf_category')}).")
        elif a.get("match") == "better":
            parts.append(f"{label} better than the TAF.")
    raised = [a for a in alerts if a.get("tier") == "alert" and a.get("event") == "raised"]
    if raised:
        parts.append(f"{len(raised)} alert{'s' if len(raised) > 1 else ''} during the flight.")
    no_data = sum(1 for s in strip if s["observed"] == "no_data")
    if strip and no_data == len(strip):
        parts.append("Radar had no coverage along the route.")
    if diffs:
        lead = "Some differences from the forecast."
    elif checked:
        lead = "Mostly as forecast."
    else:
        # Nothing the instruments could compare: never read that as "as forecast".
        lead = "Little the instruments could check on this flight."
    return {"text": " ".join([lead, *parts]), "differences": diffs, "checked": checked, "parts": parts}


def build_interest(lane, strip, airports, alerts, questions) -> dict:
    """Would this flight earn a push? Any reason below makes it interesting."""
    reasons = []
    flagged = [a["advisory_id"] for a in (lane or {}).get("advisories") or []
               if a.get("advisory_id") in ROUTE_WEATHER_ADVISORIES and a.get("aggregate_status") in ("amber", "red")]
    if flagged:
        reasons.append({"kind": "forecast_flagged", "advisories": sorted(set(flagged))})
    if any(a.get("tier") == "alert" for a in alerts):
        reasons.append({"kind": "alert_in_flight"})
    if any(s["observed"] == "heavy" for s in strip):
        reasons.append({"kind": "heavy_precip_observed"})
    if any(a.get("match") == "lower" for a in airports):
        reasons.append({"kind": "airport_lower_than_taf"})
    if questions:
        reasons.append({"kind": "questions", "count": len(questions)})
    return {"interesting": bool(reasons), "reasons": reasons}


# --- Answers ---------------------------------------------------------------------


def load_answers(flight_dir: Path | None) -> dict:
    if flight_dir is None:
        return {}
    path = Path(flight_dir) / ANSWERS_FILE
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        logger.warning("Unreadable %s in %s", ANSWERS_FILE, flight_dir, exc_info=True)
        return {}


def save_answer(flight_dir: Path, question: dict, answer: str | None, *, avoided: bool = False,
                note: str | None = None, now: datetime | None = None) -> dict:
    """Store (or clear, with ``answer=None``) one answer, with the question as
    asked so a later rules change cannot reinterpret it."""
    answers = load_answers(flight_dir)
    if answer is None:
        answers.pop(question["id"], None)
    else:
        if answer not in {o["id"] for o in question["options"]}:
            raise ValueError(f"Unknown answer {answer!r} for {question['id']}")
        answers[question["id"]] = {
            "answer": answer, "avoided": bool(avoided), "note": (note or None),
            "answered_at": (now or datetime.now(timezone.utc)).isoformat(),
            "question": {k: question.get(k) for k in ("kind", "text", "stretch", "at", "flown_ft", "flown_source")},
            "report_version": REPORT_VERSION,
        }
    (Path(flight_dir) / ANSWERS_FILE).write_text(json.dumps(answers, indent=1))
    return answers


# --- Assembly ---------------------------------------------------------------------


def build_report(recap: dict, *, lane: dict | None, manifest: dict | None, history: list[dict],
                 progress, route, locator, answers: dict | None = None) -> dict:
    """The report from its inputs (pure apart from euro_aip parsing)."""
    answers = answers or {}
    out: dict = {
        "v": REPORT_VERSION,
        "flight_id": recap.get("flight_id"),
        "status": recap.get("status"),
        "message": recap.get("message"),
        "time_base": recap.get("time_base"),
        "departed_at": recap.get("departed_at"),
        "arrival_at": recap.get("arrival_at"),
        "forecast_pack": (recap.get("forecast") or {}).get("pack_timestamp"),
    }
    if route is not None and route.waypoints:
        out["route"] = [w.icao for w in route.waypoints]
    planned_ft = (lane or {}).get("cruise_altitude_ft") or (manifest or {}).get("cruise_altitude_ft") \
        or (route.cruise_altitude_ft if route is not None else None)
    flown = flown_altitude(history, recap.get("route_nm"), planned_ft)
    out["flown_altitude"] = flown
    if recap.get("status") != "ok":
        out.update(summary=None, strip=[], airports=[], questions=[],
                   interest={"interesting": False, "reasons": []})
        return out

    strip = []
    for seg in recap.get("segments") or []:
        mid = (seg["from_nm"] + seg["to_nm"]) / 2
        fc = forecast_at(lane, mid)
        strip.append({
            "from_nm": seg["from_nm"], "to_nm": seg["to_nm"], "at": seg.get("at"),
            "forecast": fc["severity"], "forecast_hazards": fc["hazards"],
            "observed": observed_class(seg), "radar_max_dbz": seg.get("radar_max_dbz"),
            "lightning": seg.get("lightning"),
        })

    airports = []
    if route is not None and route.waypoints:
        dep_at = _utc(recap.get("departed_at"))
        arr_at = _utc(recap.get("arrival_at"))
        for role, icao, at in (("departure", route.waypoints[0].icao, dep_at),
                               ("arrival", route.waypoints[-1].icao, arr_at)):
            try:
                airports.append({"role": role, **airport_at(history, icao.upper(), at, dep_at)})
            except Exception:
                logger.warning("Airport %s failed", icao, exc_info=True)
                airports.append({"role": role, "icao": icao, "match": "unverifiable"})

    questions = build_questions(manifest, recap.get("segments") or [], flown, progress, locator)
    for q in questions:
        q["answer"] = answers.get(q["id"])
    alerts = recap.get("alerts") or []
    out.update(
        route_nm=recap.get("route_nm"),
        waypoints=[{"icao": w.get("icao") or w.get("ident"), "along_nm": w.get("along_nm"), "eta": w.get("eta")}
                   for w in recap.get("waypoints") or []],
        summary=build_summary(strip, airports, alerts, locator),
        strip=strip,
        airports=airports,
        questions=questions,
        interest=build_interest(lane, strip, airports, alerts, questions),
        alerts=[{k: a.get(k) for k in ("at", "event", "tier", "message", "aircraft_nm")}
                for a in alerts if a.get("tier") == "alert"],
        # Answers to questions no longer asked (rules changed): kept visible.
        orphan_answers={k: v for k, v in answers.items() if k not in {q["id"] for q in questions}},
    )
    return out


def report_for_flight(db, flight_id: str, *, now: datetime | None = None) -> dict:
    """Everything the report needs, read from the DB and the flight dir."""
    from weatherbrief.analysis.route_locator import for_route
    from weatherbrief.tasks.artifacts import load_briefing
    from weatherbrief.tasks.live_layer import load_live_history
    from weatherbrief.tasks.live_recap import (
        default_forecast_pack,
        flight_dir_of,
        flight_packs,
        flight_progress_for,
        load_forecast_lane,
        recap_for_flight,
    )
    from weatherbrief.tasks.live_trail import route_context

    now = now or datetime.now(timezone.utc)
    packs = flight_packs(db, flight_id)
    flight_dir = flight_dir_of(packs)
    progress = flight_progress_for(packs, now)
    recap = recap_for_flight(db, flight_id, now=now, packs=packs, progress=progress)
    chosen = default_forecast_pack(packs, progress.departed_at if progress is not None else None)
    pack_dir = chosen[1] if chosen else None
    lane = load_forecast_lane(pack_dir)
    manifest = load_manifest(pack_dir)
    route = None
    for _, d in ([chosen] if chosen else []) + packs:
        if d is not None and d.is_dir():
            route, _dep = route_context(load_briefing(d) or {})
            if route is not None:
                break
    history = load_live_history(flight_dir) if flight_dir is not None else []
    locator = None
    if route is not None:
        try:
            locator = for_route(route)
        except Exception:
            logger.warning("Locator failed for %s", flight_id, exc_info=True)
    report = build_report(recap, lane=lane, manifest=manifest, history=history, progress=progress,
                          route=route, locator=locator, answers=load_answers(flight_dir))
    report["flight_id"] = flight_id
    return report


def flight_dir_for(db, flight_id: str) -> Path | None:
    from weatherbrief.tasks.live_recap import flight_dir_of, flight_packs

    return flight_dir_of(flight_packs(db, flight_id))


__all__ = [
    "REPORT_VERSION", "build_report", "report_for_flight", "save_answer", "load_answers",
    "hazard_stretches", "forecast_at", "observed_class", "flown_altitude", "airport_at",
    "build_questions", "build_summary", "build_interest", "flight_dir_for",
]

