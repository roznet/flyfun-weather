"""Load and format the triage prompt template."""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path

from weatherbrief.triage.security import sanitize_for_untrusted_block

logger = logging.getLogger(__name__)

_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs" / "triage"
DEFAULT_TEMPLATE = "triage_prompt_v1.md"

# Values placed in the prompt's *trusted* metadata section must be plain
# identifiers / ISO timestamps. Anything else (old rows, a future field that
# skips API validation) is replaced rather than trusted, so it can never carry
# instructions or placeholders outside the untrusted block.
_TRUSTED_VALUE_RE = re.compile(r"[A-Za-z0-9._:+\-]{0,256}")


def load_prompt(item: dict, *, template: str = DEFAULT_TEMPLATE) -> str:
    """Read the prompt template and substitute feedback placeholders.

    The user-authored ``comment`` is wrapped in an `<UNTRUSTED_INPUT_xxx>`
    block with a random per-invocation delimiter, and any literal occurrence
    of the delimiter tags is stripped from it so it cannot break out of the
    block. Identifying PII (name, email) is intentionally NOT sent to the LLM
    (see PRIVACY.md).
    """
    path = _CONFIGS_DIR / template
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    text = path.read_text()

    delimiter = f"UNTRUSTED_INPUT_{secrets.token_hex(8)}"

    def _untrusted(key: str) -> str:
        return sanitize_for_untrusted_block(item.get(key), delimiter)

    def _trusted(key: str, default: str = "") -> str:
        value = str(item.get(key, default) or "")
        return value if _TRUSTED_VALUE_RE.fullmatch(value) else "N/A (invalid)"

    replacements = {
        "untrusted_delimiter": delimiter,
        "category": _trusted("category"),
        "flight_id": _trusted("flight_id"),
        "pack_timestamp": _trusted("pack_timestamp", "N/A"),
        "feedback_created_at": _trusted("feedback_created_at"),
        "comment": _untrusted("comment"),
    }

    # Single pass over the template: a substituted value is never rescanned,
    # so a value containing "{comment}" cannot pull the comment out of its
    # untrusted block.
    pattern = re.compile(r"\{(" + "|".join(map(re.escape, replacements)) + r")\}")
    return pattern.sub(lambda m: str(replacements[m.group(1)] or "N/A"), text)
