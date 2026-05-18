"""Validation gate for issue #154 — verification_daily_stats rollup correctness.

Builds a synthetic 7-day dataset of raw NWP scores, computes expected
aggregates in Python from the raw rows, runs the daily rollup, then calls
``get_digest_data`` (which now reads from the rollup) and asserts every
numeric field matches the expectation within float-rounding tolerance.

If this test fails, the dashboard switch in issue #154 is unsafe.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from weatherbrief.db.models import (
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.verification_daily_rollup import rollup_all_complete_days
from weatherbrief.tasks.verification_stats import (
    get_category_accuracy,
    get_category_bias_stats,
    get_digest_data,
    get_optimistic_bias_leaderboard,
    get_wind_advisory_accuracy,
)


_CAT = ["VFR", "MVFR", "IFR", "LIFR"]
_ADV = ["green", "amber", "red"]
_CAT_IDX = {c: i for i, c in enumerate(_CAT)}
_ADV_IDX = {a: i for i, a in enumerate(_ADV)}
_MODELS = ["gfs", "icon", "ecmwf"]
_ICAOS = ["LFPG", "EDDF", "EGLL", "LSZH", "LIRF", "LEMD", "EHAM", "LOWW"]
_SOURCE = "standalone_full"


def _utc(year, month, day, hour=12) -> datetime:
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


def _build_dataset(db_session, base_date: date, days: int = 7):
    """Insert obs+scores spanning ``days`` UTC days starting at ``base_date``.

    Returns the inserted ``VerificationScoreRow`` list so the test can also
    compute expected aggregates independently.
    """
    rng = random.Random(0xC0DE)  # deterministic
    score_rows: list[VerificationScoreRow] = []

    for day_off in range(days):
        day = base_date + timedelta(days=day_off)
        for hour in (6, 12, 18):
            obs_time = datetime(
                day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc,
            )
            for icao in _ICAOS:
                # One observation row per (icao, obs_time)
                obs_cat = rng.choice(_CAT)
                obs_adv = rng.choice(_ADV)
                obs_precip = rng.choice([True, False, None])
                obs_conv = rng.choice([True, False, None])
                obs = VerificationObservationRow(
                    icao=icao, observation_time=obs_time, collected_at=obs_time,
                    flight_category=obs_cat,
                )
                db_session.add(obs)
                db_session.flush()
                # Multiple scores per observation: one per (model, days_out)
                for model in _MODELS:
                    for days_out in (0, 1, 2):
                        # Vary init_time so UNIQUE constraint is satisfied
                        init = obs_time - timedelta(hours=days_out * 24 + 1)
                        mod_cat = rng.choice(_CAT)
                        mod_adv = rng.choice(_ADV)
                        mod_precip = rng.choice([True, False, None])
                        mod_conv = rng.choice([True, False, None])

                        ceiling_d = rng.choice([None, *range(-1500, 1500, 100)])
                        wind_d = rng.choice([None, *range(-20, 20, 2)])
                        temp_d = rng.choice([None, *[x * 0.5 for x in range(-10, 10)]])
                        vis_d = rng.choice([None, *range(-5000, 5000, 200)])

                        s = VerificationScoreRow(
                            observation_id=obs.id,
                            icao=icao,
                            observation_time=obs_time,
                            model=model,
                            model_init_time=init,
                            lead_hours=days_out * 24,
                            days_out=days_out,
                            source=_SOURCE,
                            obs_flight_category=obs_cat,
                            model_flight_category=mod_cat,
                            category_match=(obs_cat == mod_cat),
                            ceiling_delta_ft=ceiling_d,
                            wind_speed_delta_kt=wind_d,
                            temperature_delta_c=temp_d,
                            visibility_delta_m=vis_d,
                            obs_wind_advisory=obs_adv,
                            model_wind_advisory=mod_adv,
                            advisory_match=(obs_adv == mod_adv),
                            obs_has_precipitation=obs_precip,
                            model_has_precipitation=mod_precip,
                            obs_has_convection=obs_conv,
                            model_has_convection=mod_conv,
                        )
                        db_session.add(s)
                        score_rows.append(s)
    db_session.flush()
    return score_rows


def _expected_category_accuracy(
    scores: list[VerificationScoreRow], date_lo: date, date_hi: date,
):
    """Compute the same per-(model, days_out) accuracy that the new query produces."""
    n_match: dict[tuple[str, int], int] = defaultdict(int)
    n_with_cat: dict[tuple[str, int], int] = defaultdict(int)
    for s in scores:
        d = s.observation_time.date()
        if d < date_lo or d > date_hi:
            continue
        if s.source != _SOURCE:
            continue
        if s.obs_flight_category is None or s.model_flight_category is None:
            continue
        key = (s.model, s.days_out)
        n_with_cat[key] += 1
        if s.obs_flight_category == s.model_flight_category:
            n_match[key] += 1
    return n_match, n_with_cat


def _expected_bias(
    scores: list[VerificationScoreRow], date_lo: date, date_hi: date,
):
    """Compute per-(model, days_out) bias direction counts, days_out in (0,1)."""
    n_total: dict[tuple[str, int], int] = defaultdict(int)
    opt1: dict[tuple[str, int], int] = defaultdict(int)
    opt2: dict[tuple[str, int], int] = defaultdict(int)
    pess1: dict[tuple[str, int], int] = defaultdict(int)
    pess2: dict[tuple[str, int], int] = defaultdict(int)
    for s in scores:
        d = s.observation_time.date()
        if d < date_lo or d > date_hi:
            continue
        if s.source != _SOURCE or s.days_out not in (0, 1):
            continue
        if s.obs_flight_category is None or s.model_flight_category is None:
            continue
        obs_i = _CAT_IDX[s.obs_flight_category]
        mod_i = _CAT_IDX[s.model_flight_category]
        diff = mod_i - obs_i
        key = (s.model, s.days_out)
        n_total[key] += 1
        if diff == -1:
            opt1[key] += 1
        elif diff <= -2:
            opt2[key] += 1
        elif diff == 1:
            pess1[key] += 1
        elif diff >= 2:
            pess2[key] += 1
    return n_total, opt1, opt2, pess1, pess2


def _expected_wind_advisory(
    scores: list[VerificationScoreRow], date_lo: date, date_hi: date,
):
    n_match: dict[str, int] = defaultdict(int)
    n_with_adv: dict[str, int] = defaultdict(int)
    for s in scores:
        d = s.observation_time.date()
        if d < date_lo or d > date_hi:
            continue
        if s.source != _SOURCE:
            continue
        if s.obs_wind_advisory is None or s.model_wind_advisory is None:
            continue
        n_with_adv[s.model] += 1
        if s.obs_wind_advisory == s.model_wind_advisory:
            n_match[s.model] += 1
    return n_match, n_with_adv


def _close(a: float | None, b: float | None, rel_tol=1e-9, abs_tol=0.05) -> bool:
    """Compare two optional floats with relative + absolute tolerance.

    The rollup stores SUMs and we divide-at-query-time; the raw query
    computes the same divisions, but rounding ``round(x, 1)`` introduces
    ±0.05 absolute drift in percent values — within tolerance.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)


