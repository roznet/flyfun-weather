#!/usr/bin/env python3
"""Copy prod flights' live layer into the local dev DB, frozen, to review the display.

    venv/bin/python scripts/ops/import_live_flight.py --list
    venv/bin/python scripts/ops/import_live_flight.py --live [--user ID|EMAIL] [--overwrite]
    venv/bin/python scripts/ops/import_live_flight.py FLIGHT_ID... [--user ID|EMAIL] [--overwrite]
    venv/bin/python scripts/ops/import_live_flight.py FLIGHT_ID --highlight-at 14:20Z [--overwrite]

For each flight: the prod flight row and its pack rows are exported from the
weatherbrief container (exact copies, not rebuilt from briefing.json), the
flight directory is rsynced from the droplet (packs + live.json,
live_history.jsonl, live_highlights.jsonl), and the flight is recreated
locally under the same id for ``--user`` (default the dev-login account),
private, auto-refresh off.

**Frozen.** The flight dir gets ``live_layer.LIVE_FROZEN_FILE``, which the
live tick skips, so a running devserver does not overwrite prod's layer with
a local one (nor pay for a local highlight). A ↻ press in a client still
refreshes it — re-import to restore. Re-importing the same flight later in
the day takes a fresh snapshot (``--overwrite``).

**Take flights while they are live.** A finished flight's ``live.json`` has
no highlight (none is written after planned arrival) and ``live_history.jsonl``
keeps report texts, not layers, so the layer as a pilot saw it mid-flight only
exists while the flight is in its window. ``--highlight-at HH:MMZ`` patches
the last line *written* by then from ``live_highlights.jsonl`` onto the final
layer — the text only; the ribbon and nutshell stay the final tick's.

Read-only on prod. Local data is the owner's prod data: never commit it.
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hosts  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
DEV_USER = "dev-user-001"

# Runs inside the prod container (`docker exec -i weatherbrief python - ARGS`):
# argv[1] is "list" or a JSON list of flight ids. Prints one JSON document.
PROD_SCRIPT = r'''
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from weatherbrief.db import SessionLocal, init_shared_db
from weatherbrief.storage.flights import list_packs, load_flight, _resolve_artifact_path
from weatherbrief.tasks.live_layer import load_live
from weatherbrief.tasks.live_tick import _as_utc, find_live_flights

init_shared_db()
db = SessionLocal()
now = datetime.now(timezone.utc)

def layer_of(flight_id):
    packs = list_packs(db, flight_id)
    pack_dir = Path(_resolve_artifact_path(packs[0].artifact_path)) if packs else None
    return packs, pack_dir, (load_live(pack_dir.parent) if pack_dir else None)

if sys.argv[1] == "list":
    out = []
    for f in find_live_flights(db, now):
        dep = _as_utc(f.departure_time)
        arr = dep + timedelta(hours=f.flight_duration_hours or 0)
        _, _, layer = layer_of(f.id)
        hl = layer.glance.highlight if layer and layer.glance else None
        out.append({"id": f.id, "dep": dep.isoformat(), "arr": arr.isoformat(),
                    "phase": "airborne" if dep <= now <= arr else ("before" if now < dep else "after"),
                    "live_at": layer.live_updated_at.isoformat() if layer and layer.live_updated_at else None,
                    "highlight": hl.text if hl else None})
    print(json.dumps({"now": now.isoformat(), "flights": out}))
else:
    out = []
    for fid in json.loads(sys.argv[1]):
        try:
            flight = load_flight(db, fid)
        except KeyError:
            out.append({"id": fid, "error": "not found"})
            continue
        packs, pack_dir, layer = layer_of(fid)
        out.append({
            "id": fid,
            "flight": flight.model_dump(mode="json"),
            "packs": [{"dir": Path(_resolve_artifact_path(m.artifact_path)).name,
                       "meta": m.model_dump(mode="json", exclude={"is_historical"})} for m in packs],
            "flight_dir": str(pack_dir.parent) if pack_dir else None,
            "live_pack": layer.pack_dir_name if layer else None,
        })
    print(json.dumps(out))
'''


def say(state: str, what: str, evidence: str = "") -> None:
    print(f"{state:<8} {what}" + (f"  ({evidence})" if evidence else ""), file=sys.stderr)


def prod(arg: str) -> object:
    ssh = hosts.server_values("SERVER_SSH")["SERVER_SSH"]
    r = subprocess.run([*SSH, ssh, "docker exec -i weatherbrief python - " + shlex.quote(arg)],
                       input=PROD_SCRIPT, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise SystemExit(f"prod export failed: {r.stderr.strip()[-400:]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def cmd_list() -> list[dict]:
    doc = prod("list")
    say("ok", f"prod at {doc['now'][:16]}Z", f"{len(doc['flights'])} flight(s) in their live window")
    for f in doc["flights"]:
        print(f"{f['id']:<45} {f['phase']:<9} dep {f['dep'][11:16]}Z arr {f['arr'][11:16]}Z  "
              f"{(f['highlight'] or '(no highlight)')[:80]}")
    return doc["flights"]


def highlight_at(flight_dir: Path, at: str) -> dict | None:
    """The last highlight written at or before HH:MMZ on the layer's day."""
    from weatherbrief.tasks.live_layer import LIVE_HIGHLIGHT_LOG

    hh, mm = at.rstrip("Zz").split(":")
    best = None
    for line in (flight_dir / LIVE_HIGHLIGHT_LOG).read_text().splitlines():
        rec = json.loads(line)
        if rec.get("outcome") != "written" or not rec.get("text"):
            continue
        t = datetime.fromisoformat(rec["at"])
        if (t.hour, t.minute) <= (int(hh), int(mm)):
            best = rec
    return best


