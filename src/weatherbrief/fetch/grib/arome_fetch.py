"""Météo-France AROME 0.025° GRIB2 download (#529, #940).

Builds the whole ``meteofrance`` briefing slot (#940): the sounding,
condensate and surface all come from AROME, and the slot never calls
Open-Meteo. Before #940 the slot was Open-Meteo ``meteofrance_seamless``
(AROME inside its footprint, ARPEGE elsewhere, the seam unmarked) with
AROME's condensate patched on top (#529).

Packages fetched per 6-hour group, all hourly:

* ``IP1``: ``t``, ``r``, ``z``, ``u``, ``v`` on 24 pressure levels
  (100–1000 hPa), ~408 MB.
* ``IP2``: ``clwc``, ``ciwc``, ``crwc``, ``cswc``, graupel and ``cc`` on the
  same levels, ~151 MB.
* ``SP1``: ``2t``, ``2r``, ``10u``/``10v``, the time-max gust, ``tp``
  (accumulated since the run start), ``prmsl``, total cloud, ~52 MB.
* ``SP2``: ``2d``, ``sp``, ``lcc``/``mcc``/``hcc``, ``CAPE_INS``, ~41 MB.

Source: the public OVH mirror, no key::

    {BASE}/{RUN}/arome/0025/{PKG}/arome__0025__{PKG}__{GROUP}__{RUN}.grib2

``RUN`` is ``2026-07-30T12:00:00Z``; ``GROUP`` is one of nine 6-hour package
groups (``00H06H`` … ``49H51H``). 8 runs a day, 51 h horizon, first group
published ≈ +3 h. Do **not** use ``object.data.gouv.fr/meteofrance-pnt``
(stale since 2026-05-11) or ``public-api.meteofrance.fr`` (keyed, no ranges).
The mirror keeps about 15 days of runs.

Volume is the constraint that shapes this module: one group of the four
packages is ~650 MB and holds every hour of its 6-hour span, so a briefing
downloads only the groups its flight window touches (one or two for a
typical GA flight; ``SP1`` of the group before, for the first hour's
precipitation) and the decode reads them message by message.
``WB_AROME_ENABLED=0`` turns the whole slot off (see :func:`arome_enabled`).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

AROME_BASE_URL = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt"

# Kill switch for the whole meteofrance slot (#940), on by default. Off means
# no meteofrance slot at all, never an Open-Meteo one.
AROME_ENABLED_ENV = "WB_AROME_ENABLED"


@dataclass(frozen=True)
class AromeConfig:
    """Static description of the AROME 0.025° product as we consume it.

    Attributes:
        slug: Cache-dir model key (``.cache/grib/arome/...``).
        source_key: Freshness ``SOURCE_REGISTRY`` key, also recorded as
            ``grib_sources["meteofrance"]`` on an enriched pack.
        resolution_token: Path token for the 0.025° stream (the 1.3 km
            stream is ``001`` and is not used here).
        cycles: UTC run hours, freshest first (run-finder order).
        publish_delay_hours: Earliest a run is worth probing after init.
            The 12z first group was observed landing at 15:13Z; later groups
            land after it, so the probe targets the group actually needed.
        horizon_h: Last forecast hour published.
        package: The condensate package (#529); the default of the
            per-package helpers.
        packages: Every package a briefing downloads (#940): the sounding
            (``IP1``), the condensate (``IP2``) and the surface (``SP1``,
            ``SP2``).
        pressure_levels: hPa levels the pressure packages carry.
        groups: ``(label, first_hour, last_hour)`` for each package group.
    """

    slug: str = "arome"
    source_key: str = "arome:mf"
    resolution_token: str = "0025"
    cycles: tuple[int, ...] = (21, 18, 15, 12, 9, 6, 3, 0)
    publish_delay_hours: float = 3.0
    horizon_h: int = 51
    package: str = "IP2"
    packages: tuple[str, ...] = ("IP1", "IP2", "SP1", "SP2")
    pressure_levels: tuple[int, ...] = (
        100, 125, 150, 175, 200, 225, 250, 275, 300, 350, 400, 450,
        500, 550, 600, 650, 700, 750, 800, 850, 900, 925, 950, 1000,
    )
    groups: tuple[tuple[str, int, int], ...] = (
        ("00H06H", 0, 6),
        ("07H12H", 7, 12),
        ("13H18H", 13, 18),
        ("19H24H", 19, 24),
        ("25H30H", 25, 30),
        ("31H36H", 31, 36),
        ("37H42H", 37, 42),
        ("43H48H", 43, 48),
        ("49H51H", 49, 51),
    )


AROME = AromeConfig()

# Bump when the decoded field set changes so a cached group is never read
# with the wrong expectations. The cached bytes are MF's file as published,
# so today this only guards against a future switch to byte-range subsets.
_CACHE_SCHEMA = "V1"

REQUEST_TIMEOUT = (10, 60)  # (connect, read) seconds; read is per chunk
_CHUNK_BYTES = 1 << 20


#: The package holding the accumulated precipitation, needed one hour before
#: the window so the first hour gets its own amount.
AROME_ACCUM_PACKAGE = "SP1"
#: Surface packages (decoded by ``decode_arome_surface``).
AROME_SURFACE_PACKAGES: tuple[str, ...] = ("SP1", "SP2")
#: Pressure-level packages (decoded by ``decode_arome_pressure``).
AROME_PRESSURE_PACKAGES: tuple[str, ...] = ("IP1", "IP2")


def arome_enabled() -> bool:
    """False only when ``WB_AROME_ENABLED`` is set to a falsy value (#940).

    Default on. Read at call time so a test or redeploy can flip it without
    reimporting.
    """
    raw = os.environ.get(AROME_ENABLED_ENV, "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _init_dt(init_date: str, init_hour: int) -> datetime:
    return datetime.strptime(f"{init_date}{init_hour:02d}", "%Y%m%d%H").replace(
        tzinfo=timezone.utc,
    )


def arome_run_token(init_date: str, init_hour: int) -> str:
    """``YYYYMMDD`` + hour → the mirror's run token ``2026-07-30T12:00:00Z``."""
    return _init_dt(init_date, init_hour).strftime("%Y-%m-%dT%H:00:00Z")


def arome_package_url(
    init_date: str,
    init_hour: int,
    group_label: str,
    config: AromeConfig = AROME,
    package: str | None = None,
) -> str:
    """URL of one package group file on the OVH mirror."""
    run = arome_run_token(init_date, init_hour)
    res = config.resolution_token
    pkg = package or config.package
    return (
        f"{AROME_BASE_URL}/{run}/arome/{res}/{pkg}/"
        f"arome__{res}__{pkg}__{group_label}__{run}.grib2"
    )


def arome_group_for_fhour(fhour: int, config: AromeConfig = AROME) -> str:
    """Package-group label holding forecast hour ``fhour``.

    Raises ``ValueError`` outside ``0..horizon_h`` — callers clamp first.
    """
    for label, first, last in config.groups:
        if first <= fhour <= last:
            return label
    raise ValueError(f"AROME forecast hour {fhour} outside 0..{config.horizon_h}")


def arome_groups_for_fhours(
    fhours: list[int], config: AromeConfig = AROME,
) -> dict[str, list[int]]:
    """Group the window's forecast hours by the package file that holds them.

    Ordered by first hour. This is the prefetch cap: only these groups are
    downloaded, never all nine.
    """
    out: dict[str, list[int]] = {}
    for fh in sorted(set(fhours)):
        out.setdefault(arome_group_for_fhour(fh, config), []).append(fh)
    return out


def arome_group_cache_key(
    group_label: str, config: AromeConfig = AROME, package: str | None = None,
) -> str:
    """Cache filename for one downloaded package group."""
    first = next(f for label, f, _ in config.groups if label == group_label)
    pkg = package or config.package
    return f"f{first:03d}_AROME_{pkg}_{group_label}_{_CACHE_SCHEMA}.grib2"


def compute_arome_flight_window_hours(
    init_date: str,
    init_hour: int,
    departure_time: datetime,
    flight_duration_hours: float,
    config: AromeConfig = AROME,
) -> list[int]:
    """Forecast hours covering the flight window: contiguous ``floor..ceil``.

    AROME output is hourly across its whole horizon, so — like HRRR — the
    covering set is the inclusive range from the hour at or before departure
    to the hour at or after arrival, clamped to ``[0, horizon_h]``. Not the
    sample-and-round shape, whose ties-to-even drops hours for :30 departures.
    """
    init_dt = _init_dt(init_date, init_hour)
    end_dt = departure_time + timedelta(hours=max(flight_duration_hours, 0.0))
    first = math.floor((departure_time - init_dt).total_seconds() / 3600)
    last = math.ceil((end_dt - init_dt).total_seconds() / 3600)
    first = max(0, min(first, config.horizon_h))
    last = max(first, min(last, config.horizon_h))
    return list(range(first, last + 1))


def _candidate_inits(reference_time: datetime, config: AromeConfig):
    """Yield publishable run inits, freshest first, as of ``reference_time``."""
    for days_back in range(3):
        day = reference_time - timedelta(days=days_back)
        for cycle in config.cycles:
            init_time = day.replace(hour=cycle, minute=0, second=0, microsecond=0)
            if init_time > reference_time:
                continue
            hours_since = (reference_time - init_time).total_seconds() / 3600
            if hours_since < config.publish_delay_hours:
                continue
            yield init_time


def arome_window_out_of_range(
    target_time: datetime,
    flight_duration_hours: float = 0.0,
    as_of_time: datetime | None = None,
    config: AromeConfig = AROME,
) -> bool:
    """True when no publishable run reaches the end of the flight window.

    Deterministic (no network). Separates the expected "beyond the 51 h
    horizon" skip from a probe/mirror failure — the run finder returns
    ``None`` for both.
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = target_time + timedelta(hours=flight_duration_hours)
    for init_time in _candidate_inits(reference_time, config):
        if init_time + timedelta(hours=config.horizon_h) >= need_until:
            return False
    return True


def arome_horizon_end(
    as_of_time: datetime | None = None, config: AromeConfig = AROME,
) -> datetime:
    """End of the newest publishable run's horizon (no network, #940).

    The meteofrance slot's horizon in the briefing fetch gate: a window
    ending after it has no slot (``MODEL_SKIPPED_HORIZON``).
    """
    reference_time = as_of_time or datetime.now(timezone.utc)
    newest = next(_candidate_inits(reference_time, config))
    return newest + timedelta(hours=config.horizon_h)


def arome_window_fhours(
    init_date: str,
    init_hour: int,
    hours_utc: list[datetime],
    config: AromeConfig = AROME,
) -> list[int]:
    """Forecast hours of ``hours_utc`` (whole UTC hours) in this run, sorted.

    Hours outside ``0..horizon_h`` are dropped: the caller's coverage check
    sees them missing.
    """
    init_dt = _init_dt(init_date, init_hour)
    out = set()
    for t in hours_utc:
        fh = round((t - init_dt).total_seconds() / 3600)
        if 0 <= fh <= config.horizon_h:
            out.add(fh)
    return sorted(out)


def arome_package_groups(
    fhours: list[int], config: AromeConfig = AROME,
) -> dict[str, dict[str, list[int]]]:
    """Package → group label → forecast hours to read, for a window (#940).

    Every package covers the window's groups. ``SP1`` also reads the hour
    before the window, which may sit in the previous group: the first window
    hour's precipitation is the difference of the two accumulations.
    """
    if not fhours:
        return {}
    window = arome_groups_for_fhours(fhours, config)
    out: dict[str, dict[str, list[int]]] = {
        pkg: {label: list(hs) for label, hs in window.items()}
        for pkg in config.packages
    }
    lead = min(fhours) - 1
    if lead >= 0 and AROME_ACCUM_PACKAGE in out:
        accum = out[AROME_ACCUM_PACKAGE]
        label = arome_group_for_fhour(lead, config)
        accum.setdefault(label, [])
        if lead not in accum[label]:
            accum[label].insert(0, lead)
        out[AROME_ACCUM_PACKAGE] = {
            k: accum[k] for k in sorted(accum, key=lambda g: _group_first(g, config))
        }
    return out


def _group_first(label: str, config: AromeConfig = AROME) -> int:
    return next(f for g, f, _ in config.groups if g == label)


def find_latest_arome_run_with_response(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
    config: AromeConfig = AROME,
) -> tuple[str, int, requests.Response] | None:
    """Find the freshest published run covering the window + its probe response.

    Walks cycles freshest-first, skipping runs not yet due or whose 51 h
    horizon stops short of ``cover_until``. The probe HEADs the group file
    holding the LAST hour the flight needs: groups are published in order,
    so a run whose first group is up may not have the one we want yet.

    Returns ``(init_date_YYYYMMDD, init_hour, head_response)`` or ``None``.
    """
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time

    for init_time in _candidate_inits(reference_time, config):
        if init_time + timedelta(hours=config.horizon_h) < need_until:
            continue
        last_needed = math.ceil((need_until - init_time).total_seconds() / 3600)
        last_needed = max(0, min(last_needed, config.horizon_h))
        date_str = init_time.strftime("%Y%m%d")
        url = arome_package_url(
            date_str, init_time.hour, arome_group_for_fhour(last_needed, config),
            config,
        )
        try:
            resp = sess.head(url, timeout=10)
        except requests.RequestException:
            logger.debug("AROME probe failed: %s", url, exc_info=True)
            continue
        if resp.status_code == 200:
            logger.info(
                "Found AROME run: %s %02dz (probed f%03d)",
                date_str, init_time.hour, last_needed,
            )
            return date_str, init_time.hour, resp
    return None


def find_latest_arome_run(
    target_time: datetime,
    session: requests.Session | None = None,
    as_of_time: datetime | None = None,
    cover_until: datetime | None = None,
) -> tuple[str, int] | None:
    """:func:`find_latest_arome_run_with_response` without the response."""
    found = find_latest_arome_run_with_response(
        target_time, session, as_of_time, cover_until,
    )
    return (found[0], found[1]) if found is not None else None


class AromeDownloadError(RuntimeError):
    """A group download ended short of its advertised length."""


def _iter_group_chunks(url: str, session: requests.Session):
    """Stream one group file, raising if it ends short of Content-Length.

    Raising inside the iterator makes ``put_cached_from_chunks`` discard the
    tempfile, so a truncated download is never cached as a valid group.
    """
    with session.get(url, stream=True, timeout=REQUEST_TIMEOUT) as resp:
        resp.raise_for_status()
        expected = resp.headers.get("Content-Length")
        total = 0
        for chunk in resp.iter_content(chunk_size=_CHUNK_BYTES):
            if chunk:
                total += len(chunk)
                yield chunk
        if expected is not None and expected.isdigit() and total != int(expected):
            raise AromeDownloadError(
                f"AROME download truncated: {total} of {expected} bytes from {url}"
            )


def download_arome_group(
    init_date: str,
    init_hour: int,
    group_label: str,
    run_dir: Path,
    session: requests.Session,
    config: AromeConfig = AROME,
    package: str | None = None,
) -> Path | None:
    """Download one package group into the run cache. Returns its path or None.

    Streams straight to the cache file so the payload (up to ~410 MB for
    ``IP1``) never sits in memory. A cached group is reused as-is (cache TTL
    governs expiry).
    """
    from weatherbrief.fetch.grib.cache import is_cached, put_cached_from_chunks

    pkg = package or config.package
    filename = arome_group_cache_key(group_label, config, pkg)
    if is_cached(run_dir, filename):
        return run_dir / filename
    url = arome_package_url(init_date, init_hour, group_label, config, pkg)
    try:
        path = put_cached_from_chunks(
            run_dir, filename, _iter_group_chunks(url, session),
        )
    except Exception:
        logger.warning("AROME group download failed: %s", url, exc_info=True)
        return None
    if path is not None:
        logger.info(
            "AROME %s %s %02dz %s: %.1f MB",
            pkg, init_date, init_hour, group_label,
            path.stat().st_size / 1e6,
        )
    return path
