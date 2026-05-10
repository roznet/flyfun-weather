"""Rollup of raw observations into airport_monthly_summary + airport_daily_summary.

These tables power the public Patterns map (climatology metrics per airport)
and Spotlight leaderboards/heatmaps. Aggregation runs nightly at 02:00 UTC
once a calendar period (month/day) is complete; idempotent DELETE+INSERT.

Volatility convention: count of category transitions between consecutive
observations within the same UTC day, summed across the period. A flap of
VFR→MVFR→VFR within a day counts as 2 transitions.

Diurnal JSON convention (monthly only): keys are zero-padded HH strings
(00..23), values are dicts {n, n_ifr, n_ts, n_fog, n_precip, ceiling_p50}.
Hours with zero observations are omitted to keep the blob compact.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    AirportDailySummaryRow,
    AirportMonthlySummaryRow,
    VerificationObservationRow,
)

logger = logging.getLogger(__name__)


# Phenomenon detection: a weather list (e.g. ["+TSRA", "BR"]) matches a
# phenomenon if any code contains the marker substring. "TSRA" matches both
# TS and RA — intentional, since the obs has both. FG matches FG, BCFG,
# MIFG, PRFG, VCFG, FZFG. FZ matches FZRA, FZDZ, FZFG, FZUP.
_PHENOMENA = {
    "ts": ("TS",),
    "fg": ("FG",),
    "br": ("BR",),
    "haze": ("HZ",),
    "ra": ("RA",),
    "sn": ("SN",),
    "freezing": ("FZ",),
}
# Anything that reduces visibility on its own
_OBSCURATION_MARKERS = ("FG", "BR", "HZ", "FU", "SA", "DU", "VA")
# Any precipitation
_PRECIP_MARKERS = ("RA", "DZ", "SN", "SG", "IC", "PL", "GR", "GS", "UP")

_CAT_SEVERITY = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _has_marker(weather: list[str], markers: tuple[str, ...]) -> bool:
    """True if any code in *weather* contains any of *markers* as substring."""
    return any(any(m in code for m in markers) for code in weather)


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile. ``pct`` is 0–100. Returns None for empty."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    # Position in [0, n-1]
    pos = (len(s) - 1) * (pct / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _worst_category(cats: list[str]) -> str | None:
    """Return the severity-max non-null category, or None if none present."""
    valid = [c for c in cats if c in _CAT_SEVERITY]
    if not valid:
        return None
    return max(valid, key=lambda c: _CAT_SEVERITY[c])


def _category_changes_within_day(obs_list: list[VerificationObservationRow]) -> int:
    """Count category transitions between consecutive obs within each UTC day.

    Sorts by observation_time; counts pairs where both have a category and
    the categories differ. Cross-day pairs are not counted (we segment by
    UTC date first).
    """
    by_day: dict[date, list[VerificationObservationRow]] = defaultdict(list)
    for obs in obs_list:
        t = _ensure_utc(obs.observation_time)
        if t is None:
            continue
        by_day[t.date()].append(obs)

    total = 0
    for day_obs in by_day.values():
        day_obs.sort(key=lambda o: _ensure_utc(o.observation_time))
        prev: str | None = None
        for obs in day_obs:
            cat = obs.flight_category
            if cat is None:
                # Treat unknown as a hole — don't count adjacency over it.
                prev = None
                continue
            if prev is not None and prev != cat:
                total += 1
            prev = cat
    return total


def _build_diurnal_json(obs_list: list[VerificationObservationRow]) -> str | None:
    """Per-hour-of-day breakdown JSON, or None if obs_list empty.

    Only hours with at least one observation are emitted, keeping the blob
    compact for airports that only report at sample hours.
    """
    if not obs_list:
        return None
    by_hour: dict[int, list[VerificationObservationRow]] = defaultdict(list)
    for obs in obs_list:
        t = _ensure_utc(obs.observation_time)
        if t is None:
            continue
        by_hour[t.hour].append(obs)

    out: dict[str, dict[str, int | None]] = {}
    for h in sorted(by_hour):
        rows = by_hour[h]
        weathers = [json.loads(r.weather) if r.weather else [] for r in rows]
        ceilings = [r.ceiling_ft for r in rows if r.ceiling_ft is not None]
        out[f"{h:02d}"] = {
            "n": len(rows),
            "n_ifr": sum(1 for r in rows if r.flight_category in ("IFR", "LIFR")),
            "n_ts": sum(1 for w in weathers if _has_marker(w, _PHENOMENA["ts"])),
            "n_fog": sum(1 for w in weathers if _has_marker(w, _PHENOMENA["fg"])),
            "n_precip": sum(1 for w in weathers if _has_marker(w, _PRECIP_MARKERS)),
            "ceiling_p50": (
                int(_percentile([float(c) for c in ceilings], 50))
                if ceilings else None
            ),
        }
    return json.dumps(out, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Monthly rollup
# ---------------------------------------------------------------------------


def rollup_month(db: Session, month_start: datetime) -> int:
    """Aggregate raw observations for one month into airport_monthly_summary.

    ``month_start`` is the first instant of the month, UTC. Idempotent —
    rows for this month are deleted and re-inserted. Returns row count.
    """
    if month_start.tzinfo is None:
        month_start = month_start.replace(tzinfo=timezone.utc)

    if month_start.month == 12:
        month_end = datetime(month_start.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        month_end = datetime(
            month_start.year, month_start.month + 1, 1, tzinfo=timezone.utc,
        )

    # Wipe existing rows for this month (idempotency). Expire the session
    # afterwards so any cached AirportMonthlySummaryRow identity-map entries
    # don't fight with the new rows we're about to insert.
    db.execute(
        AirportMonthlySummaryRow.__table__.delete().where(
            AirportMonthlySummaryRow.month == month_start
        )
    )
    db.expunge_all()

    # Stream obs for this month grouped by ICAO. ~830 airports × ~150 obs ≈
    # 125k rows worst case — fits easily in memory.
    rows = db.execute(
        select(VerificationObservationRow)
        .where(VerificationObservationRow.observation_time >= month_start)
        .where(VerificationObservationRow.observation_time < month_end)
    ).scalars().all()

    by_icao: dict[str, list[VerificationObservationRow]] = defaultdict(list)
    for obs in rows:
        by_icao[obs.icao].append(obs)

    inserted = 0
    for icao, obs_list in by_icao.items():
        summary = _build_monthly_summary(month_start, icao, obs_list)
        db.add(summary)
        inserted += 1

    db.flush()
    logger.info(
        "airport_monthly_summary: rolled up %d airports for %s",
        inserted, month_start.strftime("%Y-%m"),
    )
    return inserted


def _build_monthly_summary(
    month_start: datetime,
    icao: str,
    obs_list: list[VerificationObservationRow],
) -> AirportMonthlySummaryRow:
    """Compute one summary row from a month's worth of observations for one airport."""
    weathers = [json.loads(o.weather) if o.weather else [] for o in obs_list]
    ceilings = [float(o.ceiling_ft) for o in obs_list if o.ceiling_ft is not None]
    visibilities = [float(o.visibility_m) for o in obs_list if o.visibility_m is not None]
    winds = [float(o.wind_speed_kt) for o in obs_list if o.wind_speed_kt is not None]
    gusts = [o.wind_gust_kt for o in obs_list if o.wind_gust_kt is not None]
    temps = [o.temperature_c for o in obs_list if o.temperature_c is not None]

    return AirportMonthlySummaryRow(
        month=month_start,
        icao=icao,
        n_obs=len(obs_list),
        # Category counts
        n_vfr=sum(1 for o in obs_list if o.flight_category == "VFR"),
        n_mvfr=sum(1 for o in obs_list if o.flight_category == "MVFR"),
        n_ifr=sum(1 for o in obs_list if o.flight_category == "IFR"),
        n_lifr=sum(1 for o in obs_list if o.flight_category == "LIFR"),
        # Phenomena
        n_obscuration=sum(1 for w in weathers if _has_marker(w, _OBSCURATION_MARKERS)),
        n_fg=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["fg"])),
        n_br=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["br"])),
        n_haze=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["haze"])),
        n_precip=sum(1 for w in weathers if _has_marker(w, _PRECIP_MARKERS)),
        n_ra=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["ra"])),
        n_sn=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["sn"])),
        n_ts=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["ts"])),
        n_freezing=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["freezing"])),
        # Ceiling percentiles
        ceiling_p10_ft=int(_percentile(ceilings, 10)) if ceilings else None,
        ceiling_p25_ft=int(_percentile(ceilings, 25)) if ceilings else None,
        ceiling_p50_ft=int(_percentile(ceilings, 50)) if ceilings else None,
        ceiling_p75_ft=int(_percentile(ceilings, 75)) if ceilings else None,
        ceiling_p90_ft=int(_percentile(ceilings, 90)) if ceilings else None,
        # Visibility percentiles
        visibility_p10_m=int(_percentile(visibilities, 10)) if visibilities else None,
        visibility_p25_m=int(_percentile(visibilities, 25)) if visibilities else None,
        visibility_p50_m=int(_percentile(visibilities, 50)) if visibilities else None,
        # Wind aggregates
        wind_mean_kt=(sum(winds) / len(winds)) if winds else None,
        wind_p50_kt=_percentile(winds, 50) if winds else None,
        wind_p95_kt=_percentile(winds, 95) if winds else None,
        gust_max_kt=max(gusts) if gusts else None,
        # Hours-below-threshold
        n_ceiling_below_500=sum(
            1 for o in obs_list if o.ceiling_ft is not None and o.ceiling_ft < 500
        ),
        n_ceiling_below_1500=sum(
            1 for o in obs_list if o.ceiling_ft is not None and o.ceiling_ft < 1500
        ),
        n_visibility_below_5km=sum(
            1 for o in obs_list if o.visibility_m is not None and o.visibility_m < 5000
        ),
        n_wind_over_25kt=sum(
            1 for o in obs_list if o.wind_speed_kt is not None and o.wind_speed_kt > 25
        ),
        # Temperature
        temp_min_c=min(temps) if temps else None,
        temp_max_c=max(temps) if temps else None,
        # Volatility
        category_changes=_category_changes_within_day(obs_list),
        # Diurnal
        diurnal_json=_build_diurnal_json(obs_list),
    )


