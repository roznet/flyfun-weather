"""Splitting the combined DWD translation back into per-day blocks."""

from __future__ import annotations

from datetime import date

from weatherbrief.digest.dwd_translate import _split_translation
from weatherbrief.fetch.dwd_text import DWDDayBlock


def _blocks(*days: tuple[str, date]) -> list[DWDDayBlock]:
    return [
        DWDDayBlock(day_name_de=name, date_iso=d, text="...", source="kurzfrist")
        for name, d in days
    ]


BLOCKS = _blocks(
    ("Freitag", date(2026, 10, 9)),
    ("Samstag", date(2026, 10, 10)),
    ("Sonntag", date(2026, 10, 11)),
)


def test_plain_headers_split_one_section_per_day():
    english = (
        "=== Freitag (2026-10-09) ===\nLow over Iceland.\n\n"
        "=== Samstag (2026-10-10) ===\nCold front at 5E.\n\n"
        "=== Sonntag (2026-10-11) ===\nRidge builds."
    )
    out = _split_translation(BLOCKS, english)
    assert [text for _, text in out] == [
        "Low over Iceland.", "Cold front at 5E.", "Ridge builds.",
    ]


def test_markdown_title_and_decoration_do_not_break_the_split(caplog):
    # Shape seen from the extract prompt in prod: a title before the first
    # header, "## " before each header and "---" rules between days. The title
    # used to count as a section, so every day fell back to the whole text.
    english = (
        "# SYNOPTIC EXTRACTION – FRIDAY 2026-10-09 TO SUNDAY 2026-10-11\n\n---\n\n"
        "## === Freitag (2026-10-09) ===\n\nLow GABRIELA over Iceland.\n\n---\n\n"
        "## === Samstag (2026-10-10) ===\n\nCold front at 5E.\n\n---\n\n"
        "## === Sonntag (2026-10-11) ===\n\nRidge builds.\n"
    )
    out = _split_translation(BLOCKS, english)
    assert "split mismatch" not in caplog.text
    assert [text for _, text in out] == [
        "Low GABRIELA over Iceland.", "Cold front at 5E.", "Ridge builds.",
    ]


def test_missing_day_still_falls_back_to_full_text():
    english = (
        "=== Freitag (2026-10-09) ===\nLow over Iceland.\n\n"
        "=== Samstag (2026-10-10) ===\nCold front at 5E."
    )
    out = _split_translation(BLOCKS, english)
    assert [text for _, text in out] == [english] * 3


def test_long_markdown_rule_inside_a_day_is_kept_and_fast():
    # A table rule mid-section must neither be stripped nor make the trim
    # backtrack (a nested-quantifier regex here hung on real cache files).
    rule = "|" + "-" * 400 + "|"
    english = (
        f"=== Freitag (2026-10-09) ===\n| a |\n{rule}\n| b |\n\n"
        "=== Samstag (2026-10-10) ===\nCold front at 5E.\n---\n\n"
        "=== Sonntag (2026-10-11) ===\nRidge builds.\n##"
    )
    out = _split_translation(BLOCKS, english)
    assert out[0][1] == f"| a |\n{rule}\n| b |"
    assert [text for _, text in out[1:]] == ["Cold front at 5E.", "Ridge builds."]
