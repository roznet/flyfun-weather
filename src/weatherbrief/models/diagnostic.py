"""Unified pipeline diagnostic record.

A single typed entry capturing one notable event during briefing generation —
warning, info, or error. Collected per-pipeline-run, persisted both into the
pack on disk (``fetch_meta.json``) and into the DB (``diagnostics_json``
column on ``BriefingPackRow``).

Design intent
-------------
- ``level`` + ``message`` are the user-facing surface (rendered in the
  freshness banner).
- ``stage`` + ``code`` are stable machine identifiers for log greps, metrics,
  and (future) localisation.
- ``detail`` is debug context — exception text, traceback excerpts, raw
  upstream payloads. Never shown to end users; capped and lightly redacted.
- ``error_id`` is a per-entry UUID so users can quote it back to support and
  we can correlate it with structured logs.

Backward compatibility
----------------------
DB rows written before this model existed only have ``{level, message}``.
All other fields are ``Optional`` with ``None`` defaults so legacy rows
validate cleanly. ``error_id`` and ``occurred_at`` are NOT generated at
construction by Pydantic (no ``default_factory``) — they are minted only via
:func:`Diagnostic.create` so legacy reads don't accidentally mint fresh IDs
that look like new data.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


# Cap detail strings — leaves comfortable headroom in MySQL TEXT columns
# (64KB) even when a row carries 10+ diagnostics.
DETAIL_MAX_BYTES = 4 * 1024  # 4 KB

# Patterns we redact from `detail` before persisting. Cheap defensive layer:
# tracebacks/repr() output occasionally include arg values that contain
# tokens. Not a substitute for actually structuring exceptions to avoid
# leaking secrets — just a guardrail.
_REDACTION_PATTERNS = [
    # Authorization headers / Bearer tokens
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[^\s'\"&]+"),
     r"\1<REDACTED>"),
    # Anthropic / OpenAI / generic API key prefixes — most-specific first
    # so the broader sk- rule doesn't shadow sk-ant-. Same replacement
    # either way, but the order keeps the rule list honest.
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "<REDACTED_API_KEY>"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "<REDACTED_API_KEY>"),
    # Common query-string secret params
    (re.compile(r"(?i)([?&](?:api[_-]?key|token|access[_-]?token)=)[^&\s]+"),
     r"\1<REDACTED>"),
]


def _redact(text: str) -> str:
    for pattern, replacement in _REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= DETAIL_MAX_BYTES:
        return text
    truncated = encoded[:DETAIL_MAX_BYTES].decode("utf-8", errors="ignore")
    return truncated + f"\n... [truncated, {len(encoded)} bytes total]"


DiagnosticLevel = Literal["info", "warn", "error"]
"""Severity convention used across the pipeline.

The frontend banner (``web/ts/managers/briefing-ui.ts``) shows ``warn`` and
``error`` entries; ``info`` is persisted but never surfaced to the user.
Pick the level based on **what the user can do about it**:

- **info** — Persisted for debugging only, not shown to the user.
  Use for: normal pipeline events ("ICON skipped, out of range"),
  internal misconfiguration the user can't fix ("server missing
  euro_aip dependency"), expected-state non-events.
- **warn** — Shown in the banner. Use for transient or retryable
  issues the user can act on by refreshing or waiting:
  "Anthropic API overloaded — try again in a few minutes",
  "GFS forecast fetch failed", "GRIB enrichment failed".
- **error** — Shown in the banner. Use for irrecoverable failures
  the user should know about even though they can't fix them:
  malformed input, server-side bugs that surfaced to a user request.
  Reach for ``warn`` first; ``error`` is for the rare case where
  retrying genuinely won't help.
"""


class Diagnostic(BaseModel):
    """One structured event from the briefing pipeline.

    Construct new entries via :meth:`create` so ``error_id`` and
    ``occurred_at`` are populated. Direct construction (or ``model_validate``)
    leaves them ``None`` — this is intentional and required for legacy DB row
    round-tripping.
    """

    level: DiagnosticLevel
    message: str
    stage: Optional[str] = None
    code: Optional[str] = None
    detail: Optional[str] = None
    request_id: Optional[str] = None
    error_id: Optional[UUID] = None
    occurred_at: Optional[datetime] = None

    model_config = {"extra": "ignore"}  # tolerate unknown fields in old rows

    @field_validator("detail", mode="before")
    @classmethod
    def _clean_detail(cls, v):
        if v is None:
            return None
        if not isinstance(v, str):
            v = str(v)
        return _truncate(_redact(v))

    @classmethod
    def create(
        cls,
        *,
        level: DiagnosticLevel,
        stage: str,
        code: str,
        message: str,
        detail: str | None = None,
        request_id: str | None = None,
    ) -> "Diagnostic":
        """Create a fresh diagnostic with auto-minted ``error_id`` and timestamp.

        Always use this for newly-emitted diagnostics. ``Diagnostic(...)``
        directly is reserved for round-tripping persisted records.
        """
        return cls(
            level=level,
            stage=stage,
            code=code,
            message=message,
            detail=detail,
            request_id=request_id,
            error_id=uuid4(),
            occurred_at=datetime.now(timezone.utc),
        )

    def to_public(self) -> "DiagnosticPublic":
        """Project to the public schema (omits debug-only fields).

        Use this whenever a diagnostic crosses the API boundary so we
        don't leak ``detail`` (stack traces, file paths) or
        ``request_id`` (Anthropic-internal correlation id) to clients.
        """
        return DiagnosticPublic(
            level=self.level,
            message=self.message,
            stage=self.stage,
            code=self.code,
            error_id=self.error_id,
            occurred_at=self.occurred_at,
        )


class DiagnosticPublic(BaseModel):
    """Wire-safe projection of :class:`Diagnostic` for API responses.

    Excludes ``detail`` (capped/redacted but still contains stack traces,
    file paths, library versions — debug-only by design) and
    ``request_id`` (Anthropic-internal correlation id, not user-actionable).

    ``error_id`` IS exposed: it's a per-entry UUID a user can quote back
    to support, with no information value beyond that.
    """

    level: DiagnosticLevel
    message: str
    stage: Optional[str] = None
    code: Optional[str] = None
    error_id: Optional[UUID] = None
    occurred_at: Optional[datetime] = None

    model_config = {"extra": "ignore"}
