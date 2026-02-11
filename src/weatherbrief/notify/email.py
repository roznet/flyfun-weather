"""Send briefing emails with HTML body + PDF attachment via SMTP."""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from pydantic import BaseModel

from weatherbrief.models import BriefingPackMeta, Flight

logger = logging.getLogger(__name__)


class SmtpConfig(BaseModel):
    """SMTP settings loaded from environment variables."""

    host: str
    port: int = 587
    user: str
    password: str
    from_address: str
    use_tls: bool = True

    @classmethod
    def from_env(cls) -> SmtpConfig:
        """Load from environment variables. Raises ValueError if not configured."""
        host = os.environ.get("WEATHERBRIEF_SMTP_HOST")
        if not host:
            raise ValueError(
                "SMTP not configured. Set WEATHERBRIEF_SMTP_HOST, "
                "WEATHERBRIEF_SMTP_USER, WEATHERBRIEF_SMTP_PASSWORD, "
                "and WEATHERBRIEF_FROM_EMAIL."
            )
        return cls(
            host=host,
            port=int(os.environ.get("WEATHERBRIEF_SMTP_PORT", "587")),
            user=os.environ.get("WEATHERBRIEF_SMTP_USER", ""),
            password=os.environ.get("WEATHERBRIEF_SMTP_PASSWORD", ""),
            from_address=os.environ.get("WEATHERBRIEF_FROM_EMAIL", ""),
            use_tls=os.environ.get("WEATHERBRIEF_SMTP_TLS", "true").lower() != "false",
        )


def get_recipients() -> list[str]:
    """Read recipients from WEATHERBRIEF_EMAIL_RECIPIENTS env var (comma-separated)."""
    raw = os.environ.get("WEATHERBRIEF_EMAIL_RECIPIENTS", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def _build_subject(flight: Flight, pack: BriefingPackMeta) -> str:
    """Build email subject line."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    assessment = f"[{pack.assessment}] " if pack.assessment else ""
    return f"{assessment}WeatherBrief: {route} — {flight.target_date} D-{pack.days_out}"


def _build_html_body(
    flight: Flight,
    pack: BriefingPackMeta,
    digest: dict | None,
) -> str:
    """Build a concise HTML email body with key briefing info."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    alt_ft = flight.cruise_altitude_ft
    alt_str = f"FL{alt_ft // 100:03d}" if alt_ft >= 10000 else f"{alt_ft} ft"

    # Assessment banner
    colors = {
        "GREEN": ("#d1e7dd", "#0f5132"),
        "AMBER": ("#fff3cd", "#664d03"),
        "RED": ("#f8d7da", "#842029"),
    }
    assessment_html = ""
    assessment = pack.assessment or (digest.get("assessment") if digest else None)
    reason = pack.assessment_reason or (digest.get("assessment_reason") if digest else None)
    if assessment:
        bg, fg = colors.get(assessment.upper(), ("#f0f0f0", "#333"))
        assessment_html = (
            f'<div style="background:{bg};color:{fg};padding:8px 12px;'
            f'border-radius:4px;font-weight:600;margin-bottom:12px;">'
            f'{assessment}{f" &mdash; {reason}" if reason else ""}</div>'
        )

    # Synopsis excerpt
    synopsis_html = ""
    if digest:
        synoptic = digest.get("synoptic", "")
        if synoptic:
            synopsis_html = f"<p><strong>Synoptic:</strong> {synoptic}</p>"
        watch = digest.get("watch_items", "")
        if watch:
            synopsis_html += f"<p><strong>Watch Items:</strong> {watch}</p>"

    return f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;">
  <h2 style="margin:0 0 4px;">{route}</h2>
  <p style="color:#555;margin:0 0 12px;">
    {flight.target_date} &mdash; {flight.target_time_utc:02d}00Z &mdash; {alt_str}
    &mdash; D-{pack.days_out}
  </p>
  {assessment_html}
  {synopsis_html}
  <hr style="border:none;border-top:1px solid #ddd;margin:12px 0;">
  <p style="color:#888;font-size:12px;">Full details in the attached PDF report.</p>
</body>
</html>"""


def _build_plain_body(
    flight: Flight,
    pack: BriefingPackMeta,
    digest: dict | None,
) -> str:
    """Build a plain-text fallback email body."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    alt_ft = flight.cruise_altitude_ft
    alt_str = f"FL{alt_ft // 100:03d}" if alt_ft >= 10000 else f"{alt_ft} ft"

    lines = [
        f"{route}",
        f"{flight.target_date} — {flight.target_time_utc:02d}00Z — {alt_str} — D-{pack.days_out}",
        "",
    ]
    assessment = pack.assessment or (digest.get("assessment") if digest else None)
    reason = pack.assessment_reason or (digest.get("assessment_reason") if digest else None)
    if assessment:
        lines.append(f"{assessment}{f' — {reason}' if reason else ''}")
        lines.append("")
    if digest:
        synoptic = digest.get("synoptic", "")
        if synoptic:
            lines.extend([f"Synoptic: {synoptic}", ""])
        watch = digest.get("watch_items", "")
        if watch:
            lines.extend([f"Watch Items: {watch}", ""])
    lines.append("Full details in the attached PDF report.")
    return "\n".join(lines)


def send_briefing_email(
    recipients: list[str],
    flight: Flight,
    pack: BriefingPackMeta,
    pack_dir: Path,
    smtp_config: SmtpConfig | None = None,
) -> None:
    """Send briefing email with HTML body + PDF attachment.

    Args:
        recipients: Email addresses to send to.
        flight: The flight definition.
        pack: Pack metadata.
        pack_dir: Directory containing pack artifacts.
        smtp_config: SMTP settings; loaded from env if None.

    Raises:
        ValueError: If SMTP is not configured or no recipients.
        smtplib.SMTPException: On send failure.
    """
    if not recipients:
        raise ValueError("No email recipients specified")

    if smtp_config is None:
        smtp_config = SmtpConfig.from_env()

    # Load digest JSON for email body context
    import json

    digest: dict | None = None
    digest_path = pack_dir / "digest.json"
    if digest_path.exists():
        digest = json.loads(digest_path.read_text())

    # Build email
    msg = MIMEMultipart("mixed")
    msg["Subject"] = _build_subject(flight, pack)
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(recipients)

    # Body: multipart/alternative with plain text + HTML
    body_alt = MIMEMultipart("alternative")
    body_alt.attach(MIMEText(_build_plain_body(flight, pack, digest), "plain"))
    body_alt.attach(MIMEText(_build_html_body(flight, pack, digest), "html"))
    msg.attach(body_alt)

    # PDF attachment
    from weatherbrief.report.render import render_pdf

    pdf_bytes = render_pdf(pack_dir, flight, pack)
    route_slug = flight.route_name or "-".join(flight.waypoints)
    filename = f"briefing_{route_slug}_{flight.target_date}_d{pack.days_out}.pdf"

    pdf_part = MIMEApplication(pdf_bytes, _subtype="pdf")
    pdf_part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(pdf_part)

    # Send
    logger.info("Sending briefing email to %s via %s:%d", recipients, smtp_config.host, smtp_config.port)
    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        if smtp_config.use_tls:
            server.starttls()
        if smtp_config.user:
            server.login(smtp_config.user, smtp_config.password)
        server.send_message(msg)
    logger.info("Briefing email sent successfully")
