#!/usr/bin/env python3
"""What the live layer made of a flight's progress, tick by tick (#761).

    venv/bin/python scripts/check_flight_progress.py FLIGHT [--log G1000.csv] [--step 10]

FLIGHT is a flight id (looked up under ``DATA_DIR/packs/*/``, e.g. after
``scripts/ops/import_flight.py <id>``) or a flight directory. Read-only.

Prints:

* **Fixes**: every progress event in ``live_history.jsonl``, each fix with
  its projection, height above the terrain and how the fusion reads it
  (airborne / ground / unknown, at the departure / destination). Fixes the
  endpoint ignored are not in the history: see the ``LIVE_POSITION`` log
  lines on the droplet for those.
* **Ticks**: every ``--step`` minutes, what a tick at that time measured
  against (``FlightProgress.at``: only the events recorded by then), with
  the latest pack whose fetch preceded it.
* With ``--log`` (a Garmin G1000 / Perspective CSV, as synced by
  flightlogstats): the truth beside it. Take-off and landing from the
  ground speed (the first and last time it crosses 50 kt for a minute),
  the real along-route position at each tick, and the errors.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from dotenv import load_dotenv  # noqa: E402

from weatherbrief.analysis.flight_progress import (  # noqa: E402
    EVENT_FIX,
    FlightProgress,
    progress_events,
)
from weatherbrief.models.analysis import RouteConfig  # noqa: E402

AIRBORNE_KT = 50.0
SUSTAIN = timedelta(seconds=60)


def flight_dir(arg: str) -> Path:
    p = Path(arg)
    if p.is_dir():
        return p
    load_dotenv(REPO / ".env", override=True)
    hits = list((Path(os.environ["DATA_DIR"]) / "packs").glob(f"*/{arg}"))
    if not hits:
        sys.exit(f"no flight dir for {arg} under DATA_DIR/packs (import it first)")
    return hits[0]


def packs(fdir: Path) -> list[tuple[datetime, dict]]:
    """(fetch time, briefing) for each pack, oldest first."""
    out = []
    for b in sorted(fdir.glob("*/briefing.json")):
        stamp = b.parent.name.replace("p00-00", "+00:00")
        date, _, time = stamp.partition("T")
        try:
            fetched = datetime.fromisoformat(f"{date}T{time.replace('-', ':')}")
        except ValueError:
            continue
        out.append((fetched, json.loads(b.read_text())))
    if not out:
        sys.exit(f"no pack briefing.json in {fdir}")
    return out


def pack_at(all_packs, t: datetime) -> dict:
    current = all_packs[0][1]
    for fetched, briefing in all_packs:
        if fetched <= t:
            current = briefing
    return current


def progress(briefing: dict, events, t: datetime) -> FlightProgress:
    route = RouteConfig.model_validate(briefing["route"])
    planned = datetime.fromisoformat(briefing["departure_time"])
    return FlightProgress.from_route(route, planned, t, events)


def read_log(path: Path) -> list[tuple[datetime, float, float, float | None, float]]:
    """(utc, lat, lon, alt_ft, gs_kt) rows with a position, from a G1000 CSV."""
    rows = []
    with path.open(newline="") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    reader = csv.reader(lines)
    header = [h.strip() for h in next(reader)]
    col = {name: header.index(name) for name in
           ("Lcl Date", "Lcl Time", "UTCOfst", "Latitude", "Longitude", "AltMSL", "GndSpd")}
    for r in reader:
        try:
            lat, lon = float(r[col["Latitude"]]), float(r[col["Longitude"]])
            gs = float(r[col["GndSpd"]])
            local = datetime.fromisoformat(f"{r[col['Lcl Date']].strip()}T{r[col['Lcl Time']].strip()}")
            off = r[col["UTCOfst"]].strip() or "+00:00"
        except (ValueError, IndexError):
            continue
        sign = -1 if off.startswith("-") else 1
        hh, _, mm = off.lstrip("+-").partition(":")
        utc = local - sign * timedelta(hours=int(hh or 0), minutes=int(mm or 0))
        try:
            alt = float(r[col["AltMSL"]])
        except ValueError:
            alt = None
        rows.append((utc.replace(tzinfo=timezone.utc), lat, lon, alt, gs))
    return rows


def sustained(rows, i: int, fast: bool) -> bool:
    t0 = rows[i][0]
    for t, *_, gs in rows[i:]:
        if t - t0 > SUSTAIN:
            return True
        if (gs >= AIRBORNE_KT) != fast:
            return False
    return False


def truth_times(rows) -> tuple[datetime | None, datetime | None]:
    takeoff = landing = None
    for i, (t, *_, gs) in enumerate(rows):
        if takeoff is None and gs >= AIRBORNE_KT and sustained(rows, i, True):
            takeoff = t
        elif takeoff is not None and gs < AIRBORNE_KT and sustained(rows, i, False):
            landing = t
    return takeoff, landing


def row_at(rows, t: datetime):
    best = min(rows, key=lambda r: abs((r[0] - t).total_seconds()))
    return best if abs((best[0] - t).total_seconds()) <= 120 else None


def hm(t: datetime | None) -> str:
    return t.strftime("%H:%M:%S") if t is not None else "-"


def mins(a: datetime | None, b: datetime | None) -> str:
    return f"{(a - b).total_seconds() / 60:+.1f}" if a is not None and b is not None else "-"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("flight")
    ap.add_argument("--log", type=Path, help="G1000 / Perspective CSV for the same leg")
    ap.add_argument("--step", type=int, default=10, help="minutes between ticks (default 10)")
    args = ap.parse_args()

    fdir = flight_dir(args.flight)
    all_packs = packs(fdir)
    hist = fdir / "live_history.jsonl"
    records = [json.loads(ln) for ln in hist.read_text().splitlines() if ln.strip()] if hist.exists() else []
    events = progress_events(records)
    last = pack_at(all_packs, datetime.now(timezone.utc))
    print(f"flight {fdir.name}: {len(all_packs)} packs, {len(events)} progress events")

    # --- Fixes, as the fusion reads them at the end of the day ---------------
    end = max([e.recorded_at for e in events] + [datetime.now(timezone.utc)])
    final = progress(last, events, end)
    total = final.total_nm
    print(f"\nroute {total:.1f} NM, planned {final.planned_departure:%H:%MZ} for {final.duration_h:.2f} h")
    print("\nFIXES (time / received / along / cross / gs / alt / height / reads as)")
    seen = {s.event: s for s in final._fixes}
    for e in events:
        if e.kind != EVENT_FIX:
            print(f"  {e.kind:8s} {e.source:6s} at {hm(e.at)}  rec {hm(e.recorded_at)}")
            continue
        s, f = seen.get(e), e.fix
        if s is None:
            print(f"  fix      {hm(e.at)}  rec {hm(e.recorded_at)}  not used (far off route or unprojectable)")
            continue
        reads = "airborne" if s.airborne else "ground" if s.on_ground else "unknown"
        where = " at-dep" if s.at_departure else " at-dest" if s.at_destination(total) else ""
        h = f"{s.height:.0f}" if s.height is not None else "-"
        print(f"  fix      {hm(e.at)}  rec {hm(e.recorded_at)}  {s.along:6.1f} {s.cross:5.1f}  "
              f"gs={f.gs_kt if f.gs_kt is not None else '-'} alt={f.alt_ft if f.alt_ft is not None else '-'} "
              f"h={h}  {reads}{where}  acc={f.h_accuracy_m}")

    rows = read_log(args.log) if args.log else []
    t_takeoff, t_landing = truth_times(rows) if rows else (None, None)
    if rows and not (rows[0][0] - timedelta(hours=6) <= final.planned_departure <= rows[-1][0] + timedelta(hours=6)):
        print(f"\nWARNING: the log runs {rows[0][0]:%Y-%m-%d %H:%MZ}…{rows[-1][0]:%H:%MZ}, "
              f"not around this flight's {final.planned_departure:%Y-%m-%d %H:%MZ}: wrong log?")
    if rows:
        print(f"\nTRUTH from {args.log.name}: take-off {hm(t_takeoff)}Z, landing {hm(t_landing)}Z")
        print(f"  inferred take-off {hm(final.takeoff_at)}Z ({mins(final.takeoff_at, t_takeoff)} min), "
              f"landing {hm(final.landed_at)}Z ({mins(final.landed_at, t_landing)} min), "
              f"time base {final.time_base}")

    # --- Ticks ---------------------------------------------------------------
    start = final.planned_departure - timedelta(minutes=30)
    last_event = max((e.recorded_at for e in events), default=None)
    stop = max(filter(None, [final.arrival, t_landing, last_event])) + timedelta(minutes=20)
    t = start.replace(minute=start.minute - start.minute % args.step, second=0, microsecond=0)
    print(f"\nTICKS every {args.step} min (what a tick at that time showed)")
    head = "  time      source        dep(tko)  arrival   flown  fix-age est"
    if rows:
        head += "   true-along  err-NM  eta-err-min"
    print(head)
    while t <= stop:
        p = progress(pack_at(all_packs, t), events, t)
        flown = p.flown_nm
        age = p.fix_age_min
        line = (f"  {t:%H:%M}Z   {p.source:12s}  {hm(p.takeoff_at)[:5]:8s}  {hm(p.arrival)[:5]:8s}  "
                f"{flown if flown is None else round(flown, 1)!s:>5}  "
                f"{'-' if age is None else round(age)!s:>7} {'y' if p.estimated else 'n'}")
        if rows:
            r = row_at(rows, t)
            if r is not None and p.track is not None:
                true_along = p.track.project(r[1], r[2]).along_nm
                if t_takeoff is None or t < t_takeoff:
                    true_along = 0.0
                err = (flown - true_along) if flown is not None else None
                line += f"   {true_along:9.1f}  {'-' if err is None else f'{err:+.1f}':>6}"
            else:
                line += f"   {'-':>9}  {'-':>6}"
            line += f"  {mins(p.arrival, t_landing):>10}"
        print(line)
        t += timedelta(minutes=args.step)


if __name__ == "__main__":
    main()
