"""Altitude mitigation for embedded convection (#568 Fix 4).

EMBEDDED is RED because a deck hides the cells: no see-and-avoid, nothing to
circumnavigate around. A *different cruise level* is often exactly what restores
it — climb on top and the buildups penetrating the layer are visible; descend
below and you see the cells from underneath. Until now the card offered nothing.

What these tests pin:

* the tip is offered ONLY for EMBEDDED (altitude cannot fix horizontal extent);
* both directions are produced, and they read differently;
* ``mitigated_status`` is the band you would ACTUALLY get at that altitude —
  clearing a deck usually leaves SCATTERED/ISOLATED (AMBER), not GREEN — and
  never the advisory's own status;
* the tip never moves ``status`` (the ``Mitigation`` contract);
* the terrain floor and the flight ceiling bound the ladder;
* the aggregate only promotes an altitude that clears EVERY model currently
  grading EMBEDDED.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.convective_character import (
    CHARACTER_PARAM_DEFAULTS,
    ConvectiveCharacterEvaluator,
    classify_route_character,
    resolve_character_params,
)
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveCharacter,
    ConvectiveRisk,
    ElevationPoint,
    ElevationProfile,
    EnhancedCloudLayer,
    MitigationKind,
    NWPCloudDiagnostics,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses

_SPACING_NM = 20.0


def _defaults() -> dict:
    return {
        p.key: p.default
        for p in ConvectiveCharacterEvaluator.catalog_entry().parameters
    }


def _cell_under_deck(deck_base: float, deck_top: float) -> SoundingAnalysis:
    """A realized MODERATE cell with an OVC deck between ``deck_base`` and ``deck_top``.

    Bulk cover is published for every band so the deck corroborates wherever the
    candidate cruise lands inside it, and so the band-matched cover lookup is the
    thing under test rather than a missing-cover fallback.
    """
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, cape_jkg=1200,
            base_ft=6000, top_ft=28000, convective_precip_mm_h=1.5,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, cape_jkg=1200,
            base_ft=6000, top_ft=28000,
        ),
        cloud_layers=[
            EnhancedCloudLayer(
                base_ft=deck_base, top_ft=deck_top, coverage=CloudCoverage.OVC
            ),
        ],
        cloud_cover_low_pct=90.0,
        cloud_cover_mid_pct=90.0,
        cloud_cover_high_pct=90.0,
        nwp_cloud_diagnostics=NWPCloudDiagnostics(convective_precip_mm_h=1.5),
    )


def _cell_in_clear_air() -> SoundingAnalysis:
    """The same realized cell with no deck at all."""
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, cape_jkg=1200,
            base_ft=6000, top_ft=28000, convective_precip_mm_h=1.5,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, cape_jkg=1200,
            base_ft=6000, top_ft=28000,
        ),
        cloud_layers=[],
        nwp_cloud_diagnostics=NWPCloudDiagnostics(convective_precip_mm_h=1.5),
    )


def _quiet() -> SoundingAnalysis:
    return SoundingAnalysis(
        convective_nwp=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=None, base_ft=None, top_ft=None,
        ),
        convective_thermo=ConvectiveAssessment(
            risk_level=ConvectiveRisk.NONE, cape_jkg=10, base_ft=None, top_ft=None,
        ),
    )


def _elevation(max_ft: float, total_nm: float) -> ElevationProfile:
    return ElevationProfile(
        route_name="t",
        points=[ElevationPoint(distance_nm=0.0, lat=48.0, lon=2.0, elevation_ft=max_ft)],
        max_elevation_ft=max_ft,
        total_distance_nm=total_nm,
    )


def _ctx(
    per_model: dict[str, list[SoundingAnalysis]],
    *,
    cruise_ft: int,
    ceiling_ft: int,
    max_terrain_ft: float = 1000.0,
) -> RouteContext:
    models = list(per_model)
    n = len(next(iter(per_model.values())))
    total_nm = n * _SPACING_NM
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * _SPACING_NM,
            interpolated_time=datetime(2026, 3, 1, 10, 0),
            forecast_hour=datetime(2026, 3, 1, 9, 0), track_deg=135.0,
            sounding={m: per_model[m][i] for m in models},
        )
        for i in range(n)
    ]
    resolved = _resolve_analyses(
        analyses, icing_method=None, cloud_source=None, convective_method="nwp",
    )
    return RouteContext(
        analyses=resolved, cross_sections=[], elevation=_elevation(max_terrain_ft, total_nm),
        models=models, cruise_altitude_ft=cruise_ft, flight_ceiling_ft=ceiling_ft,
        total_distance_nm=total_nm,
    )


def _route(cells: list[SoundingAnalysis], n_total: int = 20) -> list[SoundingAnalysis]:
    """`cells` contiguous from the origin, quiet for the rest of the route.

    Four cells at 20 nm spacing span 70 nm under the midpoint-owned-cell
    convention — comfortably over the 50 nm ``embed_min_nm`` floor — while
    4/20 realized keeps the fall-through band at SCATTERED (AMBER), so a cleared
    deck has somewhere honest to land.
    """
    return cells + [_quiet()] * (n_total - len(cells))


def _result(ctx: RouteContext, model: str = "gfs"):
    res = ConvectiveCharacterEvaluator.evaluate(ctx, _defaults())
    return res, next(m for m in res.per_model if m.model == model)


# --- the tip itself ---------------------------------------------------------


def test_climb_clears_a_deck_the_route_cannot_descend_below():
    """Deck 5,000-10,000 ft, cruise 8,000, terrain floor 4,000 → the only out is up.

    Every candidate from the floor up to 11,000 ft is still inside the deck once
    the 1,000 ft buffer is applied, so the nearest clearing level is 11,500.
    """
    ctx = _ctx(
        {"gfs": _route([_cell_under_deck(5_000, 10_000)] * 4)},
        cruise_ft=8_000, ceiling_ft=14_000, max_terrain_ft=1_000.0,
    )
    assert classify_route_character(ctx, "gfs", resolve_character_params(ctx)) is (
        ConvectiveCharacter.EMBEDDED
    )

    _, gfs = _result(ctx)
    assert gfs.status == AdvisoryStatus.RED  # advice never moves the grade
    assert len(gfs.mitigations) == 1
    tip = gfs.mitigations[0]
    assert tip.kind == MitigationKind.ALTITUDE
    assert tip.addresses == "embedded_deck"
    assert tip.altitude_ft == 11_500
    assert "Climbing" in tip.detail
    # The band you would ACTUALLY get there: the cells are still realized over
    # 20 % of the route, so clearing the deck buys SCATTERED (AMBER), not GREEN.
    assert tip.mitigated_status == AdvisoryStatus.AMBER
    # v1 is level-altitude only — the contiguous-extent gate is route-level and
    # non-additive, so it cannot be a per-point cost in the profile solver.
    assert tip.profile is None


def test_descend_clears_a_deck_when_the_ceiling_blocks_the_climb():
    """Deck 9,000-14,000 ft, cruise 10,000, ceiling 12,000 → the only out is down."""
    ctx = _ctx(
        {"gfs": _route([_cell_under_deck(9_000, 14_000)] * 4)},
        cruise_ft=10_000, ceiling_ft=12_000, max_terrain_ft=1_000.0,
    )
    _, gfs = _result(ctx)
    assert len(gfs.mitigations) == 1
    tip = gfs.mitigations[0]
    assert tip.altitude_ft == 7_500  # 8,000 is still inside the buffered deck
    assert "Descending" in tip.detail


def test_no_tip_when_no_altitude_clears_the_deck():
    """A deck spanning the whole usable band leaves nothing to offer."""
    ctx = _ctx(
        {"gfs": _route([_cell_under_deck(2_000, 20_000)] * 4)},
        cruise_ft=10_000, ceiling_ft=12_000, max_terrain_ft=1_000.0,
    )
    _, gfs = _result(ctx)
    assert gfs.status == AdvisoryStatus.RED
    assert gfs.mitigations == []


def test_terrain_floor_bounds_the_ladder():
    """Raising the terrain floor above the only clearing level withdraws the tip.

    Deck 9,000-14,000 with a 12,000 ft ceiling clears only by descending to
    7,500 ft; terrain at 6,000 ft plus the 3,000 ft AGL floor puts that level out
    of reach, and the advisory must then offer nothing rather than a level it
    would not fly.
    """
    ctx = _ctx(
        {"gfs": _route([_cell_under_deck(9_000, 14_000)] * 4)},
        cruise_ft=10_000, ceiling_ft=12_000, max_terrain_ft=6_000.0,
    )
    _, gfs = _result(ctx)
    assert gfs.mitigations == []


def test_mitigation_floor_is_this_advisory_s_own_parameter():
    """``mitigation_min_base_agl_ft`` is tunable here, not read from vfr_feasibility."""
    soundings = {"gfs": _route([_cell_under_deck(9_000, 14_000)] * 4)}
    ctx = _ctx(soundings, cruise_ft=10_000, ceiling_ft=12_000, max_terrain_ft=6_000.0)

    blocked = {**_defaults()}
    assert blocked["mitigation_min_base_agl_ft"] == 3_000
    res = ConvectiveCharacterEvaluator.evaluate(ctx, blocked)
    assert next(m for m in res.per_model if m.model == "gfs").mitigations == []

    lowered = {**blocked, "mitigation_min_base_agl_ft": 1_000}
    res = ConvectiveCharacterEvaluator.evaluate(ctx, lowered)
    assert next(m for m in res.per_model if m.model == "gfs").mitigations[0].altitude_ft == 7_500


# --- scoping ----------------------------------------------------------------


def test_no_tip_for_a_non_embedded_band():
    """Altitude cannot fix horizontal extent — offered for EMBEDDED only."""
    ctx = _ctx(
        {"gfs": [_cell_in_clear_air()] * 16 + [_quiet()] * 4},
        cruise_ft=10_000, ceiling_ft=16_000,
    )
    res, gfs = _result(ctx)
    assert classify_route_character(ctx, "gfs", resolve_character_params(ctx)) is not (
        ConvectiveCharacter.EMBEDDED
    )
    assert gfs.status == AdvisoryStatus.RED  # widespread — genuinely unavoidable
    assert gfs.mitigations == []
    assert res.aggregate_mitigations == []


# --- the aggregate ----------------------------------------------------------


def test_aggregate_promotes_only_an_altitude_that_clears_every_embedded_model():
    """Two models, two different decks: only a level clearing BOTH is promoted.

    gfs is embedded in a 5,000-10,000 deck and clears at 11,500; ecmwf is
    embedded in a 5,000-16,000 deck and clears only at 17,500. The per-model tips
    differ, and the aggregate must carry the one that works for both rather than
    the representative model's — advice that helps one model and not another is
    worse than none.
    """
    ctx = _ctx(
        {
            "gfs": _route([_cell_under_deck(5_000, 10_000)] * 4),
            "ecmwf": _route([_cell_under_deck(5_000, 16_000)] * 4),
        },
        cruise_ft=8_000, ceiling_ft=20_000, max_terrain_ft=1_000.0,
    )
    res, gfs = _result(ctx)
    ecmwf = next(m for m in res.per_model if m.model == "ecmwf")
    assert gfs.mitigations[0].altitude_ft == 11_500
    assert ecmwf.mitigations[0].altitude_ft == 17_500

    assert res.aggregate_status == AdvisoryStatus.RED
    assert len(res.aggregate_mitigations) == 1
    assert res.aggregate_mitigations[0].altitude_ft == 17_500


def test_aggregate_is_empty_when_no_common_altitude_exists():
    """One model has an out, the other has none → nothing to promote."""
    ctx = _ctx(
        {
            "gfs": _route([_cell_under_deck(5_000, 10_000)] * 4),
            "ecmwf": _route([_cell_under_deck(2_000, 20_000)] * 4),
        },
        cruise_ft=8_000, ceiling_ft=14_000, max_terrain_ft=1_000.0,
    )
    res, gfs = _result(ctx)
    assert gfs.mitigations[0].altitude_ft == 11_500
    assert res.aggregate_mitigations == []


def test_a_model_that_is_not_embedded_does_not_constrain_the_aggregate():
    """Only models *currently grading EMBEDDED* participate in the intersection."""
    ctx = _ctx(
        {
            "gfs": _route([_cell_under_deck(5_000, 10_000)] * 4),
            "ecmwf": _route([_cell_in_clear_air()] * 4),
        },
        cruise_ft=8_000, ceiling_ft=14_000, max_terrain_ft=1_000.0,
    )
    res, _ = _result(ctx)
    assert len(res.aggregate_mitigations) == 1
    assert res.aggregate_mitigations[0].altitude_ft == 11_500


# --- the parameters are user-tunable, like the other six --------------------


def test_new_parameters_are_in_the_catalog_and_the_defaults():
    keys = {p.key for p in ConvectiveCharacterEvaluator.catalog_entry().parameters}
    for key in (
        "embed_min_nm", "embed_cruise_buffer_ft",
        "embed_deck_cover_pct", "mitigation_min_base_agl_ft",
    ):
        assert key in keys, key
        assert key in CHARACTER_PARAM_DEFAULTS, key


def test_embed_min_nm_override_reaches_the_classifier():
    """A pilot raising the floor above the run's extent drops the EMBEDDED verdict."""
    ctx = _ctx(
        {"gfs": _route([_cell_under_deck(5_000, 10_000)] * 4)},
        cruise_ft=8_000, ceiling_ft=14_000,
    )
    params = resolve_character_params(ctx)
    assert classify_route_character(ctx, "gfs", params) is ConvectiveCharacter.EMBEDDED
    raised = {**params, "embed_min_nm": 100}
    assert classify_route_character(ctx, "gfs", raised) is not (
        ConvectiveCharacter.EMBEDDED
    )
