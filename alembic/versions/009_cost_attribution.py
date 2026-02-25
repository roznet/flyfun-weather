"""Add cost attribution: cost_config, credit_ledger tables, credit_balance on users.

Revision ID: 009
Revises: 008
Create Date: 2026-02-25
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- cost_config: versioned admin configuration ---
    op.create_table(
        "cost_config",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "active_from",
            sa.DateTime(timezone=True),
            nullable=False,
            index=True,
            server_default=sa.func.now(),
        ),
        sa.Column("active_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_cost_per_1k_input", sa.Float, nullable=False, server_default="0.003"),
        sa.Column("token_cost_per_1k_output", sa.Float, nullable=False, server_default="0.015"),
        sa.Column("droplet_monthly_usd", sa.Float, nullable=False, server_default="24.0"),
        sa.Column("misc_monthly_usd", sa.Float, nullable=False, server_default="2.0"),
        sa.Column("subscriptions_monthly_usd", sa.Float, nullable=False, server_default="30.0"),
        sa.Column(
            "subscription_details_json",
            sa.String(1024),
            nullable=False,
            server_default='{"open_meteo": 30}',
        ),
        sa.Column("disk_cost_per_gb_monthly", sa.Float, nullable=False, server_default="0.10"),
        sa.Column("estimated_monthly_briefings", sa.Integer, nullable=False, server_default="500"),
        sa.Column("margin_percent", sa.Float, nullable=False, server_default="30.0"),
        sa.Column("usd_per_credit", sa.Float, nullable=False, server_default="0.01"),
    )

    # --- credit_ledger: append-only transaction log ---
    op.create_table(
        "credit_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("amount", sa.Float, nullable=False),
        sa.Column("balance_after", sa.Float, nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("description", sa.String(256), nullable=False, server_default=""),
        sa.Column("breakdown_json", sa.Text, nullable=True),
        sa.Column(
            "briefing_usage_id",
            sa.Integer,
            sa.ForeignKey("briefing_usage.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "cost_config_id",
            sa.Integer,
            sa.ForeignKey("cost_config.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # --- Add credit_balance to users ---
    op.add_column(
        "users",
        sa.Column("credit_balance", sa.Float, nullable=False, server_default="500.0"),
    )

    # --- Add result_size_bytes to briefing_usage ---
    op.add_column(
        "briefing_usage",
        sa.Column("result_size_bytes", sa.Integer, nullable=True),
    )

    # --- Seed default cost_config ---
    cost_config = sa.table(
        "cost_config",
        sa.column("active_from", sa.DateTime(timezone=True)),
        sa.column("active_until", sa.DateTime(timezone=True)),
        sa.column("token_cost_per_1k_input", sa.Float),
        sa.column("token_cost_per_1k_output", sa.Float),
        sa.column("droplet_monthly_usd", sa.Float),
        sa.column("misc_monthly_usd", sa.Float),
        sa.column("subscriptions_monthly_usd", sa.Float),
        sa.column("subscription_details_json", sa.Text),
        sa.column("disk_cost_per_gb_monthly", sa.Float),
        sa.column("estimated_monthly_briefings", sa.Integer),
        sa.column("margin_percent", sa.Float),
        sa.column("usd_per_credit", sa.Float),
    )
    op.bulk_insert(
        cost_config,
        [
            {
                "active_from": datetime.now(timezone.utc),
                "active_until": None,
                "token_cost_per_1k_input": 0.003,
                "token_cost_per_1k_output": 0.015,
                "droplet_monthly_usd": 24.0,
                "misc_monthly_usd": 2.0,
                "subscriptions_monthly_usd": 30.0,
                "subscription_details_json": '{"open_meteo": 30}',
                "disk_cost_per_gb_monthly": 0.10,
                "estimated_monthly_briefings": 500,
                "margin_percent": 30.0,
                "usd_per_credit": 0.01,
            }
        ],
    )


def downgrade() -> None:
    op.drop_column("briefing_usage", "result_size_bytes")
    op.drop_column("users", "credit_balance")
    op.drop_table("credit_ledger")
    op.drop_table("cost_config")
