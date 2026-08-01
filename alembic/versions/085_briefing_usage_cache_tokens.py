"""Record the prompt-cache split of a digest's input tokens.

Anthropic prices a cached prefix in three tiers: uncached input at 1x, a cache
write at 2x (the 1h TTL we use), and a cache read at 0.1x. langchain-anthropic
folds all three into a single ``input_tokens`` figure, so without these two
columns ``compute_cost`` prices every cached token as if it were full price —
the ledger would show no saving at all once caching is enabled, and would
overcharge users for reads.

Both columns are subsets of ``llm_input_tokens``, never additions to it:

    uncached = llm_input_tokens - llm_cache_read_tokens - llm_cache_write_tokens

Nullable with no server_default on purpose. Rows written before this shipped
genuinely have no cache data, and NULL says that; a 0 default would claim
"nothing was cached", which is indistinguishable from a real cache miss and
would quietly bias any hit-rate measurement over the backfilled window.

Revision ID: 085
Revises: 084
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "085"
down_revision: Union[str, None] = "084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_usage") as batch_op:
        batch_op.add_column(
            sa.Column("llm_cache_read_tokens", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("llm_cache_write_tokens", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_usage") as batch_op:
        batch_op.drop_column("llm_cache_write_tokens")
        batch_op.drop_column("llm_cache_read_tokens")
