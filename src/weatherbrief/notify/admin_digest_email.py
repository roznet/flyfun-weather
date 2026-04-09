"""Unified admin daily digest email — HTML + plain text.

Replaces the verification-only digest with a broader status email
covering users, flights, performance, and verification.
"""

from __future__ import annotations

import html
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from weatherbrief.models.verification import AdminDigestData
from weatherbrief.notify.email import SmtpConfig, send_message

logger = logging.getLogger(__name__)

# Accuracy color thresholds
_GREEN = 80.0
_AMBER = 60.0

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif"

_STYLE_BASE = (
    f"font-family:{_FONT}; font-size:14px; color:#1a1a1a; "
    "max-width:640px; margin:0 auto; padding:16px;"
)

_STYLE_TABLE = (
    "border-collapse:collapse; width:100%; font-size:13px; "
    "font-variant-numeric:tabular-nums;"
)

_STYLE_TH = (
    "border:1px solid #ddd; padding:6px 8px; background:#f5f5f5; "
    "text-align:left; font-weight:600;"
)

_STYLE_TD = "border:1px solid #ddd; padding:6px 8px; text-align:left;"

_STYLE_METRIC = (
    "display:inline-block; text-align:center; padding:8px 16px; "
    "background:#f8f9fa; border-radius:6px; margin:4px;"
)

_STYLE_SECTION = "margin:20px 0 8px; font-size:15px;"


def _accuracy_color(pct: float | None) -> str:
    if pct is None:
        return ""
    if pct >= _GREEN:
        return "background:#d1e7dd; color:#0f5132;"
    if pct >= _AMBER:
        return "background:#fff3cd; color:#664d03;"
    return "background:#f8d7da; color:#842029;"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "\u2014"
    return f"{pct:.0f}%"


def _fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.0f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.0f} MB"
    return f"{b / (1024 * 1024 * 1024):.1f} GB"


def _fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K"
    return f"{n / 1_000_000:.2f}M"


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------


def _metric_pill(label: str, value: str) -> str:
    return (
        f'<div style="{_STYLE_METRIC}">'
        f'<div style="font-size:20px; font-weight:700;">{value}</div>'
        f'<div style="font-size:11px; color:#666;">{label}</div>'
        f"</div>"
    )


