"""Tests for DWD text forecast client and day extraction."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
import responses

from weatherbrief.fetch.dwd_text import (
    DWD_BASE_URL,
    DWDTextForecasts,
    _SXDL31_PATH,
    _SXDL33_PATH,
    extract_day_blocks,
    fetch_dwd_text_forecasts,
    filter_blocks_for_flight,
    parse_issue_date,
)

SAMPLE_KURZFRIST = """\
SXDL31 DWAV 100800
Synoptische Übersicht Kurzfrist
ausgegeben am Montag, den 10.02.2026 um 08 UTC

Kurzfrist: Ein Hoch über Mitteleuropa sorgt für ruhiges Wetter.
"""

SAMPLE_MITTELFRIST = """\
SXDL33 DWAV 101030
Synoptische Übersicht Mittelfrist
ausgegeben am Montag, den 10.02.2026 um 10:30 UTC

Mittelfrist: Graduelle Umstellung der Großwetterlage auf Westwetterlage.
"""


@responses.activate
def test_fetch_both_forecasts():
    """Both SXDL31 and SXDL33 fetched successfully."""
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL31_PATH}",
        body=SAMPLE_KURZFRIST,
        status=200,
    )
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL33_PATH}",
        body=SAMPLE_MITTELFRIST,
        status=200,
    )

    result = fetch_dwd_text_forecasts()

    assert isinstance(result, DWDTextForecasts)
    assert result.short_range is not None
    assert "Kurzfrist" in result.short_range
    assert result.medium_range is not None
    assert "Mittelfrist" in result.medium_range
    assert result.fetched_at is not None


@responses.activate
def test_fetch_graceful_failure_sxdl31():
    """SXDL31 fails, SXDL33 succeeds — short_range is None."""
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL31_PATH}",
        body="Server Error",
        status=500,
    )
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL33_PATH}",
        body=SAMPLE_MITTELFRIST,
        status=200,
    )

    result = fetch_dwd_text_forecasts()

    assert result.short_range is None
    assert result.medium_range is not None


@responses.activate
def test_fetch_graceful_failure_both():
    """Both endpoints fail — both fields None, no exception raised."""
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL31_PATH}",
        body="Server Error",
        status=500,
    )
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL33_PATH}",
        body="Server Error",
        status=503,
    )

    result = fetch_dwd_text_forecasts()

    assert result.short_range is None
    assert result.medium_range is None
    assert result.fetched_at is not None


@responses.activate
def test_fetch_connection_error():
    """Connection error is handled gracefully."""
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL31_PATH}",
        body=ConnectionError("Connection refused"),
    )
    responses.add(
        responses.GET,
        f"{DWD_BASE_URL}/{_SXDL33_PATH}",
        body=ConnectionError("Connection refused"),
    )

    result = fetch_dwd_text_forecasts()

    assert result.short_range is None
    assert result.medium_range is None


# ---------------------------------------------------------------------------
# Day extraction tests
# ---------------------------------------------------------------------------

MITTELFRIST_STRUCTURED = """\
S Y N O P T I S C H E   Ü B E R S I C H T   M I T T E L F R I S T
ausgegeben am Mittwoch, den 11.03.2026 um 10.30 UTC



Wechselhaft mit Regen.
__________________________________________________________

Synoptische Entwicklung bis zum Mittwoch, den 18.03.2026


Zu Beginn der Mittelfrist am Samstag greift von Westen ein Trog über.
Die Front bringt Regen. In der Nacht zum Sonntag lockert die Bewölkung
im Westen auf.

Am Sonntag wird Deutschland vom Trogresiduum überquert. Im Tagesverlauf
nähert sich der nächste Trog.

Am Montag liegt der Trog über Deutschland. Es regnet verbreitet.

Am Dienstag dehnt sich ein Höhenkeil aus. Wetterberuhigung setzt ein.

In der erweiterten Mittelfrist deutet sich Hochdruckeinfluss an.
__________________________________________________________

Bewertung der Konsistenz des operationellen Laufs
Gute Konsistenz.
"""

KURZFRIST_STRUCTURED = """\
SXEU31 DWAV 111800

S Y N O P T I S C H E   Ü B E R S I C H T   K U R Z F R I S T
ausgegeben am Mittwoch, den 11.03.2026 um 18 UTC


SCHLAGZEILE:
Umstellung der Wetterlage.

Synoptische Entwicklung bis Freitag 06 UTC
----------------------------------------------------------------
Aktuell ... geht die blockierende Lage zu Ende.

Donnerstag ... hängt die Front im Südosten zurück. Der Wind frischt auf.

----------------------------------------------------------------

Synoptische Entwicklung bis Samstag 06 UTC

Freitag ... der Langwellentrog amplifiziert sich.

