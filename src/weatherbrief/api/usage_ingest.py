"""Node usage ingest endpoints (#922): nodes upload their usage rows to prod.

Two endpoints, both reachable only with a node's ``usage:ingest`` token
(``python -m weatherbrief.node_usage mint-token``). Any other credential is
refused, an ordinary admin token or session included: this is an append-only
door, and a token that can do anything else must not be able to use it by
accident. The logic lives in ``weatherbrief.node_usage``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flyfun_common.db import get_db
from weatherbrief.node_usage import (
    API_USAGE,
    COST_LEDGER,
    INGEST_SCOPE,
    MAX_BATCH,
    MAX_ROW_COST_USD,
    MAX_ROW_WEIGHTED_CALLS,
    SERVICE_ACCOUNT_PREFIX,
    OriginMismatch,
    ingest,
    validate_origin,
    watermark,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/usage/ingest", tags=["admin"])

_NODE_DB = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_NAME = r"^[a-z0-9_.:-]{1,32}$"


def ingest_origin(
    request: Request, db: Session = Depends(get_db, scope="function"),
) -> str:
    """The calling node's origin, from its ``usage:ingest`` token; else 401/403.

    Reads the bearer token itself rather than through ``current_user_id``:
    that dependency lets unscoped tokens and cookie sessions through (full
    access), and this endpoint must refuse exactly those.
    """
    from flyfun_common.db.deps import _authenticate_bearer_token

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id, scope = _authenticate_bearer_token(auth[7:], db)  # 401 on a bad token
    if INGEST_SCOPE not in (scope or "").split() or not user_id.startswith(SERVICE_ACCOUNT_PREFIX):
        raise HTTPException(status_code=403, detail="insufficient_scope")
    try:
        return validate_origin(user_id[len(SERVICE_ACCOUNT_PREFIX):])
    except ValueError:
        raise HTTPException(status_code=403, detail="insufficient_scope") from None


class ApiUsageIn(BaseModel):
    local_id: int = Field(ge=1)
    timestamp: datetime
    service: str = Field(pattern=_NAME)
    pipeline: str = Field(pattern=_NAME)
    api_calls: int = Field(ge=0, le=1_000_000)
    weighted_calls: float | None = Field(default=None, ge=0, le=MAX_ROW_WEIGHTED_CALLS)


class CostLedgerIn(BaseModel):
    local_id: int = Field(ge=1)
    created_at: datetime
    action: str = Field(min_length=1, max_length=64)
    category: str | None = Field(default=None, max_length=32)
    cost: float = Field(ge=0, le=MAX_ROW_COST_USD)
    description: str | None = Field(default=None, max_length=256)
    metadata_json: str | None = Field(default=None, max_length=8000)
    detail_json: str | None = Field(default=None, max_length=8000)


class IngestRows(BaseModel):
    api_usage_log: list[ApiUsageIn] = Field(default_factory=list, max_length=MAX_BATCH)
    cost_ledger: list[CostLedgerIn] = Field(default_factory=list, max_length=MAX_BATCH)


class IngestRequest(BaseModel):
    node_db: str = Field(pattern=_NODE_DB)
    rows: IngestRows


@router.get("/watermark")
def get_watermark(
    node_db: str = Query(pattern=_NODE_DB),
    origin: str = Depends(ingest_origin),
    db: Session = Depends(get_db, scope="function"),
) -> dict[str, int]:
    """Highest local id already received per table for this node DB."""
    return watermark(db, node_db)


@router.post("")
def post_ingest(
    body: IngestRequest,
    origin: str = Depends(ingest_origin),
    db: Session = Depends(get_db, scope="function"),
) -> dict:
    """Append a node's usage rows; rows already received are skipped."""
    rows = {
        API_USAGE: [r.model_dump() for r in body.rows.api_usage_log],
        COST_LEDGER: [r.model_dump() for r in body.rows.cost_ledger],
    }
    try:
        inserted = ingest(db, origin=origin, node_db=body.node_db, rows=rows)
        db.commit()
    except OriginMismatch as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except IntegrityError:
        # Two pushes of the same node racing: the loser's rows are already in.
        db.rollback()
        raise HTTPException(status_code=409, detail="concurrent push, retry") from None
    logger.info("Usage ingest from %s (%s): %s", origin, body.node_db, inserted)
    return {"inserted": inserted}
