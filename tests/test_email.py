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
        "specific_concerns": "None.",
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
        assert "FlyFun Weather" in subject


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


# ---------------------------------------------------------------------------
# Digest sections (#753 small fixes)
# ---------------------------------------------------------------------------


class TestDigestSections:
    def test_plain_body_reads_only_current_digest_keys(self, sample_flight, sample_pack):
        """`winds` / `icing` are gone from WeatherDigest; `trend` is there."""
        digest = {
            "assessment": "GREEN", "synoptic": "High.", "specific_concerns": "None.",
            "trend": "Improving.", "watch_items": "Fog.",
            # A stale key a broken template would still print:
            "winds": "SHOULD NOT APPEAR",
        }
        text = _build_plain_body(sample_flight, sample_pack, digest, None, "")
        assert "Trend: Improving." in text
        assert "SHOULD NOT APPEAR" not in text
        assert "Winds:" not in text and "Icing:" not in text

    def test_html_body_shows_short_range_trend(self, sample_flight, sample_pack, sample_digest):
        body = _build_html_body(sample_flight, sample_pack, sample_digest, None, "")
        assert "Trend:" in body and "Stable." in body


# ---------------------------------------------------------------------------
# Flight-day brief (#753)
# ---------------------------------------------------------------------------

from weatherbrief.notify.email import (  # noqa: E402
    FLIGHT_DAY_APP_NOTE,
    FLIGHT_DAY_PUSH_NOTE,
    AdvisoryStatusChange,
    FlightDaySince,
    build_flight_day_html,
    build_flight_day_plain,
    flight_day_subject,
    send_flight_day_email,
)

_FD_DEP = datetime(2026, 2, 21, 9, tzinfo=timezone.utc)
_FD_NOW = datetime(2026, 2, 21, 7, 2, tzinfo=timezone.utc)


def _fd_flight():
    return Flight(
        id="zz-flightday",
        route_name="egtk_lfat",
        waypoints=["EGTK", "LFAT"],
        departure_time=_FD_DEP,
        cruise_altitude_ft=5500,
        flight_duration_hours=1.5,
        created_at=datetime(2026, 2, 19, tzinfo=timezone.utc),
    )


def _fd_pack(assessment: str):
    return BriefingPackMeta(
        flight_id="zz-flightday",
        fetch_timestamp=datetime(2026, 2, 21, 6, 40, tzinfo=timezone.utc),
        days_out=0,
        assessment=assessment,
        assessment_reason={
            "GREEN": "VMC throughout",
            "AMBER": "Low cloud at destination around ETA",
            "RED": "Embedded CB on the route",
            "UNAVAILABLE": None,
        }[assessment],
    )


def _fd_digest(assessment: str) -> dict:
    return {
        "assessment": assessment,
        "assessment_reason": "x",
        "synoptic": "Weak front clearing east through the morning.",
        "specific_concerns": "Stratus at LFAT until mid-morning.",
        "trend": "Improving after 10Z.",
        "watch_items": "LFAT TAF amendments.",
    }


def _fd_advisories(assessment: str) -> dict:
    vmc = {"GREEN": "green", "AMBER": "amber", "RED": "red", "UNAVAILABLE": "unavailable"}[assessment]
    return {
        "advisories": [
            {"advisory_id": "icing_escape", "aggregate_status": "green",
             "aggregate_detail": "Freezing level above cruise"},
            {"advisory_id": "vmc_cruise", "aggregate_status": vmc,
             "aggregate_detail": "Cloud along 30% of route"},
        ],
        "catalog": [
            {"id": "icing_escape", "name": "Icing Escape"},
            {"id": "vmc_cruise", "name": "VMC at Cruise"},
        ],
    }


