"""The flight path in the pooled forecast wind (#1007)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from weatherbrief.analysis.flight_path import (
    PRESETS,
    analyses_flight_path,
    build_flight_path,
    compute_flight_path,
    recompute_for_cruise,
    route_flight_path,
)
from weatherbrief.analysis.wind import route_headwind_grid
from weatherbrief.models import AircraftEquipage
from weatherbrief.models.analysis import HourlyForecast, PressureLevelData
from weatherbrief.models.flight_path import FlightPathSummary, HeadwindGrid

DEP = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
N = 11  # 0..200 nm every 20 nm, due north
LEVELS = (1000, 925, 850, 700, 600, 500, 400, 300)


def _climb(path):
    """(minutes, nm) of the climb to the path's peak, unscaled."""
    top = next(p for p in path.profile if p[1] >= path.peak_ft - 1e-6)
    return top[2] / 60.0, top[0]


def _hourly(t, speed, direction=0.0):
    levels = [
        PressureLevelData(pressure_hpa=p, wind_speed_kt=speed, wind_direction_deg=direction)
        if speed is not None else PressureLevelData(pressure_hpa=p)
        for p in LEVELS
    ]
    return HourlyForecast(time=t, pressure_levels=levels)


class _Wf:
    """A point forecast: the same hourly wind for every hour of the day."""

    def __init__(self, speed, direction=0.0):
        self.speed, self.direction = speed, direction

    def at_time(self, t):
        return _hourly(t.replace(minute=0, second=0, microsecond=0), self.speed, self.direction)


def _cs(model, speed, direction=0.0):
    return SimpleNamespace(
        model=SimpleNamespace(value=model),
        point_forecasts=[_Wf(speed, direction) for _ in range(N)],
    )


def _route_points():
    return [
        SimpleNamespace(lat=48.0 + i * 20.0 / 60.0, lon=2.0, distance_from_origin_nm=i * 20.0)
        for i in range(N)
    ]


def _samples():
    return [(i, i * 20.0, 0.0, DEP) for i in range(N)]


# --- the integration in a uniform wind (hand calculation) ---------------------


@pytest.mark.parametrize("headwind", [30.0, -30.0])
def test_uniform_wind_moves_toc_by_the_climb_time(headwind):
    perf = PRESETS["hp_piston"]
    still = compute_flight_path([0, 200], 8000, perf, cruise_ias_kt=140)
    windy = compute_flight_path(
        [0, 200], 8000, perf, cruise_ias_kt=140, wind_at=lambda d, a: headwind,
    )
    still_min, still_nm = _climb(still)
    windy_min, windy_nm = _climb(windy)
    # A fixed-rate climb takes the same minutes; the ground covered changes by
    # the wind times that time.
    assert windy_min == pytest.approx(still_min, rel=1e-6)
    assert windy_nm == pytest.approx(still_nm - headwind * still_min / 60.0, abs=0.05)
    if headwind > 0:
        assert windy.toc_nm < still.toc_nm
        assert windy_min / windy_nm > still_min / still_nm  # longer per nm
        assert windy.duration_h > still.duration_h
    else:
        assert windy.toc_nm > still.toc_nm
        assert windy_min / windy_nm < still_min / still_nm
        assert windy.duration_h < still.duration_h
    # The descent (fixed rate) starts closer to the destination in a headwind.
    still_desc = still.total_distance_nm - still.tod_nm
    windy_desc = windy.total_distance_nm - windy.tod_nm
    assert (windy_desc < still_desc) == (headwind > 0)
    assert windy.wind == "consensus" and still.wind == "still_air"


# --- the grid -------------------------------------------------------------------


def test_grid_is_the_median_of_the_models_on_the_track():
    sections = [_cs("gfs", 10.0), _cs("ecmwf", 20.0), _cs("icon", 60.0)]
    grid = route_headwind_grid(sections, _samples(), 10000)
    # Wind from the north, track north: all headwind; median of 10, 20, 60.
    assert grid.headwind_at(100, 5000) == pytest.approx(20.0)
    assert grid.models == ["gfs", "ecmwf", "icon"]
    assert grid.altitudes_ft[0] == 0 and grid.altitudes_ft[-1] == 10000
    # From the south it is a tailwind.
    tail = route_headwind_grid([_cs("gfs", 25.0, 180.0)], _samples(), 10000)
    assert tail.headwind_at(100, 5000) == pytest.approx(-25.0)


def test_a_model_without_wind_is_left_out_of_the_pool():
    sections = [_cs("gfs", 10.0), _cs("ecmwf", None), _cs("icon", 30.0)]
    grid = route_headwind_grid(sections, _samples(), 10000)
    assert grid.headwind_at(100, 5000) == pytest.approx(20.0)


