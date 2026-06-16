"""Send lightweight HTML briefing emails with link to full web briefing."""

from __future__ import annotations

import html
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.models.airport_conditions import FLIGHT_CATEGORY_COLORS

logger = logging.getLogger(__name__)

_ASSESSMENT_COLORS = {
    "GREEN": ("#d1e7dd", "#0f5132"),
    "AMBER": ("#fff3cd", "#664d03"),
    "RED": ("#f8d7da", "#842029"),
}

# Long-range outlook — muted palette (paired with a dashed border) so it reads
# as a soft preview, distinct from the solid assessment traffic light.
_OUTLOOK_LABELS = {
    "TRENDING_SETTLED": "Trending settled",
    "MIXED_SIGNALS": "Mixed signals",
    "TRENDING_UNSETTLED": "Trending unsettled",
}
_OUTLOOK_COLORS = {
    "TRENDING_SETTLED": ("#f8f9fa", "#0f5132"),
    "MIXED_SIGNALS": ("#f8f9fa", "#555555"),
    "TRENDING_UNSETTLED": ("#f8f9fa", "#664d03"),
}

_ADVISORY_STATUS_COLORS = {
    "green": "#0f5132",
    "amber": "#b45309",
    "red": "#dc2626",
    "unavailable": "#888",
}


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
        user = os.environ.get("WEATHERBRIEF_SMTP_USER")
        password = os.environ.get("WEATHERBRIEF_SMTP_PASSWORD")
        from_address = os.environ.get("WEATHERBRIEF_FROM_EMAIL")

        if not all([host, user, password, from_address]):
            raise ValueError(
                "SMTP not fully configured. Set WEATHERBRIEF_SMTP_HOST, "
                "WEATHERBRIEF_SMTP_USER, WEATHERBRIEF_SMTP_PASSWORD, "
                "and WEATHERBRIEF_FROM_EMAIL."
            )
        return cls(
            host=host,
            port=int(os.environ.get("WEATHERBRIEF_SMTP_PORT", "587")),
            user=user,
            password=password,
            from_address=from_address,
            use_tls=os.environ.get("WEATHERBRIEF_SMTP_TLS", "true").lower() != "false",
        )


def _resend_send(api_key: str, msg: MIMEMultipart) -> None:
    """Send an email via the Resend HTTP API.

    Uses RESEND_FROM / RESEND_REPLY_TO env vars when set,
    otherwise falls back to the From header already on *msg*.
    """
    from_addr = os.environ.get("RESEND_FROM") or msg["From"]
    reply_to = os.environ.get("RESEND_REPLY_TO")

    # Extract plain and HTML parts from the multipart/alternative message
    text_body: str | None = None
    html_body: str | None = None
    for part in msg.get_payload():
        ct = part.get_content_type()
        if ct == "text/plain":
            text_body = part.get_payload(decode=True).decode()
        elif ct == "text/html":
            html_body = part.get_payload(decode=True).decode()

    payload: dict = {
        "from": from_addr,
        "to": [addr.strip() for addr in msg["To"].split(",")],
        "subject": msg["Subject"],
    }
    if html_body:
        payload["html"] = html_body
    if text_body:
        payload["text"] = text_body
    if reply_to:
        payload["reply_to"] = reply_to

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=30,
    )
    if not resp.is_success:
        logger.error("Resend API error %d: %s", resp.status_code, resp.text)
        raise RuntimeError(f"Resend API error {resp.status_code}: {resp.text}")
    logger.info("Email sent via Resend (id=%s)", resp.json().get("id"))


def send_message(msg: MIMEMultipart, smtp_config: SmtpConfig | None = None) -> None:
    """Send an email, routing through Resend when RESEND_API_KEY is set.

    Falls back to SMTP otherwise. *smtp_config* is only needed for the
    SMTP path and will be loaded from env if not provided.
    """
    resend_key = os.environ.get("RESEND_API_KEY")
    if resend_key:
        _resend_send(resend_key, msg)
        return

    if smtp_config is None:
        smtp_config = SmtpConfig.from_env()

    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        if smtp_config.use_tls:
            server.starttls()
        if smtp_config.user:
            server.login(smtp_config.user, smtp_config.password)
        server.send_message(msg)


def _briefing_url(base_url: str, flight_id: str) -> str:
    """Build the URL to the web briefing page."""
    return f"{base_url}/briefing.html?flight={quote(flight_id)}"