def _fd_live(*, alerts: bool, highlight: bool = True) -> dict:
    """A live block built by the real ``summarize_live`` from a layer, so the
    template is tested against the shape it gets in production."""
    from weatherbrief.models.live import (
        LiveChange, LiveChanges, LiveGlance, LiveGlanceLine, LiveHighlight, LiveLayer,
    )
    from weatherbrief.models.observations import (
        AirportObservation, RouteObservations, RouteSigmets, SigmetAlongRoute,
    )
    from weatherbrief.tasks.live_layer import summarize_live

    changes = []
    if alerts:
        changes.append(LiveChange(
            key="metar:LFAT", kind="metar_category", source="METAR", direction="worse",
            tier="alert", role="destination", icao="LFAT", from_value="VFR", to_value="IFR",
            observed_at=_FD_NOW, message="LFAT METAR: VFR → IFR",
        ))
    changes.append(LiveChange(
        key="radar:R1", kind="radar", source="RADAR", direction="worse", tier="highlight",
        role="route", message="Heavy radar echo within 10 NM of route",
    ))
    airports = [
        AirportObservation(
            icao="EGTK", distance_from_route_nm=0, nearest_waypoint_icao="EGTK",
            metar_raw="EGTK 210650Z 24008KT 9999 SCT030 08/04 Q1018",
            metar_time=datetime(2026, 2, 21, 6, 50, tzinfo=timezone.utc),
            metar_flight_category="VFR",
        ),
        AirportObservation(
            icao="LFAT", distance_from_route_nm=0, nearest_waypoint_icao="LFAT",
            metar_raw="LFAT 210700Z 20006KT 3000 BR OVC004 07/06 Q1016",
            metar_time=datetime(2026, 2, 21, 7, 0, tzinfo=timezone.utc),
            metar_flight_category="IFR" if alerts else "VFR",
        ),
    ]
    glance = LiveGlance(
        as_of=_FD_NOW,
        headline="Observed 07:02Z · 1 worse since the briefing (arrival)" if alerts
        else "Observed 07:02Z · as briefed",
        comparison="worse" if alerts else "as_briefed",
        lines=[
            LiveGlanceLine(phase="departure", icao="EGTK", text="VFR 06:50Z · no storms near · no lightning"),
            LiveGlanceLine(phase="enroute", text="No storms ahead · SIGMET LFFF 3: SEV TURB"),
            LiveGlanceLine(phase="arrival", icao="LFAT", text="IFR now · TAF at ETA MVFR · no storms"),
        ],
        highlight=LiveHighlight(
            text="LFAT has dropped to IFR in mist; the TAF lifts it to MVFR by your ETA.",
            model="m", facts_hash="h", generated_at=datetime(2026, 2, 21, 7, 3, tzinfo=timezone.utc),
        ) if highlight else None,
    )
    layer = LiveLayer(
        flight_id="zz-flightday", pack_timestamp="2026-02-21T06:40:00+00:00",
        pack_dir_name="p", live_updated_at=_FD_NOW,
        route_observations=RouteObservations(
            corridor_nm=30, fetch_time=_FD_NOW, airports_found=2, airports_with_metar=2,
            airports_with_taf=1, airports=airports,
        ),
        route_sigmets=RouteSigmets(
            corridor_nm=50, fetch_time=_FD_NOW,
            sigmets=[SigmetAlongRoute(
                fir_id="LFFF", hazard="TURB", qualifier="SEV",
                valid_from=datetime(2026, 2, 21, 6, tzinfo=timezone.utc),
                valid_to=datetime(2026, 2, 21, 10, tzinfo=timezone.utc),
            )],
        ),
        glance=glance,
        changes=LiveChanges(computed_at=_FD_NOW, changes=changes),
    )
    briefing = {"route": {"waypoints": [{"icao": "EGTK"}, {"icao": "LFAT"}]}}
    return summarize_live(layer, briefing)


