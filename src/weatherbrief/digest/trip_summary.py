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

from sqlalchemy.orm import Session

from weatherbrief.db.models import FlightTripRow
from weatherbrief.models import Flight
from weatherbrief.trips import TripSummary

logger = logging.getLogger(__name__)

#: Vocabulary that turns a description into a recommendation. Whole-word
#: matched, case-insensitive. Kept deliberately blunt: a false positive costs a
#: fallback to the deterministic sentence, which is the safe direction.
_GO_NOGO_PATTERNS = [
    r"\bgo/?no[- ]?go\b",
    r"\bno[- ]?go\b",
    r"\bshould (?:not )?fly\b",
    r"\bshouldn'?t fly\b",
    r"\bdon'?t fly\b",
    r"\bdo not fly\b",
    r"\brecommend(?:ed|ation|s)?\b",
    r"\badvis(?:e|ed|able)\b",
    r"\bsafe to fly\b",
    r"\bunsafe\b",
    r"\bcancel(?:led|ling)?\b",
    r"\bscrub\b",
    r"\bpostpone\b",
    r"\bI would\b",
    r"\byou should\b",
]
_GO_NOGO_RE = re.compile("|".join(_GO_NOGO_PATTERNS), re.IGNORECASE)

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
    lines.append("Legs (in order):")
    for index, leg in enumerate(summary.legs, start=1):
        bits = [
            f"{index}. {leg.departure_time.strftime('%A %d %b %H:%MZ')} {leg.label}",
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
            f"Deterministic binding leg (you MUST name this one as the worst): "
            f"{binding.departure_time.strftime('%A')}'s {binding.label}"
        )
    else:
        lines.append("Deterministic binding leg: none — no remaining leg is gradeable yet.")
    lines.append(f"Deterministic sentence: {summary.headline}")
    if summary.continuity_warnings:
        lines.append(
            "Note: the chain does not join up geographically "
            f"({len(summary.continuity_warnings)} gap(s))."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------


def check_guardrail(text: str, summary: TripSummary) -> str | None:
    """Return a rejection reason, or None when the paragraph may be shown.

    Cheap and exact because the deterministic layer already knows the answer:
    the model's named worst leg must be the computed binding leg, and
    recommendation vocabulary is out. Sibling of ``digest.guardrails``.
    """
    if not text or not text.strip():
        return "empty"
    if len(text) > MAX_SUMMARY_CHARS:
        return "too_long"

    match = _GO_NOGO_RE.search(text)
    if match:
        return f"go/no-go vocabulary: {match.group(0)!r}"

    binding = next(
        (leg for leg in summary.legs if leg.flight_id == summary.binding_leg_id), None,
    )
    if binding is None:
        return None

    # The paragraph must mention the binding leg. Matched on the *ordered*
    # route rather than the exact label, so "LFAT to EGTF", "LFAT-EGTF" and
    # "LFAT → EGTF" all count as the same leg.
    if _mentions_leg(text, binding.origin, binding.destination) is False:
        return "named worst leg does not match the computed binding leg"

    # And it must not headline a *different* leg as the worst one. Any other
    # leg named alongside a superlative is the failure this guardrail exists
    # for; naming other legs neutrally is fine and expected.
    for leg in summary.legs:
        if leg.flight_id == summary.binding_leg_id:
            continue
        # Every *clause* naming the leg, not just the first sentence: a lead-in
        # can mention a leg well before the text that actually describes it, and
        # a clause boundary is what keeps a superlative attached to the leg it
        # was written about (see _fragments_naming).
        for window in _fragments_naming(text, leg.origin, leg.destination):
            if _SUPERLATIVE_RE.search(window):
                return "a leg other than the binding one is named as the worst"
    return None


#: Words that turn a mention of a leg into a claim that *it* is the problem.
#: ``difficult`` is load-bearing and easy to miss: ``prompts/trip_v1.md`` tells
#: the model to describe "what kind of problem the difficult leg has", so that
#: is the phrasing a mislabelled paragraph is most likely to use.
_SUPERLATIVE_RE = re.compile(
    r"\b(worst|difficult|hardest|toughest|problem(?:atic)?|decides|deciding|binding|"
    r"limiting|weakest|marginal)\b",
    re.IGNORECASE,
)


def _names_leg(fragment: str, origin: str | None, destination: str | None) -> bool:
    """Does ``fragment`` name the leg ``origin`` → ``destination``?

    **Order matters, and that is the whole point.** On a round trip both legs
    carry the same two ICAO codes — ``EGTF → LSGS`` out and ``LSGS → EGTF``
    back — so an unordered "does it mention both codes" test cannot tell them
    apart, and every sentence about the return would also read as a sentence
    about the outbound. Requiring the origin to appear *before* the
    destination separates them, which is what stops "Sunday's LSGS to EGTF is
    the difficult one" from being scored as a claim about Friday's outbound.
    """
    if not origin or not destination:
        return False
    # Word-boundary matched, like the superlative and go/no-go regexes: a bare
    # substring search would let an ICAO code match inside a longer token.
    first = re.search(rf"\b{re.escape(origin)}\b", fragment, re.IGNORECASE)
    if first is None:
        return False
    return re.search(
        rf"\b{re.escape(destination)}\b", fragment[first.end():], re.IGNORECASE,
    ) is not None


def _mentions_leg(text: str, origin: str | None, destination: str | None) -> bool:
    """Whether the paragraph names this leg anywhere (see :func:`_names_leg`)."""
    if not origin or not destination:
        # Nothing to match against — don't reject on an unknowable condition.
        return True
    return _names_leg(text, origin, destination)


#: Clause boundaries within a sentence. Splitting on these is what keeps the
#: superlative check *local* to the leg it is next to.
_CLAUSE_SPLIT_RE = re.compile(r"[,;:]|\s+(?:but|and|while|whereas|though|although)\s+|\s+[—–-]\s+")


def _fragments_naming(
    text: str, origin: str | None, destination: str | None,
) -> list[str]:
    """Every **clause** that names the leg ``origin`` → ``destination``.

    Clause-level, not sentence-level, and that distinction is what keeps the
    guardrail from rejecting correct paragraphs. Chain-adjacent legs share an
    airport — ``A → B`` then ``B → C`` — so in

        "Friday's A to B looks fine, but Saturday's B to C is the difficult one"

    the ordered match for ``A → B`` succeeds on the whole sentence (it contains
    A before B), and a sentence-wide search for "difficult" would then flag a
    perfectly *correct* paragraph as naming the wrong leg. Scoped to the clause,
    "difficult" stays attached to ``B → C``, where it belongs.
    """
    fragments: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        for clause in _CLAUSE_SPLIT_RE.split(sentence):
            if clause and _names_leg(clause, origin, destination):
                fragments.append(clause)
    return fragments


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(summary: TripSummary) -> tuple[str | None, dict]:
    """Call the model. Returns ``(text_or_None, token_usage)``. Never raises."""
    try:
        from weatherbrief.digest.llm_config import create_chat_model, load_digest_config

        config = load_digest_config()
        model = create_chat_model(config.trip)
        system = config.load_prompt("trip")
        result = model.invoke([
            {"role": "system", "content": system},
            {"role": "user", "content": build_context(summary)},
        ])
        text = getattr(result, "content", None)
        if isinstance(text, list):  # some providers return content blocks
            text = "".join(
                block.get("text", "") for block in text if isinstance(block, dict)
            )
        usage = getattr(result, "usage_metadata", None) or {}
        details = usage.get("input_token_details") or {}
        return (text or "").strip() or None, {
            "input_tokens": usage.get("input_tokens") or 0,
            "output_tokens": usage.get("output_tokens") or 0,
            "cache_read_tokens": details.get("cache_read") or 0,
            "cache_write_tokens": details.get("cache_creation") or 0,
        }
    except Exception:
        logger.warning("Trip AI summary generation failed", exc_info=True)
        return None, {}


def _charge(db: Session, user_id: str, trip_id: str, text: str, usage: dict) -> None:
    """Put the (tiny) cost through the ledger.

    A fraction of a cent per trip — three orders of magnitude below a briefing —
    and it still goes through ``compute_cost``, because an invisible cost line is
    how a small cost becomes an unexplained one. Never blocks.
    """
    try:
        from flyfun_common.costs import record_cost
        from weatherbrief.api.credits import SERVICE, get_active_cost_config
        from weatherbrief.costs import compute_cost, config_from_row

        config_row = get_active_cost_config(db)
        if not config_row:
            return
        config, config_id = config_from_row(config_row)
        breakdown = compute_cost(
            input_tokens=usage.get("input_tokens") or 0,
            output_tokens=usage.get("output_tokens") or 0,
            result_size_bytes=len(text.encode()),
            config=config,
            config_id=config_id,
            cache_read_tokens=usage.get("cache_read_tokens") or 0,
            cache_write_tokens=usage.get("cache_write_tokens") or 0,
        )
        record_cost(
            db,
            user_id,
            service=SERVICE,
            action="trip_summary",
            cost=breakdown.total_usd,
            category="trip_summary",
            description=f"Trip summary (${breakdown.total_usd:.4f})",
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
    force: bool = False,
    leg_inputs=None,
) -> TripAiResult:
    """Return the trip's AI paragraph, generating it only when stale.

    Called on a completed trip refresh and on demand when the trip page opens.
    ``force`` is for the refresh path, which knows the inputs just changed.
    ``leg_inputs`` lets a caller that already built them (every caller that
    computed ``summary``) avoid a second packs + debriefs query pair.
    """
    from weatherbrief.api.trips import ai_summary_key, build_leg_inputs

    if not members:
        return TripAiResult(unavailable_reason="no_legs")

    if leg_inputs is None:
        leg_inputs = build_leg_inputs(db, members)
    key = ai_summary_key(leg_inputs)
    if not force and row.ai_summary_text and row.ai_summary_key == key:
        return TripAiResult(text=row.ai_summary_text)

    if not legs_allow_ai(db, members, user_id):
        # Inherited gate: clear any stale text so a leg switched to AI-off
        # cannot keep being described by a paragraph written before the switch.
        row.ai_summary_text = None
        row.ai_summary_key = None
        row.ai_summary_at = None
        return TripAiResult(unavailable_reason="ai_disabled")

    text, usage = generate(summary)
    if text is None:
        return TripAiResult(unavailable_reason="generation_failed")

    rejection = check_guardrail(text, summary)
    if rejection is not None:
        logger.info("Trip %s: AI summary rejected (%s)", row.id, rejection)
        # Deliberately not persisted: the deterministic sentence stands on its
        # own, and storing a rejected paragraph would only invite showing it.
        _charge(db, user_id, row.id, text, usage)
        return TripAiResult(unavailable_reason="guardrail_rejected")

    row.ai_summary_text = text
    row.ai_summary_key = key
    row.ai_summary_at = datetime.now(timezone.utc)
    _charge(db, user_id, row.id, text, usage)
    return TripAiResult(text=text, regenerated=True)
