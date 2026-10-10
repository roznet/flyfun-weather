#!/usr/bin/env python3
"""Pin flights so retention never strips or deletes their packs.

    venv/bin/python scripts/ops/pin_flight.py --list [--local]
    venv/bin/python scripts/ops/pin_flight.py FLIGHT_ID... [--unpin] [--local]

Sets ``flights.retention_pinned`` (migration 101): a pinned flight keeps every
pack in full — cross-section, forecasts, Skew-T, live layer — past the T1/T2
windows of ``tasks/retention.py``. For flights a talk or write-up links to
(``talks/*/talk.json`` lists them; its build checks they are pinned).

Prod by default (runs inside the weatherbrief container over ssh); ``--local``
writes the dev DB named by ``.env``. Prints one line per flight and the
pinned list. A pin does not stop a deliberate delete by the owner.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]

# argv[1]: JSON {"ids": [...], "pin": bool} or "list". Prints one JSON document.
PIN_SCRIPT = r'''
import json, sys
from sqlalchemy import select
from weatherbrief.db import SessionLocal, init_shared_db
from weatherbrief.db.models import FlightRow

init_shared_db()
db = SessionLocal()
changed = []
if sys.argv[1] != "list":
    req = json.loads(sys.argv[1])
    for fid in req["ids"]:
        row = db.get(FlightRow, fid)
        if row is None:
            changed.append({"id": fid, "error": "not found"})
            continue
        before = bool(row.retention_pinned)
        row.retention_pinned = req["pin"]
        changed.append({"id": fid, "was": before, "now": req["pin"]})
    db.commit()
pinned = db.execute(select(FlightRow.id).where(FlightRow.retention_pinned.is_(True))
                    .order_by(FlightRow.departure_time)).scalars().all()
print(json.dumps({"changed": changed, "pinned": list(pinned)}))
'''


def run(arg: str, local: bool) -> dict:
    if local:
        cmd = [sys.executable, "-", arg]
        r = subprocess.run(cmd, input=PIN_SCRIPT, capture_output=True, text=True,
                           timeout=120, cwd=REPO)
    else:
        ssh = hosts.server_values("SERVER_SSH")["SERVER_SSH"]
        r = subprocess.run([*SSH, ssh, "docker exec -i weatherbrief python - " + shlex.quote(arg)],
                           input=PIN_SCRIPT, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise SystemExit(f"pin failed: {r.stderr.strip()[-600:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flights", nargs="*", help="flight ids")
    ap.add_argument("--unpin", action="store_true", help="remove the pin instead")
    ap.add_argument("--list", action="store_true", help="only list pinned flights")
    ap.add_argument("--local", action="store_true", help="dev DB instead of prod")
    a = ap.parse_args(argv)

    if a.local:
        from dotenv import load_dotenv

        load_dotenv(REPO / ".env")
    arg = "list" if a.list or not a.flights else json.dumps({"ids": a.flights, "pin": not a.unpin})
    doc = run(arg, a.local)
    where = "local" if a.local else "prod"
    bad = 0
    for c in doc["changed"]:
        if "error" in c:
            bad += 1
            print(f"problem  {c['id']}  ({c['error']} on {where})")
        else:
            state = "pinned" if c["now"] else "unpinned"
            print(f"ok       {c['id']}  ({state}{', unchanged' if c['was'] == c['now'] else ''})")
    print(f"{len(doc['pinned'])} pinned on {where}:")
    for fid in doc["pinned"]:
        print(f"  {fid}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
