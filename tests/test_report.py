"""Tests for the report rendering module."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.report.render import render_html, render_pdf


@pytest.fixture
def sample_flight():
    return Flight(
        id="egtk_lsgs-2026-02-21",
        route_name="egtk_lsgs",
        waypoints=["EGTK", "LFPB", "LSGS"],
        target_date="2026-02-21",
        target_time_utc=9,
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
        created_at=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_pack():
    return BriefingPackMeta(
        flight_id="egtk_lsgs-2026-02-21",
        fetch_timestamp="2026-02-19T18:00:00+00:00",
        days_out=2,
        has_gramet=True,
        has_skewt=True,
        has_digest=True,
        assessment="GREEN",
        assessment_reason="Conditions favorable",
    )


@pytest.fixture
def pack_dir(tmp_path, sample_pack):
    """Create a realistic pack directory with artifacts."""
    pack = tmp_path / "pack"
    pack.mkdir()

    # Snapshot
    snapshot = {
        "route": {
            "name": "Oxford to Sion",
            "waypoints": [
                {"icao": "EGTK", "name": "Oxford Kidlington", "lat": 51.8, "lon": -1.3},
                {"icao": "LFPB", "name": "Paris Le Bourget", "lat": 48.9, "lon": 2.4},
                {"icao": "LSGS", "name": "Sion", "lat": 46.2, "lon": 7.3},
            ],
            "cruise_altitude_ft": 8000,
        },
        "target_date": "2026-02-21",
        "fetch_date": "2026-02-19",
        "days_out": 2,
        "analyses": [
            {
                "waypoint": {"icao": "EGTK", "name": "Oxford Kidlington"},
                "model_divergence": [
                    {
                        "variable": "temperature_c",
                        "model_values": {"gfs": 5.0, "ecmwf": 6.0, "icon": 5.5},
                        "mean": 5.5,
                        "spread": 1.0,
                        "agreement": "good",
                    }
                ],
            }
        ],
    }
    (pack / "snapshot.json").write_text(json.dumps(snapshot))

    # Digest
    digest = {
        "assessment": "GREEN",
        "assessment_reason": "Conditions favorable",
        "synoptic": "High pressure over Western Europe.",
        "winds": "Light westerlies at cruise level.",
        "cloud_visibility": "Mostly clear above 3000ft.",
        "precipitation_convection": "None expected.",
        "icing": "Negligible risk.",
        "specific_concerns": "None.",
        "model_agreement": "Good agreement across all models.",
        "trend": "Stable conditions expected.",
        "watch_items": "Monitor fog risk at EGTK.",
    }
    (pack / "digest.json").write_text(json.dumps(digest))

    # GRAMET (minimal valid-ish PNG header)
    (pack / "gramet.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    # Skew-T (ECMWF only)
    skewt_dir = pack / "skewt"
    skewt_dir.mkdir()
    for icao in ["EGTK", "LFPB", "LSGS"]:
        (skewt_dir / f"{icao}_ecmwf.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    return pack


class TestRenderHtml:
    def test_renders_html_string(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_contains_route(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert "EGTK" in html
        assert "LSGS" in html

    def test_contains_assessment(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert "GREEN" in html
        assert "Conditions favorable" in html

    def test_contains_synopsis_sections(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert "High pressure over Western Europe" in html
        assert "Light westerlies" in html
        assert "Monitor fog risk" in html

    def test_contains_gramet_data_uri(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert "data:image/png;base64," in html

    def test_contains_skewt_images(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        # Each waypoint should have a Skew-T card
        assert html.count("Skew-T") >= 3  # title + per-waypoint alt texts

    def test_contains_model_comparison(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert "temperature_c" in html
        assert "5.0" in html

    def test_missing_artifacts_graceful(self, tmp_path, sample_flight, sample_pack):
        """Renders without errors even when artifacts are missing."""
        empty_dir = tmp_path / "empty_pack"
        empty_dir.mkdir()
        html = render_html(empty_dir, sample_flight, sample_pack)
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_date_and_altitude_in_header(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack)
        assert "2026-02-21" in html
        assert "8000 ft" in html
        assert "D-2" in html


class TestRenderPdf:
    def test_renders_pdf_bytes(self, pack_dir, sample_flight, sample_pack):
        """PDF rendering produces bytes (mocking WeasyPrint)."""
        mock_pdf = b"%PDF-1.4 fake content"
        with patch("weasyprint.HTML") as mock_html_cls:
            mock_html_cls.return_value.write_pdf.return_value = mock_pdf
            result = render_pdf(pack_dir, sample_flight, sample_pack)
            assert result == mock_pdf
            mock_html_cls.assert_called_once()
            call_kwargs = mock_html_cls.call_args
            assert "string" in call_kwargs.kwargs
