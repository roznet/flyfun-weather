"""Clutter evidence per cell (#696).

The scenes are **synthetic but shaped from the measurement**: the repository
commits no real OPERA pixels (``make_fixtures.py``), so the reference case is
reproduced from the numbers in the issue's own table — a flat 45-57 dBZ block
of 2 km-duplicated pixels sitting in air the radar looked at and found empty,
with no gradient at its edge.  ``test_reference_case_from_real_frames`` is the
check against the actual frames; it runs only where they exist.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.observed.cells.clutter import (
    CLEAR,
    CONFIRMED,
    SUSPECT,
    assess,
    assessable,
    rescore,
    round_features,
    score_cell,
    suspect,
)
from weatherbrief.observed.cells.detect import detect
from weatherbrief.observed.cells.policy import DEFAULT_POLICY, ClutterPolicy

from .cells_helpers import grid_frame, scene

T0 = datetime(2026, 10, 7, 0, 25, tzinfo=timezone.utc)
SIZE = 96
CLUTTER = DEFAULT_POLICY.clutter


def clutter_scene(size: int = SIZE, peak: float = 57.0) -> np.ndarray:
    """A flat, sharp-edged block in clear air, on 2 km-duplicated pixels.

    The shape measured on the reference case: a ~3 x 7 km block whose values
    repeat in 2 x 2 squares (the French contribution arrives on a 2 km grid
    duplicated onto the 1 km composite) with 0-4 dBZ or undetect immediately
    outside it.  Nothing here is a Gaussian: that is the point.
    """
    field = np.zeros((size, size))
    block = np.array([
        [45.0, 52.0, 57.0, 57.0, 52.0],
        [45.0, 52.0, 57.0, 57.0, 52.0],
        [45.0, 50.0, 50.0, 48.0, 48.0],
    ])
    block = np.repeat(np.repeat(block, 2, axis=0), 2, axis=1)  # 2 km -> 1 km
    block *= peak / 57.0
    r0, c0 = size // 2, size // 2
    field[r0:r0 + block.shape[0], c0:c0 + block.shape[1]] = block
    return field


def weather_scene(size: int = SIZE) -> np.ndarray:
    """A genuine core: a textured blob embedded in its own broad rain area."""
    return scene(size, [(size // 2, size // 2, 52.0, 9.0)])


def _detections(values: np.ndarray, valid_time: datetime = T0, nodata=None):
    frame = grid_frame(values, valid_time, nodata=nodata)
    return frame, {tier.name: detect(frame, tier) for tier in DEFAULT_POLICY.tiers}


def _assess(values, *, earlier=None, nodata=None, flashes=None, tops=None,
            tier: str = "core41", policy: ClutterPolicy = CLUTTER, valid_time=T0):
    frame, dets = _detections(values, valid_time, nodata=nodata)
    det = dets[tier]
    n = len(det.cells)
    assert n, f"the {tier} scene produced no cell to assess"
    earlier_frame = grid_frame(earlier, valid_time - timedelta(minutes=5)) if earlier is not None else None
    return det, assess(
        det, frame,
        rain_labels=dets["rain20"].labels,
        earlier=earlier_frame,
        quality=None,
        flashes=[0] * n if flashes is None else flashes,
        tops=[None] * n if tops is None else tops,
        policy=policy,
    )


def _biggest(det, evidence):
    """The evidence for the scene's largest cell."""
    k = max(range(len(det.cells)), key=lambda i: det.cells[i].area_px)
    return det.cells[k], evidence[k]


# --- The two scenes separate -------------------------------------------------


def test_the_clutter_block_is_flagged_with_its_reasons():
    det, evidence = _assess(clutter_scene())
    cell, found = _biggest(det, evidence)
    assert found.level in (SUSPECT, CONFIRMED)
    fired = {r["feature"] for r in found.reasons if r["points"] > 0}
    # The isolation evidence is what must carry it, not the corroborators.
    assert fired & {"ring_rain", "rain_ratio"}, found.reasons
    assert found.features["ring_rain"] <= CLUTTER.ring_rain_bare


