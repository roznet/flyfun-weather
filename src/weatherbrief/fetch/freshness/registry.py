"""Per-(model, source) schedule registry.

Pure config + pure functions — no I/O.  Computes when each (model, source)
pair's next run should be expected, and how far the run's forecast horizon
reaches.  Used by both the freshness loop (to decide when to poll) and the
freshness check (to decide whether a newer run actually covers the flight).

Each ``SourceConfig`` is keyed by ``"{model}:{source}"`` (e.g. ``"ecmwf:direct"``,
``"gfs:openmeteo"``).  Cycles are UTC hours of day; ``delivery_offset`` is the
expected wallclock lag from cycle init to data readiness.

``run_horizon`` may differ per cycle (e.g. ECMWF 00/12 reach 168h; 06/18 are
medium-only at ~90h; ICON-EU main cycles 120h vs intermediate 78h).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class SourceConfig:
    """Schedule + horizon config for one (model, source) pair.

    Attributes:
        key: Stable identifier ``"{model}:{source}"``.
        cycles: UTC hours-of-day at which the source publishes runs.
        delivery_offset: Expected lag from cycle start to data readiness.
            May be a single ``timedelta`` (uniform) or a per-cycle dict.
        horizon: Forecast horizon.  Uniform ``timedelta`` or per-cycle dict.
        retry_interval: Base interval used for the *first* slip retry.
            Subsequent retries grow exponentially (×2 per slip) up to
            :attr:`max_retry_interval` — so we don't keep hammering a
            slow-publishing source every 10 min for 6 h.
        max_retry_interval: Ceiling for the exponential backoff bump.
        max_slip_retries: After this many slip bumps, jump ``next_expected``
            forward to the *next* scheduled cycle's expected delivery time
            (avoids polling forever on a skipped cycle).  With the default
            backoff schedule, 8 slips ≈ 6 h before cycle-jump.
        readiness_check: Symbolic name of the check_source dispatch target.
            Resolved by :mod:`sources`.
    """

    key: str
    cycles: tuple[int, ...]
    delivery_offset: timedelta | dict[int, timedelta]
    horizon: timedelta | dict[int, timedelta]
    retry_interval: timedelta = timedelta(minutes=10)
    max_retry_interval: timedelta = timedelta(hours=1)
    max_slip_retries: int = 8
    readiness_check: str = ""

    def slip_bump(self, slip_count: int) -> timedelta:
        """Return the next-expected bump for the ``slip_count``-th slip.

        Exponential backoff capped at :attr:`max_retry_interval`.  ``slip_count``
        is 1-based — i.e. the first slip uses :attr:`retry_interval` exactly.
        """
        if slip_count <= 0:
            return self.retry_interval
        bump = self.retry_interval * (2 ** (slip_count - 1))
        return min(bump, self.max_retry_interval)

    def offset_for(self, cycle: int) -> timedelta:
        if isinstance(self.delivery_offset, dict):
            return self.delivery_offset[cycle]
        return self.delivery_offset

    def horizon_for(self, cycle: int) -> timedelta:
        if isinstance(self.horizon, dict):
            return self.horizon[cycle]
        return self.horizon


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# ECMWF: empirically +6h27m–6h37m across 14 days; +6h40m gives 3min margin
# and works for both long (00/12 = 168h) and medium-only (06/18) cycles.
_ECMWF_OFFSET = timedelta(hours=6, minutes=40)
# 168h on 00/12 (with the long-tail step set), ~90h medium-only on 06/18
# (3-hourly phase ends init+6h27m, no 168h step delivered).
_ECMWF_HORIZON: dict[int, timedelta] = {
    0: timedelta(hours=168),
    6: timedelta(hours=90),
    12: timedelta(hours=168),
    18: timedelta(hours=90),
}

# GFS: PUBLISH_DELAY_HOURS = 5 in grib_fetch.py; horizon 384h (16 days).
_GFS_NOAA_OFFSET = timedelta(hours=5)
_GFS_NOAA_HORIZON = timedelta(hours=384)

# ICON-EU: ICON_EU_PUBLISH_DELAY_HOURS = 3; main cycles {0,6,12,18} reach
# 120h, intermediate {3,9,15,21} reach 78h.  See icon_eu_fetch.py:31-40.
_ICON_EU_OFFSET = timedelta(hours=3)
_ICON_EU_MAIN = {0, 6, 12, 18}
_ICON_EU_HORIZON: dict[int, timedelta] = {
    h: (timedelta(hours=120) if h in _ICON_EU_MAIN else timedelta(hours=78))
    for h in (0, 3, 6, 9, 12, 15, 18, 21)
}

# Open-Meteo offsets — calibrated against meta.json observation 2026-05-03.
# Open-Meteo republishes other providers' models with notable lag; the figures
# in issue #108's table (sourced from OM docs) underestimate real delivery.
# Bumped here with margin over observed delays so the marker doesn't slip
# every cycle.  Drift detection (deque of last 20 observations on each marker)
# will surface further drift if real-world publish times keep changing.
_OM_GFS_OFFSET = timedelta(hours=6, minutes=45)      # observed 6h37m
_OM_ECMWF_OFFSET = timedelta(hours=8)                # observed 7h30m
_OM_ICON_OFFSET = timedelta(hours=4, minutes=30)     # observed 4h17m
_OM_ARPEGE_OFFSET = timedelta(hours=4, minutes=30)   # observed 4h00m
_OM_UKMO_OFFSET = timedelta(hours=8, minutes=30)     # observed 8h19m


SOURCE_REGISTRY: dict[str, SourceConfig] = {
    "ecmwf:direct": SourceConfig(
        key="ecmwf:direct",
        cycles=(0, 6, 12, 18),
        delivery_offset=_ECMWF_OFFSET,
        horizon=_ECMWF_HORIZON,
        readiness_check="ecmwf_direct",
    ),
    "gfs:noaa": SourceConfig(
        key="gfs:noaa",
        cycles=(0, 6, 12, 18),
        delivery_offset=_GFS_NOAA_OFFSET,
        horizon=_GFS_NOAA_HORIZON,
        readiness_check="gfs_noaa",
    ),
    "icon_eu:dwd": SourceConfig(
        key="icon_eu:dwd",
        cycles=(0, 3, 6, 9, 12, 15, 18, 21),
        delivery_offset=_ICON_EU_OFFSET,
        horizon=_ICON_EU_HORIZON,
        readiness_check="icon_eu_dwd",
    ),
    "gfs:openmeteo": SourceConfig(
        key="gfs:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_GFS_OFFSET,
        horizon=timedelta(days=16),
        readiness_check="om_meta",
    ),
    # Open-Meteo only effectively publishes ECMWF 00/12 main runs in time;
    # 06/18 are bc-runs that lag heavily — see issue #100.  We track only the
    # main cycles so the marker doesn't bounce on bc-run shuffles.
    "ecmwf:openmeteo": SourceConfig(
        key="ecmwf:openmeteo",
        cycles=(0, 12),
        delivery_offset=_OM_ECMWF_OFFSET,
        horizon=timedelta(days=10),
        readiness_check="om_meta",
    ),
    # Open-Meteo's meta.json reports update_interval_seconds=21600 (6h) for
    # ICON, even though DWD itself runs ICON on a 3h cycle.  OM only
    # republishes the 6-hourly main runs (0/6/12/18Z) — tracking 3h here
    # would slip every other cycle for no benefit.
    "icon:openmeteo": SourceConfig(
        key="icon:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_ICON_OFFSET,
        horizon=timedelta(days=7),
        readiness_check="om_meta",
    ),
    "meteofrance:openmeteo": SourceConfig(
        key="meteofrance:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_ARPEGE_OFFSET,
        horizon=timedelta(days=6),
        readiness_check="om_meta",
    ),
    "ukmo:openmeteo": SourceConfig(
        key="ukmo:openmeteo",
        cycles=(0, 6, 12, 18),
        delivery_offset=_OM_UKMO_OFFSET,
        horizon=timedelta(days=7),
        readiness_check="om_meta",
    ),
}


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def _floor_to_cycle(dt: datetime, cycles: tuple[int, ...]) -> datetime:
    """Return the most recent cycle init at-or-before ``dt``."""
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [midnight + timedelta(hours=h) for h in cycles]
    candidates += [c - timedelta(days=1) for c in candidates]
    valid = [c for c in candidates if c <= dt]
    return max(valid)


def _next_cycle_init(dt: datetime, cycles: tuple[int, ...]) -> datetime:
    """Return the earliest cycle init strictly after ``dt``."""
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = [midnight + timedelta(hours=h) for h in cycles]
    candidates += [c + timedelta(days=1) for c in candidates]
    future = [c for c in candidates if c > dt]
    return min(future)


def next_run_after(source: str, current_init: datetime) -> datetime:
    """Wallclock time at which the next run *after* ``current_init`` is expected.

    Handles cycles within the same day and rollover to next day.  The result
    is the next cycle's init time plus that cycle's delivery offset.
    """
    cfg = SOURCE_REGISTRY[source]
    next_init = _next_cycle_init(current_init, cfg.cycles)
    return next_init + cfg.offset_for(next_init.hour)


def next_cycle_init_after(source: str, init: datetime) -> datetime:
    """Return the next cycle init (without offset) strictly after ``init``.

    Companion to :func:`next_run_after`, exposed so callers can reason
    about cycles separately from delivery wallclocks.  Used by the
    marker store's slip-cap path to identify the cycle being skipped.
    """
    cfg = SOURCE_REGISTRY[source]
    return _next_cycle_init(init, cfg.cycles)


def run_horizon(source: str, init: datetime) -> timedelta:
    """Forecast horizon (timedelta from init) for the run started at ``init``."""
    cfg = SOURCE_REGISTRY[source]
    return cfg.horizon_for(init.hour)


def cycle_init_for(source: str, dt: datetime) -> datetime:
    """Return the latest cycle init at-or-before ``dt`` for ``source``."""
    cfg = SOURCE_REGISTRY[source]
    return _floor_to_cycle(dt, cfg.cycles)


def expected_delivery_for_init(source: str, init: datetime) -> datetime:
    """Return the wallclock time at which ``init`` is expected to be ready."""
    cfg = SOURCE_REGISTRY[source]
    return init + cfg.offset_for(init.hour)


def initial_marker_for(source: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Bootstrap (init, next_expected) for a source at startup.

    Picks the most recent cycle whose expected delivery is at-or-before
    ``now`` (so the loop's first dynamic check has a plausible "current"
    init to compare against).  Falls back to one cycle prior if the latest
    cycle hasn't been delivered yet.
    """
    now = now or datetime.now(timezone.utc)
    cfg = SOURCE_REGISTRY[source]
    init = _floor_to_cycle(now, cfg.cycles)
    if init + cfg.offset_for(init.hour) > now:
        # current cycle not yet expected to be ready — use prior cycle
        init = _floor_to_cycle(init - timedelta(seconds=1), cfg.cycles)
    next_expected = next_run_after(source, init)
    return init, next_expected
