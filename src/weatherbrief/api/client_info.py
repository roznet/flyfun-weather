"""Which client (iOS app, web browser, something else) sent a request.

Derived from the ``User-Agent`` header alone, so it needs no client change:
the iOS app talks through ``URLSession``, whose default agent is
``flyfun-weather/<build> CFNetwork/<v> Darwin/<v>``, and every browser —
including mobile Safari on an iPhone — sends a ``Mozilla/5.0 …`` agent. That
split is exactly the one we want: a pilot on their phone in Safari is a *web*
user, not an app user.
"""

from __future__ import annotations

from fastapi import Request

# Wide enough for a real browser agent (~150–200 chars) without letting a
# hostile client stuff an arbitrary blob into the column.
USER_AGENT_MAX_LEN = 256


def classify_user_agent(user_agent: str | None) -> str | None:
    """Return ``'ios'``, ``'web'`` or ``'other'``; ``None`` when there is no agent."""
    if not user_agent:
        return None
    if "CFNetwork/" in user_agent:
        return "ios"
    if user_agent.startswith("Mozilla/"):
        return "web"
    return "other"


def request_client(request: Request) -> tuple[str | None, str | None]:
    """``(client, truncated user agent)`` for ``request``."""
    user_agent = request.headers.get("user-agent") or None
    if user_agent:
        user_agent = user_agent[:USER_AGENT_MAX_LEN]
    return classify_user_agent(user_agent), user_agent
