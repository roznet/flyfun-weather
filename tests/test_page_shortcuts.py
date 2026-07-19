"""Tests for extensionless page shortcut URLs.

The app registers a 302 redirect for every ``web/<stem>.html`` page so that a
bare ``/maps`` lands on ``/maps.html``. ``.html`` stays canonical; these are
additive convenience aliases (see ``create_app`` in ``weatherbrief.api.app``).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from weatherbrief.api.app import create_app


def _client() -> TestClient:
    # The shortcut routes are pure (no DB / auth), so a bare app is enough.
    return TestClient(create_app(), raise_server_exceptions=False)


class TestPageShortcuts:
    def test_maps_redirects_to_html(self):
        resp = _client().get("/maps", follow_redirects=False)
        assert resp.status_code == 302, resp.text
        assert resp.headers["location"] == "/maps.html"

    def test_settings_redirects_to_html(self):
        resp = _client().get("/settings", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/settings.html"

    def test_query_string_is_forwarded(self):
        resp = _client().get(
            "/briefing?flight=egtk-lfat-0001&pack=2026-04-30T08:00:00Z",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith("/briefing.html?")
        assert "flight=egtk-lfat-0001" in loc
        assert "pack=2026-04-30T08:00:00Z" in loc

    def test_index_has_no_shortcut(self):
        # "/" is already served by the html=True mount; there is no "/index"
        # shortcut, so it falls through to the static mount and 404s (no
        # ``index`` file at that path).
        resp = _client().get("/index", follow_redirects=False)
        assert resp.status_code != 302

    def test_html_paths_still_served_directly(self):
        # The aliases must not shadow the canonical static files.
        resp = _client().get("/maps.html", follow_redirects=False)
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_unknown_page_is_not_redirected(self):
        resp = _client().get("/definitely-not-a-page", follow_redirects=False)
        assert resp.status_code != 302
