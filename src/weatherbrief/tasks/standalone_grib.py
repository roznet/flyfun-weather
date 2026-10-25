"""GRIB cloud diagnostics adapter for standalone verification.

Thin wrapper around existing GRIB fetch/decode to extract ceiling and cloud base
at airport coordinates. Reuses the shared GRIB cache — no duplicate downloads.

Decode goes through the shared pool by cache path, same as the briefing
path (``_fetch_cloud_diag_for_fhour``) — never in this process. The per-hour
decodes are collected and fanned out via ``_dispatch_decode_parallel`` (#459)
so pool workers overlap instead of one blocking dispatch at a time. cfgrib/xarray
decode of full-domain grids in the orchestrating process was a parent-RSS
contributor before issue #236 (the dispatcher exists precisely to keep that
out of the parent). The subprocess cycle runs its own small pool since #448
PR B (``GRIB_DECODE_WORKERS`` = ``STANDALONE_ANALYSIS_WORKERS``, default 2;
``0`` restores inline dispatch, which is fine — that process is disposable).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,
    is_cached,
    purge_old_runs,
    put_cached,
)

if TYPE_CHECKING:
    from weatherbrief.fetch.grib import DecodePriority
    from weatherbrief.fetch.grib.icon_eu_fetch import IconVariant
    from weatherbrief.models.analysis import NWPCloudDiagnostics

logger = logging.getLogger(__name__)


@dataclass
class AirportGribDiagnostics:
    """GRIB diagnostics extracted for one airport at one forecast hour.

    Carries the subset of :class:`NWPCloudDiagnostics` that the standalone
    snapshot persists. It used to hold ceiling and cloud base *only*, which
    made it the narrowest point in the pipeline: the GFS and ICON cloud-diag
    decode already produced convective cover, base, top, precipitation rate and
    mixed-layer CAPE/CIN, and every one of them was thrown away here — after
    being downloaded and decoded (#565/#566).

    ``diagnostics`` keeps the full object the scalars were projected from, so
    the standalone cycle can attach it to the hour *before* the sounding pass
    and grade GFS/ICON through the native convective track, as ECMWF does
    (#635). It is not persisted itself.
    """

    nwp_ceiling_ft: float | None = None
    cloud_base_ft: float | None = None
    convective_cover_pct: float | None = None
    convective_base_ft: float | None = None
    convective_top_ft: float | None = None
    convective_precip_mm_h: float | None = None
    ml_cape_jkg: float | None = None
    ml_cin_jkg: float | None = None
    diagnostics: "NWPCloudDiagnostics | None" = None


def _diagnostics_from(diag) -> AirportGribDiagnostics:
    """Project an ``NWPCloudDiagnostics`` onto the persisted subset.

    Shared by the GFS and ICON fetchers so the two cannot drift — they build
    their diagnostics through different decoders but persist the same fields.
    """
    return AirportGribDiagnostics(
        nwp_ceiling_ft=diag.ceiling_ft,
        # Lowest available cloud base (low layer).
        cloud_base_ft=diag.low.base_ft if diag.low else None,
        convective_cover_pct=diag.convective_cover_pct,
        convective_base_ft=diag.convective_base_ft,
        convective_top_ft=diag.convective_top_ft,
        convective_precip_mm_h=diag.convective_precip_mm_h,
        ml_cape_jkg=diag.ml_cape_jkg,
        ml_cin_jkg=diag.ml_cin_jkg,
        diagnostics=diag,
    )


def fetch_gfs_cloud_diag(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    session: requests.Session | None = None,
    priority: "int | DecodePriority | None" = None,
) -> dict[int, list[AirportGribDiagnostics]]:
    """Fetch GFS cloud diagnostics for airports at specified forecast hours.

    Args:
        priority: Decode priority for the dispatched cloud-diag jobs. ``None``
            (default) falls through to the decode-priority ContextVar via
            ``_resolve_priority`` — so an interactive briefing's alternates
            decode inherits INTERACTIVE. The standalone verification cycle
            passes ``DecodePriority.BACKGROUND`` explicitly.

    Returns:
        Dict mapping forecast_hour → list of AirportGribDiagnostics (same order
        as lats/lons).
        Missing hours are omitted from the dict.
    """
    from weatherbrief.fetch.grib import _dispatch_decode_parallel, _grib_session
    from weatherbrief.fetch.grib.decode import build_cloud_diagnostics
    from weatherbrief.fetch.grib.gfs_idx import plan_cloud_diag_byte_ranges
    from weatherbrief.fetch.grib.grib_fetch import fetch_cloud_diag_ranges, fetch_idx

    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        session = _grib_session()

    # TTL purge, as the briefing enrichment path does: on a compute node this is
    # the only thing that ages the cache (no scheduler retention pass runs there),
    # and without it every cycle's run accumulated forever (142 GB on the mini).
    purge_old_runs(data_dir, model="gfs")
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")
    result: dict[int, list[AirportGribDiagnostics]] = {}

    # Phase 1: ensure each hour is cached (sequential network I/O), collecting
    # the decode jobs to fan out. Decode is GIL-bound and was walked one
    # blocking dispatch per hour (#459); batching lets the pool workers actually
    # overlap. No dedicated concurrency cap — this leg is light (single field).
    decode_jobs: list[tuple[str, tuple]] = []
    job_fhours: list[int] = []
    for fhour in forecast_hours:
        ck = cache_key(fhour, "CLOUD_DIAG")

        if not is_cached(run_dir, ck):
            try:
                idx_text = fetch_idx(init_date, init_hour, fhour, session=session)
                ranges = plan_cloud_diag_byte_ranges(idx_text)
                if not ranges:
                    continue
                grib_bytes = fetch_cloud_diag_ranges(
                    init_date, init_hour, fhour, ranges, session=session,
                )
                put_cached(run_dir, ck, grib_bytes)
                del grib_bytes
            except Exception:
                logger.warning("GFS cloud diag fetch failed f%03d", fhour, exc_info=True)
                continue

        decode_jobs.append(
            ("decode_gfs_cloud_diag", (str(run_dir / ck), lats, lons)),
        )
        job_fhours.append(fhour)

    if not decode_jobs:
        return result

    # Phase 2: fan the decodes out through the pool. TOCTOU: the decode worker
    # re-reads the path, so a TTL expiry between is_cached and decode surfaces
    # as a per-job exception here and skips that fhour (snapshot just lacks
    # nwp_ceiling — same graceful degradation as any decode failure). TTLs are
    # 12-24h, the window is milliseconds, and the briefing path
    # (_fetch_cloud_diag_for_fhour) accepts the identical race.
    decoded_all = _dispatch_decode_parallel(
        decode_jobs, priority=priority, return_exceptions=True,
    )

    for fhour, decoded in zip(job_fhours, decoded_all):
        if isinstance(decoded, Exception):
            logger.warning(
                "GFS cloud diag decode failed f%03d", fhour, exc_info=decoded,
            )
            continue
        if not decoded:
            continue

        airport_data: list[AirportGribDiagnostics] = []
        for raw in decoded:
            diag = build_cloud_diagnostics(raw)
            if diag is None:
                airport_data.append(AirportGribDiagnostics())
            else:
                airport_data.append(_diagnostics_from(diag))
        result[fhour] = airport_data

    return result


def fetch_icon_cloud_diag(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    session: requests.Session | None = None,
    priority: "int | DecodePriority | None" = None,
    variant: "IconVariant | None" = None,
) -> dict[int, list[AirportGribDiagnostics]]:
    """Fetch ICON cloud diagnostics for airports at specified forecast hours.

    Same interface as fetch_gfs_cloud_diag (incl. the ``priority`` pass-through)
    but uses DWD ICON-EU data source, or ICON-D2 with ``variant`` (#928; its
    ``cloud_diag_variables``, no ``rain_con`` and so no predecessor step).

    ``rain_con`` (cfgrib ``crr``) is accumulated since init, so a sampled hour
    on its own yields no convective precipitation rate. Each sampled hour is
    paired with its predecessor step on the ICON grid and de-accumulated the
    same way the briefing path's enrichment loop does (#585). The verification
    grid samples ~24 h apart, so the predecessor is almost never itself a
    sampled hour; it is fetched as a ``rain_con``-only blob (one file, not the
    twelve of the full cloud-diag set) unless a full blob is already cached.
    """
    from weatherbrief.fetch.grib import _dispatch_decode_parallel, _grib_session
    from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU,
        icon_cloud_diag_cache_key,
        icon_eu_conv_rain_rate_mm_h,
    )

    variant = variant or ICON_EU
    diag_key = icon_cloud_diag_cache_key(variant)
    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        session = _grib_session()

    purge_old_runs(data_dir, model=variant.slug)  # see fetch_gfs_cloud_diag
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=variant.slug)
    result: dict[int, list[AirportGribDiagnostics]] = {}

    # Phase 1: ensure each hour is cached (sequential network I/O), then fan
    # the decodes out — see fetch_gfs_cloud_diag for the rationale (#459).
    job_fhours, predecessor_of, preds = _ensure_icon_cloud_diag_cached(
        run_dir, init_date, init_hour, forecast_hours, variant, session,
    )
    decode_jobs: list[tuple[str, tuple]] = [
        ("decode_icon_cloud_diag", (str(run_dir / cache_key(fhour, diag_key)), lats, lons))
        for fhour in job_fhours
    ]
    pred_jobs: list[tuple[str, tuple]] = [
        ("decode_icon_cloud_diag", (str(run_dir / ck), lats, lons)) for _, ck in preds
    ]
    pred_fhours: list[int] = [fhour for fhour, _ in preds]

    if not decode_jobs:
        return result

    # Phase 2: fan the decodes out through the pool. TOCTOU window accepted —
    # see the GFS branch above.
    decoded_all = _dispatch_decode_parallel(
        decode_jobs + pred_jobs, priority=priority, return_exceptions=True,
    )

    # Decoded raw dicts by step, sampled and predecessor alike, so a sampled
    # hour whose predecessor is itself sampled reuses that decode.
    raw_by_step: dict[int, list[dict]] = {}
    for fhour, decoded in zip(job_fhours + pred_fhours, decoded_all):
        if isinstance(decoded, Exception):
            logger.warning(
                "ICON cloud diag decode failed f%03d", fhour, exc_info=decoded,
            )
            continue
        if decoded:
            raw_by_step[fhour] = decoded

    for fhour in job_fhours:
        decoded = raw_by_step.get(fhour)
        if not decoded:
            continue

        prev_step = predecessor_of.get(fhour)
        prev_decoded = (
            raw_by_step.get(prev_step) if prev_step is not None else None
        )
        # Same init, so the window is the step difference: 1 h inside ICON's
        # hourly region, 3 h past +78 h.
        window_h = (fhour - prev_step) if prev_decoded else None
        if prev_decoded and len(prev_decoded) < len(decoded):
            # Same lats/lons on both decodes, so this should not happen; the
            # missing points would otherwise go NULL with no trace (#585).
            logger.warning(
                "ICON rain_con predecessor f%03d decoded %d points, f%03d has %d",
                prev_step, len(prev_decoded), fhour, len(decoded),
            )

        airport_data: list[AirportGribDiagnostics] = []
        for i, raw in enumerate(decoded):
            diag = build_icon_cloud_diagnostics(raw)
            if diag is None:
                airport_data.append(AirportGribDiagnostics())
                continue
            prev_raw = (
                prev_decoded[i]
                if prev_decoded is not None and i < len(prev_decoded)
                else {}
            )
            rate = icon_eu_conv_rain_rate_mm_h(
                raw.get("conv_rain_kg_m2"), prev_raw.get("conv_rain_kg_m2"),
                window_h,
            )
            if rate is not None:
                # Rebuild rather than mutate, matching the briefing path's
                # immutability contract for NWPCloudDiagnostics.
                diag = diag.model_copy(update={"convective_precip_mm_h": rate})
            airport_data.append(_diagnostics_from(diag))
        result[fhour] = airport_data

    return result


def _ensure_icon_cloud_diag_cached(
    run_dir: Path,
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    variant: "IconVariant",
    session: requests.Session,
) -> tuple[list[int], dict[int, int], list[tuple[int, str]]]:
    """Download half of :func:`fetch_icon_cloud_diag`: cache what *run_dir* lacks.

    Returns the steps whose cloud-diag blob is cached, ``predecessor_of``
    (cached step -> its rain_con predecessor) and the ``(step, cache filename)``
    of each predecessor blob that is cached. Never raises for a step: a failed
    download is logged and leaves that step out.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_RAIN_CON_CACHE_KEY,
        fetch_icon_eu_single_level,
        icon_cloud_diag_cache_key,
        icon_eu_previous_step,
    )

    diag_key = icon_cloud_diag_cache_key(variant)
    job_fhours: list[int] = []
    for fhour in forecast_hours:
        ck = cache_key(fhour, diag_key)

        if not is_cached(run_dir, ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session, variant=variant,
                )
                grib_bytes = fetched.get(fhour)
                if not grib_bytes:
                    continue
                put_cached(run_dir, ck, grib_bytes)
                del grib_bytes
            except Exception:
                logger.warning("ICON cloud diag fetch failed f%03d", fhour, exc_info=True)
                continue
        job_fhours.append(fhour)

    # Predecessor steps, only to seed the rain_con de-accumulation, and only
    # for hours that are actually going to be decoded. A failure here costs
    # the rate for one sampled hour and nothing else.
    predecessor_of: dict[int, int] = {}
    for fhour in job_fhours if "rain_con" in variant.cloud_diag_variables else ():
        prev = icon_eu_previous_step(fhour, variant)
        if prev is not None:
            predecessor_of[fhour] = prev
    extra_steps = sorted(set(predecessor_of.values()) - set(job_fhours))

    preds: list[tuple[int, str]] = []
    for fhour in extra_steps:
        full_ck = cache_key(fhour, diag_key)
        ck = (
            full_ck if is_cached(run_dir, full_ck)
            else cache_key(fhour, ICON_EU_RAIN_CON_CACHE_KEY)
        )
        if not is_cached(run_dir, ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour],
                    variables=["rain_con"], session=session,
                )
                grib_bytes = fetched.get(fhour)
                if not grib_bytes:
                    continue
                put_cached(run_dir, ck, grib_bytes)
                del grib_bytes
            except Exception:
                logger.warning(
                    "ICON rain_con predecessor fetch failed f%03d", fhour,
                    exc_info=True,
                )
                continue
        preds.append((fhour, ck))
    return job_fhours, predecessor_of, preds


