"""Migrate cost_config from typed columns to single config_json column.

Revision ID: 028
Revises: 027
Create Date: 2026-03-27
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Columns that move into config_json
_CONFIG_COLS = [
    "token_cost_per_1k_input",
    "token_cost_per_1k_output",
    "droplet_monthly_usd",
    "misc_monthly_usd",
    "subscriptions_monthly_usd",
    "subscription_details_json",
    "disk_cost_per_gb_monthly",
    "estimated_monthly_briefings",
    "margin_percent",
    "usd_per_credit",
]


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add config_json column (nullable initially for backfill)
    op.add_column(
        "cost_config",
        sa.Column("config_json", sa.Text, nullable=True),
    )

    # 2. Backfill existing rows
    rows = conn.execute(sa.text("SELECT id, " + ", ".join(_CONFIG_COLS) + " FROM cost_config")).fetchall()
    for row in rows:
        row_id = row[0]
        data = {}
        for i, col in enumerate(_CONFIG_COLS):
            val = row[i + 1]
            if col == "subscription_details_json":
                # Flatten into subscription_details dict
                try:
                    data["subscription_details"] = json.loads(val) if val else None
                except (json.JSONDecodeError, TypeError):
                    data["subscription_details"] = None
            elif col == "usd_per_credit":
                pass  # Drop vestigial field
            else:
                data[col] = val
        conn.execute(
            sa.text("UPDATE cost_config SET config_json = :cj WHERE id = :id"),
            {"cj": json.dumps(data), "id": row_id},
        )

    # 3. Make config_json NOT NULL and drop old columns (batch for SQLite compat)
    with op.batch_alter_table("cost_config") as batch_op:
        batch_op.alter_column("config_json", existing_type=sa.Text, nullable=False)
        for col in _CONFIG_COLS:
            batch_op.drop_column(col)


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Re-add typed columns
    op.add_column("cost_config", sa.Column("token_cost_per_1k_input", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("token_cost_per_1k_output", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("droplet_monthly_usd", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("misc_monthly_usd", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("subscriptions_monthly_usd", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("subscription_details_json", sa.String(1024), nullable=True))
    op.add_column("cost_config", sa.Column("disk_cost_per_gb_monthly", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("estimated_monthly_briefings", sa.Integer, nullable=True))
    op.add_column("cost_config", sa.Column("margin_percent", sa.Float, nullable=True))
    op.add_column("cost_config", sa.Column("usd_per_credit", sa.Float, nullable=True))

    # 2. Backfill from config_json
    rows = conn.execute(sa.text("SELECT id, config_json FROM cost_config")).fetchall()
    for row in rows:
        row_id, cj = row
        data = json.loads(cj) if cj else {}
        sub_details = data.get("subscription_details")
        conn.execute(
            sa.text("""UPDATE cost_config SET
                token_cost_per_1k_input = :a, token_cost_per_1k_output = :b,
                droplet_monthly_usd = :c, misc_monthly_usd = :d,
                subscriptions_monthly_usd = :e, subscription_details_json = :f,
                disk_cost_per_gb_monthly = :g, estimated_monthly_briefings = :h,
                margin_percent = :i, usd_per_credit = :j
                WHERE id = :id"""),
            {
                "a": data.get("token_cost_per_1k_input", 0.003),
                "b": data.get("token_cost_per_1k_output", 0.015),
                "c": data.get("droplet_monthly_usd", 24.0),
                "d": data.get("misc_monthly_usd", 2.0),
                "e": data.get("subscriptions_monthly_usd", 30.0),
                "f": json.dumps(sub_details) if sub_details else '{"open_meteo": 30}',
                "g": data.get("disk_cost_per_gb_monthly", 0.10),
                "h": data.get("estimated_monthly_briefings", 500),
                "i": data.get("margin_percent", 30.0),
                "j": 0.01,
                "id": row_id,
            },
        )

    # 3. Drop config_json
    with op.batch_alter_table("cost_config") as batch_op:
        batch_op.drop_column("config_json")
