"""Tests for LLM digest graph with mocked LLM."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.digest.llm_config import DigestConfig
from weatherbrief.digest.llm_digest import (
    DigestState,
    WeatherDigest,
    assemble_context_node,
    build_digest_graph,
    fetch_text_node,
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
        winds="Light and variable at FL080.",
        cloud_visibility="Scattered high cloud only.",
        precipitation_convection="No precipitation expected.",
        icing="Negligible risk at FL080.",
        specific_concerns="Morning valley fog at LSGS.",
        model_agreement="GFS and ECMWF in strong agreement.",
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
    assert "WINDS:" in text
    assert "ICING:" in text
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


@patch("weatherbrief.digest.llm_digest.fetch_dwd_text_forecasts")
def test_fetch_text_node_success(mock_fetch):
    """fetch_text_node returns text forecasts on success."""
    from weatherbrief.fetch.dwd_text import DWDTextForecasts

    mock_fetch.return_value = DWDTextForecasts(
        short_range="Test short",
        medium_range="Test medium",
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    result = fetch_text_node({})
    assert result["text_forecasts"].short_range == "Test short"


@patch("weatherbrief.digest.llm_digest.fetch_dwd_text_forecasts")
def test_fetch_text_node_failure(mock_fetch):
    """fetch_text_node returns None on failure."""
    mock_fetch.side_effect = Exception("DWD down")

    result = fetch_text_node({})
    assert result["text_forecasts"] is None


def test_assemble_context_node(minimal_snapshot):
    """assemble_context_node produces a non-empty context string."""
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    state: DigestState = {
        "snapshot": minimal_snapshot,
        "target_time": target_time,
    }
    result = assemble_context_node(state)
    assert "context" in result
    assert len(result["context"]) > 0
    assert "EGTK" in result["context"]


@patch("weatherbrief.digest.llm_digest.create_llm")
@patch("weatherbrief.digest.llm_digest.fetch_dwd_text_forecasts")
def test_run_digest_full_graph(mock_dwd, mock_create_llm, minimal_snapshot, sample_digest):
    """Full graph execution with mocked LLM produces a digest."""
    from weatherbrief.fetch.dwd_text import DWDTextForecasts

    # Mock DWD
    mock_dwd.return_value = DWDTextForecasts(
        short_range="Test",
        medium_range=None,
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    # Mock LLM
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.invoke.return_value = sample_digest
    mock_llm.with_structured_output.return_value = mock_structured
    mock_create_llm.return_value = mock_llm

    config = DigestConfig()
    target_time = datetime(2026, 2, 17, 9, 0, 0)

    result = run_digest(minimal_snapshot, target_time, config)

    assert result["digest"] is not None
    assert result["digest"].assessment == "GREEN"
    assert result["digest_text"] is not None
    assert "GREEN" in result["digest_text"]
    assert result.get("error") is None


@patch("weatherbrief.digest.llm_digest.create_llm")
@patch("weatherbrief.digest.llm_digest.fetch_dwd_text_forecasts")
def test_run_digest_llm_failure(mock_dwd, mock_create_llm, minimal_snapshot):
    """Graph handles LLM failure gracefully."""
    from weatherbrief.fetch.dwd_text import DWDTextForecasts

    mock_dwd.return_value = DWDTextForecasts(
        short_range=None,
        medium_range=None,
        fetched_at=datetime(2026, 2, 10, 12, 0, 0, tzinfo=timezone.utc),
    )

    mock_create_llm.side_effect = Exception("API key invalid")

    config = DigestConfig()
    target_time = datetime(2026, 2, 17, 9, 0, 0)

    result = run_digest(minimal_snapshot, target_time, config)

    assert result.get("error") is not None
    assert "API key invalid" in result["error"]


def test_weather_digest_model():
    """WeatherDigest model validates correctly."""
    digest = WeatherDigest(
        assessment="AMBER",
        assessment_reason="Frontal passage uncertain",
        synoptic="Low from west.",
        winds="25kt headwind.",
        cloud_visibility="BKN 3000ft.",
        precipitation_convection="Light rain.",
        icing="Moderate at 5000ft.",
        specific_concerns="Alpine foehn.",
        model_agreement="Models diverge.",
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
            winds="test",
            cloud_visibility="test",
            precipitation_convection="test",
            icing="test",
            specific_concerns="test",
            model_agreement="test",
            trend="test",
            watch_items="test",
        )