def icon_cloud_diag_cold_steps(
    run_dir: Path, steps: list[int], variant: "IconVariant",
) -> list[int]:
    """The *steps* whose cloud-diag read would download: the step's blob, or
    (where the variant has rain_con) its predecessor as a full or
    rain_con-only blob, is not in *run_dir*. The cache-side mirror of
    :func:`_ensure_icon_cloud_diag_cached`."""
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_RAIN_CON_CACHE_KEY,
        icon_cloud_diag_cache_key,
        icon_eu_previous_step,
    )

    diag_key = icon_cloud_diag_cache_key(variant)
    has_rain_con = "rain_con" in variant.cloud_diag_variables
    cold = []
    for fhour in steps:
        if not is_cached(run_dir, cache_key(fhour, diag_key)):
            cold.append(fhour)
            continue
        prev = icon_eu_previous_step(fhour, variant) if has_rain_con else None
        if prev is not None and not any(
            is_cached(run_dir, cache_key(prev, k))
            for k in (diag_key, ICON_EU_RAIN_CON_CACHE_KEY)
        ):
            cold.append(fhour)
    return cold


def icon_snapshot_cold_steps(
    run_dir: Path, init_date: str, init_hour: int, steps: list[int],
    variant: "IconVariant",
) -> list[int]:
    """The *steps* whose snapshot read would download: a file of the step, or
    its predecessor's accumulated surface (:func:`_icon_snapshot_plan`), is
    not in *run_dir*."""
    wanted, predecessor_of = _icon_snapshot_plan(init_date, init_hour, steps, variant)
    cold = {key[0] for key, (_url, ck) in wanted.items() if not is_cached(run_dir, ck)}
    return sorted(f for f in steps if f in cold or predecessor_of.get(f) in cold)


