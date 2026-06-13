"""Push pilot digest thumb ratings to LangSmith as run feedback (issue #244).

The digest LLM call is traced in LangSmith (enabled in prod, off locally) under
a controlled ``run_id`` that ``run_digest`` generates and the pipeline persists
on the pack (``BriefingPackRow.digest_trace_id``). When a pilot rates the digest
👍/👎, we attach that as feedback on the run so digest quality can be reviewed
and evaluated in LangSmith alongside the actual model run that produced it.

Best-effort by design: a no-op when LangSmith isn't configured (so local/dev
POSTs aren't blocked by a ``Client()`` that would error), and never raises into
the caller's request path — a LangSmith hiccup must not fail the user's POST.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _tracing_configured() -> bool:
    """True when LangSmith is set up to receive feedback.

    Mirrors the env vars LangChain itself reads for the API key. Locally these
    are unset (``.env.sample`` ships ``LANGCHAIN_TRACING_V2=false`` next to a
    blank key), so we skip constructing a ``Client()`` that would otherwise
    raise for a missing key.
    """
    return bool(os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY"))


def push_digest_thumb_feedback(
    *,
    run_id: str | None,
    sentiment: str,
    comment: str | None = None,
) -> None:
    """Attach a 👍/👎 digest rating to its LangSmith run (fire-and-forget).

    Maps ``sentiment`` → score (``up`` = 1.0, ``down`` = 0.0). No-op when the
    pack has no ``run_id`` (legacy/old pack, or digest never ran) or when
    LangSmith isn't configured. Swallows every error so feedback submission
    stays unaffected when the call fails.
    """
    if not run_id:
        return
    if not _tracing_configured():
        logger.debug("LangSmith not configured — skipping digest feedback push")
        return
    try:
        from langsmith import Client

        score = 1.0 if sentiment == "up" else 0.0
        Client().create_feedback(
            run_id,
            key="user_thumb",
            score=score,
            comment=comment or None,
        )
        logger.info(
            "Pushed digest thumb feedback to LangSmith run %s (score=%.1f)",
            run_id, score,
        )
    except Exception:
        logger.warning(
            "Failed to push digest feedback to LangSmith (run_id=%s)",
            run_id, exc_info=True,
        )
