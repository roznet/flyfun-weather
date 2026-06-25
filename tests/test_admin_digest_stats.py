"""Tests for the admin daily digest data builder — donations + debriefs sections."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from flyfun_common.db.models import DonationRow, UserRow

from weatherbrief.db.models import FlightDebriefRow, FlightRow
from weatherbrief.notify.admin_digest_email import _build_html, _build_plain
from weatherbrief.tasks.admin_digest_stats import get_admin_digest_data

_SERVICE = "flyfun-weather"


def _mk_user(db, user_id="u1", email="pilot@example.com"):
    db.add(UserRow(
        id=user_id, provider="local", provider_sub=user_id,
        email=email, display_name="Pilot", approved=True,
    ))
    db.flush()


def _mk_flight(db, flight_id, user_id="u1", route_name="LFAT-EGKA", dep=None):
    db.add(FlightRow(
        id=flight_id, user_id=user_id, route_name=route_name, waypoints_json="[]",
        departure_time=dep or datetime.now(timezone.utc),
        cruise_altitude_ft=8000, flight_ceiling_ft=18000, flight_duration_hours=2.0,
    ))
    db.flush()


def _mk_debrief(db, flight_id, decision, *, reasons_json=None, outcomes_json=None,
                note=None, created_at=None):
    db.add(FlightDebriefRow(
        flight_id=flight_id, decision=decision,
        reasons_json=reasons_json, outcomes_json=outcomes_json, note=note,
        created_at=created_at or datetime.now(timezone.utc),
        updated_at=created_at or datetime.now(timezone.utc),
    ))
    db.flush()


def _mk_donation(db, amount_usd, *, user_id="u1", provider_ref="pi_1",
                 status="succeeded", currency="USD", amount=None, recurring=False,
                 created_at=None):
    db.add(DonationRow(
        user_id=user_id, service=_SERVICE,
        amount=amount if amount is not None else amount_usd, currency=currency,
        amount_usd=amount_usd, fx_rate=1.0, recurring=recurring, status=status,
        provider="stripe", provider_ref=provider_ref,
        created_at=created_at or datetime.now(timezone.utc),
    ))
    db.flush()


def _window(db):
    now = datetime.now(timezone.utc)
    return get_admin_digest_data(db, now - timedelta(days=1), now + timedelta(minutes=1))


# --- Donations -------------------------------------------------------------


def test_donations_counted_and_totalled(db_session):
    _mk_user(db_session)
    _mk_donation(db_session, 25.0, provider_ref="pi_a")
    _mk_donation(db_session, 10.0, provider_ref="pi_b", recurring=True)

    don = _window(db_session).donations
    assert don.count == 2
    assert don.total_usd == 35.0
    assert {d.donor for d in don.donations} == {"pilot@example.com"}
    assert any(d.recurring for d in don.donations)


def test_donations_exclude_refunded_and_out_of_window(db_session):
    _mk_user(db_session)
    _mk_donation(db_session, 25.0, provider_ref="pi_ok")
    _mk_donation(db_session, 99.0, provider_ref="pi_refund", status="refunded")
    _mk_donation(db_session, 50.0, provider_ref="pi_old",
                 created_at=datetime.now(timezone.utc) - timedelta(days=10))

    don = _window(db_session).donations
    assert don.count == 1
    assert don.total_usd == 25.0


def test_anonymous_donation_labelled(db_session):
    _mk_donation(db_session, 5.0, user_id=None, provider_ref="pi_anon")

    don = _window(db_session).donations
    assert don.count == 1
    assert don.donations[0].donor == "anonymous"


# --- Debriefs --------------------------------------------------------------


def test_debriefs_counts_by_decision(db_session):
    _mk_user(db_session)
    _mk_flight(db_session, "f1")
    _mk_flight(db_session, "f2", route_name="LFMD-LIMJ")
    _mk_flight(db_session, "f3", route_name="EGKB-LFAT")
    _mk_debrief(db_session, "f1", "cancelled", reasons_json='["WIND", "IMC"]',
                note="gusting 30kt")
    _mk_debrief(db_session, "f2", "flown",
                outcomes_json='{"ICE": "worse", "WIND": "consistent"}')
    _mk_debrief(db_session, "f3", "monitoring")

    deb = _window(db_session).debriefs
    assert deb.total_count == 3
    assert deb.flown_count == 1
    assert deb.cancelled_count == 1
    assert deb.monitoring_count == 1


def test_debrief_summaries(db_session):
    _mk_user(db_session)
    _mk_flight(db_session, "f1")
    _mk_flight(db_session, "f2", route_name="LFMD-LIMJ")
    _mk_debrief(db_session, "f1", "cancelled", reasons_json='["WIND", "IMC"]')
    _mk_debrief(db_session, "f2", "flown",
                outcomes_json='{"ICE": "worse", "VIS": "better"}')

    by_route = {d.route_name: d for d in _window(db_session).debriefs.recent}
    assert by_route["LFAT-EGKA"].summary == "WIND, IMC"
    assert "worse: ICE" in by_route["LFMD-LIMJ"].summary
    assert "better: VIS" in by_route["LFMD-LIMJ"].summary


def test_debriefs_recent_capped_at_five_newest_first(db_session):
    _mk_user(db_session)
    base = datetime.now(timezone.utc) - timedelta(hours=12)
    for i in range(7):
        _mk_flight(db_session, f"f{i}", route_name=f"RTE{i}")
        _mk_debrief(db_session, f"f{i}", "flown",
                    outcomes_json='{"WIND": "consistent"}',
                    created_at=base + timedelta(minutes=i))

    deb = _window(db_session).debriefs
    assert deb.total_count == 7
    assert len(deb.recent) == 5
    # newest first → RTE6 then RTE5 ...
    assert deb.recent[0].route_name == "RTE6"
    assert deb.recent[-1].route_name == "RTE2"


# --- End-to-end render (no verification section) ---------------------------


def test_render_has_new_sections_and_no_verification(db_session):
    _mk_user(db_session)
    _mk_flight(db_session, "f1")
    _mk_debrief(db_session, "f1", "cancelled", reasons_json='["WIND"]')
    _mk_donation(db_session, 25.0, provider_ref="pi_a")

    data = _window(db_session)
    h = _build_html(data)
    pl = _build_plain(data)

    assert "Donations" in h and "Flight Debriefs" in h
    assert "DONATIONS" in pl and "FLIGHT DEBRIEFS" in pl
    # verification section fully removed
    assert "Verification" not in h and "VERIFICATION" not in pl
    assert "LFAT-EGKA" in h and "pilot@example.com" in h
