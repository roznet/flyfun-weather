"""Tests for the mountain wind advisory's wave-signature corroboration."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.mountain_wind import MountainWindEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    ElevationPoint,
    ElevationProfile,
    HourlyForecast,
    InversionLayer,
    ModelSource,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
    VerticalMotionAssessment,
    VerticalMotionClass,
    Waypoint,
    WaypointForecast,
)

_T0 = datetime(2026, 3, 1, 12, 0)
_N = 10
_TOTAL_NM = 200.0


def _mountain_elevation() -> ElevationProfile:
    """Flat 500ft profile with a 5000ft plateau mid-route (points 4-6)."""
    points = []
    for i in range(_N):
        d = i * _TOTAL_NM / (_N - 1)
        elev = 5000.0 if 4 <= i <= 6 else 500.0
        points.append(ElevationPoint(distance_nm=d, elevation_ft=elev, lat=46.0, lon=6.0 + i * 0.2))
    return ElevationProfile(
        route_name="test", points=points,
        max_elevation_ft=5000, total_distance_nm=_TOTAL_NM,
    )


def _cross_section(
    wind_speed_kt: float,
    missing_wind_indices: set[int] | None = None,
) -> RouteCrossSection:
    """One-model cross-section with uniform wind at a single 800hPa level."""
    missing_wind_indices = missing_wind_indices or set()
    route_points = [
        RoutePoint(lat=46.0, lon=6.0 + i * 0.2, distance_from_origin_nm=i * _TOTAL_NM / (_N - 1))
        for i in range(_N)
    ]
    forecasts = [
        WaypointForecast(
            waypoint=Waypoint(icao=f"P{i}", name=f"P{i}", lat=46.0, lon=6.0 + i * 0.2),
            model=ModelSource.GFS,
            fetched_at=_T0,
            hourly=[HourlyForecast(
                time=_T0,
                pressure_levels=(
                    []
                    if i in missing_wind_indices
                    else [PressureLevelData(
                        pressure_hpa=800,
                        wind_speed_kt=wind_speed_kt,
                        wind_direction_deg=270.0,
                    )]
                ),
            )],
        )
        for i in range(_N)
    ]
    return RouteCrossSection(
        model=ModelSource.GFS, route_points=route_points,
        fetched_at=_T0, point_forecasts=forecasts,
    )


def _sounding(
    ridge_inversion: bool = False,
    oscillating: bool = False,
) -> SoundingAnalysis:
    inversions = (
        [InversionLayer(base_ft=5500, top_ft=6500, strength_c=3.0)]
        if ridge_inversion else []
    )
    vm = VerticalMotionAssessment(
        classification=(
            VerticalMotionClass.OSCILLATING if oscillating
            else VerticalMotionClass.QUIESCENT
        ),
    )
    return SoundingAnalysis(
        indices=ThermodynamicIndices(),
        inversion_layers=inversions,
        vertical_motion=vm,
    )


def _ctx(
    wind_speed_kt: float,
    ridge_inversion: bool = False,
    oscillating: bool = False,
    missing_wind_indices: set[int] | None = None,
) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i,
            lat=46.0,
            lon=6.0 + i * 0.2,
            distance_from_origin_nm=i * _TOTAL_NM / (_N - 1),
            interpolated_time=_T0,
            forecast_hour=_T0,
            track_deg=90.0,
            sounding={"gfs": _sounding(ridge_inversion, oscillating)},
        )
        for i in range(_N)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[_cross_section(wind_speed_kt, missing_wind_indices)],
        elevation=_mountain_elevation(),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=_TOTAL_NM,
    )


def _evaluate(ctx: RouteContext, params: dict | None = None):
    entry = MountainWindEvaluator.catalog_entry()
    defaults = {p.key: p.default for p in entry.parameters}
    return MountainWindEvaluator.evaluate(ctx, {**defaults, **(params or {})})


def _with_point_pressure_levels(
    ctx: RouteContext,
    levels_by_point: dict[int, list[PressureLevelData]],
) -> RouteContext:
    cross_section = ctx.cross_sections[0]
    forecasts = []
    for point_index, forecast in enumerate(cross_section.point_forecasts):
        hourly = forecast.hourly[0]
        levels = levels_by_point.get(point_index, hourly.pressure_levels)
        forecasts.append(
            forecast.model_copy(
                update={
                    "hourly": [hourly.model_copy(update={"pressure_levels": levels})]
                }
            )
        )
    return replace(
        ctx,
        cross_sections=[
            cross_section.model_copy(update={"point_forecasts": forecasts})
        ],
    )


def _with_ridge_inversions(
    ctx: RouteContext,
    point_indices: set[int],
) -> RouteContext:
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(
            update={
                "inversion_layers": (
                    [InversionLayer(base_ft=5500, top_ft=6500, strength_c=3.0)]
                    if rpa.point_index in point_indices
                    else []
                )
            }
        )
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    return replace(ctx, analyses=analyses)


class TestMountainWindWaveCorroboration:

    def test_light_wind_green(self):
        result = _evaluate(_ctx(10.0))
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_strong_wind_no_signature_amber(self):
        # 32kt is above the corroborated red bar (30) but with no wave
        # signature it stays AMBER until the plain red bar (40).
        result = _evaluate(_ctx(32.0))
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert result.per_model[0].primary_method_id == "terrain_wind"

    def test_strong_wind_with_ridge_inversion_red(self):
        result = _evaluate(_ctx(32.0, ridge_inversion=True))
        assert result.aggregate_status == AdvisoryStatus.RED
        assert result.per_model[0].primary_method_id == "terrain_wind_wave"
        assert "stable layer" in result.per_model[0].detail

    def test_strong_wind_with_oscillating_motion_red(self):
        result = _evaluate(_ctx(32.0, oscillating=True))
        assert result.aggregate_status == AdvisoryStatus.RED
        assert "wave-like" in result.per_model[0].detail

    def test_moderate_wind_with_signature_stays_amber(self):
        # 25kt + signature: below the corroborated red bar — wave possible
        # but flow too weak for an automatic RED.
        result = _evaluate(_ctx(25.0, ridge_inversion=True))
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_very_strong_wind_red_regardless(self):
        result = _evaluate(_ctx(45.0))
        assert result.aggregate_status == AdvisoryStatus.RED

    def test_very_strong_wind_with_signature_appends_suffix(self):
        # max_wind >= wind_red (40) AND a wave signature present: the severe
        # branch wins (RED) and the signature suffix is appended to the detail.
        result = _evaluate(_ctx(45.0, ridge_inversion=True))
        assert result.aggregate_status == AdvisoryStatus.RED
        assert result.per_model[0].primary_method_id == "terrain_wind"
        assert "stable layer" in result.per_model[0].detail

    def test_no_mountains_green(self):
        ctx = _ctx(45.0)
        flat = ElevationProfile(
            route_name="test",
            points=[ElevationPoint(distance_nm=p.distance_nm, elevation_ft=500, lat=p.lat, lon=p.lon)
                    for p in ctx.elevation.points],
            max_elevation_ft=500, total_distance_nm=_TOTAL_NM,
        )
        result = _evaluate(replace(ctx, elevation=flat))
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_corroborated_threshold_tunable(self):
        result = _evaluate(
            _ctx(32.0, ridge_inversion=True),
            params={"corroborated_red_kt": 35},
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER


def test_mountain_wind_evidence_is_route_only():
    result = _evaluate(_ctx(32.0, ridge_inversion=True))
    regions = result.per_model[0].evidence_regions
    assert {r.reason_code for r in regions} == {
        "mountain_wind",
        "mountain_wave_corroborated",
    }
    assert all(
        r.lower_altitude_ft is None and r.upper_altitude_ft is None
        for r in regions
    )
    by_reason = {r.reason_code: r for r in regions}
    assert by_reason["mountain_wind"].metric_id == "wind_speed_kt"
    assert by_reason["mountain_wind"].method_id == "terrain_wind"
    assert by_reason["mountain_wave_corroborated"].metric_id == "wind_speed_kt"
    assert (
        by_reason["mountain_wave_corroborated"].method_id
        == "terrain_wind_wave"
    )


def test_mountain_wind_missing_target_wind_is_unavailable():
    result = _evaluate(_ctx(32.0, missing_wind_indices={4, 5, 6}))
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "partial"


def test_mountain_wind_missing_elevation_is_unavailable():
    result = _evaluate(replace(_ctx(32.0), elevation=None))
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"


def test_mountain_wind_partial_hazard_remains_red():
    result = _evaluate(
        _ctx(
            32.0,
            ridge_inversion=True,
            missing_wind_indices={4},
        )
    )
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.RED
    assert model.data_state == "partial"


def test_mountain_wind_uses_target_speed_without_direction():
    ctx = _with_point_pressure_levels(
        _ctx(10.0),
        {
            4: [
                PressureLevelData(
                    pressure_hpa=800,
                    wind_speed_kt=45,
                    wind_direction_deg=None,
                ),
                PressureLevelData(
                    pressure_hpa=600,
                    wind_speed_kt=10,
                    wind_direction_deg=270,
                ),
            ]
        },
    )

    result = _evaluate(ctx)

    assert result.per_model[0].status == AdvisoryStatus.RED
    assert "45kt" in result.per_model[0].detail


def test_mountain_wave_detail_uses_same_point_speed_signature_and_extent():
    ctx = _with_point_pressure_levels(
        _ctx(10.0),
        {
            4: [PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=39,
                wind_direction_deg=270,
            )],
            5: [PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=31,
                wind_direction_deg=270,
            )],
        },
    )
    ctx = _with_ridge_inversions(ctx, {5})

    result = _evaluate(ctx)

    model = result.per_model[0]
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "terrain_wind_wave"
    assert "31kt" in model.detail
    assert "39kt" not in model.detail
    assert "stable layer" in model.detail
    assert "22nm/200nm (10%)" in model.detail


def test_severe_mountain_wind_does_not_borrow_another_points_signature():
    ctx = _with_point_pressure_levels(
        _ctx(10.0),
        {
            4: [PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=45,
                wind_direction_deg=270,
            )],
            5: [PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=32,
                wind_direction_deg=270,
            )],
        },
    )
    ctx = _with_ridge_inversions(ctx, {5})

    result = _evaluate(ctx)

    model = result.per_model[0]
    assert model.status == AdvisoryStatus.RED
    assert "45kt" in model.detail
    assert "stable layer" not in model.detail


def test_amber_mountain_wind_does_not_borrow_another_points_signature():
    ctx = _with_point_pressure_levels(
        _ctx(10.0),
        {
            4: [PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=29,
                wind_direction_deg=270,
            )],
            5: [PressureLevelData(
                pressure_hpa=800,
                wind_speed_kt=25,
                wind_direction_deg=270,
            )],
        },
    )
    ctx = _with_ridge_inversions(ctx, {5})

    result = _evaluate(ctx)

    model = result.per_model[0]
    assert model.status == AdvisoryStatus.AMBER
    assert "29kt" in model.detail
    assert "stable layer" not in model.detail
