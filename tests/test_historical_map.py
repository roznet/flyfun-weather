"""Tests for the historical map (#629): source selection, TAF reading, archive."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from weatherbrief.api.app import create_app  # must import first to avoid circular import
from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.db.models import AirportForecastSnapshotRow, VerificationObservationRow
from weatherbrief.tasks import historical_map as hm

# T = 2026-04-05 10:30Z; "now" is a few days later so everything is final.
T = datetime(2026, 4, 5, 10, 30, tzinfo=timezone.utc)
NOW = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)
COORDS = {"LFPG": (49.01, 2.55), "EDDF": (50.03, 8.57)}

# Base VFR; TEMPO 10-14Z brings 4000 m and BKN008 (IFR).
TAF_TEMPO = (
    "TAF LFPG 050500Z 0506/0612 27010KT 9999 SCT030 "
    "TEMPO 0510/0514 25020G32KT 4000 SHRA BKN008"
)
TAF_EXPIRED = "TAF LFPG 040500Z 0406/0506 27010KT 9999 SCT030"


@pytest.fixture
def db_engine():
    """Per-test engine: the archive tests commit."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _airports():
    """No airports DB in tests: coords only, no runways/elevations/approaches."""
    with patch("weatherbrief.tasks.map_queries._get_coords", return_value=COORDS), \
         patch("weatherbrief.tasks.map_queries._get_runways", return_value={}), \
         patch("weatherbrief.tasks.map_queries._get_elevations", return_value={}), \
         patch("weatherbrief.tasks.map_queries._get_approach_classes", return_value={}):
        hm.payload_cache.clear()
        yield


@pytest.fixture
def archive_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return tmp_path / "archive" / "verification"


def _obs(db, *, icao="LFPG", time, raw="METAR", ceiling=None, vis=9999,
         wind=(270, 10, None), taf=None, report_type="METAR"):
    row = VerificationObservationRow(
        icao=icao, observation_time=time, collected_at=time,
        metar_raw=f"{raw} {icao} {time:%d%H%M}Z", report_type=report_type,
        ceiling_ft=ceiling, visibility_m=vis,
        wind_dir=wind[0], wind_speed_kt=wind[1], wind_gust_kt=wind[2],
        temperature_c=10, dewpoint_c=5, qnh=1015.0, weather=json.dumps(["-RA"]),
        taf_raw=taf,
        taf_issue_time=datetime(2026, 4, 5, 5, 0, tzinfo=timezone.utc) if taf else None,
    )
    db.add(row)
    db.flush()
    return row


def _snap(db, *, icao="LFPG", model="gfs", init, valid, fetched, ceiling=4000.0,
          vis=9999.0, wind=10.0):
    row = AirportForecastSnapshotRow(
        icao=icao, model=model, model_init_time=init, forecast_hour=valid,
        fetched_at=fetched, visibility_m=vis, wind_speed_10m_kt=wind,
        wind_direction_10m_deg=270.0, wind_gusts_10m_kt=None,
        sounding_ceiling_ft=ceiling, cape_jkg=10.0, cloud_cover_pct=20.0,
        sounding_convective_risk="none", temperature_2m_c=10.0,
    )
    db.add(row)
    db.flush()
    return row


def _get(db, at=T, lead=0, now=NOW):
    return hm.get_historical_map_data(db, at, lead, "/fake/airports.db", now=now)


def _airport(data, icao="LFPG"):
    return next(a for a in data["airports"] if a["icao"] == icao)


# ---------------------------------------------------------------------------
# Time grid
# ---------------------------------------------------------------------------


class TestTimeGrid:

    def test_snap_floors_to_half_hour(self):
        assert hm.snap_time(datetime(2026, 4, 5, 10, 44, 59, tzinfo=timezone.utc)) == T
        assert hm.snap_time(T) == T

    def test_valid_times_newest_first_within_three_hours(self):
        vts = hm.model_valid_times(datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc))
        assert [v.hour for v in vts] == [12, 9]

    def test_no_valid_time_at_night(self):
        assert hm.model_valid_times(datetime(2026, 4, 5, 22, 0, tzinfo=timezone.utc)) == []
        assert hm.model_valid_times(datetime(2026, 4, 5, 3, 0, tzinfo=timezone.utc)) == []

    def test_last_slot_holds_three_hours(self):
        vts = hm.model_valid_times(datetime(2026, 4, 5, 21, 0, tzinfo=timezone.utc))
        assert [v.hour for v in vts] == [18]


