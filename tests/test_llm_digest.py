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
        out = _system_content("HEAD", "TAIL", self._cfg(), longrange=False)
        assert isinstance(out, list)
        # Breakpoint on the head only — the guidance tail must stay uncached,
        # or every guidance preset forks into its own cache entry again.
        assert out[0]["text"] == "HEAD"
        # Literal on purpose: this is the tripwire that makes a TTL change an
        # explicit act. The write *price* follows the constant automatically
        # (costs.DIGEST_CACHE_TTL -> CACHE_WRITE_MULTIPLIERS, guarded in
        # tests/test_costs.py::TestCacheTtlPricingCoupling); this line just
        # stops one drifting in unnoticed.
        assert out[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert out[1]["text"] == "TAIL"
        assert "cache_control" not in out[1]

    def test_openai_provider_gets_a_plain_string(self):
        from weatherbrief.digest.llm_digest import _system_content
        out = _system_content("HEAD", "TAIL", self._cfg("openai", "gpt-4o"), longrange=False)
        assert out == "HEADTAIL"

    def test_longrange_gets_a_plain_string(self):
        """Haiku 4.5's 4096-token minimum is above the long-range prompt."""
        from weatherbrief.digest.llm_digest import _system_content
        out = _system_content("HEAD", "TAIL", self._cfg(model="claude-haiku-4-5"), longrange=True)
        assert out == "HEADTAIL"

    def test_shipped_openai_config_never_gets_a_breakpoint(self):
        """Guards the real config, not just a synthetic one."""
        from weatherbrief.digest.llm_config import load_digest_config
        from weatherbrief.digest.llm_digest import _system_content
        cfg = load_digest_config("openai")
        assert _system_content("HEAD", "TAIL", cfg.llm, longrange=False) == "HEADTAIL"

    def test_uncacheable_locale_gets_a_plain_string(self):
        """A locale off the allowlist bills 1x rather than 2x for a dead write."""
        from weatherbrief.digest.llm_digest import _system_content
        out = _system_content(
            "HEAD", "TAIL", self._cfg(), longrange=False, locale_cacheable=False,
        )
        assert out == "HEADTAIL"


class TestCacheLocaleAllowlist:
    """Which locales earn a cache breakpoint.

    Locale is injected *before* the head/tail split, so unlike guidance it
    forks the cached head. The allowlist keeps sparse locales from paying the
    2x write premium on an entry that expires unread — measured on production
    2026-08-11 as `de`: 6 digests / 11 days, 0 hits, 2 wasted writes.
    """

    def test_none_and_en_and_unknown_all_resolve_together(self):
        """The three render a byte-identical head, so they must decide alike."""
        from weatherbrief.digest.llm_config import DigestConfig, resolve_locale_key
        assert resolve_locale_key(None) == "en"
        assert resolve_locale_key("en") == "en"
        # No zz.md on disk -> falls back to English, so it shares the entry.
        assert resolve_locale_key("zz") == "en"

        cfg = DigestConfig()
        heads = {cfg.load_prompt_parts("briefer", locale=loc)[0]
                 for loc in (None, "en", "zz")}
        assert len(heads) == 1, "None/en/unknown must render one identical head"
        assert all(cfg.should_cache_locale(loc) for loc in (None, "en", "zz"))

    def test_translated_locale_is_excluded_by_default(self):
        from weatherbrief.digest.llm_config import DigestConfig
        cfg = DigestConfig()
        assert cfg.should_cache_locale("de") is False
        # ...and it really is a different head, i.e. a real fork being avoided.
        assert (cfg.load_prompt_parts("briefer", locale="de")[0]
                != cfg.load_prompt_parts("briefer", locale="en")[0])

    def test_allowlist_is_configurable_without_a_code_change(self):
        """Turning a locale on once it earns its keep is a config edit."""
        from weatherbrief.digest.llm_config import DigestConfig
        assert DigestConfig(cache_locales=["en", "de"]).should_cache_locale("de")

    def test_shipped_default_config_caches_english_only(self):
        from weatherbrief.digest.llm_config import load_digest_config
        cfg = load_digest_config()
        assert cfg.cache_locales == ["en"]
        assert cfg.should_cache_locale(None) is True
        assert all(cfg.should_cache_locale(loc) is False for loc in ("de", "es", "fr"))

    def _sent_content(self, locale):
        """What ``briefer_node`` actually puts on the wire for ``locale``."""
        from unittest.mock import MagicMock, patch
        from weatherbrief.digest.llm_config import DigestConfig
        from weatherbrief.digest.llm_digest import briefer_node

        structured = MagicMock()
        structured.invoke.return_value = {
            "raw": None, "parsed": MagicMock(), "parsing_error": None,
        }
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        with patch("weatherbrief.digest.llm_digest.create_llm", return_value=llm):
            briefer_node({
                "config": DigestConfig(), "context": "ctx",
                "longrange": False, "locale": locale,
            })
        return structured.invoke.call_args[0][0][0]["content"]

    def test_call_site_actually_threads_the_flag(self):
        """The gate is worthless if ``briefer_node`` forgets to pass it.

        A config flag wired to nothing fails silently — exactly how the
        ``head``/``tail`` split broke the eval call site. Assert on the wire
        format, not on the predicate.
        """
        assert isinstance(self._sent_content(None), list)      # cached: blocks
        assert isinstance(self._sent_content("en"), list)
        assert isinstance(self._sent_content("de"), str)       # uncached: plain


class TestEvalCacheCallSite:
    """The eval is the only legitimate caller of a non-production TTL.

    It lives in ``scripts/``, outside the import graph pytest walks, so a
    signature change to ``_system_content`` cannot break it visibly — the
    ``head``/``tail`` split did exactly that and the cached eval path has been
    raising ``TypeError`` ever since. This exercises the call site with the LLM
    mocked out.
    """

    def _run_one(self):
        import importlib.util
        from pathlib import Path

        script = Path(__file__).resolve().parent.parent / "scripts" / "run_digest_eval.py"
        spec = importlib.util.spec_from_file_location("run_digest_eval_ttl", script)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _invoke(self, module, *, cache):
        structured = MagicMock()
        structured.invoke.return_value = {"raw": None, "parsed": MagicMock()}
        llm = MagicMock()
        llm.with_structured_output.return_value = structured
        with patch.object(module, "create_llm", return_value=llm):
            module.run_one("ctx", "SYSTEM PROMPT", DigestConfig(), cache=cache)
        return structured.invoke.call_args[0][0][0]["content"]

    def test_cached_eval_call_still_matches_the_signature(self):
        module = self._run_one()
        content = self._invoke(module, cache=True)
        assert isinstance(content, list)
        assert content[0]["text"] == "SYSTEM PROMPT"
        # 5m, not production's 1h: a burst never expires, and the eval writes
        # no ledger row, so the cheaper write premium is free to take.
        assert content[0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}

    def test_uncached_eval_call_sends_a_plain_string(self):
        module = self._run_one()
        assert self._invoke(module, cache=False) == "SYSTEM PROMPT"


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

    def test_cache_write_langchain_1_3_shape(self):
        """1.3.x populated ``cache_creation`` and mirrored it into the tiers."""
        out = self._run({
            "input_tokens": 11478,
            "output_tokens": 1170,
            "input_token_details": {
                "cache_read": 0, "cache_creation": 4661,
                "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 4661,
            },
        })
        # Must not double-count the mirrored tier key.
        assert out["llm_cache_write_tokens"] == 4661

    def test_cache_write_langchain_1_5_shape(self):
        """1.5.x zeroes ``cache_creation`` and reports only the tiered key.

        Observed live on prod's langchain-anthropic 1.5.3: a cold call whose
        raw Anthropic usage said ``cache_creation_input_tokens: 4689`` surfaced
        as ``cache_creation: 0`` with ``ephemeral_1h_input_tokens: 4689``.
        Reading ``cache_creation`` alone recorded the write as 0, so
        compute_cost billed a 2x write as 1x uncached input.
        """
        out = self._run({
            "input_tokens": 5141,
            "output_tokens": 316,
            "input_token_details": {
                "cache_read": 0, "cache_creation": 0,
                "ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 4689,
            },
        })
        assert out["llm_cache_write_tokens"] == 4689

    def test_cache_write_five_minute_tier(self):
        """Same fallback must work if the TTL is ever switched back to 5m."""
        out = self._run({
            "input_tokens": 5141,
            "output_tokens": 316,
            "input_token_details": {
                "cache_read": 0, "cache_creation": 0,
                "ephemeral_5m_input_tokens": 4689, "ephemeral_1h_input_tokens": 0,
            },
        })
        assert out["llm_cache_write_tokens"] == 4689

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


class TestDigestModelLabelling:
    """``llm_model`` must name the model that actually ran.

    Beyond the ECMWF GRIB horizon run_digest() switches to ``config.longrange``
    (Haiku 4.5), but the recorded label was built unconditionally from
    ``config.llm``, so every long-range digest was logged as Sonnet. Observed on
    prod row 5562 — a D+7 flight recorded as claude-sonnet-4-6.
    """

    def _label(self, longrange: bool) -> str:
        from unittest.mock import MagicMock, patch
        from weatherbrief.tasks import outputs

        fake = {
            "digest": MagicMock(), "digest_text": "text", "longrange": longrange,
            "llm_input_tokens": 100, "llm_output_tokens": 10,
            "llm_cache_read_tokens": 0, "llm_cache_write_tokens": 0,
            "diagnostic": None, "digest_trace_id": None,
        }
        with patch("weatherbrief.digest.llm_digest.run_digest", return_value=fake):
            # No pack_dir/data_dir → returns before any file write.
            return outputs.run_llm_digest(MagicMock(), MagicMock()).llm_model

    def test_short_range_labels_the_main_model(self):
        assert self._label(longrange=False) == "anthropic:claude-sonnet-4-6"

    def test_long_range_labels_the_longrange_model(self):
        label = self._label(longrange=True)
        assert "haiku" in label, f"long-range digest mislabelled as {label!r}"
