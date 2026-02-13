"""Tests for usage tracking, rate limits, and usage summary API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from weatherbrief.api.app import create_app
from weatherbrief.api.usage import (
    DAILY_LIMITS,
    check_rate_limits,
    get_usage_summary,
    log_briefing_usage,
)
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.engine import DEV_USER_ID
from weatherbrief.db.models import Base, BriefingUsageRow, UserPreferencesRow, UserRow
from weatherbrief.pipeline import BriefingUsage


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
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
    import weatherbrief.api.routes as routes_mod

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "routes.yaml").write_text("routes: {}\n")
    monkeypatch.setattr(routes_mod, "CONFIG_DIR", config_dir)
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

    def test_blocks_gramet_limit(self, db_session):
        """429 when GRAMET daily limit exceeded."""
        from fastapi import HTTPException

        for i in range(DAILY_LIMITS["gramet"]):
            usage = BriefingUsage(gramet_fetched=True)
            log_briefing_usage(db_session, DEV_USER_ID, f"f-{i}", usage)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            check_rate_limits(db_session, DEV_USER_ID)
        assert exc_info.value.status_code == 429
        assert "GRAMET" in exc_info.value.detail

    def test_blocks_llm_limit(self, db_session):
        """429 when LLM digest daily limit exceeded."""
        from fastapi import HTTPException

        for i in range(DAILY_LIMITS["llm_digest"]):
            usage = BriefingUsage(llm_digest=True)
            log_briefing_usage(db_session, DEV_USER_ID, f"f-{i}", usage)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            check_rate_limits(db_session, DEV_USER_ID)
        assert exc_info.value.status_code == 429
        assert "LLM" in exc_info.value.detail

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
        three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
        row = BriefingUsageRow(
            user_id=DEV_USER_ID,
            flight_id="old-flight",
            timestamp=three_days_ago,
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
