"""Verification stats must state which lead times they cover.

An observation is scored once per lead time, and forecasts get worse the
further out they are made. So any statistic that averages across lead times
without saying which ones is not a property of the model — it is a property of
whatever mix of lead times happens to be in the table, and it moves whenever
the horizon moves.

Extending the map to D+6 (#415) is exactly such a move: it adds days_out 5 and
6, the least accurate rows there are. `get_category_accuracy`,
`get_notable_misses` and `get_category_bias_stats` already scope themselves;
`get_wind_advisory_accuracy` did not, and would have silently drifted.

(`get_missed_warnings` had the same omission on top of a query that was dead in
production — it filtered on a wind-advisory value that is never stored. Both are
fixed in #418 and covered by `test_verification_missed_warnings.py`.)
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from weatherbrief.db.models import (
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_daily_rollup import rollup_day
from weatherbrief.tasks.verification_stats import get_wind_advisory_accuracy

SOURCE = "standalone_full"
BASE = date(2026, 5, 10)

# Lead times the extended horizon now produces. 0-3 are in scope for accuracy;
# 4-6 are the new long leads that must not leak into it.
ALL_LEADS = (0, 1, 2, 3, 4, 5, 6)
IN_SCOPE_FOR_ACCURACY = (0, 1, 2, 3)


def _rollup_seeded_days(db_session):
    """Roll up exactly the days we seeded.

    Not ``rollup_all_complete_days``: that consults bookkeeping of which days are
    already summarised, and the test engine is shared, so another test's rollup
    of the same dates would make ours a no-op.
    """
    for day_off in range(3):
        rollup_day(db_session, BASE + timedelta(days=day_off))
    db_session.flush()


def _seed(db_session, icao: str, *, advisory_matches_at: tuple[int, ...]):
    """One observation per day, scored at every lead time.

    A lead in ``advisory_matches_at`` predicts the wind advisory correctly;
    every other lead gets it wrong. That way the accuracy the stats report
    tells us exactly which leads were counted.

    Flushed, never committed. The stats read through this same session, so the
    rows are visible without a commit — and the ``db_session`` fixture then rolls
    them back, so nothing survives into another test. A commit here would leak,
    and the engine is shared: ``run_monthly_rollup`` in
    ``test_verification_rollup.py`` counts *every* month that has observations in
    it, so leaked rows there become an order-dependent failure over here.
    """
    for day_off in range(3):
        day = BASE + timedelta(days=day_off)
        obs_time = datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc)
        obs = VerificationObservationRow(
            icao=icao, observation_time=obs_time, collected_at=obs_time,
            flight_category="VFR", ceiling_ft=3000, visibility_m=9999,
            wind_dir=270, wind_speed_kt=30, temperature_c=12,
            weather=json.dumps([]),
        )
        db_session.add(obs)
        db_session.flush()

        for lead in ALL_LEADS:
            matched = lead in advisory_matches_at
            db_session.add(VerificationScoreRow(
                observation_id=obs.id,
                icao=icao,
                observation_time=obs_time,
                model="gfs",
                model_init_time=obs_time - timedelta(hours=lead * 24 + 1),
                lead_hours=lead * 24,
                days_out=lead,
                source=SOURCE,
                obs_flight_category="VFR",
                model_flight_category="VFR",
                category_match=True,
                # Real vocabulary: the advisory column holds green/amber/red.
                # Observed a red (strong crosswind); the model agrees only at
                # the leads we nominate, and calls it green elsewhere.
                obs_wind_advisory="red",
                model_wind_advisory="red" if matched else "green",
            ))
    db_session.flush()


class TestWindAdvisoryAccuracyIsLeadScoped:
    def test_long_leads_do_not_dilute_the_accuracy(self, db_session):
        """Only leads 0-3 count, so 'correct at 0-3' must read as 100%.

        Unscoped, the 4/5/6 misses would drag this to 4/7 = 57.1% — the models
        looking worse purely because we forecast further ahead.
        """
        _seed(db_session, "EGLL", advisory_matches_at=IN_SCOPE_FOR_ACCURACY)
        _rollup_seeded_days(db_session)

        stats = get_wind_advisory_accuracy(
            db_session,
            datetime(2026, 5, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 12, 23, tzinfo=timezone.utc),
            source=SOURCE, icao_filter=["EGLL"],
        )
        gfs = next(s for s in stats if s.model == "gfs")
        assert gfs.accuracy_pct == 100.0, (
            "long-lead scores leaked into the accuracy number"
        )
        assert gfs.sample_count == 3 * len(IN_SCOPE_FOR_ACCURACY)

    def test_a_long_lead_hit_does_not_flatter_the_accuracy(self, db_session):
        """The converse: being right only at D+4..D+6 must count for nothing."""
        _seed(db_session, "LSZH", advisory_matches_at=(4, 5, 6))
        _rollup_seeded_days(db_session)

        stats = get_wind_advisory_accuracy(
            db_session,
            datetime(2026, 5, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 5, 12, 23, tzinfo=timezone.utc),
            source=SOURCE, icao_filter=["LSZH"],
        )
        gfs = next(s for s in stats if s.model == "gfs")
        assert gfs.accuracy_pct == 0.0
