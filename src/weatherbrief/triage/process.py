"""Invoke Claude CLI to analyse and classify feedback items."""

from __future__ import annotations

import json
import logging
import os
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
    """Build the dict expected by load_prompt() from DB rows."""
    return {
        "category": fb.category or "",
        "comment": fb.comment or "",
        "user_email": user.email or "",
        "user_name": user.display_name or "",
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
        "--tools", "Read,Grep,Glob,Agent",
        "--model", "sonnet",
        "--max-turns", "10",
        "--max-budget-usd", "0.50",
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
        partial = (exc.stdout or "") + "\n" + (exc.stderr or "")
        if log_file:
            _write_log(log_file, partial)
        raise _TimeoutWithOutput(timeout, partial.strip()) from None

    elapsed = time.monotonic() - t0
    stdout = result.stdout

    if log_file:
        _write_log(log_file, stdout)

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: "
            f"{result.stderr[:500] if result.stderr else '(no stderr)'}"
        )

    return _parse_claude_output(stdout, elapsed)


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
        classification = outer

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
    logger.debug("Triage result: %.500s", result)
    fb.status = "ready"
    fb.classification = result.get("classification") if isinstance(result, dict) else None
    fb.ai_analysis = result.get("analysis") if isinstance(result, dict) else str(result)
    fb.admin_reply = result.get("suggested_response") if isinstance(result, dict) else None
    fb.confidence = result.get("confidence") if isinstance(result, dict) else None
    fb.processed_at = datetime.now(timezone.utc)


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
            try:
                prompt_dict = _feedback_to_prompt_dict(fb, user)
                prompt = load_prompt(prompt_dict)
                parsed = _run_claude(
                    prompt,
                    timeout=timeout,
                    worktree_path=worktree_path,
                    log_file=log_file,
                )
                processing_time = time.monotonic() - t0

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
                db.flush()
            except Exception as exc:
                logger.exception("Feedback #%d triage failed", fb.id)
                fb.admin_notes = f"Triage error: {str(exc)[:500]}"
                db.flush()

        db.commit()
        return processed
    finally:
        db.close()
