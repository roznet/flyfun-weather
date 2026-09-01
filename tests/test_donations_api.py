"""Tests for the donation endpoints: checkout, webhook, /me, /summary.

The donations router is mounted in isolation (not the full ``create_app``) so
these run without the heavy briefing/GRIB stack. Stripe is stubbed at the
flyfun-common boundary; the donation ledger, FX(USD), idempotency, and impact
math are exercised for real.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, optional_user_id, DEV_USER_ID
from flyfun_common.db.models import (
    CostLedgerRow,
    DonationRow,
    UserPreferencesRow,
    UserRow,
)
from flyfun_common.payments.stripe_client import SignatureVerificationError

import weatherbrief.api.donations as donations
from weatherbrief.costs import DEFAULT_CONFIG
from weatherbrief.db.models import BriefingUsageRow, CostConfigRow

SERVICE = "flyfun-weather"
OTHER_USER = "user-2"


@pytest.fixture
def app_db():
    from conftest import make_app_engine

    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(id=DEV_USER_ID, provider="local", provider_sub="dev",
                  email="dev@localhost", display_name="Dev", approved=True))
    s.commit()
    s.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def session_factory(app_db):
    return app_db


@pytest.fixture
def make_client(session_factory):
    """Build a TestClient with overridable auth (viewer + optional viewer)."""

    def _make(viewer: str | None = DEV_USER_ID, optional: str | None = DEV_USER_ID):
        app = FastAPI()
        app.include_router(donations.router, prefix="/api")

        def _override_get_db():
            s = session_factory()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[current_user_id] = lambda: viewer
        app.dependency_overrides[optional_user_id] = lambda: optional
        return TestClient(app, raise_server_exceptions=False)

    return _make


def _seed_economics(session_factory, other_user_briefings: int = 1):
    """Active cost config + briefing ledger rows (2 distinct users).

    ``other_user_briefings`` sets the program's monthly volume, which is what
    the fixed monthly cost is amortized over when a pilot's *true* lifetime cost
    is recomputed (``impact.usage_footprint``). At the default of one briefing
    each, the whole $56/month lands as $28 on every briefing — arithmetically
    right but nothing like prod, so tests that care about a realistic per-pilot
    cost raise it.
    """
    s = session_factory()
    s.add(CostConfigRow(active_from=datetime.now(timezone.utc),
                        config_json=DEFAULT_CONFIG.to_json()))
    detail = json.dumps({"token_cost_usd": 0.05, "storage_cost_usd": 0.01})

    def _row(uid):
        return CostLedgerRow(
            user_id=uid, service=SERVICE, action="briefing", cost=0.1,
            category="briefing", description="Briefing", detail_json=detail,
            created_at=datetime.now(timezone.utc),
        )

    s.add(_row(DEV_USER_ID))
    for _ in range(other_user_briefings):
        s.add(_row(OTHER_USER))
    s.commit()
    s.close()


def _session_obj(provider_ref="pi_123", user_id=DEV_USER_ID, amount_total=2800,
                 currency="usd", mode="payment"):
    """A minimal checkout.session.completed object (USD avoids FX network)."""
    return {
        "id": "cs_test_1",
        "payment_intent": provider_ref,
        "client_reference_id": user_id,
        "metadata": {"service": SERVICE, "user_id": user_id or ""},
        "currency": currency,
        "amount_total": amount_total,
        "mode": mode,
        "payment_status": "paid",
    }


def _event(event_type, obj):
    return {"type": event_type, "data": {"object": obj}}


@pytest.fixture(autouse=True)
def _stub_fx(monkeypatch):
    """Deterministic, offline FX so currency-dependent assertions never hit ECB.

    The default display currency is EUR, so /me and /summary resolve a non-USD
    rate — stub the fetch so tests don't depend on the network.
    """
    from flyfun_common import fx
    fx.clear_cache()
    monkeypatch.setattr(fx, "_fetch_rates", lambda: (
        {"USD": 1.0, "EUR": 0.9, "GBP": 0.8, "CHF": 0.9, "NOK": 10.0,
         "SEK": 10.5, "DKK": 6.7, "PLN": 4.0, "CZK": 23.0, "RON": 4.6},
        "2026-06-15",
    ))
    yield
    fx.clear_cache()


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


class TestCheckout:
    def test_creates_session(self, make_client, monkeypatch):
        captured = {}

        class _Sess:
            url = "https://checkout.stripe.test/sess"

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return _Sess()

        monkeypatch.setattr(donations, "create_checkout_session", _fake_create)
        client = make_client()
        r = client.post("/api/donations/checkout", json={"amount": 25, "currency": "EUR"})
        assert r.status_code == 200, r.text
        assert r.json()["url"] == "https://checkout.stripe.test/sess"
        assert captured["amount"] == 25
        assert captured["currency"] == "EUR"
        assert captured["service"] == SERVICE
        assert captured["user_id"] == DEV_USER_ID
        # Logged-in donor → Checkout email is pre-filled from the account.
        assert captured["customer_email"] == "dev@localhost"
        # Success URL carries the Stripe session-id template so the thank-you
        # page can offer an opt-in email receipt.
        assert "/donate-thanks.html?session_id={CHECKOUT_SESSION_ID}" in captured["success_url"]
        assert captured["cancel_url"].endswith("/donate-cancel.html")

    def test_opt_out_of_account_email(self, make_client, monkeypatch):
        # Donor unchecks "use my account email" → don't pre-fill, so Stripe shows
        # a blank, editable email field for a different address.
        captured = {}

        class _Sess:
            url = "https://x/y"

        monkeypatch.setattr(donations, "create_checkout_session",
                            lambda **kw: captured.update(kw) or _Sess())
        client = make_client()
        r = client.post("/api/donations/checkout",
                        json={"amount": 25, "use_account_email": False})
        assert r.status_code == 200, r.text
        assert captured["user_id"] == DEV_USER_ID  # still attributed to the account
        assert captured["customer_email"] is None  # but email left for the donor to enter

    def test_anonymous_allowed(self, make_client, monkeypatch):
        captured = {}

        class _Sess:
            url = "https://x/y"

        monkeypatch.setattr(donations, "create_checkout_session",
                            lambda **kw: captured.update(kw) or _Sess())
        client = make_client(viewer=None, optional=None)
        r = client.post("/api/donations/checkout", json={"amount": 10})
        assert r.status_code == 200
        assert captured["user_id"] is None
        # Anonymous donor → no account email to pre-fill.
        assert captured["customer_email"] is None

    def test_rejects_bad_amount(self, make_client):
        client = make_client()
        assert client.post("/api/donations/checkout", json={"amount": 0}).status_code == 422
        assert client.post("/api/donations/checkout",
                          json={"amount": 999999}).status_code == 422

    def test_rejects_bad_currency(self, make_client):
        client = make_client()
        r = client.post("/api/donations/checkout", json={"amount": 10, "currency": "EU"})
        assert r.status_code == 422

    def test_rejects_recurring(self, make_client, monkeypatch):
        # Recurring is gated at the API until subscription lifecycle is handled,
        # so it must 422 before ever reaching Stripe.
        def _should_not_be_called(**kw):
            raise AssertionError("create_checkout_session must not run for recurring")

        monkeypatch.setattr(donations, "create_checkout_session", _should_not_be_called)
        r = make_client().post(
            "/api/donations/checkout", json={"amount": 10, "recurring": True}
        )
        assert r.status_code == 422

    def test_not_configured_returns_503(self, make_client, monkeypatch):
        from flyfun_common.payments import StripeNotConfigured

        def _raise(**kw):
            raise StripeNotConfigured("no key")

        monkeypatch.setattr(donations, "create_checkout_session", _raise)
        r = make_client().post("/api/donations/checkout", json={"amount": 10})
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# Opt-in email receipt
# ---------------------------------------------------------------------------


def _receipt(payment_status="paid", service=SERVICE, user_id=None,
             email="donor@example.com", amount=25.0, currency="EUR",
             created=1_700_000_000):
    from flyfun_common.payments import CheckoutReceipt

    return CheckoutReceipt(
        session_id="cs_test_1", service=service, user_id=user_id,
        payment_status=payment_status, amount=amount, currency=currency,
        email=email, created=created,
    )


class TestEmailReceipt:
    def test_prefers_account_email_for_attributed_donation(self, make_client, monkeypatch):
        # Donation attributed to DEV_USER_ID → goes to the account contact email
        # (dev@localhost from the fixture), NOT the Checkout email.
        captured = {}

        monkeypatch.setattr(donations, "retrieve_checkout_receipt",
                            lambda sid: _receipt(user_id=DEV_USER_ID,
                                                 email="typed-at-stripe@example.com"))
        monkeypatch.setattr(donations, "send_donation_receipt_email",
                            lambda **kw: captured.update(kw))
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_test_1"})
        assert r.status_code == 200, r.text
        assert captured["email"] == "dev@localhost"  # account, not the Stripe contact
        assert r.json()["email"] == "d***@localhost"

    def test_anonymous_falls_back_to_checkout_email(self, make_client, monkeypatch):
        captured = {}

        monkeypatch.setattr(donations, "retrieve_checkout_receipt",
                            lambda sid: _receipt(user_id=None, email="donor@example.com"))
        monkeypatch.setattr(donations, "send_donation_receipt_email",
                            lambda **kw: captured.update(kw))
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_test_1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sent"] is True
        assert body["email"] == "d***@example.com"  # masked, not the raw address
        assert captured["amount"] == 25.0
        assert captured["currency"] == "EUR"
        assert captured["email"] == "donor@example.com"
        # created → tz-aware UTC datetime
        assert captured["donated_at"].tzinfo is not None

    def test_rejects_unpaid_session(self, make_client, monkeypatch):
        monkeypatch.setattr(donations, "retrieve_checkout_receipt",
                            lambda sid: _receipt(payment_status="unpaid"))
        monkeypatch.setattr(donations, "send_donation_receipt_email",
                            lambda **kw: pytest.fail("must not send for unpaid"))
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_x"})
        assert r.status_code == 409

    def test_rejects_other_service(self, make_client, monkeypatch):
        monkeypatch.setattr(donations, "retrieve_checkout_receipt",
                            lambda sid: _receipt(service="some-other-app"))
        monkeypatch.setattr(donations, "send_donation_receipt_email",
                            lambda **kw: pytest.fail("must not send for other service"))
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_x"})
        assert r.status_code == 404

    def test_rejects_session_without_email(self, make_client, monkeypatch):
        monkeypatch.setattr(donations, "retrieve_checkout_receipt",
                            lambda sid: _receipt(email=None))
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_x"})
        assert r.status_code == 422

    def test_stripe_not_configured_returns_503(self, make_client, monkeypatch):
        from flyfun_common.payments import StripeNotConfigured

        def _raise(sid):
            raise StripeNotConfigured("no key")

        monkeypatch.setattr(donations, "retrieve_checkout_receipt", _raise)
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_x"})
        assert r.status_code == 503

    def test_send_failure_returns_502(self, make_client, monkeypatch):
        def _boom(**kw):
            raise RuntimeError("smtp down")

        monkeypatch.setattr(donations, "retrieve_checkout_receipt",
                            lambda sid: _receipt())
        monkeypatch.setattr(donations, "send_donation_receipt_email", _boom)
        r = make_client().post("/api/donations/email-receipt",
                               json={"session_id": "cs_x"})
        assert r.status_code == 502


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class TestWebhook:
    def test_records_and_is_idempotent(self, make_client, session_factory, monkeypatch):
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda payload, sig: _event("checkout.session.completed", _session_obj()))
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda pi: None)
        client = make_client()

        r1 = client.post("/api/donations/webhook", content=b"{}",
                         headers={"stripe-signature": "t=1,v1=x"})
        assert r1.status_code == 200, r1.text
        assert r1.json()["created"] is True

        r2 = client.post("/api/donations/webhook", content=b"{}",
                         headers={"stripe-signature": "t=1,v1=x"})
        assert r2.json()["created"] is False  # idempotent on provider_ref

        s = session_factory()
        rows = s.query(DonationRow).all()
        assert len(rows) == 1
        assert rows[0].amount == pytest.approx(28.0)  # 2800 minor units USD
        assert rows[0].amount_usd == pytest.approx(28.0)
        assert rows[0].user_id == DEV_USER_ID
        assert rows[0].status == "succeeded"
        s.close()

    def test_records_net_usd_when_ratio_available(self, make_client, session_factory, monkeypatch):
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("checkout.session.completed", _session_obj()))
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda pi: 0.95)
        make_client().post("/api/donations/webhook", content=b"{}",
                          headers={"stripe-signature": "x"})
        s = session_factory()
        row = s.query(DonationRow).one()
        assert row.net_usd == pytest.approx(28.0 * 0.95)
        s.close()

    def test_fx_failure_falls_back_to_usd(self, make_client, session_factory, monkeypatch):
        # An FX outage must never lose a donation: record it 1:1 rather than 500.
        def _raise(amount, currency):
            raise RuntimeError("ECB unreachable")

        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("checkout.session.completed", _session_obj()))
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda pi: None)
        monkeypatch.setattr(donations.fx, "to_usd", _raise)
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "x"})
        assert r.json()["created"] is True
        s = session_factory()
        row = s.query(DonationRow).one()
        assert row.amount_usd == pytest.approx(28.0)  # 1:1 fallback
        assert row.fx_rate == pytest.approx(1.0)
        s.close()

    def test_bad_signature_is_400(self, make_client, monkeypatch):
        def _raise(payload, sig):
            raise SignatureVerificationError("bad", sig)

        monkeypatch.setattr(donations, "verify_webhook_event", _raise)
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "bad"})
        assert r.status_code == 400

    def test_refund_marks_donation(self, make_client, session_factory, monkeypatch):
        # First record a donation.
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("checkout.session.completed", _session_obj()))
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda pi: None)
        make_client().post("/api/donations/webhook", content=b"{}",
                          headers={"stripe-signature": "x"})

        # Then refund it.
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("charge.refunded", {"payment_intent": "pi_123"}))
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "x"})
        assert r.json()["refunded"] is True
        s = session_factory()
        assert s.query(DonationRow).one().status == "refunded"
        s.close()

    def test_charge_updated_backfills_net_usd(self, make_client, session_factory, monkeypatch):
        # Record a donation whose fee wasn't ready yet (net_usd stays None).
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("checkout.session.completed", _session_obj()))
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda pi: None)
        make_client().post("/api/donations/webhook", content=b"{}",
                           headers={"stripe-signature": "x"})
        s = session_factory()
        assert s.query(DonationRow).one().net_usd is None
        s.close()

        # charge.updated arrives once the balance transaction (fee) is ready.
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("charge.updated", {"payment_intent": "pi_123"}))
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda pi: 0.95)
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "x"})
        assert r.json()["net_updated"] is True
        s = session_factory()
        # amount_total 2800 → 28.00 USD; net = 28.00 * 0.95
        assert s.query(DonationRow).one().net_usd == pytest.approx(28.0 * 0.95)
        s.close()

    def test_charge_updated_idempotent_and_skips_unknown(self, make_client, session_factory, monkeypatch):
        # No matching donation → ignored, and (guard) the fee API is never called.
        def _boom(pi):
            raise AssertionError("retrieve_net_ratio must not be called without a row")
        monkeypatch.setattr(donations, "retrieve_net_ratio", _boom)
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("charge.updated", {"payment_intent": "pi_absent"}))
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "x"})
        assert r.status_code == 200 and "ignored" in r.json()

    def test_unknown_event_ignored(self, make_client, monkeypatch):
        monkeypatch.setattr(donations, "verify_webhook_event",
                            lambda p, s: _event("payment_intent.created", {}))
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "x"})
        assert r.status_code == 200
        assert r.json()["ignored"] == "payment_intent.created"


# ---------------------------------------------------------------------------
# /me and /summary
# ---------------------------------------------------------------------------


def _record_donation(session_factory, amount_usd, user_id=DEV_USER_ID, provider_ref="pi_x",
                     status="succeeded", year=None):
    s = session_factory()
    created = datetime.now(timezone.utc) if year is None else datetime(year, 6, 1, tzinfo=timezone.utc)
    s.add(DonationRow(
        user_id=user_id, service=SERVICE, amount=amount_usd, currency="USD",
        amount_usd=amount_usd, fx_rate=1.0, recurring=False, status=status,
        provider="stripe", provider_ref=provider_ref, created_at=created,
    ))
    s.commit()
    s.close()


class TestMe:
    def test_total_and_impact(self, make_client, session_factory):
        _seed_economics(session_factory)
        _record_donation(session_factory, 56.0)
        r = make_client().get("/api/donations/me")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_usd"] == pytest.approx(56.0)
        assert body["impact"]["empty"] is False
        assert body["impact"]["user_months"] > 0
        assert body["impact"]["summary"]  # non-empty phrasing
        assert body["fx"]["currency"] == "EUR"  # EU-first default for an unset pref

    def test_neutral_when_no_economics(self, make_client, session_factory):
        # No cost config seeded → economics unavailable → neutral impact.
        _record_donation(session_factory, 56.0)
        body = make_client().get("/api/donations/me").json()
        assert body["total_usd"] == pytest.approx(56.0)
        assert body["impact"]["empty"] is True
        assert body["impact"]["summary"] == ""

    def test_excludes_refunded(self, make_client, session_factory):
        _record_donation(session_factory, 10.0, provider_ref="pi_ok")
        _record_donation(session_factory, 99.0, provider_ref="pi_ref", status="refunded")
        body = make_client().get("/api/donations/me").json()
        assert body["total_usd"] == pytest.approx(10.0)

    def test_currency_override(self, make_client, session_factory):
        _seed_economics(session_factory)
        _record_donation(session_factory, 10.0)
        body = make_client().get("/api/donations/me?currency=NOK").json()
        assert body["fx"]["currency"] == "NOK"
        assert body["fx"]["rate"] == pytest.approx(10.0)

    def test_donation_history_newest_first_excludes_refunds(self, make_client, session_factory):
        _record_donation(session_factory, 10.0, provider_ref="pi_a", year=2025)
        _record_donation(session_factory, 25.0, provider_ref="pi_b", year=2026)
        _record_donation(session_factory, 99.0, provider_ref="pi_r",
                         status="refunded", year=2026)
        body = make_client().get("/api/donations/me").json()
        hist = body["donations"]
        assert len(hist) == 2  # refunded one excluded
        # newest first
        assert hist[0]["amount"] == pytest.approx(25.0)
        assert hist[1]["amount"] == pytest.approx(10.0)
        assert hist[0]["currency"] == "USD"
        assert hist[0]["date"].startswith("2026-")


class TestSummary:
    def test_public_no_auth(self, make_client, session_factory):
        _seed_economics(session_factory)
        _record_donation(session_factory, 112.0)
        r = make_client(viewer=None, optional=None).get("/api/donations/summary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["year"] == datetime.now(timezone.utc).year
        assert body["total_year_usd"] == pytest.approx(112.0)
        assert body["impact"]["months_covered"] > 0
        assert body["fx"]["currency"] == "USD"  # anon, no ?currency → USD-canonical

    def test_enabled_reflects_stripe_config(self, make_client, session_factory, monkeypatch):
        _seed_economics(session_factory)
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        assert make_client().get("/api/donations/summary").json()["enabled"] is False
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
        assert make_client().get("/api/donations/summary").json()["enabled"] is True

    def test_anon_currency_override(self, make_client, session_factory):
        _seed_economics(session_factory)
        _record_donation(session_factory, 100.0)
        # Anonymous viewers have no saved pref — ?currency= drives display.
        body = make_client(viewer=None, optional=None).get(
            "/api/donations/summary?currency=EUR"
        ).json()
        assert body["fx"]["currency"] == "EUR"
        assert body["fx"]["rate"] == pytest.approx(0.9)

    def test_only_current_year(self, make_client, session_factory):
        _seed_economics(session_factory)
        _record_donation(session_factory, 50.0, provider_ref="pi_now")
        _record_donation(session_factory, 500.0, provider_ref="pi_old", year=2023)
        body = make_client(viewer=None, optional=None).get("/api/donations/summary").json()
        assert body["total_year_usd"] == pytest.approx(50.0)


def _seed_stats(session_factory, *, recent_usage=3, old_usage=0, packs=0,
                output_tokens=10_000):
    """A flight + briefing usage rows (the briefing counter) + optional packs.

    ``recent_usage`` rows land inside the 30-day window, ``old_usage`` rows
    well outside it. ``packs`` seeds ``briefing_packs`` rows that must NOT be
    counted — retention deletes those, so the stats read ``briefing_usage``.
    """
    from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow, FlightRow

    now = datetime.now(timezone.utc)
    s = session_factory()
    s.add(FlightRow(id="flt-1", user_id=DEV_USER_ID,
                    departure_time=datetime(2026, 6, 1, tzinfo=timezone.utc)))
    s.flush()
    for i in range(packs):
        s.add(BriefingPackRow(
            flight_id="flt-1",
            fetch_timestamp=datetime(2026, 6, 1, tzinfo=timezone.utc),
            days_out=i,
        ))
    # Tokens ride on the first row only, so the word count stays predictable
    # regardless of how many rows a test seeds.
    for i in range(recent_usage):
        s.add(BriefingUsageRow(
            user_id=DEV_USER_ID, flight_id="flt-1",
            timestamp=now - timedelta(days=1),
            llm_output_tokens=output_tokens if i == 0 else 0,
            llm_input_tokens=999_999,
        ))
    for _ in range(old_usage):
        s.add(BriefingUsageRow(
            user_id=DEV_USER_ID, flight_id="flt-1",
            timestamp=now - timedelta(days=120),
            llm_output_tokens=0, llm_input_tokens=1,
        ))
    s.commit()
    s.close()


class TestSummaryStats:
    def test_stats_trio(self, make_client, session_factory):
        _seed_economics(session_factory)  # 2 distinct briefing users (30d)
        _seed_stats(session_factory, recent_usage=4, old_usage=2, packs=9,
                    output_tokens=10_000)
        body = make_client(viewer=None, optional=None).get("/api/donations/summary").json()
        stats = body["stats"]
        assert stats["active_pilots_30d"] == 2
        # 6 usage rows all-time, 4 of them inside the window. The 9 packs are
        # deliberately ignored — a pack count shrinks under retention.
        assert stats["briefings_all_time"] == 6
        assert stats["briefings_last_30d"] == 4
        # Output tokens only, ~0.75 words/token: 10_000 → 7_500 words.
        assert stats["analysis_words_all_time"] == 7_500
        assert stats["words_summary"]  # non-empty phrasing
        # run-cost block exposed for transparency.
        assert body["run_cost"]["monthly_run_cost_usd"] > 0
        assert body["run_cost"]["cost_per_user_month_usd"] > 0

    def test_stats_zero_when_empty(self, make_client, session_factory):
        # No economics, no packs/usage → neutral zeros, no crash.
        body = make_client(viewer=None, optional=None).get("/api/donations/summary").json()
        assert body["stats"]["briefings_all_time"] == 0
        assert body["stats"]["briefings_last_30d"] == 0
        assert body["stats"]["analysis_words_all_time"] == 0
        assert body["stats"]["words_summary"] == ""


class TestMePersonal:
    def test_personal_retrospective(self, make_client, session_factory):
        # DEV has one $0.1 briefing row (from _seed_economics) → lifetime $0.1; a
        # donation below that covers only a fraction → retrospective band.
        _seed_economics(session_factory)
        _record_donation(session_factory, 0.05)
        body = make_client().get("/api/donations/me").json()
        p = body["personal"]
        assert p["empty"] is False
        assert p["band"] == "retrospective"
        assert p["summary"]

    def test_personal_covers_others(self, make_client, session_factory):
        # $56 dwarfs DEV's tiny lifetime cost AND the 2-pilot active base, so the
        # surplus caps to whole-service months rather than claiming more pilots
        # than exist. (The uncapped "+N pilots" phrasing is unit-tested in
        # test_impact.py.) The extra volume keeps DEV's own true cost small —
        # at one briefing each, the fixed monthly would amortize to $28 on DEV's
        # single row and there would be no surplus to speak of.
        _seed_economics(session_factory, other_user_briefings=200)
        _record_donation(session_factory, 56.0)
        body = make_client().get("/api/donations/me").json()
        p = body["personal"]
        assert p["band"] == "covers_others"
        assert p["overflow_capped"] is True
        assert "running the whole service" in p["summary"]
        assert "pilot" not in p["summary"]

    def test_personal_empty_without_economics(self, make_client, session_factory):
        _record_donation(session_factory, 56.0)
        body = make_client().get("/api/donations/me").json()
        assert body["personal"]["empty"] is True
        assert body["personal"]["summary"] == ""


class TestPreview:
    def test_program_average_for_anon(self, make_client, session_factory):
        _seed_economics(session_factory)  # cpum from default config / 2 users
        body = make_client(viewer=None, optional=None).get(
            "/api/donations/preview?amount=20&currency=USD"
        ).json()
        assert body["amount_usd"] == pytest.approx(20.0)
        assert body["translation"]["empty"] is False
        assert body["translation"]["summary"]

    def test_currency_converted_to_usd(self, make_client, session_factory):
        _seed_economics(session_factory)
        # EUR 18 at rate 0.9 → $20 USD-canonical.
        body = make_client(viewer=None, optional=None).get(
            "/api/donations/preview?amount=18&currency=EUR"
        ).json()
        assert body["amount_usd"] == pytest.approx(20.0)
        assert body["fx"]["currency"] == "EUR"

    def test_empty_when_no_economics(self, make_client, session_factory):
        body = make_client(viewer=None, optional=None).get(
            "/api/donations/preview?amount=20&currency=USD"
        ).json()
        assert body["translation"]["empty"] is True


# ---------------------------------------------------------------------------
# Donate nudge (web-only)
# ---------------------------------------------------------------------------


def _seed_nudge_eligible(
    session_factory,
    *,
    dev_briefings: int = 20,
    flights: int = 6,
    account_age_days: int = 200,
    other_users: int = 19,
):
    """A program and a pilot that clear every evergreen gate condition.

    Deliberately proportioned like prod rather than like a unit test: 20 pilots
    over ~210 briefings a month puts ``cost_per_user_month_usd`` around $3.4
    (measured: $2.84), which is what makes the K=1.5 rung mean anything. Ledger
    ``cost`` is set well above the recomputed true cost, as it is in prod — the
    gate uses the ledger sum as a cheap upper bound before parsing breakdowns.
    """
    s = session_factory()
    s.add(CostConfigRow(active_from=datetime.now(timezone.utc),
                        config_json=DEFAULT_CONFIG.to_json()))
    detail = json.dumps({"token_cost_usd": 0.05, "storage_cost_usd": 0.01})
    now = datetime.now(timezone.utc)

    dev = s.get(UserRow, DEV_USER_ID)
    dev.created_at = now - timedelta(days=account_age_days)

    def _ledger(uid, when):
        return CostLedgerRow(
            user_id=uid, service=SERVICE, action="briefing", cost=0.60,
            category="briefing", description="Briefing", detail_json=detail,
            created_at=when,
        )

    for i in range(dev_briefings):
        s.add(_ledger(DEV_USER_ID, now - timedelta(days=i)))
    for u in range(other_users):
        uid = f"bulk-{u}"
        s.add(UserRow(id=uid, provider="local", provider_sub=uid,
                      email=f"{uid}@x", display_name=uid, approved=True))
        for i in range(10):
            s.add(_ledger(uid, now - timedelta(days=i)))

    # Distinct flights, with the fifth far enough back that eligibility (age +
    # flights) is well in the past.
    for f in range(flights):
        s.add(BriefingUsageRow(
            user_id=DEV_USER_ID, flight_id=f"flight-{f}",
            timestamp=now - timedelta(days=account_age_days - 1),
        ))
    s.commit()
    s.close()


def _nudge_state(session_factory) -> dict:
    """The stored ``donate_nudge`` blob for the dev user."""
    s = session_factory()
    row = s.get(UserPreferencesRow, DEV_USER_ID)
    data = json.loads(row.app_prefs_json) if row and row.app_prefs_json else {}
    s.close()
    return data.get("donate_nudge", {})


@pytest.fixture(autouse=True)
def _nudge_env(monkeypatch):
    """Stripe on, no campaign, caches cold — the evergreen path under test."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_nudge")
    monkeypatch.delenv("WB_DONATE_CAMPAIGN", raising=False)
    donations.reset_nudge_cache()
    yield
    donations.reset_nudge_cache()


