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
    # Bootstrap sets last_check so freshly-bootstrapped markers don't
    # immediately read as suspect (which would force every freshness HTTP
    # call into the inline-fallback sync-I/O path).  The actual is_stale
    # check is wall-clock-relative and tested separately.
    assert m.last_check == _utc(2026, 5, 3, 12)


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
    # Direct sources advance without a published_at — Open-Meteo is the
    # only dispatch that supplies one.
    assert m.published_at is None
    # Observations are (cycle_init, arrival_wallclock) pairs.
    obs_inits = [pair[0] for pair in m.observations]
    obs_arrivals = [pair[1] for pair in m.observations]
    assert new_init in obs_inits
    assert _utc(2026, 5, 3, 19) in obs_arrivals


@pytest.mark.asyncio
async def test_update_records_published_at_for_open_meteo():
    """OM advances should persist the provider-reported publish wallclock."""
    store = MarkerStore()
    await store.bootstrap([("gfs:openmeteo", "gfs")], now=_utc(2026, 5, 3, 12))
    new_init = _utc(2026, 5, 3, 18)
    publish_time = _utc(2026, 5, 3, 18, 45)

    await store.update(
        "gfs:openmeteo", "gfs", new_init,
        now=_utc(2026, 5, 3, 19),
        published_at=publish_time,
    )
    m = store.get_sync("gfs:openmeteo", "gfs")
    assert m.init == new_init
    assert m.published_at == publish_time


@pytest.mark.asyncio
async def test_update_captures_published_at_on_no_advance():
    """Bootstrap → first loop tick observes the same init → published_at
    must still be captured, otherwise the popover stays blank for hours.
    """
    store = MarkerStore()
    await store.bootstrap([("gfs:openmeteo", "gfs")], now=_utc(2026, 5, 3, 12))
    same_init = store.get_sync("gfs:openmeteo", "gfs").init
    publish_time = same_init + timedelta(hours=7)  # OM publishes ~7h after init

    # Same init, before next_expected (no slip) — should still record publish.
    early = store.get_sync("gfs:openmeteo", "gfs").next_expected - timedelta(minutes=30)
    await store.update(
        "gfs:openmeteo", "gfs", same_init,
        now=early, published_at=publish_time,
    )
    m = store.get_sync("gfs:openmeteo", "gfs")
    assert m.published_at == publish_time

    # Subsequent no-advance check WITHOUT a publish_time must not overwrite.
    later = early + timedelta(minutes=5)
    await store.update("gfs:openmeteo", "gfs", same_init, now=later, published_at=None)
    m2 = store.get_sync("gfs:openmeteo", "gfs")
    assert m2.published_at == publish_time


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
    """After slip-cap, marker should target the cycle right after the
    one that was slipping — not several cycles later.

    Regression for PR #109 review: previous code derived the skipped cycle
    from the bumped ``next_expected`` instead of from ``marker.init``, so
    accumulated backoff pushed the jump past intermediate cycles.
    """
    store = MarkerStore()
    # Bootstrap at a time when the latest delivered ECMWF cycle is 00Z.
    await store.bootstrap([("ecmwf:direct", "ecmwf")], now=_utc(2026, 5, 3, 8))
    cfg = registry.SOURCE_REGISTRY["ecmwf:direct"]
    pre_init = store.get_sync("ecmwf:direct", "ecmwf").init

    for _ in range(cfg.max_slip_retries + 1):
        m = store.get_sync("ecmwf:direct", "ecmwf")
        await store.update("ecmwf:direct", "ecmwf", m.init, now=m.next_expected)

    m_final = store.get_sync("ecmwf:direct", "ecmwf")
    assert m_final.slip_count == 0
    # The cycle we were waiting for is the one right after pre_init.
    # Slip-cap should target the cycle two ahead of pre_init.
    skipped_cycle = registry.next_cycle_init_after("ecmwf:direct", pre_init)
    target_cycle = registry.next_cycle_init_after("ecmwf:direct", skipped_cycle)
    assert m_final.next_expected == registry.expected_delivery_for_init(
        "ecmwf:direct", target_cycle,
    )


@pytest.mark.asyncio
async def test_is_stale_when_last_check_is_none():
    """Manually-constructed marker with last_check=None should be stale.

    Bootstrap sets last_check (so this can't happen via the public API),
    but Marker(...) is also used internally — the heartbeat logic must
    still treat last_check=None as suspect.
    """
    from weatherbrief.fetch.freshness.markers import Marker
    m = Marker(
        source="gfs:noaa", model="gfs",
        init=_utc(2026, 5, 3, 6), next_expected=_utc(2026, 5, 3, 12),
        last_check=None,
    )
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


@pytest.mark.asyncio
async def test_loop_tick_refreshes_heartbeat_for_not_yet_due_marker(monkeypatch):
    """The freshness loop must bump last_check even for sources that aren't
    yet due — otherwise their heartbeat goes stale during the gap between
    next_expected boundaries, forcing freshness HTTP calls into the
    inline-fallback (sync I/O on the event loop).

    Regression for PR #109 review: previously the loop's not-due branch
    used ``continue`` without touching last_check.
    """
    from weatherbrief.fetch.freshness import markers as markers_mod
    from weatherbrief.scheduler import _run_freshness_check_once

    # Inject our own store as the singleton.
    test_store = MarkerStore()
    await test_store.bootstrap(
        [("gfs:noaa", "gfs")], now=_utc(2026, 5, 3, 12),
    )
    monkeypatch.setattr(markers_mod, "_STORE", test_store)

    before = test_store.get_sync("gfs:noaa", "gfs")
    # Simulate a tick well before next_expected (so we hit the not-due path).
    next_exp = before.next_expected
    assert next_exp > _utc(2026, 5, 3, 13)  # sanity

    # Patch wallclock used by the loop to a moment before next_expected, but
    # after bootstrap_now (so last_check would change visibly).
    fake_now = _utc(2026, 5, 3, 13)

    class _FakeDatetime:
        @staticmethod
        def now(tz):
            return fake_now

    import weatherbrief.scheduler as scheduler_mod
    monkeypatch.setattr(scheduler_mod, "datetime", _FakeDatetime)

    await _run_freshness_check_once()

    after = test_store.get_sync("gfs:noaa", "gfs")
    assert after.last_check == fake_now
    assert after.next_expected == before.next_expected  # untouched
    assert after.init == before.init                    # untouched
