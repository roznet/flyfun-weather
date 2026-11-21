"""Post-flight debrief report (#1023, umbrella #710).

One report per flight, built on the server and drawn the same way on iOS,
Android and web (#1024). The rules were settled on the phase 0 prototype
(branch ``claude/project-thread-9dvcd6``, reviewed on real flights 17-18 Nov
2026; plan https://claude.ai/artifact/MXa2nY3mXsz9LH2tgLCbRR).

The report is built from what already exists:

- the observed recap (``tasks/live_recap.recap_for_flight``): per segment what
  radar saw when the aircraft was there, the alerts, the time base;
- the forecast the pilot flew with: the recap's default pack
  ``route_advisories.json`` (per-model ribbons for the strip, per-model
  highlight *regions* with base/top for the questions);
- the live history: METAR/TAF records for the airports at actual times, and
  the app's position fixes for the flown altitude;
- the route locator, for "between Lydd and Abbeville" wording.

It answers four things:

1. ``summary``: the headline (judges only what the instruments saw) and the
   collapsed detail lines (forecast and observed side by side, no verdict);
2. ``strip``: forecast / observed lanes per recap segment;
3. ``airports``: departure and arrival METAR at the actual times vs the TAF
   the pilot had;
4. ``questions``: one to three one-tap questions, only on what the
   instruments cannot see (icing, cloud at your level, turbulence, showers
   where radar had no coverage).

``interest`` says whether the flight earns a push (``tasks/debrief_close``).
Answers live in ``flight_debrief_answers`` (``storage/debriefs``), each with
the question as asked and the forecast it was asked about; the flown
outcomes of ``flight_debriefs`` are derived from them
(:func:`outcomes_from_answers`).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

#: The rules version, stored with every answer (``rules_version``). Bump it
#: when a change to the rules would make an old answer read differently.
REPORT_VERSION = "1"
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
#: How far around the departure / arrival to look for a worse METAR (the
#: fog that lifted just before you left, the cell that hit after you landed).
AROUND_BEFORE = timedelta(hours=3)
AROUND_AFTER = timedelta(hours=1)
#: Gusts at or above this are kept when a METAR is read back.
GUST_KT = 25

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
        per_model: dict[str, dict] = {}
        for r in rs:
            pm = per_model.setdefault(r["model"], {"severity": "amber", "base_ft": None, "top_ft": None})
            if r["severity"] == "red":
                pm["severity"] = "red"
            if r.get("base_ft") is not None:
                pm["base_ft"] = r["base_ft"] if pm["base_ft"] is None else min(pm["base_ft"], r["base_ft"])
            if r.get("top_ft") is not None:
                pm["top_ft"] = r["top_ft"] if pm["top_ft"] is None else max(pm["top_ft"], r["top_ft"])
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
            "per_model": dict(sorted(per_model.items())),
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


def _metar_conditions(raw: str | None) -> str | None:
    """The weather part of a METAR, for reading back: visibility, weather,
    cloud ("2700 BR OVC002", "CAVOK"); wind, temperatures, pressure and the
    trend left out."""
    if not raw:
        return None
    toks = raw.split()
    out = []
    gust = None
    started = False
    for tok in toks:
        if tok in ("TEMPO", "BECMG", "NOSIG", "RMK") or tok.startswith("RE"):
            break
        if re.fullmatch(r"\d{6}Z", tok):
            started = True
            continue
        if not started or tok in ("AUTO", "COR", "METAR", "SPECI"):
            continue
        wind = re.fullmatch(r"(\d{3}|VRB)\d{2,3}(?:G(\d{2,3}))?(KT|MPS)", tok)
        if wind:
            if wind.group(2) and int(wind.group(2)) >= GUST_KT:
                gust = f"gusts {int(wind.group(2))} kt"
            continue
        if re.fullmatch(r"\d{3}V\d{3}", tok):
            continue
        if re.fullmatch(r"M?\d{2}/M?\d{2}", tok) or re.fullmatch(r"[QA]\d{4}", tok):
            continue
        tok = tok.replace("///", "").strip("/")
        if tok:
            out.append(tok)
    if gust:
        out.append(gust)
    return " ".join(out) or None


def _convective(raw: str | None) -> bool:
    """CB, TCU, thunder, hail or heavy showers in a METAR's present weather
    or cloud (the trend left out)."""
    body = re.split(r"\b(?:TEMPO|BECMG|NOSIG|RMK)\b", raw or "")[0]
    return bool(re.search(r"CB\b|TCU\b|\bTS|\+(?:SH|TS)?RA|\bGR\b|\bGS\b|\+SH", body))


def _severity(cat: str | None, raw: str | None) -> tuple[int, int]:
    return (CATEGORY_RANK.get(cat, -1), int(_convective(raw)))


def _ft_plain(v: float | None) -> str:
    return f"{int(round(v, -2)):,} ft" if v is not None else "?"


def _taf_conditions(reading) -> str | None:
    """Visibility, ceiling and weather of the TAF group behind the reading."""
    bits = []
    if reading.visibility_m is not None and reading.visibility_m < 9999:
        bits.append(f"{reading.visibility_m:,} m")
    if reading.ceiling_ft is not None:
        bits.append(f"ceiling {_ft_plain(reading.ceiling_ft)}")
    bits += list(reading.significant_weather or [])
    return " ".join(bits) or None


def _becoming_at(taf, at: datetime) -> dict | None:
    """The BECMG group whose transition window contains ``at``: the TAF
    reads the old conditions until it ends, which alone says "LIFR" for a
    fog forecast to lift over the next hours."""
    for g in getattr(taf, "trends", None) or []:
        if getattr(g, "trend_type", None) != "BECMG" or g.flight_category is None:
            continue
        lo, hi = g.validity_start, g.validity_end
        if lo is not None and hi is not None and lo <= at < hi:
            return {"category": g.flight_category.value, "from": lo.isoformat(), "to": hi.isoformat()}
    return None


def _metar_category(r: dict, t: datetime) -> str | None:
    from euro_aip.briefing.weather.parser import WeatherParser

    cat = r.get("flight_category")
    if cat is None:
        rep = WeatherParser.parse_metar(r["raw"], source="live_history", reference=t)
        cat = rep.flight_category.value if rep is not None and rep.flight_category is not None else None
    return cat


def _metars(history: list[dict], station: str) -> list[tuple[datetime, dict]]:
    out, seen = [], set()
    for r in history:
        if r.get("type") != "report" or r.get("kind") != "metar" or (r.get("icao") or "").upper() != station:
            continue
        t = _utc(r.get("observed_at"))
        if t is None or not r.get("raw") or r["raw"] in seen:
            continue
        seen.add(r["raw"])
        out.append((t, r))
    return sorted(out, key=lambda x: x[0])


def airport_at(history: list[dict], icao: str, at: datetime | None, taf_before: datetime | None, *,
               metar_station: str | None = None, taf_station: str | None = None,
               around: str = "before") -> dict:
    """What the airport did at ``at`` and what the TAF the pilot had (latest
    issued before ``taf_before``) said for then, both written out.

    ``metar_station`` / ``taf_station``: the proxy the live layer read for a
    field without its own reports (EGTF -> EGLL). ``around`` adds the worst
    METAR in the hours before departure (``"before"``) or the hour after
    landing (``"after"``) when it was worse than the one at ``at``.
    ``match`` stays as data: ``within`` (inside what the TAF allowed),
    ``worse`` (worse than all of it), ``better`` (better than all of it)."""
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.analysis.taf_reading import read_taf_at

    m_st = (metar_station or icao).upper()
    t_st = (taf_station or icao).upper()
    out: dict = {"icao": icao, "at": at.isoformat() if at else None,
                 "metar_station": m_st if m_st != icao else None,
                 "taf_station": t_st if t_st != icao else None}
    if at is None:
        out["match"] = "untimed"
        return out
    metars = _metars(history, m_st)
    best = None
    for t, r in metars:
        d = abs(t - at)
        if d <= METAR_WINDOW and (best is None or d < best[0]):
            best = (d, r, t)
    if best is not None:
        _, r, t = best
        out["metar_raw"] = r["raw"]
        out["metar_time"] = t.isoformat()
        out["metar_category"] = _metar_category(r, t)
        out["metar_conditions"] = _metar_conditions(r["raw"])
    lo, hi = (at - AROUND_BEFORE, at) if around == "before" else (at, at + AROUND_AFTER)
    worst = None
    for t, r in metars:
        if not (lo <= t <= hi):
            continue
        c = _metar_category(r, t)
        if c in CATEGORY_RANK and (worst is None or _severity(c, r["raw"]) > _severity(worst[0], worst[2]["raw"])):
            worst = (c, t, r)
    if worst is not None and out.get("metar_category") in CATEGORY_RANK \
            and _severity(worst[0], worst[2]["raw"]) > _severity(out["metar_category"], out.get("metar_raw")):
        out["worst_around"] = {"when": around, "category": worst[0], "time": worst[1].isoformat(),
                               "conditions": _metar_conditions(worst[2]["raw"]), "raw": worst[2]["raw"]}
    tafs = [r for r in history if r.get("type") == "report" and r.get("kind") == "taf"
            and (r.get("icao") or "").upper() == t_st and r.get("raw")]
    cutoff = taf_before or at
    had = [r for r in tafs if (_utc(r.get("issued_at")) or _utc(r.get("tick_at")) or cutoff) <= cutoff]
    # Newest first, the first one valid at ``at``: a TAF issued just before
    # departure can start after it (EGLL 10:55Z valid from 12Z).
    reading = None
    for taf_rec in sorted(had or tafs, key=lambda r: r.get("issued_at") or "", reverse=True):
        taf = WeatherParser.parse_taf(taf_rec["raw"], source="live_history",
                                      reference=_utc(taf_rec.get("issued_at")) or at)
        reading = read_taf_at(taf, at) if taf is not None else None
        if reading is not None:
            out["taf_issued_at"] = taf_rec.get("issued_at")
            becmg = _becoming_at(taf, at)
            if becmg is not None and becmg["category"] != reading.prevailing_category:
                out["taf_becoming"] = becmg
            break
    if reading is not None:
        out["taf_category"] = reading.prevailing_category
        out["taf_temporary_category"] = reading.temporary_category
        out["taf_temporary_type"] = reading.temporary_type
        out["taf_conditions"] = _taf_conditions(reading)
    m = out.get("metar_category")
    cats = [c for c in (out.get("taf_category"), out.get("taf_temporary_category")) if c in CATEGORY_RANK]
    if m not in CATEGORY_RANK or not cats:
        out["match"] = "unverifiable"
    elif CATEGORY_RANK[m] > max(CATEGORY_RANK[c] for c in cats):
        out["match"] = "worse"
    elif CATEGORY_RANK[m] < min(CATEGORY_RANK[c] for c in cats):
        out["match"] = "better"
    else:
        out["match"] = "within"
    return out


def airport_line(a: dict, role_label: str) -> str:
    """"Arrival LFAT 10:00Z: IFR, 3000 RA BR OVC008 TCU. TAF: VFR, PROB40
    TEMPO LIFR (1,200 m ceiling 900 ft +SHRA)." No verdict word: the two
    side by side, with a flag only when it was worse than anything allowed."""
    via = f" (via {a['metar_station']})" if a.get("metar_station") else ""
    head = f"{role_label} {a['icao']}{via}"
    if a.get("match") == "untimed":
        return f"{head}: no time to read it at."
    if a.get("metar_category"):
        obs = f"{_hhmmz(_utc(a['metar_time']))} {a['metar_category']}" \
            + (f", {a['metar_conditions']}" if a.get("metar_conditions") else "")
    else:
        obs = "no METAR near the time"
    parts = [f"{head}: {obs}."]
    w = a.get("worst_around")
    if w:
        when = "Before departure" if w["when"] == "before" else "After landing"
        parts.append(f"{when}: {w['category']} at {_hhmmz(_utc(w['time']))}"
                     + (f", {w['conditions']}" if w.get("conditions") else "") + ".")
    if a.get("taf_category"):
        taf = a["taf_category"]
        tmp = a.get("taf_temporary_category")
        if tmp:
            taf += f", {a.get('taf_temporary_type') or 'TEMPO'} {tmp}"
        if a.get("taf_conditions"):
            taf += f" ({a['taf_conditions']})"
        b = a.get("taf_becoming")
        if b:
            taf += (f", becoming {b['category']} between {_utc(b['from']).strftime('%H')}"
                    f" and {_utc(b['to']).strftime('%H')}Z")
        src = f" ({a['taf_station']})" if a.get("taf_station") else ""
        parts.append(f"TAF{src}: {taf}.")
    else:
        parts.append("No TAF for the time.")
    if a.get("match") == "worse":
        parts.append("Worse than the TAF allowed.")
    return " ".join(parts)


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


def _majority(s: dict) -> bool:
    """More than half of the models that ran flagged the stretch: one model
    alone is not enough to ask about it or to push for it."""
    total = s.get("models_total") or len(s["models"])
    return 2 * len(s["models"]) > total


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
            if s["at_level"] is False or not _majority(s):
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
                "forecast": s["per_model"],
                "at": when, "flown_ft": flown_ft, "flown_source": flown.get("source"),
            }
            key = (rank, -SEV_RANK[s["severity"]], -len(s["models"]), -(s["to_nm"] - s["from_nm"]))
            candidates.append((key, q))

    # Showers only where radar could not see a forecast convective stretch.
    for s in hazard_stretches(manifest, CONVECTIVE_ADVISORIES, None):
        if not _majority(s):
            continue
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
            "forecast": s["per_model"],
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


def forecast_at_level(manifest: dict | None, flown_ft: float | None) -> list[dict]:
    """The hazards most models had at the flown level (showers at any level),
    as ``{"kind", "stretch"}``, in route order."""
    out = []
    for kind, advisories, level in (("icing", ICING_ADVISORIES, flown_ft), ("cloud", CLOUD_ADVISORIES, flown_ft),
                                    ("turbulence", TURBULENCE_ADVISORIES, flown_ft),
                                    ("showers", CONVECTIVE_ADVISORIES, None)):
        for s in hazard_stretches(manifest, advisories, level):
            if s["at_level"] is False or not _majority(s):
                continue
            out.append({"kind": kind, "stretch": s})
    return sorted(out, key=lambda h: h["stretch"]["from_nm"])


def _forecast_text(hazards: list[dict], flown: dict, locator) -> str:
    level = _ft(flown.get("ft")) + (" planned" if flown.get("source") == "planned" else "")
    if not hazards:
        return f"Forecast at your level ({level}): nothing most models agreed on."
    bits = []
    for h in hazards:
        s = h["stretch"]
        band = (f" {_ft(s['base_ft'])}–{_ft(s['top_ft'])}"
                if h["kind"] in ("icing", "turbulence") and s["base_ft"] is not None and s["top_ft"] is not None
                else "")
        what = {"icing": "icing", "cloud": "in cloud", "turbulence": "turbulence",
                "showers": "showers or CB"}[h["kind"]]
        bits.append(f"{what}{band} {_span(locator, s)} ({_models_phrase(s)})")
    return f"Forecast at your level ({level}): " + "; ".join(bits) + "."


def _runs(strip: list[dict], cls: str) -> list[list[dict]]:
    runs, cur = [], []
    for s in strip:
        if s["observed"] == cls:
            cur.append(s)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def _cells_text(storms: list[dict], locator) -> str | None:
    """The strongest cell near the route (the recap's storms, the red and
    orange blocks of the ribbon): "a 54 dBZ cell 23 NM left of track near WOD"."""
    strong = [c for c in storms or [] if (c.get("peak_dbz") or 0) >= HEAVY_DBZ]
    if not strong:
        return None
    c = max(strong, key=lambda c: c["peak_dbz"])
    where = ""
    if locator is not None and c.get("along_nm") is not None:
        try:
            where = " " + locator.span(c["along_nm"], c["along_nm"])
        except Exception:
            where = ""
    off = f" {c['offtrack_nm']:.0f} NM {c.get('side') or 'off'} of track" if c.get("offtrack_nm") else " on track"
    flashes = ", lightning" if c.get("flashes") else ""
    n = len({x.get("id") for x in strong})
    more = f" ({n} cells of {HEAVY_DBZ:.0f}+ dBZ in all)" if n > 1 else ""
    return f"strongest cell {c['peak_dbz']:.0f} dBZ{off}{where}{flashes}{more}"


def _radar_text(strip: list[dict], locator, storms: list[dict] | None = None) -> str | None:
    """What radar saw along the route, in words."""
    if not strip:
        return None
    seen = [s for s in strip if s["observed"] in ("clear", "rain", "heavy")]
    if not seen:
        return "Radar: no coverage along the route."
    bits = []
    for cls, word in (("heavy", "heavy rain"), ("rain", "rain")):
        runs = _runs(strip, cls)
        if not runs:
            continue
        if len(runs) > 2:
            span = _span(locator, {"from_nm": runs[0][0]["from_nm"], "to_nm": runs[-1][-1]["to_nm"]})
            where = f"in {len(runs)} patches {span}"
        else:
            where = " and ".join(_span(locator, {"from_nm": r[0]["from_nm"], "to_nm": r[-1]["to_nm"]}) for r in runs)
        segs = [s for r in runs for s in r]
        dbz = max((s["radar_max_dbz"] for s in segs if s.get("radar_max_dbz") is not None), default=None)
        extra = [f"max {dbz:.0f} dBZ"] if dbz is not None else []
        if any(s.get("lightning") for s in segs):
            extra.append("lightning")
        bits.append(f"{word} {where}" + (f" ({', '.join(extra)})" if extra else ""))
    gaps = _runs([{**s, "observed": "gap" if s["observed"] == "no_data" else s["observed"]} for s in strip], "gap")
    text = "Radar: " + ("; ".join(bits) if bits else ("dry all the way" if not gaps else "dry where it could see"))
    if gaps:
        text += "; no coverage " + " and ".join(
            _span(locator, {"from_nm": g[0]["from_nm"], "to_nm": g[-1]["to_nm"]}) for g in gaps)
    cells = _cells_text(storms or [], locator)
    if cells:
        text += f"; {cells}"
    return text + "."


def flight_alerts(alerts: list[dict], departed_at, arrival_at) -> list[dict]:
    """Alerts raised while the aircraft was flying (departure to landing):
    the ones before departure belong to the briefing, the ones after landing
    to the next flight."""
    dep, arr = _utc(departed_at), _utc(arrival_at)
    out = []
    for a in alerts:
        t = _utc(a.get("at"))
        if a.get("tier") != "alert" or a.get("event") != "raised" or t is None:
            continue
        if dep is not None and t < dep or arr is not None and t > arr:
            continue
        out.append(a)
    return out


def _route_phrase(strip: list[dict], hazards: list[dict]) -> tuple[str | None, bool]:
    """What radar saw en route against the forecast, and whether that broke
    "as forecast". Only radar-visible weather is judged: heavy showers the
    forecast did not have break it, forecast showers that did not show say
    so, light rain does not. Icing, cloud and turbulence are never judged
    here: they are the questions."""
    seen = [s for s in strip if s["observed"] in ("clear", "rain", "heavy")]
    if not seen:
        return None, False
    forecast_showers = any(h["kind"] == "showers" for h in hazards)
    heavy = any(s["observed"] == "heavy" for s in seen)
    rain = any(s["observed"] == "rain" for s in seen)
    if heavy:
        return ("Heavy showers en route, as forecast" if forecast_showers
                else "Heavy showers en route that the forecast did not have"), not forecast_showers
    if rain:
        return ("Some showers en route, as forecast" if forecast_showers
                else "Mostly clear with a few light showers"), False
    if forecast_showers:
        return "Clear: the forecast showers did not show", True
    return "Clear, as forecast", False


def _airport_phrase(a: dict) -> str | None:
    """An airport earns the headline only when it was below VFR at your
    time, worse than the TAF allowed, or had something notable just before
    departure or just after landing."""
    role = a["role"]
    icao = a["icao"]
    cat = a.get("metar_category")
    out = []
    if cat in CATEGORY_RANK and cat != "VFR":
        verdict = (", worse than the TAF" if a.get("match") == "worse"
                   else ", as the TAF had it" if a.get("match") in ("within", "better") else "")
        out.append(f"{cat} {role} at {icao}{verdict}")
    w = a.get("worst_around")
    if w:
        cond = w.get("conditions") or ""
        if w["when"] == "before" and CATEGORY_RANK.get(w["category"], 0) >= CATEGORY_RANK["IFR"]:
            what = "fog" if re.search(r"\b(FG|BR)\b", cond) else "low cloud"
            out.append(f"the {what} at {icao} lifted before you left" if cat == "VFR"
                       else f"{icao} was {w['category']} before you left")
        elif w["when"] == "after":
            out.append(f"a storm cell reached {icao} after you landed" if _convective(w.get("raw"))
                       else f"{icao} went {w['category']} after you landed")
    return "; ".join(out) or None


def build_headline(strip: list[dict], airports: list[dict], hazards: list[dict], *,
                   cancelled: bool = False) -> dict:
    """One line for the top of the report: "Clear, as forecast. The fog at
    EGTF lifted before you left." The details hold the rest."""
    route, differs = _route_phrase(strip, hazards)
    sentences = []
    if cancelled:
        sentences.append("Cancelled")
    sentences.append(route or "No radar along the route")
    dep = next((a for a in airports if a["role"] == "departure"), None)
    arr = next((a for a in airports if a["role"] == "arrival"), None)
    both = (dep is not None and arr is not None and dep.get("metar_category") == arr.get("metar_category")
            and dep.get("metar_category") in ("MVFR", "IFR", "LIFR") and dep.get("match") == arr.get("match")
            and not dep.get("worst_around") and not arr.get("worst_around"))
    if both:
        verdict = (", worse than the TAFs" if dep.get("match") == "worse"
                   else ", as the TAFs had it" if dep.get("match") in ("within", "better") else "")
        sentences.append(f"{dep['metar_category']} at both ends{verdict}")
        differs = differs or dep.get("match") == "worse"
    else:
        for a in airports:
            p = _airport_phrase(a)
            if p:
                sentences.append(p[0].upper() + p[1:])
                differs = differs or a.get("match") == "worse"
    return {"text": ". ".join(sentences) + ".", "differs_from_forecast": differs}


