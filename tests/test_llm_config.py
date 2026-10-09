"""Tests for LLM digest configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from weatherbrief.digest.llm_config import (
    DigestConfig,
    LLMConfig,
    PromptsConfig,
    load_digest_config,
)


def test_default_config_loads():
    """Default config file exists and loads correctly."""
    config = load_digest_config("default")

    assert config.name == "default"
    assert config.version == "1.0"
    assert config.llm.provider == "anthropic"
    # Sonnet 5.5 at effort low since #717: no sampling params (it 400s on any
    # non-default temperature) and native structured output (it 400s on the
    # forced tool call LangChain's default method sends).
    assert config.llm.model == "claude-sonnet-5-5"
    assert config.llm.temperature is None
    assert config.llm.thinking == {"type": "adaptive"}
    assert config.llm.effort == "low"
    assert config.llm.structured_output == "json_schema"
    assert config.llm.max_tokens and config.llm.max_tokens >= 8000
    assert config.prompts.briefer == "prompts/briefer_v5.md"


def test_sonnet46_rollback_config_is_the_pre_717_default():
    """``sonnet46`` keeps the previous production briefer for rollback / A-B."""
    config = load_digest_config("sonnet46")

    assert config.llm.model == "claude-sonnet-4-6"
    assert config.llm.temperature == 0.0
    assert config.llm.thinking is None and config.llm.effort is None
    assert config.llm.structured_output == "function_calling"
    assert config.prompts.briefer == "prompts/briefer_v3.md"


def test_openai_config_loads():
    """OpenAI config file exists and loads correctly."""
    config = load_digest_config("openai")

    assert config.name == "openai"
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o"


def test_config_defaults():
    """DigestConfig has sensible defaults without loading a file."""
    config = DigestConfig()

    assert config.name == "default"
    assert config.llm.provider == "anthropic"
    assert config.prompts.briefer == "prompts/briefer_v1.md"


def test_load_prompt():
    """load_prompt reads the briefer prompt file with EN defaults."""
    config = load_digest_config("default")
    prompt = config.load_prompt("briefer")

    assert "aviation weather briefer" in prompt
    assert "assessment" in prompt
    # EN locale tokens should be resolved
    assert 'Say "None"' in prompt
    assert '"I don\'t know"' in prompt
    assert '"DWD synoptic overview"' in prompt
    # No unresolved placeholders (except {guidance} which needs guidance_key)
    assert "{locale}" not in prompt
    assert "{none_word}" not in prompt
    assert "{uncertainty_phrase}" not in prompt
    assert "{dwd_label}" not in prompt
    assert "{aviation_terms_note}" not in prompt


def test_load_prompt_french():
    """French locale injects language instruction and vocabulary."""
    config = load_digest_config("default")
    prompt = config.load_prompt("briefer", locale="fr")

    assert "Write ALL text fields in French" in prompt
    assert 'Say "Aucun"' in prompt
    assert '"Données incertaines"' in prompt
    assert "aperçu synoptique DWD" in prompt
    assert "{locale}" not in prompt
    assert "{none_word}" not in prompt


def test_load_prompt_german():
    """German locale injects language instruction and vocabulary."""
    config = load_digest_config("default")
    prompt = config.load_prompt("briefer", locale="de")

    assert "Write ALL text fields in German" in prompt
    assert 'Say "Keine"' in prompt
    assert "DWD-Synoptikübersicht" in prompt


def test_load_prompt_locale_gets_guidance():
    """Non-EN locales correctly get guidance injection."""
    config = load_digest_config("default")
    prompt = config.load_prompt("briefer", locale="fr", guidance_key="balanced")

    assert "ROUTE ADVISORIES" in prompt
    assert "VFR only" in prompt
    assert "{guidance}" not in prompt


def test_load_prompt_unknown_locale_falls_back():
    """Unknown locale falls back to EN defaults."""
    config = load_digest_config("default")
    prompt = config.load_prompt("briefer", locale="ja")

    assert 'Say "None"' in prompt
    assert "Write ALL text fields" not in prompt


def test_load_missing_config():
    """Loading a non-existent config raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_digest_config("nonexistent_config_xyz")


