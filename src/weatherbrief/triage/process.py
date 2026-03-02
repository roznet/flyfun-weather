"""Invoke Claude CLI to analyse and classify triage queue items."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone

from weatherbrief.triage.db import connect, log_action
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


def _check_rate_limit(conn: sqlite3.Connection, user_id: str) -> bool:
    """Return True if the user has exceeded the rate limit."""
    row = conn.execute(
        """\
        SELECT feedback_count, first_feedback_at, last_feedback_at
        FROM user_rate WHERE user_id = ?""",
        (user_id,),
    ).fetchone()
    if row is None:
        return False

    last = row["last_feedback_at"]
    first = row["first_feedback_at"]
    if not last or not first:
        return False

    first_dt = datetime.fromisoformat(first)
    last_dt = datetime.fromisoformat(last)
    window_days = (last_dt - first_dt).days
    if window_days <= RATE_LIMIT_WINDOW_DAYS and row["feedback_count"] > RATE_LIMIT_MAX:
        return True
    return False


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


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

    When *log_file* is set, stdout and stderr are streamed to that file
    in real time (suitable for ``tail -f``).
    """
    output_format = "stream-json" if log_file else "json"
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format", output_format,
        "--json-schema", json.dumps(TRIAGE_SCHEMA),
        "--tools", "Read,Grep,Glob,Agent",
        "--model", "sonnet",
        "--max-turns", "10",
        "--max-budget-usd", "0.50",
        "--no-session-persistence",
    ]

    t0 = time.monotonic()

    if log_file:
        fh = open(log_file, "w", buffering=1)  # noqa: SIM115  # line-buffered for tail -f
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=worktree_path,
            )
            # Stream lines to log file in real time, keep last result line
            result_line = None
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    fh.write(line)
                    fh.flush()
                    stripped = line.strip()
                    if stripped:
                        result_line = stripped
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                partial = _read_partial(log_file)
                raise _TimeoutWithOutput(timeout, partial) from None
        finally:
            fh.close()

        elapsed = time.monotonic() - t0

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode} (see {log_file})"
            )

        # The last line of stream-json is the result event
        stdout = result_line or _read_partial(log_file)
    else:
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
            raise _TimeoutWithOutput(timeout, partial.strip()) from None

        elapsed = time.monotonic() - t0
        stdout = result.stdout

        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {result.returncode}: "
                f"{result.stderr[:500] if result.stderr else '(no stderr)'}"
            )

    return _parse_claude_output(stdout, elapsed)


def _read_partial(path: str, max_bytes: int = 50_000) -> str:
    """Read up to *max_bytes* from the end of a file."""
    try:
        with open(path) as f:
            content = f.read()
        if len(content) > max_bytes:
            return content[-max_bytes:]
        return content
    except OSError:
        return ""


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


def _update_queue_result(
    conn: sqlite3.Connection,
    queue_id: int,
    parsed: dict,
    processing_time_s: float,
) -> None:
    """Write classification results back to the queue row."""
    result = parsed["result"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """\
        UPDATE queue SET
            status             = 'completed',
            classification     = ?,
            analysis           = ?,
            suggested_response = ?,
            confidence         = ?,
            processed_at       = ?,
            processing_time_s  = ?,
            claude_cost_usd    = ?,
            claude_duration_ms = ?,
            claude_num_turns   = ?,
            claude_session_id  = ?
        WHERE id = ?""",
        (
            result.get("classification"),
            result.get("analysis"),
            result.get("suggested_response"),
            result.get("confidence"),
            now,
            processing_time_s,
            parsed.get("cost_usd"),
            parsed.get("duration_ms"),
            parsed.get("num_turns"),
            parsed.get("session_id"),
            queue_id,
        ),
    )
    conn.commit()


def _mark_failed(
    conn: sqlite3.Connection,
    queue_id: int,
    error: str,
) -> None:
    """Mark a queue item as failed."""
    conn.execute(
        "UPDATE queue SET status = 'failed', error_message = ? WHERE id = ?",
        (error[:2000], queue_id),
    )
    conn.commit()
    log_action(conn, queue_id, "failed", error[:500])


def process(
    *,
    n: int = 1,
    timeout: int = 300,
    dry_run: bool = False,
    app_feedback_id: int | None = None,
    log_file: str | None = None,
) -> int:
    """Process pending items from the triage queue.

    If *app_feedback_id* is given, process that specific item (must be
    pending).  Otherwise process up to *n* oldest pending items.

    When *log_file* is set, Claude's raw output is streamed there
    so you can ``tail -f`` it while processing runs.

    Returns the number of items successfully processed.
    """
    worktree_path = os.environ.get(
        "TRIAGE_WORKTREE_PATH",
        os.environ.get("WORKING_DIR", os.getcwd()),
    )

    conn = connect()
    try:
        if app_feedback_id is not None:
            rows = conn.execute(
                "SELECT * FROM queue WHERE app_feedback_id = ? AND status = 'pending'",
                (app_feedback_id,),
            ).fetchall()
            if not rows:
                logger.error(
                    "No pending item with app_feedback_id=%d", app_feedback_id
                )
                return 0
        else:
            rows = conn.execute(
                "SELECT * FROM queue WHERE status = 'pending' ORDER BY id LIMIT ?",
                (n,),
            ).fetchall()

        if not rows:
            logger.info("No pending items to process")
            return 0

        processed = 0
        for row in rows:
            item = _row_to_dict(row)
            queue_id = item["id"]

            # Rate-limit check
            if _check_rate_limit(conn, item["user_id"]):
                conn.execute(
                    "UPDATE queue SET status = 'deferred' WHERE id = ?",
                    (queue_id,),
                )
                conn.commit()
                log_action(conn, queue_id, "deferred", "rate limit exceeded")
                logger.info("Deferred item %d (rate limit)", queue_id)
                continue

            if dry_run:
                logger.info("[dry-run] Would process item %d", queue_id)
                processed += 1
                continue

            # Mark processing
            conn.execute(
                "UPDATE queue SET status = 'processing' WHERE id = ?",
                (queue_id,),
            )
            conn.commit()
            log_action(conn, queue_id, "processing_started")

            t0 = time.monotonic()
            try:
                prompt = load_prompt(item)
                parsed = _run_claude(
                    prompt,
                    timeout=timeout,
                    worktree_path=worktree_path,
                    log_file=log_file,
                )
                processing_time = time.monotonic() - t0

                _update_queue_result(conn, queue_id, parsed, processing_time)
                log_action(
                    conn,
                    queue_id,
                    "completed",
                    parsed["result"].get("classification"),
                )
                processed += 1
                logger.info(
                    "Processed item %d → %s (%.1fs)",
                    queue_id,
                    parsed["result"].get("classification"),
                    processing_time,
                )
            except _TimeoutWithOutput as exc:
                detail = f"Timeout after {exc.timeout_s}s"
                if exc.partial_output:
                    detail += f"\n--- partial output ---\n{exc.partial_output[-2000:]}"
                _mark_failed(conn, queue_id, detail)
                logger.warning("Item %d timed out", queue_id)
            except Exception as exc:
                _mark_failed(conn, queue_id, str(exc))
                logger.exception("Item %d failed", queue_id)

        return processed
    finally:
        conn.close()
