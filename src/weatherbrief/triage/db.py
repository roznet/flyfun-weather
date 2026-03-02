"""Triage SQLite database — schema, connection, and helpers.

Separate from the main app DB.  Uses plain sqlite3 (no ORM overhead
for this small, self-contained schema).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app_feedback_id INTEGER NOT NULL UNIQUE,
    user_email      TEXT    NOT NULL DEFAULT '',
    user_name       TEXT    NOT NULL DEFAULT '',
    user_id         TEXT    NOT NULL DEFAULT '',
    flight_id       TEXT    NOT NULL DEFAULT '',
    pack_timestamp  TEXT,
    category        TEXT    NOT NULL DEFAULT '',
    comment         TEXT    NOT NULL DEFAULT '',
    feedback_created_at TEXT NOT NULL,

    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','processing','completed','failed','deferred')),
    classification  TEXT
                    CHECK(classification IS NULL OR classification IN (
                        'BUG_FIXABLE','RESPOND_ONLY','NEEDS_INVESTIGATION','DEFER_TO_HUMAN')),
    analysis        TEXT,
    suggested_response TEXT,
    confidence      REAL,

    processed_at    TEXT,
    processing_time_s REAL,
    claude_cost_usd   REAL,
    claude_duration_ms INTEGER,
    claude_num_turns   INTEGER,
    claude_session_id  TEXT,
    error_message      TEXT,

    ingested_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS processing_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id  INTEGER NOT NULL REFERENCES queue(id),
    action    TEXT    NOT NULL,
    detail    TEXT,
    timestamp TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS user_rate (
    user_id            TEXT PRIMARY KEY,
    feedback_count     INTEGER NOT NULL DEFAULT 0,
    first_feedback_at  TEXT,
    last_feedback_at   TEXT,
    flagged            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_user   ON queue(user_id);
CREATE INDEX IF NOT EXISTS idx_log_queue    ON processing_log(queue_id);
"""

_UPDATED_AT_TRIGGER = """\
CREATE TRIGGER IF NOT EXISTS trg_queue_updated_at
AFTER UPDATE ON queue
BEGIN
    UPDATE queue SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
    WHERE id = NEW.id;
END;
"""


def _default_db_path() -> str:
    data_dir = os.environ.get("DATA_DIR", "data")
    return os.path.join(data_dir, "triage.db")


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open (and initialise if needed) the triage database."""
    path = db_path or os.environ.get("TRIAGE_DB_PATH") or _default_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    _ensure_schema(conn)
    logger.info("Triage DB opened: %s", path)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist and check schema version."""
    conn.executescript(_SCHEMA_SQL)
    conn.executescript(_UPDATED_AT_TRIGGER)

    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        conn.commit()
    elif row["value"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"Triage DB schema version mismatch: "
            f"expected {SCHEMA_VERSION}, got {row['value']}"
        )


def log_action(
    conn: sqlite3.Connection,
    queue_id: int,
    action: str,
    detail: str | None = None,
) -> None:
    """Insert an audit-trail entry into processing_log."""
    conn.execute(
        "INSERT INTO processing_log (queue_id, action, detail) VALUES (?, ?, ?)",
        (queue_id, action, detail),
    )
    conn.commit()
