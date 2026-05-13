"""Audit logging for the WeatherBrief API.

Provides:
- Structured audit logging for admin actions and security events
- Pack access pattern tracking

Note: Security headers (CSP, HSTS, X-Frame-Options, etc.) are handled by
Caddy at the reverse-proxy layer — see deploy/weather.flyfun.aero.caddy and
SECURITY_AUDIT.md §I4. Rate limiting and request validation middleware were
considered but deferred due to Starlette's BaseHTTPMiddleware buffering SSE
streaming responses.
"""

from __future__ import annotations

import logging

from starlette.requests import Request

# ---------------------------------------------------------------------------
# Structured security event logger
# ---------------------------------------------------------------------------

_audit_logger = logging.getLogger("weatherbrief.security.audit")
_access_logger = logging.getLogger("weatherbrief.security.access")


def client_ip(request: Request) -> str:
    """Extract the client IP from a request behind Caddy.

    Source priority is chosen so the value cannot be spoofed by a client
    sending arbitrary headers:

    1. ``X-Real-IP`` — Caddy sets this from the connection peer; clients
       cannot make Caddy override it.
    2. ``X-Forwarded-For`` rightmost — Caddy *appends* the real remote
       address to whatever the client sent, so the last comma-separated
       entry is the one Caddy added. The leftmost entries are
       client-controlled and unsafe to trust.
    3. ``request.client.host`` — fallback for direct connections (local
       dev), where no proxy header is present.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Audit logging helpers
# ---------------------------------------------------------------------------

def audit_admin_action(
    action: str,
    admin_id: str,
    request: Request,
    *,
    target_user_id: str | None = None,
    details: str = "",
) -> None:
    """Log a structured admin action for audit trail."""
    ip = client_ip(request)
    parts = [
        f"admin_action={action}",
        f"admin_id={admin_id}",
        f"ip={ip}",
    ]
    if target_user_id:
        parts.append(f"target_user={target_user_id}")
    if details:
        parts.append(f"details={details}")
    _audit_logger.info(" ".join(parts))


def audit_pack_access(
    user_id: str,
    flight_id: str,
    endpoint: str,
    request: Request,
) -> None:
    """Log pack/artifact access for pattern tracking."""
    ip = client_ip(request)
    _access_logger.info(
        "pack_access user=%s flight=%s endpoint=%s ip=%s",
        user_id, flight_id, endpoint, ip,
    )
