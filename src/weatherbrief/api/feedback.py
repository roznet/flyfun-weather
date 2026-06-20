"""Feedback API: submit and list user feedback tied to briefing packs."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from weatherbrief.api.admin import require_admin
from weatherbrief.api.throttle import (
    digest_rating_burst_limiter,
    digest_rating_daily_limiter,
    feedback_burst_limiter,
    feedback_daily_limiter,
)
from flyfun_common.auth import is_dev_mode
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import FeedbackRow, FlightRow
from weatherbrief.privacy import mask_email
from weatherbrief.triage.security import scan_for_exfil

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

ALLOWED_CATEGORIES = {"data_issue", "too_conservative", "too_optimistic", "incorrect_interpretation", "other", "digest_rating"}
ALLOWED_STATUSES = {"pending", "ready", "replied", "ignored"}
ALLOWED_SENTIMENTS = {"up", "down"}
ALLOWED_TARGETS = {"digest", "general"}


class FeedbackRequest(BaseModel):
    flight_id: str = Field("", max_length=256)
    pack_timestamp: str = Field("", max_length=64)
    category: str = Field(max_length=32)
    comment: str = Field("", max_length=2000)
    sentiment: Optional[str] = Field(None, max_length=8)
    target: Optional[str] = Field(None, max_length=16)
    contact_ok: bool = True

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of: {', '.join(sorted(ALLOWED_CATEGORIES))}")
        return v

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, v: str) -> str:
        return v.strip()

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_SENTIMENTS:
            raise ValueError(f"sentiment must be one of: {', '.join(sorted(ALLOWED_SENTIMENTS))}")
        return v

    @field_validator("target")
    @classmethod
    def validate_target(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ALLOWED_TARGETS:
            raise ValueError(f"target must be one of: {', '.join(sorted(ALLOWED_TARGETS))}")
        return v

    @model_validator(mode="after")
    def require_comment_unless_thumb(self) -> "FeedbackRequest":
        # A bare thumb rating is valid; the traditional form needs text.
        if self.sentiment is None and not self.comment:
            raise ValueError("comment must not be empty")
        return self


class StatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(ALLOWED_STATUSES))}")
        return v


class ReplyUpdate(BaseModel):
    reply: str = Field(max_length=5000)


class NotesUpdate(BaseModel):
    notes: str = Field(max_length=5000)


class SendReplyRequest(BaseModel):
    reply: Optional[str] = Field(None, max_length=5000)
    override_safety_check: bool = False


# ---------------------------------------------------------------------------
# User endpoint
# ---------------------------------------------------------------------------

@router.post("")
def submit_feedback(
    body: FeedbackRequest,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Submit feedback for a specific briefing pack."""
    # Lightweight digest thumb ratings get their own, looser limiters — a pilot
    # may legitimately rate several briefings in a session, which the verbose
    # form's 1/min burst would block. The form keeps the stricter limits.
    if body.category == "digest_rating":
        digest_rating_burst_limiter.check(user_id)
        digest_rating_daily_limiter.check(user_id)
    else:
        feedback_burst_limiter.check(user_id)
        feedback_daily_limiter.check(user_id)

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
        sentiment=body.sentiment,
        target=body.target,
        contact_ok=body.contact_ok,
    )
    db.add(row)
    db.flush()
    logger.info("Feedback #%d from user %s on flight %s", row.id, user_id, body.flight_id)

    # Mirror digest thumb ratings to LangSmith as run feedback (issue #244).
    # Fire-and-forget: look up the pack's digest_trace_id and attach the rating
    # to that run so digest quality is reviewable in LangSmith. Never fails the
    # POST — pack missing (legacy), no trace id, or a LangSmith hiccup all no-op.
    if body.category == "digest_rating" and body.sentiment and body.flight_id and pack_ts:
        try:
            from weatherbrief.digest.langsmith_feedback import push_digest_thumb_feedback
            from weatherbrief.storage.flights import load_pack_meta

            meta = load_pack_meta(db, body.flight_id, pack_ts)
            # push_digest_thumb_feedback normalizes "" → None at the LangSmith
            # boundary, so pass the raw comment rather than duplicating it here.
            push_digest_thumb_feedback(
                run_id=meta.digest_trace_id,
                sentiment=body.sentiment,
                comment=body.comment,
            )
        except KeyError:
            # Pack not found (old/legacy pack, or mismatched ids) — nothing to
            # attach feedback to in LangSmith. The DB row above still records it.
            pass
        except Exception:
            logger.warning("Failed to mirror digest rating to LangSmith", exc_info=True)

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
            sentiment=body.sentiment,
        )
    except Exception:
        logger.warning("Failed to send feedback notification email", exc_info=True)

    return {"id": row.id, "status": "ok"}


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

