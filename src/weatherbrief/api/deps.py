"""Shared FastAPI dependency functions for the API package.

Extracted to avoid duplication between modules that need the same
app-state values (the `Request`-keyed accessors). Add new accessors
here when more than one router needs them — single-use accessors can
stay private to their module.

Also home to ``current_user_id_short``, the auth dependency for endpoints that
must not hold a pooled DB connection while they wait (#719).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from flyfun_common.db import SessionLocal, current_user_id


def airports_db(request: Request) -> str:
    """Path to the euro_aip airports SQLite database."""
    return request.app.state.db_path


def data_dir(request: Request) -> Path:
    """Shared data directory (DATA_DIR env var). Houses GRIB cache,
    pack snapshots, and other per-deployment artifacts."""
    return request.app.state.data_dir


def current_user_id_short(request: Request) -> str:
    """``current_user_id`` without holding a pooled connection for the request.

    flyfun-common's ``current_user_id`` takes ``Depends(get_db)``. ``get_db``
    is a generator dependency, and FastAPI runs its teardown only after the
    response has been sent, so that session stays checked out for the whole
    request, not just the user lookup. On an endpoint that then waits on
    something slow (an upstream fetch, a render, a large file transfer) that
    is one pooled connection per in-flight request. The engine keeps
    SQLAlchemy's default pool (5 + 10 overflow), so a map pan firing dozens of
    tile requests exhausted it and every other endpoint 500'd for minutes
    (#719).

    This runs the exact same checks (token / cookie decode, API-token
    revocation and expiry, scope, approval, session-epoch revocation) on a
    session it owns and closes before returning. Same pattern as
    ``/airport-profile`` and ``/refresh/stream``.

    Use it on endpoints that do no other DB work. An endpoint that also takes
    ``Depends(get_db)`` should keep ``current_user_id``: the two share the
    request-cached session there, so this would only add a second checkout.

    Tests override this dependency, not ``current_user_id``, on the endpoints
    that use it: overriding ``current_user_id`` has no effect here because it
    is called directly rather than resolved through ``Depends``.
    """
    db = SessionLocal()
    try:
        # Every argument is passed explicitly: no ``Depends`` default leaks in.
        user_id = current_user_id(request, db)
        # Persist what get_db would have committed (an API token's last_used_at).
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return user_id
