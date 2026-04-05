#!/usr/bin/env python3
"""Verification pipeline audit — independent data validation.

Usage:
    python scripts/audit_verification.py EGLL              # full-day audit for airport
    python scripts/audit_verification.py EGLL --hours 6    # last 6 hours
    python scripts/audit_verification.py --misses 10       # spot-check top 10 misses
    python scripts/audit_verification.py --sanity           # statistical sanity checks
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from weatherbrief.analysis.airport_conditions import classify_flight_category
from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    VerificationObservationRow,
    VerificationScoreRow,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

_AWC_URL = "https://aviationweather.gov/api/data/metar"
_M_PER_SM = 1609.34
_CAT_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}


def _get_engine():
    """Get SQLAlchemy engine from environment."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        # Try loading from .env
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            for line in open(env_path):
                if line.startswith("DATABASE_URL="):
                    db_url = line.strip().split("=", 1)[1]
    if not db_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)
    return create_engine(db_url)


def _parse_metar_ceiling(raw: str) -> int | None:
    """Extract ceiling (lowest BKN/OVC) from raw METAR text."""
    clouds = re.findall(r"(FEW|SCT|BKN|OVC)(\d{3})", raw)
    ceilings = [int(h) * 100 for t, h in clouds if t in ("BKN", "OVC")]
    return min(ceilings) if ceilings else None


def _parse_metar_vis(raw: str) -> int | None:
    """Extract visibility in meters from raw METAR text."""
    if "CAVOK" in raw:
        return 10000
    if "9999" in raw:
        return 10000
    m = re.search(r" (\d{4}) ", raw)
    return int(m.group(1)) if m else None


def _classify_from_raw(ceil_ft: int | None, vis_m: int | None) -> str:
    """Classify flight category from ceiling and visibility."""
    vis_sm = vis_m / _M_PER_SM if vis_m is not None else None
    return classify_flight_category(ceil_ft, vis_sm).value


def _fetch_awc_metars(icaos: list[str], hours: int = 24) -> dict[str, list[dict]]:
    """Fetch METARs from aviationweather.gov, grouped by ICAO."""
    result: dict[str, list[dict]] = {}
    for i in range(0, len(icaos), 400):
        batch = icaos[i : i + 400]
        try:
            resp = requests.get(
                _AWC_URL,
                params={"ids": ",".join(batch), "format": "json", "hours": hours},
                timeout=15,
            )
            if resp.status_code == 200:
                for entry in resp.json():
                    icao = entry.get("icaoId", "").strip()
                    result.setdefault(icao, []).append(entry)
        except Exception as e:
            log.warning("AWC fetch error: %s", e)
    return result


# ---------------------------------------------------------------------------
# Audit: airport full-day
# ---------------------------------------------------------------------------


