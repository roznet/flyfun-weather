"""Database package — shared models from flyfun_common + app-specific models.

Re-exports common DB utilities so existing code can import from weatherbrief.db.
Deps (get_db, current_user_id) should be imported directly from flyfun_common.db.
"""

from flyfun_common.db import (
    Base,
    SessionLocal,
    get_engine,
    init_shared_db,
    ensure_dev_user,
    DEV_USER_ID,
)

__all__ = [
    "Base",
    "SessionLocal",
    "get_engine",
    "init_shared_db",
    "ensure_dev_user",
    "DEV_USER_ID",
]
