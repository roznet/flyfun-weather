"""Literal metric contours exercise the real continuous route solver."""
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import importlib
import math
import time

import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from weatherbrief.models import RouteConfig, Waypoint
from weatherbrief.observed.motion.geometry import AnalysisGrid
from weatherbrief.observed.motion.policy import DEFAULT_POLICY

T0 = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


@pytest.fixture
def api():
    class RequiredAPI:
        def __getattr__(self, name):
            spec = importlib.util.find_spec("weatherbrief.observed.motion.route")
            assert spec is not None, "continuous route primitive is not implemented"
            return getattr(importlib.import_module(spec.name), name)
    return RequiredAPI()


@dataclass
class LiteralTrack:
    footprint: object
    velocity_xy_m_s: tuple | None = (0., 0.)
    feature_id: str = "literal-track"
    source_id: str = "literal-radar"
    reference_at: datetime = T0
    history: list = field(default_factory=list)
    reason_codes: list = field(default_factory=list)
    pair_diagnostics: list = field(default_factory=list)
    fit_rms_residual_cells: float | None = 0.


@pytest.fixture
def grid():
    return AnalysisGrid("+proj=aeqd +lat_0=0 +lon_0=0 +datum=WGS84 +units=m", (0., 0.),
                        -20000., -20000., 40, 40, 1000.)


def route(grid, points=((0., 0.), (6000., 0.)), seconds=600):
    return RouteConfig(name="literal", flight_duration_hours=seconds / 3600,
        waypoints=[Waypoint(icao="SAME", name=f"Point {i}", lon=grid.inverse(x,y)[0],
                            lat=grid.inverse(x,y)[1]) for i, (x,y) in enumerate(points)])


def relationships(api, grid, track, planned=None, cutoff=T0, times=(), **kwargs):
    return api.route_relationships(track, planned or route(grid), grid, T0, cutoff, times, **kwargs)


def test_parallel_motion_has_ground_speed_but_no_closure(api, grid):
    track = LiteralTrack(box(2000, 1852, 3000, 2852), (10., 0.))
    rows, _ = relationships(api, grid, track)
    assert rows[0].distance_nm == pytest.approx(1.)
    assert rows[0].closure_kt == pytest.approx(0., abs=1e-8)
    assert rows[0].relationship == "approximately_unchanged"
    speed, bearing, _ = api.ground_velocity(track, grid)
    assert speed == pytest.approx(19.4384, rel=1e-4)
    assert bearing == pytest.approx(90., abs=.001)


def test_full_contour_intersection_has_no_closure(api, grid):
    rows, _ = relationships(api, grid, LiteralTrack(box(2000, -500, 3000, 500), (0., 10.)))
    assert rows[0].distance_nm == 0.
    assert rows[0].closure_kt is None
    assert rows[0].closure_interval is None
    assert rows[0].relationship == "intersecting"


def test_moving_crossing_between_ticks_is_solved_continuously(api, grid):
    # Aircraft x=10t; contour y=[100,200]-t, x=[0,6000]. Contact t=[100,200].
    track = LiteralTrack(box(0, 100, 6000, 200), (0., -1.))
    rows, result = relationships(api, grid, track, times=(T0+timedelta(minutes=5),))
    assert result.status == "available" and result.complete
    assert [(i.start_at, i.end_at, i.contact) for i in result.intervals] == [
        (T0+timedelta(minutes=1), T0+timedelta(minutes=4), "interval")]
    assert [r.planned_overlap_at_time for r in rows] == [False, False]


def test_hole_preserves_two_distinct_passage_intervals(api, grid):
    contour = Polygon([(1000,-500),(5000,-500),(5000,500),(1000,500)],
                      [[(2000,-100),(4000,-100),(4000,100),(2000,100)]])
    _, result = relationships(api, grid, LiteralTrack(contour))
    assert [(i.start_at, i.end_at) for i in result.intervals] == [
        (T0+timedelta(minutes=1), T0+timedelta(minutes=4)),
        (T0+timedelta(minutes=6), T0+timedelta(minutes=9))]


