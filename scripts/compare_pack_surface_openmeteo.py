"""Check a day's GRIB-built packs against Open-Meteo for the same model run (#912, #913).

Since #912 the ECMWF and ICON slots of a briefing are built from GRIB when
the GRIB gate passes, and since #913 the GFS slot too; Open-Meteo is no
longer fetched for them. This script is the post-rollout check that our GRIB
decode (units, accumulation, domain edges) agrees with Open-Meteo's
processing of the SAME run:

1. find the packs fetched on ``--date`` whose ``fetch_meta.json`` says a slot
   was GRIB-primary (``fetch_facts.grib_primary``, written since #912);
2. for each such slot, sample a few route points and ask Open-Meteo's
   single-run endpoint for the surface variables of the run the pack used
   (``fetch_facts.grib_init_times``);
3. report per model and variable: bias, mean |Δ|, max |Δ| (with where), and
   for the accumulated fields the ratio of the totals, which is what an
   accumulation or unit slip shows up as (×3 on a 3-hourly step, ×1000 on
   metres vs mm).

For GFS and Météo-France (AROME, #940) the sounding is new from GRIB as
well (ECMWF's and ICON's already were before #912), so their slots also
compare temperature, RH, wind and height at ``--levels`` (default 850, 700,
500 hPa), reported as ``field@level``.

Billing: each request is one location-set × ~1.6 (16 variables / 10) per
point, ~2.8 for GFS with the default levels. ``--budget`` (default 300
units) stops before it is exceeded and ``--dry-run`` only prints the
estimate.

Points that fell back to Open-Meteo in the briefing compare against
Open-Meteo by construction (Δ≈0); the per-pack line says how many there
were.

Run where the packs are (prod or a pulled copy):
    python scripts/compare_pack_surface_openmeteo.py --date 2026-10-25 --dry-run
    python scripts/compare_pack_surface_openmeteo.py --date 2026-10-25 --points 3

The single-run endpoint and its ``models`` names are configurable because
they were not checked from the session that wrote this:
``--base-url`` (default the Open-Meteo Single Runs API) and
``--om-model ecmwf=ecmwf_ifs --om-model icon_d2:dwd=icon_d2
--om-model gfs:noaa=gfs_global --om-model arome:mf=meteofrance_arome_france``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# HourlyForecast field → Open-Meteo hourly variable (same units as the
# briefing client: wind in kn, everything else Open-Meteo's default).
SURFACE_VARS: dict[str, str] = {
    "temperature_2m_c": "temperature_2m",
    "dewpoint_2m_c": "dew_point_2m",
    "relative_humidity_2m_pct": "relative_humidity_2m",
    "pressure_msl_hpa": "pressure_msl",
    "surface_pressure_hpa": "surface_pressure",
    "wind_speed_10m_kt": "wind_speed_10m",
    "wind_gusts_10m_kt": "wind_gusts_10m",
    "precipitation_mm": "precipitation",
    "rain_mm": "rain",
    "showers_mm": "showers",
    "snowfall_cm": "snowfall",
    "cloud_cover_pct": "cloud_cover",
    "cloud_cover_low_pct": "cloud_cover_low",
    "visibility_m": "visibility",
    "freezing_level_m": "freezing_level_height",
    "cape_jkg": "cape",
}
ACCUMULATED = ("precipitation_mm", "rain_mm", "showers_mm", "snowfall_cm")

# PressureLevelData field → Open-Meteo per-level variable stem (#913).
LEVEL_VARS: dict[str, str] = {
    "temperature_c": "temperature",
    "relative_humidity_pct": "relative_humidity",
    "wind_speed_kt": "wind_speed",
    "geopotential_height_m": "geopotential_height",
}
# Sources whose sounding is compared too: the slots whose column came from
# Open-Meteo before GRIB-primary: GFS (#913), Météo-France (#940), UKMO (#937).
SOUNDING_SOURCES = ("gfs", "gfs:noaa", "arome:mf", "meteofrance", "ukmo", "ukmo:ukv")
DEFAULT_LEVELS = (850, 700, 500)

# |Δ| above which one sample is listed as suspect.
SUSPECT: dict[str, float] = {
    "temperature_2m_c": 3.0, "dewpoint_2m_c": 4.0, "relative_humidity_2m_pct": 25.0,
    "pressure_msl_hpa": 3.0, "surface_pressure_hpa": 5.0,
    "wind_speed_10m_kt": 10.0, "wind_gusts_10m_kt": 15.0,
    "precipitation_mm": 2.0, "rain_mm": 2.0, "showers_mm": 2.0, "snowfall_cm": 1.0,
    "cloud_cover_pct": 50.0, "cloud_cover_low_pct": 50.0,
    "visibility_m": 20000.0, "freezing_level_m": 400.0, "cape_jkg": 500.0,
    "temperature_c": 3.0, "relative_humidity_pct": 30.0, "wind_speed_kt": 15.0,
    "geopotential_height_m": 60.0,
}

DEFAULT_OM_MODELS: dict[str, str] = {
    "ecmwf": "ecmwf_ifs",
    "icon_eu:dwd": "icon_eu",
    "icon_d2:dwd": "icon_d2",
    "icon": "icon_eu",
    # GFS 0.25° alone, not the gfs_seamless blend briefings fetched (#913).
    "gfs:noaa": "ncep_gfs025",
    "gfs": "ncep_gfs025",
    # AROME France 0.025° alone, what meteofrance_seamless served inside
    # AROME's footprint (#940). The name was not checked from the session
    # that added it: override with --om-model arome:mf=<name> if refused.
    "arome:mf": "meteofrance_arome_france",
    "meteofrance": "meteofrance_arome_france",
}
DEFAULT_BASE_URL = "https://single-runs-api.open-meteo.com/v1/forecast"


@dataclass
class VarStats:
    n: int = 0
    sum_d: float = 0.0
    sum_abs: float = 0.0
    max_abs: float = 0.0
    max_where: str = ""
    total_pack: float = 0.0
    total_om: float = 0.0
    suspects: list[str] = field(default_factory=list)

    def add(self, pack_v: float, om_v: float, where: str, var: str) -> None:
        d = pack_v - om_v
        self.n += 1
        self.sum_d += d
        self.sum_abs += abs(d)
        self.total_pack += pack_v
        self.total_om += om_v
        if abs(d) > self.max_abs:
            self.max_abs, self.max_where = abs(d), where
        if abs(d) > SUSPECT.get(var.split("@")[0], math.inf) and len(self.suspects) < 5:
            self.suspects.append(f"{where}: pack {pack_v:.2f} vs OM {om_v:.2f}")


def sample_indices(n: int, k: int) -> list[int]:
    """``k`` evenly spaced indices over ``n`` points, ends included."""
    if n <= 0:
        return []
    if k >= n:
        return list(range(n))
    if k <= 1:
        return [n // 2]
    return sorted({round(i * (n - 1) / (k - 1)) for i in range(k)})


def billed_units(n_points: int, n_vars: int = len(SURFACE_VARS)) -> float:
    """Open-Meteo weight of one multi-point request: points × max(1, vars/10)."""
    return n_points * max(1.0, n_vars / 10.0)


def level_variables(levels) -> list[str]:
    """Open-Meteo hourly names of the compared sounding fields at ``levels``."""
    return [f"{stem}_{p}hPa" for p in levels for stem in LEVEL_VARS.values()]


def compare_point(
    stats: dict[str, VarStats], pack_hourly, om_hourly: dict, where: str,
    levels=(),
) -> int:
    """Fold one point's pack hours vs Open-Meteo's into ``stats``.

    Only hours where the pack has a GRIB value are compared. ``levels`` adds
    the sounding fields at those pressure levels, keyed ``field@level``.
    Returns the number of hours matched.
    """
    om_times = om_hourly.get("time") or []
    index = {t: i for i, t in enumerate(om_times)}
    matched = 0
    for h in pack_hourly:
        t = h.time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        i = index.get(t)
        if i is None or h.temperature_2m_c is None:
            continue
        matched += 1
        for f, om_var in SURFACE_VARS.items():
            pv = getattr(h, f)
            arr = om_hourly.get(om_var)
            ov = arr[i] if arr is not None and i < len(arr) else None
            if pv is None or ov is None:
                continue
            stats.setdefault(f, VarStats()).add(float(pv), float(ov), f"{where} {t}", f)
        for p in levels:
            lvl = h.level_at(p)
            if lvl is None:
                continue
            for f, stem in LEVEL_VARS.items():
                pv = getattr(lvl, f)
                arr = om_hourly.get(f"{stem}_{p}hPa")
                ov = arr[i] if arr is not None and i < len(arr) else None
                if pv is None or ov is None:
                    continue
                key = f"{f}@{p}"
                stats.setdefault(key, VarStats()).add(float(pv), float(ov), f"{where} {t}", key)
    return matched


def hourly_as_open_meteo(hourly) -> dict:
    """A pack's hourly list in Open-Meteo's response shape, for ``compare_point``.

    Lets an Open-Meteo-built pack (``WB_GRIB_PRIMARY=0``) stand in for the
    Open-Meteo response, as ``scripts/compare_grib_primary.py`` does.
    """
    out: dict[str, list] = {
        "time": [h.time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M") for h in hourly],
    }
    for f, om_var in SURFACE_VARS.items():
        out[om_var] = [getattr(h, f) for h in hourly]
    levels = sorted({pl.pressure_hpa for h in hourly for pl in h.pressure_levels})
    for p in levels:
        for f, stem in LEVEL_VARS.items():
            out[f"{stem}_{p}hPa"] = [
                getattr(lvl, f) if (lvl := h.level_at(p)) is not None else None
                for h in hourly
            ]
    return out


def print_stats(stats: dict[str, dict[str, VarStats]]) -> None:
    """One table per model source: bias, mean/max |Δ|, total ratio, suspects."""
    for source, per_var in sorted(stats.items()):
        print(f"\n=== {source}")
        print(f"{'field':<26} {'n':>5} {'bias':>8} {'mean|Δ|':>8} {'max|Δ|':>8}  total ratio")
        level_keys = sorted(
            (k for k in per_var if "@" in k),
            key=lambda k: (-int(k.split("@")[1]), list(LEVEL_VARS).index(k.split("@")[0])),
        )
        for f in [*SURFACE_VARS, *level_keys]:
            s = per_var.get(f)
            if s is None or s.n == 0:
                print(f"{f:<26} {'0':>5}  (no pairs: field missing on one side)")
                continue
            ratio = ""
            if f in ACCUMULATED and s.total_om > 0:
                ratio = f"{s.total_pack / s.total_om:.2f}"
            print(f"{f:<26} {s.n:>5} {s.sum_d / s.n:>8.2f} {s.sum_abs / s.n:>8.2f} "
                  f"{s.max_abs:>8.2f}  {ratio}")
            for line in s.suspects:
                print(f"    suspect: {line}")
            if s.max_where and s.max_abs > SUSPECT.get(f.split("@")[0], math.inf):
                print(f"    max at {s.max_where}")


def find_packs(packs_root: Path, day: date) -> list[tuple[Path, dict]]:
    out = []
    for meta_path in sorted(packs_root.glob("*/*/*/fetch_meta.json")):
        try:
            meta = json.loads(meta_path.read_text())
        except (ValueError, OSError):
            continue
        facts = meta.get("fetch_facts") or {}
        if not facts.get("grib_primary"):
            continue
        try:
            fetched = datetime.fromisoformat(meta["fetched_at"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        if fetched.date() == day:
            out.append((meta_path.parent, facts))
    return out


def fetch_open_meteo(base_url: str, points, om_model: str, run: datetime,
                     start: date, end: date, levels=()) -> list[dict]:
    import requests

    params = {
        "latitude": ",".join(f"{p.lat:.4f}" for p in points),
        "longitude": ",".join(f"{p.lon:.4f}" for p in points),
        "hourly": ",".join([*SURFACE_VARS.values(), *level_variables(levels)]),
        "models": om_model,
        "run": run.strftime("%Y-%m-%dT%H:%M"),
        # The single-run endpoint rejects start_date/end_date; its time axis
        # starts at the run, so ask for enough days to reach ``end``.
        "forecast_days": max(1, (end - run.date()).days + 1),
        "wind_speed_unit": "kn",
        "timezone": "UTC",
    }
    key = os.environ.get("OPENMETEO_API_KEY")
    if key and "customer-" in base_url:
        params["apikey"] = key
    resp = requests.get(base_url, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else [data]


def main() -> int:
    from weatherbrief.tasks.artifacts import load_cross_sections, load_route_points

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", type=date.fromisoformat,
                    default=datetime.now(timezone.utc).date(),
                    help="UTC fetch date of the packs (default today)")
    ap.add_argument("--packs-root", type=Path,
                    default=Path(os.environ.get("DATA_DIR", ROOT / "data")) / "packs")
    ap.add_argument("--points", type=int, default=3, help="route points sampled per slot")
    ap.add_argument("--max-packs", type=int, default=30)
    ap.add_argument("--budget", type=float, default=300.0, help="max billed units")
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--om-model", action="append", default=[],
                    help="override a source→Open-Meteo model name, e.g. ecmwf=ecmwf_ifs")
    ap.add_argument("--levels", default=",".join(str(p) for p in DEFAULT_LEVELS),
                    help="pressure levels (hPa) compared on GFS slots; '' for none")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    om_models = dict(DEFAULT_OM_MODELS)
    for item in args.om_model:
        k, _, v = item.partition("=")
        om_models[k] = v

    levels = tuple(int(p) for p in args.levels.split(",") if p.strip())
    packs = find_packs(args.packs_root, args.date)[: args.max_packs]
    print(f"{len(packs)} GRIB-primary pack(s) fetched on {args.date} under {args.packs_root}")

    stats: dict[str, dict[str, VarStats]] = {}
    spent = 0.0
    for pack_dir, facts in packs:
        sections = {cs.model.value: cs for cs in load_cross_sections(pack_dir)}
        route_points = load_route_points(pack_dir) or []
        for model, n_fallback in facts["grib_primary"].items():
            cs = sections.get(model)
            init_ts = (facts.get("grib_init_times") or {}).get(model)
            source = (facts.get("grib_sources") or {}).get(model, model)
            om_model = om_models.get(source) or om_models.get(model)
            if cs is None or init_ts is None or om_model is None:
                print(f"  {pack_dir}: {model} skipped (no section / GRIB run / OM model)")
                continue
            idx = sample_indices(len(route_points), args.points)
            slot_levels = levels if source in SOUNDING_SOURCES or model in SOUNDING_SOURCES else ()
            cost = billed_units(len(idx), len(SURFACE_VARS) + len(level_variables(slot_levels)))
            label = f"{pack_dir.parent.name}/{pack_dir.name} {source}"
            if spent + cost > args.budget:
                print(f"  budget reached ({spent:.0f}/{args.budget:.0f} units), stopping")
                break
            run = datetime.fromtimestamp(init_ts, tz=timezone.utc)
            times = [h.time for i in idx for h in cs.point_forecasts[i].hourly
                     if h.temperature_2m_c is not None]
            if not times:
                continue
            print(f"  {label}: run {run:%Y-%m-%d %H}Z, {len(idx)} pts, "
                  f"{n_fallback} fell back in the briefing, ~{cost:.1f} units")
            spent += cost
            if args.dry_run:
                continue
            try:
                om = fetch_open_meteo(
                    args.base_url, [route_points[i] for i in idx], om_model, run,
                    min(times).date(), max(times).date(), slot_levels,
                )
            except Exception as exc:  # noqa: BLE001 — report and go on
                print(f"    Open-Meteo request failed: {exc}")
                continue
            per_model = stats.setdefault(source, {})
            for i, point_om in zip(idx, om):
                rp = route_points[i]
                where = f"{label} pt{i} ({rp.distance_from_origin_nm:.0f} nm)"
                compare_point(per_model, cs.point_forecasts[i].hourly,
                              point_om.get("hourly") or {}, where, slot_levels)

    print(f"\nBilled units {'estimated' if args.dry_run else 'spent'}: ~{spent:.0f}")
    print_stats(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
