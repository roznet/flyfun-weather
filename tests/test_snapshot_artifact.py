"""Round-trip, idempotency, and rejection tests for snapshot artifacts (P2/P3).

These exercise the emit → ingest contract entirely on in-memory SQLite (the same
dialect-agnostic code runs against prod MySQL). They are the correctness gate for
the compute-offload pipeline; the cross-machine dogfood test is layered on top.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    Base,
    VerificationCycleRow,
)
from weatherbrief.tasks.snapshot_artifact import (
    ArtifactValidationError,
    IMPORT_CYCLE_SOURCE,
    export_snapshots,
    import_snapshots,
    read_manifest,
    snapshot_columns,
)

NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
GFS_INIT = datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
ECMWF_INIT = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)


def _make_db():
    """Fresh in-memory app DB + session factory (isolated per call)."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    import weatherbrief.db.models  # noqa: F401
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _snap(icao, model, init, hour_offset, **overrides):
    fields = dict(
        icao=icao, model=model, model_init_time=init,
        forecast_hour=datetime(2026, 4, 5, hour_offset, 0, 0, tzinfo=timezone.utc),
        fetched_at=NOW,
        temperature_2m_c=12.0 + hour_offset, dewpoint_2m_c=7.0,
        visibility_m=9999.0, wind_speed_10m_kt=10.0,
        wind_direction_10m_deg=270.0, wind_gusts_10m_kt=15.0,
        precipitation_mm=0.0, snowfall_cm=0.0, cape_jkg=50.0,
        cloud_cover_pct=30.0, cloud_cover_low_pct=10.0,
        sounding_ceiling_ft=4000.0, sounding_convective_risk="none",
    )
    fields.update(overrides)
    return AirportForecastSnapshotRow(**fields)


def _seed(session, rows):
    for r in rows:
        session.add(r)
    session.commit()


def _all_snapshots(session):
    """All snapshot rows as comparable dicts (natural-key sorted, no id)."""
    cols = snapshot_columns()
    rows = session.execute(select(AirportForecastSnapshotRow)).scalars().all()
    dicts = [{c: getattr(r, c) for c in cols} for r in rows]
    return sorted(dicts, key=lambda d: (d["icao"], d["model"],
                                        str(d["model_init_time"]),
                                        str(d["forecast_hour"])))


# ---------------------------------------------------------------------------
# Round-trip equivalence
# ---------------------------------------------------------------------------

def test_round_trip_equivalence(tmp_path):
    """Ingesting an artifact reproduces the source rows exactly (all columns)."""
    Src = _make_db()
    src = Src()
    seeded = [
        _snap("LFPG", "gfs", GFS_INIT, 12),
        _snap("LFPG", "gfs", GFS_INIT, 13),
        _snap("EDDF", "gfs", GFS_INIT, 12, nwp_ceiling_ft=1500.0),
        _snap("LFPG", "ecmwf", ECMWF_INIT, 12, precip_period_h=3),
    ]
    _seed(src, seeded)
    expected = _all_snapshots(src)

    artifact = str(tmp_path / "eu.sqlite")
    manifest = export_snapshots(src, artifact, region="eu", generated_at=NOW)
    assert manifest.row_count == 4
    assert set(manifest.models) == {"gfs", "ecmwf"}

    Dst = _make_db()
    dst = Dst()
    result = import_snapshots(dst, artifact)
    assert result.rows_inserted == 4
    assert result.rows_skipped == 0

    assert _all_snapshots(dst) == expected

    # And a cycle row was recorded with the import source tag.
    cycles = dst.execute(select(VerificationCycleRow)).scalars().all()
    assert len(cycles) == 1
    assert cycles[0].source == IMPORT_CYCLE_SOURCE
    assert cycles[0].airports == 2  # LFPG + EDDF


def test_latest_init_per_model_is_exported(tmp_path):
    """export keeps only the freshest init per model (what a serving replica wants)."""
    Src = _make_db()
    src = Src()
    old_init = datetime(2026, 4, 4, 12, tzinfo=timezone.utc)
    new_init = datetime(2026, 4, 5, 0, tzinfo=timezone.utc)
    _seed(src, [
        _snap("LFPG", "gfs", old_init, 12),
        _snap("LFPG", "gfs", new_init, 12),
    ])

    artifact = str(tmp_path / "latest.sqlite")
    manifest = export_snapshots(src, artifact, generated_at=NOW)
    assert manifest.row_count == 1
    # Only the newer init is exported (SQLite hands datetimes back naive, so
    # compare on the date/time, not tz — prod MySQL stores these naive too).
    assert list(manifest.models) == ["gfs"]
    assert len(manifest.models["gfs"]) == 1
    assert manifest.models["gfs"][0].startswith("2026-04-05T00:00")

    Dst = _make_db()
    dst = Dst()
    import_snapshots(dst, artifact)
    rows = _all_snapshots(dst)
    assert len(rows) == 1
    assert rows[0]["model_init_time"].replace(tzinfo=None) == new_init.replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_ingest_is_idempotent(tmp_path):
    """Re-importing the same artifact inserts nothing the second time."""
    Src = _make_db()
    src = Src()
    _seed(src, [_snap("LFPG", "gfs", GFS_INIT, 12), _snap("LFPG", "gfs", GFS_INIT, 13)])
    artifact = str(tmp_path / "a.sqlite")
    export_snapshots(src, artifact, generated_at=NOW)

    Dst = _make_db()
    dst = Dst()
    first = import_snapshots(dst, artifact)
    second = import_snapshots(dst, artifact)

    assert first.rows_inserted == 2
    assert second.rows_inserted == 0
    assert second.rows_skipped == 2
    assert len(_all_snapshots(dst)) == 2  # no duplication


