from __future__ import annotations

from datetime import datetime, timezone

import pytest

import weatherbrief.analysis.advisories as advisory_framework
import weatherbrief.analysis.advisories.altitude_table as altitude_table_module
import weatherbrief.tasks.advise as advise
import weatherbrief.tasks.analyze as analyze
import weatherbrief.tasks.artifacts as artifacts
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AltitudeTableResult,
    RouteAnalysesManifest,
    RoutePoint,
    RoutePointAnalysis,
    SoundingAnalysis,
)


NOW = datetime(2026, 7, 10, 9, 0, tzinfo=timezone.utc)
METHODS = {
    "icing_method": "sfip_nwp",
    "cloud_method": "natural_nwp",
    "convective_method": "nwp",
}


def _analysis() -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=0,
        lat=51.0,
        lon=0.0,
        distance_from_origin_nm=0.0,
        interpolated_time=NOW,
        forecast_hour=NOW,
        track_deg=90.0,
        sounding={"gfs": SoundingAnalysis()},
    )


def _manifest() -> RouteAnalysesManifest:
    return RouteAnalysesManifest(
        route_name="test-route",
        target_date="2026-07-10",
        departure_time=NOW,
        flight_duration_hours=1.0,
        total_distance_nm=100.0,
        cruise_altitude_ft=8000,
        models=["gfs"],
        analyses=[_analysis()],
    )


def _empty_table(*, cruise_altitude_ft: int, flight_ceiling_ft: int, step_ft: int) -> AltitudeTableResult:
    return AltitudeTableResult(
        rows=[],
        advisory_ids=[],
        advisory_names={},
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=flight_ceiling_ft,
        step_ft=step_ft,
    )


def _method_values(kwargs: dict) -> dict[str, str | None]:
    return {name: kwargs[name] for name in METHODS}


@pytest.fixture
def captured_contexts(monkeypatch):
    captured: list[dict] = []

    class RecordingRouteContext:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.__dict__.update(kwargs)

    monkeypatch.setattr(advisory_framework, "RouteContext", RecordingRouteContext)
    monkeypatch.setattr(advisory_framework, "evaluate_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(advisory_framework, "get_catalog", lambda: [])
    return captured


def test_run_advisories_threads_methods_to_context_and_altitude_precompute(
    monkeypatch,
    captured_contexts,
    sample_route,
):
    altitude_calls: list[dict] = []

    def record_altitude_table(**kwargs):
        altitude_calls.append(kwargs)
        return _empty_table(
            cruise_altitude_ft=kwargs["cruise_altitude_ft"],
            flight_ceiling_ft=kwargs["flight_ceiling_ft"],
            step_ft=kwargs["step_ft"],
        )

    monkeypatch.setattr(advise, "_compute_airport_conditions", lambda *args, **kwargs: None)
    monkeypatch.setattr(advise, "_compute_route_sun", lambda *args, **kwargs: None)
    monkeypatch.setattr(altitude_table_module, "compute_altitude_table", record_altitude_table)

    result = advise.run_advisories(
        rp_analyses=[_analysis()],
        cross_sections=[],
        elevation_profile=None,
        model_names=["gfs"],
        route=sample_route,
        total_distance_nm=100.0,
        altitude_table_step_ft=2000,
        **METHODS,
    )

    assert result.error is None
    assert len(captured_contexts) == 1
    assert _method_values(captured_contexts[0]) == METHODS
    assert len(altitude_calls) == 1
    assert _method_values(altitude_calls[0]) == METHODS


def test_run_advisories_from_pack_threads_methods_to_context(
    tmp_path,
    monkeypatch,
    captured_contexts,
):
    monkeypatch.setattr(artifacts, "load_route_analyses", lambda pack_dir: _manifest())
    monkeypatch.setattr(artifacts, "load_cross_sections", lambda pack_dir: [])
    monkeypatch.setattr(artifacts, "load_elevation_profile", lambda pack_dir: None)
    monkeypatch.setattr(advise, "_front_context", lambda *args, **kwargs: (None, args[1]))
    monkeypatch.setattr(advise, "_compute_route_sun", lambda *args, **kwargs: None)

    result = advise.run_advisories_from_pack(
        tmp_path,
        flight_ceiling_ft=18000,
        persist=False,
        **METHODS,
    )

    assert result.error is None
    assert len(captured_contexts) == 1
    assert _method_values(captured_contexts[0]) == METHODS


def test_run_alt_from_pack_threads_methods_to_context(
    tmp_path,
    monkeypatch,
    captured_contexts,
    sample_route,
):
    route_points = [
        RoutePoint(lat=51.0, lon=0.0, distance_from_origin_nm=0.0),
        RoutePoint(lat=51.0, lon=1.0, distance_from_origin_nm=100.0),
    ]
    monkeypatch.setattr(artifacts, "load_route_points", lambda pack_dir: route_points)
    monkeypatch.setattr(artifacts, "load_elevation_profile", lambda pack_dir: None)
    monkeypatch.setattr(analyze, "analyze_all_route_points", lambda **kwargs: [_analysis()])
    monkeypatch.setattr(advise, "_front_context", lambda *args, **kwargs: (None, args[1]))

    result = advise.run_alt_from_pack(
        tmp_path,
        NOW,
        sample_route,
        cross_sections=[object()],
        persist=False,
        detect_fronts=False,
        **METHODS,
    )

    assert result.error is None
    assert len(captured_contexts) == 1
    assert _method_values(captured_contexts[0]) == METHODS


def test_compute_altitude_table_threads_methods_to_every_context(
    monkeypatch,
    captured_contexts,
):
    entry = AdvisoryCatalogEntry(
        id="cloud_top",
        name="Cloud top",
        short_description="Cloud top",
        description="Cloud top",
        category="cloud",
        altitude_dependent=True,
    )
    monkeypatch.setattr(advisory_framework, "get_altitude_dependent_ids", lambda: {"cloud_top"})
    monkeypatch.setattr(advisory_framework, "get_catalog", lambda: [entry])

    altitude_table_module.compute_altitude_table(
        analyses=[_analysis()],
        cross_sections=[],
        elevation=None,
        models=["gfs"],
        cruise_altitude_ft=3000,
        flight_ceiling_ft=4000,
        total_distance_nm=100.0,
        step_ft=2000,
        **METHODS,
    )

    assert {ctx["cruise_altitude_ft"] for ctx in captured_contexts} == {2000, 3000, 4000}
    assert all(_method_values(ctx) == METHODS for ctx in captured_contexts)


def test_run_altitude_table_from_pack_threads_methods_to_compute(
    tmp_path,
    monkeypatch,
):
    calls: list[dict] = []

    def record_altitude_table(**kwargs):
        calls.append(kwargs)
        return _empty_table(
            cruise_altitude_ft=kwargs["cruise_altitude_ft"],
            flight_ceiling_ft=kwargs["flight_ceiling_ft"],
            step_ft=kwargs["step_ft"],
        )

    monkeypatch.setattr(artifacts, "load_route_analyses", lambda pack_dir: _manifest())
    monkeypatch.setattr(artifacts, "load_cross_sections", lambda pack_dir: [])
    monkeypatch.setattr(artifacts, "load_elevation_profile", lambda pack_dir: None)
    monkeypatch.setattr(altitude_table_module, "compute_altitude_table", record_altitude_table)

    advise.run_altitude_table_from_pack(
        tmp_path,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        **METHODS,
    )

    assert len(calls) == 1
    assert _method_values(calls[0]) == METHODS
