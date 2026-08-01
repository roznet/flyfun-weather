"""Row-level Parquet archive for the verification tables (#522, Phase 2).

Purpose
-------
The aggregate rollups answer every dashboard and digest question, but they
are lossy by construction: you cannot re-score history from a SUM. This
module writes the *rows* to Parquet under ``DATA_DIR/archive/verification/``
so the raw data outlives the 180-day MySQL window and stays usable for
re-scoring, calibration and ad-hoc data science.

It is **write-only**. Nothing here deletes a database row — that is
``tasks/retention``'s job, and it refuses to delete anything this module
hasn't archived and verified first.

Layout
------
::

    DATA_DIR/archive/verification/
      observations/YYYY-MM.parquet
      scores/YYYY-MM.parquet
      taf_scores/YYYY-MM.parquet
      snapshots/YYYY-MM-DD.parquet

The three score/observation tables are monthly; ``airport_forecast_snapshots``
is daily and partitioned by **``fetched_at`` UTC date**, not ``forecast_hour``,
so archive partitions line up exactly with the existing 10-day prune
predicate. DuckDB filters on ``forecast_hour`` inside the files perfectly
well.

Columns are 1:1 with the ORM tables **including ``id``**, so
``scores.observation_id -> observations.id`` joins still work in DuckDB.
Datetimes are written as UTC-aware microsecond timestamps: MySQL hands back
naive values, and they are stamped UTC on the way out rather than left
ambiguous in a file meant to be read years later.

Why snapshots are archived at all
---------------------------------
Snapshots are the only record of what the forecast actually *said* — scores
keep deltas. The gust work (#491) had to denormalise ``model_wind_gust_kt``
onto the score row precisely because snapshots are pruned at 10 days; the
archive generalises that lesson so future scoring improvements can be
replayed over history instead of only applying going forward.

Source of truth is the database, not the inbox artifacts: the droplet-fallback
compute path writes snapshots straight to MySQL and never emits an artifact,
so the SQLite inbox has holes on node-failure days.

Finality
--------
A period is archived only once it can no longer change:

- **Monthly tables** — month M is archived once we are ≥10 days into M+1.
  Late scores cannot arrive after that: scoring reads snapshots, and
  snapshots are pruned at 10 days. The current month is never archived.
- **Snapshots** — day D is archived at D+2. Rows are immutable after insert,
  and the compute-node artifact ingest restamps ``fetched_at`` at ingest
  time, so D+2 by ``fetched_at`` is always final.

Re-runnability
--------------
Every write is: keyset-paginated SELECT → Parquet temp file → fsync → verify
the file's row count against a live ``COUNT(*)`` → atomic rename → upsert the
manifest row. Re-archiving a period overwrites the file and updates the
manifest. If a live count later drifts from the manifest (a late row, a
repair), :func:`verify_archives` reports it and the fix is to re-archive, not
to error out.

Querying the archive with DuckDB
--------------------------------
::

    -- one month
    SELECT model, days_out, AVG(ABS(ceiling_delta_ft))
    FROM 'archive/verification/scores/2026-05.parquet'
    WHERE source = 'standalone'
    GROUP BY 1, 2;

    -- all history, joined to ground truth
    SELECT s.model, o.icao, o.flight_category, s.model_flight_category
    FROM 'archive/verification/scores/*.parquet' s
    JOIN 'archive/verification/observations/*.parquet' o
      ON s.observation_id = o.id
    WHERE o.observation_time >= TIMESTAMP '2026-01-01';

    -- what the forecast actually said, for re-scoring
    SELECT * FROM 'archive/verification/snapshots/*.parquet'
    WHERE icao = 'LFPG' AND model = 'ecmwf';
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import date as date_t, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    ArchiveManifestRow,
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_tiering import archive_root

logger = logging.getLogger(__name__)

# Rows pulled per keyset page. Sized so a page of the widest table
# (airport_forecast_snapshots, 27 columns) stays a few tens of MB in Python
# objects — the whole point of paginating is that a month of scores (~1.5M
# rows) never exists in memory at once.
_PAGE_ROWS = 50_000

# Monthly tables become final this many days into the following month. Ties
# to the 10-day snapshot retention: scoring reads snapshots, so once the
# snapshots for month M are gone no new score for M can be written.
MONTHLY_FINALITY_DAYS = 10

# Snapshot day D is archived at D + this many days.
SNAPSHOT_FINALITY_DAYS = 2


class ArchiveError(RuntimeError):
    """An archive write could not be verified and was therefore abandoned."""


@dataclass(frozen=True)
class ArchiveSpec:
    """How one table is partitioned into the archive."""

    name: str
    model: type
    time_column: str
    #: ``"monthly"`` (period ``YYYY-MM``) or ``"daily"`` (period ``YYYY-MM-DD``)
    granularity: str

    @property
    def table(self):
        return self.model.__table__

    @property
    def time_col(self):
        return getattr(self.model, self.time_column)


ARCHIVE_SPECS: dict[str, ArchiveSpec] = {
    "observations": ArchiveSpec(
        "observations", VerificationObservationRow, "observation_time", "monthly",
    ),
    "scores": ArchiveSpec(
        "scores", VerificationScoreRow, "observation_time", "monthly",
    ),
    "taf_scores": ArchiveSpec(
        "taf_scores", TafVerificationScoreRow, "observation_time", "monthly",
    ),
    "snapshots": ArchiveSpec(
        "snapshots", AirportForecastSnapshotRow, "fetched_at", "daily",
    ),
}

#: The three tables Phase 3's raw prune requires a manifest for.
MONTHLY_TABLES = ("observations", "scores", "taf_scores")


# ---------------------------------------------------------------------------
# Period arithmetic
# ---------------------------------------------------------------------------


def _month_bounds(period: str) -> tuple[datetime, datetime]:
    year, month = (int(p) for p in period.split("-"))
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    )
    return start, end


def _day_bounds(period: str) -> tuple[datetime, datetime]:
    d = date_t.fromisoformat(period)
    start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def period_bounds(spec: ArchiveSpec, period: str) -> tuple[datetime, datetime]:
    """Half-open ``[start, end)`` UTC bounds for a period string."""
    if spec.granularity == "monthly":
        return _month_bounds(period)
    return _day_bounds(period)


def month_period(dt: datetime | date_t) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def _next_month(period: str) -> str:
    year, month = (int(p) for p in period.split("-"))
    return f"{year + 1:04d}-01" if month == 12 else f"{year:04d}-{month + 1:02d}"


def final_periods(spec: ArchiveSpec, now: datetime) -> tuple[str, str]:
    """(oldest, newest) period that is safe to archive as of *now*.

    The newest is what the finality rules allow; the oldest is only used to
    bound a walk and is returned as ``""`` when unknown (the caller supplies
    its own start).
    """
    if spec.granularity == "monthly":
        # Month M is final once now >= start(M+1) + MONTHLY_FINALITY_DAYS.
        cutoff = now - timedelta(days=MONTHLY_FINALITY_DAYS)
        # The month *before* the month containing the cutoff is the newest
        # final one; the current month is never archived.
        newest = month_period(cutoff)
        newest = _prev_month(newest)
        return "", newest
    newest_day = (now - timedelta(days=SNAPSHOT_FINALITY_DAYS)).date()
    return "", newest_day.isoformat()


def _prev_month(period: str) -> str:
    year, month = (int(p) for p in period.split("-"))
    return f"{year - 1:04d}-12" if month == 1 else f"{year:04d}-{month - 1:02d}"


def _walk_periods(spec: ArchiveSpec, start: str, end: str) -> list[str]:
    """Every period from *start* to *end* inclusive."""
    if start > end:
        return []
    out: list[str] = []
    if spec.granularity == "monthly":
        cur = start
        while cur <= end:
            out.append(cur)
            cur = _next_month(cur)
        return out
    cur = date_t.fromisoformat(start)
    last = date_t.fromisoformat(end)
    while cur <= last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def get_manifest(db: Session, table_name: str, period: str) -> ArchiveManifestRow | None:
    return db.execute(
        select(ArchiveManifestRow).where(
            ArchiveManifestRow.table_name == table_name,
            ArchiveManifestRow.period == period,
        )
    ).scalars().first()


def list_manifests(db: Session, table_name: str | None = None) -> list[ArchiveManifestRow]:
    stmt = select(ArchiveManifestRow)
    if table_name:
        stmt = stmt.where(ArchiveManifestRow.table_name == table_name)
    return list(
        db.execute(
            stmt.order_by(ArchiveManifestRow.table_name, ArchiveManifestRow.period)
        ).scalars().all()
    )


def _upsert_manifest(
    db: Session, *, table_name: str, period: str, row_count: int,
    file_path: str, sha256: str,
) -> None:
    row = get_manifest(db, table_name, period)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(ArchiveManifestRow(
            table_name=table_name, period=period, row_count=row_count,
            file_path=file_path, sha256=sha256, created_at=now,
        ))
    else:
        row.row_count = row_count
        row.file_path = file_path
        row.sha256 = sha256
        row.created_at = now
    db.flush()


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def _known_sources(db: Session, spec: ArchiveSpec) -> list[str]:
    """Distinct ``source`` values, or ``[]`` if the table has no source column.

    Cheap despite table size: every source-carrying verification table leads
    an index with ``source``, so MySQL answers this with a loose index scan.
    """
    col = getattr(spec.model, "source", None)
    if col is None:
        return []
    return sorted(db.execute(select(col).distinct()).scalars().all())


def live_count(db: Session, spec: ArchiveSpec, period: str) -> int:
    """``COUNT(*)`` of live rows for a period.

    Names ``source`` in the WHERE whenever the table has one. This is
    redundant with the data — it lists every source already present — but it
    is what makes the count indexable: ``verification_scores`` has no index
    leading with ``observation_time``, so a bare time-range count is a full
    scan of 8.8M rows, while ``source IN (...) AND observation_time >= ... <
    ...`` becomes two range seeks on ``ix_verif_scores_source_time``. Same
    trick, same reason, as ``verification_daily_rollup._build_rollup_select``.
    """
    start, end = period_bounds(spec, period)
    stmt = select(func.count()).select_from(spec.table).where(
        spec.time_col >= start, spec.time_col < end,
    )
    sources = _known_sources(db, spec)
    if sources:
        stmt = stmt.where(spec.model.source.in_(sources))
    return int(db.execute(stmt).scalar() or 0)


# ---------------------------------------------------------------------------
# Parquet writing
# ---------------------------------------------------------------------------


def _require_pyarrow():
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise ArchiveError(
            "pyarrow is required for the verification archive "
            "(pip install 'pyarrow>=15'); see designs/metar-taf-accuracy.md"
        ) from exc
    import pyarrow as pa
    import pyarrow.parquet as pq
    return pa, pq


def _arrow_type(pa, col):
    """Map a SQLAlchemy column type to the archive's Arrow type.

    Deliberately narrow: these four tables use six column types between them,
    and an unrecognised one should fail loudly at write time rather than be
    silently stringified into a file nobody re-reads for two years.
    """
    t = col.type
    if isinstance(t, Boolean):
        return pa.bool_()
    if isinstance(t, DateTime):
        # Microsecond precision matches MySQL DATETIME(6) and Python's
        # datetime; UTC is stamped explicitly because MySQL returns naive.
        return pa.timestamp("us", tz="UTC")
    if isinstance(t, Date):
        return pa.date32()
    if isinstance(t, Integer):
        return pa.int64()
    if isinstance(t, Float):
        return pa.float64()
    if isinstance(t, (String, Text)):
        return pa.string()
    raise ArchiveError(
        f"No archive Arrow mapping for column {col.name} of type {t!r}"
    )


def _arrow_schema(pa, spec: ArchiveSpec):
    return pa.schema([
        pa.field(c.name, _arrow_type(pa, c), nullable=c.nullable or c.primary_key)
        for c in spec.table.columns
    ])


def _as_utc(value):
    """Stamp a naive datetime as UTC; leave everything else alone.

    MySQL returns naive datetimes for ``DateTime(timezone=True)`` columns and
    SQLite may return either. The archive is read long after the process that
    wrote it is gone, so the tz is made explicit here rather than left as a
    convention someone has to rediscover.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return value


