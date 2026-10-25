"""Side-by-side for GRIB-primary ICON / GFS map snapshots (#914).

Builds one forecast-map cycle of ``--model`` (icon, default, or gfs) twice for
the watchlist airports, once with ``WB_GRIB_SNAPSHOTS=0`` (Open-Meteo first,
the pre-#914 path) and once GRIB-primary, on the same init, then prints:

- wall time, Open-Meteo calls and billed weight of each build,
- how many airports GRIB covered and how many fell back,
- flight-category agreement (the map's colour) and the off-by-N counts,
  overall and split inside the ICON-D2 domain within 48 h vs elsewhere (#928;
  ``WB_GRIB_SNAPSHOTS_D2=0`` makes the GRIB side ICON-EU only),
- bias and mean / max |Δ| of ceiling, visibility, wind, gust, temperature,
  freezing level and precipitation, plus the worst airports for each.

Nothing is stored. Needs DWD opendata (or NOAA) and Open-Meteo, so run it on the Mac
mini (where the map cycle runs), not in a sandbox. The init comes from
Open-Meteo's metadata, as in the cycle.

Usage:
    python scripts/compare_grib_snapshots.py [--model icon|gfs] [--limit N] [--days D]
        [--sample-hours 6,12] [--worst 5]
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

import requests  # noqa: E402

from weatherbrief.analysis.airport_consensus import best_ceiling, flight_category  # noqa: E402
from weatherbrief.fetch.grib.icon_eu_fetch import (  # noqa: E402
    ICON_D2,
    icon_eu_model_level_max_hour,
)
from weatherbrief.fetch.model_status import fetch_model_metadata  # noqa: E402
from weatherbrief.fetch.open_meteo import open_meteo_meter  # noqa: E402
from weatherbrief.tasks.airport_watchlist import (  # noqa: E402
    get_configs_dir,
    load_watchlist_with_coords,
)
from weatherbrief.tasks.forecast_grid import MAP_FORECAST_DAYS  # noqa: E402
from weatherbrief.tasks.standalone_grib import datetime_to_init_parts as _init_parts  # noqa: E402
from weatherbrief.tasks.standalone_verification import (  # noqa: E402
    _icon_d2_airports,
    fetch_model_snapshots,
)

_CATEGORIES = ["LIFR", "IFR", "MVFR", "VFR"]

_FIELDS = (
    ("ceiling_ft", lambda s: best_ceiling(s)),
    ("visibility_m", lambda s: s.get("visibility_m")),
    ("wind_kt", lambda s: s.get("wind_speed_10m_kt")),
    ("gust_kt", lambda s: s.get("wind_gusts_10m_kt")),
    ("t2m_c", lambda s: s.get("temperature_2m_c")),
    ("freezing_level_ft", lambda s: s.get("freezing_level_ft")),
    # Per hour, so a 3 h GRIB row compares with Open-Meteo's 1 h total.
    ("precip_mm_h", lambda s: (
        s["precipitation_mm"] / (s.get("precip_period_h") or 1)
        if s.get("precipitation_mm") is not None else None
    )),
)


class _GribLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith(("GRIB-primary", "ICON snapshot GRIB", "GFS snapshot GRIB", "Model icon GRIB", "Model gfs GRIB")):
            self.lines.append(msg)


def _build(label: str, switch: str, model, init_time, airports, days, sample_hours):
    os.environ["WB_GRIB_SNAPSHOTS"] = switch
    log = _GribLog()
    logging.getLogger("weatherbrief").addHandler(log)
    t0 = time.monotonic()
    try:
        with open_meteo_meter() as meter:
            snaps, calls = fetch_model_snapshots(
                model, init_time, airports, requests.Session(),
                sample_hours=sample_hours, days=days,
            )
    finally:
        logging.getLogger("weatherbrief").removeHandler(log)
    took = time.monotonic() - t0
    print(f"[{label}] {len(snaps)} rows in {took:.0f}s, Open-Meteo "
          f"{calls} calls, billed weight {meter.usage.weight:.0f}")
    for line in log.lines:
        print(f"[{label}]   {line}")
    return {(s["icao"], s["forecast_hour"]): s for s in snaps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=None, help="first N watchlist airports")
    parser.add_argument("--model", choices=("icon", "gfs"), default="icon")
    parser.add_argument("--days", type=int, default=None, help="default: the map's horizon")
    parser.add_argument("--sample-hours", default=None,
                        help="flat UTC hour list (default: the map's per-day grid)")
    parser.add_argument("--worst", type=int, default=5)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("weatherbrief").setLevel(logging.INFO)

    airports = load_watchlist_with_coords(get_configs_dir(), os.environ["AIRPORTS_DB"])
    if args.limit:
        airports = airports[: args.limit]
    model = args.model
    days = args.days if args.days is not None else MAP_FORECAST_DAYS.get(model, 4)
    meta = fetch_model_metadata([model])[model]
    init_time = datetime.fromtimestamp(meta.last_init_time, tz=timezone.utc)
    sample_hours = (
        [int(h) for h in args.sample_hours.split(",")] if args.sample_hours else None
    )
    print(f"{model.upper()} init {init_time:%Y-%m-%d %HZ}, {len(airports)} airports, {days} days")

    off = _build("open-meteo", "0", model, init_time, airports, days, sample_hours)
    on = _build("grib", model, model, init_time, airports, days, sample_hours)

    keys = sorted(set(off) & set(on))
    print(f"\n{len(keys)} common point-hours "
          f"(only Open-Meteo {len(set(off) - set(on))}, only GRIB {len(set(on) - set(off))})")

    def _category_line(label, subset):
        same, steps = 0, {}
        for k in subset:
            a, b = flight_category(off[k]), flight_category(on[k])
            if a == b:
                same += 1
            else:
                d = _CATEGORIES.index(b) - _CATEGORIES.index(a)
                steps[d] = steps.get(d, 0) + 1
        if subset:
            print(f"Flight category{label}: {same}/{len(subset)} same "
                  f"({100 * same / len(subset):.1f} %); GRIB minus Open-Meteo, in categories: "
                  + ", ".join(f"{d:+d}: {n}" for d, n in sorted(steps.items())))

    _category_line("", keys)
    # #928: inside the ICON-D2 domain within 48 h (D2 rows; Open-Meteo's
    # icon_seamless served D2 there) vs the rest (ICON-EU on both sides).
    if model == "icon":
        d2_icaos = {a.icao for a in _icon_d2_airports(airports, *_init_parts(init_time))}
        d2_hours = icon_eu_model_level_max_hour(init_time.hour, ICON_D2)
        in_d2 = {k for k in keys if k[0] in d2_icaos
                 and (k[1] - init_time).total_seconds() <= d2_hours * 3600}
        _category_line(f" inside D2 <= {d2_hours} h", [k for k in keys if k in in_d2])
        _category_line(" elsewhere", [k for k in keys if k not in in_d2])

    print(f"\n{'field':<18}{'n':>6}{'bias':>10}{'mean|Δ|':>10}{'max|Δ|':>10}  worst")
    for name, get in _FIELDS:
        diffs = []
        for k in keys:
            a, b = get(off[k]), get(on[k])
            if a is not None and b is not None:
                diffs.append((b - a, k, a, b))
        n_none = sum(1 for k in keys if (get(off[k]) is None) != (get(on[k]) is None))
        if not diffs:
            print(f"{name:<18}{0:>6}  (no pairs; {n_none} one-sided None)")
            continue
        absd = [abs(d) for d, *_ in diffs]
        worst = sorted(diffs, key=lambda x: -abs(x[0]))[: args.worst]
        worst_s = "; ".join(
            f"{k[0]} {k[1]:%d %HZ} {a:.0f}->{b:.0f}" for _d, k, a, b in worst
        )
        print(f"{name:<18}{len(diffs):>6}{statistics.fmean(d for d, *_ in diffs):>10.2f}"
              f"{statistics.fmean(absd):>10.2f}{max(absd):>10.1f}  {worst_s}"
              + (f"  [{n_none} one-sided None]" if n_none else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
