"""User-facing API token management.

Authenticated users can create, list, and revoke their own API tokens.
Tokens are used for MCP server authentication and other programmatic access.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from flyfun_common.admin import generate_api_token, hash_token
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import ApiTokenRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tokens", tags=["tokens"])

MAX_TOKENS_PER_USER = 5


class CreateTokenRequest(BaseModel):
    name: str = Field(max_length=256, description="A label for this token (e.g. 'Claude Desktop MCP')")


class TokenResponse(BaseModel):
    """Returned after creating a token — includes the plaintext (shown once)."""
    id: int
    name: str
    token: str
    created_at: str


class TokenListItem(BaseModel):
    """Token metadata for listing — never includes the plaintext."""
    id: int
    name: str
    created_at: str
    last_used_at: str | None = None


@router.post("", response_model=TokenResponse, status_code=201)
def create_token(
    body: CreateTokenRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new API token for the current user.

    The plaintext token is returned exactly once — it cannot be retrieved later.
    """
    # Enforce per-user limit
    active_count = (
        db.query(ApiTokenRow)
        .filter(ApiTokenRow.user_id == user_id, ApiTokenRow.revoked == False)  # noqa: E712
        .count()
    )
    if active_count >= MAX_TOKENS_PER_USER:
        raise HTTPException(
            status_code=409,
            detail=f"You already have {active_count} active tokens (limit {MAX_TOKENS_PER_USER}). Revoke one first.",
        )

    plaintext = generate_api_token()
    token_row = ApiTokenRow(
        user_id=user_id,
        token_hash=hash_token(plaintext),
        name=body.name,
    )
    db.add(token_row)
    db.flush()

    logger.info("User %s created API token id=%d name=%r", user_id, token_row.id, body.name)

    return TokenResponse(
        id=token_row.id,
        name=token_row.name,
        token=plaintext,
        created_at=token_row.created_at.isoformat(),
    )


@router.get("", response_model=list[TokenListItem])
def list_tokens(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List all active (non-revoked) tokens for the current user."""
    rows = (
        db.query(ApiTokenRow)
        .filter(ApiTokenRow.user_id == user_id, ApiTokenRow.revoked == False)  # noqa: E712
        .order_by(ApiTokenRow.created_at.desc())
        .all()
    )
    return [
        TokenListItem(
            id=row.id,
            name=row.name,
            created_at=row.created_at.isoformat(),
            last_used_at=row.last_used_at.isoformat() if row.last_used_at else None,
        )
        for row in rows
    ]


@router.delete("/{token_id}", status_code=204)
def revoke_token(
    token_id: int,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Revoke one of the current user's tokens."""
    token_row = (
        db.query(ApiTokenRow)
        .filter(ApiTokenRow.id == token_id, ApiTokenRow.user_id == user_id)
        .first()
    )
    if not token_row:
        raise HTTPException(status_code=404, detail="Token not found")

    token_row.revoked = True
    db.flush()

    logger.info("User %s revoked API token id=%d", user_id, token_id)