class TestNudgeEndpoint:
    def test_opens_an_ask_for_an_eligible_pilot(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory)
        body = make_client().get("/api/donations/nudge").json()
        assert body["show"] is True
        assert body["kind"] == "evergreen"
        assert body["rung"] == 1
        assert body["summary"]["briefing_count"] == 20
        assert body["summary"]["first_briefing_at"]
        # No money figure crosses the wire — the popover is a hook, and the
        # donate page carries the numbers.
        assert not any("usd" in k for k in body["summary"])

    def test_no_ask_without_stripe(self, make_client, session_factory, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        _seed_nudge_eligible(session_factory)
        body = make_client().get("/api/donations/nudge").json()
        assert body["show"] is False and body["reason"] == "stripe_not_configured"

    def test_no_ask_below_the_flight_floor(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory, flights=4)
        assert make_client().get("/api/donations/nudge").json()["reason"] == (
            "too_few_flights"
        )

    def test_no_ask_for_a_new_account(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory, account_age_days=30)
        assert make_client().get("/api/donations/nudge").json()["reason"] == (
            "account_too_new"
        )

    def test_no_ask_for_a_donor(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory)
        _record_donation(session_factory, 10.0)
        assert make_client().get("/api/donations/nudge").json()["reason"] == (
            "already_donated"
        )

    def test_no_ask_below_the_first_cost_rung(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory, dev_briefings=2)
        assert make_client().get("/api/donations/nudge").json()["reason"] == (
            "no_rung_crossed"
        )

    def test_the_rung_is_measured_on_the_true_cost_not_the_ledger(
        self, make_client, session_factory
    ):
        """The whole point of `usage_footprint`, pinned end to end.

        This pilot's *charged* total clears K=1.5 comfortably — it is what the
        ledger over-recovered — while their recomputed cost does not. The
        ledger sum is only allowed to be a cheap upper-bound pre-filter; if it
        ever became the figure the rung is compared against, this pilot would
        be asked for money on the strength of an amortization artefact.
        """
        _seed_nudge_eligible(session_factory, dev_briefings=12)
        report = None
        s = session_factory()
        try:
            from weatherbrief.api.credits import build_program_report, user_usage_stats

            report = build_program_report(s, 30)
            stats = user_usage_stats(s, DEV_USER_ID, report)
            econ = donations.economics_from_report(report)
        finally:
            s.close()

        rung1 = 1.5 * econ.cost_per_user_month_usd
        # The premise: ledger above the rung, recomputed cost below it. If this
        # ever stops holding the test is not testing what it says it is.
        assert stats.footprint.ledger_cost_usd > rung1 > stats.footprint.true_cost_usd

        assert make_client().get("/api/donations/nudge").json()["reason"] == (
            "no_rung_crossed"
        )

    def test_the_get_opens_the_ask_but_never_burns_an_impression(
        self, make_client, session_factory
    ):
        """A prefetch must not consume the budget the client is responsible for."""
        _seed_nudge_eligible(session_factory)
        client = make_client()
        client.get("/api/donations/nudge")
        state = _nudge_state(session_factory)
        assert state["open_ask"]["shown"] == 0
        assert "last_shown" not in state["open_ask"]
        assert state["tier_asked"] == 1.5


class TestNudgeDonationClosesAsk:
    """A donation mid-ask must close the ask *through the real endpoint order*.

    ``blocked_cheaply`` runs in front of ``decide`` and used to short-circuit on
    ``has_donated`` before it ever looked at ``open_ask`` — so the close, which
    only ``decide`` performs, never ran on the path the endpoint actually takes.
    The unit test for this calls ``decide`` directly and cannot catch it.
    """

    def _donate(self, session_factory, *, recurring: bool = False, status: str = "succeeded"):
        s = session_factory()
        s.add(DonationRow(
            user_id=DEV_USER_ID, service="flyfun-weather",
            amount=25.0, currency="USD", amount_usd=25.0, fx_rate=1.0,
            recurring=recurring, status=status, provider="stripe",
            provider_ref=f"pi_test_{status}_{recurring}",
            created_at=datetime.now(timezone.utc),
        ))
        s.commit()
        s.close()

    def test_a_donation_clears_the_open_ask_from_stored_state(
        self, make_client, session_factory
    ):
        _seed_nudge_eligible(session_factory)
        client = make_client()
        assert client.get("/api/donations/nudge").json()["show"] is True
        assert _nudge_state(session_factory)["open_ask"]["kind"] == "evergreen"

        self._donate(session_factory)
        body = client.get("/api/donations/nudge").json()
        assert body["show"] is False
        assert body["reason"] == "already_donated"
        # The point of the test: not merely hidden, actually closed and persisted.
        assert "open_ask" not in _nudge_state(session_factory)

    def test_a_recurring_donation_also_clears_it(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory)
        client = make_client()
        assert client.get("/api/donations/nudge").json()["show"] is True

        self._donate(session_factory, recurring=True)
        assert client.get("/api/donations/nudge").json()["reason"] == "already_donated"
        assert "open_ask" not in _nudge_state(session_factory)

    def test_a_refund_cannot_resurrect_a_leftover_ask(self, make_client, session_factory):
        """The failure mode the ordering bug allowed: a refund flips has_donated
        back to False, and a stale open_ask would paint again on a rung the
        pilot had already been asked."""
        _seed_nudge_eligible(session_factory)
        client = make_client()
        assert client.get("/api/donations/nudge").json()["show"] is True
        self._donate(session_factory)
        assert client.get("/api/donations/nudge").json()["show"] is False

        s = session_factory()
        s.query(DonationRow).update({DonationRow.status: "refunded"})
        s.commit()
        s.close()

        # No open ask survives to be re-rendered; the closed rung stands.
        body = client.get("/api/donations/nudge").json()
        assert body["show"] is False
        assert body["reason"] == "asked_recently"
        assert "open_ask" not in _nudge_state(session_factory)


class TestNudgeKillSwitch:
    """``WB_DONATE_NUDGE_ENABLED`` is on by default and kills both routes."""

    def test_on_by_default_when_unset(self, make_client, session_factory, monkeypatch):
        monkeypatch.delenv("WB_DONATE_NUDGE_ENABLED", raising=False)
        _seed_nudge_eligible(session_factory)
        assert make_client().get("/api/donations/nudge").json()["show"] is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " false "])
    def test_falsey_values_switch_it_off(
        self, make_client, session_factory, monkeypatch, value
    ):
        monkeypatch.setenv("WB_DONATE_NUDGE_ENABLED", value)
        _seed_nudge_eligible(session_factory)
        body = make_client().get("/api/donations/nudge").json()
        assert body["show"] is False
        assert body["reason"] == "nudge_disabled"

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "banana"])
    def test_anything_else_reads_as_on(
        self, make_client, session_factory, monkeypatch, value
    ):
        """Fails open: a typo must not silently disable the ask."""
        monkeypatch.setenv("WB_DONATE_NUDGE_ENABLED", value)
        _seed_nudge_eligible(session_factory)
        assert make_client().get("/api/donations/nudge").json()["show"] is True

    def test_disabling_also_stops_the_ack_spending_a_rung(
        self, make_client, session_factory, monkeypatch
    ):
        """A page loaded before the switch was thrown can still ack; recording
        an impression against an ask nobody can see would spend it invisibly."""
        _seed_nudge_eligible(session_factory)
        client = make_client()
        assert client.get("/api/donations/nudge").json()["show"] is True

        monkeypatch.setenv("WB_DONATE_NUDGE_ENABLED", "0")
        client.post("/api/donations/nudge/ack", json={"action": "shown"})
        assert _nudge_state(session_factory)["open_ask"]["shown"] == 0


