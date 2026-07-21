"""Tests for the ICON-D2 in-place upgrade of the icon slot (issue #456).

ICON-D2 (2.2 km, convection-permitting) serves the ``icon`` model slot in place
of ICON-EU when the whole route fits the D2 domain AND the flight window is
within a D2 run's 48h horizon; otherwise ICON-EU exactly as before. The two
variants share the entire download/decode path via :class:`IconVariant`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_D2,
    ICON_EU,
    compute_icon_eu_flight_window_hours,
    icon_cloud_diag_cache_key,
    icon_eu_file_url,
    icon_eu_model_level_max_hour,
    icon_eu_previous_step,
    icon_eu_single_level_url,
    icon_eu_window_out_of_range,
    route_in_icon_eu_domain,
)
from weatherbrief.models import RoutePoint


def _rp(lat: float, lon: float, nm: float = 0.0) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=nm)


# ---------------------------------------------------------------------------
# URL builders — D2 filename quirks vs ICON-EU
# ---------------------------------------------------------------------------


class TestD2UrlBuilders:
    def test_model_level_lowercase_var_and_germany_grid(self):
        url = icon_eu_file_url("20260720", 0, 6, 60, "t", ICON_D2)
        assert url == (
            "https://opendata.dwd.de/weather/nwp/icon-d2/grib/00/t/"
            "icon-d2_germany_regular-lat-lon_model-level_"
            "2026072000_006_60_t.grib2.bz2"
        )

    def test_single_level_carries_2d_segment_and_lowercase_var(self):
        url = icon_eu_single_level_url("20260720", 0, 6, "ceiling", ICON_D2)
        assert url == (
            "https://opendata.dwd.de/weather/nwp/icon-d2/grib/00/ceiling/"
            "icon-d2_germany_regular-lat-lon_single-level_"
            "2026072000_006_2d_ceiling.grib2.bz2"
        )

    def test_eu_urls_unchanged_by_default(self):
        # Back-compat: no variant arg → ICON-EU exactly as before.
        assert icon_eu_file_url("20260221", 0, 6, 74, "t") == (
            "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/t/"
            "icon-eu_europe_regular-lat-lon_model-level_"
            "2026022100_006_74_T.grib2.bz2"
        )
        assert icon_eu_single_level_url("20260221", 0, 6, "ceiling") == (
            "https://opendata.dwd.de/weather/nwp/icon-eu/grib/00/ceiling/"
            "icon-eu_europe_regular-lat-lon_single-level_"
            "2026022100_006_CEILING.grib2.bz2"
        )


# ---------------------------------------------------------------------------
# Domain gate — all-or-nothing over the central-European D2 box
# ---------------------------------------------------------------------------


class TestD2Domain:
    def test_germany_internal_route_inside(self):
        # EDDM (Munich) → EDDH (Hamburg), both well inside the D2 box.
        points = [_rp(48.35, 11.79), _rp(51.0, 12.5, 150), _rp(53.63, 9.99, 300)]
        assert route_in_icon_eu_domain(points, ICON_D2) is True

    def test_one_point_outside_fails_all_or_nothing(self):
        # A single Spanish endpoint drops the whole route out of D2.
        points = [_rp(48.35, 11.79), _rp(41.3, 2.08, 400)]  # ...→ Barcelona
        assert route_in_icon_eu_domain(points, ICON_D2) is False
        # Same route is still inside the wider ICON-EU domain.
        assert route_in_icon_eu_domain(points, ICON_EU) is True

    def test_scotland_outside_d2_but_inside_eu(self):
        # Glasgow (4.25°W) is west of the D2 edge (-3.94°E); note that eastern
        # Scotland (e.g. Edinburgh, 3.19°W) does sit inside the box.
        points = [_rp(52.0, 0.0), _rp(55.86, -4.25, 300)]  # ...→ Glasgow
        assert route_in_icon_eu_domain(points, ICON_D2) is False
        assert route_in_icon_eu_domain(points, ICON_EU) is True

    def test_brittany_edge_west_outside_d2(self):
        # Brittany sits west of the D2 western edge (-3.94°E).
        points = [_rp(48.35, 11.79), _rp(48.45, -4.42, 400)]  # ...→ Brest
        assert route_in_icon_eu_domain(points, ICON_D2) is False


# ---------------------------------------------------------------------------
# Horizon / temporal grid — D2 is hourly to 48h, all cycles
# ---------------------------------------------------------------------------


class TestD2HorizonAndGrid:
    @pytest.mark.parametrize("cycle", [0, 3, 6, 9, 12, 15, 18, 21])
    def test_all_cycles_reach_48h(self, cycle):
        assert icon_eu_model_level_max_hour(cycle, ICON_D2) == 48

    def test_eu_horizon_unchanged(self):
        assert icon_eu_model_level_max_hour(0, ICON_EU) == 120
        assert icon_eu_model_level_max_hour(3, ICON_EU) == 30

    def test_previous_step_is_hourly(self):
        assert icon_eu_previous_step(1, ICON_D2) == 0
        assert icon_eu_previous_step(48, ICON_D2) == 47
        assert icon_eu_previous_step(0, ICON_D2) is None

    def test_window_hours_hourly(self):
        # init 12z, dep 14z, 2h flight → hourly f2..f4.
        dep = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
        hours = compute_icon_eu_flight_window_hours("20260720", 12, dep, 2.0, ICON_D2)
        assert hours == [2, 3, 4]

    def test_level_slice_and_variant_config(self):
        # Level slice validated against DWD's HHL field (2026-07-21): D2 level
        # 16 ≈ 9,460 m ≈ FL310 (≈ ICON-EU's level-35 300 hPa top); level 65 =
        # surface. D2 level numbers are NOT comparable to EU's.
        assert (ICON_D2.level_min, ICON_D2.level_max) == (16, 65)
        assert ICON_D2.slug == "icon-d2"
        assert ICON_D2.source_key == "icon_d2:dwd"
        assert icon_cloud_diag_cache_key(ICON_D2) == "ICON_D2_CLOUD_DIAG_V2"
        assert icon_cloud_diag_cache_key(ICON_EU) == "ICON_EU_CLOUD_DIAG_V2"

    def test_d2_diag_list_drops_parameterized_convection_fields(self):
        # D2 has no deep-convection scheme: hbas_con/htop_con 404 on the feed
        # and rain_con is near-zero in explicit storms (verified live
        # 2026-07-21). They must be absent so downstream fields stay None
        # (missing-data semantics) rather than misleadingly quiet. #462 adds
        # the convection-permitting replacements.
        for var in ("hbas_con", "htop_con", "rain_con"):
            assert var not in ICON_D2.cloud_diag_variables
            assert var in ICON_EU.cloud_diag_variables
        # The shared non-convective diagnostics stay identical.
        for var in ("ceiling", "clcl", "clcm", "clch", "clct", "cape_ml", "cin_ml"):
            assert var in ICON_D2.cloud_diag_variables
            assert var in ICON_EU.cloud_diag_variables


# ---------------------------------------------------------------------------
# Out-of-range classification against the selected model's horizon
# ---------------------------------------------------------------------------


class TestD2OutOfRange:
    def test_flight_beyond_48h_is_out_of_range_for_d2(self):
        ref = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 7, 23, 6, 0, tzinfo=timezone.utc)  # ~66h out
        assert icon_eu_window_out_of_range(dep, 1.0, ref, ICON_D2) is True

    def test_flight_within_48h_in_range_for_d2(self):
        ref = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)  # 24h out
        assert icon_eu_window_out_of_range(dep, 1.0, ref, ICON_D2) is False

    def test_same_far_flight_still_in_range_for_eu(self):
        ref = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        dep = datetime(2026, 7, 23, 6, 0, tzinfo=timezone.utc)  # ~66h out < 120h
        assert icon_eu_window_out_of_range(dep, 1.0, ref, ICON_EU) is False


# ---------------------------------------------------------------------------
# Variant selection in _prepare_icon_eu (the gate + run interaction)
# ---------------------------------------------------------------------------


class TestPrepareIconVariantSelection:
    """`_prepare_icon_eu` should pick D2 only when the domain + run gate passes."""

    def _icon_cross_sections(self):
        from weatherbrief.models import ModelSource, RouteCrossSection

        cs = RouteCrossSection(
            model=ModelSource.ICON,
            route_points=[],
            fetched_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            point_forecasts=[],
        )
        return [cs]

    def _run_finder(self, d2_run, eu_run):
        """Return a fake find_latest_icon_eu_run keyed on the variant kwarg."""
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2 as _D2

        def _fake(target_time, session=None, as_of_time=None,
                  cover_until=None, variant=None):
            return d2_run if variant is _D2 else eu_run

        return _fake

    def test_picks_d2_when_domain_and_run_ok(self, tmp_path):
        from weatherbrief.fetch.grib import _prepare_icon_eu

        route = [_rp(48.35, 11.79), _rp(52.52, 13.4, 300)]  # Munich→Berlin
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ), patch(
            # Keep the #462 validity-mask gate hermetic (it would otherwise
            # try to download a D2 probe file to build the bitmap mask).
            "weatherbrief.fetch.grib._d2_corridor_mask_ok", return_value=True,
        ):
            ctx, skip = _prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert skip is None
        assert ctx is not None
        assert ctx.variant is ICON_D2
        assert ctx.levels == list(range(16, 66))

    def test_falls_back_to_eu_when_route_outside_d2(self, tmp_path):
        from weatherbrief.fetch.grib import _prepare_icon_eu

        route = [_rp(48.35, 11.79), _rp(41.3, 2.08, 400)]  # ...→ Barcelona
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ):
            ctx, skip = _prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert skip is None
        assert ctx is not None
        assert ctx.variant is ICON_EU

    def test_falls_back_to_eu_when_d2_run_uncovered(self, tmp_path):
        from weatherbrief.fetch.grib import _prepare_icon_eu

        # Route fits the D2 box but the D2 run-finder returns None (window past
        # 48h); ICON-EU still covers it → variant must be EU.
        route = [_rp(48.35, 11.79), _rp(52.52, 13.4, 300)]
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=None, eu_run=("20260720", 12)),
        ):
            ctx, skip = _prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert skip is None
        assert ctx is not None
        assert ctx.variant is ICON_EU

    def test_force_variant_skips_d2_gate(self, tmp_path):
        from weatherbrief.fetch.grib import _prepare_icon_eu

        route = [_rp(48.35, 11.79), _rp(52.52, 13.4, 300)]  # inside D2
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ):
            ctx, skip = _prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
                force_variant=ICON_EU,
            )
        assert ctx is not None
        assert ctx.variant is ICON_EU


# ---------------------------------------------------------------------------
# Cache TTL + freshness registry wiring
# ---------------------------------------------------------------------------


def test_icon_d2_cache_ttl_registered():
    from weatherbrief.fetch.grib.cache import MODEL_TTL_SECONDS

    assert MODEL_TTL_SECONDS["icon-d2"] == 6 * 3600


def test_icon_d2_source_registry_entry():
    from weatherbrief.fetch.freshness import registry

    cfg = registry.SOURCE_REGISTRY["icon_d2:dwd"]
    assert cfg.readiness_check == "icon_d2_dwd"
    assert cfg.model_label == "ICON-D2"
    assert cfg.provider_label == "DWD"
    assert cfg.role == "primary-sounding"
    assert cfg.cycles == (0, 3, 6, 9, 12, 15, 18, 21)
    # Uniform 48h horizon on every cycle.
    for h in cfg.cycles:
        assert registry.run_horizon("icon_d2:dwd", _utc(2026, 7, 20, h)) == timedelta(hours=48)


def test_icon_d2_readiness_dispatch_registered():
    from weatherbrief.fetch.freshness.sources import _DISPATCH, _check_icon_d2_dwd

    assert _DISPATCH["icon_d2_dwd"] is _check_icon_d2_dwd


def _utc(y, mo, d, h=0):
    return datetime(y, mo, d, h, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Download-worker default (#469 "also worth doing")
# ---------------------------------------------------------------------------


def test_download_workers_default_is_16():
    from weatherbrief.fetch.grib.icon_eu_fetch import MAX_DOWNLOAD_WORKERS
    assert MAX_DOWNLOAD_WORKERS == 16


def test_download_workers_env_override(monkeypatch):
    from weatherbrief.fetch.grib.icon_eu_fetch import _default_download_workers

    monkeypatch.setenv("MAX_DOWNLOAD_WORKERS", "24")
    assert _default_download_workers() == 24
    monkeypatch.setenv("MAX_DOWNLOAD_WORKERS", "garbage")
    assert _default_download_workers() == 16
    monkeypatch.setenv("MAX_DOWNLOAD_WORKERS", "0")
    assert _default_download_workers() == 1  # clamped to >= 1
    monkeypatch.delenv("MAX_DOWNLOAD_WORKERS")
    assert _default_download_workers() == 16