def audit_airport(icao: str, hours: int, db: Session) -> None:
    """Full-day audit for one airport: compare stored data to independent source."""
    icao = icao.upper()
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    log.info("=" * 80)
    log.info("AIRPORT AUDIT: %s (last %d hours)", icao, hours)
    log.info("=" * 80)

    # 1. Our stored observations
    obs_rows = (
        db.execute(
            select(VerificationObservationRow)
            .where(
                VerificationObservationRow.icao == icao,
                VerificationObservationRow.observation_time >= since,
            )
            .order_by(VerificationObservationRow.observation_time)
        )
        .scalars()
        .all()
    )
    log.info("\nStored observations: %d", len(obs_rows))

    # 2. Independent METARs from AWC
    awc_data = _fetch_awc_metars([icao], hours=hours)
    awc_metars = awc_data.get(icao, [])
    log.info("AWC METARs fetched: %d", len(awc_metars))

    # 3. Our scores for this airport
    scores = (
        db.execute(
            select(VerificationScoreRow)
            .where(
                VerificationScoreRow.icao == icao,
                VerificationScoreRow.observation_time >= since,
                VerificationScoreRow.source == "standalone",
            )
            .order_by(
                VerificationScoreRow.observation_time,
                VerificationScoreRow.model,
            )
        )
        .scalars()
        .all()
    )
    log.info("Stored scores: %d", len(scores))

    # 4. Forecast snapshots
    snapshots = (
        db.execute(
            select(AirportForecastSnapshotRow)
            .where(
                AirportForecastSnapshotRow.icao == icao,
                AirportForecastSnapshotRow.model_init_time >= since - timedelta(days=2),
            )
            .order_by(
                AirportForecastSnapshotRow.model,
                AirportForecastSnapshotRow.model_init_time,
                AirportForecastSnapshotRow.forecast_hour,
            )
        )
        .scalars()
        .all()
    )
    log.info("Forecast snapshots: %d", len(snapshots))

    # 5. Cross-reference observations
    issues = 0
    log.info("\n--- Observation Verification ---")
    for obs in obs_rows:
        raw = obs.metar_raw or ""
        manual_ceil = _parse_metar_ceiling(raw)
        manual_vis = _parse_metar_vis(raw)
        manual_cat = _classify_from_raw(manual_ceil, manual_vis)

        status = "OK"
        problems = []
        if obs.ceiling_ft != manual_ceil:
            problems.append(f"ceil: stored={obs.ceiling_ft} parsed={manual_ceil}")
        if obs.visibility_m != manual_vis:
            problems.append(f"vis: stored={obs.visibility_m} parsed={manual_vis}")
        if obs.flight_category and obs.flight_category != manual_cat:
            problems.append(
                f"cat: stored={obs.flight_category} recomputed={manual_cat}"
            )

        if problems:
            status = "MISMATCH"
            issues += 1

        # Check against AWC
        obs_ddhhmm = obs.observation_time.strftime("%d%H%M")
        awc_match = [m for m in awc_metars if obs_ddhhmm in m.get("rawOb", "")]
        if awc_match:
            awc_raw = awc_match[0].get("rawOb", "")
            if awc_raw.strip() != raw.strip():
                problems.append("RAW TEXT DIFFERS FROM AWC")
                status = "MISMATCH"
                issues += 1

        log.info(
            "  %s  ceil=%s  vis=%s  cat=%s  [%s] %s",
            obs.observation_time.strftime("%H:%MZ"),
            obs.ceiling_ft,
            obs.visibility_m,
            obs.flight_category,
            status,
            " | ".join(problems) if problems else "",
        )

    # 6. Score verification
    log.info("\n--- Score Verification ---")
    score_issues = 0
    for s in scores:
        # Verify category classification
        obs = (
            db.execute(
                select(VerificationObservationRow).where(
                    VerificationObservationRow.id == s.observation_id
                )
            )
            .scalar_one_or_none()
        )
        if not obs:
            log.info("  ⚠ Score %d: observation_id %d not found!", s.id, s.observation_id)
            score_issues += 1
            continue

        problems = []

        # Check days_out calculation
        if s.model_init_time:
            expected_lead = int(
                (obs.observation_time - s.model_init_time).total_seconds() / 3600
            )
            if s.lead_hours != expected_lead:
                problems.append(
                    f"lead_hours: stored={s.lead_hours} expected={expected_lead}"
                )

        # Check obs category matches observation row
        if s.obs_flight_category and obs.flight_category:
            if s.obs_flight_category.upper() != obs.flight_category.upper():
                problems.append(
                    f"obs_cat: score={s.obs_flight_category} obs_row={obs.flight_category}"
                )

        if problems:
            score_issues += 1
            log.info(
                "  ⚠ %s %s D-%d %s: %s",
                obs.observation_time.strftime("%H:%MZ"),
                s.model,
                s.days_out,
                s.model_flight_category,
                " | ".join(problems),
            )

    log.info(
        "\nSummary: %d observation issues, %d score issues out of %d obs / %d scores",
        issues,
        score_issues,
        len(obs_rows),
        len(scores),
    )


# ---------------------------------------------------------------------------
# Audit: spot-check misses
# ---------------------------------------------------------------------------


