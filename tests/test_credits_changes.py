"""Tests for the issue #186 credits changes: retired spending_limit + fx block.

Mounts only the credits/transparency routers (not the full ``create_app``) so it
runs without the heavy briefing stack. FX resolves to USD (no network) for a
user with no currency preference.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, optional_user_id, DEV_USER_ID
from flyfun_common.db.models import CostLedgerRow, UserRow

from weatherbrief.api import credits
from weatherbrief.costs import DEFAULT_CONFIG, compute_cost, config_from_row
from weatherbrief.db.models import CostConfigRow


@pytest.fixture(autouse=True)
def _stub_fx(monkeypatch):
    """Offline, deterministic FX. The default display currency is now EUR, so
    fx-carrying endpoints resolve a non-USD rate — stub the fetch to avoid the
    network and pin a known rate."""
    from flyfun_common import fx
    fx.clear_cache()
    monkeypatch.setattr(fx, "_fetch_rates", lambda: ({"USD": 1.0, "EUR": 0.9}, "2026-06-15"))
    yield
    fx.clear_cache()

SERVICE = "flyfun-weather"


@pytest.fixture
def session_factory():
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(id=DEV_USER_ID, provider="local", provider_sub="dev",
                  email="dev@localhost", display_name="Dev", approved=True))
    s.commit()
    s.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(session_factory):
    app = FastAPI()
    app.include_router(credits.router, prefix="/api")
    app.include_router(credits.transparency_router, prefix="/api")

    def _override_get_db():
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    app.dependency_overrides[optional_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


def test_charge_briefing_records_without_spending_limit(session_factory):
    """charge_briefing appends a ledger row and never reads users.spending_limit."""
    # The shared UserRow model no longer even has the attribute.
    assert not hasattr(UserRow, "spending_limit")

    s = session_factory()
    breakdown = compute_cost(1000, 500, 1024, DEFAULT_CONFIG, config_id=1)
    entry = credits.charge_briefing(s, DEV_USER_ID, usage_row_id=42, breakdown=breakdown)
    s.commit()
    assert entry.cost == pytest.approx(breakdown.total_usd)
    rows = s.query(CostLedgerRow).filter(CostLedgerRow.category == "briefing").all()
    assert len(rows) == 1
    # No topup/auto-reload churn anymore.
    assert s.query(CostLedgerRow).filter(CostLedgerRow.category == "topup").count() == 0
    s.close()


def test_credits_endpoint_carries_fx(client):
    r = client.get("/api/user/credits")
    assert r.status_code == 200, r.text
    body = r.json()
    # EU-first default for a user with no explicit currency preference.
    assert body["fx"]["currency"] == "EUR"
    assert body["fx"]["rate"] == pytest.approx(0.9)


def test_transparency_carries_fx(client, session_factory):
    s = session_factory()
    s.add(CostConfigRow(active_from=datetime.now(timezone.utc),
                        config_json=DEFAULT_CONFIG.to_json()))
    s.commit()
    s.close()
    r = client.get("/api/transparency")
    assert r.status_code == 200, r.text
    assert r.json()["fx"]["currency"] == "EUR"
