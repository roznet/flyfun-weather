"""Review PNG: one frame with its cells drawn over the radar.

Eyeballing is the first validation — a confident wrong tracker looks fine in
numbers and wrong on a picture.  Drawn in native grid pixels (no reprojection,
north is grid-up, which on the OPERA Lambert grid is close to true north over
central Europe and visibly not at the domain edges):

* radar in the shared intensity ramp (``imagery``), nodata as a grey wash;
* outlines: rain20 light grey, core35 white, core41 black;
* an arrow per tracked core: where the cell would be in 30 minutes;
* lightning flashes as yellow dots;
* core labels: age in minutes and trend (``+`` developing, ``-`` decaying,
  ``=`` steady, ``~`` mixed, ``n`` new); ``s``/``m`` mark a split/merge this frame.

Not a product surface — a development tool.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

from ..frames import SOURCE_EUMETSAT_LI
from ..imagery import _DBZ_STOPS, _colourise
from .catalogue import catalogue_path, read_catalogue
from .policy import DEFAULT_POLICY, CellPolicy
from .runner import FrameCache, Workspace, _li_frame

_OUTLINE = {"rain20": (200, 200, 200), "core35": (255, 255, 255), "core41": (0, 0, 0)}
_TREND = {"developing": "+", "decaying": "-", "steady": "=", "mixed": "~", "new": "n"}


def render_frame(
    ws: Workspace,
    valid_time: datetime,
    out_path: Path,
    *,
    policy: CellPolicy = DEFAULT_POLICY,
    bbox: tuple[float, float, float, float] | None = None,
    scale: int = 1,
) -> Path:
    from PIL import Image, ImageDraw

    cache = FrameCache(ws.frames)
    frame = cache.dbzh(valid_time)
    if frame is None:
        raise FileNotFoundError(f"no DBZH frame at {valid_time.isoformat()}")
    catalogue = read_catalogue(catalogue_path(ws.root, valid_time))
    dets = cache.detections(valid_time, policy)
    grid = frame.grid

    r0, r1, c0, c1 = 0, frame.values.shape[0], 0, frame.values.shape[1]
    if bbox is not None:
        south, west, north, east = bbox
        lons = [west, east, west, east]
        lats = [south, south, north, north]
        cols, rows = grid.lonlat_to_colrow(np.array(lons), np.array(lats))
        r0, r1 = max(0, int(np.min(rows))), min(r1, int(np.ceil(np.max(rows))) + 1)
        c0, c1 = max(0, int(np.min(cols))), min(c1, int(np.ceil(np.max(cols))) + 1)

    values = np.asarray(frame.values)[r0:r1, c0:c1]
    nodata = np.asarray(frame.nodata)[r0:r1, c0:c1]
    rgb = np.full(values.shape + (3,), 255, dtype=np.uint8)
    detected = np.isfinite(values)
    rgb[detected] = _colourise(values, _DBZ_STOPS)[detected]
    rgb[nodata] = (205, 205, 210)

    for tier in policy.tiers:
        lab = dets[tier.name].labels[r0:r1, c0:c1] > 0
        edge = lab & ~ndimage.binary_erosion(lab)
        rgb[edge] = _OUTLINE.get(tier.name, (90, 90, 90))

    img = Image.fromarray(rgb, "RGB")
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    draw = ImageDraw.Draw(img)

    li, _ = _li_frame(ws.frames, valid_time)
    if li is not None and li.lats.size:
        cols, rows = grid.lonlat_to_colrow(li.lons, li.lats)
        for c, rr in zip(np.asarray(cols) - c0, np.asarray(rows) - r0):
            if 0 <= rr < (r1 - r0) and 0 <= c < (c1 - c0):
                x, y = c * scale, rr * scale
                draw.ellipse([x - 1, y - 1, x + 1, y + 1], fill=(255, 220, 0))

    if catalogue is not None:
        for cell in catalogue["cells"]:
            if cell["tier"] == "rain20":
                continue
            x = (cell["col"] - c0) * scale
            y = (cell["row"] - r0) * scale
            if not (0 <= x < img.width and 0 <= y < img.height):
                continue
            m = cell["motion"]
            if m["status"] == "available":
                x2 = x + m["dcol_per_min"] * 30 * scale
                y2 = y + m["drow_per_min"] * 30 * scale
                draw.line([x, y, x2, y2], fill=(200, 0, 200), width=2)
                draw.ellipse([x2 - 2, y2 - 2, x2 + 2, y2 + 2], fill=(200, 0, 200))
            if cell["tier"] == "core35":
                event = {"split": "s", "merge": "m"}.get(cell["lineage"]["event"], "")
                label = f"{cell['lineage']['age_min']:.0f}{_TREND.get(cell['trend']['state'], '?')}{event}"
                draw.text((x + 3, y + 3), label, fill=(20, 20, 20))

    title = f"{valid_time:%Y-%m-%d %H:%MZ}  {policy.policy_version}"
    if catalogue is None:
        title += "  (no catalogue)"
    draw.rectangle([0, 0, 8 * len(title) + 6, 14], fill=(255, 255, 255))
    draw.text((3, 2), title, fill=(0, 0, 0))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


__all__ = ["render_frame", "SOURCE_EUMETSAT_LI"]
