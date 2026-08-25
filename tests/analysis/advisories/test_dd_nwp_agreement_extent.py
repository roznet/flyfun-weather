"""The DD/NWP sentence names one category; the object must publish it.

`dd_nwp_agreement` grades on the union of every disagreeing category — any
category diverging is a divergence — but its sentence names only the top one
("freezing track diverges over 70nm"). It published the union, so the miles a
reader saw and the miles the API carried described different populations: the
D1 defect the PR exists to remove, in the evaluator whose comment claimed it
was already handled (#571 review round 8).

The advisory is off by default, which bounds the blast radius but not the
defect: a user who enables it gets the same disagreement everyone else's
advisories no longer have.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.dd_nwp_agreement import DDvsNWPAgreementEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    EnhancedCloudLayer,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

_DECK = [EnhancedCloudLayer(base_ft=3000, top_ft=6000, coverage=CloudCoverage.OVC)]
_DEFAULTS = {
    p.key: p.default for p in DDvsNWPAgreementEvaluator.catalog_entry().parameters
}


def _sounding(freezing_diverges: bool, clouds_diverge: bool) -> SoundingAnalysis:
    """One point. Freezing diverges by 4000 ft (gate is 2000); clouds diverge by
    the DD deck having no native NWP counterpart at all."""
    cloud_kw = (
        dict(
            dd_cloud_layers=_DECK,
            nwp_cloud_layers=[],
            nwp_cloud_diagnostics={"cloud_cover_pct": 0.0},
        )
        if clouds_diverge else {}
    )
    return SoundingAnalysis(
        indices=ThermodynamicIndices(
            freezing_level_ft=5000,
            nwp_freezing_level_ft=9000 if freezing_diverges else 5100,
        ),
        **cloud_kw,
    )


def _ctx(freezing_idx: set[int], cloud_idx: set[int], n: int = 10) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": _sounding(i in freezing_idx, i in cloud_idx)},
        )
        for i in range(n)
    ]
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=(n - 1) * 20.0,
    )


class TestNamedCategoryExtent:
    @pytest.fixture
    def split(self):
        """Freezing diverges on 4 points, clouds on 2 others: union 6, top 4."""
        ctx = _ctx({0, 1, 2, 3}, {6, 7})
        m = DDvsNWPAgreementEvaluator.evaluate(ctx, _DEFAULTS).per_model[0]
        assert m.status != AdvisoryStatus.GREEN, "fixture must reach the graded branch"
        return m

    def test_the_two_populations_are_actually_different(self, split):
        """Guards the guard: with one category the assertions below are vacuous."""
        assert split.affected_points == 6
        assert split.affected_mod_points == 4

    def test_the_sentence_names_the_category_extent(self, split):
        assert "freezing track diverges over 70nm/180nm" in split.detail
        assert "110nm" not in split.detail

    def test_the_object_publishes_the_named_extent(self, split):
        assert split.affected_nm == 110.0        # the union the grade keys on
        assert split.affected_mod_nm == 70.0     # the category the sentence named

    def test_a_single_category_publishes_one_number_twice(self):
        """When only one category diverges the two populations coincide."""
        ctx = _ctx({0, 1, 2, 3}, set())
        m = DDvsNWPAgreementEvaluator.evaluate(ctx, _DEFAULTS).per_model[0]
        assert m.affected_mod_nm == m.affected_nm
        assert m.affected_mod_points == m.affected_points

    def test_agreement_publishes_the_empty_extent(self):
        """The GREEN branch never computes a category extent; it must not carry
        a stale one from the union."""
        ctx = _ctx(set(), set())
        m = DDvsNWPAgreementEvaluator.evaluate(ctx, _DEFAULTS).per_model[0]
        assert m.status == AdvisoryStatus.GREEN
        assert m.affected_mod_nm == 0.0
        assert m.affected_mod_points == 0