def test_env_var_override(tmp_path):
    """WEATHERBRIEF_DIGEST_CONFIG env var overrides default name."""
    # Create a custom config
    custom = {
        "version": "1.0",
        "name": "custom",
        "llm": {"provider": "openai", "model": "gpt-4o-mini", "temperature": 0.5},
        "prompts": {"briefer": "prompts/briefer_v1.md"},
    }
    # We test the env var resolution path (without writing an actual file)
    with patch.dict(os.environ, {"WEATHERBRIEF_DIGEST_CONFIG": "openai"}):
        config = load_digest_config()
        assert config.name == "openai"
        assert config.llm.provider == "openai"


def test_explicit_name_overrides_env():
    """Explicit name parameter takes precedence over env var."""
    with patch.dict(os.environ, {"WEATHERBRIEF_DIGEST_CONFIG": "openai"}):
        config = load_digest_config("default")
        assert config.name == "default"
        assert config.llm.provider == "anthropic"


class TestLoadPromptParts:
    """The head/tail split is what lets all guidance presets share one cache entry."""

    def test_split_is_byte_identical_to_load_prompt(self):
        """head + tail must equal load_prompt() exactly, or production drifts."""
        from weatherbrief.digest.llm_config import load_digest_config
        config = load_digest_config("default")
        for guidance in ("balanced", "conservative", "tolerant"):
            head, tail = config.load_prompt_parts("briefer", guidance_key=guidance)
            assert head + tail == config.load_prompt("briefer", guidance_key=guidance)

    def test_head_is_identical_across_guidance_presets(self):
        """The whole point: one cached prefix for every pilot.

        If a prompt puts {guidance} back near the top this fails, which is the
        signal that the cache breakpoint has stopped paying for itself.
        """
        from weatherbrief.digest.llm_config import load_digest_config
        config = load_digest_config("default")
        heads = {
            g: config.load_prompt_parts("briefer", guidance_key=g)[0]
            for g in ("balanced", "conservative", "tolerant")
        }
        assert len(set(heads.values())) == 1, "guidance leaked into the cached head"

    def test_guidance_lands_in_the_tail_not_the_head(self):
        from weatherbrief.digest.llm_config import load_digest_config, load_guidance_text
        config = load_digest_config("default")
        head, tail = config.load_prompt_parts("briefer", guidance_key="conservative")
        marker = load_guidance_text("conservative").strip().splitlines()[0].strip()
        assert marker in tail
        assert marker not in head


class TestCreateChatModel:
    """Optional knobs reach ``init_chat_model`` only when set."""

    def _kwargs(self, llm_config):
        from weatherbrief.digest.llm_config import create_chat_model
        with patch("weatherbrief.digest.llm_config.init_chat_model") as init:
            create_chat_model(llm_config)
        return init.call_args.kwargs

    def test_a_plain_config_sends_exactly_what_it_always_did(self):
        kwargs = self._kwargs(LLMConfig(model="claude-haiku-4-5-20251001"))
        assert kwargs == {
            "model": "claude-haiku-4-5-20251001",
            "model_provider": "anthropic",
            "temperature": 0.0,
        }

    def test_a_null_temperature_is_omitted_not_sent_as_none(self):
        kwargs = self._kwargs(LLMConfig(model="claude-sonnet-5-5", temperature=None))
        assert "temperature" not in kwargs

    def test_thinking_effort_and_max_tokens_pass_through(self):
        kwargs = self._kwargs(LLMConfig(
            model="claude-sonnet-5-5", temperature=None,
            thinking={"type": "adaptive"}, effort="low", max_tokens=16000,
        ))
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["effort"] == "low"
        assert kwargs["max_tokens"] == 16000


def test_with_structured_uses_the_configured_method():
    from unittest.mock import MagicMock
    from weatherbrief.digest.llm_config import with_structured

    llm = MagicMock()
    with_structured(llm, dict, LLMConfig(structured_output="json_schema"), include_raw=True)
    llm.with_structured_output.assert_called_once_with(
        dict, method="json_schema", include_raw=True,
    )
