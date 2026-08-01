"""Tests for the TZDateTime type decorator (issue #520)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from weatherbrief.db.models import ModelDeliveryLogRow
from weatherbrief.db.types import TZDateTime


CYCLE = datetime(2026, 7, 1, 12, tzinfo=timezone.utc)


def _row(**overrides) -> ModelDeliveryLogRow:
    values = dict(
        source="ecmwf:direct",
        model="ecmwf",
        cycle_init=CYCLE,
        expected_at=CYCLE + timedelta(hours=7),
        published_at=CYCLE + timedelta(hours=7, minutes=12),
        detected_at=CYCLE + timedelta(hours=7, minutes=20),
        last_absent_at=CYCLE + timedelta(hours=7, minutes=5),
        observed_via="http_last_modified",
    )
    values.update(overrides)
    return ModelDeliveryLogRow(**values)


class TestRoundTrip:
    def test_reads_come_back_utc_aware(self, db_session):
        db_session.add(_row())
        db_session.flush()
        db_session.expire_all()

        loaded = db_session.execute(select(ModelDeliveryLogRow)).scalars().one()
        for field in ("cycle_init", "expected_at", "published_at",
                      "detected_at", "last_absent_at"):
            value = getattr(loaded, field)
            assert value.tzinfo == timezone.utc, field
        assert loaded.cycle_init == CYCLE

    def test_non_utc_aware_write_normalises_to_utc(self, db_session):
        cest = timezone(timedelta(hours=2))
        db_session.add(_row(cycle_init=CYCLE.astimezone(cest)))
        db_session.flush()
        db_session.expire_all()

        loaded = db_session.execute(select(ModelDeliveryLogRow)).scalars().one()
        assert loaded.cycle_init == CYCLE
        assert loaded.cycle_init.tzinfo == timezone.utc

    def test_nullable_column_roundtrips_none(self, db_session):
        db_session.add(_row(published_at=None, last_absent_at=None))
        db_session.flush()
        db_session.expire_all()

        loaded = db_session.execute(select(ModelDeliveryLogRow)).scalars().one()
        assert loaded.published_at is None
        assert loaded.last_absent_at is None


class TestNaiveRejection:
    def test_naive_write_raises(self, db_session):
        db_session.add(_row(cycle_init=CYCLE.replace(tzinfo=None)))
        with pytest.raises(Exception) as excinfo:
            db_session.flush()
        assert "naive datetime" in str(excinfo.value)

    def test_naive_query_bind_param_raises(self, db_session):
        db_session.add(_row())
        db_session.flush()
        with pytest.raises(Exception) as excinfo:
            db_session.execute(
                select(ModelDeliveryLogRow).where(
                    ModelDeliveryLogRow.cycle_init >= CYCLE.replace(tzinfo=None)
                )
            ).all()
        assert "naive datetime" in str(excinfo.value)

    def test_aware_query_bind_param_matches_stored_rows(self, db_session):
        db_session.add(_row())
        db_session.flush()
        rows = db_session.execute(
            select(ModelDeliveryLogRow).where(
                ModelDeliveryLogRow.cycle_init >= CYCLE - timedelta(hours=1)
            )
        ).scalars().all()
        assert len(rows) == 1


class TestSnapshotDedupPath:
    """The `tuple_(...).in_(...)` key lookup the standalone cycle dedups on.

    A composite-tuple IN is a less-common construct than a plain column
    comparison, and it is what decides whether a snapshot is re-inserted every
    cycle — so pin that the bind processor really is applied to its elements
    rather than assuming it.
    """

    def _snapshot(self, hour: datetime) -> dict:
        return dict(
            icao="EGTK", region="eu", model="ecmwf",
            model_init_time=CYCLE, forecast_hour=hour,
            fetched_at=CYCLE + timedelta(hours=1),
        )

    def test_aware_tuple_in_matches_the_stored_row(self, db_session):
        from sqlalchemy import tuple_

        from weatherbrief.db.models import (
            AirportForecastSnapshotRow, snapshot_insert_ignore,
        )

        hour = CYCLE + timedelta(hours=3)
        snapshot_insert_ignore(db_session, [self._snapshot(hour)])

        found = db_session.execute(
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
                ).in_([("EGTK", "ecmwf", CYCLE, hour)])
            )
        ).all()
        assert len(found) == 1
        assert found[0].model_init_time == CYCLE
        assert found[0].forecast_hour == hour

    def test_natural_key_agrees_between_memory_and_db_read(self, db_session):
        """`snapshot_natural_key` must key an in-memory row and its stored
        counterpart identically, including for a non-UTC aware value."""
        from weatherbrief.db.models import (
            AirportForecastSnapshotRow, snapshot_natural_key,
        )

        from weatherbrief.db.models import snapshot_insert_ignore

        cest = timezone(timedelta(hours=2))
        hour = (CYCLE + timedelta(hours=3)).astimezone(cest)
        snapshot_insert_ignore(db_session, [self._snapshot(hour)])

        stored = db_session.execute(
            select(AirportForecastSnapshotRow)
        ).scalars().one()
        assert snapshot_natural_key(
            "EGTK", "ecmwf", CYCLE, hour,
        ) == snapshot_natural_key(
            stored.icao, stored.model,
            stored.model_init_time, stored.forecast_hour,
        )


class TestDdl:
    def test_default_ddl_is_plain_datetime_on_mysql(self):
        from sqlalchemy.dialects import mysql

        compiled = TZDateTime().compile(dialect=mysql.dialect())
        assert compiled == "DATETIME"

    def test_fsp_variant_renders_datetime_6_on_mysql(self):
        from sqlalchemy.dialects import mysql

        compiled = TZDateTime(fsp=6).compile(dialect=mysql.dialect())
        assert compiled == "DATETIME(6)"

    def test_fsp_is_ignored_on_sqlite(self):
        from sqlalchemy.dialects import sqlite

        compiled = TZDateTime(fsp=6).compile(dialect=sqlite.dialect())
        assert compiled == "DATETIME"
