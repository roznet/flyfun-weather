"""Debrief answers, the window-close report, and the form marker (#1023).

Revision ID: 121
Revises: 120
Create Date: 2026-11-21

* ``flight_debrief_answers``: one row per answer to a debrief question, with
  the question as asked and the forecast it was asked about.
* ``flight_debrief_reports``: the report built once at window close
  (headline, interest, questions asked, push outcome).
* ``flight_debriefs.form`` (VARCHAR(16)): ``questions`` | ``legacy``. Every
  existing row is the old form, so it is backfilled to ``legacy``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# DateTime(timezone=True) renders the same DDL as the models' TZDateTime()
# (designs/time-alignment-audit.md): answer and build times are not keys.

revision: str = "121"
down_revision: Union[str, None] = "120"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_debrief_answers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flight_id", sa.String(length=256),
                  sa.ForeignKey("flights.id", ondelete="CASCADE", name="fk_flight_debrief_answers_flight_id"),
                  nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("answer", sa.String(length=32), nullable=False),
        sa.Column("avoided", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rules_version", sa.String(length=16), nullable=False),
        sa.Column("question_json", sa.Text(), nullable=False),
        sa.Column("forecast_json", sa.Text(), nullable=True),
        sa.UniqueConstraint("flight_id", "question_id", name="uq_flight_debrief_answer"),
    )
    op.create_index("ix_flight_debrief_answers_flight_id", "flight_debrief_answers", ["flight_id"])

    op.create_table(
        "flight_debrief_reports",
        sa.Column("flight_id", sa.String(length=256),
                  sa.ForeignKey("flights.id", ondelete="CASCADE", name="fk_flight_debrief_reports_flight_id"),
                  primary_key=True),
        sa.Column("built_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rules_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("headline", sa.Text(), nullable=True),
        sa.Column("differs_from_forecast", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("interesting", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("reasons_json", sa.Text(), nullable=True),
        sa.Column("questions_json", sa.Text(), nullable=True),
        sa.Column("push_reason", sa.String(length=300), nullable=True),
        sa.Column("push_outcome", sa.String(length=32), nullable=True),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
    )

    with op.batch_alter_table("flight_debriefs") as batch_op:
        batch_op.add_column(sa.Column("form", sa.String(length=16), nullable=True))
    op.execute("UPDATE flight_debriefs SET form = 'legacy' WHERE form IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("flight_debriefs") as batch_op:
        batch_op.drop_column("form")
    op.drop_table("flight_debrief_reports")
    op.drop_index("ix_flight_debrief_answers_flight_id", table_name="flight_debrief_answers")
    op.drop_table("flight_debrief_answers")
