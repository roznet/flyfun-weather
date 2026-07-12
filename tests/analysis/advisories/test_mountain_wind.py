"""Tests for the mountain wind advisory's wave-signature corroboration."""

from __future__ import annotations

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


def _cross_section(wind_speed_kt: float) -> RouteCrossSection:
    """One-model cross-section with uniform wind at a single 800hPa level."""
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
                pressure_levels=[PressureLevelData(
                    pressure_hpa=800,
                    wind_speed_kt=wind_speed_kt,
                    wind_direction_deg=270.0,
                )],
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
        cross_sections=[_cross_section(wind_speed_kt)],
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


class TestMountainWindWaveCorroboration:

    def test_light_wind_green(self):
        result = _evaluate(_ctx(10.0))
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_strong_wind_no_signature_amber(self):
        # 32kt is above the corroborated red bar (30) but with no wave
        # signature it stays AMBER until the plain red bar (40).
        result = _evaluate(_ctx(32.0))
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_strong_wind_with_ridge_inversion_red(self):
        result = _evaluate(_ctx(32.0, ridge_inversion=True))
        assert result.aggregate_status == AdvisoryStatus.RED
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
        assert "stable layer" in result.per_model[0].detail

    def test_no_mountains_green(self):
        ctx = _ctx(45.0)
        flat = ElevationProfile(
            route_name="test",
            points=[ElevationPoint(distance_nm=p.distance_nm, elevation_ft=500, lat=p.lat, lon=p.lon)
                    for p in ctx.elevation.points],
            max_elevation_ft=500, total_distance_nm=_TOTAL_NM,
        )
        from dataclasses import replace
        result = _evaluate(replace(ctx, elevation=flat))
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_corroborated_threshold_tunable(self):
        result = _evaluate(
            _ctx(32.0, ridge_inversion=True),
            params={"corroborated_red_kt": 35},
        )
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_mountains_but_no_wind_data_is_unavailable(self):
        """Mountain points on route but no wind lookup for any of them.

        Regression for #391: max_wind stayed 0.0 → GREEN "light winds (0kt)"
        while the highlight ribbon already marked those points UNAVAILABLE. The
        grade must agree — UNAVAILABLE, not a benign green.
        """
        from dataclasses import replace

        ctx = replace(_ctx(32.0), cross_sections=[])  # no wind data anywhere
        result = _evaluate(ctx)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_no_elevation_profile_is_unavailable(self):
        """No elevation profile at all — terrain is unknown, not "no mountains".

        Regression for #391: missing elevation used to read as GREEN "no
        significant terrain". We cannot assert flat terrain we never saw.
        """
        from dataclasses import replace

        ctx = replace(_ctx(45.0), elevation=None)
        result = _evaluate(ctx)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_light_wind_at_few_mountain_points_is_unavailable(self):
        """"Light winds" when wind resolves at too few mountain points → UNAVAILABLE.

        Regression for #391 review: the plateau spans points 4-6 (3 mountain
        points); a cross-section that resolves wind at only one of them, calm,
        used to grade GREEN "Light winds" for a mostly-unassessed mountain
        segment. Coverage is measured over mountain points.
        """
        from dataclasses import replace

        # Mountain points are 4, 5, 6. A cross-section whose forecasts only reach
        # index 4 leaves wind_at_altitude returning None for points 5 and 6 → 1 of
        # 3 mountain points covered (< 50%).
        ctx = _ctx(10.0)  # light wind where resolvable
        cs = ctx.cross_sections[0]
        trimmed = cs.model_copy(update={"point_forecasts": cs.point_forecasts[:5]})
        ctx = replace(ctx, cross_sections=[trimmed])
        result = _evaluate(ctx)
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
