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

# Real coordinates for the airports used in country-gating tests, so the
# geometry-based detector resolves the right country. Unknown codes (nav-fix
# names, navaids in prefix tests) fall back to (0, 0) — fine for the
# ICAO-string-only checks that use them.
_COORDS: dict[str, tuple[float, float]] = {
    "EGTK": (51.8369, -1.3200),   # Oxford, UK
    "EGLL": (51.4700, -0.4543),   # London Heathrow, UK
    "LFPB": (48.9694, 2.4414),    # Paris Le Bourget, FR
    "EDDK": (50.8659, 7.1427),    # Cologne, DE
    "EDDM": (48.3538, 11.7861),   # Munich, DE
    "LSGS": (46.2196, 7.3268),    # Sion, CH
}


def _route(*icaos: str) -> RouteConfig:
    """Build a minimal RouteConfig from ICAO codes (real coords when known)."""
    return RouteConfig(
        name="Test",
        waypoints=[
            Waypoint(
                icao=icao,
                name=icao,
                lat=_COORDS.get(icao.upper(), (0.0, 0.0))[0],
                lon=_COORDS.get(icao.upper(), (0.0, 0.0))[1],
            )
            for icao in icaos
        ],
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


# --- route_covers_prefixes (airport ICAO-prefix fallback) ---

class TestAirportPrefixFallback:
    """ICAO-prefix matching, used as the fallback when geometry is unavailable.

    Only real 4-letter ICAO airports count — nav fixes and navaids don't.
    """

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


# --- Country gating (geometry-based) ---

class TestCountryGating:
    """Country-specific models gated on the route's geometry (overflight)."""

    def test_skip_meteofrance_on_non_french_route(self):
        """MeteoFrance skipped for Germany-only route."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        route = _route("EDDK", "EDDM")
        assert _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_keep_meteofrance_on_french_route(self):
        """MeteoFrance kept when route lands in France."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        route = _route("EGTK", "LFPB")
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_keep_meteofrance_on_french_overflight(self):
        """The headline gain: a route that overflies France without landing
        there (London → Sion crosses French airspace) keeps MeteoFrance —
        ICAO-prefix matching would have wrongly skipped it."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        route = _route("EGLL", "LSGS")
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

    def test_no_country_requirement_never_skips(self):
        """Models without required_country are unaffected by the country gate."""
        ep = MODEL_ENDPOINTS["icon"]
        route = _route("EDDK", "EDDM")
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, route)

    def test_country_check_skipped_when_no_route(self):
        """Backward compat: no route passed → country check not applied."""
        ep = MODEL_ENDPOINTS["meteofrance"]
        assert not _should_skip_for_region(ep, ModelRegion.EUROPE, None)

    def test_falls_back_to_prefixes_when_detection_fails(self, monkeypatch):
        """If geometry detection raises, fall back to the ICAO-prefix heuristic."""
        import weatherbrief.airports as airports

        def _boom(route, spacing_nm=25.0):
            raise RuntimeError("timezone data unavailable")

        monkeypatch.setattr(airports, "route_countries", _boom)
        ep = MODEL_ENDPOINTS["meteofrance"]  # required_icao_prefixes=["LF"]
        # French airport present → fallback keeps the model.
        assert not _should_skip_for_region(
            ep, ModelRegion.EUROPE, _route("EGTK", "LFPB")
        )
        # No French airport → fallback skips it.
        assert _should_skip_for_region(
            ep, ModelRegion.EUROPE, _route("EDDK", "EDDM")
        )


# --- route_countries (geometry → country) ---

class TestRouteCountries:
    def test_maps_timezones_to_countries(self, monkeypatch):
        """Hermetic: stub the walk + timezone lookup, check the FR/GB mapping."""
        import weatherbrief.airports as airports

        monkeypatch.setattr(
            airports,
            "walk_route",
            lambda route, spacing_nm: [
                (51.47, -0.45, 0.0, None, None),   # London → Europe/London
                (48.97, 2.44, 0.0, None, None),    # Paris → Europe/Paris
            ],
        )
        monkeypatch.setattr(
            airports,
            "get_timezone",
            lambda lat, lon: "Europe/London" if lon < 1 else "Europe/Paris",
        )
        assert airports.route_countries(object()) == {"GB", "FR"}

    def test_ignores_ungated_countries(self, monkeypatch):
        """Timezones outside the gated set (e.g. Germany) are not reported."""
        import weatherbrief.airports as airports

        monkeypatch.setattr(
            airports,
            "walk_route",
            lambda route, spacing_nm: [(50.87, 7.14, 0.0, None, None)],
        )
        monkeypatch.setattr(airports, "get_timezone", lambda lat, lon: "Europe/Berlin")
        assert airports.route_countries(object()) == set()


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

    def test_country_gated_models(self):
        assert MODEL_ENDPOINTS["meteofrance"].required_country == "FR"
        assert MODEL_ENDPOINTS["ukmo"].required_country == "GB"
        # Prefix fallback still declared alongside the country gate.
        assert MODEL_ENDPOINTS["meteofrance"].required_icao_prefixes == ["LF"]
        assert MODEL_ENDPOINTS["ukmo"].required_icao_prefixes == ["EG"]
