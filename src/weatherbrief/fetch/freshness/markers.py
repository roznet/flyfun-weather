"""In-memory marker store for the freshness loop.

Each marker tracks the latest observed init for one (source, model) pair
along with when the next run is expected, slip-retry state, and the
timestamp of the last dynamic check.  No persistence — markers
re-bootstrap from :func:`registry.initial_marker_for` on startup.

Concurrency: the freshness loop writes markers; the freshness HTTP
handler reads them.  An ``asyncio.Lock`` guards mutations.  Reads via
:meth:`MarkerStore.get` return a frozen snapshot (dataclass replace)
so callers can inspect without holding the lock.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from . import registry

logger = logging.getLogger(__name__)


# In-memory rolling window of recent advances per marker.  Sized to comfortably
# cover ~3 days of observations across 4 daily cycles per source — enough to
# spot drift without unbounded memory.  Survives advances + slip-cap jumps,
# lost on process restart (no persistence by design — issue #108 scope).
OBSERVATIONS_MAXLEN = 100


@dataclass
class Marker:
    """Latest-known state of one (source, model) pair.

    Attributes:
        source: Registry key, e.g. ``"ecmwf:direct"``.
        model: Logical model name, e.g. ``"ecmwf"``.
        init: Latest observed init time (aware UTC).
        next_expected: Wallclock time at which the next run is expected.
        last_check: When the dynamic check last ran (None until first check).
        slip_count: Number of consecutive slip retries since last advance.
        published_at: Provider-reported wallclock when the run became
            available (only Open-Meteo exposes this via ``meta.json``'s
            ``last_run_availability_time``).  ``None`` for direct GRIB
            sources where we can only observe local arrival.
        observations: Recent ``(cycle_init, arrival_wallclock)`` pairs — used
            for drift detection / calibration.  Each pair lets you compute
            actual delivery delay vs. registry expectation.
    """

    source: str
    model: str
    init: datetime
    next_expected: datetime
    last_check: datetime | None = None
    slip_count: int = 0
    published_at: datetime | None = None
    observations: deque[tuple[datetime, datetime]] = field(
        default_factory=lambda: deque(maxlen=OBSERVATIONS_MAXLEN),
    )

    def is_stale(self, loop_interval: timedelta) -> bool:
        """Return True if the loop hasn't checked this marker recently.

        A marker is "stale" (suspect) when ``last_check`` is older than
        ``2 × loop_interval`` (or never set).  Callers should fall back to
        an inline check and surface ``marker_health="suspect"`` to the UI.
        """
        if self.last_check is None:
            return True
        return datetime.now(timezone.utc) - self.last_check > 2 * loop_interval


class MarkerStore:
    """Async-safe in-memory store for all (source, model) markers.

    Bootstrap is lazy: :meth:`bootstrap` populates initial values from the
    registry without doing any I/O.  The freshness loop then runs
    :meth:`update` / :meth:`mark_slip` per dynamic check.
    """

    def __init__(self) -> None:
        self._markers: dict[tuple[str, str], Marker] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    async def bootstrap(
        self,
        sources: list[tuple[str, str]],
        now: datetime | None = None,
    ) -> None:
        """Populate initial markers from the registry without I/O.

        ``sources`` is a list of ``(source_key, model_name)`` pairs.  Each
        gets a marker whose ``init`` is the most recent expected-delivered
        cycle and ``next_expected`` is the next cycle's expected delivery.

        ``last_check`` is set to ``now`` so :meth:`Marker.is_stale` doesn't
        immediately flag every marker as suspect — the registry-derived
        ``init`` is a good-faith estimate, and the loop will run a real
        dynamic check the first time a marker's ``next_expected`` passes.
        """
        bootstrap_now = now or datetime.now(timezone.utc)
        async with self._lock:
            for source, model in sources:
                init, nxt = registry.initial_marker_for(source, now=now)
                self._markers[(source, model)] = Marker(
                    source=source, model=model, init=init, next_expected=nxt,
                    last_check=bootstrap_now,
                )
            logger.info(
                "MarkerStore: bootstrapped %d (source, model) markers",
                len(self._markers),
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_sync(self, source: str, model: str) -> Marker | None:
        """Lock-free read.  Returns a snapshot copy or None if not bootstrapped.

        Used from sync contexts (FastAPI threadpool routes) where awaiting
        the asyncio lock isn't ideal — and where the lock wouldn't help
        anyway, since it only serialises coroutines.

        Coherence comes from how :meth:`update` publishes state: it builds a
        new ``Marker`` (with a copied observations deque) and assigns it to
        the dict in a single ``dict.__setitem__`` call.  The reader's
        ``self._markers.get(...)`` returns either the old or the new
        ``Marker`` reference whole — never a half-updated one — and the
        subsequent ``replace(m, observations=deque(...))`` operates on
        whichever wholly-consistent snapshot it captured.
        """
        m = self._markers.get((source, model))
        if m is None:
            return None
        return replace(m, observations=deque(m.observations, maxlen=OBSERVATIONS_MAXLEN))

    async def get(self, source: str, model: str) -> Marker | None:
        async with self._lock:
            return self.get_sync(source, model)

    def all_sync(self) -> dict[tuple[str, str], Marker]:
        """Lock-free snapshot of every marker, intended for the admin endpoint."""
        return {
            k: replace(m, observations=deque(m.observations, maxlen=OBSERVATIONS_MAXLEN))
            for k, m in self._markers.items()
        }

    def keys_sync(self) -> list[tuple[str, str]]:
        return list(self._markers.keys())

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def update(
        self,
        source: str,
        model: str,
        observed_init: datetime,
        now: datetime | None = None,
        published_at: datetime | None = None,
    ) -> None:
        """Record a fresh check.  Advances ``init`` if newer; bumps slip otherwise.

        - If ``observed_init > marker.init``: advance, reset slip, append a
          ``(cycle_init, arrival_wallclock)`` observation, refresh
          ``published_at`` from the dispatch (Open-Meteo only — direct sources
          pass ``None``), and log the actual delivery delay vs. the registry
          expectation (used for calibration).
        - If equal and ``now >= next_expected``: slip — bump ``next_expected``
          by ``cfg.slip_bump(slip_count)`` (exponential backoff, capped).
          After ``max_slip_retries``, jump forward to the next cycle's
          expected delivery and reset slip.
        - Else: just update ``last_check`` (early/null check).

        State transitions go through a single atomic ``dict.__setitem__`` of
        a new ``Marker`` instance (with a copied observations deque).  The
        ``asyncio.Lock`` only serialises coroutines — :meth:`get_sync` is
        called from FastAPI's threadpool, and the GIL alone isn't enough to
        guarantee a coherent multi-field read off a marker mid-mutation.
        Atomic dict assignment + immutable snapshot is what makes the
        "wholesale replace" claim in :meth:`get_sync` actually true.
        """
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            marker = self._markers.get((source, model))
            if marker is None:
                # Source observed before bootstrap — accept and pin from registry.
                next_exp = registry.next_run_after(source, observed_init)
                new_observations: deque[tuple[datetime, datetime]] = deque(
                    maxlen=OBSERVATIONS_MAXLEN,
                )
                new_observations.append((observed_init, now))
                self._markers[(source, model)] = Marker(
                    source=source, model=model,
                    init=observed_init, next_expected=next_exp,
                    last_check=now,
                    published_at=published_at,
                    observations=new_observations,
                )
                return

            if observed_init > marker.init:
                expected_delivery = registry.expected_delivery_for_init(source, observed_init)
                actual_delay = now - observed_init
                vs_expected = now - expected_delivery
                new_observations = deque(marker.observations, maxlen=OBSERVATIONS_MAXLEN)
                new_observations.append((observed_init, now))
                self._markers[(source, model)] = replace(
                    marker,
                    init=observed_init,
                    next_expected=registry.next_run_after(source, observed_init),
                    slip_count=0,
                    last_check=now,
                    published_at=published_at,
                    observations=new_observations,
                )
                logger.info(
                    "Marker advanced: %s/%s init=%s arrived_at=%s "
                    "delivery=+%s (registry expected +%s, drift=%+ds)",
                    source, model,
                    observed_init.isoformat(), now.isoformat(),
                    _fmt_td(actual_delay),
                    _fmt_td(expected_delivery - observed_init),
                    int(vs_expected.total_seconds()),
                )
                return

            # No advance — compute the new state, then publish atomically.
            new_slip_count = marker.slip_count
            new_next_expected = marker.next_expected
            log_warning: tuple | None = None
            log_info: tuple | None = None
            if now >= marker.next_expected:
                cfg = registry.SOURCE_REGISTRY[source]
                new_slip_count = marker.slip_count + 1
                if new_slip_count > cfg.max_slip_retries:
                    # Give up on the cycle we were waiting for and target the
                    # one *after* it.  The slipping cycle is always the one
                    # right after ``marker.init`` (the last successfully
                    # observed cycle) — derive it directly so accumulated
                    # backoff bumps to ``next_expected`` don't push the jump
                    # several cycles into the future.
                    skipped_cycle = registry.next_cycle_init_after(
                        source, marker.init,
                    )
                    target_cycle = registry.next_cycle_init_after(
                        source, skipped_cycle,
                    )
                    new_next_expected = registry.expected_delivery_for_init(
                        source, target_cycle,
                    )
                    new_slip_count = 0
                    log_warning = (
                        "Marker slip cap hit: %s/%s — skipping cycle %s, "
                        "target cycle %s, next_expected=%s",
                        source, model,
                        skipped_cycle.isoformat(), target_cycle.isoformat(),
                        new_next_expected.isoformat(),
                    )
                else:
                    bump = cfg.slip_bump(new_slip_count)
                    new_next_expected = marker.next_expected + bump
                    log_info = (
                        "Marker slip: %s/%s slip_count=%d bump=+%s next_expected=%s",
                        source, model, new_slip_count,
                        _fmt_td(bump), new_next_expected.isoformat(),
                    )
            # Capture published_at on every successful OM observation, even
            # when init hasn't advanced — otherwise we'd discard a freshly
            # observed publish wallclock just because the run didn't change,
            # leaving popover restarts blank for hours until the next cycle.
            # Direct sources always pass None here; only update when set.
            new_published_at = (
                published_at if published_at is not None else marker.published_at
            )
            self._markers[(source, model)] = replace(
                marker,
                next_expected=new_next_expected,
                slip_count=new_slip_count,
                last_check=now,
                published_at=new_published_at,
            )
            if log_warning is not None:
                logger.warning(*log_warning)
            elif log_info is not None:
                logger.info(*log_info)

    async def mark_check(self, source: str, model: str, now: datetime | None = None) -> None:
        """Refresh ``last_check`` without changing init/next_expected.

        Used when a dynamic check returns None (couldn't determine init) so
        the heartbeat doesn't go stale just because of a transient failure.
        """
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            marker = self._markers.get((source, model))
            if marker is not None:
                marker.last_check = now


# Module-level singleton, bound from the lifespan startup hook.  Importing
# code reaches the live store via :func:`get_store` so tests can swap it.

_STORE: MarkerStore | None = None


def get_store() -> MarkerStore:
    """Return the process-wide marker store, creating it on first access."""
    global _STORE
    if _STORE is None:
        _STORE = MarkerStore()
    return _STORE


def reset_store_for_tests() -> None:
    """Drop the singleton so a test can install a fresh store."""
    global _STORE
    _STORE = None


def _fmt_td(td: timedelta) -> str:
    """Compact ``Hh Mm`` (or ``Mm Ss``) format for log lines."""
    total_s = int(td.total_seconds())
    sign = "-" if total_s < 0 else ""
    total_s = abs(total_s)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{sign}{h}h {m}m"
    if m:
        return f"{sign}{m}m {s}s"
    return f"{sign}{s}s"
