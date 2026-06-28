"""Tests for the slim airports-DB download endpoint (iOS autocomplete source).

The iOS app downloads a slimmed airports SQLite here and refreshes it via ETag
when the source nav DB changes. See ``weatherbrief.api.nav``.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient
from flyfun_common.db import DEV_USER_ID, current_user_id

from weatherbrief.api.app import create_app


def _make_nav_db(path: str) -> None:
    """Build a minimal nav DB with the columns KnownAirports reads."""
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE airports (
            icao_code TEXT NOT NULL PRIMARY KEY,
            name TEXT,
            type TEXT,
            latitude_deg REAL,
            longitude_deg REAL,
            elevation_ft REAL
        )
        """
    )
    con.execute(
        "CREATE TABLE runways (id INTEGER PRIMARY KEY, airport_icao TEXT NOT NULL, le_ident TEXT)"
    )
    # Tables the slim build must drop.
    con.execute("CREATE TABLE waypoints (ident TEXT)")
    con.execute("CREATE TABLE procedures (airport_icao TEXT)")
    con.executemany(
        "INSERT INTO airports VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("EGTF", "Fairoaks", "small_airport", 51.35, -0.56, 80.0),
            ("LFQA", "Reims", "small_airport", 49.31, 4.05, 312.0),
        ],
    )
    con.execute("INSERT INTO runways VALUES (1, 'EGTF', '06')")
    con.commit()
    con.close()


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    nav_db = tmp_path / "nav.db"
    _make_nav_db(str(nav_db))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    monkeypatch.setenv("AIRPORTS_DB", str(nav_db))
    app = create_app()
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


def test_download_returns_slim_sqlite(client, tmp_path):
    resp = client.get("/api/nav/airports-db")
    assert resp.status_code == 200
    assert resp.headers.get("ETag")
    assert len(resp.content) > 0

    # The payload is a real SQLite with only airports + runways (no waypoints).
    out = tmp_path / "downloaded.db"
    out.write_bytes(resp.content)
    con = sqlite3.connect(str(out))
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "airports" in tables
    assert "runways" in tables
    assert "waypoints" not in tables
    assert "procedures" not in tables
    icaos = {r[0] for r in con.execute("SELECT icao_code FROM airports")}
    assert icaos == {"EGTF", "LFQA"}
    con.close()


def test_if_none_match_returns_304(client):
    first = client.get("/api/nav/airports-db")
    etag = first.headers["ETag"]
    second = client.get("/api/nav/airports-db", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["ETag"] == etag
