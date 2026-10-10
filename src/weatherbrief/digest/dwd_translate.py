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

_EXTRACT_SYNOPTIC_PROMPT = """\
You are an aviation meteorologist extracting large-scale synoptic information \
from a DWD (Deutscher Wetterdienst) forecast that was written for Germany.

The pilot's route does NOT cross Germany, so German regional details are \
irrelevant. Extract ONLY the large-scale synoptic features that could affect \
weather across a wider European area.

For each day section, output a concise English summary (3-5 sentences) covering:

1. **Named pressure systems**: name, approximate position (use lat/lon or \
compass bearings relative to landmarks like "north of Scotland", \
"Bay of Biscay"), central pressure if given, track and speed.

2. **Fronts**: type (warm/cold/occluded), current position in geographic \
terms (NOT German Bundesländer or cities — use lat/lon, compass bearings, \
or large geographic features like "central Mediterranean", "English Channel"). \
Include temporal evolution: where the front is NOW and where it will be at \
key times (e.g. "cold front along 5°E at 00Z, clearing east of 15°E by 18Z").

3. **Air mass classification**: type (mP, mPS, cT, etc.) and advection \
direction behind/ahead of fronts.

4. **Large-scale flow**: general pattern (zonal, meridional, blocking ridge, \
trough axis position).

Rules:
- Keep the === day header === structure from the input.
- Preserve all numerical values (pressure in hPa, temperatures, wind speeds) \
exactly as stated.
- Do NOT include German regional details: no Bundesländer, German cities, \
rivers, or mountain ranges. Replace with geographic coordinates or compass \
references (e.g. "~50°N, 8°E" instead of "Hesse", "~48°N, 8°E" instead \
of "southwestern Germany"). The pilot does not know German geography.
- Do NOT invent positions — if the text only says "over southwestern Germany" \
and gives no coordinates, use approximate coordinates like "~48°N, 8°E".
- If temporal progression is described, always include it — this is critical \
for pilots to judge whether features have passed their route by flight time.\
"""

_DEFAULT_CACHE_DIR = Path("data/.cache/dwd_translations")


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def translate_dwd_blocks(
    blocks: list[DWDDayBlock],
    config: DigestConfig,
    cache_dir: Path | None = None,
    *,
    synoptic_extract: bool = False,
) -> list[tuple[DWDDayBlock, str]]:
    """Translate DWD day blocks to English.

    Args:
        synoptic_extract: When True, extract only large-scale synoptic
            features (named systems, fronts with positions/timing, air masses)
            instead of a faithful full translation.  Used for routes that
            do not cross Germany so the briefer LLM receives geographically
            honest context without German regional details to misapply.

    Returns list of (block, english_text) tuples.
    Uses content-hash caching to avoid re-translating identical text.
    """
    if not blocks:
        return []

    cache_path = cache_dir or _DEFAULT_CACHE_DIR
    cache_path.mkdir(parents=True, exist_ok=True)

    # Combine all blocks into a single translation request (cheaper than N calls)
    combined_de = _format_blocks_for_translation(blocks)
    # Include mode in cache key so full vs extracted are cached separately
    mode_tag = "extract" if synoptic_extract else "translate"
    content_hash = _cache_key(combined_de)
    cache_file = cache_path / f"{content_hash}_{mode_tag}.json"

    # Check cache
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("german_hash") == content_hash:
                logger.info("DWD %s cache hit: %s", mode_tag, cache_file.name)
                return _split_translation(blocks, cached["english"])
        except (json.JSONDecodeError, KeyError):
            pass

    # Translate or extract
    prompt = _EXTRACT_SYNOPTIC_PROMPT if synoptic_extract else _TRANSLATE_SYSTEM_PROMPT
    logger.info(
        "DWD %s (%d blocks, %d chars)",
        mode_tag, len(blocks), len(combined_de),
    )
    llm = init_chat_model(
        model=config.translator.model,
        model_provider=config.translator.provider,
        temperature=config.translator.temperature,
    )

    result = llm.invoke([
        {"role": "system", "content": prompt},
        {"role": "user", "content": combined_de},
    ])
    english = result.content

    # Cache
    cache_data = {
        "german_hash": content_hash,
        "english": english,
        "translated_at": datetime.now(timezone.utc).isoformat(),
        "model": config.translator.model,
        "mode": mode_tag,
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


def _strip_trailing_decoration(section: str) -> str:
    """Drop trailing lines that are only markdown decoration (``---``, ``##``).

    Line-based rather than one regex: a nested-quantifier pattern over runs of
    ``-``/``#`` backtracks exponentially on a long rule that is not at the end.
    """
    lines = section.strip().splitlines()
    while lines and not lines[-1].strip(" \t#-"):
        lines.pop()
    return "\n".join(lines).strip()


def _split_translation(
    blocks: list[DWDDayBlock],
    english: str,
) -> list[tuple[DWDDayBlock, str]]:
    """Split a combined English translation back into per-block texts.

    Looks for the same === day headers used in the input.  When the headers
    do not line up one-to-one (the model may split one German day in two, or
    merge two), returns a single entry spanning all the days with the whole
    text: it keeps its own day headers, and repeating it under every block
    put the full forecast in the briefer's context N times.

    The model sometimes dresses the reply up as markdown: a title before the
    first header (``# SYNOPTIC EXTRACTION ...``), ``## `` in front of each
    header, ``---`` rules between days.  Text before the first header is not a
    day and is dropped; the decoration around each header is trimmed so it does
    not bleed into the neighbouring day.
    """
    result: list[tuple[DWDDayBlock, str]] = []

    # Try to split on the header markers
    import re
    parts = re.split(r"===\s*.+?\s*===\n?", english)
    # parts[0] is whatever preceded the first header; the rest map to blocks
    sections = [_strip_trailing_decoration(s) for s in parts[1:]]
    sections = [s for s in sections if s]

    if len(sections) == len(blocks):
        for block, section in zip(blocks, sections):
            result.append((block, section))
    else:
        logger.warning(
            "Translation split mismatch: %d sections vs %d blocks; kept as one entry",
            len(sections), len(blocks),
        )
        first, last = blocks[0], blocks[-1]
        span = first if len(blocks) == 1 else DWDDayBlock(
            day_name_de=f"{first.day_name_de} – {last.day_name_de}",
            date_iso=first.date_iso,
            text="\n\n".join(b.text for b in blocks),
            source=first.source if all(b.source == first.source for b in blocks) else last.source,
        )
        result.append((span, english.strip()))

    return result
