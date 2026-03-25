"""API endpoints for system messages (What's New / announcements)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])

_MESSAGES_FILE = Path(__file__).resolve().parent.parent / "messages.json"


class SystemMessage(BaseModel):
    id: str
    date: str
    title: str
    body: str
    category: str  # feature, change, fix


class MessagesStatus(BaseModel):
    unseen_count: int
    latest_message_date: str | None


def _load_messages() -> list[dict]:
    """Load messages from the static JSON file."""
    try:
        return json.loads(_MESSAGES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Failed to load messages from %s", _MESSAGES_FILE)
        return []


def _get_messages_seen_at(row: UserPreferencesRow | None) -> str | None:
    """Extract messages_seen_at from user preferences."""
    if not row or not row.app_prefs_json:
        return None
    try:
        data = json.loads(row.app_prefs_json)
        return data.get("messages_seen_at")
    except json.JSONDecodeError:
        return None


def _count_unseen(messages: list[dict], seen_at: str | None) -> int:
    """Count messages newer than the seen_at timestamp."""
    if not seen_at:
        return len(messages)
    return sum(1 for m in messages if m.get("date", "") > seen_at)


@router.get("", response_model=list[SystemMessage])
def get_messages():
    """Return all system messages (public, no auth required)."""
    return _load_messages()


@router.get("/status", response_model=MessagesStatus)
def get_messages_status(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return unseen message count for the current user."""
    messages = _load_messages()
    row = db.get(UserPreferencesRow, user_id)
    seen_at = _get_messages_seen_at(row)
    latest = max((m.get("date", "") for m in messages), default=None) if messages else None
    return MessagesStatus(
        unseen_count=_count_unseen(messages, seen_at),
        latest_message_date=latest,
    )


@router.post("/seen", status_code=204)
def mark_messages_seen(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Mark all messages as seen (sets messages_seen_at to today)."""
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        row = UserPreferencesRow(user_id=user_id)
        db.add(row)
        db.flush()

    try:
        data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    except json.JSONDecodeError:
        data = {}

    data["messages_seen_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row.app_prefs_json = json.dumps(data)
