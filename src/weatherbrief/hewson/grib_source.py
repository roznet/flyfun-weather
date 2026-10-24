"""GRIB sources for the Hewson grid precompute (#911).

The precompute used to pull its 925/850/700 hPa T/Td/wind grid from Open-Meteo
(~70k billed calls per run). Every model it reads is published on a 0.25°
lattice that contains ours, so the same fields come straight from GRIB with no
interpolation at all:

* **ECMWF** — the ECPDS-delivered ``a2`` files in ``ECMWF_GRIB_DIR`` (local
  disk). The Europe sub-grid (35–59.5°N, 17.5°W–39.5°E) is a superset of
  ``FRONTAL_GRID``, so the lattice is a slice.
* **GFS** — ``pgrb2.0p25`` on the NOAA S3 bucket, 12 messages per step through
  ``.idx`` byte ranges. Global 0–360° grid: target longitudes are aligned to the
  axis first (``decode._align_lons_to_axis``).
* **ICON-EU** — DWD open-data pressure-level files. The 0.0625° grid starts at
  29.5°N / 23.5°W, so every 4th point is one of ours: a subsample.

The lattice is matched by exact coordinate lookup, never by nearest neighbour:
a grid that does not hold every target point is skipped, so a domain change
fails loudly instead of quietly shifting the field.

Open-Meteo is not called anywhere here. A model whose run cannot be resolved
or downloaded in full is skipped for the cycle (``run_once`` records why);
there is no Open-Meteo fallback, which would put the cost back.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

logger = logging.getLogger(__name__)

# cfVarName of the four inputs; identical across ECMWF (t/r/u/v), GFS
# (TMP/RH/UGRD/VGRD) and ICON (T/RELHUM/U/V) once eccodes has named them.
HEWSON_GRIB_VARS: tuple[str, ...] = ("t", "r", "u", "v")
# idx / DWD-directory names for the same four, in HEWSON_GRIB_VARS order.
_GFS_IDX_VARS: tuple[str, ...] = ("TMP", "RH", "UGRD", "VGRD")
_ICON_FILE_VARS: tuple[str, ...] = ("t", "relhum", "u", "v")

MS_TO_KMH = 3.6
# Coordinates are matched to this tolerance (degrees). Grid axes come from
# eccodes as float64 decimals of 0.25 / 0.0625 multiples, so 1e-4 is generous
# and still far below any real grid spacing.
_COORD_TOL_DEG = 1e-4
# One retry per step: a single dropped byte range or 5xx should not cost a
# model its whole cycle, a persistent one should.
_STEP_ATTEMPTS = 2
# Pause before the retry, so a throttled or briefly failing server gets a
# moment instead of a second request in the same millisecond.
_STEP_RETRY_DELAY_S = 2.0
# How many older cycles to try when the newest one is not complete yet.
_MAX_STEP_BACK = 2
_ICON_DOWNLOAD_WORKERS = 12
# The newest cycle each model should have out in full by a given time:
# (cycle interval, hours from init until the whole 0-117 h run is published),
# with margin. ECMWF a2 lands ~6 h 40 min after 00Z/12Z (the compute node syncs
# it at 07:15/19:15); GFS and ICON-EU finish their 120 h runs ~4 h after init
# (5 h here: one hour of margin).
# At the 08Z/20Z sample hours this expects the 00Z/12Z run of every model.
_EXPECTED_CYCLE_HOURS: dict[str, tuple[int, float]] = {
    "ecmwf": (12, 7.5),
    "gfs": (6, 5.0),
    "icon": (6, 5.0),
}

SOURCE_LABELS = {
    "ecmwf": "ecmwf-ecpds",
    "gfs": "gfs-s3",
    "icon": "icon-eu-dwd",
}


@dataclass
class GribRun:
    """A resolved model run: where each forecast step's GRIB comes from.

    ``fetch_step(hour)`` returns the GRIB data for one forecast hour (a local
    path or a byte blob, both accepted by ``grib_reader.iter_messages``), or
    ``None`` when the step could not be read.
    """

    model: str
    source: str
    init: datetime
    fetch_step: Callable[[int], Path | bytes | None]

    @property
    def init_unix(self) -> int:
        return int(self.init.timestamp())

    def describe(self) -> str:
        return f"{self.source} init {self.init.strftime('%Y-%m-%dT%H:%MZ')}"


# ---------------------------------------------------------------------------
# Lattice extraction (shared by all three models)
# ---------------------------------------------------------------------------


def _axis_indices(axis: np.ndarray, targets: np.ndarray) -> np.ndarray | None:
    """Index into ``axis`` of every target, or None if any target is absent."""
    if axis.size == 0:
        return None
    dist = np.abs(axis[None, :] - targets[:, None])
    idx = dist.argmin(axis=1)
    if not np.all(dist[np.arange(targets.size), idx] <= _COORD_TOL_DEG):
        return None
    return idx


def lattice_indices(
    grid_lats: np.ndarray, grid_lons: np.ndarray,
    lat: np.ndarray, lon: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Row/column indices of the target lattice inside a GRIB grid, or None.

    Longitudes are first expressed in the grid's own convention (a 0–360 GFS
    axis, a −17.5…39.5 ECMWF Europe axis), the same alignment the route
    decoders use. None means the grid does not hold every target point.
    """
    from weatherbrief.fetch.grib.decode import _align_lons_to_axis

    rows = _axis_indices(np.asarray(grid_lats, dtype=np.float64), np.asarray(lat, dtype=np.float64))
    if rows is None:
        return None
    aligned = _align_lons_to_axis(grid_lons, lon)
    cols = _axis_indices(np.asarray(grid_lons, dtype=np.float64), aligned)
    if cols is None:
        return None
    return rows, cols