def _iter_pages(db: Session, spec: ArchiveSpec, period: str):
    """Yield keyset-paginated pages of rows for a period, ordered by ``id``.

    Keyset rather than OFFSET: a month of scores is ~1.5M rows, and OFFSET
    pagination re-walks the skipped prefix on every page, so page N costs
    O(N·page) on the server. ``id > last_id`` costs the same for every page.
    """
    cols = [spec.table.c[c.name] for c in spec.table.columns]
    start, end = period_bounds(spec, period)
    last_id = 0
    while True:
        rows = db.execute(
            select(*cols)
            .where(
                spec.time_col >= start,
                spec.time_col < end,
                spec.model.id > last_id,
            )
            .order_by(spec.model.id)
            .limit(_PAGE_ROWS)
        ).all()
        if not rows:
            return
        yield rows
        last_id = rows[-1][0]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def archive_period(
    db: Session, table_name: str, period: str, *, force: bool = False,
) -> dict:
    """Write one (table, period) Parquet file and record its manifest row.

    Returns a summary dict. Raises :class:`ArchiveError` if the written file's
    row count doesn't match a live recount — the temp file is discarded and no
    manifest row is written, so a failed archive can never authorise a delete.

    Idempotent: re-archiving overwrites the file and updates the manifest.
    A period that already has a manifest row matching the live count is
    skipped unless *force*.
    """
    spec = ARCHIVE_SPECS[table_name]
    pa, pq = _require_pyarrow()

    expected = live_count(db, spec, period)
    existing = get_manifest(db, table_name, period)
    out_dir = archive_root() / table_name
    out_path = out_dir / f"{period}.parquet"

    if (
        not force
        and existing is not None
        and existing.row_count == expected
        and out_path.exists()
    ):
        return {
            "table": table_name, "period": period, "rows": expected,
            "path": str(out_path), "skipped": True,
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    schema = _arrow_schema(pa, spec)
    names = [c.name for c in spec.table.columns]
    tmp_path = out_dir / f".{period}.parquet.tmp"

    written = 0
    writer = pq.ParquetWriter(str(tmp_path), schema, compression="zstd")
    try:
        for page in _iter_pages(db, spec, period):
            columns = [
                pa.array([_as_utc(row[i]) for row in page], type=schema.field(i).type)
                for i in range(len(names))
            ]
            writer.write_table(pa.Table.from_arrays(columns, schema=schema))
            written += len(page)
        writer.close()
        writer = None

        if written != expected:
            raise ArchiveError(
                f"{table_name}/{period}: wrote {written} rows but the live "
                f"count is {expected} — refusing to record a manifest"
            )

        file_rows = pq.ParquetFile(str(tmp_path)).metadata.num_rows
        if file_rows != expected:
            raise ArchiveError(
                f"{table_name}/{period}: parquet holds {file_rows} rows, "
                f"expected {expected}"
            )

        digest = _sha256_file(tmp_path)
        os.replace(tmp_path, out_path)
    finally:
        if writer is not None:
            writer.close()
        if tmp_path.exists():
            tmp_path.unlink()

    _upsert_manifest(
        db, table_name=table_name, period=period, row_count=expected,
        # Relative to the archive root so the whole tree stays relocatable
        # (an off-box backup restores it under a different DATA_DIR).
        file_path=f"{table_name}/{period}.parquet",
        sha256=digest,
    )
    logger.info(
        "archive: wrote %s/%s (%d rows, %.1f MB)",
        table_name, period, expected, out_path.stat().st_size / (1024 * 1024),
    )
    return {
        "table": table_name, "period": period, "rows": expected,
        "path": str(out_path), "skipped": False,
    }


# ---------------------------------------------------------------------------
# Period discovery
# ---------------------------------------------------------------------------


def _earliest_period(db: Session, spec: ArchiveSpec) -> str | None:
    earliest = db.execute(select(func.min(spec.time_col))).scalar()
    if earliest is None:
        return None
    if earliest.tzinfo is None:
        earliest = earliest.replace(tzinfo=timezone.utc)
    if spec.granularity == "monthly":
        return month_period(earliest)
    return earliest.date().isoformat()


def pending_periods(
    db: Session, table_name: str, *, now: datetime | None = None,
    full: bool = False,
) -> list[str]:
    """Final periods for *table_name* that have no manifest row yet.

    Incremental mode (the default, used by the scheduled run) walks forward
    from the newest archived period, so the steady-state cost is one or two
    periods per day and no scan of the raw table.

    ``full=True`` (``archive backfill``) walks from the earliest live row
    instead, so gaps left by a deleted manifest row or an interrupted
    backfill are picked up. That start point costs one ``MIN(time_col)`` —
    acceptable for an explicitly-invoked backfill, not for a daily loop.
    """
    spec = ARCHIVE_SPECS[table_name]
    now = now or datetime.now(timezone.utc)
    _, newest = final_periods(spec, now)

    existing = {m.period for m in list_manifests(db, table_name)}

    if full or not existing:
        start = _earliest_period(db, spec)
        if start is None:
            return []
    else:
        newest_done = max(existing)
        start = (
            _next_month(newest_done)
            if spec.granularity == "monthly"
            else (date_t.fromisoformat(newest_done) + timedelta(days=1)).isoformat()
        )

    return [p for p in _walk_periods(spec, start, newest) if p not in existing]


def run_archive(
    db: Session, *, now: datetime | None = None, full: bool = False,
    tables: tuple[str, ...] | None = None,
) -> dict:
    """Archive every final, unarchived period. Caller commits.

    A failure on one period is logged and does not stop the others: a corrupt
    month must not block the archive of the months around it, and Phase 3's
    prune gate is per-period, so an unarchived month simply stays undeletable.
    A *missing pyarrow*, on the other hand, fails every period identically, so
    it is raised once here rather than logged N times as N separate mysteries.
    """
    _require_pyarrow()
    results: dict[str, list[str]] = {}
    errors = 0
    for name in (tables or tuple(ARCHIVE_SPECS)):
        done: list[str] = []
        for period in pending_periods(db, name, now=now, full=full):
            try:
                res = archive_period(db, name, period)
                if not res["skipped"]:
                    done.append(period)
            except Exception:
                errors += 1
                logger.error(
                    "archive: failed on %s/%s", name, period, exc_info=True,
                )
        results[name] = done
    return {"archived": results, "errors": errors}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_archives(
    db: Session, *, table_name: str | None = None, check_counts: bool = True,
) -> list[dict]:
    """Re-check every manifest row against its file (and optionally the DB).

    Returns one dict per manifest row with an ``ok`` flag and a ``problem``
    string when something is wrong. Reported, not raised: the point is a
    complete report of the archive's health, not a stop at the first bad row.

    A count mismatch is a "re-archive this period" signal, not corruption —
    a late-arriving row or a data repair (e.g. the #519 METAR fix) legitimately
    changes what a month contains.
    """
    out: list[dict] = []
    for m in list_manifests(db, table_name):
        path = archive_root() / m.file_path
        entry = {
            "table": m.table_name, "period": m.period,
            "rows": m.row_count, "ok": True, "problem": "",
        }
        if not path.exists():
            entry.update(ok=False, problem="file missing")
            out.append(entry)
            continue
        if _sha256_file(path) != m.sha256:
            entry.update(ok=False, problem="sha256 mismatch")
            out.append(entry)
            continue
        if check_counts:
            spec = ARCHIVE_SPECS.get(m.table_name)
            if spec is not None:
                live = live_count(db, spec, m.period)
                if live != m.row_count:
                    entry.update(
                        ok=False,
                        problem=f"live count {live} != manifest {m.row_count}",
                    )
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Gates consumed by tasks/retention (Phase 3)
# ---------------------------------------------------------------------------


def month_archive_ok(db: Session, period: str) -> tuple[bool, str]:
    """Whether all three monthly tables are archived and still match for *period*.

    This is the gate Phase 3's raw prune consults. It re-counts live rows
    rather than trusting the stored ``row_count``, so a month that gained rows
    after being archived (a late score, a repair) fails the gate and stays
    undeletable until it is re-archived. Returns ``(ok, reason)`` — the reason
    is what gets logged when a delete is refused, so it has to name the table.
    """
    for name in MONTHLY_TABLES:
        m = get_manifest(db, name, period)
        if m is None:
            return False, f"no {name} manifest for {period}"
        live = live_count(db, ARCHIVE_SPECS[name], period)
        if live != m.row_count:
            return False, (
                f"{name}/{period}: live count {live} != manifest {m.row_count}"
            )
    return True, ""


def snapshot_day_archived(db: Session, day: date_t) -> bool:
    """Whether ``snapshots`` for a ``fetched_at`` UTC date has a manifest row.

    The snapshot prune consults this per day. Deliberately *not* a count
    recheck: snapshot rows are immutable after insert, so a stale count can
    only mean rows arrived late, and the D+2 finality rule already covers that.
    """
    return get_manifest(db, "snapshots", day.isoformat()) is not None
