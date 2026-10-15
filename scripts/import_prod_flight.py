#!/usr/bin/env python3
"""Copy ONE flight from production into the local dev setup (read-only on prod).

Brings over the flight's DB rows and its whole pack directory (packs, live
files, per-tick snapshots, forecast lanes) so the flight opens on the local
devserver and the debrief recap / single-tick replay work against real data:

    http://localhost:8000/briefing.html?flight=<flight_id>

What is copied, and how it is remapped (``remap_flight`` / ``remap_pack``):

* ``flights`` — ``user_id`` → the local user; ``profile_id`` / ``aircraft_id``
  / ``trip_id`` → NULL (prod-only rows; all three are nullable, SET NULL FKs);
  ``share_code`` → NULL (unique, prod's link is meaningless here);
  ``auto_refresh`` → off so the local scheduler never re-briefs the import.
* ``briefing_packs`` — prod ``id`` dropped (local autoincrement);
  ``artifact_path`` re-rooted to ``<DATA_DIR>/packs/<local user>/<flight>/<ts>``;
  ``integrity_hmac`` recomputed with the local key (it covers the path).
* ``flight_debriefs`` — copied as is when present (the recap reads it:
  a cancelled flight is recapped against the plan).

Not copied: subscriptions / briefing-seen (per-user), live_tick_timing and
live_delivery (prod latency telemetry, no FK, not read by the page).

Prod access: ``scripts/ops/hosts.py`` → ssh, ``docker compose exec -T
weatherbrief`` — rows dumped by a Python snippet on stdin, the flight
directory streamed as a tar over the same pipe.

Usage:
    python scripts/import_prod_flight.py <flight_id> [--user <local user id>] [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import re
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "scripts" / "ops"))
from hosts import server_values  # noqa: E402

CONTAINER = "weatherbrief"
CONTAINER_PACKS = "/app/data/packs"

# Runs inside the prod container: dump the flight's rows as JSON + dir size.
# Generic SELECT * so a prod/local schema skew does not break the dump; the
# local side keeps only the columns its models know.
_REMOTE_DUMP = r'''
import json, os, sys
from datetime import date, datetime
from decimal import Decimal
import flyfun_common.db as fdb
from sqlalchemy import text
fid = FLIGHT_ID
def enc(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v
def rows(c, sql):
    return [{k: enc(v) for k, v in r.items()} for r in c.execute(text(sql), {"f": fid}).mappings()]
out = {}
with fdb.get_engine().connect() as c:
    out["flights"] = rows(c, "SELECT * FROM flights WHERE id = :f")
    out["briefing_packs"] = rows(c, "SELECT * FROM briefing_packs WHERE flight_id = :f ORDER BY fetch_timestamp")
    out["flight_debriefs"] = rows(c, "SELECT * FROM flight_debriefs WHERE flight_id = :f")
size = files = 0
if out["flights"]:
    d = os.path.join("/app/data/packs", out["flights"][0]["user_id"], fid)
    out["dir_exists"] = os.path.isdir(d)
    for root, _, names in os.walk(d):
        for n in names:
            files += 1
            size += os.path.getsize(os.path.join(root, n))
out["dir_bytes"], out["dir_files"] = size, files
print("JSON_START")
print(json.dumps(out))
print("JSON_END")
'''


# ---------------------------------------------------------------------------
# Pure remapping (tested in tests/test_import_prod_flight.py)
# ---------------------------------------------------------------------------


def parse_dt(value) -> datetime | None:
    """ISO string from the prod dump → aware UTC datetime (prod stores naive UTC)."""
    if value is None or isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def coerce_row(raw: dict, columns: dict[str, type | None]) -> tuple[dict, list[str]]:
    """Keep only the local model's columns, typed for SQLAlchemy.

    ``columns`` maps column name → python type (``datetime`` / ``bool`` / other
    or None). Returns (row, dropped prod-only column names).
    """
    out, dropped = {}, []
    for k, v in raw.items():
        if k not in columns:
            dropped.append(k)
            continue
        pytype = columns[k]
        if v is not None and pytype is datetime:
            v = parse_dt(v)
        elif v is not None and pytype is bool:
            v = bool(v)
        out[k] = v
    return out, dropped


def remap_flight(row: dict, local_user_id: str) -> dict:
    """The prod ``flights`` row rewritten for the local owner."""
    out = dict(row)
    out["user_id"] = local_user_id
    out["profile_id"] = None
    out["aircraft_id"] = None
    out["trip_id"] = None
    out["share_code"] = None
    out["auto_refresh"] = False
    out["last_auto_refresh_at"] = None
    return out


def local_artifact_path(prod_path: str | None, packs_root: Path, local_user_id: str, flight_id: str) -> str:
    """Prod ``/app/data/packs/<prod user>/<flight>/<ts>`` → local equivalent."""
    if not prod_path:
        return ""
    return str(packs_root / local_user_id / flight_id / Path(prod_path).name)


def remap_pack(row: dict, packs_root: Path, local_user_id: str) -> dict:
    """A prod ``briefing_packs`` row for local insert: no id, local path, no HMAC
    (recomputed after insert with the local key)."""
    out = dict(row)
    out.pop("id", None)
    out["artifact_path"] = local_artifact_path(
        row.get("artifact_path"), packs_root, local_user_id, row["flight_id"]
    )
    out["integrity_hmac"] = None
    return out


# ---------------------------------------------------------------------------
# Prod I/O
# ---------------------------------------------------------------------------


def _prod() -> tuple[str, str]:
    v = server_values("SERVER_SSH", "SERVER_PROJECT_DIR")
    return v["SERVER_SSH"], v["SERVER_PROJECT_DIR"]


def dump_prod(flight_id: str) -> dict:
    ssh, project = _prod()
    script = _REMOTE_DUMP.replace("FLIGHT_ID", json.dumps(flight_id))
    remote = f"cd {project} && docker compose exec -T {CONTAINER} python -"
    res = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=15", ssh, remote],
        input=script, capture_output=True, text=True, timeout=180,
    )
    if res.returncode != 0:
        sys.exit(f"prod dump failed ({res.returncode}):\n{res.stderr[-2000:]}")
    out = res.stdout
    try:
        body = out.split("JSON_START", 1)[1].split("JSON_END", 1)[0]
    except IndexError:
        sys.exit(f"prod dump: no JSON in output:\n{out[-2000:]}\n{res.stderr[-2000:]}")
    return json.loads(body)


def stream_prod_dir(prod_user_id: str, flight_id: str, dest_parent: Path) -> None:
    """tar the flight dir inside the container, extract under ``dest_parent``."""
    ssh, project = _prod()
    remote = (
        f"cd {project} && docker compose exec -T {CONTAINER} "
        f"tar -C {CONTAINER_PACKS}/{prod_user_id} -czf - {flight_id}"
    )
    proc = subprocess.Popen(
        ["ssh", "-o", "ConnectTimeout=15", ssh, remote],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdout is not None
    with tarfile.open(fileobj=proc.stdout, mode="r|gz") as tf:
        # "data" filter: no absolute paths, no escaping links, no devices.
        tf.extractall(dest_parent, filter="data")
    err = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        sys.exit(f"prod tar stream failed:\n{err[-2000:]}")


# ---------------------------------------------------------------------------
# Local side
# ---------------------------------------------------------------------------


def _columns(model) -> dict[str, type | None]:
    out = {}
    for col in model.__table__.columns:
        try:
            out[col.name] = col.type.python_type
        except NotImplementedError:
            out[col.name] = None
    return out


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def report_live_window(flight_row, flight_dir: Path) -> None:
    from weatherbrief.tasks.live_layer import load_progress_events
    from weatherbrief.tasks.live_tick import in_live_window, live_window_end, live_window_hours

    now = datetime.now(timezone.utc)
    before, after = live_window_hours()
    events = load_progress_events(flight_dir)
    dep = parse_dt(flight_row.departure_time)
    end = live_window_end(dep, flight_row.flight_duration_hours, after, events, now)
    if in_live_window(dep, flight_row.flight_duration_hours, now, before, after, events):
        print(f"WARNING: the live window is still OPEN (closes {end.isoformat()}): the local "
              "live tick will append to the imported live files until then.")
    elif now < end:
        print(f"Note: live window not open yet (departure {dep.isoformat()}); the local live "
              "tick will start writing to this flight's live files when it opens.")
    else:
        print(f"Live window closed at {end.isoformat()}: the local live tick leaves it alone.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy one prod flight into the local dev DB + DATA_DIR")
    ap.add_argument("flight_id")
    ap.add_argument("--user", help="Local owner (default: the dev user)")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be copied, write nothing")
    ap.add_argument("--force", action="store_true", help="Replace the flight if it exists locally")
    args = ap.parse_args()

    import flyfun_common.db as fdb
    from flyfun_common.db.engine import ensure_dev_user, is_dev_mode
    from sqlalchemy.orm import Session

    from weatherbrief.db.models import BriefingPackRow, FlightDebriefRow, FlightRow
    from weatherbrief.storage.flights import _compute_pack_hmac, _data_dir

    if not is_dev_mode():
        sys.exit("Refusing: ENVIRONMENT is production — this script only writes to the dev setup.")

    flight_id = args.flight_id
    # It names local paths (and the --force rmtree): plain ids only.
    if not re.fullmatch(r"[A-Za-z0-9_-]+", flight_id):
        sys.exit(f"Not a flight id: {flight_id!r}")
    local_user = args.user or fdb.DEV_USER_ID
    packs_root = _data_dir().resolve() / "packs"
    local_dir = packs_root / local_user / flight_id

    print(f"Dumping {flight_id} from prod…")
    dump = dump_prod(flight_id)
    if not dump["flights"]:
        sys.exit(f"Flight {flight_id} not found on prod.")
    prod_flight = dump["flights"][0]
    prod_user = prod_flight["user_id"]

    engine = fdb.get_engine()
    print(f"Local DB: {engine.url}  DATA_DIR packs: {packs_root}")
    print(f"  flights: 1  briefing_packs: {len(dump['briefing_packs'])}  "
          f"flight_debriefs: {len(dump['flight_debriefs'])}")
    print(f"  pack dir: {dump['dir_files']} files, {_fmt_bytes(dump['dir_bytes'])}"
          f"{'' if dump.get('dir_exists') else '  (MISSING on prod)'}")
    print(f"  owner: prod {prod_user} → local {local_user}")
    print(f"  target dir: {local_dir}")

    with Session(bind=engine) as s:
        if local_user == fdb.DEV_USER_ID:
            ensure_dev_user(s)
            s.commit()
        from flyfun_common.db.models import UserRow

        if s.get(UserRow, local_user) is None:
            sys.exit(f"Local user {local_user!r} does not exist.")
        existing = s.get(FlightRow, flight_id)
        if (existing is not None or local_dir.exists()) and not args.force:
            sys.exit(f"Flight already exists locally (row={existing is not None}, "
                     f"dir={local_dir.exists()}); pass --force to replace it.")
        if args.dry_run:
            print("Dry run: nothing written.")
            return

        # Rows first built (fail before touching anything on a schema problem).
        fcols, pcols, dcols = _columns(FlightRow), _columns(BriefingPackRow), _columns(FlightDebriefRow)
        frow, dropped = coerce_row(remap_flight(prod_flight, local_user), fcols)
        dropped_all = set(dropped)
        packs = []
        for raw in dump["briefing_packs"]:
            prow, dropped = coerce_row(remap_pack(raw, packs_root, local_user), pcols)
            dropped_all |= set(dropped)
            packs.append(prow)
        debriefs = []
        for raw in dump["flight_debriefs"]:
            drow, dropped = coerce_row(raw, dcols)
            dropped_all |= set(dropped)
            debriefs.append(drow)
        if dropped_all:
            print(f"  prod-only columns dropped: {sorted(dropped_all)}")

        # Files: stream into a staging dir, then swap in.
        staging = packs_root / local_user / f".import-{flight_id}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        print("Streaming pack directory from prod…")
        stream_prod_dir(prod_user, flight_id, staging)
        if existing is not None:
            s.delete(existing)  # cascades packs + debrief
            s.flush()
        if local_dir.exists():
            shutil.rmtree(local_dir)
        (staging / flight_id).rename(local_dir)
        shutil.rmtree(staging)

        flight = FlightRow(**frow)
        s.add(flight)
        s.flush()
        for prow in packs:
            row = BriefingPackRow(**prow)
            row.integrity_hmac = _compute_pack_hmac(row)
            s.add(row)
        for drow in debriefs:
            s.add(FlightDebriefRow(**drow))
        s.commit()

        n_files = sum(1 for p in local_dir.rglob("*") if p.is_file())
        print(f"Imported: 1 flight, {len(packs)} packs, {len(debriefs)} debriefs, {n_files} files.")
        missing = [p["artifact_path"] for p in packs if p["artifact_path"] and not Path(p["artifact_path"]).is_dir()]
        if missing:
            print(f"WARNING: {len(missing)} pack dirs missing on disk: {missing}")
        report_live_window(s.get(FlightRow, flight_id), local_dir)

    print(f"\nOpen: http://localhost:8000/briefing.html?flight={flight_id}  (main's devserver; worktrees use 8001+)")


if __name__ == "__main__":
    main()