def _build_html(data: AdminDigestData) -> str:
    p: list[str] = []
    label = data.period_label or "Daily"

    p.append(f'<div style="{_STYLE_BASE}">')
    p.append(
        f'<h2 style="margin:0 0 16px; font-size:18px;">'
        f"FlyFun Admin Digest &mdash; {html.escape(label)}</h2>"
    )

    # --- Users ---
    u = data.users
    p.append(f'<h3 style="{_STYLE_SECTION}">Users</h3>')
    p.append("<div>")
    p.append(_metric_pill("New", str(u.new_user_count)))
    p.append(_metric_pill("Active", str(u.active_user_count)))
    p.append(_metric_pill("Total", str(u.total_user_count)))
    p.append("</div>")

    if u.new_users:
        p.append('<div style="margin:8px 0; font-size:12px; color:#555;">')
        names = [
            f"{html.escape(nu.display_name or nu.email)}"
            for nu in u.new_users
        ]
        p.append("New: " + ", ".join(names))
        p.append("</div>")

    # --- Flights & Briefings ---
    fb = data.flights_briefings
    p.append(f'<h3 style="{_STYLE_SECTION}">Flights &amp; Briefings</h3>')
    p.append("<div>")
    p.append(_metric_pill("New flights", str(fb.new_flights)))
    p.append(_metric_pill("Briefings", str(fb.total_briefings)))
    p.append(_metric_pill("Manual", str(fb.manual_briefings)))
    p.append(_metric_pill("Auto", str(fb.auto_briefings)))
    p.append("</div>")

    p.append('<div style="margin:8px 0; font-size:12px; color:#555;">')
    p.append(
        f"Single-briefing flights: {fb.flights_single_briefing} &middot; "
        f"Refreshed flights: {fb.flights_refreshed} &middot; "
        f"GRAMET: {fb.gramet_count} &middot; "
        f"LLM digest: {fb.digest_count}"
    )
    p.append("</div>")

    # --- Performance & Resources ---
    perf = data.performance
    p.append(f'<h3 style="{_STYLE_SECTION}">Performance &amp; Resources</h3>')
    p.append(f'<table style="{_STYLE_TABLE}">')
    rows = [
        ("Total cost", f"${perf.total_cost_usd:.4f}"),
        ("LLM tokens", f"{_fmt_tokens(perf.total_llm_input_tokens)} in / "
                       f"{_fmt_tokens(perf.total_llm_output_tokens)} out"),
        ("Disk usage", _fmt_bytes(perf.total_disk_bytes)),
    ]
    if perf.avg_elapsed_seconds is not None:
        rows.append(("Avg briefing time", f"{perf.avg_elapsed_seconds:.1f}s"))
    if perf.avg_queue_wait_seconds is not None:
        rows.append(("Avg queue wait", f"{perf.avg_queue_wait_seconds:.1f}s"))
    if perf.briefing_count_for_perf > 0:
        rows.append(("Sample size", str(perf.briefing_count_for_perf)))

    for label_text, value in rows:
        p.append(
            f"<tr>"
            f'<td style="{_STYLE_TD} font-weight:600; width:40%;">{label_text}</td>'
            f'<td style="{_STYLE_TD}">{value}</td>'
            f"</tr>"
        )
    p.append("</table>")

    # --- Verification ---
    v = data.verification
    p.append(f'<h3 style="{_STYLE_SECTION}">Verification</h3>')

    if v.category_accuracy:
        p.append(_render_accuracy_table(v))
    else:
        p.append('<p style="color:#666; font-style:italic;">No verification data.</p>')

    # Wind advisory summary line
    if v.wind_advisory:
        wind_parts = []
        for w in v.wind_advisory:
            pct = _fmt_pct(w.accuracy_pct)
            wind_parts.append(f"{html.escape(w.model.upper())}: {pct} (n={w.sample_count})")
        p.append(
            f'<p style="font-size:12px; color:#555; margin:8px 0;">'
            f"Wind advisory: {' &middot; '.join(wind_parts)}</p>"
        )

    if v.notable_miss_count > 0:
        p.append(
            f'<p style="font-size:12px; color:#555; margin:4px 0;">'
            f"Notable misses: {v.notable_miss_count}</p>"
        )

    if v.dashboard_url:
        p.append(
            f'<p style="margin:8px 0;">'
            f'<a href="{html.escape(v.dashboard_url)}" '
            f'style="color:#2563eb; text-decoration:none;">'
            f"View full verification dashboard &rarr;</a></p>"
        )

    # Footer
    p.append(
        '<p style="font-size:11px; color:#999; margin-top:24px;">'
        "Generated by FlyFun Weather</p>"
    )
    p.append("</div>")
    return "".join(p)


def _render_accuracy_table(v) -> str:
    """Render model x D-0..D-3 accuracy table."""
    today_map: dict[tuple[str, int], float | None] = {}
    today_n: dict[tuple[str, int], int] = {}
    for r in v.category_accuracy:
        today_map[(r.model, r.days_out)] = r.accuracy_pct
        today_n[(r.model, r.days_out)] = r.sample_count

    models_set: set[str] = {r.model for r in v.category_accuracy}
    models = sorted(m for m in models_set if m != "TAF")
    if "TAF" in models_set:
        models.append("TAF")

    if not models:
        return ""

    days_out_cols = [0, 1, 2, 3]
    p: list[str] = []
    p.append(f'<table style="{_STYLE_TABLE}">')
    p.append("<tr>")
    p.append(f'<th style="{_STYLE_TH}">Model</th>')
    for d in days_out_cols:
        p.append(f'<th style="{_STYLE_TH} text-align:center;">D-{d}</th>')
    p.append("</tr>")

    for model in models:
        p.append("<tr>")
        p.append(
            f'<td style="{_STYLE_TD} font-weight:600;">'
            f"{html.escape(model.upper())}</td>"
        )
        for d in days_out_cols:
            pct = today_map.get((model, d))
            n = today_n.get((model, d), 0)
            color = _accuracy_color(pct)
            cell = _fmt_pct(pct)
            if n > 0:
                cell += f'<span style="font-size:10px; color:#999;"> ({n})</span>'
            p.append(
                f'<td style="{_STYLE_TD} text-align:center; {color}">{cell}</td>'
            )
        p.append("</tr>")

    p.append("</table>")
    return "".join(p)


# ---------------------------------------------------------------------------
# Plain text builder
# ---------------------------------------------------------------------------


