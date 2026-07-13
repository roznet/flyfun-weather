"""De-fossilize the DD engine-method seeds on template profiles (#410, part D).

The ``vfr_only`` / ``ifr_conservative`` system templates seeded every profile
they created with the pre-``001_method_defaults_v2`` engine triple
``(icing_method=ogimet_dd, cloud=dd, convective_method=thermo)`` — a fossil: the
account-level defaults moved to NWP long ago and nobody chose DD. This pins
**873 profiles (61% of prod)** to DD icing / DD cloud / thermo convective while
their cross-section displays NWP layers — the exact evidence-vs-grade split
#391/#393/#408 exist to kill.

The template configs (``configs/system_profiles.json``) drop the three keys in
the same change, so *new* profiles are born sparse and follow the declared NWP
:data:`ENGINE_METHOD_DEFAULTS`. This migration clears the fossil from *existing*
profiles so they follow the same default.

**The one grading change in #402** (isolated to this migration): the 873 fossil
profiles flip from DD/DD/thermo to NWP. It is a strict subset — a profile that
diverges from the seed in ANY of the three axes is a deliberate override and is
left untouched. Only ``vfr_only`` / ``ifr_conservative`` profiles carrying the
*exact* source-normalized fossil triple are cleared.

Anything else — the ``ifr_fiki`` all-NWP seeds, custom profiles, the 62 styled
``cloud_method`` values, the two genuinely-diverged template profiles — is
untouched here. The all-NWP de-seed (redundant-but-harmless keys equal to the
default) is #405's job and is byte-identical for grading.

Pure Python data update over the JSON ``settings_json`` Text column (cf.
migration 069) — raw SQL with named binds, dialect-agnostic (SQLite dev / MySQL
prod), no ALTER / batch mode. ``updated_at`` is deliberately left untouched: a
raw UPDATE does not fire the ORM ``onupdate`` hook, and this is a system-driven
correction, not a user edit.

Revision ID: 078
Revises: 077
Create Date: 2026-07-13
"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "078"
down_revision: Union[str, None] = "077"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The fossil seed shared by the vfr_only / ifr_conservative templates before
# their engine keys were de-seeded. Cloud is compared source-normalized (a
# styled ``square_dd`` still normalizes to the ``dd`` source).
_FOSSIL_TEMPLATES = {"vfr_only", "ifr_conservative"}
_ENGINE_KEYS = ("icing_method", "cloud_method", "cloud_source", "convective_method")


def _cloud_source(data: dict) -> str | None:
    """Source-normalized cloud choice: prefer ``cloud_source``, else reduce the
    legacy fused ``cloud_method`` (``*_nwp`` → nwp, any other non-empty → dd)."""
    source = data.get("cloud_source")
    if source is not None:
        return source
    cm = data.get("cloud_method")
    if not cm:
        return None
    return "nwp" if cm.endswith("nwp") else "dd"


def _is_fossil(data: dict) -> bool:
    """True when all three axes match the exact DD/DD/thermo fossil seed."""
    return (
        data.get("icing_method") == "ogimet_dd"
        and data.get("convective_method") == "thermo"
        and _cloud_source(data) == "dd"
    )


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, system_template_key, settings_json FROM flight_profiles"
        )
    ).fetchall()
    cleared = 0
    for row_id, template_key, raw in rows:
        if template_key not in _FOSSIL_TEMPLATES:
            continue
        data = json.loads(raw) if raw else {}
        if not _is_fossil(data):
            continue
        for key in _ENGINE_KEYS:
            data.pop(key, None)
        conn.execute(
            sa.text(
                "UPDATE flight_profiles SET settings_json = :s WHERE id = :id"
            ),
            {"s": json.dumps(data), "id": row_id},
        )
        cleared += 1
    # Alembic captures stdout in the migration log — record the blast radius.
    print(f"[078] cleared fossil engine methods from {cleared} template profiles")


def downgrade() -> None:
    """Irreversible: the fossil values were deleted, not archived, and a cleared
    profile is indistinguishable from a freshly-created (de-seeded) one. Re-adding
    the DD/DD/thermo triple would wrongly re-fossilize new profiles, so downgrade
    is a deliberate no-op — the grading change does not cleanly reverse."""
    pass
