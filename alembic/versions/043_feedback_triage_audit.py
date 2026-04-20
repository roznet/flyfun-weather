"""Add triage_prompt + triage_raw_response audit columns to feedback.

Persists the full rendered prompt sent to ``claude -p`` and the full raw
Claude JSON response. Supports post-incident forensics of prompt-injection
attempts (C1 in the 2026-04-20 security audit): the stored structured
fields only show what ended up in analysis/reply, not what the LLM
explored or how the prompt was rendered.

MEDIUMTEXT on MySQL because raw responses with long ``analysis`` text
plus ``relevant_files`` lists can exceed the 64KB TEXT limit.

Revision ID: 043
Revises: 042
Create Date: 2026-04-20
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(sa.Column(
            "triage_prompt",
            sa.Text().with_variant(MEDIUMTEXT(), "mysql"),
            nullable=True,
        ))
        batch_op.add_column(sa.Column(
            "triage_raw_response",
            sa.Text().with_variant(MEDIUMTEXT(), "mysql"),
            nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_column("triage_raw_response")
        batch_op.drop_column("triage_prompt")
