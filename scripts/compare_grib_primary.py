"""Advisory replay diff for GRIB-primary ECMWF / ICON / GFS / Météo-France / UKMO slots.

Runs the same flight twice through the production refresh path, once with
``WB_GRIB_PRIMARY=0`` (Open-Meteo first, today's behaviour) and once with
the GRIB-primary gate on, then prints:

- the Open-Meteo calls each run made (billed weight is calls x variables/10),
- which models each run built from GRIB and how many points fell back,
- every (advisory, model) status that differs between the two runs,
- the surface fields of each GRIB-built slot against the same slot from
  Open-Meteo (bias, mean/max |Δ|, total ratio of the accumulated fields,
  suspects with their distance along the route, for domain-edge jumps),
  plus the sounding fields at 850/700/500 hPa for GFS and UKMO slots (#913
  builds the whole GFS column from NOAA GRIB, #937 the UKMO one from UKV).

``--off`` / ``--on`` set the two switch values. To isolate one model against
what prod runs today, keep the others GRIB-primary on both sides, e.g. for
GFS: ``--off ecmwf,icon --on 1``; for UKMO: ``--off ecmwf,icon,gfs --on 1``.

Météo-France (#940) has no Open-Meteo side any more: with the switch off the
slot is simply absent, so the 'off' run shows what the advisories do without
it (they should not change: no profile grades on it), and the surface and
sounding parity against Open-Meteo's AROME is
``scripts/compare_pack_surface_openmeteo.py`` on the 'on' packs.

Needs the ECMWF ECPDS disk, DWD opendata, NOAA S3 and Open-Meteo, so run it where
the briefings run (droplet or Mac mini), not in a sandbox. Runs back to back
use the same model runs as long as no new run lands in between: the script
prints both runs' GRIB init times so a run change is visible.

Usage:
    python scripts/compare_grib_primary.py <flight-id> [<flight-id> ...]
        [--user-id dev-user-001] [--force-live] [--off 0] [--on 1]

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


def _run(flight_id: str, user_id: str, force_live: bool, switch: str, label: str):
    os.environ["WB_GRIB_PRIMARY"] = switch
    db = SessionLocal()
    try:
        flight = _load_flight_or_404(db, flight_id, viewer_id=user_id)
        db_path = os.environ.get("AIRPORTS_DB", "") or str(ROOT / "data" / "nav.db")
        route, _ts, _pack, options, _as_of = _prepare_refresh(
            flight, db_path, user_id, flight_id, db=db, is_privileged=True,
        )
    finally:
        db.close()

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


def _print_fetch_facts(pack_dir: Path) -> None:
    facts = json.loads((pack_dir / "fetch_meta.json").read_text()).get("fetch_facts") or {}
    for key in ("grib_primary", "grib_primary_dropped", "open_meteo_calls",
                "open_meteo_weighted_calls"):
        if key in facts:
            print(f"  fetch_facts.{key}: {facts[key]}")


def _single(flight_id: str, user_id: str, force_live: bool, side: str, switch: str) -> int:
    """One side only: a quick look at a run without the diff."""
    print(f"=== {flight_id} ({side} only, WB_GRIB_PRIMARY={switch})")
    res, _status, log, pack_dir = _run(flight_id, user_id, force_live, switch, side)
    print(f"Open-Meteo calls: {res.usage.open_meteo_calls}; "
          f"billed weight: {res.usage.open_meteo_weighted_calls:.1f}")
    for line in log:
        print(f"  {line}")
    print(f"GRIB init times: {res.grib_init_times}")
    _print_fetch_facts(pack_dir)
    print(f"pack: {pack_dir}\n")
    return 0


def _compare(flight_id: str, user_id: str, force_live: bool, off_switch: str, on_switch: str) -> int:
    print(f"=== {flight_id} (WB_GRIB_PRIMARY off={off_switch} on={on_switch})")
    off, off_status, _, off_dir = _run(flight_id, user_id, force_live, off_switch, "off")
    on, on_status, on_log, on_dir = _run(flight_id, user_id, force_live, on_switch, "on")

    print(f"Open-Meteo calls: off={off.usage.open_meteo_calls} on={on.usage.open_meteo_calls}; "
          f"billed weight: off={off.usage.open_meteo_weighted_calls:.1f} "
          f"on={on.usage.open_meteo_weighted_calls:.1f}")
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
    GFS and UKMO slots also diff the sounding at ``SOUNDING_LEVELS``. Slots that were
    GRIB-primary on both sides are skipped (nothing to compare against).
    """
    sys.path.insert(0, str(ROOT))
    from scripts.compare_pack_surface_openmeteo import (
        DEFAULT_LEVELS,
        SOUNDING_SOURCES,
        compare_point,
        hourly_as_open_meteo,
        print_stats,
    )
    from weatherbrief.tasks.artifacts import load_cross_sections, load_route_points

    facts = json.loads((on_dir / "fetch_meta.json").read_text()).get("fetch_facts") or {}
    primary = facts.get("grib_primary") or {}
    off_facts = json.loads((off_dir / "fetch_meta.json").read_text()).get("fetch_facts") or {}
    primary = {m: v for m, v in primary.items() if m not in (off_facts.get("grib_primary") or {})}
    if not primary:
        print("No slot was GRIB-primary only in the 'on' run: nothing to diff.")
        return
    off_cs = {cs.model.value: cs for cs in load_cross_sections(off_dir)}
    on_cs = {cs.model.value: cs for cs in load_cross_sections(on_dir)}
    points = load_route_points(on_dir) or []
    stats: dict = {}
    for model in primary:
        if model not in off_cs or model not in on_cs:
            if model in on_cs:
                print(f"{model}: no Open-Meteo slot to diff against (#940); "
                      "use scripts/compare_pack_surface_openmeteo.py on the 'on' pack")
            continue
        per_model = stats.setdefault(model, {})
        levels = DEFAULT_LEVELS if model in SOUNDING_SOURCES else ()
        for i, (a, b) in enumerate(zip(off_cs[model].point_forecasts, on_cs[model].point_forecasts)):
            nm = points[i].distance_from_origin_nm if i < len(points) else float(i)
            compare_point(
                per_model, b.hourly, hourly_as_open_meteo(a.hourly), f"pt{i} ({nm:.0f} nm)",
                levels=levels,
            )
    print("Surface (and GFS / UKMO sounding), GRIB pack minus Open-Meteo pack:")
    print_stats(stats)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("flight_ids", nargs="+")
    ap.add_argument("--user-id", default="dev-user-001")
    ap.add_argument("--force-live", action="store_true",
                    help="Brief as live even if the departure has passed")
    ap.add_argument("--side", choices=("both", "on", "off"), default="both",
                    help="Run one side only (no diff), e.g. 'on' to shake out a GRIB-primary run")
    ap.add_argument("--off", default="0", metavar="SWITCH",
                    help="WB_GRIB_PRIMARY for the baseline run (default 0, all Open-Meteo). "
                         "Use 'ecmwf,icon' to compare GFS against prod today")
    ap.add_argument("--on", default="1", metavar="SWITCH",
                    help="WB_GRIB_PRIMARY for the GRIB run (default 1, every GRIB-primary model)")
    ap.add_argument("--om-ecmwf-model", metavar="MODEL",
                    help="Open-Meteo ECMWF model for this process only (e.g. ecmwf_ifs, "
                         "when ecmwf_ifs025 lags our ECPDS run). Changes the 'off' baseline "
                         "and the ECMWF fallback, so it is no longer prod's exact path")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("weatherbrief").setLevel(logging.INFO)
    if args.om_ecmwf_model:
        from weatherbrief.fetch.variables import MODEL_ENDPOINTS

        MODEL_ENDPOINTS["ecmwf"].model_param = args.om_ecmwf_model
        print(f"Open-Meteo ECMWF model: {args.om_ecmwf_model} (this process only)")
    init_shared_db()
    if args.side != "both":
        for fid in args.flight_ids:
            _single(fid, args.user_id, args.force_live, args.side,
                    args.on if args.side == "on" else args.off)
        return 0
    total = sum(
        _compare(fid, args.user_id, args.force_live, args.off, args.on) for fid in args.flight_ids
    )
    print(f"Total status changes across {len(args.flight_ids)} flight(s): {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
