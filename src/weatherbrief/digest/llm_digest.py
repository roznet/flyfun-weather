"""LLM-powered weather digest using LangGraph.

Produces a structured WeatherDigest from quantitative forecast data
and regional text forecasts (NWS AFD or DWD) via an LLM briefer.

Heavy data (ForecastSnapshot, text forecasts) is processed outside the
graph so that only the lightweight context string enters LangGraph state.
This keeps LangSmith trace payloads small (~100 KB instead of ~30 MB).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from weatherbrief.costs import DIGEST_CACHE_TTL, cache_write_multiplier_for
from weatherbrief.digest.exceptions import classify_llm_exception
from weatherbrief.digest.llm_config import DigestConfig, LLMConfig, create_llm
from weatherbrief.digest.outlook import OUTLOOK_ICONS, OUTLOOK_LABELS
from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.fetch.freshness.registry import (
    ECMWF_GRIB_SOURCE as _ECMWF_GRIB_SOURCE,
    first_full_coverage,
    max_horizon,
)
from weatherbrief.fetch.text_forecasts import fetch_text_forecasts
from weatherbrief.models import Diagnostic, ForecastSnapshot

logger = logging.getLogger(__name__)

# `_ECMWF_GRIB_SOURCE` is imported from the registry (see above): its horizon
# (168h on the 00/12Z full-horizon cycles) is the boundary between the full
# short-range briefing and the trimmed long-range outlook.


# --- Structured output models ---


class WeatherDigest(BaseModel):
    """Structured LLM weather digest output (short range, within GRIB horizon)."""

    assessment: Literal["GREEN", "AMBER", "RED"]
    assessment_reason: str
    synoptic: str
    specific_concerns: str
    trend: str
    watch_items: str


class LongRangeDigest(BaseModel):
    """Structured early long-range outlook (beyond the ECMWF GRIB horizon).

    A distinct, softer scale from the GREEN/AMBER/RED assessment: at this range
    confidence is driven by how well the two/three remaining global models agree,
    not by any single deterministic value, so the output is a *tendency* plus an
    explicit agreement read — never a go/no-go verdict.
    """

    outlook: Literal["TRENDING_SETTLED", "TRENDING_UNSETTLED", "MIXED_SIGNALS"]
    outlook_reason: str
    synoptic: str
    model_agreement: str
    trend: str
    watch_items: str


# --- LangGraph state (lightweight — no snapshot) ---


class DigestState(TypedDict, total=False):
    context: str
    config: DigestConfig
    locale: str | None
    guidance_key: str | None
    longrange: bool
    digest: WeatherDigest | LongRangeDigest | None
    digest_text: str
    llm_input_tokens: int | None
    llm_output_tokens: int | None
    llm_cache_read_tokens: int | None
    llm_cache_write_tokens: int | None
    diagnostic: Diagnostic | None


# --- Forecast-horizon regime ---


def ecmwf_grib_horizon_days() -> int:
    """Whole days the ECMWF GRIB feed reaches (168h → 7) — the regime boundary."""
    return int(max_horizon(_ECMWF_GRIB_SOURCE).total_seconds() // 86400)


def is_long_range(snapshot: ForecastSnapshot) -> bool:
    """True when the flight is beyond the ECMWF GRIB horizon (the long-range regime).

    Past this point the high-resolution ECMWF GRIB and ICON soundings no longer
    reach the flight date; only global models (ECMWF/GFS via Open-Meteo, GEM)
    remain, so we switch to the trimmed, cheaper long-range outlook.
    """
    return snapshot.days_out > ecmwf_grib_horizon_days()


def build_confidence_note(snapshot: ForecastSnapshot, target_time: datetime) -> str:
    """Code-computed 'more detail from <date>' note for the long-range context.

    The date is derived from the registry (first full-horizon ECMWF GRIB run that
    reaches ``target_time``) rather than asked of the LLM, which is unreliable at
    date arithmetic.
    """
    _, delivery = first_full_coverage(_ECMWF_GRIB_SOURCE, target_time)
    fetch_day = date.fromisoformat(snapshot.fetch_date[:10])
    days_until = (delivery.date() - fetch_day).days
    when = delivery.strftime("%A %d %b")
    horizon = " (~{} day{} from this briefing)".format(
        days_until, "" if days_until == 1 else "s"
    ) if days_until > 0 else ""
    return (
        f"This is an early outlook (D-{snapshot.days_out}). Beyond the ~"
        f"{ecmwf_grib_horizon_days()}-day ECMWF GRIB horizon only global models "
        "(ECMWF, GFS) reach this date, so confidence is low and details will "
        "shift run to run. High-resolution ECMWF GRIB guidance first covers this "
        f"flight from {when}{horizon}."
    )


# --- Graph node ---


def _cache_write_tokens(details: dict) -> int:
    """Cache-creation tokens from a langchain ``input_token_details`` block.

    Version-tolerant on purpose.  ``langchain-anthropic`` 1.3.x reported a
    TTL-tiered cache write in ``cache_creation``; 1.5.x zeroes that key and
    reports the count only under ``ephemeral_5m_input_tokens`` /
    ``ephemeral_1h_input_tokens``.  Reading ``cache_creation`` alone silently
    recorded every write as 0 on 1.5.x, which made ``compute_cost`` bill a 2x
    cache write as 1x uncached input — an undercharge, and invisible because
    nothing raises.

    The ``cache_creation`` branch wins when it is non-zero so 1.3.x is not
    double-counted: there it is populated *and* mirrored into the tiered keys.
    """
    write = details.get("cache_creation") or 0
    if write:
        return write
    return (
        (details.get("ephemeral_5m_input_tokens") or 0)
        + (details.get("ephemeral_1h_input_tokens") or 0)
    )


def _system_content(
    head: str,
    tail: str,
    llm_config: LLMConfig,
    longrange: bool,
    ttl: str = DIGEST_CACHE_TTL,
    locale_cacheable: bool = True,
) -> str | list[dict]:
    """System message content, with a prompt-cache breakpoint where it pays.

    Anthropic renders ``tools`` → ``system`` → ``messages``, so a breakpoint on
    the system block caches the structured-output tool schema *and* the whole
    system prompt — about 4.7k tokens, roughly 40% of a typical digest request.
    Everything after it (the pack context) stays uncached, which is what we
    want: it differs on every call.

    The default ``ttl`` comes from ``costs.DIGEST_CACHE_TTL`` — 1 hour, which
    is deliberate for production.  It costs 2x on a write against the 5-minute
    tier's 1.25x, but break-even is a hit rate of 53% vs 22%, and measured gaps
    between consecutive production digests clear the bar more comfortably at an
    hour than at five minutes (replaying real arrival gaps at 5m gives a 20%
    hit rate — below its own 21.7% break-even, i.e. worse than not caching).

    **Do not hardcode a TTL here.**  The ledger's ``cache_write_multiplier`` is
    derived from ``DIGEST_CACHE_TTL`` via ``CACHE_WRITE_MULTIPLIERS``, and the
    charged write price never travels with the write — cost is computed later,
    from a ``briefing_usage`` row that does not record which TTL produced its
    tokens.  Move the constant and the price follows; hardcode a string and
    every digest mis-prices its writes silently.  An unpriced TTL raises rather
    than billing at the wrong tier.

    Callers running a *burst* — the eval replays back-to-back, seconds apart —
    may pass ``ttl="5m"``: nothing expires inside a run, so the cheaper write
    premium wins outright.  That override is only legitimate because the eval
    does not write to the ledger.

    Three cases fall back to a plain string:

    * **A locale outside ``DigestConfig.cache_locales``** (``locale_cacheable``
      False).  Locale is injected before the head/tail split, so each one forks
      the cached head — and a locale too sparse to re-read its entry inside the
      TTL pays 2x for a write nobody reads.  Defaults True so the non-Anthropic
      and long-range paths, which never reach the breakpoint anyway, need not
      thread it through; the production caller passes it explicitly.

    * **Non-Anthropic providers.** ``cache_control`` is Anthropic-only wire
      format, and ``configs/weather_digest/openai.json`` points this same
      non-longrange block at ``gpt-4o``.  ``langchain-openai`` currently drops
      the unknown key rather than erroring, so the provider swap documented in
      ``designs/digest.md`` still works — but it works by accident, and stricter
      content-block validation upstream would turn that into a hard failure at
      exactly the moment you'd be reaching for the fallback.  Gate on the
      resolved provider rather than relying on the silent drop.
    * **Long-range digests**, which run on Haiku 4.5.  Its minimum cacheable
      prefix is 4096 tokens, well above that prompt's ~1.5k, so a breakpoint
      there caches nothing and silently costs nothing either.
    """
    if llm_config.provider != "anthropic" or longrange or not locale_cacheable:
        return head + tail
    cache_write_multiplier_for(ttl)  # reject a TTL the rate card cannot price
    # Breakpoint on the head only. Everything after it is uncached, which is
    # the point: the head is identical for every pilot, the guidance tail is
    # not, so all guidance presets share one cache entry instead of forking.
    blocks: list[dict] = [{
        "type": "text",
        "text": head,
        "cache_control": {"type": "ephemeral", "ttl": ttl},
    }]
    if tail:
        blocks.append({"type": "text", "text": tail})
    return blocks


def briefer_node(state: DigestState) -> dict:
    """Call LLM with structured output to produce WeatherDigest."""
    config: DigestConfig = state["config"]
    longrange = state.get("longrange", False)
    try:
        llm_config = config.longrange if longrange else config.llm
        llm = create_llm(config, longrange=longrange)
        schema = LongRangeDigest if longrange else WeatherDigest
        structured_llm = llm.with_structured_output(schema, include_raw=True)
        locale = state.get("locale")
        guidance_key = state.get("guidance_key")
        prompt_key = "briefer_longrange" if longrange else "briefer"
        head, tail = config.load_prompt_parts(
            prompt_key, locale=locale, guidance_key=guidance_key,
        )

        raw_result = structured_llm.invoke([
            {
                "role": "system",
                "content": _system_content(
                    head, tail, llm_config, longrange,
                    locale_cacheable=config.should_cache_locale(locale),
                ),
            },
            {"role": "user", "content": state["context"]},
        ])

        result = raw_result["parsed"]

        # Extract token usage from the raw AIMessage
        token_info: dict = {}
        raw_msg = raw_result.get("raw")
        if raw_msg is not None:
            usage_meta = getattr(raw_msg, "usage_metadata", None)
            if usage_meta:
                # NOTE: langchain-anthropic folds cached tokens *into*
                # ``input_tokens`` (Anthropic's own field excludes them), so
                # this is the true total and the two cache figures below are
                # subsets of it — never add them together.  costs.py splits
                # them back out to price each tier correctly.
                token_info["llm_input_tokens"] = usage_meta.get("input_tokens")
                token_info["llm_output_tokens"] = usage_meta.get("output_tokens")
                details = usage_meta.get("input_token_details") or {}
                token_info["llm_cache_read_tokens"] = details.get("cache_read") or 0
                token_info["llm_cache_write_tokens"] = _cache_write_tokens(details)

        return {"digest": result, **token_info}
    except Exception as e:
        diagnostic = classify_llm_exception(e)
        # Gate the log level by the diagnostic severity so transient
        # conditions like ANTHROPIC_RATE_LIMITED / ANTHROPIC_OVERLOADED
        # (warn-level — retryable, expected under load) don't trip
        # error-level alerting rules.
        log_fn = logger.error if diagnostic.level == "error" else logger.warning
        log_fn(
            "LLM digest generation failed (code=%s, error_id=%s, request_id=%s)",
            diagnostic.code, diagnostic.error_id, diagnostic.request_id,
            exc_info=True,
        )
        return {"diagnostic": diagnostic}


# --- Graph builder ---


def build_digest_graph(config: DigestConfig) -> CompiledStateGraph:
    """Build the LangGraph digest pipeline.

    The graph contains only the LLM briefer node.  All data preparation
    (text forecast fetching, DWD translation, context assembly) happens
    in run_digest() before graph.invoke() so the snapshot never enters
    the traced graph state.
    """
    graph = StateGraph(DigestState)
    graph.add_node("briefer", briefer_node)

    graph.add_edge(START, "briefer")
    graph.add_edge("briefer", END)

    return graph.compile()


def _fetch_and_translate_text(
    snapshot: ForecastSnapshot,
    config: DigestConfig,
    longrange: bool = False,
) -> tuple:
    """Fetch text forecasts and translate DWD blocks if applicable.

    Returns (text_forecasts, dwd_translated, dwd_is_synoptic_extract).

    In the long-range regime DWD blocks are matched ``strict``ly: a flight
    beyond DWD's ~7-day Mittelfrist horizon yields no block (rather than a stale
    fallback), so the LLM is never handed synoptic text that doesn't cover the
    flight day.
    """
    from weatherbrief.fetch.dwd_text import DWDDayBlock

    # Fetch text forecasts
    text_forecasts = None
    try:
        text_forecasts = fetch_text_forecasts(route=snapshot.route)
    except Exception:
        logger.warning("Text forecast fetch failed", exc_info=True)

    # Translate DWD blocks for European routes
    dwd_translated: list[tuple[DWDDayBlock, str]] | None = None
    synoptic_extract = False
    if text_forecasts is not None and text_forecasts.region.value == "europe":
        try:
            from weatherbrief.digest.dwd_translate import translate_dwd_blocks
            from weatherbrief.fetch.dwd_text import DWDTextForecasts, get_dwd_day_blocks

            target_date = date.fromisoformat(snapshot.target_date)
            dwd_text = DWDTextForecasts(
                short_range=next(
                    (e.text for e in text_forecasts.entries if "Kurzfrist" in e.label),
                    None,
                ),
                medium_range=next(
                    (e.text for e in text_forecasts.entries if "Mittelfrist" in e.label),
                    None,
                ),
                fetched_at=text_forecasts.fetched_at,
            )
            blocks = get_dwd_day_blocks(dwd_text, target_date, strict=longrange)
            if blocks:
                # Use synoptic extraction for non-German routes to avoid
                # the briefer LLM misapplying German regional details
                in_germany = any(
                    47.0 <= wp.lat <= 55.0 and 5.5 <= wp.lon <= 15.5
                    for wp in snapshot.route.waypoints
                    if wp.lat is not None and wp.lon is not None
                )
                synoptic_extract = not in_germany
                dwd_translated = translate_dwd_blocks(
                    blocks, config, synoptic_extract=synoptic_extract,
                )
        except Exception:
            logger.warning("DWD translation failed, falling back to raw text", exc_info=True)

    return text_forecasts, dwd_translated, synoptic_extract


def run_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    config: DigestConfig,
    previous_digest: WeatherDigest | None = None,
    route_advisories=None,  # RouteAdvisoriesManifest | None
    altitude_table=None,  # AltitudeTableResult | None
    flight_rules: str | None = None,
    locale: str | None = None,
    units_region: str | None = None,
    guidance_key: str | None = None,
    longrange: bool | None = None,
) -> DigestState:
    """Run the full digest pipeline and return final state.

    Data preparation (text fetching, translation, context assembly) runs
    outside the graph so only the context string is traced by LangSmith.

    ``longrange`` forces the regime; when ``None`` (default) it is auto-detected
    from the flight's lead time via :func:`is_long_range`. Beyond the ECMWF GRIB
    horizon the briefer runs the cheaper model on a trimmed prompt and emits a
    :class:`LongRangeDigest` (outlook) instead of a :class:`WeatherDigest`.
    """
    if longrange is None:
        longrange = is_long_range(snapshot)

    # --- Pre-graph data preparation (not traced) ---
    text_forecasts, dwd_translated, dwd_is_synoptic_extract = (
        _fetch_and_translate_text(snapshot, config, longrange=longrange)
    )

    confidence_note = (
        build_confidence_note(snapshot, target_time) if longrange else None
    )
    context = build_digest_context(
        snapshot=snapshot,
        target_time=target_time,
        text_forecasts=text_forecasts,
        previous_digest=previous_digest,
        route_advisories=route_advisories,
        altitude_table=altitude_table,
        flight_rules=flight_rules,
        units_region=units_region,
        dwd_translated=dwd_translated,
        dwd_is_synoptic_extract=dwd_is_synoptic_extract,
        longrange=longrange,
        confidence_note=confidence_note,
    )

    # --- LLM call via graph (traced — lightweight state) ---
    # Pre-generate the root run id so we own it and can persist it with the
    # pack. ``run_id`` in the RunnableConfig sets the id of the trace this
    # invocation produces in LangSmith, which is what digest thumb feedback
    # later attaches to (issue #244). Pass a UUID object (not a str) to match
    # RunnableConfig.run_id's declared Optional[UUID] type — relying on string
    # coercion would silently break the trace<->feedback link if a pinned
    # LangChain stopped coercing. Generated unconditionally — harmless when
    # tracing is off locally (no trace is created), persisted regardless.
    trace_id = uuid4()
    graph = build_digest_graph(config)
    result = graph.invoke(
        {
            "context": context,
            "config": config,
            "locale": locale,
            "guidance_key": guidance_key,
            "longrange": longrange,
        },
        config={"run_id": trace_id},
    )
    result["digest_trace_id"] = str(trace_id)
    result["longrange"] = longrange

    # --- Post-graph formatting (not traced) ---
    if result.get("digest") is not None:
        if longrange:
            result["digest_text"] = format_longrange_markdown(
                result["digest"], snapshot, confidence_note,
            )
        else:
            result["digest_text"] = format_digest_markdown(result["digest"], snapshot)

    # Carry dwd_translated through to the caller for persistence. It's
    # intentionally kept out of the LangGraph state (commit 2589691d, to keep
    # LangSmith trace payloads small), so we attach it post-graph instead.
    result["dwd_translated"] = dwd_translated

    # Carry the exact context string (the user message sent to the LLM) out for
    # persistence. Saving it in the pack lets the digest eval (#254) build
    # byte-faithful fixtures — the reconstructed-from-snapshot context omits the
    # text-forecast / previous-digest sections the LLM actually saw.
    result["digest_context"] = context

    return result


# --- Markdown formatter ---

_ASSESSMENT_ICONS = {
    "GREEN": "\U0001f7e2",   # green circle
    "AMBER": "\U0001f7e0",   # orange circle
    "RED": "\U0001f534",      # red circle
}

_SEPARATOR = "=" * 55


def format_digest_markdown(
    digest: WeatherDigest,
    snapshot: ForecastSnapshot,
) -> str:
    """Format a WeatherDigest into the spec's output format."""
    waypoints = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    icon = _ASSESSMENT_ICONS.get(digest.assessment, "")

    lines = [
        _SEPARATOR,
        f"  {waypoints}",
        f"  Target: {snapshot.target_date}  FL{snapshot.route.cruise_altitude_ft // 100:03d}",
        f"  D-{snapshot.days_out}  Fetched: {snapshot.fetch_date}",
        _SEPARATOR,
        "",
        f"{icon} {digest.assessment} — {digest.assessment_reason}",
        "",
        f"SYNOPTIC: {digest.synoptic}",
        "",
        f"SPECIFIC CONCERNS: {digest.specific_concerns}",
        "",
        f"TREND: {digest.trend}",
        "",
        f"WATCH: {digest.watch_items}",
        _SEPARATOR,
    ]
    return "\n".join(lines)


def format_longrange_markdown(
    digest: LongRangeDigest,
    snapshot: ForecastSnapshot,
    confidence_note: str | None = None,
) -> str:
    """Format a LongRangeDigest into the same fixed-width layout as the digest."""
    waypoints = " -> ".join(wp.icao for wp in snapshot.route.waypoints)
    icon = OUTLOOK_ICONS.get(digest.outlook, "")
    label = f"{icon} {OUTLOOK_LABELS.get(digest.outlook, digest.outlook)}".strip()

    lines = [
        _SEPARATOR,
        f"  {waypoints}",
        f"  Target: {snapshot.target_date}  FL{snapshot.route.cruise_altitude_ft // 100:03d}",
        f"  D-{snapshot.days_out}  Fetched: {snapshot.fetch_date}  (early outlook)",
        _SEPARATOR,
        "",
        f"{label} — {digest.outlook_reason}",
        "",
        f"SYNOPTIC: {digest.synoptic}",
        "",
        f"MODEL AGREEMENT: {digest.model_agreement}",
        "",
        f"TREND: {digest.trend}",
        "",
        f"WATCH: {digest.watch_items}",
        _SEPARATOR,
    ]
    return "\n".join(lines)