def _get_feedback_or_404(db: Session, feedback_id: int) -> FeedbackRow:
    """Fetch a FeedbackRow by ID or raise 404."""
    row = db.query(FeedbackRow).filter(FeedbackRow.id == feedback_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return row


def _parse_waypoints(waypoints_json: Optional[str]) -> list[str]:
    """Parse a flight's waypoints_json column into a list of ICAO codes."""
    if not waypoints_json:
        return []
    try:
        value = json.loads(waypoints_json)
    except (ValueError, TypeError):
        return []
    return [str(w) for w in value] if isinstance(value, list) else []


def _serialize_feedback(
    fb: FeedbackRow,
    email: str,
    name: str,
    route_name: Optional[str] = None,
    waypoints_json: Optional[str] = None,
) -> dict:
    """Serialize a FeedbackRow + user info to a response dict."""
    return {
        "id": fb.id,
        "user_email": email,
        "user_name": name,
        "flight_id": fb.flight_id,
        "route_name": route_name or "",
        "waypoints": _parse_waypoints(waypoints_json),
        "pack_timestamp": fb.pack_timestamp.isoformat() if fb.pack_timestamp else "",
        "category": fb.category,
        "comment": fb.comment,
        "sentiment": fb.sentiment,
        "target": fb.target,
        "contact_ok": fb.contact_ok,
        "created_at": fb.created_at.isoformat() if fb.created_at else None,
        "status": fb.status,
        "classification": fb.classification,
        "ai_analysis": fb.ai_analysis,
        "admin_reply": fb.admin_reply,
        "admin_notes": fb.admin_notes,
        "confidence": fb.confidence,
        "replied_at": fb.replied_at.isoformat() if fb.replied_at else None,
        "processed_at": fb.processed_at.isoformat() if fb.processed_at else None,
    }


@router.get("/admin")
def list_feedback(
    status: Optional[str] = Query(None, description="Comma-separated status filter"),
    kind: Optional[str] = Query(
        None,
        description="'feedback' excludes digest_rating; 'ratings' returns only digest_rating",
    ),
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all feedback entries (admin only), optionally filtered by status/kind."""
    query = (
        db.query(
            FeedbackRow,
            UserRow.email,
            UserRow.display_name,
            FlightRow.route_name,
            FlightRow.waypoints_json,
        )
        .join(UserRow, FeedbackRow.user_id == UserRow.id)
        .outerjoin(FlightRow, FeedbackRow.flight_id == FlightRow.id)
    )

    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        invalid = set(statuses) - ALLOWED_STATUSES
        if invalid:
            raise HTTPException(400, f"Invalid status values: {', '.join(sorted(invalid))}")
        query = query.filter(FeedbackRow.status.in_(statuses))

    if kind == "ratings":
        query = query.filter(FeedbackRow.category == "digest_rating")
    elif kind == "feedback":
        query = query.filter(FeedbackRow.category != "digest_rating")
    elif kind is not None:
        raise HTTPException(400, "kind must be 'feedback' or 'ratings'")

    rows = query.order_by(FeedbackRow.created_at.desc()).all()
    return [
        _serialize_feedback(fb, email, name, route_name, waypoints_json)
        for fb, email, name, route_name, waypoints_json in rows
    ]


@router.put("/admin/{feedback_id}/status")
def update_feedback_status(
    feedback_id: int,
    body: StatusUpdate,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Update the workflow status of a feedback entry."""
    row = _get_feedback_or_404(db, feedback_id)
    row.status = body.status
    if body.status == "replied" and row.replied_at is None:
        row.replied_at = datetime.now(timezone.utc)
    db.flush()
    logger.info("Feedback #%d status → %s", feedback_id, body.status)
    return {"id": feedback_id, "status": row.status}


@router.put("/admin/{feedback_id}/reply")
def save_feedback_reply(
    feedback_id: int,
    body: ReplyUpdate,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save a draft reply for a feedback entry (does not send email)."""
    row = _get_feedback_or_404(db, feedback_id)
    row.admin_reply = body.reply
    db.flush()
    logger.info("Feedback #%d reply saved (%d chars)", feedback_id, len(body.reply))
    return {"id": feedback_id, "admin_reply": row.admin_reply}


@router.post("/admin/{feedback_id}/send")
def send_feedback_reply_email(
    feedback_id: int,
    request: Request,
    body: SendReplyRequest = SendReplyRequest(),
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Send the reply email to the user and mark feedback as replied."""
    row = _get_feedback_or_404(db, feedback_id)
    if not row.contact_ok:
        raise HTTPException(403, "User did not consent to be contacted")
    user = db.query(UserRow).filter(UserRow.id == row.user_id).first()
    if not user or not user.email:
        raise HTTPException(400, "User has no email address")

    reply_text = body.reply if body.reply is not None else row.admin_reply
    if not reply_text or not reply_text.strip():
        raise HTTPException(400, "No reply text to send")

    exfil_hits = scan_for_exfil(reply_text)
    if exfil_hits and not body.override_safety_check:
        logger.warning("Feedback #%d send blocked by exfil scan: %s", feedback_id, exfil_hits)
        raise HTTPException(
            status_code=409,
            detail={
                "error": "reply_flagged_by_safety_scan",
                "patterns": exfil_hits,
                "hint": "Review the reply for secrets/paths, then re-submit with override_safety_check=true to send.",
            },
        )
    if exfil_hits:
        logger.warning("Feedback #%d sent with override; flagged patterns: %s", feedback_id, exfil_hits)

    # Update the stored reply if an override was provided
    if body.reply is not None:
        row.admin_reply = body.reply

    base_url = str(request.base_url).rstrip("/")
    if not is_dev_mode():
        base_url = base_url.replace("http://", "https://")

    from weatherbrief.notify.admin_email import send_feedback_reply

    send_feedback_reply(
        to_email=user.email,
        user_name=user.display_name or "",
        reply_text=reply_text,
        original_comment=row.comment,
        category=row.category,
        base_url=base_url,
        flight_id=row.flight_id or "",
        pack_timestamp=row.pack_timestamp.isoformat() if row.pack_timestamp else "",
    )

    row.status = "replied"
    row.replied_at = datetime.now(timezone.utc)
    db.flush()
    logger.info("Feedback #%d reply sent to %s", feedback_id, mask_email(user.email))
    return {"id": feedback_id, "status": "replied", "sent_to": user.email}


@router.post("/admin/{feedback_id}/reopen")
def reopen_feedback(
    feedback_id: int,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Re-open a replied/ignored feedback so the admin can send another reply.

    Archives the previous reply into admin_notes, clears admin_reply,
    and sets status back to 'ready'.
    """
    row = _get_feedback_or_404(db, feedback_id)
    if row.status not in ("replied", "ignored"):
        raise HTTPException(400, "Only replied or ignored feedback can be re-opened")

    # Archive previous reply into admin_notes
    if row.admin_reply:
        timestamp = row.replied_at.strftime("%Y-%m-%d %H:%M") if row.replied_at else "unknown"
        archived = f"[Previous reply ({timestamp})]:\n{row.admin_reply}"
        row.admin_notes = f"{row.admin_notes}\n\n{archived}" if row.admin_notes else archived

    row.admin_reply = None
    row.status = "ready"
    db.flush()
    logger.info("Feedback #%d re-opened for follow-up reply", feedback_id)
    return {"id": feedback_id, "status": "ready"}


@router.put("/admin/{feedback_id}/notes")
def save_feedback_notes(
    feedback_id: int,
    body: NotesUpdate,
    _admin_id: str = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Save admin notes for a feedback entry."""
    row = _get_feedback_or_404(db, feedback_id)
    row.admin_notes = body.notes
    db.flush()
    logger.info("Feedback #%d notes saved (%d chars)", feedback_id, len(body.notes))
    return {"id": feedback_id, "admin_notes": row.admin_notes}
