"""Admin notification emails: new-user signup alerts with one-click approval."""

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
    """Notify all admin emails about a new user signup.

    In dev mode (no ADMIN_EMAILS set), logs the approve URL instead.
    """
    from weatherbrief.api.auth_config import get_jwt_secret, is_dev_mode

    admin_emails = get_admin_emails()
    secret = get_jwt_secret()
    approve_url = generate_approve_url(user_id, base_url, secret)

    if is_dev_mode() or not admin_emails:
        logger.info(
            "New user signup: %s (%s). Approve URL: %s",
            email, user_id, approve_url,
        )
        return

    try:
        smtp_config = SmtpConfig.from_env()
    except ValueError:
        logger.warning("SMTP not configured — cannot send admin notification for %s", email)
        return

    admin_page_url = f"{base_url}/admin.html"

    subject = f"[WeatherBrief] New user signup: {email}"
    html_body = f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;">
  <h2 style="margin:0 0 12px;">New User Signup</h2>
  <table style="border-collapse:collapse;margin-bottom:16px;">
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">Name</td><td>{html.escape(name)}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">Email</td><td>{html.escape(email)}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#6c757d;">User ID</td><td style="font-family:monospace;font-size:12px;">{html.escape(user_id)}</td></tr>
  </table>
  <div style="margin-bottom:16px;">
    <a href="{html.escape(approve_url)}"
       style="display:inline-block;padding:10px 24px;background:#198754;color:#fff;
              border-radius:6px;text-decoration:none;font-weight:600;">
      Approve User
    </a>
  </div>
  <p style="color:#6c757d;font-size:12px;">
    Or manage users on the <a href="{html.escape(admin_page_url)}">admin page</a>.
    This link expires in 7 days.
  </p>
</body>
</html>"""

    plain_body = (
        f"New User Signup\n\n"
        f"Name: {name}\nEmail: {email}\nUser ID: {user_id}\n\n"
        f"Approve: {approve_url}\n\n"
        f"Admin page: {admin_page_url}\n"
        f"This link expires in 7 days."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_config.from_address
    msg["To"] = ", ".join(admin_emails)
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending admin notification for new user %s to %s", email, admin_emails)
    with smtplib.SMTP(smtp_config.host, smtp_config.port) as server:
        if smtp_config.use_tls:
            server.starttls()
        if smtp_config.user:
            server.login(smtp_config.user, smtp_config.password)
        server.send_message(msg)
    logger.info("Admin notification sent for %s", email)