def audit_misses(count: int, db: Session) -> None:
    """Spot-check top notable misses against independent data."""
    log.info("=" * 80)
    log.info("SPOT-CHECK: Top %d notable misses (standalone, last 48h)", count)
    log.info("=" * 80)

    since = datetime.now(timezone.utc) - timedelta(hours=48)

    rows = (
        db.execute(
            select(VerificationScoreRow, VerificationObservationRow)
            .join(
                VerificationObservationRow,
                VerificationScoreRow.observation_id == VerificationObservationRow.id,
            )
            .where(
                VerificationScoreRow.source == "standalone",
                VerificationScoreRow.category_match == False,  # noqa: E712
                VerificationScoreRow.days_out.in_((0, 1)),
                VerificationScoreRow.observation_time >= since,
                VerificationScoreRow.obs_flight_category.in_(tuple(_CAT_ORDER)),
                VerificationScoreRow.model_flight_category.in_(tuple(_CAT_ORDER)),
            )
            .order_by(VerificationScoreRow.observation_time.desc())
            .limit(200)
        )
        .all()
    )

    # Sort by severity
    def severity(r):
        s, o = r
        obs_rank = _CAT_ORDER.get(s.obs_flight_category or "", 0)
        mod_rank = _CAT_ORDER.get(s.model_flight_category or "", 0)
        return -abs(obs_rank - mod_rank)

    rows.sort(key=severity)
    rows = rows[:count]

    # Fetch AWC data for verification
    icaos = list(set(s.icao for s, _ in rows))
    awc_data = _fetch_awc_metars(icaos, hours=48)

    issues = 0
    for i, (score, obs) in enumerate(rows, 1):
        raw = obs.metar_raw or ""
        manual_ceil = _parse_metar_ceiling(raw)
        manual_vis = _parse_metar_vis(raw)
        manual_cat = _classify_from_raw(manual_ceil, manual_vis)

        obs_rank = _CAT_ORDER.get(score.obs_flight_category or "", 0)
        mod_rank = _CAT_ORDER.get(score.model_flight_category or "", 0)
        diff = obs_rank - mod_rank
        direction = "OPT" if diff > 0 else "PESS"
        sev = abs(diff)

        problems = []
        if obs.flight_category and obs.flight_category.upper() != manual_cat.upper():
            problems.append(
                f"cat mismatch: stored={obs.flight_category} recomputed={manual_cat}"
            )
        if obs.ceiling_ft != manual_ceil:
            problems.append(f"ceil: stored={obs.ceiling_ft} parsed={manual_ceil}")

        # AWC cross-reference
        obs_ddhhmm = obs.observation_time.strftime("%d%H%M")
        awc_metars = awc_data.get(obs.icao, [])
        awc_match = [m for m in awc_metars if obs_ddhhmm in m.get("rawOb", "")]
        if awc_match:
            awc_raw = awc_match[0].get("rawOb", "")
            if awc_raw.strip() != raw.strip():
                problems.append("RAW DIFFERS FROM AWC")
            awc_cat = awc_match[0].get("fltcat")
            if awc_cat and awc_cat != obs.flight_category:
                problems.append(f"AWC cat={awc_cat} vs ours={obs.flight_category}")
        else:
            problems.append("no AWC match (may have aged out)")

        status = "⚠ ISSUE" if problems else "✓ OK"
        if problems:
            issues += 1

        log.info(
            "\n#%d %s %s %s D-%d: obs=%s model=%s [%s +%d] %s",
            i,
            obs.icao,
            obs.observation_time.strftime("%Y-%m-%d %H:%MZ"),
            score.model,
            score.days_out,
            score.obs_flight_category,
            score.model_flight_category,
            direction,
            sev,
            status,
        )
        if problems:
            for p in problems:
                log.info("    → %s", p)
        log.info("    METAR: %s", raw[:100])

    log.info("\n" + "=" * 80)
    log.info("Result: %d issues found in %d misses checked", issues, len(rows))


# ---------------------------------------------------------------------------
# Audit: statistical sanity checks
# ---------------------------------------------------------------------------


