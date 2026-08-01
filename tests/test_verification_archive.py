"""Tests for the Parquet archive of the raw verification tables (#522 Phase 2).

The round-trip test is the one that matters: export a period, validate the
manifest, re-read the file with pyarrow, and re-import into an empty database
through the natural-key idempotent path. If that passes, deleting the MySQL
rows is recoverable; if it doesn't, Phase 3 must not be enabled.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from conftest import make_app_engine
from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    ArchiveManifestRow,
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.archive import (
    ArchiveError,
    archive_period,
    final_periods,
    ARCHIVE_SPECS,
    live_count,
    month_archive_ok,
    pending_periods,
    run_archive,
    snapshot_day_archived,
    verify_archives,
)

pytest.importorskip("pyarrow")


def _utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


MAY = _utc(2026, 5, 15, 12, 0)


@pytest.fixture
def archive_dir(tmp_path, monkeypatch):
    """Point DATA_DIR at a temp tree so the archive writes under it."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path / "archive" / "verification"


def _seed(db, *, n_obs: int = 3, hour_offset: int = 0) -> list[int]:
    """Insert observations with model + TAF scores; return observation ids.

    ``hour_offset`` shifts the observation hours so a second call adds new
    rows rather than colliding on ``uq_verif_obs_icao_time``.
    """
    ids = []
    for i in range(n_obs):
        when = MAY + timedelta(hours=hour_offset + i)
        obs = VerificationObservationRow(
            icao="LFPG",
            observation_time=when,
            collected_at=when,
            flight_category="MVFR",
            ceiling_ft=2000,
            visibility_m=6000,
            wind_dir=270,
            wind_speed_kt=12,
            wind_gust_kt=24 if i == 0 else None,
            temperature_c=11,
            dewpoint_c=8,
            qnh=1013.2,
            weather='["RA"]',
            metar_raw=f"LFPG 15{i:02d}00Z 27012KT 6000 BKN020",
            taf_issue_time=when - timedelta(hours=3),
            taf_flight_category="VFR",
            taf_wind_gust_kt=30 if i == 0 else None,
        )
        db.add(obs)
        db.flush()
        ids.append(obs.id)
        db.add(VerificationScoreRow(
            observation_id=obs.id,
            icao="LFPG",
            observation_time=when,
            model="gfs",
            model_init_time=when - timedelta(hours=6),
            lead_hours=6,
            days_out=0,
            source="standalone",
            obs_flight_category="MVFR",
            model_flight_category="VFR",
            category_match=False,
            ceiling_delta_ft=500.0,
            model_wind_gust_kt=28.0,
            model_gust_flag=True,
        ))
        db.add(TafVerificationScoreRow(
            observation_id=obs.id,
            icao="LFPG",
            observation_time=when,
            taf_issue_time=when - timedelta(hours=3),
            lead_hours=3,
            source="standalone",
            obs_flight_category="MVFR",
            taf_flight_category="VFR",
            category_match=False,
            ceiling_delta_ft=300.0,
        ))
    db.flush()
    return ids


class TestFinality:
    """A period is archived only once it can no longer change."""

    def test_current_month_is_never_final(self):
        spec = ARCHIVE_SPECS["scores"]
        _, newest = final_periods(spec, _utc(2026, 8, 25, 0, 0))
        assert newest == "2026-07"

    def test_month_becomes_final_ten_days_into_the_next(self):
        spec = ARCHIVE_SPECS["scores"]
        # Aug 10: July is not yet final (only 10 days into August at 00:00 on
        # the 11th), so June is the newest.
        _, newest = final_periods(spec, _utc(2026, 8, 10, 0, 0))
        assert newest == "2026-06"
        _, newest = final_periods(spec, _utc(2026, 8, 11, 0, 0))
        assert newest == "2026-07"

    def test_snapshot_day_is_final_at_d_plus_two(self):
        spec = ARCHIVE_SPECS["snapshots"]
        _, newest = final_periods(spec, _utc(2026, 5, 20, 6, 0))
        assert newest == "2026-05-18"


