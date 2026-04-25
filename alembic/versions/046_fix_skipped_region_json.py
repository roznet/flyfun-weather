"""Backfill briefing_packs.models_skipped_region_json: '{}' → '[]'.

Pre-2026-03-06 rows ended up with the literal JSON object ``'{}'`` instead
of an empty list. The Pydantic model `BriefingPackMeta.models_skipped_region`
is typed `list[str]`, so loading those rows 500s on `/packs/latest`.

The correct value for those rows is `'[]'` — they predate the auto-skip
feature (introduced in migration 016, Feb 2026), so semantically they
"skipped nothing".

The writer is no longer in the codebase; we keep the loader strict so
any future bad data fails loudly instead of getting silently coerced.

Revision ID: 046
Revises: 045
Create Date: 2026-04-25
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE briefing_packs SET models_skipped_region_json = '[]' "
        "WHERE models_skipped_region_json = '{}'"
    )


def downgrade() -> None:
    # Intentionally a no-op: the original '{}' values were a bug, not a
    # historical state we want to restore on rollback.
    pass
