"""Build ``src/weatherbrief/analysis/data/europe_land.json.gz`` from Natural Earth.

The route locator (#790) names coast-out / coast-in points, which needs a
land/sea test at runtime. Natural Earth is public domain; the 10m land layer
is clipped to Europe and simplified so the shipped file stays small and the
runtime needs no GEOS (``analysis/coastline.py`` is a pure-python ray cast).

Build-time only dependency: shapely (``pip install shapely`` in a scratch venv).

    curl -LO https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_land.geojson
    python scripts/build_europe_land.py ne_10m_land.geojson

Format: ``{"source", "bbox", "scale", "rings": [[x0, y0, x1, y1, ...], ...]}``
with lon/lat as integers in units of 1/``scale`` degree. Rings are outer
boundaries only: Natural Earth's land layer cuts no holes that matter at
route scale (lakes are land in it), and dropping holes keeps the even-odd
test trivially right.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from shapely.geometry import box, shape

#: lon_min, lat_min, lon_max, lat_max: Iceland/Azores to the Urals' foot,
#: Canaries to North Cape.
BBOX = (-32.0, 26.0, 45.0, 72.0)
#: Douglas–Peucker tolerance in degrees (~300 m): well under the ~1 NM the
#: locator samples the route at.
TOLERANCE_DEG = 0.003
SCALE = 10_000
#: Islets smaller than this (deg²; ~1 km² at 50°N) are dropped.
MIN_AREA_DEG2 = 1.5e-4

OUT = Path(__file__).resolve().parents[1] / "src/weatherbrief/analysis/data/europe_land.json.gz"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    args = ap.parse_args()
    clip = box(*BBOX)
    rings: list[list[int]] = []
    with open(args.geojson) as f:
        features = json.load(f)["features"]
    for feat in features:
        geom = shape(feat["geometry"])
        if not geom.intersects(clip):
            continue
        part = geom.intersection(clip).simplify(TOLERANCE_DEG, preserve_topology=True)
        polys = getattr(part, "geoms", [part])
        for poly in polys:
            if poly.geom_type != "Polygon" or poly.area < MIN_AREA_DEG2:
                continue
            flat: list[int] = []
            for x, y in poly.exterior.coords[:-1]:
                flat += [round(x * SCALE), round(y * SCALE)]
            rings.append(flat)
    doc = {
        "source": "Natural Earth 10m land (public domain), clipped and simplified by scripts/build_europe_land.py",
        "bbox": list(BBOX),
        "scale": SCALE,
        "rings": rings,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(f"{len(rings)} rings, {sum(len(r) // 2 for r in rings)} vertices -> {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