class TestArchivePeriod:

    def test_writes_file_and_manifest(self, db_session, archive_dir):
        _seed(db_session)

        result = archive_period(db_session, "scores", "2026-05")

        assert result["rows"] == 3
        assert result["skipped"] is False
        path = archive_dir / "scores" / "2026-05.parquet"
        assert path.exists()

        manifest = db_session.execute(
            select(ArchiveManifestRow).where(
                ArchiveManifestRow.table_name == "scores"
            )
        ).scalars().one()
        assert manifest.period == "2026-05"
        assert manifest.row_count == 3
        # Relative to the archive root so the tree stays relocatable.
        assert manifest.file_path == "scores/2026-05.parquet"
        assert len(manifest.sha256) == 64

    def test_columns_are_one_to_one_with_the_table(self, db_session, archive_dir):
        import pyarrow.parquet as pq

        _seed(db_session)
        archive_period(db_session, "observations", "2026-05")

        table = pq.read_table(archive_dir / "observations" / "2026-05.parquet")
        expected = [c.name for c in VerificationObservationRow.__table__.columns]
        assert table.column_names == expected
        # id is present, which is what makes the DuckDB join work.
        assert "id" in table.column_names

    def test_datetimes_are_utc_aware(self, db_session, archive_dir):
        import pyarrow.parquet as pq

        _seed(db_session, n_obs=1)
        archive_period(db_session, "observations", "2026-05")

        table = pq.read_table(archive_dir / "observations" / "2026-05.parquet")
        assert str(table.schema.field("observation_time").type) == "timestamp[us, tz=UTC]"
        got = table.column("observation_time")[0].as_py()
        assert got.tzinfo is not None
        assert got == MAY

    def test_rerun_is_skipped_when_nothing_changed(self, db_session, archive_dir):
        _seed(db_session)
        archive_period(db_session, "scores", "2026-05")
        again = archive_period(db_session, "scores", "2026-05")
        assert again["skipped"] is True

    def test_rerun_after_new_rows_rewrites(self, db_session, archive_dir):
        _seed(db_session, n_obs=1)
        archive_period(db_session, "scores", "2026-05")
        _seed(db_session, n_obs=1, hour_offset=5)  # a later hour arrives late
        result = archive_period(db_session, "scores", "2026-05")
        assert result["skipped"] is False
        assert result["rows"] == live_count(
            db_session, ARCHIVE_SPECS["scores"], "2026-05",
        )

    def test_empty_period_writes_an_empty_file(self, db_session, archive_dir):
        result = archive_period(db_session, "scores", "2026-01")
        assert result["rows"] == 0
        assert (archive_dir / "scores" / "2026-01.parquet").exists()

    def test_no_manifest_when_the_write_cannot_be_verified(
        self, db_session, archive_dir, monkeypatch,
    ):
        """A failed verification must leave nothing that could authorise a delete."""
        _seed(db_session)
        monkeypatch.setattr(
            "weatherbrief.tasks.archive.live_count", lambda *a, **k: 99,
        )

        with pytest.raises(ArchiveError):
            archive_period(db_session, "scores", "2026-05")

        assert db_session.execute(select(ArchiveManifestRow)).scalars().all() == []
        assert not (archive_dir / "scores" / "2026-05.parquet").exists()


