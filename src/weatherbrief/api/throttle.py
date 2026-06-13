"""Per-user rate limiting and concurrency control for CPU-heavy endpoints."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException


def _format_window(seconds: int) -> str:
    """Human-readable window for rate-limit messages (e.g. ``60`` -> "minute").

    The old ``{seconds // 3600}h`` rendered sub-hour windows as a confusing
    "0h" ("1 per 0h"), so format to the largest whole unit that divides evenly.
    """
    for unit_seconds, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= unit_seconds and seconds % unit_seconds == 0:
            n = seconds // unit_seconds
            return name if n == 1 else f"{n} {name}s"
    return "second" if seconds == 1 else f"{seconds} seconds"


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

    def check(self, key: str, *, count: int = 1) -> None:
        """Raise 429 if *key* has exceeded the rate limit.

        *count* is the number of slots to consume (e.g. batch size).
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._hits[key]
            # Prune expired entries
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) + count > self.max_requests:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded ({self.max_requests} per "
                    f"{_format_window(self.window_seconds)}). Try again later.",
                )
            timestamps.extend([now] * count)


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

pirep_burst_limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=120)
"""PIREP submission — max 1 per 2 minutes."""

pirep_daily_limiter = SlidingWindowRateLimiter(max_requests=50, window_seconds=86400)
"""PIREP submission — max 50 per 24 hours."""

feedback_burst_limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
"""Feedback submission — max 1 per minute. Blocks double-click floods and
prevents rapid-fire mail amplification via admin notifications."""

feedback_daily_limiter = SlidingWindowRateLimiter(max_requests=20, window_seconds=86400)
"""Feedback submission — max 20 per 24 hours. Hard cap on admin mailbox
amplification and on DB rows that the triage worker will later have to ignore
(worker cap is 10 per 7 days — this is a pre-insert belt)."""

digest_rating_burst_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)
"""Digest thumb 👍/👎 rating — max 10 per minute. Looser than the verbose
feedback form's 1/min: the widget already blocks double-submit client-side
(buttons disable + per-pack guard), so the only legitimate burst is a pilot
rating several briefings in one session. This bounds mail/DB amplification
without punishing that flow."""

digest_rating_daily_limiter = SlidingWindowRateLimiter(max_requests=50, window_seconds=86400)
"""Digest thumb rating — max 50 per 24 hours. Separate from the feedback-form
daily cap so ratings and written feedback don't starve each other."""

analytics_burst_limiter = SlidingWindowRateLimiter(max_requests=30, window_seconds=60)
"""Analytics ingest — max 30 batches per minute per IP. Real clients flush every
10s, so a single tab generates ~6/min; the cap leaves headroom for a few open
tabs but blocks a bot pounding the endpoint."""

analytics_daily_limiter = SlidingWindowRateLimiter(max_requests=20_000, window_seconds=86400)
"""Analytics ingest — max 20,000 *events* per 24h per IP. Batch size is folded
into the count, so this caps row inflation regardless of how the client splits
its batches. Generous enough that no real user will hit it."""
