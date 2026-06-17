"""Graduate weather-alternates to default-on: backfill existing profiles.

``compute_alternates`` (#210) shipped as an opt-in experimental feature
(default off, stored in ``flight_profiles.settings_json``). It is now a normal,
default-on feature. New profiles pick up the new default at resolution time
(``packs.py`` ``get("compute_alternates", True)``), but existing profiles have an
explicit ``false`` stored by the settings save handler — so flip them on here.

Pure Python data update over the JSON ``settings_json`` Text column — works on
SQLite (dev) and MySQL (prod) without ALTER/batch (cf. migration 028).

Trade-off: this re-enables the rare user who explicitly opted out during the
experimental period — acceptable for a feature graduating from a small cohort;
they can re-opt-out in Settings. This migration is the reusable template for any
future "experimental → default-on" promotion.

Revision ID: 069
Revises: 068
Create Date: 2026-06-17
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, settings_json FROM flight_profiles")
    ).fetchall()
    for row_id, raw in rows:
        data = json.loads(raw) if raw else {}
        if data.get("compute_alternates") is True:
            continue
        data["compute_alternates"] = True
        conn.execute(
            sa.text("UPDATE flight_profiles SET settings_json = :s WHERE id = :id"),
            {"s": json.dumps(data), "id": row_id},
        )


def downgrade() -> None:
    # Best-effort reverse: drop the key. This only restores the old opt-out
    # (off) default when the code is *also* rolled back — with the current code
    # a missing key resolves to True (the new default-on). Pair this downgrade
    # with reverting packs.py / pipeline.py if you truly want alternates off.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, settings_json FROM flight_profiles")
    ).fetchall()
    for row_id, raw in rows:
        data = json.loads(raw) if raw else {}
        if "compute_alternates" not in data:
            continue
        data.pop("compute_alternates", None)
        conn.execute(
            sa.text("UPDATE flight_profiles SET settings_json = :s WHERE id = :id"),
            {"s": json.dumps(data), "id": row_id},
        )