def test_tangent_stays_an_instant(api, grid):
    triangle = Polygon([(1500, 0),(1000, 500),(2000,500)])
    _, result = relationships(api, grid, LiteralTrack(triangle))
    assert len(result.intervals) == 1
    contact = result.intervals[0]
    assert contact.contact == "tangent"
    assert contact.start_at == contact.end_at == T0+timedelta(minutes=3)


def test_zero_relative_displacement_uses_point_coverage(api, grid):
    _, result = relationships(api, grid, LiteralTrack(box(-100,-100,100,100), (10.,0.)))
    assert result.status == "available"
    assert [(i.start_at, i.end_at) for i in result.intervals] == [(T0,T0+timedelta(minutes=10))]


@pytest.mark.parametrize("duration", [0, -1, math.inf, math.nan, 1e-12, 1e300])
def test_invalid_timing_never_guesses_a_speed(api, grid, duration):
    planned = route(grid).model_copy(update={"flight_duration_hours":duration})
    assert api.route_identities(planned, T0)[1] is None
    rows, result = relationships(api, grid, LiteralTrack(box(0,-100,6000,100)), planned)
    assert result.reason_codes == ["invalid_planned_timing"]
    assert rows[0].planned_overlap_at_time is None
    assert rows[0].status == "available"


def test_identity_binds_order_bends_labels_and_utc_timing(api, grid):
    planned = route(grid)
    geometry_id, timing_id = api.route_identities(planned,T0)
    assert api.route_identities(planned,T0.astimezone(timezone(timedelta(hours=1)))) == (geometry_id,timing_id)
    assert api.route_identities(planned,T0.replace(tzinfo=None)) == (geometry_id,None)
    changed = planned.model_copy(update={"flight_duration_hours":1.})
    assert api.route_identities(changed,T0)[0] == geometry_id
    assert api.route_identities(changed,T0)[1] != timing_id
    bent = route(grid, ((0,0),(3000,1000),(6000,0)))
    assert api.route_identities(bent,T0)[0] != geometry_id


def test_invalid_geometry_still_has_fingerprint_but_no_timing(api, grid):
    planned = route(grid)
    planned.waypoints[0].lon = math.nan
    identity = api.route_identities(planned,T0)
    assert isinstance(identity[0], str) and identity[0]
    assert identity[1] is None
    assert api.route_identities(planned,T0) == identity
    with pytest.raises(ValueError,match="invalid_route"):
        api.build_route_geometry(planned,grid)


def test_degenerate_leg_does_not_erase_repeated_label_valid_legs(api, grid):
    planned = route(grid, ((0,0),(0,0),(3000,0),(3000,3000)))
    rows, result = relationships(api, grid, LiteralTrack(box(-100,-100,4000,4000)), planned)
    assert [r.leg_index for r in rows] == [0,1,2]
    assert len({r.leg_id for r in rows}) == 3
    assert rows[0].reason_codes == ["degenerate_leg"]
    assert {i.leg_index for i in result.intervals} == {1,2}
    geometry = api.build_route_geometry(planned,grid)
    assert geometry.distance(Polygon([(2999,2999),(3001,2999),(3001,3001),(2999,3001)])) == 0


def test_expired_reference_has_observed_row_but_no_future_lead(api, grid):
    rows, result = relationships(api,grid,LiteralTrack(box(1000,-100,2000,100)),cutoff=T0+timedelta(minutes=16))
    assert rows[0].status == "available"
    assert result.reason_codes == ["no_future_lead"]


def test_before_arrival_crossing_does_not_claim_planned_overlap(api, grid):
    planned = route(grid)
    _, result = api.route_relationships(LiteralTrack(box(0,100,6000,200),(0.,-1.)),planned,grid,
                                      T0+timedelta(minutes=4),T0,())
    assert result.status == "available" and result.intervals == []


