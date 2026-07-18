"""Tests for the standalone airport verification pipeline.

Covers:
- Airport watchlist: discover, save, load, load_with_coords
- Snapshot to HourlyForecast adapter
- Forecast snapshot storage (Phase B)
- Observation fetch + store (Phase C, reuses existing infra)
- Scoring against stored snapshots (Phase D)
- Pruning old snapshots (Phase E)
- Scheduler helper: _seconds_until_next_sample_hour
- Full cycle orchestration (run_standalone_cycle)
- Verification stats source filtering
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import responses
from sqlalchemy import select


@pytest.fixture
def db_engine():
    """Per-test engine — functions under test call session.commit()."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    TafVerificationScoreRow,
    VerificationCycleRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.airport_watchlist import (
    DEFAULT_PREFIXES,
    WatchlistAirport,
    discover_airports,
    load_watchlist,
    load_watchlist_with_coords,
    save_watchlist,
)
from weatherbrief.tasks.standalone_verification import (
    SAMPLE_HOURS_UTC,
    STANDALONE_MODELS,
    _fetch_and_store_observations,
    _prune_old_snapshots,
    _score_cycle,
    _snapshot_to_hourly,
    _store_snapshots,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = _utc(2026, 4, 2, 12, 0)
GFS_INIT = _utc(2026, 4, 2, 6, 0)
ICON_INIT = _utc(2026, 4, 2, 6, 0)


# ---------------------------------------------------------------------------
# Airport watchlist
# ---------------------------------------------------------------------------


class TestSaveLoadWatchlist:

    def test_save_and_load_roundtrip(self, tmp_path):
        airports = {"LF": ["LFPG", "LFBO"], "ED": ["EDDF"]}
        save_watchlist(airports, tmp_path)
        loaded = load_watchlist(tmp_path)
        assert loaded == airports

    def test_load_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="watchlist not found"):
            load_watchlist(tmp_path)

    def test_save_includes_generated_at(self, tmp_path):
        save_watchlist({"LF": ["LFPG"]}, tmp_path)
        data = json.loads((tmp_path / "airport_watchlist.json").read_text())
        assert "generated_at" in data

    def test_load_with_coords_filters_missing(self, tmp_path):
        save_watchlist({"LF": ["LFPG", "XXXX"]}, tmp_path)

        mock_airport = SimpleNamespace(latitude_deg=49.01, longitude_deg=2.55)
        mock_model = MagicMock()
        mock_model.airports.__getitem__ = MagicMock(side_effect=lambda k: mock_airport if k == "LFPG" else (_ for _ in ()).throw(KeyError(k)))

        with patch("weatherbrief.airports._load_airport_model", return_value=mock_model):
            result = load_watchlist_with_coords(tmp_path, "/fake/db")

        assert len(result) == 1
        assert result[0].icao == "LFPG"
        assert result[0].lat == pytest.approx(49.01)


class TestDiscoverAirports:

    @responses.activate
    def test_discover_filters_to_reporting(self):
        """Airports in DB but not returning METARs are excluded."""
        mock_model = MagicMock()
        mock_model.airports.__iter__ = MagicMock(
            return_value=iter([
                SimpleNamespace(ident="LSGG"),
                SimpleNamespace(ident="LSZH"),
                SimpleNamespace(ident="LSGE"),  # no METAR
            ])
        )

        # aviationweather returns METARs only for LSGG and LSZH
        responses.add(
            responses.GET,
            "https://aviationweather.gov/api/data/metar",
            json=[
                {"icaoId": "LSGG", "temp": 10},
                {"icaoId": "LSZH", "temp": 12},
            ],
            status=200,
        )

        with patch("weatherbrief.airports._load_airport_model", return_value=mock_model):
            result = discover_airports(["LS"], "/fake/db")

        assert result["LS"] == ["LSGG", "LSZH"]

    @responses.activate
    def test_discover_handles_204_no_content(self):
        """aviationweather returns 204 for empty results."""
        mock_model = MagicMock()
        mock_model.airports.__iter__ = MagicMock(
            return_value=iter([SimpleNamespace(ident="XXXX")])
        )

        responses.add(
            responses.GET,
            "https://aviationweather.gov/api/data/metar",
            body="",
            status=204,
        )

        with patch("weatherbrief.airports._load_airport_model", return_value=mock_model):
            result = discover_airports(["XX"], "/fake/db")

        assert result["XX"] == []


