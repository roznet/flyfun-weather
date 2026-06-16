"""DWD Open Data text forecast client.

Fetches synoptic overviews from https://opendata.dwd.de/weather/text_forecasts/txt/:
- SXDL31 (Synoptische Übersicht Kurzfrist) — short-range (2-3 day), updated 2x daily
- SXDL33 (Synoptische Übersicht Mittelfrist) — medium-range (7-day), updated daily ~10:30 UTC

Also provides day-level extraction: given a flight date, extract only the
relevant paragraph(s) from the multi-day narrative.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

import requests
from pydantic import BaseModel

logger = logging.getLogger(__name__)

DWD_BASE_URL = "https://opendata.dwd.de/weather/text_forecasts/txt"

# DWD filenames for latest synoptic overviews
_SXDL31_PATH = "SXDL31_DWAV_LATEST"  # Kurzfrist (short-range)
_SXDL33_PATH = "SXDL33_DWAV_LATEST"  # Mittelfrist (medium-range)

_TIMEOUT_SECONDS = 15

# German day names → weekday number (Monday=0)
_DE_DAYS = {
    "Montag": 0,
    "Dienstag": 1,
    "Mittwoch": 2,
    "Donnerstag": 3,
    "Freitag": 4,
    "Samstag": 5,
    "Sonntag": 6,
}
_DE_DAY_PATTERN = "|".join(_DE_DAYS.keys())


class DWDDayBlock(BaseModel):
    """A single day's narrative from a DWD synoptic overview."""

    day_name_de: str  # e.g. "Donnerstag", "Aktuell"
    date_iso: date | None = None  # computed from issue date + day name
    text: str  # raw German paragraph(s) for this day
    source: str  # "kurzfrist" or "mittelfrist"


class DWDTextForecasts(BaseModel):
    """Container for fetched DWD text forecasts."""

    short_range: str | None = None  # SXDL31 - Kurzfrist
    medium_range: str | None = None  # SXDL33 - Mittelfrist
    fetched_at: datetime


