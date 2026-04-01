"""CLI for METAR/TAF verification system.

Usage:
    python -m weatherbrief.verify collect [--flight-id ID] [--corridor NM]
    python -m weatherbrief.verify export [--format csv|json] [--output FILE]
    python -m weatherbrief.verify stats [--model MODEL] [--icao ICAO]
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


def cmd_stats(args):
    """Show verification statistics."""
    SessionLocal = _init_db()
    db = SessionLocal()

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

        score_count = db.execute(
            select(func.count(VerificationScoreRow.id))
        ).scalar() or 0

        airport_count = db.execute(
            select(func.count(func.distinct(VerificationObservationRow.icao)))
        ).scalar() or 0

        flight_count = db.execute(
            select(func.count(func.distinct(FlightVerificationMapRow.flight_id)))
        ).scalar() or 0

        print(f"Verification Database Statistics")
        print(f"{'─' * 40}")
        print(f"Observations:    {obs_count:>8}")
        print(f"Scores:          {score_count:>8}")
        print(f"Unique airports: {airport_count:>8}")
        print(f"Flights tracked: {flight_count:>8}")

        if obs_count > 0:
            # Category distribution
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

    # stats
    p_stats = subparsers.add_parser("stats", help="Show verification statistics")

    args = parser.parse_args()

    if args.command == "collect":
        cmd_collect(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "stats":
        cmd_stats(args)


if __name__ == "__main__":
    main()
