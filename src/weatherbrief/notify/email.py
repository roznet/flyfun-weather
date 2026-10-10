"""Send lightweight HTML briefing emails with link to full web briefing."""

from __future__ import annotations

import html
import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import quote

import httpx
from pydantic import BaseModel

from weatherbrief.digest.outlook import OUTLOOK_LABELS
from weatherbrief.privacy import mask_email
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.models.airport_conditions import FLIGHT_CATEGORY_COLORS

logger = logging.getLogger(__name__)

_ASSESSMENT_COLORS = {
    "GREEN": ("#d1e7dd", "#0f5132"),
    "AMBER": ("#fff3cd", "#664d03"),
    "RED": ("#f8d7da", "#842029"),
    # Could not assess (#392) — deliberately colourless rather than falling through
    # to the default grey by accident. Automatic briefing notifications are
    # suppressed for this state (notify/dispatch.py), but the on-demand "email me
    # this briefing" endpoint still sends: the user explicitly asked for it, and
    # silently dropping their request would be worse than an honest empty-handed
    # email.
    "UNAVAILABLE": ("#f8f9fa", "#495057"),
}

# Long-range outlook — muted palette (paired with a dashed border) so it reads
# as a soft preview, distinct from the solid assessment traffic light. Labels are
# the shared canonical strings (see weatherbrief.digest.outlook).
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

# Digest sections, in reading order. The keys are the fields of
# ``digest/llm_digest.py``'s ``WeatherDigest`` / ``LongRangeDigest``: an email
# that names a key the model no longer returns renders nothing for it, silently.
_SHORT_RANGE_SECTIONS = [
    ("synoptic", "Synoptic"),
    ("specific_concerns", "Specific Concerns"),
    ("trend", "Trend"),
    ("watch_items", "Watch Items"),
]
_LONG_RANGE_SECTIONS = [
    ("synoptic", "Synoptic"),
    ("model_agreement", "Model Agreement"),
    ("trend", "Trend"),
    ("watch_items", "Watch Items"),
]


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


def _render_advisories_html(advisories_data: dict, *, only: set[str] | None = None) -> str:
    """Render advisories as an HTML table with per-model status.

    ``only`` keeps the rows whose aggregate status is in it (the flight-day
    email lists amber/red only); None keeps every row.
    """
    results = advisories_data.get("advisories", [])
    if only is not None:
        results = [r for r in results if r.get("aggregate_status") in only]
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


def _assessment_banner_html(pack: BriefingPackMeta, digest: dict | None) -> str:
    """Assessment / outlook banner. A long-range pack shows a soft "early
    outlook" (dashed, muted) instead of the GREEN/AMBER/RED traffic light."""
    outlook = getattr(pack, "outlook", None) or (digest.get("outlook") if digest else None)
    if outlook:
        o = outlook.upper()
        bg, fg = _OUTLOOK_COLORS.get(o, ("#f0f0f0", "#333"))
        label = OUTLOOK_LABELS.get(o, outlook)
        o_reason = getattr(pack, "outlook_reason", None) or (
            digest.get("outlook_reason") if digest else None
        )
        esc_reason = html.escape(o_reason) if o_reason else ""
        return (
            f'<div style="background:{bg};color:{fg};padding:8px 12px;'
            f'border:1px dashed {fg};border-radius:4px;font-weight:600;margin-bottom:12px;">'
            f'EARLY OUTLOOK &mdash; {html.escape(label)}'
            f'{f" &mdash; {esc_reason}" if o_reason else ""}</div>'
        )
    assessment = pack.assessment or (digest.get("assessment") if digest else None)
    reason = pack.assessment_reason or (digest.get("assessment_reason") if digest else None)
    if not assessment:
        return ""
    bg, fg = _ASSESSMENT_COLORS.get(assessment.upper(), ("#f0f0f0", "#333"))
    esc_reason = html.escape(reason) if reason else ""
    return (
        f'<div style="background:{bg};color:{fg};padding:8px 12px;'
        f'border-radius:4px;font-weight:600;margin-bottom:12px;">'
        f'{html.escape(assessment)}{f" &mdash; {esc_reason}" if reason else ""}</div>'
    )


