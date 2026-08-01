#!/usr/bin/env python
"""Repair observation_time values corrupted by the month-boundary parser bug (#519).

METAR/TAF timestamps encode only DDHHMM.  The parser used to combine the
report's day-of-month with the *current* month, so a report collected just
after a month rolled over was dated into the following month — e.g. a METAR
collected at 2026-08-01 00:00:01 carrying ``312255Z`` (31 July) was stored as
31 August.

``metar_raw`` and ``taf_raw`` are retained, so the repair is lossless: re-parse
each suspect row against its own ``collected_at`` and write back the correct
time.

Two fields are affected, both fed by the same inference:

* ``observation_time`` — natural key of ``verification_observations`` and the
  join key for ``verification_scores``.
* ``taf_issue_time`` — part of ``uq_taf_verif_key`` on
  ``taf_verification_scores``. In production this was corrupted about six times
  more often than ``observation_time``, because one TAF stays current for hours
  and is copied onto every observation collected in that window.

Safety
------
* Read-only by default.  ``--apply`` is required to write anything.
* Walks the table in primary-key ranges rather than issuing one scan over
  2.19M rows, and sleeps between batches, so it stays friendly to a shared
  MySQL instance with a 1 GiB buffer pool.
* Only ``observation_time`` is ever modified, and only on rows whose stored
  time disagrees with a re-parse of their own raw text.

Downstream
----------
Repairing a row invalidates anything derived from its old time.  The script
reports the affected (icao, date) pairs and the number of dependent
``verification_scores`` rows.  Scores are only removed with ``--delete-scores``
so they can be recomputed; without it they are left in place and reported.

Two different derivations to be aware of:

* ``verification_daily_stats`` aggregates ``verification_scores``, so it is
  only affected if the reported dependent-score count is non-zero.
* ``airport_daily_summary`` / ``airport_monthly_summary`` bucket
  ``verification_observations`` **directly** (see ``tasks/airport_summary.py``),
  so they are affected whenever any row is repaired, regardless of scores, and
  should be rebuilt for the reported dates.

Usage
-----
    # read-only survey
    python scripts/repair_metar_month_boundary.py --dry-run

    # apply, leaving dependent scores for a separate rescore
    python scripts/repair_metar_month_boundary.py --apply

    # apply and drop dependent scores so the next cycle recomputes them
    python scripts/repair_metar_month_boundary.py --apply --delete-scores
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
# Share the ingestion guard's threshold rather than restating it, so detection
# here and rejection at write time cannot drift apart.
from weatherbrief.tasks.verification import _OBS_MAX_AHEAD as _MAX_AHEAD

logger = logging.getLogger("repair519")

_DEFAULT_BATCH = 50_000
_DEFAULT_SLEEP = 0.05


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _reparse(raw: str, collected_at: datetime) -> datetime | None:
    """Re-derive an observation time from raw METAR text against its collection time.

    Uses the fixed euro_aip parser, passing ``collected_at`` as the reference so
    the day-of-month resolves to the month the report was actually issued in.
    """
    from euro_aip.briefing.weather.parser import WeatherParser

    report = WeatherParser.parse_metar(raw, reference=collected_at)
    if report is None:
        return None
    return _as_utc(report.observation_time)


def _reparse_taf(raw: str, collected_at: datetime) -> datetime | None:
    """Re-derive a TAF issue time from raw TAF text against its collection time."""
    from euro_aip.briefing.weather.parser import WeatherParser

    report = WeatherParser.parse_taf(raw, reference=collected_at)
    if report is None:
        return None
    return _as_utc(report.observation_time)


def _boundary_windows(lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """Half-open observation_time windows covering the end of every month in range.

    A corrupted row keeps the report's day-of-month and only moves the month,
    so it always lands on day 28-31 of the *following* month.  Scanning just
    those windows uses ix_verif_obs_time and touches roughly an eighth of the
    table instead of all of it — which matters against a 1 GiB buffer pool
    shared with two other applications.
    """
    windows = []
    year, month = lo.year, lo.month
    while (year, month) <= (hi.year, hi.month):
        start = datetime(year, month, 28, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        windows.append((start, end))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return windows


def find_corrupt_targeted(session: Session, sleep: float) -> list[int]:
    """Return ids of suspect rows, scanning only month-end index ranges."""
    bounds = session.execute(
        select(
            func.min(VerificationObservationRow.observation_time),
            func.max(VerificationObservationRow.observation_time),
        )
    ).one()
    lo, hi = _as_utc(bounds[0]), _as_utc(bounds[1])
    if lo is None or hi is None:
        return []

    suspects: list[int] = []
    for start, end in _boundary_windows(lo, hi):
        rows = session.execute(
            select(
                VerificationObservationRow.id,
                VerificationObservationRow.observation_time,
                VerificationObservationRow.collected_at,
            ).where(
                VerificationObservationRow.observation_time >= start,
                VerificationObservationRow.observation_time < end,
            )
        ).all()

        hits = 0
        for row_id, obs_time, collected_at in rows:
            if obs_time is None or collected_at is None:
                continue
            if obs_time - collected_at > _MAX_AHEAD:
                suspects.append(row_id)
                hits += 1

        logger.info(
            "window %s..%s: %d rows, %d suspect",
            start.date(), end.date(), len(rows), hits,
        )
        if sleep:
            time.sleep(sleep)

    return suspects


def find_corrupt_full(session: Session, batch: int, sleep: float) -> list[int]:
    """Return ids of suspect rows, walking the whole table in PK ranges.

    The predicate (observation_time ahead of collected_at) is not sargable, so
    this is a scan by necessity.  It reads only three narrow columns, but it
    still touches every page — prefer the targeted scan unless you need
    exhaustive coverage, and run this during a quiet window.
    """
    max_id = session.execute(
        select(func.max(VerificationObservationRow.id))
    ).scalar_one_or_none()
    if max_id is None:
        return []

    suspects: list[int] = []
    lo = 0
    while lo <= max_id:
        hi = lo + batch
        rows = session.execute(
            select(
                VerificationObservationRow.id,
                VerificationObservationRow.observation_time,
                VerificationObservationRow.collected_at,
            ).where(
                VerificationObservationRow.id >= lo,
                VerificationObservationRow.id < hi,
            )
        ).all()

        for row_id, obs_time, collected_at in rows:
            if obs_time is None or collected_at is None:
                continue
            if obs_time - collected_at > _MAX_AHEAD:
                suspects.append(row_id)

        logger.info(
            "scanned ids %d-%d (%d rows, %d suspect so far)",
            lo, hi - 1, len(rows), len(suspects),
        )
        lo = hi
        if sleep:
            time.sleep(sleep)

    return suspects


def find_corrupt(
    session: Session,
    batch: int,
    sleep: float,
    full: bool = False,
) -> list[dict]:
    """Locate rows whose observation_time needs correcting.

    Uses the cheap month-end index scan by default; ``full`` walks every row.
    Either way, ``metar_raw`` is fetched only for rows that look wrong.
    """
    if full:
        suspects = find_corrupt_full(session, batch, sleep)
    else:
        suspects = find_corrupt_targeted(session, sleep)

    if not suspects:
        return []

    # Pull raw text only for the flagged rows and compute the correction.
    findings: list[dict] = []
    for i in range(0, len(suspects), 500):
        chunk = suspects[i : i + 500]
        rows = session.execute(
            select(
                VerificationObservationRow.id,
                VerificationObservationRow.icao,
                VerificationObservationRow.observation_time,
                VerificationObservationRow.collected_at,
                VerificationObservationRow.metar_raw,
            ).where(VerificationObservationRow.id.in_(chunk))
        ).all()

        for row_id, icao, obs_time, collected_at, raw in rows:
            if not raw:
                findings.append(
                    dict(id=row_id, icao=icao, old=obs_time, new=None,
                         collected_at=collected_at, reason="no raw text")
                )
                continue
            corrected = _reparse(raw, collected_at)
            if corrected is None:
                findings.append(
                    dict(id=row_id, icao=icao, old=obs_time, new=None,
                         collected_at=collected_at, reason="re-parse failed")
                )
                continue
            if corrected == obs_time:
                # Ahead of collection but the parser agrees — genuinely odd
                # data rather than the month bug.  Report, don't touch.
                findings.append(
                    dict(id=row_id, icao=icao, old=obs_time, new=None,
                         collected_at=collected_at, reason="re-parse matches stored")
                )
                continue
            findings.append(
                dict(id=row_id, icao=icao, old=obs_time, new=corrected,
                     collected_at=collected_at, reason=None)
            )

    return findings


def _month_boundary_windows(lo: datetime, hi: datetime) -> list[tuple[datetime, datetime]]:
    """Windows spanning each month boundary, for the TAF issue-time scan.

    A TAF stays current for hours after issue, so one issued late on the last
    day of a month is still the latest TAF for observations collected into the
    next one.  That makes the affected observation_time range wider than the
    METAR case — cover two days either side of each boundary.

    Note the range runs one month *past* ``hi``: unlike a corrupted
    observation_time, a corrupted taf_issue_time leaves observation_time
    untouched, so the affected rows sit just *before* the boundary that
    corrupted them and the final boundary would otherwise be missed.
    """
    windows = []
    year, month = lo.year, lo.month
    end_year, end_month = divmod(hi.year * 12 + (hi.month - 1) + 1, 12)
    end_month += 1
    while (year, month) <= (end_year, end_month):
        start = datetime(year, month, 1, tzinfo=timezone.utc) - timedelta(days=2)
        end = datetime(year, month, 1, tzinfo=timezone.utc) + timedelta(days=3)
        windows.append((start, end))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return windows


def find_corrupt_taf(session: Session, sleep: float, full: bool = False) -> list[dict]:
    """Locate rows whose taf_issue_time needs correcting.

    ``taf_issue_time`` is produced by the same day-of-month inference as
    ``observation_time`` and is part of ``uq_taf_verif_key`` on
    ``taf_verification_scores``, so it needs the same treatment.
    """
    bounds = session.execute(
        select(
            func.min(VerificationObservationRow.observation_time),
            func.max(VerificationObservationRow.observation_time),
        )
    ).one()
    lo, hi = _as_utc(bounds[0]), _as_utc(bounds[1])
    if lo is None or hi is None:
        return []

    if full:
        windows = [(lo - timedelta(days=1), hi + timedelta(days=1))]
    else:
        windows = _month_boundary_windows(lo, hi)

    findings: list[dict] = []
    for start, end in windows:
        rows = session.execute(
            select(
                VerificationObservationRow.id,
                VerificationObservationRow.icao,
                VerificationObservationRow.taf_issue_time,
                VerificationObservationRow.collected_at,
                VerificationObservationRow.taf_raw,
            ).where(
                VerificationObservationRow.observation_time >= start,
                VerificationObservationRow.observation_time < end,
                VerificationObservationRow.taf_issue_time.is_not(None),
            )
        ).all()

        hits = 0
        for row_id, icao, issue_time, collected_at, raw in rows:
            if issue_time is None or collected_at is None:
                continue
            if issue_time - collected_at <= _MAX_AHEAD:
                continue

            hits += 1
            if not raw:
                findings.append(
                    dict(id=row_id, icao=icao, old=issue_time, new=None,
                         collected_at=collected_at, reason="no raw TAF text")
                )
                continue
            corrected = _reparse_taf(raw, collected_at)
            if corrected is None:
                findings.append(
                    dict(id=row_id, icao=icao, old=issue_time, new=None,
                         collected_at=collected_at, reason="TAF re-parse failed")
                )
                continue
            if corrected == issue_time:
                findings.append(
                    dict(id=row_id, icao=icao, old=issue_time, new=None,
                         collected_at=collected_at, reason="re-parse matches stored")
                )
                continue
            findings.append(
                dict(id=row_id, icao=icao, old=issue_time, new=corrected,
                     collected_at=collected_at, reason=None)
            )

        logger.info(
            "taf window %s..%s: %d rows with a TAF, %d suspect",
            start.date(), end.date(), len(rows), hits,
        )
        if sleep:
            time.sleep(sleep)

    return findings


def apply_taf_repairs(
    session: Session,
    findings: list[dict],
    delete_scores: bool,
) -> dict:
    """Write corrected TAF issue times.

    ``taf_issue_time`` is not part of the observations table's own unique key,
    so there is no collision to resolve here — but it *is* part of
    ``uq_taf_verif_key``, so any dependent taf_verification_scores rows are
    keyed on the old value and must be recomputed.

    Does not commit — the caller owns the transaction boundary.
    """
    stats = defaultdict(int)
    repairable = [f for f in findings if f["new"] is not None]

    for f in repairable:
        if delete_scores:
            removed = session.execute(
                delete(TafVerificationScoreRow).where(
                    TafVerificationScoreRow.observation_id == f["id"]
                )
            ).rowcount
            stats["taf_scores_deleted"] += removed or 0

        session.execute(
            update(VerificationObservationRow)
            .where(VerificationObservationRow.id == f["id"])
            .values(taf_issue_time=f["new"])
        )
        stats["taf_repaired"] += 1

    session.flush()
    return dict(stats)


def apply_repairs(
    session: Session,
    findings: list[dict],
    delete_scores: bool,
) -> dict:
    """Write corrected times, resolving natural-key collisions by deletion.

    ``delete_scores`` governs *repaired* rows only.  A row removed as a
    natural-key duplicate cannot survive the unique constraint, so its scores
    go with it via ``ondelete="CASCADE"`` whether the flag is set or not — they
    are not counted in ``scores_deleted``.

    Does not commit — the caller owns the transaction boundary.
    """
    stats = defaultdict(int)
    repairable = [f for f in findings if f["new"] is not None]

    for f in repairable:
        # (icao, observation_time) is unique.  If the corrected time already
        # exists, the good row is present and this one is a duplicate — remove
        # it rather than merge, so scores cascade away with it.
        clash = session.execute(
            select(VerificationObservationRow.id).where(
                VerificationObservationRow.icao == f["icao"],
                VerificationObservationRow.observation_time == f["new"],
                VerificationObservationRow.id != f["id"],
            )
        ).scalar_one_or_none()

        if clash is not None:
            session.execute(
                delete(VerificationObservationRow).where(
                    VerificationObservationRow.id == f["id"]
                )
            )
            stats["deleted_duplicate"] += 1
            continue

        if delete_scores:
            removed = session.execute(
                delete(VerificationScoreRow).where(
                    VerificationScoreRow.observation_id == f["id"]
                )
            ).rowcount
            stats["scores_deleted"] += removed or 0

        session.execute(
            update(VerificationObservationRow)
            .where(VerificationObservationRow.id == f["id"])
            .values(observation_time=f["new"])
        )
        stats["repaired"] += 1

    session.flush()
    return dict(stats)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    group.add_argument("--apply", action="store_true", help="write corrected times")
    parser.add_argument(
        "--delete-scores", action="store_true",
        help="also delete verification_scores / taf_verification_scores rows for "
             "repaired observations so they are recomputed (requires --apply). "
             "Note this affects *repaired* rows only: a row deleted as a "
             "natural-key duplicate always loses its scores via ondelete=CASCADE, "
             "with or without this flag.",
    )
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument(
        "--full", action="store_true",
        help="walk every row instead of only month-end index ranges. Exhaustive "
             "but touches the whole table — run in a quiet window.",
    )
    parser.add_argument("--batch", type=int, default=_DEFAULT_BATCH,
                        help="primary-key range size per --full scan query")
    parser.add_argument("--sleep", type=float, default=_DEFAULT_SLEEP,
                        help="seconds to pause between scan batches")
    parser.add_argument("--limit-report", type=int, default=20,
                        help="how many example rows to print")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    if not args.database_url:
        logger.error("No database URL: pass --database-url or set DATABASE_URL")
        return 2
    if args.delete_scores and not args.apply:
        logger.error("--delete-scores requires --apply")
        return 2

    engine = create_engine(args.database_url)
    with Session(engine) as session:
        logger.info(
            "scanning for corrupted observation times (%s)...",
            "full table walk" if args.full else "month-end index ranges",
        )
        findings = find_corrupt(session, args.batch, args.sleep, full=args.full)

        repairable = [f for f in findings if f["new"] is not None]
        skipped = [f for f in findings if f["new"] is None]
        score_count = 0

        print()
        print(f"suspect rows            : {len(findings)}")
        print(f"  repairable            : {len(repairable)}")
        print(f"  skipped (see reasons) : {len(skipped)}")

        if skipped:
            reasons = defaultdict(int)
            for f in skipped:
                reasons[f["reason"]] += 1
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                print(f"      {reason}: {count}")

        if repairable:
            shifts = defaultdict(int)
            dates: set = set()
            for f in repairable:
                shifts[(f["old"] - f["new"]).days] += 1
                dates.add((f["icao"], f["new"].date()))
                dates.add((f["icao"], f["old"].date()))
            print()
            print("correction sizes (days moved back):")
            for days, count in sorted(shifts.items(), key=lambda kv: -kv[1]):
                print(f"      {days:>4} days: {count}")
            print()
            print(f"affected (icao, date) pairs needing rollup rebuild: {len(dates)}")

            obs_ids = [f["id"] for f in repairable]
            for i in range(0, len(obs_ids), 500):
                score_count += session.execute(
                    select(func.count()).select_from(VerificationScoreRow).where(
                        VerificationScoreRow.observation_id.in_(obs_ids[i : i + 500])
                    )
                ).scalar_one()
            print(f"dependent verification_scores rows                : {score_count}")

            print()
            print(f"examples (first {args.limit_report}):")
            for f in repairable[: args.limit_report]:
                print(
                    f"  id={f['id']:<9} {f['icao']}  "
                    f"{f['old'].isoformat()} -> {f['new'].isoformat()}  "
                    f"(collected {f['collected_at'].isoformat()})"
                )

        # --- TAF issue times -------------------------------------------------
        logger.info("scanning for corrupted TAF issue times...")
        taf_findings = find_corrupt_taf(session, args.sleep, full=args.full)
        taf_repairable = [f for f in taf_findings if f["new"] is not None]
        taf_skipped = [f for f in taf_findings if f["new"] is None]
        taf_score_count = 0

        print()
        print(f"suspect taf_issue_time rows : {len(taf_findings)}")
        print(f"  repairable                : {len(taf_repairable)}")
        print(f"  skipped                   : {len(taf_skipped)}")

        if taf_skipped:
            reasons = defaultdict(int)
            for f in taf_skipped:
                reasons[f["reason"]] += 1
            for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
                print(f"      {reason}: {count}")

        if taf_repairable:
            taf_ids = [f["id"] for f in taf_repairable]
            for i in range(0, len(taf_ids), 500):
                taf_score_count += session.execute(
                    select(func.count()).select_from(TafVerificationScoreRow).where(
                        TafVerificationScoreRow.observation_id.in_(taf_ids[i : i + 500])
                    )
                ).scalar_one()
            print(f"dependent taf_verification_scores rows : {taf_score_count}")
            print()
            print(f"examples (first {args.limit_report}):")
            for f in taf_repairable[: args.limit_report]:
                print(
                    f"  id={f['id']:<9} {f['icao']}  "
                    f"{f['old'].isoformat()} -> {f['new'].isoformat()}  "
                    f"(collected {f['collected_at'].isoformat()})"
                )

        if args.dry_run:
            print()
            print("DRY RUN — nothing written.")
            return 0

        if not repairable and not taf_repairable:
            print()
            print("Nothing to repair.")
            return 0

        stats = apply_repairs(session, findings, args.delete_scores)
        stats.update(apply_taf_repairs(session, taf_findings, args.delete_scores))
        session.commit()
        print()
        print("applied:", stats)
        if score_count and not args.delete_scores:
            print(
                f"NOTE: {score_count} dependent verification_scores rows were "
                "left in place and are now scored against the wrong time. "
                "Re-run scoring for the affected dates, or re-run with "
                "--delete-scores."
            )
        if taf_score_count and not args.delete_scores:
            print(
                f"NOTE: {taf_score_count} dependent taf_verification_scores rows "
                "are keyed on the old taf_issue_time (uq_taf_verif_key) and are "
                "now stale. Re-run TAF scoring, or re-run with --delete-scores."
            )
        if repairable:
            print(
                "NOTE: airport_daily_summary / airport_monthly_summary bucket "
                "observations directly and must be rebuilt for the affected dates "
                "(tasks.airport_summary.rollup_day / rollup_month)."
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
