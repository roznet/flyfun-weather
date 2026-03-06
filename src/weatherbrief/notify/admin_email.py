"""Admin notification emails and user lifecycle emails.

Includes: new-user signup alerts (to admin), one-click approval,
and welcome emails (to user on approval).
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlencode

from weatherbrief.notify.email import SmtpConfig

logger = logging.getLogger(__name__)

APPROVE_LINK_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days


def get_admin_emails() -> list[str]:
    """Parse ADMIN_EMAILS env var (comma-separated). Returns empty list if unset."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


def generate_approve_url(user_id: str, base_url: str, secret: str) -> str:
    """Build an HMAC-signed one-click approval URL.

    The URL is valid for 7 days. The signature covers
    ``approve:{user_id}:{timestamp}`` using HMAC-SHA256.
    """
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode(), f"approve:{user_id}:{ts}".encode(), hashlib.sha256
    ).hexdigest()
    params = urlencode({"ts": ts, "sig": sig})
    return f"{base_url}/api/admin/approve/{user_id}?{params}"


def send_new_user_notification(
    email: str,
    name: str,
    user_id: str,
    base_url: str,
) -> None:
    """Notify all admin emails about a new user signup (auto-approved).

    In dev mode (no ADMIN_EMAILS set), logs instead.
    """
    from flyfun_common.auth import is_dev_mode

    admin_emails = get_admin_emails()

    if is_dev_mode() or not admin_emails:
        logger.info("New user signup (auto-approved): %s (%s)", email, user_id)
        return

    try:
        smtp_config = SmtpConfig.from_env()
    except ValueError:
        logger.warning("SMTP not configured — cannot send admin notification for %s", email)
        return

    admin_page_url = f"{base_url}/admin.html"

    subject = f"[WeatherBrief] New user joined: {email}"
    html_body = f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;">
  <h2 style="margin:0 0 12px;">New User Joined</h2>
  <p>A new user has signed up and been automatically approved.</p>
  <table style="border-collapse:collapse;margin-bottom:16px;">
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">Name</td><td>{html.escape(name)}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">Email</td><td>{html.escape(email)}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">User ID</td><td style="font-family:monospace;font-size:12px;">{html.escape(user_id)}</td></tr>
  </table>
  <p style="color:#6c757d;font-size:12px;">
    Manage users on the <a href="{html.escape(admin_page_url)}">admin page</a>.
  </p>
