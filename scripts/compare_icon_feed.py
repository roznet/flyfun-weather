#!/usr/bin/env python3
"""Replay diff for the ICON feed switch: legacy lat-lon vs DWD native grid (#974).

Runs the same flight twice through the production refresh path, GRIB-primary
on both sides, once with ``WB_ICON_FEED`` unset (DWD's regular lat-lon files,
today) and once with ``WB_ICON_FEED=v1`` (the native-grid feed), then prints:

- which ICON variant and run each side used (the ``GRIB init times`` line;
  both sides should show the same run),
- every (advisory, model) status that differs between the two,
- the ICON slot's surface fields and its 850/700/500 hPa sounding, native
  minus legacy: bias, mean / max |Δ| and the worst points.

Expect regridding-sized differences, not offsets. Grade changes should be
rare and explainable: visibility (no ICON VIS on v1, ceiling-only, §78) or a
route point at the edge of the ICON-D2 domain (real grid vs old rectangle).

Needs DWD opendata (and the other GRIB sources the briefing reads), so run it
on the Mac or the droplet, not in a sandbox. Until DWD retires the lat-lon
feed after 2026-11-30 both feeds carry the same runs. The two feeds have
separate cache keys, so the first v1 run downloads its own files.

Usage:
    python scripts/compare_icon_feed.py <flight-id> [<flight-id> ...]
        [--user-id dev-user-001] [--force-live]

Packs go under ``profiles/_packs/<flight-id>/grib_primary_icon_{legacy,v1}/``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.compare_grib_primary import _run  # noqa: E402  (loads .env)
from scripts.compare_pack_surface_openmeteo import (  # noqa: E402
    DEFAULT_LEVELS,
    compare_point,
    hourly_as_open_meteo,
    print_stats,
)
from weatherbrief.db import init_shared_db  # noqa: E402


def _side(flight_id: str, user_id: str, force_live: bool, feed: str):
    previous = os.environ.get("WB_ICON_FEED")
    if feed == "legacy":
        os.environ.pop("WB_ICON_FEED", None)
    else:
        os.environ["WB_ICON_FEED"] = feed
    try:
        return _run(flight_id, user_id, force_live, "1", f"icon_{feed}")
    finally:
        if previous is None:
            os.environ.pop("WB_ICON_FEED", None)
        else:
            os.environ["WB_ICON_FEED"] = previous


def _icon_slots(pack_dir: Path) -> dict:
    from weatherbrief.tasks.artifacts import load_cross_sections

    return {
        cs.model.value: cs for cs in load_cross_sections(pack_dir)
        if cs.model.value.startswith("icon")
    }


def _key(wf) -> tuple[float, float]:
    return (round(wf.waypoint.lat, 4), round(wf.waypoint.lon, 4))


def _compare(flight_id: str, user_id: str, force_live: bool) -> int:
    from weatherbrief.tasks.artifacts import load_route_points

    print(f"=== {flight_id} (ICON feed legacy vs v1)")
    old, old_status, _, old_dir = _side(flight_id, user_id, force_live, "legacy")
    new, new_status, new_log, new_dir = _side(flight_id, user_id, force_live, "v1")
    print(f"GRIB init times: legacy={old.grib_init_times} v1={new.grib_init_times}")
    for line in new_log:
        print(f"  {line}")

    changes = 0
    for adv_id in sorted(set(old_status) | set(new_status)):
        before, after = old_status.get(adv_id, {}), new_status.get(adv_id, {})
        for model in sorted(set(before) | set(after)):
            b, a = before.get(model, "-"), after.get(model, "-")
            if b != a:
                changes += 1
                print(f"  {adv_id:<28} {model:<12} {b:>12} -> {a}")
    print(f"{changes} status change(s); packs: {old_dir} | {new_dir}")

    old_cs, new_cs = _icon_slots(old_dir), _icon_slots(new_dir)
    if not old_cs or not new_cs:
        print(f"ICON slot missing: legacy={sorted(old_cs)} v1={sorted(new_cs)}\n")
        return changes
    nm_at = {
        (round(p.lat, 4), round(p.lon, 4)): p.distance_from_origin_nm
        for p in load_route_points(new_dir) or []
    }
    stats: dict = {}
    for model in sorted(set(old_cs) & set(new_cs)):
        per_model = stats.setdefault(model, {})
        # Pair on the waypoint position, not the list index: a side that
        # drops a point (e.g. outside the D2 footprint) must not shift the rest.
        legacy_at = {_key(wf): wf for wf in old_cs[model].point_forecasts}
        unmatched = 0
        for i, b in enumerate(new_cs[model].point_forecasts):
            a = legacy_at.pop(_key(b), None)
            if a is None:
                unmatched += 1
                continue
            nm = nm_at.get(_key(b), float(i))
            compare_point(
                per_model, b.hourly, hourly_as_open_meteo(a.hourly), f"pt{i} ({nm:.0f} nm)",
                levels=DEFAULT_LEVELS,
            )
        unmatched += len(legacy_at)
        if unmatched:
            print(f"{model}: {unmatched} route point(s) on one side only, not compared")
    if set(old_cs) != set(new_cs):
        print(f"ICON variant differs: legacy={sorted(old_cs)} v1={sorted(new_cs)}")
    print("ICON slot, native (v1) minus legacy:")
    print_stats(stats)
    print()
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
