"""Backfill share_code on existing trips.

Revision ID: 096
Revises: 095
Create Date: 2026-09-14

``flight_trips.share_code`` was created by 095 (unique, indexed) and reserved
for a later share-a-trip feature. That feature is here, so every existing row
needs a code. No schema change, so no ``batch_alter_table``: this is data only.

Trips created from now on get a code at insert (``storage/trips.py``), and the
read path mints one lazily for any row that still has NULL, so this backfill is
belt-and-braces for rows that predate the feature rather than the only source.
"""
from __future__ import annotations

import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "096"
down_revision: Union[str, None] = "095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Same alphabet and length as the flight codes (049), so both shapes pass the
# one ``SHARE_CODE_RE`` the ``/s/`` and ``/t/`` redirects share.
_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _gen_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def upgrade() -> None:
    bind = op.get_bind()
    trips = sa.table(
        "flight_trips",
        sa.column("id", sa.String),
        sa.column("share_code", sa.String),
    )
    rows = bind.execute(
        sa.select(trips.c.id).where(trips.c.share_code.is_(None))
    ).fetchall()
    # Seed the used set from the codes already present so the unique index
    # can't be tripped by a re-run or by rows minted lazily before deploy.
    used: set[str] = {
        r[0]
        for r in bind.execute(
            sa.select(trips.c.share_code).where(trips.c.share_code.is_not(None))
        ).fetchall()
    }
    for (trip_id,) in rows:
        code = _gen_code()
        while code in used:
            code = _gen_code()
        used.add(code)
        bind.execute(
            sa.update(trips).where(trips.c.id == trip_id).values(share_code=code)
        )


def downgrade() -> None:
    # The column outlives this migration (095 owns it); clearing the codes is
    # the honest inverse of filling them.
    bind = op.get_bind()
    trips = sa.table(
        "flight_trips",
        sa.column("id", sa.String),
        sa.column("share_code", sa.String),
    )
    bind.execute(sa.update(trips).values(share_code=None))
