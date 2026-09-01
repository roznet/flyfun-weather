"""Donation endpoints: Stripe Checkout, webhook (source of truth), impact.

Thin app layer over flyfun-common's shared payment plumbing
(:mod:`flyfun_common.payments`) and FX (:mod:`flyfun_common.fx`). The webhook is
the **only** place a donation is written, and it is idempotent on
``provider_ref`` — never trust the client redirect. Money is USD-canonical;
responses carry an ``fx`` block so the frontend renders the viewer's currency.

Impact framing ("covers 1 user for ~8 months") is computed from the program
cost report with **margin excluded** (:mod:`weatherbrief.impact`).

Web-only by design (App Store IAP rule): the donate UI lives in the web
frontend; the iOS binary must not surface a donate button. ``/me`` is read-only
and safe to surface in-app later.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from flyfun_common import fx
from flyfun_common.db import current_user_id, get_db, optional_user_id
from flyfun_common.db.models import (
    CostLedgerRow,
    DonationRow,
    UserPreferencesRow,
    UserRow,
)
from flyfun_common.payments import (
    StripeNotConfigured,
    create_checkout_session,
    extract_donation_from_session,
    get_donation,
    get_user_total_usd,
    get_year_total_usd,
    list_user_donations,
    mark_refunded,
    record_donation,
    retrieve_checkout_receipt,
    retrieve_net_ratio,
    set_net_usd,
    verify_webhook_event,
)
from flyfun_common.payments.stripe_client import (
    SignatureVerificationError,
    from_minor_units,
)

from weatherbrief import donate_nudge as nudge
from weatherbrief.api.credits import build_program_report, user_usage_stats
from weatherbrief.api.preferences import (
    FxBlock,
    fx_block_for_currency,
    fx_block_for_user,
    stripe_configured,
    usd_fx_block,
)
from weatherbrief.costs import ProgramCostReport
from weatherbrief.db.models import BriefingUsageRow
from weatherbrief.notify.donation_email import send_donation_receipt_email
from weatherbrief.privacy import mask_email
from weatherbrief.impact import (
    ProgramEconomics,
    UsageFootprint,
    choose_translation,
    donation_impact,
    economics_from_report,
    footprint_to_dict,
    format_words_written,
    impact_to_dict,
    personal_impact,
    personal_to_dict,
    tokens_to_words,
    translation_to_dict,
    words_to_books,
    yearly_impact,
    yearly_to_dict,
)

logger = logging.getLogger(__name__)

SERVICE = "flyfun-weather"
_ECONOMICS_WINDOW_DAYS = 30

# Sanity bounds on a single donation (major units, any currency).
_MIN_AMOUNT = 1.0
_MAX_AMOUNT = 100_000.0

router = APIRouter(prefix="/donations", tags=["donations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _economics(db: Session) -> ProgramEconomics | None:
    """Derive margin-excluded run-cost economics, or None when no cost config."""
    report = build_program_report(db, _ECONOMICS_WINDOW_DAYS)
    return economics_from_report(report) if report is not None else None


def _empty_economics() -> ProgramEconomics:
    """Neutral economics → impact layer renders a neutral empty state."""
    return ProgramEconomics(monthly_run_cost_usd=0.0, active_users=0, cost_per_user_month_usd=0.0)


def _report_and_economics(db: Session) -> tuple[ProgramCostReport | None, ProgramEconomics]:
    """The cost report **and** its economics, from a single build.

    Anything that needs a pilot's *true* cost needs the report too (it carries
    the real fixed monthly cost and the actual briefing volume to amortize
    over), and ``build_program_report`` is the expensive call — so callers that
    need both take them together rather than building it twice.
    """
    report = build_program_report(db, _ECONOMICS_WINDOW_DAYS)
    econ = economics_from_report(report) if report is not None else _empty_economics()
    return report, econ


def _program_stats(db: Session, *, since: datetime) -> tuple[int, int, int]:
    """``(briefings_all_time, briefings_since, ai_output_tokens)`` for the header.

    Briefings count ``briefing_usage`` rows — one per briefing actually
    generated — *not* ``briefing_packs``. A pack row is deleted by tiered
    retention (``tasks/retention.py`` T2) and cascades away when its flight is
    deleted, so a pack count both understates history and can shrink over
    time; the briefing still cost real tokens and CPU when it ran. Usage rows
    are never purged, so this is the honest all-time figure and it matches the
    admin Usage tab, which aggregates the same table.

    AI words derive from the sum of ``briefing_usage.llm_output_tokens``
    (output only — what the AI *wrote*).
    """
    briefings_all = db.query(func.count()).select_from(BriefingUsageRow).scalar() or 0
    briefings_since = (
        db.query(func.count())
        .select_from(BriefingUsageRow)
        .filter(BriefingUsageRow.timestamp >= since)
        .scalar()
        or 0
    )
    out_tokens = (
        db.query(func.coalesce(func.sum(BriefingUsageRow.llm_output_tokens), 0)).scalar() or 0
    )
    return int(briefings_all), int(briefings_since), int(out_tokens)


_YEAR_DAYS = 365


def _community_year_stats(db: Session, *, since: datetime) -> tuple[int, int]:
    """``(distinct_pilots, briefings)`` over a trailing window.

    Counts ``briefing_usage`` — never purged, so history does not shrink —
    rather than ``briefing_packs`` (deleted by T2 retention) or
    ``analytics_briefings_dim`` (undercounts ~2x). This is the pair the campaign
    copy reads; ``/summary`` carries 30-day and all-time figures, which say
    nothing about a *year*.
    """
    pilots = (
        db.query(func.count(func.distinct(BriefingUsageRow.user_id)))
        .filter(BriefingUsageRow.timestamp >= since)
        .scalar()
        or 0
    )
    briefings = (
        db.query(func.count())
        .select_from(BriefingUsageRow)
        .filter(BriefingUsageRow.timestamp >= since)
        .scalar()
        or 0
    )
    return int(pilots), int(briefings)


def _site_covered(total_year_usd: float, econ: ProgramEconomics, *, now: datetime) -> bool:
    """Whether the whole site's cost is covered this year (forward-framing gate)."""
    return yearly_impact(total_year_usd, econ, now=now).coverage_ratio >= 1.0


