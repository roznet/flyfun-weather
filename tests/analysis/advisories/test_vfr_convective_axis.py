"""VFR feasibility grades on convection too (§22, the other composite).

`ifr_feasibility` was taught to consume the one convective grade in §22. The VFR
composite was not, and it had no convective input **at all** — its axes were
airport category, en-route cloud clearance, corridor decks and precipitation.
Reported from LFMD→EGTF 2026-08-27: HIGH convection over 47% of route published
**VFR Feasibility GREEN** directly beside **IFR Feasibility RED**, on the same
soundings and the same run. The card whose name promises the VFR answer was the
one card that never looked at the convection.

The invariant here: VFR's convective axis is the convective advisory's status for
that model, softened by — and only by — the character band, which is the axis
built to answer "can a VFR pilot operate *around* this?". The softening is
one-directional; nothing in this path may grade convection *worse* than the
convective card, and nothing may make it GREEN when that card is not.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.convective_character import (
    AVOIDABLE_BANDS as AVOIDABLE_BANDS_IMPORT,
    CHARACTER_STATUS,
    classify_route_character,
    resolve_character_params,
)
from weatherbrief.analysis.advisories.vfr_feasibility import (
    VFRFeasibilityEvaluator,
    _least_severe,
)
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveCharacter,
    ConvectiveRisk,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses

_MODELS = ["gfs"]


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _ctx(soundings: list[SoundingAnalysis], *, cruise_ft: int = 8000) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]
    resolved = _resolve_analyses(
        analyses, icing_method=None, cloud_source=None, convective_method="nwp",
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=_MODELS,
        cruise_altitude_ft=cruise_ft, flight_ceiling_ft=cruise_ft + 4000,
        total_distance_nm=len(soundings) * 20.0,
    )


def _nwp_cell(risk: ConvectiveRisk, *, base_ft: int = 3000, top_ft: int = 30000):
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=risk, cape_jkg=2500, base_ft=base_ft, top_ft=top_ft,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=risk, cape_jkg=2500, base_ft=base_ft, top_ft=top_ft,
        ),
    )


def _quiet():
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=None, base_ft=None, top_ft=None,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=10, base_ft=None, top_ft=None,
        ),
    )


def _status_of(evaluator, ctx: RouteContext) -> AdvisoryStatus:
    res = evaluator.evaluate(ctx, _defaults(evaluator))
    return next(m for m in res.per_model if m.model == "gfs").status


def _vfr(ctx: RouteContext):
    res = VFRFeasibilityEvaluator.evaluate(ctx, _defaults(VFRFeasibilityEvaluator))
    return next(m for m in res.per_model if m.model == "gfs")


_SCENARIOS = {
    "nwp_high_everywhere": [_nwp_cell(ConvectiveRisk.HIGH)] * 6,
    "nwp_high_sparse": [_nwp_cell(ConvectiveRisk.HIGH)] + [_quiet()] * 5,
    "nwp_moderate": [_nwp_cell(ConvectiveRisk.MODERATE)] + [_quiet()] * 5,
    "quiet": [_quiet()] * 6,
}


class TestVFRConsumesTheConvectiveGrade:

    def test_convection_reaches_the_vfr_card_at_all(self):
        """The reported regression: HIGH convection must not read GREEN here."""
        ctx = _ctx(_SCENARIOS["nwp_high_everywhere"])
        assert _status_of(ConvectiveEvaluator, ctx) == AdvisoryStatus.RED
        assert _vfr(ctx).status != AdvisoryStatus.GREEN

    @pytest.mark.parametrize("name", sorted(_SCENARIOS))
    def test_vfr_is_never_calmer_than_the_softened_convective_axis(self, name):
        """VFR >= the convective colour after the sanctioned character softening.

        Stated as a bound rather than an equality because the composite's other
        axes (airport, cloud, corridor, precip) may legitimately push it worse.
        What must never happen is VFR reading calmer than the convection it is
        supposed to have consumed.
        """
        ctx = _ctx(_SCENARIOS[name])
        conv = _status_of(ConvectiveEvaluator, ctx)
        if conv in (AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE):
            return
        character = classify_route_character(ctx, "gfs", resolve_character_params(ctx))
        floor = _least_severe(
            conv,
            CHARACTER_STATUS.get(character, AdvisoryStatus.RED)
            if character is not None else AdvisoryStatus.RED,
        )
        assert AdvisoryStatus.worst([_vfr(ctx).status, floor]) == _vfr(ctx).status

    def test_quiet_air_adds_no_convective_colour(self):
        """The axis may add a colour; it must not invent one."""
        ctx = _ctx(_SCENARIOS["quiet"])
        assert _status_of(ConvectiveEvaluator, ctx) in (
            AdvisoryStatus.GREEN, AdvisoryStatus.UNAVAILABLE,
        )
        assert "convection" not in _vfr(ctx).detail.lower()

    def test_the_sentence_names_the_avoidability(self):
        """A pilot reading the card learns the band, not just the tier."""
        ctx = _ctx(_SCENARIOS["nwp_high_everywhere"])
        detail = _vfr(ctx).detail.lower()
        assert "convection" in detail
        assert "circumnavigable" in detail


class TestTheCapOnlySoftens:
    """`_least_severe` is the cap's one direction, isolated from the fixtures."""

    def test_takes_the_calmer_of_the_two(self):
        assert _least_severe(AdvisoryStatus.RED, AdvisoryStatus.AMBER) == AdvisoryStatus.AMBER
        assert _least_severe(AdvisoryStatus.AMBER, AdvisoryStatus.RED) == AdvisoryStatus.AMBER

    def test_a_worse_band_can_never_escalate_the_activity_grade(self):
        """An ISOLATED band must not lift AMBER activity to RED, and a RED band
        must not lift a GREEN axis — the composite adds colour through `worst`
        over all axes, never through this cap."""
        assert _least_severe(AdvisoryStatus.AMBER, AdvisoryStatus.RED) == AdvisoryStatus.AMBER
        assert _least_severe(AdvisoryStatus.GREEN, AdvisoryStatus.RED) == AdvisoryStatus.GREEN

    def test_equal_statuses_are_idempotent(self):
        for s in (AdvisoryStatus.GREEN, AdvisoryStatus.AMBER, AdvisoryStatus.RED):
            assert _least_severe(s, s) == s

    def test_every_character_band_maps_to_a_status(self):
        """A band with no mapping would silently read GREEN through `.get`."""
        for band in ConvectiveCharacter:
            assert band in CHARACTER_STATUS


