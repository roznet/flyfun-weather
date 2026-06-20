"""Invoke Claude CLI to analyse and classify feedback items."""

from __future__ import annotations

import json
import logging
import os
import pwd
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from flyfun_common.costs import record_cost
from flyfun_common.db import SessionLocal, get_engine
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FeedbackRow
from weatherbrief.triage.prompt import load_prompt
from weatherbrief.triage.security import scan_for_exfil

logger = logging.getLogger(__name__)

# JSON schema for structured output from Claude CLI
TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "classification": {
            "type": "string",
            "enum": [
                "BUG_FIXABLE",
                "RESPOND_ONLY",
                "NEEDS_INVESTIGATION",
                "DEFER_TO_HUMAN",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "analysis": {"type": "string"},
        "suggested_response": {"type": "string"},
        "relevant_files": {"type": "array", "items": {"type": "string"}},
        "component": {"type": "string"},
    },
    "required": [
        "classification",
        "confidence",
        "analysis",
        "suggested_response",
    ],
}

RATE_LIMIT_WINDOW_DAYS = 7
RATE_LIMIT_MAX = 10

SANDBOX_USER = "triage"
SANDBOX_BYPASS_ENV = "TRIAGE_ALLOW_UNSAFE"


def _assert_sandboxed() -> None:
    """Refuse to run unless we are the dedicated `triage` system user.

    The triage worker feeds attacker-controlled text into ``claude -p`` with
    Read/Grep/Glob tools enabled. If run from a normal developer account, the
    LLM's Read tool can reach ``.env``, ``~/.aws/credentials``, and any other
    absolute path the current UID can open. See designs/triage-sandbox.md.
    """
    user = pwd.getpwuid(os.geteuid()).pw_name
    if user == SANDBOX_USER:
        return
    if os.environ.get(SANDBOX_BYPASS_ENV):
        logger.warning(
            "Triage running as %r with %s set — skipping sandbox check. "
            "Only do this against a scratch DB with no real secrets reachable.",
            user, SANDBOX_BYPASS_ENV,
        )
        return
    raise RuntimeError(
        f"Refusing to run triage as user {user!r}. "
        f"Triage must run as the {SANDBOX_USER!r} system user from "
        f"/mnt/flyfun_data/sandboxes/triage (see designs/triage-sandbox.md). "
        f"Set {SANDBOX_BYPASS_ENV}=1 only for local dev against a scratch DB."
    )


def _check_rate_limit(db: Session, user_id: str) -> bool:
    """Return True if the user has exceeded the rate limit."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RATE_LIMIT_WINDOW_DAYS)
    count = (
        db.query(func.count(FeedbackRow.id))
        .filter(
            FeedbackRow.user_id == user_id,
            FeedbackRow.created_at > cutoff,
        )
        .scalar()
    )
    return count > RATE_LIMIT_MAX


def _feedback_to_prompt_dict(fb: FeedbackRow, user: UserRow) -> dict:
    """Build the dict expected by load_prompt() from DB rows.

    Deliberately omits the user's name and email: triage only needs the
    feedback content, and we do not send identifying PII to the LLM (see
    PRIVACY.md). ``user`` is still accepted for signature stability.
    """
    return {
        "category": fb.category or "",
        "comment": fb.comment or "",
        "flight_id": fb.flight_id or "",
        "pack_timestamp": fb.pack_timestamp.isoformat() if fb.pack_timestamp else "N/A",
        "feedback_created_at": fb.created_at.isoformat() if fb.created_at else "",
    }


def _run_claude(
    prompt: str,
    *,
    timeout: int,
    worktree_path: str,
    log_file: str | None = None,
) -> dict:
    """Invoke ``claude -p`` and return parsed output.

    Returns a dict with keys: result, cost_usd, duration_ms, num_turns,
    session_id.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format", "json",
        "--json-schema", json.dumps(TRIAGE_SCHEMA),
        # No Agent tool — prevents the triage LLM from spawning sub-agents
        # with different tool sets. Read/Grep/Glob is enough to investigate
        # a bug report against source.
        "--tools", "Read,Grep,Glob",
        "--model", "sonnet",
        "--max-turns", "20",
        "--max-budget-usd", "1.00",
        "--no-session-persistence",
    ]

    t0 = time.monotonic()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=worktree_path,
        )
    except subprocess.TimeoutExpired as exc:
        partial = ((exc.stdout or "") + "\n" + (exc.stderr or "")).strip()
        if log_file:
            _write_log(log_file, partial)
        raise _TimeoutWithOutput(timeout, partial) from None

    elapsed = time.monotonic() - t0
    stdout = result.stdout

    if log_file:
        _write_log(log_file, stdout)

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: "
            f"{result.stderr[:500] if result.stderr else '(no stderr)'}"
        )

    parsed = _parse_claude_output(stdout, elapsed)
    parsed["raw_response"] = stdout
    return parsed