def _redirect_urls(request: Request) -> tuple[str, str]:
    """Build (success_url, cancel_url). Stripe requires absolute URLs.

    Prefers the configured public base (``WEATHERBRIEF_BASE_URL``, the same env
    used for email links) so the host is correct behind a proxy; falls back to
    the request's own origin in dev.
    """
    base = os.environ.get("WEATHERBRIEF_BASE_URL") or str(request.base_url)
    base = base.rstrip("/")
    # Stripe substitutes the literal {CHECKOUT_SESSION_ID} template in the
    # success URL with the real session id on redirect, so the thank-you page
    # can offer an opt-in email receipt (see POST /donations/email-receipt).
    return (
        f"{base}/donate-thanks.html?session_id={{CHECKOUT_SESSION_ID}}",
        f"{base}/donate-cancel.html",
    )


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = "USD"
    recurring: bool = False
    # When true (default) a logged-in donor's account email pre-fills the
    # (locked) Stripe Checkout email field. Unchecking it omits customer_email so
    # Stripe shows a blank, editable field — letting the donor use a different
    # address. No effect for anonymous donors (they always type their email).
    use_account_email: bool = True


class CheckoutResponse(BaseModel):
    url: str


@router.post("/checkout", response_model=CheckoutResponse)
def create_checkout(
    body: CheckoutRequest,
    request: Request,
    viewer_id: str | None = Depends(optional_user_id),
    db: Session = Depends(get_db),
) -> CheckoutResponse:
    """Create a hosted Stripe Checkout Session for a donation.

    Anonymous donations are allowed (``viewer_id`` may be ``None``). The webhook
    — not this redirect — is the source of truth for recording the donation.
    """
    # Recurring is backend-capable, but renewal events (invoice.payment_succeeded)
    # aren't handled yet, so a subscription would only ever record its first
    # payment. Reject it at the API — not just the UI — until the lifecycle
    # webhooks land, so a direct POST can't trigger the lossy path.
    if body.recurring:
        raise HTTPException(status_code=422, detail="Recurring donations are not yet supported")

    currency = body.currency.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(status_code=422, detail="currency must be a 3-letter ISO code")
    if not (_MIN_AMOUNT <= body.amount <= _MAX_AMOUNT):
        raise HTTPException(
            status_code=422,
            detail=f"amount must be between {_MIN_AMOUNT:.0f} and {_MAX_AMOUNT:.0f}",
        )

    # Pre-fill the (mandatory, non-removable) Stripe Checkout email field for
    # logged-in donors so they don't retype it — and so the Checkout contact
    # matches their account email. Stripe locks a pre-filled email; a donor who
    # wants a different address opts out (use_account_email=False), leaving the
    # field blank and editable at Checkout.
    customer_email: str | None = None
    if viewer_id and body.use_account_email:
        user = db.get(UserRow, viewer_id)
        if user and user.email:
            customer_email = user.email

    success_url, cancel_url = _redirect_urls(request)
    try:
        session = create_checkout_session(
            amount=body.amount,
            currency=currency,
            recurring=body.recurring,
            success_url=success_url,
            cancel_url=cancel_url,
            service=SERVICE,
            user_id=viewer_id,
            customer_email=customer_email,
            product_name="Donation to FlyFun Weather",
        )
    except StripeNotConfigured:
        raise HTTPException(status_code=503, detail="Donations are not configured")
    except Exception:
        logger.exception("Failed to create Stripe Checkout Session")
        raise HTTPException(status_code=502, detail="Could not start checkout")

    if not session.url:
        raise HTTPException(status_code=502, detail="Checkout session has no redirect URL")
    return CheckoutResponse(url=session.url)


