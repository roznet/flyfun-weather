"""Translate DWD synoptic text from German to English.

Uses a cheap/fast LLM for faithful translation with content-hash caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from langchain.chat_models import init_chat_model

from weatherbrief.digest.llm_config import DigestConfig, LLMConfig
from weatherbrief.fetch.dwd_text import DWDDayBlock

logger = logging.getLogger(__name__)

_TRANSLATE_SYSTEM_PROMPT = """\
You are a professional German-to-English translator specializing in \
meteorological text from the DWD (Deutscher Wetterdienst).

Translate the following German weather forecast faithfully into English.
- Preserve all meteorological terms, pressure values, temperatures, altitudes, \
and wind speeds exactly as given.
- Keep the day-by-day structure. Each section starts with a day header line.
- Do not interpret, add, or remove information.
- Do not add day-of-week to date references or vice versa — translate as-is.
- Use standard aviation/meteorology English terminology.\
"""

_DEFAULT_CACHE_DIR = Path("data/.cache/dwd_translations")


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def translate_dwd_blocks(
    blocks: list[DWDDayBlock],
    config: DigestConfig,
    cache_dir: Path | None = None,
) -> list[tuple[DWDDayBlock, str]]:
    """Translate DWD day blocks to English.

    Returns list of (block, english_text) tuples.
    Uses content-hash caching to avoid re-translating identical text.
    """
    if not blocks:
        return []

    cache_path = cache_dir or _DEFAULT_CACHE_DIR
    cache_path.mkdir(parents=True, exist_ok=True)

    # Combine all blocks into a single translation request (cheaper than N calls)
    combined_de = _format_blocks_for_translation(blocks)
    cache_file = cache_path / f"{_cache_key(combined_de)}.json"

    # Check cache
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("german_hash") == _cache_key(combined_de):
                logger.info("DWD translation cache hit: %s", cache_file.name)
                return _split_translation(blocks, cached["english"])
        except (json.JSONDecodeError, KeyError):
            pass

    # Translate
    logger.info("Translating DWD text (%d blocks, %d chars)", len(blocks), len(combined_de))
    llm = init_chat_model(
        model=config.translator.model,
        model_provider=config.translator.provider,
        temperature=config.translator.temperature,
    )

    result = llm.invoke([
        {"role": "system", "content": _TRANSLATE_SYSTEM_PROMPT},
        {"role": "user", "content": combined_de},
    ])
    english = result.content

    # Cache
    cache_data = {
        "german_hash": _cache_key(combined_de),
        "english": english,
        "translated_at": datetime.now(timezone.utc).isoformat(),
        "model": config.translator.model,
    }
    try:
        cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2))
    except OSError:
        logger.warning("Failed to write translation cache", exc_info=True)

    return _split_translation(blocks, english)


def _format_blocks_for_translation(blocks: list[DWDDayBlock]) -> str:
    """Format blocks with clear day headers for translation."""
    parts = []
    for b in blocks:
        date_str = b.date_iso.isoformat() if b.date_iso else "?"
        header = f"=== {b.day_name_de} ({date_str}) ==="
        parts.append(f"{header}\n{b.text}")
    return "\n\n".join(parts)


def _split_translation(
    blocks: list[DWDDayBlock],
    english: str,
) -> list[tuple[DWDDayBlock, str]]:
    """Split a combined English translation back into per-block texts.

    Looks for the same === day headers used in the input.
    Falls back to returning the full text for each block if splitting fails.
    """
    result: list[tuple[DWDDayBlock, str]] = []

    # Try to split on the header markers
    import re
    sections = re.split(r"===\s*.+?\s*===\n?", english)
    # First element is empty (before first header), rest map to blocks
    sections = [s.strip() for s in sections if s.strip()]

    if len(sections) == len(blocks):
        for block, section in zip(blocks, sections):
            result.append((block, section))
    else:
        # Splitting didn't work cleanly — return full text for all blocks
        logger.warning(
            "Translation split mismatch: %d sections vs %d blocks",
            len(sections), len(blocks),
        )
        for block in blocks:
            result.append((block, english))

    return result