def read_lattice_fields(
    sources: Path | bytes | Sequence[Path | bytes],
    levels: Sequence[int],
    lat: np.ndarray,
    lon: np.ndarray,
) -> dict[int, dict[str, np.ndarray]]:
    """Read T/RH/u/v on the target lattice from one step's GRIB data.

    Returns ``{level: {"t": K, "r": %, "u": m/s, "v": m/s}}`` as float64
    ``(n_lat, n_lon)`` arrays, with only the (variable, level) pairs found.
    Messages are unpacked only when their header matches; the first grid that
    holds the whole lattice wins (the ECMWF files also carry Nordic and US
    sub-grids, which never do).
    """
    from weatherbrief.fetch.grib.grib_reader import iter_messages

    if isinstance(sources, (Path, str, bytes, bytearray)):
        sources = [sources]
    wanted_levels = {float(L): int(L) for L in levels}
    out: dict[int, dict[str, np.ndarray]] = {int(L): {} for L in levels}
    # grid_key → (rows, cols) or None (grid does not hold the lattice)
    index_cache: dict[str, tuple[np.ndarray, np.ndarray] | None] = {}

    for source in sources:
        for msg in iter_messages(source):
            if msg.var not in HEWSON_GRIB_VARS or msg.type_of_level != "isobaricInhPa":
                continue
            level = wanted_levels.get(msg.level)
            if level is None or msg.var in out[level]:
                continue
            if msg.grid_key not in index_cache:
                grid = msg.grid()
                index_cache[msg.grid_key] = (
                    None if grid is None
                    else lattice_indices(grid.lats, grid.lons, lat, lon)
                )
            idx = index_cache[msg.grid_key]
            if idx is None:
                continue
            grid = msg.grid()
            values = msg.values(grid) if grid is not None else None
            if values is None:
                continue
            rows, cols = idx
            out[level][msg.var] = values[np.ix_(rows, cols)].astype(np.float64)
    return out