class TestRoundTrip:
    """Export → verify → re-import into an empty database."""

    def test_reimport_reproduces_every_row(self, db_session, archive_dir):
        import pyarrow.parquet as pq

        _seed(db_session, n_obs=4)
        for name in ("observations", "scores", "taf_scores"):
            archive_period(db_session, name, "2026-05")

        report = verify_archives(db_session)
        assert [r for r in report if not r["ok"]] == []

        # Re-import into a fresh database through the ORM's natural-key path.
        engine = make_app_engine()
        fresh = sessionmaker(bind=engine)()
        try:
            for name, model in (
                ("observations", VerificationObservationRow),
                ("scores", VerificationScoreRow),
                ("taf_scores", TafVerificationScoreRow),
            ):
                table = pq.read_table(archive_dir / name / "2026-05.parquet")
                rows = table.to_pylist()
                fresh.execute(model.__table__.insert(), rows)
            fresh.commit()

            # Same row counts, same natural keys, same joinability.
            assert fresh.execute(
                select(VerificationObservationRow)
            ).scalars().all().__len__() == 4
            joined = fresh.execute(
                select(VerificationScoreRow.icao, VerificationObservationRow.icao)
                .join(
                    VerificationObservationRow,
                    VerificationScoreRow.observation_id
                    == VerificationObservationRow.id,
                )
            ).all()
            assert len(joined) == 4
            assert all(a == b == "LFPG" for a, b in joined)

            # Field-level fidelity on the columns that carry the awkward types:
            # a nullable gust, a JSON-ish text column, a float, a tz datetime.
            obs = fresh.execute(
                select(VerificationObservationRow)
                .order_by(VerificationObservationRow.observation_time)
            ).scalars().all()
            assert obs[0].wind_gust_kt == 24
            assert obs[1].wind_gust_kt is None
            assert obs[0].weather == '["RA"]'
            assert obs[0].qnh == pytest.approx(1013.2)
            assert obs[0].metar_raw.startswith("LFPG")
        finally:
            fresh.close()
            engine.dispose()


class TestPendingPeriods:

    def test_incremental_walks_from_the_newest_manifest(self, db_session, archive_dir):
        _seed(db_session)
        archive_period(db_session, "scores", "2026-05")

        pending = pending_periods(
            db_session, "scores", now=_utc(2026, 8, 15, 0, 0),
        )
        assert pending == ["2026-06", "2026-07"]

    def test_backfill_refills_a_gap(self, db_session, archive_dir):
        _seed(db_session)
        archive_period(db_session, "scores", "2026-05")
        # Simulate a manifest row lost to a restore: archive July, drop May.
        archive_period(db_session, "scores", "2026-07")
        db_session.execute(
            ArchiveManifestRow.__table__.delete().where(
                ArchiveManifestRow.period == "2026-05"
            )
        )
        db_session.flush()

        incremental = pending_periods(
            db_session, "scores", now=_utc(2026, 8, 15, 0, 0),
        )
        assert "2026-05" not in incremental  # walks forward from 2026-07

        full = pending_periods(
            db_session, "scores", now=_utc(2026, 8, 15, 0, 0), full=True,
        )
        assert "2026-05" in full


class TestVerifyArchives:

    def test_detects_a_tampered_file(self, db_session, archive_dir):
        _seed(db_session)
        archive_period(db_session, "scores", "2026-05")
        (archive_dir / "scores" / "2026-05.parquet").write_bytes(b"not parquet")

        report = verify_archives(db_session)
        assert [r["problem"] for r in report] == ["sha256 mismatch"]

    def test_detects_a_missing_file(self, db_session, archive_dir):
        _seed(db_session)
        archive_period(db_session, "scores", "2026-05")
        (archive_dir / "scores" / "2026-05.parquet").unlink()

        report = verify_archives(db_session)
        assert [r["problem"] for r in report] == ["file missing"]

    def test_detects_count_drift(self, db_session, archive_dir):
        _seed(db_session, n_obs=1)
        archive_period(db_session, "scores", "2026-05")
        _seed(db_session, n_obs=1, hour_offset=5)  # live table grows past manifest

        report = verify_archives(db_session)
        assert not report[0]["ok"]
        assert "live count 2 != manifest 1" in report[0]["problem"]