</body>
</html>"""

    plain_body = (
        f"New User Joined\n\n"
        f"A new user has signed up and been automatically approved.\n\n"
        f"Name: {name}\nEmail: {email}\nUser ID: {user_id}\n\n"
        f"Admin page: {admin_page_url}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(admin_emails)
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending admin notification for new user %s to %s", email, admin_emails)
    _smtp_send(smtp_config, msg)
    logger.info("Admin notification sent for %s", email)


def send_welcome_email(
    email: str,
    name: str,
    base_url: str,
) -> None:
    """Send a welcome email to a newly approved user.

    In dev mode or if SMTP is not configured, logs instead of sending.
    """
    from flyfun_common.auth import is_dev_mode

    if is_dev_mode():
        logger.info("Welcome email (dev mode, not sent): %s", email)
        return

    try:
        smtp_config = SmtpConfig.from_env()
    except ValueError:
        logger.warning("SMTP not configured — cannot send welcome email to %s", email)
        return

    esc_name = html.escape(name or "there")
    site_url = html.escape(base_url)
    help_url = html.escape(f"{base_url}/help.html")

    subject = "[WeatherBrief] Your account has been approved"
    html_body = f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;max-width:560px;">
  <h2 style="margin:0 0 12px;">Welcome to WeatherBrief, {esc_name}!</h2>
  <p>Your account has been approved. You can now sign in and start using WeatherBrief
     for your flight weather planning.</p>
  <div style="margin:20px 0;">
    <a href="{site_url}"
       style="display:inline-block;padding:10px 24px;background:#2563eb;color:#fff;
              border-radius:6px;text-decoration:none;font-weight:600;">
      Go to WeatherBrief
    </a>
  </div>
  <p>Before your first flight, we recommend reviewing the
     <a href="{help_url}" style="color:#2563eb;font-weight:500;">Help &amp; Guide</a>
     to get the most out of the briefings.</p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0;">
  <p style="color:#6c757d;font-size:13px;">
    <strong>Early preview</strong> &mdash; WeatherBrief is still in active development.
    Please review your briefings carefully and don't hesitate to share feedback
    or report any issues you encounter.
  </p>
</body>
</html>"""

    plain_body = (
        f"Welcome to WeatherBrief, {name or 'there'}!\n\n"
        f"Your account has been approved. You can now sign in and start\n"
        f"using WeatherBrief for your flight weather planning.\n\n"
        f"Sign in: {base_url}\n\n"
        f"We recommend reviewing the Help & Guide before your first flight:\n"
        f"{base_url}/help.html\n\n"
        f"---\n"
        f"Early preview — WeatherBrief is still in active development.\n"
        f"Please review your briefings carefully and don't hesitate to\n"
        f"share feedback or report any issues you encounter.\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_address
    msg["To"] = email
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending welcome email to %s", email)
    _smtp_send(smtp_config, msg)
    logger.info("Welcome email sent to %s", email)


CATEGORY_LABELS = {
    "data_issue": "Briefing Data Issue",
    "too_conservative": "Briefing Too Conservative",
    "too_optimistic": "Briefing Too Optimistic",
    "incorrect_interpretation": "Briefing Incorrect Interpretation",
    "other": "Other Bug/Issue",
}


def send_feedback_notification(
    user_email: str,
    user_name: str,
    flight_id: str,
    pack_timestamp: str,
    category: str,
    comment: str,
    base_url: str,
) -> None:
    """Notify admins when a user submits feedback.

    In dev mode (no ADMIN_EMAILS set), logs instead.
    """
    from flyfun_common.auth import is_dev_mode

    admin_emails = get_admin_emails()

    if is_dev_mode() or not admin_emails:
        logger.info("Feedback from %s: [%s] %s", user_email, category, comment)
        return

    try:
        smtp_config = SmtpConfig.from_env()
    except ValueError:
        logger.warning("SMTP not configured — cannot send feedback notification")
        return

    category_label = CATEGORY_LABELS.get(category, category)

    if pack_timestamp:
        briefing_url = (
            f"{base_url}/briefing.html?flight={flight_id}&t={pack_timestamp}"
        )
    else:
        briefing_url = f"{base_url}/briefing.html?flight={flight_id}"

    subject = f"[WeatherBrief] Feedback: {category_label}"
    html_body = f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;">
  <h2 style="margin:0 0 12px;">User Feedback</h2>
  <table style="border-collapse:collapse;margin-bottom:16px;">
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">From</td><td>{html.escape(user_name)} ({html.escape(user_email)})</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">Category</td><td>{html.escape(category_label)}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">Flight</td><td style="font-family:monospace;font-size:12px;">{html.escape(flight_id)}</td></tr>
  </table>
  <div style="background:#f8f9fa;border:1px solid #dee2e6;border-radius:6px;padding:12px;margin-bottom:16px;">
    {html.escape(comment).replace(chr(10), '<br>')}
  </div>
  <p>
    <a href="{html.escape(briefing_url)}" style="color:#2563eb;font-weight:500;">View Briefing</a>
    &nbsp;&middot;&nbsp;
    <a href="{html.escape(base_url + '/admin.html')}" style="color:#2563eb;">Admin Page</a>
  </p>
</body>
</html>"""

    plain_body = (
        f"User Feedback\n\n"
        f"From: {user_name} ({user_email})\n"
        f"Category: {category_label}\n"
        f"Flight: {flight_id}\n\n"
        f"Comment:\n{comment}\n\n"
        f"Briefing: {briefing_url}\n"
        f"Admin page: {base_url}/admin.html\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(admin_emails)
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending feedback notification to %s", admin_emails)
    _smtp_send(smtp_config, msg)
    logger.info("Feedback notification sent")


def _smtp_send(smtp_config: SmtpConfig, msg: MIMEMultipart) -> None:
    """Send an email message via SMTP."""
    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        if smtp_config.use_tls:
            server.starttls()
        if smtp_config.user:
            server.login(smtp_config.user, smtp_config.password)
        server.send_message(msg)