class TestNudgeAck:
    def _open(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory)
        client = make_client()
        assert client.get("/api/donations/nudge").json()["show"] is True
        return client

    def test_shown_records_one_impression_and_arms_the_floor(
        self, make_client, session_factory
    ):
        client = self._open(make_client, session_factory)
        client.post("/api/donations/nudge/ack", json={"action": "shown"})
        state = _nudge_state(session_factory)
        assert state["open_ask"]["shown"] == 1
        assert state["last_ask_at"]

    def test_shown_is_idempotent_within_the_day(self, make_client, session_factory):
        client = self._open(make_client, session_factory)
        for _ in range(3):
            client.post("/api/donations/nudge/ack", json={"action": "shown"})
        assert _nudge_state(session_factory)["open_ask"]["shown"] == 1
        # ...and the chip does not paint again today.
        assert client.get("/api/donations/nudge").json()["reason"] == "shown_today"

    @pytest.mark.parametrize("action", ["clicked", "dismissed"])
    def test_an_answer_closes_the_ask(self, make_client, session_factory, action):
        client = self._open(make_client, session_factory)
        client.post("/api/donations/nudge/ack", json={"action": action})
        state = _nudge_state(session_factory)
        assert "open_ask" not in state
        # The rung stays consumed: the next ask is the next rung, months out.
        assert state["tier_asked"] == 1.5
        assert client.get("/api/donations/nudge").json()["reason"] == "asked_recently"

    def test_rejects_an_unknown_action(self, make_client, session_factory):
        client = self._open(make_client, session_factory)
        r = client.post("/api/donations/nudge/ack", json={"action": "snoozed"})
        assert r.status_code == 422

    def test_ack_without_an_open_ask_is_a_no_op(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory, dev_briefings=2)
        client = make_client()
        r = client.post("/api/donations/nudge/ack", json={"action": "shown"})
        assert r.status_code == 200 and r.json()["reason"] == "no_open_ask"

    def test_preserves_sibling_prefs_keys(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory)
        s = session_factory()
        s.add(UserPreferencesRow(
            user_id=DEV_USER_ID, app_prefs_json=json.dumps({"units_region": "europe"})
        ))
        s.commit()
        s.close()
        client = make_client()
        client.get("/api/donations/nudge")
        client.post("/api/donations/nudge/ack", json={"action": "shown"})
        s = session_factory()
        data = json.loads(s.get(UserPreferencesRow, DEV_USER_ID).app_prefs_json)
        s.close()
        assert data["units_region"] == "europe"
        assert data["donate_nudge"]["open_ask"]["shown"] == 1


