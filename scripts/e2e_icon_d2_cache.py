#!/usr/bin/env python
"""End-to-end check of the ICON-D2 per-level GRIB cache against LIVE DWD data.

Run before shipping ICON-D2 to production, and after any change to the fetch,
cache-layout or decode path. Needs network and downloads real GRIB (~430 MB per
forecast hour), so it is a script rather than a pytest.

WHY THIS EXISTS. Every unit test mocks the decode, so nothing in the suite has
ever fed real bytes through cfgrib in the per-level layout. `weather-engine-specs.md`
asserts "cfgrib indexes by the level coordinate so concatenation order is
irrelevant" — that is an assumption, not a verified fact, and ICON-D2 has never
run in production. Tier A exists to settle it.

    Tier A  real fetch + real cfgrib decode of the full column
    Tier B  the completeness guards fire on real data (reuses A's cache, no
            new downloads)
    Tier C  a second overlapping flight reuses cached hours instead of
            re-downloading them

Usage:
    python scripts/e2e_icon_d2_cache.py                 # temp dir, ~1.3 GB
    python scripts/e2e_icon_d2_cache.py --keep          # keep the cache dir
    python scripts/e2e_icon_d2_cache.py --data-dir DIR  # reuse a warm dir
    python scripts/e2e_icon_d2_cache.py --tier A        # A / B / C / ABC

Exit code is non-zero if any check fails, so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import weatherbrief.fetch.grib as grib_mod  # noqa: E402
from weatherbrief.airports import resolve_waypoints  # noqa: E402
from weatherbrief.fetch.grib.cache import cache_key, is_cached, put_cached  # noqa: E402
from weatherbrief.fetch.grib.icon_eu_fetch import (  # noqa: E402
    ICON_D2,
    ICON_EU_VARIABLES,
    icon_model_level_var_label,
)
from weatherbrief.fetch.route_points import interpolate_route  # noqa: E402
from weatherbrief.models import (  # noqa: E402
    ModelSource,
    RouteConfig,
    RouteCrossSection,
)

# A short hop well inside the D2 domain (43.18-58.08N, 3.94W-20.34E). Short on
# purpose: route length does not change download volume (DWD serves
# whole-domain files) but flight duration does, via the forecast-hour window.
ROUTE = ["EDDC", "EDDE"]
DURATION_H = 1.0
CEILING_FT = 10_000
CRUISE_FT = 8_000

# Decoded field keys (decode._ICON_FULL_VAR_MAP) that must be present at every
# pressure level the model covers.
REQUIRED_FIELDS = {
    "raw_temperature_k": "temperature",
    "raw_u_wind_m_s": "u wind",
    "raw_v_wind_m_s": "v wind",
}

failures: list[str] = []
checks_run = 0


def check(ok: bool, label: str, detail: str = "") -> bool:
    global checks_run
    checks_run += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  — ' + detail if detail else ''}")
    if not ok:
        failures.append(f"{label}{'  — ' + detail if detail else ''}")
    return ok


def build_context(data_dir: Path, departure: datetime):
    """Real _prepare_icon_eu — including the live run-finder and corridor mask."""
    db_path = os.environ.get("AIRPORTS_DB", "data/nav.db")
    waypoints, rejected = resolve_waypoints(ROUTE, db_path)
    if rejected:
        raise SystemExit(f"could not resolve waypoints: {rejected}")
    route = RouteConfig(
        name="-".join(ROUTE), waypoints=waypoints,
        cruise_altitude_ft=CRUISE_FT, flight_ceiling_ft=CEILING_FT,
        flight_duration_hours=DURATION_H,
    )
    points = interpolate_route(route, spacing_nm=10.0)
    sections = [RouteCrossSection(
        model=ModelSource.ICON, route_points=[],
        fetched_at=datetime.now(timezone.utc), point_forecasts=[],
    )]
    ctx, skip = grib_mod._prepare_icon_eu(
        sections, points, departure,
        data_dir=data_dir, flight_duration_hours=DURATION_H,
    )
    if ctx is None:
        raise SystemExit(f"_prepare_icon_eu returned no context (skip={skip!r})")
    return ctx, points


def cached_levels(ctx, fhour: int, var: str) -> list[int]:
    return [
        lv for lv in ctx.levels
        if is_cached(ctx.run_dir, cache_key(fhour, icon_model_level_var_label(ICON_D2, var, lv)))
    ]


def tier_a(ctx) -> None:
    print("\n=== Tier A — real fetch + real cfgrib decode ===")
    check(ctx.variant is ICON_D2, "route selected ICON-D2", ctx.variant.slug)
    n_lv, n_fh = len(ctx.levels), len(ctx.forecast_hours)
    print(f"  run {ctx.init_date}_{ctx.init_hour:02d}z, fhours={ctx.forecast_hours}, "
          f"levels {ctx.levels[0]}-{ctx.levels[-1]} ({n_lv})")
    print(f"  downloading ~{n_fh * len(ICON_EU_VARIABLES) * n_lv * 0.95 / 1024:.1f} GB ...")

    grib_mod._prefetch_icon_eu_data(ctx)

    # Every variable complete at every forecast hour — the exact predicate the
    # decode gate uses. If this fails, the gate skips every hour in production
    # and ICON-D2 silently produces nothing.
    short = [
        f"f{fh:03d}/{var}:{len(cached_levels(ctx, fh, var))}/{n_lv}"
        for fh in ctx.forecast_hours for var in ICON_EU_VARIABLES
        if len(cached_levels(ctx, fh, var)) != n_lv
    ]
    check(not short, f"all {len(ICON_EU_VARIABLES)}x{n_lv} level files cached per fhour",
          ", ".join(short[:6]) if short else f"{n_fh} fhour(s)")

    # No whole-column blob is ever written by a per-level variant.
    blobs = [
        f"f{fh:03d}/{var}" for fh in ctx.forecast_hours for var in ICON_EU_VARIABLES
        if is_cached(ctx.run_dir, cache_key(fh, f"ICON_D2_{var.upper()}"))
    ]
    check(not blobs, "no whole-column blobs written", ", ".join(blobs[:5]))

    # --- the real decode ---
    fh = ctx.forecast_hours[0]
    var_paths = {
        var: [
            str(ctx.run_dir / cache_key(fh, icon_model_level_var_label(ICON_D2, var, lv)))
            for lv in ctx.levels
        ]
        for var in ICON_EU_VARIABLES
    }
    from weatherbrief.fetch.grib.decode_worker import decode_icon_chunked

    decoded, _clc = decode_icon_chunked(var_paths, ctx.point_lats, ctx.point_lons)
    check(bool(decoded) and len(decoded) == len(ctx.point_lats),
          "decode returned one entry per route point", f"{len(decoded)} points")

    pt = decoded[0]
    pressures = sorted(pt.keys(), reverse=True)
    check(len(pressures) >= 20,
          "decoded a usable pressure column", f"{len(pressures)} levels "
          f"({pressures[0]}-{pressures[-1]} hPa)")
    check(pressures == sorted(pressures, reverse=True) and len(set(pressures)) == len(pressures),
          "pressure levels strictly descending and unique")

    # THE point of Tier A: concatenation really does yield every level with a
    # full field set. A silent cfgrib mis-index would show up as missing fields
    # or a short column here.
    for key, human in REQUIRED_FIELDS.items():
        missing = [p for p in pressures if pt.get(p, {}).get(key) is None]
        check(not missing, f"{human} present at every decoded level",
              f"missing at {missing[:6]}" if missing else f"{len(pressures)} levels")

    temps_c = [pt[p]["raw_temperature_k"] - 273.15 for p in pressures
               if pt.get(p, {}).get("raw_temperature_k") is not None]
    check(all(-90 < t < 60 for t in temps_c), "temperatures physically plausible",
          f"{min(temps_c):.1f}..{max(temps_c):.1f} C" if temps_c else "none")
    check(temps_c[0] > temps_c[-1], "temperature decreases with height",
          f"{temps_c[0]:.1f} C at {pressures[0]} -> {temps_c[-1]:.1f} C at {pressures[-1]}")

    winds = [
        (pt[p]["raw_u_wind_m_s"] ** 2 + pt[p]["raw_v_wind_m_s"] ** 2) ** 0.5
        for p in pressures
    ]
    check(all(w < 150 for w in winds), "wind speeds physically plausible",
          f"{min(winds):.0f}..{max(winds):.0f} m/s")


def tier_b(ctx) -> None:
    """Guards fire on real cached data. Reuses Tier A's cache — no downloads."""
    print("\n=== Tier B — completeness guards on real data ===")
    fh = ctx.forecast_hours[0]

    def plan_jobs() -> dict:
        captured: dict = {}
        sections = [RouteCrossSection(
            model=ModelSource.ICON, route_points=[],
            fetched_at=datetime.now(timezone.utc), point_forecasts=[],
        )]
        orig = grib_mod._dispatch_decode_parallel
        try:
            grib_mod._dispatch_decode_parallel = lambda jobs: captured.setdefault("jobs", jobs) and []
            grib_mod._decode_and_merge_icon_eu(ctx, sections, [], [])
        finally:
            grib_mod._dispatch_decode_parallel = orig
        return captured.get("jobs", [])

    check(len(plan_jobs()) == len(ctx.forecast_hours),
          "complete cache plans a decode job for every hour")

    # 1. Remove three level files -> that hour must be skipped, not decoded short.
    victims = [
        ctx.run_dir / cache_key(fh, icon_model_level_var_label(ICON_D2, "u", lv))
        for lv in ctx.levels[:3]
    ]
    stash = {p: p.read_bytes() for p in victims}
    for p in victims:
        p.unlink()
    try:
        check(len(plan_jobs()) == len(ctx.forecast_hours) - 1,
              "hour with 3 missing level files is skipped",
              f"removed u L{ctx.levels[0]}-L{ctx.levels[2]}")
    finally:
        for p, data in stash.items():
            p.write_bytes(data)
    check(len(plan_jobs()) == len(ctx.forecast_hours), "restoring the files re-enables the hour")

    # 2. A legacy whole-column blob must NOT be trusted by a per-level variant,
    #    and must NOT stop the prefetch from fetching per-level (that pairing
    #    would deadlock the hour until the blob aged out).
    blob_ck = cache_key(fh, "ICON_D2_U")
    put_cached(ctx.run_dir, blob_ck, b"not-a-real-column")
    try:
        jobs = plan_jobs()
        check(len(jobs) == len(ctx.forecast_hours),
              "legacy blob is ignored, per-level files still used")
        if jobs:
            _worker, (var_paths, *_rest) = jobs[0]
            check(all(len(p) == len(ctx.levels) for p in var_paths.values()),
                  "every variable still resolves to its full level list")
            check(not any(str(blob_ck) in p for paths in var_paths.values() for p in paths),
                  "the blob itself is never read")
    finally:
        (ctx.run_dir / blob_ck).unlink(missing_ok=True)