def dewpoint_magnus_c(t_c: np.ndarray, rh_pct: np.ndarray) -> np.ndarray:
    """Dewpoint (°C) from T (°C) and RH (%) — vectorised Magnus.

    Same constants and RH floor as the GRIB sounding path
    (``decode._convert_raw_sounding``: a = 17.67, b = 243.5 °C, RH ≥ 0.1 %), so
    a Hewson θe and a briefing sounding derive Td the same way. RH above 100 %
    (model supersaturation) is kept, as it is there.
    """
    a, b = 17.67, 243.5
    rh = np.maximum(rh_pct, 0.1)
    gamma = a * t_c / (b + t_c) + np.log(rh / 100.0)
    return b * gamma / (a - gamma)


def level_inputs(
    fields: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    """``(T °C, Td °C, u km/h, v km/h)`` from one level's raw fields, or None."""
    if any(v not in fields for v in HEWSON_GRIB_VARS):
        return None
    t_c = fields["t"] - 273.15
    td_c = dewpoint_magnus_c(t_c, fields["r"])
    return t_c, td_c, fields["u"] * MS_TO_KMH, fields["v"] * MS_TO_KMH


def read_step(
    run: GribRun, hour: int, levels: Sequence[int],
    lat: np.ndarray, lon: np.ndarray,
) -> dict[int, dict[str, np.ndarray]] | None:
    """One forecast step on the lattice, complete for every level, or None.

    Retries once: a step that still lacks a field after that is reported as
    missing so the caller can drop the model for the cycle rather than write a
    snapshot with a hole in it.
    """
    for attempt in range(1, _STEP_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(_STEP_RETRY_DELAY_S)
        try:
            data = run.fetch_step(hour)
            if data is not None:
                fields = read_lattice_fields(data, levels, lat, lon)
                if all(
                    all(v in fields[L] for v in HEWSON_GRIB_VARS) for L in levels
                ):
                    return fields
                logger.warning(
                    "Hewson GRIB %s f%03d: incomplete (%s), attempt %d/%d",
                    run.model, hour,
                    {L: sorted(fields[L]) for L in levels}, attempt, _STEP_ATTEMPTS,
                )
            else:
                logger.warning(
                    "Hewson GRIB %s f%03d: no data, attempt %d/%d",
                    run.model, hour, attempt, _STEP_ATTEMPTS,
                )
        except Exception:
            logger.warning(
                "Hewson GRIB %s f%03d: read failed, attempt %d/%d",
                run.model, hour, attempt, _STEP_ATTEMPTS, exc_info=True,
            )
    return None


# ---------------------------------------------------------------------------
# Run resolution, per model
# ---------------------------------------------------------------------------


def _resolve_ecmwf(
    hours: Sequence[int], levels: Sequence[int], *,
    init: datetime | None = None, **_kwargs,
) -> GribRun | None:
    """Newest ready ECMWF run whose a2 files hold every wanted step (or ``init``)."""
    from weatherbrief.fetch.grib.ecmwf_fetch import (
        ecmwf_grib_dir,
        filter_ready_runs,
        scan_ecmwf_files,
    )

    data_dir = ecmwf_grib_dir()
    files = [f for f in scan_ecmwf_files(data_dir) if f.is_pressure_level]
    files = filter_ready_runs(files, data_dir)
    runs: dict[datetime, dict[int, Path]] = {}
    for f in files:
        runs.setdefault(f.base_time, {})[f.step_hours] = f.path
    candidates = sorted(runs, reverse=True)[: _MAX_STEP_BACK + 1]
    if init is not None:
        candidates = [bt for bt in runs if bt == init]
    for base_time in candidates:
        steps = runs[base_time]
        missing = [h for h in hours if h not in steps]
        if missing:
            logger.info(
                "Hewson GRIB ecmwf: run %s lacks a2 steps %s, trying older",
                base_time.isoformat(), missing[:5],
            )
            continue
        return GribRun("ecmwf", SOURCE_LABELS["ecmwf"], base_time, steps.get)
    logger.warning("Hewson GRIB ecmwf: no complete ready run in %s", data_dir)
    return None


def _new_session() -> requests.Session:
    """A session whose connection pool fits the ICON download pool, so the
    workers share it without "connection pool is full" churn.

    Shared across those threads for plain GETs only (no auth, no cookies to
    mutate), the common and safe use of a ``requests.Session``.
    """
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=4, pool_maxsize=_ICON_DOWNLOAD_WORKERS,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _head_ok(session: requests.Session, url: str) -> bool:
    try:
        return session.head(url, timeout=10).status_code == 200
    except requests.RequestException:
        return False


def _resolve_gfs(
    hours: Sequence[int], levels: Sequence[int], *,
    now: datetime, session: requests.Session, init: datetime | None = None,
) -> GribRun | None:
    """Newest GFS run whose last wanted step is already on S3 (or ``init``)."""
    from weatherbrief.fetch.grib.gfs_idx import plan_byte_ranges
    from weatherbrief.fetch.grib.grib_fetch import (
        fetch_byte_ranges,
        fetch_idx,
        find_latest_run,
        gfs_idx_url,
    )

    last = max(hours)
    as_of = now
    pinned = init
    for _ in range(_MAX_STEP_BACK + 1):
        found = (
            (pinned.strftime("%Y%m%d"), pinned.hour) if pinned is not None
            else find_latest_run(as_of, session=session, as_of_time=as_of)
        )
        if found is None:
            break
        date_str, cycle = found
        init = datetime.strptime(f"{date_str}{cycle:02d}", "%Y%m%d%H").replace(
            tzinfo=timezone.utc,
        )
        if _head_ok(session, gfs_idx_url(date_str, cycle, last)):

            def fetch_step(hour: int, _d=date_str, _c=cycle) -> bytes | None:
                idx = fetch_idx(_d, _c, hour, session=session)
                ranges = plan_byte_ranges(
                    idx, target_levels=list(levels),
                    variables=set(_GFS_IDX_VARS),
                )
                if not ranges:
                    return None
                return fetch_byte_ranges(_d, _c, hour, ranges, session=session) or None

            return GribRun("gfs", SOURCE_LABELS["gfs"], init, fetch_step)
        logger.info(
            "Hewson GRIB gfs: %s %02dz has no f%03d yet%s",
            date_str, cycle, last, "" if pinned is None else " (pinned init)",
        )
        if pinned is not None:
            break
        as_of = init - timedelta(minutes=1)
    logger.warning("Hewson GRIB gfs: no run with f%03d found", last)
    return None


def _resolve_icon(
    hours: Sequence[int], levels: Sequence[int], *,
    now: datetime, session: requests.Session, init: datetime | None = None,
) -> GribRun | None:
    """Newest ICON-EU main run (120 h horizon) with its last wanted step up (or ``init``)."""
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        _download_one_file,
        find_latest_icon_eu_run,
        icon_eu_pressure_level_url,
    )

    last = max(hours)
    # Only main runs reach the 117 h we read; a cover requirement past the
    # short runs' 30 h horizon makes the run finder skip them.
    cover_until = now + timedelta(hours=31)
    as_of = now
    pinned = init
    for _ in range(_MAX_STEP_BACK + 1):
        found = (
            (pinned.strftime("%Y%m%d"), pinned.hour) if pinned is not None
            else find_latest_icon_eu_run(
                now, session=session, as_of_time=as_of, cover_until=cover_until,
            )
        )
        if found is None:
            break
        date_str, cycle = found
        init = datetime.strptime(f"{date_str}{cycle:02d}", "%Y%m%d%H").replace(
            tzinfo=timezone.utc,
        )
        probe = icon_eu_pressure_level_url(date_str, cycle, last, 850, "t")
        if _head_ok(session, probe):

            def fetch_step(hour: int, _d=date_str, _c=cycle) -> list[bytes] | None:
                urls = [
                    icon_eu_pressure_level_url(_d, _c, hour, L, var)
                    for L in levels for var in _ICON_FILE_VARS
                ]
                with ThreadPoolExecutor(max_workers=_ICON_DOWNLOAD_WORKERS) as pool:
                    results = list(pool.map(lambda u: _download_one_file(u, session), urls))
                blobs = [data for data, _status in results if data is not None]
                return blobs or None

            return GribRun("icon", SOURCE_LABELS["icon"], init, fetch_step)
        logger.info(
            "Hewson GRIB icon: %s %02dz has no f%03d pressure levels yet%s",
            date_str, cycle, last, "" if pinned is None else " (pinned init)",
        )
        if pinned is not None:
            break
        as_of = init - timedelta(minutes=1)
    logger.warning("Hewson GRIB icon: no ICON-EU main run with f%03d found", last)
    return None


_RESOLVERS = {
    "ecmwf": _resolve_ecmwf,
    "gfs": _resolve_gfs,
    "icon": _resolve_icon,
}


def resolve_run(
    model: str,
    hours: Sequence[int],
    levels: Sequence[int],
    *,
    now: datetime | None = None,
    session: requests.Session | None = None,
    init: datetime | None = None,
) -> GribRun | None:
    """Resolve the run a Hewson precompute would read for ``model``, or None.

    Cheap: a directory scan (ECMWF) or a few HEAD probes (GFS, ICON). Nothing
    is downloaded until ``GribRun.fetch_step`` is called. The serving box uses
    the same resolution to know which init the compute node should deliver.

    ``init`` pins the run (aware UTC) instead of taking the newest: used to
    recompute a cycle that already has an Open-Meteo snapshot, for the
    side-by-side check. None if that run is not (or no longer) available.
    """
    resolver = _RESOLVERS.get(model)
    if resolver is None:
        raise ValueError(f"no GRIB source for Hewson model {model!r}")
    if not hours:
        raise ValueError("hours must not be empty")
    return resolver(
        list(hours), [int(L) for L in levels],
        now=now or datetime.now(timezone.utc),
        session=session or _new_session(),
        init=init,
    )


def expected_init(model: str, now: datetime | None = None) -> datetime:
    """The newest init of ``model`` that should be complete by ``now``.

    A resolved run older than this is stale: its delivery or sync failed (the
    ECMWF resolver then falls back to an older ready run, GFS/ICON step back a
    cycle). ``run_once`` reports that as a failure instead of quietly
    publishing, or keeping, the older run (#911).
    """
    interval, lag = _EXPECTED_CYCLE_HOURS[model]
    now = now or datetime.now(timezone.utc)
    ref = now - timedelta(hours=lag)
    day = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    return day + timedelta(hours=(ref.hour // interval) * interval)


def forecast_step_hours(forecast_days: int, stride_hours: int) -> list[int]:
    """Forecast hours a snapshot holds: ``0, stride, …`` below ``forecast_days``.

    5 days at a 3 h stride gives 0…117 h, 40 steps — the count the Open-Meteo
    path produced. Every model publishes all of them: GFS is hourly to 120 h,
    ICON-EU main runs hourly to 78 h then 3-hourly, ECMWF a2 hourly to 90 h
    then 3-hourly.
    """
    if stride_hours < 1:
        raise ValueError(f"stride_hours must be >= 1, got {stride_hours}")
    return list(range(0, max(1, forecast_days) * 24, stride_hours))


__all__ = [
    "GribRun",
    "HEWSON_GRIB_VARS",
    "SOURCE_LABELS",
    "dewpoint_magnus_c",
    "expected_init",
    "forecast_step_hours",
    "lattice_indices",
    "level_inputs",
    "read_lattice_fields",
    "read_step",
    "resolve_run",
]