def _fetch_text(url: str) -> str | None:
    """Fetch a single text forecast, returning None on failure."""
    try:
        resp = requests.get(url, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        # DWD files are latin-1 encoded
        resp.encoding = "latin-1"
        return resp.text
    except (requests.RequestException, ConnectionError):
        logger.warning("Failed to fetch DWD forecast from %s", url, exc_info=True)
        return None


def fetch_dwd_text_forecasts() -> DWDTextForecasts:
    """Fetch latest DWD synoptic overviews.

    Gracefully handles failures — if DWD is unreachable, text fields are None.
    """
    short_range = _fetch_text(f"{DWD_BASE_URL}/{_SXDL31_PATH}")
    medium_range = _fetch_text(f"{DWD_BASE_URL}/{_SXDL33_PATH}")

    return DWDTextForecasts(
        short_range=short_range,
        medium_range=medium_range,
        fetched_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Day-level extraction
# ---------------------------------------------------------------------------

def parse_issue_date(text: str) -> date | None:
    """Extract issue date from DWD header line.

    Matches patterns like:
      'ausgegeben am Mittwoch, den 11.03.2026 um 18 UTC'
      'ausgegeben am Dienstag, den 10.03.2026 um 10.30 UTC'
    """
    m = re.search(r"ausgegeben am .+?,\s*den\s+(\d{2})\.(\d{2})\.(\d{4})", text)
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(year, month, day)


def _next_weekday(reference: date, weekday: int) -> date:
    """Find the next occurrence of `weekday` strictly after `reference`.

    DWD forecasts refer to upcoming days, so even if the issue date is a
    Wednesday and the text says "Mittwoch", it means next Wednesday.
    Exception: Kurzfrist "Aktuell" block uses the issue date itself.
    """
    days_ahead = (weekday - reference.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7  # always the *next* occurrence, not today
    return reference + timedelta(days=days_ahead)


def _resolve_day_date(day_name_de: str, issue_date: date) -> date | None:
    """Map a German day name to a concrete date relative to the issue date.

    DWD forecasts always refer to upcoming days, so we find the next
    occurrence of that weekday on or after the issue date.
    """
    weekday = _DE_DAYS.get(day_name_de)
    if weekday is None:
        return None
    return _next_weekday(issue_date, weekday)


def _extract_synoptic_body(text: str) -> str:
    """Extract only the synoptic development section from DWD text.

    Strips headers, model comparison, consistency assessment, etc.
    """
    # Find start of synoptic content
    # Kurzfrist: after "Synoptische Entwicklung bis ..."
    # Mittelfrist: after "Synoptische Entwicklung bis zum ..."
    start_match = re.search(
        r"Synoptische Entwicklung bis[^\n]*\n[-]*\n?",
        text,
    )
    if start_match:
        body = text[start_match.end():]
    else:
        body = text

    # Find end — stop at section terminators
    terminators = [
        r"\n_{5,}",  # __________ separator lines
        r"\nModellvergleich",
        r"\nBewertung der Konsistenz",
        r"\nVorhersage- und Beratungszentrale",
    ]
    for term in terminators:
        m = re.search(term, body)
        if m:
            body = body[:m.start()]

    return body.strip()


def extract_day_blocks(text: str, source: str) -> list[DWDDayBlock]:
    """Split a DWD synoptic text into per-day blocks.

    Args:
        text: Raw DWD text (Kurzfrist or Mittelfrist).
        source: "kurzfrist" or "mittelfrist".

    Returns:
        List of DWDDayBlock with day_name_de, date_iso (if resolvable), and text.
    """
    issue_date = parse_issue_date(text)
    body = _extract_synoptic_body(text)
    if not body:
        return []

    # Split on day markers. Patterns seen:
    #   "Aktuell ..." (Kurzfrist today)
    #   "Donnerstag ..." (day name at start of paragraph)
    #   "Am Sonntag ..." (Mittelfrist style)
    #   "In der Nacht zum Freitag ..." (night transition — belongs to previous day)
    #   Second "Synoptische Entwicklung bis ..." section in Kurzfrist
    #
    # Strategy: split on day-name markers, attach night paragraphs to previous day.

    # Also handle second synoptic section in Kurzfrist
    body = re.sub(
        r"\n-+\n\nSynoptische Entwicklung bis[^\n]*\n",
        "\n",
        body,
    )

    # Split into paragraphs (separated by blank lines), then identify
    # which paragraph starts a new day. This avoids false matches on
    # day names that appear mid-sentence on wrapped lines.
    day_start_pattern = re.compile(
        rf"^(?:Zu Beginn der Mittelfrist am\s+)?"
        rf"(Aktuell|(?:Am\s+)?(?:{_DE_DAY_PATTERN}))\b"
    )
    # Skip the closing "In der erweiterten Mittelfrist" summary paragraph
    skip_pattern = re.compile(r"^In der erweiterten Mittelfrist\b")

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]

    # Group paragraphs by day: each day-marker paragraph starts a new group,
    # subsequent non-marker paragraphs belong to the same day.
    groups: list[tuple[str, list[str]]] = []  # (day_name, [paragraphs])
    for para in paragraphs:
        if skip_pattern.match(para):
            continue
        m = day_start_pattern.match(para)
        if m:
            raw_name = m.group(1)
            day_name = re.sub(r"^Am\s+", "", raw_name)
            groups.append((day_name, [para]))
        elif groups:
            # Continuation paragraph — append to current day
            groups[-1][1].append(para)
        # else: orphan paragraph before first day marker — skip

    if not groups:
        return [DWDDayBlock(
            day_name_de="Aktuell",
            date_iso=issue_date,
            text=body.strip(),
            source=source,
        )]

    blocks: list[DWDDayBlock] = []
    seen_dates: set[date] = set()
    for day_name, paras in groups:
        if day_name == "Aktuell":
            day_date = issue_date
        elif issue_date:
            day_date = _resolve_day_date(day_name, issue_date)
            # If we've already seen this date (e.g. duplicate day name after
            # _next_weekday wraps), bump forward a week
            if day_date and day_date in seen_dates:
                day_date = day_date + timedelta(days=7)
        else:
            day_date = None

        if day_date:
            seen_dates.add(day_date)

        blocks.append(DWDDayBlock(
            day_name_de=day_name,
            date_iso=day_date,
            text="\n\n".join(paras),
            source=source,
        ))

    return blocks


def filter_blocks_for_flight(
    blocks: list[DWDDayBlock],
    target_date: date,
    strict: bool = False,
) -> list[DWDDayBlock]:
    """Filter DWD day blocks to those relevant for a flight date.

    Returns blocks for the flight day and the day before (for synoptic setup).
    Falls back to the nearest block if no exact match — UNLESS ``strict`` is set,
    in which case an empty list is returned when no block actually covers the
    flight. The long-range digest uses ``strict=True`` so a flight beyond the
    Mittelfrist window (D+7) is not fed a stale, non-covering block that would
    mislead the LLM into thinking it has synoptic guidance for the flight day.
    """
    day_before = target_date - timedelta(days=1)
    relevant_dates = {target_date, day_before}

    matched = [b for b in blocks if b.date_iso in relevant_dates]
    if matched:
        return sorted(matched, key=lambda b: b.date_iso or date.min)

    if strict:
        return []

    # Fallback: if no exact date match (e.g. target_date is beyond the
    # forecast range), return the last available block
    dated = [b for b in blocks if b.date_iso is not None]
    if dated:
        dated.sort(key=lambda b: b.date_iso)
        return [dated[-1]]

    return blocks[:1] if blocks else []


def get_dwd_day_blocks(
    dwd_text: DWDTextForecasts,
    target_date: date,
    strict: bool = False,
) -> list[DWDDayBlock]:
    """Extract and filter day blocks from both Kurzfrist and Mittelfrist.

    Selects blocks relevant to the target flight date. With ``strict=True``,
    returns nothing unless a block actually covers the flight (no nearest-block
    fallback) — used by the long-range digest to avoid feeding the LLM a stale
    block for a flight beyond DWD's ~7-day horizon.
    """
    all_blocks: list[DWDDayBlock] = []

    if dwd_text.short_range:
        all_blocks.extend(extract_day_blocks(dwd_text.short_range, "kurzfrist"))
    if dwd_text.medium_range:
        all_blocks.extend(extract_day_blocks(dwd_text.medium_range, "mittelfrist"))

    return filter_blocks_for_flight(all_blocks, target_date, strict=strict)
