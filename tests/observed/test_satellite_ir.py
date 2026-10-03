"""EUMETView IR proxy (#652): time dimension, tile fetch, error handling."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import responses

from weatherbrief.observed import satellite_ir


@pytest.fixture(autouse=True)
def _fresh_caches():
    satellite_ir._reset_caches_for_tests()
    yield
    satellite_ir._reset_caches_for_tests()


def _caps(dimension: str) -> str:
    return (
        '<WMS_Capabilities><Capability><Layer><Layer><Name>ir105_hrfi</Name>'
        f'<Dimension name="time" default="x" units="ISO8601" nearestValue="1">{dimension}</Dimension>'
        "</Layer></Layer></Capability></WMS_Capabilities>"
    )


def test_interval_dimension_gives_the_newest_cycles_first():
    times = satellite_ir.parse_time_dimension(
        "2024-09-23T00:00:00.000Z/2026-10-03T16:00:00.000Z/PT10M", keep=3
    )
    assert times == [
        datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 3, 15, 50, tzinfo=timezone.utc),
        datetime(2026, 10, 3, 15, 40, tzinfo=timezone.utc),
    ]


def test_list_dimension_is_parsed_too():
    times = satellite_ir.parse_time_dimension(
        "2026-10-03T15:40:00Z,2026-10-03T16:00:00Z,2026-10-03T15:50:00Z"
    )
    assert times[0] == datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    assert len(times) == 3


def test_retention_matches_the_radar():
    """A loop (#653) steps radar and IR together over the same 3 h."""
    from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, SOURCE_SPECS

    span = satellite_ir.CADENCE * satellite_ir.RETAINED_FRAMES
    assert span == SOURCE_SPECS[SOURCE_OPERA_DBZH].retention


def test_tile_bbox_covers_the_world_at_zoom_zero():
    minx, miny, maxx, maxy = satellite_ir.tile_bbox_3857(0, 0, 0)
    assert minx == pytest.approx(-20037508.34, abs=1)
    assert maxy == pytest.approx(20037508.34, abs=1)
    assert maxx == pytest.approx(-minx)
    assert miny == pytest.approx(-maxy)


@responses.activate
def test_available_times_reads_the_layer_capabilities():
    responses.add(
        responses.GET, satellite_ir.CAPABILITIES_URL,
        body=_caps("2024-09-23T00:00:00Z/2026-10-03T16:00:00Z/PT10M"),
    )
    times = satellite_ir.available_times()
    assert times[0] == datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    assert len(times) == satellite_ir.RETAINED_FRAMES
    # Cached: a second call makes no request.
    satellite_ir.available_times()
    assert len(responses.calls) == 1


@responses.activate
def test_unreachable_capabilities_raise_when_nothing_is_cached():
    responses.add(responses.GET, satellite_ir.CAPABILITIES_URL, status=502)
    with pytest.raises(satellite_ir.SatelliteUnavailable):
        satellite_ir.available_times()


@responses.activate
def test_a_wms_exception_is_not_cached_as_a_tile():
    """WMS errors come back as HTTP 200 + XML; caching one would pin a broken tile."""
    cycle = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    responses.add(
        responses.GET, satellite_ir.WMS_URL,
        body="<ServiceExceptionReport/>", content_type="application/vnd.ogc.se_xml",
    )
    with pytest.raises(satellite_ir.SatelliteUnavailable):
        satellite_ir.fetch_tile(cycle, 6, 32, 21)
    responses.replace(
        responses.GET, satellite_ir.WMS_URL, body=b"\x89PNGfake", content_type="image/png",
    )
    assert satellite_ir.fetch_tile(cycle, 6, 32, 21) == b"\x89PNGfake"


@responses.activate
def test_tile_request_pins_layer_style_crs_and_time():
    cycle = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    responses.add(responses.GET, satellite_ir.WMS_URL, body=b"png", content_type="image/png")
    satellite_ir.fetch_tile(cycle, 6, 32, 21)
    satellite_ir.fetch_tile(cycle, 6, 32, 21)  # cached
    assert len(responses.calls) == 1
    params = responses.calls[0].request.params
    assert params["layers"] == satellite_ir.LAYER
    assert params["styles"] == satellite_ir.STYLE
    assert params["crs"] == "EPSG:3857"
    assert params["time"] == "2026-10-03T16:00:00Z"
    assert params["width"] == params["height"] == "256"


# --- Upstream protection (#655 review) ---------------------------------------------


@responses.activate
def test_an_expired_list_is_served_stale_while_one_caller_refreshes(monkeypatch):
    responses.add(
        responses.GET, satellite_ir.CAPABILITIES_URL,
        body=_caps("2024-09-23T00:00:00Z/2026-10-03T16:00:00Z/PT10M"),
    )
    first = satellite_ir.available_times()
    # Expire the cache, and pretend another thread holds the refresh.
    stamp_, times = satellite_ir._caps_cache
    satellite_ir._caps_cache = (stamp_ - 10_000, times)
    assert satellite_ir._caps_refresh_lock.acquire(blocking=False)
    try:
        assert satellite_ir.available_times() == first
    finally:
        satellite_ir._caps_refresh_lock.release()
    assert len(responses.calls) == 1, "a waiting caller must not fetch capabilities too"


@responses.activate
def test_a_failing_upstream_is_not_hit_once_per_tile():
    cycle = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    responses.add(responses.GET, satellite_ir.WMS_URL, status=503)
    for x in range(5):
        with pytest.raises(satellite_ir.SatelliteUnavailable):
            satellite_ir.fetch_tile(cycle, 6, 30 + x, 21)
    assert len(responses.calls) == 1


@responses.activate
def test_a_young_cycles_tile_is_refetched_after_a_few_minutes(monkeypatch):
    cycle = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    responses.add(responses.GET, satellite_ir.WMS_URL, body=b"v1", content_type="image/png")
    monkeypatch.setattr(satellite_ir, "is_young", lambda c, now=None: True)
    assert satellite_ir.fetch_tile(cycle, 6, 32, 21) == b"v1"
    key = (satellite_ir.stamp(cycle), 6, 32, 21)
    body, fetched = satellite_ir._tile_cache[key]
    satellite_ir._tile_cache[key] = (body, fetched - satellite_ir.YOUNG_TILE_TTL_SECONDS - 1)
    responses.replace(responses.GET, satellite_ir.WMS_URL, body=b"v2", content_type="image/png")
    assert satellite_ir.fetch_tile(cycle, 6, 32, 21) == b"v2"
    # An old cycle's tile is final.
    monkeypatch.setattr(satellite_ir, "is_young", lambda c, now=None: False)
    body, fetched = satellite_ir._tile_cache[key]
    satellite_ir._tile_cache[key] = (body, fetched - 10_000)
    assert satellite_ir.fetch_tile(cycle, 6, 32, 21) == b"v2"
    assert len(responses.calls) == 2


def test_concurrent_misses_of_one_tile_share_one_fetch(monkeypatch):
    import threading

    cycle = datetime(2026, 10, 3, 16, 0, tzinfo=timezone.utc)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(*_a):
        calls.append(1)
        started.set()
        release.wait(5)
        return b"png"

    monkeypatch.setattr(satellite_ir, "_fetch_tile_upstream", slow)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(satellite_ir.fetch_tile(cycle, 6, 32, 21)))
        for _ in range(4)
    ]
    threads[0].start()
    started.wait(5)
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(5)
    assert results == [b"png"] * 4
    assert len(calls) == 1
