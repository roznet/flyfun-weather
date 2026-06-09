"""Add edr_calibration_accumulator table.

Streaming ln-moment accumulator for the Sharman & Pearson (2017) EDR remap
(issue #221). One row per (model, diagnostic, band) holding running sums of
ln(D); coefficients are derived offline, samples are never stored.

Revision ID: 063
Revises: 062
Create Date: 2026-06-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_table works on both SQLite and MySQL without batch mode.
    # sum_ln / sum_ln2 are DOUBLE — they accumulate indefinitely across cycles
    # and would lose precision in MySQL's single-precision FLOAT.
    op.create_table(
        "edr_calibration_accumulator",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("diagnostic", sa.String(20), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("n", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("sum_ln", sa.Double, nullable=False, server_default="0"),
        sa.Column("sum_ln2", sa.Double, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model", "diagnostic", "band", name="uq_edr_calib_key"),
    )


def downgrade() -> None:
    op.drop_table("edr_calibration_accumulator")