def test_two_disjoint_artifacts_no_loss(tmp_path):
    """Two artifacts covering disjoint airports both ingest with no dup/loss."""
    Eu = _make_db()
    eu = Eu()
    _seed(eu, [_snap("LFPG", "gfs", GFS_INIT, 12), _snap("EDDF", "gfs", GFS_INIT, 12)])
    eu_art = str(tmp_path / "eu.sqlite")
    export_snapshots(eu, eu_art, region="eu", generated_at=NOW)

    Us = _make_db()
    us = Us()
    _seed(us, [_snap("KJFK", "gfs", GFS_INIT, 12), _snap("KLAX", "gfs", GFS_INIT, 12)])
    us_art = str(tmp_path / "us.sqlite")
    export_snapshots(us, us_art, region="us", generated_at=NOW)

    Dst = _make_db()
    dst = Dst()
    r_eu = import_snapshots(dst, eu_art)
    r_us = import_snapshots(dst, us_art)

    assert r_eu.rows_inserted == 2
    assert r_us.rows_inserted == 2
    icaos = {r["icao"] for r in _all_snapshots(dst)}
    assert icaos == {"LFPG", "EDDF", "KJFK", "KLAX"}


# ---------------------------------------------------------------------------
# Rejection (no partial import)
# ---------------------------------------------------------------------------

def test_corrupt_row_rejected_no_partial_import(tmp_path):
    """A tampered row fails the checksum; nothing is written to the target."""
    Src = _make_db()
    src = Src()
    _seed(src, [_snap("LFPG", "gfs", GFS_INIT, 12), _snap("EDDF", "gfs", GFS_INIT, 12)])
    artifact = str(tmp_path / "bad.sqlite")
    export_snapshots(src, artifact, generated_at=NOW)

    # Tamper a data value directly in the artifact (manifest checksum unchanged).
    conn = sqlite3.connect(artifact)
    conn.execute(
        'UPDATE airport_forecast_snapshots SET temperature_2m_c = 99.0 '
        'WHERE icao = "LFPG"'
    )
    conn.commit()
    conn.close()

    Dst = _make_db()
    dst = Dst()
    with pytest.raises(ArtifactValidationError, match="checksum mismatch"):
        import_snapshots(dst, artifact)

    assert _all_snapshots(dst) == []  # no partial write
    assert dst.execute(select(VerificationCycleRow)).scalars().all() == []


def test_row_count_mismatch_rejected(tmp_path):
    """A truncated artifact (row dropped) is rejected on the count check."""
    Src = _make_db()
    src = Src()
    _seed(src, [_snap("LFPG", "gfs", GFS_INIT, 12), _snap("EDDF", "gfs", GFS_INIT, 12)])
    artifact = str(tmp_path / "trunc.sqlite")
    export_snapshots(src, artifact, generated_at=NOW)

    conn = sqlite3.connect(artifact)
    conn.execute('DELETE FROM airport_forecast_snapshots WHERE icao = "EDDF"')
    conn.commit()
    conn.close()

    Dst = _make_db()
    dst = Dst()
    with pytest.raises(ArtifactValidationError, match="row count mismatch"):
        import_snapshots(dst, artifact)
    assert _all_snapshots(dst) == []


def test_missing_manifest_rejected(tmp_path):
    """A file with no manifest table is rejected, not silently ingested."""
    path = str(tmp_path / "empty.sqlite")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()

    with pytest.raises(ArtifactValidationError):
        read_manifest(path)


def test_bypass_checksum_still_imports(tmp_path):
    """--no-verify-checksum path still loads a (valid) artifact."""
    Src = _make_db()
    src = Src()
    _seed(src, [_snap("LFPG", "gfs", GFS_INIT, 12)])
    artifact = str(tmp_path / "ok.sqlite")
    export_snapshots(src, artifact, generated_at=NOW)

    Dst = _make_db()
    dst = Dst()
    result = import_snapshots(dst, artifact, verify_checksum=False)
    assert result.rows_inserted == 1