# ---------------------------------------------------------------------------
# Daily rollup
# ---------------------------------------------------------------------------


def rollup_day(db: Session, day: date) -> int:
    """Aggregate raw observations for one UTC date into airport_daily_summary.

    Idempotent — rows for this date are deleted and re-inserted. Returns
    row count.
    """
    day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    db.execute(
        AirportDailySummaryRow.__table__.delete().where(
            AirportDailySummaryRow.date == day
        )
    )
    db.expunge_all()

    rows = db.execute(
        select(VerificationObservationRow)
        .where(VerificationObservationRow.observation_time >= day_start)
        .where(VerificationObservationRow.observation_time < day_end)
    ).scalars().all()

    by_icao: dict[str, list[VerificationObservationRow]] = defaultdict(list)
    for obs in rows:
        by_icao[obs.icao].append(obs)

    inserted = 0
    for icao, obs_list in by_icao.items():
        summary = _build_daily_summary(day, icao, obs_list)
        db.add(summary)
        inserted += 1

    db.flush()
    logger.info(
        "airport_daily_summary: rolled up %d airports for %s",
        inserted, day.isoformat(),
    )
    return inserted


def _build_daily_summary(
    day: date,
    icao: str,
    obs_list: list[VerificationObservationRow],
) -> AirportDailySummaryRow:
    weathers = [json.loads(o.weather) if o.weather else [] for o in obs_list]
    ceilings = [float(o.ceiling_ft) for o in obs_list if o.ceiling_ft is not None]
    visibilities = [float(o.visibility_m) for o in obs_list if o.visibility_m is not None]
    winds = [o.wind_speed_kt for o in obs_list if o.wind_speed_kt is not None]
    gusts = [o.wind_gust_kt for o in obs_list if o.wind_gust_kt is not None]
    temps = [o.temperature_c for o in obs_list if o.temperature_c is not None]
    cats = [o.flight_category for o in obs_list]

    return AirportDailySummaryRow(
        date=day,
        icao=icao,
        n_obs=len(obs_list),
        worst_category=_worst_category(cats),
        n_vfr=sum(1 for c in cats if c == "VFR"),
        n_mvfr=sum(1 for c in cats if c == "MVFR"),
        n_ifr=sum(1 for c in cats if c == "IFR"),
        n_lifr=sum(1 for c in cats if c == "LIFR"),
        n_obscuration=sum(1 for w in weathers if _has_marker(w, _OBSCURATION_MARKERS)),
        n_fg=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["fg"])),
        n_br=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["br"])),
        n_precip=sum(1 for w in weathers if _has_marker(w, _PRECIP_MARKERS)),
        n_ts=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["ts"])),
        n_freezing=sum(1 for w in weathers if _has_marker(w, _PHENOMENA["freezing"])),
        ceiling_min_ft=int(min(ceilings)) if ceilings else None,
        ceiling_p10_ft=int(_percentile(ceilings, 10)) if ceilings else None,
        ceiling_p50_ft=int(_percentile(ceilings, 50)) if ceilings else None,
        visibility_min_m=int(min(visibilities)) if visibilities else None,
        visibility_p50_m=int(_percentile(visibilities, 50)) if visibilities else None,
        wind_max_kt=max(winds) if winds else None,
        gust_max_kt=max(gusts) if gusts else None,
        temp_min_c=min(temps) if temps else None,
        temp_max_c=max(temps) if temps else None,
    )