# ---------------------------------------------------------------------------
# Webhook (source of truth)
# ---------------------------------------------------------------------------


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    """Stripe webhook: record/refund donations. Idempotent on ``provider_ref``.

    Verifies the signature (``STRIPE_WEBHOOK_SECRET``); a bad/forged signature
    or malformed body is a 400. Handled events return 200 so Stripe stops
    retrying. Unknown event types are acknowledged and ignored.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = verify_webhook_event(payload, sig_header)
    except StripeNotConfigured:
        raise HTTPException(status_code=503, detail="Webhook not configured")
    except SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed payload")

    event_type = event.get("type")
    data_object = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        return _handle_session_completed(db, data_object)
    if event_type == "charge.updated":
        return _handle_charge_updated(db, data_object)
    if event_type == "charge.refunded":
        return _handle_charge_refunded(db, data_object)
    if event_type == "invoice.payment_succeeded":
        return _handle_invoice_paid(db, data_object)

    return {"received": True, "ignored": event_type}


def _handle_session_completed(db: Session, session: dict) -> dict:
    """Record a completed Checkout Session as a donation (idempotent)."""
    cd = extract_donation_from_session(session)
    if not cd.provider_ref or cd.amount <= 0:
        return {"received": True, "ignored": "empty session"}

    # Never let an FX hiccup lose a donation: an unhandled raise here → 500 →
    # Stripe retries for ~3 days → if the outage outlives that, the donation is
    # gone. fx.to_usd already degrades to the last cached rate; this guards the
    # remaining cases (no cache yet, or an ECB-unlisted currency) with a 1:1 USD
    # fallback (only off if currency != USD during a total outage — rare, and far
    # better than silent loss). USD always converts exactly.
    try:
        amount_usd, fx_rate, _as_of = fx.to_usd(cd.amount, cd.currency)
    except Exception:
        logger.warning("FX unavailable for %s; recording 1:1 USD fallback", cd.currency)
        amount_usd, fx_rate = cd.amount, 1.0

    net_usd = None
    if cd.payment_intent_id:
        try:
            ratio = retrieve_net_ratio(cd.payment_intent_id)
            if ratio is not None:
                net_usd = round(amount_usd * ratio, 6)
        except Exception:
            logger.warning("Could not retrieve Stripe fee for %s", cd.payment_intent_id)

    _row, created = record_donation(
        db,
        provider_ref=cd.provider_ref,
        service=cd.service or SERVICE,
        amount=cd.amount,
        currency=cd.currency,
        amount_usd=round(amount_usd, 6),
        fx_rate=fx_rate,
        user_id=cd.user_id,
        net_usd=net_usd,
        recurring=cd.recurring,
    )
    return {"received": True, "created": created}


def _handle_charge_updated(db: Session, charge: dict) -> dict:
    """Backfill ``net_usd`` once the Stripe fee is known.

    The balance transaction carrying the fee is created slightly after the
    charge, so ``net_usd`` is usually still NULL after
    ``checkout.session.completed``. ``charge.updated`` fires when the charge
    changes (including when the balance transaction attaches), so we use it to
    fill the fee in. Cheap-guard first: skip the Stripe API call entirely unless
    we have a matching donation that still needs the fee. Idempotent — repeated
    deliveries no-op once ``net_usd`` is set.
    """
    provider_ref = charge.get("payment_intent")
    if not provider_ref:
        return {"received": True, "ignored": "no payment_intent"}
    row = get_donation(db, provider_ref)
    if row is None or row.net_usd is not None:
        return {"received": True, "ignored": "unknown charge or net already set"}
    try:
        ratio = retrieve_net_ratio(provider_ref)
    except Exception:
        logger.warning("Could not retrieve Stripe fee for %s", provider_ref)
        return {"received": True, "net_pending": True}
    if ratio is None:
        return {"received": True, "net_pending": True}
    set_net_usd(db, provider_ref, round(row.amount_usd * ratio, 6))
    return {"received": True, "net_updated": True}


def _invoice_metadata(invoice: dict) -> dict:
    """Best-effort donation metadata for a subscription invoice.

    Checkout puts ``{service, user_id}`` on the *session*, and Stripe does not
    copy session metadata onto the subscription — so a renewal invoice carries
    it only if ``subscription_data.metadata`` was set when the subscription was
    created. We read every place it could legitimately land, newest shape first,
    and treat "not found" as an anonymous donation rather than dropping the
    money.
    """
    for candidate in (
        (invoice.get("subscription_details") or {}).get("metadata"),
        invoice.get("metadata"),
        *[
            (line or {}).get("metadata")
            for line in ((invoice.get("lines") or {}).get("data") or [])
        ],
    ):
        if isinstance(candidate, dict) and candidate.get("user_id"):
            return candidate
    return invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}


def _handle_invoice_paid(db: Session, invoice: dict) -> dict:
    """Record a **recurring** donation's renewal payment (idempotent).

    Without this, only the pledge that started a subscription ever reached the
    ledger: ``checkout.session.completed`` fires once, and every month after it
    Stripe sends ``invoice.payment_succeeded`` and nothing else. A donor paying
    every month would have looked, to any time-windowed rule, like someone who
    gave once and stopped.

    The subscription's *first* invoice is skipped: ``checkout.session.completed``
    already recorded that payment, under a different ``provider_ref``, so
    handling both would double-count it. ``provider_ref`` is the invoice id,
    which is stable and unique per billing period.

    Attribution is best-effort — see :func:`_invoice_metadata`. An unattributed
    renewal is still recorded (it is real money, and it counts toward the
    community total); it just cannot suppress that donor's nudge, which is why
    the gate additionally treats *any* recurring donation as indefinite
    suppression.
    """
    provider_ref = invoice.get("id")
    amount_minor = invoice.get("amount_paid") or 0
    if not provider_ref or amount_minor <= 0:
        return {"received": True, "ignored": "empty invoice"}
    if invoice.get("billing_reason") == "subscription_create":
        # The Checkout session that opened the subscription already recorded it.
        return {"received": True, "ignored": "subscription_create"}

    metadata = _invoice_metadata(invoice)
    service = metadata.get("service") or SERVICE
    if service != SERVICE:
        return {"received": True, "ignored": "other service"}

    currency = (invoice.get("currency") or "usd").upper()
    amount = from_minor_units(amount_minor, currency)
    try:
        amount_usd, fx_rate, _as_of = fx.to_usd(amount, currency)
    except Exception:
        logger.warning("FX unavailable for %s; recording 1:1 USD fallback", currency)
        amount_usd, fx_rate = amount, 1.0

    user_id = metadata.get("user_id") or None
    if not user_id:
        logger.warning(
            "invoice.payment_succeeded %s could not be attributed to a user "
            "(no user_id in subscription metadata); recording anonymously",
            provider_ref,
        )

    _row, created = record_donation(
        db,
        provider_ref=provider_ref,
        service=SERVICE,
        amount=amount,
        currency=currency,
        amount_usd=round(amount_usd, 6),
        fx_rate=fx_rate,
        user_id=user_id,
        recurring=True,
    )
    return {"received": True, "created": created}


def _handle_charge_refunded(db: Session, charge: dict) -> dict:
    """Flip a refunded donation out of aggregation, keyed by PaymentIntent.

    Donations are stored with ``provider_ref`` = the PaymentIntent id, so we match
    on that. We deliberately do NOT fall back to ``charge.id``: that would never
    match a ledger row and would silently mark nothing while returning 200 (so
    Stripe stops retrying) — a silent miss. Checkout always creates a
    PaymentIntent, so a charge without one isn't ours; log and ignore.
    """
    provider_ref = charge.get("payment_intent")
    if not provider_ref:
        logger.warning(
            "charge.refunded with no payment_intent (charge id: %s) — no ledger match",
            charge.get("id"),
        )
        return {"received": True, "ignored": "no payment_intent"}
    row = mark_refunded(db, provider_ref)
    return {"received": True, "refunded": row is not None}


# ---------------------------------------------------------------------------
# Opt-in email receipt (post-donation thank-you page)
# ---------------------------------------------------------------------------


class EmailReceiptRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=255)


class EmailReceiptResponse(BaseModel):
    sent: bool
    email: str  # masked, for a "sent to j***@example.com" confirmation


@router.post("/email-receipt", response_model=EmailReceiptResponse)
def send_email_receipt(
    body: EmailReceiptRequest, db: Session = Depends(get_db)
) -> EmailReceiptResponse:
    """Send an opt-in confirmation email for a just-completed donation.

    We don't email donors by default (respect-your-inbox). The thank-you page
    surfaces a single button that calls this with the Checkout ``session_id``
    from the redirect. We read the session straight from Stripe — not the local
    ledger — so we avoid racing the async webhook, then send a one-off receipt
    confirming the date and amount.

    The receipt goes to the **account that made the donation** (its contact
    email), falling back to the address entered at Checkout only for anonymous
    donors. Either way the recipient is derived from the donation itself, so a
    forged ``session_id`` can't be used to spam an arbitrary inbox.
    """
    try:
        receipt = retrieve_checkout_receipt(body.session_id)
    except StripeNotConfigured:
        raise HTTPException(status_code=503, detail="Donations are not configured")
    except Exception:
        logger.exception("Could not retrieve Checkout session for receipt")
        raise HTTPException(status_code=502, detail="Could not look up the donation")

    # Only confirm a paid session for this service; never email on an incomplete
    # or unrelated (another flyfun app's) checkout.
    if receipt.service and receipt.service != SERVICE:
        raise HTTPException(status_code=404, detail="Unknown donation")
    if receipt.payment_status != "paid":
        raise HTTPException(status_code=409, detail="Donation is not completed yet")

    # Prefer the donor's account contact email (attributed donations); fall back
    # to the Checkout email for anonymous donors.
    recipient: str | None = None
    if receipt.user_id:
        user = db.get(UserRow, receipt.user_id)
        if user and user.email:
            recipient = user.email
    if not recipient:
        recipient = receipt.email
    if not recipient:
        raise HTTPException(status_code=422, detail="No email is associated with this donation")

    donated_at = (
        datetime.fromtimestamp(receipt.created, tz=timezone.utc)
        if receipt.created
        else datetime.now(timezone.utc)
    )
    try:
        send_donation_receipt_email(
            email=recipient,
            amount=receipt.amount,
            currency=receipt.currency,
            donated_at=donated_at,
            base_url=os.environ.get("WEATHERBRIEF_BASE_URL", ""),
        )
    except Exception:
        logger.exception("Failed to send donation receipt email")
        raise HTTPException(status_code=502, detail="Could not send the confirmation email")

    return EmailReceiptResponse(sent=True, email=mask_email(recipient))


# ---------------------------------------------------------------------------
# Read: viewer impact + public community summary
# ---------------------------------------------------------------------------


class TranslationChoiceResponse(BaseModel):
    """One chosen prospective-donation translation (mirror of translation_to_dict)."""

    amount_usd: float
    kind: str
    value: float
    summary: str
    empty: bool


class DonationImpactResponse(BaseModel):
    """Per-viewer impact framing (mirror of impact_to_dict)."""

    amount_usd: float
    user_months: float
    users_until_eoy: float
    months_until_eoy: float
    empty: bool
    summary: str


class YearlyImpactResponse(BaseModel):
    """Community yearly coverage framing (mirror of yearly_to_dict)."""

    total_year_usd: float
    months_covered: float
    users_full_year: float
    coverage_ratio: float
    months_elapsed: float
    empty: bool
    summary: str


class PersonalImpactResponse(BaseModel):
    """Per-viewer lifetime coverage (mirror of personal_to_dict)."""

    donation_total_usd: float
    lifetime_cost_usd: float
    own_months_covered: float
    # null when the pilot has no realized cost yet (ratio is donation ÷ 0).
    coverage_ratio: float | None = None
    extra_pilots: int
    future_months: float
    service_months: float
    overflow_capped: bool
    band: str
    empty: bool
    summary: str


class DonationHistoryItem(BaseModel):
    """One past donation for the contribution-history details list.

    ``amount``/``currency`` are what the donor was actually charged (the truthful
    record); ``amount_usd`` is the canonical converted value. ``date`` is the
    ISO-8601 timestamp the donation was recorded.
    """

    date: str
    amount: float
    currency: str
    amount_usd: float


class UsageFootprintResponse(BaseModel):
    """What the viewer's own briefings really cost (mirror of footprint_to_dict).

    ``true_cost_usd`` is recomputed on the program's real amortization basis;
    ``ledger_cost_usd`` is what ``cost_ledger`` charged, carried alongside
    because the two genuinely differ (the rate card's volume estimate was ~3x
    below actual until 2026-08-31). Present for **every** pilot with briefings,
    donor or not — it is what the donate page shows someone who has never given.
    """

    briefings: int
    variable_usd: float
    fixed_share_usd: float
    true_cost_usd: float
    ledger_cost_usd: float
    unknown_variable_rows: int
    empty: bool
    # ISO-8601 timestamp of the viewer's first briefing; null when they have none.
    first_briefing_at: str | None = None
    # What a donation matching that cost would cover — the honest ask, phrased
    # in the vocabulary the donate page already uses.
    translation: TranslationChoiceResponse


class DonationMeResponse(BaseModel):
    total_usd: float
    impact: DonationImpactResponse
    personal: PersonalImpactResponse
    usage: UsageFootprintResponse
    donations: list[DonationHistoryItem]
    fx: FxBlock


@router.get("/me", response_model=DonationMeResponse)
def get_my_donations(
    viewer_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    currency: str | None = None,
) -> DonationMeResponse:
    """The viewer's own donation total + impact framing (USD + ``fx`` block).

    Carries both the program-average ``impact`` (legacy) and the retrospective
    ``personal`` panel (donation total vs the viewer's *lifetime* cost, with the
    "+N other pilots" / forward overflow). ``?currency=`` overrides the viewer's
    saved display currency for this response.
    """
    total = get_user_total_usd(db, viewer_id, service=SERVICE)
    report, econ = _report_and_economics(db)
    now = datetime.now(timezone.utc)
    impact = donation_impact(total, econ, now=now)

    # Lifetime cost and burn rate both come off the **recomputed** basis. The
    # ledger sum over-recovered by ~2.94x before the 2026-08-31 rate-card bump
    # and blends two amortization bases after it, so feeding it to
    # personal_impact told donors they had covered ~3x less of their own usage
    # than they really had, and held the covers_others / future bands shut.
    stats = user_usage_stats(db, viewer_id, report)
    burn_rate = stats.burn_rate_monthly_usd
    year_total = get_year_total_usd(db, now.year, service=SERVICE)
    site_covered = _site_covered(year_total, econ, now=now)
    personal = personal_impact(
        total, stats.footprint.true_cost_usd, burn_rate, econ, site_covered=site_covered
    )
    usage = footprint_to_dict(stats.footprint) | {
        "first_briefing_at": (
            stats.first_briefing_at.isoformat() if stats.first_briefing_at else None
        ),
        # A donation equal to what you have used is the honest ask, so the same
        # ladder that powers the "donate €X" preview also captions the
        # never-donor panel.
        "translation": translation_to_dict(
            choose_translation(
                stats.footprint.true_cost_usd, econ, burn_rate_monthly_usd=burn_rate
            )
        ),
    }

    history = [
        DonationHistoryItem(
            date=row.created_at.isoformat(),
            amount=round(row.amount, 2),
            currency=row.currency,
            amount_usd=round(row.amount_usd, 2),
        )
        for row in list_user_donations(db, viewer_id, service=SERVICE)
    ]

    fx_block = fx_block_for_currency(currency) if currency else fx_block_for_user(db, viewer_id)
    return DonationMeResponse(
        total_usd=round(total, 2),
        impact=impact_to_dict(impact),
        personal=personal_to_dict(personal),
        usage=usage,
        donations=history,
        fx=fx_block,
    )


class StatsResponse(BaseModel):
    """Transparency stats trio for the donate-page header (all human-facing)."""

    active_pilots_30d: int
    briefings_all_time: int
    briefings_last_30d: int
    # Trailing 365 days — the pair the annual campaign copy quotes. Distinct
    # from the 30-day and all-time figures beside them.
    active_pilots_last_year: int = 0
    briefings_last_year: int = 0
    analysis_words_all_time: int
    analysis_books_equiv: float
    words_summary: str


class RunCostResponse(BaseModel):
    """Margin-excluded run cost, in USD (frontend renders via the ``fx`` block)."""

    monthly_run_cost_usd: float
    cost_per_user_month_usd: float


class DonationSummaryResponse(BaseModel):
    year: int
    total_year_usd: float
    impact: YearlyImpactResponse
    stats: StatsResponse
    run_cost: RunCostResponse
    fx: FxBlock
    enabled: bool


@router.get("/summary", response_model=DonationSummaryResponse)
def get_summary(
    viewer_id: str | None = Depends(optional_user_id),
    db: Session = Depends(get_db),
    currency: str | None = None,
) -> DonationSummaryResponse:
    """Public this-year community total + coverage framing + stats header.

    Adds the transparency stats (active pilots 30d, briefings all-time and over
    the same 30d window, all-time AI words) and the margin-excluded run cost —
    publishing these lets a reader back out per-pilot cost, which is intended.
    ``?currency=`` overrides the display currency (needed for anonymous viewers
    and instant reformatting).
    """
    now = datetime.now(timezone.utc)
    total = get_year_total_usd(db, now.year, service=SERVICE)
    econ = _economics(db) or _empty_economics()
    yi = yearly_impact(total, econ, now=now)

    since = now - timedelta(days=_ECONOMICS_WINDOW_DAYS)
    briefings, briefings_recent, out_tokens = _program_stats(db, since=since)
    pilots_year, briefings_year = _community_year_stats(db, since=now - timedelta(days=_YEAR_DAYS))
    words = tokens_to_words(out_tokens)
    stats = StatsResponse(
        active_pilots_30d=econ.active_users,
        briefings_all_time=briefings,
        briefings_last_30d=briefings_recent,
        active_pilots_last_year=pilots_year,
        briefings_last_year=briefings_year,
        analysis_words_all_time=words,
        analysis_books_equiv=words_to_books(words),
        words_summary=format_words_written(out_tokens),
    )

    if currency:
        fx_block = fx_block_for_currency(currency)
    elif viewer_id:
        fx_block = fx_block_for_user(db, viewer_id)
    else:
        fx_block = usd_fx_block()
    return DonationSummaryResponse(
        year=now.year,
        total_year_usd=round(total, 2),
        impact=yearly_to_dict(yi),
        stats=stats,
        run_cost=RunCostResponse(
            monthly_run_cost_usd=econ.monthly_run_cost_usd,
            cost_per_user_month_usd=econ.cost_per_user_month_usd,
        ),
        fx=fx_block,
        enabled=stripe_configured(),
    )


# ---------------------------------------------------------------------------
# Prospective donation preview (the "donate €X" button → translation)
# ---------------------------------------------------------------------------


class DonationPreviewResponse(BaseModel):
    amount_usd: float
    translation: TranslationChoiceResponse
    fx: FxBlock


@router.get("/preview", response_model=DonationPreviewResponse)
def preview_donation(
    amount: float,
    currency: str | None = None,
    viewer_id: str | None = Depends(optional_user_id),
    db: Session = Depends(get_db),
) -> DonationPreviewResponse:
    """Translate a prospective amount via the adaptive ladder.

    For a logged-in pilot with usage history, small amounts translate against
    *their own* burn rate; otherwise the program average. ``amount`` is in the
    display currency — convert to USD-canonical before running the math.
    """
    fx_block = fx_block_for_currency(currency) if currency else (
        fx_block_for_user(db, viewer_id) if viewer_id else usd_fx_block()
    )
    rate = fx_block.rate or 1.0
    amount_usd = amount / rate

    report, econ = _report_and_economics(db)
    burn_rate = 0.0
    if viewer_id:
        # Same recomputed basis as the personal panel — mixing the ledger burn
        # rate in here would price "covers ~N months of your own usage" ~3x low
        # against the figure shown right above it.
        burn_rate = user_usage_stats(db, viewer_id, report).burn_rate_monthly_usd
    tc = choose_translation(amount_usd, econ, burn_rate_monthly_usd=burn_rate)
    return DonationPreviewResponse(
        amount_usd=round(amount_usd, 2),
        translation=translation_to_dict(tc),
        fx=fx_block,
    )


# ---------------------------------------------------------------------------
# Donate nudge (web-only) — see designs/plans/donate-nudge.md
# ---------------------------------------------------------------------------
#
# Web-only is structural, not stylistic: Apple requires donations from
# non-registered-nonprofits to go through IAP, so a donate button in the app
# binary is a review rejection. A separate endpoint that only ``web/ts`` calls
# makes it impossible for this flag to leak into /api/flights, pack meta, or any
# DTO iOS consumes — a rule that a field on an existing payload could only ever
# be *discouraged* from breaking.
#
# The gate is ordered cheap-first and the economics are cached, because unlike
# the donate page this runs on the briefing page — the hottest page in the app.
# A nudge check must never make a briefing page slower to load; if it cannot be
# made cheap it returns "no ask" rather than blocking.

_CAMPAIGN_ENV = "WB_DONATE_CAMPAIGN"
# The economics move slowly (a rate card edit or a month of drift), and building
# them JSON-parses every briefing ledger row in the 30-day window — ~1,632 rows
# as of 2026-09-01. An hour of staleness costs nothing; doing it per page view
# would cost the briefing page.
_NUDGE_CACHE_TTL_SECONDS = 3600

_nudge_cache_lock = threading.Lock()
_nudge_cache: dict[str, tuple[float, object]] = {}


def reset_nudge_cache() -> None:
    """Drop the cached report/community stats. For tests and rate-card edits."""
    with _nudge_cache_lock:
        _nudge_cache.clear()


def _cached(key: str, build):
    """Memoize ``build()`` under ``key`` for :data:`_NUDGE_CACHE_TTL_SECONDS`."""
    now = time.monotonic()
    with _nudge_cache_lock:
        entry = _nudge_cache.get(key)
        if entry is not None and now - entry[0] < _NUDGE_CACHE_TTL_SECONDS:
            return entry[1]
    value = build()
    with _nudge_cache_lock:
        _nudge_cache[key] = (now, value)
    return value


def _cached_report(db: Session) -> ProgramCostReport | None:
    """The program cost report, memoized for the nudge's hot path.

    ``build_program_report`` JSON-parses every briefing ledger row in the 30-day
    window — ~1,632 rows as of 2026-09-01. That is fine on a rare donate-page
    load and not fine on every briefing page view, and the figures move slowly
    enough that an hour of staleness costs nothing.
    """
    return _cached("report", lambda: build_program_report(db, _ECONOMICS_WINDOW_DAYS))


def _donor_status(db: Session, user_id: str) -> tuple[bool, bool, date | None]:
    """``(has_donated, has_recurring, last_donation_at)`` in one indexed query.

    Refunded donations fall out (``status == "succeeded"``), matching the shared
    aggregation helpers. ``has_recurring`` is *any* recurring pledge, ever:
    until subscription cancellation is tracked there is no honest way to tell an
    active subscriber from a lapsed one, and nagging someone who is paying every
    month is the worse error.
    """
    count, last_at, recurring = (
        db.query(
            func.count(DonationRow.id),
            func.max(DonationRow.created_at),
            func.max(DonationRow.recurring),
        )
        .filter(
            DonationRow.user_id == user_id,
            DonationRow.service == SERVICE,
            DonationRow.status == "succeeded",
        )
        .one()
    )
    return int(count or 0) > 0, bool(recurring), nudge.as_utc_date(last_at)


def _distinct_flights(db: Session, user_id: str) -> int:
    """How many distinct flights the pilot has actually briefed.

    Distinct *flights*, not usage rows: a usage row is written per refresh, so a
    pilot who hammers refresh on one flight is not a pilot with five briefings.
    """
    return int(
        db.query(func.count(func.distinct(BriefingUsageRow.flight_id)))
        .filter(BriefingUsageRow.user_id == user_id, BriefingUsageRow.flight_id != "")
        .scalar()
        or 0
    )


def _nth_flight_briefed_at(db: Session, user_id: str, n: int) -> datetime | None:
    """When the pilot's ``n``-th distinct flight was first briefed, or None."""
    rows = (
        db.query(func.min(BriefingUsageRow.timestamp))
        .filter(BriefingUsageRow.user_id == user_id, BriefingUsageRow.flight_id != "")
        .group_by(BriefingUsageRow.flight_id)
        .order_by(func.min(BriefingUsageRow.timestamp).asc())
        .limit(n)
        .all()
    )
    return rows[n - 1][0] if len(rows) >= n else None


