"""Tests for usage tracking, rate limits, and usage summary API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from weatherbrief.api.usage import (
    DAILY_LIMITS,
    _clip_flight_id,
    check_rate_limits,
    check_service_limits,
    count_user_time_scans,
    get_usage_summary,
    log_api_usage,
    log_briefing_usage,
    user_has_used_time_scan,
)
from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.db.models import ApiUsageRow, BriefingUsageRow
from weatherbrief.pipeline import BriefingUsage


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory."""
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


@pytest.fixture
def db_session(app_db):
    """Single DB session for unit tests."""
    session = app_db()
    yield session
    session.close()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    """Create a test client with isolated DB."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    app = create_app()
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)

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
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID

    return TestClient(app, raise_server_exceptions=False)


class TestClipFlightId:
    """Defense-in-depth guard for over-length flight_ids (column is VARCHAR(256))."""

    def test_short_unchanged(self):
        fid = "LFPG-EGLL-EDDF"
        assert _clip_flight_id(fid) == fid

    def test_real_119_char_route_unchanged(self):
        fid = (
            "lfrq_namar_sable_luman_kovak_domod_gilux_motal_oskin_kutan_balmu_"
            "rlp_epl_oborn_str_lupen_sul_reutl_edds-2026-05-18-a0c1"
        )
        assert len(fid) == 119
        assert _clip_flight_id(fid) == fid

    def test_exactly_256_unchanged(self):
        fid = "A" * 256
        assert _clip_flight_id(fid) == fid

    def test_over_limit_clips_to_256_with_hash(self):
        fid = "A" * 302
        clipped = _clip_flight_id(fid)
        assert len(clipped) == 256
        # Last 9 chars = "-" + 8 hex digits
        assert clipped[-9] == "-"
        assert all(c in "0123456789abcdef" for c in clipped[-8:])

    def test_clip_is_deterministic(self):
        fid = "B" * 400
        assert _clip_flight_id(fid) == _clip_flight_id(fid)

    def test_distinct_inputs_get_distinct_hashes(self):
        a = "C" * 300
        b = "C" * 299 + "D"
        assert _clip_flight_id(a) != _clip_flight_id(b)


class TestLogBriefingUsage:
    """Test that usage rows are correctly created."""

    def test_log_basic_usage(self, db_session):
        usage = BriefingUsage(open_meteo_calls=3)
        log_briefing_usage(db_session, DEV_USER_ID, "flight-1", usage)
        db_session.commit()

        rows = db_session.query(BriefingUsageRow).all()
        assert len(rows) == 1
        assert rows[0].open_meteo_calls == 3
        assert rows[0].flight_id == "flight-1"
        assert rows[0].gramet_fetched is False
        assert rows[0].llm_digest is False

    def test_log_full_usage(self, db_session):
        usage = BriefingUsage(
            open_meteo_calls=2,
            gramet_fetched=True,
            llm_digest=True,
            llm_model="anthropic:claude-sonnet-4-5",
            llm_input_tokens=5000,
            llm_output_tokens=1000,
        )
        log_briefing_usage(db_session, DEV_USER_ID, "flight-2", usage)
        db_session.commit()

        row = db_session.query(BriefingUsageRow).one()
        assert row.gramet_fetched is True
        assert row.llm_digest is True
        assert row.llm_model == "anthropic:claude-sonnet-4-5"
        assert row.llm_input_tokens == 5000
        assert row.llm_output_tokens == 1000

    def test_log_gramet_failure(self, db_session):
        usage = BriefingUsage(open_meteo_calls=1, gramet_failed=True)
        log_briefing_usage(db_session, DEV_USER_ID, "flight-3", usage)
        db_session.commit()

        row = db_session.query(BriefingUsageRow).one()
        assert row.gramet_fetched is False
        assert row.gramet_failed is True


class TestRateLimits:
    """Test rate limit checking."""

    def test_allows_under_quota(self, db_session):
        """No exception when under all limits."""
        usage = BriefingUsage(open_meteo_calls=3, gramet_fetched=True, llm_digest=True)
        log_briefing_usage(db_session, DEV_USER_ID, "f1", usage)
        db_session.commit()

        # Should not raise
        check_rate_limits(db_session, DEV_USER_ID)

    def test_blocks_open_meteo_limit(self, db_session):
        """429 when Open-Meteo daily limit exceeded."""
        from fastapi import HTTPException

        for i in range(17):  # 17 * 3 = 51 > 50
            usage = BriefingUsage(open_meteo_calls=3)
            log_briefing_usage(db_session, DEV_USER_ID, f"f-{i}", usage)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            check_rate_limits(db_session, DEV_USER_ID)
        assert exc_info.value.status_code == 429
        assert "Open-Meteo" in exc_info.value.detail

    def test_gramet_limit_skips_gracefully(self, db_session):
        """GRAMET at limit is reported as unavailable (not 429)."""
        for i in range(DAILY_LIMITS["gramet"]):
            usage = BriefingUsage(gramet_fetched=True)
            log_briefing_usage(db_session, DEV_USER_ID, f"f-{i}", usage)
        db_session.commit()

        # check_rate_limits should NOT raise — GRAMET is soft-limited
        check_rate_limits(db_session, DEV_USER_ID)

        # check_service_limits should report GRAMET as unavailable
        limits = check_service_limits(db_session, DEV_USER_ID)
        assert limits["gramet"] is False
        assert limits["llm_digest"] is True

    def test_llm_limit_skips_gracefully(self, db_session):
        """LLM digest at limit is reported as unavailable (not 429)."""
        for i in range(DAILY_LIMITS["llm_digest"]):
            usage = BriefingUsage(llm_digest=True)
            log_briefing_usage(db_session, DEV_USER_ID, f"f-{i}", usage)
        db_session.commit()

        # check_rate_limits should NOT raise — LLM is soft-limited
        check_rate_limits(db_session, DEV_USER_ID)

        # check_service_limits should report LLM as unavailable
        limits = check_service_limits(db_session, DEV_USER_ID)
        assert limits["gramet"] is True
        assert limits["llm_digest"] is False

    def test_yesterday_usage_not_counted(self, db_session):
        """Usage from yesterday doesn't count toward today's limits."""
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        for i in range(20):
            row = BriefingUsageRow(
                user_id=DEV_USER_ID,
                flight_id=f"f-{i}",
                timestamp=yesterday,
                open_meteo_calls=3,
                gramet_fetched=True,
                llm_digest=True,
            )
            db_session.add(row)
        db_session.commit()

        # Should not raise — all usage is from yesterday
        check_rate_limits(db_session, DEV_USER_ID)


class TestUsageSummary:
    """Test usage summary aggregation."""

    def test_empty_summary(self, db_session):
        summary = get_usage_summary(db_session, DEV_USER_ID)
        assert summary.today.briefings == 0
        assert summary.today.open_meteo.used == 0
        assert summary.today.open_meteo.limit == DAILY_LIMITS["open_meteo"]
        assert summary.month.briefings == 0
        assert summary.month.total_tokens == 0

    def test_summary_with_usage(self, db_session):
        for i in range(3):
            usage = BriefingUsage(
                open_meteo_calls=2,
                gramet_fetched=i < 2,
                llm_digest=True,
                llm_input_tokens=4000,
                llm_output_tokens=800,
            )
            log_briefing_usage(db_session, DEV_USER_ID, f"f-{i}", usage)
        db_session.commit()

        summary = get_usage_summary(db_session, DEV_USER_ID)
        assert summary.today.briefings == 3
        assert summary.today.open_meteo.used == 6  # 3 * 2
        assert summary.today.gramet.used == 2
        assert summary.today.llm_digest.used == 3
        assert summary.month.briefings == 3
        assert summary.month.total_tokens == 3 * (4000 + 800)

    def test_month_includes_older_today_does_not(self, db_session):
        """Month summary includes older data, today does not."""
        # Use yesterday at noon — always within the current month and not today,
        # unless today is the 1st. In that case, fall back to 1 hour ago
        # (still today but before _today_start() won't work).  Instead,
        # pick a timestamp that is: (a) within this month, (b) before today.
        # If today is the 1st, there IS no such timestamp — skip gracefully.
        now = datetime.now(timezone.utc)
        if now.day == 1:
            pytest.skip("Cannot test month-vs-today on the 1st of the month")
        yesterday = now - timedelta(days=1)
        row = BriefingUsageRow(
            user_id=DEV_USER_ID,
            flight_id="old-flight",
            timestamp=yesterday,
            open_meteo_calls=3,
            gramet_fetched=True,
            llm_digest=True,
            llm_input_tokens=2000,
            llm_output_tokens=500,
        )
        db_session.add(row)
        db_session.commit()

        summary = get_usage_summary(db_session, DEV_USER_ID)
        assert summary.today.briefings == 0
        assert summary.month.briefings == 1
        assert summary.month.total_tokens == 2500


class TestTimeScanUsage:
    """Durable per-user ``time_scan`` count helpers (flexibility gate substrate)."""

    OTHER_USER = "other-user-123"

    def _log_scan(self, db, user_id, *, pipeline="same_day"):
        log_api_usage(
            db, service="time_scan", pipeline=pipeline,
            api_calls=1, user_id=user_id, flight_id="f-1",
        )

    def test_no_rows_returns_false(self, db_session):
        assert user_has_used_time_scan(db_session, DEV_USER_ID) is False
        assert count_user_time_scans(db_session, DEV_USER_ID) == 0

    def test_other_service_does_not_count(self, db_session):
        log_api_usage(
            db_session, service="open_meteo", pipeline="briefing",
            api_calls=5, user_id=DEV_USER_ID, flight_id="f-1",
        )
        db_session.commit()
        assert user_has_used_time_scan(db_session, DEV_USER_ID) is False
        assert count_user_time_scans(db_session, DEV_USER_ID) == 0

    def test_other_user_does_not_count(self, db_session):
        self._log_scan(db_session, self.OTHER_USER)
        db_session.commit()
        assert user_has_used_time_scan(db_session, DEV_USER_ID) is False
        assert count_user_time_scans(db_session, DEV_USER_ID) == 0

    def test_own_time_scan_row_counts(self, db_session):
        self._log_scan(db_session, DEV_USER_ID)
        self._log_scan(db_session, DEV_USER_ID, pipeline="next_day")
        db_session.commit()
        assert user_has_used_time_scan(db_session, DEV_USER_ID) is True
        assert count_user_time_scans(db_session, DEV_USER_ID) == 2

    def test_count_respects_since(self, db_session):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        db_session.add(ApiUsageRow(
            service="time_scan", pipeline="same_day", api_calls=1,
            user_id=DEV_USER_ID, flight_id="f-old", timestamp=old,
        ))
        self._log_scan(db_session, DEV_USER_ID)  # now
        db_session.commit()

        one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
        assert count_user_time_scans(db_session, DEV_USER_ID) == 2
        assert count_user_time_scans(db_session, DEV_USER_ID, since=one_day_ago) == 1

    def test_summary_surfaces_time_scan_flag(self, db_session):
        summary = get_usage_summary(db_session, DEV_USER_ID)
        assert summary.time_scan_used is False
        assert summary.time_scan_count == 0

        self._log_scan(db_session, DEV_USER_ID)
        db_session.commit()

        summary = get_usage_summary(db_session, DEV_USER_ID)
        assert summary.time_scan_used is True
        assert summary.time_scan_count == 1


class TestUsageAPI:
    """Test GET /api/user/usage endpoint."""

    def test_get_usage_empty(self, client):
        resp = client.get("/api/user/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["today"]["briefings"] == 0
        assert data["today"]["open_meteo"]["limit"] == DAILY_LIMITS["open_meteo"]
        assert data["month"]["total_tokens"] == 0

    def test_get_usage_with_data(self, client, app_db):
        """Usage endpoint reflects logged data."""
        session = app_db()
        row = BriefingUsageRow(
            user_id=DEV_USER_ID,
            flight_id="test-flight",
            open_meteo_calls=2,
            gramet_fetched=True,
            llm_digest=True,
            llm_input_tokens=3000,
            llm_output_tokens=600,
        )
        session.add(row)
        session.commit()
        session.close()

        resp = client.get("/api/user/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["today"]["briefings"] == 1
        assert data["today"]["open_meteo"]["used"] == 2
        assert data["today"]["gramet"]["used"] == 1
        assert data["today"]["llm_digest"]["used"] == 1
        assert data["month"]["total_tokens"] == 3600
