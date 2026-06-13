"""Tests for weather overview map queries and API endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient


@pytest.fixture
def db_engine():
    """Per-test engine — unit tests call db_session.commit()."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()

from weatherbrief.api.app import create_app  # must import first to avoid circular import
from flyfun_common.db import DEV_USER_ID, get_db, current_user_id
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    VerificationObservationRow,
    VerificationScoreRow,
)

NOW = datetime(2026, 4, 5, 12, 0, 0, tzinfo=timezone.utc)
GFS_INIT = datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
ICON_INIT = datetime(2026, 4, 5, 0, 0, 0, tzinfo=timezone.utc)
ECMWF_INIT = datetime(2026, 4, 4, 12, 0, 0, tzinfo=timezone.utc)

FAKE_COORDS = {
    "LFPG": (49.01, 2.55),
    "EDDF": (50.03, 8.57),
    "EHAM": (52.31, 4.76),
    "EGLL": (51.47, -0.46),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_snapshot(db_session, *, icao="LFPG", model="gfs",
                     model_init_time=None, forecast_hour=None, **overrides):
    defaults = dict(
        icao=icao,
        model=model,
        model_init_time=model_init_time or GFS_INIT,
        forecast_hour=forecast_hour or NOW,
        fetched_at=NOW,
        temperature_2m_c=12.0,
        dewpoint_2m_c=7.0,
        visibility_m=9999.0,
        wind_speed_10m_kt=10.0,
        wind_direction_10m_deg=270.0,
        wind_gusts_10m_kt=15.0,
        precipitation_mm=0.0,
        snowfall_cm=0.0,
        cape_jkg=50.0,
        cloud_cover_pct=30.0,
        cloud_cover_low_pct=10.0,
        nwp_ceiling_ft=None,
        cloud_base_ft=None,
        lcl_ft=2000.0,
        sounding_ceiling_ft=4000.0,
        sounding_cloud_base_ft=3500.0,
        freezing_level_ft=8000.0,
        sounding_cape_jkg=60.0,
        sounding_cin_jkg=-10.0,
        sounding_lifted_index=-1.0,
        sounding_convective_risk="none",
    )
    defaults.update(overrides)
    row = AirportForecastSnapshotRow(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


def _insert_verif_score(db_session, *, icao="LFPG", model="gfs",
                        days_out=0, observation_time=None, **overrides):
    # Need an observation first
    obs_time = observation_time or NOW
    obs = db_session.query(VerificationObservationRow).filter_by(
        icao=icao, observation_time=obs_time,
    ).first()
    if not obs:
        obs = VerificationObservationRow(
            icao=icao, observation_time=obs_time, collected_at=NOW,
            flight_category="VFR", ceiling_ft=3500, visibility_m=9999,
            wind_dir=270, wind_speed_kt=10, temperature_c=12,
            weather=json.dumps([]),
        )
        db_session.add(obs)
        db_session.flush()

    defaults = dict(
        observation_id=obs.id,
        icao=icao,
        observation_time=obs_time,
        model=model,
        model_init_time=GFS_INIT,
        lead_hours=12,
        days_out=days_out,
        source="standalone",
        obs_flight_category="VFR",
        model_flight_category="VFR",
        category_match=True,
        ceiling_delta_ft=200.0,
        visibility_delta_m=100.0,
        wind_speed_delta_kt=2.0,
        wind_dir_delta_deg=10.0,
        temperature_delta_c=1.0,
    )
    defaults.update(overrides)
    row = VerificationScoreRow(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


# ---------------------------------------------------------------------------
# Unit tests: map_queries
# ---------------------------------------------------------------------------


@patch("weatherbrief.tasks.map_queries._get_runways", return_value={})
class TestGetForecastMapData:
    """Tests for get_forecast_map_data()."""

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_returns_airports_with_forecasts(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        # Insert snapshots for 2 airports, 2 models
        for icao in ("LFPG", "EDDF"):
            for model, init in [("gfs", GFS_INIT), ("icon", ICON_INIT)]:
                _insert_snapshot(db_session, icao=icao, model=model,
                                 model_init_time=init)
        db_session.commit()

        result = get_forecast_map_data(db_session, NOW, "/fake/db")

        assert result["forecast_time"] == NOW.isoformat()
        assert len(result["airports"]) == 2
        assert "gfs" in result["model_init_times"]

        # Check airport structure
        apt = next(a for a in result["airports"] if a["icao"] == "LFPG")
        assert apt["lat"] == pytest.approx(49.01)
        assert "gfs" in apt["models"]
        assert "icon" in apt["models"]
        assert "consensus" in apt
        assert apt["consensus"]["flight_category"] in ("VFR", "MVFR", "IFR", "LIFR")

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_empty_when_no_data(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        result = get_forecast_map_data(db_session, NOW, "/fake/db")
        assert result["airports"] == []
        assert result["model_init_times"] == {}

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_picks_latest_init_time(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        old_init = GFS_INIT - timedelta(hours=6)
        _insert_snapshot(db_session, icao="LFPG", model="gfs",
                         model_init_time=old_init, temperature_2m_c=5.0)
        _insert_snapshot(db_session, icao="LFPG", model="gfs",
                         model_init_time=GFS_INIT, temperature_2m_c=15.0)
        db_session.commit()

        result = get_forecast_map_data(db_session, NOW, "/fake/db")
        # SQLite loses tzinfo, so compare without timezone suffix
        assert result["model_init_times"]["gfs"].startswith("2026-04-05T00:00:00")
        # Should use latest init data
        apt = result["airports"][0]
        assert apt["models"]["gfs"]["temperature_c"] == pytest.approx(15.0)

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_flight_category_derived(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        # Insert with low ceiling → should be IFR
        _insert_snapshot(db_session, icao="LFPG", model="gfs",
                         sounding_ceiling_ft=800.0, visibility_m=9999.0)
        db_session.commit()

        result = get_forecast_map_data(db_session, NOW, "/fake/db")
        apt = result["airports"][0]
        assert apt["models"]["gfs"]["flight_category"] == "IFR"

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_consensus_agreement(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        # All models agree on VFR
        for model, init in [("gfs", GFS_INIT), ("icon", ICON_INIT), ("ecmwf", ECMWF_INIT)]:
            _insert_snapshot(db_session, icao="LFPG", model=model,
                             model_init_time=init,
                             sounding_ceiling_ft=5000.0, wind_speed_10m_kt=10.0)
        db_session.commit()

        result = get_forecast_map_data(db_session, NOW, "/fake/db")
        apt = result["airports"][0]
        assert apt["consensus"]["flight_category"] == "VFR"
        assert apt["consensus"]["agreement"]["flight_category"] == "consistent"
        assert apt["consensus"]["agreement"]["wind_speed_kt"] == "consistent"

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_consensus_disagreement(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        # GFS says IFR, others say VFR — wind speed diverges too
        _insert_snapshot(db_session, icao="LFPG", model="gfs",
                         model_init_time=GFS_INIT,
                         sounding_ceiling_ft=800.0, wind_speed_10m_kt=5.0)
        _insert_snapshot(db_session, icao="LFPG", model="icon",
                         model_init_time=ICON_INIT,
                         sounding_ceiling_ft=5000.0, wind_speed_10m_kt=25.0)
        _insert_snapshot(db_session, icao="LFPG", model="ecmwf",
                         model_init_time=ECMWF_INIT,
                         sounding_ceiling_ft=5000.0, wind_speed_10m_kt=25.0)
        db_session.commit()

        result = get_forecast_map_data(db_session, NOW, "/fake/db")
        apt = result["airports"][0]
        # Worst category should be IFR (from GFS)
        assert apt["consensus"]["flight_category"] == "IFR"
        # Wind spread is 20kt → mixed or divergent
        assert apt["consensus"]["agreement"]["wind_speed_kt"] in ("mixed", "divergent")
        # Flight category: GFS=IFR, others=VFR → mixed
        assert apt["consensus"]["agreement"]["flight_category"] in ("mixed", "divergent")

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_skips_airports_without_coords(self, _mock_coords, _mock_rwy, db_session):
        from weatherbrief.tasks.map_queries import get_forecast_map_data

        # ZZZZ not in coords
        _insert_snapshot(db_session, icao="ZZZZ", model="gfs")
        _insert_snapshot(db_session, icao="LFPG", model="gfs")
        db_session.commit()

        result = get_forecast_map_data(db_session, NOW, "/fake/db")
        icaos = [a["icao"] for a in result["airports"]]
        assert "ZZZZ" not in icaos
        assert "LFPG" in icaos


# Note: TestGetVerificationMapData was removed in #154 along with
# get_verification_map_data. The optimistic-bias leaderboard that
# replaces this view is exercised in
# ``tests/test_verification_stats_rollup_gate.py``.


# ---------------------------------------------------------------------------
# Unit tests: internal helpers
# ---------------------------------------------------------------------------


class TestFlightCategoryDerivation:
    """Test _flight_category and _best_ceiling helpers."""

    def test_vfr_high_ceiling(self):
        from weatherbrief.tasks.map_queries import _flight_category
        snap = SimpleNamespace(
            sounding_ceiling_ft=5000.0, nwp_ceiling_ft=None,
            cloud_base_ft=None, lcl_ft=None, visibility_m=9999.0,
        )
        assert _flight_category(snap) == "VFR"

    def test_ifr_low_ceiling(self):
        from weatherbrief.tasks.map_queries import _flight_category
        snap = SimpleNamespace(
            sounding_ceiling_ft=800.0, nwp_ceiling_ft=None,
            cloud_base_ft=None, lcl_ft=None, visibility_m=9999.0,
        )
        assert _flight_category(snap) == "IFR"

    def test_lifr_low_vis(self):
        from weatherbrief.tasks.map_queries import _flight_category
        snap = SimpleNamespace(
            sounding_ceiling_ft=5000.0, nwp_ceiling_ft=None,
            cloud_base_ft=None, lcl_ft=None, visibility_m=800.0,
        )
        assert _flight_category(snap) == "LIFR"

    def test_fallback_to_lcl(self):
        from weatherbrief.tasks.map_queries import _best_ceiling
        snap = SimpleNamespace(
            sounding_ceiling_ft=None, nwp_ceiling_ft=None,
            cloud_base_ft=None, lcl_ft=3000.0,
        )
        assert _best_ceiling(snap) == 3000.0


class TestConsensus:
    """Test _consensus helper."""

    def test_all_agree(self):
        from weatherbrief.tasks.map_queries import _consensus
        per_model = {
            "gfs": {"flight_category": "VFR", "wind_speed_kt": 10, "ceiling_ft": 5000, "cape_jkg": 50},
            "icon": {"flight_category": "VFR", "wind_speed_kt": 11, "ceiling_ft": 4800, "cape_jkg": 55},
            "ecmwf": {"flight_category": "VFR", "wind_speed_kt": 12, "ceiling_ft": 5200, "cape_jkg": 45},
        }
        result = _consensus(per_model)
        assert result["flight_category"] == "VFR"
        # Ceiling spread 400ft < 500ft good threshold; wind 2kt < 5kt; CAPE 10 < 200
        assert result["agreement"]["flight_category"] == "consistent"
        assert result["agreement"]["wind_speed_kt"] == "consistent"
        assert result["agreement"]["ceiling_ft"] == "consistent"
        assert result["agreement"]["cape_jkg"] == "consistent"
        assert result["wind_speed_kt"] == pytest.approx(11.0)

    def test_worst_mode_picks_most_restrictive(self):
        from weatherbrief.tasks.map_queries import _consensus
        per_model = {
            "gfs": {"flight_category": "IFR", "wind_speed_kt": 10, "ceiling_ft": 800, "cape_jkg": 50},
            "icon": {"flight_category": "VFR", "wind_speed_kt": 10, "ceiling_ft": 5000, "cape_jkg": 50},
            "ecmwf": {"flight_category": "VFR", "wind_speed_kt": 10, "ceiling_ft": 5000, "cape_jkg": 50},
        }
        result = _consensus(per_model, mode="worst")
        assert result["flight_category"] == "IFR"

    def test_majority_mode_picks_most_common(self):
        from weatherbrief.tasks.map_queries import _consensus
        per_model = {
            "gfs": {"flight_category": "IFR", "wind_speed_kt": 10, "ceiling_ft": 800, "cape_jkg": 50},
            "icon": {"flight_category": "VFR", "wind_speed_kt": 10, "ceiling_ft": 5000, "cape_jkg": 50},
            "ecmwf": {"flight_category": "VFR", "wind_speed_kt": 10, "ceiling_ft": 5000, "cape_jkg": 50},
        }
        result = _consensus(per_model, mode="majority")
        # 2 of 3 say VFR
        assert result["flight_category"] == "VFR"

    def test_majority_tiebreaker_picks_worst(self):
        from weatherbrief.tasks.map_queries import _consensus
        per_model = {
            "gfs": {"flight_category": "IFR", "wind_speed_kt": 10, "ceiling_ft": 800, "cape_jkg": 50},
            "icon": {"flight_category": "VFR", "wind_speed_kt": 10, "ceiling_ft": 5000, "cape_jkg": 50},
        }
        # 1 IFR, 1 VFR — tie, tiebreaker picks worst (IFR)
        result = _consensus(per_model, mode="majority")
        assert result["flight_category"] == "IFR"

    def test_empty_models(self):
        from weatherbrief.tasks.map_queries import _consensus
        result = _consensus({})
        assert result["flight_category"] == "VFR"
        assert result["agreement"] == {}


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


TEST_SECRET = "test-jwt-secret"
ADMIN_EMAIL = "admin@test.com"
ADMIN_ID = "admin-user-001"


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory for the test app."""
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
    session.add(UserRow(
        id=ADMIN_ID, provider="google", provider_sub="goog-admin",
        email=ADMIN_EMAIL, display_name="Admin", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=ADMIN_ID))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


def _make_client(app_db, tmp_path, monkeypatch, *, user_id=DEV_USER_ID):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN_EMAIL)
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("DISABLE_RETENTION", "1")
    monkeypatch.setenv("DISABLE_VERIFICATION", "1")
    monkeypatch.setenv("DISABLE_DIGEST", "1")
    monkeypatch.setenv("DISABLE_STANDALONE_VERIFICATION", "1")

    app = create_app()

    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: user_id
    app.state.db_path = "/fake/airports.db"

    return TestClient(app, raise_server_exceptions=False)


@patch("weatherbrief.tasks.map_queries._get_runways", return_value={})
class TestForecastMapEndpoint:
    """Tests for GET /api/maps/forecast."""

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_returns_forecast_data(self, _mock_coords, _mock_rwy, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)

        # Seed snapshot at today's 12Z (endpoint uses current date)
        today_12z = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        session = app_db()
        _insert_snapshot(session, icao="LFPG", model="gfs", forecast_hour=today_12z)
        session.commit()
        session.close()

        resp = client.get("/api/maps/forecast?day=0&hour=12")
        assert resp.status_code == 200
        data = resp.json()
        assert "airports" in data
        assert len(data["airports"]) >= 1
        assert data["airports"][0]["icao"] == "LFPG"

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_empty_for_future_hour(self, _mock_coords, _mock_rwy, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)

        resp = client.get("/api/maps/forecast?day=3&hour=18")
        assert resp.status_code == 200
        assert resp.json()["airports"] == []

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_snaps_invalid_hour(self, _mock_coords, _mock_rwy, app_db, tmp_path, monkeypatch):
        """Non-sample hour should snap to nearest sample hour."""
        client = _make_client(app_db, tmp_path, monkeypatch)
        resp = client.get("/api/maps/forecast?day=0&hour=10")
        assert resp.status_code == 200


# TestVerificationMapEndpoint was removed in #154 — the endpoint and
# its associated query function (get_verification_map_data) are gone.


class TestAvailableHoursEndpoint:
    """Tests for GET /api/maps/forecast/hours."""

    @patch("weatherbrief.tasks.map_queries._get_coords", return_value=FAKE_COORDS)
    def test_returns_available_hours(self, _mock_coords, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)

        # Seed snapshot at today's 12Z
        today_12z = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        session = app_db()
        _insert_snapshot(session, icao="LFPG", model="gfs", forecast_hour=today_12z)
        session.commit()
        session.close()

        resp = client.get("/api/maps/forecast/hours?day=0")
        assert resp.status_code == 200
        data = resp.json()
        assert 12 in data["hours"]

    def test_empty_hours_for_future_day(self, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)

        resp = client.get("/api/maps/forecast/hours?day=3")
        assert resp.status_code == 200
        assert resp.json()["hours"] == []


class TestAvailableDaysEndpoint:
    """Tests for GET /api/maps/forecast/days — drives the day-picker
    disable/“not forecast yet” behaviour."""

    def test_reports_per_day_availability(self, app_db, tmp_path, monkeypatch):
        client = _make_client(app_db, tmp_path, monkeypatch)

        # Seed D-0 (today) only; D-3 stays unforecast, as it would be before
        # the next twice-daily model run extends the horizon.
        today_12z = datetime.now(timezone.utc).replace(
            hour=12, minute=0, second=0, microsecond=0)
        session = app_db()
        _insert_snapshot(session, icao="LFPG", model="gfs", forecast_hour=today_12z)
        session.commit()
        session.close()

        resp = client.get("/api/maps/forecast/days")
        assert resp.status_code == 200
        days = {d["day"]: d for d in resp.json()["days"]}
        assert set(days) == {0, 1, 2, 3}
        assert days[0]["available"] is True
        # D-1/D-2 also had nothing seeded — pin them so a regression that
        # erroneously marks them available is caught.
        assert days[1]["available"] is False
        assert days[2]["available"] is False
        assert days[3]["available"] is False
        # Date label tracks today + day so the UI label stays consistent.
        assert days[0]["date"] == datetime.now(timezone.utc).date().isoformat()


def test_alt_required_flags():
    """FAA/EASA destination-alternate flags used by the map 'Alternate required?' mode."""
    from weatherbrief.tasks.map_queries import _alt_required

    # IFR ILS field (ESMQ-like): both regimes require an alternate
    assert _alt_required(960, 26000, "ILS", True) == {"faa": True, "easa": True}
    # Comfortable VFR: neither
    assert _alt_required(3000, 26000, "ILS", True) == {"faa": False, "easa": False}
    # 1500 ft at an ILS: FAA (flat 2000) requires, EASA (DH+1000 ≈ 1200-1300) does not
    assert _alt_required(1500, 26000, "ILS", True) == {"faa": True, "easa": False}
    # Missing visibility alone (e.g. ECMWF, GRIB-only) → judged on ceiling, NOT
    # forced "required" by the absent value
    assert _alt_required(32880, None, "ILS", True) == {"faa": False, "easa": False}
    # A genuinely low visibility still triggers
    assert _alt_required(3000, 800, "ILS", True)["faa"] is True
    # Neither ceiling nor visibility → no usable data → None ("no data" marker)
    assert _alt_required(None, None, "ILS", True) is None
