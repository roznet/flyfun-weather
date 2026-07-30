"""Persist a model delivery log (issue #515).

The freshness registry's ``delivery_offset`` constants were calibrated against
observation once and never revisited, and nothing durable recorded what
actually happened: ``published_at`` was fetched on every check, held on the
in-memory ``Marker``, and dropped. The only persisted history —
``MarkerStore``'s ``(observed_init, now)`` deque — recorded our *detection*
time, which the 5-minute poll quantises and which by construction can never
observe an early arrival (the loop doesn't look before a run is due).

This table records one row per observed run, keeping the provider's publish
time separate from our detection time so drift and poll lag stay distinct
quantities. Collect-only: written by ``scheduler._run_freshness_check_once``,
read by nothing yet.

``create_table`` / ``create_index`` work on SQLite (dev) and MySQL (prod)
without batch mode.

Revision ID: 084
Revises: 083
Create Date: 2026-07-30
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "084"
down_revision: Union[str, None] = "083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_delivery_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # Registry key, e.g. "ecmwf:direct".
        sa.Column("source", sa.String(32), nullable=False),
        # Logical model ("ecmwf"), denormalised from the key prefix so
        # "all GFS deliveries" needs no string split.
        sa.Column("model", sa.String(20), nullable=False),
        sa.Column("cycle_init", sa.DateTime(timezone=True), nullable=False),
        # Registry prediction frozen at observation time — recomputing it at
        # read time would erase the record of what was wrong the moment we
        # recalibrate the constants.
        sa.Column("expected_at", sa.DateTime(timezone=True), nullable=False),
        # Provider-reported publish wallclock: the actual measurement.
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        # When our loop noticed (quantised by the poll interval).
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        # Last dynamic check that did NOT yet see this run.
        sa.Column("last_absent_at", sa.DateTime(timezone=True), nullable=True),
        # "sentinel_mtime" | "http_last_modified" | "om_meta" — these
        # instruments carry different systematic biases.
        sa.Column("observed_via", sa.String(24), nullable=False),
        sa.UniqueConstraint(
            "source", "cycle_init", name="uq_model_delivery_source_cycle"
        ),
    )
    # Calibration reads are "the last N weeks", optionally narrowed by source.
    op.create_index("ix_model_delivery_cycle_init", "model_delivery_log", ["cycle_init"])


def downgrade() -> None:
    op.drop_index("ix_model_delivery_cycle_init", table_name="model_delivery_log")
    op.drop_table("model_delivery_log")
