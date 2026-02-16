"""Tests for the email notification module."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.notify.email import (
    SmtpConfig,
    _build_html_body,
    _build_plain_body,
    _build_subject,
    _render_advisories_html,
    send_briefing_email,
)


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
def smtp_config():
    return SmtpConfig(
        host="smtp.example.com",
        port=587,
        user="test@example.com",
        password="secret",
        from_address="briefing@example.com",
        use_tls=True,
    )


@pytest.fixture
def sample_digest():
    return {
        "assessment": "GREEN",
        "assessment_reason": "Conditions favorable",
        "synoptic": "High pressure dominant.",
        "winds": "Light.",
        "cloud_visibility": "Clear.",
        "precipitation_convection": "None.",
        "icing": "None.",
        "specific_concerns": "None.",
        "model_agreement": "Good.",
        "trend": "Stable.",
        "watch_items": "Monitor EGTK fog.",
    }


@pytest.fixture
def sample_advisories():
    return {
        "advisories": [
            {
                "advisory_id": "icing_escape",
                "aggregate_status": "green",
                "aggregate_detail": "Freezing level above cruise",
            },
            {
                "advisory_id": "vmc_cruise",
                "aggregate_status": "amber",
                "aggregate_detail": "Cloud along 30% of route",
            },
        ],
        "catalog": [
            {"id": "icing_escape", "name": "Icing Escape"},
            {"id": "vmc_cruise", "name": "VMC at Cruise"},
        ],
    }


@pytest.fixture
def pack_dir(tmp_path, sample_digest, sample_advisories):
    """Pack directory with digest.json and route_advisories.json."""
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "digest.json").write_text(json.dumps(sample_digest))
    (pack / "route_advisories.json").write_text(json.dumps(sample_advisories))
    return pack


class TestSmtpConfig:
    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("WEATHERBRIEF_SMTP_HOST", "mail.test.com")
        monkeypatch.setenv("WEATHERBRIEF_SMTP_PORT", "465")
        monkeypatch.setenv("WEATHERBRIEF_SMTP_USER", "user")
        monkeypatch.setenv("WEATHERBRIEF_SMTP_PASSWORD", "pass")
        monkeypatch.setenv("WEATHERBRIEF_FROM_EMAIL", "from@test.com")
        monkeypatch.setenv("WEATHERBRIEF_SMTP_TLS", "false")

        cfg = SmtpConfig.from_env()
        assert cfg.host == "mail.test.com"
        assert cfg.port == 465
        assert cfg.user == "user"
        assert cfg.password == "pass"
        assert cfg.from_address == "from@test.com"
        assert cfg.use_tls is False

    def test_from_env_missing_raises(self, monkeypatch):
        monkeypatch.delenv("WEATHERBRIEF_SMTP_HOST", raising=False)
        with pytest.raises(ValueError, match="SMTP not fully configured"):
            SmtpConfig.from_env()

    def test_from_env_partial_raises(self, monkeypatch):
        """Missing any one required var should raise."""
        monkeypatch.setenv("WEATHERBRIEF_SMTP_HOST", "mail.test.com")
        monkeypatch.delenv("WEATHERBRIEF_SMTP_USER", raising=False)
        monkeypatch.delenv("WEATHERBRIEF_SMTP_PASSWORD", raising=False)
        monkeypatch.delenv("WEATHERBRIEF_FROM_EMAIL", raising=False)
        with pytest.raises(ValueError, match="SMTP not fully configured"):
            SmtpConfig.from_env()


class TestBuildSubject:
    def test_includes_assessment(self, sample_flight, sample_pack):
        subject = _build_subject(sample_flight, sample_pack)
        assert "[GREEN]" in subject
        assert "EGTK" in subject
        assert "2026-02-21" in subject
        assert "D-2" in subject

    def test_no_assessment(self, sample_flight, sample_pack):
        sample_pack.assessment = None
        subject = _build_subject(sample_flight, sample_pack)
        assert "[" not in subject
        assert "WeatherBrief" in subject


class TestBuildBody:
    def test_html_body_contains_key_info(self, sample_flight, sample_pack):
        digest = {"assessment": "GREEN", "assessment_reason": "OK", "synoptic": "High pressure.", "watch_items": "Fog."}
        body = _build_html_body(sample_flight, sample_pack, digest, None, "https://weather.example.com/briefing.html?flight=test")
        assert "EGTK" in body
        assert "High pressure" in body
        assert "Fog" in body
        assert "GREEN" in body
        assert "View Full Briefing" in body

    def test_html_body_contains_advisories(self, sample_flight, sample_pack, sample_advisories):
        body = _build_html_body(sample_flight, sample_pack, None, sample_advisories, "")
        assert "VMC at Cruise" in body
        assert "AMBER" in body

    def test_html_body_contains_briefing_link(self, sample_flight, sample_pack):
        link = "https://weather.test.com/briefing.html?flight=egtk_lsgs-2026-02-21"
        body = _build_html_body(sample_flight, sample_pack, None, None, link)
        assert link in body
        assert "View Full Briefing" in body

    def test_plain_body_contains_key_info(self, sample_flight, sample_pack):
        digest = {"assessment": "GREEN", "assessment_reason": "OK", "synoptic": "High pressure.", "watch_items": "Fog."}
        text = _build_plain_body(sample_flight, sample_pack, digest, None, "https://example.com")
        assert "EGTK" in text
        assert "High pressure" in text
        assert "https://example.com" in text

    def test_plain_body_contains_advisories(self, sample_flight, sample_pack, sample_advisories):
        text = _build_plain_body(sample_flight, sample_pack, None, sample_advisories, "")
        assert "VMC at Cruise" in text
        assert "AMBER" in text

    def test_body_without_digest(self, sample_flight, sample_pack):
        body = _build_html_body(sample_flight, sample_pack, None, None, "")
        assert "EGTK" in body
        text = _build_plain_body(sample_flight, sample_pack, None, None, "")
        assert "EGTK" in text


class TestRenderAdvisories:
    def test_all_green(self):
        data = {
            "advisories": [{"advisory_id": "test", "aggregate_status": "green", "per_model": []}],
            "catalog": [{"id": "test", "name": "Test Advisory"}],
        }
        result = _render_advisories_html(data)
        assert "Test Advisory" in result
        assert "GREEN" in result

    def test_amber_shown(self, sample_advisories):
        result = _render_advisories_html(sample_advisories)
        assert "VMC at Cruise" in result
        assert "AMBER" in result
        # All advisories shown (not just flagged)
        assert "Icing Escape" in result


class TestSendBriefingEmail:
    def test_send_email_no_recipients_raises(self, sample_flight, sample_pack, pack_dir, smtp_config):
        with pytest.raises(ValueError, match="No email recipients"):
            send_briefing_email([], sample_flight, sample_pack, pack_dir, smtp_config=smtp_config)

    def test_send_email_calls_smtp(self, sample_flight, sample_pack, pack_dir, smtp_config):
        """Verify SMTP send_message is called with correct structure."""
        with patch("weatherbrief.notify.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

            send_briefing_email(
                ["pilot@test.com"],
                sample_flight,
                sample_pack,
                pack_dir,
                base_url="https://weather.test.com",
                smtp_config=smtp_config,
            )

            mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("test@example.com", "secret")
            mock_server.send_message.assert_called_once()

            # Verify message structure
            msg = mock_server.send_message.call_args[0][0]
            assert "GREEN" in msg["Subject"]
            assert msg["To"] == "pilot@test.com"
            assert msg["From"] == "briefing@example.com"

    def test_send_email_no_pdf_attachment(self, sample_flight, sample_pack, pack_dir, smtp_config):
        """Email should NOT have a PDF attachment (lightweight HTML only)."""
        with patch("weatherbrief.notify.email.smtplib.SMTP") as mock_smtp_cls:
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

            send_briefing_email(
                ["pilot@test.com"],
                sample_flight,
                sample_pack,
                pack_dir,
                smtp_config=smtp_config,
            )

            msg = mock_server.send_message.call_args[0][0]
            # multipart/alternative with text + html, no application/pdf
            assert msg.get_content_type() == "multipart/alternative"
            subtypes = [p.get_content_type() for p in msg.get_payload()]
            assert "text/plain" in subtypes
            assert "text/html" in subtypes
            assert "application/pdf" not in subtypes
