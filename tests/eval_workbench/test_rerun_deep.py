"""``--deep`` must actually re-grade the soundings it recomputed (#578).

``rerun_manifest_deep`` recomputed the soundings and then threw them away:
``run_analysis_from_pack`` *returns* a manifest, it writes nothing, and the
``run_advisories_from_pack`` beside it re-reads ``route_analyses.json`` off
disk. So the deep re-run graded the OLD soundings and reported "no change" for
exactly the sounding-layer changes it exists to validate — byte-identical
turbulence output for #539, a change that demonstrably fires (9 severe-at-cruise
layers → 0 on one pack).

The check ran, reported success, and never touched the thing it claimed to
verify. These tests pin both halves: the recomputed analyses reach disk before
the re-grade reads them, and a deliberate sounding-layer perturbation produces a
diff end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.eval_workbench import rerun
from weatherbrief.models import (
    CloudCoverage,
    EnhancedCloudLayer,
    ForecastSnapshot,
    HourlyForecast,
    RouteAnalysesManifest,
    RouteConfig,
    RouteCrossSection,
    RoutePoint,
    RoutePointAnalysis,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.tasks.advise import run_advisories_from_pack
from weatherbrief.tasks.artifacts import save_analysis_artifacts, save_fetch_artifacts

_MODEL = "gfs"
_T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
_CRUISE_FT = 8000
_POINTS = 5
_SPACING_NM = 25.0


@pytest.fixture
def hourly(sample_pressure_levels_with_omega) -> HourlyForecast:
    return HourlyForecast(
        time=_T0,
        temperature_2m_c=12.0,
        dewpoint_2m_c=8.0,
        cloud_cover_pct=40.0,
        pressure_levels=sample_pressure_levels_with_omega,
    )


@pytest.fixture
def route() -> RouteConfig:
    return RouteConfig(
        name="Deep re-run",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32),
            Waypoint(icao="LFPB", name="Paris Le Bourget", lat=48.9694, lon=2.4414),
        ],
        cruise_altitude_ft=_CRUISE_FT,
        flight_duration_hours=2.0,
    )


@pytest.fixture
def cross_sections(hourly, route) -> list[RouteCrossSection]:
    """One model over a five-point route, every point carrying a real profile."""
    route_points, forecasts = [], []
    for i in range(_POINTS):
        frac = i / (_POINTS - 1)
        lat = 51.8361 + (48.9694 - 51.8361) * frac
        lon = -1.32 + (2.4414 + 1.32) * frac
        icao = "EGTK" if i == 0 else ("LFPB" if i == _POINTS - 1 else None)
        route_points.append(RoutePoint(
            lat=lat, lon=lon,
            distance_from_origin_nm=i * _SPACING_NM,
            waypoint_icao=icao,
        ))
        forecasts.append(WaypointForecast(
            waypoint=Waypoint(icao=icao or f"P{i}", name=f"Point {i}", lat=lat, lon=lon),
            model=_MODEL, fetched_at=_T0, hourly=[hourly],
        ))
    return [RouteCrossSection(
        model=_MODEL, route_points=route_points, fetched_at=_T0,
        point_forecasts=forecasts,
    )]


@pytest.fixture
def pack_dir(tmp_path, route, cross_sections, hourly):
    """A written pack with a persisted advisory baseline to diff against."""
    analysis = analyze_sounding(hourly.pressure_levels, hourly)
    assert analysis is not None, "fixture needs a real sounding"

    cs = cross_sections[0]
    manifest = RouteAnalysesManifest(
        route_name=route.name,
        target_date="2026-06-01",
        departure_time=_T0,
        flight_duration_hours=2.0,
        total_distance_nm=(_POINTS - 1) * _SPACING_NM,
        cruise_altitude_ft=_CRUISE_FT,
        models=[_MODEL],
        analyses=[
            RoutePointAnalysis(
                point_index=i, lat=rp.lat, lon=rp.lon,
                distance_from_origin_nm=rp.distance_from_origin_nm,
                waypoint_icao=rp.waypoint_icao,
                interpolated_time=_T0, forecast_hour=_T0, track_deg=120.0,
                sounding={_MODEL: analysis},
            )
            for i, rp in enumerate(cs.route_points)
        ],
    )
    snapshot = ForecastSnapshot(
        route=route, target_date="2026-06-01", fetch_date="2026-05-29",
        days_out=3, departure_time=_T0, cross_sections=cross_sections,
    )
    pd = tmp_path / "pack"
    save_fetch_artifacts(pd, cross_sections, None, cs.route_points)
    save_analysis_artifacts(pd, snapshot, manifest)
    # The baseline the diff compares against — written by the same path a real
    # refresh uses.
    result = run_advisories_from_pack(
        pd, route=route, advisory_models=[_MODEL], persist=True,
    )
    assert result.manifest is not None
    assert (pd / "route_advisories.json").exists()
    return pd


def _cover_cruise_in_cloud(monkeypatch):
    """Perturb the *sounding layer*: every point flies an OVC deck at cruise.

    Deliberately upstream of the evaluators — it is the layer ``--deep`` exists
    to re-grade, and the layer a plain re-run cannot see.
    """
    import weatherbrief.tasks.analyze as analyze

    real = analyze.analyze_sounding

    deck = [EnhancedCloudLayer(
        base_ft=_CRUISE_FT - 2000,
        top_ft=_CRUISE_FT + 3000,
        coverage=CloudCoverage.OVC,
    )]

    def perturbed(*args, **kwargs):
        result = real(*args, **kwargs)
        if result is None:
            return None
        # Both slots: the advisory stage resolves clouds per the engine default
        # (NWP), so patching only the DD-derived list would change nothing that
        # gets graded.
        return result.model_copy(update={
            "cloud_layers": list(deck),
            "dd_cloud_layers": list(deck),
            "nwp_cloud_layers": list(deck),
        })

    monkeypatch.setattr(analyze, "analyze_sounding", perturbed)


def test_deep_rerun_persists_the_recomputed_analyses(pack_dir, monkeypatch):
    """The re-grade must read the *new* soundings, not the ones on disk.

    Asserted at the seam so the failure names the defect: the file the advisory
    stage opens has to carry the recomputed cloud deck.
    """
    import weatherbrief.tasks.advise as advise

    _cover_cruise_in_cloud(monkeypatch)
    seen: dict[str, object] = {}
    real_advise = advise.run_advisories_from_pack

    def spy(pack, *args, **kwargs):
        from weatherbrief.tasks.artifacts import load_route_analyses

        manifest = load_route_analyses(pack)
        seen["tops"] = {
            layer.top_ft
            for a in manifest.analyses
            for s in a.sounding.values()
            for layer in (s.nwp_cloud_layers or []) + s.cloud_layers
        }
        return real_advise(pack, *args, **kwargs)

    monkeypatch.setattr(advise, "run_advisories_from_pack", spy)

    rerun.rerun_manifest_deep(pack_dir, rerun.load_saved_manifest(pack_dir))

    # The pack's own soundings carry cloud layers of their own, so "some layers
    # are present" proves nothing — the perturbed deck's top is the marker.
    assert _CRUISE_FT + 3000 in seen["tops"], (
        "the advisory re-grade read the pack's OLD route_analyses.json — the "
        f"recomputed soundings never reached disk (tops seen: {sorted(seen['tops'])})"
    )


def test_a_sounding_layer_perturbation_shows_up_in_the_deep_diff(pack_dir, monkeypatch):
    """End-to-end: perturb the soundings, and `--deep` must report a change."""
    _cover_cruise_in_cloud(monkeypatch)

    deep = rerun.rerun_diff(pack_dir, deep=True)
    assert deep["had_baseline"]
    changed = {c["advisory_id"] for c in deep["changes"]}
    assert changed, "deep re-run reported no change after a sounding-layer change"
    assert "vmc_cruise" in changed, (
        f"an OVC deck through cruise must move vmc_cruise; changed={sorted(changed)}"
    )


def test_a_shallow_rerun_cannot_see_a_sounding_layer_change(pack_dir, monkeypatch):
    """The other half of the contract — why `--deep` has to exist at all.

    Without it the saved (old) soundings are re-graded, so the same perturbation
    is invisible. This is the behaviour that made the broken deep path look
    plausible: both modes agreed, and both were reporting the old numbers.
    """
    _cover_cruise_in_cloud(monkeypatch)

    shallow = rerun.rerun_diff(pack_dir, deep=False)
    assert shallow["changed_count"] == 0, shallow["changes"]
