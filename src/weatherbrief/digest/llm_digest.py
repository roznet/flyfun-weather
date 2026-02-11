"""LLM-powered weather digest using LangGraph.

Produces a structured WeatherDigest from quantitative forecast data
and DWD text forecasts via an LLM briefer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from weatherbrief.digest.llm_config import DigestConfig, create_llm
from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.fetch.dwd_text import DWDTextForecasts, fetch_dwd_text_forecasts
from weatherbrief.models import ForecastSnapshot

logger = logging.getLogger(__name__)


# --- Structured output model ---


class WeatherDigest(BaseModel):
    """Structured LLM weather digest output."""

    assessment: Literal["GREEN", "AMBER", "RED"]
    assessment_reason: str
    synoptic: str
    winds: str
    cloud_visibility: str
    precipitation_convection: str
    icing: str
    specific_concerns: str
    model_agreement: str
    trend: str
    watch_items: str


# --- LangGraph state ---


class DigestState(TypedDict, total=False):
    snapshot: ForecastSnapshot
    target_time: datetime
    config: DigestConfig
    previous_digest: WeatherDigest | None
    text_forecasts: DWDTextForecasts | None
    context: str
    digest: WeatherDigest | None
    digest_text: str
    error: str | None


# --- Graph nodes ---


def fetch_text_node(state: DigestState) -> dict:
    """Fetch DWD text forecasts (graceful failure)."""
    try:
        text_forecasts = fetch_dwd_text_forecasts()
        return {"text_forecasts": text_forecasts}
    except Exception:
        logger.warning("DWD text forecast fetch failed", exc_info=True)
        return {"text_forecasts": None}


def assemble_context_node(state: DigestState) -> dict:
    """Combine quantitative snapshot + text forecasts into LLM context string."""
    context = build_digest_context(
        snapshot=state["snapshot"],
        target_time=state["target_time"],
        text_forecasts=state.get("text_forecasts"),
        previous_digest=state.get("previous_digest"),
    )
    return {"context": context}


def briefer_node(state: DigestState) -> dict:
    """Call LLM with structured output to produce WeatherDigest."""
    config: DigestConfig = state["config"]
    try:
        llm = create_llm(config)
        structured_llm = llm.with_structured_output(WeatherDigest)
        system_prompt = config.load_prompt("briefer")

        result = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["context"]},
        ])

        digest_text = format_digest_markdown(result, state["snapshot"])
        return {"digest": result, "digest_text": digest_text}
    except Exception as e:
        logger.error("LLM digest generation failed", exc_info=True)
        return {"error": str(e)}


# --- Graph builder ---


def build_digest_graph(config: DigestConfig) -> CompiledStateGraph:
    """Build the LangGraph digest pipeline."""
    graph = StateGraph(DigestState)
    graph.add_node("fetch_text", fetch_text_node)
    graph.add_node("assemble", assemble_context_node)
    graph.add_node("briefer", briefer_node)

    graph.add_edge(START, "fetch_text")
    graph.add_edge("fetch_text", "assemble")
    graph.add_edge("assemble", "briefer")
    graph.add_edge("briefer", END)

    return graph.compile()


def run_digest(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    config: DigestConfig,
    previous_digest: WeatherDigest | None = None,
) -> DigestState:
    """Run the full digest pipeline and return final state."""
    graph = build_digest_graph(config)
    result = graph.invoke({
        "snapshot": snapshot,
        "target_time": target_time,
        "config": config,
        "previous_digest": previous_digest,
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
        f"WINDS: {digest.winds}",
        "",
        f"CLOUD & VISIBILITY: {digest.cloud_visibility}",
        "",
        f"PRECIPITATION & CONVECTION: {digest.precipitation_convection}",
        "",
        f"ICING: {digest.icing}",
        "",
        f"SPECIFIC CONCERNS: {digest.specific_concerns}",
        "",
        f"MODELS: {digest.model_agreement}",
        "",
        f"TREND: {digest.trend}",
        "",
        f"WATCH: {digest.watch_items}",
        _SEPARATOR,
    ]
    return "\n".join(lines)
