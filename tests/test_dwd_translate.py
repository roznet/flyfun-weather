"""DWD translation: per-day splitting, cache keying, prompt content."""

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


def test_missing_day_keeps_the_text_once():
    english = (
        "=== Freitag (2026-10-09) ===\nLow over Iceland.\n\n"
        "=== Samstag (2026-10-10) ===\nCold front at 5E."
    )
    out = _split_translation(BLOCKS, english)
    assert [text for _, text in out] == [english]
    assert out[0][0].day_name_de == "Freitag – Sonntag"
    assert out[0][0].date_iso == date(2026, 10, 9)


def test_day_split_in_two_keeps_the_text_once():
    # Prod 2026-10-10 (pack 9542): two German blocks came back as three days,
    # and the whole translation was pasted under both blocks.
    blocks = BLOCKS[:2]
    english = (
        "## === Current (2026-10-09) ===\nLow over Iceland.\n\n"
        "## === Saturday (2026-10-10) ===\nCold front at 5E.\n\n"
        "## === Saturday Night into Sunday (2026-10-10/11) ===\nRidge builds.\n"
    )
    out = _split_translation(blocks, english)
    # The dated headers name both input days; the extra night section stays
    # with Saturday, once.
    assert [b.day_name_de for b, _ in out] == ["Freitag", "Samstag"]
    assert out[0][1] == "Low over Iceland."
    assert "Cold front at 5E." in out[1][1]
    assert sum(text.count("Ridge builds.") for _, text in out) == 1


def test_extra_night_header_folds_into_its_day(caplog):
    # Prod 2026-10-15: one Donnerstag block came back with a Freitag header
    # for its "night into Friday" paragraph.
    blocks = BLOCKS[:1]
    english = (
        "=== Freitag (2026-10-09) ===\nHigh over central Europe.\n\n"
        "=== Samstag (2026-10-10) ===\nFog in places overnight.\n"
    )
    out = _split_translation(blocks, english)
    assert "split mismatch" not in caplog.text
    assert len(out) == 1 and out[0][0] is blocks[0]
    assert out[0][1] == (
        "High over central Europe.\n\nSamstag (2026-10-10):\nFog in places overnight."
    )


def test_undated_unmatched_headers_still_fall_back():
    blocks = BLOCKS[:2]
    english = "=== Today ===\nA.\n\n=== Tonight ===\nB.\n\n=== Tomorrow ===\nC."
    out = _split_translation(blocks, english)
    assert len(out) == 1 and out[0][1] == english


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


class _FakeLLM:
    def __init__(self, calls: list):
        self.calls = calls

    def invoke(self, messages):
        self.calls.append(messages)

        class _R:
            content = "=== Freitag (2026-10-09) ===\nok"

        return _R()


def test_cache_is_keyed_on_prompt_and_model(tmp_path, monkeypatch):
    """A prompt or model change must not keep serving the old translation."""
    from weatherbrief.digest import dwd_translate
    from weatherbrief.digest.llm_config import DigestConfig, LLMConfig

    calls: list = []
    monkeypatch.setattr(dwd_translate, "create_chat_model", lambda cfg: _FakeLLM(calls))
    blocks = _blocks(("Freitag", date(2026, 10, 9)))
    config = DigestConfig()

    dwd_translate.translate_dwd_blocks(blocks, config, tmp_path, synoptic_extract=True)
    dwd_translate.translate_dwd_blocks(blocks, config, tmp_path, synoptic_extract=True)
    assert len(calls) == 1  # second call is a cache hit

    monkeypatch.setattr(dwd_translate, "_EXTRACT_SYNOPTIC_PROMPT", "changed prompt")
    dwd_translate.translate_dwd_blocks(blocks, config, tmp_path, synoptic_extract=True)
    assert len(calls) == 2

    config.translator = LLMConfig(model="another-model")
    dwd_translate.translate_dwd_blocks(blocks, config, tmp_path, synoptic_extract=True)
    assert len(calls) == 3


def test_prompts_carry_the_place_table_and_glossary():
    """Places the 2026-10-14 review saw misplaced have one fixed position."""
    from weatherbrief.digest.dwd_translate import (
        _EXTRACT_SYNOPTIC_PROMPT,
        _TRANSLATE_SYSTEM_PROMPT,
    )

    assert "southern Germany (~48.5°N 10.5°E)" in _EXTRACT_SYNOPTIC_PROMPT
    assert "Norwegian Sea (~68°N 2°E)" in _EXTRACT_SYNOPTIC_PROMPT
    for prompt in (_EXTRACT_SYNOPTIC_PROMPT, _TRANSLATE_SYSTEM_PROMPT):
        assert "Hochnebel = low stratus" in prompt
        assert "gebietsweise = in places" in prompt
