"""Literal synthetic translations exercise matching, never source registration."""
from datetime import datetime, timedelta, timezone
import importlib

import numpy as np
import pytest
from scipy.ndimage import shift
from shapely.geometry import LineString

from weatherbrief.models.observed_motion import FrameRecord, GeolocationRecord, Interval
from weatherbrief.observed.motion.geometry import AnalysisGrid
from weatherbrief.observed.motion.history import AnalysisFrame


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
GRID = AnalysisGrid("+proj=aeqd +lat_0=50 +lon_0=0 +datum=WGS84 +units=m", (0., 50.),
                    0., 0., 128, 128, 2000.)
ROUTE = LineString([(120000., 0.), (120000., 256000.)])


def tracker():
    try:
        return importlib.import_module("weatherbrief.observed.motion.tracking").track_history
    except ModuleNotFoundError:
        pytest.fail("independent conservative tracking is absent")


def frames(offsets, *, source="opera_dbzh", seconds=None, high_contrast=False):
    base = np.zeros(GRID.shape)
    if source == "eumetsat_ctth":
        base[52:69, 52:69] = 1.
    else:
        base[52:69, 52:69] = np.random.default_rng(317).uniform(.2, 1., (17, 17))
        if high_contrast:
            base[52:69, 52:69] = np.random.default_rng(317).choice([.01, .99], (17, 17))
    result = []
    for i, (dx, dy) in enumerate(offsets):
        at = NOW + timedelta(seconds=seconds[i] if seconds else i*300)
        descriptor = shift(base, (dy, dx), order=1, mode="constant", prefilter=False)
        detected = descriptor > 0
        record = FrameRecord(frame_id=f"{source}-{i}", content_id=f"synthetic-{i}",
                             product_id="synthetic", decoder_version="synthetic-v1", grid_id="synthetic-grid",
                             valid_at=at, received_at=at, reference_at=at,
                             acquisition_window=Interval(start_at=at-timedelta(seconds=60), end_at=at))
        geo = GeolocationRecord(status="unverified", reason_codes=["geolocation_unverified"],
                                evidence_id=None, method=None, method_version=None, applicability_id=None)
        result.append(AnalysisFrame(source, record.frame_id, at, GRID, descriptor,
                                    np.ones(GRID.shape, dtype=bool), detected,
                                    np.where(detected, 5.+60.*descriptor, np.nan), None, record, geo))
    return result


def test_clean_stationary_echo_is_not_unknown():
    result = tracker()(frames([(0, 0)]*3), route_geometry=ROUTE)
    assert len(result.tracks) == 1
    track = result.tracks[0]
    assert track.velocity_xy_m_s == pytest.approx((0., 0.), abs=1e-10)
    assert len(track.history) == 3
    assert len(track.pair_diagnostics) == 2
    assert all(p.status == "available" and len(p.patches) == 4 for p in track.pair_diagnostics)
    assert all(p.refinement == "integer" for pair in track.pair_diagnostics for p in pair.patches)


