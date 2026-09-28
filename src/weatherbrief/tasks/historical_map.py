"""Historical map: METAR, TAF and model forecasts at a past instant (#629).

For a past time ``T`` (snapped to the 30-minute grid) and a model lead ``N``
days, every watchlist airport gets:

- **METAR** — the latest report (routine or SPECI) at or before ``T``, no
  older than :data:`METAR_MAX_AGE`. A stale report is left out rather than
  shown as if it were current.
- **TAF** — the TAF stored on the latest observation row at or before ``T``
  (within :data:`TAF_LOOKBACK`), **re-read at T** through
  ``analysis.taf_reading.read_taf_at`` — the same reading the route briefing
  uses. The stored ``taf_*`` columns are not used (see that module).
- **GFS / ICON / ECMWF** — the latest run *fetched* at or before
  ``T - N days`` (within :data:`RUN_LOOKBACK` of it), at the latest sample
  hour at or before ``T`` on the same UTC day and at most
  :data:`MODEL_MAX_AGE` old. ``N = 0`` is "what the models said just
  before T"; ``N = 1..`` is "what they said N days before". Selecting on
  ``fetched_at`` rather than on the latest init is what keeps a forecast made
  *after* T out of the picture.

Everything a client needs to render is decided here — which report, which
run, which valid time, why a source is missing — so the web tab (and a future
iOS one) only picks a block and colours it with the shared metric catalog.

Storage: observations live in MySQL (raw pruning is off; once
``VERIFICATION_RAW_RETENTION_DAYS`` is set, older months are read from the
Parquet archive). Snapshots are pruned from MySQL after ~10 days, so fetch
days older than :data:`SNAPSHOT_LIVE_DAYS` are read from the daily Parquet
archive, falling back to MySQL for a day that was never archived.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from weatherbrief.analysis.airport_conditions import classify_flight_category
from weatherbrief.analysis.airport_consensus import snap_to_dict
from weatherbrief.analysis.taf_reading import read_taf_at
from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    VerificationObservationRow,
)
from weatherbrief.tasks.forecast_grid import MAP_FORECAST_DAYS, all_sample_hours
from weatherbrief.units import M_PER_SM

logger = logging.getLogger(__name__)

MODELS: tuple[str, ...] = ("gfs", "icon", "ecmwf")
OBSERVED_SOURCES: tuple[str, ...] = ("metar", "taf")

TIME_STEP = timedelta(minutes=30)
METAR_MAX_AGE = timedelta(minutes=90)
TAF_LOOKBACK = timedelta(hours=6)
# A model value older than this is "no model data at this time", not a
# stand-in for T: past 18Z nothing is sampled until 06Z the next day.
MODEL_MAX_AGE = timedelta(hours=3)
# How far before the cutoff a run may have been fetched. Two forecast fetches
# a day, so 24 h always spans at least one; a longer gap means the pipeline
# was down, and silently showing an older run would mislabel its lead.
RUN_LOOKBACK = timedelta(hours=24)
# Snapshot fetch days newer than this are read from MySQL; older ones from the
# Parquet archive. The prune deletes ``fetched_at`` older than 10 days — one
# day of margin keeps a day that is mid-prune on the archive side.
SNAPSHOT_LIVE_DAYS = 9
MAX_LEAD_DAYS = max(MAP_FORECAST_DAYS.values())
# A result is immutable once every input for T has landed: METARs are ingested
# every 30 min, and a late snapshot ingest restamps ``fetched_at`` (so it can
# never enter a past cutoff). Two hours covers a delayed ingest cycle.
FINAL_AFTER = timedelta(hours=2)

# Reason codes for a model with no data (client maps them to text).
REASON_BEYOND_HORIZON = "beyond_horizon"  # lead past the model's map horizon
REASON_NO_VALID_TIME = "no_valid_time"    # T is outside the sampled hours
REASON_NO_RUN = "no_run"                  # no run fetched in the window

_SNAPSHOT_COLUMNS = (
    "icao",
    "model",
    "model_init_time",
    "forecast_hour",
    "fetched_at",
    "sounding_ceiling_ft",
    "nwp_ceiling_ft",
    "cloud_base_ft",
    "lcl_ft",
    "visibility_m",
    "wind_speed_10m_kt",
    "wind_direction_10m_deg",
    "wind_gusts_10m_kt",
    "cloud_cover_pct",
    "cape_jkg",
    "sounding_convective_risk",
    "temperature_2m_c",
)

_OBSERVATION_COLUMNS = (
    "icao",
    "observation_time",
    "report_type",
    "metar_raw",
    "ceiling_ft",
    "visibility_m",
    "wind_dir",
    "wind_speed_kt",
    "wind_gust_kt",
    "temperature_c",
    "dewpoint_c",
    "qnh",
    "weather",
    "taf_raw",
    "taf_issue_time",
)


# ---------------------------------------------------------------------------
# Time grid
# ---------------------------------------------------------------------------


def _as_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def snap_time(at: datetime) -> datetime:
    """Floor ``at`` to the 30-minute grid, in UTC."""
    at = _as_utc(at)
    step_s = int(TIME_STEP.total_seconds())
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % step_s, tz=timezone.utc)


def model_valid_times(at: datetime) -> list[datetime]:
    """Candidate model valid times for ``at``, newest first.

    Sample hours at or before ``at`` on the same UTC day and at most
    :data:`MODEL_MAX_AGE` old. More than one when an older slot is still in
    range, so a run that doesn't carry the newest slot (ECMWF past 144 h is
    6-hourly: no 09/15Z) falls back to the one before.
    """
    at = _as_utc(at)
    day0 = datetime(at.year, at.month, at.day, tzinfo=timezone.utc)
    out = []
    for hour in sorted(all_sample_hours(), reverse=True):
        vt = day0 + timedelta(hours=hour)
        if vt <= at and at - vt <= MODEL_MAX_AGE:
            out.append(vt)
    return out


def _days_between(lo: datetime, hi: datetime) -> list[date]:
    days, d = [], lo.date()
    while d <= hi.date():
        days.append(d)
        d += timedelta(days=1)
    return days


def _months_between(lo: datetime, hi: datetime) -> list[str]:
    months, (y, m) = [], (lo.year, lo.month)
    while (y, m) <= (hi.year, hi.month):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


# ---------------------------------------------------------------------------
# Loading (MySQL + Parquet archive)
# ---------------------------------------------------------------------------


def _load_snapshot_rows(
    db: Session,
    models: list[str],
    valid_times: list[datetime],
    fetched_after: datetime,
    fetched_until: datetime,
    now: datetime,
) -> list[dict[str, Any]]:
    """Snapshot rows for ``models`` at ``valid_times``, fetched in the window.

    The window is ``(fetched_after, fetched_until]``. Fetch days inside the
    MySQL window come from the database; older days from the daily archive,
    falling back to the database for a day with no archive file (archive off,
    or not final yet). Deduplicated on the natural key, so a day present in
    both can never double-count.
    """
    from weatherbrief.tasks.archive import read_archived_rows

    if not models or not valid_times:
        return []

    live_from = (now - timedelta(days=SNAPSHOT_LIVE_DAYS)).date()
    rows: list[dict[str, Any]] = []
    need_db = False
    for day in _days_between(fetched_after, fetched_until):
        if day > live_from:
            need_db = True
            continue
        archived = read_archived_rows(
            "snapshots", day.isoformat(),
            columns=list(_SNAPSHOT_COLUMNS),
            filters=[
                ("model", "in", list(models)),
                ("forecast_hour", "in", list(valid_times)),
            ],
        )
        if archived is None:
            need_db = True
        else:
            rows.extend(archived)

    if need_db:
        t = AirportForecastSnapshotRow
        result = db.execute(
            select(*[getattr(t, c) for c in _SNAPSHOT_COLUMNS])
            .where(t.forecast_hour.in_(valid_times))
            .where(t.model.in_(models))
            .where(t.fetched_at > fetched_after)
            .where(t.fetched_at <= fetched_until)
        )
        rows.extend(dict(r._mapping) for r in result)

    seen: set[tuple] = set()
    out = []
    for r in rows:
        for k in ("model_init_time", "forecast_hour", "fetched_at"):
            r[k] = _as_utc(r[k])
        if not (fetched_after < r["fetched_at"] <= fetched_until):
            continue
        key = (r["icao"], r["model"], r["model_init_time"], r["forecast_hour"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _load_observation_rows(
    db: Session, after: datetime, until: datetime, now: datetime,
) -> list[dict[str, Any]]:
    """Observation rows with ``observation_time`` in ``(after, until]``.

    MySQL holds every row while raw pruning is off. Once it is on, months
    reaching past the online window are read from the monthly archive too
    (deduplicated on the ``(icao, observation_time)`` natural key).
    """
    from weatherbrief.tasks.archive import read_archived_rows
    from weatherbrief.tasks.verification_tiering import (
        raw_retention_days,
        raw_retention_disabled,
    )

    t = VerificationObservationRow
    result = db.execute(
        select(*[getattr(t, c) for c in _OBSERVATION_COLUMNS])
        .where(t.observation_time > after)
        .where(t.observation_time <= until)
    )
    rows = [dict(r._mapping) for r in result]

    if not raw_retention_disabled() and after < now - timedelta(days=raw_retention_days()):
        for period in _months_between(after, until):
            archived = read_archived_rows(
                "observations", period,
                columns=list(_OBSERVATION_COLUMNS),
                filters=[
                    ("observation_time", ">", after),
                    ("observation_time", "<=", until),
                ],
            )
            rows.extend(archived or [])

    seen: set[tuple] = set()
    out = []
    for r in rows:
        r["observation_time"] = _as_utc(r["observation_time"])
        if r.get("taf_issue_time") is not None:
            r["taf_issue_time"] = _as_utc(r["taf_issue_time"])
        key = (r["icao"], r["observation_time"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------


def select_model_runs(
    db: Session, at: datetime, lead_days: int, now: datetime,
) -> tuple[dict[str, list[dict]], dict[str, dict[str, Any]]]:
    """Pick, per model, the run and valid time to show for ``at``.

    Returns ``(rows_by_model, meta_by_model)``: the chosen run's snapshot
    rows, and for every model either the run/valid time/lead it came from or
    a ``reason`` it has none.
    """
    cutoff = at - timedelta(days=lead_days)
    valid_times = model_valid_times(at)
    meta: dict[str, dict[str, Any]] = {}
    in_reach = []
    for m in MODELS:
        if lead_days > MAP_FORECAST_DAYS.get(m, 0):
            meta[m] = {"available": False, "reason": REASON_BEYOND_HORIZON}
        elif not valid_times:
            meta[m] = {"available": False, "reason": REASON_NO_VALID_TIME}
        else:
            in_reach.append(m)

    rows = _load_snapshot_rows(
        db, in_reach, valid_times, cutoff - RUN_LOOKBACK, cutoff, now,
    )
    rows_by_model: dict[str, list[dict]] = {}
    for m in in_reach:
        for vt in valid_times:
            candidates = [r for r in rows if r["model"] == m and r["forecast_hour"] == vt]
            if not candidates:
                continue
            init = max(r["model_init_time"] for r in candidates)
            chosen = [r for r in candidates if r["model_init_time"] == init]
            rows_by_model[m] = chosen
            meta[m] = {
                "available": True,
                "valid_time": vt.isoformat(),
                "model_init_time": init.isoformat(),
                "fetched_at": max(r["fetched_at"] for r in chosen).isoformat(),
                "lead_hours": round((vt - init).total_seconds() / 3600),
                "count": len(chosen),
            }
            break
        else:
            meta[m] = {"available": False, "reason": REASON_NO_RUN}
    return rows_by_model, meta


def _parse_weather(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def metar_to_dict(row: dict[str, Any], at: datetime) -> dict[str, Any]:
    """A METAR row as a map source dict (same value keys as a model).

    The category is re-derived with the same ``classify_flight_category`` the
    models go through, so METAR and model colours share thresholds.
    """
    vis_m = row.get("visibility_m")
    ceiling = row.get("ceiling_ft")
    vis_sm = vis_m / M_PER_SM if vis_m is not None else None
    obs_time = row["observation_time"]
    return {
        "flight_category": classify_flight_category(ceiling, vis_sm).value,
        "ceiling_ft": ceiling,
        "visibility_m": vis_m,
        "wind_speed_kt": row.get("wind_speed_kt"),
        "wind_dir_deg": row.get("wind_dir"),
        "wind_gust_kt": row.get("wind_gust_kt"),
        "temperature_c": row.get("temperature_c"),
        "dewpoint_c": row.get("dewpoint_c"),
        "qnh": row.get("qnh"),
        "weather": _parse_weather(row.get("weather")),
        "observation_time": obs_time.isoformat(),
        "age_min": round((at - obs_time).total_seconds() / 60),
        "report_type": row.get("report_type") or "METAR",
        "raw": row.get("metar_raw"),
    }


def taf_to_dict(row: dict[str, Any], at: datetime) -> dict[str, Any] | None:
    """The row's TAF read at ``at``, as a map source dict.

    ``None`` when the TAF can't be parsed or its validity doesn't contain
    ``at`` — an expired TAF says nothing about that instant.
    """
    from euro_aip.briefing.weather.models import WeatherReport

    raw = row.get("taf_raw")
    if not raw:
        return None
    reference = row.get("taf_issue_time") or row["observation_time"]
    try:
        taf = WeatherReport.from_taf(raw, reference=reference)
    except Exception:  # noqa: BLE001 - one bad TAF must not sink the map
        logger.debug("historical map: TAF parse failed for %s", row.get("icao"), exc_info=True)
        return None
    if taf is None:
        return None
    reading = read_taf_at(taf, at)
    if reading is None:
        return None
    issue = row.get("taf_issue_time") or taf.observation_time
    return {
        "flight_category": reading.flight_category,
        "prevailing_category": reading.prevailing_category,
        "temporary_category": reading.temporary_category,
        "temporary_type": reading.temporary_type,
        "trend_type": reading.trend_type,
        "ceiling_ft": reading.ceiling_ft,
        "visibility_m": reading.visibility_m,
        "wind_speed_kt": reading.wind_speed_kt,
        "wind_dir_deg": reading.wind_dir,
        "wind_gust_kt": reading.wind_gust_kt,
        "significant_weather": reading.significant_weather,
        "issue_time": _as_utc(issue).isoformat() if issue else None,
        "raw": raw,
    }


def select_observed(
    db: Session, at: datetime, now: datetime,
) -> dict[str, dict[str, dict]]:
    """Per airport, the METAR and TAF source dicts to show for ``at``."""
    rows = _load_observation_rows(db, at - TAF_LOOKBACK, at, now)
    rows.sort(key=lambda r: r["observation_time"], reverse=True)

    latest_metar: dict[str, dict] = {}
    latest_taf: dict[str, dict] = {}
    for r in rows:
        icao = r["icao"]
        if (
            icao not in latest_metar
            and r.get("metar_raw")
            and at - r["observation_time"] <= METAR_MAX_AGE
        ):
            latest_metar[icao] = r
        if icao not in latest_taf and r.get("taf_raw"):
            latest_taf[icao] = r

    observed: dict[str, dict[str, dict]] = {}
    for icao, r in latest_metar.items():
        observed.setdefault(icao, {})["metar"] = metar_to_dict(r, at)
    for icao, r in latest_taf.items():
        taf = taf_to_dict(r, at)
        if taf is not None:
            observed.setdefault(icao, {})["taf"] = taf
    return observed


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def get_historical_map_data(
    db: Session,
    at: datetime,
    lead_days: int,
    airports_db_path: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The historical map payload for ``at`` (snapped) and ``lead_days``.

    Airport entries have the forecast map's shape (``models``, both baked
    consensus blocks over the NWP models only) plus ``observed`` holding the
    ``metar``/``taf`` source dicts. ``sources`` says what each source is based
    on, or why it has nothing.
    """
    from weatherbrief.tasks.map_queries import _get_elevations, assemble_map_airports

    now = _as_utc(now or datetime.now(timezone.utc))
    at = snap_time(min(_as_utc(at), now))
    lead_days = max(0, min(int(lead_days), MAX_LEAD_DAYS))

    rows_by_model, model_meta = select_model_runs(db, at, lead_days, now)
    elevations = _get_elevations(airports_db_path)
    by_airport: dict[str, dict[str, dict]] = {}
    for model, rows in rows_by_model.items():
        meta = model_meta[model]
        for r in rows:
            d = snap_to_dict(r, field_elevation_ft=elevations.get(r["icao"]))
            d["valid_time"] = meta["valid_time"]
            d["model_init_time"] = meta["model_init_time"]
            by_airport.setdefault(r["icao"], {})[model] = d

    observed = select_observed(db, at, now)
    airports = assemble_map_airports(by_airport, airports_db_path, observed=observed)

    sources: dict[str, dict[str, Any]] = {
        "metar": {
            "available": any("metar" in a["observed"] for a in airports),
            "count": sum(1 for a in airports if "metar" in a["observed"]),
            "max_age_min": int(METAR_MAX_AGE.total_seconds() // 60),
        },
        "taf": {
            "available": any("taf" in a["observed"] for a in airports),
            "count": sum(1 for a in airports if "taf" in a["observed"]),
        },
    }
    sources.update(model_meta)

    return {
        "at": at.isoformat(),
        "lead_days": lead_days,
        "run_cutoff": (at - timedelta(days=lead_days)).isoformat(),
        "sources": sources,
        "airports": airports,
    }


def is_final(at: datetime, now: datetime | None = None) -> bool:
    """Whether the payload for ``at`` can no longer change (safe to cache)."""
    now = _as_utc(now or datetime.now(timezone.utc))
    return now - _as_utc(at) >= FINAL_AFTER


class _LruCache:
    """Tiny thread-safe LRU for immutable historical payloads.

    Past instants never change once final, so a hit is always correct; the
    bound only caps memory. Deliberately in-process rather than in
    ``verification_cache``: users can pick any of ~48 slots × 7 leads × every
    past day, and that table isn't meant to grow with browsing.
    """

    def __init__(self, maxsize: int) -> None:
        self._data: OrderedDict[tuple, dict] = OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def get(self, key: tuple) -> dict | None:
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key: tuple, value: dict) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