def test_interval_limit_withholds_entire_result(api, grid):
    contour = MultiPolygon([box(1000,-100,1500,100),box(4000,-100,4500,100)])
    _, result = relationships(api,grid,LiteralTrack(contour),policy=replace(DEFAULT_POLICY,max_overlap_intervals=1))
    assert result.reason_codes == ["overlap_interval_limit"]
    assert result.intervals == [] and not result.complete


def test_fractional_whole_contour_rim_exit_is_unavailable(api, grid):
    # Domain ends at 20000; one-cell rim ends at 19999.75 initially.
    track = LiteralTrack(box(18000,-100,18999.75,100),(1.,0.))
    _, result = relationships(api,grid,track)
    assert result.reason_codes == ["outside_analysis_domain"]
    assert not result.complete and result.intervals == []


def test_ground_velocity_offcentre_uses_true_geodesic_direction(api):
    grid = AnalysisGrid("+proj=aeqd +lat_0=50 +lon_0=0 +datum=WGS84 +units=m",(0.,50.),
                        -1000000.,-1000000.,1000,1000,2000.)
    speed, bearing, point = api.ground_velocity(LiteralTrack(box(499900,499900,500100,500100),(10.,0.)),grid)
    assert 19 < speed < 20
    assert 95 < bearing < 97  # Off-centre grid east is NOT true east.
    assert 7 < point[0] < 8 and 54 < point[1] < 55
    assert api.ground_velocity(LiteralTrack(box(0,0,100,100)),grid)[:2] == (0.,None)


@pytest.mark.parametrize("vy,relationship,closure", [
    (-1.,"approaching",1.94384449),(1.,"receding",-1.94384449),
    (-.5,"approximately_unchanged",.97192225),
])
def test_closure_sign_and_reference_expiry_shortening(api,grid,vy,relationship,closure):
    rows,_ = relationships(api,grid,LiteralTrack(box(1000,4000,2000,5000),(0.,vy)),
                          times=(T0+timedelta(minutes=5),T0+timedelta(minutes=15)))
    assert [r.relationship for r in rows] == [relationship]*3
    assert [r.closure_kt for r in rows] == pytest.approx([closure]*3)
    assert [(r.closure_interval.start_at-T0,r.closure_interval.end_at-T0) for r in rows] == [
        (timedelta(0),timedelta(seconds=30)),
        (timedelta(seconds=270),timedelta(seconds=330)),
        (timedelta(seconds=870),timedelta(seconds=900))]


def test_cutoff_consumes_source_lead_and_clamps_rounded_interval(api,grid):
    rows,result = relationships(api,grid,LiteralTrack(box(-100,-100,10000,100)),
                               cutoff=T0+timedelta(seconds=125))
    assert result.evaluated_interval.start_at == T0+timedelta(seconds=125)
    assert result.intervals[0].start_at == T0+timedelta(seconds=125)
    assert result.intervals[0].end_at == T0+timedelta(minutes=10)


def test_future_tick_after_feature_expiry_keeps_specific_unavailable_row(api,grid):
    rows,_ = relationships(api,grid,LiteralTrack(box(1000,1000,2000,2000)),
                          times=(T0+timedelta(minutes=20),))
    assert rows[1].reason_codes == ["no_future_lead"]
    assert rows[1].distance_nm is None
    assert rows[1].planned_overlap_at_time is None


def test_route_segment_cap_is_explicit_in_geometry_and_overlap(api,grid):
    planned=route(grid,((0,0),(6000,0)))
    policy=replace(DEFAULT_POLICY,max_route_segments=3)
    with pytest.raises(ValueError,match="route_segment_limit"):
        api.build_route_geometry(planned,grid,policy=policy)
    rows,result=relationships(api,grid,LiteralTrack(box(0,0,100,100)),planned,policy=policy)
    assert rows == [] and result.reason_codes == ["route_segment_limit"]


