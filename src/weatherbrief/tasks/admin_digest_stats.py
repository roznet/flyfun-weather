"""Query functions for the unified admin daily digest.

Gathers user, flight, briefing, cost, performance, and verification
stats for a given time period.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from flyfun_common.db.models import CostLedgerRow, UserRow
from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow, FlightRow
from weatherbrief.models.verification import (
    AdminDigestData,
    FlightsBriefingsSectionData,
    NewUserInfo,
    PerformanceSectionData,
    UsersSectionData,
    VerificationSectionData,
)
from weatherbrief.tasks.verification_stats import (
    get_category_accuracy,
    get_notable_misses,
    get_wind_advisory_accuracy,
)

logger = logging.getLogger(__name__)

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


def _get_verification_section(
    db: Session, since: datetime, until: datetime,
    base_url: str = "",
) -> VerificationSectionData:
    """Condensed verification: accuracy matrix D-0..D-3, notable miss count, wind."""
    # Use standalone source for broader coverage
    accuracy = get_category_accuracy(db, since, until, source="standalone")
    notable = get_notable_misses(db, since, until, source="standalone")
    wind = get_wind_advisory_accuracy(db, since, until, source="standalone")

    dashboard_url = f"{base_url}/admin.html#verification" if base_url else ""

    return VerificationSectionData(
        category_accuracy=accuracy,
        notable_miss_count=len(notable),
        wind_advisory=wind,
        dashboard_url=dashboard_url,
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
        verification=_get_verification_section(db, since, until, base_url),
    )
