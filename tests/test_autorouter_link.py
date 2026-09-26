"""Autorouter linking through the weatherbrief app (#625).

The flow itself lives in flyfun-common; these check it is wired up here the
way both clients rely on: the iOS app's ticket → autorouter.aero → back to
``flyfunweather://``, and the web picker's ``next`` → back to the flights page.
Runs with production settings, so the session cookie is the real https-only
one the OAuth state rides on.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from flyfun_common import autorouter as autorouter_module
from flyfun_common.db import DEV_USER_ID, current_user_id, optional_user_id
from weatherbrief.api.app import create_app


@pytest.fixture
def linked(monkeypatch):
    """Record tokens instead of storing them; fake Autorouter's token endpoint."""
    stored: dict = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "ar-token"}

    class _FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, data=None):
            stored["redirect_uri"] = data["redirect_uri"]
            return _FakeResponse()

    monkeypatch.setattr(autorouter_module.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(
        autorouter_module, "_store_token",
        lambda db, user_id, token: stored.update(user_id=user_id, token=token["access_token"]),
    )
    return stored


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret-long-enough-for-hs256-signing")
    monkeypatch.setenv("AUTOROUTER_CLIENT_ID", "flyfun_weather")
    monkeypatch.setenv("AUTOROUTER_CLIENT_SECRET", "secret")
    app = create_app()
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    app.dependency_overrides[optional_user_id] = lambda: DEV_USER_ID
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


def _authorize_state(resp) -> str:
    assert resp.status_code == 302, resp.text
    location = resp.headers["location"]
    assert location.startswith("https://www.autorouter.aero/authorize"), location
    query = parse_qs(urlsplit(location).query)
    assert query["redirect_uri"] == ["https://testserver/auth/callback/autorouter"]
    return query["state"][0]


def test_ios_app_links_and_returns_to_the_app(client, linked):
    resp = client.post("/autorouter/link-ticket", json={"scheme": "flyfunweather"})
    assert resp.status_code == 200, resp.text
    url = resp.json()["url"]
    assert url.startswith("https://testserver/autorouter/link?ticket=")

    state = _authorize_state(client.get(url, follow_redirects=False))
    resp = client.get(
        f"/auth/callback/autorouter?code=abc&state={state}", follow_redirects=False
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "flyfunweather://autorouter/callback?status=linked"
    assert linked["user_id"] == DEV_USER_ID
    assert linked["token"] == "ar-token"


def test_web_picker_links_and_returns_to_the_flights_page(client, linked):
    state = _authorize_state(client.get("/autorouter/link?next=/", follow_redirects=False))
    resp = client.get(
        f"/auth/callback/autorouter?code=abc&state={state}", follow_redirects=False
    )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/?autorouter=linked"
    assert linked["user_id"] == DEV_USER_ID