def _build_subject(flight: Flight, pack: BriefingPackMeta) -> str:
    """Build email subject line."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    assessment = f"[{pack.assessment}] " if pack.assessment else ""
    return f"{assessment}FlyFun Weather: {route} — {flight.target_date} D-{pack.days_out}"


def _render_advisories_html(advisories_data: dict) -> str:
    """Render all advisories as an HTML table with per-model status."""
    results = advisories_data.get("advisories", [])
    catalog = {e["id"]: e for e in advisories_data.get("catalog", [])}

    if not results:
        return ""

    # Collect model names from first advisory with per_model data
    models: list[str] = []
    for r in results:
        per_model = r.get("per_model", [])
        if per_model:
            models = [m["model"] for m in per_model]
            break

    model_headers = "".join(
        f'<th style="padding:3px 6px;background:#f0f0f0;font-size:11px;text-transform:uppercase;">'
        f'{html.escape(m)}</th>'
        for m in models
    )

    rows = []
    for adv in results:
        adv_id = adv["advisory_id"]
        name = catalog.get(adv_id, {}).get("name", adv_id)
        status = adv["aggregate_status"]
        detail = adv.get("aggregate_detail", "")
        color = _ADVISORY_STATUS_COLORS.get(status, "#333")

        model_cells = ""
        for m_result in adv.get("per_model", []):
            m_color = _ADVISORY_STATUS_COLORS.get(m_result.get("status", ""), "#888")
            m_label = m_result.get("status", "?").upper()
            model_cells += (
                f'<td style="padding:3px 6px;border-bottom:1px solid #eee;text-align:center;font-size:11px;">'
                f'<span style="color:{m_color};font-weight:600;">{m_label}</span></td>'
            )

        rows.append(
            f'<tr>'
            f'<td style="padding:3px 8px;border-bottom:1px solid #eee;">'
            f'<span style="color:{color};font-weight:600;">{html.escape(status.upper())}</span></td>'
            f'<td style="padding:3px 8px;border-bottom:1px solid #eee;font-weight:500;">{html.escape(name)}</td>'
            f'<td style="padding:3px 8px;border-bottom:1px solid #eee;color:#555;font-size:12px;">'
            f'{html.escape(detail)}</td>'
            f'{model_cells}'
            f'</tr>'
        )

    return (
        '<table style="border-collapse:collapse;width:100%;margin:4px 0;">'
        f'<thead><tr>'
        f'<th style="padding:3px 8px;background:#f0f0f0;font-size:11px;text-align:left;">Status</th>'
        f'<th style="padding:3px 8px;background:#f0f0f0;font-size:11px;text-align:left;">Advisory</th>'
        f'<th style="padding:3px 8px;background:#f0f0f0;font-size:11px;text-align:left;">Detail</th>'
        f'{model_headers}'
        f'</tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody>'
        '</table>'
    )


def _format_wind(cond: dict) -> str:
    """Format wind string from a condition dict."""
    from weatherbrief.analysis.airport_conditions import format_wind_string

    wind = format_wind_string(cond.get("wind_direction_deg"), cond.get("wind_speed_kt"), cond.get("wind_gust_kt"))
    if not wind:
        return ""
    rwy = f" RW{cond['best_runway']['runway_id']}" if cond.get("best_runway") else ""
    return f"{wind}kt{rwy}"


def _render_airport_card_html(apt: dict, role: str) -> str:
    """Render one airport card (departure or arrival) as an HTML table."""
    if not apt:
        return ""
    conditions = apt.get("conditions", [])
    if not conditions:
        return ""

    cat_colors = {k.upper(): v for k, v in FLIGHT_CATEGORY_COLORS.items()}
    icao = apt.get("icao", "")

    rows = []
    for cond in conditions:
        cat = cond.get("flight_category", "").upper()
        color = cat_colors.get(cat, "#333")
        vis = f"vis {cond['visibility_sm']}sm" if cond.get("visibility_sm") is not None else ""
        ceil = f"ceil {cond['ceiling_ft']:.0f}ft" if cond.get("ceiling_ft") is not None else "CLR"
        wind = _format_wind(cond)
        rows.append(
            f'<tr>'
            f'<td style="padding:2px 6px;font-size:12px;">{html.escape(cond.get("model", "").upper())}</td>'
            f'<td style="padding:2px 6px;"><span style="color:{color};font-weight:600;">{cat}</span></td>'
            f'<td style="padding:2px 6px;font-size:12px;">{html.escape(vis)}</td>'
            f'<td style="padding:2px 6px;font-size:12px;">{html.escape(ceil)}</td>'
            f'<td style="padding:2px 6px;font-size:12px;">{html.escape(wind)}</td>'
            f'</tr>'
        )

    return (
        f'<div style="flex:1;border:1px solid #ddd;border-radius:4px;padding:6px;min-width:250px;">'
        f'<div style="font-weight:600;font-size:12px;border-bottom:1px solid #eee;padding-bottom:3px;margin-bottom:3px;">'
        f'{html.escape(role)}: {html.escape(icao)}</div>'
        f'<table style="border-collapse:collapse;width:100%;">{"".join(rows)}</table>'
        f'</div>'
    )


def _render_airport_conditions_html(advisories_data: dict) -> str:
    """Render departure/arrival airport conditions as side-by-side cards."""
    airport_cond = advisories_data.get("airport_conditions")
    if not airport_cond:
        return ""

    dep = _render_airport_card_html(airport_cond.get("departure"), "Departure")
    arr = _render_airport_card_html(airport_cond.get("arrival"), "Arrival")
    if not dep and not arr:
        return ""

    return f'<div style="display:flex;gap:10px;margin:8px 0;">{dep}{arr}</div>'


def _digest_section(digest: dict, key: str, label: str) -> str:
    """Render one digest section as HTML if present."""
    text = digest.get(key, "")
    if not text:
        return ""
    return (
        f'<p style="margin:6px 0;">'
        f'<strong>{html.escape(label)}:</strong> {html.escape(text)}'
        f'</p>'
    )


def _build_html_body(
    flight: Flight,
    pack: BriefingPackMeta,
    digest: dict | None,
    advisories_data: dict | None,
    briefing_link: str,
) -> str:
    """Build a lightweight HTML email body with key briefing info and a link."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    alt_ft = flight.cruise_altitude_ft
    alt_str = f"FL{alt_ft // 100:03d}" if alt_ft >= 10000 else f"{alt_ft} ft"

    # Briefing link (prominent, at top)
    link_html = (
        f'<div style="margin-bottom:14px;">'
        f'<a href="{html.escape(briefing_link)}" '
        f'style="display:inline-block;padding:8px 16px;background:#2563eb;color:#fff;'
        f'text-decoration:none;border-radius:4px;font-weight:600;font-size:14px;">'
        f'View Full Briefing &rarr;</a>'
        f'</div>'
    )

    # Assessment / outlook banner. A long-range pack shows a soft "early outlook"
    # (dashed, muted) instead of the GREEN/AMBER/RED traffic light.
    assessment_html = ""
    outlook = getattr(pack, "outlook", None) or (digest.get("outlook") if digest else None)
    if outlook:
        o = outlook.upper()
        bg, fg = _OUTLOOK_COLORS.get(o, ("#f0f0f0", "#333"))
        label = _OUTLOOK_LABELS.get(o, outlook)
        o_reason = getattr(pack, "outlook_reason", None) or (
            digest.get("outlook_reason") if digest else None
        )
        esc_reason = html.escape(o_reason) if o_reason else ""
        assessment_html = (
            f'<div style="background:{bg};color:{fg};padding:8px 12px;'
            f'border:1px dashed {fg};border-radius:4px;font-weight:600;margin-bottom:12px;">'
            f'EARLY OUTLOOK &mdash; {html.escape(label)}'
            f'{f" &mdash; {esc_reason}" if o_reason else ""}</div>'
        )
    else:
        assessment = pack.assessment or (digest.get("assessment") if digest else None)
        reason = pack.assessment_reason or (digest.get("assessment_reason") if digest else None)
        if assessment:
            bg, fg = _ASSESSMENT_COLORS.get(assessment.upper(), ("#f0f0f0", "#333"))
            esc_reason = html.escape(reason) if reason else ""
            assessment_html = (
                f'<div style="background:{bg};color:{fg};padding:8px 12px;'
                f'border-radius:4px;font-weight:600;margin-bottom:12px;">'
                f'{html.escape(assessment)}{f" &mdash; {esc_reason}" if reason else ""}</div>'
            )

    # Airport conditions
    airport_html = ""
    if advisories_data:
        airport_html = _render_airport_conditions_html(advisories_data)

    # Advisories summary
    advisories_html = ""
    if advisories_data:
        advisories_html = (
            '<div style="margin:10px 0;">'
            '<strong style="font-size:13px;">Advisories</strong>'
            + _render_advisories_html(advisories_data)
            + '</div>'
        )

    # Digest sections — long-range swaps specific concerns for model agreement.
    digest_html = ""
    if digest:
        if digest.get("outlook"):
            sections = [
                ("synoptic", "Synoptic"),
                ("model_agreement", "Model Agreement"),
                ("trend", "Trend"),
                ("watch_items", "Watch Items"),
            ]
        else:
            sections = [
                ("synoptic", "Synoptic"),
                ("specific_concerns", "Specific Concerns"),
                ("watch_items", "Watch Items"),
            ]
        digest_parts = [_digest_section(digest, key, label) for key, label in sections]
        digest_html = "".join(digest_parts)

    return f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;max-width:600px;">
  <h2 style="margin:0 0 4px;">{html.escape(route)}</h2>
  <p style="color:#555;margin:0 0 12px;">
    {html.escape(flight.target_date)} &mdash; {flight.target_time_utc:02d}00Z &mdash; {html.escape(alt_str)}
    &mdash; D-{pack.days_out}
  </p>
  {link_html}
  {assessment_html}
  {airport_html}
  {advisories_html}
  <hr style="border:none;border-top:1px solid #eee;margin:10px 0;">
  {digest_html}
  <hr style="border:none;border-top:1px solid #eee;margin:10px 0;">
  <p style="color:#888;font-size:12px;">
    <a href="{html.escape(briefing_link)}" style="color:#888;">Open full briefing on FlyFun Weather</a>
  </p>
