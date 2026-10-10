"""Flight-day brief (#753): the T-2h preflight slot always sends an
observed-first notification, whether or not a model updated.

Covers the scheduler wiring (which refresh is the preflight one, one
notification not two, trip legs kept out of coalescing) and the notify gate
(mute and presence honoured, change-only ignored). The email template itself
is tested in ``tests/test_email.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from flyfun_common.db import DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow

from weatherbrief.db.models import BriefingPackRow, DeviceTokenRow, FlightRow
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.notify import badge as badge_mod
from weatherbrief.notify import dispatch as dispatch_mod
from weatherbrief import scheduler as scheduler_mod


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Which refresh is the preflight one
# ---------------------------------------------------------------------------


def _row(**overrides) -> SimpleNamespace:
    defaults = dict(
        id="zz-leg", user_id="u1", trip_id=None,
        departure_time=_utc(2026, 3, 1, 9),
        auto_refresh=True, auto_refresh_hour=None, last_auto_refresh_at=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestIsPreflightSlot:
    def test_before_preflight_is_not(self):
        assert not scheduler_mod._is_preflight_slot(_row(), _utc(2026, 3, 1, 6, 59))

    def test_at_preflight_with_no_refresh_yet(self):
        assert scheduler_mod._is_preflight_slot(_row(), _utc(2026, 3, 1, 7, 0))

    def test_earlier_explicit_d0_refresh_does_not_consume_it(self):
        # A 05Z explicit-hour refresh on D-0 keeps the normal email; the T-2h
        # slot that follows is still the flight-day one.
        row = _row(auto_refresh_hour=5, last_auto_refresh_at=_utc(2026, 3, 1, 5, 2))
        assert not scheduler_mod._is_preflight_slot(row, _utc(2026, 3, 1, 5, 2))
        assert scheduler_mod._is_preflight_slot(row, _utc(2026, 3, 1, 7, 5))

    def test_served_preflight_is_not_again(self):
        row = _row(last_auto_refresh_at=_utc(2026, 3, 1, 7, 1))
        assert not scheduler_mod._is_preflight_slot(row, _utc(2026, 3, 1, 8, 0))

    def test_naive_last_refresh_is_read_as_utc(self):
        row = _row(last_auto_refresh_at=datetime(2026, 3, 1, 7, 1))
        assert not scheduler_mod._is_preflight_slot(row, _utc(2026, 3, 1, 8, 0))

    def test_after_departure_is_not(self):
        assert not scheduler_mod._is_preflight_slot(_row(), _utc(2026, 3, 1, 9, 0))


# ---------------------------------------------------------------------------
# process_auto_refreshes: one notification, never skipped at T-2h
# ---------------------------------------------------------------------------


@pytest.fixture
def cycle(monkeypatch):
    """Run one scheduler cycle over given due rows with the pipeline faked."""
    from weatherbrief.api import trip_refresh

    calls = {"refresh": [], "brief": [], "opened": [], "outcomes": {}}
    gate = {"ran": False}

    def fake_refresh(row, app_state, user_id, *, notify=True, triggered_by="scheduler"):
        calls["refresh"].append((row.id, notify))
        if gate.get("raise"):
            raise RuntimeError("pipeline exploded")
        return gate["ran"]

    def fake_brief(row, app_state, user_id, *, refreshed, present=False):
        calls["brief"].append((row.id, refreshed, present))
        return True

    monkeypatch.setattr(scheduler_mod, "_auto_refresh_one", fake_refresh)
    monkeypatch.setattr(scheduler_mod, "_flight_day_brief", fake_brief)
    monkeypatch.setattr(scheduler_mod, "SessionLocal", MagicMock())
    monkeypatch.setattr(scheduler_mod, "_brief_failures", {})
    monkeypatch.setattr(
        trip_refresh, "open_scheduler_run",
        lambda db, rows: calls["opened"].append([r.id for r in rows]),
    )
    monkeypatch.setattr(trip_refresh, "note_leg_done", lambda *a, **k: None)

    from weatherbrief.api.packs import refresh_registry

    real_mark = refresh_registry.mark_outcome

    def spy_mark(flight_id, status, error=None):
        calls["outcomes"][flight_id] = (status, error)
        real_mark(flight_id, status, error)

    monkeypatch.setattr(refresh_registry, "mark_outcome", spy_mark)

    def run(rows, now):
        monkeypatch.setattr(scheduler_mod, "_find_due_flights", lambda db: rows)

        class _Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(scheduler_mod, "datetime", _Clock)
        import asyncio

        asyncio.run(scheduler_mod.process_auto_refreshes(SimpleNamespace(db_path="/x")))
        return calls

    run.gate = gate
    return run


def test_gate_decline_at_preflight_still_sends_the_brief(cycle):
    """The case that sent nothing before #753."""
    cycle.gate["ran"] = False
    calls = cycle([_row()], _utc(2026, 3, 1, 7, 5))
    assert calls["refresh"] == [("zz-leg", False)]       # ordinary notify off
    assert calls["brief"] == [("zz-leg", False, False)]  # observed-only brief
    status, detail = calls["outcomes"]["zz-leg"]
    assert status == "skipped" and "observed-only flight-day brief" in detail