def audit_sanity(db: Session) -> None:
    """Statistical sanity checks on the verification data."""
    log.info("=" * 80)
    log.info("STATISTICAL SANITY CHECKS")
    log.info("=" * 80)

    since = datetime.now(timezone.utc) - timedelta(days=7)

    # 1. Category distribution
    log.info("\n--- Observed Category Distribution (7d, standalone) ---")
    rows = db.execute(
        select(
            VerificationScoreRow.obs_flight_category,
            func.count(VerificationScoreRow.id),
        )
        .where(
            VerificationScoreRow.observation_time >= since,
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.days_out == 0,
        )
        .group_by(VerificationScoreRow.obs_flight_category)
    ).all()
    total = sum(c for _, c in rows)
    for cat, cnt in sorted(rows, key=lambda x: _CAT_ORDER.get(x[0] or "", 99)):
        pct = cnt / total * 100 if total else 0
        log.info("  %-6s %5d  (%5.1f%%)", cat, cnt, pct)
    log.info("  TOTAL  %5d", total)

    # Sanity: expect mostly VFR (>50%), some MVFR, less IFR, very little LIFR
    if total > 100:
        vfr_pct = sum(c for cat, c in rows if cat == "VFR") / total * 100
        if vfr_pct < 30:
            log.info("  ⚠ VFR < 30%% seems low — check data quality")
        elif vfr_pct > 90:
            log.info("  ⚠ VFR > 90%% — might not have enough IFR data to validate")

    # 2. Model score counts
    log.info("\n--- Score Counts per Model (7d, standalone, D-0) ---")
    rows = db.execute(
        select(
            VerificationScoreRow.model,
            func.count(VerificationScoreRow.id),
        )
        .where(
            VerificationScoreRow.observation_time >= since,
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.days_out == 0,
        )
        .group_by(VerificationScoreRow.model)
    ).all()
    counts = {m: c for m, c in rows}
    for model, cnt in sorted(rows, key=lambda x: -x[1]):
        log.info("  %-12s %5d", model, cnt)
    if counts:
        max_c = max(counts.values())
        min_c = min(counts.values())
        if max_c > 3 * min_c:
            log.info(
                "  ⚠ Large imbalance: max=%d min=%d — a model may be failing",
                max_c,
                min_c,
            )

    # 3. Model init times
    log.info("\n--- Latest Model Init Times (standalone snapshots) ---")
    rows = db.execute(
        select(
            AirportForecastSnapshotRow.model,
            func.max(AirportForecastSnapshotRow.model_init_time),
        )
        .group_by(AirportForecastSnapshotRow.model)
    ).all()
    for model, latest in rows:
        age = datetime.now(timezone.utc) - latest.replace(tzinfo=timezone.utc)
        stale = " ⚠ STALE" if age.total_seconds() > 24 * 3600 else ""
        log.info("  %-12s %s (%.1fh ago)%s", model, latest, age.total_seconds() / 3600, stale)

    # 4. Duplicate check
    log.info("\n--- Duplicate Score Check (7d, standalone) ---")
    dups = db.execute(
        text("""
            SELECT icao, observation_time, model, COUNT(*) as cnt
            FROM verification_scores
            WHERE source = 'standalone'
              AND observation_time >= :since
            GROUP BY icao, observation_time, model, model_init_time
            HAVING cnt > 1
            LIMIT 5
        """),
        {"since": since},
    ).fetchall()
    if dups:
        log.info("  ⚠ Found duplicate scores:")
        for d in dups:
            log.info("    %s %s %s: %d duplicates", d[0], d[1], d[2], d[3])
    else:
        log.info("  ✓ No duplicate scores found")

    # 5. NULL category check
    log.info("\n--- NULL Category Check (7d) ---")
    null_obs = db.execute(
        select(func.count(VerificationScoreRow.id)).where(
            VerificationScoreRow.observation_time >= since,
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.obs_flight_category.is_(None),
        )
    ).scalar()
    null_model = db.execute(
        select(func.count(VerificationScoreRow.id)).where(
            VerificationScoreRow.observation_time >= since,
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.model_flight_category.is_(None),
        )
    ).scalar()
    total_scores = db.execute(
        select(func.count(VerificationScoreRow.id)).where(
            VerificationScoreRow.observation_time >= since,
            VerificationScoreRow.source == "standalone",
        )
    ).scalar()
    log.info(
        "  obs_flight_category NULL: %d / %d (%.1f%%)",
        null_obs, total_scores, null_obs / total_scores * 100 if total_scores else 0,
    )
    log.info(
        "  model_flight_category NULL: %d / %d (%.1f%%)",
        null_model, total_scores, null_model / total_scores * 100 if total_scores else 0,
    )
    if null_obs and null_obs / total_scores > 0.05:
        log.info("  ⚠ >5%% NULL obs categories — check METAR parsing")


def main():
    parser = argparse.ArgumentParser(description="Verification pipeline audit")
    parser.add_argument("icao", nargs="?", help="Airport ICAO for full-day audit")
    parser.add_argument("--hours", type=int, default=24, help="Hours to audit (default: 24)")
    parser.add_argument("--misses", type=int, help="Spot-check N top misses")
    parser.add_argument("--sanity", action="store_true", help="Run statistical sanity checks")
    args = parser.parse_args()

    engine = _get_engine()

    with Session(engine) as db:
        if args.sanity:
            audit_sanity(db)
        elif args.misses:
            audit_misses(args.misses, db)
        elif args.icao:
            audit_airport(args.icao, args.hours, db)
        else:
            # Run all three
            audit_sanity(db)
            print()
            audit_misses(10, db)


if __name__ == "__main__":
    main()