def ensure_icon_cloud_diag_files(
    init_date: str,
    init_hour: int,
    steps: list[int],
    *,
    variant: "IconVariant | None" = None,
    session: requests.Session | None = None,
    data_dir: Path | None = None,
) -> list[int]:
    """Download-only half of :func:`fetch_icon_cloud_diag`; the steps now cached.

    Same files under the same cache keys (the cloud-diag blob per step, the
    rain_con predecessor where the variant has it), no decode, so a pre-warm
    (#929) leaves exactly what the alternates' cloud diagnostics read.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU

    variant = variant or ICON_EU
    steps = sorted(set(steps))
    if not steps:
        return []
    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        with requests.Session() as own_session:
            return ensure_icon_cloud_diag_files(
                init_date, init_hour, steps,
                variant=variant, session=own_session, data_dir=data_dir,
            )
    purge_old_runs(data_dir, model=variant.slug)  # see fetch_gfs_cloud_diag
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=variant.slug)
    cached, _, _ = _ensure_icon_cloud_diag_cached(
        run_dir, init_date, init_hour, steps, variant, session,
    )
    return cached


@dataclass
class GribSnapshotStep:
    """One ICON or GFS forecast step's raw fields at the airports (#914).

    Per-point lists in ``lats``/``lons`` order. ``pressure`` is the
    ``{level_hpa: {raw_key: value}}`` shape ``build_pressure_levels_from_grib``
    takes; ``surface`` / ``prev_surface`` are keyed by the producer's variable
    name (DWD's, or the GFS .idx name; an ICON predecessor carries the
    accumulated fields only), ``window_h`` the hours between them.
    ``prev_surface`` is None when the predecessor could not be had, which
    leaves the step without precipitation.
    """

    pressure: list[dict[int, dict[str, float]]]
    surface: list[dict[str, float]]
    prev_surface: list[dict[str, float]] | None
    window_h: int | None


# A DWD snapshot file that 404'd is not asked for again for this long, in this
# process (#928): a late or partial run would otherwise cost one 404 per file
# on every alternates search. Short, because "not yet published" heals within
# minutes. Snapshot files only (``_download_icon_files``): the cloud-diag
# fetch still asks once per hour.
_ICON_404_TTL_S = 600.0
_ICON_404_UNTIL: dict[str, float] = {}
_ICON_404_LOCK = threading.Lock()


def _recent_404(url: str, now: float) -> bool:
    with _ICON_404_LOCK:
        until = _ICON_404_UNTIL.get(url)
        if until is None:
            return False
        if until <= now:
            del _ICON_404_UNTIL[url]
            return False
        return True


def _note_404(url: str, now: float) -> None:
    with _ICON_404_LOCK:
        # Expired entries go here too, so a long-lived worker doesn't keep
        # every URL that ever 404'd.
        for stale in [u for u, until in _ICON_404_UNTIL.items() if until <= now]:
            del _ICON_404_UNTIL[stale]
        _ICON_404_UNTIL[url] = now + _ICON_404_TTL_S


def _icon_snapshot_plan(
    init_date: str, init_hour: int, steps: list[int], variant,
) -> tuple[dict[tuple, tuple[str, str]], dict[int, int]]:
    """``(wanted, predecessor_of)`` for an ICON snapshot fetch (#914, #928).

    ``wanted`` maps ``(step, "pl", level, var)`` / ``(step, "sfc", var)`` to
    ``(url, cache filename)``: per step the variant's isobaric files and the
    #912 surface files, plus the accumulated surface files of each step's
    predecessor so precipitation can be de-accumulated.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_SNAPSHOT_PRESSURE_VARIABLES,
        ICON_SURFACE_ACCUMULATED,
        ICON_SURFACE_VARIABLES,
        icon_eu_pressure_level_url,
        icon_eu_previous_step,
        icon_eu_single_level_url,
        icon_pressure_cache_key,
        icon_snapshot_pressure_levels,
        icon_surface_cache_key,
    )

    predecessor_of = {
        fhour: prev for fhour in steps
        if (prev := icon_eu_previous_step(fhour, variant)) is not None
    }
    wanted: dict[tuple, tuple[str, str]] = {}
    for fhour in steps:
        for level in icon_snapshot_pressure_levels(variant):
            for var in ICON_SNAPSHOT_PRESSURE_VARIABLES:
                wanted[(fhour, "pl", level, var)] = (
                    icon_eu_pressure_level_url(
                        init_date, init_hour, fhour, level, var, variant,
                    ),
                    cache_key(fhour, icon_pressure_cache_key(var, level, variant)),
                )
        for var in ICON_SURFACE_VARIABLES:
            wanted[(fhour, "sfc", var)] = (
                icon_eu_single_level_url(init_date, init_hour, fhour, var, variant),
                cache_key(fhour, icon_surface_cache_key(var, variant)),
            )
    for prev in set(predecessor_of.values()):
        for var in ICON_SURFACE_ACCUMULATED:
            wanted.setdefault((prev, "sfc", var), (
                icon_eu_single_level_url(init_date, init_hour, prev, var, variant),
                cache_key(prev, icon_surface_cache_key(var, variant)),
            ))
    return wanted, predecessor_of


