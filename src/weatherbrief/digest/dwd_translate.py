"""Translate DWD synoptic text from German to English.

Uses a cheap/fast LLM for faithful translation with content-hash caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from weatherbrief.digest.llm_config import DigestConfig, create_chat_model
from weatherbrief.fetch.dwd_text import DWDDayBlock

logger = logging.getLogger(__name__)

# DWD vocabulary the translator got wrong in production (2026-10-14 review):
# Hochnebel read as "high fog", Nordmeer as "North Sea", the rain probability
# easing "in der Nacht zum Montag" turned into a wind decrease.
_GLOSSARY = """\
DWD terms (use these renderings):
- Hochnebel = low stratus (NOT "high fog"); Nebel = fog
- Nordmeer / Europäisches Nordmeer = Norwegian Sea (NOT the North Sea)
- "in der Nacht zum Samstag" = write "in the night into Saturday", exactly \
that, and never convert it to another day name; "ausgangs der Nacht" = \
towards the end of the night; teils = partly / in places
- Höhentief = upper low; Höhentrog = upper trough; Randtrog = peripheral \
trough; Kaltlufttropfen / abtropfen = cut-off low / to cut off; Keil = ridge; \
Hochdruckbrücke = high-pressure bridge; Wellentief = frontal-wave low
- schleifende Front = quasi-stationary front; rückläufig (of a front) = \
retreating
- gering bewölkt = little cloud (NOT low cloud); lockere Wolken / locker \
bewölkt = loose, scattered cloud (never "broken", which reads as BKN); \
heiter bis wolkig = fair to cloudy; Aufzug (von Wolken) = cloud moving in, \
not forming
- gebietsweise = in places (NOT area-wide); in der Fläche = widely, \
across the area
- Gusts are gusts, never sustained wind, and keep DWD's categories apart: \
Windböen / steife Böen = near-gale gusts (Bft 7); stürmische Böen = gale \
gusts (Bft 8); Sturmböen = strong-gale gusts (Bft 9); schwere Sturmböen = \
storm gusts (Bft 10); orkanartige Böen = violent-storm gusts (Bft 11). Never \
"moderate", "fresh" or "strong" for these. "stark bis stürmisch" = \
strong to gale (Bft 6-8); "Wind- und Sturmböen" = gusts and gale \
gusts (both kept)
- "dreht auf Nordwest" (from SW) = veers to northwest; "Geschichte sein" = \
be over / have ended
- "mit der Progression (der Front) ist es vorbei" = the front has stopped \
advancing; "Außer in X ... überwiegt Y" = Y everywhere except X (X is the \
exception, keep it that way round)
- "uns" / "unsere Richtung" / "bei uns" = Germany ("towards Germany", "over Germany"), never "us"
- Niederschlagswahrscheinlichkeit = probability of precipitation (it is \
about rain, never about wind)\
"""

# One fixed position per DWD place phrase, so the extract never guesses one:
# guessed coordinates put "Süddeutschland" in Austria and the north-east in
# Poland (2026-10-14 review).
_GAZETTEER = """\
Place phrases (DWD writes from Germany's point of view; "im Süden" means \
southern Germany when it locates weather in Germany. When a direction word \
places a large-scale system that lies outside Germany — "schwenkt der Trog \
im Nordwesten ostwärts" for an Atlantic trough — it means northwest OF \
Germany: say so, without a German region's coordinate). Write the English name followed by the coordinate given \
here, e.g. "over southern Germany (~48.5°N 10.5°E)":
- Norden, norddeutsch, nördliche Landesteile = northern Germany (~53.5°N 10°E)
- Nordwesten = northwestern Germany (~53°N 8°E)
- Nordosten = northeastern Germany (~53.5°N 13°E)
- Mitte, mittlere Landesteile, Mitteldeutschland = central Germany (~51°N 10°E)
- Westen = western Germany (~51°N 7°E)
- Osten = eastern Germany (~51.5°N 13°E)
- Süden, Süddeutschland, südliche Landesteile = southern Germany (~48.5°N 10.5°E)
- Südwesten = southwestern Germany (~48.3°N 8.5°E)
- Südosten = southeastern Germany (~48.3°N 12.5°E)
- Mittelgebirge = the German central uplands (~50.5°N 9.5°E)
- Alpen, Alpenraum = the Alps (~47.2°N 11°E); Alpenvorland = the northern \
Alpine foreland (~47.9°N 11°E); alpensüdseitig = the south side of the Alps \
(~46.2°N 10°E)
- Nordseeküste, Nordseeumfeld, Deutsche Bucht, "an der Nordsee" = the German \
North Sea coast (~54°N 8°E); only "über der (offenen) Nordsee" = the North \
Sea (~56°N 3°E)
- Ostseeküste, Ostseeumfeld, "an der Ostsee" = the German Baltic coast \
(~54.3°N 12°E); westliche Ostsee = the western Baltic (~54.8°N 11°E); only \
"über der Ostsee" = the Baltic Sea (~56°N 17°E)
- Nordmeer = the Norwegian Sea (~68°N 2°E)
- Schleswig-Holstein (~54.2°N 9.8°E); Niedersachsen = Lower Saxony \
(~52.8°N 9.5°E); Nordrhein-Westfalen (~51.5°N 7.5°E); Hessen (~50.6°N 9°E); \
Rheinland-Pfalz (~49.9°N 7.4°E); Saarland (~49.4°N 6.9°E); \
Baden-Württemberg (~48.5°N 9°E); Bayern = Bavaria (~49°N 11.5°E); \
Thüringen (~50.9°N 11°E); Sachsen = Saxony (~51°N 13.3°E); Sachsen-Anhalt \
(~51.9°N 11.7°E); Brandenburg (~52.4°N 13.3°E); Mecklenburg-Vorpommern \
(~53.8°N 12.5°E)
- Parts of states: östliches Sachsen = eastern Saxony (~51.1°N 14.5°E); \
südöstliches Bayern / Südosten Bayerns = southeastern Bavaria \
(~48.3°N 12.7°E)
- Emsland (~52.7°N 7.3°E); Schwarzwald = Black Forest (~48.2°N 8.2°E); \
Harz (~51.75°N 10.6°E); Erzgebirge (~50.6°N 13°E); Bodensee = Lake \
Constance (~47.6°N 9.4°E); Donau (the Bavarian stretch, Ulm to Passau) = the Danube in Bavaria (~48.7°N 11.5°E); \
Oberrhein = the Upper Rhine valley (~48.5°N 7.8°E)
- Countries and seas keep their English name: Frankreich = France \
(~46.5°N 2.5°E), Benelux (~51°N 5°E), Britische Inseln = the British Isles \
(~54°N 3°W), Skandinavien = Scandinavia (~62°N 14°E), Südskandinavien = southern \
Scandinavia (~58.5°N 12°E), Nordskandinavien = northern Scandinavia \
(~68°N 20°E), Tschechien = Czechia \
(~49.8°N 15.5°E), Polen = Poland (~52°N 19°E), Biskaya = Bay of Biscay \
(~45°N 5°W)
- A compound ("im östlichen Sachsen und südöstlichen Bayern") narrows the \
place: write "eastern Saxony and southeastern Bavaria" with the nearest \
entries' coordinates. A place not listed: translate the name and give your \
best approximate coordinate for that place itself (its centre), never a \
wider region's.\
"""

_TRANSLATE_SYSTEM_PROMPT = f"""\
You are a professional German-to-English translator specializing in \
meteorological text from the DWD (Deutscher Wetterdienst).

