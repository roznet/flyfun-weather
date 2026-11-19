"""Score the #1003 collector's ECMWF columns against US pilot reports.

``icing_pirep_collector.py score`` prints this. Where ``report`` (the #1000
hindcast table) asks about the SLD signals, this asks whether the product's
icing methods agree with what pilots reported:

- **Icing methods**: the product's own Ogimet-NWP, Ogimet-DD, SFIP and IENG
  zones, run through ``analyze_sounding`` on each stored column, against three
  plain baselines (cloud cover, humidity, cloud liquid water). A method that
  cannot beat a baseline adds nothing.
- **Severity**: how often a method says moderate or worse, per reported class.
- **Temperature** at the report's altitude and **sky** cover.

Choices that change the numbers:

- **Where the icing was.** A report's flight level is often not where the icing
  happened (``/FL220 ... /IC LGT RIME 180-120 /RM DURD``). A stated layer up to
  ``MAX_LAYER_FT`` thick is used as the icing altitude; a climb or descent
  report without one has an unknown altitude. Headline tables keep only reports
  with a known altitude; a last table shows every report.
- **Flight levels.** US altitudes are MSL below 18,000 ft and pressure altitude
  above, so the upper ones are moved onto the model's geopotential height.
- **Sub-zero only.** Reports where the model is outside -20..0 °C are left out,
  so warm-air NEG reports do not make every signal look skilful.
- **Skill** is POD minus the false-alarm rate on NEG reports (the true skill
  statistic). Pilots choose what to report, so the rates describe reported
  events, not the atmosphere. The 95% range resamples whole valid hours,
  because reports in the same hour share one weather pattern.

Inputs are the stored columns, so they differ from a briefing: no Open-Meteo
merge, and no orography (freezing level and convective top are dropped).
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np

import sld_pirep_hindcast as hc

MODEL = "ecmwf"
#: A zone or baseline counts when it comes within this of the icing altitude.
ALT_TOL_FT = 1000.0
#: A stated icing layer thicker than this does not say where the icing was.
MAX_LAYER_FT = 4000
#: Below this, US altitudes are MSL; from here up they are flight levels.
FLIGHT_LEVEL_FROM_FT = 18000
#: Model minus reported beyond this is a bad report (/TA from another level).
GROSS_TEMP_ERROR_C = 15.0
BOOTSTRAP_DRAWS = 1000

ICING_CLASSES = tuple(t for t in hc.TRUTH_CLASSES if t != "neg")
LIGHT_CLASSES = ("rime_lgt", "clr_mxd_lgt")
MODERATE_CLASSES = ("rime_mod", "clr_mxd_mod")
METHODS = {
    "SFIP": "sfip_zones",
    "Ogimet-DD": "icing_ogimet_dd_zones",
    "IENG": "ieng_icing_zones",
    "Ogimet-NWP": "icing_ogimet_nwp_zones",
}
BASELINES = ("humidity >= 80%", "any cloud liquid", "cloud cover >= 50%")
RISK_RANK = {"none": 0, "light": 1, "moderate": 2, "severe": 3}
#: Below every zone: no zone at the altitude.
NO_ZONE = -1
_CLIMB_OR_DESCENT = re.compile(r"\bDUR[A-Z]*\b")


# ═══════════════════════════════════════════════════════════════════════
# Where the report is
# ═══════════════════════════════════════════════════════════════════════


def isa_pressure_hpa(alt_ft: float) -> float:
    return 1013.25 * (1.0 - 6.87535e-6 * alt_ft) ** 5.2559


def model_altitude_ft(levels: dict, alt_ft: float) -> float:
    """A reported altitude on the model's height scale (see the module note)."""
    if alt_ft < FLIGHT_LEVEL_FROM_FT:
        return alt_ft
    pts = sorted((math.log(int(p)), v["gh"] * hc.FT_PER_M)
                 for p, v in levels.items() if v.get("gh") is not None)
    if len(pts) < 2:
        return alt_ft
    return float(np.interp(math.log(isa_pressure_hpa(alt_ft)), [p[0] for p in pts], [p[1] for p in pts]))


def level_value(levels: dict, alt_ft: float, key: str) -> float | None:
    """A level field interpolated in height; None outside the stored levels."""
    pts = sorted((v["gh"] * hc.FT_PER_M, v[key]) for v in levels.values()
                 if v.get("gh") is not None and v.get(key) is not None)
    if len(pts) < 2 or not pts[0][0] <= alt_ft <= pts[-1][0]:
        return None
    return float(np.interp(alt_ft, [p[0] for p in pts], [p[1] for p in pts]))


