"""API test for POST .../advisories/preview — the non-persisting draft preview (#387, slice 4).

The preview endpoint must (a) return a fresh manifest computed under explicit
draft overrides and (b) never write anything into the pack dir (unlike
recalculate, which persists route_advisories.json + route_fronts.json).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.storage.flights import pack_dir_for, save_flight, save_pack_meta

_NOW = datetime(2026, 5, 31, 6, 0, tzinfo=timezone.utc)
_DEP = _NOW + timedelta(days=2)


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
    monkeypatch.setenv("ENVIRONMENT", "production")
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


def _route_analyses() -> dict:
    """A minimal but loadable route_analyses manifest (one point)."""
    return {
        "route_name": "EGKB-LFAT",
        "target_date": "2026-05-31",
        "departure_time": "2026-05-31T06:00:00+00:00",
        "flight_duration_hours": 2.0,
        "total_distance_nm": 160.0,
        "cruise_altitude_ft": 5000,
        "models": ["ecmwf", "gfs", "icon"],
        "analyses": [
            {
                "point_index": 0,
                "lat": 47.0,
                "lon": -1.5,
                "distance_from_origin_nm": 0.0,
                "interpolated_time": "2026-05-31T06:00:00+00:00",
                "forecast_hour": "2026-05-31T06:00:00+00:00",
                "track_deg": 90.0,
                "model_divergence": [],
            }
        ],
    }


def _make_profile(session, *, name: str, settings: dict, user_id: str = DEV_USER_ID) -> int:
    """Persist a flight profile and return its id."""
    from weatherbrief.db.models import FlightProfileRow

    row = FlightProfileRow(
        user_id=user_id, name=name, is_default=False, settings_json=json.dumps(settings),
    )
    session.add(row)
    session.flush()
    return row.id


def _crosswind_profile_settings(red_kt: float, cloud_method: str = "square_nwp") -> dict:
    return {
        "cloud_method": cloud_method,
        "advisories": {
            "enabled": {"airport_wind": True},
            "params": {"airport_wind": {"crosswind_red_kt": red_kt}},
        },
    }


def _seed(app_db, profile_id: int | None = None) -> tuple[str, str, Path]:
    fid = "egkb_lfat-" + hashlib.sha256(b"preview").hexdigest()[:4]
    session = app_db()
    flight = Flight(
        id=fid, user_id=DEV_USER_ID, route_name="egkb_lfat",
        waypoints=["EGKB", "LFAT"], departure_time=_DEP,
        cruise_altitude_ft=5000, flight_ceiling_ft=18000,
        flight_duration_hours=2.0, created_at=_NOW - timedelta(days=1),
        profile_id=profile_id,
    )
    save_flight(session, flight, DEV_USER_ID)
    meta = BriefingPackMeta(
        flight_id=flight.id, fetch_timestamp=_NOW - timedelta(hours=1),
        days_out=2, has_gramet=False, has_skewt=False, has_digest=False,
        assessment="AMBER", assessment_reason="test",
    )
    save_pack_meta(session, meta)
    session.commit()
    session.close()

    ts = meta.fetch_timestamp.isoformat()
    pack_dir = Path(pack_dir_for(DEV_USER_ID, flight.id, ts))
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "route_analyses.json").write_text(json.dumps(_route_analyses()))
    return flight.id, ts, pack_dir


def test_preview_returns_manifest_without_persisting(client, app_db):
    """Preview returns a manifest and writes nothing into the pack dir."""
    flight_id, ts, pack_dir = _seed(app_db)
    assert not (pack_dir / "route_advisories.json").exists()

    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={"enabled": {"airport_wind": True}, "params": {}, "aggregation": "majority"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "manifest" in body
    assert isinstance(body["manifest"]["advisories"], list)

    # The core invariant: no artifact was written.
    assert not (pack_dir / "route_advisories.json").exists()
    assert not (pack_dir / "route_fronts.json").exists()


def test_preview_applies_param_override(client, app_db):
    """A draft param override shows up in the previewed advisory's parameters_used."""
    flight_id, ts, pack_dir = _seed(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={
            "enabled": {"airport_wind": True},
            "params": {"airport_wind": {"crosswind_red_kt": 33}},
        },
    )
    assert resp.status_code == 200, resp.text
    advisories = {a["advisory_id"]: a for a in resp.json()["manifest"]["advisories"]}
    assert "airport_wind" in advisories
    assert advisories["airport_wind"]["parameters_used"]["crosswind_red_kt"] == 33
    assert not (pack_dir / "route_advisories.json").exists()


def test_preview_empty_body_uses_saved_settings(client, app_db):
    """A bare body previews saved settings (baseline for the diff) and still persists nothing."""
    flight_id, ts, pack_dir = _seed(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={},
    )
    assert resp.status_code == 200, resp.text
    assert not (pack_dir / "route_advisories.json").exists()


def test_preview_rejects_invalid_aggregation(client, app_db):
    """A typo'd aggregation fails validation (422) rather than being silently ignored."""
    flight_id, ts, _ = _seed(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={"aggregation": "bogus"},
    )
    assert resp.status_code == 422, resp.text


def _seed_two_profiles(app_db) -> tuple[str, str, int, int]:
    """A flight bound to profile A, plus an unrelated profile B (the one being edited)."""
    session = app_db()
    bound_id = _make_profile(session, name="Bound", settings=_crosswind_profile_settings(22))
    edited_id = _make_profile(
        session, name="Edited",
        settings=_crosswind_profile_settings(44, cloud_method="square_dd"),
    )
    session.commit()
    session.close()
    flight_id, ts, _ = _seed(app_db, profile_id=bound_id)
    return flight_id, ts, bound_id, edited_id


