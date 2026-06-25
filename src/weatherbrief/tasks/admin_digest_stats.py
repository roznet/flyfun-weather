"""Query functions for the unified admin daily digest.

Gathers user, flight, briefing, cost, performance, donation, and
flight-debrief stats for a given time period.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from flyfun_common.db.models import CostLedgerRow, DonationRow, UserRow
from weatherbrief.db.models import (
    BriefingUsageRow,
    FlightDebriefRow,
    FlightRow,
)
from weatherbrief.debriefs.taxonomy import Decision, OutcomeValue
from weatherbrief.models.verification import (
    AdminDigestData,
    DebriefInfo,
    DebriefsSectionData,
    DonationInfo,
    DonationsSectionData,
    FlightsBriefingsSectionData,
    NewUserInfo,
    PerformanceSectionData,
    UsersSectionData,
)

logger = logging.getLogger(__name__)

_RECENT_DEBRIEF_LIMIT = 5

_SERVICE = "flyfun-weather"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _get_users_section(db: Session, since: datetime, until: datetime) -> UsersSectionData:
    """User stats: new signups, active, total."""
    # New users in period
    new_rows = db.execute(
        select(UserRow.email, UserRow.display_name).where(
            UserRow.created_at.between(since, until),
        )
    ).all()

    # Active users: distinct user_ids with briefing usage in period
    active_count = db.execute(
        select(func.count(func.distinct(BriefingUsageRow.user_id))).where(
            BriefingUsageRow.timestamp.between(since, until),
        )
    ).scalar() or 0

    # Total registered users
    total_count = db.execute(select(func.count(UserRow.id))).scalar() or 0

    return UsersSectionData(
        new_users=[
            NewUserInfo(email=email, display_name=name or "")
            for email, name in new_rows
        ],
        new_user_count=len(new_rows),
        active_user_count=active_count,
        total_user_count=total_count,
    )


def _get_flights_briefings_section(
    db: Session, since: datetime, until: datetime,
) -> FlightsBriefingsSectionData:
    """Flight and briefing stats for the period."""
    # New flights
    new_flights = db.execute(
        select(func.count(FlightRow.id)).where(
            FlightRow.created_at.between(since, until),
        )
    ).scalar() or 0

    # Briefing counts by trigger type
    usage_rows = db.execute(
        select(
            func.count(BriefingUsageRow.id),
            func.sum(case(
                (BriefingUsageRow.triggered_by == "scheduler", 1),
                else_=0,
            )),
            func.sum(case(
                (BriefingUsageRow.gramet_fetched.is_(True), 1),
                else_=0,
            )),
            func.sum(case(
                (BriefingUsageRow.llm_digest.is_(True), 1),
                else_=0,
            )),
        ).where(BriefingUsageRow.timestamp.between(since, until))
    ).one()

    total_briefings = usage_rows[0] or 0
    auto_briefings = int(usage_rows[1] or 0)
    manual_briefings = total_briefings - auto_briefings
    gramet_count = int(usage_rows[2] or 0)
    digest_count = int(usage_rows[3] or 0)

    # Flights with single vs multiple briefings in period
    flight_pack_counts = db.execute(
        select(
            BriefingUsageRow.flight_id,
            func.count(BriefingUsageRow.id).label("pack_count"),
        )
        .where(
            BriefingUsageRow.timestamp.between(since, until),
            BriefingUsageRow.flight_id != "",
        )
        .group_by(BriefingUsageRow.flight_id)
    ).all()

    single = sum(1 for _, cnt in flight_pack_counts if cnt == 1)
    refreshed = sum(1 for _, cnt in flight_pack_counts if cnt > 1)

    return FlightsBriefingsSectionData(
        new_flights=new_flights,
        total_briefings=total_briefings,
        manual_briefings=manual_briefings,
        auto_briefings=auto_briefings,
        flights_single_briefing=single,
        flights_refreshed=refreshed,
        gramet_count=gramet_count,
        digest_count=digest_count,
    )


def _get_performance_section(
    db: Session, since: datetime, until: datetime,
) -> PerformanceSectionData:
    """Cost, tokens, disk, and pipeline performance."""
    # Cost
    total_cost = db.execute(
        select(func.coalesce(func.sum(CostLedgerRow.cost), 0.0)).where(
            CostLedgerRow.service == _SERVICE,
            CostLedgerRow.created_at.between(since, until),
        )
    ).scalar() or 0.0

    # Tokens and performance from BriefingUsageRow
    perf_row = db.execute(
        select(
            func.coalesce(func.sum(BriefingUsageRow.llm_input_tokens), 0),
            func.coalesce(func.sum(BriefingUsageRow.llm_output_tokens), 0),
            func.avg(BriefingUsageRow.elapsed_seconds),
            func.avg(BriefingUsageRow.queue_wait_seconds),
            func.count(BriefingUsageRow.id),
        ).where(BriefingUsageRow.timestamp.between(since, until))
    ).one()

    avg_elapsed = round(perf_row[2], 2) if perf_row[2] is not None else None
    avg_queue = round(perf_row[3], 2) if perf_row[3] is not None else None

    # Disk usage
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    packs_dir = data_dir / "packs"
    total_disk = 0
    if packs_dir.is_dir():
        for dirpath, _dirnames, filenames in os.walk(packs_dir):
            for f in filenames:
                try:
                    total_disk += os.path.getsize(os.path.join(dirpath, f))
                except OSError:
                    pass

    return PerformanceSectionData(
        total_cost_usd=float(total_cost),
        total_llm_input_tokens=int(perf_row[0]),
        total_llm_output_tokens=int(perf_row[1]),
        total_disk_bytes=total_disk,
        avg_elapsed_seconds=avg_elapsed,
        avg_queue_wait_seconds=avg_queue,
        briefing_count_for_perf=int(perf_row[4]),
    )


def _get_donations_section(
    db: Session, since: datetime, until: datetime,
) -> DonationsSectionData:
    """Donations received in the period (succeeded only, refunds excluded)."""
    rows = db.execute(
        select(DonationRow)
        .where(
            DonationRow.service == _SERVICE,
            DonationRow.status == "succeeded",
            DonationRow.created_at.between(since, until),
        )
        .order_by(DonationRow.created_at.desc())
    ).scalars().all()

    # Resolve donor emails for attributed donations in one query.
    user_ids = {r.user_id for r in rows if r.user_id}
    emails: dict[str, str] = {}
    if user_ids:
        emails = dict(
            db.execute(
                select(UserRow.id, UserRow.email).where(UserRow.id.in_(user_ids))
            ).all()
        )

    donations = [
        DonationInfo(
            amount_usd=float(r.amount_usd),
            amount=float(r.amount),
            currency=r.currency,
            recurring=bool(r.recurring),
            donor=emails.get(r.user_id, "") if r.user_id else "anonymous",
        )
        for r in rows
    ]

    return DonationsSectionData(
        count=len(donations),
        total_usd=sum(d.amount_usd for d in donations),
        donations=donations,
    )


def _debrief_summary(row: FlightDebriefRow) -> str:
    """One-line reason summary: cancel reasons, or the categories that went worse."""
    if row.decision == Decision.CANCELLED.value and row.reasons_json:
        tags = json.loads(row.reasons_json)
        return ", ".join(tags) if tags else ""
    if row.decision == Decision.FLOWN.value and row.outcomes_json:
        outcomes = json.loads(row.outcomes_json)
        worse = [k for k, v in outcomes.items() if v == OutcomeValue.WORSE.value]
        better = [k for k, v in outcomes.items() if v == OutcomeValue.BETTER.value]
        bits = []
        if worse:
            bits.append("worse: " + ", ".join(worse))
        if better:
            bits.append("better: " + ", ".join(better))
        return " · ".join(bits) if bits else "as forecast"
    return ""


def _get_debriefs_section(
    db: Session, since: datetime, until: datetime,
) -> DebriefsSectionData:
    """Flight debriefs filed in the period: counts by decision + last 5 with reasons."""
    rows = db.execute(
        select(FlightDebriefRow, FlightRow.route_name)
        .join(FlightRow, FlightDebriefRow.flight_id == FlightRow.id)
        .where(FlightDebriefRow.created_at.between(since, until))
        .order_by(FlightDebriefRow.created_at.desc())
    ).all()

    flown = sum(1 for d, _ in rows if d.decision == Decision.FLOWN.value)
    cancelled = sum(1 for d, _ in rows if d.decision == Decision.CANCELLED.value)
    monitoring = sum(1 for d, _ in rows if d.decision == Decision.MONITORING.value)

    recent = [
        DebriefInfo(
            route_name=route_name or "(unnamed)",
            decision=d.decision,
            summary=_debrief_summary(d),
            note=d.note or "",
        )
        for d, route_name in rows[:_RECENT_DEBRIEF_LIMIT]
    ]

    return DebriefsSectionData(
        total_count=len(rows),
        flown_count=flown,
        cancelled_count=cancelled,
        monitoring_count=monitoring,
        recent=recent,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def get_admin_digest_data(
    db: Session,
    since: datetime,
    until: datetime,
    *,
    period_label: str = "",
    base_url: str = "",
) -> AdminDigestData:
    """Build complete admin digest payload."""
    return AdminDigestData(
        period_label=period_label,
        users=_get_users_section(db, since, until),
        flights_briefings=_get_flights_briefings_section(db, since, until),
        performance=_get_performance_section(db, since, until),
        donations=_get_donations_section(db, since, until),
        debriefs=_get_debriefs_section(db, since, until),
    )