def is_climb_or_descent(flt_lvl_type: str | None, report: str | None) -> bool:
    return flt_lvl_type in ("DURC", "DURD") or bool(_CLIMB_OR_DESCENT.search((report or "").upper()))


@dataclass(frozen=True)
class Target:
    """The altitude range to look at, on the model's height scale."""
    lo_ft: float
    hi_ft: float
    known_altitude: bool


def report_target(alt_ft: float, flt_lvl_type: str | None, report: str | None,
                  icing_json: str | None, levels: dict) -> Target:
    layers = json.loads(icing_json) if icing_json else []
    layer = layers[0] if layers else {}
    base, top = layer.get("base_ft"), layer.get("top_ft")
    if base is not None and top is not None:
        low, high = sorted((base, top))
        return Target(model_altitude_ft(levels, low), model_altitude_ft(levels, high),
                      high - low <= MAX_LAYER_FT)
    alt = model_altitude_ft(levels, alt_ft)
    return Target(alt, alt, not is_climb_or_descent(flt_lvl_type, report))


# ═══════════════════════════════════════════════════════════════════════
# What the model says there
# ═══════════════════════════════════════════════════════════════════════


def _pct(fraction: float | None) -> float | None:
    return None if fraction is None else max(0.0, min(100.0, fraction * 100.0))


def column_inputs(payload: dict, valid: datetime):
    """A stored column as the product's (pressure levels, hourly forecast)."""
    from weatherbrief.fetch.grib.decode import (
        _ECMWF_CLOUD_DIAG_FIELD_MAP,
        build_ecmwf_cloud_diagnostics,
    )
    from weatherbrief.models import HourlyForecast, PressureLevelData

    sfc = payload["sfc"]
    levels = []
    for p, v in payload["levels"].items():
        if "t" not in v:
            continue
        speed = direction = None
        if v.get("u") is not None and v.get("v") is not None:
            speed = math.hypot(v["u"], v["v"]) * 1.94384
            direction = math.degrees(math.atan2(-v["u"], -v["v"])) % 360.0
        levels.append(PressureLevelData(
            pressure_hpa=int(p),
            temperature_c=v["t"] - hc.T0_K,
            relative_humidity_pct=v.get("r"),
            wind_speed_kt=speed,
            wind_direction_deg=direction,
            geopotential_height_m=v.get("gh"),
            vertical_velocity_pa_s=v.get("w"),
            cloud_liquid_water_kg_kg=v.get("clwc"),
            ice_mixing_ratio_kg_kg=v.get("ciwc"),
            cloud_area_fraction_pct=_pct(v.get("cc")),
        ))
    raw = {target: sfc[key] for key, target in _ECMWF_CLOUD_DIAG_FIELD_MAP.items() if sfc.get(key) is not None}
    hourly = HourlyForecast(
        time=valid,
        temperature_2m_c=None if sfc.get("t2m") is None else sfc["t2m"] - hc.T0_K,
        dewpoint_2m_c=None if sfc.get("td2m") is None else sfc["td2m"] - hc.T0_K,
        surface_pressure_hpa=None if sfc.get("sp") is None else sfc["sp"] / 100.0,
        cloud_cover_low_pct=_pct(sfc.get("lcc")),
        cloud_cover_mid_pct=_pct(sfc.get("mcc")),
        cloud_cover_high_pct=_pct(sfc.get("hcc")),
        cape_jkg=sfc.get("mucape"),
        nwp_cloud_diagnostics=build_ecmwf_cloud_diagnostics(raw),
    )
    return levels, hourly


def column_zones(payload: dict, valid: datetime) -> dict[str, list[tuple[float, float, int]]] | None:
    """{method: [(base_ft, top_ft, risk rank)]}, None when the analysis fails."""
    from weatherbrief.analysis.sounding import analyze_sounding

    levels, hourly = column_inputs(payload, valid)
    try:
        sounding = analyze_sounding(levels, hourly, model_key=MODEL)
    except Exception:  # noqa: BLE001 — a study script counts failures and moves on
        return None
    if sounding is None:
        return None
    return {name: [(z.base_ft, z.top_ft, RISK_RANK[z.risk.value]) for z in getattr(sounding, attr)]
            for name, attr in METHODS.items()}


def zone_risk(zones: list[tuple[float, float, int]], target: Target) -> int:
    """Highest risk of the zones reaching the target range, else ``NO_ZONE``."""
    return max((rank for base, top, rank in zones
                if base - ALT_TOL_FT <= target.hi_ft and top + ALT_TOL_FT >= target.lo_ft),
               default=NO_ZONE)