def build_summary(strip: list[dict], airports: list[dict], alerts: list[dict], locator, *,
                  hazards: list[dict] | None = None, flown: dict | None = None,
                  cancelled: bool = False, storms: list[dict] | None = None) -> dict:
    """The report in a few lines, each saying what was forecast and what was
    observed side by side. No "as forecast" verdict: the pilot reads both.
    ``alerts`` are the in-flight ones (:func:`flight_alerts`)."""
    lines = []
    if cancelled:
        lines.append({"kind": "cancelled", "text": "You cancelled this flight. The weather at the planned times:"})
    names = {"departure": "Departure", "arrival": "Arrival"}
    dep = [a for a in airports if a["role"] == "departure"]
    arr = [a for a in airports if a["role"] == "arrival"]
    for a in dep:
        lines.append({"kind": "departure", "text": airport_line(a, names["departure"])})
    lines.append({"kind": "forecast", "text": _forecast_text(hazards or [], flown or {}, locator)})
    radar = _radar_text(strip, locator, storms)
    if radar:
        lines.append({"kind": "radar", "text": radar})
    if alerts and not cancelled:
        msgs = "; ".join(a.get("message") or "" for a in alerts[:2])
        more = f" and {len(alerts) - 2} more" if len(alerts) > 2 else ""
        lines.append({"kind": "alerts",
                      "text": f"{len(alerts)} alert{'s' if len(alerts) > 1 else ''} while you flew: {msgs}{more}."})
    for a in arr:
        lines.append({"kind": "arrival", "text": airport_line(a, names["arrival"])})
    headline = build_headline(strip, airports, hazards or [], cancelled=cancelled)
    return {"headline": headline["text"], "differs_from_forecast": headline["differs_from_forecast"],
            "text": "\n".join(l["text"] for l in lines), "lines": lines}


