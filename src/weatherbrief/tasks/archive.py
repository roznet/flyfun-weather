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
Every write is: keyset-paginated SELECT → Parquet temp file → fsync (file and
directory) → verify the file's row count against a live ``COUNT(*)`` → atomic
rename → upsert the manifest row. Re-archiving a period overwrites the file
and updates the manifest. If a live count later drifts from the manifest (a
late row, a repair), :func:`verify_archives` reports it and the fix is to
re-archive, not to error out.

What the manifest proves
------------------------
``max_id`` — the highest primary key written for the period — is what makes
the Phase 3 delete gate exact. Primary keys are monotonic, so *any* row
inserted after the archive was written has a higher ``id``, regardless of its
``observation_time``. The gate therefore asks "does every live row in this
period have ``id <= max_id``?" rather than "do the counts still match".

Counts can't answer that question once pruning starts, because pruning
deliberately leaves ``source='flight'`` rows and flight-linked observations
behind: a pruned month's live count is permanently *below* its manifest count,
which a count-equality gate would report as a failure forever — making "the
archive is broken" indistinguishable from "this month is already done".
``max_id`` distinguishes them: rows going away is fine (the archive is a
superset), rows appearing is not.

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
import uuid
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
from sqlalchemy.types import TypeDecorator, to_instance

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
    max_id: int, file_path: str, sha256: str,
) -> None:
    row = get_manifest(db, table_name, period)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(ArchiveManifestRow(
            table_name=table_name, period=period, row_count=row_count,
            max_id=max_id, file_path=file_path, sha256=sha256, created_at=now,
        ))
    else:
        row.row_count = row_count
        row.max_id = max_id
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
    return int(
        db.execute(
            _period_filter(db, select(func.count()).select_from(spec.table), spec, period)
        ).scalar() or 0
    )


def live_max_id(db: Session, spec: ArchiveSpec, period: str) -> int:
    """Highest live primary key in a period, or 0 if the period is empty."""
    return int(
        db.execute(
            _period_filter(db, select(func.max(spec.model.id)), spec, period)
        ).scalar() or 0
    )