def baseline_flags(levels: dict, target: Target) -> dict[str, bool]:
    """The plain signals, set when any height in the range meets them."""
    flags = dict.fromkeys(BASELINES, False)
    for alt in np.linspace(target.lo_ft - ALT_TOL_FT, target.hi_ft + ALT_TOL_FT, 5):
        rh = level_value(levels, alt, "r")
        clw = level_value(levels, alt, "clwc")
        cover = level_value(levels, alt, "cc")
        flags["humidity >= 80%"] |= rh is not None and rh >= 80.0
        flags["any cloud liquid"] |= clw is not None and clw >= 1e-6
        flags["cloud cover >= 50%"] |= cover is not None and cover >= 0.5
    return flags


# ═══════════════════════════════════════════════════════════════════════
# Going through the reports
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class Scored:
    """One icing or NEG report that reached the scoring."""
    truth: str
    hour: str
    lead_h: int
    known_altitude: bool
    nearest: dict[str, int]      # method -> risk rank, nearest column
    nearby: dict[str, int]       # method -> highest risk rank over the 3×3 columns
    base_nearest: dict[str, bool]
    base_nearby: dict[str, bool]

    @property
    def icing(self) -> bool:
        return self.truth != "neg"


@dataclass
class ScoreData:
    reports: list[Scored] = field(default_factory=list)
    #: Why an icing or NEG report was not scored. With ``reports``, this
    #: accounts for every candidate.
    skipped: Counter = field(default_factory=Counter)
    neighbour_failures: int = 0
    temps: list[tuple[float, float, int, int]] = field(default_factory=list)  # model, reported, alt, lead
    climb_descent_temps: int = 0
    sky: list[tuple[bool, float]] = field(default_factory=list)  # reported cloudy, model cover


def collect(conn: sqlite3.Connection, start: date | None = None, end: date | None = None) -> ScoreData:
    data = ScoreData()
    valids = [v for (v,) in conn.execute(
        "SELECT valid FROM fetches WHERE model=? AND status='ok' ORDER BY valid", (MODEL,))]
    for valid_s in valids:
        valid = hc._parse_iso(valid_s)
        if start and valid.date() < start or end and valid.date() > end:
            continue
        grid, cols = hc._load_columns(conn, MODEL, valid_s)
        if grid is not None:
            _collect_hour(conn, data, valid_s, valid, grid, cols)
    return data


def _collect_hour(conn, data: ScoreData, valid_s: str, valid: datetime, grid, cols: dict) -> None:
    zone_cache: dict = {}

    def zones_of(cell):
        if cell not in zone_cache:
            zone_cache[cell] = column_zones(cols[cell], valid)
        return zone_cache[cell]

    for lat, lon, alt_ft, truth, report, temp_c, flt_lvl_type, clouds_json, icing_json, lead_h in conn.execute(
            "SELECT p.lat, p.lon, p.alt_ft, p.truth, p.report, d.temp_c, d.flt_lvl_type, d.clouds_json,"
            " d.icing_json, s.lead_h FROM pireps p"
            " JOIN samples s ON s.pirep_id = p.id AND s.model = ? AND s.status = 'ok'"
            " JOIN pirep_details d ON d.pirep_id = p.id WHERE s.valid = ?", (MODEL, valid_s)).fetchall():
        candidate = truth in hc.TRUTH_CLASSES
        centre = grid.index(lat, lon)
        if flt_lvl_type == "GRND" or centre not in cols:
            if candidate:
                data.skipped["on the ground" if flt_lvl_type == "GRND" else "no model column"] += 1
            continue
        column = cols[centre]
        levels = column["levels"]
        _collect_temperature(data, levels, alt_ft, temp_c, flt_lvl_type, report, lead_h)
        _collect_sky(data, column["sfc"], clouds_json)
        if not candidate:
            continue
        target = report_target(alt_ft, flt_lvl_type, report, icing_json, levels)
        t_k = level_value(levels, (target.lo_ft + target.hi_ft) / 2.0, "t")
        if t_k is None:
            data.skipped["outside the stored levels"] += 1
            continue
        if not hc.SUBZERO_RANGE_C[0] <= t_k - hc.T0_K <= hc.SUBZERO_RANGE_C[1]:
            data.skipped["model not at -20..0 C"] += 1
            continue
        zones = zones_of(centre)
        if zones is None:
            data.skipped["sounding analysis failed"] += 1
            continue
        nearest = {m: zone_risk(zones[m], target) for m in METHODS}
        base_nearest = baseline_flags(levels, target)
        nearby, base_nearby = dict(nearest), dict(base_nearest)
        for cell in grid.neighbourhood(lat, lon):
            if cell == centre or cell not in cols:
                continue
            other = zones_of(cell)
            if other is None:
                data.neighbour_failures += 1
            else:
                for m in METHODS:
                    nearby[m] = max(nearby[m], zone_risk(other[m], target))
            for name, flag in baseline_flags(cols[cell]["levels"], target).items():
                base_nearby[name] |= flag
        data.reports.append(Scored(truth, valid_s, lead_h, target.known_altitude,
                                   nearest, nearby, base_nearest, base_nearby))