def build_interest(hazards, strip, airports, alerts, questions, *, cancelled: bool = False) -> dict:
    """Would this flight earn a push? Any reason below makes it interesting.
    ``hazards`` are :func:`forecast_at_level` (most models, at your level),
    ``alerts`` the in-flight ones. A cancelled flight never does."""
    if cancelled:
        return {"interesting": False, "reasons": [], "cancelled": True}
    reasons = []
    if hazards:
        reasons.append({"kind": "forecast_at_level", "hazards": sorted({h["kind"] for h in hazards})})
    if alerts:
        reasons.append({"kind": "alert_in_flight", "count": len(alerts)})
    if any(s["observed"] == "heavy" for s in strip):
        reasons.append({"kind": "heavy_precip_observed"})
    if any(a.get("match") == "worse" for a in airports):
        reasons.append({"kind": "airport_worse_than_taf"})
    if questions:
        reasons.append({"kind": "questions", "count": len(questions)})
    return {"interesting": bool(reasons), "reasons": reasons}


# --- Answers ---------------------------------------------------------------------

#: How an answer maps onto the old outcome categories, so the per-category
#: stats keep working (plan, decided 18 Nov). ``None`` = not checked: no
#: outcome for that category. An answer with ``avoided`` is never checked.
#: Showers have no old category to map onto (TS was a cancel reason and an
#: outcome, but the shower question asks about rain the radar missed, not
#: thunderstorms): kept only in the answers table.
OUTCOME_TAG = {"icing": "ICE", "cloud": "IMC", "turbulence": "TURB"}
OUTCOME_OF = {
    "icing": {"none": "better", "light": "consistent", "moderate_plus": "worse", "not_in_cloud": None},
    "cloud": {"in_cloud": "consistent", "in_and_out": "consistent", "on_top": "better",
              "below": "better", "clear": "better"},
    "turbulence": {"none": "better", "light": "consistent", "moderate_plus": "worse"},
}
_OUTCOME_RANK = {"better": 0, "consistent": 1, "worse": 2}


