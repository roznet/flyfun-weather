"""Classify stored observations as routine METAR or SPECI (migration 091).

``report_type`` is set on every observation written after migration 091, but
rows collected before it are NULL. They are recoverable: ``euro_aip``'s parser
keeps the original ``raw_text``, prefix and all, so ``metar_raw LIKE 'SPECI%'``
classifies history exactly.

This is deliberately **not** part of the migration. Prod holds ~2.3M
observations on a shared MySQL instance with a 1 GiB buffer pool, and a
migration that scans and rewrites the whole table would block a deploy on it.
Here it is a separate, resumable, off-peak operation, keyset-paginated by
primary key so no single transaction is large.

Leaving it unrun is safe: every read path treats NULL as routine, which is the
behaviour that existed before the column. Running it changes what a *future*
re-score would see — historical scores already written are untouched — and
that is the point, so it is an explicit act.

``survey`` does the same scan without writing, and reports the SPECI rate. That
number is the measure of how much convective truth the pre-091 ingest was
discarding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from weatherbrief.db.models import VerificationObservationRow
from weatherbrief.tasks.verification import REPORT_TYPE_METAR, REPORT_TYPE_SPECI

logger = logging.getLogger(__name__)

_DEFAULT_BATCH = 20_000

# Raw METAR/TAF text is uppercase by convention, and MySQL's default
# collation is case-insensitive anyway; SQLite's LIKE is case-insensitive for
# ASCII. A plain prefix match is therefore right on both dialects.
_SPECI_PREFIX = "SPECI%"


@dataclass
class ReportTypeSurvey:
    """Counts over the observation table. All rows, not a sample."""

    total: int = 0
    already_classified: int = 0
    speci: int = 0
    metar: int = 0
    unknown: int = 0  # metar_raw is NULL — nothing to classify from

    @property
    def classifiable(self) -> int:
        return self.speci + self.metar

    @property
    def speci_rate(self) -> float:
        """SPECIs as a share of classifiable rows, in percent."""
        return 100.0 * self.speci / self.classifiable if self.classifiable else 0.0

    def render(self) -> str:
        lines = [
            f"  total observations   {self.total:>12,}",
            f"  already classified   {self.already_classified:>12,}",
            f"  -> SPECI             {self.speci:>12,}  ({self.speci_rate:.2f}% of classifiable)",
            f"  -> METAR             {self.metar:>12,}",
        ]
        if self.unknown:
            lines.append(
                f"  unclassifiable       {self.unknown:>12,}  (metar_raw is NULL)"
            )
        return "\n".join(lines)


def _id_bounds(db: Session) -> tuple[int, int] | None:
    row = db.execute(
        select(
            func.min(VerificationObservationRow.id),
            func.max(VerificationObservationRow.id),
        )
    ).one()
    if row[0] is None:
        return None
    return int(row[0]), int(row[1])


def survey_report_types(
    db: Session, batch_size: int = _DEFAULT_BATCH,
) -> ReportTypeSurvey:
    """Count how history classifies, without writing anything.

    Paginated by primary key so the scan is a long series of small indexed
    range reads rather than one full-table scan holding buffer pool.
    """
    out = ReportTypeSurvey()
    bounds = _id_bounds(db)
    if bounds is None:
        return out

    lo, hi = bounds
    while lo <= hi:
        top = lo + batch_size
        window = (
            (VerificationObservationRow.id >= lo),
            (VerificationObservationRow.id < top),
        )
        counts = db.execute(
            select(
                func.count(),
                func.sum(
                    case(
                        (VerificationObservationRow.report_type.isnot(None), 1),
                        else_=0,
                    )
                ),
                func.sum(
                    case(
                        (VerificationObservationRow.metar_raw.is_(None), 1), else_=0
                    )
                ),
                func.sum(
                    case(
                        (
                            VerificationObservationRow.metar_raw.like(_SPECI_PREFIX),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(*window)
        ).one()

        n, classified, no_raw, speci = (int(c or 0) for c in counts)
        out.total += n
        out.already_classified += classified
        out.unknown += no_raw
        out.speci += speci
        out.metar += n - no_raw - speci
        lo = top

    return out


def backfill_report_type(
    db: Session,
    batch_size: int = _DEFAULT_BATCH,
    only_null: bool = True,
) -> int:
    """Set ``report_type`` from ``metar_raw`` for rows that lack it.

    Commits per batch, so an interrupt loses at most one batch and a re-run
    resumes rather than restarting. Rows whose ``metar_raw`` is NULL are left
    NULL — nothing can be inferred, and NULL already reads as routine.

    Returns the number of rows updated.
    """
    bounds = _id_bounds(db)
    if bounds is None:
        return 0

    lo, hi = bounds
    updated = 0

    while lo <= hi:
        top = lo + batch_size
        for value, predicate in (
            (REPORT_TYPE_SPECI, VerificationObservationRow.metar_raw.like(_SPECI_PREFIX)),
            (REPORT_TYPE_METAR, ~VerificationObservationRow.metar_raw.like(_SPECI_PREFIX)),
        ):
            stmt = (
                update(VerificationObservationRow)
                .where(VerificationObservationRow.id >= lo)
                .where(VerificationObservationRow.id < top)
                .where(VerificationObservationRow.metar_raw.isnot(None))
                .where(predicate)
                .values(report_type=value)
            )
            if only_null:
                stmt = stmt.where(VerificationObservationRow.report_type.is_(None))
            updated += db.execute(stmt).rowcount or 0

        db.commit()
        logger.info(
            "report_type backfill: ids [%d, %d) done, %d rows updated so far",
            lo, top, updated,
        )
        lo = top

    return updated
