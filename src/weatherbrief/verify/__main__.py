"""CLI for METAR/TAF verification system.

Usage:
    python -m weatherbrief.verify collect [--flight-id ID] [--corridor NM]
    python -m weatherbrief.verify export [--format csv|json] [--output FILE]
    python -m weatherbrief.verify stats [--model MODEL] [--icao ICAO] [--source SOURCE]
    python -m weatherbrief.verify discover [--prefixes LF,ED,...]
    python -m weatherbrief.verify standalone [--once]
    python -m weatherbrief.verify digest [--period 24h|7d] [--send] [--json]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv


def _init_db():
    """Initialize database connection."""
    load_dotenv()
    from flyfun_common.db import SessionLocal, init_shared_db, get_engine
    import weatherbrief.db.models  # noqa: F401 — register models

    engine = get_engine()
    if os.environ.get("ENVIRONMENT", "development") == "development":
        init_shared_db(engine)

    return SessionLocal


def cmd_collect(args):
    """Run a collection cycle."""
    SessionLocal = _init_db()
    db = SessionLocal()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        if args.flight_id:
            # Collect for a specific flight
            from sqlalchemy import select
            from weatherbrief.db.models import FlightRow
            from weatherbrief.tasks.verification import (
                gather_airports,
                fetch_observations_batch,
                store_observations,
            )

            row = db.get(FlightRow, args.flight_id)
            if row is None:
                print(f"ERROR: Flight '{args.flight_id}' not found", file=sys.stderr)
                sys.exit(1)

            icao_to_flights = gather_airports(
                [row], db, airports_db, args.corridor,
            )
            if not icao_to_flights:
                print("No corridor airports found.")
                return

            unique_icaos = sorted(icao_to_flights.keys())
            print(f"Fetching METAR/TAF for {len(unique_icaos)} airports...")

            observations = fetch_observations_batch(unique_icaos, airports_db)
            inserted = store_observations(observations, icao_to_flights, db)
            db.commit()

            print(f"Stored {inserted} new observations from {len(observations)} fetched.")
        else:
            # Full collection cycle
            from weatherbrief.tasks.verification import collect_and_store

            result = collect_and_store(db, airports_db, args.corridor)
            print(f"Flights: {result['flights']}")
            print(f"Airports: {result['airports']}")
            print(f"New observations: {result['observations']}")
            print(f"Finalized: {result['finalized']}")
    finally:
        db.close()


def cmd_export(args):
    """Export verification data."""
    SessionLocal = _init_db()
    db = SessionLocal()

    try:
        from sqlalchemy import select
        from weatherbrief.db.models import VerificationObservationRow

        stmt = select(VerificationObservationRow).order_by(
            VerificationObservationRow.observation_time.desc()
        )
        if args.icao:
            stmt = stmt.where(VerificationObservationRow.icao == args.icao.upper())
        if args.limit:
            stmt = stmt.limit(args.limit)

        rows = db.execute(stmt).scalars().all()

        if not rows:
            print("No observations found.")
            return

        records = []
        for r in rows:
            records.append({
                "id": r.id,
                "icao": r.icao,
                "observation_time": r.observation_time.isoformat() if r.observation_time else None,
                "collected_at": r.collected_at.isoformat() if r.collected_at else None,
                "flight_category": r.flight_category,
                "ceiling_ft": r.ceiling_ft,
                "visibility_m": r.visibility_m,
                "wind_dir": r.wind_dir,
                "wind_speed_kt": r.wind_speed_kt,
                "wind_gust_kt": r.wind_gust_kt,
                "temperature_c": r.temperature_c,
                "dewpoint_c": r.dewpoint_c,
                "qnh": r.qnh,
                "weather": r.weather,
                "taf_flight_category": r.taf_flight_category,
                "metar_raw": r.metar_raw,
            })

        output = args.output or sys.stdout

        if args.format == "json":
            content = json.dumps(records, indent=2)
            if isinstance(output, str):
                with open(output, "w") as f:
                    f.write(content)
                print(f"Exported {len(records)} observations to {output}")
            else:
                output.write(content + "\n")
        else:  # csv
            if not records:
                return
            fieldnames = list(records[0].keys())
            buf = io.StringIO() if not isinstance(output, str) else None
            f = open(output, "w", newline="") if isinstance(output, str) else buf
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
            if isinstance(output, str):
                f.close()
                print(f"Exported {len(records)} observations to {output}")
            else:
                output.write(buf.getvalue())
    finally:
        db.close()


def cmd_score(args):
    """Run scoring for completed flights."""
    SessionLocal = _init_db()
    db = SessionLocal()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        from weatherbrief.tasks.scoring import score_completed_flights

        result = score_completed_flights(db, airports_db)
        db.commit()

        print(f"Flights scored: {result['flights_scored']}")
        print(f"Model scores:   {result['model_scores']}")
        print(f"TAF scores:     {result['taf_scores']}")
    finally:
        db.close()


def cmd_backfill(args):
    """Re-run scoring (backfill after code changes)."""
    SessionLocal = _init_db()
    db = SessionLocal()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    try:
        from weatherbrief.tasks.scoring import backfill_scores

        result = backfill_scores(db, airports_db, flight_id=args.flight_id)
        db.commit()

        print(f"Flights scored: {result['flights_scored']}")
        print(f"Model scores:   {result['model_scores']}")
        print(f"TAF scores:     {result['taf_scores']}")
    finally:
        db.close()


def cmd_stats(args):
    """Show verification statistics."""
    SessionLocal = _init_db()
    db = SessionLocal()

    source = getattr(args, 'source', None)

    try:
        from sqlalchemy import func, select
        from weatherbrief.db.models import (
            FlightVerificationMapRow,
            VerificationObservationRow,
            VerificationScoreRow,
        )

        obs_count = db.execute(
            select(func.count(VerificationObservationRow.id))
        ).scalar() or 0

        score_stmt = select(func.count(VerificationScoreRow.id))
        if source:
            score_stmt = score_stmt.where(VerificationScoreRow.source == source)
        score_count = db.execute(score_stmt).scalar() or 0

        airport_count = db.execute(
            select(func.count(func.distinct(VerificationObservationRow.icao)))
        ).scalar() or 0

        label = f"Verification Statistics (source={source})" if source else "Verification Database Statistics"
        print(label)
        print(f"{'─' * 40}")
        print(f"Observations:    {obs_count:>8}")
        print(f"Scores:          {score_count:>8}")
        print(f"Unique airports: {airport_count:>8}")

        if not source or source == "flight":
            flight_count = db.execute(
                select(func.count(func.distinct(FlightVerificationMapRow.flight_id)))
            ).scalar() or 0
            print(f"Flights tracked: {flight_count:>8}")

        if obs_count > 0:
            print(f"\nFlight Category Distribution:")
            cat_counts = db.execute(
                select(
                    VerificationObservationRow.flight_category,
                    func.count(VerificationObservationRow.id),
                )
                .where(VerificationObservationRow.flight_category.isnot(None))
                .group_by(VerificationObservationRow.flight_category)
                .order_by(func.count(VerificationObservationRow.id).desc())
            ).all()
            for cat, count in cat_counts:
                pct = 100 * count / obs_count
                print(f"  {cat:>5}: {count:>6} ({pct:.1f}%)")
    finally:
        db.close()


def cmd_discover(args):
    """Discover METAR-reporting airports for the watchlist."""
    load_dotenv()
    from weatherbrief.tasks.airport_watchlist import (
        DEFAULT_PREFIXES,
        discover_airports,
        get_configs_dir,
        save_watchlist,
    )

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    prefixes = args.prefixes.split(",") if args.prefixes else DEFAULT_PREFIXES
    print(f"Discovering airports for prefixes: {', '.join(prefixes)}")

    airports = discover_airports(prefixes, airports_db)
    total = sum(len(v) for v in airports.values())
    print(f"\nFound {total} airports with METARs:")
    for prefix in sorted(airports):
        print(f"  {prefix}: {len(airports[prefix])}")

    configs_dir = get_configs_dir()
    path = save_watchlist(airports, configs_dir)
    print(f"\nSaved to {path}")


def _enter_background_mode() -> None:
    """Lower scheduling priority and prefer this process as the OOM victim.

    Used by scheduler-spawned standalone cycles (issue #236): the cycle
    competes less with interactive requests for CPU, and if the cgroup ever
    OOMs mid-cycle the kernel kills this disposable child instead of the
    uvicorn parent (the OOM killer favours higher oom_score_adj). Both are
    best-effort — a restricted environment just runs at normal priority.
    """
    log = logging.getLogger(__name__)
    try:
        os.nice(10)
    except OSError:
        log.warning("Could not renice background cycle", exc_info=True)
    # oom_score_adj is a Linux-only procfs knob; skip on macOS/other dev hosts
    # rather than logging a spurious FileNotFoundError traceback every cycle.
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("500")
        except OSError:
            log.warning("Could not set oom_score_adj", exc_info=True)


def cmd_standalone(args):
    """Run a standalone verification cycle."""
    if args.background:
        _enter_background_mode()

    _init_db()
    load_dotenv()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    from weatherbrief.tasks.airport_watchlist import get_configs_dir, load_watchlist_with_coords

    try:
        watchlist = load_watchlist_with_coords(get_configs_dir(), airports_db)
        print(f"Loaded {len(watchlist)} airports from watchlist")
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    from weatherbrief.tasks.standalone_verification import (
        run_post_cycle_tasks,
        run_standalone_cycle,
    )

    result = run_standalone_cycle(
        watchlist, airports_db,
        fetch_forecasts=not args.light,
        score_observations=not args.forecast_only,
    )
    print(f"\nStandalone verification cycle complete:")
    print(f"  Models fetched: {result.get('models_fetched', 0)}")
    print(f"  Snapshots stored: {result.get('snapshots_stored', 0)}")
    print(f"  Observations stored: {result.get('observations_stored', 0)}")
    print(f"  Scores created: {result.get('scores_created', 0)}")
    print(f"  Duration: {result.get('duration_ms', 0)}ms")

    if args.with_rollup:
        t_post = time.monotonic()
        run_post_cycle_tasks(airports_db, result["cycle_type"])
        post_ms = int((time.monotonic() - t_post) * 1000)
        # The cycle Duration above excludes post-cycle work, which historically
        # hid a ~40-min cache rebuild (#448) — print the full picture.
        print(f"  Post-cycle tasks (rollup + cache rebuild): {post_ms}ms")
        print(f"  Total: {result.get('duration_ms', 0) + post_ms}ms")

    # Tidy teardown of the child's sounding/decode pool (#448 PR B). A hung
    # worker must not block process exit — wait=False leaves it for the OS.
    try:
        from weatherbrief.fetch.grib import shutdown_decode_pool

        shutdown_decode_pool(wait=False, drain_dispatcher=True)
    except Exception:
        pass


def cmd_rebuild_cache(args):
    """Rebuild the verification/forecast map cache."""
    _init_db()
    load_dotenv()

    airports_db = os.environ.get("AIRPORTS_DB", "")
    if not airports_db:
        print("ERROR: AIRPORTS_DB environment variable not set", file=sys.stderr)
        sys.exit(1)

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.cache_builder import rebuild_all

    db = SessionLocal()
    try:
        result = rebuild_all(db, airports_db)
        print("Cache rebuild complete:")
        print(f"  Stats entries: {result['stats']}")
        print(f"  Bias leaderboard entries: {result['bias_leaderboard']}")
        print(f"  Forecast map entries: {result['forecast_map']}")
        print(f"  Duration: {result['duration_ms']}ms")
    finally:
        db.close()


def cmd_rollup_summary(args):
    """Roll up raw observations into airport_monthly_summary / airport_daily_summary."""
    from datetime import date as date_cls, datetime as dt, timezone

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.airport_summary import (
        rebuild_all_days,
        rollup_all_complete_days,
        rollup_all_complete_months,
        rollup_day,
        rollup_month,
    )

    _init_db()
    load_dotenv()

    db = SessionLocal()
    try:
        if args.rebuild:
            # Used at deploy time after a migration that adds a column to
            # airport_daily_summary (e.g. migration 057's n_category_changes):
            # existing rows keep the column default until re-rolled.
            n = rebuild_all_days(db)
            db.commit()
            print(f"Re-rolled {n} existing airport-days.")
            return

        if args.all or (not args.month and not args.day):
            n_months = rollup_all_complete_months(db)
            n_days = rollup_all_complete_days(db)
            db.commit()
            print(f"Rolled up {n_months} airport-months and {n_days} airport-days.")
            return

        if args.month:
            try:
                year, month = args.month.split("-")
                month_start = dt(int(year), int(month), 1, tzinfo=timezone.utc)
            except (ValueError, AttributeError):
                print(f"ERROR: invalid --month {args.month!r} (expect YYYY-MM)",
                      file=sys.stderr)
                sys.exit(1)
            n = rollup_month(db, month_start)
            db.commit()
            print(f"Rolled up {n} airport-months for {args.month}.")

        if args.day:
            try:
                d = date_cls.fromisoformat(args.day)
            except ValueError:
                print(f"ERROR: invalid --day {args.day!r} (expect YYYY-MM-DD)",
                      file=sys.stderr)
                sys.exit(1)
            n = rollup_day(db, d)
            db.commit()
            print(f"Rolled up {n} airport-days for {args.day}.")
    finally:
        db.close()


def cmd_rollup_daily_stats(args):
    """Roll up raw verification_scores into verification_daily_stats.

    Used at deploy time to backfill existing standalone data. The scheduler
    keeps this up to date after each cycle, but this command is safe to
    re-run any time (idempotent DELETE+INSERT per day).
    """
    from datetime import date as date_cls

    from flyfun_common.db import SessionLocal

    from weatherbrief.tasks.verification_daily_rollup import (
        rebuild_all_days,
        rollup_all_complete_days,
        rollup_day,
    )

    _init_db()
    load_dotenv()

    db = SessionLocal()
    try:
        if args.day:
            try:
                d = date_cls.fromisoformat(args.day)
            except ValueError:
                print(
                    f"ERROR: invalid --day {args.day!r} (expect YYYY-MM-DD)",
                    file=sys.stderr,
                )
                sys.exit(1)
            n = rollup_day(db, d)
            db.commit()
            print(f"Rolled up {n} groups for {args.day}.")
            return

        if args.rebuild:
            n = rebuild_all_days(db)
            db.commit()
            print(f"Re-rolled {n} groups across existing days.")
            return

        n = rollup_all_complete_days(db)
        db.commit()
        print(f"Rolled up {n} groups across pending days.")
    finally:
        db.close()


def cmd_digest(args):
    """Preview or send the admin digest."""
    from datetime import timedelta

    _init_db()
    from flyfun_common.db import SessionLocal
    from weatherbrief.tasks.admin_digest_stats import get_admin_digest_data

    hours = {"24h": 24, "7d": 168}[args.period]
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    period_label = f"{args.period} — {now.strftime('%Y-%m-%d %H:%M')}Z"
    base_url = os.environ.get("WEATHERBRIEF_BASE_URL", "https://weather.flyfun.aero")

    db = SessionLocal()
    try:
        data = get_admin_digest_data(db, since, now, period_label=period_label, base_url=base_url)

        if args.json:
            print(json.dumps(data.model_dump(mode="json"), indent=2))
        else:
            # Print plain text version
            from weatherbrief.notify.admin_digest_email import _build_plain
            print(_build_plain(data))

        if args.send:
            from weatherbrief.notify.admin_digest_email import send_admin_digest
            from weatherbrief.notify.admin_email import get_admin_emails

            admin_emails = get_admin_emails()
            if not admin_emails:
                print("\nNo ADMIN_EMAILS configured, cannot send.")
                sys.exit(1)
            send_admin_digest(admin_emails, data)
            print(f"\nSent to: {', '.join(admin_emails)}")
    finally:
        db.close()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="weatherbrief.verify",
        description="METAR/TAF verification system",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # collect
    p_collect = subparsers.add_parser("collect", help="Run a collection cycle")
    p_collect.add_argument("--flight-id", help="Collect for a specific flight")
    p_collect.add_argument(
        "--corridor", type=float, default=15.0,
        help="Corridor width in NM (default: 15)",
    )

    # export
    p_export = subparsers.add_parser("export", help="Export verification data")
    p_export.add_argument(
        "--format", choices=["csv", "json"], default="json",
        help="Output format (default: json)",
    )
    p_export.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_export.add_argument("--icao", help="Filter by ICAO code")
    p_export.add_argument("--limit", type=int, help="Limit number of records")

    # score
    p_score = subparsers.add_parser("score", help="Score completed flights")

    # backfill
    p_backfill = subparsers.add_parser("backfill", help="Re-run scoring (backfill)")
    p_backfill.add_argument("--flight-id", help="Backfill a specific flight")

    # stats
    p_stats = subparsers.add_parser("stats", help="Show verification statistics")
    p_stats.add_argument(
        "--source", choices=["flight", "standalone"],
        help="Filter by source (default: all)",
    )

    # discover
    p_discover = subparsers.add_parser(
        "discover",
        help="Discover METAR-reporting airports for watchlist",
    )
    p_discover.add_argument(
        "--prefixes",
        help="Comma-separated ICAO prefixes (default: LF,ED,EG,EH,EB,LS,LO)",
    )

    # standalone
    p_standalone = subparsers.add_parser(
        "standalone",
        help="Run a standalone verification cycle",
    )
    p_standalone.add_argument(
        "--once", action="store_true", default=True,
        help="Run a single cycle (default)",
    )
    standalone_mode = p_standalone.add_mutually_exclusive_group()
    standalone_mode.add_argument(
        "--light", action="store_true",
        help="Light cycle: observations + scoring only, skip forecast fetch",
    )
    standalone_mode.add_argument(
        "--forecast-only", action="store_true",
        help="Forecast cycle: fetch + store snapshots only, skip scoring "
             "(what the scheduler's 07/19 UTC fetch loop runs)",
    )
    p_standalone.add_argument(
        "--with-rollup", action="store_true",
        help="After the cycle, run the daily-stats rollup + dashboard cache "
             "rebuild (the scheduler loops' post-cycle work)",
    )
    p_standalone.add_argument(
        "--background", action="store_true",
        help="Renice and raise oom_score_adj — set by the scheduler when it "
             "runs the cycle as an isolated subprocess",
    )

    # rebuild-cache
    subparsers.add_parser(
        "rebuild-cache",
        help="Rebuild verification/forecast map cache",
    )

    # rollup-summary
    p_rollup_summary = subparsers.add_parser(
        "rollup-summary",
        help="Roll up obs into airport_monthly/daily_summary tables",
    )
    p_rollup_summary.add_argument(
        "--month",
        help="Roll up a specific month (YYYY-MM). Default: every completed month.",
    )
    p_rollup_summary.add_argument(
        "--day",
        help="Roll up a specific UTC date (YYYY-MM-DD). Default: every completed day.",
    )
    p_rollup_summary.add_argument(
        "--all", action="store_true",
        help="Roll up every completed period for both monthly and daily tables.",
    )
    p_rollup_summary.add_argument(
        "--rebuild", action="store_true",
        help=(
            "Re-roll every existing airport-day from raw (idempotent "
            "DELETE+INSERT). Use after a migration that adds a new "
            "airport_daily_summary column — existing rows keep the default "
            "until re-rolled. Mutually exclusive with --all/--month/--day."
        ),
    )

    # rollup-daily-stats
    p_rollup_daily = subparsers.add_parser(
        "rollup-daily-stats",
        help="Roll up verification_scores into verification_daily_stats",
    )
    p_rollup_daily.add_argument(
        "--day",
        help="Roll up a specific UTC date (YYYY-MM-DD). Default: every pending day.",
    )
    p_rollup_daily.add_argument(
        "--rebuild", action="store_true",
        help="Re-roll every existing date (use after schema/aggregation changes).",
    )

    # digest
    p_digest = subparsers.add_parser(
        "digest",
        help="Preview or send the admin digest email",
    )
    p_digest.add_argument(
        "--period", choices=["24h", "7d"], default="24h",
        help="Time period (default: 24h)",
    )
    p_digest.add_argument(
        "--send", action="store_true",
        help="Actually send the email to ADMIN_EMAILS",
    )
    p_digest.add_argument(
        "--json", action="store_true",
        help="Output as JSON instead of plain text",
    )

    args = parser.parse_args()

    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "score":
        cmd_score(args)
    elif args.command == "backfill":
        cmd_backfill(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "discover":
        cmd_discover(args)
    elif args.command == "standalone":
        cmd_standalone(args)
    elif args.command == "rebuild-cache":
        cmd_rebuild_cache(args)
    elif args.command == "rollup-summary":
        cmd_rollup_summary(args)
    elif args.command == "rollup-daily-stats":
        cmd_rollup_daily_stats(args)
    elif args.command == "digest":
        cmd_digest(args)


if __name__ == "__main__":
    main()
