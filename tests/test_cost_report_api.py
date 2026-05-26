"""Tests for the admin program cost-report endpoint and config auto-sum."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app
from weatherbrief.api.admin import require_admin
from weatherbrief.costs import DEFAULT_CONFIG
from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import CostLedgerRow, UserRow
from weatherbrief.db.models import CostConfigRow

SERVICE = "flyfun-weather"


@pytest.fixture
def app_db():
    """In-memory SQLite engine + session factory with a dev user."""
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev User", approved=True,
    ))
    session.commit()
    session.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
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
    app.dependency_overrides[require_admin] = lambda: DEV_USER_ID

    return TestClient(app, raise_server_exceptions=False)


def _seed_active_config(session, **overrides) -> int:
    cfg = DEFAULT_CONFIG
    if overrides:
        from dataclasses import replace
        cfg = replace(DEFAULT_CONFIG, **overrides)
    row = CostConfigRow(active_from=datetime.now(timezone.utc), config_json=cfg.to_json())
    session.add(row)
    session.commit()
    cid = row.id
    session.close()
    return cid


def _add_briefing(session, user_id, token_usd, storage_usd, *, days_ago=0):
    bd = {
        "token_cost_usd": token_usd,
        "infra_share_usd": 0.05,
        "subscription_share_usd": 0.06,
        "storage_cost_usd": storage_usd,
        "subtotal_usd": token_usd + storage_usd + 0.11,
        "margin_usd": 0.03,
        "total_usd": token_usd + storage_usd + 0.14,
        "config_id": 1,
    }
    session.add(CostLedgerRow(
        user_id=user_id,
        service=SERVICE,
        action="briefing",
        cost=bd["total_usd"],
        category="briefing",
        description="Briefing",
        detail_json=json.dumps(bd),
        created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    ))


class TestCostReportEndpoint:
    def test_no_config_returns_null(self, client):
        resp = client.get("/api/admin/cost-report?window=30d")
        assert resp.status_code == 200
        assert resp.json() is None

    def test_invalid_window_rejected(self, client, app_db):
        _seed_active_config(app_db())
        resp = client.get("/api/admin/cost-report?window=90d")
        assert resp.status_code == 422

    def test_30d_report_aggregates_variable_and_counts(self, client, app_db):
        _seed_active_config(app_db())
        s = app_db()
        _add_briefing(s, "user-a", 0.20, 0.01)
        _add_briefing(s, "user-a", 0.10, 0.02)
        _add_briefing(s, "user-b", 0.05, 0.00)
        s.commit()
        s.close()

        data = client.get("/api/admin/cost-report?window=30d").json()
        assert data["num_briefings"] == 3
        assert data["num_users"] == 2
        assert abs(data["variable_token_usd"] - 0.35) < 1e-6
        assert abs(data["variable_storage_usd"] - 0.03) < 1e-6
        # Fixed = 24 + 2 + 30 = 56 at 30d
        assert data["fixed_prorated_usd"] == 56.0
        # cost per briefing/user > 0 and consistent with total
        assert data["cost_per_briefing_usd"] > 0
        assert abs(data["cost_per_briefing_usd"] - round(data["total_usd"] / 3, 4)) < 1e-3

    def test_7d_window_excludes_old_briefings(self, client, app_db):
        _seed_active_config(app_db())
        s = app_db()
        _add_briefing(s, "user-a", 0.20, 0.01, days_ago=1)   # in window
        _add_briefing(s, "user-b", 0.10, 0.02, days_ago=10)  # out of 7d window
        s.commit()
        s.close()

        data = client.get("/api/admin/cost-report?window=7d").json()
        assert data["num_briefings"] == 1
        assert data["num_users"] == 1
        assert abs(data["variable_token_usd"] - 0.20) < 1e-6
        # 7d fixed proration
        assert abs(data["fixed_prorated_usd"] - 56.0 * 7 / 30) < 1e-2


class TestConfigSubscriptionAutoSum:
    def test_put_auto_sums_subscriptions_from_details(self, client, app_db):
        _seed_active_config(app_db())
        resp = client.put(
            "/api/admin/cost-config",
            json={"subscription_details": {"open_meteo": 30, "ecmwf": 50}},
        )
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        assert cfg["subscriptions_monthly_usd"] == 80.0
        assert cfg["subscription_details"] == {"open_meteo": 30, "ecmwf": 50}

    def test_put_rejects_non_numeric_subscription(self, client, app_db):
        _seed_active_config(app_db())
        resp = client.put(
            "/api/admin/cost-config",
            json={"subscription_details": {"open_meteo": "free"}},
        )
        assert resp.status_code == 422
