"""Per-user platform days (#879): middleware, recorder, MCP, admin activity."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from flyfun_common.db.models import ApiTokenRow
from test_api_tokens import (  # noqa: F401 - fixtures
    ADMIN_ID, REGULAR_ID, _admin_cookie, client, db_session,
)
from weatherbrief.api import platform_usage
from weatherbrief.db.models import UserPlatformDayRow

IOS_UA = "flyfun-weather/22 CFNetwork/3826.500.131 Darwin/25.0.0"
ANDROID_UA = "okhttp/4.12.0"
WEB_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


@pytest.fixture(autouse=True)
def _fresh_seen(monkeypatch):
    monkeypatch.setattr(platform_usage, "_seen", set())
    monkeypatch.setattr(platform_usage, "_seen_day", None)


@pytest.fixture
def recorded(monkeypatch):
    """Capture the middleware's writes (it uses the global engine, not the test DB)."""
    calls: list[tuple[str, str]] = []

    def _capture(user_id, platform):
        calls.append((user_id, platform))
        platform_usage._seen.add((user_id, platform))
        platform_usage._seen_day = datetime.now(timezone.utc).date()

    monkeypatch.setattr(platform_usage, "_record_in_own_session", _capture)
    return calls


class TestMiddleware:
    @pytest.mark.parametrize("agent, platform", [
        (IOS_UA, "ios"), (ANDROID_UA, "android"), (WEB_UA, "web"),
    ])
    def test_session_request_records_platform(self, client, recorded, agent, platform):
        resp = client.get("/api/flights", cookies=_admin_cookie(), headers={"User-Agent": agent})
        assert resp.status_code == 200
        assert recorded == [(ADMIN_ID, platform)]

    def test_once_per_user_platform_day(self, client, recorded):
        for _ in range(3):
            client.get("/api/flights", cookies=_admin_cookie(), headers={"User-Agent": IOS_UA})
        client.get("/api/flights", cookies=_admin_cookie(), headers={"User-Agent": WEB_UA})
        assert recorded == [(ADMIN_ID, "ios"), (ADMIN_ID, "web")]

    def test_passive_badge_sync_not_recorded(self, client, recorded):
        resp = client.get("/api/flights/badge", cookies=_admin_cookie(), headers={"User-Agent": IOS_UA})
        assert resp.status_code == 200
        assert recorded == []

    def test_unknown_agent_and_anonymous_not_recorded(self, client, recorded):
        client.get("/api/flights", cookies=_admin_cookie(), headers={"User-Agent": "curl/8.7.1"})
        client.get("/api/flights", headers={"User-Agent": IOS_UA})  # 401, no session
        assert recorded == []

    def test_api_token_not_recorded(self, client, recorded):
        token = client.post(
            "/api/admin/agents", json={"name": "Platform Agent"}, cookies=_admin_cookie(),
        ).json()["token"]
        recorded.clear()
        resp = client.get(
            "/api/flights", headers={"Authorization": f"Bearer {token}", "User-Agent": IOS_UA},
        )
        assert resp.status_code == 200
        assert recorded == []


class TestRecorder:
    def test_dedupes_and_tolerates_existing_row(self, db_session, monkeypatch):
        db = db_session()
        day = date(2026, 10, 23)
        platform_usage.record_platform_day(db, ADMIN_ID, "ios", day)
        platform_usage.record_platform_day(db, ADMIN_ID, "ios", day)
        db.commit()
        # A restart forgets the in-memory set; the unique constraint absorbs it.
        monkeypatch.setattr(platform_usage, "_seen", set())
        platform_usage.record_platform_day(db, ADMIN_ID, "ios", day)
        platform_usage.record_platform_day(db, ADMIN_ID, "web", day)
        db.commit()
        rows = {(r.user_id, r.platform) for r in db.query(UserPlatformDayRow).all()}
        assert rows == {(ADMIN_ID, "ios"), (ADMIN_ID, "web")}
        db.close()


def test_mcp_tool_call_records_mcp_platform(client, db_session):
    token = client.post(
        "/api/admin/agents", json={"name": "MCP Agent"}, cookies=_admin_cookie(),
    ).json()
    resp = client.post(
        "/api/mcp/tool-calls", json={"tool": "get_briefing"},
        headers={"Authorization": f"Bearer {token['token']}"},
    )
    assert resp.status_code == 204
    db = db_session()
    assert [(r.user_id, r.platform) for r in db.query(UserPlatformDayRow).all()] == [
        (token["user_id"], "mcp"),
    ]
    db.close()


def test_activity_platform_rows_and_mix(client, db_session):
    today = datetime.now(timezone.utc).date()
    db = db_session()
    for user, platform, age in (
        (ADMIN_ID, "ios", 0), (ADMIN_ID, "ios", 1), (ADMIN_ID, "web", 3),
        (REGULAR_ID, "ios", 1), (REGULAR_ID, "android", 40),
    ):
        db.add(UserPlatformDayRow(user_id=user, platform=platform, day=today - timedelta(days=age)))
    db.commit()
    db.close()

    body = client.get("/api/admin/activity", cookies=_admin_cookie()).json()
    m = body["metrics"]
    order = body["windows"]

    def row(key, part="users"):
        return [m[key][part][w] for w in order]

    assert row("platform_ios") == [1, 2, 2, 2, 2]
    assert row("platform_ios", "count") == [1, 2, 3, 3, 3]
    assert row("platform_web") == [0, 0, 1, 1, 1]
    assert row("platform_android") == [0, 0, 0, 0, 1]
    assert row("platform_mcp") == [0, 0, 0, 0, 0]
    assert body["platform_mix_30d"] == [
        {"platforms": ["ios"], "users": 1},
        {"platforms": ["ios", "web"], "users": 1},
    ]
