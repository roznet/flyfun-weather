"""Exception classification for the digest stage.

Lives in its own module so it can be imported from both the heavyweight
LLM-call path (``llm_digest.py``, which pulls langchain/langgraph) and
the lightweight outer pipeline glue (``tasks/outputs.py``) without
forcing the latter to load LLM dependencies at module import time.
"""

from __future__ import annotations

import traceback

from weatherbrief.models import Diagnostic, DigestCode


def classify_llm_exception(exc: Exception) -> Diagnostic:
    """Map any digest-stage exception to a typed Diagnostic.

    Two call sites:

    1. ``briefer_node`` (in ``llm_digest.py``) wraps the actual LLM
       ``invoke()`` call. The Anthropic exception hierarchy (status
       codes, connection/timeout, base ``APIError``) is checked from
       most specific to most general; each kind gets a stable
       :class:`DigestCode` and a friendly user-facing message.
    2. ``run_llm_digest`` outer ``except`` (in ``tasks/outputs.py``)
       catches everything *outside* the LLM call (config load, context
       build, file-write). These are not Anthropic exceptions, so they
       fall through to ``DIGEST_UNKNOWN`` — also a useful default.

    The raw exception goes into ``detail`` (capped and redacted at the
    model boundary), and ``request_id`` is captured when the SDK
    exposes it (Anthropic ``APIStatusError`` subclasses).
    """
    request_id: str | None = None
    try:
        import anthropic  # local — module is heavy and only needed when classifying
    except ImportError:
        anthropic = None  # type: ignore[assignment]

    # Use the explicit (type, value, traceback) form so we capture the trace
    # of the *given* exception regardless of whether we're in an active
    # except block. ``format_exc()`` would silently return "NoneType: None\n"
    # if called outside an except — fragile.
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    if anthropic is not None:
        # Best-effort request id (present on APIStatusError subclasses)
        request_id = getattr(exc, "request_id", None)

        if (
            isinstance(exc, anthropic.APIStatusError)
            and getattr(exc, "status_code", None) == 529
        ):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_OVERLOADED,
                message="AI weather digest unavailable — Anthropic API overloaded. Try refreshing again shortly.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.RateLimitError):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_RATE_LIMITED,
                message="AI weather digest unavailable — rate-limited by Anthropic. Try refreshing again in a moment.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.APITimeoutError):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_TIMEOUT,
                message="AI weather digest unavailable — Anthropic API timed out. Try refreshing again.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.APIConnectionError):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_CONNECTION,
                message="AI weather digest unavailable — could not reach Anthropic API. Try refreshing again.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(
            exc,
            (anthropic.AuthenticationError, anthropic.PermissionDeniedError),
        ):
            # 401/403 — the server's API key was rejected (rotated, expired,
            # revoked, or misconfigured). Retrying won't help; the team
            # needs to act. Don't echo "API key rejected" in the user
            # message — that leaks server-side state.
            return Diagnostic.create(
                level="error", stage="digest",
                code=DigestCode.ANTHROPIC_AUTH_ERROR,
                message="AI weather digest unavailable — internal authentication issue. The team has been notified.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.BadRequestError):
            # 400 from Anthropic — usually a server-side prompt/config bug;
            # retrying won't help.
            return Diagnostic.create(
                level="error", stage="digest",
                code=DigestCode.DIGEST_BAD_REQUEST,
                message="AI weather digest unavailable — internal request error.",
                detail=detail,
                request_id=request_id,
            )
        if isinstance(exc, anthropic.InternalServerError) or (
            isinstance(exc, anthropic.APIStatusError)
            and 500 <= (getattr(exc, "status_code", 0) or 0) < 600
        ):
            return Diagnostic.create(
                level="warn", stage="digest",
                code=DigestCode.ANTHROPIC_INTERNAL_ERROR,
                message="AI weather digest unavailable — Anthropic API error. Try refreshing again in a few minutes.",
                detail=detail,
                request_id=request_id,
            )

    return Diagnostic.create(
        level="error", stage="digest",
        code=DigestCode.DIGEST_UNKNOWN,
        message="AI weather digest unavailable — unexpected error. Try refreshing again later.",
        detail=detail,
        request_id=request_id,
    )
