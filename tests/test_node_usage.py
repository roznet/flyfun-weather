"""Node usage upload (#922): node push, prod ingest, auth, idempotency, reporting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import get_db
from flyfun_common.db.models import ApiTokenRow, CostLedgerRow
from weatherbrief.api.app import create_app
from weatherbrief.db.models import ApiUsageRow, InstanceSettingRow, NodeUsageIngestRow
from weatherbrief import node_usage
from weatherbrief.node_usage import (
    INGEST_SCOPE,
    get_node_db_id,
    mint_token,
    push,
)

SERVICE = "flyfun-weather"
T0 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _engine_session():
    from conftest import make_app_engine

    engine = make_app_engine()
    return engine, sessionmaker(bind=engine)


@pytest.fixture
def prod_db():
    engine, factory = _engine_session()
    yield factory
    engine.dispose()


@pytest.fixture
def node_db():
    engine, factory = _engine_session()
    yield factory
    engine.dispose()


@pytest.fixture
def client(prod_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    app = create_app()

    def _override_get_db():
        session = prod_db()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def token(prod_db):
    s = prod_db()
    try:
        return mint_token(s, "macmini")
    finally:
        s.close()


def _seed_node(factory, *, api=(), costs=()):
    """api: (service, pipeline, calls, weight, origin); costs: (category, cost)."""
    s = factory()
    for i, (service, pipeline, calls, weight, origin) in enumerate(api):
        s.add(ApiUsageRow(
            timestamp=T0 + timedelta(minutes=i), service=service, pipeline=pipeline,
            api_calls=calls, weighted_calls=weight, origin=origin,
            user_id="dev-user", flight_id="zz-flight",
        ))
    for i, (category, cost) in enumerate(costs):
        s.add(CostLedgerRow(
            user_id="dev-user", service=SERVICE, action=category, cost=cost,
            category=category, created_at=T0 + timedelta(minutes=i),
            description=f"{category} row",
            detail_json=json.dumps({"token_cost_usd": cost}),
            metadata_json=json.dumps({"model": "claude-haiku-5-5"}),
        ))
    s.commit()
    s.close()


def _push(factory, client, tok, **kw):
    s = factory()
    try:
        return push(s, "", tok, http=client, **kw)
    finally:
        s.close()


# --- node identity ----------------------------------------------------------


def test_node_db_id_is_minted_once_and_kept(node_db):
    s = node_db()
    first = get_node_db_id(s)
    assert get_node_db_id(s) == first
    s.close()
    s = node_db()
    assert s.get(InstanceSettingRow, "node_db_id").value == first
    s.close()


# --- end to end --------------------------------------------------------------


def test_push_lands_rows_on_prod_with_origin(client, prod_db, node_db, token):
    _seed_node(
        node_db,
        api=[
            ("open_meteo", "hewson_precompute", 3, 1234.5, None),
            ("open_meteo", "briefing", 2, 50.0, "dev"),  # uploaded earlier: never forwarded
        ],
        costs=[("briefing", 0.12), ("topup", 0.0), ("eval", 0.5)],
    )
    result = _push(node_db, client, token)
    assert result.error is None
    assert result.sent == {"api_usage_log": 1, "cost_ledger": 2}
    assert result.inserted == {"api_usage_log": 1, "cost_ledger": 2}

    s = prod_db()
    (api,) = s.query(ApiUsageRow).all()
    assert (api.origin, api.service, api.pipeline, api.api_calls) == (
        "macmini", "open_meteo", "hewson_precompute", 3,
    )
    assert api.weighted_calls == pytest.approx(1234.5)
    # Node-local ids mean nothing on prod.
    assert api.user_id is None and api.flight_id is None

    costs = s.query(CostLedgerRow).order_by(CostLedgerRow.id).all()
    assert [c.category for c in costs] == ["node", "node"]
    assert {c.user_id for c in costs} == {"system:macmini"}
    assert [c.action for c in costs] == ["briefing", "eval"]
    assert [c.cost for c in costs] == [pytest.approx(0.12), pytest.approx(0.5)]
    detail = json.loads(costs[0].detail_json)
    assert detail["origin"] == "macmini"
    assert detail["category"] == "briefing"
    assert detail["local_id"] == 1
    assert detail["detail"] == {"token_cost_usd": 0.12}
    # Spent-at time is the node's, so the row lands in the right window.
    created = costs[0].created_at
    assert (created if created.tzinfo else created.replace(tzinfo=timezone.utc)) == T0
    s.close()


def test_second_push_sends_nothing_then_only_new_rows(client, prod_db, node_db, token):
    _seed_node(node_db, api=[("open_meteo", "verification", 1, 10.0, None)],
               costs=[("briefing", 0.1)])
    assert _push(node_db, client, token).sent == {"api_usage_log": 1, "cost_ledger": 1}
    assert _push(node_db, client, token).sent == {"api_usage_log": 0, "cost_ledger": 0}

    _seed_node(node_db, api=[("open_meteo", "verification", 4, 40.0, None)])
    again = _push(node_db, client, token)
    assert again.sent == {"api_usage_log": 1, "cost_ledger": 0}
    s = prod_db()
    assert s.query(ApiUsageRow).count() == 2
    assert s.query(NodeUsageIngestRow).count() == 3
    s.close()


def test_batches_cover_everything(client, prod_db, node_db, token):
    _seed_node(node_db, api=[("open_meteo", "frontal", 1, 1.0, None)] * 7)
    result = _push(node_db, client, token, batch_size=3)
    assert result.sent["api_usage_log"] == 7
    s = prod_db()
    assert s.query(ApiUsageRow).count() == 7
    s.close()


def test_recreated_node_db_does_not_collide(client, prod_db, node_db, token):
    _seed_node(node_db, api=[("open_meteo", "frontal", 1, 1.0, None)])
    _push(node_db, client, token)

    # Same local id 1 in a fresh DB: a new node_db id, so it is a new row.
    engine, fresh = _engine_session()
    try:
        _seed_node(fresh, api=[("open_meteo", "frontal", 2, 2.0, None)])
        assert _push(fresh, client, token).inserted["api_usage_log"] == 1
    finally:
        engine.dispose()
    s = prod_db()
    assert sorted(r.api_calls for r in s.query(ApiUsageRow)) == [1, 2]
    s.close()


def test_resent_batch_is_idempotent(client, prod_db, node_db, token):
    _seed_node(node_db, costs=[("briefing", 0.1)])
    s = node_db()
    nid = get_node_db_id(s)
    rows = {
        "api_usage_log": [],
        "cost_ledger": node_usage.collect_cost_ledger(s, 0, 10)[0],
    }
    s.close()
    headers = {"Authorization": f"Bearer {token}"}
    body = {"node_db": nid, "rows": rows}
    assert client.post("/api/admin/usage/ingest", json=body, headers=headers).json() == {
        "inserted": {"api_usage_log": 0, "cost_ledger": 1},
    }
    assert client.post("/api/admin/usage/ingest", json=body, headers=headers).json() == {
        "inserted": {"api_usage_log": 0, "cost_ledger": 0},
    }


def test_a_row_prod_would_refuse_never_stalls_the_push(client, prod_db, node_db, token):
    """Review on #939: one refused row used to 422 its batch forever."""
    s = node_db()
    s.add_all([
        ApiUsageRow(timestamp=T0, service="Open Meteo", pipeline="Hewson CLI",
                    api_calls=1, weighted_calls=5.0),                     # normalised
        ApiUsageRow(timestamp=T0, service="open_meteo", pipeline="frontal",
                    api_calls=1, weighted_calls=1e12),                    # over the cap
        ApiUsageRow(timestamp=T0, service="open_meteo", pipeline="frontal",
                    api_calls=2, weighted_calls=2.0),
        CostLedgerRow(user_id="u", service=SERVICE, action="eval" * 30, cost=500.0,
                      category="node", created_at=T0),                     # over the cap
        CostLedgerRow(user_id="u", service=SERVICE, action="eval" * 30, cost=0.25,
                      category="x" * 40, description="d" * 400, created_at=T0,
                      detail_json="{" + " " * 9000 + "}"),                 # clipped
    ])
    s.commit()
    s.close()
    result = _push(node_db, client, token, batch_size=1)
    assert result.error is None
    assert result.inserted == {"api_usage_log": 2, "cost_ledger": 1}
    p = prod_db()
    assert sorted((r.service, r.pipeline) for r in p.query(ApiUsageRow)) == [
        ("open_meteo", "frontal"), ("open_meteo", "hewson_cli"),
    ]
    (cost,) = p.query(CostLedgerRow).all()
    assert cost.cost == pytest.approx(0.25)
    assert json.loads(cost.detail_json)["detail"] is None
    p.close()
    # The skipped rows are not retried into a stall on the next run either.
    assert _push(node_db, client, token).error is None


