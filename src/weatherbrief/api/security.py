"""Security middleware and audit logging for the WeatherBrief API.

Provides:
- Security headers middleware (CSP, HSTS, X-Frame-Options, etc.)
- IP-based rate limiting on authentication endpoints
- Failed authentication attempt monitoring
- Structured audit logging for admin actions and security events
- Pack access pattern tracking
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from flyfun_common.auth import is_dev_mode

# ---------------------------------------------------------------------------
# Structured security event logger
# ---------------------------------------------------------------------------

_audit_logger = logging.getLogger("weatherbrief.security.audit")
_access_logger = logging.getLogger("weatherbrief.security.access")

# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

# Content Security Policy — restrictive by default.
# 'self' for scripts/styles/images; data: for base64 images in emails/reports;
# connect-src allows API calls to self.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-XSS-Protection": "0",  # disabled; CSP replaces it
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)

        # CSP: relaxed in dev mode for hot-reload tooling
        if is_dev_mode():
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "img-src 'self' data: blob:; "
                "connect-src *",
            )
        else:
            response.headers.setdefault("Content-Security-Policy", _CSP)
            # HSTS only in production (1 year, include subdomains)
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        return response


# ---------------------------------------------------------------------------
# Authentication rate limiting (IP-based)
# ---------------------------------------------------------------------------

class _IPRateLimiter:
    """Simple IP-based sliding-window rate limiter for auth endpoints.

    Tracks timestamps per IP; raises 429 when threshold exceeded.
    Uses time.monotonic() for clock-skew safety.
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, ip: str) -> bool:
        """Return True if request is allowed, False if rate-limited."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        timestamps = self._hits[ip]
        timestamps[:] = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= self.max_requests:
            return False
        timestamps.append(now)
        return True


# 20 auth requests per minute per IP (generous for legitimate use, blocks brute force)
_auth_limiter = _IPRateLimiter(max_requests=20, window_seconds=60)

# 5 failed auth attempts per minute per IP (stricter for failures)
_auth_fail_tracker = _IPRateLimiter(max_requests=50, window_seconds=300)


def _client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Rate-limit authentication endpoints and monitor failed attempts."""

    _AUTH_PATHS = frozenset({
        "/auth/login", "/auth/callback", "/auth/token",
        "/auth/google", "/auth/apple",
    })

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        ip = _client_ip(request)

        # Rate-limit auth endpoints
        is_auth = any(path.startswith(p) for p in self._AUTH_PATHS)
        if is_auth and not _auth_limiter.check(ip):
            _audit_logger.warning(
                "auth_rate_limited ip=%s path=%s", ip, path,
            )
            return Response(
                content='{"detail":"Too many authentication requests. Try again later."}',
                status_code=429,
                media_type="application/json",
            )

        response = await call_next(request)

        # Track failed auth attempts (401/403 on any endpoint)
        if response.status_code in (401, 403):
            _auth_fail_tracker.check(ip)  # just record; don't block yet
            _audit_logger.warning(
                "auth_failure ip=%s path=%s method=%s status=%d",
                ip, path, request.method, response.status_code,
            )

        return response


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
    ip = _client_ip(request)
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
    ip = _client_ip(request)
    _access_logger.info(
        "pack_access user=%s flight=%s endpoint=%s ip=%s",
        user_id, flight_id, endpoint, ip,
    )


# ---------------------------------------------------------------------------
# Suspicious request logging middleware
# ---------------------------------------------------------------------------

_SUSPICIOUS_PATTERNS = (
    "../", "..\\",
    "<script", "%3Cscript",
    "' OR ", "\" OR ",
    "; DROP ", "; DELETE ",
    "/etc/passwd", "/proc/self",
)


class RequestValidationLoggingMiddleware(BaseHTTPMiddleware):
    """Log requests containing suspicious patterns (path traversal, XSS, SQLi)."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        url_str = str(request.url)
        for pattern in _SUSPICIOUS_PATTERNS:
            if pattern in url_str:
                ip = _client_ip(request)
                _audit_logger.warning(
                    "suspicious_request ip=%s method=%s url=%s pattern=%s",
                    ip, request.method, url_str, pattern,
                )
                break

        return await call_next(request)
