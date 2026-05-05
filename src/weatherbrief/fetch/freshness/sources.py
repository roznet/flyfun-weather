"""Dynamic readiness checks for the freshness loop.

Each source in :data:`registry.SOURCE_REGISTRY` has a ``readiness_check``
symbol — this module dispatches that symbol to the existing helper that
already finds the latest available init for that (model, source).  The
loop calls :func:`check_source` only when ``now >= marker.next_expected``
so the cost of these I/O calls is paid at most twice per cycle per
source (once on-time, once on slip).

All wrappers must be safe to call from a thread (the loop offloads them
via ``asyncio.to_thread``) and must return either an :class:`Observation`
for the latest observed init (with an optional provider-reported
``published_at`` wallclock) or ``None`` if the check couldn't determine
one (transient network failure, missing data).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

logger = logging.getLogger(__name__)


class Observation(NamedTuple):
    """Result of a dynamic readiness check.

    ``published_at`` is the provider-reported wallclock when the run
    became available — only Open-Meteo exposes this (via ``meta.json``'s
    ``last_run_availability_time``).  Direct GRIB sources return ``None``
    because the providers don't publish a server-side availability time;
    the closest local proxy is the marker's ``last_check`` wallclock when
    the run was first observed.
    """

    init: datetime
    published_at: datetime | None = None


# ---------------------------------------------------------------------------
# Per-source dispatch
# ---------------------------------------------------------------------------


def _check_ecmwf_direct(model: str) -> Observation | None:
    """Return latest ECMWF run with a readiness sentinel."""
    from weatherbrief.fetch.grib.ecmwf_watcher import get_latest_ready
    init = get_latest_ready()
    if init is None:
        return None
    return Observation(init=init)


def _check_gfs_noaa(model: str) -> Observation | None:
    """Return latest GFS run that has its .idx file published on S3."""
    from weatherbrief.fetch.grib.grib_fetch import find_latest_run
    now = datetime.now(timezone.utc)
    # find_latest_run uses target_time only for as-of/bracketing; passing now
    # gets the most recently published cycle.
    result = find_latest_run(target_time=now)
    if result is None:
        return None
    init_date, init_hour = result
    init = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return Observation(init=init)


def _check_icon_eu_dwd(model: str) -> Observation | None:
    """Return latest ICON-EU run whose level-74 P file responds 200 on HEAD.

    For the marker we don't filter by horizon — the freshness check applies
    horizon-awareness later.  We still rely on the existing helper, which
    requires ``cover_until`` to verify horizon; pass ``target_time=now`` and
    ``cover_until=now`` so any published run qualifies.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import find_latest_icon_eu_run
    now = datetime.now(timezone.utc)
    result = find_latest_icon_eu_run(target_time=now, cover_until=now)
    if result is None:
        return None
    init_date, init_hour = result
    init = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return Observation(init=init)


def _check_om_meta(model: str) -> Observation | None:
    """Return latest Open-Meteo init + publish time for ``model``."""
    from weatherbrief.fetch.model_status import fetch_model_metadata
    meta = fetch_model_metadata([model])
    if model not in meta:
        return None
    m = meta[model]
    return Observation(
        init=datetime.fromtimestamp(m.last_init_time, tz=timezone.utc),
        published_at=datetime.fromtimestamp(
            m.last_availability_time, tz=timezone.utc,
        ),
    )


_DISPATCH = {
    "ecmwf_direct": _check_ecmwf_direct,
    "gfs_noaa": _check_gfs_noaa,
    "icon_eu_dwd": _check_icon_eu_dwd,
    "om_meta": _check_om_meta,
}


def check_source(source: str, model: str) -> Observation | None:
    """Run the dynamic readiness check for one (source, model) pair.

    Looks up the registry to find which check to dispatch, then invokes it.
    Returns the latest :class:`Observation` (aware UTC ``init``, optional
    ``published_at``) or None on failure.  Exceptions are caught and
    logged — the loop should never crash on a transient I/O error.
    """
    from .registry import SOURCE_REGISTRY

    cfg = SOURCE_REGISTRY.get(source)
    if cfg is None:
        logger.warning("check_source: unknown source %r", source)
        return None
    fn = _DISPATCH.get(cfg.readiness_check)
    if fn is None:
        logger.warning(
            "check_source: registry has no dispatch for %r (source %r)",
            cfg.readiness_check, source,
        )
        return None
    try:
        return fn(model)
    except Exception:
        logger.warning(
            "check_source: dynamic check failed for %s/%s",
            source, model, exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Source enumeration
# ---------------------------------------------------------------------------


def all_tracked_sources() -> list[tuple[str, str]]:
    """Return every (source, model) pair the marker store should track.

    The model name is derived from the source key prefix (``"ecmwf:direct"``
    → model ``"ecmwf"``).  Open-Meteo sources use the suffix-less model
    name to match how :func:`fetch.model_status.fetch_model_metadata`
    keys its result dict.
    """
    from .registry import SOURCE_REGISTRY

    pairs: list[tuple[str, str]] = []
    for key in SOURCE_REGISTRY:
        model = key.split(":", 1)[0]
        # icon_eu_dwd is a separate logical model from icon:openmeteo;
        # keep the explicit mapping rather than collapsing.
        pairs.append((key, model))
    return pairs