def _collect_temperature(data: ScoreData, levels: dict, alt_ft: int, temp_c: float | None,
                         flt_lvl_type: str | None, report: str | None, lead_h: int) -> None:
    if temp_c is None:
        return
    if is_climb_or_descent(flt_lvl_type, report):
        data.climb_descent_temps += 1
        return
    t_k = level_value(levels, model_altitude_ft(levels, alt_ft), "t")
    if t_k is not None:
        data.temps.append((t_k - hc.T0_K, temp_c, alt_ft, lead_h))


def _collect_sky(data: ScoreData, sfc: dict, clouds_json: str | None) -> None:
    covers = {c.get("cover") for c in json.loads(clouds_json)} if clouds_json else set()
    cover = sfc.get("tcc")
    if not covers or cover is None:
        return
    if covers & {"BKN", "OVC", "OVX"}:
        data.sky.append((True, cover))
    elif covers <= {"CLR", "SKC"}:
        data.sky.append((False, cover))


# ═══════════════════════════════════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Skill:
    pod: float | None           # share of icing reports flagged
    false_alarm: float | None   # share of NEG reports flagged
    low: float | None = None    # 95% range of pod - false_alarm
    high: float | None = None

    @property
    def value(self) -> float | None:
        return None if self.pod is None or self.false_alarm is None else self.pod - self.false_alarm


def skill(flags: list[bool], icing: list[bool], hours: list[str], seed: int = 1003) -> Skill:
    """POD, NEG false-alarm rate and a 95% range from resampling whole hours."""
    flag = np.asarray(flags, dtype=bool)
    pos = np.asarray(icing, dtype=bool)
    pod = float(flag[pos].mean()) if pos.any() else None
    false_alarm = float(flag[~pos].mean()) if (~pos).any() else None
    if pod is None or false_alarm is None:
        return Skill(pod, false_alarm)
    per_hour: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for h, f, p in zip(hours, flag, pos):
        row = per_hour[h]
        row[0 if p else 2] += int(f)
        row[1 if p else 3] += 1
    table = np.array(list(per_hour.values()), dtype=float)
    picks = np.random.default_rng(seed).integers(0, len(table), (BOOTSTRAP_DRAWS, len(table)))
    totals = table[picks].sum(axis=1)
    usable = (totals[:, 1] > 0) & (totals[:, 3] > 0)
    values = totals[usable, 0] / totals[usable, 1] - totals[usable, 2] / totals[usable, 3]
    if not len(values):
        return Skill(pod, false_alarm)
    low, high = np.percentile(values, [2.5, 97.5])
    return Skill(pod, false_alarm, float(low), float(high))


# ═══════════════════════════════════════════════════════════════════════
# Report
# ═══════════════════════════════════════════════════════════════════════


def _share(v: float | None) -> str:
    return "-" if v is None else f"{100 * v:.0f}%"


def _signed(v: float | None) -> str:
    return "-" if v is None else f"{v:+.2f}"


def _skill_cells(s: Skill) -> str:
    spread = "" if s.low is None else f"{s.low:+.2f}..{s.high:+.2f}"
    return f"{_share(s.pod):>6}{_share(s.false_alarm):>7}{_signed(s.value):>7} {spread:<13}"


def _skill_table(title: str, reports: list[Scored]) -> list[str]:
    n_icing = sum(r.icing for r in reports)
    out = [f"\n{title}: {n_icing} icing, {len(reports) - n_icing} NEG"]
    if not reports:
        return out
    out.append(f"{'':<20}{'nearest column':<34}{'any column within ~40 km'}")
    out.append(f"{'signal':<20}" + f"{'POD':>6}{'false':>7}{'skill':>7} {'95% range':<13}" * 2)
    icing = [r.icing for r in reports]
    hours = [r.hour for r in reports]
    for name in METHODS:
        near = skill([r.nearest[name] >= RISK_RANK["light"] for r in reports], icing, hours)
        wide = skill([r.nearby[name] >= RISK_RANK["light"] for r in reports], icing, hours)
        out.append(f"{name:<20}{_skill_cells(near)}{_skill_cells(wide)}")
    for name in BASELINES:
        near = skill([r.base_nearest[name] for r in reports], icing, hours)
        wide = skill([r.base_nearby[name] for r in reports], icing, hours)
        out.append(f"{'= ' + name:<20}{_skill_cells(near)}{_skill_cells(wide)}")
    return out


