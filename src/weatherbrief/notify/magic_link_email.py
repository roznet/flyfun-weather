"""Magic-link sign-in email.

Builds a branded HTML + plain-text email containing both the click-through
link (web flow) and a 6-digit code (iOS flow). flyfun-common calls this
via the ``send_magic_link_email`` callback wired in ``api/app.py``.

In dev mode the link/code are logged instead of sent, so local
development doesn't depend on Resend/SMTP being configured.
"""

from __future__ import annotations

import html
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from weatherbrief.notify.email import SmtpConfig, send_message

logger = logging.getLogger(__name__)


def send_magic_link_email(
    email: str,
    link: str,
    code: str,
    requesting_ip: str | None,
) -> None:
    """Send a sign-in email with the magic link and 6-digit code.

    Signature matches flyfun-common's
    ``SendMagicLinkEmail = Callable[[str, str, str, str | None], None]``.
    """
    from flyfun_common.auth import is_dev_mode

    if is_dev_mode():
        logger.info(
            "Magic-link sign-in (dev mode, not sent) email=%s ip=%s code=%s link=%s",
            email, requesting_ip, code, link,
        )
        return

    # From-address resolution: prefer RESEND_FROM (works for Resend path
    # without SMTP env vars); fall back to SMTP config; finally a placeholder
    # so we never silently send from "" — the MIMEMultipart From header is
    # used by Resend when RESEND_FROM is not set.
    from_address = os.environ.get("RESEND_FROM")
    if not from_address:
        try:
            from_address = SmtpConfig.from_env().from_address
        except ValueError as exc:
            raise RuntimeError(
                f"Cannot send magic-link email to {email}: neither RESEND_FROM "
                "nor SMTP is configured"
            ) from exc

    esc_email = html.escape(email)
    esc_link = html.escape(link)
    esc_code = html.escape(code)
    esc_ip = html.escape(requesting_ip or "unknown")

    subject = "Your FlyFun Weather sign-in link"
    html_body = f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;max-width:560px;">
  <h2 style="margin:0 0 12px;">Sign in to FlyFun Weather</h2>
  <p>Click the button below to finish signing in as <strong>{esc_email}</strong>.</p>
  <div style="margin:24px 0;">
    <a href="{esc_link}"
       style="display:inline-block;padding:12px 28px;background:#2563eb;color:#fff;
              border-radius:6px;text-decoration:none;font-weight:600;font-size:15px;">
      Sign in to FlyFun Weather
    </a>
  </div>
  <p style="color:#555;font-size:13px;">
    This link expires in 15 minutes and can only be used once.
  </p>
  <p style="margin-top:24px;font-size:13px;color:#555;">
    If you're signing in from the FlyFun iOS app, enter this code instead:
  </p>
  <p style="font-family:'SF Mono',Menlo,monospace;font-size:24px;letter-spacing:4px;
            font-weight:600;color:#1a1a2e;background:#f4f4f7;padding:12px 16px;
            border-radius:6px;display:inline-block;margin:4px 0 16px;">
    {esc_code}
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
  <p style="color:#6c757d;font-size:12px;">
    Requested from IP <strong>{esc_ip}</strong>.<br>
    If you didn't request this email, just ignore it — no one can sign in
    without clicking the link or entering the code.
  </p>
</body>
</html>"""

    plain_body = (
        f"Sign in to FlyFun Weather as {email}\n\n"
        f"Click this link to finish signing in:\n"
        f"  {link}\n\n"
        f"This link expires in 15 minutes and can only be used once.\n\n"
        f"If you're signing in from the FlyFun iOS app, enter this code:\n"
        f"  {code}\n\n"
        f"---\n"
        f"Requested from IP {requesting_ip or 'unknown'}.\n"
        f"If you didn't request this email, just ignore it.\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = email
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending magic-link sign-in email to %s", email)
    try:
        send_message(msg)
    except Exception:
        # Re-raise so flyfun-common can log; it intentionally swallows the
        # error to avoid leaking account existence via response codes.
        logger.exception("Magic-link email send failed for %s", email)
        raise
