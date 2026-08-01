"""Durable model-delivery observations — the realised half of freshness calibration.

Two layers, matching ``storage/refresh_jobs.py``:

* :func:`insert_delivery` takes a caller-supplied :class:`~sqlalchemy.orm.Session`
  and behaves like any other storage function (flush, let the caller commit);
* :func:`record_delivery` opens its own short-lived session, commits, and
  **swallows every exception**. It is called from the freshness loop, which has
  no session in hand and whose job — deciding whether a pack is stale — must
  never fail because a telemetry insert did.

Collect-only in v1 (issue #515): nothing reads these rows yet. See
``designs/freshness-markers.md``.

Datetime columns are :class:`~weatherbrief.db.types.TZDateTime` (issue #520):
writes must be aware (naive raises ``ValueError``), and reads come back
UTC-aware on both SQLite and MySQL — no per-call-site tzinfo fixups needed on
either side.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from flyfun_common.db import SessionLocal
from weatherbrief.db.models import ModelDeliveryLogRow

logger = logging.getLogger(__name__)


def insert_delivery(
    db: Session,
    *,
    source: str,
    model: str,
    cycle_init: datetime,
    expected_at: datetime,
    detected_at: datetime,
    observed_via: str,
    published_at: datetime | None = None,
    last_absent_at: datetime | None = None,
) -> ModelDeliveryLogRow | None:
    """Append one observed delivery; return the row, or None if already logged.

    ``(source, cycle_init)`` is unique — a re-observation of a run we already
    recorded is a no-op rather than an error. The first observation is the one
    worth keeping: a later one has a longer, less informative detection
    bracket.
    """
    existing = db.execute(
        select(ModelDeliveryLogRow).where(
            ModelDeliveryLogRow.source == source,
            ModelDeliveryLogRow.cycle_init == cycle_init,
        )
    ).scalars().first()
    if existing is not None:
        return None

    row = ModelDeliveryLogRow(
        source=source,
        model=model,
        cycle_init=cycle_init,
        expected_at=expected_at,
        published_at=published_at,
        detected_at=detected_at,
        last_absent_at=last_absent_at,
        observed_via=observed_via,
    )
    db.add(row)
    db.flush()
    return row


def record_delivery(
    *,
    source: str,
    model: str,
    cycle_init: datetime,
    expected_at: datetime,
    detected_at: datetime,
    observed_via: str,
    published_at: datetime | None = None,
    last_absent_at: datetime | None = None,
) -> None:
    """Write one delivery observation in its own session, swallowing failures.

    The freshness loop calls this on every marker advance. A DB hiccup here
    costs one calibration sample, which is not worth failing a freshness tick
    over — and the ``UNIQUE (source, cycle_init)`` constraint means a racing
    duplicate is a no-op, not a lost row.
    """
    db = None
    try:
        db = SessionLocal()
        insert_delivery(
            db,
            source=source,
            model=model,
            cycle_init=cycle_init,
            expected_at=expected_at,
            published_at=published_at,
            detected_at=detected_at,
            last_absent_at=last_absent_at,
            observed_via=observed_via,
        )
        db.commit()
    except IntegrityError:
        # Lost the race on the unique constraint — the other writer's row is
        # as good as ours. Not worth a warning.
        logger.debug(
            "model-delivery row already present for %s/%s", source, cycle_init,
        )
        _rollback(db)
    except Exception:
        # Deliberately broad and non-fatal: this is telemetry, not correctness.
        logger.debug(
            "model-delivery write failed for %s/%s", source, cycle_init, exc_info=True,
        )
        _rollback(db)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def _rollback(db: Session | None) -> None:
    if db is None:
        return
    try:
        db.rollback()
    except Exception:
        pass