def _severity_table(reports: list[Scored]) -> list[str]:
    groups = (("NEG", [r for r in reports if r.truth == "neg"]),
              ("light", [r for r in reports if r.truth in LIGHT_CLASSES]),
              ("moderate", [r for r in reports if r.truth in MODERATE_CLASSES]))
    out = ["\nShare of reports where the method says moderate or worse (nearest column):",
           f"{'method':<20}" + "".join(f"{f'{n} ({len(g)})':>16}" for n, g in groups)]
    for name in METHODS:
        cells = [None if not g else sum(r.nearest[name] >= RISK_RANK["moderate"] for r in g) / len(g)
                 for _, g in groups]
        out.append(f"{name:<20}" + "".join(f"{_share(c):>16}" for c in cells))
    return out


def _temperature_lines(data: ScoreData) -> list[str]:
    out = [f"\nTemperature, model minus reported (level flight; {data.climb_descent_temps} climb or descent"
           " reports left out):"]
    if not data.temps:
        return out + ["  no reports"]
    diff = np.array([m - r for m, r, _, _ in data.temps])
    alts = np.array([a for _, _, a, _ in data.temps])
    good = np.abs(diff) <= GROSS_TEMP_ERROR_C
    out.append(f"  {int((~good).sum())} of {len(diff)} differ by more than {GROSS_TEMP_ERROR_C:.0f} C and are left out:"
               " the reported /TA is often not from that level, so read the median, not the mean error")
    for label, mask in (("all", np.ones(len(diff), dtype=bool)),
                        ("below 10,000 ft", alts < 10000),
                        ("10,000-18,000 ft", (alts >= 10000) & (alts < 18000)),
                        ("18,000 ft and up", alts >= 18000)):
        d = diff[mask & good]
        if len(d):
            out.append(f"  {label:<18} n={len(d):<5} median {np.median(d):+.1f}  mean error {np.abs(d).mean():.1f}"
                       f"  within 2 C {100 * np.mean(np.abs(d) <= 2):.0f}%")
    return out


def _sky_lines(data: ScoreData) -> list[str]:
    cloudy = [c for reported, c in data.sky if reported]
    clear = [c for reported, c in data.sky if not reported]
    if not cloudy or not clear:
        return []
    return ["\nSky: model total cloud >= 50%",
            f"  on {100 * np.mean(np.array(cloudy) >= 0.5):.0f}% of {len(cloudy)} reports of broken or overcast",
            f"  on {100 * np.mean(np.array(clear) >= 0.5):.0f}% of {len(clear)} reports of clear sky"]


def format_score(data: ScoreData) -> str:
    known = [r for r in data.reports if r.known_altitude]
    days = sorted({r.hour[:10] for r in data.reports})
    hours = {r.hour for r in data.reports}
    out = [f"ECMWF vs US PIREPs: {len(data.reports)} icing or NEG reports scored over {len(days)} days"
           f" ({len(hours)} valid hours), {len(known)} with a known icing altitude."]
    if data.skipped:
        out.append("Not scored: " + ", ".join(f"{k} {v}" for k, v in data.skipped.most_common()))
    if data.neighbour_failures:
        out.append(f"Neighbouring columns whose analysis failed: {data.neighbour_failures}")
    out.extend(_skill_table("Known icing altitude, leads +1 to +12 h", known))
    out.extend(_skill_table("Known icing altitude, leads +1 to +6 h", [r for r in known if r.lead_h <= 6]))
    out.extend(_skill_table("Known icing altitude, leads +7 to +12 h", [r for r in known if r.lead_h > 6]))
    out.extend(_skill_table("Every report, unknown altitudes included", data.reports))
    out.extend(_severity_table(known))
    out.extend(_temperature_lines(data))
    out.extend(_sky_lines(data))
    out.append("\nPOD: share of icing reports flagged. false: share of NEG reports flagged. skill: POD - false"
               " (0 = no better than chance).\nA method flags a report when a zone of light or worse comes within"
               f" {ALT_TOL_FT:.0f} ft of it; '=' rows are plain baselines at the same tolerance.")
    return "\n".join(out)
