"""Tests for the HRRR fetch module (#457): S3 URL builders, run selection
across hourly cycles, Lambert domain gate, and flight-window hours."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
import requests

from weatherbrief.fetch.grib.hrrr_fetch import (
    HRRR_EXTENDED_CYCLES,
    HRRR_GRID,
    HRRR_HORIZON_LONG_H,
    HRRR_HORIZON_SHORT_H,
    find_latest_hrrr_run,
    hrrr_grib2_url,
    hrrr_idx_url,
    hrrr_window_hours,
    route_in_hrrr_domain,
)
from weatherbrief.models import RoutePoint


def _rp(lat: float, lon: float) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=0.0)


# --- URL builder tests ---


def test_hrrr_grib2_url_flat_conus_layout():
    url = hrrr_grib2_url("20260731", 6, 12)
    assert url == (
        "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20260731/conus/"
        "hrrr.t06z.wrfprsf12.grib2"
    )


def test_hrrr_grib2_url_two_digit_fhour():
    """HRRR uses 2-digit forecast hours (GFS uses 3) and no atmos/ subdir."""
    url = hrrr_grib2_url("20260731", 0, 5)
    assert "wrfprsf05.grib2" in url
    assert "f005" not in url
    assert "/atmos/" not in url


def test_hrrr_idx_url_appends_idx():
    assert hrrr_idx_url("20260731", 6, 12) == (
        hrrr_grib2_url("20260731", 6, 12) + ".idx"
    )


# --- Grid constants (verified against the real bucket 2026-07-31) ---


def test_hrrr_grid_constants():
    assert HRRR_GRID.nx == 1799
    assert HRRR_GRID.ny == 1059
    assert HRRR_GRID.dx == 3000.0
    assert HRRR_GRID.dy == 3000.0
    assert HRRR_GRID.lad == 38.5
    assert HRRR_GRID.lov == 262.5
    assert HRRR_EXTENDED_CYCLES == frozenset({0, 6, 12, 18})
    assert HRRR_HORIZON_LONG_H == 48
    assert HRRR_HORIZON_SHORT_H == 18


# --- Run selection tests (fake HEAD keyed on cycle/fhour) ---

_URL_RE = re.compile(
    r"hrrr\.(\d{8})/conus/hrrr\.t(\d{2})z\.wrfprsf(\d{2})\.grib2\.idx$"
)


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeSession:
    """Session stand-in: HEAD returns 200 only for (date, cycle, fhour) keys
    in ``available``; keys in ``broken`` raise like a network error."""

    def __init__(self, available=(), broken=()):
        self.available = set(available)
        self.broken = set(broken)
        self.headed_urls: list[str] = []

    def head(self, url, timeout=None):
        self.headed_urls.append(url)
        m = _URL_RE.search(url)
        assert m, f"unexpected probe URL: {url}"
        key = (m.group(1), int(m.group(2)), int(m.group(3)))
        if key in self.broken:
            raise requests.ConnectionError("simulated network failure")
        return _FakeResponse(200 if key in self.available else 404)


AS_OF = datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)
TARGET = datetime(2026, 7, 31, 18, 0, tzinfo=timezone.utc)


def test_find_latest_prefers_freshest_covering_cycle():
    # At 15:00 UTC the freshest publishable cycle is 14z (1h delay); its
    # 18h horizon covers 18:00 easily. 13z also covers but must lose.
    sess = FakeSession(available={("20260731", 14, 4), ("20260731", 13, 5)})
    result = find_latest_hrrr_run(TARGET, as_of_time=AS_OF, session=sess)
    assert result == ("20260731", 14)
    assert len(sess.headed_urls) == 1  # freshest hit stops the search


def test_find_latest_skips_too_early_cycles():
    # At 15:30 the 15z cycle is only 0.5h old (< 1h publish delay): it must
    # not even be probed, let alone selected.
    sess = FakeSession(available={("20260731", 15, 3), ("20260731", 14, 4)})
    as_of = datetime(2026, 7, 31, 15, 30, tzinfo=timezone.utc)
    result = find_latest_hrrr_run(TARGET, as_of_time=as_of, session=sess)
    assert result == ("20260731", 14)
    assert all(".t15z." not in url for url in sess.headed_urls)


def test_find_latest_walks_back_a_day():
    # Nothing usable on day 0: only yesterday's extended 12z cycle (48h
    # horizon) still covers an 18:00 target — f30 is its last-needed hour.
    sess = FakeSession(available={("20260730", 12, 30)})
    result = find_latest_hrrr_run(TARGET, as_of_time=AS_OF, session=sess)
    assert result == ("20260730", 12)


def test_find_latest_probes_last_needed_not_f000():
    # HRRR files appear progressively; f000 may exist while the last-needed
    # fhour does not (and vice versa in this fake). Only f04 is "published".
    sess = FakeSession(available={("20260731", 14, 4), ("20260731", 14, 0)})
    sess2 = FakeSession(available={("20260731", 14, 0)})  # f000 only
    result = find_latest_hrrr_run(TARGET, as_of_time=AS_OF, session=sess)
    assert result == ("20260731", 14)
    assert sess.headed_urls[0].endswith("wrfprsf04.grib2.idx")
    # A run probing f000 would wrongly accept the f000-only session.
    assert find_latest_hrrr_run(TARGET, as_of_time=AS_OF, session=sess2) is None


def test_find_latest_cover_until_drives_last_needed():
    # cover_until past target pushes the probe to a later fhour: 14z needs
    # f06 to cover 20:00, so f04 alone is not enough.
    cover_until = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    sess = FakeSession(available={("20260731", 14, 4)})
    result = find_latest_hrrr_run(
        TARGET, cover_until=cover_until, as_of_time=AS_OF, session=sess,
    )
    assert result is None  # f04 alone must not satisfy a 20:00 cover_until
    sess2 = FakeSession(available={("20260731", 14, 6)})
    result2 = find_latest_hrrr_run(
        TARGET, cover_until=cover_until, as_of_time=AS_OF, session=sess2,
    )
    assert result2 == ("20260731", 14)


def test_find_latest_skips_cycles_whose_horizon_does_not_cover():
    # need_until 20:00: 01z has an 18h horizon (covers to 19:00) and must be
    # skipped; 00z is an extended cycle (48h) and covers — probe f20.
    sess = FakeSession(available={("20260731", 0, 20)})
    cover_until = datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)
    result = find_latest_hrrr_run(
        TARGET, cover_until=cover_until, as_of_time=AS_OF, session=sess,
    )
    assert result == ("20260731", 0)
    assert all(".t01z." not in url for url in sess.headed_urls)


def test_find_latest_returns_none_when_nothing_covers():
    sess = FakeSession(available=set())
    assert find_latest_hrrr_run(TARGET, as_of_time=AS_OF, session=sess) is None


def test_find_latest_tolerates_network_errors():
    sess = FakeSession(broken={("20260731", 14, 4)}, available={("20260731", 13, 5)})
    result = find_latest_hrrr_run(TARGET, as_of_time=AS_OF, session=sess)
    assert result == ("20260731", 13)


# --- Domain gate tests (Lambert projection, no bbox approximation) ---


def test_domain_gate_conus_point_in():
    assert route_in_hrrr_domain([_rp(39.856, -104.676)]) is True  # KDEN


def test_domain_gate_conus_route_in():
    assert route_in_hrrr_domain([
        _rp(39.856, -104.676),  # KDEN
        _rp(41.979, -87.908),   # KORD
    ]) is True


def test_domain_gate_brest_out():
    assert route_in_hrrr_domain([_rp(48.447, -4.418)]) is False  # BREST, France


def test_domain_gate_hawaii_out():
    assert route_in_hrrr_domain([_rp(21.32, -157.92)]) is False  # PHNL


def test_domain_gate_all_or_nothing():
    """One point outside the grid rejects the whole route."""
    assert route_in_hrrr_domain([
        _rp(39.856, -104.676),  # KDEN — in
        _rp(48.447, -4.418),    # BREST — out
    ]) is False


def test_domain_gate_edge_points():
    """Points near the grid edge: a bbox gate would get these wrong."""
    # Key West: outside the CONUS landmass bbox but inside the HRRR grid.
    assert route_in_hrrr_domain([_rp(24.55, -81.78)]) is True
    # Just outside the SW grid corner (verified against the projection).
    assert route_in_hrrr_domain([_rp(20.9788, -122.8152)]) is False


# --- Flight window hours ---


def test_hrrr_window_hours_basic_hourly():
    # 12z init, depart 15:00 for 2h → fhours 3,4,5 on the 1-hourly grid.
    dep = datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)
    assert hrrr_window_hours("20260731", 12, dep, 2.0) == [3, 4, 5]


def test_hrrr_window_hours_non_synoptic_cycle():
    # HRRR cycles run every hour, unlike GFS 00/06/12/18.
    dep = datetime(2026, 7, 31, 10, 0, tzinfo=timezone.utc)
    assert hrrr_window_hours("20260731", 7, dep, 2.0) == [3, 4, 5]


def test_hrrr_window_hours_round_snap_and_floor_inclusion():
    # Depart 15:45: deltas 3.75..6.75 round to 4..7; the floor hour (3) is
    # added so a 15:xx departure still has f03 coverage.
    dep = datetime(2026, 7, 31, 15, 45, tzinfo=timezone.utc)
    assert hrrr_window_hours("20260731", 12, dep, 2.0) == [3, 4, 5, 6, 7]


def test_hrrr_window_hours_clamps_at_48():
    dep = datetime(2026, 8, 1, 23, 0, tzinfo=timezone.utc)
    assert hrrr_window_hours("20260731", 0, dep, 2.0) == [47, 48]