def _eligible_since(
    db: Session, user_id: str, created_at: datetime | None, distinct_flights: int
) -> date | None:
    """The date the pilot passed the engagement floor (flights **and** age).

    This is what the 12-month fallback clause counts from for a pilot who has
    never been asked. Reading "never asked" as "infinitely long ago" would fire
    the fallback for the entire eligible base on rollout day — measured, the
    difference between 54 and 94 pilots asked in the first days — and make the
    cost ladder decorative.
    """
    if distinct_flights < nudge.MIN_DISTINCT_FLIGHTS:
        return None
    age_ok_on = nudge.as_utc_date(created_at)
    if age_ok_on is None:
        return None
    age_ok_on = age_ok_on + timedelta(days=nudge.MIN_ACCOUNT_AGE_DAYS)
    flights_ok_on = nudge.as_utc_date(
        _nth_flight_briefed_at(db, user_id, nudge.MIN_DISTINCT_FLIGHTS)
    )
    if flights_ok_on is None:
        return None
    return max(age_ok_on, flights_ok_on)


def _load_nudge_state(db: Session, user_id: str) -> tuple[UserPreferencesRow | None, dict, nudge.NudgeState]:
    """Read the ``donate_nudge`` key out of ``app_prefs_json``."""
    row = db.get(UserPreferencesRow, user_id)
    data: dict = {}
    if row is not None and row.app_prefs_json:
        try:
            loaded = json.loads(row.app_prefs_json)
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            data = loaded
    return row, data, nudge.load_state(data.get(nudge.PREFS_KEY))


