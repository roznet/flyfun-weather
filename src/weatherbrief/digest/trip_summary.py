"""Haiku trip paragraph over already-summarized inputs (#602, checklist item 10).

A short LLM paragraph over the chain: which legs are fine, which are not, and
what shape the problem has. Explicitly **not** a go/no-go call — the trip view
must never print a verdict for the trip.

Why Haiku is the structural answer, not merely the cheap one
------------------------------------------------------------
This is a **rewriting task, not an analysis task**. It runs over data that has
*already* been analysed — per-leg ``assessment`` / ``outlook``, advisory chips,
``days_out``, and the deterministic binding leg from :mod:`weatherbrief.trips`.
It never sees a sounding, never sees ``route_analyses``, never re-derives
meteorology. Sonnet does the heavy lifting once per flight in the briefer; this
is a second-order pass over its conclusions, so there is nothing left for a
bigger model to do. It also bounds the failure mode: a model that only ever sees
three colours and nine advisory names cannot invent a verdict from weather it
never read.

Three properties are load-bearing:

* **Gating inherits, it does not get its own switch.** If *any* member leg has
  ``llm_digest_enabled`` off, the trip gets no AI paragraph at all — only the
  deterministic sentence. A pilot who turned AI off for a leg should not find
  that leg described by an LLM because it was grouped with others; all-on is the
  only unambiguous consent.
* **The guardrail is exact, because the deterministic layer already knows the
  answer.** The named worst leg must match the computed binding leg, and
  go/no-go vocabulary is rejected. On a mismatch we fall back to the
  deterministic sentence, so the LLM can only ever make the deterministic answer
  *nicer to read*, never different.
* **It is keyed and persisted.** Unlike the deterministic aggregate — which must
  never be stored — this one is, precisely because it costs money: unchanged
  member ``(flight_id, fetch_timestamp)`` tuples must never pay twice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightTripRow
from weatherbrief.models import Flight
from weatherbrief.trips import TripSummary

logger = logging.getLogger(__name__)

#: Longest paragraph we will show. Not a prompt-side limit (the model is asked
#: for 2-4 sentences) but a display guard.
MAX_SUMMARY_CHARS = 900

UnavailableReason = Literal[
    "ai_disabled", "no_legs", "generation_failed", "guardrail_rejected",
]


@dataclass
class TripAiResult:
    text: str | None = None
    unavailable_reason: UnavailableReason | None = None
    regenerated: bool = False


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------


def legs_allow_ai(db: Session, flights: list[Flight], user_id: str) -> bool:
    """True only when **every** member leg has the AI digest enabled.

    Inherits rather than inventing a trip-level preference that could disagree
    with the legs underneath it. Conservative on error: a profile that cannot be
    read counts as "not consented".
    """
    if not flights:
        return False
    try:
        from weatherbrief.api.profiles import load_profile_settings

        for flight in flights:
            settings = load_profile_settings(db, flight.profile_id, user_id)
            if not settings.get("llm_digest_enabled", True):
                return False
        return True
    except Exception:
        logger.warning("Trip AI gate: profile lookup failed — treating as off", exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Prompt payload
# ---------------------------------------------------------------------------


def build_context(summary: TripSummary) -> str:
    """The user message: the chain state, and nothing else.

    Deliberately a few hundred tokens of already-summarized state. Note the
    absence of a prompt-cache breakpoint: the minimum cacheable prefix is
    512-4096 tokens depending on model and this prompt head may sit below it, so
    a breakpoint could do nothing while billing 2x for the write. Measure with
    ``count_tokens`` before adding one — the ``cache_locales`` comment in
    ``llm_config.py`` is the precedent for making that call from data.
    """
    lines: list[str] = [f"Trip: {summary.name or summary.chain_label}"]
    if summary.chain_label:
        lines.append(f"Chain: {summary.chain_label}")
    lines.append(f"Legs: {summary.total_legs} total, {summary.remaining_legs} remaining")
    lines.append("")
    lines.append("Legs (in order). Each carries the id you must echo back:")
    for index, leg in enumerate(summary.legs, start=1):
        bits = [
            f"{index}. id={leg.flight_id}",
            f"{leg.departure_time.strftime('%A %d %b %H:%MZ')} {leg.label}",
            f"state={leg.state}",
        ]
        if leg.grade_kind == "assessment":
            bits.append(f"assessment={(leg.assessment or '').upper()}")
        elif leg.grade_kind == "outlook":
            bits.append(f"long-range outlook={(leg.outlook or '').upper()} (beyond the high-resolution horizon)")
        elif leg.grade_kind == "pending_coverage":
            bits.append("no model reaches this date yet")
        elif leg.grade_kind == "needs_briefing":
            bits.append("no briefing yet")
        else:
            bits.append("could not be assessed")
        if leg.days_out is not None:
            bits.append(f"days_out={leg.days_out}")
        if leg.advisory_summary and leg.advisory_summary.top:
            chips = ", ".join(
                f"{chip.status} {chip.name}" for chip in leg.advisory_summary.top
            )
            bits.append(f"advisories: {chips}")
        lines.append("  " + "; ".join(bits))

    lines.append("")
    binding = next(
        (leg for leg in summary.legs if leg.flight_id == summary.binding_leg_id), None,
    )
    if binding is not None:
        lines.append(
            f"Deterministic binding leg — set worst_leg_id to exactly this id: "
            f"{binding.flight_id} "
            f"({binding.departure_time.strftime('%A')}'s {binding.label})"
        )
    else:
        lines.append(
            "Deterministic binding leg: none — no remaining leg is gradeable "
            "yet. Set worst_leg_id to the empty string."
        )
    lines.append(f"Deterministic sentence: {summary.headline}")
    if summary.continuity_warnings:
        lines.append(
            "Note: the chain does not join up geographically "
            f"({len(summary.continuity_warnings)} gap(s))."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Guardrail
#
# The model returns the worst leg as a **field**, not as prose to be parsed, so
# "did it name the right leg?" is an equality check on a flight id.
#
# The previous shape — regexes over the paragraph, looking for superlatives near
# an ordered route match — was a losing game, and three review rounds proved it:
# widen the superlative list and a correct paragraph gets rejected; scope the
# search to a clause and a comma-appositive slips through; add negation
# handling and something else appears. Each patch traded a false negative for a
# false positive. Asking for the id removes the parse, and with it the entire
# class of finding.
#
# The vocabulary and length checks stay: those genuinely are properties of the
# prose, and matching a fixed word list is exactly what a regex is good at.
# ---------------------------------------------------------------------------


class TripParagraph(BaseModel):
    """Structured output from the trip model.

    ``worst_leg_id`` is what makes the guardrail exact. The prompt lists each
    leg with its id, and the model must echo the id of the leg it treats as the
    difficult one — so verifying it is a comparison, not an interpretation.
    """

    worst_leg_id: str = Field(
        description=(
            "The flight id (exactly as given in the leg list) of the leg you "
            "describe as the difficult one. Use the empty string only if no leg "
            "is gradeable."
        ),
    )
    paragraph: str = Field(description="Two to four sentences of plain prose.")


#: Vocabulary that turns a description into a recommendation. Whole-word
#: matched, case-insensitive, and deliberately a superset of what
#: ``prompts/trip_v1.md`` forbids — the guardrail exists for the case where the
#: model ignores the instruction, so anything the prompt bans must appear here.
#: Kept blunt: a false positive costs a fallback to the deterministic sentence,
#: which is the safe direction.
_GO_NOGO_PATTERNS = [
    r"\bgo/?no[- ]?go\b",
    r"\bno[- ]?go\b",
    # Bare "go" as a verdict, which the prompt forbids by name. Guarded with a
    # lookahead so ordinary uses survive — "going", "goes", and the infinitive
    # in "the weather is going to move" are description, not a recommendation.
    r"\bgo\b(?!\s+(?:to|through|into|from|down|up|via|around|over)\b)",
    r"\bavoid(?:ed|ing|s)?\b",
    r"\bshould (?:not )?fly\b",
    r"\bshouldn'?t fly\b",
    r"\bdon'?t fly\b",
    r"\bdo not fly\b",
    r"\brecommend(?:ed|ation|s)?\b",
    r"\badvis(?:e|ed|able)\b",
    r"\bsafe\b",
    r"\bunsafe\b",
    r"\bcancel(?:led|ling)?\b",
    r"\bscrub\b",
    r"\bpostpone\b",
    r"\bI would\b",
    r"\byou should\b",
]
_GO_NOGO_RE = re.compile("|".join(_GO_NOGO_PATTERNS), re.IGNORECASE)


def check_guardrail(
    text: str, summary: TripSummary, worst_leg_id: str | None = None,
) -> str | None:
    """Return a rejection reason, or None when the paragraph may be shown.

    Cheap and exact because the deterministic layer already knows the answer:
    the model's declared worst leg must *be* the computed binding leg, and
    recommendation vocabulary is out. Sibling of ``digest.guardrails``.

    ``worst_leg_id`` is the model's structured answer. It is optional so the
    vocabulary and length checks can be used on their own.
    """
    if not text or not text.strip():
        return "empty"
    if len(text) > MAX_SUMMARY_CHARS:
        return "too_long"

    match = _GO_NOGO_RE.search(text)
    if match:
        return f"go/no-go vocabulary: {match.group(0)!r}"

    if worst_leg_id is None:
        return None

    expected = summary.binding_leg_id or ""
    if (worst_leg_id or "").strip() != expected:
        return (
            f"named worst leg {worst_leg_id!r} is not the computed binding leg "
            f"{expected!r}"
        )
    return None


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(summary: TripSummary) -> tuple[TripParagraph | None, dict]:
    """Call the model. Returns ``(parsed_or_None, token_usage)``. Never raises.

    Structured output via ``with_structured_output(..., include_raw=True)`` —
    the same shape ``digest/llm_digest.py`` uses — because the raw message is
    where the token usage lives, and the cost has to be recorded even when the
    parse or the guardrail later rejects the content.
    """
    try:
        from weatherbrief.digest.llm_config import create_chat_model, load_digest_config

        config = load_digest_config()
        model = create_chat_model(config.trip)
        structured = model.with_structured_output(TripParagraph, include_raw=True)
        system = config.load_prompt("trip")
        raw_result = structured.invoke([
            {"role": "system", "content": system},
            {"role": "user", "content": build_context(summary)},
        ])
        parsed = raw_result.get("parsed") if isinstance(raw_result, dict) else None
        raw_msg = raw_result.get("raw") if isinstance(raw_result, dict) else None
        usage = getattr(raw_msg, "usage_metadata", None) or {}
        details = usage.get("input_token_details") or {}
        tokens = {
            # The model that actually ran — what the charge is priced at.
            "model": config.trip.model,
            "input_tokens": usage.get("input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
            "cache_read_tokens": details.get("cache_read") or 0,
            "cache_write_tokens": details.get("cache_creation") or 0,
        }
        if parsed is None or not (parsed.paragraph or "").strip():
            # Billed but unusable. Return the usage anyway so the caller can
            # still charge it — an invisible cost line is how a small cost
            # becomes an unexplained one.
            return None, tokens
        return parsed, tokens
    except Exception:
        logger.warning("Trip AI summary generation failed", exc_info=True)
        return None, {}


def _charge(db: Session, user_id: str, trip_id: str, text: str, usage: dict) -> None:
    """Put the (tiny) cost through the ledger, at the trip model's own rate.

    A fraction of a cent per trip. It is priced by ``compute_call_cost``, *not*
    the per-briefing ``compute_cost``: that one adds a droplet/subscription
    share and margin to every call, which billed each Haiku paragraph at ~$0.62
    — more than the briefing it summarises. Still recorded, because an invisible
    cost line is how a small cost becomes an unexplained one; a call that failed
    before returning any usage records nothing. Never blocks.
    """
    usage = usage or {}
    model = usage.get("model")
    if not model:
        return
    try:
        from flyfun_common.costs import record_cost
        from weatherbrief.api.credits import SERVICE
        from weatherbrief.costs import compute_call_cost

        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        cost = compute_call_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=usage.get("cache_read_tokens") or 0,
            cache_write_tokens=usage.get("cache_write_tokens") or 0,
        )
        record_cost(
            db,
            user_id,
            service=SERVICE,
            action="trip_summary",
            cost=cost,
            category="trip_summary",
            description=f"Trip summary (${cost:.4f})",
            metadata={
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            reference_id=trip_id,
        )
    except Exception:
        logger.warning("Trip AI summary cost charge failed for %s", trip_id, exc_info=True)


def ensure_trip_ai_summary(
    db: Session,
    row: FlightTripRow,
    summary: TripSummary,
    members: list[Flight],
    *,
    user_id: str,
    leg_inputs=None,
) -> TripAiResult:
    """Return the trip's AI paragraph, generating it only when stale.

    Called on a completed trip refresh and on demand when the trip page opens.
    Both go through the same key check — there is deliberately no ``force``
    escape hatch. The refresh path used one on the reasoning that it "knows the
    inputs just changed", which is not true: the refresh gate can skip every
    leg for want of a new model run, and the paragraph was then re-billed for
    provably identical input. The key is derived from every input the paragraph
    depends on, so a chain that did change something misses the cache anyway.

    ``leg_inputs`` lets a caller that already built them (every caller that
    computed ``summary``) avoid a second packs + debriefs query pair.
    """
    from weatherbrief.api.trips import ai_summary_key, build_leg_inputs

    if not members:
        return TripAiResult(unavailable_reason="no_legs")

    # The consent gate runs FIRST, ahead of the cache. It has to: the cache key
    # is built from packs and debriefs, and ``llm_digest_enabled`` is in
    # neither — so a pilot who turns AI off on a leg without touching its pack
    # leaves the key unchanged, and a gate placed after the cache check would
    # never be reached. The stored paragraph would keep being served, which is
    # exactly the "gating inherits" guarantee this module promises three times
    # over. Cheapest correct order, not merely the safest.
    if not legs_allow_ai(db, members, user_id):
        # Clear any stored text too, so a leg switched to AI-off cannot keep
        # being described by a paragraph written before the switch.
        row.ai_summary_text = None
        row.ai_summary_key = None
        row.ai_summary_at = None
        return TripAiResult(unavailable_reason="ai_disabled")

    if leg_inputs is None:
        leg_inputs = build_leg_inputs(db, members)
    key = ai_summary_key(leg_inputs)
    if row.ai_summary_key == key:
        # Keyed on the *key alone*, not on the text: a stored key means these
        # inputs have already been through the model, and the text is whatever
        # came of it — a paragraph, or None because the guardrail rejected it or
        # the call came back empty. Requiring text here would re-run (and
        # re-charge) every page open for exactly the inputs that reliably fail.
        # A None text simply hides the AI section; the deterministic sentence is
        # always there.
        return TripAiResult(text=row.ai_summary_text)

    parsed, usage = generate(summary)
    if parsed is None:
        # A call that billed tokens and returned nothing usable still costs
        # money, so it is still charged (``usage`` is empty when the call
        # itself failed, which charges zero).
        _charge(db, user_id, row.id, "", usage)
        _remember_attempt(row, key)
        return TripAiResult(unavailable_reason="generation_failed")

    text = parsed.paragraph.strip()
    rejection = check_guardrail(text, summary, worst_leg_id=parsed.worst_leg_id)
    _charge(db, user_id, row.id, text, usage)
    if rejection is not None:
        logger.info("Trip %s: AI summary rejected (%s)", row.id, rejection)
        # Deliberately not shown: the deterministic sentence stands on its own.
        # The key is still recorded so a leg combination that reliably fails the
        # guardrail is not regenerated — and re-charged — on every page open.
        _remember_attempt(row, key)
        return TripAiResult(unavailable_reason="guardrail_rejected")

    row.ai_summary_text = text
    row.ai_summary_key = key
    row.ai_summary_at = datetime.now(timezone.utc)
    return TripAiResult(text=text, regenerated=True)


def _remember_attempt(row: FlightTripRow, key: str) -> None:
    """Record that this input set was tried and produced nothing showable.

    Without it, inputs that reliably fail (a rejected paragraph, a model that
    keeps returning empty) are regenerated and re-charged on every trip-page
    open — the opposite of the "unchanged inputs never pay twice" invariant.
    The text stays ``None``, so the page falls back to the deterministic
    sentence; only the key advances.
    """
    row.ai_summary_text = None
    row.ai_summary_key = key
    row.ai_summary_at = datetime.now(timezone.utc)
