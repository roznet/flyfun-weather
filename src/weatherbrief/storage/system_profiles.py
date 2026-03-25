"""System profile templates — seed data for new users and reset-to-default."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_TEMPLATES_PATH = Path(__file__).resolve().parent.parent.parent.parent / "configs" / "system_profiles.json"

_cached_templates: list[dict] | None = None


def load_system_templates() -> list[dict]:
    """Load system profile templates from the JSON config file.

    Returns a list of dicts with keys: key, name, descriptions, settings.
    Results are cached after first load.
    """
    global _cached_templates
    if _cached_templates is not None:
        return _cached_templates

    try:
        data = json.loads(_TEMPLATES_PATH.read_text())
        _cached_templates = data.get("profiles", [])
    except Exception:
        logger.warning("Failed to load system profile templates from %s", _TEMPLATES_PATH, exc_info=True)
        _cached_templates = []

    return _cached_templates


def get_system_template(key: str) -> dict | None:
    """Get a single system template by key (e.g. 'vfr_only')."""
    for tpl in load_system_templates():
        if tpl.get("key") == key:
            return tpl
    return None


def get_template_description(key: str, locale: str = "en") -> str:
    """Get the localized description for a system template."""
    tpl = get_system_template(key)
    if not tpl:
        return ""
    descriptions = tpl.get("descriptions", {})
    return descriptions.get(locale, descriptions.get("en", ""))
