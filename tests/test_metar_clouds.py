"""Cloud detail on verification observations (#793): derivation, ingest, backfill."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from weatherbrief.db.models import VerificationObservationRow
from weatherbrief.tasks.metar_clouds import (
    backfill_cloud_layers,
    derive_archived_month,
    metar_cloud_fields,
)


@pytest.fixture
def db_engine():
    """Per-test engine — the backfill commits."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()


def _layers(fields):
    return json.loads(fields["cloud_layers"])


class TestMetarCloudFields:

    def test_layers_lowest_base_and_cb(self):
        f = metar_cloud_fields(
            "METAR ZZAA 171220Z 24015G25KT 9999 -TSRA FEW008 SCT025CB BKN040 12/10 Q1012"
        )
        assert _layers(f) == [
            {"amount": "FEW", "base_ft": 800, "type": None},
            {"amount": "SCT", "base_ft": 2500, "type": "CB"},
            {"amount": "BKN", "base_ft": 4000, "type": None},
        ]
        # The VFR question: a FEW base under a 4000 ft ceiling.
        assert f["lowest_cloud_base_ft"] == 800
        assert f["convective_cloud"] == "CB"

    def test_trend_cloud_is_not_observed(self):
        """A TEMPO TCU is a forecast, not an observation (same rule as the live layer)."""
        f = metar_cloud_fields(
            "METAR ZZAA 171220Z 24010KT 9999 SCT030 12/08 Q1012 TEMPO BKN015TCU"
        )
        assert _layers(f) == [{"amount": "SCT", "base_ft": 3000, "type": None}]
        assert f["convective_cloud"] is None

    @pytest.mark.parametrize("tail", [
        "TEMPO BKN008", "BECMG FEW005", "RMK BKN006", "NOSIG RMK OVC004 VV002",
    ])
    def test_trend_and_remark_groups_never_lower_the_base(self, tail):
        f = metar_cloud_fields(f"METAR ZZAA 171220Z 24010KT 9999 SCT030 12/08 Q1012 {tail}")
        assert _layers(f) == [{"amount": "SCT", "base_ft": 3000, "type": None}]
        assert f["lowest_cloud_base_ft"] == 3000

    def test_cb_outranks_tcu(self):
        f = metar_cloud_fields(
            "METAR ZZAA 171220Z 24010KT 9999 FEW030TCU SCT045CB 12/08 Q1012"
        )
        assert f["convective_cloud"] == "CB"

    def test_tcu_alone(self):
        f = metar_cloud_fields("METAR ZZAA 171220Z 24010KT 9999 SCT030TCU 12/08 Q1012")
        assert f["convective_cloud"] == "TCU"

    def test_no_cloud_is_an_empty_list_not_null(self):
        f = metar_cloud_fields("METAR ZZAA 171220Z 24010KT CAVOK 12/08 Q1012")
        assert _layers(f) == []
        assert f["lowest_cloud_base_ft"] is None
        assert f["convective_cloud"] is None

    def test_vertical_visibility_is_a_layer_but_not_a_base(self):
        f = metar_cloud_fields("METAR ZZAA 171230Z 00000KT 0100 FG VV001 08/08 Q1020")
        assert _layers(f) == [{"amount": "VV", "base_ft": 100, "type": None}]
        assert f["lowest_cloud_base_ft"] is None

    def test_empty_or_missing_raw(self):
        assert metar_cloud_fields(None) is None
        assert metar_cloud_fields("  ") is None


