"""Tests for route solar analysis (compute_route_sun)."""

from datetime import datetime, timedelta, timezone

from weatherbrief.analysis.sun import compute_route_sun, normalize_180
from weatherbrief.models import RoutePointAnalysis
from weatherbrief.models.airport_conditions import (
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
    RunwayEnd,
    RunwayWind,
)


def _route(
    *,
    start: datetime,
    n: int,
    lat0: float,
    lon0: float,
    dlat: float,
    dlon: float,
    track_deg: float,
    minutes_step: float = 20.0,
    leg_nm: float = 20.0,
) -> list[RoutePointAnalysis]:
    return [
        RoutePointAnalysis(
            point_index=i,
            lat=lat0 + i * dlat,
            lon=lon0 + i * dlon,
            distance_from_origin_nm=i * leg_nm,
            interpolated_time=start + timedelta(minutes=i * minutes_step),
            forecast_hour=start,
            track_deg=track_deg,
        )
        for i in range(n)
    ]


def _summary(icao: str, heading_deg: float, wind_dir: float, wind_kt: float = 12.0) -> AirportConditionsSummary:
    """Airport summary whose single-model wind-best runway has the given heading."""
    rwy = RunwayWind(runway_id=str(round(heading_deg / 10)).zfill(2),
                     heading_deg=heading_deg, crosswind_kt=2.0, headwind_kt=10.0)
    return AirportConditionsSummary(
        icao=icao,
        name=icao,
        runway_ends=[RunwayEnd(id=rwy.runway_id, heading_deg=heading_deg)],
        conditions=[AirportModelCondition(
            model="gfs", flight_category=FlightCategory.VFR,
            wind_speed_kt=wind_kt, wind_direction_deg=wind_dir, best_runway=rwy,
        )],
    )


class TestNormalize180:
    def test_wraps(self):
        assert normalize_180(350) == -10
        assert normalize_180(-200) == 160
        assert normalize_180(0) == 0
        # 180 maps to -180 (range [-180, 180)); glare uses abs() so the sign is moot.
        assert normalize_180(180) == -180


class TestNightIntervals:
    def test_evening_westbound_yields_twilight_then_night(self):
        # Evening departure; over a couple of hours the sun sets.
        start = datetime(2024, 6, 21, 19, 0, tzinfo=timezone.utc)
        analyses = _route(start=start, n=11, lat0=48.0, lon0=2.0,
                          dlat=0.0, dlon=-0.5, track_deg=270.0)
        rs = compute_route_sun(analyses)
        phases = [n.phase for n in rs.night_intervals]
        assert "twilight" in phases
        assert "night" in phases
        # Boundaries are ordered and within the route extent.
        for n in rs.night_intervals:
            assert 0.0 <= n.start_distance_nm <= n.end_distance_nm <= 200.0

    def test_midday_flight_has_no_night(self):
        start = datetime(2024, 6, 21, 11, 0, tzinfo=timezone.utc)
        analyses = _route(start=start, n=8, lat0=48.0, lon0=2.0,
                          dlat=0.0, dlon=0.4, track_deg=90.0)
        rs = compute_route_sun(analyses)
        assert rs.night_intervals == []

    def test_empty_route_does_not_crash(self):
        rs = compute_route_sun([])
        assert rs.night_intervals == []
        assert rs.sun_side.dominant_side == "none"


class TestSunSide:
    def test_eastbound_morning_sun_on_right_then_left_by_direction(self):
        # Morning, sun in the east (~90deg true). Heading north (track 0):
        # rel = az - track ~ +90 -> right. Heading south (track 180): rel ~ -90 -> left.
        start = datetime(2024, 6, 21, 7, 0, tzinfo=timezone.utc)
        north = _route(start=start, n=6, lat0=46.0, lon0=2.0, dlat=0.3, dlon=0.0, track_deg=0.0)
        south = _route(start=start, n=6, lat0=46.0, lon0=2.0, dlat=-0.3, dlon=0.0, track_deg=180.0)
        rs_n = compute_route_sun(north)
        rs_s = compute_route_sun(south)
        assert rs_n.sun_side.dominant_side == "right"
        assert rs_s.sun_side.dominant_side == "left"

    def test_all_night_has_no_side(self):
        start = datetime(2024, 6, 21, 1, 0, tzinfo=timezone.utc)  # deep night in Europe
        analyses = _route(start=start, n=5, lat0=48.0, lon0=2.0, dlat=0.0, dlon=0.3, track_deg=90.0)
        rs = compute_route_sun(analyses)
        assert rs.sun_side.dominant_side == "none"


class TestGlare:
    def test_sunset_landing_onto_westerly_runway_is_into_sun(self):
        # Autumn equinox late afternoon: sun ~7.5deg up, azimuth ~262 (near due
        # west). Land on a westerly (270) runway -> low sun down the runway.
        start = datetime(2024, 9, 22, 16, 0, tzinfo=timezone.utc)
        analyses = _route(start=start, n=5, lat0=48.0, lon0=2.0, dlat=0.0, dlon=0.0, track_deg=270.0,
                          minutes_step=15.0)
        arr = _summary("LFXX", heading_deg=270.0, wind_dir=270.0)
        rs = compute_route_sun(analyses, arrival=arr)
        assert rs.landing is not None
        assert rs.landing.runway_ident == "27"
        # Sun should be low and roughly down the runway.
        assert rs.landing.sun_elevation_deg is not None
        assert 0 < rs.landing.sun_elevation_deg <= 15
        assert rs.landing.into_sun is True

    def test_no_runway_data_keeps_glare_false(self):
        start = datetime(2024, 6, 21, 19, 30, tzinfo=timezone.utc)
        analyses = _route(start=start, n=4, lat0=48.0, lon0=2.0, dlat=0.0, dlon=-0.3, track_deg=270.0)
        # Summary with no runway/wind data.
        arr = AirportConditionsSummary(icao="LFYY", name="LFYY")
        rs = compute_route_sun(analyses, arrival=arr)
        assert rs.landing is not None
        assert rs.landing.runway_ident is None
        assert rs.landing.into_sun is False

    def test_high_sun_is_not_glare(self):
        # Midday landing: sun high -> no glare even down the runway.
        start = datetime(2024, 6, 21, 11, 30, tzinfo=timezone.utc)
        analyses = _route(start=start, n=4, lat0=48.0, lon0=2.0, dlat=0.0, dlon=0.1, track_deg=180.0)
        arr = _summary("LFZZ", heading_deg=180.0, wind_dir=180.0)
        rs = compute_route_sun(analyses, arrival=arr)
        assert rs.landing is not None
        assert rs.landing.into_sun is False