class TestNudgeCampaign:
    def test_campaign_reaches_a_pilot_the_evergreen_path_would_not(
        self, make_client, session_factory, monkeypatch
    ):
        today = datetime.now(timezone.utc).date()
        monkeypatch.setenv(
            "WB_DONATE_CAMPAIGN",
            f"testwin:{today - timedelta(days=1)}..{today + timedelta(days=7)}",
        )
        # Two briefings is nowhere near K=1.5, and the account is only 20 days
        # old — the evergreen gate refuses on both counts.
        _seed_nudge_eligible(session_factory, dev_briefings=2, account_age_days=20)
        body = make_client().get("/api/donations/nudge").json()
        assert body["show"] is True and body["kind"] == "campaign"
        assert body["summary"]["pilots_last_year"] >= 1
        assert body["summary"]["briefings_last_year"] >= 1

    def test_a_malformed_window_simply_disables_the_campaign(
        self, make_client, session_factory, monkeypatch
    ):
        monkeypatch.setenv("WB_DONATE_CAMPAIGN", "not-a-window")
        _seed_nudge_eligible(session_factory, dev_briefings=2, account_age_days=20)
        body = make_client().get("/api/donations/nudge").json()
        assert body["show"] is False


class TestMeUsageFootprint:
    def test_a_never_donor_still_sees_what_their_usage_cost(
        self, make_client, session_factory
    ):
        """The hole the nudge would otherwise point at: a blank personal panel."""
        _seed_economics(session_factory, other_user_briefings=200)
        body = make_client().get("/api/donations/me").json()
        assert body["total_usd"] == 0
        assert body["personal"]["empty"] is True  # nothing donated to frame
        usage = body["usage"]
        assert usage["empty"] is False
        assert usage["briefings"] == 1
        assert usage["true_cost_usd"] > 0
        assert usage["translation"]["summary"]

    def test_true_cost_is_not_the_ledger_cost(self, make_client, session_factory):
        _seed_nudge_eligible(session_factory)
        usage = make_client().get("/api/donations/me").json()["usage"]
        assert usage["ledger_cost_usd"] == pytest.approx(12.0)  # 20 x $0.60
        assert usage["true_cost_usd"] < usage["ledger_cost_usd"]
        assert usage["unknown_variable_rows"] == 0

    def test_no_briefings_reads_empty(self, make_client, session_factory):
        usage = make_client().get("/api/donations/me").json()["usage"]
        assert usage["empty"] is True and usage["briefings"] == 0


