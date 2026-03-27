"""Per-user data migrations for app_prefs_json.

Migrations run lazily when a user's preferences are read.  Each migration
is a function that receives the parsed prefs dict and returns the (possibly
modified) dict.  Applied migration keys are tracked in a
``_applied_migrations`` list inside the same JSON blob, so the marker and
the migrated data are written atomically.

To add a new migration:
    1. Write a ``def _migrate_NNN_description(prefs: dict) -> dict:`` function.
    2. Append ``("NNN_description", _migrate_NNN_description)`` to ``_MIGRATIONS``.
    3. The next time any user's preferences are loaded, the migration runs once.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from sqlalchemy.orm import Session

from flyfun_common.db.models import UserPreferencesRow

logger = logging.getLogger(__name__)

# Type alias for a migration function: receives prefs dict, returns updated prefs dict.
MigrationFn = Callable[[dict], dict]

# Ordered list of (key, function).  Append new migrations at the end.
_MIGRATIONS: list[tuple[str, MigrationFn]] = []


def _register(key: str) -> Callable[[MigrationFn], MigrationFn]:
    """Decorator to register a migration function."""
    def decorator(fn: MigrationFn) -> MigrationFn:
        _MIGRATIONS.append((key, fn))
        return fn
    return decorator


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

@_register("001_method_defaults_v2")
def _migrate_method_defaults_v2(prefs: dict) -> dict:
    """Upgrade legacy analysis method values to GRAMET-aligned defaults.

    dd       -> soft_nwp   (cloud method)
    nwp      -> soft_nwp   (cloud method)
    ogimet_dd -> ogimet_nwp (icing method)
    thermo   -> nwp        (convective method)
    """
    upgrades = {
        "cloud_method": {"dd": "soft_nwp", "nwp": "soft_nwp"},
        "icing_method": {"ogimet_dd": "ogimet_nwp"},
        "convective_method": {"thermo": "nwp"},
    }
    for key, mapping in upgrades.items():
        current = prefs.get(key)
        if current in mapping:
            prefs[key] = mapping[current]
    return prefs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_pending_migrations(db: Session, row: UserPreferencesRow) -> None:
    """Apply any pending per-user data migrations to *row*.

    Modifies ``row.app_prefs_json`` in place and flushes if anything changed.
    The caller is responsible for committing the transaction (FastAPI's
    ``get_db`` dependency commits on success).
    """
    try:
        prefs = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    except json.JSONDecodeError:
        prefs = {}

    # New user with no prefs yet — nothing to migrate, skip the DB write.
    if not prefs:
        return

    applied = set(prefs.get("_applied_migrations", []))

    changed = False
    for key, fn in _MIGRATIONS:
        if key not in applied:
            prefs = fn(prefs)
            applied.add(key)
            changed = True
            logger.info("Applied user migration %s for user %s", key, row.user_id)

    if changed:
        prefs["_applied_migrations"] = sorted(applied)
        row.app_prefs_json = json.dumps(prefs)
        db.flush()
