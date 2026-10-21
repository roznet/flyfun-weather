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
        departure_time=datetime(2026, 2, 21, 9, tzinfo=timezone.utc),
        cruise_altitude_ft=8000,
        flight_duration_hours=4.5,
        created_at=datetime(2026, 2, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_pack():
    return BriefingPackMeta(
        flight_id="egtk_lsgs-2026-02-21",
        fetch_timestamp=datetime(2026, 2, 19, 18, 0, 0, tzinfo=timezone.utc),
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

    # Briefing (route + analyses + observations — no forecasts)
    briefing = {
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
    (pack / "briefing.json").write_text(json.dumps(briefing))

    # Digest
    digest = {
        "assessment": "GREEN",
        "assessment_reason": "Conditions favorable",
        "synoptic": "High pressure over Western Europe.",
        "specific_concerns": "None.",
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
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_contains_route(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "EGTK" in html
        assert "LSGS" in html

    def test_contains_assessment(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "GREEN" in html
        assert "Conditions favorable" in html

    def test_contains_synopsis_sections(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "High pressure over Western Europe" in html
        assert "Monitor fog risk" in html

    def test_contains_gramet_data_uri(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "data:image/png;base64," in html

    def test_contains_skewt_images(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        # Each waypoint should have a Skew-T card
        assert html.count("Skew-T") >= 3  # title + per-waypoint alt texts

    def test_contains_model_comparison(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "temperature_c" in html
        assert "5.0" in html

    def test_missing_artifacts_graceful(self, tmp_path, sample_flight, sample_pack):
        """Renders without errors even when artifacts are missing."""
        empty_dir = tmp_path / "empty_pack"
        empty_dir.mkdir()
        html = render_html(empty_dir, sample_flight, sample_pack, position_sharing="full")
        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html

    def test_date_and_altitude_in_header(self, pack_dir, sample_flight, sample_pack):
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "2026-02-21" in html
        assert "8000 ft" in html
        assert "D-2" in html

    def _with_sigmets(self, pack_dir, block):
        path = pack_dir / "briefing.json"
        briefing = json.loads(path.read_text())
        briefing["route_sigmets"] = block
        path.write_text(json.dumps(briefing))

    def test_failed_sigmet_fetch_reads_unavailable(self, pack_dir, sample_flight, sample_pack):
        """#734: a failed fetch's empty list is "not checked", not hidden."""
        self._with_sigmets(pack_dir, {
            "corridor_nm": 50.0, "fetch_time": "2026-02-21T07:00:00Z",
            "sigmets": [], "fetch_ok": False,
        })
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "SIGMETs Along Route" in html
        assert "SIGMETs unavailable at briefing time (source did not respond)" in html

    def test_older_pack_without_fetch_ok_renders_as_today(self, pack_dir, sample_flight, sample_pack):
        """No ``fetch_ok`` key (older packs) and nothing matched: no section."""
        self._with_sigmets(pack_dir, {
            "corridor_nm": 50.0, "fetch_time": "2026-02-21T07:00:00Z", "sigmets": [],
        })
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "SIGMETs Along Route" not in html
        assert "SIGMETs unavailable" not in html


    def _with_observations(self, pack_dir, airports):
        from weatherbrief.models.observations import RouteObservations

        obs = RouteObservations(
            corridor_nm=30.0, fetch_time=datetime(2026, 2, 21, 7, 0, tzinfo=timezone.utc),
            airports_found=len(airports),
            airports_with_metar=sum(a.has_metar for a in airports),
            airports_with_taf=sum(a.has_taf for a in airports),
            airports=airports,
        )
        path = pack_dir / "briefing.json"
        briefing = json.loads(path.read_text())
        # As the pipeline writes it: model_dump serializes the computed caption.
        briefing["route_observations"] = obs.model_dump(mode="json")
        path.write_text(json.dumps(briefing))

    def test_proxied_field_names_its_stand_in(self, pack_dir, sample_flight, sample_pack):
        """#776: a field with no METAR/TAF has no row, so the report names the
        station reading for it above the table, worded as the web does."""
        from weatherbrief.models.observations import AirportObservation, ObsProxy

        proxy = ObsProxy(icao="ZZPX", distance_nm=8.2, bearing_deg=45)
        self._with_observations(pack_dir, [
            AirportObservation(icao="ZZFD", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZFD",
                               metar_proxy=proxy, taf_proxy=proxy),
            AirportObservation(icao="ZZPX", distance_from_route_nm=6.0, nearest_waypoint_icao="ZZFD",
                               has_metar=True, metar_raw="ZZPX 210650Z 24008KT CAVOK 08/02 Q1022"),
        ])
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert '<p class="obs-proxy">ZZFD: using ZZPX as proxy · 8 NM NE</p>' in html
        assert html.count('class="obs-proxy"') == 1

    def test_no_proxy_line_when_fields_report_themselves(self, pack_dir, sample_flight, sample_pack):
        from weatherbrief.models.observations import AirportObservation

        self._with_observations(pack_dir, [
            AirportObservation(icao="ZZPX", distance_from_route_nm=6.0, nearest_waypoint_icao="ZZFD",
                               has_metar=True, metar_raw="ZZPX 210650Z 24008KT CAVOK 08/02 Q1022"),
        ])
        html = render_html(pack_dir, sample_flight, sample_pack, position_sharing="full")
        assert "METAR/TAF Observations" in html
        assert '<p class="obs-proxy">' not in html


class TestRenderPdf:
    def test_renders_pdf_bytes(self, pack_dir, sample_flight, sample_pack):
        """PDF rendering produces bytes (mocking WeasyPrint)."""
        mock_pdf = b"%PDF-1.4 fake content"
        with patch("weasyprint.HTML") as mock_html_cls:
            mock_html_cls.return_value.write_pdf.return_value = mock_pdf
            result = render_pdf(pack_dir, sample_flight, sample_pack, position_sharing="full")
            assert result == mock_pdf
            mock_html_cls.assert_called_once()
            call_kwargs = mock_html_cls.call_args
            assert "string" in call_kwargs.kwargs
