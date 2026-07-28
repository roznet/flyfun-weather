"""Disk cache for downloaded GRIB2 data.

Cache layout:
    {data_dir}/.cache/grib/{model}/{YYYYMMDD}_{HH}z/
        f{FFF}_{var}_{bbox_hash}.grib2

TTL is per-model: ICON-EU is precached on each main run so the previous run
is dead weight after a few hours (12 h); GFS isn't precached and is small
enough that 24 h costs almost nothing on disk, so it gets the more generous
window for fall-through to a prior run.

Runs are aged by the init time in the directory name, not the directory mtime
(which resets whenever a later briefing tops the run up with a new file). A
per-model size cap (:func:`cache_cap_bytes`) backstops the TTL: if the retained
set still exceeds the cap, the oldest-init runs are evicted whole until it fits,
never below a floor of runs. See issue #475.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active-run pins (PR #508 review round 4)
#
# Purging runs on the hot path, concurrently with other briefings. A per-call
# ``protect=`` argument could only shield the CALLER'S own run: during an
# extended-cycle transition, briefing A can still be decoding the previous run
# while briefing B selects the newly-published successor — B protects its own
# run and would purge A's now-expired directory out from under A's decode.
# The registry makes "someone is reading this" visible ACROSS callers: every
# reader pins its run for the duration, and both eviction rules (TTL and size
# cap) skip every pinned directory, whoever pinned it.
#
# In-process is sufficient: all purge/enrichment/scheduler/warm callers live
# in the single uvicorn worker (see refresh-durability — single-worker is a
# standing assumption there too). Reference-counted so two briefings pinning
# the same run release it correctly.
# ---------------------------------------------------------------------------

_PIN_LOCK = threading.Lock()
_PINNED_RUN_DIRS: dict[str, int] = {}


@contextmanager
def pin_run_dir(run_dir: Path):
    """Mark ``run_dir`` as actively in use for the duration of the block.

    While pinned, :func:`purge_old_runs` will not TTL-evict it and
    :func:`_enforce_size_cap` will not select it as a victim — regardless of
    which caller runs the purge. Pin BEFORE the first cache read and hold
    through the last decode; age-based eviction cannot otherwise see that a
    concurrent briefing (an ``as_of_time`` backtest, or a flight served by an
    older extended cycle) still depends on the directory.
    """
    key = str(Path(run_dir).resolve())
    with _PIN_LOCK:
        _PINNED_RUN_DIRS[key] = _PINNED_RUN_DIRS.get(key, 0) + 1
    try:
        yield run_dir
    finally:
        with _PIN_LOCK:
            n = _PINNED_RUN_DIRS.get(key, 0) - 1
            if n <= 0:
                _PINNED_RUN_DIRS.pop(key, None)
            else:
                _PINNED_RUN_DIRS[key] = n


def _pinned_run_dirs() -> set[str]:
    """Snapshot of currently pinned run-dir paths (resolved)."""
    with _PIN_LOCK:
        return set(_PINNED_RUN_DIRS)


def _rmtree_unless_pinned(run_dir: Path) -> bool:
    """Delete ``run_dir`` unless it is pinned AT DELETION TIME.

    A snapshot taken at the top of a purge pass races with concurrent pins:
    a reader can pin between the snapshot and the ``rmtree`` (PR #508 round
    5). Re-checking membership under the lock immediately before deleting
    closes that window down to "pin acquired after this check" — and a pin
    that late is safe by construction: pinning precedes the pinner's FIRST
    cache read, so a directory deleted in that residual sliver behaves like
    an ordinary cold cache (the reads miss and the artifact is re-fetched;
    the corrupt-cache retry covers a partially-removed file). What the
    re-check protects is the dangerous case: a reader pinned BEFORE the
    deletion decision, possibly mid-decode.

    Returns True when the directory was deleted.
    """
    with _PIN_LOCK:
        if str(run_dir.resolve()) in _PINNED_RUN_DIRS:
            return False
        shutil.rmtree(run_dir, ignore_errors=True)
    return True

# Per-model TTL overrides. The model is recovered from the cache layout —
# ``run_dir.parent.name`` is the model key (see :func:`cache_dir_for_run`).
# Models not listed here fall back to :data:`CACHE_TTL_SECONDS`.
MODEL_TTL_SECONDS: dict[str, int] = {
    "gfs": 24 * 3600,       # no precache, small footprint (~0.5 GB/run)
    "icon-eu": 12 * 3600,   # precached each main run; previous run is fallback
    "icon-d2": 6 * 3600,    # 8 runs/day (every 3h); keep current + prior run
    # HRRR (#457): hourly cycles and ~200 MB per cached forecast hour, so a
    # stale run is superseded fast and heavy. But the TTL cannot be set from
    # the HOURLY cadence, because only the 00/06/12/18z cycles reach 48 h —
    # every other cycle stops at 18 h, so a flight past that horizon is served
    # by an EXTENDED run whose successor is 6 h away (PR #508 review). At the
    # old 6 h the 12z run was purged at 18z, exactly while the 18z run was
    # still inside its 1.25 h publication delay or had an incomplete
    # last-needed hour: a guaranteed re-download of ~1 GB during every
    # extended-cycle transition. 9 h covers the 6 h gap plus the publication
    # delay with margin, keeping at most one superseded extended run.
    "hrrr": 9 * 3600,
}

# Fallback TTL for models without an explicit entry. Currently unreachable
# (gfs + icon-eu are the only cache users and both are listed above); kept
# as a safe default for any future non-precached model added to the cache.
CACHE_TTL_SECONDS = 12 * 3600

_GIB = 1024 ** 3

# Per-model hard disk cap (see :func:`purge_old_runs`). Applied *after* the TTL
# rule as a backstop: TTL alone can't bound disk because retained size scales
# with whatever the flight set demands, and the flight count grows over time.
# Eviction is oldest-init-first, whole run dirs, and never drops below
# ``DEFAULT_CACHE_FLOOR_RUNS`` (so the current run + its prior-run fallback are
# preserved). Deliberately *not* LRU — a stale run is stale regardless of how
# recently it was read, so age (init time) is the right utility signal.
#
# ICON-D2 (issue #475). Mind the units — the #475 measurements are DECIMAL GB
# and this cap is BINARY GiB. Two full runs measure ~41.6 GB = ~38.7 GiB, so the
# 45 GiB cap sits ~6 GiB above the normal current+prior pair: the pair is never
# evicted, but a third run (~59 GiB) that TTL drift or a growing flight set
# would pile on is. Note 45 GiB = ~48 GB against prod's ~52 GB free, so the cap
# is a backstop with only ~4 GB of slack — lower it (e.g. 40) if the volume
# tightens; the normal steady state is ~39 GiB and unaffected.
# Env override per model: ``WB_GRIB_CACHE_CAP_GB_<MODEL>`` (e.g.
# ``WB_GRIB_CACHE_CAP_GB_ICON_D2=40``; ``0``/negative disables). Models without
# a default and no override are uncapped (GFS is cheap; ICON-EU is bounded by
# its 12 h TTL once init-time aging lands).
DEFAULT_CACHE_FLOOR_RUNS = 2

_DEFAULT_CACHE_CAP_GIB: dict[str, float] = {
    "icon-d2": 45.0,
    # HRRR (#457): ~190 MB per cached forecast hour → ~1 GB per run for a
    # typical flight window. The 9h TTL is the primary bound (~2 concurrent
    # runs per flight); the cap backstops many concurrent US flights each
    # pulling a different hourly run. Enforced from the daily retention pass
    # (HRRR is not precached, so there is no warm-loop enforcement site).
    "hrrr": 8.0,
}


def cache_cap_bytes(model: str) -> int | None:
    """Return the disk cap in bytes for ``model``, or ``None`` if uncapped.

    Reads the ``WB_GRIB_CACHE_CAP_GB_<MODEL>`` env override first (``0`` or a
    negative value disables the cap for that model), then the built-in
    :data:`_DEFAULT_CACHE_CAP_GIB` default.
    """
    env_key = "WB_GRIB_CACHE_CAP_GB_" + model.upper().replace("-", "_")
    raw = os.environ.get(env_key)
    if raw is not None and raw.strip():
        try:
            gb = float(raw)
        except ValueError:
            logger.warning("Invalid %s=%r, ignoring cap override", env_key, raw)
        else:
            return int(gb * _GIB) if gb > 0 else None
    gb = _DEFAULT_CACHE_CAP_GIB.get(model)
    return int(gb * _GIB) if gb else None


def _ttl_for(run_dir: Path) -> int:
    """Look up the TTL for the model owning ``run_dir``.

    The cache layout puts the model key one level above the run directory
    (``.cache/grib/{model}/{init}z``), so the model name is recoverable
    without threading it through every call site.
    """
    return MODEL_TTL_SECONDS.get(run_dir.parent.name, CACHE_TTL_SECONDS)


def cache_dir_for_run(
    data_dir: Path,
    init_date: str,
    init_hour: int,
    model: str = "gfs",
) -> Path:
    """Return the cache directory for a specific model run.

    Args:
        data_dir: Base data directory.
        init_date: Model init date as YYYYMMDD.
        init_hour: Model init hour (0, 6, 12, 18).
        model: Model identifier (e.g. "gfs", "icon-eu").

    Returns:
        Path like {data_dir}/.cache/grib/{model}/20231027_00z/
    """
    return data_dir / ".cache" / "grib" / model / f"{init_date}_{init_hour:02d}z"


def cache_key(
    forecast_hour: int,
    variable: str,
) -> str:
    """Generate a cache filename for a specific GRIB2 download.

    Route-independent: GRIB files cover the full model domain (GFS global,
    ICON-EU all-Europe), so the same cached file serves any route.

    Args:
        forecast_hour: Forecast hour (e.g. 6).
        variable: Variable name (e.g. "CLWMR").

    Returns:
        Filename like "f006_CLWMR.grib2"
    """
    return f"f{forecast_hour:03d}_{variable}.grib2"


def is_cached(
    run_dir: Path,
    filename: str,
) -> bool:
    """Check whether a cache entry exists and is not expired, without reading it.

    Use this for cache-hit short-circuits where the caller will skip work
    rather than consume the bytes — e.g. ``_prefetch_icon_eu_data`` skipping
    already-downloaded variables. Calling :func:`get_cached` for the same
    purpose pulls the entire file into memory just to check ``is not None``,
    which inflates RSS on warm-cache refreshes.

    Side effect: if the file is past TTL, it is unlinked here, matching
    :func:`get_cached` semantics. Returns ``False`` in that case.
    """
    path = run_dir / filename
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    if age > _ttl_for(run_dir):
        logger.debug("Cache expired: %s (%.0fh old)", path, age / 3600)
        path.unlink(missing_ok=True)
        return False
    return True


def get_cached(
    run_dir: Path,
    filename: str,
) -> bytes | None:
    """Retrieve cached GRIB2 data if it exists and is not expired."""
    path = run_dir / filename
    if not path.exists():
        return None

    age = time.time() - path.stat().st_mtime
    if age > _ttl_for(run_dir):
        logger.debug("Cache expired: %s (%.0fh old)", path, age / 3600)
        path.unlink(missing_ok=True)
        return None

    logger.debug("Cache hit: %s", path)
    return path.read_bytes()


def put_cached(
    run_dir: Path,
    filename: str,
    data: bytes,
) -> Path:
    """Store GRIB2 data in the cache atomically.

    Writes to a tempfile in the same directory and then ``os.replace``s it
    into place. The rename is atomic on POSIX and on NTFS, so concurrent
    callers racing on the same ``(run_dir, filename)`` either see the file
    not-yet-present or fully-written — never a half-written interleave.

    The written pages are flushed and dropped from the page cache
    (``POSIX_FADV_DONTNEED``): cache writes are charged to the container's
    cgroup and a warm/briefing pass writes gigabytes, pinning the cgroup at
    its memory limit and starving concurrent decode workers of reclaimable
    headroom (the 2026-07-23 05:09Z OOM). Nothing re-reads these files soon
    enough to profit from residency — decode re-reads from SSD in ~ms
    against a ~30 s decode.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=run_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            if hasattr(os, "posix_fadvise"):  # absent on macOS dev machines
                os.posix_fadvise(
                    f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED,
                )
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    logger.debug("Cached: %s (%d bytes)", path, len(data))
    return path


