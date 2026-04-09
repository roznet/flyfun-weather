"""Widen verification_cache.data_json to MEDIUMTEXT for MySQL.

MySQL TEXT is limited to ~65KB which is too small for verification map
JSON with ~830 airports. MEDIUMTEXT supports up to 16MB.
SQLite TEXT is already unlimited, so no change needed there.

Revision ID: 040
Revises: 039
"""

revision = "040"
down_revision = "039"

from alembic import op


def upgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.execute("ALTER TABLE verification_cache MODIFY data_json MEDIUMTEXT NOT NULL")


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        op.execute("ALTER TABLE verification_cache MODIFY data_json TEXT NOT NULL")