def _assessment_banner_plain(pack: BriefingPackMeta, digest: dict | None) -> str:
    """Plain-text twin of :func:`_assessment_banner_html` ("" when none)."""
    outlook = getattr(pack, "outlook", None) or (digest.get("outlook") if digest else None)
    if outlook:
        label = OUTLOOK_LABELS.get(outlook.upper(), outlook)
        o_reason = getattr(pack, "outlook_reason", None) or (
            digest.get("outlook_reason") if digest else None
        )
        return f"EARLY OUTLOOK — {label}{f' — {o_reason}' if o_reason else ''}"
    assessment = pack.assessment or (digest.get("assessment") if digest else None)
    reason = pack.assessment_reason or (digest.get("assessment_reason") if digest else None)
    if not assessment:
        return ""
    return f"{assessment}{f' — {reason}' if reason else ''}"


def _digest_sections(digest: dict | None) -> list[tuple[str, str]]:
    """``(label, text)`` for each non-empty digest section, in reading order.
    Long range swaps specific concerns for model agreement."""
    if not digest:
        return []
    keys = _LONG_RANGE_SECTIONS if digest.get("outlook") else _SHORT_RANGE_SECTIONS
    return [(label, digest[key]) for key, label in keys if digest.get(key)]


def _digest_sections_html(digest: dict | None) -> str:
    return "".join(
        f'<p style="margin:6px 0;"><strong>{html.escape(label)}:</strong> {html.escape(text)}</p>'
        for label, text in _digest_sections(digest)
    )


def _digest_sections_plain(digest: dict | None) -> list[str]:
    lines: list[str] = []
    for label, text in _digest_sections(digest):
        lines.extend([f"{label}: {text}", ""])
    return lines


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

    assessment_html = _assessment_banner_html(pack, digest)

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

    digest_html = _digest_sections_html(digest)

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

    banner = _assessment_banner_plain(pack, digest)
    if banner:
        lines.extend([banner, ""])

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

    lines.extend(_digest_sections_plain(digest))

    return "\n".join(lines)


def send_trip_email(
    recipients: list[str],
    *,
    trip_name: str,
    subject: str,
    lines: list[str],
    trip_url: str = "",
    smtp_config: SmtpConfig | None = None,
) -> None:
    """One coalesced email for a completed trip refresh.

    Deliberately plainer than ``send_briefing_email``: a trip refresh has no
    single pack to render, and the useful content is the per-leg line-up plus a
    link. ``lines`` is the already-composed per-leg summary from the driver, so
    the wording of "which leg decides this trip" lives in exactly one place.
    """
    if not recipients:
        raise ValueError("No email recipients specified")
    if smtp_config is None:
        smtp_config = SmtpConfig.from_env()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(recipients)

    plain = "\n".join([trip_name, "", *[f"- {line}" for line in lines]])
    if trip_url:
        plain += f"\n\nOpen the trip: {trip_url}"

    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    link = (
        f'<p><a href="{html.escape(trip_url)}">Open the trip</a></p>' if trip_url else ""
    )
    body_html = (
        f"<h2>{html.escape(trip_name)}</h2><ul>{items}</ul>{link}"
    )

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    logger.info("Sending trip email to %s", mask_email(recipients))
    send_message(msg, smtp_config)


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

    digest, advisories_data = load_pack_display_data(pack_dir)

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
    logger.info("Sending briefing email to %s", mask_email(recipients))
    send_message(msg, smtp_config)
    logger.info("Briefing email sent successfully")


# ---------------------------------------------------------------------------
# Flight-day brief (#753)
# ---------------------------------------------------------------------------
#
# The T-2h preflight auto-refresh sends this instead of the ordinary briefing
# email: observed conditions now first, then what changed since the last
# briefing, then the forecast assessment. The observed part is rendered from
# the live layer's agent summary (``tasks/live_layer.py::summarize_live``)
# word for word — the glance lines, change messages and highlight are the
# same text the apps and the MCP show, never re-derived here.