class TestValidationGate:
    """Issue #154 step-3 validation gate.

    Synthetic 7-day dataset → rollup → get_digest_data, assert that every
    numeric field matches the expected values computed straight from raw.
    """

    def test_category_accuracy_matches_raw_aggregation(self, db_session):
        scores = _build_dataset(db_session, date(2026, 4, 1), days=7)
        rollup_all_complete_days(db_session)

        since = _utc(2026, 4, 1, 0)
        until = _utc(2026, 4, 7, 23)

        actual = get_category_accuracy(db_session, since, until, source=_SOURCE)
        actual_map = {(r.model, r.days_out): r for r in actual if r.model != "TAF"}

        n_match, n_with_cat = _expected_category_accuracy(
            scores, date(2026, 4, 1), date(2026, 4, 7),
        )
        # Every (model, days_out) in 0..3 expected, but our dataset uses 0..2
        for key, want_n in n_with_cat.items():
            assert key in actual_map, f"missing {key}"
            row = actual_map[key]
            assert row.sample_count == want_n
            want_acc = round(n_match[key] / want_n * 100, 1) if want_n else None
            assert _close(row.accuracy_pct, want_acc), (
                f"{key}: rollup={row.accuracy_pct} expected={want_acc}"
            )

    def test_category_bias_matches_raw_aggregation(self, db_session):
        scores = _build_dataset(db_session, date(2026, 4, 1), days=7)
        rollup_all_complete_days(db_session)

        since = _utc(2026, 4, 1, 0)
        until = _utc(2026, 4, 7, 23)

        actual = get_category_bias_stats(db_session, since, until, source=_SOURCE)
        actual_map = {(b.model, b.days_out): b for b in actual}

        n_total, opt1, opt2, pess1, pess2 = _expected_bias(
            scores, date(2026, 4, 1), date(2026, 4, 7),
        )
        for key, want_total in n_total.items():
            assert key in actual_map, f"missing {key}"
            b = actual_map[key]
            assert b.total_scores == want_total
            assert b.optimistic_1 == opt1[key]
            assert b.optimistic_2 == opt2[key]
            assert b.pessimistic_1 == pess1[key]
            assert b.pessimistic_2 == pess2[key]

    def test_wind_advisory_matches_raw_aggregation(self, db_session):
        scores = _build_dataset(db_session, date(2026, 4, 1), days=7)
        rollup_all_complete_days(db_session)

        since = _utc(2026, 4, 1, 0)
        until = _utc(2026, 4, 7, 23)

        actual = get_wind_advisory_accuracy(db_session, since, until, source=_SOURCE)
        actual_map = {a.model: a for a in actual if a.model != "TAF"}

        n_match, n_with_adv = _expected_wind_advisory(
            scores, date(2026, 4, 1), date(2026, 4, 7),
        )
        for model, want_n in n_with_adv.items():
            assert model in actual_map, f"missing {model}"
            row = actual_map[model]
            assert row.sample_count == want_n
            want_acc = round(n_match[model] / want_n * 100, 1)
            assert _close(row.accuracy_pct, want_acc), (
                f"{model}: rollup={row.accuracy_pct} expected={want_acc}"
            )

    def test_optimistic_bias_leaderboard_matches_formula(self, db_session):
        scores = _build_dataset(db_session, date(2026, 4, 1), days=7)
        rollup_all_complete_days(db_session)

        since = _utc(2026, 4, 1, 0)
        until = _utc(2026, 4, 7, 23)

        # Per-icao expectation for (gfs, d-1)
        per_icao_n = defaultdict(int)
        per_icao_opt1 = defaultdict(int)
        per_icao_opt2 = defaultdict(int)
        for s in scores:
            d = s.observation_time.date()
            if d < date(2026, 4, 1) or d > date(2026, 4, 7):
                continue
            if s.source != _SOURCE or s.model != "gfs" or s.days_out != 1:
                continue
            per_icao_n[s.icao] += 1
            if s.obs_flight_category is None or s.model_flight_category is None:
                continue
            diff = (
                _CAT_IDX[s.model_flight_category]
                - _CAT_IDX[s.obs_flight_category]
            )
            if diff == -1:
                per_icao_opt1[s.icao] += 1
            elif diff <= -2:
                per_icao_opt2[s.icao] += 1

        rows = get_optimistic_bias_leaderboard(
            db_session, since, until,
            model="gfs", days_out=1, source=_SOURCE,
        )
        actual_map = {r.icao: r for r in rows}
        for icao, n in per_icao_n.items():
            if n < 10:
                # Leaderboard filters airports with too few samples
                continue
            assert icao in actual_map, f"missing {icao}"
            r = actual_map[icao]
            assert r.n == n
            assert r.n_cat_opt_1 == per_icao_opt1[icao]
            assert r.n_cat_opt_2 == per_icao_opt2[icao]
            want_score = (
                per_icao_opt1[icao] + 2 * per_icao_opt2[icao]
            ) / n
            assert _close(r.score, want_score, rel_tol=1e-6, abs_tol=1e-6)

        # Sorted descending by score
        scores_seq = [r.score for r in rows]
        assert scores_seq == sorted(scores_seq, reverse=True)

    def test_digest_data_orchestrator_runs(self, db_session):
        """Smoke test: full digest payload assembles without errors."""
        _build_dataset(db_session, date(2026, 4, 1), days=7)
        rollup_all_complete_days(db_session)

        since = _utc(2026, 4, 1, 0)
        until = _utc(2026, 4, 7, 23)
        data = get_digest_data(
            db_session, since, until,
            source=_SOURCE, period_label="test", include_7d=True,
        )
        assert data.period_label == "test"
        # Some category accuracy rows present
        assert len(data.category_accuracy_today) > 0
        # Bias rows present (only d-0, d-1)
        assert len(data.category_bias) > 0
        # Wind advisory rows present
        assert len(data.wind_advisory) > 0

    def test_source_string_must_match_exactly(self, db_session):
        """Production calls pass source='standalone'; our synthetic fixture
        uses 'standalone_full'. Querying with the wrong string must return
        empty — guards against future code that maps source strings (e.g.
        treats 'standalone_full' and 'standalone' as the same group) from
        silently merging buckets.
        """
        _build_dataset(db_session, date(2026, 4, 1), days=7)
        rollup_all_complete_days(db_session)

        since = _utc(2026, 4, 1, 0)
        until = _utc(2026, 4, 7, 23)
        # Wrong source — different from the fixture's _SOURCE
        wrong = get_digest_data(
            db_session, since, until,
            source="standalone", period_label="x", include_7d=False,
        )
        assert len(wrong.category_accuracy_today) == 0
        assert len(wrong.category_bias) == 0
        # NWP wind advisory rows must be empty too. TAF may still appear if
        # the fixture stored TAF rows with source='standalone' — our fixture
        # doesn't, but assert specifically against NWP models to be precise.
        nwp_wind = [w for w in wrong.wind_advisory if w.model != "TAF"]
        assert len(nwp_wind) == 0
