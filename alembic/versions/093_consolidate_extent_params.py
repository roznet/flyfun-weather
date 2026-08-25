"""Rewrite pre-consolidation extent parameter keys in profile settings (#571 S3).

Twenty-four catalog keys across thirteen advisories expressed one idea — "how
much of the route counts as a lot" — and became ``extent_pct_amber`` /
``extent_pct_red`` / ``extent_min_nm``, each advisory still declaring its own
default. Stored profiles key on the OLD names, so they must be rewritten.

**Why this cannot be left to sparsify.** ``profile_sparsify`` deliberately keeps
any key it cannot prove is a default (*"we never delete a value we cannot prove
is a default"*). After the rename the old keys are simply *unknown* keys, so the
sparsify would preserve them forever — sitting in the profile, doing nothing,
while the pilot believes their tuning is live. The rewrite has to be active, and
this is it.

**Rule** (identical to the client's ``profile-sparsify.ts`` ``renameExtentParams``
and the server helper
:mod:`weatherbrief.analysis.advisories.extent_param_migration` — imported here,
not re-implemented):

* rename each old ``advisories.params[id][key]`` to its consolidated key;
* invert the VALUE of ``fiki_icing``'s pair, which expressed a percentage of the
  *clear* cruise compared with ``<``. "Amber below 70% clear" means "amber at or
  above 30% affected", so the number must flip with the name or the pilot's
  tuning would silently invert;
* apply ``icing_escape``'s pre-existing read-path aliases (``route_pct_amber``,
  and ``min_route_pct``, which was never a catalog key at all) only when the
  primary name is absent — exactly as the fallback chain they replace did, so a
  profile carrying both resolves as it graded before;
* where a value is already stored under the new key, that value wins and the old
  key is dropped.

``advisories.enabled`` is never touched, and no key outside the rename map is
read or written.

**Lossless for grading.** The keys are renamed, not re-valued (except the
deliberate ``fiki_icing`` inversion, which preserves the *meaning* of the number
rather than its digits). The accompanying test asserts a profile carrying every
old key resolves byte-identically before and after.

**Sequencing.** Must run *after* the #571 code is live — the deploy path already
orders ``docker compose up`` before ``alembic upgrade head``. Pre-#571 code reads
the old keys and does not know the new ones, so migrating against it would drop
every pilot override to its default.

**Dry-run.** Set ``EXTENT_RENAME_DRY_RUN=1`` to compute and print the per-profile
and total blast radius, then **abort before commit** (raises to roll back).

*Precondition — 093 must be the sole pending revision.* ``env.py`` opens one
transaction per ``alembic upgrade`` invocation, so the raise rolls back the
**entire** invocation, not just this revision. Prefer the transaction-free
offline report ``scripts/extent_param_rename_dryrun.py``, which reads the DB
read-only and has no rollback footprint at all.

Pure Python data update over the JSON ``settings_json`` Text column (cf.
migrations 079 / 069 / 078) — raw SQL with named binds, dialect-agnostic (SQLite
dev / MySQL prod), no ALTER / batch mode. ``updated_at`` is deliberately left
untouched: a raw UPDATE does not fire the ORM ``onupdate`` hook, and this is a
system-driven correction, not a user edit.

Revision ID: 093
Revises: 092
Create Date: 2026-08-25
"""
from __future__ import annotations

import json
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "093"
down_revision: Union[str, None] = "092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Imported inside upgrade() so a bare `alembic history` never imports the app.
    from weatherbrief.analysis.advisories.extent_param_migration import (
        rename_extent_params,
    )

    dry_run = os.environ.get("EXTENT_RENAME_DRY_RUN") == "1"

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, settings_json FROM flight_profiles")
    ).fetchall()

    profiles_touched = 0
    totals = {"renamed": 0, "inverted": 0, "dropped_shadowed": 0}
    for row_id, raw in rows:
        settings = json.loads(raw) if raw else {}
        new_settings, stats = rename_extent_params(settings)
        if not stats.touched:
            continue
        profiles_touched += 1
        totals["renamed"] += stats.renamed
        totals["inverted"] += stats.inverted
        totals["dropped_shadowed"] += stats.dropped_shadowed
        if dry_run:
            detail = ", ".join(
                f"{adv}:{n}" for adv, n in sorted(stats.per_advisory.items())
            )
            print(
                f"[093][dry-run] profile {row_id}: {stats.renamed} renamed "
                f"({detail}), {stats.inverted} inverted, "
                f"{stats.dropped_shadowed} shadowed"
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
        f"[093] rewrote extent params on {profiles_touched} profiles "
        f"({totals['renamed']} keys renamed, {totals['inverted']} values "
        f"inverted, {totals['dropped_shadowed']} shadowed old keys dropped)"
    )

    if dry_run:
        raise RuntimeError(
            "[093] EXTENT_RENAME_DRY_RUN=1 — aborting before commit; no rows "
            "written. This rolls back the entire `alembic upgrade` invocation; "
            "run only when 093 is the sole pending revision (else use "
            "scripts/extent_param_rename_dryrun.py)."
        )


def downgrade() -> None:
    """Reverse the rename, restoring the pre-consolidation keys and polarity.

    Genuinely reversible, unlike 079: nothing was deleted. Two edges are worth
    stating, neither of which loses a graded value:

    * A profile that already carried BOTH an old and a new key for one threshold
      had the old one dropped as shadowed on upgrade, so the downgrade restores a
      single key rather than the redundant pair. The new key was the one the
      engine would read either way.
    * ``icing_escape`` maps two old names onto one new one (the primary plus the
      read-path aliases ``route_pct_amber`` / ``min_route_pct``). Only the
      primary can be restored, so a profile that stored *only* an alias comes
      back under the primary name — the value round-trips exactly, but a DB diff
      will show it as a key rename rather than a pure revert.
    """
    from weatherbrief.analysis.advisories.extent_param_migration import (
        EXTENT_KEY_RENAMES,
        INVERTED_PCT_KEYS,
        SECONDARY_ALIASES,
    )

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, settings_json FROM flight_profiles")
    ).fetchall()

    restored = 0
    for row_id, raw in rows:
        settings = json.loads(raw) if raw else {}
        adv = settings.get("advisories")
        if not isinstance(adv, dict) or not isinstance(adv.get("params"), dict):
            continue
        changed = False
        for adv_id, renames in EXTENT_KEY_RENAMES.items():
            params = adv["params"].get(adv_id)
            if not isinstance(params, dict):
                continue
            inverted = INVERTED_PCT_KEYS.get(adv_id, set())
            secondary = SECONDARY_ALIASES.get(adv_id, set())
            # Reverse map, skipping the secondary aliases: an advisory maps two
            # old names onto one new one, and only the primary can be restored.
            # Scoped by advisory — ``route_pct_amber`` is a secondary alias for
            # icing_escape and the primary for turbulence.
            for old, new in renames.items():
                if old in secondary:
                    continue
                if new not in params:
                    continue
                value = params.pop(new)
                params[old] = 100.0 - value if old in inverted else value
                changed = True
                restored += 1
        if changed:
            conn.execute(
                sa.text(
                    "UPDATE flight_profiles SET settings_json = :s WHERE id = :id"
                ),
                {"s": json.dumps(settings), "id": row_id},
            )
    print(f"[093] downgrade restored {restored} pre-consolidation keys")