def tier_c(data_dir: Path, departure: datetime, ctx_a) -> None:
    """A second, overlapping flight must reuse cached hours, not refetch them."""
    print("\n=== Tier C — cross-briefing reuse ===")
    sample = [
        ctx_a.run_dir / cache_key(fh, icon_model_level_var_label(ICON_D2, var, lv))
        for fh in ctx_a.forecast_hours for var in ("t", "u") for lv in ctx_a.levels[:4]
    ]
    before = {p: p.stat().st_mtime_ns for p in sample if p.exists()}

    # Same route, departure shifted +1 h → window overlaps but is not identical.
    ctx_b, _pts = build_context(data_dir, departure + timedelta(hours=1))
    check(ctx_b.run_dir == ctx_a.run_dir, "second flight lands on the same run dir")
    overlap = sorted(set(ctx_a.forecast_hours) & set(ctx_b.forecast_hours))
    fresh = sorted(set(ctx_b.forecast_hours) - set(ctx_a.forecast_hours))
    print(f"  flight A fhours={ctx_a.forecast_hours}  B={ctx_b.forecast_hours}")
    print(f"  overlap={overlap}  new={fresh}")

    grib_mod._prefetch_icon_eu_data(ctx_b)

    unchanged = [p for p, m in before.items() if p.stat().st_mtime_ns == m]
    check(len(unchanged) == len(before),
          "already-cached files were not re-downloaded",
          f"{len(unchanged)}/{len(before)} untouched")

    if fresh:
        short = [
            f"f{fh:03d}/{var}" for fh in fresh for var in ICON_EU_VARIABLES
            if len(cached_levels(ctx_b, fh, var)) != len(ctx_b.levels)
        ]
        check(not short, "newly-needed hours fetched complete", ", ".join(short[:5]))
    else:
        print("  NOTE: no new forecast hours — reuse verified, top-up not exercised")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", help="reuse an existing cache dir instead of a temp one")
    ap.add_argument("--keep", action="store_true", help="keep the temp cache dir")
    ap.add_argument("--tier", default="ABC", help="tiers to run, e.g. A / AB / ABC")
    ap.add_argument("--hours-ahead", type=int, default=8,
                    help="departure offset from now (default 8h)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")

    tmp_created = args.data_dir is None
    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="d2-e2e-"))
    departure = (datetime.now(timezone.utc) + timedelta(hours=args.hours_ahead)).replace(
        minute=0, second=0, microsecond=0,
    )
    print(f"data dir : {data_dir}")
    print(f"departure: {departure:%Y-%m-%d %H:%M} UTC  ({ROUTE[0]}->{ROUTE[-1]}, {DURATION_H} h)")

    try:
        ctx, _points = build_context(data_dir, departure)
        if "A" in args.tier:
            tier_a(ctx)
        if "B" in args.tier:
            tier_b(ctx)
        if "C" in args.tier:
            tier_c(data_dir, departure, ctx)
    finally:
        if tmp_created and not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)
            print(f"\ncleaned up {data_dir}")
        elif tmp_created:
            print(f"\nkept {data_dir}")

    print(f"\n{'=' * 62}")
    if failures:
        print(f"FAILED — {len(failures)} of {checks_run} checks")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"OK — {checks_run}/{checks_run} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