def test_push_failure_never_raises(node_db):
    class _Down:
        def get(self, *a, **kw):
            raise ConnectionError("prod down")

    _seed_node(node_db, costs=[("briefing", 0.1)])
    s = node_db()
    result = push(s, "https://example.invalid", "ff_x", http=_Down())
    s.close()
    assert result.error and "prod down" in result.error
    assert result.sent == {"api_usage_log": 0, "cost_ledger": 0}


def test_cli_push_exits_zero_when_not_configured(monkeypatch, capsys):
    monkeypatch.setattr(node_usage, "_session", lambda: _FakeSession())
    assert node_usage.main(["push"]) == 0
    assert "not configured" in capsys.readouterr().out


class _FakeSession:
    def close(self):
        pass


def test_push_config_refuses_production(monkeypatch):
    monkeypatch.setenv("WB_USAGE_PUSH_URL", "https://weather.example/")
    monkeypatch.setenv("WB_USAGE_PUSH_TOKEN", "ff_x")
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert node_usage.push_config() == ("https://weather.example", "ff_x")
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert node_usage.push_config() is None


# --- auth --------------------------------------------------------------------


def _add_token(prod_db, *, user_id, scope, revoked=False) -> str:
    raw = f"ff_test_{user_id}_{scope}_{revoked}".replace(":", "_").replace(" ", "_")
    s = prod_db()
    s.add(ApiTokenRow(
        user_id=user_id, token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        name="t", scope=scope, revoked=revoked,
    ))
    s.commit()
    s.close()
    return raw


