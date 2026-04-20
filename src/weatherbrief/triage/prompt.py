"""Load and format the triage prompt template."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path

from weatherbrief.triage.security import sanitize_for_untrusted_block

logger = logging.getLogger(__name__)

_CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs" / "triage"
DEFAULT_TEMPLATE = "triage_prompt_v1.md"


def load_prompt(item: dict, *, template: str = DEFAULT_TEMPLATE) -> str:
    """Read the prompt template and substitute feedback placeholders.

    User-authored fields (comment, user_name, user_email) are wrapped in an
    `<UNTRUSTED_INPUT_xxx>` block with a random per-invocation delimiter,
    and any literal occurrence of the delimiter tags is stripped from the
    user values so they cannot break out of the block.
    """
    path = _CONFIGS_DIR / template
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    text = path.read_text()

    delimiter = f"UNTRUSTED_INPUT_{secrets.token_hex(8)}"

    def _untrusted(key: str) -> str:
        return sanitize_for_untrusted_block(item.get(key), delimiter)

    replacements = {
        "untrusted_delimiter": delimiter,
        "category": item.get("category", ""),
        "flight_id": item.get("flight_id", ""),
        "pack_timestamp": item.get("pack_timestamp", "N/A"),
        "feedback_created_at": item.get("feedback_created_at", ""),
        "comment": _untrusted("comment"),
        "user_email": _untrusted("user_email"),
        "user_name": _untrusted("user_name"),
    }

    for key, value in replacements.items():
        text = text.replace("{" + key + "}", str(value or "N/A"))

    return text
