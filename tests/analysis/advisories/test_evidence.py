"""Tests for the single-assessment evidence helper (#393 Part 2).

``summarize_evidence`` reduces one per-point :class:`EvidenceSample` list into
the grade counts, the geometry-accurate ``affected_nm``, the highlight geometry,
and the coverage ``data_state`` — the one place grade and highlight are derived
from a single predicate so they cannot drift.
"""

from __future__ import annotations

from weatherbrief.analysis.advisories._helpers import (
    EvidenceSample,
    FlaggedCell,
    summarize_evidence,
)
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
