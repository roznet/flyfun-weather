"""Advisory replay diff for GRIB-primary ECMWF / ICON slots (#912).

Runs the same flight twice through the production refresh path, once with
``WB_GRIB_PRIMARY=0`` (Open-Meteo first, today's behaviour) and once with
the GRIB-primary gate on, then prints:

- the Open-Meteo calls each run made (billed weight is calls x variables/10),
- which models each run built from GRIB and how many points fell back,
- every (advisory, model) status that differs between the two runs,
- the surface fields of each GRIB-built slot against the same slot from
  Open-Meteo (bias, mean/max |Δ|, total ratio of the accumulated fields,
  suspects with their distance along the route, for domain-edge jumps).

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
import json
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
    print(f"{changes} status change(s); packs: {off_dir} | {on_dir}")
    _surface_diff(off_dir, on_dir)
    print()
    return changes


def _surface_diff(off_dir: Path, on_dir: Path) -> None:
    """Surface fields of the GRIB-built slots vs the same slots from Open-Meteo.

    Every route point and every hour the GRIB pack has a value for. Watch the
    total ratio on the accumulated fields (an accumulation or unit slip), and
    the suspects for jumps at a domain edge (their distance along the route).
    """
    sys.path.insert(0, str(ROOT))
    from scripts.compare_pack_surface_openmeteo import (
        compare_point,
        hourly_as_open_meteo,
        print_stats,
    )
    from weatherbrief.tasks.artifacts import load_cross_sections, load_route_points

    facts = json.loads((on_dir / "fetch_meta.json").read_text()).get("fetch_facts") or {}
    primary = facts.get("grib_primary") or {}
    if not primary:
        print("No slot was GRIB-primary in the 'on' run: nothing to diff.")
        return
    off_cs = {cs.model.value: cs for cs in load_cross_sections(off_dir)}
    on_cs = {cs.model.value: cs for cs in load_cross_sections(on_dir)}
    points = load_route_points(on_dir) or []
    stats: dict = {}
    for model in primary:
        if model not in off_cs or model not in on_cs:
            continue
        per_model = stats.setdefault(model, {})
        for i, (a, b) in enumerate(zip(off_cs[model].point_forecasts, on_cs[model].point_forecasts)):
            nm = points[i].distance_from_origin_nm if i < len(points) else float(i)
            compare_point(per_model, b.hourly, hourly_as_open_meteo(a.hourly), f"pt{i} ({nm:.0f} nm)")
    print("Surface, GRIB pack minus Open-Meteo pack:")
    print_stats(stats)


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
