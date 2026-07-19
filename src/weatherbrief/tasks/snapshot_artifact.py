"""Portable forecast-snapshot artifacts: export (P2) + import (P3).

The standalone forecast cycle can be computed off the serving box and shipped in
as a self-contained file. This module defines that file format and the two
provider-agnostic operations:

- :func:`export_snapshots` (P2) reads ``airport_forecast_snapshots`` from a
  source DB and writes a portable, versioned **SQLite artifact** (a mirror table
  + a ``_manifest`` row). The compute node runs this; the artifact *is* the
  node's output — no persistent node DB required.
- :func:`import_snapshots` (P3) validates such an artifact (row count + content
  checksum) and **upserts** the rows into a target DB by natural key
  ``(icao, model, model_init_time, forecast_hour)``, recording a
  ``verification_cycles`` row (``source='standalone_forecast_imported'``).

Design invariants:

- **Dialect-agnostic.** The artifact is read with the stdlib ``sqlite3`` module
  and rows are upserted through SQLAlchemy Core, so import works identically on
  dev SQLite and prod MySQL. It never uses ``ATTACH`` (SQLite-only).
- **Idempotent.** Re-importing the same artifact, or two artifacts covering
  disjoint scopes, inserts each natural key at most once and never loses rows.
- **Self-validating.** The manifest carries a content checksum over the exact
  rows; a tampered or truncated artifact is rejected *before* any row is written
  (no partial import).
- **Column-driven.** The set of data columns is derived from the ORM model
  (minus the local ``id`` PK), so a new snapshot column (e.g. ``region`` in the
  US-expansion work) is carried automatically. Import writes only the
  intersection of artifact columns and the target table, so a newer artifact
  ingested by an older importer degrades gracefully instead of crashing.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import insert, select, tuple_

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    VerificationCycleRow,
    snapshot_natural_key,
)

# Bump when the on-disk artifact layout changes incompatibly.
ARTIFACT_SCHEMA_VERSION = 1

# verification_cycles.source tag written by import_snapshots. MUST fit
# VerificationCycleRow.source (String(24)) — prod MySQL rejects/truncates a
# longer value and, sharing the import transaction, would roll the import back.
IMPORT_CYCLE_SOURCE = "standalone_imported"

_SNAPSHOT_TABLE = "airport_forecast_snapshots"
_MANIFEST_TABLE = "_manifest"

# Natural key that uq_afs_key enforces.
_KEY_COLUMNS = ("icao", "model", "model_init_time", "forecast_hour")


class ArtifactValidationError(Exception):
    """Raised when an artifact fails manifest validation (no rows are imported)."""


def snapshot_columns() -> list[str]:
    """Data columns carried by an artifact — every model column except ``id``.

    Derived from the ORM table so new columns are picked up automatically.
    """
    return [c.name for c in AirportForecastSnapshotRow.__table__.columns if c.name != "id"]


@dataclass
class ArtifactManifest:
    """Sidecar metadata stored inside the artifact (``_manifest`` table)."""

    schema_version: int
    region: str | None
    generated_at: str  # ISO-8601 UTC
    source_host: str
    wall_time_ms: int
    row_count: int
    checksum: str  # sha256 hex over canonical rows
    models: dict[str, list[str]]  # model -> sorted list of init-time ISO strings
    columns: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True)

    @classmethod
    def from_json(cls, blob: str) -> "ArtifactManifest":
        # A manifest written by a mismatched producer (bad JSON, missing or
        # extra keys) must fail as a clean rejection, not a raw TypeError that
        # escapes the CLI's ArtifactValidationError handler.
        try:
            return cls(**json.loads(blob))
        except (ValueError, TypeError) as e:
            raise ArtifactValidationError(f"malformed manifest: {e}") from e


@dataclass
class ImportResult:
    rows_total: int
    rows_inserted: int
    rows_skipped: int
    manifest: ArtifactManifest


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _iso(value):
    """Canonical string form for a cell used in both storage and checksum."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_dt(value):
    """Inverse of :func:`_iso` for the datetime columns."""
    if value is None:
        return None
    return datetime.fromisoformat(value)


_DATETIME_COLUMNS = frozenset({"model_init_time", "forecast_hour", "fetched_at"})


