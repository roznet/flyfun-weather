"""The flight path through the analysis stage (#1005): point stamps and times."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import weatherbrief.analysis.spatial_interpolation as spatial
import weatherbrief.tasks.analyze as analyze
from weatherbrief.analysis.flight_path import PRESETS, route_flight_path
from weatherbrief.models import AircraftEquipage

DEP = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
N = 11  # 0..200 nm every 20 nm


def _route_points():
    return [
        SimpleNamespace(
            lat=48.0 + i * 0.3, lon=2.0, distance_from_origin_nm=i * 20.0,
            waypoint_icao=None, waypoint_name=None,
        )
        for i in range(N)
    ]


def _cross_sections():
    class _Wf:
        def at_time(self, t):
            return SimpleNamespace(time=t.replace(minute=0, second=0, microsecond=0))

    return [SimpleNamespace(model=SimpleNamespace(value="gfs"), point_forecasts=[_Wf() for _ in range(N)])]


@pytest.fixture
def stubbed(monkeypatch):
    monkeypatch.setattr(spatial, "interpolate_all_spatially", lambda cs, rps: None)
    monkeypatch.setattr(analyze, "_enrich_e_shear", lambda analyses: None)
    monkeypatch.setattr(
        analyze, "_run_point_analysis", lambda *a, **k: ({}, {}, None, [], {}),
    )


def _path():
    eq = AircraftEquipage(known=True, performance=PRESETS["hp_piston"])
    return route_flight_path(_route_points(), 10000, equipage=eq, cruise_speed_ias_kt=150,
                             planned_duration_h=1.5)


def test_points_are_stamped_with_the_path_and_keep_linear_times_in_shadow(stubbed):
    path = _path()
    out = analyze.analyze_all_route_points(
        _cross_sections(), _route_points(), DEP, 1.5, 10000, 18000,
        flight_path=path, path_times=False,
    )
    assert [a.phase for a in out][:2] == ["climb", "climb"]
    assert out[5].phase == "cruise" and out[5].planned_altitude_ft == pytest.approx(10000)
    assert out[-1].phase == "descent"
    # Shadow: times stay linear in distance.
    assert out[5].interpolated_time == DEP + timedelta(hours=0.75)


def test_live_times_come_from_the_path(stubbed):
    path = _path()
    out = analyze.analyze_all_route_points(
        _cross_sections(), _route_points(), DEP, 1.5, 10000, 18000,
        flight_path=path, path_times=True,
    )
    # The pilot's 1.5 h is kept end to end; the middle point is reached later
    # than linear, because the climb is slower than cruise.
    assert out[-1].interpolated_time == pytest.approx(DEP + timedelta(hours=1.5), abs=timedelta(seconds=2))
    assert out[5].interpolated_time > DEP + timedelta(hours=0.75)


def test_no_path_reads_as_before(stubbed):
    out = analyze.analyze_all_route_points(
        _cross_sections(), _route_points(), DEP, 1.5, 10000, 18000,
    )
    assert all(a.planned_altitude_ft is None and a.phase is None for a in out)


def test_off_flag_builds_no_path(monkeypatch):
    monkeypatch.setenv("WB_FLIGHT_PATH", "off")
    assert _path() is None
