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
from flyfun_common.db.models import CostLedgerRow, DonationRow, UserRow
from flyfun_common.payments.stripe_client import SignatureVerificationError

import weatherbrief.api.donations as donations
from weatherbrief.costs import DEFAULT_CONFIG
from weatherbrief.db.models import CostConfigRow

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


def _seed_economics(session_factory):
    """Active cost config + a couple of briefing ledger rows (2 distinct users)."""
    s = session_factory()
    s.add(CostConfigRow(active_from=datetime.now(timezone.utc),
                        config_json=DEFAULT_CONFIG.to_json()))
    detail = json.dumps({"token_cost_usd": 0.05, "storage_cost_usd": 0.01})
    for uid in (DEV_USER_ID, OTHER_USER):
        s.add(CostLedgerRow(
            user_id=uid, service=SERVICE, action="briefing", cost=0.1,
            category="briefing", description="Briefing", detail_json=detail,
            created_at=datetime.now(timezone.utc),
        ))
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
            # Distinct per-row timestamps: uq_briefing_packs_flight_ts
            # forbids duplicate (flight_id, fetch_timestamp) pairs.
            fetch_timestamp=(
                datetime(2026, 6, 1, tzinfo=timezone.utc) - timedelta(hours=i)
            ),
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
        # test_impact.py.)
        _seed_economics(session_factory)
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
