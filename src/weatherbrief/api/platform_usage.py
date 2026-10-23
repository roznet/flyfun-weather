"""Which platforms each user uses, per UTC day (``user_platform_days``).

``PlatformUsageMiddleware`` records ``ios`` / ``android`` / ``web`` for every
request a first-party session authenticated: flyfun-common's auth marks the
ASGI scope with the user (``mark_session_authenticated``), and this outer
middleware reads that mark once the response has been sent, so it costs the
client nothing and needs no per-endpoint wiring. API tokens are never marked
(``mcp`` is recorded by the tool-call endpoint instead), and passive paths
(flyfun-common's ``register_passive_paths``, e.g. the background badge sync)
don't count as using the app.

Each (day, user, platform) is written once per process: an in-memory set
skips the rest, and a duplicate after a restart hits the unique constraint
and is ignored. Never raises — usage stats must not fail a request.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from anyio import to_thread
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from flyfun_common.auth.middleware import _AUTHENTICATED_SUB_KEY
from flyfun_common.db.deps import _is_passive
from weatherbrief.api.client_info import classify_user_agent
from weatherbrief.db.models import UserPlatformDayRow

logger = logging.getLogger(__name__)

#: Platforms the session middleware records; ``mcp`` comes from the MCP log.
SESSION_PLATFORMS = ("ios", "android", "web")

_seen_day: date | None = None
_seen: set[tuple[str, str]] = set()


def record_platform_day(db: Session, user_id: str, platform: str, day: date | None = None) -> None:
    """Note that ``user_id`` used ``platform`` on ``day`` (default today, UTC).

    Runs in a savepoint of ``db``; the caller commits.
    """
    global _seen_day
    day = day or datetime.now(timezone.utc).date()
    if day != _seen_day:
        _seen.clear()
        _seen_day = day
    key = (user_id, platform)
    if key in _seen:
        return
    try:
        with db.begin_nested():
            db.add(UserPlatformDayRow(day=day, user_id=user_id, platform=platform))
    except IntegrityError:
        pass  # already recorded (another process, or before a restart)
    _seen.add(key)


def _record_in_own_session(user_id: str, platform: str) -> None:
    from flyfun_common.db import SessionLocal

    try:
        with SessionLocal() as db:
            record_platform_day(db, user_id, platform)
            db.commit()
    except Exception:  # noqa: BLE001 - stats must never fail a request
        logger.warning("platform-day record failed", exc_info=True)


class PlatformUsageMiddleware:
    """Record (user, platform, day) for session-authenticated requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)
        if scope["type"] != "http":
            return
        user_id = scope.get(_AUTHENTICATED_SUB_KEY)
        if not user_id:
            return
        request = Request(scope)
        platform = classify_user_agent(request.headers.get("user-agent"))
        if platform not in SESSION_PLATFORMS or _is_passive(request):
            return
        if (user_id, platform) in _seen and _seen_day == datetime.now(timezone.utc).date():
            return  # skip the thread hop on the common path
        await to_thread.run_sync(_record_in_own_session, user_id, platform)
