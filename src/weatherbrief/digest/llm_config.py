"""LLM digest configuration schema, loading, and factory."""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

_CONFIGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "weather_digest"
_LOCALES_DIR = _CONFIGS_DIR / "prompts" / "locales"
_GUIDANCE_DIR_DEFAULT = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "digest_guidance"

DEFAULT_GUIDANCE = "balanced"

# Locales whose rendered prompt head is worth a prompt-cache breakpoint.  See
# ``DigestConfig.cache_locales`` for why this is an allowlist rather than "all".
DEFAULT_CACHE_LOCALES = ["en"]


def resolve_locale_key(locale: str | None) -> str:
    """Resolve a requested locale to the locale file that will render.

    ``None`` and ``"en"`` both resolve to ``en``, and so does any locale with no
    file on disk — the three produce a **byte-identical** prompt head, which is
    what makes them share one prompt-cache entry.  Callers deciding whether to
    cache must key off this, not the raw request value: ``locale="zz"`` renders
    English and should cache with everyone else.

    Single source of truth for the rule — ``_inject_locale`` resolves through
    here too, so the fallback cannot drift away from the caching decision.
    """
    lang = locale if locale and locale != "en" else "en"
    if not (_LOCALES_DIR / f"{lang}.md").exists():
        lang = "en"
    return lang


class LLMConfig(BaseModel):
    """LLM provider and model configuration."""

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0


class PromptsConfig(BaseModel):
    """Paths to prompt templates (relative to configs/weather_digest/)."""

    briefer: str = "prompts/briefer_v1.md"
    # Long-range (>7 day) outlook prompt — softer, model-agreement driven.
    briefer_longrange: str = "prompts/briefer_longrange_v1.md"


