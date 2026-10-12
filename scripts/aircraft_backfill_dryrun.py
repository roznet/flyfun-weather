#!/usr/bin/env python
"""Read-only dry-run of migration 103 (#777) — default aircraft + speed move.

Runs the exact plan the ``103`` migration applies
(``weatherbrief.storage.aircraft_backfill.plan_backfill``) against a database
WITHOUT writing anything, and prints the counts plus every flight whose cruise
IAS would change. Use it against a production snapshot before deploying.

Usage::

    # against the current dev DB (DATABASE_URL from .env)
    python scripts/aircraft_backfill_dryrun.py

    # against a snapshot loaded into a local sqlite file, listing every change
    python scripts/aircraft_backfill_dryrun.py --db sqlite:////tmp/prod.db --limit 0

Takes a DB URL and touches nothing else.
"""

from __future__ import annotations

import argparse
import os

import sqlalchemy as sa

from weatherbrief.storage.aircraft_backfill import format_report, plan_backfill


def _default_db_url() -> str:
    """Same resolution as ``alembic/env.py``: ``DATABASE_URL`` else the dev sqlite."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    data_dir = os.environ.get("DATA_DIR", "data")
    return f"sqlite:///{data_dir}/flyfun.db"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--db",
        default=_default_db_url(),
        help="SQLAlchemy DB URL (default: $DATABASE_URL, else sqlite:///{DATA_DIR}/flyfun.db)",
    )
    ap.add_argument(
        "--limit", type=int, default=20,
        help="flights with a changed cruise IAS to list (0 = all)",
    )
    args = ap.parse_args()

    engine = sa.create_engine(args.db)
    with engine.connect() as conn:
        plan = plan_backfill(conn)
    print(format_report(plan, limit=args.limit or len(plan.before_ias)))


if __name__ == "__main__":
    main()