class TestIngest:

    def test_fetched_observation_carries_cloud_detail(self):
        from weatherbrief.tasks.verification import fetch_observations_batch

        now = datetime.now(timezone.utc)
        metar = SimpleNamespace(
            observation_time=now - timedelta(minutes=20),
            report_type="METAR",
            raw_text="METAR ZZAA 011200Z 27010KT 9999 FEW012 BKN040TCU 12/05 Q1013",
            ceiling_ft=4000, visibility_meters=9999, wind_direction=270,
            wind_speed=10, wind_gust=None, temperature=12, dewpoint=5,
            altimeter=1013.0, weather_conditions=[], flight_category=None,
        )
        reports = MagicMock()
        reports.metars.return_value.chronological.return_value.all.return_value = [metar]
        airport = SimpleNamespace(icao="ZZAA", reports=reports, latest_taf=None)
        service = MagicMock()
        service.fetch_route_weather.return_value = SimpleNamespace(airports=[airport])

        with patch(
            "euro_aip.briefing.weather.route_weather.RouteWeatherService",
            return_value=service,
        ), patch(
            "weatherbrief.airports._load_airport_model", return_value=object(),
        ), patch(
            "euro_aip.briefing.weather.analysis.WeatherAnalyzer.find_applicable_taf",
            return_value=None,
        ):
            [obs] = fetch_observations_batch(["ZZAA"], "unused.db")

        assert obs.lowest_cloud_base_ft == 1200
        assert obs.convective_cloud == "TCU"
        assert json.loads(obs.cloud_layers)[1] == {
            "amount": "BKN", "base_ft": 4000, "type": "TCU",
        }

    def test_store_writes_cloud_columns(self, db_session):
        from weatherbrief.models.verification import VerificationObservation
        from weatherbrief.tasks.verification import store_observations

        raw = "METAR ZZAA 011200Z 27010KT 9999 SCT025CB 12/05 Q1013"
        t = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
        obs = VerificationObservation(
            icao="ZZAA", observation_time=t, collected_at=t, metar_raw=raw,
            **metar_cloud_fields(raw),
        )
        assert store_observations([obs], {}, db_session) == 1
        row = db_session.execute(select(VerificationObservationRow)).scalar_one()
        assert row.convective_cloud == "CB"
        assert row.lowest_cloud_base_ft == 2500


def _old_row(db, icao, minute, raw):
    t = datetime(2026, 7, 1, 12, minute, tzinfo=timezone.utc)
    db.add(VerificationObservationRow(
        icao=icao, observation_time=t, collected_at=t, metar_raw=raw,
    ))


class TestBackfill:

    def _seed(self, db):
        _old_row(db, "ZZAA", 0, "METAR ZZAA 011200Z 27010KT 9999 FEW008 SCT030CB 12/05 Q1013")
        _old_row(db, "ZZAB", 0, "METAR ZZAB 011200Z 27010KT CAVOK 12/05 Q1013")
        _old_row(db, "ZZAC", 0, None)
        db.commit()

    def test_dry_run_counts_without_writing(self, db_session):
        self._seed(db_session)
        result = backfill_cloud_layers(db_session, batch_size=2, dry_run=True)
        assert (result.scanned, result.updated, result.with_cb) == (2, 2, 1)
        rows = db_session.execute(select(VerificationObservationRow)).scalars().all()
        assert all(r.cloud_layers is None for r in rows)

    def test_backfill_matches_ingest_and_is_resumable(self, db_session):
        self._seed(db_session)
        result = backfill_cloud_layers(db_session, batch_size=2)
        assert result.updated == 2
        db_session.expire_all()
        by_icao = {
            r.icao: r
            for r in db_session.execute(select(VerificationObservationRow)).scalars()
        }
        a = by_icao["ZZAA"]
        expected = metar_cloud_fields(a.metar_raw)
        assert (a.cloud_layers, a.lowest_cloud_base_ft, a.convective_cloud) == (
            expected["cloud_layers"], 800, "CB",
        )
        assert by_icao["ZZAB"].cloud_layers == "[]"
        assert by_icao["ZZAC"].cloud_layers is None  # nothing to derive from
        # Second run: nothing left to do.
        assert backfill_cloud_layers(db_session).scanned == 0


def test_archived_month_side_file(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    from weatherbrief.tasks import archive

    src = tmp_path / "verification" / "observations" / "2026-06.parquet"
    src.parent.mkdir(parents=True)
    t = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
    pq.write_table(pa.table({
        "id": [7, 8],
        "metar_raw": [
            "METAR ZZAA 031200Z 27010KT 9999 BKN012TCU 12/05 Q1013",
            "garbage",
        ],
        "observation_time": pa.array([t, t], pa.timestamp("us", tz="UTC")),
    }), src)
    monkeypatch.setattr(archive, "archive_file_path", lambda table, period: src)

    out_root = tmp_path / "derived"
    n = derive_archived_month("2026-06", out_root=out_root)

    rows = pq.read_table(out_root / "2026-06.parquet").to_pylist()
    assert n == len(rows)
    assert rows[0]["id"] == 7
    assert rows[0]["convective_cloud"] == "TCU"
    assert rows[0]["lowest_cloud_base_ft"] == 1200

    monkeypatch.setattr(
        archive, "archive_file_path", lambda table, period: tmp_path / "missing.parquet",
    )
    assert derive_archived_month("2026-05", out_root=out_root) is None
