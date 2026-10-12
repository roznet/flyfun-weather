"""The node marks a suspect echo's own rain ring (#702).

``rain20`` carries no clutter evidence (``clutter.assessable``), so with
suppression on a phantom core could still leave its bare rain skirt on the
ribbon.  The node knows which cores it suspects and marks the rain rings that
are nothing but their skirt (``suspect_outlines``); the droplet skips those.
These tests pin the node's rule; the droplet side is in
``tests/test_live_clutter_suppression.py``.  Since #768 a core takes its skirt
with it only when it is ``suppress``ed — flagged *and* at a site flagged on
earlier days — so the runner scene seeds that memory.
"""

from __future__ import annotations

import gzip
import json
from datetime import timedelta

import numpy as np
import pytest

from weatherbrief.observed.cells import DEFAULT_POLICY, clutter_sites
from weatherbrief.observed.cells.catalogue import catalogue_path, display_path, read_catalogue
from weatherbrief.observed.cells.display import outline_step, outlines, suspect_regions, tier_outlines
from weatherbrief.observed.cells.runner import FrameCache, Workspace, analyse_tick
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, FrameStore
from weatherbrief.observed.route_bands import _inside

from .cells_helpers import grid_frame, grid_spec, scene, write_dbzh
from .test_cells_clutter import T0, _detections, clutter_scene

SIZE = 160


def _scene() -> np.ndarray:
    """A bare clutter block, a plain rain area with no core, and a real storm."""
    field = np.zeros((SIZE, SIZE))
    field[10:74, 10:74] = clutter_scene(64)
    field += scene(SIZE, [(120, 50, 30, 10)])
    field += scene(SIZE, [(60, 120, 50, 8)], seed=3)
    return field


def _run(root, seed_sites=()):
    """The scene through the real runner; ``seed_sites`` flagged on two earlier days."""
    for back in (1, 2):
        day = T0 - timedelta(days=back)
        path = clutter_sites.day_path(root, day.date())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"valid_time": day.isoformat(), "sites": sorted(seed_sites)}) + "\n")
    write_dbzh(FrameStore(root, retain_all=True), T0, _scene())
    ws = Workspace(root)
    assert analyse_tick(ws, T0 + timedelta(minutes=1), timedelta(hours=1), DEFAULT_POLICY,
                        FrameCache(ws.frames), (SOURCE_OPERA_DBZH,)) == 1
    catalogue = read_catalogue(catalogue_path(root, T0))
    display = json.loads(gzip.decompress(display_path(root, T0).read_bytes()))
    return catalogue, display


@pytest.fixture(scope="module")
def first_seen(tmp_path_factory):
    """The scene on a site's first day: flagged, but nothing earlier to recur on."""
    return _run(tmp_path_factory.mktemp("suspect-outlines-new"))


@pytest.fixture(scope="module")
def processed(tmp_path_factory, first_seen):
    """The same scene where the flagged sites were also flagged on two earlier days."""
    sites = clutter_sites.flagged_sites(first_seen[0]["cells"], DEFAULT_POLICY.clutter)
    assert sites
    return _run(tmp_path_factory.mktemp("suspect-outlines"), sites)


def _rings_around(display, tier, lat, lon) -> list[int]:
    return [i for i, ring in enumerate(display["outlines"][tier]) if _inside(lat, lon, ring)]


def test_the_clutter_blocks_own_rain_ring_is_marked(processed):
    catalogue, display = processed
    phantom = next(c for c in catalogue["cells"] if c["tier"] == "core41" and c.get("clutter", {}).get("level") != "clear")
    marked = display["suspect_outlines"]["rain20"]
    assert marked
    assert set(_rings_around(display, "rain20", phantom["lat"], phantom["lon"])) <= set(marked)
    assert _rings_around(display, "rain20", phantom["lat"], phantom["lon"])


def test_a_first_seen_site_is_flagged_but_keeps_its_ring(first_seen):
    """#768: the evidence is the same on the first day; only the decision waits."""
    catalogue, display = first_seen
    phantom = [c for c in catalogue["cells"] if c.get("clutter", {}).get("level") not in (None, "clear")]
    assert phantom
    assert all(c["clutter"]["site_days"] == 0 and c["clutter"]["suppress"] is False for c in phantom)
    assert "suspect_outlines" not in display
    shown = [c for c in display["cells"] if c.get("clutter")]
    assert shown and all(c["clutter"]["suppress"] is False for c in shown)