class TestBelowBaseEscape:
    """`below_base_escape` finds a level under the cells, when one exists.

    Kept separate from the composite tests because the helper's usefulness and
    its current *reach* are different questions. It is correct — proven here on a
    route where an escape exists — but `_below_base_geometry` tests the whole
    route at once, so on a long route a single low-based cell anywhere condemns
    every altitude. Pilot-reported ground truth on LFMD→EGTF 2026-08-27 is
    exactly that case: the mid-route system was flyable underneath, while cells
    based near 1,500 ft at the arrival end (a different system, hours later)
    made the route-wide answer "no". Scoping the test to contiguous convective
    clusters is the follow-up; until then this helper returns None on such routes
    and the composite simply offers no tip.
    """

    def _route(self, *, cruise_ft: int, base_ft: int):
        """One cell at the origin, quiet elsewhere. `_ctx` sets the ceiling to
        cruise + 4,000 ft and leaves elevation None, so the candidate ladder
        floors at `mitigation_min_base_agl_ft` (3,000 ft) — no terrain to
        confound the geometry under test."""
        soundings = [
            _nwp_cell(ConvectiveRisk.MODERATE, base_ft=base_ft, top_ft=30000),
        ] + [_quiet()] * 5
        return _ctx(soundings, cruise_ft=cruise_ft)

    def test_finds_a_level_beneath_the_cells(self):
        from weatherbrief.analysis.advisories.convective_character import (
            below_base_escape, resolve_character_params,
        )
        # Cells based at 12,000 ft, filed cruise 16,000 ft — inside the layer.
        ctx = self._route(cruise_ft=16000, base_ft=12000)
        escape = below_base_escape(ctx, "gfs", resolve_character_params(ctx))
        assert escape is not None
        # Must clear the 2,000 ft default below-base buffer, and be a descent.
        assert escape.altitude_ft <= 12000 - 2000
        assert escape.altitude_ft < ctx.cruise_altitude_ft
        assert escape.band in AVOIDABLE_BANDS_IMPORT

    def test_publishes_the_base_it_reasoned_from(self):
        """The tip names the base as well as the altitude.

        Modelled convective bases read low (ICON grossly so on the reported
        flight), which makes the offered level conservative — the pilot needs the
        number it came from to check it against what they can actually see.
        """
        from weatherbrief.analysis.advisories.convective_character import (
            below_base_escape, resolve_character_params,
        )
        ctx = self._route(cruise_ft=16000, base_ft=12000)
        escape = below_base_escape(ctx, "gfs", resolve_character_params(ctx))
        assert escape.base_fl == pytest.approx(120, abs=1)
        assert escape.margin_ft >= 2000

    def test_no_escape_when_the_cells_are_based_below_every_candidate(self):
        """The LFMD→EGTF shape: a cell based low enough to condemn the ladder."""
        from weatherbrief.analysis.advisories.convective_character import (
            below_base_escape, resolve_character_params,
        )
        ctx = self._route(cruise_ft=16000, base_ft=1500)
        assert below_base_escape(ctx, "gfs", resolve_character_params(ctx)) is None


