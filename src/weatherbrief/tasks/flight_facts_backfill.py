"""One-off backfill of the past-flights record (#1031).

    python -m weatherbrief.tasks.flight_facts_backfill [--dry-run] [--limit N]

Runs ``storage/flight_facts.update_flight_facts`` for every flight: route
ends and line (newest readable pack, else the airports DB named by
``AIRPORTS_DB``, else the codes alone), forecast tags wherever the chosen
pack still exists (``tags_recorded=False`` otherwise), the outcome; flights
whose departure has passed are frozen. Idempotent: a second run rewrites the
same values and never un-records tags already stored. ``--dry-run`` rolls
back and only counts. Prints one JSON summary line. Wrapped from the Mac by
``scripts/ops/backfill_flight_facts.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightRow

logger = logging.getLogger(__name__)


def backfill(db: Session, *, dry_run: bool = False, limit: int | None = None,
             now: datetime | None = None, airports_db_path: str | None = None) -> dict:
    """Refresh every flight's record; commit per flight unless ``dry_run``."""
    from weatherbrief.storage.flight_facts import update_flight_facts

    now = now or datetime.now(timezone.utc)
    ids = db.execute(select(FlightRow.id).order_by(FlightRow.departure_time.desc())).scalars().all()
    if limit:
        ids = ids[:limit]
    stats = {"flights": len(ids), "recorded": 0, "not_recorded": 0, "with_line": 0, "frozen": 0,
             "errors": 0, "dry_run": dry_run}
    for fid in ids:
        try:
            row = update_flight_facts(db, fid, now=now, freeze=True, airports_db_path=airports_db_path)
            if row is None:
                continue
            stats["recorded" if row.tags_recorded else "not_recorded"] += 1
            stats["with_line"] += row.route_line_json is not None
            stats["frozen"] += row.forecast_frozen_at is not None
            if dry_run:
                db.rollback()
            else:
                db.commit()
        except Exception:
            db.rollback()
            stats["errors"] += 1
            logger.warning("Flight facts backfill failed for %s", fid, exc_info=True)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill flight_facts (#1031)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    from weatherbrief.db import SessionLocal, init_shared_db

    init_shared_db()
    db = SessionLocal()
    try:
        print(json.dumps(backfill(db, dry_run=a.dry_run, limit=a.limit)))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
