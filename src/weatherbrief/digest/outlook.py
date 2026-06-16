"""Canonical long-range outlook labels, shared across digest + notify code.

Kept in this lightweight module (no heavy imports) so ``notify/email.py`` can use
the same strings without importing the LangGraph-laden ``llm_digest``. The web UI
keeps its own localised labels in the i18n catalogs; these are the Python-side
canonical English strings.
"""
from __future__ import annotations

# Plain-text labels — emoji stripped, since email clients render emoji
# inconsistently.
OUTLOOK_LABELS = {
    "TRENDING_SETTLED": "Trending settled",
    "MIXED_SIGNALS": "Mixed signals",
    "TRENDING_UNSETTLED": "Trending unsettled",
}

# Icons keyed by the same outlook values, for the markdown/CLI digest.
OUTLOOK_ICONS = {
    "TRENDING_SETTLED": "\U0001f7e2",   # green circle
    "MIXED_SIGNALS": "\U0001f7e1",      # yellow circle
    "TRENDING_UNSETTLED": "\U0001f7e0",  # orange circle
}
