"""API endpoints for system messages (What's New / announcements)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow

from weatherbrief.api.admin import require_admin
from weatherbrief.db.models import SystemMessageRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])
admin_router = APIRouter(prefix="/admin/messages", tags=["admin"])


# --- Pydantic models ---

class SystemMessage(BaseModel):
    id: int
    date: str
    title: str
    body: str
    category: str  # feature, change, fix


class MessagesStatus(BaseModel):
    unseen_count: int
    latest_message_date: str | None


class MessageCreate(BaseModel):
    date: str  # YYYY-MM-DD
    title: str
    body: str
    category: str = "feature"


class MessageUpdate(BaseModel):
    date: str | None = None
    title: str | None = None
    body: str | None = None
    category: str | None = None


# --- Helpers ---

def _get_messages_seen_at(row: UserPreferencesRow | None) -> str | None:
    """Extract messages_seen_at from user preferences."""
    if not row or not row.app_prefs_json:
        return None
    try:
        data = json.loads(row.app_prefs_json)
        return data.get("messages_seen_at")
    except json.JSONDecodeError:
        return None


def _count_unseen(messages: list[SystemMessageRow], seen_at: str | None) -> int:
    """Count messages newer than the seen_at timestamp."""
    if not seen_at:
        return len(messages)
    return sum(1 for m in messages if m.date > seen_at)


def _row_to_response(r: SystemMessageRow) -> SystemMessage:
    return SystemMessage(id=r.id, date=r.date, title=r.title, body=r.body, category=r.category)


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
    rows = db.query(SystemMessageRow).all()
    prefs_row = db.get(UserPreferencesRow, user_id)
    seen_at = _get_messages_seen_at(prefs_row)
    latest = max((r.date for r in rows), default=None)
    return MessagesStatus(
        unseen_count=_count_unseen(rows, seen_at),
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
