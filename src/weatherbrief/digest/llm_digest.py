"""LLM-powered weather digest using LangGraph.

Produces a structured WeatherDigest from quantitative forecast data
and regional text forecasts (NWS AFD or DWD) via an LLM briefer.
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
from weatherbrief.fetch.dwd_text import DWDDayBlock
from weatherbrief.fetch.text_forecasts import TextForecasts, fetch_text_forecasts
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


# --- LangGraph state ---


class DigestState(TypedDict, total=False):
    snapshot: ForecastSnapshot
    target_time: datetime
    config: DigestConfig
    previous_digest: WeatherDigest | None
    route_advisories: object | None  # RouteAdvisoriesManifest
    flight_rules: str | None
    text_forecasts: TextForecasts | None
    dwd_translated: list[tuple[DWDDayBlock, str]] | None
    context: str
    digest: WeatherDigest | None
    digest_text: str
    llm_input_tokens: int | None
    llm_output_tokens: int | None
    error: str | None


# --- Graph nodes ---


def fetch_text_node(state: DigestState) -> dict:
    """Fetch region-appropriate text forecasts (graceful failure)."""
    try:
        route = state["snapshot"].route
        text_forecasts = fetch_text_forecasts(route=route)
        return {"text_forecasts": text_forecasts}
    except Exception:
        logger.warning("Text forecast fetch failed", exc_info=True)
        return {"text_forecasts": None}


def translate_text_node(state: DigestState) -> dict:
    """Extract and translate DWD day blocks for European routes."""
    text_forecasts = state.get("text_forecasts")
    if text_forecasts is None or text_forecasts.region.value != "europe":
        return {"dwd_translated": None}

    try:
        from weatherbrief.digest.dwd_translate import translate_dwd_blocks
        from weatherbrief.fetch.dwd_text import DWDTextForecasts, get_dwd_day_blocks

        target_date = date.fromisoformat(state["snapshot"].target_date)
        config = state["config"]

        # Build a DWDTextForecasts from the entries already fetched
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

        # Extract day blocks relevant to flight date
        blocks = get_dwd_day_blocks(dwd_text, target_date)
        if not blocks:
            return {"dwd_translated": None}

        # Translate
        translated = translate_dwd_blocks(blocks, config)
        return {"dwd_translated": translated}

    except Exception:
        logger.warning("DWD translation failed, falling back to raw text", exc_info=True)
        return {"dwd_translated": None}


def assemble_context_node(state: DigestState) -> dict:
    """Combine quantitative snapshot + text forecasts into LLM context string."""
    context = build_digest_context(
        snapshot=state["snapshot"],
        target_time=state["target_time"],
        text_forecasts=state.get("text_forecasts"),
        previous_digest=state.get("previous_digest"),
        route_advisories=state.get("route_advisories"),
        flight_rules=state.get("flight_rules"),
        dwd_translated=state.get("dwd_translated"),
    )
    return {"context": context}


def briefer_node(state: DigestState) -> dict:
    """Call LLM with structured output to produce WeatherDigest."""
    config: DigestConfig = state["config"]
    try:
        llm = create_llm(config)
        structured_llm = llm.with_structured_output(WeatherDigest, include_raw=True)
        system_prompt = config.load_prompt("briefer")

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

        digest_text = format_digest_markdown(result, state["snapshot"])
        return {"digest": result, "digest_text": digest_text, **token_info}
    except Exception as e:
        logger.error("LLM digest generation failed", exc_info=True)
        return {"error": str(e)}


# --- Graph builder ---


def build_digest_graph(config: DigestConfig) -> CompiledStateGraph:
    """Build the LangGraph digest pipeline.

    Flow: fetch_text -> translate_text -> assemble -> briefer -> END
    """
    graph = StateGraph(DigestState)
    graph.add_node("fetch_text", fetch_text_node)
    graph.add_node("translate_text", translate_text_node)
    graph.add_node("assemble", assemble_context_node)
    graph.add_node("briefer", briefer_node)

    graph.add_edge(START, "fetch_text")
    graph.add_edge("fetch_text", "translate_text")
    graph.add_edge("translate_text", "assemble")
    graph.add_edge("assemble", "briefer")
    graph.add_edge("briefer", END)

    return graph.compile()


def run_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    config: DigestConfig,
    previous_digest: WeatherDigest | None = None,
    route_advisories=None,  # RouteAdvisoriesManifest | None
    flight_rules: str | None = None,
) -> DigestState:
    """Run the full digest pipeline and return final state."""
    graph = build_digest_graph(config)
    result = graph.invoke({
        "snapshot": snapshot,
        "target_time": target_time,
        "config": config,
        "previous_digest": previous_digest,
        "route_advisories": route_advisories,
        "flight_rules": flight_rules,
    })
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
