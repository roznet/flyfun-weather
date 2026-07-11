"""Tests for the experimental front advisory evaluator (#196)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.fronts import FRONTS_ADVISORY_ID, FrontsEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    FrontCrossingModel,
    FrontProximityModel,
    RouteFrontAnalysisModel,
    RouteFrontsManifest,
)

# Default parameter set (matches the catalog default).
_PARAMS = {"closing_within_km": 300}


def _crossing(
    *,
    distance_km: float = 200.0,
    kind: str = "cold",
    intensity: str = "classical",
    advection: float = 0.0,
    gradient: float = 9.0,
    co_location: str | None = None,
    weather_top_ft: float | None = None,
    persistence: float | None = None,
    vertical_levels: int | None = None,
) -> FrontCrossingModel:
    return FrontCrossingModel(
        lat=48.0, lon=2.0, distance_km=distance_km,
        gradient=gradient, neg_laplacian=1.0, advection=advection,
        tfp_before=0.5, tfp_after=-0.5, delta_theta_e=8.0,
        kind=kind, intensity=intensity,
        co_location=co_location, weather_top_ft=weather_top_ft, persistence=persistence,
        vertical_levels=vertical_levels,
    )


def _manifest(
    *,
    crossings: list[FrontCrossingModel] | None = None,
    nearest: FrontProximityModel | None = None,
    model: str = "gfs",
    primary_level: int = 850,
) -> RouteFrontsManifest:
    analysis = RouteFrontAnalysisModel(
        model=model, level_hPa=primary_level, hour=12.0,
        crossings=crossings or [], nearest=nearest,
    )
    return RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=primary_level,
        levels=[primary_level],
        models=[model],
        per_model={model: [analysis]},
    )


def _ctx(
    manifest: RouteFrontsManifest | None,
    models: list[str] | None = None,
) -> RouteContext:
    return RouteContext(
        analyses=[], cross_sections=[], elevation=None,
        models=models or ["gfs"], cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=200.0,  # ~370 km route
        route_fronts=manifest,
    )


def test_no_artifact_is_unavailable():
    """No route_fronts → experimental feature off → UNAVAILABLE."""
    result = FrontsEvaluator.evaluate(_ctx(None, ["gfs", "ecmwf"]), _PARAMS)
    assert result.advisory_id == FRONTS_ADVISORY_ID
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert result.representative_model == "gfs"
    assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
    assert all(model.status == AdvisoryStatus.UNAVAILABLE for model in result.per_model)
    assert all(model.data_state == "unavailable" for model in result.per_model)


def test_empty_per_model_is_unavailable():
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=850, levels=[850], models=[], per_model={},
    )
    result = FrontsEvaluator.evaluate(_ctx(manifest, ["gfs", "ecmwf"]), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
    assert all(model.data_state == "unavailable" for model in result.per_model)


def test_sharp_crossing_is_red():
    manifest = _manifest(crossings=[_crossing(intensity="sharp", gradient=14.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED
    assert "sharp" in result.aggregate_detail.lower()
    assert result.per_model[0].data_state == "complete"
    assert result.per_model[0].primary_method_id == "hewson"
    assert result.per_model[0].evidence_regions == []


def test_missing_requested_model_analysis_is_explicitly_unavailable():
    manifest = _manifest(crossings=[_crossing(intensity="sharp", gradient=14.0)])

    result = FrontsEvaluator.evaluate(
        _ctx(manifest, ["gfs", "ecmwf"]),
        _PARAMS,
    )

    assert [model.model for model in result.per_model] == ["gfs", "ecmwf"]
    by_model = {model.model: model for model in result.per_model}
    assert by_model["gfs"].status == AdvisoryStatus.RED
    assert by_model["gfs"].data_state == "complete"
    assert by_model["gfs"].primary_method_id == "hewson"
    assert by_model["gfs"].evidence_regions == []
    assert by_model["ecmwf"].status == AdvisoryStatus.UNAVAILABLE
    assert by_model["ecmwf"].data_state == "unavailable"


def test_classical_crossing_is_amber():
    manifest = _manifest(crossings=[_crossing(intensity="classical")])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_significant_crossing_is_amber():
    manifest = _manifest(crossings=[_crossing(intensity="significant", gradient=6.5)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_warm_advection_adds_deteriorating_tail():
    manifest = _manifest(crossings=[_crossing(kind="warm", advection=2.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert "deteriorating" in result.aggregate_detail.lower()


def test_cold_advection_adds_improving_tail():
    manifest = _manifest(crossings=[_crossing(kind="cold", advection=-2.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert "improving" in result.aggregate_detail.lower()


def test_closing_offtrack_front_is_amber():
    nearest = FrontProximityModel(
        distance_km=120.0, lat=49.0, lon=3.0, gradient=7.0, delta_theta_e=6.0,
        on_track=False, trend="closing", closing_km_per_h=8.0,
    )
    manifest = _manifest(crossings=[], nearest=nearest)
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_receding_offtrack_front_is_green():
    nearest = FrontProximityModel(
        distance_km=120.0, lat=49.0, lon=3.0, gradient=7.0, delta_theta_e=6.0,
        on_track=False, trend="receding", closing_km_per_h=-5.0,
    )
    manifest = _manifest(crossings=[], nearest=nearest)
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_distant_closing_front_beyond_threshold_is_green():
    nearest = FrontProximityModel(
        distance_km=500.0, lat=49.0, lon=3.0, gradient=7.0, delta_theta_e=6.0,
        on_track=False, trend="closing", closing_km_per_h=8.0,
    )
    manifest = _manifest(crossings=[], nearest=nearest)
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_multiple_crossings_picks_worst_and_counts():
    manifest = _manifest(crossings=[
        _crossing(distance_km=50.0, intensity="significant", gradient=6.5),
        _crossing(distance_km=300.0, intensity="sharp", gradient=13.0),
    ])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED  # sharp wins
    assert "2" in result.aggregate_detail  # count surfaced


def test_grades_fronts_at_non_primary_level():
    """A front at a NON-PRIMARY level still grades — the Dijon false-GREEN fix.
    Here the primary 850 hPa is empty and the sharp crossing is at 700 hPa
    (~10,000 ft, ABOVE 850); it must still RED. The below-cruise case (a 925 hPa
    front overflown) is covered by test_sharp_front_below_flight_capped_to_amber."""
    a700 = RouteFrontAnalysisModel(
        model="gfs", level_hPa=700, hour=12.0,
        crossings=[_crossing(intensity="sharp", gradient=14.0)],
    )
    a850 = RouteFrontAnalysisModel(
        model="gfs", level_hPa=850, hour=12.0, crossings=[],  # primary, no fronts
    )
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=850, levels=[700, 850], models=["gfs"],
        per_model={"gfs": [a700, a850]},
    )
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED


# --- Relevance gating: co-location + persistence + flight-band (the Alpine
# false-RED / Dijon false-GREEN fixes). cruise=8000 ft, buffer=2000 → weather
# is "relevant" when its top reaches >= 6000 ft.

def test_dry_boundary_demoted_to_green():
    """A sharp but DRY boundary (clear air) is a wind-shift only → GREEN
    (the Alpine orographic false-RED fix)."""
    manifest = _manifest(crossings=[_crossing(
        intensity="sharp", gradient=18.0, co_location="dry", weather_top_ft=None,
    )])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN
    assert "boundary" in result.aggregate_detail.lower()


def test_flickering_crossing_demoted_to_green():
    """Low persistence → likely artifact → GREEN even if wet+sharp."""
    manifest = _manifest(crossings=[_crossing(
        intensity="sharp", gradient=18.0, co_location="wet",
        weather_top_ft=30000.0, persistence=0.2,
    )])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_weather_below_flight_is_green():
    """Wet boundary but its cloud tops out below cruise → overflown → GREEN."""
    manifest = _manifest(crossings=[_crossing(
        intensity="sharp", co_location="wet", weather_top_ft=3000.0, persistence=0.8,
    )])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_convective_reaching_flight_is_red_with_tail():
    """Convective tops through/above the flight band → RED + towers tail."""
    manifest = _manifest(crossings=[_crossing(
        kind="warm", intensity="classical", co_location="convective",
        weather_top_ft=33000.0, persistence=0.8,
    )])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED
    assert "fl330" in result.aggregate_detail.lower()


def test_sharp_front_below_flight_capped_to_amber():
    """A sharp wet front whose core sits BELOW cruise is overflown → AMBER, not
    RED (the Dijon FL100-over-a-925-hPa-front fix). Flight high (primary 700),
    sharp crossing at 925."""
    a925 = RouteFrontAnalysisModel(
        model="gfs", level_hPa=925, hour=12.0,
        crossings=[_crossing(intensity="sharp", gradient=14.0, co_location="wet",
                             weather_top_ft=30000.0, persistence=0.8)],
    )
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=700, levels=[700, 925], models=["gfs"],
        per_model={"gfs": [a925]},
    )
    ctx = RouteContext(
        analyses=[], cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=10000, flight_ceiling_ft=18000, total_distance_nm=200.0,
        route_fronts=manifest,
    )
    assert FrontsEvaluator.evaluate(ctx, _PARAMS).aggregate_status == AdvisoryStatus.AMBER


def test_shallow_convective_is_amber_deep_is_red():
    """Convective tops just above cruise → AMBER (isolated build-ups); deep
    towers → RED. cruise=8000, convective_deep_ft=15000 → RED at >= FL230."""
    shallow = _manifest(crossings=[_crossing(
        kind="warm", co_location="convective", weather_top_ft=18000.0, persistence=0.8,
    )])
    assert FrontsEvaluator.evaluate(_ctx(shallow), _PARAMS).aggregate_status == AdvisoryStatus.AMBER
    deep = _manifest(crossings=[_crossing(
        kind="warm", co_location="convective", weather_top_ft=33000.0, persistence=0.8,
    )])
    assert FrontsEvaluator.evaluate(_ctx(deep), _PARAMS).aggregate_status == AdvisoryStatus.RED


def test_wet_sharp_reaching_is_red_classical_is_amber():
    red = _manifest(crossings=[_crossing(
        intensity="sharp", co_location="wet", weather_top_ft=20000.0, persistence=0.8,
    )])
    assert FrontsEvaluator.evaluate(_ctx(red), _PARAMS).aggregate_status == AdvisoryStatus.RED
    amber = _manifest(crossings=[_crossing(
        intensity="classical", co_location="wet", weather_top_ft=20000.0, persistence=0.8,
    )])
    assert FrontsEvaluator.evaluate(_ctx(amber), _PARAMS).aggregate_status == AdvisoryStatus.AMBER


def test_single_level_wet_sharp_capped_to_amber():
    """Vertical coherence: a sharp wet front seen on ONE level only is shallow →
    AMBER, not RED. The same crossing on ≥2 levels stays RED."""
    shallow = _manifest(crossings=[_crossing(
        intensity="sharp", co_location="wet", weather_top_ft=20000.0,
        persistence=0.8, vertical_levels=1,
    )])
    assert FrontsEvaluator.evaluate(_ctx(shallow), _PARAMS).aggregate_status == AdvisoryStatus.AMBER
    coherent = _manifest(crossings=[_crossing(
        intensity="sharp", co_location="wet", weather_top_ft=20000.0,
        persistence=0.8, vertical_levels=2,
    )])
    assert FrontsEvaluator.evaluate(_ctx(coherent), _PARAMS).aggregate_status == AdvisoryStatus.RED


def test_single_level_convective_still_red_when_deep():
    """A deep convective tower at/above the flight's free-atmosphere (primary)
    level still REDs on a single θe level — graded by depth, not level count.
    (The overflown below-cruise case is held to coherence: see
    test_overflown_single_level_convective_capped_to_amber.)"""
    deep = _manifest(crossings=[_crossing(
        kind="warm", co_location="convective", weather_top_ft=33000.0,
        persistence=0.8, vertical_levels=1,
    )])
    assert FrontsEvaluator.evaluate(_ctx(deep), _PARAMS).aggregate_status == AdvisoryStatus.RED


def _lsgs_ctx(*, vertical_levels: int) -> RouteContext:
    """LSGS 2026-06-07 shape: convective θe crossing only at 925 hPa (below the
    700 hPa free-atmosphere primary, which is empty), cruise FL120, weather_top
    FL272 (the parcel EL). ``vertical_levels`` flips coherence."""
    a925 = RouteFrontAnalysisModel(
        model="gfs", level_hPa=925, hour=55.5,
        crossings=[_crossing(
            kind="quasi-stationary", co_location="convective",
            weather_top_ft=27233.0, persistence=0.6, vertical_levels=vertical_levels,
        )],
    )
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 6, 6, tzinfo=timezone.utc),
        primary_level_hPa=700, levels=[700, 925], models=["gfs"],
        # per_model_primary_hPa left empty → falls back to primary_level_hPa=700,
        # the correct primary for this single-model case (cf.
        # test_per_model_primary_level_not_flattened, which exercises the map).
        per_model={"gfs": [a925]},
    )
    return RouteContext(
        analyses=[], cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=12000, flight_ceiling_ft=18000, total_distance_nm=486.0,
        route_fronts=manifest,
    )


def test_overflown_single_level_convective_capped_to_amber():
    """LSGS 2026-06-07 regression (#216): a convective θe crossing seen ONLY on a
    single below-cruise level (925 hPa) over Alpine terrain must NOT RED on its
    parcel EL alone — overflown convection needs vertical coherence. Caps AMBER."""
    result = FrontsEvaluator.evaluate(_lsgs_ctx(vertical_levels=1), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_overflown_convective_reds_when_vertically_coherent():
    """The same overflown 925 hPa convective crossing, but seen on ≥2 levels, is a
    real sloping boundary → RED restored (coherence gate, not a blanket cap).
    weather_top 27233 sits 233 ft above the deep cutoff (cruise 12000 +
    convective_deep 15000 = 27000) — real LSGS data, an intentionally tight
    margin; tuning convective_deep_ft would flip this case."""
    result = FrontsEvaluator.evaluate(_lsgs_ctx(vertical_levels=2), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED


def test_unknown_coherence_treated_as_coherent():
    """vertical_levels None (older artifact) must not suppress — stays RED."""
    m = _manifest(crossings=[_crossing(
        intensity="sharp", co_location="wet", weather_top_ft=20000.0,
        persistence=0.8, vertical_levels=None,
    )])
    assert FrontsEvaluator.evaluate(_ctx(m), _PARAMS).aggregate_status == AdvisoryStatus.RED


def test_default_disabled_in_catalog():
    """Front advisory must not run by default — gated by artifact presence."""
    assert FrontsEvaluator.catalog_entry().default_enabled is False


def test_per_model_primary_level_not_flattened():
    """A model's own cruise-level crossing must grade against *its* primary (#203).

    GFS exposes only 850 hPa (its nearest-cruise level) while the manifest-wide
    ``primary_level_hPa`` reflects another model's 700. A sharp, coherent wet GFS
    crossing at 850 reaches the flight: graded against 850 it is at-cruise → RED;
    graded against the wrong manifest-wide 700 it would read as overflown →
    capped AMBER. ``per_model_primary_hPa`` keeps it RED.
    """
    xing = _crossing(
        kind="cold", intensity="sharp", co_location="wet",
        weather_top_ft=None, persistence=1.0, vertical_levels=2,
    )
    analysis = RouteFrontAnalysisModel(
        model="gfs", level_hPa=850, hour=12.0, crossings=[xing],
    )
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=700,                       # another model's level
        per_model_primary_hPa={"gfs": 850},          # gfs's own nearest-cruise
        levels=[700, 850],
        models=["gfs"],
        per_model={"gfs": [analysis]},
    )
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED

    # Without the per-model map (pre-#203 pack), the same crossing falls back to
    # the manifest-wide 700 → 850 reads as overflown → capped AMBER. This is the
    # latent mis-grade the field fixes.
    manifest_old = manifest.model_copy(update={"per_model_primary_hPa": {}})
    result_old = FrontsEvaluator.evaluate(_ctx(manifest_old), _PARAMS)
    assert result_old.aggregate_status == AdvisoryStatus.AMBER