def outcomes_from_answers(answers) -> dict[str, str]:
    """The old ``outcomes`` dict (tag -> consistent / better / worse) derived
    from answers (each with ``kind``, ``answer``, ``avoided``). Two answers on
    one category keep the worse."""
    out: dict[str, str] = {}
    for a in answers:
        kind = a.get("kind")
        tag = OUTCOME_TAG.get(kind)
        if tag is None or a.get("avoided"):
            continue
        value = OUTCOME_OF[kind].get(a.get("answer"))
        if value is None:
            continue
        if tag not in out or _OUTCOME_RANK[value] > _OUTCOME_RANK[out[tag]]:
            out[tag] = value
    return out


def validate_answer(question: dict, answer: str, avoided: bool) -> None:
    """Raise ``ValueError`` for an answer the question does not offer, or an
    ``avoided`` on a question that cannot be avoided (cloud)."""
    if answer not in {o["id"] for o in question["options"]}:
        raise ValueError(f"Unknown answer {answer!r} for {question['id']}")
    if avoided and not question.get("avoidable"):
        raise ValueError(f"Question {question['id']} cannot be avoided")


def question_as_asked(question: dict) -> dict:
    """What an answer row keeps of its question (``question_json``)."""
    return {k: question.get(k) for k in ("kind", "text", "stretch", "at", "flown_ft", "flown_source")}