def _save_nudge_state(
    db: Session, user_id: str, row: UserPreferencesRow | None, data: dict, state: nudge.NudgeState
) -> None:
    """Merge the nudge state back into ``app_prefs_json``, preserving siblings.

    No explicit commit — ``get_db`` commits the request, as everywhere else in
    the app.
    """
    if row is None:
        row = UserPreferencesRow(user_id=user_id)
        db.add(row)
    data[nudge.PREFS_KEY] = nudge.dump_state(state)
    row.app_prefs_json = json.dumps(data)


def _campaign_window() -> nudge.CampaignWindow | None:
    """The configured campaign, or None. Env-driven: set + restart, no redeploy."""
    return nudge.parse_campaign(os.environ.get(_CAMPAIGN_ENV))


class NudgeSummary(BaseModel):
    """The runtime values the popover copy substitutes. **No money figures.**

    Community activity stats are deliberate and fine; a cost or donation amount
    is not — a euro figure in a donation ask sets an anchor and invites the
    reader to price their own share. The donate page carries the numbers.
    """

    # Evergreen: "You've had {briefing_count} briefings since {first_month}."
    briefing_count: int = 0
    # ISO-8601 timestamp of the first briefing; the client formats the month so
    # it lands in the viewer's locale.
    first_briefing_at: str | None = None
    # Campaign: "{n_pilots} pilots generated {n_briefings} briefings…". Zero
    # when unknown — the client suppresses the sentence rather than printing a
    # number that undercuts the point.
    pilots_last_year: int = 0
    briefings_last_year: int = 0