#: Where the web briefing keeps the Observed nutshell. A fragment, not a
#: query parameter: the web page has no "open on tab" parameter, and a
#: fragment that misses is harmless.
_OBSERVED_ANCHOR = "observed-glance-wrapper"

_GLANCE_PHASE_LABELS = {"departure": "Departure", "enroute": "En route", "arrival": "Arrival"}

#: Section 4 wording. Live alerts are push-only by design (decision 5 on #753):
#: a user with no registered device is told where they would get them.
FLIGHT_DAY_PUSH_NOTE = "Live alerts will be pushed to your iPhone/iPad until arrival +1h."
FLIGHT_DAY_APP_NOTE = (
    "Live alerts on flight day are sent as notifications in the FlyFun Weather app."
)


class AdvisoryStatusChange(BaseModel):
    """One advisory whose aggregate status moved between two packs."""

    name: str
    from_status: str | None = None  # None: the advisory is new in this pack
    to_status: str


class FlightDaySince(BaseModel):
    """The "Since the last briefing" section, computed by the dispatcher."""

    #: True when this preflight run built a new pack; False when the email
    #: describes the latest existing pack (no model update since it).
    refreshed: bool
    briefing_at: datetime
    prior_briefing_at: datetime | None = None
    prior_assessment: str | None = None
    assessment: str | None = None
    advisory_changes: list[AdvisoryStatusChange] = []