</body>
</html>"""


def _build_plain_body(
    flight: Flight,
    pack: BriefingPackMeta,
    digest: dict | None,
    advisories_data: dict | None,
    briefing_link: str,
) -> str:
    """Build a plain-text fallback email body."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    alt_ft = flight.cruise_altitude_ft
    alt_str = f"FL{alt_ft // 100:03d}" if alt_ft >= 10000 else f"{alt_ft} ft"

    lines = [
        f"View full briefing: {briefing_link}",
        "",
        route,
        f"{flight.target_date} — {flight.target_time_utc:02d}00Z — {alt_str} — D-{pack.days_out}",
        "",
    ]

    outlook = getattr(pack, "outlook", None) or (digest.get("outlook") if digest else None)
    if outlook:
        label = _OUTLOOK_LABELS.get(outlook.upper(), outlook)
        o_reason = getattr(pack, "outlook_reason", None) or (
            digest.get("outlook_reason") if digest else None
        )
        lines.append(f"EARLY OUTLOOK — {label}{f' — {o_reason}' if o_reason else ''}")
        lines.append("")
    else:
        assessment = pack.assessment or (digest.get("assessment") if digest else None)
        reason = pack.assessment_reason or (digest.get("assessment_reason") if digest else None)
        if assessment:
            lines.append(f"{assessment}{f' — {reason}' if reason else ''}")
            lines.append("")

    # Advisories summary
    if advisories_data:
        results = advisories_data.get("advisories", [])
        catalog = {e["id"]: e for e in advisories_data.get("catalog", [])}
        flagged = [r for r in results if r.get("aggregate_status") in ("amber", "red")]
        if flagged:
            lines.append("ADVISORIES:")
            for adv in flagged:
                name = catalog.get(adv["advisory_id"], {}).get("name", adv["advisory_id"])
                lines.append(f"  {adv['aggregate_status'].upper()} — {name}: {adv.get('aggregate_detail', '')}")
            lines.append("")
        else:
            lines.extend(["All advisories GREEN", ""])

    if digest:
        if digest.get("outlook"):
            section_keys = [
                ("synoptic", "Synoptic"),
                ("model_agreement", "Model Agreement"),
                ("trend", "Trend"),
                ("watch_items", "Watch Items"),
            ]
        else:
            section_keys = [
                ("synoptic", "Synoptic"),
                ("winds", "Winds"),
                ("icing", "Icing"),
                ("specific_concerns", "Specific Concerns"),
                ("watch_items", "Watch Items"),
            ]
        for key, label in section_keys:
            text = digest.get(key, "")
            if text:
                lines.extend([f"{label}: {text}", ""])

    return "\n".join(lines)


