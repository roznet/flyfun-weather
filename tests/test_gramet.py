"""Tests for Autorouter GRAMET client (euro_aip) with mocked HTTP."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import responses

from euro_aip.briefing.sources.autorouter_gramet import (
    GRAMET_URL,
    AutorouterGrametSource,
)


@responses.activate
def test_fetch_gramet():
    """GRAMET client calls API with correct params and returns content."""
    mock_cred = MagicMock()
    mock_cred.get_token.return_value = "test-token-123"

    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    responses.add(
        responses.GET,
        GRAMET_URL,
        body=fake_png,
        status=200,
        content_type="image/png",
    )

    client = AutorouterGrametSource(mock_cred)
    departure = datetime(2026, 2, 14, 9, 0, 0)
    result = client.fetch_gramet(
        waypoints=["EGTK", "LFPB", "LSGS"],
        altitude_ft=8000,
        departure_time=departure,
        duration_hours=4.5,
    )

    assert result == fake_png

    # Verify request params
    req = responses.calls[0].request
    assert "EGTK+LFPB+LSGS" in req.url or "EGTK%20LFPB%20LSGS" in req.url
    assert "altitude=8000" in req.url
    assert "format=png" in req.url
    assert req.headers["Authorization"] == "Bearer test-token-123"


@responses.activate
def test_fetch_gramet_pdf():
    """GRAMET client supports PDF format."""
    mock_cred = MagicMock()
    mock_cred.get_token.return_value = "test-token"

    fake_pdf = b"%PDF-1.4" + b"\x00" * 100
    responses.add(
        responses.GET,
        GRAMET_URL,
        body=fake_pdf,
        status=200,
        content_type="application/pdf",
    )

    client = AutorouterGrametSource(mock_cred)
    result = client.fetch_gramet(
        waypoints=["EGTK", "LSGS"],
        altitude_ft=6000,
        departure_time=datetime(2026, 2, 14, 9, 0),
        duration_hours=3.0,
        fmt="pdf",
    )

    assert result == fake_pdf
    assert "format=pdf" in responses.calls[0].request.url