def test_full_refresh_at_preflight_notifies_once(cycle):
    cycle.gate["ran"] = True
    calls = cycle([_row()], _utc(2026, 3, 1, 7, 5))
    # The ordinary notification is suppressed; the brief is the one.
    assert calls["refresh"] == [("zz-leg", False)]
    assert calls["brief"] == [("zz-leg", True, False)]
    assert calls["outcomes"]["zz-leg"] == ("succeeded", None)


def test_regular_slot_keeps_the_normal_notification(cycle):
    cycle.gate["ran"] = True
    calls = cycle([_row(auto_refresh_hour=5)], _utc(2026, 3, 1, 5, 2))
    assert calls["refresh"] == [("zz-leg", True)]
    assert calls["brief"] == []


def test_pipeline_failure_sends_nothing_and_is_retried(cycle):
    """A raised refresh keeps today's retry: no brief now, and
    ``last_auto_refresh_at`` is not written, so the next cycle tries again."""
    cycle.gate["raise"] = True
    calls = cycle([_row()], _utc(2026, 3, 1, 7, 5))
    assert calls["brief"] == []
    assert calls["outcomes"]["zz-leg"][0] == "failed"


def test_a_failing_brief_is_retried_then_consumes_the_slot(cycle, monkeypatch):
    """A failed brief leaves the slot open for a bounded number of cycles, then
    marks it done: a brief that went out before failing cannot repeat every
    cycle until departure."""
    def boom(*a, **k):
        raise RuntimeError("commit failed after send")

    monkeypatch.setattr(scheduler_mod, "_flight_day_brief", boom)
    cycle.gate["ran"] = False
    for _ in range(scheduler_mod._BRIEF_MAX_RETRIES):
        calls = cycle([_row()], _utc(2026, 3, 1, 7, 5))
        status, detail = calls["outcomes"]["zz-leg"]
        assert status == "failed" and "retrying" in detail
    calls = cycle([_row()], _utc(2026, 3, 1, 7, 25))
    assert calls["outcomes"]["zz-leg"][0] == "skipped"
    assert scheduler_mod._brief_failures == {}


def test_preflight_legs_stay_out_of_trip_coalescing(cycle):
    """Each leg gets its own brief at its own T-2h; trip-mates pulled in at
    the same time keep the coalesced notification."""
    cycle.gate["ran"] = True
    leg1 = _row(id="zz-leg1", trip_id="zz-trip")
    leg2 = _row(id="zz-leg2", trip_id="zz-trip", departure_time=_utc(2026, 3, 1, 15))
    leg3 = _row(id="zz-leg3", trip_id="zz-trip", departure_time=_utc(2026, 3, 2, 9))
    calls = cycle([leg1, leg2, leg3], _utc(2026, 3, 1, 7, 5))
    assert calls["opened"] == [["zz-leg2", "zz-leg3"]]
    assert calls["brief"] == [("zz-leg1", True, False)]
    assert ("zz-leg2", True) in calls["refresh"] and ("zz-leg3", True) in calls["refresh"]


