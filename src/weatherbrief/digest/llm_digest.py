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
from weatherbrief.models import ForecastSnapshot

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
    error: str | None


# --- Graph node ---


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
        logger.error("LLM digest generation failed", exc_info=True)
        return {"error": str(e)}


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

    Returns (text_forecasts, dwd_translated).
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
                dwd_translated = translate_dwd_blocks(
                    blocks, config, synoptic_extract=not in_germany,
                )
        except Exception:
            logger.warning("DWD translation failed, falling back to raw text", exc_info=True)

    return text_forecasts, dwd_translated


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
    text_forecasts, dwd_translated = _fetch_and_translate_text(snapshot, config)

    context = build_digest_context(
        snapshot=snapshot,
        target_time=target_time,
        text_forecasts=text_forecasts,
        previous_digest=previous_digest,
        route_advisories=route_advisories,
        flight_rules=flight_rules,
        dwd_translated=dwd_translated,
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