def _build_plain(data: AdminDigestData) -> str:
    lines: list[str] = []
    label = data.period_label or "Daily"
    lines.append(f"FlyFun Admin Digest -- {label}")
    lines.append("=" * 50)
    lines.append("")

    # Users
    u = data.users
    lines.append("USERS")
    lines.append(f"  New:    {u.new_user_count}")
    lines.append(f"  Active: {u.active_user_count}")
    lines.append(f"  Total:  {u.total_user_count}")
    if u.new_users:
        names = ", ".join(nu.display_name or nu.email for nu in u.new_users)
        lines.append(f"  New users: {names}")
    lines.append("")

    # Flights & Briefings
    fb = data.flights_briefings
    lines.append("FLIGHTS & BRIEFINGS")
    lines.append(f"  New flights:       {fb.new_flights}")
    lines.append(f"  Total briefings:   {fb.total_briefings}  (manual: {fb.manual_briefings}, auto: {fb.auto_briefings})")
    lines.append(f"  Single-briefing:   {fb.flights_single_briefing}")
    lines.append(f"  Refreshed flights: {fb.flights_refreshed}")
    lines.append(f"  GRAMET: {fb.gramet_count}  LLM digest: {fb.digest_count}")
    lines.append("")

    # Performance
    perf = data.performance
    lines.append("PERFORMANCE & RESOURCES")
    lines.append(f"  Total cost:      ${perf.total_cost_usd:.4f}")
    lines.append(f"  LLM tokens:      {_fmt_tokens(perf.total_llm_input_tokens)} in / {_fmt_tokens(perf.total_llm_output_tokens)} out")
    lines.append(f"  Disk usage:      {_fmt_bytes(perf.total_disk_bytes)}")
    if perf.avg_elapsed_seconds is not None:
        lines.append(f"  Avg briefing:    {perf.avg_elapsed_seconds:.1f}s")
    if perf.avg_queue_wait_seconds is not None:
        lines.append(f"  Avg queue wait:  {perf.avg_queue_wait_seconds:.1f}s")
    if perf.briefing_count_for_perf > 0:
        lines.append(f"  Sample size:     {perf.briefing_count_for_perf}")
    lines.append("")

    # Verification
    v = data.verification
    lines.append("VERIFICATION")
    if v.category_accuracy:
        models_set: set[str] = {r.model for r in v.category_accuracy}
        models = sorted(m for m in models_set if m != "TAF")
        if "TAF" in models_set:
            models.append("TAF")

        header = f"{'Model':<10}" + "".join(f"{'D-' + str(d):>8}" for d in (0, 1, 2, 3))
        lines.append(f"  {header}")
        lines.append(f"  {'-' * len(header)}")

        today_map = {(r.model, r.days_out): r for r in v.category_accuracy}
        for model in models:
            row = f"{model.upper():<10}"
            for d in (0, 1, 2, 3):
                r = today_map.get((model, d))
                if r and r.accuracy_pct is not None:
                    row += f"{r.accuracy_pct:>7.0f}%"
                else:
                    row += f"{'--':>8}"
            lines.append(f"  {row}")
    else:
        lines.append("  No verification data.")

    if v.notable_miss_count > 0:
        lines.append(f"  Notable misses: {v.notable_miss_count}")

    if v.wind_advisory:
        wind_parts = [
            f"{w.model.upper()}: {_fmt_pct(w.accuracy_pct)} (n={w.sample_count})"
            for w in v.wind_advisory
        ]
        lines.append(f"  Wind advisory: {', '.join(wind_parts)}")

    if v.dashboard_url:
        lines.append(f"  Dashboard: {v.dashboard_url}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def send_admin_digest(
    recipients: list[str],
    data: AdminDigestData,
    *,
    smtp_config: SmtpConfig | None = None,
) -> None:
    """Build and send the unified admin digest email."""
    if not recipients:
        logger.debug("No recipients for admin digest, skipping")
        return

    label = data.period_label or "Daily"
    subject = f"[FlyFun] Admin Digest \u2014 {label}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = (
        smtp_config.from_address
        if smtp_config
        else "FlyFun Weather <noreply@flyfun.aero>"
    )
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(_build_plain(data), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(data), "html", "utf-8"))

    send_message(msg, smtp_config)
    logger.info("Admin digest sent to %d recipient(s)", len(recipients))
