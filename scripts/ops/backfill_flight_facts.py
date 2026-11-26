#!/usr/bin/env python3
"""Backfill the past-flights record (``flight_facts``, #1031) on prod or the dev DB.

    venv/bin/python scripts/ops/backfill_flight_facts.py [--dry-run] [--limit N] [--local]

Runs ``python -m weatherbrief.tasks.flight_facts_backfill`` inside the
weatherbrief container over ssh (prod, the default) or locally against the
dev DB named by ``.env`` (``--local``). Idempotent; run it once after the
migration that creates the table (123) is deployed. Prints the summary:
flights seen, how many have forecast tags recorded / not recorded (their
packs were already gone), how many got a route line, errors.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
MODULE = "weatherbrief.tasks.flight_facts_backfill"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    ap.add_argument("--limit", type=int, default=None, help="newest N flights only")
    ap.add_argument("--local", action="store_true", help="dev DB instead of prod")
    a = ap.parse_args(argv)
    args = (["--dry-run"] if a.dry_run else []) + (["--limit", str(a.limit)] if a.limit else [])
    if a.local:
        from weatherbrief.envfiles import load_env

        load_env(REPO)
        r = subprocess.run([sys.executable, "-m", MODULE, *args], cwd=REPO, timeout=3600)
    else:
        ssh = hosts.server_values("SERVER_SSH")["SERVER_SSH"]
        r = subprocess.run([*SSH, ssh, "docker exec weatherbrief python -m " + MODULE + " " + " ".join(args)],
                           timeout=3600)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
