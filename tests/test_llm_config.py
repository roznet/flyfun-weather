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
    assert config.llm.model == "claude-sonnet-4-5-20250929"
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
    """load_prompt reads the briefer prompt file."""
    config = load_digest_config("default")
    prompt = config.load_prompt("briefer")

    assert "aviation weather briefer" in prompt
    assert "assessment" in prompt


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