def _period_filter(db: Session, stmt, spec: ArchiveSpec, period: str, sources=None):
    """Add the period's time range (and the indexability source clause).

    *sources* lets a caller that applies this filter repeatedly — notably
    :func:`_iter_pages`, once per 50K-row page — resolve the distinct sources
    once and pass them in. Left to itself this re-issues ``SELECT DISTINCT
    source`` on every call, which for a ~1.5M-row month is ~30 redundant
    round-trips per period, times four tables, across the whole backfill.
    """
    start, end = period_bounds(spec, period)
    stmt = stmt.where(spec.time_col >= start, spec.time_col < end)
    if sources is None:
        sources = _known_sources(db, spec)
    if sources:
        stmt = stmt.where(spec.model.source.in_(sources))
    return stmt


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

    ``TypeDecorator`` columns are resolved to the type they wrap before
    matching. ``TZDateTime`` (#520) is a decorator over ``DateTime``, so an
    ``isinstance(t, DateTime)`` test is False for it and every converted
    column would raise here — the archive would refuse to write
    ``observation_time`` at all. Unwrapping keeps the "fail loudly on a type
    we have not thought about" property while following the decorator to the
    storage type that actually determines the Arrow mapping.
    """
    t = col.type
    while isinstance(t, TypeDecorator):
        t = to_instance(t.impl)
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
    """Arrow schema mirroring the table, nullability included.

    Nullability tracks the column, so a re-import can't quietly accept a NULL
    the source table forbids — and ``id`` in particular stays non-nullable,
    since the joins the archive exists to support depend on it.
    """
    return pa.schema([
        pa.field(c.name, _arrow_type(pa, c), nullable=c.nullable)
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

    Carries the same ``source IN (...)`` clause as :func:`live_count`, and for
    the same reason: nothing on ``verification_scores`` leads with
    ``observation_time``, so without it every page is a full scan of 8.8M rows
    — paid once per page, per period, for the whole backfill.
    """
    cols = [spec.table.c[c.name] for c in spec.table.columns]
    id_pos = [c.name for c in spec.table.columns].index("id")
    # Resolved once, not per page — see :func:`_period_filter`.
    sources = _known_sources(db, spec)
    last_id = 0
    while True:
        rows = db.execute(
            _period_filter(db, select(*cols), spec, period, sources=sources)
            .where(spec.model.id > last_id)
            .order_by(spec.model.id)
            .limit(_PAGE_ROWS)
        ).all()
        if not rows:
            return
        yield rows
        last_id = rows[-1][id_pos]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fsync_path(path: Path) -> None:
    """Flush a written file to stable storage."""
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry (the rename itself) to stable storage."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # pragma: no cover - not all filesystems allow this
        return
    try:
        os.fsync(fd)
    except OSError:  # pragma: no cover - e.g. some network filesystems
        pass
    finally:
        os.close(fd)


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

    pre_count = live_count(db, spec, period)
    pre_max_id = live_max_id(db, spec, period)
    existing = get_manifest(db, table_name, period)
    out_dir = archive_root() / table_name
    out_path = out_dir / f"{period}.parquet"

    if (
        not force
        and existing is not None
        and existing.row_count == pre_count
        and existing.max_id == pre_max_id
        and out_path.exists()
    ):
        return {
            "table": table_name, "period": period, "rows": pre_count,
            "path": str(out_path), "skipped": True,
        }

    out_dir.mkdir(parents=True, exist_ok=True)
    schema = _arrow_schema(pa, spec)
    names = [c.name for c in spec.table.columns]
    # Unique per writer, not a fixed `.{period}.parquet.tmp`. Two writers for
    # the same (table, period) are a supported configuration — the scheduled
    # run_archive can overlap a hand-run `verify archive backfill` (see
    # designs/multi-user-deployment.md) — and on a shared path they interleave
    # into one file. The row-count check would still pass, because it counts
    # live rows rather than the file, so the result is a hashed, manifested
    # Parquet matching neither writer's output. That manifest is the sole gate
    # retention consults before deleting the rows it claims to hold.
    tmp_path = out_dir / f".{period}.parquet.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"

    written = 0
    # The highest id actually written to the file. This — not a second
    # live_max_id() query — is what the manifest records, because max_id is
    # the *sole* gate Phase 3 consults before deleting rows
    # (:func:`period_fully_archived`). A row inserted between the post-write
    # live_count() and a separate live_max_id() would be invisible to the
    # count check and yet raise the recorded max_id above its own id, so the
    # gate would report the period fully archived and authorise deleting a row
    # that was never written. Taking it from the pages closes that window by
    # construction: max_id cannot describe a row the file does not contain.
    max_id = 0
    id_pos = names.index("id")
    writer = pq.ParquetWriter(str(tmp_path), schema, compression="zstd")
    try:
        for page in _iter_pages(db, spec, period):
            columns = [
                pa.array([_as_utc(row[i]) for row in page], type=schema.field(i).type)
                for i in range(len(names))
            ]
            writer.write_table(pa.Table.from_arrays(columns, schema=schema))
            written += len(page)
            # Pages arrive ordered by id, so the last row of the last page
            # carries the maximum; max() rather than an assignment so this
            # holds even if that ordering guarantee is ever relaxed.
            max_id = max(max_id, page[-1][id_pos])
        writer.close()
        writer = None

        # Recount *after* the write, not before. A row landing mid-write is
        # the one race this module has to survive: if it lands before
        # pagination reaches its key the keyset picks it up (ids ascend), and
        # if it lands after, the post-write count exceeds what was written and
        # this raises. Comparing against a count taken before the write would
        # match in both cases and record a manifest for a file that is missing
        # a row — a manifest that later authorises deleting it.
        expected = live_count(db, spec, period)

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
        _fsync_path(tmp_path)
        os.replace(tmp_path, out_path)
        # The rename is atomic but not durable: without fsyncing the directory
        # too, a crash can leave the manifest committed in MySQL while the
        # entry that points at the file never reached disk — and the manifest
        # is what authorises deleting the only other copy.
        _fsync_dir(out_dir)
    finally:
        if writer is not None:
            writer.close()
        if tmp_path.exists():
            tmp_path.unlink()

    _upsert_manifest(
        db, table_name=table_name, period=period, row_count=expected,
        max_id=max_id,
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

    Incremental mode (the default, used by the scheduled run) walks from the
    **oldest** archived period, not the newest, and filters out everything
    already in the manifest. Walking forward from ``max(existing)`` would be
    marginally cheaper and permanently wrong: if one period in a run fails
    while a later one succeeds, the max moves past the failure and every
    subsequent incremental run starts after it, so the gap is never retried
    and only an explicit ``archive backfill`` would ever find it. Filtering a
    set of at most a few hundred period strings costs nothing.

    ``full=True`` (``archive backfill``) starts from the earliest live row
    instead, which additionally picks up periods older than the first
    manifest. That start point costs one ``MIN(time_col)`` — acceptable for an
    explicitly-invoked backfill, not for a daily loop.
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
        start = min(existing)

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
                # Unlike the delete gate, this reports count drift in *both*
                # directions, because a human running `archive verify` wants
                # to see "this month shrank" too — after Phase 3 that is the
                # expected, healthy state of every pruned month, so it is
                # labelled as such rather than as a failure.
                live = live_count(db, spec, m.period)
                live_max = live_max_id(db, spec, m.period)
                if live_max > m.max_id:
                    entry.update(
                        ok=False,
                        problem=(
                            f"live rows newer than the archive "
                            f"(max id {live_max} > archived {m.max_id})"
                        ),
                    )
                elif live < m.row_count:
                    entry["problem"] = (
                        f"live count {live} < archived {m.row_count} "
                        f"(expected after pruning)"
                    )
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Gates consumed by tasks/retention (Phase 3)
# ---------------------------------------------------------------------------


def archive_file_ok(m: ArchiveManifestRow) -> tuple[bool, str]:
    """Whether a manifest's Parquet file is still present and unmodified.

    A manifest row is a database fact; the file it describes lives on a
    filesystem that can lose or corrupt it (a bad restore, a disk fault, an
    accidental ``rm``). Checking only the row would let the prune delete the
    last remaining copy of data whose "archive" is a dangling pointer, so the
    delete gates re-hash the file rather than trusting the manifest alone.

    This is the expensive part of the gate, which is why
    :func:`weatherbrief.tasks.retention.prunable_months` only reaches it for
    months that still have rows to delete — an already-pruned month never
    re-hashes its archive.
    """
    path = archive_root() / m.file_path
    if not path.exists():
        return False, f"{m.table_name}/{m.period}: archive file missing ({path})"
    if _sha256_file(path) != m.sha256:
        return False, f"{m.table_name}/{m.period}: archive file sha256 mismatch"
    return True, ""


def period_fully_archived(
    db: Session, table_name: str, period: str,
) -> tuple[bool, str]:
    """Whether every live row of a period is inside the archived file.

    Asks "is there a live row the archive doesn't have?", not "do the counts
    still match". Primary keys are monotonic, so any row inserted after the
    archive was written has ``id > manifest.max_id`` — regardless of its
    ``observation_time``, which matters because a backfilled old METAR gets a
    new high ``id``.

    Count equality cannot be used here: pruning deliberately leaves
    ``source='flight'`` rows and flight-linked observations behind, so a
    pruned month's live count sits permanently below its manifest count. A
    count gate would report every already-pruned month as broken forever,
    which is precisely the "is the archive broken or is this month done?"
    ambiguity the gate exists to prevent.
    """
    m = get_manifest(db, table_name, period)
    if m is None:
        return False, f"no {table_name} manifest for {period}"

    ok, reason = archive_file_ok(m)
    if not ok:
        return False, reason

    live_max = live_max_id(db, ARCHIVE_SPECS[table_name], period)
    if live_max > m.max_id:
        return False, (
            f"{table_name}/{period}: live rows newer than the archive "
            f"(max id {live_max} > archived {m.max_id}) — re-archive first"
        )
    return True, ""


def month_archive_ok(db: Session, period: str) -> tuple[bool, str]:
    """Whether all three monthly tables are safely archived for *period*.

    This is the gate Phase 3's raw prune consults before permanently deleting
    MySQL rows. Returns ``(ok, reason)`` — the reason is what gets logged when
    a delete is refused, so it names the table.
    """
    for name in MONTHLY_TABLES:
        ok, reason = period_fully_archived(db, name, period)
        if not ok:
            return False, reason
    return True, ""


def snapshot_day_archived(db: Session, day: date_t) -> tuple[bool, str]:
    """Whether ``snapshots`` for a ``fetched_at`` UTC date is safely archived.

    Same gate as :func:`month_archive_ok`, for the one table the 10-day
    snapshot prune deletes. Snapshot rows are immutable after insert, so the
    ``max_id`` check will normally pass trivially; the file-integrity half is
    what earns its keep here.
    """
    return period_fully_archived(db, "snapshots", day.isoformat())
