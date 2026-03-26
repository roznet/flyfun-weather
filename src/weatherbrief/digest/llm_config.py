"""LLM digest configuration schema, loading, and factory."""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

_CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "weather_digest"
_GUIDANCE_DIR_DEFAULT = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "digest_guidance"

DEFAULT_GUIDANCE = "balanced"
VALID_GUIDANCE_KEYS = ("conservative", "balanced", "tolerant")


class LLMConfig(BaseModel):
    """LLM provider and model configuration."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0


class PromptsConfig(BaseModel):
    """Paths to prompt templates (relative to configs/weather_digest/)."""

    briefer: str = "prompts/briefer_v1.md"


class DigestConfig(BaseModel):
    """Top-level digest configuration."""

    version: str = "1.0"
    name: str = "default"
    llm: LLMConfig = LLMConfig()
    translator: LLMConfig = LLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        temperature=0.0,
    )
    prompts: PromptsConfig = PromptsConfig()

    def load_prompt(
        self,
        key: str,
        locale: str | None = None,
        guidance_key: str | None = None,
    ) -> str:
        """Load prompt markdown from configs/weather_digest/{path}.

        If a locale is provided (e.g. 'fr'), looks for a locale-specific
        variant first (e.g. prompts/briefer_v1.fr.md). Falls back to the
        default prompt if no locale variant exists.

        If the prompt contains a ``{guidance}`` placeholder and *guidance_key*
        is given (or defaults to ``DEFAULT_GUIDANCE``), the placeholder is
        replaced with the guidance text loaded from the digest_guidance
        config directory.
        """
        rel_path = getattr(self.prompts, key)
        if locale and locale != "en":
            base, ext = rel_path.rsplit(".", 1)
            locale_path = _CONFIGS_DIR / f"{base}.{locale}.{ext}"
            if locale_path.exists():
                return self._inject_guidance(locale_path.read_text(), guidance_key)
        prompt_path = _CONFIGS_DIR / rel_path
        return self._inject_guidance(prompt_path.read_text(), guidance_key)

    @staticmethod
    def _inject_guidance(prompt: str, guidance_key: str | None) -> str:
        """Replace ``{guidance}`` placeholder with guidance text."""
        if "{guidance}" not in prompt:
            return prompt
        key = guidance_key or DEFAULT_GUIDANCE
        guidance_text = load_guidance_text(key)
        # Indent guidance to match the prompt context (3 spaces for markdown list)
        indented = "\n".join(
            f"   {line}" if line.strip() else "" for line in guidance_text.splitlines()
        )
        return prompt.replace("{guidance}", indented)


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


def _get_guidance_dir() -> Path:
    """Return the guidance directory, respecting env var override."""
    override = os.environ.get("WEATHERBRIEF_GUIDANCE_DIR")
    if override:
        return Path(override)
    return _GUIDANCE_DIR_DEFAULT


def load_guidance_text(key: str) -> str:
    """Load guidance markdown for a given preset key.

    Raises FileNotFoundError if the key doesn't map to a file.
    """
    if key not in VALID_GUIDANCE_KEYS:
        raise ValueError(
            f"Unknown guidance key {key!r}; valid: {VALID_GUIDANCE_KEYS}"
        )
    guidance_dir = _get_guidance_dir()
    path = guidance_dir / f"{key}.md"
    if not path.exists():
        raise FileNotFoundError(f"Guidance file not found: {path}")
    return path.read_text()


def load_guidance_index(locale: str | None = None) -> list[dict]:
    """Load the guidance index with localised names/descriptions.

    Returns a list of dicts with keys: key, name, description.
    """
    guidance_dir = _get_guidance_dir()
    index_path = guidance_dir / "index.json"
    if not index_path.exists():
        raise FileNotFoundError(f"Guidance index not found: {index_path}")

    raw = json.loads(index_path.read_text())
    lang = locale if locale and locale != "en" else "en"
    result = []
    for preset in raw["presets"]:
        result.append({
            "key": preset["key"],
            "name": preset["name"].get(lang, preset["name"]["en"]),
            "description": preset["description"].get(lang, preset["description"]["en"]),
        })
    return result


def create_llm(config: DigestConfig) -> BaseChatModel:
    """Create a LangChain chat model from digest config."""
    return init_chat_model(
        model=config.llm.model,
        model_provider=config.llm.provider,
        temperature=config.llm.temperature,
    )
