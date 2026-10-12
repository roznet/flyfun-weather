"""Every user has a default aircraft; cruise speed moves to the aircraft (#777).

0. DDL: ``user_aircraft.is_placeholder`` (Boolean, server default false).
1. Every user gets exactly one default aircraft — an existing one promoted, or
   a synthesised placeholder ("My aircraft", ZZZZ, ``is_placeholder``).
2. An aircraft without a cruise speed takes the speed its flights used via the
   profile fallback, so their results don't change.
3. ``speed_kt`` is stripped from every ``flight_profiles.settings_json``
   (``updated_at`` untouched, as 079).

The plan lives in :mod:`weatherbrief.storage.aircraft_backfill`, shared with
the read-only ``scripts/aircraft_backfill_dryrun.py`` — the preferred way to
preview the numbers against a prod snapshot, since it has no transaction
footprint at all.

**Same cruise IAS for every flight.** After writing, the migration re-reads
the DB and asserts every flight resolves the cruise IAS the plan predicted
(a mismatch there is a bug → raise, roll back). Flights whose IAS differs from
*before* the migration are printed: they can only be flights that shared an
aircraft across profiles with different speeds, or a no-aircraft flight whose
profile speed disagrees with the user's real default aircraft.

**Dry-run.** ``AIRCRAFT_BACKFILL_DRY_RUN=1`` prints the plan and the
before/after report, then aborts **before the DDL** (MySQL commits an ALTER
implicitly, so aborting after it would leave the column behind with the
revision unstamped). Like 079's dry run, the raise rolls back the whole
``alembic upgrade`` invocation — run it only with 103 as the sole pending
revision, or use the offline script.

Revision ID: 103
Revises: 102
Create Date: 2026-10-12
"""
from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "103"
down_revision: Union[str, None] = "102"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Imported inside upgrade() so a bare `alembic history` never imports the app.
    from weatherbrief.storage.aircraft_backfill import (
        apply_plan,
        cruise_ias_after,
        format_report,
        plan_backfill,
    )

    conn = op.get_bind()
    plan = plan_backfill(conn)
    print(f"[103] {format_report(plan)}")

    if os.environ.get("AIRCRAFT_BACKFILL_DRY_RUN") == "1":
        raise RuntimeError(
            "[103] AIRCRAFT_BACKFILL_DRY_RUN=1 — aborting before any write. This "
            "rolls back the entire `alembic upgrade` invocation; run only when 103 "
            "is the sole pending revision (else use scripts/aircraft_backfill_dryrun.py)."
        )

    # Idempotent: MySQL commits the ALTER implicitly, so if anything below
    # raises, the column survives the rollback while the revision stays
    # unstamped — the re-run must not trip over it.
    columns = {c["name"] for c in sa.inspect(conn).get_columns("user_aircraft")}
    if "is_placeholder" not in columns:
        with op.batch_alter_table("user_aircraft") as batch_op:
            batch_op.add_column(
                sa.Column("is_placeholder", sa.Boolean(), nullable=False, server_default=sa.false())
            )

    apply_plan(conn, plan)

    actual = cruise_ias_after(conn)
    drift = [fid for fid, kt in plan.after_ias.items() if actual.get(fid) != kt]
    if drift:
        raise RuntimeError(
            f"[103] {len(drift)} flights resolve a cruise IAS other than planned "
            f"(e.g. {drift[:5]}) — rolling back the data (on MySQL the "
            f"is_placeholder column stays; a re-run skips adding it)."
        )


def downgrade() -> None:
    """Drops the placeholder flag and the placeholder aircraft it marks.

    Promoted defaults, gap-filled speeds and the stripped profile ``speed_kt``
    are not restored. Flights on a real aircraft keep their speed (pre-#777
    code reads the aircraft speed first); flights on a deleted placeholder go
    back to NULL and, with ``speed_kt`` gone from the profile, lose their
    cruise speed — the headwind advisory falls back to the planned duration.
    Rollback for that is the pre-migration snapshot, not this downgrade.
    """
    conn = op.get_bind()
    placeholder_ids = [
        r[0] for r in conn.execute(
            sa.text("SELECT id FROM user_aircraft WHERE is_placeholder = :t"), {"t": True}
        ).fetchall()
    ]
    for aid in placeholder_ids:
        conn.execute(sa.text("UPDATE flights SET aircraft_id = NULL WHERE aircraft_id = :id"), {"id": aid})
        conn.execute(sa.text("UPDATE pireps SET aircraft_id = NULL WHERE aircraft_id = :id"), {"id": aid})
        conn.execute(sa.text("DELETE FROM user_aircraft WHERE id = :id"), {"id": aid})
    with op.batch_alter_table("user_aircraft") as batch_op:
        batch_op.drop_column("is_placeholder")