class TestAnUncharacterisedBandNeverSoftens:
    """Review round 1, Critical: `NONE`/`UNKNOWN` must not zero a flagged axis.

    The first cut of the cap read `CHARACTER_STATUS`, which is the character
    *card's own grade* — where NONE → GREEN is correct ("nothing to
    characterise"). Used as a cap it turned "no answer" into "it's fine":
    `_least_severe(RED, GREEN)` is GREEN. That is the #391 false-clear, and the
    §22 divergence this whole axis exists to close, reintroduced through it.

    The live reproduction is a LOW-risk route: the character axis floors at
    `min_risk` MODERATE while grading floors at LOW, so an AMBER convective card
    yields character NONE — and the composite published GREEN with a detail line
    that read "not circumnavigable VFR" beside it.
    """

    def _low_risk_route(self):
        return _ctx([_nwp_cell(ConvectiveRisk.LOW)] * 8)

    def test_low_risk_route_keeps_the_convective_colour(self):
        ctx = self._low_risk_route()
        assert _status_of(ConvectiveEvaluator, ctx) == AdvisoryStatus.AMBER
        assert classify_route_character(
            ctx, "gfs", resolve_character_params(ctx)
        ) is ConvectiveCharacter.NONE
        # The bug published GREEN here.
        assert _vfr(ctx).status == AdvisoryStatus.AMBER

    def test_it_claims_neither_avoidability_nor_its_absence(self):
        """The sentence tracks what was established, not the colour.

        "circumnavigable" would be the false clear in prose; "not
        circumnavigable" (what the buggy ternary printed) asserts a judgement the
        character axis never made.
        """
        detail = _vfr(self._low_risk_route()).detail.lower()
        assert "avoidability not established" in detail
        assert "circumnavigable" not in detail

    def test_the_cap_still_fires_for_a_band_that_did_establish_it(self):
        """The guard must not have disabled the softening it guards."""
        ctx = _ctx([_nwp_cell(ConvectiveRisk.HIGH)] + [_quiet()] * 7)
        assert _status_of(ConvectiveEvaluator, ctx) == AdvisoryStatus.RED
        assert classify_route_character(
            ctx, "gfs", resolve_character_params(ctx)
        ) is ConvectiveCharacter.ISOLATED
        result = _vfr(ctx)
        assert result.status == AdvisoryStatus.AMBER
        assert "circumnavigable with see-and-avoid" in result.detail.lower()


class TestConvectionReachesTheHighlight:
    """Review round 1, Important: the graded axis must also be drawn.

    Convection can be the sole driver of the composite's colour — the reported
    LFMD→EGTF shape had airport, cloud, corridor and precip all clear. The
    highlight built only cloud/corridor geometry, so it returned an empty
    `regions` list and an all-GREEN ribbon; both renderers skip the scrim on
    empty regions, so the cross-section drew a spotless chart beside an AMBER
    badge.
    """

    def _conv_only_route(self):
        # Cells based well above cruise so the cloud axes stay quiet and
        # convection is the only thing flagging.
        return _ctx(
            [_nwp_cell(ConvectiveRisk.HIGH, base_ft=14000, top_ft=32000)] * 4
            + [_quiet()] * 4,
            cruise_ft=8000,
        )

    def test_regions_are_not_empty_when_convection_drives_the_grade(self):
        ctx = self._conv_only_route()
        result = _vfr(ctx)
        assert result.status != AdvisoryStatus.GREEN
        regions = (result.highlights.regions if result.highlights else [])
        assert regions, "convective grade with no geometry — the scrim would skip"
        assert any(r.metric_id == "convective_risk" for r in regions)

    def test_the_ribbon_is_not_all_green_under_a_flagged_grade(self):
        result = _vfr(self._conv_only_route())
        severities = {s.severity for s in result.highlights.ribbon}
        assert severities - {"green"}, f"ribbon reads all-green: {severities}"
