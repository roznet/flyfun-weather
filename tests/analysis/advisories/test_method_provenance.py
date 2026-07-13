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
from weatherbrief.analysis.advisories.enroute_precip import EnroutePrecipEvaluator
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
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
    cloud_method: str | None = None,
    convective_method: str | None = None,
) -> RouteContext:
    resolved = _resolve_analyses(
        _analyses(soundings),
        icing_method=icing_method,
        cloud_method=cloud_method,
        convective_method=convective_method,
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=8000, flight_ceiling_ft=18000, total_distance_nm=180,
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

    def test_matches_region_of_grade_severity(self):
        hl = self._hl(self._region(HighlightSeverity.RED, "ogimet_nwp"))
        assert driving_method_id(hl, AdvisoryStatus.RED) == "ogimet_nwp"

    def test_green_grade_has_no_driving_region(self):
        hl = self._hl(self._region(HighlightSeverity.AMBER, "dd"))
        assert driving_method_id(hl, AdvisoryStatus.GREEN) is None

    def test_none_highlights(self):
        assert driving_method_id(None, AdvisoryStatus.RED) is None

    def test_skips_unstamped_regions_of_same_severity(self):
        # Composite: a method-less tower and a method-bearing icing_band both RED.
        hl = self._hl(
            self._region(HighlightSeverity.RED, None, kind="tower"),
            self._region(HighlightSeverity.RED, "ogimet_nwp"),
        )
        assert driving_method_id(hl, AdvisoryStatus.RED) == "ogimet_nwp"

    def test_unstamped_only_returns_none(self):
        hl = self._hl(self._region(HighlightSeverity.AMBER, None, kind="cat_layer"))
        assert driving_method_id(hl, AdvisoryStatus.AMBER) is None


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
        ctx = _ctx([self._ovc_sounding() for _ in range(6)], cloud_method="square_nwp")
        result = VMCCruiseEvaluator.evaluate(ctx, _defaults(VMCCruiseEvaluator))
        assert result.aggregate_status == AdvisoryStatus.RED
        methods = _region_method_ids(result)
        assert methods and all(m == "dd" for m in methods)
        rep = next(m for m in result.per_model if m.model == result.representative_model)
        assert rep.primary_method_id == "dd"

    def test_cloud_top_badges_effective_method(self):
        # Reachable deck with tops above ceiling → amber blocking_deck.
        s = SoundingAnalysis(
            cloud_layers=[EnhancedCloudLayer(base_ft=7000, top_ft=17500, coverage=CloudCoverage.BKN)],
            nwp_cloud_layers=None,
        )
        ctx = _ctx([s for _ in range(6)], cloud_method="square_nwp")
        result = CloudTopEvaluator.evaluate(ctx, _defaults(CloudTopEvaluator))
        assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)
        assert _region_method_ids(result) and all(m == "dd" for m in _region_method_ids(result))


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

    def test_fiki_icing_badges_effective(self):
        ctx = _ctx([self._iced_no_escape() for _ in range(6)], icing_method="ogimet_nwp")
        result = FIKIIcingEvaluator.evaluate(ctx, _defaults(FIKIIcingEvaluator))
        assert _region_method_ids(result)
        assert all(m == "ogimet_nwp" for m in _region_method_ids(result))

    def test_ifr_feasibility_icing_regions_badge_effective(self):
        ctx = _ctx([self._iced_no_escape() for _ in range(6)], icing_method="ogimet_nwp")
        result = IFRFeasibilityEvaluator.evaluate(ctx, _defaults(IFRFeasibilityEvaluator))
        icing_methods = [
            r.method_id
            for m in result.per_model if m.highlights
            for r in m.highlights.regions if r.kind == "icing_band"
        ]
        assert icing_methods and all(m == "ogimet_nwp" for m in icing_methods)


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