def push_text(report: dict) -> str | None:
    """The push body (#1023), or None when the flight earns no push: only an
    interesting flight with at least one question or a headline that differs
    from the forecast. "Two quick questions about the icing forecast."."""
    if report.get("cancelled") or not (report.get("interest") or {}).get("interesting"):
        return None
    questions = report.get("questions") or []
    summary = report.get("summary") or {}
    if questions:
        n = len(questions)
        count = {1: "One quick question", 2: "Two quick questions", 3: "Three quick questions"}[min(n, 3)]
        kinds = []
        for q in questions:
            if q["kind"] not in kinds:
                kinds.append(q["kind"])
        what = " and ".join({"icing": "icing", "cloud": "cloud", "turbulence": "turbulence",
                             "showers": "showers"}[k] for k in kinds[:2])
        return f"{count} about the {what} forecast."
    if summary.get("differs_from_forecast") and summary.get("headline"):
        return summary["headline"]
    return None


# --- Assembly ---------------------------------------------------------------------


def build_report(recap: dict, *, lane: dict | None, manifest: dict | None, history: list[dict],
                 progress, route, locator, answers: dict | None = None,
                 proxies: dict[str, tuple[str | None, str | None]] | None = None) -> dict:
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
        "cancelled": bool(recap.get("cancelled")),
    }
    cancelled = out["cancelled"]
    proxies = proxies or {}
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
                m_st, t_st = proxies.get(icao.upper(), (None, None))
                # No TAF proxy recorded: the METAR proxy station's TAF.
                t_st = t_st or m_st
                airports.append({"role": role, **airport_at(
                    history, icao.upper(), at, dep_at, metar_station=m_st, taf_station=t_st,
                    around="before" if role == "departure" else "after")})
            except Exception:
                logger.warning("Airport %s failed", icao, exc_info=True)
                airports.append({"role": role, "icao": icao, "match": "unverifiable"})

    # A cancelled flight: nobody was there to answer.
    questions = [] if cancelled else build_questions(manifest, recap.get("segments") or [], flown, progress, locator)
    alerts = flight_alerts(recap.get("alerts") or [], recap.get("departed_at"), recap.get("arrival_at"))
    hazards = forecast_at_level(manifest, flown.get("ft"))
    out.update(
        route_nm=recap.get("route_nm"),
        waypoints=[{"icao": w.get("icao") or w.get("ident"), "along_nm": w.get("along_nm"), "eta": w.get("eta")}
                   for w in recap.get("waypoints") or []],
        summary=build_summary(strip, airports, alerts, locator, hazards=hazards, flown=flown, cancelled=cancelled,
                              storms=recap.get("storms")),
        strip=strip,
        airports=airports,
        questions=questions,
        interest=build_interest(hazards, strip, airports, alerts, questions, cancelled=cancelled),
        alerts=[{k: a.get(k) for k in ("at", "event", "tier", "message", "aircraft_nm")} for a in alerts],
    )
    return with_answers(out, answers)