def _utc(value: datetime) -> datetime:
    """Aware UTC; a naive value (SQLite drops the zone) is read as UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _hhmmz(value) -> str:
    """"09:05Z" from a datetime or an ISO string; "" when unparseable."""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.strftime("%H:%MZ")


def flight_day_grade(pack: BriefingPackMeta) -> str | None:
    """The traffic light to lead with, or None. UNAVAILABLE is not a grade to
    headline (#392): the forecast section still shows it, the subject and push
    don't."""
    a = (pack.assessment or "").upper()
    return a if a and a != "UNAVAILABLE" else None


def _alert_rows(live: dict | None) -> list[dict]:
    return [c for c in (live or {}).get("changes") or [] if c.get("tier") == "alert"]


def flight_day_headline(live: dict | None) -> str | None:
    """The observed headline: the top alert-tier change (``summarize_live``
    already orders alert → worse → destination → departure → newest), or None
    when nothing at the departure, destination or an alternate moved."""
    rows = _alert_rows(live)
    return rows[0].get("message") if rows else None


def flight_day_subject(flight: Flight, pack: BriefingPackMeta, live: dict | None) -> str:
    """"Today 09:00Z EGTK → LFAT · AMBER · LFAT METAR: VFR → IFR"."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    parts = [f"Today {_hhmmz(flight.departure_time)} {route}"]
    grade = flight_day_grade(pack)
    if grade:
        parts.append(grade)
    headline = flight_day_headline(live)
    if headline:
        parts.append(headline)
    return " · ".join(parts)


def _since_lines(since: FlightDaySince) -> list[str]:
    """Plain sentences for section 2, shared by both bodies."""
    if not since.refreshed:
        return [f"No model update since the {_hhmmz(since.briefing_at)} briefing."]
    if since.prior_briefing_at is None:
        return [f"First briefing for this flight, built {_hhmmz(since.briefing_at)}."]
    previous = _hhmmz(since.prior_briefing_at)
    prior_day, this_day = _utc(since.prior_briefing_at), _utc(since.briefing_at)
    if prior_day.date() != this_day.date():
        previous = f"{prior_day.strftime('%d %b')} {previous}"
    lines = [f"New briefing built {_hhmmz(since.briefing_at)} (previous {previous})."]
    before, after = since.prior_assessment, since.assessment
    if before and after and before.upper() != after.upper():
        lines.append(f"Grade: {before} → {after}")
    elif after:
        lines.append(f"Grade unchanged: {after}")
    if since.advisory_changes:
        for ch in since.advisory_changes:
            was = ch.from_status.upper() if ch.from_status else "new"
            lines.append(f"{ch.name}: {was} → {ch.to_status.upper()}")
    else:
        lines.append("No advisory changed status.")
    return lines


def _observed_parts(live: dict | None) -> dict:
    """Everything section 1 shows, pulled from the live summary as is."""
    if not live:
        return {}
    glance = live.get("glance") or {}
    airports = sorted(
        (
            a for a in live.get("airports") or []
            if a.get("role") in ("departure", "destination") and a.get("metar_raw")
        ),
        key=lambda a: a.get("role") != "departure",  # departure first, as flown
    )
    return {
        "headline": glance.get("headline"),
        "highlight": live.get("highlight"),
        "alerts": [c.get("message") for c in _alert_rows(live) if c.get("message")],
        "lines": [
            (_GLANCE_PHASE_LABELS.get(ln.get("phase"), ln.get("phase") or ""), ln.get("text") or "")
            for ln in glance.get("lines") or []
        ],
        "metars": airports,
        "sigmets": live.get("sigmets") or [],
    }


def _sigmet_text(s: dict) -> str:
    valid = ""
    if s.get("valid_from") or s.get("valid_to"):
        valid = f", valid {_hhmmz(s.get('valid_from'))}–{_hhmmz(s.get('valid_to'))}"
    return f"{s.get('label') or s.get('fir_id') or 'SIGMET'}{valid}"


def _metar_text(a: dict) -> str:
    cat = f" ({a['flight_category']})" if a.get("flight_category") else ""
    when = f" {_hhmmz(a.get('metar_time'))}" if a.get("metar_time") else ""
    return f"{a.get('icao', '')}{when}{cat}: {a.get('metar_raw', '')}"


def _flight_day_link(base_url: str, flight_id: str) -> str:
    return f"{_briefing_url(base_url, flight_id)}#{_OBSERVED_ANCHOR}" if base_url else ""


def build_flight_day_plain(
    flight: Flight,
    pack: BriefingPackMeta,
    digest: dict | None,
    advisories_data: dict | None,
    *,
    live: dict | None,
    since: FlightDaySince,
    has_device: bool,
    briefing_link: str,
) -> str:
    """Plain-text flight-day body, in the same order as the HTML."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    lines = [
        f"Flight day — {route} — departs {_hhmmz(flight.departure_time)}",
        "",
    ]
    if briefing_link:
        lines.extend([f"Open the briefing: {briefing_link}", ""])

    lines.append("OBSERVED NOW")
    obs = _observed_parts(live)
    if not obs:
        lines.append("No live observations for this flight yet.")
    else:
        if obs["headline"]:
            lines.append(obs["headline"])
        hl = obs["highlight"]
        if hl and hl.get("text"):
            lines.append(f"{hl['text']} (Experimental · written {_hhmmz(hl.get('written_at'))})")
        for msg in obs["alerts"]:
            lines.append(f"  ! {msg}")
        for label, text in obs["lines"]:
            lines.append(f"  {label}: {text}")
        for a in obs["metars"]:
            lines.append(f"  METAR {_metar_text(a)}")
        for s in obs["sigmets"]:
            lines.append(f"  SIGMET {_sigmet_text(s)}")
    lines.append("")

    lines.append("SINCE THE LAST BRIEFING")
    lines.extend(_since_lines(since))
    lines.append("")

    lines.append(
        f"FORECAST ASSESSMENT (written {_hhmmz(pack.fetch_timestamp)}, D-{pack.days_out})"
    )
    banner = _assessment_banner_plain(pack, digest)
    if banner:
        lines.append(banner)
    if advisories_data:
        catalog = {e["id"]: e for e in advisories_data.get("catalog", [])}
        flagged = [
            r for r in advisories_data.get("advisories", [])
            if r.get("aggregate_status") in ("amber", "red")
        ]
        if flagged:
            for adv in flagged:
                name = catalog.get(adv["advisory_id"], {}).get("name", adv["advisory_id"])
                lines.append(
                    f"  {adv['aggregate_status'].upper()} — {name}: {adv.get('aggregate_detail', '')}"
                )
        else:
            lines.append("All advisories GREEN")
    lines.append("")
    lines.extend(_digest_sections_plain(digest))

    lines.append("DURING THE FLIGHT")
    lines.append(FLIGHT_DAY_PUSH_NOTE if has_device else FLIGHT_DAY_APP_NOTE)
    return "\n".join(lines)