def test_preview_scopes_settings_to_the_edited_profile(client, app_db):
    """Settings resolve from ``profile_id`` — the profile being *edited* — not the
    profile bound to the flight whose pack supplies the weather (#390 review).

    The settings page edits an arbitrary profile; the preview target is just "the
    pilot's most recent flight with a pack", so for a multi-profile pilot the two
    routinely differ. Grading under the flight's profile silently previews the
    wrong settings.
    """
    flight_id, ts, _bound_id, edited_id = _seed_two_profiles(app_db)

    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={"profile_id": edited_id},
    )
    assert resp.status_code == 200, resp.text
    advisories = {a["advisory_id"]: a for a in resp.json()["manifest"]["advisories"]}
    # 44 = the edited profile's saved value; 22 would mean it graded under the
    # flight's own (bound) profile.
    assert advisories["airport_wind"]["parameters_used"]["crosswind_red_kt"] == 44


def test_preview_without_profile_id_falls_back_to_the_flight_profile(client, app_db):
    """Omitting profile_id keeps the pre-#390 behaviour: the flight's own profile."""
    flight_id, ts, _bound_id, _edited_id = _seed_two_profiles(app_db)

    resp = client.post(f"/api/flights/{flight_id}/packs/{ts}/advisories/preview", json={})
    assert resp.status_code == 200, resp.text
    advisories = {a["advisory_id"]: a for a in resp.json()["manifest"]["advisories"]}
    assert advisories["airport_wind"]["parameters_used"]["crosswind_red_kt"] == 22


def test_preview_dense_at_default_grades_identically_to_sparse(client, app_db):
    """No-op guard (#403 Part B): a *dense* profile whose engine methods and
    advisory params are all at their defaults grades **byte-identically** to a
    *sparse* profile that omits them — the property that proves pruning a value at
    its default never changes a grade (so any grading change could only come from
    the Part A default flip, never from Part B).
    """
    from weatherbrief.analysis.advisories import get_catalog
    from weatherbrief.analysis.advisories.engine_methods import ENGINE_METHOD_DEFAULTS

    cat = {e.id: e for e in get_catalog()}
    aw_default = next(p.default for p in cat["airport_wind"].parameters if p.key == "crosswind_red_kt")

    enabled = {"airport_wind": True, "vmc_cruise": True, "cloud_top": True}
    dense = {
        # Engine methods all stated at their declared defaults …
        **ENGINE_METHOD_DEFAULTS,
        # … and a param frozen at its catalog default.
        "advisories": {"enabled": enabled, "params": {"airport_wind": {"crosswind_red_kt": aw_default}}},
    }
    sparse = {"advisories": {"enabled": enabled}}  # no engine keys, no params

    session = app_db()
    dense_id = _make_profile(session, name="Dense", settings=dense)
    sparse_id = _make_profile(session, name="Sparse", settings=sparse)
    session.commit()
    session.close()

    flight_id, ts, _ = _seed(app_db)

    def graded(pid: int):
        r = client.post(
            f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
            json={"profile_id": pid},
        )
        assert r.status_code == 200, r.text
        return {
            a["advisory_id"]: (a["aggregate_status"], a["parameters_used"])
            for a in r.json()["manifest"]["advisories"]
        }

    assert graded(dense_id) == graded(sparse_id)


def test_preview_rejects_another_users_profile(client, app_db):
    """A profile the caller doesn't own 404s rather than silently previewing defaults."""
    session = app_db()
    session.add(UserRow(
        id="other-user", provider="local", provider_sub="other",
        email="other@localhost", display_name="Other", approved=True,
    ))
    session.flush()
    foreign_id = _make_profile(
        session, name="Theirs", settings=_crosswind_profile_settings(99),
        user_id="other-user",
    )
    session.commit()
    session.close()

    flight_id, ts, _ = _seed(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={"profile_id": foreign_id},
    )
    assert resp.status_code == 404, resp.text


def test_preview_threads_draft_engine_settings(client, app_db, monkeypatch):
    """Draft engine axes (models, icing/cloud/convective method) reach the evaluator.

    They live on the same settings form and change how identical weather is graded,
    so a preview that ignored them would predict an outcome the Save won't produce.
    Fields *absent* from the body still fall back to the edited profile's saved
    value; an explicit ``null`` is a draft value ("no model selection ⇒ defaults"),
    not "unset".
    """
    from weatherbrief.tasks import advise

    captured: dict = {}
    real = advise.run_advisories_from_pack

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(advise, "run_advisories_from_pack", spy)

    flight_id, ts, _bound_id, edited_id = _seed_two_profiles(app_db)
    resp = client.post(
        f"/api/flights/{flight_id}/packs/{ts}/advisories/preview",
        json={
            "profile_id": edited_id,
            "icing_method": "ogimet_nwp",
            "advisory_models": None,
        },
    )
    assert resp.status_code == 200, resp.text
    assert captured["icing_method"] == "ogimet_nwp"       # draft override
    assert captured["advisory_models"] is None            # explicit null, not the saved value
    # absent from body → edited profile's saved value, read through the #410
    # legacy fallback: the seeded ``cloud_method="square_dd"`` reduces to source "dd".
    assert captured["cloud_source"] == "dd"
    assert captured["persist"] is False