def with_answers(report: dict, answers: dict) -> dict:
    """``report`` with each question's ``answer`` set from ``answers`` (by
    question id) and the answers to questions no longer asked (rules
    changed) kept visible under ``orphan_answers``. Lets the answers PUT
    re-shape the report it already built instead of building it again."""
    questions = report.get("questions") or []
    for q in questions:
        q["answer"] = answers.get(q["id"])
    asked = {q["id"] for q in questions}
    report["orphan_answers"] = {k: v for k, v in answers.items() if k not in asked}
    return report


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
    from weatherbrief.storage.debriefs import get_report_row, load_answers

    answers = load_answers(db, flight_id)
    report = build_report(recap, lane=lane, manifest=manifest, history=history, progress=progress,
                          route=route, locator=locator, answers=answers,
                          proxies=load_proxies(flight_dir))
    report["flight_id"] = flight_id
    # After window close the questions are the set stored then: the list
    # flags, the agents' summary and the answers PUT all count against it,
    # so a rebuild that would now ask a different set (newer live history,
    # rules change) never shows or accepts a question that was not asked.
    stored = get_report_row(db, flight_id)
    if stored is not None:
        asked = {q["id"] for q in json.loads(stored.questions_json or "[]")}
        report["questions"] = [q for q in report.get("questions") or [] if q["id"] in asked]
        report = with_answers(report, answers)
    return report


