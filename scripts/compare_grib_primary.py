"""Advisory replay diff for GRIB-primary ECMWF / ICON slots (#912).

Runs the same flight twice through the production refresh path, once with
``WB_GRIB_PRIMARY=0`` (Open-Meteo first, today's behaviour) and once with
the GRIB-primary gate on, then prints:

- the Open-Meteo calls each run made (billed weight is calls x variables/10),
- which models each run built from GRIB and how many points fell back,
- every (advisory, model) status that differs between the two runs.

Needs the ECMWF ECPDS disk, DWD opendata and Open-Meteo, so run it where
the briefings run (droplet or Mac mini), not in a sandbox. Runs back to back
use the same model runs as long as no new run lands in between: the script
prints both runs' GRIB init times so a run change is visible.

Usage:
    python scripts/compare_grib_primary.py <flight-id> [<flight-id> ...]
        [--user-id dev-user-001] [--force-live]

Packs go under ``profiles/_packs/<flight-id>/grib_primary_{off,on}/``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from weatherbrief.api.flights import _load_flight_or_404  # noqa: E402
from weatherbrief.api.packs import _prepare_refresh  # noqa: E402
from weatherbrief.db import SessionLocal, init_shared_db  # noqa: E402
from weatherbrief.models import RouteAdvisoriesManifest  # noqa: E402
from weatherbrief.pipeline import execute_briefing  # noqa: E402
from weatherbrief.tasks.advise import per_model_status_from_manifest  # noqa: E402


class _GribPrimaryLog(logging.Handler):
    """Collects the GRIB-primary log lines of one run."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("GRIB-primary"):
            self.lines.append(msg)


def _run(flight_id: str, user_id: str, force_live: bool, switch: str):
    os.environ["WB_GRIB_PRIMARY"] = switch
    db = SessionLocal()
    try:
        flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
        db_path = os.environ.get("AIRPORTS_DB", "") or str(ROOT / "data" / "nav.db")
        route, _ts, _pack, options, _meta = _prepare_refresh(
            flight, db_path, user_id, flight_id, db=db, is_privileged=True,
        )
    finally:
        db.close()

    label = "on" if switch != "0" else "off"
    pack_dir = ROOT / "profiles" / "_packs" / flight_id / f"grib_primary_{label}"
    pack_dir.mkdir(parents=True, exist_ok=True)
    options.output_dir = pack_dir
    options.generate_llm_digest = False
    options.fetch_gramet = False
    if force_live:
        options.historical_mode = False
        options.as_of_time = None

    handler = _GribPrimaryLog()
    logging.getLogger("weatherbrief").addHandler(handler)
    try:
        result = execute_briefing(
            route=route, departure_time=flight.departure_time, options=options,
        )
    finally:
        logging.getLogger("weatherbrief").removeHandler(handler)

    manifest = RouteAdvisoriesManifest.model_validate_json(
        (pack_dir / "route_advisories.json").read_text()
    )
    return result, per_model_status_from_manifest(manifest), handler.lines, pack_dir


def _compare(flight_id: str, user_id: str, force_live: bool) -> int:
    print(f"=== {flight_id}")
    off, off_status, _, off_dir = _run(flight_id, user_id, force_live, "0")
    on, on_status, on_log, on_dir = _run(flight_id, user_id, force_live, "1")

    print(f"Open-Meteo calls: off={off.usage.open_meteo_calls} on={on.usage.open_meteo_calls}")
    for line in on_log:
        print(f"  {line}")
    print(f"GRIB init times: off={off.grib_init_times} on={on.grib_init_times}")

    changes = 0
    for adv_id in sorted(set(off_status) | set(on_status)):
        before, after = off_status.get(adv_id, {}), on_status.get(adv_id, {})
        for model in sorted(set(before) | set(after)):
            b, a = before.get(model, "-"), after.get(model, "-")
            if b != a:
                changes += 1
                print(f"  {adv_id:<28} {model:<12} {b:>12} -> {a}")
    print(f"{changes} status change(s); packs: {off_dir} | {on_dir}\n")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("flight_ids", nargs="+")
    ap.add_argument("--user-id", default="dev-user-001")
    ap.add_argument("--force-live", action="store_true",
                    help="Brief as live even if the departure has passed")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("weatherbrief").setLevel(logging.INFO)
    init_shared_db()
    total = sum(_compare(fid, args.user_id, args.force_live) for fid in args.flight_ids)
    print(f"Total status changes across {len(args.flight_ids)} flight(s): {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
