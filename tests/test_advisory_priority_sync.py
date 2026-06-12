"""Guard: every registered advisory must appear in the frontend priority list.

The briefing UI sorts advisory cards within each severity band by an explicit
``ADVISORY_PRIORITY`` list in ``web/ts/helpers/advisory-order.ts``. Advisories
missing from that list silently sort last. Since advisories are *born* in the
backend registry (``@register``), this test — run as part of the backend suite a
dev already runs after adding one — fails loudly if the frontend list drifts out
of sync, telling them exactly which IDs to add or remove.
"""

from __future__ import annotations

import re
from pathlib import Path

from weatherbrief.analysis.advisories import get_catalog

_PRIORITY_FILE = (
    Path(__file__).resolve().parent.parent
    / "web" / "ts" / "helpers" / "advisory-order.ts"
)


def _parse_priority_ids() -> list[str]:
    """Extract the quoted IDs from the ``ADVISORY_PRIORITY`` array in the TS file."""
    text = _PRIORITY_FILE.read_text()
    m = re.search(r"const ADVISORY_PRIORITY:\s*string\[\]\s*=\s*\[(.*?)\]", text, re.S)
    assert m, f"Could not find ADVISORY_PRIORITY array in {_PRIORITY_FILE}"
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


def test_advisory_priority_list_covers_all_registered_advisories():
    registered = {e.id for e in get_catalog()}
    priority = _parse_priority_ids()

    missing = registered - set(priority)
    stale = set(priority) - registered

    assert not missing, (
        "Advisories registered in the backend but absent from ADVISORY_PRIORITY "
        f"(web/ts/helpers/advisory-order.ts) — add them in the right order: {sorted(missing)}"
    )
    assert not stale, (
        "IDs in ADVISORY_PRIORITY that no longer exist in the registry — remove "
        f"them: {sorted(stale)}"
    )


def test_advisory_priority_list_has_no_duplicates():
    priority = _parse_priority_ids()
    dupes = {x for x in priority if priority.count(x) > 1}
    assert not dupes, f"Duplicate IDs in ADVISORY_PRIORITY: {sorted(dupes)}"
