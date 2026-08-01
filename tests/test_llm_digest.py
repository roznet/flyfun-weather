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


# ---------------------------------------------------------------------------
# Prompt caching: breakpoint placement and usage extraction
# ---------------------------------------------------------------------------


class TestSystemContentCacheBreakpoint:
    """``_system_content`` decides where (and whether) to cache.

    ``cache_control`` is Anthropic-only wire format. ``langchain-openai``
    currently drops the unknown key instead of erroring, so a regression here
    would not fail loudly — it would quietly ship a provider-specific field
    down the documented Anthropic<->OpenAI swap path.
    """

    def _cfg(self, provider="anthropic", model="claude-sonnet-4-6"):
        from weatherbrief.digest.llm_config import LLMConfig
        return LLMConfig(provider=provider, model=model)

    def test_anthropic_short_range_gets_a_breakpoint(self):
        from weatherbrief.digest.llm_digest import _system_content
        out = _system_content("PROMPT", self._cfg(), longrange=False)
        assert isinstance(out, list)
        assert out[0]["text"] == "PROMPT"
        assert out[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_openai_provider_gets_a_plain_string(self):
        from weatherbrief.digest.llm_digest import _system_content
        out = _system_content("PROMPT", self._cfg("openai", "gpt-4o"), longrange=False)
        assert out == "PROMPT"

    def test_longrange_gets_a_plain_string(self):
        """Haiku 4.5's 4096-token minimum is above the long-range prompt."""
        from weatherbrief.digest.llm_digest import _system_content
        out = _system_content("PROMPT", self._cfg(model="claude-haiku-4-5"), longrange=True)
        assert out == "PROMPT"

    def test_shipped_openai_config_never_gets_a_breakpoint(self):
        """Guards the real config, not just a synthetic one."""
        from weatherbrief.digest.llm_config import load_digest_config
        from weatherbrief.digest.llm_digest import _system_content
        cfg = load_digest_config("openai")
        assert _system_content("PROMPT", cfg.llm, longrange=False) == "PROMPT"


class TestCacheUsageExtraction:
    """The cache split must survive the langchain usage_metadata mapping.

    This is the half of the caching change that fails *silently*: if the
    ``cache_read`` / ``cache_creation`` keys are renamed upstream, the ``or 0``
    fallback reports zero cached tokens forever, the ledger reverts to
    full-price billing, and nothing raises. costs.py is well covered; this
    mapping was not.
    """

    def _run(self, usage_metadata):
        from unittest.mock import MagicMock, patch
        from weatherbrief.digest.llm_config import DigestConfig
        from weatherbrief.digest.llm_digest import briefer_node

        raw = MagicMock()
        raw.usage_metadata = usage_metadata
        structured = MagicMock()
        structured.invoke.return_value = {
            "raw": raw, "parsed": MagicMock(), "parsing_error": None,
        }
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        with patch("weatherbrief.digest.llm_digest.create_llm", return_value=llm):
            return briefer_node({
                "config": DigestConfig(), "context": "ctx", "longrange": False,
            })

    def test_cache_split_is_extracted(self):
        out = self._run({
            "input_tokens": 11478,
            "output_tokens": 1170,
            "input_token_details": {"cache_read": 4661, "cache_creation": 0},
        })
        assert out["llm_input_tokens"] == 11478
        assert out["llm_cache_read_tokens"] == 4661
        assert out["llm_cache_write_tokens"] == 0

    def test_cache_write_is_extracted(self):
        out = self._run({
            "input_tokens": 11478,
            "output_tokens": 1170,
            "input_token_details": {"cache_read": 0, "cache_creation": 4661},
        })
        assert out["llm_cache_write_tokens"] == 4661

    def test_cache_tokens_are_a_subset_of_input_tokens(self):
        """The invariant compute_cost relies on: never add these together.

        langchain-anthropic folds cached tokens *into* ``input_tokens``. If that
        ever changed, compute_cost's subtraction would be wrong — so pin it.
        """
        out = self._run({
            "input_tokens": 11478,
            "output_tokens": 1170,
            "input_token_details": {"cache_read": 4661, "cache_creation": 0},
        })
        assert out["llm_cache_read_tokens"] + out["llm_cache_write_tokens"] <= out["llm_input_tokens"]

    def test_missing_details_degrade_to_zero_not_none(self):
        """A provider without cache reporting must not write NULL columns."""
        out = self._run({"input_tokens": 900, "output_tokens": 100})
        assert out["llm_cache_read_tokens"] == 0
        assert out["llm_cache_write_tokens"] == 0
