"""Node usage upload: dev laptops and compute nodes report their usage to prod (#922).

Anything run outside prod spends on the same Open-Meteo key and the same
Anthropic account, but writes its usage to its *own* DB. This module moves it:

* **Node side** — ``push()`` / ``python -m weatherbrief.node_usage push``.
  The node's ``api_usage_log`` and ``cost_ledger`` tables are the outbox; no
  new local state beyond one UUID (``node_db_id`` in ``instance_settings``).
  Each run asks prod for its high-water mark per table and sends the newer
  rows in batches. Failure is logged and never raised: an offline laptop or a
  prod deploy only delays the data, the next run catches up.
* **Prod side** — ``ingest()`` and ``watermark()`` behind
  ``api/usage_ingest.py``. Rows are keyed ``(node_db, source_table,
  local_id)`` in ``node_usage_ingest``, so re-sends insert nothing.
  ``api_usage_log`` rows keep their service / pipeline / calls / weight and
  gain ``origin``; ``cost_ledger`` rows land as ``category="node"`` under
  ``system:<origin>``, never ``briefing``, so they reach the program total
  but no pilot's figures (see ``designs/cost-attribution-design.md``).
* **Auth** — a per-node ``ff_`` token on the service account
  ``system:<origin>`` carrying only the ``usage:ingest`` scope
  (``mint-token``). The origin comes from the token, never from the payload,
  so a leaked laptop token can only append rows as that laptop.

The wire format is the explicit dicts below, not ORM dumps, so a node and prod
on different SHAs still agree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from weatherbrief.costs import NODE_CATEGORY  # every uploaded cost row lands under it
from weatherbrief.db.models import (
    ApiUsageRow,
    CostLedgerRow,
    InstanceSettingRow,
    NodeUsageIngestRow,
)

logger = logging.getLogger(__name__)

#: OAuth scope of a node's upload token. Registered with no endpoints in
#: flyfun-common's scope registry, so the token reaches nothing but the two
#: ingest endpoints, which check for it themselves.
INGEST_SCOPE = "usage:ingest"
#: Service accounts are ``system:<origin>``; the origin is what prod records.
SERVICE_ACCOUNT_PREFIX = "system:"
ORIGIN_RE = re.compile(r"^[a-z][a-z0-9-]{0,15}$")
#: "prod" labels the server's own rows in reports, so no node may claim it.
RESERVED_ORIGINS = frozenset({"prod"})

API_USAGE = "api_usage_log"
COST_LEDGER = "cost_ledger"
SOURCE_TABLES = (API_USAGE, COST_LEDGER)

NODE_DB_ID_KEY = "node_db_id"
#: ``push_floor:<table>``: the highest local id this DB must never send. Set by
#: ``worktree_init.py --fork-db`` on the copy, whose rows up to the fork point
#: are main's and already pushed under main's node id.
PUSH_FLOOR_KEY = "push_floor:{}"

#: Rows per table per POST; also the server's cap on one request.
BATCH_SIZE = 500
MAX_BATCH = 1000
#: Sanity caps on one uploaded row. Generous for real usage (a briefing's
#: Open-Meteo weight is a few thousand, an LLM call a few cents), small enough
#: that a leaked token cannot make the bill read as anything absurd.
MAX_ROW_COST_USD = 50.0
MAX_ROW_WEIGHTED_CALLS = 10_000_000.0
MAX_ROW_API_CALLS = 1_000_000
#: Wire-format limits, shared with the API models so the node never sends a
#: row prod would refuse: one refused row fails its whole batch, and the push
#: would retry that batch forever with every later row stuck behind it.
NAME_PATTERN = r"^[a-z0-9_.:-]{1,32}$"
MAX_ACTION_CHARS = 64
MAX_CATEGORY_CHARS = 32
MAX_DESCRIPTION_CHARS = 256
MAX_JSON_CHARS = 8000


def validate_origin(origin: str) -> str:
    if not ORIGIN_RE.match(origin or "") or origin in RESERVED_ORIGINS:
        raise ValueError(
            f"origin {origin!r}: use 1-16 lowercase letters, digits or '-', "
            f"starting with a letter (not {', '.join(sorted(RESERVED_ORIGINS))})"
        )
    return origin


def _utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _wire_name(name: str | None) -> str:
    """A service / pipeline name in the wire format (``NAME_PATTERN``)."""
    out = re.sub(r"[^a-z0-9_.:-]", "_", (name or "").lower())[:32]
    return out or "unknown"


def _wire_json(text: str | None, row_id: int, column: str) -> str | None:
    """A JSON text column, or None (logged) when over the wire limit.

    Not truncated: a cut JSON document is no longer JSON.
    """
    if text is None or len(text) <= MAX_JSON_CHARS:
        return text
    logger.warning(
        "Usage push: cost_ledger %d %s is %d chars (> %d), sent without it",
        row_id, column, len(text), MAX_JSON_CHARS,
    )
    return None


def _skip(table: str, row_id: int, why: str) -> None:
    logger.error("Usage push: %s %d not sent, %s", table, row_id, why)


# ---------------------------------------------------------------------------
# Node side
# ---------------------------------------------------------------------------


def get_node_db_id(db: Session) -> str:
    """This DB's own UUID, minted (and committed) on first use."""
    row = db.get(InstanceSettingRow, NODE_DB_ID_KEY)
    if row is None:
        row = InstanceSettingRow(key=NODE_DB_ID_KEY, value=str(uuid.uuid4()))
        db.add(row)
        db.commit()
    return row.value


