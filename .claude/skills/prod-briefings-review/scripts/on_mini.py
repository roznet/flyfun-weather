"""Run ON THE MAC MINI with its repo venv (~/Developer/public/flyfun-weather/venv/bin/python).
Reads the observed archive (~/flyfun-data/observed-archive) in place, so no frames move
over the network; only small JSON comes back. See ../SKILL.md.

  observed      JOBS.json OUT.json     route radar/lightning (ObservedConditions) per prod tick,
                                       using only frames received by the tick (sidecar received_at)
  airport-radar POINTS.json OUT.json   max dBZ within 3/5/10 NM and flashes within 3/5/10 NM around
                                       each METAR's airport over the 10 min ending at the METAR time
  cells         JOBS.json OUT_DIR      the cells display files (newest revision) from 40 min before
                                       each flight's first tick to 3 h after its last, cut to the
                                       routes' box + 60 NM, mtime kept (= when the node wrote it, the
                                       replay's "received by then"), for review.py replay --cells and
                                       score-estimates (#688)
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI, SOURCE_OPERA_DBZH, SOURCE_SPECS, FrameStore

ARCHIVE = Path(os.environ.get("WB_OBSERVED_ARCHIVE") or Path.home() / "flyfun-data/observed-archive")


class AsOfStore(FrameStore):
    """Only the frames that had arrived by ``now``."""

    now = None

    def list_frames(self, source):
        return [
            f for f in super().list_frames(source)
            if f.meta.get("received_at") and datetime.fromisoformat(f.meta["received_at"]) <= self.now
        ]


def observed(jobs_path, out_path):
    from weatherbrief.models.analysis import RouteConfig
    from weatherbrief.observed.payload import build_observed_conditions

    jobs = json.load(open(jobs_path))
    store = AsOfStore(ARCHIVE, retain_all=True)
    out = {}
    for fl, j in jobs.items():
        route = RouteConfig.model_validate(j["route"])
        out[fl] = {}
        for t in j["ticks"]:
            store.now = datetime.fromisoformat(t)
            oc = build_observed_conditions(route, store=store, now=store.now, sources=(SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI))
            out[fl][t] = oc.model_dump(mode="json")
        print(fl, len(j["ticks"]), file=sys.stderr, flush=True)
    json.dump(out, open(out_path, "w"))


def airport_radar(points_path, out_path):
    from weatherbrief.observed import lightning
    from weatherbrief.observed.grid import nm_to_km
    from weatherbrief.observed.payload import read_grid_frame
    from weatherbrief.observed.sampler import SampleStation, sample, sample_flashes

    radii = (3.0, 5.0, 10.0)
    points = json.load(open(points_path))
    store = FrameStore(ARCHIVE, retain_all=True)
    dbzh = {f.valid_time: f for f in store.list_frames(SOURCE_OPERA_DBZH)}
    li = {f.valid_time: f for f in store.list_frames(SOURCE_EUMETSAT_LI)}
    by_time = defaultdict(list)
    for i, p in enumerate(points):
        by_time[datetime.fromisoformat(p["t"])].append(i)
    out = [dict(p, dbz={}, flashes={}, radar_frames=0, li_frames=0) for p in points]
    for t, idx in sorted(by_time.items()):
        stations = [SampleStation(id=str(i), lat=points[i]["lat"], lon=points[i]["lon"]) for i in idx]
        for vt in (t - timedelta(minutes=5), t):
            f = dbzh.get(vt)
            if f is None:
                continue
            frame, window = read_grid_frame(f, SOURCE_OPERA_DBZH, [s.lat for s in stations], [s.lon for s in stations], nm_to_km(max(radii)))
            for sid, annuli in sample(frame, window, stations, radii).items():
                o = out[int(sid)]
                o["radar_frames"] += 1
                for r in radii:
                    vals = [a.max_value for a in annuli if a.radius_nm <= r and a.max_value is not None]
                    if vals:
                        o["dbz"][str(r)] = max(o["dbz"].get(str(r), max(vals)), max(vals))
        f = li.get(t)
        if f is not None:
            frame = lightning.read_flashes(f.path, source=SOURCE_EUMETSAT_LI, window_minutes=SOURCE_SPECS[SOURCE_EUMETSAT_LI].window_minutes)
            for sid, annuli in sample_flashes(frame, stations, radii).items():
                o = out[int(sid)]
                o["li_frames"] += 1
                for r in radii:
                    o["flashes"][str(r)] = sum(a.flash_count for a in annuli if a.radius_nm <= r)
    json.dump(out, open(out_path, "w"))
    print("done", len(out), "with radar", sum(o["radar_frames"] > 0 for o in out), file=sys.stderr)


def cells(jobs_path, out_dir):
    import gzip

    from weatherbrief.observed.cells.catalogue import encode
    from weatherbrief.observed.cells_display import filter_bbox
    from weatherbrief.observed.frames import parse_frame_stamp

    jobs = json.load(open(jobs_path))
    lats = [wp["lat"] for j in jobs.values() for wp in j["route"]["waypoints"]]
    lons = [wp["lon"] for j in jobs.values() for wp in j["route"]["waypoints"]]
    pad = 1.0 + 60 / 60.0  # 60 NM, generous in longitude
    box = (min(lats) - pad, min(lons) - 2 * pad, max(lats) + pad, max(lons) + 2 * pad)
    windows = []
    for j in jobs.values():
        ticks = [datetime.fromisoformat(t) for t in j["ticks"]]
        windows.append((min(ticks) - timedelta(minutes=40), max(ticks) + timedelta(hours=3)))
    src = ARCHIVE / "cells" / "display"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    newest = {}
    for p in src.glob("*.json.gz"):
        stamp, _, rest = p.name.partition(".")
        rev = int(rest.split(".")[0][1:]) if rest.startswith("r") else 0
        if stamp not in newest or rev > newest[stamp][0]:
            newest[stamp] = (rev, p)
    n = 0
    for stamp, (rev, p) in sorted(newest.items()):
        t = parse_frame_stamp(stamp)
        if not any(a <= t <= b for a, b in windows):
            continue
        doc = filter_bbox(json.loads(gzip.decompress(p.read_bytes())), *box)
        doc.pop("bbox", None)
        dest = out / p.name
        dest.write_bytes(encode(doc))
        st = p.stat()
        os.utime(dest, (st.st_atime, st.st_mtime))
        n += 1
    print("cells frames", n, file=sys.stderr)


if __name__ == "__main__":
    {"observed": observed, "airport-radar": airport_radar, "cells": cells}[sys.argv[1]](*sys.argv[2:4])