def _canonical_checksum(rows: list[dict], columns: list[str]) -> str:
    """sha256 over rows, sorted by natural key, serialized deterministically.

    Independent of SQLite page layout: it hashes the *data*, so it survives
    transport/VACUUM and catches any changed, added, or dropped row.
    """
    def sort_key(r):
        return tuple(str(_iso(r.get(k))) for k in _KEY_COLUMNS)

    canonical = [
        [[c, _iso(r.get(c))] for c in columns]
        for r in sorted(rows, key=sort_key)
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()




# ---------------------------------------------------------------------------
# P2: export
# ---------------------------------------------------------------------------

def _select_rows(session, *, region, latest_per_model) -> list[dict]:
    columns = snapshot_columns()
    has_region = "region" in columns

    stmt = select(AirportForecastSnapshotRow)
    if region and region != "all" and has_region:
        # Forward-compat: only filter once the column exists (Stage 2).
        stmt = stmt.where(AirportForecastSnapshotRow.region == region)

    orm_rows = session.execute(stmt).scalars().all()
    rows = [{c: getattr(r, c) for c in columns} for r in orm_rows]

    if latest_per_model:
        # Keep only the freshest init per model — what a fresh cycle produced
        # and what a serving replica wants to ingest.
        latest: dict[str, datetime] = {}
        for r in rows:
            m, init = r["model"], r["model_init_time"]
            if m not in latest or init > latest[m]:
                latest[m] = init
        rows = [r for r in rows if r["model_init_time"] == latest[r["model"]]]

    return rows


def export_snapshots(
    session,
    path: str,
    *,
    region: str | None = None,
    latest_per_model: bool = True,
    wall_time_ms: int = 0,
    source_host: str | None = None,
    generated_at: datetime | None = None,
) -> ArtifactManifest:
    """Write the source DB's snapshots to a portable SQLite artifact at ``path``.

    Returns the manifest. ``region`` filters once the snapshot table has a
    ``region`` column (US-expansion); until then it is recorded in the manifest
    but not applied.
    """
    columns = snapshot_columns()
    rows = _select_rows(session, region=region, latest_per_model=latest_per_model)

    models: dict[str, list[str]] = {}
    for r in rows:
        models.setdefault(r["model"], set()).add(_iso(r["model_init_time"]))
    models = {m: sorted(inits) for m, inits in models.items()}

    gen_at = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    manifest = ArtifactManifest(
        schema_version=ARTIFACT_SCHEMA_VERSION,
        region=region,
        generated_at=gen_at.isoformat(),
        source_host=source_host or socket.gethostname(),
        wall_time_ms=wall_time_ms,
        row_count=len(rows),
        checksum=_canonical_checksum(rows, columns),
        models=models,
        columns=columns,
    )

    # Write to a temp file in the destination dir, then atomically rename into
    # place. This makes emit re-runnable (a fresh file each cycle, so no "table
    # already exists" on a fixed path) and crash-safe (a killed mid-write leaves
    # only the temp file — never a partial artifact at `path` that the shipping
    # process could pick up or that would block future emits).
    dest_dir = os.path.dirname(os.path.abspath(path))
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=".artifact-", suffix=".sqlite.tmp", dir=dest_dir,
    )
    os.close(tmp_fd)
    try:
        conn = sqlite3.connect(tmp_path)
        try:
            col_defs = ", ".join(f'"{c}"' for c in columns)
            conn.execute(f'CREATE TABLE "{_SNAPSHOT_TABLE}" ({col_defs})')
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f'INSERT INTO "{_SNAPSHOT_TABLE}" ({col_defs}) VALUES ({placeholders})',
                [[_iso(r.get(c)) for c in columns] for r in rows],
            )
            conn.execute(f'CREATE TABLE "{_MANIFEST_TABLE}" (manifest_json TEXT)')
            conn.execute(
                f'INSERT INTO "{_MANIFEST_TABLE}" (manifest_json) VALUES (?)',
                (manifest.to_json(),),
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, path)  # atomic within the same filesystem
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return manifest


# ---------------------------------------------------------------------------
# P3: import
# ---------------------------------------------------------------------------

def read_manifest(path: str) -> ArtifactManifest:
    # Guard first: sqlite3.connect() auto-creates a file, so a mistyped path
    # would otherwise leave a stray empty .sqlite behind instead of erroring.
    if not os.path.isfile(path):
        raise ArtifactValidationError(f"artifact not found: {path}")
    conn = sqlite3.connect(path)
    try:
        try:
            row = conn.execute(
                f'SELECT manifest_json FROM "{_MANIFEST_TABLE}"'
            ).fetchone()
        except sqlite3.OperationalError as e:
            raise ArtifactValidationError(f"artifact has no manifest: {e}") from e
        if not row:
            raise ArtifactValidationError("artifact manifest is empty")
        return ArtifactManifest.from_json(row[0])
    finally:
        conn.close()