def push_floors(db: Session) -> dict[str, int]:
    """Per source table, the local id up to which rows are not this DB's to send."""
    out = {}
    for table in SOURCE_TABLES:
        row = db.get(InstanceSettingRow, PUSH_FLOOR_KEY.format(table))
        out[table] = int(row.value) if row is not None else 0
    return out


def collect_api_usage(
    db: Session, after_id: int, limit: int, since: datetime | None = None,
) -> tuple[list[dict], int | None]:
    """This node's own ``api_usage_log`` rows after ``after_id``, wire format.

    Returns ``(rows, last scanned id)``; the id is None when nothing was
    scanned. A row prod's sanity caps would refuse is skipped with an error
    log rather than sent (see ``NAME_PATTERN``), and the scan id still moves
    past it so it cannot stall the rows behind it.

    ``origin IS NULL`` only: a row that was itself uploaded here is never
    forwarded again (it would be counted twice).
    """
    q = db.query(ApiUsageRow).filter(
        ApiUsageRow.id > after_id, ApiUsageRow.origin.is_(None),
    )
    if since is not None:
        q = q.filter(ApiUsageRow.timestamp >= since)
    scanned = q.order_by(ApiUsageRow.id).limit(limit).all()
    rows = []
    for r in scanned:
        weighted = r.weighted_calls
        if weighted is not None and not 0 <= weighted <= MAX_ROW_WEIGHTED_CALLS:
            _skip(API_USAGE, r.id, f"weighted_calls {weighted} outside [0, {MAX_ROW_WEIGHTED_CALLS:g}]")
            continue
        calls = min(max(r.api_calls or 0, 0), MAX_ROW_API_CALLS)
        if calls != (r.api_calls or 0):
            logger.warning(
                "Usage push: api_usage_log %d api_calls %s sent as %d",
                r.id, r.api_calls, calls,
            )
        rows.append({
            "local_id": r.id,
            "timestamp": _utc(r.timestamp).isoformat(),
            "service": _wire_name(r.service),
            "pipeline": _wire_name(r.pipeline),
            "api_calls": calls,
            "weighted_calls": weighted,
        })
    return rows, (scanned[-1].id if scanned else None)


def collect_cost_ledger(
    db: Session, after_id: int, limit: int, since: datetime | None = None,
) -> tuple[list[dict], int | None]:
    """This node's weather ``cost_ledger`` rows after ``after_id``, wire format.

    Same ``(rows, last scanned id)`` contract as ``collect_api_usage``: text
    is clipped to the wire limits, and a cost outside ``[0, MAX_ROW_COST_USD]``
    is skipped with an error log (the cap bounds what a leaked token could
    append, so prod will not take it either).

    Every category but the retired zero-cost ``topup``: on a node, briefings,
    side calls and eval runs are all spend the program pays for.
    """
    from weatherbrief.api.credits import SERVICE

    q = db.query(CostLedgerRow).filter(
        CostLedgerRow.id > after_id,
        CostLedgerRow.service == SERVICE,
        (CostLedgerRow.category.is_(None)) | (CostLedgerRow.category != "topup"),
    )
    if since is not None:
        q = q.filter(CostLedgerRow.created_at >= since)
    scanned = q.order_by(CostLedgerRow.id).limit(limit).all()
    rows = []
    for r in scanned:
        cost = float(r.cost or 0.0)
        if not 0 <= cost <= MAX_ROW_COST_USD:
            _skip(COST_LEDGER, r.id, f"cost ${cost:.4f} outside [0, {MAX_ROW_COST_USD:g}]")
            continue
        rows.append({
            "local_id": r.id,
            "created_at": _utc(r.created_at).isoformat(),
            "action": (r.action or "unknown")[:MAX_ACTION_CHARS],
            "category": r.category[:MAX_CATEGORY_CHARS] if r.category else None,
            "cost": cost,
            "description": (
                r.description[:MAX_DESCRIPTION_CHARS] if r.description else None
            ),
            "metadata_json": _wire_json(r.metadata_json, r.id, "metadata_json"),
            "detail_json": _wire_json(r.detail_json, r.id, "detail_json"),
        })
    return rows, (scanned[-1].id if scanned else None)


