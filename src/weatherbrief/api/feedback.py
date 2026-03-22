"""Feedback API: submit and list user feedback tied to briefing packs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from weatherbrief.api.admin import require_admin
from flyfun_common.auth import is_dev_mode
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FeedbackRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

ALLOWED_CATEGORIES = {"data_issue", "too_conservative", "too_optimistic", "incorrect_interpretation", "other"}


class FeedbackRequest(BaseModel):
    flight_id: str = Field("", max_length=256)
    pack_timestamp: str = Field("", max_length=64)
    category: str = Field(max_length=32)
    comment: str = Field(max_length=2000)

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(sorted(ALLOWED_CATEGORIES))}")
        return v

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("comment must not be empty")
        if len(v) > 5000:
            raise ValueError("comment must not exceed 5000 characters")
        return v


@router.post("")
def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Submit feedback for a specific briefing pack."""
    user = db.query(UserRow).filter(UserRow.id == user_id).first()

    # Parse pack_timestamp string to datetime (nullable)
    pack_ts: datetime | None = None
    if body.pack_timestamp:
        try:
            pack_ts = datetime.fromisoformat(body.pack_timestamp)
            if pack_ts.tzinfo is None:
                pack_ts = pack_ts.replace(tzinfo=timezone.utc)
        except ValueError:
            pack_ts = None

    row = FeedbackRow(
        user_id=user_id,
        flight_id=body.flight_id or None,
        pack_timestamp=pack_ts,
        category=body.category,
        comment=body.comment,
    )
    db.add(row)
    db.flush()
    logger.info("Feedback #%d from user %s on flight %s", row.id, user_id, body.flight_id)

    # Email admin (fire-and-forget)
    try:
        from weatherbrief.notify.admin_email import send_feedback_notification

        base_url = str(request.base_url).rstrip("/")
        if not is_dev_mode():
            base_url = base_url.replace("http://", "https://")
        send_feedback_notification(
            user_email=user.email if user else "",
            user_name=user.display_name if user else user_id,
            flight_id=body.flight_id,
            pack_timestamp=body.pack_timestamp,
            category=body.category,
            comment=body.comment,
            base_url=base_url,
        )
    except Exception:
        logger.warning("Failed to send feedback notification email", exc_info=True)

    return {"id": row.id, "status": "ok"}


@router.get("/admin")
def list_feedback(
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all feedback entries (admin only)."""
    rows = (
        db.query(FeedbackRow, UserRow.email, UserRow.display_name)
        .join(UserRow, FeedbackRow.user_id == UserRow.id)
        .order_by(FeedbackRow.created_at.desc())
        .all()
    )
    return [
        {
            "id": fb.id,
            "user_email": email,
            "user_name": name,
            "flight_id": fb.flight_id,
            "pack_timestamp": fb.pack_timestamp.isoformat() if fb.pack_timestamp else "",
            "category": fb.category,
            "comment": fb.comment,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        }
        for fb, email, name in rows
    ]