class TestGates:
    """What tasks/retention consults before deleting anything."""

    def test_month_needs_all_three_tables(self, db_session, archive_dir):
        _seed(db_session, n_obs=1)
        ok, reason = month_archive_ok(db_session, "2026-05")
        assert not ok and "observations" in reason

        archive_period(db_session, "observations", "2026-05")
        archive_period(db_session, "scores", "2026-05")
        ok, reason = month_archive_ok(db_session, "2026-05")
        assert not ok and "taf_scores" in reason

        archive_period(db_session, "taf_scores", "2026-05")
        assert month_archive_ok(db_session, "2026-05") == (True, "")

    def test_month_fails_when_live_count_moved(self, db_session, archive_dir):
        _seed(db_session, n_obs=1)
        for name in ("observations", "scores", "taf_scores"):
            archive_period(db_session, name, "2026-05")
        assert month_archive_ok(db_session, "2026-05")[0] is True

        _seed(db_session, n_obs=1, hour_offset=5)
        ok, reason = month_archive_ok(db_session, "2026-05")
        assert not ok
        assert "live count" in reason

    def test_snapshot_day_gate(self, db_session, archive_dir):
        db_session.add(AirportForecastSnapshotRow(
            icao="LFPG", model="gfs",
            model_init_time=_utc(2026, 5, 15, 0, 0),
            forecast_hour=_utc(2026, 5, 15, 12, 0),
            fetched_at=_utc(2026, 5, 15, 1, 0),
            temperature_2m_c=12.0,
        ))
        db_session.flush()

        assert snapshot_day_archived(db_session, date(2026, 5, 15)) is False
        archive_period(db_session, "snapshots", "2026-05-15")
        assert snapshot_day_archived(db_session, date(2026, 5, 15)) is True


class TestSnapshotPruneGate:
    """The 10-day snapshot prune waits for a manifest — but only once the
    archive is actually running, or a bounded table becomes unbounded."""

    def _add_snapshot(self, db, fetched: datetime):
        db.add(AirportForecastSnapshotRow(
            icao="LFPG", model="gfs",
            model_init_time=fetched,
            forecast_hour=fetched + timedelta(hours=6),
            fetched_at=fetched,
            temperature_2m_c=12.0,
        ))
        db.flush()

    def test_archive_off_prunes_as_before(self, db_session, archive_dir, monkeypatch):
        from weatherbrief.tasks.standalone_verification import _prune_old_snapshots

        monkeypatch.delenv("VERIFICATION_ARCHIVE_ENABLED", raising=False)
        self._add_snapshot(db_session, datetime.now(timezone.utc) - timedelta(days=30))

        assert _prune_old_snapshots(db_session) == 1

    def test_archive_on_stalls_without_a_manifest(
        self, db_session, archive_dir, monkeypatch,
    ):
        from weatherbrief.tasks.standalone_verification import _prune_old_snapshots

        monkeypatch.setenv("VERIFICATION_ARCHIVE_ENABLED", "1")
        old = datetime.now(timezone.utc) - timedelta(days=30)
        self._add_snapshot(db_session, old)

        assert _prune_old_snapshots(db_session) == 0
        assert db_session.execute(
            select(AirportForecastSnapshotRow)
        ).scalars().all() != []

        archive_period(db_session, "snapshots", old.date().isoformat())
        assert _prune_old_snapshots(db_session) == 1


class TestRunArchive:

    def test_one_bad_period_does_not_stop_the_others(
        self, db_session, archive_dir, monkeypatch,
    ):
        _seed(db_session)
        real = archive_period

        def flaky(db, table, period, **kw):
            if period == "2026-06":
                raise RuntimeError("disk full")
            return real(db, table, period, **kw)

        monkeypatch.setattr("weatherbrief.tasks.archive.archive_period", flaky)
        result = run_archive(
            db_session, now=_utc(2026, 8, 15, 0, 0), tables=("scores",),
        )

        assert result["errors"] == 1
        assert "2026-07" in result["archived"]["scores"]
