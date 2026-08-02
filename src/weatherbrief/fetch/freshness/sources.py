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

    ``data_end`` is the latest hourly timestamp the provider is actually
    serving for the current run.  Only Open-Meteo exposes this (via
    ``meta.json``'s ``data_end_time``).  Often shorter than the static
    config horizon (e.g. ARPEGE advertises 6 days but a given run may
    deliver only ~4 days).  Direct GRIB sources return ``None`` and the
    catalog falls back to ``init + cfg.horizon``.

    ``observed_via`` names the *instrument* that produced ``published_at``.
    The three are not interchangeable measurements of the same thing — see
    the module constants below — so the delivery log records which one was
    used rather than pooling them.  Each dispatch declares its own, so a
    new source can't silently inherit the wrong one.
    """

    init: datetime
    published_at: datetime | None = None
    data_end: datetime | None = None
    observed_via: str = ""


# ---------------------------------------------------------------------------
# Observation instruments
# ---------------------------------------------------------------------------
#
# Each names a different clock, with a different systematic bias against the
# provider's true publish moment.  Recorded per-observation in
# ``model_delivery_log.observed_via`` so a calibration query can keep them
# apart instead of averaging across incomparable measurements.

#: mtime of the ECMWF readiness sentinel — when *our* watcher finished writing
#: after rsync delivery.  Local arrival, biased late by the transfer.
VIA_SENTINEL_MTIME = "sentinel_mtime"

#: ``Last-Modified`` of the origin file on the provider's server (NOAA S3,
#: DWD opendata).  The file's own timestamp, not a publish announcement.
VIA_HTTP_LAST_MODIFIED = "http_last_modified"

#: Open-Meteo ``meta.json``'s ``last_run_availability_time`` — the provider's
#: own record of when it finished ingesting the run.
VIA_OM_META = "om_meta"


# ---------------------------------------------------------------------------
# Per-source dispatch
# ---------------------------------------------------------------------------


def _check_ecmwf_direct(model: str) -> Observation | None:
    """Return latest ECMWF run with a readiness sentinel.

    ``published_at`` is the sentinel mtime — i.e. when the watcher finished
    writing it after rsync delivery.  That's our local arrival time, not
    ECMWF's central publish time, but it's the closest analogue we have
    and is what a pilot really wants to know ("when did this data become
    usable on the briefing server?").
    """
    from weatherbrief.fetch.grib.ecmwf_watcher import get_latest_ready_with_mtime
    result = get_latest_ready_with_mtime()
    if result is None:
        return None
    init, mtime = result
    return Observation(init=init, published_at=mtime, observed_via=VIA_SENTINEL_MTIME)


def _check_gfs_noaa(model: str) -> Observation | None:
    """Return latest GFS run that has its .idx file published on S3.

    Reuses the HEAD that ``find_latest_run_with_response`` already issues
    to confirm the run, extracting ``published_at`` from its
    ``Last-Modified`` header — NOAA's server-side timestamp, equivalent
    in quality to Open-Meteo's ``last_run_availability_time``.
    """
    from weatherbrief.fetch.grib.grib_fetch import find_latest_run_with_response
    now = datetime.now(timezone.utc)
    # target_time is only used for as-of/bracketing; passing ``now`` gets
    # the most recently published cycle.
    result = find_latest_run_with_response(target_time=now)
    if result is None:
        return None
    init_date, init_hour, resp = result
    init = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return Observation(
        init=init,
        published_at=_parse_last_modified(resp.headers.get("Last-Modified")),
        observed_via=VIA_HTTP_LAST_MODIFIED,
    )


def _check_hrrr_noaa(model: str) -> Observation | None:
    """Return latest EXTENDED-cycle HRRR run with a published .idx (#457).

    Only 00/06/12/18z cycles are considered, matching the registry entry:
    the marker must not advance on every hourly cycle or short-lead US packs
    would read stale (and auto-refresh) every hour. ``published_at`` comes
    from the probe's ``Last-Modified`` header, same as GFS.
    """
    from weatherbrief.fetch.grib.hrrr_fetch import (
        find_latest_hrrr_run_with_response,
    )
    now = datetime.now(timezone.utc)
    result = find_latest_hrrr_run_with_response(
        target_time=now, cover_until=now, extended_cycles_only=True,
    )
    if result is None:
        return None
    init_date, init_hour, resp = result
    init = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return Observation(
        init=init,
        published_at=_parse_last_modified(resp.headers.get("Last-Modified")),
        observed_via=VIA_HTTP_LAST_MODIFIED,
    )


def _check_icon_eu_dwd(model: str) -> Observation | None:
    """Return latest ICON-EU run whose level-74 P file responds 200 on HEAD.

    For the marker we don't filter by horizon — the freshness check applies
    horizon-awareness later.  We still rely on the existing helper, which
    requires ``cover_until`` to verify horizon; pass ``target_time=now`` and
    ``cover_until=now`` so any published run qualifies.

    Reuses the HEAD that ``find_latest_icon_eu_run_with_response`` issues,
    extracting ``published_at`` from its ``Last-Modified`` header — DWD's
    server-side publish wallclock.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        find_latest_icon_eu_run_with_response,
    )
    now = datetime.now(timezone.utc)
    result = find_latest_icon_eu_run_with_response(target_time=now, cover_until=now)
    if result is None:
        return None
    init_date, init_hour, resp = result
    init = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return Observation(
        init=init,
        published_at=_parse_last_modified(resp.headers.get("Last-Modified")),
        observed_via=VIA_HTTP_LAST_MODIFIED,
    )


def _check_icon_d2_dwd(model: str) -> Observation | None:
    """Return latest ICON-D2 run whose bottom-level P file responds 200 on HEAD.

    Same shape as :func:`_check_icon_eu_dwd` but for the ICON-D2 variant — the
    run-finder is parametrized on ``ICON_D2`` so it probes the D2 filename and
    honours D2's 8-cycle / 2h-delay schedule. As with ICON-EU the marker itself
    is horizon-agnostic; the freshness check applies horizon-awareness later.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_D2,
        find_latest_icon_eu_run_with_response,
    )
    now = datetime.now(timezone.utc)
    result = find_latest_icon_eu_run_with_response(
        target_time=now, cover_until=now, variant=ICON_D2,
    )
    if result is None:
        return None
    init_date, init_hour, resp = result
    init = datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )
    return Observation(
        init=init,
        published_at=_parse_last_modified(resp.headers.get("Last-Modified")),
        observed_via=VIA_HTTP_LAST_MODIFIED,
    )


def _parse_last_modified(header: str | None) -> datetime | None:
    """Parse a ``Last-Modified`` HTTP header into an aware UTC datetime.

    Returns ``None`` if the header is missing or unparseable — failures
    here must never break the freshness loop, so no exception is allowed
    to escape.
    """
    if not header:
        return None
    try:
        import email.utils

        # parsedate_to_datetime returns aware UTC for RFC-1123 HTTP dates.
        return email.utils.parsedate_to_datetime(header).astimezone(timezone.utc)
    except Exception:
        logger.debug("Failed to parse Last-Modified header %r", header, exc_info=True)
        return None


def _check_om_meta(model: str) -> Observation | None:
    """Return latest Open-Meteo init + publish time for ``model``.

    Guards ``last_availability_time == 0`` (set by OM when a model hasn't
    yet published) so we don't surface ``1970-01-01 00:00 UTC`` as the
    publish time in the popover.
    """
    from weatherbrief.fetch.model_status import fetch_model_metadata
    meta = fetch_model_metadata([model])
    if model not in meta:
        return None
    m = meta[model]
    published_at = (
        datetime.fromtimestamp(m.last_availability_time, tz=timezone.utc)
        if m.last_availability_time
        else None
    )
    data_end = (
        datetime.fromtimestamp(m.data_end_time, tz=timezone.utc)
        if m.data_end_time
        else None
    )
    return Observation(
        init=datetime.fromtimestamp(m.last_init_time, tz=timezone.utc),
        published_at=published_at,
        data_end=data_end,
        observed_via=VIA_OM_META,
    )


_DISPATCH = {
    "ecmwf_direct": _check_ecmwf_direct,
    "gfs_noaa": _check_gfs_noaa,
    "hrrr_noaa": _check_hrrr_noaa,
    "icon_eu_dwd": _check_icon_eu_dwd,
    "icon_d2_dwd": _check_icon_d2_dwd,
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
        observation = fn(model)
    except Exception:
        logger.warning(
            "check_source: dynamic check failed for %s/%s",
            source, model, exc_info=True,
        )
        return None
    if observation is not None and not observation.observed_via:
        # A new dispatch that forgot to declare its instrument. Harmless to
        # the freshness decision, but it would land unlabelled rows in the
        # delivery log — where the whole point is not to pool measurements
        # taken with different clocks. Loud, not fatal.
        logger.warning(
            "check_source: %s/%s returned an Observation with no observed_via — "
            "set one of the VIA_* constants in its dispatch",
            source, model,
        )
    return observation


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
