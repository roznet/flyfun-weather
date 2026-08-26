"""Effective-method provenance: the evidence contract stops lying under fallback (#408).

Two halves finally joined:

* **Producer** — ``_resolve_analyses`` stamps ``icing_method_effective`` /
  ``convective_method_effective`` (the siblings of ``cloud_method_effective``)
  with the method it *actually* graded on, recording every silent fallback.
* **Consumer** — the icing/cloud evaluators badge their evidence regions and
  ``primary_method_id`` from those effective fields, so a chip reports the method
  that drove the grade — never the requested one, which diverges exactly where a
  pilot most needs the truth.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories._helpers import driving_method_id
from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.convective_character import (
    ConvectiveCharacterEvaluator,
)
from weatherbrief.analysis.advisories.enroute_precip import EnroutePrecipEvaluator
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.analysis.advisories.vfr_feasibility import VFRFeasibilityEvaluator
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.tasks.advise import _resolve_analyses
from weatherbrief.models import (
    AdvisoryHighlights,
    AdvisoryStatus,
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveRisk,
    EnhancedCloudLayer,
    HighlightRegion,
    HighlightSeverity,
    IcingRisk,
    IcingType,
    IcingZone,
    PrecipIntensity,
    PrecipitationAssessment,
    PrecipPhase,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _analyses(soundings: list[SoundingAnalysis]) -> list[RoutePointAnalysis]:
    return [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]


def _ctx(
    soundings: list[SoundingAnalysis],
    *,
    icing_method: str | None = None,
    cloud_source: str | None = None,
    convective_method: str | None = None,
) -> RouteContext:
    resolved = _resolve_analyses(
        _analyses(soundings),
        icing_method=icing_method,
        cloud_source=cloud_source,
        convective_method=convective_method,
    )
    # The route length must match where the points actually are: they sit at
    # 20 nm intervals from the origin, so a hardcoded 180 nm left the final
    # point owning a 90 nm cell and made every distance-based coverage figure
    # (#571) a fixture artefact rather than a property of the weather.
    total_nm = max((len(soundings) - 1) * 20.0, 20.0)
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=total_nm,
    )


# ---------------------------------------------------------------------------
# Part 1 (producer): _resolve_analyses stamps the EFFECTIVE method
# ---------------------------------------------------------------------------


class TestConvectiveMethodEffective:
    def test_nwp_present_records_nwp(self):
        s = SoundingAnalysis(
            convective_nwp=ConvectiveAssessment(risk_level=ConvectiveRisk.LOW, cape_jkg=500),
            convective_thermo=ConvectiveAssessment(risk_level=ConvectiveRisk.HIGH, cape_jkg=2500),
        )
        resolved = _resolve_analyses(_analyses([s]), None, None, "nwp")
        assert resolved[0].sounding["gfs"].convective_method_effective == "nwp"

    def test_nwp_absent_records_thermo_fallback(self):
        """The honesty gap: NWP requested, ``convective_nwp`` None → graded on thermo.

        Nothing recorded this before #408 — the pilot asked for NWP convective and
        silently got thermo wherever the model-native track was absent.
        """
        s = SoundingAnalysis(
            convective_nwp=None,
            convective_thermo=ConvectiveAssessment(risk_level=ConvectiveRisk.HIGH, cape_jkg=2500),
        )
        resolved = _resolve_analyses(_analyses([s]), None, None, "nwp")
        eff = resolved[0].sounding["gfs"]
        assert eff.convective_method_effective == "thermo"
        # And it really did fall back to the thermo assessment.
        assert eff.convective.risk_level == ConvectiveRisk.HIGH


class TestIcingMethodEffective:
    def _envelope(self):
        return [EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)]

    def test_ogimet_nwp_with_envelope_records_ogimet_nwp(self):
        s = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=5000),
            nwp_cloud_layers=self._envelope(),
            icing_ogimet_nwp_zones=[],
        )
        resolved = _resolve_analyses(_analyses([s]), "ogimet_nwp", None)
        eff = resolved[0].sounding["gfs"]
        assert eff.icing_method_effective == "ogimet_nwp"
        assert eff.active_icing_available is True

    def test_ogimet_nwp_no_envelope_leaves_effective_unset(self):
        """Could-not-run has no honest label — effective stays None, not a lie."""
        s = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=5000),
            nwp_cloud_layers=None,
            icing_ogimet_nwp_zones=[],
        )
        resolved = _resolve_analyses(_analyses([s]), "ogimet_nwp", None)
        eff = resolved[0].sounding["gfs"]
        assert eff.icing_method_effective is None
        assert eff.active_icing_available is False

    def test_sfip_nwp_records_sfip_nwp(self):
        s = SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))
        resolved = _resolve_analyses(_analyses([s]), "sfip_nwp", None)
        assert resolved[0].sounding["gfs"].icing_method_effective == "sfip_nwp"


class TestSparseProfileResolvesToConcreteMethod:
    """The post-#407 majority store no engine keys — absence means the default.

    Anyone sourcing the method from the profile would get None for these users;
    the effective field resolves the declared default and stays concrete.
    """

    def test_all_none_resolves_defaults(self):
        s = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=5000),
            nwp_cloud_layers=[EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)],
            icing_ogimet_nwp_zones=[],
            convective_nwp=ConvectiveAssessment(risk_level=ConvectiveRisk.LOW, cape_jkg=400),
        )
        resolved = _resolve_analyses(_analyses([s]), None, None, None)
        eff = resolved[0].sounding["gfs"]
        # Defaults: ogimet_nwp icing / square_nwp cloud / nwp convective (#403).
        # Concrete on every axis — never None, which is what sourcing from the
        # (empty) profile would have yielded for these users.
        assert eff.icing_method_effective == "ogimet_nwp"
        # "nwp" for model-native GRIB layers, "nwp_synthesized" for synthesized
        # ones — both concrete NWP, distinct from a DD fallback.
        assert eff.cloud_method_effective in ("nwp", "nwp_synthesized")
        assert eff.convective_method_effective == "nwp"


class TestExplicitDdThermoStillBadges:
    """The no-swap path must badge too.

    An explicit DD/thermo selection swaps no data, so ``_resolve_analyses`` used
    to early-return the untouched list and leave every ``*_method_effective`` as
    None. That made "graded on DD" indistinguishable from "this advisory has no
    method axis" (turbulence, mountain_wind) — the same absence-reads-as-
    something-else failure #391/#393 exist to kill. Every axis now stamps the
    method that graded it, even when there is nothing to swap.
    """

    def test_explicit_dd_thermo_badges_every_axis(self):
        s = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=5000),
            convective_thermo=ConvectiveAssessment(risk_level=ConvectiveRisk.LOW, cape_jkg=400),
        )
        resolved = _resolve_analyses(_analyses([s]), "ogimet_dd", "dd", "thermo")
        eff = resolved[0].sounding["gfs"]
        assert eff.icing_method_effective == "ogimet_dd"
        assert eff.cloud_method_effective == "dd"
        assert eff.convective_method_effective == "thermo"

    def test_dd_source_badges_dd(self):
        """Since #410 ``_resolve_analyses`` takes a bare ``cloud_source`` — the
        legacy ``<style>_<source>`` form is reduced upstream at the read boundary."""
        s = SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=5000))
        resolved = _resolve_analyses(_analyses([s]), "ogimet_dd", "dd", "thermo")
        assert resolved[0].sounding["gfs"].cloud_method_effective == "dd"


# ---------------------------------------------------------------------------
# The driving-region rollup helper
# ---------------------------------------------------------------------------


class TestDrivingMethodId:
    def _hl(self, *regions: HighlightRegion) -> AdvisoryHighlights:
        return AdvisoryHighlights(ribbon=[], regions=list(regions))

    def _region(self, sev, method_id, kind="icing_band"):
        return HighlightRegion(
            dist_from_nm=0, dist_to_nm=10, kind=kind, severity=sev, method_id=method_id,
        )

    def test_flagged_grade_badges_stamped_method(self):
        hl = self._hl(self._region(HighlightSeverity.RED, "ogimet_nwp"))
        assert driving_method_id(hl, AdvisoryStatus.RED) == "ogimet_nwp"

    def test_green_grade_has_no_badge_even_with_flagged_region(self):
        # A sub-threshold RED region under a GREEN grade is not a concern to badge.
        hl = self._hl(self._region(HighlightSeverity.RED, "dd"))
        assert driving_method_id(hl, AdvisoryStatus.GREEN) is None
        assert driving_method_id(hl, AdvisoryStatus.UNAVAILABLE) is None

    def test_none_highlights(self):
        assert driving_method_id(None, AdvisoryStatus.RED) is None

    def test_ignores_unstamped_regions(self):
        # A method-less region (non-method axis) never supplies a badge.
        hl = self._hl(self._region(HighlightSeverity.AMBER, None, kind="cat_layer"))
        assert driving_method_id(hl, AdvisoryStatus.AMBER) is None

    def test_grade_above_capped_region_still_badges(self):
        """RED grade escalated by percentage past an AMBER-capped region (#409 r1).

        cloud_top ≥60% AMBER decks → RED; the badge must survive the escalation.
        """
        hl = self._hl(self._region(HighlightSeverity.AMBER, "ogimet_nwp"))
        assert driving_method_id(hl, AdvisoryStatus.RED) == "ogimet_nwp"

    def test_grade_below_region_severity_still_badges(self):
        """AMBER grade whose only region is RED-severity (#409 r3).

        vmc_cruise sub-red OVC → AMBER off RED cruise_imc regions; icing_escape
        isolated no-escape → AMBER off a RED icing_band. The mirror of the r1
        case — the badge must survive here too.
        """
        hl = self._hl(self._region(HighlightSeverity.RED, "dd"))
        assert driving_method_id(hl, AdvisoryStatus.AMBER) == "dd"

    def test_highest_severity_region_is_representative(self):
        # One model graded points on different effective methods; the region that
        # most drove the grade (highest severity) supplies the badge.
        hl = self._hl(
            self._region(HighlightSeverity.AMBER, "nwp_synthesized"),
            self._region(HighlightSeverity.RED, "dd"),
        )
        assert driving_method_id(hl, AdvisoryStatus.RED) == "dd"


# ---------------------------------------------------------------------------
# Part 2 (consumer): evaluators badge the effective method
# ---------------------------------------------------------------------------


def _region_method_ids(result) -> list[str | None]:
    out: list[str | None] = []
    for m in result.per_model:
        if m.highlights:
            out.extend(r.method_id for r in m.highlights.regions)
    return out


class TestCloudEvaluatorsBadgeEffective:
    def _ovc_sounding(self) -> SoundingAnalysis:
        """DD layers only (OVC at cruise); NWP requested but absent → falls to DD."""
        return SoundingAnalysis(
            cloud_layers=[EnhancedCloudLayer(base_ft=3000, top_ft=12000, coverage=CloudCoverage.OVC)],
            nwp_cloud_layers=None,
        )

    def test_vmc_cruise_reports_fallback_dd_not_requested_nwp(self):
        """Requested ``square_nwp`` but graded on DD layers → region says ``dd``.

        The exact lie #408 prevents: labelling the region with the requested
        NWP method when the grade actually rode DD layers.
        """
        ctx = _ctx([self._ovc_sounding() for _ in range(6)], cloud_source="nwp")
        result = VMCCruiseEvaluator.evaluate(ctx, _defaults(VMCCruiseEvaluator))
        assert result.aggregate_status == AdvisoryStatus.RED
        methods = _region_method_ids(result)
        assert methods and all(m == "dd" for m in methods)
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "dd"

    def test_vmc_cruise_amber_off_red_regions_keeps_badge(self):
        """AMBER grade (2/6 OVC, below extent_pct_red) whose only regions are RED (#409 r3).

        The mirror of the cloud_top escalation case: here the grade lands *below*
        the region severity, and the badge must still survive.
        """
        clear = SoundingAnalysis(cloud_layers=[], nwp_cloud_layers=None)
        ctx = _ctx(
            [self._ovc_sounding(), self._ovc_sounding(), clear, clear, clear, clear],
            cloud_source="nwp",
        )
        result = VMCCruiseEvaluator.evaluate(ctx, _defaults(VMCCruiseEvaluator))
        assert result.aggregate_status == AdvisoryStatus.AMBER  # 33% OVC < 50 red
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "dd"

    def test_cloud_top_badges_effective_method(self):
        # Reachable deck with tops above ceiling → amber blocking_deck.
        s = SoundingAnalysis(
            cloud_layers=[EnhancedCloudLayer(base_ft=7000, top_ft=17500, coverage=CloudCoverage.BKN)],
            nwp_cloud_layers=None,
        )
        ctx = _ctx([s for _ in range(6)], cloud_source="nwp")
        result = CloudTopEvaluator.evaluate(ctx, _defaults(CloudTopEvaluator))
        # 6/6 blocking → 100% coverage → RED, though every region is AMBER-capped.
        assert result.aggregate_status == AdvisoryStatus.RED
        assert _region_method_ids(result) and all(m == "dd" for m in _region_method_ids(result))
        # The badge must survive the extent escalation to RED (#409 regression).
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "dd"


class TestIcingEvaluatorsBadgeEffective:
    def _iced_no_escape(self) -> SoundingAnalysis:
        """Icing near cruise with an envelope so Ogimet-NWP runs; no terrain → no escape."""
        envelope = [EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)]
        zone = IcingZone(base_ft=4000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)
        return SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope,
            icing_ogimet_nwp_zones=[zone],
        )

    def test_icing_escape_reports_effective_ogimet_nwp(self):
        ctx = _ctx([self._iced_no_escape() for _ in range(6)], icing_method="ogimet_nwp")
        result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
        assert result.aggregate_status != AdvisoryStatus.GREEN
        assert _region_method_ids(result)
        assert all(m == "ogimet_nwp" for m in _region_method_ids(result))
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "ogimet_nwp"

    def test_sparse_profile_still_reports_concrete_method(self):
        """No engine keys (None methods) → resolves ogimet_nwp; never None on a flag."""
        ctx = _ctx([self._iced_no_escape() for _ in range(6)])  # all methods None
        result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
        assert _region_method_ids(result)
        assert all(m == "ogimet_nwp" for m in _region_method_ids(result))
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id is not None

    def test_explicit_dd_icing_badges_ogimet_dd_not_none(self):
        """A pilot who explicitly grades on DD gets a real badge, not a blank.

        End-to-end proof the no-swap gap is closed: before, ``ogimet_dd`` swapped
        nothing, so ``method_id`` came back None and a chip could not tell "graded
        on DD" apart from "no method axis".
        """
        zone = IcingZone(base_ft=4000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)
        dd_iced = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            icing_zones=[zone],  # DD-derived zones already in place — no swap
        )
        ctx = _ctx([dd_iced for _ in range(6)], icing_method="ogimet_dd")
        result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
        assert result.aggregate_status != AdvisoryStatus.GREEN
        assert _region_method_ids(result)
        assert all(m == "ogimet_dd" for m in _region_method_ids(result))
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "ogimet_dd"

    def test_icing_escape_amber_off_isolated_no_escape_keeps_badge(self):
        """1/20 no-escape (RED region) on an otherwise clear route → AMBER (#409 r3).

        no_escape_count=1 of 20 = 5% < extent_pct_red (15) grades AMBER, but
        the only region present is RED-severity — the badge must survive.
        """
        envelope = [EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)]
        no_escape = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope,
            icing_ogimet_nwp_zones=[
                IcingZone(base_ft=4000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED),
            ],
        )
        clear = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope, icing_ogimet_nwp_zones=[],
        )
        # ctx has no elevation → terrain unknown → the iced point is no-escape (RED).
        ctx = _ctx([no_escape] + [clear] * 19, icing_method="ogimet_nwp")
        result = IcingEscapeEvaluator.evaluate(ctx, _defaults(IcingEscapeEvaluator))
        assert result.aggregate_status == AdvisoryStatus.AMBER
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "ogimet_nwp"

    def test_fiki_icing_badges_effective(self):
        ctx = _ctx([self._iced_no_escape() for _ in range(6)], icing_method="ogimet_nwp")
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults(FIKIIcingEvaluator))
        assert _region_method_ids(result)
        assert all(m == "ogimet_nwp" for m in _region_method_ids(result))
        # clear-cruise fraction grades RED off AMBER-capped points — badge stands.
        assert result.aggregate_status == AdvisoryStatus.RED
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "ogimet_nwp"

    def test_ifr_feasibility_icing_regions_badge_effective(self):
        ctx = _ctx([self._iced_no_escape() for _ in range(6)], icing_method="ogimet_nwp")
        result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
        icing_methods = [
            r.method_id
            for m in result.per_model if m.highlights
            for r in m.highlights.regions if r.kind == "icing_band"
        ]
        assert icing_methods and all(m == "ogimet_nwp" for m in icing_methods)
        # 6/6 iced → icing_pct RED off AMBER icing bands: primary must survive.
        assert result.aggregate_status == AdvisoryStatus.RED
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "ogimet_nwp"

    def test_ifr_convective_driven_red_does_not_borrow_icing_method(self):
        """Convective (method-less tower) drives RED while icing is only AMBER.

        The badge must NOT fall back to the icing method — that would tell the
        pilot icing drove the RED when convection did (#409 composite guard).
        """
        envelope = [EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)]
        zone = IcingZone(base_ft=6500, top_ft=9500, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME)
        # On the model's own NWP track: since #568 a model with no native
        # convective forecast is capped at AMBER, so a bare ``convective=`` HIGH
        # would no longer drive the composite RED this test is about.
        high_conv = ConvectiveAssessment(risk_level=ConvectiveRisk.HIGH, cape_jkg=2600)
        iced = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope, icing_ogimet_nwp_zones=[zone],
            convective_nwp=high_conv,
        )
        clear = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope, icing_ogimet_nwp_zones=[],
            convective_nwp=high_conv,
        )
        # 2 iced of 6 → icing_pct ~33% (AMBER band, below RED 50); HIGH conv → RED.
        ctx = _ctx([iced, iced, clear, clear, clear, clear], icing_method="ogimet_nwp")
        result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
        assert result.aggregate_status == AdvisoryStatus.RED
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id is None

    def test_ifr_below_threshold_icing_does_not_borrow_method_for_conv_amber(self):
        """The pooled-region trap (#409 round 2): icing under its own threshold.

        1/20 points has in-buffer icing → an AMBER icing_band region IS emitted
        (geometry is per-point), but icing_pct=5% is below extent_pct_amber (20),
        so the icing axis grades GREEN and contributes nothing. A single MODERATE
        convective point drives the composite to AMBER. The badge must NOT report
        the icing method just because an AMBER icing region sorts first in the
        pooled list — convective (method-less) drove it.
        """
        envelope = [EnhancedCloudLayer(base_ft=4000, top_ft=10000, coverage=CloudCoverage.OVC)]
        plain = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope, icing_ogimet_nwp_zones=[],
        )
        iced = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope,
            icing_ogimet_nwp_zones=[
                IcingZone(base_ft=6500, top_ft=9500, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME),
            ],
        )
        moderate_conv = SoundingAnalysis(
            indices=ThermodynamicIndices(freezing_level_ft=6000),
            nwp_cloud_layers=envelope, icing_ogimet_nwp_zones=[],
            convective=ConvectiveAssessment(risk_level=ConvectiveRisk.MODERATE, cape_jkg=1400),
        )
        soundings = [iced, moderate_conv] + [plain] * 18  # 1/20 iced, 1/20 conv
        ctx = _ctx(soundings, icing_method="ogimet_nwp")
        result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
        assert result.aggregate_status == AdvisoryStatus.AMBER  # convective-driven
        # An AMBER icing region exists (per-point geometry), stamped ogimet_nwp…
        assert any(
            r.kind == "icing_band" and r.method_id == "ogimet_nwp"
            for m in result.per_model if m.highlights for r in m.highlights.regions
        )
        # …but the badge must not borrow it — icing never crossed its threshold.
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id is None


class TestBuildRegionsMethodSplit:
    """build_regions must not merge a run across a method_id change (#409)."""

    def test_splits_run_on_method_change(self):
        from weatherbrief.analysis.advisories._helpers import FlaggedCell, build_regions

        # Two adjacent flagged points, same kind+severity, different method — a
        # model with NWP layers at one point and DD fallback at the next.
        per_point = [
            (0.0, FlaggedCell(kind="icing_band", severity=HighlightSeverity.AMBER,
                              base_ft=4000, top_ft=9000, method_id="ogimet_nwp")),
            (20.0, FlaggedCell(kind="icing_band", severity=HighlightSeverity.AMBER,
                               base_ft=4000, top_ft=9000, method_id="ogimet_dd")),
        ]
        regions = build_regions(per_point, 40.0)
        assert len(regions) == 2
        assert [r.method_id for r in regions] == ["ogimet_nwp", "ogimet_dd"]

    def test_still_merges_when_method_uniform(self):
        from weatherbrief.analysis.advisories._helpers import FlaggedCell, build_regions

        per_point = [
            (0.0, FlaggedCell(kind="icing_band", severity=HighlightSeverity.AMBER,
                              base_ft=4000, top_ft=9000, method_id="ogimet_nwp")),
            (20.0, FlaggedCell(kind="icing_band", severity=HighlightSeverity.AMBER,
                               base_ft=5000, top_ft=8000, method_id="ogimet_nwp")),
        ]
        regions = build_regions(per_point, 40.0)
        assert len(regions) == 1
        assert regions[0].method_id == "ogimet_nwp"


class TestNonMethodAxisReportsNone:
    """Evaluators with no engine-method axis leave ``method_id`` None (reserved space)."""

    def test_enroute_precip_regions_have_no_method(self):
        snow = PrecipitationAssessment(
            surface_phase=PrecipPhase.SNOW,
            surface_intensity=PrecipIntensity.MODERATE,
        )
        s = SoundingAnalysis(precipitation=snow)
        ctx = _ctx([s for _ in range(6)])
        result = EnroutePrecipEvaluator.evaluate(ctx, _defaults(EnroutePrecipEvaluator))
        assert result.aggregate_status != AdvisoryStatus.GREEN  # snow flags
        # Regions exist, but carry no method label, and no primary_method_id.
        assert all(m == None for m in _region_method_ids(result))  # noqa: E711
        assert all(m.primary_method_id is None for m in result.per_model)


class TestConvectiveEvaluatorBadgesEffective:
    """The convective evaluator's consumer half (the last method-bearing axis).

    The chip must select the layer the evidence was *drawn from*. The convective
    grade is now NWP-native (#442): DD no longer floors the colour, and a
    ``dd_trigger`` amber (or a plain NWP grade) still draws the NWP track's
    geometry — so the method stays ``nwp`` rather than becoming a compound token.
    A CAPE fallback (no ``convective_nwp`` at all) genuinely changes the source,
    and is reported.
    """

    @staticmethod
    def _sounding(nwp_risk, thermo_risk, *, nwp: bool = True) -> SoundingAnalysis:
        return SoundingAnalysis(
            convective_nwp=(
                ConvectiveAssessment(
                    risk_level=nwp_risk, cape_jkg=2500, base_ft=3000, top_ft=30000,
                )
                if nwp else None
            ),
            convective_thermo=ConvectiveAssessment(
                risk_level=thermo_risk, cape_jkg=2500, base_ft=3000, top_ft=30000,
            ),
        )

    def test_nwp_track_badges_nwp(self):
        ctx = _ctx(
            [self._sounding(ConvectiveRisk.HIGH, ConvectiveRisk.HIGH) for _ in range(6)],
            convective_method="nwp",
        )
        result = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "nwp"
        assert all(m == "nwp" for m in _region_method_ids(result))

    def test_dd_divergence_does_not_compound_the_method(self):
        """NWP LOW under a HIGH DD tower → graded on NWP (amber), method still
        ``nwp``: the geometry the chip draws is the NWP track's (#442). Report the
        *intended* track; the DD divergence rides ``reason_code`` / the cross-check,
        not ``method_id``.
        """
        ctx = _ctx(
            [self._sounding(ConvectiveRisk.LOW, ConvectiveRisk.HIGH) for _ in range(6)],
            convective_method="nwp",
        )
        result = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "nwp"
        assert "_with_" not in (rep.primary_method_id or "")

    def test_cape_fallback_badges_thermo(self):
        """No native NWP track → the source really is thermo, and we say so."""
        ctx = _ctx(
            [
                self._sounding(ConvectiveRisk.HIGH, ConvectiveRisk.HIGH, nwp=False)
                for _ in range(6)
            ],
            convective_method="nwp",
        )
        result = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "thermo"


class TestConvectiveCharacterBadgesEffective:
    """The character card badges its track too (#568).

    ``ConvectiveCharacterEvaluator`` built every ``ModelAdvisoryResult`` without
    ``primary_method_id``, so all models reported ``None``. The severity card
    badges it (and correctly showed "thermo" for the model with no native
    track) — but the **character card is the one that renders the EMBEDDED
    red**, and it gave the pilot no indication that one model was graded on a
    different track from the other two. ``driving_method_id`` cannot be reused:
    it sources from ``AdvisoryHighlights``, which this evaluator does not
    produce, so the method travels out of ``build_character_points`` instead.
    """

    @staticmethod
    def _cell(*, nwp: bool) -> SoundingAnalysis:
        """A MODERATE cell with resolved geometry — realized, so it makes a band."""
        thermo = ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, cape_jkg=1200,
            base_ft=6000, top_ft=28000,
        )
        return SoundingAnalysis(
            convective=thermo,
            convective_thermo=thermo,
            convective_nwp=(
                ConvectiveAssessment(
                    risk_level=ConvectiveRisk.MODERATE, cape_jkg=1200,
                    base_ft=6000, top_ft=28000, convective_precip_mm_h=1.5,
                )
                if nwp else None
            ),
        )

    @staticmethod
    def _badge(ctx) -> str | None:
        result = ConvectiveCharacterEvaluator.evaluate(
            ctx, _defaults(ConvectiveCharacterEvaluator)
        )
        return next(m for m in result.per_model if m.model == "gfs").primary_method_id

    def test_native_track_badges_nwp(self):
        ctx = _ctx([self._cell(nwp=True) for _ in range(6)], convective_method="nwp")
        assert self._badge(ctx) == "nwp"

    def test_absent_native_track_badges_thermo(self):
        """The outlier model on the motivating pack: graded on thermodynamics."""
        ctx = _ctx([self._cell(nwp=False) for _ in range(6)], convective_method="nwp")
        assert self._badge(ctx) == "thermo"

    def test_a_single_fallback_point_is_not_averaged_away(self):
        """A mixed route badges the fallback: it is the fact the badge carries."""
        soundings = [self._cell(nwp=True) for _ in range(5)] + [self._cell(nwp=False)]
        assert self._badge(_ctx(soundings, convective_method="nwp")) == "thermo"

    def test_explicit_thermo_request_badges_thermo(self):
        ctx = _ctx([self._cell(nwp=True) for _ in range(6)], convective_method="thermo")
        assert self._badge(ctx) == "thermo"


class TestCompositeRegionProvenance:
    """The feasibility composites carry provenance on every region they emit.

    They reuse other evaluators' geometry, and originally propagated none of it:
    `vfr_feasibility` emitted no `metric_id`/`method_id` at all, and
    `ifr_feasibility`'s convective towers carried no method (correct when
    convective had none — stale once it did). A region's provenance describes
    *that region*, independent of which axis won the composite grade; only
    `primary_method_id` is gated on the winning axis.
    """

    @staticmethod
    def _regions(result):
        out = []
        for pm in result.per_model:
            if pm.highlights:
                out.extend(pm.highlights.regions)
        return out

    def test_vfr_cloud_regions_carry_cloud_provenance(self):
        ctx = _ctx(
            [
                SoundingAnalysis(
                    cloud_layers=[
                        EnhancedCloudLayer(base_ft=3000, top_ft=12000, coverage=CloudCoverage.OVC)
                    ],
                    nwp_cloud_layers=None,
                )
                for _ in range(6)
            ],
            cloud_source="nwp",   # requested NWP; no native layers → DD
        )
        result = VFRFeasibilityEvaluator.evaluate(ctx, _defaults(VFRFeasibilityEvaluator))
        regions = self._regions(result)
        assert regions, "expected flagged cloud regions"
        assert all(r.metric_id == "cloud_cover" for r in regions)
        # The honesty point: requested NWP, graded on DD, region says DD.
        assert all(r.method_id == "dd" for r in regions)

    def test_ifr_convective_towers_carry_convective_provenance(self):
        ctx = _ctx(
            [
                SoundingAnalysis(
                    convective_nwp=None,   # no native track → thermo fallback
                    convective_thermo=ConvectiveAssessment(
                        risk_level=ConvectiveRisk.HIGH, cape_jkg=2500,
                        base_ft=3000, top_ft=30000,
                    ),
                )
                for _ in range(6)
            ],
            convective_method="nwp",
        )
        result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
        towers = [
            r for r in self._regions(result) if r.kind in ("tower", "tower_unresolved")
        ]
        assert towers, "expected flagged convective towers"
        assert all(r.metric_id == "convective_risk" for r in towers)
        # Was None before: the composite never propagated convective's method.
        assert all(r.method_id == "thermo" for r in towers)
