"""LLM digest configuration schema, loading, and factory."""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

_CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "weather_digest"


class LLMConfig(BaseModel):
    """LLM provider and model configuration."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5-20250929"
    temperature: float = 0.0


class PromptsConfig(BaseModel):
    """Paths to prompt templates (relative to configs/weather_digest/)."""

    briefer: str = "prompts/briefer_v1.md"


class DigestConfig(BaseModel):
    """Top-level digest configuration."""

    version: str = "1.0"
    name: str = "default"
    llm: LLMConfig = LLMConfig()
    prompts: PromptsConfig = PromptsConfig()

    def load_prompt(self, key: str) -> str:
        """Load prompt markdown from configs/weather_digest/{path}."""
        rel_path = getattr(self.prompts, key)
        prompt_path = _CONFIGS_DIR / rel_path
        return prompt_path.read_text()


def load_digest_config(name: str | None = None) -> DigestConfig:
    """Load a digest config by name.

    Resolution order:
    1. Explicit name parameter
    2. WEATHERBRIEF_DIGEST_CONFIG environment variable
    3. "default"
    """
    config_name = name or os.environ.get("WEATHERBRIEF_DIGEST_CONFIG", "default")
    config_path = _CONFIGS_DIR / f"{config_name}.json"

    if not config_path.exists():
        raise FileNotFoundError(f"Digest config not found: {config_path}")

    raw = json.loads(config_path.read_text())
    return DigestConfig.model_validate(raw)


def create_llm(config: DigestConfig) -> BaseChatModel:
    """Create a LangChain chat model from digest config."""
    return init_chat_model(
        model=config.llm.model,
        model_provider=config.llm.provider,
        temperature=config.llm.temperature,
    )