# ---------------------------------------------------------------------------
# Observed sources
# ---------------------------------------------------------------------------


class TestObserved:

    def test_latest_metar_at_or_before_t(self, db_session):
        _obs(db_session, time=T - timedelta(minutes=40), raw="OLD", ceiling=500)
        _obs(db_session, time=T - timedelta(minutes=10), raw="NEW", ceiling=2000)
        _obs(db_session, time=T + timedelta(minutes=20), raw="FUTURE")
        metar = _airport(_get(db_session))["observed"]["metar"]
        assert metar["raw"].startswith("NEW")
        assert metar["flight_category"] == "MVFR"
        assert metar["age_min"] == 10
        assert metar["weather"] == ["-RA"]

    def test_speci_counts_as_latest(self, db_session):
        _obs(db_session, time=T - timedelta(minutes=30))
        _obs(db_session, time=T - timedelta(minutes=5), raw="SPECI", report_type="SPECI")
        metar = _airport(_get(db_session))["observed"]["metar"]
        assert metar["report_type"] == "SPECI"

    def test_stale_metar_is_left_out(self, db_session):
        _obs(db_session, time=T - timedelta(minutes=120))
        data = _get(db_session)
        assert all("metar" not in a["observed"] for a in data["airports"])
        assert data["sources"]["metar"]["available"] is False

    def test_taf_is_read_at_t_with_tempo(self, db_session):
        _obs(db_session, time=T - timedelta(minutes=10), taf=TAF_TEMPO)
        taf = _airport(_get(db_session))["observed"]["taf"]
        assert taf["prevailing_category"] == "VFR"
        assert taf["temporary_category"] == "IFR"
        assert taf["flight_category"] == "IFR"
        # Numbers come from the group that sets the category.
        assert taf["ceiling_ft"] == 800
        assert taf["visibility_m"] == 4000
        # Strongest wind includes the TEMPO gust.
        assert taf["wind_gust_kt"] == 32
        assert taf["raw"] == TAF_TEMPO

    def test_taf_outside_tempo_is_prevailing(self, db_session):
        at = datetime(2026, 4, 5, 16, 0, tzinfo=timezone.utc)
        _obs(db_session, time=at - timedelta(minutes=20), taf=TAF_TEMPO)
        taf = _airport(_get(db_session, at=at))["observed"]["taf"]
        assert taf["flight_category"] == "VFR"
        assert taf["temporary_category"] is None

    def test_taf_from_an_older_row_when_latest_has_none(self, db_session):
        _obs(db_session, time=T - timedelta(hours=3), taf=TAF_TEMPO)
        _obs(db_session, time=T - timedelta(minutes=10))
        observed = _airport(_get(db_session))["observed"]
        assert observed["taf"]["flight_category"] == "IFR"
        assert "metar" in observed

    def test_expired_taf_is_left_out(self, db_session):
        _obs(db_session, time=T - timedelta(minutes=10), taf=TAF_EXPIRED)
        assert "taf" not in _airport(_get(db_session))["observed"]


# ---------------------------------------------------------------------------
# Model runs
# ---------------------------------------------------------------------------


VT09 = datetime(2026, 4, 5, 9, 0, tzinfo=timezone.utc)