def import_one(db, exp: dict, user_id: str, data_dir: Path, *, overwrite: bool,
               at: str | None) -> str | None:
    from weatherbrief.db.models import FlightRow
    from weatherbrief.models import BriefingPackMeta, Flight
    from weatherbrief.models.live import LiveHighlight
    from weatherbrief.storage.flights import delete_flight, save_flight, save_pack_meta
    from weatherbrief.tasks.live_layer import LIVE_FILE, LIVE_FROZEN_FILE, TRAIL_EXCLUDE, _atomic_write, load_live

    fid = exp["id"]
    if exp.get("error") or not exp.get("flight_dir"):
        say("problem", fid, exp.get("error") or "no packs on prod")
        return None
    if db.get(FlightRow, fid) is not None:
        if not overwrite:
            say("problem", fid, "already imported -- pass --overwrite for a fresh snapshot")
            return None
        delete_flight(db, fid)
        db.commit()

    # Packs newer than the layer's would make /live serve null (the layer is
    # relative to one pack, and /live reads the newest).
    live_pack = exp["live_pack"]
    packs = [p for p in exp["packs"] if live_pack is None or p["dir"] <= live_pack]
    dest = data_dir / "packs" / user_id / fid
    dest.mkdir(parents=True, exist_ok=True)
    v = hosts.server_values("SERVER_SSH", "HOST_DATA_DIR")
    ssh, src = v["SERVER_SSH"], f"{v['HOST_DATA_DIR']}/packs/{exp['flight']['user_id']}/{fid}"
    r = subprocess.run(["rsync", "-az", "--delete", f"{ssh}:{src}/", f"{dest}/"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        say("problem", fid, f"rsync: {r.stderr.strip()[-300:]}")
        return None
    for extra in {p.name for p in dest.iterdir() if p.is_dir()} - {p["dir"] for p in packs}:
        shutil.rmtree(dest / extra)
    # Before the DB rows exist, so no devserver tick can catch it unfrozen.
    (dest / LIVE_FROZEN_FILE).write_text(
        f"imported from prod {datetime.now(timezone.utc).isoformat()} by import_live_flight.py\n")

    flight = Flight.model_validate(exp["flight"]).model_copy(update={
        "user_id": user_id, "share_code": None, "profile_id": None, "aircraft_id": None,
        "auto_refresh": False, "auto_refresh_hour": None, "last_auto_refresh_at": None,
        "private": True,
    })
    save_flight(db, flight, user_id)
    for p in packs:
        meta = BriefingPackMeta.model_validate(p["meta"]).model_copy(
            update={"id": None, "flight_id": fid, "artifact_path": str(dest / p["dir"])})
        save_pack_meta(db, meta)
    db.commit()

    note = ""
    layer = load_live(dest)
    if at and layer is not None and layer.glance is not None:
        rec = highlight_at(dest, at)
        if rec is None:
            say("problem", fid, f"no highlight written by {at}")
        else:
            layer.glance.highlight = LiveHighlight(
                text=rec["text"], model=rec.get("model") or "", facts_hash=rec.get("facts_hash") or "",
                generated_at=datetime.fromisoformat(rec["at"]))
            _atomic_write(dest / LIVE_FILE, layer.model_dump_json(exclude={"changes": TRAIL_EXCLUDE}))
            note = f", highlight patched from {rec['at'][11:16]}Z"
    hl = layer.glance.highlight if layer and layer.glance else None
    say("ok", fid, f"{len(packs)} pack(s), layer {str(layer.live_updated_at)[11:16] if layer else '-'}Z"
        f"{note}; highlight: {(hl.text[:70] + '...') if hl else 'none'}")
    return fid


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flights", nargs="*", help="prod flight ids")
    ap.add_argument("--list", action="store_true", help="list prod flights in their live window")
    ap.add_argument("--live", action="store_true", help="import every flight in its live window")
    ap.add_argument("--user", default=DEV_USER, help=f"local owner, id or email (default {DEV_USER})")
    ap.add_argument("--overwrite", action="store_true", help="replace an earlier import (fresh snapshot)")
    ap.add_argument("--highlight-at", metavar="HH:MMZ",
                    help="patch the highlight written by then onto the layer (finished flights)")
    a = ap.parse_args(argv)

    if a.list or not (a.flights or a.live):
        cmd_list()
        return 0
    ids = list(a.flights)
    if a.live:
        ids += [f["id"] for f in cmd_list() if f["id"] not in ids]
    if not ids:
        return 0

    from dotenv import load_dotenv

    load_dotenv(REPO / ".env")
    from flyfun_common.db import UserRow
    from sqlalchemy import select

    from weatherbrief.db import SessionLocal, init_shared_db
    from weatherbrief.storage.flights import _data_dir

    init_shared_db()
    db = SessionLocal()
    user = db.get(UserRow, a.user) or db.execute(
        select(UserRow).where(UserRow.email == a.user)).scalar_one_or_none()
    if user is None:
        say("problem", "user", f"no local user {a.user!r}")
        return 1
    data_dir = Path(_data_dir())
    done = [fid for exp in prod(json.dumps(ids))
            if (fid := import_one(db, exp, user.id, data_dir, overwrite=a.overwrite, at=a.highlight_at))]
    for fid in done:
        print(f"/briefing.html?flight={fid}")
    return 0 if len(done) == len(ids) else 1


if __name__ == "__main__":
    sys.exit(main())