# ---------------------------------------------------------------------------
# Snapshot → HourlyForecast adapter
# ---------------------------------------------------------------------------


class TestSnapshotToHourly:

    def test_basic_conversion(self):
        snap = SimpleNamespace(
            forecast_hour=NOW,
            temperature_2m_c=12.5,
            dewpoint_2m_c=7.2,
            visibility_m=9999.0,
            wind_speed_10m_kt=15.0,
            wind_direction_10m_deg=270.0,
            wind_gusts_10m_kt=22.0,
            precipitation_mm=0.0,
            snowfall_cm=0.0,
            cape_jkg=50.0,
            cloud_cover_pct=75.0,
            cloud_cover_low_pct=40.0,
            nwp_ceiling_ft=3500.0,
        )

        hourly = _snapshot_to_hourly(snap)

        assert hourly.time == NOW
        assert hourly.temperature_2m_c == 12.5
        assert hourly.visibility_m == 9999.0
        assert hourly.wind_speed_10m_kt == 15.0
        assert hourly.nwp_cloud_diagnostics is not None
        assert hourly.nwp_cloud_diagnostics.ceiling_ft == 3500.0

    def test_no_ceiling(self):
        snap = SimpleNamespace(
            forecast_hour=NOW,
            temperature_2m_c=12.5,
            dewpoint_2m_c=7.2,
            visibility_m=9999.0,
            wind_speed_10m_kt=15.0,
            wind_direction_10m_deg=270.0,
            wind_gusts_10m_kt=None,
            precipitation_mm=0.0,
            snowfall_cm=0.0,
            cape_jkg=None,
            cloud_cover_pct=None,
            cloud_cover_low_pct=None,
            nwp_ceiling_ft=None,
        )

        hourly = _snapshot_to_hourly(snap)
        assert hourly.nwp_cloud_diagnostics is None

    def test_pressure_levels_empty(self):
        snap = SimpleNamespace(
            forecast_hour=NOW,
            temperature_2m_c=10.0, dewpoint_2m_c=5.0,
            visibility_m=5000.0, wind_speed_10m_kt=8.0,
            wind_direction_10m_deg=180.0, wind_gusts_10m_kt=None,
            precipitation_mm=0.0, snowfall_cm=0.0,
            cape_jkg=None,
            cloud_cover_pct=None, cloud_cover_low_pct=None,
            nwp_ceiling_ft=1200.0,
        )
        hourly = _snapshot_to_hourly(snap)
        assert hourly.pressure_levels == []


# ---------------------------------------------------------------------------
# Phase B: Store snapshots
# ---------------------------------------------------------------------------


class TestStoreSnapshots:

    def test_stores_new_snapshots(self, db_session):
        snaps = [
            {
                "icao": "LFPG", "model": "gfs",
                "model_init_time": GFS_INIT,
                "forecast_hour": _utc(2026, 4, 2, 12, 0),
                "temperature_2m_c": 12.0, "dewpoint_2m_c": 7.0,
                "visibility_m": 9999.0, "wind_speed_10m_kt": 10.0,
                "wind_direction_10m_deg": 270.0, "wind_gusts_10m_kt": None,
                "precipitation_mm": 0.0, "snowfall_cm": 0.0,
                "cape_jkg": None,
                "cloud_cover_pct": 50.0, "cloud_cover_low_pct": 20.0,
                "nwp_ceiling_ft": 3000.0, "cloud_base_ft": 2800.0,
                "lcl_ft": 2000.0,
            },
        ]

        stored = _store_snapshots(snaps, db_session)
        assert stored == 1

        rows = db_session.execute(select(AirportForecastSnapshotRow)).scalars().all()
        assert len(rows) == 1
        assert rows[0].icao == "LFPG"
        assert rows[0].nwp_ceiling_ft == 3000.0
        assert rows[0].lcl_ft == 2000.0

    def test_duplicate_is_skipped(self, db_session):
        snap = {
            "icao": "EDDF", "model": "icon",
            "model_init_time": ICON_INIT,
            "forecast_hour": _utc(2026, 4, 2, 9, 0),
            "temperature_2m_c": 8.0, "dewpoint_2m_c": 4.0,
            "visibility_m": 15000.0,
        }

        assert _store_snapshots([snap], db_session) == 1
        assert _store_snapshots([snap], db_session) == 0  # duplicate

        rows = db_session.execute(select(AirportForecastSnapshotRow)).scalars().all()
        assert len(rows) == 1

    def test_empty_list(self, db_session):
        assert _store_snapshots([], db_session) == 0