def put_cached_from_chunks(
    run_dir: Path,
    filename: str,
    chunks,
) -> Path | None:
    """Store GRIB2 data streamed as an iterable of byte chunks, atomically.

    Same tmpfile + ``os.replace`` + fsync + ``POSIX_FADV_DONTNEED`` contract
    as :func:`put_cached`, but consumes ``chunks`` incrementally so the
    caller never holds the whole payload in memory — a full HRRR forecast
    hour is ~190 MB, and the accumulate-then-write pattern held ~3× that
    transiently (PR #508 review). Yields from ``chunks`` may perform I/O
    (parallel range downloads); an exception from the iterator unlinks the
    tempfile and propagates.

    Returns the cached path, or ``None`` (nothing written, no cache entry)
    when the iterator produced no bytes — so a total download failure
    cannot commit an empty file that later reads as a valid cache hit.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    fd, tmp = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=run_dir)
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in chunks:
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
            if total:
                f.flush()
                os.fsync(f.fileno())
                if hasattr(os, "posix_fadvise"):  # absent on macOS dev machines
                    os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        if not total:
            os.unlink(tmp)
            return None
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    logger.debug("Cached (streamed): %s (%d bytes)", path, total)
    return path


def init_dt_from_run_dir(run_dir: Path) -> datetime | None:
    """Parse the model-run init time from a cache dir name.

    Cache dirs are named ``{YYYYMMDD}_{HH}z`` by :func:`cache_dir_for_run`, so
    the init time is recoverable from the name alone — no need to trust the
    directory mtime, which tracks file *creation/deletion* and so resets to
    "now" every time a later briefing tops the run up with a new
    ``(fhour, var, level)`` file (issue #475). Returns ``None`` for any name
    that doesn't parse (a stray/legacy dir), so the caller can fall back to
    mtime rather than pin it forever.
    """
    name = run_dir.name
    try:
        date_part, hour_part = name.split("_")
        if not hour_part.endswith("z"):
            return None
        return datetime.strptime(date_part, "%Y%m%d").replace(
            hour=int(hour_part[:-1]), tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        return None


def _run_age_seconds(run_dir: Path, now_ts: float) -> float:
    """Age of ``run_dir`` in seconds, by init time (mtime fallback).

    Best-effort under concurrent purges: ``purge_old_runs`` runs from several
    uncoordinated contexts (per-briefing enrichment, the warm loop, the daily
    retention pass), so a dir can vanish between ``iterdir`` and here. Parseable
    names never touch the filesystem; for the mtime fallback, a vanished dir is
    treated as infinitely old — it sorts/purges as the oldest, and the eviction
    ``rmtree`` is ``ignore_errors`` so acting on an already-gone dir is a no-op.
    Degrade-to-skip matches the surrounding ``_prepare_icon_eu`` error handling
    rather than letting a race blow up the whole enrichment call.
    """
    init_dt = init_dt_from_run_dir(run_dir)
    if init_dt is not None:
        return now_ts - init_dt.timestamp()
    try:
        return now_ts - run_dir.stat().st_mtime
    except OSError:
        return float("inf")


def _dir_size_bytes(path: Path) -> int:
    """Total size of all files under ``path`` (best-effort; skips vanished).

    Tolerant of a concurrent purge removing files/dirs mid-walk: each ``stat``
    is guarded, and the ``rglob`` scandir itself is wrapped so a run dir that
    disappears under us contributes what was counted so far rather than raising.
    """
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _enforce_size_cap(
    cache_root: Path,
    model: str,
    cap_bytes: int,
    floor_runs: int,
    now_ts: float,
) -> int:
    """Evict oldest-init run dirs until the model total is under ``cap_bytes``.

    Whole run dirs only (never individual files: per-level re-download trades
    scarce bandwidth for disk — the wrong direction). Never drops below
    ``floor_runs`` so the current run and its prior-run fallback survive.
    Pinned directories (:func:`pin_run_dir` — some briefing is actively
    reading them) are never victims, but their bytes DO count toward the
    model total: excluding them would report "cap enforced" while disk sits
    over the cap (PR #508 review round 4).
    Returns the number of directories evicted.
    """
    all_dirs = [d for d in cache_root.iterdir() if d.is_dir()]
    pinned = _pinned_run_dirs()
    sizes = {d: _dir_size_bytes(d) for d in all_dirs}
    total = sum(sizes.values())

    # Oldest init first; unparseable names sort by mtime.
    victims = [d for d in all_dirs if str(d.resolve()) not in pinned]
    victims.sort(key=lambda d: _run_age_seconds(d, now_ts), reverse=True)

    removed = 0
    idx = 0
    while (
        total > cap_bytes
        and (len(all_dirs) - removed) > floor_runs
        and idx < len(victims)
    ):
        victim = victims[idx]
        reclaimed = sizes[victim]
        idx += 1
        # Membership re-checked under the pin lock at deletion time — the
        # sizing walk above leaves a wide window for a reader to pin
        # (PR #508 round 5).
        if not _rmtree_unless_pinned(victim):
            continue
        total -= reclaimed
        removed += 1
        logger.info(
            "GRIB cache cap: evicted %s run %s (%.1f MiB reclaimed, "
            "model total now %.1f MiB / cap %.1f MiB)",
            model, victim.name,
            reclaimed / (1024 * 1024),
            total / (1024 * 1024),
            cap_bytes / (1024 * 1024),
        )

    if total > cap_bytes:
        # Floor or pins won: the retained runs (pinned included — their bytes
        # count) still exceed the cap. Disk is NOT bounded in this state, so
        # say so loudly rather than returning a clean count that reads as
        # "cap enforced". Means the per-run size has grown, or active readers
        # are pinning more than the cap allows — the alternative, evicting a
        # pinned or floor-protected run, would delete data a briefing is
        # actively reading.
        logger.warning(
            "GRIB cache cap NOT met for %s: %.1f MiB over cap %.1f MiB with "
            "only %d run(s) left (floor %d). Disk is unbounded until the cap "
            "or floor is retuned.",
            model, total / (1024 * 1024), cap_bytes / (1024 * 1024),
            len(all_dirs) - removed, floor_runs,
        )
    return removed


def purge_old_runs(
    data_dir: Path,
    model: str = "gfs",
    *,
    cap_bytes: int | None = None,
    floor_runs: int = DEFAULT_CACHE_FLOOR_RUNS,
    now: datetime | None = None,
    enforce_cap: bool = False,
) -> int:
    """Purge cache directories for ``model`` — by TTL, and optionally by cap.

    1. **TTL (always)** — drop any run dir older than the model's TTL, aged by
       the init time in the dir name (mtime fallback for unparseable names).
       Cheap: a hot-path caller pays one ``scandir`` + an ``is_dir`` stat per
       run dir, with no recursive walk.
    2. **Size cap (only when ``enforce_cap``)** — evict the oldest-init runs
       until the model total fits its cap (``cap_bytes`` or the model default
       via :func:`cache_cap_bytes`), never below ``floor_runs``. The cap check
       needs a recursive size walk (``rglob`` + ``stat`` over every cached
       file), far too expensive to run on every user-facing briefing — so it is
       a slow-moving backstop invoked only from the scheduled contexts (the
       precache/warm loop after each warm, and the daily retention pass). The
       per-briefing ``_prepare_icon_eu``/GFS enrichment path leaves it off; the
       TTL rule (plus init-time aging) already bounds those to ~2 runs.

    Directories pinned via :func:`pin_run_dir` are exempt from BOTH rules,
    whoever pinned them. Purging runs on the hot path, concurrently with
    other briefings: during an extended-cycle transition, briefing A can
    still be decoding the previous run while briefing B selects the fresh
    successor — a per-call "protect my run" argument shields only B's run,
    so the exemption must come from a shared registry of active readers
    (PR #508 review round 4).

    Returns the total number of directories removed.
    """
    cache_root = data_dir / ".cache" / "grib" / model
    if not cache_root.exists():
        return 0

    ttl = MODEL_TTL_SECONDS.get(model, CACHE_TTL_SECONDS)
    now_ts = time.time() if now is None else now.timestamp()
    removed = 0
    pinned = _pinned_run_dirs()

    for run_dir in cache_root.iterdir():
        if not run_dir.is_dir():
            continue
        if str(run_dir.resolve()) in pinned:
            continue
        if _run_age_seconds(run_dir, now_ts) > ttl:
            # Pin membership is re-checked under the lock at deletion time —
            # the snapshot above races with concurrent pin_run_dir calls
            # (PR #508 round 5).
            if _rmtree_unless_pinned(run_dir):
                removed += 1
                logger.debug("Purged old cache: %s", run_dir)

    if enforce_cap:
        if cap_bytes is None:
            cap_bytes = cache_cap_bytes(model)
        if cap_bytes is not None:
            removed += _enforce_size_cap(
                cache_root, model, cap_bytes, floor_runs, now_ts,
            )

    return removed