class TestModelRuns:

    def test_run_fetched_after_t_is_never_used(self, db_session):
        before = _snap(db_session, init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
                       valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc),
                       ceiling=4000.0)
        _snap(db_session, init=datetime(2026, 4, 5, 6, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 11, tzinfo=timezone.utc),
              ceiling=400.0)
        data = _get(db_session)
        meta = data["sources"]["gfs"]
        assert meta["model_init_time"] == before.model_init_time.isoformat()
        assert meta["valid_time"] == VT09.isoformat()
        assert meta["lead_hours"] == 9
        gfs = _airport(data)["models"]["gfs"]
        assert gfs["flight_category"] == "VFR"
        assert gfs["valid_time"] == VT09.isoformat()

    def test_lead_picks_run_fetched_n_days_before(self, db_session):
        _snap(db_session, init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc))
        older = _snap(db_session, init=datetime(2026, 4, 4, 0, tzinfo=timezone.utc),
                      valid=VT09, fetched=datetime(2026, 4, 4, 7, tzinfo=timezone.utc))
        meta = _get(db_session, lead=1)["sources"]["gfs"]
        assert meta["model_init_time"] == older.model_init_time.isoformat()
        assert meta["lead_hours"] == 33

    def test_run_outside_the_lookback_is_not_used(self, db_session):
        # Fetched two days before T: too old to stand for "lead 0".
        _snap(db_session, init=datetime(2026, 4, 3, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 3, 7, tzinfo=timezone.utc))
        meta = _get(db_session)["sources"]["gfs"]
        assert meta == {"available": False, "reason": hm.REASON_NO_RUN}

    def test_lead_beyond_model_horizon(self, db_session):
        sources = _get(db_session, lead=5)["sources"]
        assert sources["icon"]["reason"] == hm.REASON_BEYOND_HORIZON
        assert sources["gfs"]["reason"] == hm.REASON_NO_RUN

    def test_night_has_no_model_time(self, db_session):
        sources = _get(db_session, at=datetime(2026, 4, 5, 23, 0, tzinfo=timezone.utc))["sources"]
        assert all(sources[m]["reason"] == hm.REASON_NO_VALID_TIME for m in hm.MODELS)

    def test_falls_back_to_earlier_slot_when_newest_is_missing(self, db_session):
        # T=12:00 → candidates 12Z then 09Z; this run only has 09Z (like ECMWF
        # past 144 h, which has no 09/15Z — here reversed for simplicity).
        _snap(db_session, model="ecmwf", init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc))
        at = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
        assert _get(db_session, at=at)["sources"]["ecmwf"]["valid_time"] == VT09.isoformat()


# ---------------------------------------------------------------------------
# Consensus stays about the models
# ---------------------------------------------------------------------------


class TestConsensus:

    def test_observed_never_feeds_consensus(self, db_session):
        _obs(db_session, time=T - timedelta(minutes=10), ceiling=200, vis=500)  # LIFR
        _snap(db_session, init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc))
        apt = _airport(_get(db_session))
        assert apt["observed"]["metar"]["flight_category"] == "LIFR"
        assert set(apt["models"]) == {"gfs"}
        assert apt["consensus"]["flight_category"] == "VFR"
        assert apt["consensus_majority"]["flight_category"] == "VFR"

    def test_observation_only_airport_has_no_consensus(self, db_session):
        _obs(db_session, icao="EDDF", time=T - timedelta(minutes=10))
        apt = _airport(_get(db_session), "EDDF")
        assert apt["models"] == {}
        assert apt["consensus"] is None
        assert apt["consensus_majority"] is None
        # Observed sources still get the alternate-required flags.
        assert "alt_required" in apt["observed"]["metar"]


# ---------------------------------------------------------------------------
# Parquet archive
# ---------------------------------------------------------------------------


class TestArchive:

    def test_pruned_day_is_served_from_the_archive(self, db_session, archive_dir):
        from weatherbrief.tasks.archive import archive_period

        _snap(db_session, init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc),
              ceiling=700.0)
        _snap(db_session, icao="EDDF", init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc))
        db_session.commit()
        from_db = _get(db_session)

        archive_period(db_session, "snapshots", "2026-04-05")
        db_session.commit()
        db_session.execute(delete(AirportForecastSnapshotRow))
        db_session.commit()

        later = NOW + timedelta(days=20)
        hm.payload_cache.clear()
        from_archive = _get(db_session, now=later)

        assert from_archive["sources"]["gfs"] == from_db["sources"]["gfs"]
        assert [a["models"] for a in from_archive["airports"]] == [
            a["models"] for a in from_db["airports"]
        ]
        assert _airport(from_archive)["models"]["gfs"]["flight_category"] == "IFR"

    def test_unarchived_old_day_falls_back_to_db(self, db_session, archive_dir):
        _snap(db_session, init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc))
        data = _get(db_session, now=NOW + timedelta(days=20))
        assert data["sources"]["gfs"]["available"] is True


# ---------------------------------------------------------------------------
# Range + API
# ---------------------------------------------------------------------------


