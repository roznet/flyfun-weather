"""scripts/ops/merge_users.py: admin merge of two accounts of one pilot."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from weatherbrief.db.models import (
    ApiTokenRow,
    Base,
    BriefingPackRow,
    FlightBriefingSeenRow,
    FlightProfileRow,
    FlightRow,
    FlightSubscriptionRow,
    UserAircraftRow,
    UserPreferencesRow,
    UserRow,
)

_SPEC = importlib.util.spec_from_file_location(
    "merge_users", Path(__file__).resolve().parents[1] / "scripts/ops/merge_users.py"
)
mu = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mu)

SRC, DST = "u-email", "u-google"


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/merge.db")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


def _user(db, uid, provider, created_day):
    db.add(UserRow(id=uid, provider=provider, provider_sub=f"sub-{uid}",
                   email="zz.pilot@gmail.com", display_name="ZZ Pilot", approved=True,
                   created_at=datetime(2026, 8, created_day, tzinfo=timezone.utc)))


def _flight(db, fid, uid, data_dir: Path):
    db.add(FlightRow(id=fid, user_id=uid, route_name="EGTF-LFAT",
                     waypoints_json='["EGTF", "LFAT"]',
                     departure_time=datetime(2026, 10, 12, 9, tzinfo=timezone.utc)))
    db.flush()
    pack_dir = data_dir / "packs" / uid / fid / "2026-10-10T06-00-00p00-00"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.json").write_text("{}")
    db.add(BriefingPackRow(flight_id=fid, days_out=2, artifact_path=str(pack_dir),
                           fetch_timestamp=datetime(2026, 10, 10, 6, tzinfo=timezone.utc)))


@pytest.fixture
def two_accounts(db, tmp_path):
    """An email account and a Google account of one pilot, with clashes."""
    data_dir = tmp_path / "data"
    _user(db, SRC, "email", 1)
    _user(db, DST, "google", 5)
    db.flush()
    for i in range(3):
        _flight(db, f"src-f{i}", SRC, data_dir)
    _flight(db, "dst-f0", DST, data_dir)
    db.add(UserPreferencesRow(user_id=SRC, encrypted_creds_json="enc-src",
                              app_prefs_json='{"a": 1, "b": 1}'))
    db.add(UserPreferencesRow(user_id=DST, encrypted_creds_json="",
                              app_prefs_json='{"b": 2}'))
    # Both saw dst-f0: a unique (user_id, flight_id) clash.
    for uid in (SRC, DST):
        db.add(FlightBriefingSeenRow(user_id=uid, flight_id="dst-f0"))
    # The Google account subscribed to the email account's flight.
    db.add(FlightSubscriptionRow(user_id=DST, flight_id="src-f0"))
    db.add(ApiTokenRow(user_id=SRC, token_hash="h" * 64, name="mcp"))
    db.commit()
    return data_dir


def _count(db, model, **kw):
    return db.query(model).filter_by(**kw).count()


def test_pick_survivor_prefers_oauth_then_flights_then_older():
    email = {"provider": "email", "flights": 14, "created_at": "2026-08-01"}
    google = {"provider": "google", "flights": 2, "created_at": "2026-09-05"}
    assert mu.pick_survivor([email, google]) is google
    apple_more = {"provider": "apple", "flights": 5, "created_at": "2026-09-01"}
    assert mu.pick_survivor([google, apple_more]) is apple_more
    apple_tie = {"provider": "apple", "flights": 2, "created_at": "2026-08-01"}
    assert mu.pick_survivor([google, apple_tie]) is apple_tie


def test_dry_run_changes_nothing(db, two_accounts):
    report = mu.merge(db, SRC, DST, two_accounts, apply=False)

    assert any("DRY RUN" in line for line in report)
    assert any("flights: 3 moved" in line for line in report)
    assert any("pack dirs: 3 to move" in line for line in report)
    db.expire_all()
    assert _count(db, FlightRow, user_id=SRC) == 3
    assert db.get(UserRow, SRC) is not None
    assert (two_accounts / "packs" / SRC / "src-f0").is_dir()


def test_apply_moves_everything(db, two_accounts):
    report = mu.merge(db, SRC, DST, two_accounts, apply=True)
    db.expire_all()

    assert "APPLIED." in report
    assert db.get(UserRow, SRC) is None
    assert _count(db, FlightRow, user_id=DST) == 4
    assert _count(db, ApiTokenRow, user_id=DST) == 1
    # Unique-key clash: the survivor's row stays, the absorbed one goes.
    assert _count(db, FlightBriefingSeenRow, user_id=DST, flight_id="dst-f0") == 1
    assert _count(db, FlightBriefingSeenRow, user_id=SRC) == 0
    # No subscription to one's own flight.
    assert _count(db, FlightSubscriptionRow) == 0
    # Preferences: empty survivor fields filled, survivor keys win.
    prefs = db.get(UserPreferencesRow, DST)
    assert prefs.encrypted_creds_json == "enc-src"
    assert prefs.app_prefs_json in ('{"a": 1, "b": 2}', '{"b": 2, "a": 1}')
    assert db.get(UserPreferencesRow, SRC) is None
    # Pack directories and stored paths follow.
    assert not (two_accounts / "packs" / SRC).exists()
    for pack in db.query(BriefingPackRow).all():
        assert f"/packs/{DST}/" in pack.artifact_path
        assert Path(pack.artifact_path, "briefing.json").exists()


def test_survivor_keeps_the_only_default(db, two_accounts):
    """Both accounts seeded a default profile; ensure_default_profile needs one."""
    for uid in (SRC, DST):
        db.add(FlightProfileRow(user_id=uid, name="IFR FIKI", is_default=True,
                                system_template_key="ifr_fiki"))
        db.add(FlightProfileRow(user_id=uid, name="VFR Only", system_template_key="vfr_only"))
    # Only the absorbed account has an aircraft: its default carries over.
    db.add(UserAircraftRow(user_id=SRC, icao_type="SR22", is_default=True))
    db.commit()

    report = mu.merge(db, SRC, DST, two_accounts, apply=True)
    db.expire_all()

    assert any("flight_profiles: 2 moved, 1 no longer default" in line for line in report)
    defaults = db.query(FlightProfileRow).filter_by(user_id=DST, is_default=True).all()
    assert len(defaults) == 1
    assert _count(db, FlightProfileRow, user_id=DST) == 4
    assert _count(db, UserAircraftRow, user_id=DST, is_default=True) == 1


def test_existing_target_dir_refuses_before_any_change(db, two_accounts):
    (two_accounts / "packs" / DST / "src-f1").mkdir(parents=True)

    with pytest.raises(SystemExit, match="already exists"):
        mu.merge(db, SRC, DST, two_accounts, apply=True)
    db.expire_all()
    assert db.get(UserRow, SRC) is not None
    assert _count(db, FlightRow, user_id=SRC) == 3


def test_run_by_email_picks_google_and_reports(db, two_accounts, capsys):
    args = mu.parse(["ZZ.Pilot@gmail.com"])

    assert mu.run(args, db, two_accounts) == 0
    out = capsys.readouterr().out
    assert f"merge {SRC} (email) -> {DST} (google)" in out
    assert "DRY RUN" in out


def test_run_by_email_needs_two_accounts(db, two_accounts):
    assert mu.run(mu.parse(["nobody@example.com"]), db, two_accounts) == 1
