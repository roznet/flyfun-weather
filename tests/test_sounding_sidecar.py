"""Tests for the sounding-profile sidecar (issue #188).

The sidecar (``sounding_profiles.json.gz``) is written at refresh time from the
in-memory route-analyses manifest (``derived_levels`` intact) so the bundle /
sounding-profile endpoints don't recompute MetPy soundings on every request.

The contract: serving from the sidecar must be value-identical to recomputing
on the fly, and every read path must fall back to recompute when the sidecar is
absent (backward compatibility for old packs and post-T1 packs).
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.sounding import analyze_sounding
from weatherbrief.models import (
    ForecastSnapshot,
    HourlyForecast,
    RouteConfig,
    RouteCrossSection,
    RoutePoint,
    RoutePointAnalysis,
    RouteAnalysesManifest,
    WaypointForecast,
    Waypoint,
)
from weatherbrief.storage.sounding_profiles import (
    SIDECAR_FILENAME,
    _build_sounding_profile,
    build_sounding_sidecar,
    load_or_build_sounding_profile,
    read_sounding_sidecar,
    write_sounding_sidecar,
)
from weatherbrief.tasks.artifacts import save_analysis_artifacts, save_fetch_artifacts


_MODEL = "gfs"
_T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Fixtures — build a realistic single-point, single-model pack
# ---------------------------------------------------------------------------


@pytest.fixture
def hourly(sample_pressure_levels_with_omega) -> HourlyForecast:
    """An HourlyForecast carrying a full pressure-level profile."""
    return HourlyForecast(
        time=_T0,
        temperature_2m_c=12.0,
        dewpoint_2m_c=8.0,
        cloud_cover_pct=40.0,
        pressure_levels=sample_pressure_levels_with_omega,
    )


@pytest.fixture
def manifest(hourly) -> RouteAnalysesManifest:
    """RouteAnalysesManifest with one point whose sounding has derived_levels.

    The sounding is produced by the *real* analyze_sounding so the stored
    derived_levels match what the on-the-fly fallback would recompute — that's
    what makes the sidecar-vs-fallback equality meaningful.
    """
    analysis = analyze_sounding(hourly.pressure_levels, hourly)
    assert analysis is not None and analysis.derived_levels, "expected derived_levels"

    point = RoutePointAnalysis(
        point_index=0,
        lat=51.8361,
        lon=-1.32,
        distance_from_origin_nm=0.0,
        waypoint_icao="EGTK",
        waypoint_name="Oxford Kidlington",
        interpolated_time=_T0,
        forecast_hour=_T0,
        track_deg=120.0,
        sounding={_MODEL: analysis},
    )
    return RouteAnalysesManifest(
        route_name="Test",
        target_date="2026-06-01",
        departure_time=_T0,
        flight_duration_hours=2.0,
        total_distance_nm=100.0,
        cruise_altitude_ft=8000,
        models=[_MODEL],
        analyses=[point],
    )


@pytest.fixture
def cross_sections(hourly) -> list[RouteCrossSection]:
    wp = Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32)
    wf = WaypointForecast(waypoint=wp, model=_MODEL, fetched_at=_T0, hourly=[hourly])
    rp = RoutePoint(lat=51.8361, lon=-1.32, distance_from_origin_nm=0.0, waypoint_icao="EGTK")
    return [RouteCrossSection(model=_MODEL, route_points=[rp], fetched_at=_T0, point_forecasts=[wf])]


@pytest.fixture
def snapshot(cross_sections) -> ForecastSnapshot:
    route = RouteConfig(
        name="Test",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford Kidlington", lat=51.8361, lon=-1.32),
            Waypoint(icao="LFPB", name="Paris Le Bourget", lat=48.9694, lon=2.4414),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=2.0,
    )
    return ForecastSnapshot(
        route=route,
        target_date="2026-06-01",
        fetch_date="2026-05-29",
        days_out=3,
        departure_time=_T0,
        cross_sections=cross_sections,
    )


@pytest.fixture
def pack_dir(tmp_path, snapshot, manifest, cross_sections):
    """A written pack dir: fetch + analysis artifacts, including the sidecar."""
    pd = tmp_path / "pack"
    save_fetch_artifacts(pd, cross_sections, None, cross_sections[0].route_points)
    save_analysis_artifacts(pd, snapshot, manifest)
    return pd


# ---------------------------------------------------------------------------
# Sidecar is written at refresh time
# ---------------------------------------------------------------------------


def test_refresh_writes_sidecar(pack_dir):
    sidecar_path = pack_dir / SIDECAR_FILENAME
    assert sidecar_path.exists(), "save_analysis_artifacts should write the sidecar"

    sidecar = json.loads(gzip.decompress(sidecar_path.read_bytes()))
    # One (point, model) combination -> one entry.
    assert "sounding-0-gfs" in sidecar
    assert sidecar["sounding-0-gfs"]["model"] == _MODEL
    assert sidecar["sounding-0-gfs"]["levels"], "profile should have levels"


def test_sidecar_built_without_recompute(monkeypatch, pack_dir, snapshot, manifest, cross_sections):
    """The refresh-time build must NOT call analyze_sounding (no MetPy recompute)."""
    import weatherbrief.analysis.sounding as snd

    def _boom(*a, **k):
        raise AssertionError("analyze_sounding must not run when derived_levels are present")

    # _build_sounding_profile does `from weatherbrief.analysis.sounding import
    # analyze_sounding` at call time, so patch the source module attribute.
    monkeypatch.setattr(snd, "analyze_sounding", _boom)
    # Re-build straight from the in-memory manifest (derived_levels present).
    ra_full = json.loads(manifest.model_dump_json())
    cs_data = {"cross_sections": [cs.model_dump(mode="json") for cs in cross_sections]}
    out = build_sounding_sidecar(ra_full, cs_data)
    assert "sounding-0-gfs" in out


# ---------------------------------------------------------------------------
# Sidecar vs on-the-fly fallback equality
# ---------------------------------------------------------------------------


def test_sounding_profile_sidecar_equals_fallback(pack_dir):
    """get_sounding_profile: sidecar result == recompute result, value-identical."""
    ra_data = json.loads((pack_dir / "route_analyses.json").read_text())
    cs_data = json.loads((pack_dir / "cross_section.json").read_text())

    # 1. Served from the sidecar (present).
    from_sidecar = load_or_build_sounding_profile(pack_dir, ra_data, cs_data, 0, _MODEL)
    assert from_sidecar is not None

    # 2. Force the fallback by deleting the sidecar.
    (pack_dir / SIDECAR_FILENAME).unlink()
    assert read_sounding_sidecar(pack_dir) is None
    from_fallback = load_or_build_sounding_profile(pack_dir, ra_data, cs_data, 0, _MODEL)
    assert from_fallback is not None

    assert from_sidecar.model_dump() == from_fallback.model_dump()


def test_bundle_sidecar_equals_fallback(pack_dir):
    """get_bundle sounding entries: sidecar == on-the-fly build, per key."""
    ra_data = json.loads((pack_dir / "route_analyses.json").read_text())
    cs_data = json.loads((pack_dir / "cross_section.json").read_text())

    sidecar = read_sounding_sidecar(pack_dir)
    assert sidecar is not None

    fallback = build_sounding_sidecar(ra_data, cs_data)

    assert set(sidecar) == set(fallback)
    for key in sidecar:
        assert sidecar[key] == fallback[key], f"mismatch for {key}"


# ---------------------------------------------------------------------------
# Backward compatibility — no sidecar
# ---------------------------------------------------------------------------


def test_pack_without_sidecar_still_serves(pack_dir):
    """A pack with no sidecar (old packs) recomputes and still returns data."""
    (pack_dir / SIDECAR_FILENAME).unlink()
    ra_data = json.loads((pack_dir / "route_analyses.json").read_text())
    cs_data = json.loads((pack_dir / "cross_section.json").read_text())

    result = load_or_build_sounding_profile(pack_dir, ra_data, cs_data, 0, _MODEL)
    assert result is not None
    assert result.levels


def test_post_t1_no_cross_section_returns_none(pack_dir):
    """After T1 (sidecar + cross_section.json stripped) recompute yields None."""
    (pack_dir / SIDECAR_FILENAME).unlink()
    (pack_dir / "cross_section.json").unlink()
    ra_data = json.loads((pack_dir / "route_analyses.json").read_text())

    # No sidecar, no cross-section -> nothing to build from.
    result = load_or_build_sounding_profile(pack_dir, ra_data, {}, 0, _MODEL)
    assert result is None


def test_route_analyses_strips_derived_levels(pack_dir):
    """The lightweight route_analyses.json must NOT carry derived_levels."""
    ra_data = json.loads((pack_dir / "route_analyses.json").read_text())
    sounding = ra_data["analyses"][0]["sounding"][_MODEL]
    assert not sounding.get("derived_levels"), "derived_levels must stay stripped on disk"


# ---------------------------------------------------------------------------
# write_sounding_sidecar edge cases
# ---------------------------------------------------------------------------


def test_write_sidecar_returns_count(tmp_path, manifest, cross_sections):
    pd = tmp_path / "pack"
    pd.mkdir()
    ra_full = json.loads(manifest.model_dump_json())
    cs_data = {"cross_sections": [cs.model_dump(mode="json") for cs in cross_sections]}
    count = write_sounding_sidecar(pd, ra_full, cs_data)
    assert count == 1
    assert (pd / SIDECAR_FILENAME).exists()


def test_write_sidecar_empty_writes_nothing(tmp_path):
    pd = tmp_path / "pack"
    pd.mkdir()
    # No analyses -> no profiles -> no file.
    count = write_sounding_sidecar(pd, {"models": [], "analyses": []}, {"cross_sections": []})
    assert count == 0
    assert not (pd / SIDECAR_FILENAME).exists()
