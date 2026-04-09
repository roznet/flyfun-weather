"""Create OAuth 2.1 tables for MCP authentication.

Three new tables for dynamic client registration, authorization codes,
and refresh tokens.  Adds oauth_client_id to api_tokens to track
which OAuth client issued a token.

Models defined in flyfun-common; migration owned by flyfun-weather.

Revision ID: 041
Revises: 040
Create Date: 2026-04-09
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_clients",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("client_secret_hash", sa.String(64), nullable=False),
        sa.Column("client_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("redirect_uris_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "oauth_authorization_codes",
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("redirect_uri", sa.String(1024), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(256), nullable=False, server_default="mcp"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("access_token_hash", sa.String(64), nullable=True),
    )

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("client_id", sa.String(64), nullable=False, index=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("access_token_hash", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(256), nullable=False, server_default="mcp"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.add_column(
            sa.Column("oauth_client_id", sa.String(64), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch_op:
        batch_op.drop_column("oauth_client_id")

    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_authorization_codes")
    op.drop_table("oauth_clients")