@pytest.mark.parametrize("source,offsets,want", [
    ("opera_dbzh", [(0, 0), (2, -1), (4, -2)], (40./3., -20./3.)),
    ("eumetsat_ctth", [(0, 0), (-2, 1), (-4, 2)], (-40./3., 20./3.)),
])
def test_independent_literal_translation(source, offsets, want):
    track = tracker()(frames(offsets, source=source), route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s == pytest.approx(want, abs=.1)
    assert track.source_id == source
    assert track.history[-1].frame_id == f"{source}-2"
    assert track.footprint.area == 1156000000.


def test_failed_latest_match_cannot_reuse_old_velocity():
    history = frames([(0, 0), (2, 0), (4, 0), (6, 0)])
    history[-1].detected[:, 66:68] = False
    history[-1].descriptor[:, 66:68] = 0.
    result = tracker()(history, route_geometry=ROUTE)
    assert len(result.tracks) == 2
    assert all(t.velocity_xy_m_s is None for t in result.tracks)
    assert all("lineage_ambiguous" in t.reason_codes for t in result.tracks)


def test_unknown_history_is_observed_only_not_stationary():
    history = frames([(0, 0)]*3)
    history[0].known[:] = False
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert track.footprint.area > 0
    assert track.reason_codes


def test_expired_entry_budget_leaves_counts_unknown_not_zero_detections():
    result = tracker()(frames([(0, 0)]*3), route_geometry=ROUTE, deadline=0.)
    assert result.tracks == []
    assert "compute_deadline" in result.reason_codes
    count = result.counts[0]
    assert count.full_field_detections is None
    assert count.small_detections is None
    assert count.eligible_candidates is None
    assert count.selected_candidates is None
    assert count.emitted_observed_features == 0
    assert count.omitted_observed_features is None
    assert not count.counts_complete and not count.selection_complete


def test_two_sources_keep_opposing_vectors_without_fused_identity():
    radar = frames([(0, 0), (2, 0), (4, 0)])
    cloud = frames([(0, 0), (-2, 0), (-4, 0)], source="eumetsat_ctth")
    result = tracker()([*radar, *cloud], route_geometry=ROUTE)
    assert len(result.tracks) == 2
    by_source = {t.source_id: t for t in result.tracks}
    assert by_source["opera_dbzh"].velocity_xy_m_s == pytest.approx((40./3., 0.), abs=.1)
    assert by_source["eumetsat_ctth"].velocity_xy_m_s == pytest.approx((-40./3., 0.), abs=.1)
    assert len({t.feature_id for t in result.tracks}) == 2


def test_fractional_translation_retains_continuous_displacements():
    history = frames([(0, 0), (1.25, -.25), (2.5, -.5)], high_contrast=True)
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s == pytest.approx((25./3., -5./3.), abs=1.5)
    assert any(p.refinement == "quadratic" and abs(p.dx_cells-round(p.dx_cells)) > .05
               for pair in track.pair_diagnostics for p in pair.patches if p.status == "available")


def test_actual_elapsed_times_are_used_for_velocity():
    history = frames([(0, 0), (2, 0), (5, 0)], seconds=[0, 200, 500])
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s == pytest.approx((20., 0.), abs=.1)
    assert [p.elapsed_seconds for p in track.pair_diagnostics] == [200., 300.]


def test_oldest_failed_pair_allows_only_clean_latest_three_frame_suffix():
    history = frames([(0, 0), (2, 0), (4, 0), (6, 0)])
    history[0].known[:] = False
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s == pytest.approx((40./3., 0.), abs=.1)
    assert [s.frame_id for s in track.history] == ["opera_dbzh-1", "opera_dbzh-2", "opera_dbzh-3"]


def test_previous_pair_predicts_next_observation_before_in_sample_fit():
    # d01=0, d12=4 cells: 4 > 2sqrt(2), although the final in-sample
    # three-point linear-fit RMS is only sqrt(8/9) cells.
    track = tracker()(frames([(0, 0), (0, 0), (4, 0)]), route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "next_observation_inconsistent" in track.reason_codes
    assert track.pair_diagnostics[0].next_observation_residual_cells == pytest.approx(4., abs=.05)
    assert track.pair_diagnostics[-1].next_observation_residual_cells is None


@pytest.mark.parametrize("step", [9, 10])
def test_search_boundary_and_excessive_speed_remain_observed_only(step):
    # 9 cells / 300 seconds = the exact 60m/s search boundary.
    track = tracker()(frames([(0, 0), (step, 0), (2*step, 0)]), route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert track.footprint.area == 1156000000.
    assert track.reason_codes


def test_full_field_counts_include_small_and_candidate_capped_detections():
    history = frames([(0, 0)])
    latest = history[0]
    latest.detected[:] = False
    latest.descriptor[:] = 0.
    for row in (10, 30, 50, 70, 90):
        for col in (10, 24, 38, 52, 66, 80, 94, 108):
            latest.detected[row:row+3, col:col+3] = True
            latest.descriptor[row:row+3, col:col+3] = 1.
    latest.detected[120, 120] = True
    route = LineString([(222000., 0.), (222000., 256000.)])
    result = tracker()(history, route_geometry=route)
    count = result.counts[0]
    assert count.full_field_detections == 41
    assert count.small_detections == 1 and count.eligible_candidates == 40
    assert count.selected_candidates == 32 and count.emitted_observed_features == 32
    assert count.omitted_observed_features == 9
    assert count.counts_complete and count.selection_complete
    assert "selection_limit" in result.reason_codes
    assert result.tracks[0].footprint.bounds == (216000., 20000., 222000., 26000.)


def test_small_detection_is_retained_with_no_motion():
    history = frames([(0, 0)]*3)
    for f in history:
        f.detected[:] = False
        f.descriptor[:] = 0.
        f.detected[60:62, 60:62] = True
        f.descriptor[60:62, 60:62] = 1.
    result = tracker()(history, route_geometry=ROUTE)
    assert len(result.tracks) == 1
    assert result.tracks[0].velocity_xy_m_s is None
    assert result.tracks[0].reason_codes == ("small_feature",)
    assert result.counts[0].selected_candidates == 0
    assert result.counts[0].small_detections == 1


def test_budget_exit_after_latest_observation_preserves_positives_and_counts(monkeypatch):
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    ticks = iter(range(100))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    result = module.track_history(frames([(0, 0)]*3), route_geometry=ROUTE, deadline=5.)
    assert len(result.tracks) == 1
    assert result.tracks[0].velocity_xy_m_s is None
    assert "compute_deadline" in result.tracks[0].reason_codes
    assert "lineage_not_evaluated" in result.reason_codes
    count = result.counts[0]
    assert count.full_field_detections == 1 and count.emitted_observed_features == 1
    assert count.omitted_observed_features == 0
    assert count.counts_complete and count.selection_complete


def test_feature_rim_unknown_with_clean_far_side_patches_cannot_propagate():
    history = frames([(0, 0)]*3)
    texture = np.random.default_rng(29).uniform(.2, 1., (41, 41))
    for f in history:
        f.descriptor[:] = 0.
        f.detected[:] = False
        f.descriptor[40:81, 40:81] = texture
        f.detected[40:81, 40:81] = True
        f.known[60, 81] = False
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "coverage_clipped" in track.reason_codes
    assert any(len(p.patches) == 4 and all(q.status == "available" for q in p.patches)
               for p in track.pair_diagnostics)


def test_low_texture_is_diagnosed_without_fabricated_zero():
    history = frames([(0, 0)]*3, source="eumetsat_ctth")
    for f in history:
        f.detected[:] = True
        f.descriptor[:] = 1.
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "low_texture" in track.reason_codes
    assert track.pair_diagnostics[-1].status == "unavailable"


def test_insufficient_fixed_support_reports_match_failure_not_just_short_history():
    history = frames([(0, 0)]*3)
    history[-1].known[::3, ::3] = False
    # Keep the actual feature supported: remove only template/background support.
    history[-1].known[52:69, 52:69] = True
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "insufficient_usable_patches" in track.reason_codes


def test_small_unselected_parent_still_blocks_unique_lineage():
    history = frames([(0, 0)]*3)
    # Main component grows a four-cell bridge into a previously separate
    # single-cell positive. That tiny parent must not disappear at the 9-cell cap.
    for f in history[:2]:
        f.detected[60, 71] = True
        f.descriptor[60, 71] = .00000001
    history[-1].detected[60, 69:72] = True
    history[-1].descriptor[60, 69:72] = .00000001
    result = tracker()(history, route_geometry=ROUTE)
    assert len(result.tracks) == 1
    track = result.tracks[0]
    assert track.velocity_xy_m_s is None
    assert "lineage_ambiguous" in track.reason_codes
    assert track.pair_diagnostics[-1].plausible_parent_count == 2
    assert track.pair_diagnostics[-1].lineage_complete


def test_fit_uses_matched_displacements_not_growing_contour_centroids():
    history = frames([(0, 0)]*3)
    for index, f in enumerate(history):
        f.detected[59:62, 69:69+5*index] = True
        f.descriptor[59:62, 69:69+5*index] = .000000001
        f.values[59:62, 69:69+5*index] = 5.00000006
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.history[-1].footprint.centroid.x-track.history[0].footprint.centroid.x > 2000.
    assert track.velocity_xy_m_s == pytest.approx((0., 0.), abs=1e-9)
    assert track.pair_diagnostics[-1].area_ratio > 1.


def test_masked_unknown_values_cannot_influence_displacement():
    clean = frames([(0, 0), (2, 0), (4, 0)])
    poisoned = frames([(0, 0), (2, 0), (4, 0)])
    for a, b in zip(clean, poisoned):
        a.known[38, 38] = b.known[38, 38] = False
        a.descriptor[38, 38] = np.nan
        b.descriptor[38, 38] = 1e100
    first = tracker()(clean, route_geometry=ROUTE).tracks[0]
    second = tracker()(poisoned, route_geometry=ROUTE).tracks[0]
    assert first.velocity_xy_m_s == pytest.approx((40./3., 0.), abs=.1)
    assert second.velocity_xy_m_s == first.velocity_xy_m_s
    assert [p.model_dump() for p in second.pair_diagnostics] == [p.model_dump() for p in first.pair_diagnostics]


def test_fixed_mask_excludes_unknown_under_any_searched_shift():
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    first, last = frames([(0, 0)]*2)
    last.known[60, 70] = False
    support = module._fixed_support(first, last, 9.)
    assert not support[60, 61]  # Unknown under +9-column searched shift.
    assert not support[60, 62]  # Unknown under +8-column searched shift.
    assert support[60, 60]      # Unknown is outside the circular search disk.
    assert not support[8, 60]   # Domain exterior participates as unknown too.
    assert support[9, 60]


@pytest.mark.parametrize("kind,want", [
    ("negative_definite", (.25, -.2, "quadratic")),
    ("positive_definite", (0., 0., "integer")),
    ("outside_half_cell", (0., 0., "integer")),
    ("not_finite", (0., 0., "integer")),
])
def test_quadratic_refinement_uses_only_valid_local_peak(kind, want):
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    y, x = np.mgrid[-1:2, -1:2]
    scores = 1.-(x-.25)**2-2.*(y+.2)**2
    if kind == "positive_definite":
        scores = -scores
    elif kind == "outside_half_cell":
        scores = 1.-(x-.6)**2-y**2
    elif kind == "not_finite":
        scores[0, 0] = -np.inf
    dx, dy, resolution = module._refine(scores, 1, 1)
    assert (dx, dy) == pytest.approx(want[:2])
    assert resolution == want[2]


def test_reverse_estimates_are_diagnostics_not_added_to_forward_fit():
    track = tracker()(frames([(0, 0), (1.25, -.5), (2.5, -1.)], high_contrast=True), route_geometry=ROUTE).tracks[0]
    first, second = track.pair_diagnostics
    # For t=[0,300,600], a free-intercept least-squares slope is endpoint
    # cumulative displacement /600. Reverse estimates are NOT endpoints.
    assert track.velocity_xy_m_s == pytest.approx((
        (first.forward_dx_cells+second.forward_dx_cells)*10./3.,
        (first.forward_dy_cells+second.forward_dy_cells)*10./3.,
    ), abs=1e-12)
    assert any(abs(pair.forward_dx_cells + sum(p.dx_cells for p in pair.patches
                   if p.direction == "reverse")/2.) > .001 for pair in track.pair_diagnostics)


def test_fractional_lineage_overlap_counts_exact_cell_areas_without_rounding():
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    mask = np.zeros((4, 4), dtype=bool)
    mask[1, 1] = True
    labels = np.zeros((4, 4), dtype=int)
    labels[1, 1], labels[1, 2], labels[2, 1], labels[2, 2] = 1, 2, 3, 4
    overlap = module._overlaps(mask, labels, .25, .5, None)
    assert overlap.tolist() == [0., .375, .125, .375, .125]


def test_deadline_during_lineage_cannot_report_unique_match(monkeypatch):
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    actual = module.check_deadline
    expired = False

    def check(deadline):
        if expired:
            raise ValueError("compute_deadline")
        actual(deadline)

    original = module._overlaps

    def expire_after_first_overlap(*args):
        nonlocal expired
        result = original(*args)
        expired = True
        return result

    # Fault injection targets the cooperative budget boundary, not match output.
    monkeypatch.setattr(module, "check_deadline", check)
    monkeypatch.setattr(module, "_overlaps", expire_after_first_overlap)
    result = module.track_history(frames([(0, 0)]*3), route_geometry=ROUTE)
    assert result.tracks[0].velocity_xy_m_s is None
    assert "lineage_not_evaluated" in result.tracks[0].reason_codes
    assert result.counts[0].counts_complete


def test_omitted_small_components_are_not_polygonized(monkeypatch):
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    history = frames([(0, 0)])
    latest = history[0]
    latest.detected[10:31:2, 10:31:2] = True  # 121 small detections + one eligible.
    actual = module.footprint

    def bounded_materialization(mask, grid):
        # Guard the known risky side effect, while running the real contour union.
        assert np.count_nonzero(mask) in (1, 289)
        bounded_materialization.calls += 1
        assert bounded_materialization.calls <= 32
        return actual(mask, grid)

    bounded_materialization.calls = 0
    monkeypatch.setattr(module, "footprint", bounded_materialization)
    result = module.track_history(history, route_geometry=ROUTE)
    assert result.counts[0].full_field_detections == 122
    assert result.counts[0].small_detections == 121
    assert result.counts[0].omitted_observed_features == 90
    assert len(result.tracks) == 32


def test_corner_touching_detections_form_one_eight_connected_component():
    history = frames([(0, 0)])
    latest = history[0]
    latest.detected[:] = False
    latest.descriptor[:] = 0.
    latest.detected[50:53, 50:53] = True
    latest.detected[53:56, 53:56] = True
    latest.descriptor[latest.detected] = 1.
    result = tracker()(history, route_geometry=ROUTE)
    assert result.counts[0].full_field_detections == 1
    assert result.counts[0].eligible_candidates == 1
    assert result.tracks[0].footprint.area == 72000000.


def test_diagonal_two_by_two_peak_is_a_competing_peak():
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    first, last = frames([(0, 0)]*2)
    # A diagonal sinusoidal descriptor repeats at (2,2), but not axis shifts
    # of length <=2. Its second strong maximum must not hide in a square mask.
    y, x = np.indices(GRID.shape)
    descriptor = np.exp(-((x-60)**2+(y-60)**2)/120.) * (1.+.95*np.cos(np.pi*(x+y)/2.))
    first.descriptor[:] = descriptor
    last.descriptor[:] = descriptor
    result = module._patch(first, last, (60, 60), np.ones(GRID.shape, dtype=bool),
                           3., "forward", module.DEFAULT_POLICY, None)
    assert result.status == "unavailable"
    assert result.reason_codes == ["ambiguous_peak"]


def test_domain_clipped_feature_keeps_observed_contour_but_no_vector():
    history = frames([(0, 0)]*3)
    texture = np.random.default_rng(91).uniform(.2, 1., (41, 41))
    for f in history:
        f.descriptor[:] = 0.
        f.detected[:] = False
        f.descriptor[40:81, :41] = texture
        f.detected[40:81, :41] = True
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "domain_clipped" in track.reason_codes
    assert track.footprint.bounds[0] == 0.


def test_fractional_whole_feature_rim_is_not_rounded_back_to_known_cells():
    history = frames([(0, 0), (.25, 0), (.5, 0)])
    for f in history:
        f.detected[:] = False
        f.detected[40:81, 40:81] = True
        f.descriptor[40:81, 40:81] += 1e-8
        f.known[60, 82] = False
    # The observed contour's square rim ends exactly at x=164000m, where
    # unknown starts. A positive subcell translation crosses it; rounding to
    # zero would wrongly report complete feature support.
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "coverage_clipped" in track.reason_codes
    assert 0. < track.pair_diagnostics[-1].forward_dx_cells < .5


def test_low_contrast_fractional_texture_with_close_competitor_is_unavailable():
    # The literal original smooth/positive patch fails the conservative circular
    # two-cell competitor exclusion. Keep this negative case; do not relax margin.
    track = tracker()(frames([(0, 0), (1.25, -.5), (2.5, -1.)]), route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "ambiguous_peak" in track.reason_codes


def test_excessive_area_growth_with_good_texture_and_iou_is_unavailable():
    history = frames([(0, 0)]*3)
    history[-1].detected[52:69, 69:79] = True
    history[-1].descriptor[52:69, 69:79] = 1e-9
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "area_change" in track.reason_codes
    pair = track.pair_diagnostics[-1]
    assert pair.common_support_iou == pytest.approx(17./27.)
    assert pair.area_ratio == pytest.approx(27./17.)


def test_poor_whole_contour_iou_with_good_texture_is_unavailable():
    history = frames([(0, 0)]*3)
    for f in history[:2]:
        f.detected[52:69, 27:52] = True
        f.descriptor[52:69, 27:52] = 1e-9
    history[-1].detected[52:69, 69:94] = True
    history[-1].descriptor[52:69, 69:94] = 1e-9
    track = tracker()(history, route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "insufficient_overlap" in track.reason_codes
    assert track.pair_diagnostics[-1].common_support_iou == pytest.approx(17./67.)
    assert track.pair_diagnostics[-1].area_ratio == pytest.approx(1.)


def test_bad_middle_grid_cannot_be_skipped_to_recover_old_motion():
    history = frames([(0, 0)]*4)
    history[1].grid = AnalysisGrid(GRID.crs, GRID.center, 1000., 0., 128, 128, 2000.)
    result = tracker()(history, route_geometry=ROUTE)
    assert len(result.tracks) == 1
    assert result.tracks[0].velocity_xy_m_s is None
    assert "incompatible_grid" in result.tracks[0].reason_codes
    assert result.counts[0].counts_complete


def test_four_frame_chain_checks_the_final_successive_triple():
    track = tracker()(frames([(0, 0), (1, 0), (2, 0), (7, 0)]), route_geometry=ROUTE).tracks[0]
    assert track.velocity_xy_m_s is None
    assert "next_observation_inconsistent" in track.reason_codes
    assert track.pair_diagnostics[0].next_observation_residual_cells == pytest.approx(0.)
    assert track.pair_diagnostics[1].next_observation_residual_cells == pytest.approx(4.)


def test_pair_diagnostics_remain_valid_strict_dtos():
    from weatherbrief.models.observed_motion import PairDiagnostics
    for offsets in ([(0, 0)]*3, [(0, 0), (0, 0), (4, 0)], [(0, 0), (2, 0), (4, 0)]):
        track = tracker()(frames(offsets), route_geometry=ROUTE).tracks[0]
        assert track.pair_diagnostics
        for pair in track.pair_diagnostics:
            validated = PairDiagnostics.model_validate(pair.model_dump())
            assert validated == pair


def test_later_plausible_parent_failure_invalidates_earlier_accepted_child():
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    resolve = getattr(module, "_resolve_pairs", None)
    assert resolve is not None, "reciprocal match resolution is missing"
    accepted = tracker()(frames([(0, 0)]*3), route_geometry=ROUTE).tracks[0].pair_diagnostics[0]
    failed = accepted.model_copy(update={"status": "unavailable", "reason_codes": ["reverse_inconsistent"]})
    for candidates in ([(1, [5], accepted), (2, [5], failed)],
                       [(2, [5], failed), (1, [5], accepted)]):
        matched, failures = resolve(candidates, {})
        assert 5 not in matched
        assert failures[5].status == "unavailable"
        assert "lineage_ambiguous" in failures[5].reason_codes


def test_unmatched_nearby_diagnostic_is_not_a_plausible_lineage_claim():
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    resolve = getattr(module, "_resolve_pairs", None)
    assert resolve is not None, "reciprocal match resolution is missing"
    accepted = tracker()(frames([(0, 0)]*3), route_geometry=ROUTE).tracks[0].pair_diagnostics[0]
    failed = accepted.model_copy(update={"status": "unavailable", "reason_codes": ["low_texture"]})
    matched, failures = resolve([(1, [5], accepted)], {5: failed})
    assert matched[5] == (1, accepted)
    assert 5 not in failures


def test_candidate_cap_cannot_hide_an_eligible_competing_parent():
    history = frames([(0, 0)]*3)
    extras = [(r, c) for r in (5, 12, 19, 26, 33, 85, 92) for c in (44, 50, 56, 62, 68)][:31]
    for f in history:
        for row, col in extras:
            f.detected[row:row+3, col:col+3] = True
            f.descriptor[row:row+3, col:col+3] = 1e-9
    for f in history[:2]:
        f.detected[60:63, 72:75] = True
        f.descriptor[60:63, 72:75] = 1e-9
    history[-1].detected[60:63, 69:75] = True
    history[-1].descriptor[60:63, 69:75] = 1e-9
    # The 9-cell competing parent is farther from x=108000 than the other32
    # eligible components, so receives no forward patches under the work cap.
    route = LineString([(108000., 0.), (108000., 256000.)])
    result = tracker()(history, route_geometry=route)
    main = max(result.tracks, key=lambda t: t.footprint.area)
    assert main.velocity_xy_m_s is None
    assert "lineage_ambiguous" in main.reason_codes
    assert main.pair_diagnostics[-1].plausible_parent_count == 2
    assert main.pair_diagnostics[-1].lineage_complete
    assert result.counts[0].full_field_detections == 32


@pytest.mark.parametrize("cells", [1, 64, 512])
@pytest.mark.parametrize("dx,want", [(.8, [1]), (-.8, [1]), (.8+1e-12, [])])
def test_inclusive_fractional_lineage_threshold_respects_roundoff_not_real_deficit(cells, dx, want):
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    mask = np.zeros((cells, 3), dtype=bool)
    mask[:, 1] = True
    labels = mask.astype(int)
    overlap = module._overlaps(mask, labels, dx, 0., None)
    # Exactly 0.2*cells in real arithmetic for +/-0.8; 1e-12*cells less
    # for +0.800000000001. The latter is not float64 accumulation noise.
    assert module._plausible(overlap, cells, np.array([0, cells]), module.DEFAULT_POLICY).tolist() == want


@pytest.mark.parametrize("dx,main_columns,small_column,selected", [
    (.8, (52, 67), 50, [2]),
    (-.8, (50, 65), 66, [1]),
])
def test_exact_fractional_threshold_retains_second_small_unselected_lineage_competitor(
        dx, main_columns, small_column, selected):
    module = importlib.import_module("weatherbrief.observed.motion.tracking")
    first, last = frames([(0, 0)]*2)
    first.detected[:] = last.detected[:] = False
    first.detected[50:67, 50:67] = True
    last.detected[50:67, main_columns[0]:main_columns[1]] = True
    last.detected[50, small_column] = True
    for f in (first, last):
        f.descriptor[:] = f.detected.astype(float)
    earlier = module._label(first, module.DEFAULT_POLICY, None)
    later = module._label(last, module.DEFAULT_POLICY, None)
    module._rank(later, ROUTE, module.DEFAULT_POLICY, None)
    assert later.selected == selected
    # The separate single cell is eight-disconnected and below the nine-cell
    # motion-work minimum. It still overlaps the translated full parent by
    # exactly20% of its own area and must prevent a unique child/parent claim.
    assert sorted(later.sizes[1:].tolist()) == [1, 255]
    overlap = module._overlaps(earlier.labels == 1, later.labels, dx, 0., None)
    plausible = module._plausible(overlap, 289, later.sizes, module.DEFAULT_POLICY)
    assert plausible.tolist() == [1, 2]