Translate the following German weather forecast faithfully into English.
- Preserve all meteorological terms, pressure values, temperatures, altitudes, \
and wind speeds exactly as given.
- Keep the day-by-day structure: copy each === day header === line exactly, one per input day; never add a header for a night or a later day mentioned inside a section.
- Do not interpret, add, or remove information.
- Do not add day-of-week to date references or vice versa — translate as-is.
- Use standard aviation/meteorology English terminology.

{_GLOSSARY}\
"""

_EXTRACT_SYNOPTIC_PROMPT = f"""\
You are an aviation meteorologist condensing a DWD (Deutscher Wetterdienst) \
forecast that was written for Germany, for a pilot whose route does NOT \
cross Germany.

For each day section, write English prose with the large-scale content: \
pressure systems (names, central pressure, where they are and where DWD says \
they move), fronts and their positions, rain, fog / low stratus, gust and \
thunder areas, air mass and flow. Take as many sentences as that needs. \
Always keep: each sub-period DWD describes (e.g. the night into the next \
day), every statement of model disagreement or uncertainty, every gust area \
with its Bft, any thunder, and fog / low stratus areas. Leave out only \
purely local detail (town temperatures, rainfall amounts at named places).

Rules:
- Faithful, not interpretive: every statement must come from the German \
text. Do not add implications, advice to pilots, or statements about what \
the text does not mention.
- Timing and movement exactly as DWD states them ("in the night into \
Saturday", "by evening", "slowly towards the Alps"). If DWD gives no timing \
or direction for a feature, give none — never infer that a front clears, \
weakens or moves somewhere.
- Places: use the place phrases below — English name plus coordinate. Never \
widen a place ("southern Germany" is not "southern Europe").
- Copy the input's === day header === lines exactly, one per input day and in order. Never add a header for a night or later day mentioned inside a section ("in the night into Saturday" stays under its own day); no other headings.
- Preserve all numerical values (pressure in hPa, temperatures, wind speeds) \
exactly as stated.

{_GAZETTEER}

