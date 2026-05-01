"""Profile a briefing refresh: per-stage wall-clock + pyinstrument flamegraph.

Loads a flight from the local DB, builds RouteConfig + BriefingOptions to
mirror the production refresh path, then runs ``execute_briefing`` wrapped
in a stage-timing collector and a pyinstrument profiler.

Outputs:
- per-stage timing summary to stdout
- pyinstrument HTML to ``profiles/{flight_id}_{ts}.html``
- briefing pack written under ``profiles/_packs/{flight_id}/{ts}/``
  (kept separate from real ``data/packs/`` to avoid mixing with real data)

Usage:
    python scripts/profile_refresh.py <flight-id> [--force-live] [--clear-grib-cache]
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Project paths
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from pyinstrument import Profiler

from weatherbrief.api.flights import _load_flight_or_404
from weatherbrief.api.packs import _prepare_refresh
from weatherbrief.db import SessionLocal, init_shared_db
from weatherbrief.pipeline import BriefingOptions, execute_briefing


def _stage_recorder():
    """Return (callback, finalize) — finalize() returns list of (stage, seconds)."""
    starts: dict[str, float] = {}
    order: list[str] = []
    last_stage: list[str | None] = [None]
    last_t: list[float] = [time.perf_counter()]
    durations: list[tuple[str, float]] = []

    def callback(stage: str, detail: str | None = None) -> None:
        now = time.perf_counter()
        # Record duration of the previous stage
        if last_stage[0] is not None:
            durations.append((_stage_label(last_stage[0], None), now - last_t[0]))
        # Compose stage label including the model name when present
        last_stage[0] = stage
        last_t[0] = now
        # For per-model fetch_forecasts notifications, fold detail into label
        if stage == "fetch_forecasts" and detail:
            last_stage[0] = f"fetch_forecasts:{detail}"
        elif stage == "grib_enrichment" and detail:
            last_stage[0] = f"grib_enrichment:{detail}"
        if stage in starts:
            return
        starts[stage] = now
        order.append(stage)

    def finalize(end_t: float) -> list[tuple[str, float]]:
        if last_stage[0] is not None:
            durations.append((_stage_label(last_stage[0], None), end_t - last_t[0]))
        return durations

    return callback, finalize


def _stage_label(stage: str, detail: str | None) -> str:
    if detail:
        return f"{stage}:{detail}"
    return stage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("flight_id", help="Flight ID to profile")
    parser.add_argument(
        "--force-live", action="store_true",
        help="Force historical_mode=False even if departure has passed (enables GRAMET + LLM)",
    )
    parser.add_argument(
        "--clear-grib-cache", action="store_true",
        help="Delete GFS + ICON-EU cached GRIB before running (tests cold-cache path)",
    )
    parser.add_argument(
        "--no-llm", action="store_true",
        help="Skip LLM digest stage",
    )
    parser.add_argument(
        "--no-gramet", action="store_true",
        help="Skip GRAMET fetch",
    )
    parser.add_argument(
        "--user-id", default="dev-user-001",
        help="User ID owning the flight (default: dev-user-001)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    init_shared_db()
    db = SessionLocal()
    try:
        try:
            flight = _load_flight_or_404(db, args.flight_id, viewer_id=args.user_id)
        except Exception as exc:
            print(f"Flight {args.flight_id} not found: {exc}", file=sys.stderr)
            return 1

        db_path = os.environ.get("AIRPORTS_DB", "") or str(ROOT / "data" / "nav.db")

        # Mirror _prepare_refresh, but route the pack into profiles/_packs/...
        route, fetch_ts, pack_path, options, _meta = _prepare_refresh(
            flight, db_path, args.user_id, args.flight_id, db=db,
            is_privileged=True,
        )
    finally:
        db.close()

    # Redirect output_dir into ./profiles/_packs/ so we don't pollute data/packs/
    safe_ts = fetch_ts.isoformat().replace(":", "-").replace("+", "p")
    pack_path = ROOT / "profiles" / "_packs" / args.flight_id / safe_ts
    pack_path.mkdir(parents=True, exist_ok=True)
    options.output_dir = pack_path

    if args.force_live:
        options.historical_mode = False
        options.as_of_time = None
        # Re-enable services that historical mode would have stripped
        options.fetch_gramet = not args.no_gramet
        options.generate_llm_digest = not args.no_llm
    if args.no_llm:
        options.generate_llm_digest = False
    if args.no_gramet:
        options.fetch_gramet = False

    if args.clear_grib_cache:
        data_dir = options.data_dir
        if data_dir is None:
            from weatherbrief.storage.snapshots import DEFAULT_DATA_DIR
            data_dir = DEFAULT_DATA_DIR
        for sub in ("gfs", "icon-eu"):
            cache = Path(data_dir) / ".cache" / "grib" / sub
            if cache.exists():
                print(f"clearing cache: {cache}")
                shutil.rmtree(cache)

    print(f"flight: {args.flight_id}")
    print(f"route: {route.name}")
    print(f"departure: {flight.departure_time}")
    print(f"waypoints: {[w.icao for w in route.waypoints]}")
    print(f"cruise_altitude_ft: {route.cruise_altitude_ft}")
    print(f"flight_duration_hours: {route.flight_duration_hours}")
    print(f"options: enrich_grib={options.enrich_grib} "
          f"fetch_gramet={options.fetch_gramet} "
          f"generate_llm_digest={options.generate_llm_digest} "
          f"historical_mode={options.historical_mode}")
    print(f"pack_dir: {pack_path}")
    print()

    record, finalize = _stage_recorder()

    profiler = Profiler(interval=0.01, async_mode="disabled")
    profiler.start()
    t0 = time.perf_counter()
    try:
        result = execute_briefing(
            route=route,
            departure_time=flight.departure_time,
            options=options,
            progress_callback=record,
        )
    finally:
        t_end = time.perf_counter()
        profiler.stop()

    durations = finalize(t_end)
    total = t_end - t0

    print()
    print("=" * 70)
    print(f"TOTAL elapsed: {total:.2f}s")
    print("=" * 70)
    print(f"{'stage':<40} {'seconds':>10} {'pct':>6}")
    print("-" * 70)
    # Aggregate by base stage label so multiple fetch_forecasts:* lines stack
    agg: dict[str, float] = {}
    order: list[str] = []
    for stage, secs in durations:
        if stage not in agg:
            order.append(stage)
            agg[stage] = 0.0
        agg[stage] += secs
    for stage in order:
        secs = agg[stage]
        pct = 100.0 * secs / total
        print(f"{stage:<40} {secs:>10.2f} {pct:>5.1f}%")
    print("-" * 70)

    # pyinstrument output
    profiles_dir = ROOT / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    html_path = profiles_dir / f"{args.flight_id}_{safe_ts}.html"
    html_path.write_text(profiler.output_html())
    txt_path = profiles_dir / f"{args.flight_id}_{safe_ts}.txt"
    txt_path.write_text(profiler.output_text(unicode=True, color=False, show_all=False))
    print(f"\npyinstrument html: {html_path}")
    print(f"pyinstrument txt:  {txt_path}")
    print(f"open: file://{html_path}")

    print(f"\nbriefing errors: {result.errors}")
    print(f"models_fetched: {result.models_fetched}")
    print(f"grib_init_times: {result.grib_init_times}")
    print(f"open_meteo_calls: {result.usage.open_meteo_calls}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
