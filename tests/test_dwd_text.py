"""Tests for DWD text forecast client."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import responses

from weatherbrief.fetch.dwd_text import (
    DWD_BASE_URL,
    DWDTextForecasts,
    _SXDL31_PATH,
    _SXDL33_PATH,
    fetch_dwd_text_forecasts,
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
