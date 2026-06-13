"""Tests for weatherbrief.api.throttle — rate limiter and concurrency semaphore."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from weatherbrief.api.throttle import (
    SlidingWindowRateLimiter,
    _format_window,
    generation_slot,
)


# ---------------------------------------------------------------------------
# SlidingWindowRateLimiter
# ---------------------------------------------------------------------------


class TestSlidingWindowRateLimiter:
    def test_allows_requests_under_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("user-1")  # should not raise

    def test_rejects_over_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        limiter.check("user-1")
        limiter.check("user-1")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("user-1")
        assert exc_info.value.status_code == 429

    def test_separate_keys_independent(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("user-a")
        limiter.check("user-b")  # different key — should not raise

    def test_expired_entries_pruned(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=1)
        limiter.check("user-1")

        # Simulate time passing beyond the window
        with patch("weatherbrief.api.throttle.time") as mock_time:
            # First call recorded at real monotonic time; advance clock
            mock_time.monotonic.return_value = time.monotonic() + 2
            limiter.check("user-1")  # should succeed — old entry expired

    def test_thread_safety(self):
        limiter = SlidingWindowRateLimiter(max_requests=100, window_seconds=3600)
        successes = 0
        errors = 0
        lock = threading.Lock()

        def hammer():
            nonlocal successes, errors
            for _ in range(50):
                try:
                    limiter.check("shared")
                    with lock:
                        successes += 1
                except HTTPException:
                    with lock:
                        errors += 1

        threads = [threading.Thread(target=hammer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 200 total attempts, limit is 100
        assert successes == 100
        assert errors == 100

    def test_message_uses_human_window_not_zero_hours(self):
        """A sub-hour window must not render as the misleading "0h"."""
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
        limiter.check("user-1")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("user-1")
        assert "minute" in exc_info.value.detail
        assert "0h" not in exc_info.value.detail


class TestFormatWindow:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (1, "second"),
            (45, "45 seconds"),
            (60, "minute"),
            (120, "2 minutes"),
            (3600, "hour"),
            (86400, "day"),
        ],
    )
    def test_format_window(self, seconds, expected):
        assert _format_window(seconds) == expected


# ---------------------------------------------------------------------------
# generation_slot
# ---------------------------------------------------------------------------


class TestGenerationSlot:
    def test_acquires_and_releases(self):
        with generation_slot():
            pass  # should not raise

    def test_503_when_all_slots_busy(self):
        """When all 3 semaphore slots are held, new requests get 503."""
        from weatherbrief.api.throttle import _generation_semaphore

        # Drain all slots
        for _ in range(3):
            _generation_semaphore.acquire()

        try:
            with pytest.raises(HTTPException) as exc_info:
                with generation_slot():
                    pass
            assert exc_info.value.status_code == 503
        finally:
            # Restore slots
            for _ in range(3):
                _generation_semaphore.release()

    def test_slot_released_on_exception(self):
        """Semaphore is released even if the body raises."""
        from weatherbrief.api.throttle import _generation_semaphore

        initial = _generation_semaphore._value  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError):
            with generation_slot():
                raise RuntimeError("boom")
        assert _generation_semaphore._value == initial  # type: ignore[attr-defined]
