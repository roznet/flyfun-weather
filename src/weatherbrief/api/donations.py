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

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from flyfun_common import fx
from flyfun_common.db import current_user_id, get_db, optional_user_id
from flyfun_common.payments import (
    StripeNotConfigured,
    create_checkout_session,
    extract_donation_from_session,
    get_donation,
    get_user_total_usd,
    get_year_total_usd,
    mark_refunded,
    record_donation,
    retrieve_net_ratio,
    set_net_usd,
    verify_webhook_event,
)
from flyfun_common.payments.stripe_client import SignatureVerificationError

from weatherbrief.api.credits import build_program_report, user_cost_stats
from weatherbrief.api.preferences import (
    FxBlock,
    fx_block_for_currency,
    fx_block_for_user,
    stripe_configured,
    usd_fx_block,
)
from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow
from weatherbrief.impact import (
    ProgramEconomics,
    choose_translation,
    donation_impact,
    economics_from_report,
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


def _program_stats(db: Session) -> tuple[int, int]:
    """All-time ``(briefings_generated, ai_output_tokens)`` for the stats header.

    Briefings = every ``briefing_packs`` row; AI words derive from the sum of
    ``briefing_usage.llm_output_tokens`` (output only — what the AI *wrote*).
    """
    briefings = db.query(func.count()).select_from(BriefingPackRow).scalar() or 0
    out_tokens = (
        db.query(func.coalesce(func.sum(BriefingUsageRow.llm_output_tokens), 0)).scalar() or 0
    )
    return int(briefings), int(out_tokens)


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
    return f"{base}/donate-thanks.html", f"{base}/donate-cancel.html"


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


class CheckoutRequest(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = "USD"
    recurring: bool = False


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
# Read: viewer impact + public community summary
# ---------------------------------------------------------------------------


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
    coverage_ratio: float
    extra_pilots: int
    future_months: float
    band: str
    empty: bool
    summary: str


class DonationMeResponse(BaseModel):
    total_usd: float
    impact: DonationImpactResponse
    personal: PersonalImpactResponse
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
    econ = _economics(db) or _empty_economics()
    now = datetime.now(timezone.utc)
    impact = donation_impact(total, econ, now=now)

    _lifetime, _months, burn_rate = user_cost_stats(db, viewer_id)
    year_total = get_year_total_usd(db, now.year, service=SERVICE)
    site_covered = _site_covered(year_total, econ, now=now)
    personal = personal_impact(total, _lifetime, burn_rate, econ, site_covered=site_covered)

    fx_block = fx_block_for_currency(currency) if currency else fx_block_for_user(db, viewer_id)
    return DonationMeResponse(
        total_usd=round(total, 2),
        impact=impact_to_dict(impact),
        personal=personal_to_dict(personal),
        fx=fx_block,
    )


class StatsResponse(BaseModel):
    """Transparency stats trio for the donate-page header (all human-facing)."""

    active_pilots_30d: int
    briefings_all_time: int
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

    Adds the transparency stats trio (active pilots 30d, all-time briefings,
    all-time AI words) and the margin-excluded run cost — publishing these lets a
    reader back out per-pilot cost, which is intended. ``?currency=`` overrides
    the display currency (needed for anonymous viewers and instant reformatting).
    """
    now = datetime.now(timezone.utc)
    total = get_year_total_usd(db, now.year, service=SERVICE)
    econ = _economics(db) or _empty_economics()
    yi = yearly_impact(total, econ, now=now)

    briefings, out_tokens = _program_stats(db)
    words = tokens_to_words(out_tokens)
    stats = StatsResponse(
        active_pilots_30d=econ.active_users,
        briefings_all_time=briefings,
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


class TranslationChoiceResponse(BaseModel):
    """One chosen prospective-donation translation (mirror of translation_to_dict)."""

    amount_usd: float
    kind: str
    value: float
    summary: str
    empty: bool


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
    amount_usd = amount / rate if rate else amount

    econ = _economics(db) or _empty_economics()
    burn_rate = 0.0
    if viewer_id:
        _lifetime, _months, burn_rate = user_cost_stats(db, viewer_id)
    tc = choose_translation(amount_usd, econ, burn_rate_monthly_usd=burn_rate)
    return DonationPreviewResponse(
        amount_usd=round(amount_usd, 2),
        translation=translation_to_dict(tc),
        fx=fx_block,
    )
