"""Drop stored ``synoptic_forecast_map_enabled: false`` so users follow the new default.

Revision ID: 105
Revises: 104
Create Date: 2026-10-17

The Synoptic Forecast tab flips from opt-in to on by default. Every Settings
save wrote the checkbox's state, so users who saved any account setting while
the default was off carry an explicit ``false`` they never chose (72 of 874
prod rows on 2026-10-17). Removing that one key lets them follow the default;
every other key in ``app_prefs_json`` is written back unchanged, and a stored
``true`` is left alone.

Data only, no ``batch_alter_table``.
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "105"
down_revision: Union[str, None] = "104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEY = "synoptic_forecast_map_enabled"


def upgrade() -> None:
    bind = op.get_bind()
    prefs = sa.table(
        "user_preferences",
        sa.column("user_id", sa.String),
        sa.column("app_prefs_json", sa.Text),
    )
    rows = bind.execute(
        sa.select(prefs.c.user_id, prefs.c.app_prefs_json)
        .where(prefs.c.app_prefs_json.like(f'%"{KEY}"%'))
    ).fetchall()
    for user_id, raw in rows:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get(KEY) is not False:
            continue
        del data[KEY]
        bind.execute(
            sa.update(prefs)
            # Conditional on the blob read above: the new app is already
            # serving while this runs, and a save landing in between wins.
            .where(prefs.c.user_id == user_id, prefs.c.app_prefs_json == raw)
            .values(app_prefs_json=json.dumps(data))
        )


def downgrade() -> None:
    # Absence and an incidental ``false`` were indistinguishable under the old
    # default, so there is nothing meaningful to restore.
    pass
