"""Turn the new ``aircraft_icing`` card off where both old icing cards were off (#827).

``icing_escape`` and ``fiki_icing`` are retired in favour of one icing card
graded for the flight's aircraft, on by default. Without this rewrite, a pilot
who had switched icing off entirely would see it come back. The rule: off
only if *both* old cards were off (absent keys read as their old catalog
defaults); uncustomized profiles are not touched; the old ids stay in the map,
inert. It is written out here, not imported from application code, so a later
change to the app cannot change what this revision does.

Pure Python data update over the JSON ``settings_json`` Text column (cf. 093):
raw SQL with named binds, dialect-agnostic, no ALTER / batch mode. A malformed
row is skipped and reported, never fails the rest. ``updated_at`` is left alone:
this is a system-driven correction, not a user edit.

Downgrade is a no-op: the old cards read their own keys, which this never
changed, and a stray ``aircraft_icing: false`` is inert to code that does not
know the card.

Revision ID: 111
Revises: 110
Create Date: 2026-10-19
"""
from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "111"
down_revision: Union[str, None] = "110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_ID = "aircraft_icing"
_OLD_DEFAULTS = {"icing_escape": True, "fiki_icing": False}


def migrate_icing_enabled(settings: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return ``(settings, changed)`` with ``aircraft_icing`` set off where due.

    Writes only ``advisories.enabled.aircraft_icing = False``, and only when the
    map is a customized dict, does not already decide the new card, and leaves
    both old cards off.
    """
    adv = settings.get("advisories")
    if not isinstance(adv, dict):
        return settings, False
    enabled = adv.get("enabled")
    if not isinstance(enabled, dict) or not enabled or NEW_ID in enabled:
        return settings, False
    if any(bool(enabled.get(k, default)) for k, default in _OLD_DEFAULTS.items()):
        return settings, False
    new_enabled = {**enabled, NEW_ID: False}
    return {**settings, "advisories": {**adv, "enabled": new_enabled}}, True


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, settings_json FROM flight_profiles")).fetchall()
    changed = 0
    skipped: list[int] = []
    for row_id, raw in rows:
        try:
            settings = json.loads(raw) if raw else {}
            new_settings, touched = migrate_icing_enabled(settings)
        except Exception as exc:  # noqa: BLE001 - one bad row must not fail the rest
            skipped.append(row_id)
            print(f"[111] SKIPPED profile {row_id}: {type(exc).__name__}: {exc}")
            continue
        if not touched:
            continue
        conn.execute(
            sa.text("UPDATE flight_profiles SET settings_json = :s WHERE id = :id"),
            {"s": json.dumps(new_settings), "id": row_id},
        )
        changed += 1
    if skipped:
        print(f"[111] {len(skipped)} profile(s) SKIPPED: {skipped}")
    print(f"[111] aircraft_icing switched off on {changed} profile(s) with both old icing cards off")


def downgrade() -> None:
    pass
