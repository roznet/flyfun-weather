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
def test_reference_case_from_real_frames():
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
