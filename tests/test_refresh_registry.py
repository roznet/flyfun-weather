"""Tests for the RefreshRegistry per-user and global queue limits."""

import pytest

from weatherbrief.api.packs import (
    QueueFullError,
    UserQueueLimitError,
    _RefreshRegistry,
)


@pytest.fixture
def registry():
    return _RefreshRegistry()


class TestRefreshRegistry:
    def test_register_and_unregister(self, registry):
        entry = registry.try_register("flight-1", user_id="user-a")
        assert entry is not None
        assert entry.flight_id == "flight-1"
        assert entry.user_id == "user-a"
        registry.unregister("flight-1")

    def test_duplicate_flight_returns_none(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        assert registry.try_register("flight-1", user_id="user-a") is None
        registry.unregister("flight-1")

    def test_per_user_limit(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        registry.try_register("flight-2", user_id="user-a")
        with pytest.raises(UserQueueLimitError) as exc_info:
            registry.try_register("flight-3", user_id="user-a")
        assert exc_info.value.current_count == 2
        assert exc_info.value.limit == registry.MAX_PER_USER

    def test_per_user_limit_independent(self, registry):
        """Different users have independent limits."""
        registry.try_register("flight-1", user_id="user-a")
        registry.try_register("flight-2", user_id="user-a")
        # user-b should still be able to register
        entry = registry.try_register("flight-3", user_id="user-b")
        assert entry is not None

    def test_per_user_limit_frees_on_unregister(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        registry.try_register("flight-2", user_id="user-a")
        registry.unregister("flight-1")
        # Now user-a should be able to register again
        entry = registry.try_register("flight-3", user_id="user-a")
        assert entry is not None

    def test_global_queue_full(self, registry):
        for i in range(registry.MAX_QUEUE_DEPTH):
            registry.try_register(f"flight-{i}", user_id=f"user-{i}")
        with pytest.raises(QueueFullError):
            registry.try_register("flight-extra", user_id="user-new")

    def test_scheduler_bypasses_global_limit(self, registry):
        for i in range(registry.MAX_QUEUE_DEPTH):
            registry.try_register(f"flight-{i}", user_id=f"user-{i}")
        entry = registry.try_register("flight-sched", triggered_by="scheduler")
        assert entry is not None

    def test_scheduler_bypasses_per_user_limit(self, registry):
        """Scheduler-triggered refreshes bypass per-user limits."""
        registry.try_register("flight-1", triggered_by="scheduler", user_id="user-a")
        registry.try_register("flight-2", triggered_by="scheduler", user_id="user-a")
        entry = registry.try_register("flight-3", triggered_by="scheduler", user_id="user-a")
        assert entry is not None

    def test_user_id_none_skips_per_user_check(self, registry):
        """When user_id is None (legacy), per-user limit is not enforced."""
        registry.try_register("flight-1")
        registry.try_register("flight-2")
        entry = registry.try_register("flight-3")
        assert entry is not None

    def test_set_refreshing(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        registry.set_refreshing("flight-1")
        entry = registry.get("flight-1")
        assert entry.status == "refreshing"

    def test_queue_snapshot(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        registry.try_register("flight-2", user_id="user-b")
        registry.set_refreshing("flight-1")
        snap = registry.queue_snapshot()
        assert snap["queued"] == 1
        assert snap["refreshing"] == 1


class TestIdleSeconds:
    """`idle_seconds` — the signal the GRIB warm loop yields on (issue #490)."""

    def test_none_before_any_refresh(self, registry):
        assert registry.idle_seconds() is None

    def test_zero_while_a_refresh_is_active(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        assert registry.idle_seconds() == 0.0

    def test_counts_up_once_the_last_refresh_finishes(self, registry):
        registry.try_register("flight-1", user_id="user-a")
        registry.unregister("flight-1")
        idle = registry.idle_seconds()
        assert idle is not None and idle >= 0.0

    def test_still_active_while_another_refresh_runs(self, registry):
        """Idle starts when the registry empties, not on the first unregister."""
        registry.try_register("flight-1", user_id="user-a")
        registry.try_register("flight-2", user_id="user-b")
        registry.unregister("flight-1")
        assert registry.idle_seconds() == 0.0
        registry.unregister("flight-2")
        assert registry.idle_seconds() is not None

    def test_unregister_of_unknown_flight_does_not_start_the_clock(self, registry):
        registry.unregister("never-registered")
        assert registry.idle_seconds() is None
