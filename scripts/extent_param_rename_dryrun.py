#!/usr/bin/env python
"""Read-only dry-run of the #571 extent-parameter rename — reports the blast radius.

Runs the exact rule the ``093`` migration will apply (same
``extent_param_migration.rename_extent_params`` helper) against a database
WITHOUT writing anything, and prints per-profile + total counts. Use it to
reproduce the expected numbers against a production snapshot before committing to
the real write.

Preferred over the in-migration ``EXTENT_RENAME_DRY_RUN=1``, which aborts the
whole ``alembic upgrade`` invocation to roll back; this touches nothing.

Usage::

    # against the current dev DB (DATABASE_URL from .env)
    python scripts/extent_param_rename_dryrun.py

    # against a snapshot loaded into a local sqlite file
    python scripts/extent_param_rename_dryrun.py --db sqlite:////tmp/prod_profiles.db

    # verbose: one line per touched profile
    python scripts/extent_param_rename_dryrun.py -v

Deliberately takes a DB URL and touches nothing else — no server host, no
credentials, no environment assumptions baked in.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter

import sqlalchemy as sa

from weatherbrief.analysis.advisories.extent_param_migration import (
    rename_extent_params,
)


def _default_db_url() -> str:
    """Same resolution as ``alembic/env.py``: ``DATABASE_URL`` (prod) else the
    dev sqlite fallback ``sqlite:///{DATA_DIR}/flyfun.db``."""
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
        "-v", "--verbose", action="store_true", help="one line per touched profile"
    )
    args = ap.parse_args()

    engine = sa.create_engine(args.db)

    profiles_total = 0
    profiles_touched = 0
    totals = {"renamed": 0, "inverted": 0, "dropped_shadowed": 0}
    by_advisory: Counter[str] = Counter()
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT id, settings_json FROM flight_profiles")
        ).fetchall()
        for row_id, raw in rows:
            profiles_total += 1
            settings = json.loads(raw) if raw else {}
            _, stats = rename_extent_params(settings)
            if not stats.touched:
                continue
            profiles_touched += 1
            totals["renamed"] += stats.renamed
            totals["inverted"] += stats.inverted
            totals["dropped_shadowed"] += stats.dropped_shadowed
            by_advisory.update(stats.per_advisory)
            if args.verbose:
                detail = ", ".join(
                    f"{adv}:{n}" for adv, n in sorted(stats.per_advisory.items())
                )
                print(
                    f"  profile {row_id}: {stats.renamed} renamed ({detail}), "
                    f"{stats.inverted} inverted, {stats.dropped_shadowed} shadowed"
                )

    print(f"\nprofiles scanned : {profiles_total}")
    print(f"profiles touched : {profiles_touched}")
    print(f"keys renamed     : {totals['renamed']}")
    print(f"values inverted  : {totals['inverted']}  (fiki_icing polarity flip)")
    print(f"shadowed dropped : {totals['dropped_shadowed']}")
    if by_advisory:
        print("\nby advisory:")
        for adv, n in sorted(by_advisory.items(), key=lambda kv: -kv[1]):
            print(f"  {adv:<22} {n}")


if __name__ == "__main__":
    main()
