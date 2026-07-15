#!/usr/bin/env python
"""Read-only dry-run of the #405 profile sparsify — reports the blast radius.

Runs the exact rule the ``079`` migration will apply (same
``profile_sparsify.sparsify_settings`` helper, same live catalog) against a
database WITHOUT writing anything, and prints per-profile + total counts. Use it
to reproduce/refresh the expected numbers against a production snapshot before
committing to the real write.

Usage::

    # against the current dev DB (DATABASE_URL from .env)
    python scripts/sparsify_profiles_dryrun.py

    # against a snapshot loaded into a local sqlite file
    python scripts/sparsify_profiles_dryrun.py --db sqlite:////tmp/prod_profiles.db

    # verbose: one line per touched profile
    python scripts/sparsify_profiles_dryrun.py -v

Deliberately takes a DB URL and touches nothing else — no server host, no
credentials, no environment assumptions baked in.
"""

from __future__ import annotations

import argparse
import json
import os

import sqlalchemy as sa

from weatherbrief.analysis.advisories.profile_sparsify import (
    build_param_defaults,
    sparsify_settings,
)
from weatherbrief.analysis.advisories.registry import get_catalog


def _default_db_url() -> str:
    """Same resolution as ``alembic/env.py``: ``DATABASE_URL`` (prod) else the
    dev sqlite fallback ``sqlite:///{DATA_DIR}/flyfun.db``. Keeps zero-arg usage
    working on a dev box that relies on the fallback (no ``DATABASE_URL`` set)."""
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

    param_defaults = build_param_defaults(get_catalog())
    engine = sa.create_engine(args.db)

    profiles_total = 0
    profiles_touched = 0
    totals = {
        "params_deleted": 0,
        "engine_deleted": 0,
        "cloud_method_dropped": 0,
        "cloud_method_rewritten": 0,
    }
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT id, settings_json FROM flight_profiles")
        ).fetchall()
        for row_id, raw in rows:
            profiles_total += 1
            settings = json.loads(raw) if raw else {}
            _, stats = sparsify_settings(settings, param_defaults)
            if not stats.touched:
                continue
            profiles_touched += 1
            totals["params_deleted"] += stats.params_deleted
            totals["engine_deleted"] += stats.engine_deleted
            totals["cloud_method_dropped"] += stats.cloud_method_dropped
            totals["cloud_method_rewritten"] += stats.cloud_method_rewritten
            if args.verbose:
                print(
                    f"  profile {row_id}: "
                    f"-{stats.params_deleted} params, "
                    f"-{stats.engine_deleted} engine, "
                    f"cloud_method drop={stats.cloud_method_dropped} "
                    f"rewrite={stats.cloud_method_rewritten}"
                )

    print(f"\nprofiles scanned : {profiles_total}")
    print(f"profiles touched : {profiles_touched}")
    print(f"params deleted   : {totals['params_deleted']}")
    print(f"engine deleted   : {totals['engine_deleted']}")
    print(f"cloud_method drop: {totals['cloud_method_dropped']}")
    print(f"cloud_method rewr: {totals['cloud_method_rewritten']}")


if __name__ == "__main__":
    main()