def push_config() -> tuple[str, str] | None:
    """``(base_url, token)`` from the environment, or None when not configured.

    Never on a production server: prod's rows are already prod's, and pushing
    them to itself would count every call twice.
    """
    url = os.environ.get("WB_USAGE_PUSH_URL", "").strip().rstrip("/")
    token = os.environ.get("WB_USAGE_PUSH_TOKEN", "").strip()
    if not url or not token:
        return None
    if os.environ.get("ENVIRONMENT", "development").strip().lower() in ("production", "prod"):
        logger.warning("Usage push: configured on a production server, ignored")
        return None
    return url, token


@dataclass
class PushResult:
    sent: dict[str, int] = field(default_factory=lambda: {t: 0 for t in SOURCE_TABLES})
    inserted: dict[str, int] = field(default_factory=lambda: {t: 0 for t in SOURCE_TABLES})
    error: str | None = None


def push(
    db: Session,
    base_url: str,
    token: str,
    *,
    http=None,
    batch_size: int = BATCH_SIZE,
    since: datetime | None = None,
) -> PushResult:
    """Send this node's usage rows that prod has not seen yet. Never raises.

    ``http`` is anything with ``requests``' ``get``/``post`` (a
    ``requests.Session`` by default; tests pass the FastAPI ``TestClient``).
    """
    result = PushResult()
    try:
        if http is None:
            import requests

            http = requests.Session()
        node_db = get_node_db_id(db)
        headers = {"Authorization": f"Bearer {token}"}
        endpoint = f"{base_url}/api/admin/usage/ingest"

        resp = http.get(
            f"{endpoint}/watermark", params={"node_db": node_db},
            headers=headers, timeout=30,
        )
        if resp.status_code != 200:
            result.error = f"watermark: HTTP {resp.status_code} {resp.text[:200]}"
            return result
        floors = push_floors(db)
        marks = {
            t: max(int(resp.json().get(t) or 0), floors[t]) for t in SOURCE_TABLES
        }

        while True:
            rows, scanned = {}, {}
            rows[API_USAGE], scanned[API_USAGE] = collect_api_usage(
                db, marks[API_USAGE], batch_size, since,
            )
            rows[COST_LEDGER], scanned[COST_LEDGER] = collect_cost_ledger(
                db, marks[COST_LEDGER], batch_size, since,
            )
            if all(v is None for v in scanned.values()):
                break
            if any(rows.values()):
                resp = http.post(
                    endpoint, json={"node_db": node_db, "rows": rows},
                    headers=headers, timeout=60,
                )
                if resp.status_code != 200:
                    result.error = f"ingest: HTTP {resp.status_code} {resp.text[:200]}"
                    break
                inserted = resp.json().get("inserted") or {}
                for table, batch in rows.items():
                    result.sent[table] += len(batch)
                    result.inserted[table] += int(inserted.get(table) or 0)
            # Past skipped rows too: they would be skipped again.
            for table, last in scanned.items():
                if last is not None:
                    marks[table] = last
    except Exception as exc:  # noqa: BLE001 — a failed push must never fail the node's job
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        # The loop only reads; drop the snapshot so a long-lived session sees
        # rows written after this run on the next one.
        db.rollback()

    if result.error:
        logger.warning("Usage push failed (next run catches up): %s", result.error)
    else:
        logger.info(
            "Usage push: sent %s, inserted %s",
            result.sent, result.inserted,
        )
    return result


