"""Add share_code to flights for short share links.

Revision ID: 049
Revises: 048
Create Date: 2026-04-30

Adds a unique 8-char base62 ``share_code`` so flights can be shared via
``/s/{code}`` instead of the full ``/flight.html?id=<long-route-slug>``
URL. Existing rows are backfilled with random codes; the column stays
nullable so direct ``FlightRow(...)`` construction in tests keeps
working without having to seed a code.
"""
from __future__ import annotations

import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _gen_code() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(sa.Column("share_code", sa.String(16), nullable=True))

    # Backfill existing rows with unique random codes. 8 chars of base62
    # ≈ 2.18e14 combos, so collisions across a small backfill are
    # vanishingly rare — but we still re-roll on the off chance.
    bind = op.get_bind()
    flights = sa.table(
        "flights",
        sa.column("id", sa.String),
        sa.column("share_code", sa.String),
    )
    rows = bind.execute(
        sa.select(flights.c.id).where(flights.c.share_code.is_(None))
    ).fetchall()
    used: set[str] = set(
        r[0] for r in bind.execute(
            sa.select(flights.c.share_code).where(flights.c.share_code.is_not(None))
        ).fetchall()
    )
    for (flight_id,) in rows:
        code = _gen_code()
        while code in used:
            code = _gen_code()
        used.add(code)
        bind.execute(
            sa.update(flights)
            .where(flights.c.id == flight_id)
            .values(share_code=code)
        )

    with op.batch_alter_table("flights") as batch_op:
        batch_op.create_index(
            "ix_flights_share_code", ["share_code"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_index("ix_flights_share_code")
        batch_op.drop_column("share_code")