class TestInvoicePaymentSucceeded:
    @pytest.fixture(autouse=True)
    def _no_stripe_fee_calls(self, monkeypatch):
        """No test in here may reach Stripe for the fee.

        The handler always looks the fee up now, and the module-level
        ``_nudge_env`` fixture puts a (fake) ``STRIPE_SECRET_KEY`` in the
        environment — so without this the lookup is a real network call,
        ~2s per test and offline-fragile. Individual tests override it.
        """
        monkeypatch.setattr(donations, "retrieve_net_ratio", lambda ref: None)

    def _invoice(self, **overrides):
        base = {
            "id": "in_test_1",
            "payment_intent": "pi_renewal_1",
            "amount_paid": 1000,
            "currency": "usd",
            "billing_reason": "subscription_cycle",
            "subscription_details": {"metadata": {"service": SERVICE,
                                                  "user_id": DEV_USER_ID}},
        }
        base.update(overrides)
        return base

    def _post(self, make_client, monkeypatch, invoice):
        import weatherbrief.api.donations as d

        monkeypatch.setattr(
            d, "verify_webhook_event",
            lambda payload, sig: _event("invoice.payment_succeeded", invoice),
        )
        return make_client().post("/api/donations/webhook", content=b"{}",
                                  headers={"stripe-signature": "t=1,v1=x"})

    def test_records_a_renewal_keyed_on_the_payment_intent(
        self, make_client, session_factory, monkeypatch
    ):
        """Not the invoice id: the rest of the webhook matches on the PI.

        ``charge.updated`` backfills the Stripe fee by ``charge.payment_intent``
        and ``charge.refunded`` marks refunds by the same, so an invoice-keyed
        row would be unreachable by both.
        """
        r = self._post(make_client, monkeypatch, self._invoice())
        assert r.json() == {"received": True, "created": True}
        s = session_factory()
        row = s.query(DonationRow).filter_by(provider_ref="pi_renewal_1").one()
        assert (row.user_id, row.amount, row.recurring) == (DEV_USER_ID, 10.0, True)
        s.close()

    def test_falls_back_to_the_invoice_id_without_a_payment_intent(
        self, make_client, session_factory, monkeypatch
    ):
        """Better an unmatched row than a lost donation."""
        inv = self._invoice()
        del inv["payment_intent"]
        self._post(make_client, monkeypatch, inv)
        s = session_factory()
        assert s.query(DonationRow).one().provider_ref == "in_test_1"
        s.close()

    def test_reads_the_payment_intent_from_the_newer_invoice_shape(
        self, make_client, session_factory, monkeypatch
    ):
        """Recent API versions nest it under ``payments.data[].payment``."""
        inv = self._invoice()
        del inv["payment_intent"]
        inv["payments"] = {"data": [{"payment": {"payment_intent": "pi_nested"}}]}
        self._post(make_client, monkeypatch, inv)
        s = session_factory()
        assert s.query(DonationRow).one().provider_ref == "pi_nested"
        s.close()

    def test_records_the_stripe_fee(self, make_client, session_factory, monkeypatch):
        """A renewal's balance transaction is usually attached by the time this
        fires, so net_usd need not wait on charge.updated."""
        import weatherbrief.api.donations as d

        monkeypatch.setattr(d, "retrieve_net_ratio", lambda ref: 0.95)
        self._post(make_client, monkeypatch, self._invoice())
        s = session_factory()
        assert s.query(DonationRow).one().net_usd == pytest.approx(9.5)
        s.close()

    def test_a_fee_lookup_failure_never_loses_the_donation(
        self, make_client, session_factory, monkeypatch
    ):
        import weatherbrief.api.donations as d

        def _boom(ref):
            raise RuntimeError("stripe down")

        monkeypatch.setattr(d, "retrieve_net_ratio", _boom)
        assert self._post(make_client, monkeypatch, self._invoice()).json()["created"]
        s = session_factory()
        assert s.query(DonationRow).one().net_usd is None
        s.close()

    def test_a_refunded_renewal_falls_out_of_aggregation(
        self, make_client, session_factory, monkeypatch
    ):
        """The reason the PI is the key: charge.refunded has to be able to find it."""
        import weatherbrief.api.donations as d

        self._post(make_client, monkeypatch, self._invoice())
        monkeypatch.setattr(
            d, "verify_webhook_event",
            lambda payload, sig: _event(
                "charge.refunded", {"id": "ch_1", "payment_intent": "pi_renewal_1"}
            ),
        )
        r = make_client().post("/api/donations/webhook", content=b"{}",
                               headers={"stripe-signature": "t=1,v1=x"})
        assert r.json()["refunded"] is True
        s = session_factory()
        assert s.query(DonationRow).one().status == "refunded"
        s.close()

    def test_idempotent_on_redelivery(self, make_client, session_factory, monkeypatch):
        self._post(make_client, monkeypatch, self._invoice())
        assert self._post(make_client, monkeypatch, self._invoice()).json()["created"] is False
        s = session_factory()
        assert s.query(DonationRow).count() == 1
        s.close()

    def test_skips_the_first_invoice(self, make_client, session_factory, monkeypatch):
        """checkout.session.completed already recorded that payment."""
        r = self._post(make_client, monkeypatch,
                       self._invoice(billing_reason="subscription_create"))
        assert r.json()["ignored"] == "subscription_create"
        s = session_factory()
        assert s.query(DonationRow).count() == 0
        s.close()

    def test_records_anonymously_when_unattributable(
        self, make_client, session_factory, monkeypatch
    ):
        """Real money still counts toward the community total.

        The gate does not rely on this: it suppresses *any* recurring donor
        indefinitely, precisely because a renewal may arrive with no user_id.
        """
        r = self._post(make_client, monkeypatch,
                       self._invoice(subscription_details={"metadata": {}}))
        assert r.json()["created"] is True
        s = session_factory()
        assert s.query(DonationRow).one().user_id is None
        s.close()

    def test_ignores_another_service(self, make_client, session_factory, monkeypatch):
        r = self._post(make_client, monkeypatch, self._invoice(
            subscription_details={"metadata": {"service": "flyfun-maps",
                                               "user_id": DEV_USER_ID}}))
        assert r.json()["ignored"] == "other service"

    def test_ignores_a_zero_invoice(self, make_client, session_factory, monkeypatch):
        r = self._post(make_client, monkeypatch, self._invoice(amount_paid=0))
        assert r.json()["ignored"] == "empty invoice"

    def test_a_recurring_donor_is_never_nudged(
        self, make_client, session_factory, monkeypatch
    ):
        """The second defence: renewals may not be attributable, but a pledge is."""
        _seed_nudge_eligible(session_factory)
        s = session_factory()
        s.add(DonationRow(
            user_id=DEV_USER_ID, service=SERVICE, amount=5.0, currency="USD",
            amount_usd=5.0, fx_rate=1.0, recurring=True, status="succeeded",
            provider="stripe", provider_ref="sub_pledge",
            created_at=datetime.now(timezone.utc) - timedelta(days=400),
        ))
        s.commit()
        s.close()
        assert make_client().get("/api/donations/nudge").json()["reason"] == (
            "already_donated"
        )
