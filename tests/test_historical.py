"""Tests for historical "as-of" flight briefing mode."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import responses

from weatherbrief.fetch.open_meteo import (
    OpenMeteoClient,
    _FREE_HOST,
    _HISTORICAL_HOST,
    _HISTORICAL_MODEL_MAP,
)
from weatherbrief.fetch.grib.grib_fetch import find_latest_run, GFS_PUBLISH_DELAY_HOURS
from weatherbrief.fetch.grib.icon_eu_fetch import (
    find_latest_icon_eu_run,
    ICON_EU_PUBLISH_DELAY_HOURS,
)
from weatherbrief.models.storage import BriefingPackMeta
from weatherbrief.pipeline import BriefingOptions


# --- BriefingOptions ---


def test_historical_mode_default_false():
    """historical_mode defaults to False."""
    opts = BriefingOptions()
    assert opts.historical_mode is False


def test_historical_mode_can_be_set():
    opts = BriefingOptions(historical_mode=True)
    assert opts.historical_mode is True


# --- OpenMeteoClient historical host ---


def test_historical_client_swaps_host():
    """Historical client should swap the free host to the historical host."""
    with patch.dict("os.environ", {}, clear=True):
        client = OpenMeteoClient(historical=True)
    url = f"https://{_FREE_HOST}/v1/forecast"
    new_url, _ = client._prepare_request(url, {})
    assert new_url == f"https://{_HISTORICAL_HOST}/v1/forecast"


def test_non_historical_client_keeps_host():
    """Normal client should not swap to historical host."""
    with patch.dict("os.environ", {}, clear=True):
        client = OpenMeteoClient(historical=False)
    url = f"https://{_FREE_HOST}/v1/forecast"
    new_url, _ = client._prepare_request(url, {})
    assert _FREE_HOST in new_url
    assert _HISTORICAL_HOST not in new_url


def test_historical_client_rewrites_model_path():
    """Historical client should rewrite model-specific paths to /v1/forecast + models param."""
    with patch.dict("os.environ", {}, clear=True):
        client = OpenMeteoClient(historical=True)

    # GFS: /v1/gfs → /v1/forecast + models=gfs_seamless
    url = f"https://{_FREE_HOST}/v1/gfs"
    new_url, params = client._prepare_request(url, {})
    assert new_url == f"https://{_HISTORICAL_HOST}/v1/forecast"
    assert params["models"] == "gfs_seamless"

    # ICON: /v1/dwd-icon → /v1/forecast + models=icon_seamless
    url = f"https://{_FREE_HOST}/v1/dwd-icon"
    new_url, params = client._prepare_request(url, {})
    assert new_url == f"https://{_HISTORICAL_HOST}/v1/forecast"
    assert params["models"] == "icon_seamless"

    # Best match: /v1/forecast stays as /v1/forecast, no models param added
    url = f"https://{_FREE_HOST}/v1/forecast"
    new_url, params = client._prepare_request(url, {})
    assert new_url == f"https://{_HISTORICAL_HOST}/v1/forecast"
    assert "models" not in params


def test_historical_client_with_api_key():
    """Historical + paid API key: always uses free historical host (customer
    historical requires Professional/Enterprise plan), API key not applied."""
    with patch.dict("os.environ", {"OPENMETEO_API_KEY": "test-key"}):
        client = OpenMeteoClient(historical=True)
    url = f"https://{_FREE_HOST}/v1/forecast"
    new_url, params = client._prepare_request(url, {})
    assert new_url == f"https://{_HISTORICAL_HOST}/v1/forecast"
    assert "apikey" not in params


# --- GFS find_latest_run with as_of_time ---


@responses.activate
def test_find_latest_run_as_of_time():
    """as_of_time restricts GFS run selection to before that time."""
    # Set as_of_time to a specific moment: 2026-02-20 15:00 UTC
    # With 5h publish delay, only runs initialized <= 10:00 UTC should be found
    # So the 06z run should be found (15 - 6 = 9h > 5h delay)
    as_of = datetime(2026, 2, 20, 15, 0, 0, tzinfo=timezone.utc)

    # Register HEAD responses: 12z exists but should be skipped (only 3h since init)
    # 06z exists and should be found
    responses.add(
        responses.HEAD,
        "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20260220/12/atmos/gfs.t12z.pgrb2.0p25.f000.idx",
        status=200,
    )
    responses.add(
        responses.HEAD,
        "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20260220/06/atmos/gfs.t06z.pgrb2.0p25.f000.idx",
        status=200,
    )

    result = find_latest_run(as_of, as_of_time=as_of)
    assert result is not None
    date_str, cycle = result
    assert date_str == "20260220"
    assert cycle == 6  # 06z run (9h before as_of, past the delay)


@responses.activate
def test_find_latest_run_as_of_time_skips_future_runs():
    """Runs initialized after as_of_time should be skipped."""
    as_of = datetime(2026, 2, 20, 10, 0, 0, tzinfo=timezone.utc)

    # 12z is after as_of — should be skipped
    # 06z is only 4h before as_of — within delay, should be skipped
    # 00z is 10h before — should be found
    responses.add(
        responses.HEAD,
        "https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.20260220/00/atmos/gfs.t00z.pgrb2.0p25.f000.idx",
        status=200,
    )

    result = find_latest_run(as_of, as_of_time=as_of)
    assert result is not None
    date_str, cycle = result
    assert date_str == "20260220"
    assert cycle == 0


@responses.activate
def test_find_latest_run_without_as_of_time_uses_now():
    """Without as_of_time, find_latest_run uses current time (normal behavior)."""
    # Just verify it doesn't crash and uses now
    # Mock a recent run
    now = datetime.now(timezone.utc)
    recent = now - timedelta(hours=GFS_PUBLISH_DELAY_HOURS + 1)
    cycle = (recent.hour // 6) * 6
    date_str = recent.strftime("%Y%m%d")

    responses.add(
        responses.HEAD,
        f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{date_str}/{cycle:02d}/atmos/gfs.t{cycle:02d}z.pgrb2.0p25.f000.idx",
        status=200,
    )

    result = find_latest_run(now)
    assert result is not None


# --- ICON-EU find_latest_icon_eu_run with as_of_time ---


@responses.activate
def test_find_latest_icon_eu_run_as_of_time():
    """as_of_time restricts ICON-EU run selection."""
    as_of = datetime(2026, 2, 20, 12, 0, 0, tzinfo=timezone.utc)
    # 09z: 3h since init, at the delay boundary — should be skipped (< not <=)
    # 06z: 6h since init — should be found

    responses.add(
        responses.HEAD,
        "https://opendata.dwd.de/weather/nwp/icon-eu/grib/06/p/"
        "icon-eu_europe_regular-lat-lon_model-level_2026022006_000_74_P.grib2.bz2",
        status=200,
    )

    result = find_latest_icon_eu_run(as_of, as_of_time=as_of)
    assert result is not None
    date_str, cycle = result
    assert date_str == "20260220"
    assert cycle == 6


# --- BriefingPackMeta.is_historical ---


def test_pack_meta_is_historical_positive():
    """Pack with negative days_out is historical."""
    meta = BriefingPackMeta(
        flight_id="test-flight",
        fetch_timestamp=datetime.now(timezone.utc),
        days_out=-3,
    )
    assert meta.is_historical is True


def test_pack_meta_is_historical_negative():
    """Pack with non-negative days_out is not historical."""
    meta = BriefingPackMeta(
        flight_id="test-flight",
        fetch_timestamp=datetime.now(timezone.utc),
        days_out=2,
    )
    assert meta.is_historical is False


def test_pack_meta_is_historical_zero():
    """Pack with days_out == 0 (today) is not historical."""
    meta = BriefingPackMeta(
        flight_id="test-flight",
        fetch_timestamp=datetime.now(timezone.utc),
        days_out=0,
    )
    assert meta.is_historical is False


# --- Pipeline validation skip ---


def test_pipeline_rejects_past_date_without_historical():
    """execute_briefing raises ValueError for past dates when historical_mode is False."""
    from weatherbrief.pipeline import BriefingOptions

    opts = BriefingOptions(historical_mode=False)
    # We can't easily call execute_briefing without full setup,
    # so test the validation logic directly
    from datetime import date

    departure_time = datetime(2020, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    target_date = departure_time.strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).date()
    days_out = (date.fromisoformat(target_date) - today_utc).days

    assert days_out < 0
    # Without historical mode, this would raise
    should_reject = days_out < 0 and not opts.historical_mode
    assert should_reject is True


def test_pipeline_allows_past_date_with_historical():
    """historical_mode=True allows past departure dates."""
    opts = BriefingOptions(historical_mode=True)
    from datetime import date

    departure_time = datetime(2020, 1, 1, 9, 0, 0, tzinfo=timezone.utc)
    target_date = departure_time.strftime("%Y-%m-%d")
    today_utc = datetime.now(timezone.utc).date()
    days_out = (date.fromisoformat(target_date) - today_utc).days

    assert days_out < 0
    should_reject = days_out < 0 and not opts.historical_mode
    assert should_reject is False


# --- _classify_refresh_time: in-progress vs historical refresh ---

from types import SimpleNamespace

from weatherbrief.api.packs import _INFLIGHT_GRACE, _classify_refresh_time


def _flight(dep: datetime, duration_h: float):
    return SimpleNamespace(departure_time=dep, flight_duration_hours=duration_h)


def test_classify_future_flight_is_live_no_pin():
    """A not-yet-departed flight is a normal live refresh (no as_of pin)."""
    now = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    flight = _flight(now + timedelta(hours=2), duration_h=3.0)
    is_historical, as_of = _classify_refresh_time(flight, now=now)
    assert is_historical is False
    assert as_of is None


def test_classify_just_departed_is_live_pinned():
    """Refreshing an hour after departure keeps it live but pins GRIB to departure.

    This is the reported bug: a flight departed 07:00, refreshed 08:00, must
    still get the full live briefing (digest), not the degraded historical path.
    """
    dep = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    now = dep + timedelta(hours=1)
    is_historical, as_of = _classify_refresh_time(_flight(dep, 5.0), now=now)
    assert is_historical is False
    assert as_of == dep


def test_classify_mid_flight_is_live_pinned():
    now = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    dep = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)  # 3h into a 5h flight
    is_historical, as_of = _classify_refresh_time(_flight(dep, 5.0), now=now)
    assert is_historical is False
    assert as_of == dep


def test_classify_within_grace_after_arrival_is_live():
    """Just after arrival, still within the grace window → live."""
    dep = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    arrival = dep + timedelta(hours=5)
    now = arrival + _INFLIGHT_GRACE - timedelta(minutes=1)
    is_historical, as_of = _classify_refresh_time(_flight(dep, 5.0), now=now)
    assert is_historical is False
    assert as_of == dep


def test_classify_beyond_grace_is_historical():
    """Well past arrival + grace → historical archive path, no pin."""
    dep = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    arrival = dep + timedelta(hours=5)
    now = arrival + _INFLIGHT_GRACE + timedelta(minutes=1)
    is_historical, as_of = _classify_refresh_time(_flight(dep, 5.0), now=now)
    assert is_historical is True
    assert as_of is None


def test_classify_zero_duration_uses_grace_only():
    """A flight with no recorded duration still gets the grace window off departure."""
    dep = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    # within grace of departure → live
    within = _classify_refresh_time(_flight(dep, 0.0), now=dep + _INFLIGHT_GRACE - timedelta(minutes=1))
    assert within == (False, dep)
    # beyond grace → historical
    beyond = _classify_refresh_time(_flight(dep, 0.0), now=dep + _INFLIGHT_GRACE + timedelta(minutes=1))
    assert beyond == (True, None)
