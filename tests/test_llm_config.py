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
    assert config.llm.model == "claude-sonnet-4-6"
    assert config.llm.temperature == 0.0


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
