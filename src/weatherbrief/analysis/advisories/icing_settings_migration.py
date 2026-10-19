"""Carry a profile's icing opt-out over to the ``aircraft_icing`` card (#827).

``icing_escape`` (default on) and ``fiki_icing`` (default off) are retired in
favour of one card, on by default. A profile keeps icing **off** only if it had
*both* old cards off; any profile that showed at least one icing card gets the
new one. A profile with no ``advisories.enabled`` map is uncustomized and gets
the default (on) without being touched.

The old ids are left in the map: the cards are retired, so the values are
inert, and the rule of migration 093 holds here too (never delete a value we
cannot prove is a default).

Shared by Alembic revision 111 and its tests, so the rule lives once.
"""

from __future__ import annotations

from typing import Any

NEW_ID = "aircraft_icing"
_OLD_DEFAULTS = {"icing_escape": True, "fiki_icing": False}


def migrate_icing_enabled(settings: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return ``(settings, changed)`` with ``aircraft_icing`` set off where due.

    Writes only ``advisories.enabled.aircraft_icing = False``, and only when the
    map is a customized dict, does not already decide the new card, and leaves
    both old cards off (absent keys resolve to their old catalog defaults, the
    way ``resolve_enabled_ids`` read them).
    """
    adv = settings.get("advisories")
    if not isinstance(adv, dict):
        return settings, False
    enabled = adv.get("enabled")
    if not isinstance(enabled, dict) or not enabled or NEW_ID in enabled:
        return settings, False
    any_on = any(bool(enabled.get(k, default)) for k, default in _OLD_DEFAULTS.items())
    if any_on:
        return settings, False
    new_enabled = {**enabled, NEW_ID: False}
    return {**settings, "advisories": {**adv, "enabled": new_enabled}}, True
