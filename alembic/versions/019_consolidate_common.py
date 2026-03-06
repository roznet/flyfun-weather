"""Consolidate shared models into flyfun-common.

Renames columns to match flyfun-common's shared schema:
- user_preferences: defaults_json → app_prefs_json
- user_preferences: encrypted_autorouter_creds → encrypted_creds_json
- user_preferences: digest_config_json merged into app_prefs_json, then dropped
- users: credit_balance → spending_limit
- New table: cost_ledger

Revision ID: 019
Revises: 018
Create Date: 2026-03-06
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Rename columns on user_preferences ---
    with op.batch_alter_table("user_preferences") as batch_op:
        batch_op.alter_column(
            "defaults_json", new_column_name="app_prefs_json", existing_type=sa.Text
        )
        batch_op.alter_column(
            "encrypted_autorouter_creds",
            new_column_name="encrypted_creds_json",
            existing_type=sa.Text,
        )

    # --- Merge digest_config_json into app_prefs_json ---
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT user_id, app_prefs_json, digest_config_json FROM user_preferences")
    ).fetchall()

    for user_id, prefs_raw, digest_raw in rows:
        try:
            prefs = json.loads(prefs_raw) if prefs_raw else {}
        except (json.JSONDecodeError, TypeError):
            prefs = {}
        try:
            digest = json.loads(digest_raw) if digest_raw else {}
        except (json.JSONDecodeError, TypeError):
            digest = {}

        if digest:
            prefs["digest_config"] = digest

        conn.execute(
            sa.text(
                "UPDATE user_preferences SET app_prefs_json = :prefs WHERE user_id = :uid"
            ),
            {"prefs": json.dumps(prefs), "uid": user_id},
        )

    # Drop digest_config_json column
    with op.batch_alter_table("user_preferences") as batch_op:
        batch_op.drop_column("digest_config_json")

    # --- Rename credit_balance → spending_limit on users ---
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "credit_balance", new_column_name="spending_limit", existing_type=sa.Float
        )

    # --- Create cost_ledger table ---
    op.create_table(
        "cost_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("service", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("cost", sa.Float, nullable=False),
        sa.Column("metadata_json", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cost_ledger")

    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "spending_limit", new_column_name="credit_balance", existing_type=sa.Float
        )

    # Re-add digest_config_json column
    with op.batch_alter_table("user_preferences") as batch_op:
        batch_op.add_column(
            sa.Column("digest_config_json", sa.Text, nullable=False, server_default="{}")
        )

    # Extract digest_config back from app_prefs_json
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT user_id, app_prefs_json FROM user_preferences")
    ).fetchall()

    for user_id, prefs_raw in rows:
        try:
            prefs = json.loads(prefs_raw) if prefs_raw else {}
        except (json.JSONDecodeError, TypeError):
            prefs = {}
        digest = prefs.pop("digest_config", {})
        conn.execute(
            sa.text(
                "UPDATE user_preferences SET digest_config_json = :digest, "
                "app_prefs_json = :prefs WHERE user_id = :uid"
            ),
            {"digest": json.dumps(digest), "prefs": json.dumps(prefs), "uid": user_id},
        )

    with op.batch_alter_table("user_preferences") as batch_op:
        batch_op.alter_column(
            "app_prefs_json", new_column_name="defaults_json", existing_type=sa.Text
        )
        batch_op.alter_column(
            "encrypted_creds_json",
            new_column_name="encrypted_autorouter_creds",
            existing_type=sa.Text,
        )