def _fd_since(refreshed: bool, assessment: str) -> FlightDaySince:
    if not refreshed:
        return FlightDaySince(
            refreshed=False, briefing_at=datetime(2026, 2, 21, 6, 40, tzinfo=timezone.utc),
            assessment=assessment,
        )
    return FlightDaySince(
        refreshed=True, briefing_at=datetime(2026, 2, 21, 6, 40, tzinfo=timezone.utc),
        prior_briefing_at=datetime(2026, 2, 20, 18, 5, tzinfo=timezone.utc),
        prior_assessment="GREEN", assessment=assessment,
        advisory_changes=[AdvisoryStatusChange(name="VMC at Cruise", from_status="green", to_status="amber")],
    )


_LINK = "https://weather.example.com/briefing.html?flight=zz-flightday#observed-glance-wrapper"


@pytest.fixture
def flight_day_samples(request, tmp_path):
    """Render the flight-day email for green/amber/red × with/without live
    alerts (HTML + text) so the format can be reviewed without sending.

    Written to ``$WB_EMAIL_SAMPLES_DIR`` when set (e.g.
    ``WB_EMAIL_SAMPLES_DIR=/tmp/fd pytest tests/test_email.py -k samples``),
    else to the test's tmp dir.
    """
    import os

    out = Path(os.environ.get("WB_EMAIL_SAMPLES_DIR") or tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    samples = {}
    for grade in ("GREEN", "AMBER", "RED"):
        for alerts in (False, True):
            live = _fd_live(alerts=alerts)
            args = (_fd_flight(), _fd_pack(grade), _fd_digest(grade), _fd_advisories(grade))
            kwargs = dict(
                live=live, since=_fd_since(grade != "GREEN", grade),
                has_device=alerts, briefing_link=_LINK,
            )
            name = f"flight_day_{grade.lower()}_{'alerts' if alerts else 'quiet'}"
            html_body = build_flight_day_html(*args, **kwargs)
            text_body = build_flight_day_plain(*args, **kwargs)
            subject = flight_day_subject(_fd_flight(), _fd_pack(grade), live)
            (out / f"{name}.html").write_text(html_body)
            (out / f"{name}.txt").write_text(f"Subject: {subject}\n\n{text_body}")
            samples[(grade, alerts)] = (subject, html_body, text_body)
    return samples


class TestFlightDayEmail:
    def test_samples_render_all_four_sections_in_order(self, flight_day_samples):
        assert len(flight_day_samples) == 6
        for (grade, alerts), (subject, html_body, text_body) in flight_day_samples.items():
            heads = ["Observed now", "Since the last briefing", "Forecast assessment", "During the flight"]
            positions = [html_body.index(h) for h in heads]
            assert positions == sorted(positions), (grade, alerts)
            plain_heads = ["OBSERVED NOW", "SINCE THE LAST BRIEFING", "FORECAST ASSESSMENT", "DURING THE FLIGHT"]
            plain_pos = [text_body.index(h) for h in plain_heads]
            assert plain_pos == sorted(plain_pos), (grade, alerts)
            assert grade in subject
            assert subject.startswith("Today 09:00Z EGTK → LFAT")

    def test_subject_leads_with_alert_when_there_is_one(self, flight_day_samples):
        subject, _, _ = flight_day_samples[("AMBER", True)]
        assert subject == "Today 09:00Z EGTK → LFAT · AMBER · LFAT METAR: VFR → IFR"
        quiet, _, _ = flight_day_samples[("AMBER", False)]
        assert quiet == "Today 09:00Z EGTK → LFAT · AMBER"

    def test_observed_section_quotes_the_live_block(self, flight_day_samples):
        _, html_body, text_body = flight_day_samples[("RED", True)]
        for body in (html_body, text_body):
            assert "LFAT has dropped to IFR in mist" in body
            assert "Experimental" in body and "07:03Z" in body
            assert "LFAT METAR: VFR → IFR" in body           # alert-tier row
            assert "Heavy radar echo" not in body            # highlight-tier row is not an alert
            assert "TAF at ETA MVFR" in body                 # arrival glance line
            assert "LFAT 210700Z 20006KT 3000 BR OVC004" in body  # raw METAR
            assert "LFFF" in body                            # route SIGMET

    def test_advisory_table_keeps_only_amber_and_red(self, flight_day_samples):
        _, html_body, _ = flight_day_samples[("AMBER", False)]
        assert "VMC at Cruise" in html_body
        assert "Icing Escape" not in html_body
        assert "all advisories" in html_body
        _, green_html, green_text = flight_day_samples[("GREEN", False)]
        assert "All advisories GREEN" in green_html and "All advisories GREEN" in green_text

    def test_since_section(self, flight_day_samples):
        _, _, quiet = flight_day_samples[("GREEN", False)]
        assert "No model update since the 06:40Z briefing." in quiet
        _, _, changed = flight_day_samples[("AMBER", False)]
        assert "Grade: GREEN → AMBER" in changed
        assert "VMC at Cruise: GREEN → AMBER" in changed
        assert "previous 20 Feb 18:05Z" in changed

    def test_forecast_is_labelled_with_its_written_time(self, flight_day_samples):
        _, html_body, text_body = flight_day_samples[("RED", False)]
        assert "Forecast written 06:40Z (D-0)" in html_body
        assert "written 06:40Z, D-0" in text_body
        assert "Trend:" in html_body

    def test_device_note(self, flight_day_samples):
        _, with_device, _ = flight_day_samples[("GREEN", True)]
        assert FLIGHT_DAY_PUSH_NOTE in with_device
        _, without, text_without = flight_day_samples[("GREEN", False)]
        assert FLIGHT_DAY_APP_NOTE in without and FLIGHT_DAY_APP_NOTE in text_without

    def test_no_highlight_omits_its_line(self):
        live = _fd_live(alerts=True, highlight=False)
        args = (_fd_flight(), _fd_pack("AMBER"), _fd_digest("AMBER"), _fd_advisories("AMBER"))
        kwargs = dict(live=live, since=_fd_since(False, "AMBER"), has_device=False, briefing_link="")
        for body in (build_flight_day_html(*args, **kwargs), build_flight_day_plain(*args, **kwargs)):
            assert "Experimental" not in body
            assert "LFAT METAR: VFR → IFR" in body

    def test_no_live_layer_still_renders(self):
        args = (_fd_flight(), _fd_pack("GREEN"), _fd_digest("GREEN"), None)
        kwargs = dict(live=None, since=_fd_since(False, "GREEN"), has_device=False, briefing_link="")
        html_body = build_flight_day_html(*args, **kwargs)
        assert "No live observations for this flight yet." in html_body
        assert "Forecast assessment" in html_body

    def test_unavailable_grade_is_not_headlined(self):
        live = _fd_live(alerts=True)
        subject = flight_day_subject(_fd_flight(), _fd_pack("UNAVAILABLE"), live)
        assert "UNAVAILABLE" not in subject
        assert subject == "Today 09:00Z EGTK → LFAT · LFAT METAR: VFR → IFR"

    def test_send_attaches_both_parts(self, tmp_path, smtp_config):
        pack = tmp_path / "pack"
        pack.mkdir()
        (pack / "digest.json").write_text(json.dumps(_fd_digest("AMBER")))
        (pack / "route_advisories.json").write_text(json.dumps(_fd_advisories("AMBER")))
        with patch("weatherbrief.notify.email.send_message") as mock_send:
            send_flight_day_email(
                ["pilot@example.com"], _fd_flight(), _fd_pack("AMBER"), pack,
                live=_fd_live(alerts=True), since=_fd_since(True, "AMBER"), has_device=True,
                base_url="https://weather.example.com", smtp_config=smtp_config,
            )
        msg = mock_send.call_args.args[0]
        assert msg["Subject"].startswith("Today 09:00Z")
        parts = [p.get_content_type() for p in msg.get_payload()]
        assert parts == ["text/plain", "text/html"]
        html_part = msg.get_payload()[1].get_payload(decode=True).decode()
        assert "briefing.html?flight=zz-flightday#observed-glance-wrapper" in html_part