{_GLOSSARY}\
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
    # Mode, prompt and model are in the file name: a prompt or model change
    # must not keep serving translations the old one wrote.
    mode_tag = "extract" if synoptic_extract else "translate"
    prompt = _EXTRACT_SYNOPTIC_PROMPT if synoptic_extract else _TRANSLATE_SYSTEM_PROMPT
    content_hash = _cache_key(combined_de)
    version = _cache_key(f"{config.translator.model}\n{prompt}")[:8]
    cache_file = cache_path / f"{content_hash}_{mode_tag}_{version}.json"

    # Check cache
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("german_hash") == content_hash:
                logger.info("DWD %s cache hit: %s", mode_tag, cache_file.name)
                return _split_translation(blocks, cached["english"])
        except (json.JSONDecodeError, KeyError):
            pass

    logger.info(
        "DWD %s (%d blocks, %d chars)",
        mode_tag, len(blocks), len(combined_de),
    )
    llm = create_chat_model(config.translator)

    result = llm.invoke([
        {"role": "system", "content": prompt},
        {"role": "user", "content": combined_de},
    ])
    _record_translation_cost(
        config.translator.model, getattr(result, "usage_metadata", None), mode_tag,
    )
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
        _prune_stale_versions(cache_path, content_hash, mode_tag, keep=cache_file)
    except OSError:
        logger.warning("Failed to write translation cache", exc_info=True)

    return _split_translation(blocks, english)


def _record_translation_cost(model: str, usage: dict | None, mode_tag: str) -> None:
    """One ``dwd_translate`` ledger row per translator call (#922). Never raises.

    Priced by ``compute_call_cost`` (tokens only, the model's own rate), like
    the other side calls. A system cost, not a pilot's: the translation is
    cached by content and shared by every briefing that sees the same text,
    so cache hits cost nothing and write nothing. Written on its own session
    because the digest holds none.
    """
    if not isinstance(usage, dict) or not usage:
        return
    try:
        from flyfun_common.costs import record_cost

        from weatherbrief.api.credits import SERVICE
        from weatherbrief.api.usage import detached_session
        from weatherbrief.costs import compute_call_cost

        details = usage.get("input_token_details") or {}
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        cost = compute_call_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=details.get("cache_read") or 0,
            cache_write_tokens=details.get("cache_creation") or 0,
        )
        db = detached_session()
        try:
            record_cost(
                db,
                "system",
                service=SERVICE,
                action="dwd_translate",
                cost=cost,
                category="dwd_translate",
                description=f"DWD {mode_tag} (${cost:.4f})",
                metadata={
                    "model": model,
                    "mode": mode_tag,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("DWD translation cost not recorded", exc_info=True)


def _prune_stale_versions(
    cache_path: Path, content_hash: str, mode_tag: str, *, keep: Path,
) -> None:
    """Drop this text's translations written by another prompt or model.

    The file name carries a model+prompt version, so every prompt change would
    otherwise leave the old file behind for good — including the pre-version
    ``{hash}_{mode}.json`` files. Only the same German text and mode are
    touched; other texts age out with their own next write.
    """
    for old in cache_path.glob(f"{content_hash}_{mode_tag}*.json"):
        if old != keep:
            old.unlink(missing_ok=True)


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
    headers = re.findall(r"===\s*(.+?)\s*===", english)
    parts = re.split(r"===\s*.+?\s*===\n?", english)
    # parts[0] is whatever preceded the first header; the rest map to blocks
    pairs = [(h, _strip_trailing_decoration(s)) for h, s in zip(headers, parts[1:])]
    pairs = [(h, s) for h, s in pairs if s]
    sections = [s for _, s in pairs]
    if len(sections) != len(blocks):
        sections = _fold_extra_days(blocks, pairs) or sections

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


def _fold_extra_days(
    blocks: list[DWDDayBlock], pairs: list[tuple[str, str]],
) -> list[str] | None:
    """Fold sections for days the input did not have into the day before.

    The translator sometimes gives "the night into Saturday" inside a Friday
    block its own ``=== Samstag ===`` header (2026-10-15, even when told not
    to). A header naming the next input day — by German day name or ISO date —
    starts that day; any other header and its text stay with the current day,
    the header kept as a plain line so the timing is not lost. ``None`` when
    the input days still don't line up one-to-one.
    """
    out: list[str] = []
    for header, body in pairs:
        nxt = blocks[len(out)] if len(out) < len(blocks) else None
        if nxt is not None and (
            nxt.day_name_de.lower() in header.lower()
            or (nxt.date_iso is not None and nxt.date_iso.isoformat() in header)
        ):
            out.append(body)
        elif out:
            out[-1] = f"{out[-1]}\n\n{header}:\n{body}"
        else:
            return None
    if len(out) != len(blocks):
        return None
    logger.info("Translation split: folded %d extra day header(s)", len(pairs) - len(out))
    return out