def _h3(text: str) -> str:
    return (
        f'<h3 style="margin:16px 0 6px;font-size:15px;border-bottom:1px solid #eee;'
        f'padding-bottom:3px;">{html.escape(text)}</h3>'
    )


def build_flight_day_html(
    flight: Flight,
    pack: BriefingPackMeta,
    digest: dict | None,
    advisories_data: dict | None,
    *,
    live: dict | None,
    since: FlightDaySince,
    has_device: bool,
    briefing_link: str,
) -> str:
    """HTML flight-day body: observed now → since the last briefing →
    forecast assessment → during the flight."""
    route = " → ".join(flight.waypoints) if flight.waypoints else flight.route_name
    alt_ft = flight.cruise_altitude_ft
    alt_str = f"FL{alt_ft // 100:03d}" if alt_ft >= 10000 else f"{alt_ft} ft"
    esc = html.escape

    link_html = ""
    if briefing_link:
        link_html = (
            f'<div style="margin-bottom:14px;">'
            f'<a href="{esc(briefing_link)}" '
            f'style="display:inline-block;padding:8px 16px;background:#2563eb;color:#fff;'
            f'text-decoration:none;border-radius:4px;font-weight:600;font-size:14px;">'
            f'Open Observed &rarr;</a></div>'
        )

    # 1. Observed now
    obs = _observed_parts(live)
    if not obs:
        observed_html = '<p style="color:#555;">No live observations for this flight yet.</p>'
    else:
        parts = []
        if obs["headline"]:
            parts.append(f'<p style="margin:4px 0;font-weight:600;">{esc(obs["headline"])}</p>')
        hl = obs["highlight"]
        if hl and hl.get("text"):
            parts.append(
                f'<div style="background:#f3f4f6;border-left:3px solid #6366f1;padding:6px 10px;margin:6px 0;">'
                f'{esc(hl["text"])}'
                f'<div style="color:#888;font-size:11px;margin-top:2px;">'
                f'Experimental &middot; written {esc(_hhmmz(hl.get("written_at")))}</div></div>'
            )
        if obs["alerts"]:
            items = "".join(
                f'<li style="color:#842029;margin:2px 0;">{esc(m)}</li>' for m in obs["alerts"]
            )
            parts.append(f'<ul style="margin:6px 0;padding-left:18px;">{items}</ul>')
        if obs["lines"]:
            rows = "".join(
                f'<tr><td style="padding:2px 8px 2px 0;font-weight:600;vertical-align:top;'
                f'white-space:nowrap;">{esc(label)}</td>'
                f'<td style="padding:2px 0;">{esc(text)}</td></tr>'
                for label, text in obs["lines"]
            )
            parts.append(f'<table style="border-collapse:collapse;margin:6px 0;">{rows}</table>')
        for a in obs["metars"]:
            parts.append(
                f'<div style="font-family:monospace;font-size:12px;color:#333;margin:2px 0;">'
                f'METAR {esc(_metar_text(a))}</div>'
            )
        if obs["sigmets"]:
            items = "".join(f"<li>{esc(_sigmet_text(s))}</li>" for s in obs["sigmets"])
            parts.append(
                f'<div style="margin:6px 0;"><strong style="font-size:13px;">Route SIGMETs</strong>'
                f'<ul style="margin:2px 0;padding-left:18px;">{items}</ul></div>'
            )
        observed_html = "".join(parts)

    # 2. Since the last briefing
    since_html = "".join(
        f'<p style="margin:3px 0;">{esc(line)}</p>' for line in _since_lines(since)
    )

    # 3. Forecast assessment — the ordinary email's banner, cards, amber/red
    # advisories and digest sections, labelled with when it was written.
    forecast_parts = [
        f'<p style="color:#555;margin:0 0 8px;font-size:12px;">'
        f'Forecast written {esc(_hhmmz(pack.fetch_timestamp))} (D-{pack.days_out})</p>',
        _assessment_banner_html(pack, digest),
    ]
    if advisories_data:
        forecast_parts.append(_render_airport_conditions_html(advisories_data))
        table = _render_advisories_html(advisories_data, only={"amber", "red"})
        more = (
            f' &middot; <a href="{esc(_briefing_url_from(briefing_link))}" style="color:#2563eb;">'
            f'all advisories</a>' if briefing_link else ""
        )
        if table:
            forecast_parts.append(
                f'<div style="margin:10px 0;"><strong style="font-size:13px;">Advisories</strong>'
                f'<span style="font-size:12px;">{more}</span>{table}</div>'
            )
        else:
            forecast_parts.append(
                f'<p style="margin:6px 0;">All advisories GREEN<span style="font-size:12px;">{more}</span></p>'
            )
    forecast_parts.append(_digest_sections_html(digest))
    forecast_html = "".join(forecast_parts)

    # 4. During the flight
    during_html = (
        f'<p style="margin:4px 0;">{esc(FLIGHT_DAY_PUSH_NOTE if has_device else FLIGHT_DAY_APP_NOTE)}</p>'
    )

    footer = (
        f'<p style="color:#888;font-size:12px;"><a href="{esc(briefing_link)}" style="color:#888;">'
        f'Open the briefing on FlyFun Weather</a></p>' if briefing_link else ""
    )

    return f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;max-width:600px;">
  <h2 style="margin:0 0 4px;">Flight day &middot; {esc(route)}</h2>
  <p style="color:#555;margin:0 0 12px;">
    {esc(flight.target_date)} &mdash; departs {esc(_hhmmz(flight.departure_time))} &mdash; {esc(alt_str)}
  </p>
  {link_html}
  {_h3("Observed now")}
  {observed_html}
  {_h3("Since the last briefing")}
  {since_html}
  {_h3("Forecast assessment")}
  {forecast_html}
  {_h3("During the flight")}
  {during_html}
  <hr style="border:none;border-top:1px solid #eee;margin:10px 0;">
  {footer}
