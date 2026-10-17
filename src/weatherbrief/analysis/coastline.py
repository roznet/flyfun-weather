"""Land/sea along a route: where it leaves and regains the coast (#790).

The route locator names a position over a sea crossing by its coast points
("over the sea, 25 NM after coast-out (Middelburg)"); between Zeeland and
Kent there is no airfield to anchor on. This module answers the one question
that needs: where along the route does it cross a coastline.

Data: Natural Earth 10m land, clipped to Europe and simplified
(``data/europe_land.json.gz``, built by ``scripts/build_europe_land.py``).
Outside its bbox every point reads as land, so a route outside Europe gets no
coast points rather than a sea everywhere.

Pure python, no GEOS: an even-odd ray cast over edges bucketed by latitude
band, so a test reads only the edges in its band. The index is built once per
process (~0.3 s, a few MB); crossings are cached per route.
"""

from __future__ import annotations

import gzip
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

_DATA = Path(__file__).parent / "data" / "europe_land.json.gz"
#: Latitude band height for the edge index, degrees.
_BAND_DEG = 0.05

#: The route is sampled every this many NM for the land test.
SAMPLE_NM = 1.0
#: A water stretch shorter than this is not a sea crossing (a river mouth,
#: an estuary, a fjord): no coast points for it.
MIN_WATER_NM = 8.0
#: A land stretch shorter than this inside a crossing is an islet or a
#: sandbank: the crossing is not split around it.
MIN_LAND_NM = 3.0


@dataclass(frozen=True)
class CoastCrossing:
    kind: Literal["coast_out", "coast_in"]
    along_nm: float
    lat: float
    lon: float


class _LandIndex:
    def __init__(self, path: Path):
        with gzip.open(path, "rt") as f:
            doc = json.load(f)
        scale = float(doc["scale"])
        self.lon_min, self.lat_min, self.lon_max, self.lat_max = doc["bbox"]
        self.bands: dict[int, list[tuple[float, float, float, float]]] = {}
        for flat in doc["rings"]:
            pts = [(flat[i] / scale, flat[i + 1] / scale) for i in range(0, len(flat), 2)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                if y1 == y2:
                    continue  # a horizontal edge never crosses a horizontal ray
                lo, hi = (y1, y2) if y1 < y2 else (y2, y1)
                for b in range(math.floor(lo / _BAND_DEG), math.floor(hi / _BAND_DEG) + 1):
                    self.bands.setdefault(b, []).append((x1, y1, x2, y2))

    def is_land(self, lat: float, lon: float) -> bool:
        if not (self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max):
            return True
        inside = False
        for x1, y1, x2, y2 in self.bands.get(math.floor(lat / _BAND_DEG), ()):
            if (y1 > lat) != (y2 > lat):
                x = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
                if x > lon:
                    inside = not inside
        return inside


@lru_cache(maxsize=1)
def _index() -> _LandIndex:
    return _LandIndex(_DATA)


def is_land(lat: float, lon: float) -> bool:
    """Whether (lat, lon) is on land (anything outside the data's bbox is)."""
    return _index().is_land(lat, lon)


def coast_crossings(
    points: tuple[tuple[float, float], ...], distances: tuple[float, ...],
) -> tuple[CoastCrossing, ...]:
    """Where the route leaves (``coast_out``) and regains (``coast_in``) land.

    ``points`` / ``distances`` are the route's waypoints and their cumulative
    NM (a :class:`~weatherbrief.analysis.route_geometry.RouteTrack`'s). Tuples
    so the result is cached per route: the locator is rebuilt every live tick.
    A route that starts or ends over water (rare: a coastal field the
    simplified coast puts offshore) gets no point for that end.
    """
    return _coast_crossings(tuple(points), tuple(distances))


@lru_cache(maxsize=256)
def _coast_crossings(points, distances) -> tuple[CoastCrossing, ...]:
    from weatherbrief.analysis.route_geometry import RouteTrack

    track = RouteTrack(points, distances)
    total = track.total_nm
    if total <= 0:
        return ()
    n = max(2, int(math.ceil(total / SAMPLE_NM)) + 1)
    samples = []
    for i in range(n):
        d = total * i / (n - 1)
        lat, lon = track.position_at(d)
        samples.append((d, lat, lon, is_land(lat, lon)))

    # Runs of equal land/sea, then fold short ones into their neighbours:
    # islets first (short land between water), then estuaries.
    runs: list[list] = []  # [is_land, start_nm, end_nm]
    for d, _lat, _lon, land in samples:
        if runs and runs[-1][0] == land:
            runs[-1][2] = d
        else:
            if runs:
                runs[-1][2] = d
            runs.append([land, d, d])
    for keep_land, min_nm in ((False, MIN_LAND_NM), (True, MIN_WATER_NM)):
        merged: list[list] = []
        for r in runs:
            short = r[0] != keep_land and (r[2] - r[1]) < min_nm and merged
            if short or (merged and merged[-1][0] == r[0]):
                merged[-1][2] = r[2]
            else:
                merged.append(list(r))
        runs = merged
        # A short run at either end has no neighbour on that side: it takes
        # the type of the one it touches.
        for i, j in ((0, 1), (-1, -2)):
            if len(runs) > 1 and runs[i][0] != keep_land and (runs[i][2] - runs[i][1]) < min_nm:
                runs[j][1], runs[j][2] = min(runs[j][1], runs[i][1]), max(runs[j][2], runs[i][2])
                del runs[i]

    out: list[CoastCrossing] = []
    for prev, cur in zip(runs, runs[1:]):
        d = cur[1]
        lat, lon = track.position_at(d)
        kind = "coast_out" if prev[0] and not cur[0] else "coast_in"
        out.append(CoastCrossing(kind=kind, along_nm=round(d, 1), lat=lat, lon=lon))
    return tuple(out)
