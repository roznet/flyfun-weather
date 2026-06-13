"""Tests for LLM digest graph with mocked LLM."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.digest.llm_config import DigestConfig
from weatherbrief.digest.llm_digest import (
    DigestState,
    WeatherDigest,
    build_digest_graph,
    format_digest_markdown,
    run_digest,
)
from weatherbrief.models import (
    ForecastSnapshot,
    HourlyForecast,
    ModelSource,
    RouteConfig,
    Waypoint,
    WaypointAnalysis,
    WaypointForecast,
)


@pytest.fixture
def sample_digest():
    """A sample WeatherDigest for formatting tests."""
    return WeatherDigest(
        assessment="GREEN",
        assessment_reason="Ridge firmly established, models converging",
        synoptic="High pressure centered over Bay of Biscay.",
        specific_concerns="Morning valley fog at LSGS.",
        trend="Improving since D-5.",
        watch_items="Sion valley fog — check 0600Z TAF.",
    )


@pytest.fixture
def minimal_snapshot(sample_route):
    """Minimal snapshot for graph tests."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    return ForecastSnapshot(
        route=sample_route,
        target_date="2026-02-17",
        fetch_date="2026-02-10",
        days_out=7,
        forecasts=[],
        analyses=[],
    )


def test_format_digest_markdown(sample_digest, sample_route):
    """Markdown formatter produces expected output structure."""
    snapshot = ForecastSnapshot(
        route=sample_route,
        target_date="2026-02-17",
        fetch_date="2026-02-10",
        days_out=7,
    )

    text = format_digest_markdown(sample_digest, snapshot)

    assert "EGTK -> LFPB -> LSGS" in text
    assert "2026-02-17" in text
    assert "D-7" in text
    assert "GREEN" in text
    assert "Ridge firmly established" in text
    assert "SYNOPTIC:" in text
    assert "SPECIFIC CONCERNS:" in text
    assert "WATCH:" in text


def test_format_digest_assessment_icons(sample_digest, sample_route):
    """Assessment icons are correct for each level."""
    snapshot = ForecastSnapshot(
        route=sample_route,
        target_date="2026-02-17",
        fetch_date="2026-02-10",
        days_out=7,
    )

    # GREEN
    text = format_digest_markdown(sample_digest, snapshot)
    assert "\U0001f7e2" in text  # green circle

    # AMBER
    amber_digest = sample_digest.model_copy(update={"assessment": "AMBER"})
    text = format_digest_markdown(amber_digest, snapshot)
    assert "\U0001f7e0" in text  # orange circle

    # RED
    red_digest = sample_digest.model_copy(update={"assessment": "RED"})
    text = format_digest_markdown(red_digest, snapshot)
    assert "\U0001f534" in text  # red circle


@patch("weatherbrief.digest.llm_digest.create_llm")
@patch("weatherbrief.digest.llm_digest.fetch_text_forecasts")
def test_run_digest_full_graph(mock_fetch_text, mock_create_llm, minimal_snapshot, sample_digest):
    """Full graph execution with mocked LLM produces a digest."""
    from weatherbrief.fetch.text_forecasts import (
        ForecastRegion,
        TextForecastEntry,
        TextForecasts,
    )

    # Mock text forecasts
    mock_fetch_text.return_value = TextForecasts(
        region=ForecastRegion.EUROPE,
        source_label="DWD Synoptic Overview",
        language_note="German — translate relevant content",
        entries=[TextForecastEntry(label="Kurzfrist", text="Test")],
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    # Mock LLM — with_structured_output(include_raw=True) returns
    # {"raw": AIMessage, "parsed": WeatherDigest, "parsing_error": None}
    mock_llm = MagicMock()
    mock_raw_msg = MagicMock()
    mock_raw_msg.usage_metadata = {"input_tokens": 1000, "output_tokens": 200}
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = {
        "raw": mock_raw_msg,
        "parsed": sample_digest,
        "parsing_error": None,
    }
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    config = DigestConfig()
    target_time = datetime(2026, 2, 17, 9, 0, 0)

    result = run_digest(minimal_snapshot, target_time, config)

    assert result["digest"] is not None
    assert result["digest"].assessment == "GREEN"
    assert result["digest_text"] is not None
    assert "GREEN" in result["digest_text"]
    assert result.get("diagnostic") is None
    assert result.get("llm_input_tokens") == 1000
    assert result.get("llm_output_tokens") == 200

    # A controlled root run_id is generated and returned for LangSmith feedback
    # (issue #244). It must be a parseable UUID and be passed as the graph's
    # root run_id so the trace can be located later.
    from uuid import UUID
    trace_id = result.get("digest_trace_id")
    assert trace_id is not None
    UUID(trace_id)  # raises if not a valid UUID


@patch("weatherbrief.digest.llm_digest.create_llm")
@patch("weatherbrief.digest.llm_digest.fetch_text_forecasts")
def test_run_digest_llm_failure(mock_fetch_text, mock_create_llm, minimal_snapshot):
    """Graph handles LLM failure gracefully and surfaces a typed Diagnostic."""
    from weatherbrief.models import DigestCode

    mock_fetch_text.return_value = None

    mock_create_llm.side_effect = Exception("API key invalid")

    config = DigestConfig()
    target_time = datetime(2026, 2, 17, 9, 0, 0)

    result = run_digest(minimal_snapshot, target_time, config)

    diagnostic = result.get("diagnostic")
    assert diagnostic is not None
    # Falls through to the catch-all (not an anthropic.* exception).
    # DIGEST_UNKNOWN is `warn` per the level convention — the message
    # tells users to retry, so the level agrees.
    assert diagnostic.code == DigestCode.DIGEST_UNKNOWN
    assert diagnostic.stage == "digest"
    assert diagnostic.level == "warn"
    # Original exception text appears in the redacted/capped detail
    assert "API key invalid" in (diagnostic.detail or "")


def test_weather_digest_model():
    """WeatherDigest model validates correctly."""
    digest = WeatherDigest(
        assessment="AMBER",
        assessment_reason="Frontal passage uncertain",
        synoptic="Low from west.",
        specific_concerns="Alpine foehn.",
        trend="Deteriorating.",
        watch_items="TAF updates.",
    )
    assert digest.assessment == "AMBER"

    # Invalid assessment value
    with pytest.raises(Exception):
        WeatherDigest(
            assessment="BLUE",
            assessment_reason="test",
            synoptic="test",
            specific_concerns="test",
            trend="test",
            watch_items="test",
        )
