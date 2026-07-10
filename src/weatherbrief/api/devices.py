"""Device-token registration for APNs push notifications.

The iOS client registers its APNs device token here after obtaining it from
``registerForRemoteNotifications``, and unregisters on sign-out so a signed-out
device stops receiving another user's briefings.

The token's ``environment`` (``sandbox`` | ``production``) is decided by the
*app build on the device*, not by which server runs — an Xcode debug build's
token is APNs-sandbox and must be sent via the sandbox host. The client reports
it here; the server routes on it at send time (see ``notify/push.py``).
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

from weatherbrief.db.models import DeviceTokenRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegistration(BaseModel):
    """Payload for registering/upserting an APNs device token."""

    token: str
    environment: Literal["sandbox", "production"] = "production"

    @field_validator("token")
    @classmethod
    def _validate_token(cls, v: str) -> str:
        v = v.strip()
        # APNs tokens are hex; be lenient on length (32-byte legacy vs longer)
        # but reject empty / obviously-bogus values.
        if not v or len(v) > 200:
            raise ValueError("Invalid device token")
        return v


@router.post("", status_code=204)
def register_device(
    body: DeviceRegistration,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Register or update the caller's APNs device token.

    Upserts on the unique ``token``: re-registering the same token (token
    rotation keeps the same value across launches) refreshes its owner,
    environment, and ``updated_at``. A token that moves to a new user (device
    handed over, re-signed-in) is reassigned rather than duplicated.
    """
    row = db.query(DeviceTokenRow).filter(DeviceTokenRow.token == body.token).first()
    if row is None:
        db.add(
            DeviceTokenRow(
                user_id=user_id,
                token=body.token,
                environment=body.environment,
            )
        )
    else:
        row.user_id = user_id
        row.environment = body.environment
        from datetime import datetime, timezone

        row.updated_at = datetime.now(timezone.utc)
    logger.info("Registered device token for %s (%s)", user_id, body.environment)


@router.delete("/{token}", status_code=204)
def unregister_device(
    token: str,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Unregister a device token (sign-out).

    Only deletes a row the caller owns — a token registered to another user is
    left untouched (idempotent no-op), so one account can't unregister
    another's device.
    """
    db.query(DeviceTokenRow).filter(
        DeviceTokenRow.token == token,
        DeviceTokenRow.user_id == user_id,
    ).delete(synchronize_session=False)
    logger.info("Unregistered device token for %s", user_id)

    # Decay / fail-safe: if that was the user's LAST device and they were
    # push-only (email off), re-enable email so they aren't silently stranded
    # with no working channel (channel invariant).
    remaining = (
        db.query(DeviceTokenRow)
        .filter(DeviceTokenRow.user_id == user_id)
        .count()
    )
    if remaining == 0:
        from weatherbrief.api.preferences import apply_last_device_decay

        if apply_last_device_decay(db, user_id):
            logger.info(
                "Re-enabled briefing email for %s after last device unregister", user_id
            )