def load_proxies(flight_dir: Path | None) -> dict[str, tuple[str | None, str | None]]:
    """The METAR / TAF proxy station per field the live layer used (#767):
    EGTF reads EGLL. From ``live.json`` while it exists, else from the newest
    per-tick snapshot that recorded one (kept past T1; snapshots written
    before the proxy was recorded have none)."""
    if flight_dir is None:
        return {}
    try:
        from weatherbrief.analysis.obs_proxy import pins_from
        from weatherbrief.tasks.live_layer import load_live
        from weatherbrief.tasks.live_snapshots import load_snapshots

        layer = load_live(flight_dir)
        pins = pins_from(layer.route_observations if layer is not None else None)
        if pins:
            return pins
        for snap in reversed(load_snapshots(flight_dir)):
            out = {}
            for a in ((snap.get("observations") or {}).get("airports") or []):
                m, t = a.get("metar_proxy"), a.get("taf_proxy")
                if m or t:
                    out[(a.get("icao") or "").upper()] = ((m or {}).get("icao"), (t or {}).get("icao"))
            if out:
                return out
    except Exception:
        logger.warning("No proxies for %s", flight_dir, exc_info=True)
    return {}


__all__ = [
    "REPORT_VERSION", "build_report", "report_for_flight", "outcomes_from_answers", "validate_answer",
    "question_as_asked", "push_text", "with_answers",
    "hazard_stretches", "forecast_at", "observed_class", "flown_altitude", "airport_at",
    "build_questions", "build_summary", "build_interest",
    "airport_line", "build_headline", "flight_alerts", "forecast_at_level", "load_proxies",
]