def test_entirely_degenerate_route_is_invalid(api,grid):
    planned=route(grid,((0,0),(0,0)))
    assert api.route_identities(planned,T0)[1] is None
    rows,result=relationships(api,grid,LiteralTrack(box(-100,-100,100,100)),planned)
    assert rows == [] and result.reason_codes == ["invalid_route"]


@pytest.mark.parametrize("times", [(T0,), (T0+timedelta(seconds=301),),
                                  (T0+timedelta(minutes=5),)*2])
def test_invalid_advertised_times_are_not_silently_evaluated(api,grid,times):
    rows,result=relationships(api,grid,LiteralTrack(box(0,0,100,100)),times=times)
    assert rows == [] and result.reason_codes == ["unsupported_time"]


def test_row_cap_does_not_emit_partial_negative_result(api,grid):
    rows,result=relationships(api,grid,LiteralTrack(box(0,0,100,100)),
        times=(T0+timedelta(minutes=5),),policy=replace(DEFAULT_POLICY,max_route_rows=1))
    assert rows == [] and not result.complete
    assert result.reason_codes == ["selection_limit"]


def test_invalid_unsimplified_contour_is_unavailable(api,grid):
    bowtie=Polygon([(0,0),(2000,2000),(0,2000),(2000,0)])
    rows,result=relationships(api,grid,LiteralTrack(bowtie))
    assert rows == [] and result.reason_codes == ["invalid_geometry"]


def test_matching_velocity_missing_cannot_become_zero(api,grid):
    track=LiteralTrack(box(0,0,100,100),None,reason_codes=["ambiguous_peak"])
    with pytest.raises(ValueError,match="ambiguous_peak"):
        api.ground_velocity(track,grid)
    rows,result=relationships(api,grid,track)
    assert rows == [] and result.reason_codes == ["ambiguous_peak"]


def test_valid_flight_outside_supported_window_is_unavailable(api,grid):
    _,result=api.route_relationships(LiteralTrack(box(0,-100,6000,100)),route(grid),grid,
                                    T0+timedelta(hours=1),T0,())
    assert result.reason_codes == ["outside_planned_interval"]


def test_tangent_rounding_is_clamped_to_evaluated_instant(api,grid):
    # Flight ends exactly as supported time starts: one instant, not an interval.
    _,result=relationships(api,grid,LiteralTrack(box(5900,-100,6100,100)),cutoff=T0+timedelta(minutes=10))
    assert result.status == "available"
    assert len(result.intervals) == 1
    assert result.intervals[0].contact == "tangent"


def test_expired_deadline_withholds_geometry_and_entire_overlap(api,grid):
    deadline=time.monotonic()-1
    with pytest.raises(ValueError,match="compute_deadline"):
        api.build_route_geometry(route(grid),grid,deadline=deadline)
    rows,result=relationships(api,grid,LiteralTrack(box(0,-100,6000,100)),deadline=deadline)
    assert rows == [] and not result.complete
    assert result.reason_codes == ["compute_deadline"]


def test_unrepresentable_dense_segment_time_is_not_evaluated_empty(api,grid):
    planned=route(grid,seconds=.000001)
    assert api.route_identities(planned,T0)[1] is None
    _,result=relationships(api,grid,LiteralTrack(box(100,-100,200,100)),planned)
    assert result.status == "unavailable"
    assert result.reason_codes == ["invalid_planned_timing"]


def test_source_expiry_overflow_is_explicit_invalid_time(api,grid):
    reference=datetime(9999,12,31,23,59,tzinfo=timezone.utc)
    track=LiteralTrack(box(0,0,100,100),reference_at=reference)
    rows,result=relationships(api,grid,track,cutoff=reference)
    assert rows == [] and result.reason_codes == ["invalid_time"]
