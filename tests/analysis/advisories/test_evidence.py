"""Tests for the single-assessment evidence helper (#393 Part 2).

``summarize_evidence`` reduces one per-point :class:`EvidenceSample` list into
the grade counts, the geometry-accurate ``affected_nm``, the highlight geometry,
and the coverage ``data_state`` — the one place grade and highlight are derived
from a single predicate so they cannot drift.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories._helpers import (
    DEFAULT_CRUISE_TAS_KT,
    EMPTY_EXTENT,
    MIN_GROUNDSPEED_KT,
    EvidenceSample,
    FlaggedCell,
    RouteExtent,
    format_extent,
    grade_extent,
    route_extent,
    summarize_evidence,
)
from weatherbrief.models import AdvisoryStatus
from weatherbrief.models import HighlightSeverity as S


def _sample(dist, sev, *, assessed=True, affected=None, in_domain=True, region=None):
    return EvidenceSample(
        distance_nm=dist, assessed=assessed, severity=sev,
        affected=affected, in_domain=in_domain, region=region,
    )


class TestCounts:
    def test_affected_derived_from_severity(self):
        samples = [
            _sample(0.0, S.GREEN),
            _sample(50.0, S.AMBER),
            _sample(100.0, S.RED),
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.assessed == 3
        assert summ.domain == 3
        assert summ.affected == 2  # amber + red

    def test_unassessed_excluded_from_assessed_not_domain(self):
        samples = [
            _sample(0.0, S.GREEN),
            _sample(50.0, S.UNAVAILABLE, assessed=False),
            _sample(100.0, S.GREEN),
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.domain == 3          # all three points are in the domain
        assert summ.assessed == 2        # only the two with data
        assert summ.affected == 0

    def test_explicit_affected_overrides_severity(self):
        # Ribbon RED but the grade does not count it (turbulence severe-off-cruise).
        samples = [
            _sample(0.0, S.GREEN),
            _sample(50.0, S.RED, affected=False),
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.affected == 0

    def test_explicit_affected_true_with_green_ribbon(self):
        # Grade counts a point the ribbon shows green (enroute_precip light rain).
        samples = [_sample(0.0, S.GREEN, affected=True), _sample(50.0, S.GREEN)]
        summ = summarize_evidence(samples, 100.0)
        assert summ.affected == 1


class TestAffectedNm:
    def test_midpoint_owned_cells(self):
        # Four evenly spaced points over 90nm → cell edges at 15/45/75. The
        # single affected point at 30 owns [15, 45] = 30nm.
        samples = [
            _sample(0.0, S.GREEN),
            _sample(30.0, S.AMBER),
            _sample(60.0, S.GREEN),
            _sample(90.0, S.GREEN),
        ]
        summ = summarize_evidence(samples, 90.0)
        assert summ.affected_nm == 30.0

    def test_endpoint_cell_reaches_route_bounds(self):
        # Affected first point owns [0, midpoint] and last owns [midpoint, total].
        samples = [_sample(0.0, S.RED), _sample(40.0, S.GREEN), _sample(100.0, S.RED)]
        summ = summarize_evidence(samples, 100.0)
        # first cell [0,20]=20, last cell [70,100]=30 → 50
        assert summ.affected_nm == 50.0

    def test_no_affected_is_zero(self):
        summ = summarize_evidence([_sample(0.0, S.GREEN), _sample(50.0, S.GREEN)], 50.0)
        assert summ.affected_nm == 0.0

    def test_extent_consistent_with_full_route(self):
        # All points affected → owns the whole route.
        samples = [_sample(0.0, S.RED), _sample(50.0, S.RED), _sample(100.0, S.RED)]
        summ = summarize_evidence(samples, 100.0)
        assert summ.affected_nm == 100.0


class TestHighlights:
    def test_ribbon_tiles_and_regions_flagged_only(self):
        cell = FlaggedCell(kind="k", severity=S.AMBER, base_ft=1000, top_ft=5000)
        samples = [
            _sample(0.0, S.GREEN),
            _sample(50.0, S.AMBER, region=cell),
            _sample(100.0, S.GREEN),
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.highlights.ribbon[0].dist_from_nm == 0.0
        assert summ.highlights.ribbon[-1].dist_to_nm == 100.0
        assert len(summ.highlights.regions) == 1
        assert summ.highlights.regions[0].kind == "k"

    def test_peak_defaults_to_ribbon_peak(self):
        samples = [_sample(0.0, S.GREEN), _sample(40.0, S.RED), _sample(80.0, S.GREEN)]
        summ = summarize_evidence(samples, 120.0)
        assert summ.highlights.peak_dist_nm is not None

    def test_peak_override(self):
        samples = [_sample(0.0, S.RED), _sample(50.0, S.RED)]
        summ = summarize_evidence(samples, 100.0, peak_dist_nm=42.0)
        assert summ.highlights.peak_dist_nm == 42.0

    def test_region_provenance_propagates(self):
        cell = FlaggedCell(
            kind="icing_band", severity=S.RED, base_ft=3000, top_ft=9000,
            reason_code="no_escape", metric_id="icing", method_id="ogimet_nwp",
        )
        summ = summarize_evidence([_sample(0.0, S.RED, region=cell)], 100.0)
        region = summ.highlights.regions[0]
        assert region.reason_code == "no_escape"
        assert region.metric_id == "icing"
        assert region.method_id == "ogimet_nwp"


class TestDataStateAndCoverage:
    def test_complete_when_full_coverage(self):
        samples = [_sample(float(i * 10), S.GREEN) for i in range(10)]
        summ = summarize_evidence(samples, 90.0)
        assert summ.data_state == "complete"
        assert summ.below_coverage is False

    def test_partial_below_half(self):
        # 2 assessed of 10 domain → below the 0.5 coverage floor.
        samples = [_sample(0.0, S.GREEN), _sample(10.0, S.GREEN)] + [
            _sample(float(20 + i * 10), S.UNAVAILABLE, assessed=False) for i in range(8)
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.data_state == "partial"
        assert summ.below_coverage is True

    def test_unavailable_when_nothing_assessed(self):
        samples = [_sample(float(i * 10), S.UNAVAILABLE, assessed=False) for i in range(4)]
        summ = summarize_evidence(samples, 30.0)
        assert summ.data_state == "unavailable"

    def test_domain_override_excludes_out_of_domain(self):
        # mountain_wind: non-mountain points out of domain; coverage over mountains.
        samples = [
            _sample(0.0, S.GREEN, in_domain=False),         # flat, not a mountain
            _sample(25.0, S.GREEN, in_domain=False),        # flat, not a mountain
            _sample(50.0, S.GREEN),                         # mountain, assessed
            _sample(75.0, S.UNAVAILABLE, assessed=False),   # mountain, no wind
            _sample(100.0, S.UNAVAILABLE, assessed=False),  # mountain, no wind
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.domain == 3      # three mountain points
        assert summ.assessed == 1    # one with wind
        # 1 assessed of 3 domain < 0.5*3=1.5 → below coverage. The two flat
        # points do not dilute the mountain-only coverage denominator.
        assert summ.below_coverage is True


class TestRouteExtent:
    """The shared extent primitive (#571).

    Every fixture here uses **deliberately uneven** point spacing — the real
    ``interpolate_route`` output, which fills at a fixed 10 nm but inserts extra
    points at waypoints. An evenly spaced fixture cannot catch the defect this
    replaces: with even spacing the proportional ``total_nm × affected/total``
    happens to agree with the geometry, so a proportional regression would pass.
    """

    # 0, 10, 20, 22, 24, 100: two waypoint-tight gaps inside a long route.
    UNEVEN = (0.0, 10.0, 20.0, 22.0, 24.0, 100.0)

    def _uneven(self, affected_idx, **kw):
        return [
            _sample(d, S.RED if i in affected_idx else S.GREEN, **kw)
            for i, d in enumerate(self.UNEVEN)
        ]

    def test_nm_is_geometry_not_a_point_ratio(self):
        # The three tight points (20, 22, 24) own [15,21], [21,23], [23,62] =
        # 6 + 2 + 39 = 47nm. A point ratio would claim 3/6 × 100 = 50nm.
        summ = summarize_evidence(self._uneven({2, 3, 4}), 100.0)
        assert summ.extent.nm == 47.0
        assert summ.extent.points == 3
        assert summ.extent.nm != round(100.0 * 3 / 6)

    def test_pct_is_distance_based(self):
        summ = summarize_evidence(self._uneven({2, 3, 4}), 100.0)
        # 47 / 100, not the 50% point ratio.
        assert summ.extent.pct == 47.0

    def test_message_and_field_are_one_number(self):
        # The #571 D2 invariant: what the sentence prints IS ``affected_nm``.
        summ = summarize_evidence(self._uneven({2, 3, 4}), 100.0)
        assert format_extent(summ.extent) == "47nm/100nm (47%)"
        assert summ.affected_nm == summ.extent.nm

    def test_domain_nm_is_the_whole_route_by_default(self):
        summ = summarize_evidence(self._uneven({0}), 100.0)
        assert summ.extent.domain_nm == 100.0
        assert summ.extent.domain_points == 6

    def test_domain_nm_is_the_restricted_domain(self):
        # mountain_wind's shape: only the tight cluster is in-domain, and the
        # affected point's percentage is a share of THAT, not of the route.
        samples = [
            _sample(d, S.RED if i == 3 else S.GREEN, in_domain=i in (2, 3, 4))
            for i, d in enumerate(self.UNEVEN)
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.extent.domain_nm == 47.0     # 6 + 2 + 39
        assert summ.extent.nm == 2.0             # the point at 22 owns [21,23]
        assert round(summ.extent.pct) == 4
        # D3: never the route length multiplied by a domain fraction.
        assert format_extent(summ.extent, domain_label="of high terrain") == (
            "2nm/47nm of high terrain (4%)"
        )

    def test_longest_run_is_contiguous_not_the_union(self):
        # Affected at 0 and at the tight cluster: union 6+2+39+? but the longest
        # contiguous run is the cluster's 47nm, not the union.
        summ = summarize_evidence(self._uneven({0, 2, 3, 4}), 100.0)
        assert summ.extent.nm == 52.0            # +5 for the point at 0
        assert summ.extent.longest_run_nm == 47.0

    def test_extent_of_measures_a_sub_population(self):
        # Severity tiers: RED at one point, AMBER at two. The RED sentence must
        # quote the RED geometry, not the flagged union's (#571 D1).
        samples = [
            _sample(0.0, S.GREEN),
            _sample(10.0, S.AMBER),
            _sample(20.0, S.AMBER),
            _sample(22.0, S.RED),
            _sample(24.0, S.GREEN),
            _sample(100.0, S.GREEN),
        ]
        summ = summarize_evidence(samples, 100.0)
        assert summ.extent.nm == 18.0            # [5,15] + [15,21] + [21,23]
        red = summ.extent_of(lambda s: s.severity == S.RED)
        assert red.nm == 2.0
        assert red.points == 1

    def test_extent_of_reads_tags(self):
        samples = [
            _sample(0.0, S.RED),
            _sample(10.0, S.RED),
            _sample(20.0, S.RED),
            _sample(22.0, S.RED),
            _sample(24.0, S.RED),
            _sample(100.0, S.RED),
        ]
        samples[3] = EvidenceSample(
            distance_nm=22.0, assessed=True, severity=S.RED,
            tags=frozenset({"severe"}),
        )
        summ = summarize_evidence(samples, 100.0)
        assert summ.extent.nm == 100.0
        assert summ.extent_of(lambda s: "severe" in s.tags).nm == 2.0

    def test_empty_and_degenerate(self):
        assert route_extent([], 100.0, []) == EMPTY_EXTENT
        assert format_extent(EMPTY_EXTENT) == "0nm"
        assert EMPTY_EXTENT.pct == 0.0

    def test_a_zero_length_route_still_measures_coverage(self):
        """#571 review — the false-GREEN a zero-length route used to produce.

        ``total_distance_nm`` is 0 for a pattern or sightseeing flight whose
        origin and destination are the same point (waypoint *count* is
        validated, distinctness is not). Returning an empty extent there made
        every coverage gate answer GREEN however completely the weather covered
        the route — while the pre-primitive point ratio graded it correctly.
        """
        ext = route_extent([0.0, 0.0, 0.0, 0.0], 0.0, [True, True, True, False])
        assert ext.points == 3
        assert ext.pct == 75.0            # the point ratio, which is the truth
        assert ext.distance_known is False
        assert grade_extent(ext, amber_pct=15) is not AdvisoryStatus.GREEN

    def test_a_zero_length_route_prints_a_percentage_not_invented_miles(self):
        ext = route_extent([0.0, 0.0], 0.0, [True, True])
        assert format_extent(ext) == "100%"
        assert format_extent(ext, domain_label="of high terrain") == (
            "100% of high terrain"
        )

    def test_a_single_point_route_owns_the_whole_route(self):
        """One assessed point is the degenerate case of midpoint-owned cells.

        With no neighbours there are no midpoints, so the one cell must span
        ``[0, total_nm]`` — the tiling property the whole primitive rests on.
        A route this short is rare but reachable (a very short hop, or a model
        that returned one usable sounding).
        """
        ext = route_extent([0.0], 120.0, [True])
        assert ext.nm == 120.0
        assert ext.domain_nm == 120.0
        assert ext.pct == 100.0
        assert ext.longest_run_nm == 120.0
        assert ext.distance_known is True

    def test_a_single_unaffected_point_owns_the_denominator_only(self):
        ext = route_extent([0.0], 120.0, [False])
        assert ext.nm == 0.0
        assert ext.domain_nm == 120.0
        assert ext.pct == 0.0

    def test_no_in_domain_points_is_zero_not_a_zero_division(self):
        """``pct`` divides by ``domain_nm``; an empty domain must not raise.

        Distinct from the zero-*length*-route path above: here the route has
        real miles, but no point was assessable, so the denominator is empty
        while ``total_nm`` is not.
        """
        ext = route_extent(
            [0.0, 50.0, 100.0], 100.0, [False, False, False],
            in_domain=[False, False, False],
        )
        assert ext.domain_points == 0
        assert ext.domain_nm == 0.0
        assert ext.pct == 0.0
        assert grade_extent(ext, amber_pct=1) is AdvisoryStatus.GREEN

    def test_out_of_order_distances_are_sorted_before_reducing(self):
        """``cell_edges`` assumes along-route order; unsorted input would make
        cell widths negative and net out to a false GREEN."""
        shuffled = route_extent(
            [100.0, 0.0, 50.0], 100.0, [False, True, False],
        )
        in_order = route_extent([0.0, 50.0, 100.0], 100.0, [True, False, False])
        assert shuffled == in_order
        assert shuffled.nm > 0


class TestGradeExtent:
    """The single coverage gate and its minimum-extent floor (#571 Stage 2)."""

    def _ext(self, nm, domain_nm, longest_run_nm=None):
        return RouteExtent(
            points=0, domain_points=0, nm=nm, domain_nm=domain_nm,
            longest_run_nm=longest_run_nm if longest_run_nm is not None else nm,
        )

    def test_bands_on_distance_share(self):
        long_route = 600.0
        assert grade_extent(self._ext(60.0, long_route), amber_pct=15) is AdvisoryStatus.GREEN
        assert grade_extent(self._ext(100.0, long_route), amber_pct=15) is AdvisoryStatus.AMBER
        assert grade_extent(
            self._ext(200.0, long_route), amber_pct=15, red_pct=30,
        ) is AdvisoryStatus.RED

    def test_floor_is_inert_on_a_long_route(self):
        # 30nm of 600 is 5% — the floor never gets a say; the gate does.
        assert grade_extent(self._ext(30.0, 600.0), amber_pct=15) is AdvisoryStatus.GREEN
        assert grade_extent(self._ext(30.0, 600.0), amber_pct=4) is AdvisoryStatus.AMBER

    def test_floor_bites_on_a_short_route(self):
        """The D4 case, in the units it was reported in.

        Two flagged points on a ~120 nm route are 20 nm and clear a 15% gate
        outright — the gate is ~5x more sensitive on a short flight than on a
        long one, and a short flight is where a 20 nm band is most avoidable.
        """
        two_points = self._ext(20.0, 120.0)
        assert two_points.pct > 15                     # would have promoted
        assert grade_extent(two_points, amber_pct=15) is AdvisoryStatus.GREEN
        # Three points clear the 30 nm floor and grade normally again.
        assert grade_extent(self._ext(30.0, 120.0), amber_pct=15) is AdvisoryStatus.AMBER

    def test_floor_never_suppresses_a_route_mostly_in_the_hazard(self):
        """A floor may not exceed half the domain it measures.

        Without the cap an absolute-nm gate would grade a 40 nm flight GREEN
        however completely the weather covered it — the false-GREEN failure mode
        (#391) coming back through the back door.
        """
        assert grade_extent(self._ext(40.0, 40.0), amber_pct=15) is AdvisoryStatus.AMBER
        assert grade_extent(self._ext(20.0, 40.0), amber_pct=15) is AdvisoryStatus.AMBER
        # Below half the domain the floor still bites on such a short route.
        assert grade_extent(self._ext(8.0, 40.0), amber_pct=15) is AdvisoryStatus.GREEN

    def test_min_run_gates_on_contiguity_not_the_union(self):
        # 60nm of scattered cells, longest run 20nm: not a barrier.
        scattered = self._ext(60.0, 600.0, longest_run_nm=20.0)
        assert grade_extent(
            scattered, amber_pct=5, min_run_nm=50,
        ) is AdvisoryStatus.GREEN
        barrier = self._ext(60.0, 600.0, longest_run_nm=60.0)
        assert grade_extent(
            barrier, amber_pct=5, min_run_nm=50,
        ) is AdvisoryStatus.AMBER

    def test_nothing_affected_is_green(self):
        assert grade_extent(self._ext(0.0, 600.0), amber_pct=0) is AdvisoryStatus.GREEN
        assert grade_extent(EMPTY_EXTENT, amber_pct=0) is AdvisoryStatus.GREEN

    def test_floor_can_be_disabled(self):
        assert grade_extent(
            self._ext(20.0, 120.0), amber_pct=15, min_nm=0,
        ) is AdvisoryStatus.AMBER


class TestTimeAxis:
    """`minutes` on the extent — display only, never a gate (#571 Stage 4)."""

    def test_minutes_from_nm_and_groundspeed(self):
        ext = route_extent([0.0, 50.0, 100.0], 100.0, [False, True, True], speed_kt=120.0)
        # Cells: [0,25] [25,75] [75,100] → affected 50 + 25 = 75nm at 120kt.
        assert ext.nm == 75.0
        assert ext.minutes == 37.5

    def test_minutes_is_none_without_a_speed(self):
        ext = route_extent([0.0, 100.0], 100.0, [True, True])
        assert ext.minutes is None

    def test_format_appends_the_time_when_it_is_worth_saying(self):
        ext = route_extent([0.0, 50.0, 100.0], 100.0, [False, True, True], speed_kt=120.0)
        assert format_extent(ext) == "75nm/100nm (75%), about 38 min in it"

    def test_format_stays_quiet_for_a_sliver(self):
        # One point tightly bracketed by neighbours owns 4nm; at 120kt that is
        # two minutes, and the figure adds nothing the nm did not already say.
        ext = route_extent(
            [0.0, 48.0, 52.0, 56.0, 100.0], 100.0,
            [False, False, True, False, False], speed_kt=120.0,
        )
        assert ext.nm == 4.0
        assert ext.minutes == 2.0
        assert "min" not in format_extent(ext)

    def test_the_gate_never_reads_minutes_unless_asked(self):
        """A minutes floor is opt-in. A large share of flights fall back to a
        profile-default speed, so gating on it by default would grade one
        aircraft differently from another for reasons the pilot never set."""
        slow = RouteExtent(
            points=3, domain_points=10, nm=60.0, domain_nm=100.0,
            longest_run_nm=60.0, minutes=1.0,
        )
        assert grade_extent(slow, amber_pct=15) is AdvisoryStatus.AMBER
        assert grade_extent(
            slow, amber_pct=15, min_minutes=10,
        ) is AdvisoryStatus.GREEN

    def test_summary_extents_inherit_the_speed(self):
        samples = [
            _sample(0.0, S.GREEN),
            _sample(50.0, S.RED),
            _sample(100.0, S.RED),
        ]
        summ = summarize_evidence(samples, 100.0, speed_kt=60.0)
        assert summ.extent.minutes == 75.0          # 75nm at 60kt
        sub = summ.extent_of(lambda s: s.distance_nm == 100.0)
        assert sub.nm == 25.0
        assert sub.minutes == 25.0


class TestCruiseGroundspeed:
    """`RouteContext.cruise_groundspeed_kt` — the real path feeding the time axis.

    Every `TestTimeAxis` case hands `speed_kt` in by hand, so the fallback chain
    that actually runs in production (cross-section wind at the evaluated cruise
    altitude → the pack's baked component → the TAS floor) had no direct
    coverage and could have broken silently (#571 review).
    """

    def _ctx(self, *, wind_components=None, cruise_speed_ias_kt=None,
             flight_duration_hours=0.0, models=("gfs",)):
        from datetime import datetime

        from weatherbrief.analysis.advisories import RouteContext
        from weatherbrief.models import RoutePointAnalysis, SoundingAnalysis

        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 50.0,
                interpolated_time=datetime(2026, 3, 1, 10, 0),
                forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=90.0,
                sounding={"gfs": SoundingAnalysis()},
                wind_components=dict(wind_components or {}),
            )
            for i in range(3)
        ]
        return RouteContext(
            analyses=analyses, cross_sections=[], elevation=None,
            models=list(models), cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            total_distance_nm=100.0,
            cruise_speed_ias_kt=cruise_speed_ias_kt,
            flight_duration_hours=flight_duration_hours,
        )

    def _wc(self, headwind_kt):
        from weatherbrief.models import WindComponent

        return WindComponent(
            wind_speed_kt=abs(headwind_kt), wind_direction_deg=90.0,
            track_deg=90.0, headwind_kt=headwind_kt, crosswind_kt=0.0,
        )

    def test_falls_back_to_the_generic_tas_with_no_speed_and_no_wind(self):
        assert self._ctx().cruise_groundspeed_kt == DEFAULT_CRUISE_TAS_KT

    def test_uses_the_planned_speed_when_there_is_no_aircraft_speed(self):
        # 100nm in 1h is a 100kt groundspeed, already a TAS.
        ctx = self._ctx(flight_duration_hours=1.0)
        assert ctx.cruise_groundspeed_kt == 100.0

    def test_subtracts_the_route_average_headwind(self):
        ctx = self._ctx(
            wind_components={"gfs": self._wc(20.0)}, flight_duration_hours=1.0,
        )
        assert ctx.cruise_groundspeed_kt == 80.0

    def test_a_tailwind_raises_the_groundspeed(self):
        ctx = self._ctx(
            wind_components={"gfs": self._wc(-25.0)}, flight_duration_hours=1.0,
        )
        assert ctx.cruise_groundspeed_kt == 125.0

    def test_is_floored_so_a_headwind_near_tas_cannot_stop_the_clock(self):
        ctx = self._ctx(
            wind_components={"gfs": self._wc(500.0)}, flight_duration_hours=1.0,
        )
        assert ctx.cruise_groundspeed_kt == MIN_GROUNDSPEED_KT

    def test_ignores_models_this_run_does_not_grade(self):
        """The pack can carry components for slots the run excludes; averaging
        those in reports a groundspeed for a model never graded."""
        ctx = self._ctx(
            wind_components={"gfs": self._wc(20.0), "best_match": self._wc(200.0)},
            flight_duration_hours=1.0, models=("gfs",),
        )
        assert ctx.cruise_groundspeed_kt == 80.0

    def test_is_cached_across_reads(self):
        ctx = self._ctx(flight_duration_hours=1.0)
        assert ctx.cruise_groundspeed_kt is ctx.cruise_groundspeed_kt