def test_a_genuine_embedded_core_stays_clear():
    det, evidence = _assess(weather_scene())
    cell, found = _biggest(det, evidence)
    assert found.level == CLEAR
    assert found.score == 0.0
    # Not "clear because nothing was measured": the ring really is full of rain.
    assert found.features["ring_rain"] > CLUTTER.ring_rain_thin
    assert found.features["rain_ratio"] > CLUTTER.rain_ratio_thin


def test_the_single_pixel_peak_is_recorded_next_to_the_robust_one():
    """A one-pixel spike on a modest core: ``peak_dbz`` jumps, the median does not."""
    spike = (SIZE // 2 + 3, SIZE // 2 + 3)
    plain = weather_scene()
    spiked = plain.copy()
    spiked[spike] = 70.0

    def at_spike(values):
        det, evidence = _assess(values)
        label = det.labels[spike]
        assert label > 0, "the spike must land inside a detected core"
        return det.cells[label - 1], evidence[label - 1]

    before_cell, before = at_spike(plain)
    after_cell, after = at_spike(spiked)

    # One pixel moves the headline number by more than 13 dB...
    assert after_cell.peak_dbz - before_cell.peak_dbz > 13.0
    assert after_cell.peak_dbz == pytest.approx(70.0, abs=0.6)
    # ...and leaves the robust peak where it was.
    assert after.features["robust_peak_dbz"] == pytest.approx(
        before.features["robust_peak_dbz"], abs=1.0)
    assert after.features["robust_drop"] > 13.0


# --- The rules ----------------------------------------------------------------


def test_a_filament_has_no_robust_peak_rather_than_a_sentinel_one():
    """A cell too thin for a 3x3 median reports ``None``, not -999.

    One core in 60,973 on the 2026-10-03/04 replay was a 2-pixel-wide filament
    whose every pixel's window was majority-NaN.  It wrote
    ``robust_peak_dbz: -999`` into its catalogue and earned a point from a
    1044 dB ``robust_drop``.  Unknown costs it the corroborator; a sentinel
    invented one.
    """
    values = np.zeros((SIZE, SIZE))
    # A one-pixel-wide diagonal thread of core: every window is mostly empty.
    for i in range(12):
        values[SIZE // 2 + i, SIZE // 2 + i] = 50.0
    det, evidence = _assess(values, tier="core35")
    cell, found = _biggest(det, evidence)
    assert found.features["robust_peak_dbz"] is None
    assert found.features["robust_drop"] is None
    assert "robust_drop" in found.unknown
    assert not any(r["feature"] == "robust_drop" for r in found.reasons)


def test_lightning_vetoes_the_whole_score():
    det, plain = _assess(clutter_scene())
    _, flagged = _biggest(det, plain)
    assert flagged.level != CLEAR

    det, vetoed = _assess(clutter_scene(), flashes=[3] * len(det.cells))
    _, found = _biggest(det, vetoed)
    assert found.level == CLEAR
    assert found.score == 0.0
    assert [r["feature"] for r in found.reasons] == ["flashes"]


def test_a_cloud_top_vetoes_the_whole_score():
    det, _ = _assess(clutter_scene())
    det, vetoed = _assess(clutter_scene(), tops=[250.0] * len(det.cells))
    _, found = _biggest(det, vetoed)
    assert found.level == CLEAR


def test_no_suspicion_without_the_isolation_evidence():
    """Corroborators cannot convict on their own (rule 1).

    A sudden, single-pixel-peaked, discontinuous core *embedded in rain* has
    every corroborator against it and must still come out clear: those features
    have no measured separation and would flag real growing showers.
    """
    values = weather_scene()
    values[SIZE // 2 + 4, SIZE // 2 + 4] = 70.0
    det, evidence = _assess(values, earlier=np.zeros((SIZE, SIZE)))
    cell, found = _biggest(det, evidence)
    assert found.features["onset_db"] >= CLUTTER.onset_sudden_db
    assert found.level == CLEAR
    assert found.score == 0.0


def test_a_ring_the_radar_cannot_see_is_unknown_not_bare():
    """Rule 2: a ring of ``nodata`` is "we cannot see", never "no rain there"."""
    values = clutter_scene()
    # Everything is nodata except the block's own pixels, so its ring has
    # fewer than `min_ring_px` covered pixels to speak for it.
    nodata = np.ones(values.shape, dtype=bool)
    nodata[values > 0] = False
    det, evidence = _assess(values, nodata=nodata)
    cell, found = _biggest(det, evidence)
    assert found.features["ring_rain"] is None
    assert "ring_rain" in found.unknown
    assert found.level == CLEAR


def test_missing_evidence_never_adds_points():
    features = round_features({"ring_rain": 0.0, "rain_ratio": 1.0, "onset_db": None,
                               "robust_drop": None, "continuity": None})
    found = score_cell(features, CLUTTER, peak_dbz=57.0, flashes=None, top_fl=None)
    assert set(found.unknown) == {"onset_db", "robust_drop", "continuity"}
    assert found.score == CLUTTER.w_ring_bare + CLUTTER.w_ratio_bare


def test_rain20_is_not_assessed():
    """The 20 dBZ tier's ring is below 20 dBZ by construction (it is where the
    area ends), so every rain area in Europe would read as isolated."""
    policy = DEFAULT_POLICY
    assert not assessable(policy.tier("rain20"), CLUTTER)
    assert assessable(policy.tier("core35"), CLUTTER)
    assert assessable(policy.tier("core41"), CLUTTER)


def test_the_kill_switch_measures_nothing():
    off = ClutterPolicy(enabled=False)
    det, evidence = _assess(clutter_scene(), policy=off)
    assert [e.level for e in evidence] == [CLEAR] * len(det.cells)
    assert all(not e.features for e in evidence)


# --- Determinism and the amend path -------------------------------------------


def test_rescoring_a_published_cell_agrees_with_scoring_the_frame():
    """The lightning amend re-scores from the stored features (#666).

    Features are rounded before they are scored precisely so this holds, and
    it is what keeps an amended catalogue equal to its replay.
    """
    det, evidence = _assess(clutter_scene())
    k = max(range(len(det.cells)), key=lambda i: det.cells[i].area_px)
    # `_assess` scored these cells with an observed zero, so the published cell
    # must say zero too: re-scoring against a *different* observation is the
    # amend, which the next test covers.
    published = {"peak_dbz": det.cells[k].peak_dbz, "flashes": 0, "top_fl": None,
                 "clutter": evidence[k].as_dict()}
    assert rescore(published, CLUTTER) == evidence[k].as_dict()


def test_a_late_flash_clears_a_published_suspicion():
    # Published with lightning still pending (#666): `flashes` is None, which
    # is not an observation of zero and so cannot veto.
    frame, dets = _detections(clutter_scene())
    det = dets["core41"]
    _, evidence = _assess(clutter_scene(), flashes=[None] * len(det.cells))
    k = max(range(len(det.cells)), key=lambda i: det.cells[i].area_px)
    published = {"peak_dbz": det.cells[k].peak_dbz, "flashes": None, "top_fl": None,
                 "clutter": evidence[k].as_dict()}
    assert published["clutter"]["level"] != CLEAR
    published["flashes"] = 2
    revised = rescore(published, CLUTTER)
    assert revised["level"] == CLEAR


def test_rescore_leaves_an_unassessed_cell_alone():
    assert rescore({"peak_dbz": 42.0, "flashes": 0}, CLUTTER) is None


def test_suspect_reads_both_levels_and_tolerates_an_old_cell():
    assert suspect({"clutter": {"level": SUSPECT}})
    assert suspect({"clutter": {"level": CONFIRMED}})
    assert not suspect({"clutter": {"level": CLEAR}})
    assert not suspect({})  # a pre-#696 display file


# --- The real reference case, where the frames are available ------------------

REAL_FRAMES_ENV = "WB_CELLS_CLUTTER_FRAMES"
#: The cells the issue names, and the frame each is seen on.
REFERENCE_CELLS = {
    datetime(2026, 10, 7, 0, 10, tzinfo=timezone.utc): "core41-20261007T0010-0089",
    datetime(2026, 10, 7, 0, 20, tzinfo=timezone.utc): "core41-20261007T0020-0097",
    datetime(2026, 10, 7, 0, 25, tzinfo=timezone.utc): "core35-20261007T0025-0115",
}


@pytest.mark.skipif(not os.environ.get(REAL_FRAMES_ENV),
                    reason=f"set {REAL_FRAMES_ENV} to an archive root holding the "
                           "2026-10-07 00:00-00:40Z OPERA frames")
def test_reference_case_from_real_frames(monkeypatch):
    """The issue's acceptance criterion, against the frames themselves.

    Not committed data: OPERA frames are not redistributable from this
    repository (``make_fixtures.py``), so this runs against an archive root —
    the mini's, or one filled from the 24 h S3 cache.
    """
    from pathlib import Path

    from weatherbrief.observed.cells.catalogue import catalogue_path, read_catalogue
    from weatherbrief.observed.cells.runner import FrameCache, Workspace, process_frame

    root = Path(os.environ[REAL_FRAMES_ENV])
    workspace = Workspace(root=root)
    cache = FrameCache(workspace.frames)
    t = datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)
    while t <= max(REFERENCE_CELLS):
        process_frame(workspace, t, DEFAULT_POLICY, cache=cache, sources=("opera_dbzh",))
        t += timedelta(minutes=5)

    for valid_time, cell_id in REFERENCE_CELLS.items():
        catalogue = read_catalogue(catalogue_path(root, valid_time))
        assert catalogue is not None, f"no catalogue for {valid_time}"
        cell = next((c for c in catalogue["cells"] if c["id"] == cell_id), None)
        assert cell is not None, f"{cell_id} not found at {valid_time}"
        assert suspect(cell), f"{cell_id} was not flagged: {cell.get('clutter')}"

    # #702: with suppression on, the LFAT -> LFQA ribbon has no band of any
    # tier from that echo — its cores are dropped per cell and its own rain
    # skirt is marked by the node.  Band ids are "<tier>:<ring index>".
    import gzip
    import json

    from weatherbrief.analysis.route_geometry import RouteTrack
    from weatherbrief.models.analysis import RouteConfig, Waypoint
    from weatherbrief.observed.cells.catalogue import display_path
    from weatherbrief.observed.route_bands import TIERS, _inside, build_weather_bands

    track = RouteTrack.from_route(RouteConfig(
        name="LFAT-LFQA",
        waypoints=[Waypoint(icao="LFAT", name="LFAT", lat=50.515, lon=1.6275),
                   Waypoint(icao="LFQA", name="LFQA", lat=49.2067, lon=4.1567)],
        flight_duration_hours=1.0,
    ))
    monkeypatch.setenv("WB_CELLS_CLUTTER_SUPPRESS", "1")
    for valid_time, cell_id in REFERENCE_CELLS.items():
        catalogue = read_catalogue(catalogue_path(root, valid_time))
        cell = next(c for c in catalogue["cells"] if c["id"] == cell_id)
        display = json.loads(gzip.decompress(display_path(root, valid_time).read_bytes()))
        phantom = {f"{TIERS[tier]}:{i}" for tier in TIERS
                   for i, ring in enumerate(display["outlines"].get(tier) or [])
                   if _inside(cell["lat"], cell["lon"], ring)}
        bands = {b.id for b in build_weather_bands(display, track)}
        assert not (bands & phantom), f"{valid_time}: bands {sorted(bands & phantom)} from {cell_id}"


# --- The validation harness's own logic (#696) --------------------------------


def _row(valid_time, lat, lon, *, level=CONFIRMED, flashes=None, tier="core41", peak=55.0):
    return {"valid_time": valid_time, "id": f"{tier}-{valid_time}-{lat}", "tier": tier,
            "lat": lat, "lon": lon, "area_km2": 20.0, "peak_dbz": peak,
            "robust_peak_dbz": peak - 1.0, "flashes": flashes, "top_fl": None,
            "age_min": 10.0, "motion_status": "unsupported", "level": level, "score": 6.0,
            "reasons": ["ring_rain"], "unknown": [], "features": {}, "label": "unknown"}


def test_sites_groups_flags_by_ground_position_across_days():
    from weatherbrief.observed.cells.clutter_eval import sites

    rows = [
        # One place, three separate days — the recurrence that corroborates.
        _row("2026-10-03T09:00:00+00:00", 59.94, 5.37),
        _row("2026-10-05T23:30:00+00:00", 59.93, 5.38),
        _row("2026-10-07T00:25:00+00:00", 59.94, 5.37),
        # A one-off somewhere else.
        _row("2026-10-03T09:00:00+00:00", 44.70, 17.10),
        # Not flagged: must not appear at all.
        _row("2026-10-03T09:00:00+00:00", 50.00, 2.00, level=CLEAR),
    ]
    report = sites(rows, precision=0.1, min_days=2)
    assert report["n_sites"] == 2
    assert report["n_persistent"] == 1
    assert report["hits_at_persistent_sites"] == 3
    assert report["hits_total"] == 4
    persistent = report["sites"][0]
    assert persistent["n_days"] == 3
    assert persistent["days"] == ["2026-10-03", "2026-10-05", "2026-10-07"]
    assert persistent["hours_utc"] == [0, 9, 23]


def test_sites_puts_a_lightning_bearing_site_first_to_be_doubted():
    """A flagged site that ever showed lightning is the one to look at, so it
    sorts above the most persistent clean site rather than below it."""
    from weatherbrief.observed.cells.clutter_eval import sites

    rows = [
        _row("2026-10-03T09:00:00+00:00", 59.94, 5.37, flashes=0),
        _row("2026-10-05T09:00:00+00:00", 59.94, 5.37, flashes=0),
        _row("2026-10-07T09:00:00+00:00", 59.94, 5.37, flashes=0),
        _row("2026-10-03T14:00:00+00:00", 45.00, 9.00, flashes=7),
    ]
    report = sites(rows)
    assert report["sites"][0]["flashes_max"] == 7
    assert [s["lat"] for s in report["sites_with_lightning"]] == [45.0]


def test_rows_all_pools_a_root_without_a_time_window(tmp_path):
    """Pooling separately replayed spans is the point; globbing the catalogue
    tree means a report needs no window and cannot silently clip a day."""
    import gzip
    import json

    from weatherbrief.observed.cells.clutter_eval import rows_all

    for day, stamp in (("20261003", "20261003T0900"), ("20261007", "20261007T0025")):
        path = tmp_path / "cells" / "catalogues" / day / f"{stamp}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        catalogue = {
            "policy_version": DEFAULT_POLICY.policy_version,
            "valid_time": f"{day[:4]}-{day[4:6]}-{day[6:]}T00:25:00+00:00",
            "cells": [{"id": "core41-x", "tier": "core41", "lat": 50.0, "lon": 2.0,
                       "peak_dbz": 55.0, "flashes": None, "top_fl": None,
                       "clutter": {"level": CONFIRMED, "score": 6.0, "features": {}}}],
        }
        path.write_bytes(gzip.compress(json.dumps(catalogue).encode(), mtime=0))
    assert len(rows_all(tmp_path, DEFAULT_POLICY)) == 2


def test_rows_all_skips_another_policys_catalogues(tmp_path):
    """Mixing two rules into one average is the mistake the whole harness exists
    to avoid, so a foreign catalogue is skipped rather than read."""
    import gzip
    import json

    from weatherbrief.observed.cells.clutter_eval import rows_all

    path = tmp_path / "cells" / "catalogues" / "20261003" / "20261003T0900.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps({
        "policy_version": "cells-2+deadbeef", "valid_time": "2026-10-03T09:00:00+00:00",
        "cells": [{"id": "core41-x", "tier": "core41", "lat": 50.0, "lon": 2.0,
                   "peak_dbz": 55.0, "clutter": {"level": CONFIRMED, "score": 6.0}}],
    }).encode(), mtime=0))
    assert rows_all(tmp_path, DEFAULT_POLICY) == []