def _download_icon_files(
    run_dir: Path,
    files: list[tuple[str, str]],
    session: requests.Session,
    label: str,
) -> int:
    """Download the ``(url, cache filename)`` pairs the run dir lacks; bytes stored.

    In parallel (the per-file helper and worker count the ICON fetchers use).
    One file failing costs that file only. A 404 is remembered for
    ``_ICON_404_TTL_S`` and not asked again meanwhile.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor

    from weatherbrief.fetch.grib.icon_eu_fetch import (
        _default_download_workers,
        _download_one_file,
        _format_failure_summary,
    )

    now = time.monotonic()
    missing = [(url, ck) for url, ck in files if not is_cached(run_dir, ck)]
    recent = [item for item in missing if _recent_404(item[0], now)]
    missing = [item for item in missing if not _recent_404(item[0], now)]
    if not missing:
        if recent:
            logger.info("%s: %d file(s) skipped, 404 within %ds", label,
                        len(recent), int(_ICON_404_TTL_S))
        return 0

    def _get(item: tuple[str, str]) -> tuple[int | str, int]:
        url, ck = item
        try:
            data, status = _download_one_file(url, session)
            if data is None:
                return status, 0
            put_cached(run_dir, ck, data)
        except Exception:  # e.g. a full disk: costs this file, not the step
            logger.debug("ICON snapshot file %s failed", ck, exc_info=True)
            return "error", 0
        return 200, len(data)

    failures: dict[int | str, int] = {}
    failed_names: list[str] = []
    stored = 0
    with ThreadPoolExecutor(max_workers=_default_download_workers()) as pool:
        for (url, _ck), (status, n_bytes) in zip(missing, pool.map(_get, missing)):
            stored += n_bytes
            if status != 200:
                failures[status] = failures.get(status, 0) + 1
                failed_names.append(url.rsplit("/", 1)[-1])
                if status == 404:
                    _note_404(url, time.monotonic())
    n_failed = sum(failures.values())
    log = logger.warning if n_failed else logger.info
    log(
        "%s: downloaded %d/%d files, %.1f MB%s%s%s",
        label, len(missing) - n_failed, len(missing), stored / 1e6,
        _format_failure_summary(failures),
        f": {', '.join(failed_names[:6])}" if failed_names else "",
        f"; {len(recent)} skipped, 404 within {int(_ICON_404_TTL_S)}s" if recent else "",
    )
    return stored


def ensure_icon_snapshot_files(
    init_date: str,
    init_hour: int,
    steps: list[int],
    *,
    variant: "IconVariant | None" = None,
    session: requests.Session | None = None,
    data_dir: Path | None = None,
) -> int:
    """Download-only half of :func:`fetch_icon_snapshot_fields`; bytes stored.

    The same files under the same cache keys (isobaric + surface per step,
    accumulated surface of each predecessor), no decode, so a pre-warm (#929)
    leaves exactly what the snapshot path reads. Never raises for a file.
    """
    from weatherbrief.fetch.grib import _grib_session
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU

    variant = variant or ICON_EU
    steps = sorted(set(steps))
    if not steps:
        return 0
    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        with _grib_session() as own_session:
            return ensure_icon_snapshot_files(
                init_date, init_hour, steps,
                variant=variant, session=own_session, data_dir=data_dir,
            )
    purge_old_runs(data_dir, model=variant.slug)  # see fetch_gfs_cloud_diag
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=variant.slug)
    wanted, _ = _icon_snapshot_plan(init_date, init_hour, steps, variant)
    return _download_icon_files(
        run_dir, list(wanted.values()), session,
        f"ICON snapshot GRIB {variant.slug} {init_date}{init_hour:02d}z "
        f"({len(steps)} step(s))",
    )


def fetch_icon_snapshot_fields(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    session: requests.Session | None = None,
    priority: "int | DecodePriority | None" = None,
    variant: "IconVariant | None" = None,
) -> dict[int, GribSnapshotStep]:
    """ICON pressure-level sounding + surface fields at airports (#914, #928).

    The GRIB-primary source for the ICON forecast-map and alternates
    snapshots, from ICON-EU (default) or ICON-D2 (``variant``). Downloads, per
    forecast hour, the isobaric files (``icon_snapshot_pressure_levels``) and
    the #912 single-level surface files, plus the accumulated surface fields
    of the preceding published step so precipitation can be de-accumulated.
    Every file is its own cache entry in the variant's run directory, so the
    surface files a briefing already pulled are reused, and a second
    alternates search on the same run is decode-only.

    A forecast hour with no pressure-level or no surface file at all is
    absent from the result; partial levels are kept (the caller decides
    whether a point is covered). Never raises for a download or decode
    failure: those are logged and cost the hour.
    """
    from weatherbrief.fetch.grib import _dispatch_decode_parallel, _grib_session
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU,
        ICON_SNAPSHOT_PRESSURE_VARIABLES,
        ICON_SURFACE_ACCUMULATED,
        ICON_SURFACE_VARIABLES,
        icon_snapshot_pressure_levels,
    )

    if not forecast_hours:
        return {}
    variant = variant or ICON_EU
    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    if session is None:
        with _grib_session() as own_session:
            return fetch_icon_snapshot_fields(
                init_date, init_hour, forecast_hours, lats, lons,
                data_dir=data_dir, session=own_session, priority=priority,
                variant=variant,
            )

    purge_old_runs(data_dir, model=variant.slug)  # see fetch_gfs_cloud_diag
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=variant.slug)

    steps = sorted(set(forecast_hours))
    wanted, predecessor_of = _icon_snapshot_plan(init_date, init_hour, steps, variant)
    # Phase 1: download what the run directory lacks.
    _download_icon_files(
        run_dir, list(wanted.values()), session,
        f"ICON snapshot GRIB {variant.slug} {init_date}{init_hour:02d}z "
        f"({len(steps)} step(s))",
    )

    def _path(key: tuple) -> str | None:
        ck = wanted[key][1]
        return str(run_dir / ck) if is_cached(run_dir, ck) else None

    # Phase 2: one pressure job and one surface job per step, plus one surface
    # job per predecessor that is not itself a sampled step.
    jobs: list[tuple[str, tuple]] = []
    job_keys: list[tuple[str, int]] = []
    for fhour in steps:
        level_paths: dict[int, dict[str, str]] = {}
        for level in icon_snapshot_pressure_levels(variant):
            for var in ICON_SNAPSHOT_PRESSURE_VARIABLES:
                path = _path((fhour, "pl", level, var))
                if path is not None:
                    level_paths.setdefault(level, {})[var] = path
        if level_paths:
            jobs.append(("decode_icon_pressure", (level_paths, lats, lons)))
            job_keys.append(("pl", fhour))
    surface_steps = sorted(set(steps) | set(predecessor_of.values()))
    for fhour in surface_steps:
        var_names = ICON_SURFACE_VARIABLES if fhour in steps else ICON_SURFACE_ACCUMULATED
        var_paths = {
            var: path for var in var_names
            if (path := _path((fhour, "sfc", var))) is not None
        }
        if var_paths:
            jobs.append(("decode_icon_surface", (var_paths, lats, lons)))
            job_keys.append(("sfc", fhour))

    if not jobs:
        return {}
    decoded = _dispatch_decode_parallel(jobs, priority=priority, return_exceptions=True)

    pressure_by_step: dict[int, list] = {}
    surface_by_step: dict[int, list] = {}
    for (kind, fhour), res in zip(job_keys, decoded):
        if isinstance(res, Exception):
            logger.warning("ICON snapshot %s decode failed f%03d", kind, fhour, exc_info=res)
            continue
        if res:
            (pressure_by_step if kind == "pl" else surface_by_step)[fhour] = res

    result: dict[int, GribSnapshotStep] = {}
    for fhour in steps:
        pressure = pressure_by_step.get(fhour)
        surface = surface_by_step.get(fhour)
        if not pressure or not surface:
            continue
        prev_step = predecessor_of.get(fhour)
        prev_surface = surface_by_step.get(prev_step) if prev_step is not None else None
        result[fhour] = GribSnapshotStep(
            pressure=pressure,
            surface=surface,
            prev_surface=prev_surface,
            window_h=(fhour - prev_step) if prev_surface is not None else None,
        )
    return result


def fetch_gfs_snapshot_fields(
    init_date: str,
    init_hour: int,
    forecast_hours: list[int],
    lats: list[float],
    lons: list[float],
    data_dir: Path | None = None,
    priority: "int | DecodePriority | None" = None,
) -> dict[int, GribSnapshotStep]:
    """GFS pressure-level sounding + surface fields at airports (#914).

    The GFS counterpart of :func:`fetch_icon_snapshot_fields`, on #913's
    reader (``read_gfs_grib_points``): per forecast hour the
    ``gfs_sounding_levels()`` column of ``GFS_SNAPSHOT_VARIABLES`` and the
    #913 surface blob, plus the surface of the native step before
    each hour so precipitation can be de-accumulated (map hours are not
    contiguous). The surface blobs are the briefing's own cache entries; the
    reduced column caches under its own key.

    An hour with no sounding or no surface is absent from the result. Never
    raises: a failure is logged and costs the hours it touched.
    """
    from weatherbrief.fetch.grib import (
        _gfs_previous_step,
        _grib_session,
        gfs_sounding_levels,
        read_gfs_grib_points,
    )
    from weatherbrief.fetch.grib.cache import pin_run_dir
    from weatherbrief.fetch.grib.gfs_idx import GFS_SNAPSHOT_VARIABLES

    if not forecast_hours:
        return {}
    if data_dir is None:
        data_dir = Path(os.environ.get("DATA_DIR", "data"))
    purge_old_runs(data_dir, model="gfs")  # see fetch_gfs_cloud_diag
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")
    steps = sorted(set(forecast_hours))
    try:
        # Own session (the concurrent cloud-diag thread owns the caller's),
        # pooled for the reader's parallel downloads; pinned so a purge from
        # another caller can't evict the run mid-read.
        with _grib_session() as session, pin_run_dir(run_dir):
            sounding, surface, _ = read_gfs_grib_points(
                init_date, init_hour, steps, lats, lons,
                run_dir=run_dir, session=session,
                levels=gfs_sounding_levels(), variables=GFS_SNAPSHOT_VARIABLES,
                every_previous_step=True, priority=priority,
            )
    except Exception:
        # The whole GFS leg on the Open-Meteo fallback: distinct from the
        # per-hour misses the reader logs itself, so it can be alerted on.
        logger.error(
            "GFS_SNAPSHOT_GRIB_FAILED %s%02dz: every GFS row to Open-Meteo",
            init_date, init_hour, exc_info=True,
        )
        return {}

    result: dict[int, GribSnapshotStep] = {}
    for fhour in steps:
        if not sounding.get(fhour) or not surface.get(fhour):
            continue
        prev_step = _gfs_previous_step(fhour)
        prev_surface = surface.get(prev_step) if prev_step is not None else None
        result[fhour] = GribSnapshotStep(
            pressure=sounding[fhour],
            surface=surface[fhour],
            prev_surface=prev_surface,
            window_h=(fhour - prev_step) if prev_surface is not None else None,
        )
    logger.info(
        "GFS snapshot GRIB %s%02dz: %d/%d step(s) with sounding and surface",
        init_date, init_hour, len(result), len(steps),
    )
    return result


def datetime_to_init_parts(dt: datetime) -> tuple[str, int]:
    """Convert a datetime to (init_date_YYYYMMDD, init_hour) tuple."""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y%m%d"), utc.hour