# ---------------------------------------------------------------------------
# Orchestrators — walk forward through completed periods
# ---------------------------------------------------------------------------


def completed_months(db: Session) -> list[datetime]:
    """First-of-month datetimes (UTC) for fully-elapsed months that still
    need rolling up. A month is "complete" once the current UTC time is
    past its end. Already-rolled-up months are excluded.
    """
    now = datetime.now(timezone.utc)
    earliest = db.execute(
        select(func.min(VerificationObservationRow.observation_time))
    ).scalar()
    if earliest is None:
        return []
    earliest = _ensure_utc(earliest)

    existing = {
        (dt.year, dt.month)
        for dt in db.execute(
            select(AirportMonthlySummaryRow.month).distinct()
        ).scalars().all()
        if dt is not None
    }

    months: list[datetime] = []
    y, m = earliest.year, earliest.month
    while True:
        month_start = datetime(y, m, 1, tzinfo=timezone.utc)
        next_start = (
            datetime(y + 1, 1, 1, tzinfo=timezone.utc)
            if m == 12
            else datetime(y, m + 1, 1, tzinfo=timezone.utc)
        )
        if next_start <= now and (y, m) not in existing:
            months.append(month_start)
        if next_start > now:
            break
        y, m = next_start.year, next_start.month
    return months


def completed_days(db: Session) -> list[date]:
    """UTC dates for fully-elapsed days that still need rolling up.

    Excludes the current UTC day (incomplete) and dates already summarised.
    """
    now = datetime.now(timezone.utc)
    today = now.date()
    earliest = db.execute(
        select(func.min(VerificationObservationRow.observation_time))
    ).scalar()
    if earliest is None:
        return []
    earliest = _ensure_utc(earliest)

    existing = set(
        db.execute(select(AirportDailySummaryRow.date).distinct()).scalars().all()
    )

    out: list[date] = []
    d = earliest.date()
    while d < today:
        if d not in existing:
            out.append(d)
        d += timedelta(days=1)
    return out


def rollup_all_complete_months(db: Session) -> int:
    """Roll up every completed month not yet in airport_monthly_summary.

    Caller commits — matches the convention used by verification_rollup.
    Returns total rows inserted across all months.
    """
    total = 0
    for month_start in completed_months(db):
        total += rollup_month(db, month_start)
    return total


def rollup_all_complete_days(db: Session) -> int:
    """Roll up every completed UTC day not yet in airport_daily_summary.

    Caller commits.
    """
    total = 0
    for d in completed_days(db):
        total += rollup_day(db, d)
    return total