</body>
</html>"""


def _briefing_url_from(link: str) -> str:
    """The briefing page without the Observed fragment (the advisories live
    on the main view)."""
    return link.split("#", 1)[0]


def load_pack_display_data(pack_dir: Path) -> tuple[dict | None, dict | None]:
    """``(digest, route_advisories)`` from a pack directory, None when absent."""
    digest: dict | None = None
    digest_path = pack_dir / "digest.json"
    if digest_path.exists():
        digest = json.loads(digest_path.read_text())
    advisories_data: dict | None = None
    adv_path = pack_dir / "route_advisories.json"
    if adv_path.exists():
        advisories_data = json.loads(adv_path.read_text())
    return digest, advisories_data


def send_flight_day_email(
    recipients: list[str],
    flight: Flight,
    pack: BriefingPackMeta,
    pack_dir: Path,
    *,
    live: dict | None,
    since: FlightDaySince,
    has_device: bool,
    base_url: str = "",
    smtp_config: SmtpConfig | None = None,
) -> None:
    """Send the flight-day brief (#753). Same transport and error contract as
    :func:`send_briefing_email`."""
    if not recipients:
        raise ValueError("No email recipients specified")
    if smtp_config is None:
        smtp_config = SmtpConfig.from_env()

    digest, advisories_data = load_pack_display_data(pack_dir)
    link = _flight_day_link(base_url, flight.id)
    kwargs = dict(live=live, since=since, has_device=has_device, briefing_link=link)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = flight_day_subject(flight, pack, live)
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(
        build_flight_day_plain(flight, pack, digest, advisories_data, **kwargs), "plain",
    ))
    msg.attach(MIMEText(
        build_flight_day_html(flight, pack, digest, advisories_data, **kwargs), "html",
    ))

    logger.info("Sending flight-day email to %s", mask_email(recipients))
    send_message(msg, smtp_config)
