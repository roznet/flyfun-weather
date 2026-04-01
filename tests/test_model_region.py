"""Tests for model region detection and skip logic."""

from __future__ import annotations

import pytest

from weatherbrief.fetch.variables import (
    MODEL_ENDPOINTS,
    ModelEndpoint,
    ModelRegion,
    detect_model_region,
)
from weatherbrief.models import RouteConfig, Waypoint
from weatherbrief.tasks.fetch import _should_skip_for_region


# --- Fixtures ---

def _route(*icaos: str) -> RouteConfig:
    """Build a minimal RouteConfig from ICAO codes."""
    return RouteConfig(
        name="Test",
        waypoints=[Waypoint(icao=icao, name=icao, lat=0, lon=0) for icao in icaos],
    )


# --- detect_model_region ---

class TestDetectModelRegion:
    def test_us_route(self):
        route = _route("KJFK", "KORD")
        assert detect_model_region(route) == ModelRegion.NORTH_AMERICA

    def test_canadian_route(self):
        route = _route("CYUL", "CYYZ")
        assert detect_model_region(route) == ModelRegion.NORTH_AMERICA

    def test_alaska_route(self):
        route = _route("PANC", "PAFA")
        assert detect_model_region(route) == ModelRegion.NORTH_AMERICA

    def test_us_canada_mixed(self):
        """US + Canada = still NORTH_AMERICA."""
        route = _route("KJFK", "CYUL")
        assert detect_model_region(route) == ModelRegion.NORTH_AMERICA

    def test_european_route(self):
        route = _route("EGTK", "LFPB", "LSGS")
        assert detect_model_region(route) == ModelRegion.EUROPE

    def test_mixed_us_europe(self):
        """Mixed continents → GLOBAL (safe fallback, no filtering)."""
        route = _route("KJFK", "EGLL")
        assert detect_model_region(route) == ModelRegion.GLOBAL

    def test_empty_route(self):
        """No waypoints → GLOBAL."""
        assert detect_model_region(None) == ModelRegion.GLOBAL

    def test_lowercase_icao(self):
        """ICAO case-insensitive."""
        route = _route("kjfk", "kord")
        assert detect_model_region(route) == ModelRegion.NORTH_AMERICA


# --- _should_skip_for_region ---

class TestShouldSkipForRegion:
    def _ep(self, region: ModelRegion) -> ModelEndpoint:
        return ModelEndpoint(name="test", base_url="", max_days=7, region=region)

    def test_global_model_never_skipped(self):
        ep = self._ep(ModelRegion.GLOBAL)
        assert not _should_skip_for_region(ep, ModelRegion.NORTH_AMERICA)
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE)
        assert not _should_skip_for_region(ep, ModelRegion.GLOBAL)

    def test_europe_model_skipped_for_na(self):
        ep = self._ep(ModelRegion.EUROPE)
        assert _should_skip_for_region(ep, ModelRegion.NORTH_AMERICA)

    def test_europe_model_kept_for_europe(self):
        ep = self._ep(ModelRegion.EUROPE)
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE)

    def test_na_model_skipped_for_europe(self):
        ep = self._ep(ModelRegion.NORTH_AMERICA)
        assert _should_skip_for_region(ep, ModelRegion.EUROPE)

    def test_na_model_kept_for_na(self):
        ep = self._ep(ModelRegion.NORTH_AMERICA)
        assert not _should_skip_for_region(ep, ModelRegion.NORTH_AMERICA)

    def test_regional_model_not_skipped_for_global_route(self):
        """If route is GLOBAL (mixed), don't skip anything."""
        ep = self._ep(ModelRegion.EUROPE)
        assert not _should_skip_for_region(ep, ModelRegion.GLOBAL)


# --- MODEL_ENDPOINTS region assignments ---

class TestModelEndpointRegions:
    def test_global_models(self):
        for key in ("gfs", "ecmwf"):
            assert MODEL_ENDPOINTS[key].region == ModelRegion.GLOBAL, f"{key} should be GLOBAL"

    def test_european_models(self):
        for key in ("icon", "ukmo", "meteofrance"):
            assert MODEL_ENDPOINTS[key].region == ModelRegion.EUROPE, f"{key} should be EUROPE"

    def test_north_america_models(self):
        assert MODEL_ENDPOINTS["gem"].region == ModelRegion.NORTH_AMERICA