# ---------------------------------------------------------------------------
# Phase D: Scoring
# ---------------------------------------------------------------------------


def _insert_snapshot(db_session, **overrides):
    defaults = dict(
        icao="LFPG", model="gfs",
        model_init_time=GFS_INIT,
        forecast_hour=NOW,
        fetched_at=NOW,
        temperature_2m_c=12.0, dewpoint_2m_c=7.0,
        visibility_m=9999.0, wind_speed_10m_kt=10.0,
        wind_direction_10m_deg=270.0, wind_gusts_10m_kt=None,
        precipitation_mm=0.0, snowfall_cm=0.0,
        cape_jkg=None,
        cloud_cover_pct=50.0, cloud_cover_low_pct=20.0,
        nwp_ceiling_ft=3000.0, cloud_base_ft=2800.0,
        lcl_ft=2000.0,
    )
    defaults.update(overrides)
    row = AirportForecastSnapshotRow(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _insert_observation(db_session, **overrides):
    defaults = dict(
        icao="LFPG",
        observation_time=NOW,
        collected_at=NOW,
        flight_category="VFR",
        ceiling_ft=3500,
        visibility_m=9999,
        wind_dir=260,
        wind_speed_kt=8,
        wind_gust_kt=None,
        temperature_c=11,
        dewpoint_c=6,
        weather=json.dumps([]),
    )
    defaults.update(overrides)
    row = VerificationObservationRow(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


class TestScoreCycle:

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_scores_snapshot_against_observation(self, mock_rwy, db_session):
        _insert_snapshot(db_session)
        _insert_observation(db_session)

        scores = _score_cycle(NOW, [], "/fake/db", db_session)

        assert scores >= 1
        rows = db_session.execute(select(VerificationScoreRow)).scalars().all()
        assert len(rows) == 1
        assert rows[0].source == "standalone"
        assert rows[0].model == "gfs"
        assert rows[0].days_out == 0  # same day as init

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_scores_d1_snapshot(self, mock_rwy, db_session):
        """Snapshot from yesterday's init scores as D-1."""
        yesterday_init = GFS_INIT - timedelta(days=1)
        _insert_snapshot(db_session, model_init_time=yesterday_init)
        _insert_observation(db_session)

        _score_cycle(NOW, [], "/fake/db", db_session)

        row = db_session.execute(select(VerificationScoreRow)).scalar_one()
        assert row.days_out == 1

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_no_double_scoring(self, mock_rwy, db_session):
        _insert_snapshot(db_session)
        _insert_observation(db_session)

        _score_cycle(NOW, [], "/fake/db", db_session)
        _score_cycle(NOW, [], "/fake/db", db_session)

        rows = db_session.execute(select(VerificationScoreRow)).scalars().all()
        assert len(rows) == 1

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_no_scores_without_observations(self, mock_rwy, db_session):
        _insert_snapshot(db_session)
        # No observations

        scores = _score_cycle(NOW, [], "/fake/db", db_session)
        assert scores == 0

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_cloud_base_and_lcl_deltas(self, mock_rwy, db_session):
        _insert_snapshot(db_session, cloud_base_ft=2800.0, lcl_ft=2000.0)
        _insert_observation(db_session, ceiling_ft=3000)

        _score_cycle(NOW, [], "/fake/db", db_session)

        row = db_session.execute(select(VerificationScoreRow)).scalar_one()
        assert row.cloud_base_delta_ft == pytest.approx(-200.0)  # 2800 - 3000
        assert row.lcl_delta_ft == pytest.approx(-1000.0)  # 2000 - 3000

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_multiple_models_scored_independently(self, mock_rwy, db_session):
        _insert_snapshot(db_session, model="gfs")
        _insert_snapshot(db_session, model="icon", model_init_time=ICON_INIT)
        _insert_observation(db_session)

        _score_cycle(NOW, [], "/fake/db", db_session)

        rows = db_session.execute(select(VerificationScoreRow)).scalars().all()
        models = {r.model for r in rows}
        assert models == {"gfs", "icon"}

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_taf_scoring(self, mock_rwy, db_session):
        _insert_snapshot(db_session)
        _insert_observation(
            db_session,
            taf_issue_time=NOW - timedelta(hours=1),
            taf_flight_category="VFR",
            taf_ceiling_ft=4000,
            taf_visibility_m=9999,
            taf_wind_dir=260,
            taf_wind_speed_kt=10,
            taf_wind_gust_kt=None,
        )

        _score_cycle(NOW, [], "/fake/db", db_session)

        taf_rows = db_session.execute(select(TafVerificationScoreRow)).scalars().all()
        assert len(taf_rows) == 1
        assert taf_rows[0].source == "standalone"


# ---------------------------------------------------------------------------
# Phase E: Prune
# ---------------------------------------------------------------------------


class TestPruneOldSnapshots:

    @patch("weatherbrief.tasks.standalone_verification.datetime")
    def test_prunes_old(self, mock_dt, db_session):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        old_time = NOW - timedelta(days=15)
        _insert_snapshot(db_session, fetched_at=old_time, forecast_hour=old_time)
        _insert_snapshot(
            db_session, fetched_at=NOW, icao="EDDF",
            forecast_hour=NOW,
        )

        pruned = _prune_old_snapshots(db_session)
        assert pruned == 1

        remaining = db_session.execute(select(AirportForecastSnapshotRow)).scalars().all()
        assert len(remaining) == 1
        assert remaining[0].icao == "EDDF"

    @patch("weatherbrief.tasks.standalone_verification.datetime")
    def test_nothing_to_prune(self, mock_dt, db_session):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        _insert_snapshot(db_session)
        assert _prune_old_snapshots(db_session) == 0


# ---------------------------------------------------------------------------
# Scheduler helper
# ---------------------------------------------------------------------------


class TestSecondsUntilNextSampleHour:

    def test_before_first_sample(self):
        from weatherbrief.scheduler import _seconds_until_next_sample_hour

        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(2026, 4, 2, 4, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_next_sample_hour([6, 9, 12, 15, 18])

        # 04:30 → 06:00 = 1.5h = 5400s
        assert secs == pytest.approx(5400.0)

    def test_between_samples(self):
        from weatherbrief.scheduler import _seconds_until_next_sample_hour

        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(2026, 4, 2, 10, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_next_sample_hour([6, 9, 12, 15, 18])

        # 10:00 → 12:00 = 2h = 7200s
        assert secs == pytest.approx(7200.0)

    def test_after_last_sample_wraps_to_tomorrow(self):
        from weatherbrief.scheduler import _seconds_until_next_sample_hour

        with patch("weatherbrief.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = _utc(2026, 4, 2, 19, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            secs = _seconds_until_next_sample_hour([6, 9, 12, 15, 18])

        # 19:00 → tomorrow 06:00 = 11h = 39600s
        assert secs == pytest.approx(39600.0)


# ---------------------------------------------------------------------------
# Verification stats source filtering
# ---------------------------------------------------------------------------


class TestVerificationStatsSourceFilter:

    def _insert_scores(self, db_session):
        """Insert observations and scores for both sources."""
        obs = _insert_observation(db_session)

        for source in ("flight", "standalone"):
            db_session.add(VerificationScoreRow(
                observation_id=obs.id,
                icao="LFPG",
                observation_time=NOW,
                model="gfs",
                model_init_time=GFS_INIT,
                lead_hours=6,
                days_out=0,
                source=source,
                obs_flight_category="VFR",
                model_flight_category="VFR",
                category_match=True,
                advisory_match=True,
                ceiling_delta_ft=100.0,
                visibility_delta_m=500.0,
                wind_speed_delta_kt=2.0,
                temperature_delta_c=1.0,
            ))
        db_session.flush()

    def test_category_accuracy_filters_by_source(self, db_session):
        from weatherbrief.tasks.verification_daily_rollup import rollup_day
        from weatherbrief.tasks.verification_stats import get_category_accuracy

        self._insert_scores(db_session)
        # The stats query reads from verification_daily_stats — roll up
        # the test date so the SUMs are present (post-#154).
        rollup_day(db_session, NOW.date())
        since = NOW - timedelta(hours=1)
        until = NOW + timedelta(hours=1)

        flight_rows = get_category_accuracy(db_session, since, until, source="flight")
        standalone_rows = get_category_accuracy(db_session, since, until, source="standalone")

        # Each source should have exactly 1 row (gfs D-0)
        assert len(flight_rows) == 1
        assert len(standalone_rows) == 1
        assert flight_rows[0].sample_count == 1
        assert standalone_rows[0].sample_count == 1

    def test_activity_summary_cycles_filtered(self, db_session):
        from weatherbrief.tasks.verification_stats import get_activity_summary

        # Insert cycle rows for both sources
        db_session.add(VerificationCycleRow(
            started_at=NOW, duration_ms=5000, source="flight",
        ))
        db_session.add(VerificationCycleRow(
            started_at=NOW, duration_ms=30000, source="standalone",
        ))
        db_session.flush()

        since = NOW - timedelta(hours=1)
        until = NOW + timedelta(hours=1)

        flight_summary = get_activity_summary(db_session, since, until, source="flight")
        standalone_summary = get_activity_summary(db_session, since, until, source="standalone")

        assert flight_summary.cycles_run == 1
        assert flight_summary.avg_cycle_duration_ms == pytest.approx(5000.0)
        assert standalone_summary.cycles_run == 1
        assert standalone_summary.avg_cycle_duration_ms == pytest.approx(30000.0)

    def test_activity_summary_obs_filtered_by_source(self, db_session):
        """Observation and airport counts only include obs with scores for that source."""
        from weatherbrief.tasks.verification_stats import get_activity_summary

        self._insert_scores(db_session)

        since = NOW - timedelta(hours=1)
        until = NOW + timedelta(hours=1)

        flight_summary = get_activity_summary(db_session, since, until, source="flight")
        standalone_summary = get_activity_summary(db_session, since, until, source="standalone")

        # Both sources share the same observation, so each sees 1
        assert flight_summary.observations_collected == 1
        assert standalone_summary.observations_collected == 1
        assert flight_summary.airports_observed == 1
        assert standalone_summary.airports_observed == 1


# ---------------------------------------------------------------------------
# Full cycle (integration-level, mocked externals)
# ---------------------------------------------------------------------------


class TestRunStandaloneCycle:

    @patch("weatherbrief.tasks.standalone_verification._prune_old_snapshots", return_value=0)
    @patch("weatherbrief.tasks.standalone_verification._score_cycle", return_value=5)
    @patch("weatherbrief.tasks.standalone_verification._store_snapshots", return_value=100)
    @patch("weatherbrief.tasks.standalone_verification._enrich_with_grib")
    @patch("weatherbrief.tasks.standalone_verification._fetch_forecasts_for_model", return_value=([{"dummy": True}], 1))
    @patch("weatherbrief.fetch.model_status.fetch_model_metadata")
    @patch("flyfun_common.db.SessionLocal")
    def test_full_cycle_orchestration(
        self, mock_session_local, mock_meta, mock_fetch, mock_enrich,
        mock_store_snap, mock_score, mock_prune,
    ):
        from weatherbrief.tasks.standalone_verification import run_standalone_cycle

        # Mock DB session
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        mock_db.execute.return_value.scalar_one_or_none.return_value = None  # no existing snapshots

        # Mock model metadata
        mock_meta.return_value = {
            "gfs": SimpleNamespace(last_init_time=int(GFS_INIT.timestamp())),
        }

        airports = [WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55)]
        result = run_standalone_cycle(airports, "/fake/db")

        assert result["models_fetched"] == 1
        assert result["snapshots_stored"] == 100
        # METAR fetch is no longer the cycle's job — owned by metar_ingest loop
        assert result["observations_stored"] == 0
        assert result["scores_created"] == 5
        assert result["duration_ms"] >= 0
        assert mock_db.commit.call_count >= 1

    @patch("weatherbrief.fetch.model_status.fetch_model_metadata")
    @patch("flyfun_common.db.SessionLocal")
    def test_skips_already_fetched_model(self, mock_session_local, mock_meta):
        from weatherbrief.tasks.standalone_verification import run_standalone_cycle

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        # Simulate existing snapshot for this init time
        mock_db.execute.return_value.scalar_one_or_none.return_value = 1

        mock_meta.return_value = {
            "gfs": SimpleNamespace(last_init_time=int(GFS_INIT.timestamp())),
        }

        with patch("weatherbrief.tasks.standalone_verification._score_cycle", return_value=0), \
             patch("weatherbrief.tasks.standalone_verification._prune_old_snapshots", return_value=0), \
             patch("weatherbrief.tasks.standalone_verification._store_snapshots", return_value=0):
            result = run_standalone_cycle(
                [WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55)],
                "/fake/db",
            )

        assert result["models_fetched"] == 0  # skipped

    @patch("weatherbrief.tasks.standalone_verification._prune_old_snapshots", return_value=0)
    @patch("weatherbrief.tasks.standalone_verification._score_cycle", return_value=7)
    @patch("flyfun_common.db.SessionLocal")
    def test_light_cycle_skips_fetch(
        self, mock_session_local, mock_score, mock_prune,
    ):
        """Light cycle (fetch_forecasts=False) skips Phase A+B and reads obs from DB."""
        from weatherbrief.tasks.standalone_verification import run_standalone_cycle

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        airports = [WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55)]
        result = run_standalone_cycle(airports, "/fake/db", fetch_forecasts=False)

        assert result["cycle_type"] == "light"
        assert result["models_fetched"] == 0
        assert result["snapshots_stored"] == 0
        # Light cycle no longer fetches METARs — that's the metar_ingest loop's job.
        assert result["observations_stored"] == 0
        assert result["scores_created"] == 7
        assert result["duration_ms"] >= 0

    @patch("weatherbrief.tasks.standalone_verification._prune_old_snapshots", return_value=0)
    @patch("weatherbrief.tasks.standalone_verification._score_cycle", return_value=5)
    @patch("weatherbrief.tasks.standalone_verification._store_snapshots", return_value=100)
    @patch("weatherbrief.tasks.standalone_verification._enrich_with_grib")
    @patch("weatherbrief.tasks.standalone_verification._fetch_forecasts_for_model", return_value=([{"dummy": True}], 1))
    @patch("weatherbrief.fetch.model_status.fetch_model_metadata")
    @patch("flyfun_common.db.SessionLocal")
    def test_full_cycle_returns_cycle_type(
        self, mock_session_local, mock_meta, mock_fetch, mock_enrich,
        mock_store_snap, mock_score, mock_prune,
    ):
        """Full cycle sets cycle_type='full' in result."""
        from weatherbrief.tasks.standalone_verification import run_standalone_cycle

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db
        mock_db.execute.return_value.scalar_one_or_none.return_value = None

        mock_meta.return_value = {
            "gfs": SimpleNamespace(last_init_time=int(GFS_INIT.timestamp())),
        }

        airports = [WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55)]
        result = run_standalone_cycle(airports, "/fake/db")

        assert result["cycle_type"] == "full"


class TestRunMetarIngestCycle:

    @patch("weatherbrief.tasks.standalone_verification._fetch_and_store_observations", return_value=7)
    @patch("flyfun_common.db.SessionLocal")
    def test_records_cycle_row_with_metar_ingest_source(
        self, mock_session_local, mock_fetch_obs,
    ):
        """run_metar_ingest_cycle commits a cycle row tagged source='metar_ingest'."""
        from weatherbrief.tasks.standalone_verification import run_metar_ingest_cycle

        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        airports = [
            WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55),
            WatchlistAirport(icao="EGLL", lat=51.47, lon=-0.46),
        ]
        result = run_metar_ingest_cycle(airports, "/fake/db")

        assert result["cycle_type"] == "metar_ingest"
        assert result["airports"] == 2
        assert result["observations_stored"] == 7
        assert result["duration_ms"] >= 0
        # _fetch_and_store_observations was called with the watchlist
        mock_fetch_obs.assert_called_once()
        # A cycle row was added (along with whatever the fetch helper added)
        assert mock_db.add.called
        added_rows = [c.args[0] for c in mock_db.add.call_args_list]
        cycle_rows = [r for r in added_rows if getattr(r, "source", None) == "metar_ingest"]
        assert len(cycle_rows) == 1


