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

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from weatherbrief.digest.llm_config import DigestConfig, create_llm
from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.fetch.text_forecasts import fetch_text_forecasts
from weatherbrief.models import Diagnostic, DigestCode, ForecastSnapshot

logger = logging.getLogger(__name__)


# --- Structured output model ---


class WeatherDigest(BaseModel):
    """Structured LLM weather digest output."""

    assessment: Literal["GREEN", "AMBER", "RED"]
    assessment_reason: str
    synoptic: str
    specific_concerns: str
    trend: str
    watch_items: str


# --- LangGraph state (lightweight — no snapshot) ---


class DigestState(TypedDict, total=False):
    context: str
    config: DigestConfig
    locale: str | None
    guidance_key: str | None
    digest: WeatherDigest | None
    digest_text: str
    llm_input_tokens: int | None
    llm_output_tokens: int | None
    diagnostic: Diagnostic | None


# --- Graph node ---


def _classify_llm_exception(exc: Exception) -> Diagnostic:
    """Map a raised LLM-call exception to a typed Diagnostic.

    Anthropic's exception hierarchy is checked from most specific (status
    codes) to most general (connection/timeout, then base APIError, then
    Exception fallback). Each kind gets a stable code and a friendly
    user-facing message; the raw exception goes into ``detail`` (capped and
    redacted at the model boundary).
    """
    import traceback

    request_id: str | None = None
    try:
        import anthropic  # local — module is heavy and only needed when classifying
    except ImportError:
        anthropic = None  # type: ignore[assignment]

    detail = traceback.format_exc()

    if anthropic is not None:
        # Best-effort request id (present on APIStatusError subclasses)
        request_id = getattr(exc, "request_id", None)

        if (
            isinstance(exc, anthropic.APIStatusError)
            and getattr(exc, "status_code", None) == 529
        ):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_OVERLOADED,
                message="AI weather digest unavailable — Anthropic API overloaded. Try refreshing again shortly.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.RateLimitError):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_RATE_LIMITED,
                message="AI weather digest unavailable — rate-limited by Anthropic. Try refreshing again in a moment.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.APITimeoutError):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_TIMEOUT,
                message="AI weather digest unavailable — Anthropic API timed out. Try refreshing again.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_CONNECTION,
                message="AI weather digest unavailable — could not reach Anthropic API. Try refreshing again.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.BadRequestError):
            # 400 from Anthropic — usually a server-side prompt/config bug;
            # retrying won't help.
            return Diagnostic.create(
                level="error", stage="digest",
                code=DigestCode.DIGEST_BAD_REQUEST,
                message="AI weather digest unavailable — internal request error.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.InternalServerError) or (
            isinstance(exc, anthropic.APIStatusError)
            and 500 <= (getattr(exc, "status_code", 0) or 0) < 600
        ):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_INTERNAL_ERROR,
                message="AI weather digest unavailable — Anthropic API error. Try refreshing again in a few minutes.",
                detail=detail,
                request_id=request_id,
            )

    return Diagnostic.create(
        level="error", stage="digest",
        code=DigestCode.DIGEST_UNKNOWN,
        message="AI weather digest unavailable — unexpected error. Try refreshing again later.",
        detail=detail,
        request_id=request_id,
    )


def briefer_node(state: DigestState) -> dict:
    """Call LLM with structured output to produce WeatherDigest."""
    config: DigestConfig = state["config"]
    try:
        llm = create_llm(config)
        structured_llm = llm.with_structured_output(WeatherDigest, include_raw=True)
        locale = state.get("locale")
        guidance_key = state.get("guidance_key")
        system_prompt = config.load_prompt(
            "briefer", locale=locale, guidance_key=guidance_key,
        )

        raw_result = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["context"]},
        ])

        result: WeatherDigest = raw_result["parsed"]

        # Extract token usage from the raw AIMessage
        token_info: dict = {}
        raw_msg = raw_result.get("raw")
        if raw_msg is not None:
            usage_meta = getattr(raw_msg, "usage_metadata", None)
            if usage_meta:
                token_info["llm_input_tokens"] = usage_meta.get("input_tokens")
                token_info["llm_output_tokens"] = usage_meta.get("output_tokens")

        return {"digest": result, **token_info}
    except Exception as e:
        diagnostic = _classify_llm_exception(e)
        logger.error(
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
) -> tuple:
    """Fetch text forecasts and translate DWD blocks if applicable.

    Returns (text_forecasts, dwd_translated, dwd_is_synoptic_extract).
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
            blocks = get_dwd_day_blocks(dwd_text, target_date)
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
    flight_rules: str | None = None,
    locale: str | None = None,
    guidance_key: str | None = None,
) -> DigestState:
    """Run the full digest pipeline and return final state.

    Data preparation (text fetching, translation, context assembly) runs
    outside the graph so only the context string is traced by LangSmith.
    """
    # --- Pre-graph data preparation (not traced) ---
    text_forecasts, dwd_translated, dwd_is_synoptic_extract = (
        _fetch_and_translate_text(snapshot, config)
    )

    context = build_digest_context(
        snapshot=snapshot,
        target_time=target_time,
        text_forecasts=text_forecasts,
        previous_digest=previous_digest,
        route_advisories=route_advisories,
        flight_rules=flight_rules,
        dwd_translated=dwd_translated,
        dwd_is_synoptic_extract=dwd_is_synoptic_extract,
    )

    # --- LLM call via graph (traced — lightweight state) ---
    graph = build_digest_graph(config)
    result = graph.invoke({
        "context": context,
        "config": config,
        "locale": locale,
        "guidance_key": guidance_key,
    })

    # --- Post-graph formatting (not traced) ---
    if result.get("digest") is not None:
        result["digest_text"] = format_digest_markdown(result["digest"], snapshot)

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