Modellvergleich und -einschätzung
----------------------------------------------------------------
Die Modelle sind ähnlich.
"""


class TestParseIssueDate:
    def test_mittelfrist(self):
        assert parse_issue_date(MITTELFRIST_STRUCTURED) == date(2026, 3, 11)

    def test_kurzfrist(self):
        assert parse_issue_date(KURZFRIST_STRUCTURED) == date(2026, 3, 11)

    def test_no_match(self):
        assert parse_issue_date("no date here") is None


class TestExtractDayBlocksMittelfrist:
    def test_all_days_found(self):
        blocks = extract_day_blocks(MITTELFRIST_STRUCTURED, "mittelfrist")
        days = [(b.day_name_de, b.date_iso) for b in blocks]
        assert ("Samstag", date(2026, 3, 14)) in days
        assert ("Sonntag", date(2026, 3, 15)) in days
        assert ("Montag", date(2026, 3, 16)) in days
        assert ("Dienstag", date(2026, 3, 17)) in days

    def test_skips_extended_mittelfrist(self):
        blocks = extract_day_blocks(MITTELFRIST_STRUCTURED, "mittelfrist")
        texts = " ".join(b.text for b in blocks)
        assert "erweiterten Mittelfrist" not in texts

    def test_skips_consistency_section(self):
        blocks = extract_day_blocks(MITTELFRIST_STRUCTURED, "mittelfrist")
        texts = " ".join(b.text for b in blocks)
        assert "Konsistenz" not in texts

    def test_night_stays_with_preceding_day(self):
        blocks = extract_day_blocks(MITTELFRIST_STRUCTURED, "mittelfrist")
        samstag = next(b for b in blocks if b.day_name_de == "Samstag")
        assert "Nacht zum Sonntag" in samstag.text

    def test_source_tag(self):
        blocks = extract_day_blocks(MITTELFRIST_STRUCTURED, "mittelfrist")
        assert all(b.source == "mittelfrist" for b in blocks)


class TestExtractDayBlocksKurzfrist:
    def test_all_days_found(self):
        blocks = extract_day_blocks(KURZFRIST_STRUCTURED, "kurzfrist")
        days = [(b.day_name_de, b.date_iso) for b in blocks]
        assert ("Aktuell", date(2026, 3, 11)) in days
        assert ("Donnerstag", date(2026, 3, 12)) in days
        assert ("Freitag", date(2026, 3, 13)) in days

    def test_skips_model_comparison(self):
        blocks = extract_day_blocks(KURZFRIST_STRUCTURED, "kurzfrist")
        texts = " ".join(b.text for b in blocks)
        assert "Modellvergleich" not in texts

    def test_handles_second_synoptic_section(self):
        """Freitag from second 'Synoptische Entwicklung' section is captured."""
        blocks = extract_day_blocks(KURZFRIST_STRUCTURED, "kurzfrist")
        freitag = next(b for b in blocks if b.day_name_de == "Freitag")
        assert "Langwellentrog" in freitag.text


class TestFilterBlocksForFlight:
    def _all_blocks(self):
        return (
            extract_day_blocks(KURZFRIST_STRUCTURED, "kurzfrist")
            + extract_day_blocks(MITTELFRIST_STRUCTURED, "mittelfrist")
        )

    def test_thursday_flight(self):
        filtered = filter_blocks_for_flight(self._all_blocks(), date(2026, 3, 12))
        dates = {b.date_iso for b in filtered}
        assert date(2026, 3, 11) in dates  # day before (Aktuell)
        assert date(2026, 3, 12) in dates  # flight day (Donnerstag)

    def test_sunday_flight(self):
        filtered = filter_blocks_for_flight(self._all_blocks(), date(2026, 3, 15))
        dates = {b.date_iso for b in filtered}
        assert date(2026, 3, 14) in dates  # Samstag
        assert date(2026, 3, 15) in dates  # Sonntag

    def test_crosses_kurzfrist_mittelfrist_boundary(self):
        """Saturday flight: day before (Friday) from Kurzfrist, day from Mittelfrist."""
        filtered = filter_blocks_for_flight(self._all_blocks(), date(2026, 3, 14))
        sources = {b.source for b in filtered}
        assert "kurzfrist" in sources
        assert "mittelfrist" in sources

    def test_beyond_range_returns_last(self):
        filtered = filter_blocks_for_flight(self._all_blocks(), date(2026, 3, 25))
        assert len(filtered) >= 1

    def test_beyond_range_strict_returns_empty(self):
        """Strict mode (long-range digest) drops the stale fallback block so the
        LLM is not handed synoptic text that doesn't cover the flight day."""
        filtered = filter_blocks_for_flight(
            self._all_blocks(), date(2026, 3, 25), strict=True,
        )
        assert filtered == []

    def test_in_range_strict_still_matches(self):
        """Strict mode still returns blocks that genuinely cover the flight."""
        filtered = filter_blocks_for_flight(
            self._all_blocks(), date(2026, 3, 15), strict=True,
        )
        assert len(filtered) >= 1

    def test_sorted_by_date(self):
        filtered = filter_blocks_for_flight(self._all_blocks(), date(2026, 3, 15))
        dates = [b.date_iso for b in filtered]
        assert dates == sorted(dates)
