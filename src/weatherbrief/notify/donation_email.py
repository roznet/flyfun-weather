"""Opt-in donation confirmation email.

By default we do **not** email donors (a deliberate "respect your inbox"
choice surfaced on the donate page). The post-donation thank-you page offers a
single button to request a confirmation; that button hits
``POST /donations/email-receipt``, which calls this module to send a branded
thank-you confirming the donation's date and amount.

Unlike ``send_magic_link_email`` this is **not** gated on dev mode: it only
fires on a deliberate button press (no spam risk), and sending for real in dev
is exactly what lets us review the rendered email. It sends whenever an email
backend is configured (Resend or SMTP) and degrades to a log only when none is.
"""

from __future__ import annotations

import html
import logging
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from weatherbrief.notify.email import SmtpConfig, send_message
from weatherbrief.privacy import mask_email

logger = logging.getLogger(__name__)

# Symbols for the currencies the donate page offers; anything else falls back to
# the ISO code so the amount is never ambiguous.
_CURRENCY_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CHF": "CHF ",
    "CAD": "CA$",
    "AUD": "A$",
    "JPY": "¥",
}


def format_amount(amount: float, currency: str) -> str:
    """Render a charged amount as e.g. ``€25.00`` / ``¥2500`` / ``SEK 250.00``.

    Zero-decimal currencies (JPY and friends) show no minor digits; everything
    else shows two. Unknown codes prefix the ISO code instead of a symbol.
    """
    code = currency.upper()
    zero_decimal = code in {"JPY", "KRW", "CLP", "XOF", "XAF", "XPF", "VND"}
    digits = 0 if zero_decimal else 2
    sym = _CURRENCY_SYMBOLS.get(code)
    body = f"{amount:,.{digits}f}"
    return f"{sym}{body}" if sym else f"{code} {body}"


def send_donation_receipt_email(
    *,
    email: str,
    amount: float,
    currency: str,
    donated_at: datetime,
    base_url: str = "",
) -> None:
    """Send a thank-you confirming a completed donation (date + amount).

    Args:
        email: Recipient — the address Stripe collected at Checkout.
        amount: Charged amount in major units of ``currency``.
        currency: ISO 4217 code as charged.
        donated_at: When the donation completed (tz-aware preferred).
        base_url: Public site base for the "back to support" link.

    Raises:
        Exception: Propagated from the send so the caller can surface failure.
            (No email backend configured is *not* an error — it logs and
            returns, so a bare dev env without Resend/SMTP doesn't 502.)
    """
    amount_str = format_amount(amount, currency)
    date_str = donated_at.strftime("%-d %B %Y")

    # From-address resolution mirrors send_magic_link_email: prefer RESEND_FROM
    # (Resend path needs no SMTP env), fall back to SMTP config. With neither
    # configured there's no backend at all — log and return rather than fail the
    # request (the donation itself is already recorded by the webhook).
    from_address = os.environ.get("RESEND_FROM")
    if not from_address:
        try:
            from_address = SmtpConfig.from_env().from_address
        except ValueError:
            logger.warning(
                "Donation receipt not sent (no email backend configured) "
                "email=%s amount=%s date=%s",
                mask_email(email), amount_str, date_str,
            )
            return

    esc_amount = html.escape(amount_str)
    esc_date = html.escape(date_str)
    support_url = f"{base_url.rstrip('/')}/donate.html" if base_url else ""
    support_link = (
        f'<a href="{html.escape(support_url)}" style="color:#2563eb;">your support page</a>'
        if support_url
        else "your support page"
    )

    subject = "Your FlyFun Weather donation — thank you 💙"
    html_body = f"""\
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:14px;color:#1a1a2e;max-width:560px;">
  <h2 style="margin:0 0 12px;">Thank you for your donation 💙</h2>
  <p>
    Your donation was completed successfully. It directly offsets the real cost
    of running FlyFun Weather and helps keep it free for every pilot.
  </p>
  <table style="border-collapse:collapse;margin:18px 0;background:#f4f4f7;border-radius:6px;">
    <tr>
      <td style="padding:12px 18px;color:#555;font-size:13px;">Amount</td>
      <td style="padding:12px 18px;font-weight:600;font-size:16px;">{esc_amount}</td>
    </tr>
    <tr>
      <td style="padding:12px 18px;color:#555;font-size:13px;border-top:1px solid #e3e3e8;">Date</td>
      <td style="padding:12px 18px;font-weight:600;border-top:1px solid #e3e3e8;">{esc_date}</td>
    </tr>
  </table>
  <p style="color:#555;font-size:13px;">
    This is a one-off confirmation you asked for — we won't email you again.
    You can always come back to {support_link} to see your contribution.
  </p>
  <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
  <p style="color:#6c757d;font-size:12px;">
    FlyFun Weather is a voluntary, donation-supported service — no perks, no
    premium tier. Thank you for helping keep it running.
  </p>
</body>
</html>"""

    plain_body = (
        "Thank you for your donation 💙\n\n"
        "Your donation was completed successfully. It directly offsets the real "
        "cost of running FlyFun Weather and helps keep it free for every pilot.\n\n"
        f"  Amount: {amount_str}\n"
        f"  Date:   {date_str}\n\n"
        "This is a one-off confirmation you asked for — we won't email you again."
        + (f"\nSupport page: {support_url}\n" if support_url else "\n")
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = email
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    logger.info("Sending donation receipt email to %s", mask_email(email))
    try:
        send_message(msg)
    except Exception:
        logger.exception("Donation receipt email send failed for %s", mask_email(email))
        raise
