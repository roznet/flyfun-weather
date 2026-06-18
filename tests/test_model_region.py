"""Tests for model region detection and skip logic."""

from __future__ import annotations

import pytest

from weatherbrief.fetch.variables import (
    MODEL_ENDPOINTS,
    ModelEndpoint,
    ModelRegion,
    detect_model_region,
    route_covers_prefixes,
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

    def test_intermediate_nav_fix_ignored(self):
        """Only origin/destination count; nav fixes like KONAN/CINDY don't.

        A purely European route (LF→LS) routed via fixes whose names start
        with North-American ICAO prefixes must stay EUROPE.
        """
        route = _route("LFPB", "KONAN", "CINDY", "LSGS")
        assert detect_model_region(route) == ModelRegion.EUROPE

    def test_intermediate_european_fix_on_us_route_ignored(self):
        """Symmetric: a US route via an oddly-named fix stays NORTH_AMERICA."""
        route = _route("KJFK", "EAGLE", "KORD")
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


# --- required_icao_prefixes ---

class TestRequiredIcaoPrefixes:
    """Country-level ICAO prefix filtering (e.g. MeteoFrance → LF)."""

    def test_route_covers_french_prefix(self):
        route = _route("EGTK", "LFPB", "LSGS")
        assert route_covers_prefixes(route, ["LF"])

    def test_route_does_not_cover_french_prefix(self):
        route = _route("EGTK", "EDDK", "LSGS")
        assert not route_covers_prefixes(route, ["LF"])

    def test_route_covers_uk_prefix(self):
        route = _route("EGLL", "LFPB")
        assert route_covers_prefixes(route, ["EG"])

    def test_route_does_not_cover_uk_prefix(self):
        route = _route("LFPB", "EDDK")
        assert not route_covers_prefixes(route, ["EG"])

    def test_empty_route(self):
        assert not route_covers_prefixes(None, ["LF"])

    def test_case_insensitive(self):
        route = _route("lfpb", "eddk")
        assert route_covers_prefixes(route, ["LF"])

    def test_five_letter_fix_does_not_cover_prefix(self):
        """A nav fix like LFXYZ must not count as a French airport."""
        route = _route("EGLL", "LFXYZ", "EDDK")
        assert not route_covers_prefixes(route, ["LF"])

    def test_intermediate_french_airport_covers_prefix(self):
        """A real 4-letter French airport en route still counts."""
        route = _route("EGLL", "LFPB", "EDDK")
        assert route_covers_prefixes(route, ["LF"])

    def test_navaid_does_not_cover_prefix(self):
        """A 2-3 letter navaid starting with the prefix must not count."""
        route = _route("EGLL", "LFA", "EDDK")
        assert not route_covers_prefixes(route, ["LF"])

    def test_skip_meteofrance_on_non_french_route(self):
        """MeteoFrance skipped for Germany-only route."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        route = _route("EDDK", "EDDM")
        assert _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_keep_meteofrance_on_french_route(self):
        """MeteoFrance kept when route touches France."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        route = _route("EGTK", "LFPB")
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_skip_ukmo_on_non_uk_route(self):
        """UKMO skipped for France-Germany route."""
        ep = MODEL_ENDPOINTS["ukmo"]
        route = _route("LFPB", "EDDK")
        assert _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_keep_ukmo_on_uk_route(self):
        """UKMO kept when route touches UK."""
        ep = MODEL_ENDPOINTS["ukmo"]
        route = _route("EGLL", "LFPB")
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_no_prefix_requirement_never_skips(self):
        """Models without required_icao_prefixes are unaffected."""
        ep = MODEL_ENDPOINTS["icon"]
        route = _route("EDDK", "EDDM")
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_prefix_check_skipped_when_no_route(self):
        """Backward compat: no route passed → prefix check not applied."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, None)


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
