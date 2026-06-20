"""Privacy helpers for safe logging of personal data.

GDPR: ops logs must not store user PII in the clear (SECURITY_AUDIT L-new-8).
``mask_email`` keeps logs debuggable (first char of the local part + the full
domain) without writing a readable address, so an account can still be
correlated by an operator who already knows it but can't be harvested from logs.
"""

from __future__ import annotations

from collections.abc import Iterable


def mask_email(value: object) -> str:
    """Mask an email address (or an iterable of them) for safe logging.

        brice.rosenzweig@gmail.com -> b***@gmail.com
        a@x.com                    -> a***@x.com
        ["x@a.com", "y@b.com"]     -> "x***@a.com, y***@b.com"

    Non-email strings are masked defensively; ``None`` becomes ``<none>``.
    """
    if value is None:
        return "<none>"
    if isinstance(value, (list, tuple, set, frozenset)) or (
        isinstance(value, Iterable) and not isinstance(value, (str, bytes))
    ):
        return ", ".join(mask_email(v) for v in value)
    s = str(value)
    if not s:
        return "<empty>"
    if "@" not in s:
        # Not an email — keep one character so different values stay distinct.
        return s[0] + "***"
    local, _, domain = s.partition("@")
    if not local:
        return "***@" + domain
    return local[0] + "***@" + domain
