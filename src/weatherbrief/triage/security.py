"""Input/output safety checks for the Claude-CLI feedback triage pipeline.

Two controls:

- `sanitize_for_untrusted_block()` strips the random-delimiter tag sequence
  from user-submitted text before it is embedded in the prompt. Defends
  against prompt-injection attempts that try to close the untrusted-input
  block and impersonate trusted prompt content.
- `scan_for_exfil()` checks LLM-generated text for patterns that should
  never appear in a reply to a user (filesystem paths, API key formats,
  PEM blocks, environment-variable names). Used to flag triage output for
  manual review and to gate the admin send endpoint.
"""

from __future__ import annotations

import re


# (label, pattern). Conservative regex set — tuned to minimise false
# positives against normal aviation-weather reply text while catching the
# most common secret-exfiltration shapes.
_SUSPICIOUS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "absolute_fs_path",
        re.compile(r"(?:^|[\s(`'\"])(?:/Users/|/home/|/app/|/etc/|/var/|/mnt/|/root/)[A-Za-z0-9._/-]+"),
    ),
    ("pem_block", re.compile(r"-----BEGIN [A-Z ]+-----")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("flyfun_api_token", re.compile(r"\b(?:ff|ffr|wb)_[A-Za-z0-9+/=_-]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"\bxox[bpoas]-[A-Za-z0-9-]{10,}")),
    ("dotenv_reference", re.compile(r"(?<![A-Za-z0-9])\.env(?:\.[a-z]+)?\b")),
    (
        "secret_env_name",
        re.compile(
            r"\b(?:JWT_SECRET|HMAC_SECRET|SESSION_SECRET|CREDENTIAL_ENCRYPTION_KEY|"
            r"ANTHROPIC_API_KEY|OPENAI_API_KEY|RESEND_API_KEY|"
            r"GOOGLE_CLIENT_SECRET|APPLE_PRIVATE_KEY|"
            r"AUTOROUTER_PASSWORD|MYSQL_PASSWORD|DATABASE_URL)\b"
        ),
    ),
]


def scan_for_exfil(text: str | None) -> list[str]:
    """Return labels of suspicious patterns found in *text*. Empty if clean."""
    if not text:
        return []
    return [label for label, pattern in _SUSPICIOUS_PATTERNS if pattern.search(text)]


def sanitize_for_untrusted_block(value: str | None, delimiter: str) -> str:
    """Strip the open/close tags for *delimiter* from *value*.

    The prompt wraps user-submitted content in `<DELIMITER>...</DELIMITER>`
    with a random per-invocation delimiter. Stripping any literal occurrence
    of those tags from the user value prevents a comment from breaking out
    of the untrusted block, even if an attacker somehow guessed the token.
    """
    if not value:
        return ""
    return value.replace(f"<{delimiter}>", "").replace(f"</{delimiter}>", "")
