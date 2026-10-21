#!/usr/bin/env python3
"""Copy prod flights into the local dev DB + DATA_DIR, to test on real data.

    venv/bin/python scripts/ops/import_flight.py FLIGHT_ID_OR_URL... [--user ID|EMAIL] [--overwrite]
    venv/bin/python scripts/ops/import_flight.py --list            # prod flights in their live window
    venv/bin/python scripts/ops/import_flight.py --live            # import all of those
    venv/bin/python scripts/ops/import_flight.py FLIGHT_ID --highlight-at 14:20Z
    venv/bin/python scripts/ops/import_flight.py FLIGHT_ID --dry-run | --no-freeze
    venv/bin/python scripts/ops/import_flight.py FLIGHT_ID --with-aircraft

The one way to bring a prod flight to the devserver (debrief recap / tick
replay, live-layer display, any UI or iOS check on a real briefing). Accepts a
bare id or a ``briefing.html?flight=ID`` URL. Read-only on prod; refuses to run
when ENVIRONMENT is production. Local data is the owner's prod data: never
commit it.

What is copied, and how it is remapped (``remap_flight`` / ``remap_pack``):

* ``flights`` — same id; ``user_id`` → the local user (default the dev-login
  account); ``profile_id`` / ``aircraft_id`` / ``trip_id`` / ``share_code`` →
  NULL (prod-only rows / unique); private; auto-refresh off so the local
  scheduler never re-briefs it.
* ``briefing_packs`` — prod ``id`` dropped; ``artifact_path`` re-rooted to
  ``<DATA_DIR>/packs/<local user>/<flight>/<ts>``; ``integrity_hmac``
  recomputed with the local key (it covers the path).
* ``flight_debriefs`` — as is (the recap reads it).
* ``user_aircraft`` (only with ``--with-aircraft``) — the aircraft the flight
  resolves to on prod (its own, else the owner's default), copied for the local
  owner without tail number or nickname, and the flight pointed at it, so the
  icing card grades with the same FIKI / non-FIKI column (#827). A re-import
  reuses the copy (matched on the nickname ``import:<prod id>``).
* The whole flight dir (packs, live files, per-tick snapshots, forecast lanes),
  tar-streamed out of the container into a staging dir, then swapped in.

Rows are a generic ``SELECT *`` coerced to the local models' columns, so a
prod/local schema skew drops prod-only columns (listed) instead of failing.
Not copied: subscriptions / briefing-seen (per-user), live_tick_timing and
live_delivery (prod telemetry).

**Frozen by default.** The flight dir gets ``live_layer.LIVE_FROZEN_FILE``
(written before the rows exist), which the live tick and the flight-day brief
skip, so a running devserver neither overwrites prod's layer nor pays for a
local highlight; a ↻ press in a client still refreshes it — re-import with
``--overwrite`` to restore. Packs newer than the live layer's pack are left out
of a frozen import (``/live`` reads the newest pack and the layer is relative
to one). ``--no-freeze`` keeps every pack and lets the local tick run on the
flight while its live window is open (to test the tick itself).

**Take live flights while they are live.** A finished flight's ``live.json``
has no highlight (none is written after planned arrival), so the layer as a
pilot saw it mid-flight only exists during the window. ``--highlight-at
HH:MMZ`` patches the last line *written* by then from ``live_highlights.jsonl``
onto the final layer — text only; ribbon and nutshell stay the final tick's.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
CONTAINER = "weatherbrief"
CONTAINER_PACKS = "/app/data/packs"

# Runs inside the prod container (`docker exec -i weatherbrief python - ARG`).
# ARG "list": flights in their live window. Otherwise a JSON list of flight ids:
# each flight's rows (generic SELECT *, JSON-safe), its dir size and the pack
# its live layer is relative to.
_REMOTE = r'''
import json, os, sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

if sys.argv[1] == "list":
    from weatherbrief.db import SessionLocal, init_shared_db
    from weatherbrief.storage.flights import list_packs, _resolve_artifact_path
    from weatherbrief.tasks.live_layer import load_live
    from weatherbrief.tasks.live_tick import _as_utc, find_live_flights
    init_shared_db()
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    out = []
    for f in find_live_flights(db, now):
        dep = _as_utc(f.departure_time)
        arr = dep + timedelta(hours=f.flight_duration_hours or 0)
        packs = list_packs(db, f.id)
        layer = load_live(Path(_resolve_artifact_path(packs[0].artifact_path)).parent) if packs else None
        hl = layer.glance.highlight if layer and layer.glance else None
        out.append({"id": f.id, "dep": dep.isoformat(), "arr": arr.isoformat(),
                    "phase": "airborne" if dep <= now <= arr else ("before" if now < dep else "after"),
                    "highlight": hl.text if hl else None})
    print("JSON_START"); print(json.dumps({"now": now.isoformat(), "flights": out})); print("JSON_END")
    sys.exit(0)

import flyfun_common.db as fdb
from sqlalchemy import text
from weatherbrief.tasks.live_layer import load_live

def enc(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v

def rows(c, sql, fid):
    return [{k: enc(v) for k, v in r.items()} for r in c.execute(text(sql), {"f": fid}).mappings()]

result = []
with fdb.get_engine().connect() as c:
    for fid in json.loads(sys.argv[1]):
        out = {"id": fid}
        out["flights"] = rows(c, "SELECT * FROM flights WHERE id = :f", fid)
        out["briefing_packs"] = rows(c, "SELECT * FROM briefing_packs WHERE flight_id = :f ORDER BY fetch_timestamp", fid)
        out["flight_debriefs"] = rows(c, "SELECT * FROM flight_debriefs WHERE flight_id = :f", fid)
        out["aircraft"] = rows(c, "SELECT a.* FROM user_aircraft a JOIN flights f ON f.id = :f "
                                  "WHERE a.id = f.aircraft_id "
                                  "OR (f.aircraft_id IS NULL AND a.user_id = f.user_id AND a.is_default = 1) "
                                  "ORDER BY a.id = f.aircraft_id DESC LIMIT 1", fid)
        size = files = 0
        out["dir_exists"], out["live_pack"] = False, None
        if out["flights"]:
            d = os.path.join("/app/data/packs", out["flights"][0]["user_id"], fid)
            out["dir_exists"] = os.path.isdir(d)
            for root, _, names in os.walk(d):
                for n in names:
                    files += 1
                    size += os.path.getsize(os.path.join(root, n))
            try:
                layer = load_live(Path(d))
                out["live_pack"] = layer.pack_dir_name if layer else None
            except Exception as e:  # a broken live.json must not block the import
                out["live_error"] = repr(e)[:200]
        out["dir_bytes"], out["dir_files"] = size, files
        result.append(out)
print("JSON_START")
print(json.dumps(result))
print("JSON_END")
'''


def say(state: str, what: str, evidence: str = "") -> None:
    print(f"{state:<8} {what}" + (f"  ({evidence})" if evidence else ""), file=sys.stderr)


# ---------------------------------------------------------------------------
# Pure helpers (tested in tests/test_import_flight.py)
# ---------------------------------------------------------------------------


def parse_flight_ref(ref: str) -> str:
    """A bare flight id, or the ``flight=`` of a briefing URL. Plain ids only:
    the id names local paths (and the --overwrite rmtree)."""
    if "flight=" in ref:
        ref = urllib.parse.parse_qs(urllib.parse.urlparse(ref).query).get("flight", [""])[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", ref):
        raise ValueError(f"Not a flight id: {ref!r}")
    return ref


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
    out["private"] = True
    out["auto_refresh"] = False
    out["auto_refresh_hour"] = None
    out["last_auto_refresh_at"] = None
    return out


#: Aircraft columns carried over by ``--with-aircraft``: what the engine grades
#: with, nothing that identifies the owner's machine.
AIRCRAFT_FIELDS = ("icao_type", "is_ifr", "is_fiki", "cruise_speed_kt", "ceiling_ft", "is_placeholder")


def local_aircraft_id(s, prod_aircraft: dict, local_user_id: str) -> int:
    """The local copy of a prod aircraft for ``local_user_id``, created on first use."""
    from sqlalchemy import select

    from weatherbrief.db.models import UserAircraftRow

    tag = f"import:{prod_aircraft['id']}"
    row = s.execute(select(UserAircraftRow).where(
        UserAircraftRow.user_id == local_user_id, UserAircraftRow.nickname == tag,
    )).scalars().first()
    fields = {k: (bool(v) if k.startswith("is_") else v)
              for k, v in prod_aircraft.items() if k in AIRCRAFT_FIELDS}
    if row is None:
        row = UserAircraftRow(user_id=local_user_id, nickname=tag, is_default=False, **fields)
        s.add(row)
    else:
        for k, v in fields.items():
            setattr(row, k, v)
    s.flush()
    return row.id


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


def packs_after_live(pack_rows: list[dict], live_pack: str | None) -> set[str]:
    """Pack dir names newer than the live layer's pack — left out of a frozen
    import, since ``/live`` reads the newest pack and would then serve null."""
    if live_pack is None:
        return set()
    names = {Path(p["artifact_path"]).name for p in pack_rows if p.get("artifact_path")}
    return {n for n in names if n > live_pack}


def highlight_at(log: Path, at: str) -> dict | None:
    """The last highlight written at or before HH:MMZ, from ``live_highlights.jsonl``."""
    hh, mm = at.rstrip("Zz").split(":")
    best = None
    for line in log.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("outcome") != "written" or not rec.get("text"):
            continue
        t = datetime.fromisoformat(rec["at"])
        if (t.hour, t.minute) <= (int(hh), int(mm)):
            best = rec
    return best


# ---------------------------------------------------------------------------
# Prod I/O
# ---------------------------------------------------------------------------


def prod(arg: str):
    ssh = hosts.server_values("SERVER_SSH")["SERVER_SSH"]
    r = subprocess.run([*SSH, ssh, f"docker exec -i {CONTAINER} python - '{arg}'"],
                       input=_REMOTE, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        sys.exit(f"prod export failed ({r.returncode}):\n{r.stderr[-2000:]}")
    try:
        body = r.stdout.split("JSON_START", 1)[1].split("JSON_END", 1)[0]
    except IndexError:
        sys.exit(f"prod export: no JSON in output:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return json.loads(body)


def stream_prod_dir(prod_user_id: str, flight_id: str, dest_parent: Path) -> None:
    """tar the flight dir inside the container, extract under ``dest_parent``."""
    ssh = hosts.server_values("SERVER_SSH")["SERVER_SSH"]
    remote = f"docker exec {CONTAINER} tar -C {CONTAINER_PACKS}/{prod_user_id} -czf - {flight_id}"
    proc = subprocess.Popen([*SSH, ssh, remote], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    with tarfile.open(fileobj=proc.stdout, mode="r|gz") as tf:
        # "data" filter: no absolute paths, no escaping links, no devices.
        tf.extractall(dest_parent, filter="data")
    err = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        sys.exit(f"prod tar stream failed:\n{err[-2000:]}")


def cmd_list() -> list[dict]:
    doc = prod("list")
    say("ok", f"prod at {doc['now'][:16]}Z", f"{len(doc['flights'])} flight(s) in their live window")
    for f in doc["flights"]:
        print(f"{f['id']:<45} {f['phase']:<9} dep {f['dep'][11:16]}Z arr {f['arr'][11:16]}Z  "
              f"{(f['highlight'] or '(no highlight)')[:80]}")
    return doc["flights"]


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


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def report_live_window(flight_row, flight_dir: Path, frozen: bool) -> None:
    if frozen:
        print("  frozen: the local live tick leaves this flight alone (--no-freeze to let it run).")
        return
    from weatherbrief.tasks.live_layer import load_progress_events
    from weatherbrief.tasks.live_tick import in_live_window, live_window_end, live_window_hours

    now = datetime.now(timezone.utc)
    before, after = live_window_hours()
    events = load_progress_events(flight_dir)
    dep = parse_dt(flight_row.departure_time)
    end = live_window_end(dep, flight_row.flight_duration_hours, after, events, now)
    if in_live_window(dep, flight_row.flight_duration_hours, now, before, after, events):
        print(f"  WARNING: not frozen and the live window is OPEN (closes {end.isoformat()}): the "
              "local live tick will append to the imported live files until then.")
    elif now < end:
        print(f"  not frozen: the local live tick starts writing when the window opens "
              f"(departure {dep.isoformat()}).")
    else:
        print(f"  live window closed at {end.isoformat()}: the local live tick leaves it alone.")


def import_one(s, dump: dict, user_id: str, packs_root: Path, args) -> bool:
    from weatherbrief.db.models import BriefingPackRow, FlightDebriefRow, FlightRow
    from weatherbrief.storage.flights import _compute_pack_hmac

    fid = dump["id"]
    if not dump["flights"]:
        say("problem", fid, "not found on prod")
        return False
    prod_flight = dump["flights"][0]
    prod_user = prod_flight["user_id"]
    local_dir = packs_root / user_id / fid
    freeze = not args.no_freeze
    skip = packs_after_live(dump["briefing_packs"], dump["live_pack"]) if freeze else set()

    print(f"{fid}")
    print(f"  rows: briefing_packs {len(dump['briefing_packs'])}, flight_debriefs {len(dump['flight_debriefs'])}")
    print(f"  pack dir: {dump['dir_files']} files, {_fmt_bytes(dump['dir_bytes'])}"
          f"{'' if dump['dir_exists'] else '  (MISSING on prod)'}")
    print(f"  owner: prod {prod_user} → local {user_id}; target {local_dir}")
    if dump.get("live_error"):
        print(f"  live.json unreadable on prod: {dump['live_error']}")
    if skip:
        print(f"  left out (newer than the live layer's pack {dump['live_pack']}): {sorted(skip)}")

    existing = s.get(FlightRow, fid)
    if (existing is not None or local_dir.exists()) and not args.overwrite:
        say("problem", fid, f"already here (row={existing is not None}, dir={local_dir.exists()}) "
            "-- pass --overwrite for a fresh copy")
        return False
    if args.dry_run:
        print("  dry run: nothing written.")
        return True

    # Rows built first: fail before touching anything on a schema problem.
    fcols, pcols, dcols = _columns(FlightRow), _columns(BriefingPackRow), _columns(FlightDebriefRow)
    frow, dropped = coerce_row(remap_flight(prod_flight, user_id), fcols)
    dropped_all = set(dropped)
    packs = []
    for raw in dump["briefing_packs"]:
        if raw.get("artifact_path") and Path(raw["artifact_path"]).name in skip:
            continue
        prow, dropped = coerce_row(remap_pack(raw, packs_root, user_id), pcols)
        dropped_all |= set(dropped)
        packs.append(prow)
    debriefs = []
    for raw in dump["flight_debriefs"]:
        drow, dropped = coerce_row(raw, dcols)
        dropped_all |= set(dropped)
        debriefs.append(drow)
    if dropped_all:
        print(f"  prod-only columns dropped: {sorted(dropped_all)}")

    # Files: stream into a staging dir, freeze, then swap in.
    staging = packs_root / user_id / f".import-{fid}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    stream_prod_dir(prod_user, fid, staging)
    staged = staging / fid
    for name in skip:
        shutil.rmtree(staged / name, ignore_errors=True)
    if freeze:
        from weatherbrief.tasks.live_layer import LIVE_FROZEN_FILE

        # Before the DB rows exist, so no devserver tick can catch it unfrozen.
        (staged / LIVE_FROZEN_FILE).write_text(
            f"imported from prod {datetime.now(timezone.utc).isoformat()} by import_flight.py\n")
    if existing is not None:
        old_dir = packs_root / existing.user_id / fid
        s.delete(existing)  # cascades packs + debrief
        s.flush()
        if old_dir != local_dir and old_dir.exists():
            shutil.rmtree(old_dir)
    if local_dir.exists():
        shutil.rmtree(local_dir)
    staged.rename(local_dir)
    shutil.rmtree(staging)

    if args.with_aircraft:
        if dump.get("aircraft"):
            ac = dump["aircraft"][0]
            frow["aircraft_id"] = local_aircraft_id(s, ac, user_id)
            print(f"  aircraft: {ac['icao_type']} fiki={bool(ac['is_fiki'])} ceiling={ac['ceiling_ft']}"
                  f"{' (placeholder)' if ac.get('is_placeholder') else ''} → local {frow['aircraft_id']}")
        else:
            print("  aircraft: none on prod (graded as unknown)")
    s.add(FlightRow(**frow))
    s.flush()
    for prow in packs:
        row = BriefingPackRow(**prow)
        row.integrity_hmac = _compute_pack_hmac(row)
        s.add(row)
    for drow in debriefs:
        s.add(FlightDebriefRow(**drow))
    s.commit()

    if args.highlight_at:
        patch_highlight(local_dir, args.highlight_at)

    n_files = sum(1 for p in local_dir.rglob("*") if p.is_file())
    say("ok", fid, f"{len(packs)} pack(s), {len(debriefs)} debrief(s), {n_files} files")
    missing = [p["artifact_path"] for p in packs if p["artifact_path"] and not Path(p["artifact_path"]).is_dir()]
    if missing:
        print(f"  WARNING: {len(missing)} pack dirs missing on disk (stripped by retention?): {missing}")
    report_live_window(s.get(FlightRow, fid), local_dir, freeze)
    return True


def patch_highlight(flight_dir: Path, at: str) -> None:
    from weatherbrief.models.live import LiveHighlight
    from weatherbrief.tasks.live_layer import (
        LIVE_FILE, LIVE_HIGHLIGHT_LOG, TRAIL_EXCLUDE, _atomic_write, load_live,
    )

    layer = load_live(flight_dir)
    log = flight_dir / LIVE_HIGHLIGHT_LOG
    rec = highlight_at(log, at) if layer and layer.glance and log.exists() else None
    if rec is None:
        say("problem", flight_dir.name, f"no highlight written by {at}")
        return
    layer.glance.highlight = LiveHighlight(
        text=rec["text"], model=rec.get("model") or "", facts_hash=rec.get("facts_hash") or "",
        generated_at=datetime.fromisoformat(rec["at"]))
    _atomic_write(flight_dir / LIVE_FILE, layer.model_dump_json(exclude={"changes": TRAIL_EXCLUDE}))
    print(f"  highlight patched from {rec['at'][11:16]}Z: {rec['text'][:70]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flights", nargs="*", help="prod flight ids or briefing URLs")
    ap.add_argument("--list", action="store_true", help="list prod flights in their live window")
    ap.add_argument("--live", action="store_true", help="import every flight in its live window")
    ap.add_argument("--user", help="local owner, id or email (default the dev-login user)")
    ap.add_argument("--overwrite", "--force", action="store_true", help="replace an earlier import")
    ap.add_argument("--dry-run", action="store_true", help="show what would be copied, write nothing")
    ap.add_argument("--no-freeze", action="store_true",
                    help="keep every pack and let the local live tick run on the flight")
    ap.add_argument("--with-aircraft", action="store_true",
                    help="also copy the flight's aircraft (type, IFR/FIKI, speed, ceiling) "
                         "and point the flight at it")
    ap.add_argument("--highlight-at", metavar="HH:MMZ",
                    help="patch the highlight written by then onto the layer (finished flights)")
    a = ap.parse_args(argv)

    if a.list or not (a.flights or a.live):
        cmd_list()
        return 0
    try:
        ids = [parse_flight_ref(r) for r in a.flights]
    except ValueError as e:
        sys.exit(str(e))
    if a.live:
        ids += [f["id"] for f in cmd_list() if f["id"] not in ids]
    if not ids:
        return 0

    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    import flyfun_common.db as fdb
    from flyfun_common.db.engine import ensure_dev_user, is_dev_mode
    from flyfun_common.db.models import UserRow
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from weatherbrief.storage.flights import _data_dir

    if not is_dev_mode():
        sys.exit("Refusing: ENVIRONMENT is production — this script only writes to the dev setup.")

    engine = fdb.get_engine()
    packs_root = _data_dir().resolve() / "packs"
    print(f"Local DB: {engine.url}  packs: {packs_root}")
    ok = 0
    with Session(bind=engine) as s:
        if a.user is None:
            ensure_dev_user(s)
            s.commit()
        ref = a.user or fdb.DEV_USER_ID
        user = s.get(UserRow, ref) or s.execute(select(UserRow).where(UserRow.email == ref)).scalar_one_or_none()
        if user is None:
            sys.exit(f"No local user {ref!r}.")
        for dump in prod(json.dumps(ids)):
            ok += import_one(s, dump, user.id, packs_root, a)
    if not a.dry_run:
        print("\nOpen (main's devserver; worktrees use 8001+):")
        for fid in ids:
            print(f"  http://localhost:8000/briefing.html?flight={fid}")
    return 0 if ok == len(ids) else 1


if __name__ == "__main__":
    sys.exit(main())