payload_cache = _LruCache(maxsize=32)


def get_historical_map_cached(
    db: Session, at: datetime, lead_days: int, airports_db_path: str,
) -> dict[str, Any]:
    """:func:`get_historical_map_data`, memoised once the instant is final."""
    now = datetime.now(timezone.utc)
    snapped = snap_time(min(_as_utc(at), now))
    key = (snapped.isoformat(), int(lead_days))
    final = is_final(snapped, now)
    if final:
        hit = payload_cache.get(key)
        if hit is not None:
            return hit
    data = get_historical_map_data(db, snapped, lead_days, airports_db_path, now=now)
    if final:
        payload_cache.put(key, data)
    return data


# ---------------------------------------------------------------------------
# Range (what the pickers offer)
# ---------------------------------------------------------------------------


def compute_historical_range(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """What the historical pickers can offer, so the client hardcodes none of it.

    ``earliest_observation`` / ``earliest_model`` are the first instants with
    any METAR / any fetched run, across MySQL and the archive manifests. Model
    data starts later than observations (snapshots were pruned at 10 days
    before the archive existed), and the client greys model sources out
    before ``earliest_model`` rather than showing an empty map.
    """
    from weatherbrief.tasks.archive import earliest_archived_period

    now = _as_utc(now or datetime.now(timezone.utc))

    def _min(*values):
        present = [_as_utc(v) for v in values if v is not None]
        return min(present) if present else None

    obs_db = db.execute(select(func.min(VerificationObservationRow.observation_time))).scalar()
    obs_arch = earliest_archived_period(db, "observations")
    obs_arch_dt = (
        datetime.strptime(obs_arch, "%Y-%m").replace(tzinfo=timezone.utc) if obs_arch else None
    )
    snap_db = db.execute(select(func.min(AirportForecastSnapshotRow.fetched_at))).scalar()
    snap_arch = earliest_archived_period(db, "snapshots")
    snap_arch_dt = (
        datetime.strptime(snap_arch, "%Y-%m-%d").replace(tzinfo=timezone.utc) if snap_arch else None
    )
    earliest_obs = _min(obs_db, obs_arch_dt)
    earliest_model = _min(snap_db, snap_arch_dt)

    return {
        "latest": snap_time(now).isoformat(),
        "earliest_observation": earliest_obs.isoformat() if earliest_obs else None,
        "earliest_model": earliest_model.isoformat() if earliest_model else None,
        "step_minutes": int(TIME_STEP.total_seconds() // 60),
        "model_sample_hours": list(all_sample_hours()),
        "metar_max_age_min": int(METAR_MAX_AGE.total_seconds() // 60),
        "model_max_age_min": int(MODEL_MAX_AGE.total_seconds() // 60),
        "models": list(MODELS),
        "observed_sources": list(OBSERVED_SOURCES),
        "leads": [
            {
                "lead_days": n,
                "models": [m for m in MODELS if n <= MAP_FORECAST_DAYS.get(m, 0)],
            }
            for n in range(MAX_LEAD_DAYS + 1)
        ],
    }