def _read_rows(path: str, columns: list[str]) -> list[dict]:
    conn = sqlite3.connect(path)
    try:
        col_list = ", ".join(f'"{c}"' for c in columns)
        cur = conn.execute(f'SELECT {col_list} FROM "{_SNAPSHOT_TABLE}"')
        return [dict(zip(columns, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def _validate(path: str, *, verify_checksum: bool = True) -> tuple[ArtifactManifest, list[dict]]:
    """Read + validate an artifact. Row-count is ALWAYS checked; the (more
    expensive) content checksum only when ``verify_checksum`` — so
    ``--no-verify-checksum`` still rejects a truncated artifact, matching the
    flag's documented contract.
    """
    manifest = read_manifest(path)
    if manifest.schema_version > ARTIFACT_SCHEMA_VERSION:
        raise ArtifactValidationError(
            f"artifact schema v{manifest.schema_version} newer than supported "
            f"v{ARTIFACT_SCHEMA_VERSION}"
        )
    columns = manifest.columns or snapshot_columns()
    rows = _read_rows(path, columns)

    if len(rows) != manifest.row_count:
        raise ArtifactValidationError(
            f"row count mismatch: manifest={manifest.row_count} actual={len(rows)}"
        )
    if verify_checksum:
        actual = _canonical_checksum(rows, columns)
        if actual != manifest.checksum:
            raise ArtifactValidationError(
                f"checksum mismatch: manifest={manifest.checksum[:12]}… "
                f"actual={actual[:12]}…"
            )
    return manifest, rows


def import_snapshots(
    session,
    path: str,
    *,
    verify_checksum: bool = True,
) -> ImportResult:
    """Validate an artifact and upsert its snapshots into ``session``'s DB.

    Idempotent: rows whose natural key already exists are skipped. Raises
    :class:`ArtifactValidationError` (before writing anything) on a corrupt or
    truncated artifact — row-count is checked even with ``verify_checksum=False``.
    """
    t_start = time.monotonic()
    manifest, artifact_rows = _validate(path, verify_checksum=verify_checksum)

    # Only import columns the target table actually has (graceful cross-version).
    target_columns = set(snapshot_columns())
    import_columns = [c for c in (manifest.columns or snapshot_columns())
                      if c in target_columns]

    # `fetched_at` drives retention (`_prune_old_snapshots` deletes rows older
    # than 10 days by fetched_at). Stamp it at INGEST time, not the source's
    # original fetch time, so compute→ship→ingest latency never eats into the
    # replica's retention window and prunes freshly-arrived long-lead rows.
    ingest_now = datetime.now(timezone.utc)
    parsed_rows: list[dict] = []
    for r in artifact_rows:
        row = {}
        for c in import_columns:
            if c == "fetched_at":
                continue  # restamped below, not carried from the source
            v = r.get(c)
            row[c] = _parse_dt(v) if c in _DATETIME_COLUMNS else v
        row["fetched_at"] = ingest_now
        parsed_rows.append(row)

    inserted_rows = _idempotent_insert(session, parsed_rows)

    # Record this import's OWN elapsed time and the airports it actually wrote
    # (a no-op re-ingest correctly records 0), not the exporter's numbers.
    duration_ms = int((time.monotonic() - t_start) * 1000)
    session.add(VerificationCycleRow(
        started_at=ingest_now,
        duration_ms=duration_ms,
        source=IMPORT_CYCLE_SOURCE,
        airports=len({r["icao"] for r in inserted_rows}),
    ))
    session.commit()

    return ImportResult(
        rows_total=len(parsed_rows),
        rows_inserted=len(inserted_rows),
        rows_skipped=len(parsed_rows) - len(inserted_rows),
        manifest=manifest,
    )


def _idempotent_insert(session, rows: list[dict]) -> list[dict]:
    """Insert rows whose natural key is absent. Dialect-agnostic.

    Returns the rows actually inserted (so callers can report true work done).
    Uses the shared :func:`snapshot_natural_key` for dedup — the same technique
    as ``standalone_verification._store_snapshots``, so the two can't drift.
    Kept as its own light implementation (no import of that heavy module) so
    ingest stays runnable on the serving box.
    """
    if not rows:
        return []

    keys = [
        (r["icao"], r["model"], r["model_init_time"], r["forecast_hour"])
        for r in rows
    ]
    unique_keys = list(set(keys))

    existing: set[tuple] = set()
    for i in range(0, len(unique_keys), 500):
        chunk = unique_keys[i : i + 500]
        found = session.execute(
            select(
                AirportForecastSnapshotRow.icao,
                AirportForecastSnapshotRow.model,
                AirportForecastSnapshotRow.model_init_time,
                AirportForecastSnapshotRow.forecast_hour,
            ).where(
                tuple_(
                    AirportForecastSnapshotRow.icao,
                    AirportForecastSnapshotRow.model,
                    AirportForecastSnapshotRow.model_init_time,
                    AirportForecastSnapshotRow.forecast_hour,
                ).in_(chunk)
            )
        ).all()
        for r in found:
            existing.add(snapshot_natural_key(*r))

    to_insert: list[dict] = []
    for row in rows:
        k = snapshot_natural_key(row["icao"], row["model"],
                                 row["model_init_time"], row["forecast_hour"])
        if k in existing:
            continue
        existing.add(k)  # dedup within this artifact too
        to_insert.append(row)

    for i in range(0, len(to_insert), 1000):
        session.execute(insert(AirportForecastSnapshotRow), to_insert[i : i + 1000])
    return to_insert