def send_briefing_email(
    recipients: list[str],
    flight: Flight,
    pack: BriefingPackMeta,
    pack_dir: Path,
    base_url: str = "",
    smtp_config: SmtpConfig | None = None,
) -> None:
    """Send a lightweight HTML briefing email with link to the full web briefing.

    Args:
        recipients: Email addresses to send to.
        flight: The flight definition.
        pack: Pack metadata.
        pack_dir: Directory containing pack artifacts.
        base_url: Base URL of the web app (e.g. "https://weather.flyfun.aero").
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
    digest: dict | None = None
    digest_path = pack_dir / "digest.json"
    if digest_path.exists():
        digest = json.loads(digest_path.read_text())

    # Load advisories for summary
    advisories_data: dict | None = None
    adv_path = pack_dir / "route_advisories.json"
    if adv_path.exists():
        advisories_data = json.loads(adv_path.read_text())

    briefing_link = _briefing_url(base_url, flight.id) if base_url else ""

    # Build email — simple alternative (no attachment)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(flight, pack)
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(
        _build_plain_body(flight, pack, digest, advisories_data, briefing_link), "plain",
    ))
    msg.attach(MIMEText(
        _build_html_body(flight, pack, digest, advisories_data, briefing_link), "html",
    ))

    # Send
    logger.info("Sending briefing email to %s", recipients)
    send_message(msg, smtp_config)
    logger.info("Briefing email sent successfully")