class NudgeResponse(BaseModel):
    show: bool
    kind: str = ""
    rung: int = 0
    # Stable identifier for why nothing is shown — for debugging and logs, never
    # rendered to a pilot.
    reason: str = ""
    summary: NudgeSummary = NudgeSummary()


@router.get("/nudge", response_model=NudgeResponse)
def get_nudge(
    viewer_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> NudgeResponse:
    """Should the briefing page offer this pilot a donate chip right now?

    Web-only. The client applies the last render condition — **never beside a
    RED assessment** — and must withhold the ``shown`` ack when it suppresses,
    or impressions burn on views that painted nothing.

    Persisting happens here for *lifecycle* transitions only (an ask opening, an
    exhausted or expired one closing). Impressions are never recorded by a GET:
    a prefetch must not burn one.
    """
    window = _campaign_window()
    row, prefs, state = _load_nudge_state(db, viewer_id)
    today = nudge.today_utc()

    # Cheap-first: prefs blob, Stripe config and donation existence settle the
    # overwhelming majority of page views without touching flight counts or
    # economics. Measured on prod, only ~112 pilots get past this.
    has_donated, has_recurring, last_donation_at = _donor_status(db, viewer_id)
    cheap = nudge.GateInputs(
        today=today,
        stripe_configured=stripe_configured(),
        has_donated=has_donated,
        has_recurring_donation=has_recurring,
        last_donation_at=last_donation_at,
        campaign=window,
    )
    blocked = nudge.blocked_cheaply(state, cheap)
    if blocked is not None:
        return NudgeResponse(show=False, reason=blocked)

    user = db.get(UserRow, viewer_id)
    created_at = getattr(user, "created_at", None)
    account_age_days = 0
    created_date = nudge.as_utc_date(created_at)
    if created_date is not None:
        account_age_days = max((today - created_date).days, 0)
    distinct_flights = _distinct_flights(db, viewer_id)

    true_cost = 0.0
    cpum = 0.0
    stats = None
    eligible_since = None
    # An ask that is already open has cleared the cost ladder; re-deriving it
    # would run the expensive path on every page view for the whole life of the
    # ask, which is exactly what the design forbids. The same goes for the
    # eligibility date, which only ever feeds the decision to *open* one.
    if state.open_ask is None:
        eligible_since = _eligible_since(db, viewer_id, created_at, distinct_flights)
        report = _cached_report(db)
        cpum = economics_from_report(report).cost_per_user_month_usd if report else 0.0
        # The ledger sum is a cheap SQL aggregate and is always >= the true
        # cost (it amortizes over an estimate at or below actual volume, then
        # adds 10% margin), so a pilot whose *ledger* total misses the lowest
        # rung certainly misses it on the true basis too — and we skip parsing
        # their breakdown rows entirely.
        if cpum > 0 and _ledger_cost(db, viewer_id) >= nudge.RUNGS[0] * cpum:
            stats = user_usage_stats(db, viewer_id, report)
            true_cost = stats.footprint.true_cost_usd

    inputs = nudge.GateInputs(
        today=today,
        stripe_configured=cheap.stripe_configured,
        has_donated=has_donated,
        has_recurring_donation=has_recurring,
        last_donation_at=last_donation_at,
        distinct_flights=distinct_flights,
        account_age_days=account_age_days,
        eligible_since=eligible_since,
        true_lifetime_cost_usd=true_cost,
        cost_per_user_month_usd=cpum,
        campaign=window,
    )
    decision = nudge.decide(state, inputs)
    if decision.changed:
        _save_nudge_state(db, viewer_id, row, prefs, decision.state)
    if not decision.show:
        return NudgeResponse(show=False, reason=decision.reason)

    return NudgeResponse(
        show=True,
        kind=decision.kind,
        rung=decision.rung,
        reason=decision.reason,
        summary=_nudge_summary(db, viewer_id, decision.kind, stats),
    )


def _ledger_cost(db: Session, user_id: str) -> float:
    """The pilot's charged lifetime briefing cost — one SQL SUM, no JSON parse.

    A deliberately cheap **upper bound** on their true cost, used only to skip
    the per-row breakdown parse for pilots who cannot possibly have crossed the
    lowest rung.
    """
    return float(
        db.query(func.coalesce(func.sum(CostLedgerRow.cost), 0.0))
        .filter(
            CostLedgerRow.user_id == user_id,
            CostLedgerRow.service == SERVICE,
            CostLedgerRow.category == "briefing",
        )
        .scalar()
        or 0.0
    )


def _nudge_summary(db: Session, user_id: str, kind: str, stats) -> NudgeSummary:
    """The substitutions the chosen variant's copy needs — and nothing else."""
    if kind == nudge.KIND_CAMPAIGN:
        pilots, briefings = _cached(
            "community_year",
            lambda: _community_year_stats(
                db, since=datetime.now(timezone.utc) - timedelta(days=_YEAR_DAYS)
            ),
        )
        return NudgeSummary(pilots_last_year=pilots, briefings_last_year=briefings)

    if stats is None:
        stats = user_usage_stats(db, user_id, _cached_report(db))
    return NudgeSummary(
        briefing_count=stats.footprint.briefings,
        first_briefing_at=(
            stats.first_briefing_at.isoformat() if stats.first_briefing_at else None
        ),
    )


class NudgeAckRequest(BaseModel):
    action: Literal["shown", "clicked", "dismissed"]


@router.post("/nudge/ack", response_model=NudgeResponse)
def ack_nudge(
    body: NudgeAckRequest,
    viewer_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
) -> NudgeResponse:
    """Record an impression or an answer against the open ask.

    ``shown`` is idempotent within the calendar day and closes the ask once the
    four-impression budget is spent — silence is an answer, and it consumes the
    rung exactly as a dismissal does. ``clicked`` and ``dismissed`` both close
    the ask; a popover opened and shut with Esc sends neither, because the pilot
    answered nothing (the impression already counted, which is what limits it).
    """
    row, prefs, state = _load_nudge_state(db, viewer_id)
    if state.open_ask is None:
        return NudgeResponse(show=False, reason="no_open_ask")

    today = nudge.today_utc()
    updated = (
        nudge.record_shown(state, today)
        if body.action == "shown"
        else nudge.close_ask(state, today)
    )
    if updated is not state:
        _save_nudge_state(db, viewer_id, row, prefs, updated)
    return NudgeResponse(show=False, reason=body.action)
