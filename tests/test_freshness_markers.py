"""Unit tests for the in-memory MarkerStore (issue #108)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.fetch.freshness import registry
from weatherbrief.fetch.freshness.markers import MarkerStore


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_bootstrap_populates_all_requested_sources():
    store = MarkerStore()
    sources = [
        ("ecmwf:direct", "ecmwf"),
        ("gfs:noaa", "gfs"),
        ("icon_eu:dwd", "icon_eu"),
    ]
    await store.bootstrap(sources, now=_utc(2026, 5, 3, 12))
    assert len(store.keys_sync()) == 3
    m = store.get_sync("ecmwf:direct", "ecmwf")
    assert m is not None
    assert m.init <= _utc(2026, 5, 3, 12)
    assert m.next_expected > _utc(2026, 5, 3, 12)
    assert m.last_check is None


@pytest.mark.asyncio
async def test_update_advances_init_when_newer():
    store = MarkerStore()
    await store.bootstrap([("ecmwf:direct", "ecmwf")], now=_utc(2026, 5, 3, 12))
    m_before = store.get_sync("ecmwf:direct", "ecmwf")
    new_init = _utc(2026, 5, 3, 6)
    if new_init <= m_before.init:
        # Bootstrap may have already chosen 06Z — push forward to 12Z run.
        new_init = _utc(2026, 5, 3, 12)

    await store.update("ecmwf:direct", "ecmwf", new_init, now=_utc(2026, 5, 3, 19))
    m = store.get_sync("ecmwf:direct", "ecmwf")
    assert m.init == new_init
    assert m.slip_count == 0
    assert m.last_check == _utc(2026, 5, 3, 19)
    # Observations are (cycle_init, arrival_wallclock) pairs.
    obs_inits = [pair[0] for pair in m.observations]
    obs_arrivals = [pair[1] for pair in m.observations]
    assert new_init in obs_inits
    assert _utc(2026, 5, 3, 19) in obs_arrivals


@pytest.mark.asyncio
async def test_update_no_change_before_expected_does_not_slip():
    store = MarkerStore()
    await store.bootstrap([("ecmwf:direct", "ecmwf")], now=_utc(2026, 5, 3, 12))
    m_before = store.get_sync("ecmwf:direct", "ecmwf")

    # Same init, before next_expected → just update last_check, no slip.
    early = m_before.next_expected - timedelta(minutes=30)
    await store.update("ecmwf:direct", "ecmwf", m_before.init, now=early)
    m = store.get_sync("ecmwf:direct", "ecmwf")
    assert m.slip_count == 0
    assert m.next_expected == m_before.next_expected
    assert m.last_check == early


@pytest.mark.asyncio
async def test_update_no_change_after_expected_increments_slip():
    store = MarkerStore()
    await store.bootstrap([("ecmwf:direct", "ecmwf")], now=_utc(2026, 5, 3, 12))
    m_before = store.get_sync("ecmwf:direct", "ecmwf")
    cfg = registry.SOURCE_REGISTRY["ecmwf:direct"]

    # Same init, AT next_expected → first slip bumps by cfg.slip_bump(1) which
    # equals the base retry_interval.
    late = m_before.next_expected
    await store.update("ecmwf:direct", "ecmwf", m_before.init, now=late)
    m = store.get_sync("ecmwf:direct", "ecmwf")
    assert m.slip_count == 1
    assert m.next_expected == m_before.next_expected + cfg.slip_bump(1)
    assert cfg.slip_bump(1) == cfg.retry_interval


@pytest.mark.asyncio
async def test_slip_backoff_grows_then_caps():
    """Slip bumps should grow exponentially up to ``max_retry_interval``."""
    store = MarkerStore()
    await store.bootstrap([("ecmwf:direct", "ecmwf")], now=_utc(2026, 5, 3, 12))
    cfg = registry.SOURCE_REGISTRY["ecmwf:direct"]

    seen_bumps: list[timedelta] = []
    prev_next = store.get_sync("ecmwf:direct", "ecmwf").next_expected
    for slip in range(1, cfg.max_slip_retries + 1):
        m = store.get_sync("ecmwf:direct", "ecmwf")
        await store.update("ecmwf:direct", "ecmwf", m.init, now=m.next_expected)
        m_after = store.get_sync("ecmwf:direct", "ecmwf")
        seen_bumps.append(m_after.next_expected - prev_next)
        prev_next = m_after.next_expected

    # Bumps should be non-decreasing and respect the cap.
    for i in range(1, len(seen_bumps)):
        assert seen_bumps[i] >= seen_bumps[i - 1]
    assert seen_bumps[-1] <= cfg.max_retry_interval
    # And the first bump matches the base interval (1-based slip_count).
    assert seen_bumps[0] == cfg.retry_interval


@pytest.mark.asyncio
async def test_slip_cap_jumps_to_next_cycle():
    store = MarkerStore()
    await store.bootstrap([("ecmwf:direct", "ecmwf")], now=_utc(2026, 5, 3, 12))
    cfg = registry.SOURCE_REGISTRY["ecmwf:direct"]

    # Drive slip up to and past the cap.  Each call: same observed_init,
    # now == current next_expected → bumps.
    for _ in range(cfg.max_slip_retries + 1):
        m = store.get_sync("ecmwf:direct", "ecmwf")
        await store.update("ecmwf:direct", "ecmwf", m.init, now=m.next_expected)

    m_final = store.get_sync("ecmwf:direct", "ecmwf")
    assert m_final.slip_count == 0  # reset after jump
    # next_expected should be aligned to a registered cycle's delivery — same
    # minute-of-hour as the registry's offset (modulo 60).
    expected_minute = int(cfg.offset_for(0).total_seconds() / 60) % 60
    assert m_final.next_expected.minute == expected_minute


@pytest.mark.asyncio
async def test_is_stale_when_never_checked():
    store = MarkerStore()
    await store.bootstrap([("gfs:noaa", "gfs")], now=_utc(2026, 5, 3, 12))
    m = store.get_sync("gfs:noaa", "gfs")
    assert m.is_stale(loop_interval=timedelta(minutes=5)) is True


@pytest.mark.asyncio
async def test_is_stale_after_two_loop_intervals():
    store = MarkerStore()
    await store.bootstrap([("gfs:noaa", "gfs")], now=_utc(2026, 5, 3, 12))
    # Manually backdate last_check
    m = store.get_sync("gfs:noaa", "gfs")
    # Use an in-place mutation via internal access — fine for test
    store._markers[("gfs:noaa", "gfs")].last_check = (
        datetime.now(timezone.utc) - timedelta(minutes=15)
    )
    m = store.get_sync("gfs:noaa", "gfs")
    assert m.is_stale(loop_interval=timedelta(minutes=5)) is True
    # 1 × interval is not stale
    store._markers[("gfs:noaa", "gfs")].last_check = (
        datetime.now(timezone.utc) - timedelta(minutes=4)
    )
    m = store.get_sync("gfs:noaa", "gfs")
    assert m.is_stale(loop_interval=timedelta(minutes=5)) is False


@pytest.mark.asyncio
async def test_get_returns_immutable_snapshot():
    """Mutating the returned marker must not affect the store."""
    store = MarkerStore()
    await store.bootstrap([("gfs:noaa", "gfs")], now=_utc(2026, 5, 3, 12))
    m1 = store.get_sync("gfs:noaa", "gfs")
    m1.slip_count = 999
    sentinel = (_utc(2030, 1, 1), _utc(2030, 1, 1, 5))
    m1.observations.append(sentinel)
    m2 = store.get_sync("gfs:noaa", "gfs")
    assert m2.slip_count == 0
    assert sentinel not in list(m2.observations)


@pytest.mark.asyncio
async def test_mark_check_refreshes_heartbeat_only():
    store = MarkerStore()
    await store.bootstrap([("gfs:noaa", "gfs")], now=_utc(2026, 5, 3, 12))
    before = store.get_sync("gfs:noaa", "gfs")
    await store.mark_check("gfs:noaa", "gfs", now=_utc(2026, 5, 3, 13))
    after = store.get_sync("gfs:noaa", "gfs")
    assert after.last_check == _utc(2026, 5, 3, 13)
    assert after.init == before.init
    assert after.next_expected == before.next_expected
