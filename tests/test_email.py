"""Tests for the email notification module."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from email import message_from_bytes
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.notify.email import (
    SmtpConfig,
    _build_html_body,
    _build_plain_body,
    _build_subject,
    get_recipients,
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
def pack_dir(tmp_path):
    """Pack directory with digest.json."""
    pack = tmp_path / "pack"
    pack.mkdir()
    digest = {
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
    (pack / "digest.json").write_text(json.dumps(digest))
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


class TestGetRecipients:
    def test_comma_separated(self, monkeypatch):
        monkeypatch.setenv("WEATHERBRIEF_EMAIL_RECIPIENTS", "a@b.com, c@d.com")
        assert get_recipients() == ["a@b.com", "c@d.com"]

    def test_single_recipient(self, monkeypatch):
        monkeypatch.setenv("WEATHERBRIEF_EMAIL_RECIPIENTS", "a@b.com")
        assert get_recipients() == ["a@b.com"]

    def test_empty(self, monkeypatch):
        monkeypatch.setenv("WEATHERBRIEF_EMAIL_RECIPIENTS", "")
        assert get_recipients() == []

    def test_not_set(self, monkeypatch):
        monkeypatch.delenv("WEATHERBRIEF_EMAIL_RECIPIENTS", raising=False)
        assert get_recipients() == []


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
        html = _build_html_body(sample_flight, sample_pack, digest)
        assert "EGTK" in html
        assert "High pressure" in html
        assert "Fog" in html
        assert "GREEN" in html

    def test_plain_body_contains_key_info(self, sample_flight, sample_pack):
        digest = {"assessment": "GREEN", "assessment_reason": "OK", "synoptic": "High pressure.", "watch_items": "Fog."}
        text = _build_plain_body(sample_flight, sample_pack, digest)
        assert "EGTK" in text
        assert "High pressure" in text
        assert "PDF report" in text

    def test_body_without_digest(self, sample_flight, sample_pack):
        html = _build_html_body(sample_flight, sample_pack, None)
        assert "EGTK" in html
        text = _build_plain_body(sample_flight, sample_pack, None)
        assert "EGTK" in text


class TestSendBriefingEmail:
    def test_send_email_no_recipients_raises(self, sample_flight, sample_pack, pack_dir, smtp_config):
        with pytest.raises(ValueError, match="No email recipients"):
            send_briefing_email([], sample_flight, sample_pack, pack_dir, smtp_config)

    def test_send_email_calls_smtp(self, sample_flight, sample_pack, pack_dir, smtp_config):
        """Verify SMTP send_message is called with correct structure."""
        mock_pdf = b"%PDF-fake"
        with (
            patch("weatherbrief.notify.email.smtplib.SMTP") as mock_smtp_cls,
            patch("weatherbrief.report.render.render_pdf", return_value=mock_pdf),
        ):
            mock_server = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

            send_briefing_email(
                ["pilot@test.com"],
                sample_flight,
                sample_pack,
                pack_dir,
                smtp_config,
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