def _write_log(path: str, content: str) -> None:
    """Write content to a log file."""
    with open(path, "w") as f:
        f.write(content)


class _TimeoutWithOutput(Exception):
    """Timeout that carries partial stdout for diagnostics."""

    def __init__(self, timeout_s: int, partial_output: str) -> None:
        self.timeout_s = timeout_s
        self.partial_output = partial_output
        super().__init__(f"Timeout after {timeout_s}s")


def _parse_claude_output(raw: str, elapsed_s: float) -> dict:
    """Parse Claude CLI JSON output, with fallback extraction."""
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        outer = _extract_json_fallback(raw)

    # Check for errors with no structured output (max-turns, budget exceeded, etc.)
    subtype = outer.get("subtype", "")
    is_error = outer.get("is_error", False)
    if subtype == "error_max_turns" or (is_error and "structured_output" not in outer and "result" not in outer):
        error_label = subtype or "error_unknown"
        logger.warning("Claude triage ended with error: %s (stop_reason=%s)", error_label, outer.get("stop_reason"))
        return {
            "result": {},
            "cost_usd": outer.get("total_cost_usd") or outer.get("cost_usd"),
            "duration_ms": outer.get("duration_ms") or int(elapsed_s * 1000),
            "num_turns": outer.get("num_turns"),
            "session_id": outer.get("session_id"),
            "error": error_label,
        }

    # --json-schema puts structured output in "structured_output", not "result"
    if "structured_output" in outer and isinstance(outer["structured_output"], dict):
        classification = outer["structured_output"]
    elif "result" in outer and isinstance(outer["result"], dict):
        classification = outer["result"]
    elif "result" in outer and isinstance(outer["result"], str):
        try:
            classification = json.loads(outer["result"])
        except json.JSONDecodeError:
            classification = _extract_json_fallback(outer["result"])
    else:
        logger.warning("Could not find structured output in Claude response (keys: %s)", list(outer.keys()))
        classification = {}

    return {
        "result": classification,
        "cost_usd": outer.get("total_cost_usd") or outer.get("cost_usd"),
        "duration_ms": outer.get("duration_ms") or int(elapsed_s * 1000),
        "num_turns": outer.get("num_turns"),
        "session_id": outer.get("session_id"),
    }


