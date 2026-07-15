"""Sparsify stored profile settings: drop values equal to the default (#405 / #402 S2).

The one-time backfill that makes the sparse-persist design (#403) real for the
profiles that predate it. Slice 2 of #402.

**Rule** (identical to the client's ``profile-sparsify.ts`` and the server helper
:mod:`weatherbrief.analysis.advisories.profile_sparsify` — imported here, not
re-implemented):

* delete any ``advisories.params[id][key]`` whose value equals the **current
  catalog default** (imported live, never snapshotted, so a default that moved
  between merge and deploy cannot turn this no-op into a data change);
* reduce a legacy fused ``cloud_method`` to its bare source — NWP (the default)
  drops the key, DD rewrites it to the canonical ``cloud_source: "dd"`` so the
  override survives;
* delete ``icing_method`` / ``cloud_source`` / ``convective_method`` equal to
  :data:`ENGINE_METHOD_DEFAULTS`.

``advisories.enabled`` is **never touched** (#402: ``fronts: false`` equals the
catalog ``default_enabled`` yet is a real opt-out).

**Why lossless.** The #402 prod audit found every template-seeded param already
*differs* from its catalog default, so "delete == default" can only reach the
redundant copies of the default the engine applies anyway — never a template or
pilot override. The accompanying test asserts byte-identical resolved settings
before/after on prod-shaped fixtures.

**Sequencing.** Must run *after* the #403 code is live (the deploy path already
orders ``docker compose up`` before ``alembic upgrade head``). Pre-#403 code read
an absent engine method as DD via a falsy fall-through, so sparsifying against it
would silently revert profiles to DD.

**Dry-run.** Set ``SPARSIFY_DRY_RUN=1`` to compute and print the per-profile +
total blast radius and then **abort before commit** (raises, so alembic rolls the
transaction back and does not stamp the revision). Use it to reproduce the
expected counts against a prod snapshot before the real write. The richer offline
report lives in ``scripts/sparsify_profiles_dryrun.py``.

Pure Python data update over the JSON ``settings_json`` Text column (cf.
migrations 069 / 078) — raw SQL with named binds, dialect-agnostic (SQLite dev /
MySQL prod), no ALTER / batch mode. ``updated_at`` is deliberately left untouched:
a raw UPDATE does not fire the ORM ``onupdate`` hook, and this is a system-driven
correction, not a user edit.

Revision ID: 079
Revises: 078
Create Date: 2026-07-15
"""
from __future__ import annotations

import json
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "079"
down_revision: Union[str, None] = "078"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Import live at migration time (never snapshot the defaults as a literal) —
    # this is what guarantees the rule stays a no-op if a default moved between
    # merge and deploy. Imports are inside upgrade() so a bare `alembic history`
    # never imports the app.
    from weatherbrief.analysis.advisories.profile_sparsify import (
        build_param_defaults,
        sparsify_settings,
    )
    from weatherbrief.analysis.advisories.registry import get_catalog

    dry_run = os.environ.get("SPARSIFY_DRY_RUN") == "1"
    param_defaults = build_param_defaults(get_catalog())

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, settings_json FROM flight_profiles")
    ).fetchall()

    profiles_touched = 0
    totals = {
        "params_deleted": 0,
        "engine_deleted": 0,
        "cloud_method_dropped": 0,
        "cloud_method_rewritten": 0,
    }
    for row_id, raw in rows:
        settings = json.loads(raw) if raw else {}
        new_settings, stats = sparsify_settings(settings, param_defaults)
        if not stats.touched:
            continue
        profiles_touched += 1
        totals["params_deleted"] += stats.params_deleted
        totals["engine_deleted"] += stats.engine_deleted
        totals["cloud_method_dropped"] += stats.cloud_method_dropped
        totals["cloud_method_rewritten"] += stats.cloud_method_rewritten
        if dry_run:
            print(
                f"[079][dry-run] profile {row_id}: "
                f"-{stats.params_deleted} params, "
                f"-{stats.engine_deleted} engine, "
                f"cloud_method drop={stats.cloud_method_dropped} "
                f"rewrite={stats.cloud_method_rewritten}"
            )
        else:
            conn.execute(
                sa.text(
                    "UPDATE flight_profiles SET settings_json = :s WHERE id = :id"
                ),
                {"s": json.dumps(new_settings), "id": row_id},
            )

    # Alembic captures stdout in the migration log — record the blast radius so
    # the deploy log can be compared against the pre-write dry-run numbers.
    print(
        f"[079] sparsified {profiles_touched} profiles "
        f"({totals['params_deleted']} params, {totals['engine_deleted']} engine, "
        f"{totals['cloud_method_dropped']} legacy cloud_method dropped, "
        f"{totals['cloud_method_rewritten']} rewritten to cloud_source)"
    )

    if dry_run:
        # Abort so the transaction rolls back and the revision is NOT stamped.
        raise RuntimeError(
            "[079] SPARSIFY_DRY_RUN=1 — aborting before commit; no rows written."
        )


def downgrade() -> None:
    """Irreversible: the redundant values were deleted, not archived, and a
    sparsified profile is indistinguishable from one born sparse. Re-densifying
    would wrongly freeze today's defaults onto every profile (the exact thing
    #402 exists to undo) and over-densify profiles that were already sparse.
    Rollback is the pre-write ``flight_profiles`` snapshot, not this downgrade."""
    pass
