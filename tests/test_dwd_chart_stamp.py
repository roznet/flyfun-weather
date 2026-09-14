"""Tests for reading a DWD ICON forecast chart's run, lead and valid time."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbrief.fetch import dwd_chart_stamp
from weatherbrief.fetch.dwd_chart_stamp import (
    TIME_SOURCE_LAST_MODIFIED,
    TIME_SOURCE_OCR,
    ForecastStamp,
    parse_stamp_text,
    read_forecast_stamp,
    stamp_from_last_modified,
)

FIXTURES = Path(__file__).parent / "fixtures"
UTC = timezone.utc

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None, reason="tesseract binary not installed",
)


# ---------------------------------------------------------------------------
# parse_stamp_text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, init, lead",
    [
        # Verbatim Tesseract output on real charts, spacing quirks included.
        ("VT: 12 UTCFr. 18 Sept. [ICON 2026-09-14 00 UTC + 108 h]", datetime(2026, 9, 14, tzinfo=UTC), 108),
        ("VT: 12 UTC Di. 15 Sept. [ICON 2026-09-14 00 UTC + 36h] ©", datetime(2026, 9, 14, tzinfo=UTC), 36),
        ("\\VT: OO UTCFr. 11 Sept. [ICON 2026-09-09 00 UTC + 48h] ©", datetime(2026, 9, 9, tzinfo=UTC), 48),
    ],
)
def test_parse_stamp_text_reads_the_bracket(text, init, lead):
    assert parse_stamp_text(text) == (init, lead)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "VT: 12 UTC Fr. 18 Sept.",
        "[ICON 2026-13-40 00 UTC + 36h]",
        "[ICON 2026-09-14 OO UTC + 36h]",
    ],
)
def test_parse_stamp_text_rejects_unreadable(text):
    assert parse_stamp_text(text) is None


# ---------------------------------------------------------------------------
# ForecastStamp
# ---------------------------------------------------------------------------


def test_stamp_from_last_modified_takes_that_days_00z_run():
    stamp = stamp_from_last_modified(datetime(2026, 9, 14, 5, 18, 13, tzinfo=UTC), 108)
    assert stamp.init_time == datetime(2026, 9, 14, tzinfo=UTC)
    assert stamp.valid_time == datetime(2026, 9, 18, 12, tzinfo=UTC)
    assert stamp.source == TIME_SOURCE_LAST_MODIFIED


def test_meta_round_trip():
    stamp = ForecastStamp(datetime(2026, 9, 14, tzinfo=UTC), 48, TIME_SOURCE_OCR)
    meta = stamp.to_meta()
    assert meta == {
        "init_time": "2026-09-14T00:00:00Z",
        "lead_h": 48,
        "valid_time": "2026-09-16T00:00:00Z",
        "time_source": "ocr",
    }
    assert ForecastStamp.from_meta(meta) == stamp


def test_from_meta_without_a_stamp_is_none():
    assert ForecastStamp.from_meta(None) is None
    assert ForecastStamp.from_meta({"last_modified": "2026-09-14T05:18:13+00:00"}) is None


# ---------------------------------------------------------------------------
# read_forecast_stamp
# ---------------------------------------------------------------------------


@needs_tesseract
@pytest.mark.parametrize("cid, lead", [("036", 36), ("108", 108)])
def test_read_forecast_stamp_ocrs_a_real_chart_band(cid, lead):
    # Fixtures are blank 800x653 canvases carrying the real bottom band of the
    # 2026-09-14 00Z charts.
    stamp = read_forecast_stamp(
        FIXTURES / f"dwd_icon_stamp_{cid}.png",
        lead_h=lead,
        last_modified=datetime(2026, 9, 14, 5, 18, tzinfo=UTC),
    )
    assert stamp == ForecastStamp(datetime(2026, 9, 14, tzinfo=UTC), lead, TIME_SOURCE_OCR)


def test_read_forecast_stamp_falls_back_when_image_unreadable(tmp_path: Path):
    path = tmp_path / "036.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    stamp = read_forecast_stamp(path, lead_h=36, last_modified=datetime(2026, 9, 14, 3, 36, tzinfo=UTC))
    assert stamp == ForecastStamp(datetime(2026, 9, 14, tzinfo=UTC), 36, TIME_SOURCE_LAST_MODIFIED)


def test_read_forecast_stamp_without_ocr_or_last_modified_is_none(tmp_path: Path):
    path = tmp_path / "036.png"
    path.write_bytes(b"not a png")
    assert read_forecast_stamp(path, lead_h=36, last_modified=None) is None


@pytest.mark.parametrize(
    "ocr_text",
    [
        "[ICON 2026-09-09 00 UTC + 36h]",  # a misread day: run 5 days before publish
        "[ICON 2026-09-15 00 UTC + 36h]",  # run after the chart was published
        "[ICON 2026-09-14 03 UTC + 36h]",  # not a synoptic hour
    ],
)
def test_read_forecast_stamp_rejects_implausible_ocr(monkeypatch, tmp_path: Path, ocr_text):
    monkeypatch.setattr(dwd_chart_stamp, "ocr_stamp_text", lambda _path: ocr_text)
    stamp = read_forecast_stamp(
        tmp_path / "036.png", lead_h=36, last_modified=datetime(2026, 9, 14, 3, 36, tzinfo=UTC),
    )
    assert stamp == ForecastStamp(datetime(2026, 9, 14, tzinfo=UTC), 36, TIME_SOURCE_LAST_MODIFIED)


def test_read_forecast_stamp_trusts_the_image_lead_over_the_file_name(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(dwd_chart_stamp, "ocr_stamp_text", lambda _path: "[ICON 2026-09-14 00 UTC + 48h]")
    stamp = read_forecast_stamp(
        tmp_path / "036.png", lead_h=36, last_modified=datetime(2026, 9, 14, 4, 43, tzinfo=UTC),
    )
    assert stamp == ForecastStamp(datetime(2026, 9, 14, tzinfo=UTC), 48, TIME_SOURCE_OCR)