# ---------------------------------------------------------------------------
# _auto_refresh_one(notify=False)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("notify", [True, False])
@patch("weatherbrief.api.packs._notify_refresh_complete")
@patch("weatherbrief.api.packs._finalize_refresh")
@patch("weatherbrief.api.packs._prepare_refresh")
@patch("weatherbrief.pipeline.execute_briefing")
@patch("weatherbrief.storage.flights.list_packs", return_value=[])
@patch("weatherbrief.storage.flights._row_to_flight")
@patch("weatherbrief.scheduler.SessionLocal")
def test_auto_refresh_one_notify_flag(
    mock_session, mock_row_to_flight, mock_list, mock_exec, mock_prepare,
    mock_finalize, mock_notify, notify,
):
    mock_session.return_value = MagicMock()
    mock_row_to_flight.return_value = SimpleNamespace(
        departure_time=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    mock_prepare.return_value = (
        MagicMock(), datetime.now(timezone.utc), "/tmp/pack", MagicMock(), {}, None,
    )
    ran = scheduler_mod._auto_refresh_one(
        _row(), SimpleNamespace(db_path="/x"), "u1", notify=notify,
    )
    assert ran is True
    assert mock_notify.called is notify


# ---------------------------------------------------------------------------
# _flight_day_brief: observed refresh on the latest pack, then notify
# ---------------------------------------------------------------------------


def test_flight_day_brief_refreshes_observed_on_latest_pack(tmp_path, monkeypatch):
    pack_dir = tmp_path / "zz-leg" / "p1"
    pack_dir.mkdir(parents=True)
    latest = BriefingPackMeta(
        flight_id="zz-leg", fetch_timestamp=_utc(2026, 3, 1, 6, 40), days_out=0,
        assessment="AMBER", artifact_path=str(pack_dir),
    )
    flight = SimpleNamespace(id="zz-leg", trip_id=None, notify_override="notify")
    session = MagicMock()
    monkeypatch.setattr(scheduler_mod, "SessionLocal", lambda: session)
    monkeypatch.setenv("WB_LIVE_HIGHLIGHT", "0")

    with patch("weatherbrief.storage.flights.list_packs", return_value=[latest]), \
         patch("weatherbrief.storage.flights._row_to_flight", return_value=flight), \
         patch("weatherbrief.api.packs._profile_cloud_source", return_value=None), \
         patch("weatherbrief.tasks.route_weather.run_realtime_refresh",
               side_effect=RuntimeError("METAR source down")) as mock_rt, \
         patch("weatherbrief.tasks.live_highlight.highlight_enabled", return_value=False), \
         patch("weatherbrief.tasks.live_layer.live_summary", return_value={"changes": []}), \
         patch("weatherbrief.notify.dispatch.notify_flight_day", return_value=True) as mock_notify:
        sent = scheduler_mod._flight_day_brief(
            _row(), SimpleNamespace(db_path="/airports.db"), "u1", refreshed=False,
        )

    # Anchored to the pack the email describes.
    assert mock_rt.call_args.kwargs["pack_timestamp"] == "2026-03-01T06:40:00+00:00"
    assert mock_rt.call_args.kwargs["flight_id"] == "zz-leg"
    # A failed observed refresh still sends, from whatever layer is stored.
    assert sent is True
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["refreshed"] is False and kwargs["live"] == {"changes": []}
    assert mock_notify.call_args.args[2] is latest


def test_flight_day_brief_without_a_pack_sends_nothing(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "SessionLocal", lambda: MagicMock())
    with patch("weatherbrief.storage.flights.list_packs", return_value=[]), \
         patch("weatherbrief.storage.flights._row_to_flight"), \
         patch("weatherbrief.notify.dispatch.notify_flight_day") as mock_notify:
        assert scheduler_mod._flight_day_brief(
            _row(), SimpleNamespace(db_path="/x"), "u1", refreshed=False,
        ) is False
    mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# notify_flight_day: the gate
# ---------------------------------------------------------------------------


def _add_flight(db, flight_id="zz-f1"):
    db.add(FlightRow(
        id=flight_id, user_id=DEV_USER_ID, route_name="EGTK-LFAT",
        waypoints_json='["EGTK", "LFAT"]', departure_time=_utc(2026, 7, 10, 12),
    ))
    db.flush()


def _add_pack(db, ts, assessment, artifact_path=""):
    db.add(BriefingPackRow(
        flight_id="zz-f1", fetch_timestamp=ts, days_out=0, assessment=assessment,
        artifact_path=artifact_path,
    ))
    db.flush()


def _flight(notify_override="default"):
    return Flight(
        id="zz-f1", user_id=DEV_USER_ID, route_name="EGTK-LFAT", waypoints=["EGTK", "LFAT"],
        notify_override=notify_override, departure_time=_utc(2026, 7, 10, 12),
        created_at=_utc(2026, 7, 1),
    )


def _meta(assessment="AMBER", ts=None):
    return BriefingPackMeta(
        flight_id="zz-f1", fetch_timestamp=ts or _utc(2026, 7, 10, 9, 40), days_out=0,
        assessment=assessment,
    )


def _set_prefs(db, **prefs):
    row = db.get(UserPreferencesRow, DEV_USER_ID)
    row.app_prefs_json = json.dumps(prefs)
    db.flush()


@pytest.fixture
def spy(monkeypatch):
    calls = {"email": [], "push": []}
    monkeypatch.setattr(
        dispatch_mod, "_send_flight_day_email",
        lambda db, uid, flight, meta, pack_dir, **kw: calls["email"].append(kw),
    )
    monkeypatch.setattr(
        dispatch_mod, "_send_flight_day_push",
        lambda db, uid, flight, meta, **kw: calls["push"].append(kw),
    )
    return calls


def _notify(db, flight=None, meta=None, **kw):
    if db.get(FlightRow, "zz-f1") is None:
        _add_flight(db)  # the badge row references it
    kw.setdefault("refreshed", False)
    kw.setdefault("live", None)
    return dispatch_mod.notify_flight_day(
        db, flight or _flight(), meta or _meta(), Path("/nonexistent"),
        user_id=DEV_USER_ID, **kw,
    )


def test_change_only_is_ignored(db_session, dev_user, spy):
    """An unchanged briefing under the default change-only pref still sends:
    the brief is the point, not news of a change."""
    _set_prefs(db_session, notify_scope="all", notify_change_only=True, notify_push=True)
    _add_flight(db_session)
    _add_pack(db_session, _utc(2026, 7, 9, 18), "AMBER")
    _add_pack(db_session, _utc(2026, 7, 10, 9, 40), "AMBER")
    assert _notify(db_session) is True
    assert len(spy["email"]) == 1 and len(spy["push"]) == 1


def test_mute_is_honoured(db_session, dev_user, spy):
    assert _notify(db_session, flight=_flight("mute")) is False
    assert spy == {"email": [], "push": []}


def test_presence_is_honoured(db_session, dev_user, spy):
    assert _notify(db_session, flight=_flight("notify"), present=True) is False
    assert spy == {"email": [], "push": []}


def test_scope_off_without_override_is_honoured(db_session, dev_user, spy):
    _set_prefs(db_session, notify_scope="off")
    assert _notify(db_session) is False
    assert _notify(db_session, flight=_flight("notify")) is True


def test_channels_follow_prefs(db_session, dev_user, spy):
    _set_prefs(db_session, notify_scope="all", notify_email=False, notify_push=True)
    _notify(db_session)
    assert spy["email"] == [] and len(spy["push"]) == 1


def test_device_count_reaches_the_email(db_session, dev_user, spy):
    _notify(db_session, flight=_flight("notify"))
    assert spy["email"][0]["has_device"] is False
    db_session.add(DeviceTokenRow(token="zz-token", environment="sandbox", user_id=DEV_USER_ID))
    db_session.flush()
    _notify(db_session, flight=_flight("notify"))
    assert spy["email"][1]["has_device"] is True


def test_unavailable_still_sends_but_leaves_the_badge(db_session, dev_user, spy):
    _notify(db_session, flight=_flight("notify"), meta=_meta("UNAVAILABLE"))
    db_session.flush()
    assert len(spy["email"]) == 1
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 0


def test_badge_lights_for_an_unseen_pack(db_session, dev_user, spy):
    _notify(db_session, flight=_flight("notify"))
    db_session.flush()
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


# ---------------------------------------------------------------------------
# flight_day_since
# ---------------------------------------------------------------------------


def _advisories(tmp: Path, name: str, vmc: str, icing: str) -> Path:
    d = tmp / name
    d.mkdir()
    (d / "route_advisories.json").write_text(json.dumps({
        "advisories": [
            {"advisory_id": "vmc_cruise", "aggregate_status": vmc},
            {"advisory_id": "icing_escape", "aggregate_status": icing},
        ],
        "catalog": [
            {"id": "vmc_cruise", "name": "VMC at Cruise"},
            {"id": "icing_escape", "name": "Icing Escape"},
        ],
    }))
    return d


def test_since_without_a_new_pack(db_session, dev_user):
    since = dispatch_mod.flight_day_since(
        db_session, "zz-f1", _meta(), Path("/nonexistent"), refreshed=False,
    )
    assert since.refreshed is False and since.prior_briefing_at is None


def test_since_diffs_grade_and_advisories_against_the_prior_pack(db_session, dev_user, tmp_path):
    _add_flight(db_session)
    old = _advisories(tmp_path, "old", vmc="green", icing="green")
    new = _advisories(tmp_path, "new", vmc="amber", icing="green")
    _add_pack(db_session, _utc(2026, 7, 9, 18), "GREEN", artifact_path=str(old))
    _add_pack(db_session, _utc(2026, 7, 10, 9, 40), "AMBER", artifact_path=str(new))
    since = dispatch_mod.flight_day_since(db_session, "zz-f1", _meta(), new, refreshed=True)
    assert since.prior_assessment == "GREEN" and since.assessment == "AMBER"
    assert [(c.name, c.from_status, c.to_status) for c in since.advisory_changes] == [
        ("VMC at Cruise", "green", "amber"),
    ]


def test_since_first_briefing_has_no_prior(db_session, dev_user, tmp_path):
    _add_flight(db_session)
    new = _advisories(tmp_path, "new", vmc="amber", icing="green")
    _add_pack(db_session, _utc(2026, 7, 10, 9, 40), "AMBER", artifact_path=str(new))
    since = dispatch_mod.flight_day_since(db_session, "zz-f1", _meta(), new, refreshed=True)
    assert since.prior_briefing_at is None and since.advisory_changes == []


# ---------------------------------------------------------------------------
# The Email button on flight day
# ---------------------------------------------------------------------------


def _fd(departure=_utc(2026, 7, 10, 12), duration=1.5):
    return Flight(
        id="zz-f1", user_id=DEV_USER_ID, route_name="EGTK-LFAT", waypoints=["EGTK", "LFAT"],
        departure_time=departure, flight_duration_hours=duration, created_at=_utc(2026, 7, 1),
    )


class TestFlightDayEmailApplies:
    def test_d0_before_departure(self):
        assert dispatch_mod.flight_day_email_applies(_fd(), _meta(), _utc(2026, 7, 10, 9))

    def test_d0_until_arrival_plus_window(self, monkeypatch):
        monkeypatch.setenv("WB_LIVE_WINDOW_AFTER_H", "1")
        # departure 12Z + 1.5 h + 1 h → 14:30Z
        assert dispatch_mod.flight_day_email_applies(_fd(), _meta(), _utc(2026, 7, 10, 14, 29))
        assert not dispatch_mod.flight_day_email_applies(_fd(), _meta(), _utc(2026, 7, 10, 14, 30))

    def test_not_before_d0(self):
        meta = BriefingPackMeta(
            flight_id="zz-f1", fetch_timestamp=_utc(2026, 7, 9, 9), days_out=1, assessment="GREEN",
        )
        assert not dispatch_mod.flight_day_email_applies(_fd(), meta, _utc(2026, 7, 9, 9))


def test_email_button_sends_the_flight_day_format(db_session, dev_user, tmp_path):
    _add_flight(db_session)
    db_session.add(DeviceTokenRow(token="zz-token", environment="sandbox", user_id=DEV_USER_ID))
    db_session.flush()
    with patch("weatherbrief.notify.email.send_flight_day_email") as mock_send, \
         patch("weatherbrief.tasks.live_layer.live_summary", return_value={"changes": []}):
        dispatch_mod.send_flight_day_email_on_request(
            db_session, _fd(), _meta(), tmp_path,
            recipients=["pilot@example.com"], recipient_user_id=DEV_USER_ID,
            base_url="https://weather.example.com",
        )
    kwargs = mock_send.call_args.kwargs
    assert mock_send.call_args.args[0] == ["pilot@example.com"]
    assert kwargs["has_device"] is True
    assert kwargs["live"] == {"changes": []}
    assert kwargs["since"].refreshed is True


def test_since_dates_a_naive_prior_from_sqlite():
    from weatherbrief.notify.email import FlightDaySince, _since_lines

    since = FlightDaySince(
        refreshed=True, briefing_at=_utc(2026, 7, 10, 0, 30),
        prior_briefing_at=datetime(2026, 7, 9, 23, 50),  # naive, as SQLite returns it
        prior_assessment="GREEN", assessment="GREEN",
    )
    assert "previous 09 Jul 23:50Z" in _since_lines(since)[0]