class TestRange:

    def test_range_reports_earliest_sources_and_leads(self, db_session):
        _obs(db_session, time=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc))
        _snap(db_session, init=datetime(2026, 4, 5, 0, tzinfo=timezone.utc),
              valid=VT09, fetched=datetime(2026, 4, 5, 7, tzinfo=timezone.utc))
        rng = hm.compute_historical_range(db_session, now=NOW)
        assert rng["earliest_observation"].startswith("2026-03-01T10:00")
        assert rng["earliest_model"].startswith("2026-04-05T07:00")
        assert rng["step_minutes"] == 30
        leads = {entry["lead_days"]: entry["models"] for entry in rng["leads"]}
        assert leads[0] == ["gfs", "icon", "ecmwf"]
        assert leads[5] == ["gfs", "ecmwf"]
        assert max(leads) == hm.MAX_LEAD_DAYS


@pytest.fixture
def app_db():
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
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


def _client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    for flag in ("DISABLE_SCHEDULER", "DISABLE_RETENTION", "DISABLE_VERIFICATION",
                 "DISABLE_DIGEST", "DISABLE_STANDALONE_VERIFICATION"):
        monkeypatch.setenv(flag, "1")
    app = create_app()

    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    app.state.db_path = "/fake/airports.db"
    return TestClient(app, raise_server_exceptions=False)


class TestEndpoints:

    def test_historical_endpoint(self, app_db, tmp_path, monkeypatch):
        client = _client(app_db, tmp_path, monkeypatch)
        session = app_db()
        _obs(session, time=T - timedelta(minutes=10), taf=TAF_TEMPO)
        session.commit()
        session.close()

        resp = client.get("/api/maps/historical", params={"at": "2026-04-05T10:44:00Z"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["at"] == T.isoformat()
        assert data["lead_days"] == 0
        assert _airport(data)["observed"]["taf"]["flight_category"] == "IFR"
        assert "immutable" in resp.headers.get("cache-control", "")

    def test_future_instant_is_rejected(self, app_db, tmp_path, monkeypatch):
        client = _client(app_db, tmp_path, monkeypatch)
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        resp = client.get("/api/maps/historical", params={"at": future})
        assert resp.status_code == 400

    def test_lead_is_bounded(self, app_db, tmp_path, monkeypatch):
        client = _client(app_db, tmp_path, monkeypatch)
        resp = client.get("/api/maps/historical",
                          params={"at": "2026-04-05T10:30:00Z", "lead": hm.MAX_LEAD_DAYS + 1})
        assert resp.status_code == 422

    def test_range_endpoint(self, app_db, tmp_path, monkeypatch):
        client = _client(app_db, tmp_path, monkeypatch)
        resp = client.get("/api/maps/historical/range")
        assert resp.status_code == 200
        assert resp.json()["step_minutes"] == 30


class TestNow:
    """The "Now" tab's METAR read (#656): unsnapped, METAR only."""

    def test_reads_the_newest_metar_without_the_half_hour_floor(self, db_session):
        now = datetime(2026, 4, 5, 10, 59, tzinfo=timezone.utc)
        _obs(db_session, time=datetime(2026, 4, 5, 10, 20, tzinfo=timezone.utc), raw="OLDER")
        _obs(db_session, time=datetime(2026, 4, 5, 10, 50, tzinfo=timezone.utc), raw="NEWEST",
             taf=TAF_TEMPO)
        data = hm.get_now_map_data(db_session, "/fake/airports.db", now=now)
        apt = _airport(data)
        assert apt["observed"]["metar"]["raw"].startswith("NEWEST")
        assert apt["observed"]["metar"]["age_min"] == 9
        assert "taf" not in apt["observed"]
        assert apt["models"] == {} and apt["consensus"] is None
        assert data["sources"]["metar"]["count"] == 1

    def test_stale_metars_are_left_out(self, db_session):
        now = datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)
        _obs(db_session, time=now - hm.METAR_MAX_AGE - timedelta(minutes=1))
        data = hm.get_now_map_data(db_session, "/fake/airports.db", now=now)
        assert data["airports"] == [] and data["sources"]["metar"]["available"] is False

    def test_now_endpoint(self, app_db, tmp_path, monkeypatch):
        client = _client(app_db, tmp_path, monkeypatch)
        session = app_db()
        _obs(session, time=datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5))
        session.commit()
        session.close()
        resp = client.get("/api/maps/now")
        assert resp.status_code == 200
        assert _airport(resp.json())["observed"]["metar"]["flight_category"] == "VFR"
        assert "max-age=60" in resp.headers["cache-control"]