class DigestConfig(BaseModel):
    """Top-level digest configuration."""

    version: str = "1.0"
    name: str = "default"
    llm: LLMConfig = LLMConfig()
    # Beyond the ECMWF GRIB horizon (~7 days) only two global models remain and
    # confidence is low, so the long-range outlook runs a cheaper model on a
    # trimmed prompt.  Haiku by default; overridable per config like ``llm``.
    longrange: LLMConfig = LLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        temperature=0.0,
    )
    translator: LLMConfig = LLMConfig(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        temperature=0.0,
    )
    prompts: PromptsConfig = PromptsConfig()
    # Locales that get a prompt-cache breakpoint.  Locale is injected *before*
    # the head/tail split (vocabulary tokens land throughout the body), so
    # unlike guidance it genuinely forks the cached head — one cache entry per
    # locale.  A write costs 2x and only pays back above a ~53% hit rate, so a
    # locale with traffic too sparse to re-read an entry inside the 1h TTL
    # writes an entry nobody reads and bills 2x for the privilege.  Measured on
    # production 2026-08-11: `de` ran 6 digests / 11 days across 2 users, gaps
    # of 9h-8d, 0 hits and 2 wasted writes.  So: allowlist the locales dense
    # enough to amortize, let the rest bill a plain 1x.
    #
    # This is deliberately *not* self-tuning — flipping it on is a decision
    # made from the ledger, not from a request-time guess.  Re-run the locale
    # cohort query in `designs/digest.md` and add a locale once it sustains
    # more than roughly one write per day.
    cache_locales: list[str] = Field(default_factory=lambda: list(DEFAULT_CACHE_LOCALES))

    def should_cache_locale(self, locale: str | None) -> bool:
        """Whether ``locale``'s rendered prompt head is worth caching.

        Resolves through :func:`resolve_locale_key` first, so ``None``, ``"en"``
        and any unknown locale — all three of which render the *same* English
        head — share one entry rather than being split by the raw request value.
        """
        return resolve_locale_key(locale) in self.cache_locales

    def load_prompt(
        self,
        key: str,
        locale: str | None = None,
        guidance_key: str | None = None,
    ) -> str:
        """Load prompt markdown with locale and guidance injection.

        Loads the base prompt template, then injects:
        1. Locale-specific content (language instruction + vocabulary tokens)
           from configs/weather_digest/prompts/locales/{locale}.md
        2. Guidance text from digest_guidance/{key}.md into {guidance} placeholder
        """
        rel_path = getattr(self.prompts, key)
        prompt_path = _CONFIGS_DIR / rel_path
        return self.render_prompt(prompt_path.read_text(), locale, guidance_key)

    def load_prompt_parts(
        self,
        key: str,
        locale: str | None = None,
        guidance_key: str | None = None,
    ) -> tuple[str, str]:
        """Split the rendered prompt at ``{guidance}`` into (head, tail).

        ``head + tail`` is byte-identical to ``load_prompt()`` — the split is
        purely about where a prompt-cache breakpoint can go. In ``briefer_v3``
        the guidance sits at the end, so ``head`` is identical across all three
        guidance presets and can be cached once for every pilot, while the tail
        that actually varies stays uncached.

        Splitting *before* guidance injection is what guarantees the byte
        equality: ``_inject_guidance`` is a plain replace of the placeholder, so
        injecting into the tail alone produces the same bytes as injecting into
        the whole. Locale is injected first because it also substitutes
        vocabulary tokens throughout the body.

        A prompt with the placeholder still near the top (``briefer_v2``) splits
        fine but leaves a head too short to reach the model's minimum cacheable
        prefix — correct, just not worth much.
        """
        rel_path = getattr(self.prompts, key)
        prompt = (_CONFIGS_DIR / rel_path).read_text()
        prompt = self._inject_locale(prompt, locale)
        if "{guidance}" not in prompt:
            return prompt, ""
        head, rest = prompt.split("{guidance}", 1)
        tail = self._inject_guidance("{guidance}" + rest, guidance_key)
        return head, tail

    def render_prompt(
        self,
        prompt: str,
        locale: str | None = None,
        guidance_key: str | None = None,
    ) -> str:
        """Inject locale + guidance into already-loaded prompt text.

        Split out of ``load_prompt`` so callers holding a prompt from somewhere
        other than ``self.prompts`` — the eval runner's ``--prompt`` override,
        for one — render it the same way rather than shipping raw ``{guidance}``
        and ``{locale}`` placeholders to the model.
        """
        prompt = self._inject_locale(prompt, locale)
        prompt = self._inject_guidance(prompt, guidance_key)
        return prompt

    @staticmethod
    def _parse_locale_file(text: str) -> tuple[dict[str, str], str]:
        """Parse a locale file with YAML-like frontmatter and body.

        Returns (metadata_dict, body_text).
        """
        if text.startswith("---"):
            _, frontmatter, body = text.split("---", 2)
            metadata: dict[str, str] = {}
            for line in frontmatter.strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    v = v.strip().strip('"')
                    metadata[k.strip()] = v
            return metadata, body.strip()
        return {}, text.strip()

    @staticmethod
    def _inject_locale(prompt: str, locale: str | None) -> str:
        """Replace {locale} and locale vocabulary tokens."""
        locale_path = _LOCALES_DIR / f"{resolve_locale_key(locale)}.md"

        metadata, body = DigestConfig._parse_locale_file(locale_path.read_text())

        # Replace {locale} — collapse blank lines when body is empty
        if body:
            prompt = prompt.replace("{locale}", body)
        else:
            prompt = prompt.replace("\n\n{locale}\n", "\n")

        # Replace vocabulary tokens from frontmatter
        for key, value in metadata.items():
            prompt = prompt.replace(f"{{{key}}}", value)

        return prompt

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


def get_valid_guidance_keys() -> tuple[str, ...]:
    """Return valid guidance keys derived from index.json."""
    guidance_dir = _get_guidance_dir()
    index_path = guidance_dir / "index.json"
    if not index_path.exists():
        return (DEFAULT_GUIDANCE,)
    raw = json.loads(index_path.read_text())
    return tuple(p["key"] for p in raw["presets"])


def load_guidance_text(key: str) -> str:
    """Load guidance markdown for a given preset key.

    Raises ValueError if the key is not in the guidance index.
    Raises FileNotFoundError if the file doesn't exist.
    """
    valid = get_valid_guidance_keys()
    if key not in valid:
        raise ValueError(
            f"Unknown guidance key {key!r}; valid: {valid}"
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


def create_chat_model(llm_config: LLMConfig) -> BaseChatModel:
    """Create a LangChain chat model from a single LLMConfig block."""
    return init_chat_model(
        model=llm_config.model,
        model_provider=llm_config.provider,
        temperature=llm_config.temperature,
    )


def create_llm(config: DigestConfig, *, longrange: bool = False) -> BaseChatModel:
    """Create the briefer chat model from digest config.

    ``longrange=True`` selects the cheaper long-range model (``config.longrange``)
    used beyond the ECMWF GRIB horizon; otherwise the default ``config.llm``.
    """
    return create_chat_model(config.longrange if longrange else config.llm)
