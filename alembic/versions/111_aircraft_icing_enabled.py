"""Turn the new ``aircraft_icing`` card off where both old icing cards were off (#827).

``icing_escape`` and ``fiki_icing`` are retired in favour of one icing card
graded for the flight's aircraft, on by default. Without this rewrite, a pilot
who had switched icing off entirely would see it come back. The rule lives in
:mod:`weatherbrief.analysis.advisories.icing_settings_migration` (imported, not
re-implemented): off only if *both* old cards were off; uncustomized profiles
are not touched.

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
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "111"
down_revision: Union[str, None] = "110"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from weatherbrief.analysis.advisories.icing_settings_migration import (
        migrate_icing_enabled,
    )

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
