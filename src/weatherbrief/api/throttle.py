"""Per-user rate limiting and concurrency control for CPU-heavy endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException


class SlidingWindowRateLimiter:
    """In-memory per-key sliding-window rate limiter.

    Uses ``time.monotonic()`` for clock-skew-safe timing.
    Thread-safe via a single lock.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> None:
        """Raise 429 if *key* has exceeded the rate limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._hits[key]
            # Prune expired entries
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= self.max_requests:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded ({self.max_requests} per "
                    f"{self.window_seconds // 3600}h). Try again later.",
                )
            timestamps.append(now)


# ---------------------------------------------------------------------------
# Concurrency semaphore — caps simultaneous heavy generations server-wide
# ---------------------------------------------------------------------------

_generation_semaphore = threading.Semaphore(3)


@contextmanager
def generation_slot() -> Iterator[None]:
    """Acquire a generation slot or return 503 immediately."""
    acquired = _generation_semaphore.acquire(blocking=False)
    if not acquired:
        raise HTTPException(
            status_code=503,
            detail="Server busy — too many concurrent generations. "
            "Please retry in a few seconds.",
        )
    try:
        yield
    finally:
        _generation_semaphore.release()


# ---------------------------------------------------------------------------
# Pre-configured limiter instances
# ---------------------------------------------------------------------------

pdf_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=3600)
"""WeasyPrint PDF rendering — very heavy."""

plot_limiter = SlidingWindowRateLimiter(max_requests=60, window_seconds=3600)
"""Skew-T and hodograph matplotlib rendering (combined budget)."""