def test_a_recurring_site_is_suppressed(processed):
    catalogue, _ = processed
    phantom = [c for c in catalogue["cells"] if c.get("clutter", {}).get("level") not in (None, "clear")]
    assert phantom and all(c["clutter"]["site_days"] == 2 and c["clutter"]["suppress"] for c in phantom)


def test_genuine_rain_and_a_plain_rain_area_are_not_marked(processed):
    catalogue, display = processed
    marked = set(display["suspect_outlines"]["rain20"])
    others = [c for c in catalogue["cells"] if c["tier"] == "rain20" and c["peak_dbz"] < 55.0]
    assert len(others) == 2  # the storm's rain area and the plain one
    for c in others:
        around = _rings_around(display, "rain20", c["lat"], c["lon"])
        assert around and not (set(around) & marked)


def test_the_map_still_draws_every_ring(processed):
    """Marking is not removing: the rings stay in `outlines` for every client."""
    _, display = processed
    assert len(display["outlines"]["rain20"]) == 3
    assert set(display["suspect_outlines"]) == {"rain20"}  # cores are handled per cell


def test_a_clean_frame_has_no_marks_and_the_same_trace():
    frame, dets = _detections(scene(96, [(48, 48, 52.0, 9.0)]))
    assert suspect_regions([], dets, DEFAULT_POLICY) == {}
    step = outline_step("rain20", False)
    lines, marked = tier_outlines(dets["rain20"], frame.grid, step, None)
    assert marked == []
    assert lines == outlines(dets["rain20"], frame.grid, 0, 96, 0, 96, step)


# --- The rule, with the levels set by hand --------------------------------------


def _catalogue_cells(dets, flagged_tiers=("core35", "core41"), clear_labels=(), suppress=True):
    """Catalogue-shaped cells for every detection; cores flagged unless listed clear.

    Flagged at a recurring site (``suppress``) by default: since #768 only a
    suppressed core takes its rain skirt with it.
    """
    out = []
    for name, det in dets.items():
        for cell in det.cells:
            c = {"tier": name, "label": cell.label}
            if name in flagged_tiers and (name, cell.label) not in clear_labels:
                c["clutter"] = {"level": "suspect", "site_days": 2 if suppress else 0,
                                "suppress": suppress}
            out.append(c)
    return out


def _block(field, r0, c0):
    b = clutter_scene(24)[12:18, 12:22]
    field[r0:r0 + b.shape[0], c0:c0 + b.shape[1]] = np.maximum(field[r0:r0 + b.shape[0], c0:c0 + b.shape[1]], b)


def test_a_suspect_core_in_a_broad_rain_area_leaves_it_alone():
    """The rain is far bigger than the core: it is weather, and keeps its band."""
    field = scene(96, [(48, 48, 30.0, 14.0)])
    _block(field, 45, 43)
    _, dets = _detections(field)
    assert dets["core35"].cells
    assert suspect_regions(_catalogue_cells(dets), dets, DEFAULT_POLICY) == {}


def test_one_genuine_core_keeps_the_ring():
    field = np.zeros((96, 96))
    _block(field, 40, 30)
    _block(field, 40, 41)
    field[40:46, 40:41] = 25.0  # a 1 px rain bridge: two cores, one rain20 region
    _, dets = _detections(field)
    rain = dets["rain20"]
    assert len(rain.cells) == 1 and len(dets["core35"].cells) == 2
    first = min(dets["core35"].cells, key=lambda c: c.centroid_col).label
    cells = _catalogue_cells(dets, clear_labels={("core35", first)})
    assert suspect_regions(cells, dets, DEFAULT_POLICY) == {}
    # ... and with both cores suspect the same ring is the echo's own.
    assert suspect_regions(_catalogue_cells(dets), dets, DEFAULT_POLICY) == {"rain20": {rain.cells[0].label}}


def test_nothing_is_marked_without_a_suspect_core():
    field = np.zeros((96, 96))
    _block(field, 40, 30)
    _, dets = _detections(field)
    assert suspect_regions(_catalogue_cells(dets, flagged_tiers=()), dets, DEFAULT_POLICY) == {}
    assert suspect_regions(_catalogue_cells(dets), dets, DEFAULT_POLICY) == {"rain20": {1}}
    # Flagged but at a new site (#768): evidence only, the ring stays.
    assert suspect_regions(_catalogue_cells(dets, suppress=False), dets, DEFAULT_POLICY) == {}