def _extract_json_fallback(text: str) -> dict:
    """Try to find a JSON object in free-form text."""
    # Look for ```json ... ``` blocks first
    m = re.search(r"```json\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))

    # Look for any { ... } block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))

    raise ValueError(f"Could not extract JSON from claude output ({len(text)} chars)")


def _apply_result(fb: FeedbackRow, parsed: dict) -> None:
    """Write classification results back to the feedback row."""
    result = parsed["result"]
    logger.debug("Triage result keys: %s", list(result.keys()) if isinstance(result, dict) else type(result))

    if not isinstance(result, dict) or not result.get("classification"):
        # No usable structured output — leave as pending with a note
        error = parsed.get("error", "no structured output")
        fb.admin_notes = f"AI triage incomplete: {error}"
        fb.processed_at = datetime.now(timezone.utc)
        logger.warning("Feedback #%d: triage produced no structured output (%s)", fb.id, error)
        return

    fb.status = "ready"
    fb.classification = result.get("classification")
    fb.ai_analysis = result.get("analysis")
    fb.admin_reply = result.get("suggested_response")
    fb.confidence = result.get("confidence")
    fb.processed_at = datetime.now(timezone.utc)

    exfil_hits = sorted(set(
        scan_for_exfil(fb.ai_analysis) + scan_for_exfil(fb.admin_reply)
    ))
    if exfil_hits:
        warning = f"⚠ exfil-scan flagged: {', '.join(exfil_hits)}"
        fb.admin_notes = f"{fb.admin_notes}\n\n{warning}" if fb.admin_notes else warning
        logger.warning("Feedback #%d triage output flagged: %s", fb.id, exfil_hits)


def _record_triage_cost(db: Session, fb: FeedbackRow, parsed: dict) -> None:
    """Record the Claude triage cost in the shared cost ledger."""
    cost_usd = parsed.get("cost_usd") or 0.0
    if cost_usd <= 0:
        return
    record_cost(
        db,
        user_id="system",
        service="flyfun-weather",
        action="triage",
        cost=cost_usd,
        category="support",
        description=f"AI triage for feedback #{fb.id}",
        metadata={
            "feedback_id": fb.id,
            "duration_ms": parsed.get("duration_ms"),
            "num_turns": parsed.get("num_turns"),
            "session_id": parsed.get("session_id"),
        },
        reference_id=str(fb.id),
    )


def process(
    *,
    n: int = 1,
    timeout: int = 300,
    dry_run: bool = False,
    feedback_id: int | None = None,
    log_file: str | None = None,
) -> int:
    """Process pending feedback items with Claude.

    If *feedback_id* is given, process that specific item (must be pending).
    Otherwise process up to *n* oldest pending items.

    Returns the number of items successfully processed.
    """
    _assert_sandboxed()

    worktree_path = os.environ.get(
        "TRIAGE_WORKTREE_PATH",
        os.environ.get("WORKING_DIR", os.getcwd()),
    )

    get_engine()
    db = SessionLocal()
    try:
        query = (
            db.query(FeedbackRow, UserRow)
            .join(UserRow, FeedbackRow.user_id == UserRow.id)
        )

        if feedback_id is not None:
            rows = query.filter(
                FeedbackRow.id == feedback_id,
                FeedbackRow.status == "pending",
            ).all()
            if not rows:
                logger.error("No pending feedback with id=%d", feedback_id)
                return 0
        else:
            rows = (
                query.filter(FeedbackRow.status == "pending")
                .order_by(FeedbackRow.created_at)
                .limit(n)
                .all()
            )

        if not rows:
            logger.info("No pending items to process")
            return 0

        processed = 0
        for fb, user in rows:
            # Rate-limit check
            if _check_rate_limit(db, fb.user_id):
                fb.status = "ignored"
                fb.admin_notes = "Auto-ignored: user exceeded rate limit"
                db.flush()
                logger.info("Ignored feedback #%d (rate limit for user %s)", fb.id, fb.user_id)
                continue

            if dry_run:
                logger.info("[dry-run] Would process feedback #%d", fb.id)
                processed += 1
                continue

            t0 = time.monotonic()
            prompt: str | None = None
            try:
                prompt_dict = _feedback_to_prompt_dict(fb, user)
                prompt = load_prompt(prompt_dict)
                fb.triage_prompt = prompt
                parsed = _run_claude(
                    prompt,
                    timeout=timeout,
                    worktree_path=worktree_path,
                    log_file=log_file,
                )
                processing_time = time.monotonic() - t0

                fb.triage_raw_response = parsed.get("raw_response")
                _apply_result(fb, parsed)
                _record_triage_cost(db, fb, parsed)
                db.flush()

                processed += 1
                logger.info(
                    "Processed feedback #%d → %s (%.1fs, $%.4f)",
                    fb.id,
                    parsed["result"].get("classification"),
                    processing_time,
                    parsed.get("cost_usd") or 0,
                )
            except _TimeoutWithOutput as exc:
                logger.warning("Feedback #%d timed out after %ds", fb.id, exc.timeout_s)
                fb.admin_notes = f"Triage timeout after {exc.timeout_s}s"
                fb.triage_raw_response = exc.partial_output or None
                db.flush()
            except Exception as exc:
                logger.exception("Feedback #%d triage failed", fb.id)
                fb.admin_notes = f"Triage error: {str(exc)[:500]}"
                db.flush()

        db.commit()
        return processed
    finally:
        db.close()
