"""Feedback API: submit and list user feedback tied to briefing packs."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from weatherbrief.api.admin import require_admin
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.models import FeedbackRow, UserRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

ALLOWED_CATEGORIES = {"data_quality", "missing_data", "ui_issue", "feature_request", "other"}


class FeedbackRequest(BaseModel):
    flight_id: str
    pack_timestamp: str = ""
    category: str
    comment: str

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
        return v


@router.post("")
def submit_feedback(
    body: FeedbackRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Submit feedback for a specific briefing pack."""
    row = FeedbackRow(
        user_id=user_id,
        flight_id=body.flight_id,
        pack_timestamp=body.pack_timestamp,
        category=body.category,
        comment=body.comment,
    )
    db.add(row)
    db.flush()
    logger.info("Feedback #%d from user %s on flight %s", row.id, user_id, body.flight_id)
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
            "pack_timestamp": fb.pack_timestamp,
            "category": fb.category,
            "comment": fb.comment,
            "created_at": fb.created_at.isoformat() if fb.created_at else None,
        }
        for fb, email, name in rows
    ]
