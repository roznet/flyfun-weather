"""Server-side delete-semantics for sparse profile settings (#403 Part B).

``update_profile`` merged with ``dict.update()``, which can never remove a key —
so a client that prunes a value at its default could not shrink an already-dense
profile. The PUT endpoint now uses ``exclude_unset`` + null-means-delete:

* a key the client omits entirely is left untouched (partial-writer safe);
* a key sent explicitly as ``null`` is deleted (follow the default);
* any other value replaces.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.storage.flights import load_profile


@pytest.fixture
def app_db():
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    session.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev", approved=True,
    ))
    session.flush()
    session.add(UserPreferencesRow(user_id=DEV_USER_ID))
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

    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


def _create(client, settings: dict) -> int:
    r = client.post("/api/user/profiles", json={"name": "P", "settings": settings})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _put(client, pid: int, settings: dict):
    r = client.put(f"/api/user/profiles/{pid}", json={"settings": settings})
    assert r.status_code == 200, r.text
    return r


def _raw(app_db, pid: int) -> dict:
    """The *actual* stored settings dict (not the None-filled response model), so
    key ABSENCE is observable."""
    session = app_db()
    try:
        return load_profile(session, pid).settings
    finally:
        session.close()


def test_explicit_null_deletes_a_key(client, app_db):
    """A key sent as null is removed from the stored settings (follow the default)."""
    pid = _create(client, {"icing_method": "ogimet_dd", "cruise_altitude_ft": 6000})
    assert _raw(app_db, pid)["icing_method"] == "ogimet_dd"

    _put(client, pid, {"icing_method": None})
    stored = _raw(app_db, pid)
    assert "icing_method" not in stored          # deleted, not just nulled
    assert stored["cruise_altitude_ft"] == 6000  # sibling untouched


def test_omitted_key_is_untouched_partial_writer_safe(client, app_db):
    """A partial writer that omits keys never wipes unrelated ones."""
    pid = _create(client, {
        "icing_method": "ogimet_dd",
        "cloud_source": "dd",
        "cruise_altitude_ft": 6000,
    })
    # Partial PUT touching only the altitude — the audit's core safety property.
    _put(client, pid, {"cruise_altitude_ft": 7000})
    stored = _raw(app_db, pid)
    assert stored["cruise_altitude_ft"] == 7000
    assert stored["icing_method"] == "ogimet_dd"
    assert stored["cloud_source"] == "dd"


def test_resave_dense_profile_shrinks(client, app_db):
    """Re-saving with the pruned (sparse) payload shrinks an already-dense profile —
    the advisories block is replaced wholesale and the defaulted engine keys are
    deleted. Proves the server can *delete*, which a dict.update() merge cannot."""
    dense = {
        "icing_method": "ogimet_nwp",
        "cloud_source": "nwp",
        "convective_method": "nwp",
        "advisories": {
            "enabled": {"airport_wind": True},
            "params": {"airport_wind": {"crosswind_red_kt": 25}, "vmc_cruise": {"extent_pct_red": 50}},
        },
    }
    pid = _create(client, dense)
    stored = _raw(app_db, pid)
    assert stored["icing_method"] == "ogimet_nwp"
    assert stored["advisories"]["params"] != {}

    # What the pruned client sends: engine keys at default → null; advisories block
    # complete but with its params emptied (all were at default).
    pruned = {
        "icing_method": None,
        "cloud_source": None,
        "convective_method": None,
        "advisories": {"enabled": {"airport_wind": True}, "params": {}},
    }
    _put(client, pid, pruned)

    stored = _raw(app_db, pid)
    assert "icing_method" not in stored
    assert "cloud_source" not in stored
    assert "convective_method" not in stored
    assert stored["advisories"]["params"] == {}


def test_all_default_save_persists_no_engine_keys_or_params(client, app_db):
    """Saving a profile whose engine settings and params are all at default (the
    pruned client sends nulls / empty params) leaves settings_json with no engine
    keys and no advisories.params entries."""
    pid = _create(client, {"cruise_altitude_ft": 5500})
    _put(client, pid, {
        "icing_method": None,
        "cloud_source": None,
        "convective_method": None,
        "advisories": {"enabled": {"cloud_top": True}, "params": {}},
    })
    stored = _raw(app_db, pid)
    assert "icing_method" not in stored
    assert "cloud_source" not in stored
    assert "convective_method" not in stored
    assert stored["advisories"]["params"] == {}


def test_response_reads_cloud_source_directly():
    """Post-#405 every profile is sparse and stores the bare ``cloud_source`` (the
    legacy fused ``cloud_method`` was swept off all profiles by ``alembic 079`` and
    its read-path fallback removed). The response model reads the stored source
    directly: present → reported; absent → ``None`` (client applies the declared
    default)."""
    from weatherbrief.api.profiles import ProfileSettings

    assert ProfileSettings(**{"cloud_source": "dd", "cruise_altitude_ft": 5500}).cloud_source == "dd"
    assert ProfileSettings(**{"cloud_source": "nwp"}).cloud_source == "nwp"
    # No engine key at all → None (client applies the declared default).
    assert ProfileSettings(**{"cruise_altitude_ft": 5000}).cloud_source is None