class TestEnrichWithSounding:

    def test_populates_sounding_fields(self):
        """_enrich_with_sounding extracts fields from pressure-level analysis."""
        from weatherbrief.tasks.standalone_verification import _enrich_with_sounding
        from weatherbrief.models.analysis import (
            CloudCoverage,
            ConvectiveRisk,
        )

        # Build a mock hourly with pressure levels that analyze_sounding can use
        snap = {"icao": "LFPG", "model": "gfs"}

        # Mock the sounding analysis result
        mock_sounding = SimpleNamespace(
            indices=SimpleNamespace(
                sounding_ceiling_ft=3500.0,
                freezing_level_ft=8000.0,
                cape_surface_jkg=150.0,
                cin_surface_jkg=-20.0,
                lifted_index=-2.5,
            ),
            dd_cloud_layers=[
                SimpleNamespace(base_ft=3500.0, coverage=CloudCoverage.BKN),
                SimpleNamespace(base_ft=8000.0, coverage=CloudCoverage.SCT),
            ],
            convective=SimpleNamespace(risk_level=ConvectiveRisk.MODERATE),
        )

        mock_hourly = SimpleNamespace(pressure_levels=[1, 2, 3])  # non-empty

        with patch("weatherbrief.analysis.sounding.analyze_sounding_lite", return_value=mock_sounding):
            _enrich_with_sounding(snap, mock_hourly, "gfs")

        assert snap["sounding_ceiling_ft"] == 3500.0
        assert snap["sounding_cloud_base_ft"] == 3500.0  # lowest BKN
        assert snap["freezing_level_ft"] == 8000.0
        assert snap["sounding_cape_jkg"] == 150.0
        assert snap["sounding_cin_jkg"] == -20.0
        assert snap["sounding_lifted_index"] == -2.5
        assert snap["sounding_convective_risk"] == ConvectiveRisk.MODERATE.value

    def test_no_pressure_levels_is_noop(self):
        """No pressure levels → snap unchanged."""
        from weatherbrief.tasks.standalone_verification import _enrich_with_sounding

        snap = {"icao": "LFPG", "model": "gfs"}
        mock_hourly = SimpleNamespace(pressure_levels=None)
        _enrich_with_sounding(snap, mock_hourly, "gfs")
        assert "sounding_ceiling_ft" not in snap

    def test_analysis_failure_preserves_surface_data(self):
        """Sounding analysis exception doesn't lose surface fields."""
        from weatherbrief.tasks.standalone_verification import _enrich_with_sounding

        snap = {"icao": "LFPG", "model": "gfs", "temperature_2m_c": 15.0}
        mock_hourly = SimpleNamespace(pressure_levels=[1, 2])

        with patch("weatherbrief.analysis.sounding.analyze_sounding_lite", side_effect=ValueError("bad data")):
            _enrich_with_sounding(snap, mock_hourly, "gfs")

        assert snap["temperature_2m_c"] == 15.0  # preserved
        assert "sounding_ceiling_ft" not in snap  # not added