_NODE = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize("make, status", [
    (lambda db: None, 401),                                                   # no token
    (lambda db: _add_token(db, user_id="admin-user", scope=None), 403),       # full-access admin token
    (lambda db: _add_token(db, user_id="pilot", scope=INGEST_SCOPE), 403),    # scope on a person
    (lambda db: _add_token(db, user_id="system:dev", scope="mcp"), 403),      # other scope
    (lambda db: _add_token(db, user_id="system:prod", scope=INGEST_SCOPE), 403),  # reserved origin
    (lambda db: _add_token(db, user_id="system:dev", scope=INGEST_SCOPE, revoked=True), 401),
])
def test_only_an_ingest_token_gets_in(client, prod_db, make, status):
    tok = make(prod_db)
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    r = client.get("/api/admin/usage/ingest/watermark", params={"node_db": _NODE}, headers=headers)
    assert r.status_code == status
    r = client.post("/api/admin/usage/ingest", json={"node_db": _NODE, "rows": {}}, headers=headers)
    assert r.status_code == status


def test_ingest_token_reaches_nothing_else(client, token, monkeypatch):
    # Out of dev mode, or every request is the dev user whatever the token.
    monkeypatch.setenv("ENVIRONMENT", "production")
    r = client.get("/api/admin/api-usage", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 403)


