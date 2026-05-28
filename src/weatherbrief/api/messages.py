"""API endpoints for system messages (What's New / announcements)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow

from weatherbrief.api.admin import require_admin
from weatherbrief.db.models import SystemMessageRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])
admin_router = APIRouter(prefix="/admin/messages", tags=["admin"])

MessageCategory = Literal["feature", "change", "fix"]


# --- Pydantic models ---

class SystemMessage(BaseModel):
    id: int
    date: str
    title: str
    body: str
    category: str
    highlight: bool


class MessagesStatus(BaseModel):
    unseen_count: int
    latest_message_date: str | None


class MessageCreate(BaseModel):
    date: str  # YYYY-MM-DD
    title: str
    body: str
    category: MessageCategory = "feature"
    highlight: bool = False


class MessageUpdate(BaseModel):
    date: str | None = None
    title: str | None = None
    body: str | None = None
    category: MessageCategory | None = None
    highlight: bool | None = None


# --- Helpers ---

def _get_last_seen_id(row: UserPreferencesRow | None) -> int:
    """Extract messages_last_seen_id from user preferences. Returns 0 if unset."""
    if not row or not row.app_prefs_json:
        return 0
    try:
        data = json.loads(row.app_prefs_json)
        return int(data.get("messages_last_seen_id", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return 0


def _row_to_response(r: SystemMessageRow) -> SystemMessage:
    return SystemMessage(
        id=r.id, date=r.date, title=r.title, body=r.body,
        category=r.category, highlight=r.highlight,
    )


# --- Public endpoints ---

@router.get("", response_model=list[SystemMessage])
def get_messages(db: Session = Depends(get_db)):
    """Return all system messages, newest first."""
    rows = db.query(SystemMessageRow).order_by(desc(SystemMessageRow.date), desc(SystemMessageRow.id)).all()
    return [_row_to_response(r) for r in rows]


@router.get("/status", response_model=MessagesStatus)
def get_messages_status(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return unseen message count for the current user."""
    prefs_row = db.get(UserPreferencesRow, user_id)
    last_seen_id = _get_last_seen_id(prefs_row)
    # Only highlighted messages light the notification dot. Non-highlighted
    # releases still appear in the stream but never bump the unseen count.
    unseen_count = db.query(func.count(SystemMessageRow.id)).filter(
        SystemMessageRow.id > last_seen_id,
        SystemMessageRow.highlight.is_(True),
    ).scalar() or 0
    latest = db.query(func.max(SystemMessageRow.date)).scalar()
    return MessagesStatus(
        unseen_count=unseen_count,
        latest_message_date=latest,
    )


@router.post("/seen", status_code=204)
def mark_messages_seen(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Mark all current messages as seen (stores max message ID)."""
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        row = UserPreferencesRow(user_id=user_id)
        db.add(row)
        db.flush()

    max_id = db.query(func.max(SystemMessageRow.id)).scalar() or 0

    try:
        data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    except json.JSONDecodeError:
        data = {}

    data["messages_last_seen_id"] = max_id
    row.app_prefs_json = json.dumps(data)


# --- Admin CRUD ---

@admin_router.get("", response_model=list[SystemMessage])
def admin_list_messages(
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all system messages (admin)."""
    rows = db.query(SystemMessageRow).order_by(desc(SystemMessageRow.date), desc(SystemMessageRow.id)).all()
    return [_row_to_response(r) for r in rows]


@admin_router.post("", response_model=SystemMessage, status_code=201)
def admin_create_message(
    body: MessageCreate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new system message (admin)."""
    row = SystemMessageRow(
        date=body.date,
        title=body.title,
        body=body.body,
        category=body.category,
        highlight=body.highlight,
    )
    db.add(row)
    db.flush()
    return _row_to_response(row)


@admin_router.put("/{message_id}", response_model=SystemMessage)
def admin_update_message(
    message_id: int,
    body: MessageUpdate,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update a system message (admin)."""
    row = db.get(SystemMessageRow, message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    if body.date is not None:
        row.date = body.date
    if body.title is not None:
        row.title = body.title
    if body.body is not None:
        row.body = body.body
    if body.category is not None:
        row.category = body.category
    if body.highlight is not None:
        row.highlight = body.highlight
    db.flush()
    return _row_to_response(row)


@admin_router.delete("/{message_id}", status_code=204)
def admin_delete_message(
    message_id: int,
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a system message (admin)."""
    row = db.get(SystemMessageRow, message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Message not found")
    db.delete(row)