class TestRunPostCycleTasks:
    """Cycle-aware post-cycle work (#448): rebuild only what the cycle changed."""

    def _run(self, cycle_type):
        from weatherbrief.tasks.standalone_verification import run_post_cycle_tasks

        with patch("flyfun_common.db.SessionLocal") as sl, \
             patch(
                 "weatherbrief.tasks.verification_daily_rollup.rollup_today_and_pending",
                 return_value=3,
             ) as rollup, \
             patch(
                 "weatherbrief.tasks.cache_builder.rebuild_all",
                 return_value={"duration_ms": 1},
             ) as rebuild:
            sl.return_value = MagicMock()
            run_post_cycle_tasks("/fake/db", cycle_type)
        return rollup, rebuild

    def test_forecast_cycle_skips_rollup_and_score_stats(self):
        rollup, rebuild = self._run("forecast")
        assert not rollup.called, "forecast cycles create no scores — no rollup"
        kwargs = rebuild.call_args.kwargs
        assert kwargs["include_score_stats"] is False
        assert kwargs["include_forecast_map"] is True

    def test_light_cycle_skips_forecast_map(self):
        rollup, rebuild = self._run("light")
        assert rollup.called
        kwargs = rebuild.call_args.kwargs
        assert kwargs["include_score_stats"] is True
        assert kwargs["include_forecast_map"] is False

    def test_full_cycle_rebuilds_everything(self):
        rollup, rebuild = self._run("full")
        assert rollup.called
        kwargs = rebuild.call_args.kwargs
        assert kwargs["include_score_stats"] is True
        assert kwargs["include_forecast_map"] is True
