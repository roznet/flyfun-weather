"""``reason_code``: why a region fired, when the shape and the colour can't say.

The field earns its place only where an advisory emits regions that share a
``kind`` AND a ``severity`` yet mean different things to a pilot. Where the
distinction is already carried by the geometry class or the colour, the reason is
just the advisory's definition and is deliberately left ``None`` — see
``HighlightRegion``.

So every test here asserts the discrimination *within* one severity band. A test
that merely showed "RED has a reason and GREEN does not" would prove nothing.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.tasks.advise import _resolve_analyses
from weatherbrief.models import (
    AdvisoryStatus,
    ConvectiveAssessment,
    ConvectiveRisk,
    HighlightSeverity,
    IcingRisk,
    IcingType,
    IcingZone,
    RoutePointAnalysis,
    SoundingAnalysis,
)


def _defaults(evaluator) -> dict:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _ctx(
    soundings: list[SoundingAnalysis],
    *,
    cruise_ft: int = 8000,
    icing_method: str | None = None,
    convective_method: str | None = None,
) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={"gfs": s},
        )
        for i, s in enumerate(soundings)
    ]
    # Go through the real resolver so the fixture exercises the same active-track
    # / active-zone selection the pipeline does. Note ``icing_method=None`` now
    # resolves to the *default* (ogimet_nwp, #403), which swaps ``icing_zones``
    # for the Ogimet-NWP list — so an icing test that hand-sets ``icing_zones``
    # must ask for "ogimet_dd", the no-swap path.
    resolved = _resolve_analyses(
        analyses, icing_method=icing_method, cloud_source=None,
        convective_method=convective_method,
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=cruise_ft, flight_ceiling_ft=18000, total_distance_nm=180,
    )


def _reasons(result, severity: HighlightSeverity) -> set[str]:
    out = set()
    for pm in result.per_model:
        if not pm.highlights:
            continue
        for r in pm.highlights.regions:
            if r.severity == severity and r.reason_code:
                out.add(r.reason_code)
    return out


def _zone(base, top, risk=IcingRisk.MODERATE, *, sld=False) -> IcingZone:
    return IcingZone(
        base_ft=base, top_ft=top, risk=risk, icing_type=IcingType.RIME,
        sld_risk=sld,
    )


class TestFIKIReasonCodes:
    """A RED FIKI band means three different things. The colour tells you none."""

    def test_sld_and_thick_transit_are_both_red_but_distinguishable(self):
        # SLD in the transit column: the FIKI certification does not cover it.
        sld_pt = SoundingAnalysis(icing_zones=[_zone(2000, 5000, sld=True)])
        # A merely THICK transit layer: same RED, but the aircraft can transit it.
        thick_pt = SoundingAnalysis(icing_zones=[_zone(1000, 7500)])

        sld_res = FIKIIcingEvaluator.evaluate(_ctx([sld_pt] * 6, icing_method="ogimet_dd"), _defaults(FIKIIcingEvaluator))
        thick_res = FIKIIcingEvaluator.evaluate(_ctx([thick_pt] * 6, icing_method="ogimet_dd"), _defaults(FIKIIcingEvaluator))

        assert sld_res.aggregate_status == AdvisoryStatus.RED
        assert thick_res.aggregate_status == AdvisoryStatus.RED
        # Same grade, same kind, same colour — only reason_code separates them.
        assert "sld" in _reasons(sld_res, HighlightSeverity.RED)
        assert "thick_transit" in _reasons(thick_res, HighlightSeverity.RED)

    def test_severe_icing_is_named_distinctly_from_thickness(self):
        severe = SoundingAnalysis(icing_zones=[_zone(2000, 4000, risk=IcingRisk.SEVERE)])
        res = FIKIIcingEvaluator.evaluate(_ctx([severe] * 6, icing_method="ogimet_dd"), _defaults(FIKIIcingEvaluator))
        assert "severe_icing" in _reasons(res, HighlightSeverity.RED)

    def test_amber_splits_loiter_from_transit(self):
        """The advisory's thesis: FIKI can transit icing, but not loiter in it.

        Ice sitting at the cruise level is a different problem from ice on the
        climb, and both grade AMBER.
        """
        # Icing sitting AT cruise (8000 ft) → you would loiter in it.
        at_cruise = SoundingAnalysis(icing_zones=[_zone(7500, 8500)])
        res = FIKIIcingEvaluator.evaluate(_ctx([at_cruise] * 6, icing_method="ogimet_dd"), _defaults(FIKIIcingEvaluator))
        assert "icing_at_cruise" in _reasons(res, HighlightSeverity.AMBER)


class TestConvectiveReasonCodes:
    """Was the model's own scheme alarmed [active_track], or quiet with loaded
    thermodynamics raising it to amber [dd_trigger, #442]?"""

    @staticmethod
    def _sounding(nwp_risk, thermo_risk) -> SoundingAnalysis:
        return SoundingAnalysis(
            convective_nwp=ConvectiveAssessment(
                risk_level=nwp_risk, cape_jkg=2500, base_ft=3000, top_ft=30000,
            ),
            convective_thermo=ConvectiveAssessment(
                risk_level=thermo_risk, cape_jkg=2500, base_ft=3000, top_ft=30000,
            ),
        )

    @staticmethod
    def _quiet_nwp_sounding(thermo_risk) -> SoundingAnalysis:
        # NWP genuinely quiet (green, no tower / precip / cover) so the cross-check
        # reads ``dd_not_corroborated``; DD loaded above it.
        return SoundingAnalysis(
            convective_nwp=ConvectiveAssessment(
                risk_level=ConvectiveRisk.NONE, cape_jkg=None,
                base_ft=None, top_ft=None,
            ),
            convective_thermo=ConvectiveAssessment(
                risk_level=thermo_risk, cape_jkg=2500, base_ft=3000, top_ft=30000,
            ),
        )

    def test_active_track_saw_it(self):
        ctx = _ctx([self._sounding(ConvectiveRisk.HIGH, ConvectiveRisk.LOW)] * 6, convective_method="nwp")
        res = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
        reasons = _reasons(res, HighlightSeverity.RED) | _reasons(res, HighlightSeverity.AMBER)
        assert reasons == {"active_track"}

    def test_low_nwp_not_floored_up_by_dd(self):
        """#442: DD no longer floors the colour. A LOW NWP under a HIGH DD tower
        grades on the NWP itself — amber, reason ``active_track``, NOT red and NOT
        ``thermo_floor``. The DD divergence rides the cross-check note instead."""
        ctx = _ctx([self._sounding(ConvectiveRisk.LOW, ConvectiveRisk.HIGH)] * 6, convective_method="nwp")
        res = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
        assert res.aggregate_status == AdvisoryStatus.AMBER
        reasons = _reasons(res, HighlightSeverity.RED) | _reasons(res, HighlightSeverity.AMBER)
        assert reasons == {"active_track"}

    def test_dd_trigger_amber_when_nwp_green(self):
        """#442: NWP green + uncorroborated DD MODERATE+ → AMBER (never red),
        reason ``dd_trigger``, with the cross-check note surfaced. DD alone can
        raise green→amber; it can never produce a red."""
        ctx = _ctx([self._quiet_nwp_sounding(ConvectiveRisk.HIGH)] * 6, convective_method="nwp")
        res = ConvectiveEvaluator.evaluate(ctx, _defaults(ConvectiveEvaluator))
        assert res.aggregate_status == AdvisoryStatus.AMBER
        assert _reasons(res, HighlightSeverity.RED) == set()   # DD never reds
        assert _reasons(res, HighlightSeverity.AMBER) == {"dd_trigger"}
        # The divergence carries a note, not just a colour.
        assert any(pm.cross_check for pm in res.per_model)
        # And the method is NOT compounded — that decision stands.
        rep = next(m for m in res.per_model if m.model == res.representative_model)
        assert rep.primary_method_id == "nwp"
