"""CLI for METAR/TAF verification system.

Usage:
    python -m weatherbrief.verify collect [--flight-id ID] [--corridor NM]
    python -m weatherbrief.verify export [--format csv|json] [--output FILE]
    python -m weatherbrief.verify stats [--model MODEL] [--icao ICAO] [--source SOURCE]
    python -m weatherbrief.verify discover [--prefixes LF,ED,...]
    python -m weatherbrief.verify standalone [--once]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import sys
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


def cmd_standalone(args):
    """Run a standalone verification cycle."""
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

    from weatherbrief.tasks.standalone_verification import run_standalone_cycle

    result = run_standalone_cycle(watchlist, airports_db)
    print(f"\nStandalone verification cycle complete:")
    print(f"  Models fetched: {result.get('models_fetched', 0)}")
    print(f"  Snapshots stored: {result.get('snapshots_stored', 0)}")
    print(f"  Observations stored: {result.get('observations_stored', 0)}")
    print(f"  Scores created: {result.get('scores_created', 0)}")
    print(f"  Duration: {result.get('duration_ms', 0)}ms")


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


if __name__ == "__main__":
    main()