def push_once() -> PushResult | None:
    """One push on a fresh session when configured; None when not configured."""
    cfg = push_config()
    if cfg is None:
        return None
    from weatherbrief.api.usage import detached_session

    db = detached_session()
    try:
        return push(db, *cfg)
    finally:
        db.close()


def record_eval_cost(
    script: str,
    cost_usd: float,
    *,
    calls: int,
    model: str,
    cache_ttl: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    """Write one ``node``/``eval`` ledger row for an eval run. Never raises.

    ``cost_usd`` is what the eval computed itself (``compute_call_cost`` at the
    cache TTL the eval really used, ``cache_ttl``); nothing here re-prices it,
    so an eval's 5-minute override never touches the production charging path.
    The row stays on this machine until the usage push uploads it.
    """
    if cost_usd <= 0:
        return
    try:
        from flyfun_common.costs import record_cost

        from weatherbrief.api.credits import SERVICE
        from weatherbrief.api.usage import detached_session

        db = detached_session()
        try:
            record_cost(
                db,
                f"{SERVICE_ACCOUNT_PREFIX}eval",
                service=SERVICE,
                action="eval",
                cost=round(cost_usd, 6),
                category=NODE_CATEGORY,
                description=f"Eval {script}: {calls} call(s) (${cost_usd:.4f})"[:256],
                metadata={
                    "script": script,
                    "calls": calls,
                    "model": model,
                    "cache_ttl": cache_ttl,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("Eval cost not recorded (%s, $%.4f)", script, cost_usd, exc_info=True)


# ---------------------------------------------------------------------------
# Prod side
# ---------------------------------------------------------------------------


class OriginMismatch(ValueError):
    """The node DB id is already registered to another origin."""


def watermark(db: Session, node_db: str) -> dict[str, int]:
    """Highest ``local_id`` received per source table for one node DB (0 = none)."""
    rows = (
        db.query(NodeUsageIngestRow.source_table, func.max(NodeUsageIngestRow.local_id))
        .filter(NodeUsageIngestRow.node_db == node_db)
        .group_by(NodeUsageIngestRow.source_table)
        .all()
    )
    out = {t: 0 for t in SOURCE_TABLES}
    out.update({t: int(m or 0) for t, m in rows if t in out})
    return out


def _cost_detail(origin: str, node_db: str, row: dict) -> str:
    """``detail_json`` of an uploaded cost row: provenance + the node's own detail."""
    detail = row.get("detail_json")
    try:
        detail = json.loads(detail) if detail else None
    except (json.JSONDecodeError, TypeError):
        pass  # keep the raw string
    return json.dumps({
        "origin": origin,
        "node_db": node_db,
        "local_id": row["local_id"],
        "category": row.get("category"),
        "detail": detail,
    })


def ingest(
    db: Session, *, origin: str, node_db: str, rows: dict[str, list[dict]],
) -> dict[str, int]:
    """Insert a node's rows that are not here yet; returns inserted counts.

    Raises ``OriginMismatch`` when ``node_db`` already belongs to another
    origin (a token must not write into another node's id space). Rows are
    validated by the API layer; ``created_at``/``timestamp`` stay the node's,
    so usage lands in the window it was spent in.
    """
    from weatherbrief.api.credits import SERVICE

    owner = (
        db.query(NodeUsageIngestRow.origin)
        .filter(NodeUsageIngestRow.node_db == node_db)
        .first()
    )
    if owner is not None and owner[0] != origin:
        raise OriginMismatch(f"node DB {node_db} belongs to {owner[0]!r}")

    inserted = {t: 0 for t in SOURCE_TABLES}
    for table in SOURCE_TABLES:
        batch = {r["local_id"]: r for r in rows.get(table) or []}  # dedupe in-batch
        if not batch:
            continue
        seen = {
            lid for (lid,) in db.query(NodeUsageIngestRow.local_id).filter(
                NodeUsageIngestRow.node_db == node_db,
                NodeUsageIngestRow.source_table == table,
                NodeUsageIngestRow.local_id.in_(list(batch)),
            )
        }
        for local_id, r in sorted(batch.items()):
            if local_id in seen:
                continue
            if table == API_USAGE:
                target = ApiUsageRow(
                    timestamp=_utc(r["timestamp"]),
                    service=r["service"],
                    pipeline=r["pipeline"],
                    api_calls=r["api_calls"],
                    weighted_calls=r.get("weighted_calls"),
                    origin=origin,
                )
            else:
                desc = r.get("description") or r["action"]
                target = CostLedgerRow(
                    user_id=f"{SERVICE_ACCOUNT_PREFIX}{origin}",
                    service=SERVICE,
                    action=r["action"],
                    cost=r["cost"],
                    metadata_json=r.get("metadata_json"),
                    created_at=_utc(r["created_at"]),
                    category=NODE_CATEGORY,
                    description=f"[{origin}] {desc}"[:256],
                    detail_json=_cost_detail(origin, node_db, r),
                )
            db.add(target)
            db.flush()
            db.add(NodeUsageIngestRow(
                node_db=node_db, origin=origin, source_table=table,
                local_id=local_id, target_id=target.id,
            ))
            inserted[table] += 1
    db.flush()
    return inserted


def mint_token(db: Session, origin: str, *, expires_days: int | None = None) -> str:
    """Create a node's upload token; returns the raw token (shown once).

    Creates the service account's ``users`` row first when missing: prod's
    ``api_tokens.user_id`` still carries migration 007's FK to ``users.id``
    (the model does not declare it, so ``create_all`` DBs lack it). Unapproved,
    like no pilot: the token's scope reaches the ingest endpoints only.
    """
    from datetime import timedelta

    from flyfun_common.admin import generate_api_token
    from flyfun_common.db.models import UserRow

    from weatherbrief.db.models import ApiTokenRow

    validate_origin(origin)
    user_id = f"{SERVICE_ACCOUNT_PREFIX}{origin}"
    if db.get(UserRow, user_id) is None:
        db.add(UserRow(
            id=user_id, provider="api_token", provider_sub=user_id,
            display_name=f"Usage push: {origin}", approved=False,
        ))
        db.flush()
    raw = generate_api_token()
    db.add(ApiTokenRow(
        user_id=user_id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        name=f"usage push: {origin}",
        scope=INGEST_SCOPE,
        expires_at=(
            datetime.now(timezone.utc) + timedelta(days=expires_days)
            if expires_days else None
        ),
    ))
    db.commit()
    return raw


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _session():
    from dotenv import load_dotenv

    load_dotenv()
    from flyfun_common.db import SessionLocal, get_engine, init_shared_db

    import weatherbrief.db.models  # noqa: F401 — register models

    engine = get_engine()  # binds SessionLocal; prod (mint-token) needs it too
    if os.environ.get("ENVIRONMENT", "development") == "development":
        init_shared_db(engine)
    return SessionLocal()


def _cmd_push(args: argparse.Namespace) -> int:
    db = _session()
    try:
        cfg = push_config()
        if cfg is None:
            print("usage push: not configured (WB_USAGE_PUSH_URL / WB_USAGE_PUSH_TOKEN)")
            return 0
        since = (
            datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
            if args.since else None
        )
        result = push(db, *cfg, since=since)
    finally:
        db.close()
    print(f"usage push: sent {result.sent}, inserted {result.inserted}"
          + (f", FAILED: {result.error}" if result.error else ""))
    # Exit 0 on failure unless asked: the push is a side step of a node's job
    # (the forecast cycle), and must never fail it.
    return 1 if (result.error and args.strict) else 0


def _cmd_mint_token(args: argparse.Namespace) -> int:
    db = _session()
    try:
        token = mint_token(db, args.origin, expires_days=args.days)
    finally:
        db.close()
    print(f"Token for origin {args.origin!r} (shown once; scope {INGEST_SCOPE}):")
    print(token)
    print("Set on the node: WB_USAGE_PUSH_URL=<prod base URL>  WB_USAGE_PUSH_TOKEN=<token>")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m weatherbrief.node_usage", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_push = sub.add_parser("push", help="(node) upload new usage rows to prod")
    p_push.add_argument("--since", help="only rows at or after this UTC date (YYYY-MM-DD)")
    p_push.add_argument("--strict", action="store_true", help="exit 1 when the push fails")
    p_push.set_defaults(func=_cmd_push)
    p_mint = sub.add_parser("mint-token", help="(prod) create a node's upload token")
    p_mint.add_argument("--origin", required=True, help="node name, e.g. dev, macmini")
    p_mint.add_argument("--days", type=int, default=None, help="expiry in days (default none)")
    p_mint.set_defaults(func=_cmd_mint_token)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