def test_node_db_cannot_be_written_by_another_origin(client, prod_db, token):
    other = _add_token(prod_db, user_id="system:dev", scope=INGEST_SCOPE)
    row = {"local_id": 1, "timestamp": T0.isoformat(), "service": "open_meteo",
           "pipeline": "frontal", "api_calls": 1, "weighted_calls": 1.0}
    body = {"node_db": _NODE, "rows": {"api_usage_log": [row]}}
    ok = client.post("/api/admin/usage/ingest", json=body,
                     headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    row["local_id"] = 2
    clash = client.post("/api/admin/usage/ingest", json=body,
                        headers={"Authorization": f"Bearer {other}"})
    assert clash.status_code == 409


def test_absurd_rows_are_rejected(client, token):
    row = {"local_id": 1, "created_at": T0.isoformat(), "action": "briefing", "cost": 5000.0}
    r = client.post(
        "/api/admin/usage/ingest",
        json={"node_db": _NODE, "rows": {"cost_ledger": [row]}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_mint_token_creates_the_service_account_once(prod_db):
    # Prod's api_tokens.user_id keeps migration 007's FK to users.id, which
    # this create_all DB lacks: assert the users row the FK needs exists.
    from flyfun_common.db.models import UserRow

    s = prod_db()
    mint_token(s, "dev")
    mint_token(s, "dev")  # a second token reuses the account
    user = s.get(UserRow, "system:dev")
    assert user is not None and user.approved is False
    assert s.query(ApiTokenRow).filter(ApiTokenRow.user_id == "system:dev").count() == 2
    s.close()


def test_push_floor_keeps_a_forked_db_from_resending_copied_rows(
    client, prod_db, node_db, token,
):
    # A --fork-db copy: rows up to the floor are main's, already pushed.
    _seed_node(
        node_db,
        api=[("open_meteo", "briefing", 1, 10.0, None)] * 3,
        costs=[("briefing", 0.1)] * 3,
    )
    s = node_db()
    s.add(InstanceSettingRow(key="push_floor:api_usage_log", value="2"))
    s.add(InstanceSettingRow(key="push_floor:cost_ledger", value="3"))
    s.commit()
    s.close()

    result = _push(node_db, client, token)
    assert result.error is None
    assert result.sent == {"api_usage_log": 1, "cost_ledger": 0}
    s = prod_db()
    assert [r.local_id for r in s.query(NodeUsageIngestRow).all()] == [3]
    s.close()


def test_mint_token_cli_runs_on_a_production_server(tmp_path):
    # Prod never runs init_shared_db: the CLI must still bind its session.
    import os
    import subprocess
    import sys

    from sqlalchemy import create_engine

    from flyfun_common.db.models import Base

    db_path = tmp_path / "prod.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    env = {**os.environ, "ENVIRONMENT": "production", "DATABASE_URL": f"sqlite:///{db_path}"}
    out = subprocess.run(
        [sys.executable, "-m", "weatherbrief.node_usage", "mint-token", "--origin", "dev"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert any(line.startswith("ff_") for line in out.stdout.splitlines())


def test_mint_token_rejects_bad_origin(prod_db):
    s = prod_db()
    with pytest.raises(ValueError):
        mint_token(s, "prod")
    with pytest.raises(ValueError):
        mint_token(s, "Mac Mini")
    s.close()


# --- reporting -----------------------------------------------------------------


def test_api_usage_summary_breaks_down_by_origin(prod_db):
    from weatherbrief.api.usage import get_api_usage_summary

    s = prod_db()
    now = datetime.now(timezone.utc)
    s.add_all([
        ApiUsageRow(timestamp=now, service="open_meteo", pipeline="briefing",
                    api_calls=2, weighted_calls=100.0),
        ApiUsageRow(timestamp=now, service="open_meteo", pipeline="briefing",
                    api_calls=1, weighted_calls=300.0, origin="macmini"),
        ApiUsageRow(timestamp=now, service="open_meteo", pipeline="frontal",
                    api_calls=1, weighted_calls=50.0, origin="dev"),
    ])
    s.commit()
    period = get_api_usage_summary(s).last_30d
    assert [(o.origin, o.total_weighted_calls) for o in period.by_origin] == [
        ("macmini", 300.0), ("prod", 100.0), ("dev", 50.0),
    ]
    assert period.total_weighted_calls == 450.0
    assert {(b.origin, b.pipeline) for b in period.by_service} == {
        ("prod", "briefing"), ("macmini", "briefing"), ("dev", "frontal"),
    }
    s.close()


def test_record_eval_cost_writes_a_node_eval_row(prod_db, monkeypatch):
    import flyfun_common.db as fdb

    monkeypatch.setattr(fdb, "SessionLocal", prod_db)
    node_usage.record_eval_cost("run_digest_eval", 0.4321, calls=12,
                                model="claude-sonnet-5-5", cache_ttl="5m")
    node_usage.record_eval_cost("run_digest_eval", 0.0, calls=0,
                                model="claude-sonnet-5-5", cache_ttl="5m")  # nothing spent
    s = prod_db()
    (row,) = s.query(CostLedgerRow).all()
    assert (row.category, row.action, row.cost) == ("node", "eval", pytest.approx(0.4321))
    assert json.loads(row.metadata_json)["cache_ttl"] == "5m"
    s.close()