def test_a_pack_without_winds_has_no_grid():
    assert route_headwind_grid([_cs("gfs", None)], _samples(), 10000) is None
    assert route_headwind_grid([], _samples(), 10000) is None


def test_cells_no_model_reaches_read_still_air():
    # Levels stop at 300 hPa (~30,000 ft); the grid runs to 40,000 ft.
    grid = route_headwind_grid([_cs("gfs", 30.0)], _samples(), 40000)
    assert grid.headwind_at(100, 5000) == pytest.approx(30.0)
    assert grid.headwind_at(100, 40000) == 0.0


def test_grid_interpolates_and_clamps():
    grid = HeadwindGrid(
        distances_nm=[0, 100], altitudes_ft=[0, 10000],
        headwind_kt=[[0, 20], [10, 30]],
    )
    assert grid.headwind_at(50, 5000) == pytest.approx(15)
    assert grid.headwind_at(-10, -500) == 0
    assert grid.headwind_at(500, 50000) == 30


# --- the builders carry the wind ------------------------------------------------


def _eq():
    return AircraftEquipage(known=True, performance=PRESETS["hp_piston"])


def _route_path(cross_sections, **kw):
    return route_flight_path(
        _route_points(), 8000, equipage=_eq(), cruise_speed_ias_kt=140,
        planned_duration_h=1.5, cross_sections=cross_sections, departure_time=DEP, **kw,
    )


def test_route_path_flies_the_forecast_wind():
    still = _route_path(None)
    windy = _route_path([_cs("gfs", 30.0)])
    assert still.wind == "still_air" and still.wind_grid is None
    assert windy.wind == "consensus" and windy.wind_grid is not None
    assert windy.toc_nm < still.toc_nm
    # The planned duration stays authoritative.
    assert windy.duration_h * windy.time_scale == pytest.approx(1.5)
    assert FlightPathSummary.of(windy, live=True).wind == "consensus"


def test_without_winds_the_path_is_todays_still_air_path():
    before = route_flight_path(
        _route_points(), 8000, equipage=_eq(), cruise_speed_ias_kt=140, planned_duration_h=1.5,
    )
    after = _route_path([_cs("gfs", None)])
    assert after.model_dump() == before.model_dump()


def test_recompute_for_cruise_keeps_the_wind():
    windy = _route_path([_cs("gfs", 30.0)])
    still = _route_path(None)
    higher = recompute_for_cruise(windy, 10000)
    assert higher.wind == "consensus" and higher.wind_grid == windy.wind_grid
    assert higher.toc_nm < recompute_for_cruise(still, 10000).toc_nm


def _analyses(path):
    return [
        SimpleNamespace(
            point_index=p.point_index, distance_from_origin_nm=p.distance_nm, track_deg=0.0,
            interpolated_time=DEP + timedelta(seconds=p.time_offset_s),
        )
        for p in path.points
    ]


def test_recalculation_keeps_or_rebuilds_the_wind():
    sections = [_cs("gfs", 30.0)]
    windy = _route_path(sections)
    # A stored path with its grid: kept, no cross-sections needed.
    assert analyses_flight_path(_analyses(windy), 10000, stored=windy).wind == "consensus"
    # A stored still-air path (built before #1007) gets the wind when the
    # cross-sections are there, at the stored cruise too.
    old = _route_path(None)
    rebuilt = analyses_flight_path(_analyses(old), 8000, stored=old, cross_sections=sections)
    assert rebuilt.wind == "consensus"
    assert rebuilt.toc_nm == pytest.approx(windy.toc_nm, abs=0.5)
    # An old pack (no stored path) builds one in the wind.
    fresh = analyses_flight_path(
        _analyses(old), 8000, equipage=_eq(), cruise_speed_ias_kt=140,
        planned_duration_h=1.5, cross_sections=sections,
    )
    assert fresh.wind == "consensus"
    # No cross-sections: still air, as before.
    assert analyses_flight_path(_analyses(old), 8000, stored=old).wind == "still_air"


def test_build_reads_the_wind_at_the_given_times():
    seen = []

    class _Spy(_Wf):
        def at_time(self, t):
            seen.append(t)
            return super().at_time(t)

    cs = SimpleNamespace(model=SimpleNamespace(value="gfs"), point_forecasts=[_Spy(20.0) for _ in range(N)])
    late = DEP + timedelta(hours=5)
    path = build_flight_path(
        [i * 20.0 for i in range(N)], list(range(N)), 8000, cruise_ias_kt=140,
        cross_sections=[cs], tracks_deg=[0.0] * N, times=[late] * N,
    )
    assert path.wind == "consensus"
    assert set(seen) == {late}
