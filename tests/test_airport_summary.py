"""Tests for airport_monthly_summary and airport_daily_summary rollups."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from sqlalchemy import select

from weatherbrief.db.models import (
    AirportDailySummaryRow,
    AirportMonthlySummaryRow,
    VerificationObservationRow,
)
from weatherbrief.tasks.airport_summary import (
    _build_diurnal_json,
    _category_changes_within_day,
    _has_marker,
    _percentile,
    _worst_category,
    _OBSCURATION_MARKERS,
    _PHENOMENA,
    _PRECIP_MARKERS,
    completed_days,
    completed_months,
    rollup_all_complete_days,
    rollup_all_complete_months,
    rollup_day,
    rollup_month,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _obs(
    icao: str,
    when: datetime,
    *,
    category: str | None = "VFR",
    ceiling: int | None = 5000,
    visibility: int | None = 9999,
    wind_kt: int | None = 5,
    gust_kt: int | None = None,
    temp: float | None = 12.0,
    weather: list[str] | None = None,
) -> VerificationObservationRow:
    return VerificationObservationRow(
        icao=icao,
        observation_time=when,
        collected_at=when,
        flight_category=category,
        ceiling_ft=ceiling,
        visibility_m=visibility,
        wind_speed_kt=wind_kt,
        wind_gust_kt=gust_kt,
        temperature_c=temp,
        weather=json.dumps(weather or []),
    )


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 50) is None

    def test_single_value(self):
        assert _percentile([42], 50) == 42

    def test_median_odd(self):
        assert _percentile([1, 2, 3, 4, 5], 50) == 3

    def test_p10(self):
        # Position = (10-1) * 0.1 = 0.9 → between values[0]=1 and values[1]=2
        assert _percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 10) == 1.9

    def test_p90(self):
        assert _percentile([1, 2, 3, 4, 5], 90) == 4.6

    def test_unsorted_input(self):
        # Function must sort internally
        assert _percentile([5, 2, 4, 1, 3], 50) == 3


class TestHasMarker:
    def test_thunderstorm_variants(self):
        assert _has_marker(["TS"], _PHENOMENA["ts"])
        assert _has_marker(["+TSRA"], _PHENOMENA["ts"])
        assert _has_marker(["VCTS"], _PHENOMENA["ts"])
        assert _has_marker(["TSGR"], _PHENOMENA["ts"])
        assert not _has_marker(["RA"], _PHENOMENA["ts"])

    def test_fog_variants(self):
        # FG, BCFG, MIFG, PRFG, VCFG, FZFG all contain "FG"
        for code in ("FG", "BCFG", "MIFG", "VCFG", "FZFG"):
            assert _has_marker([code], _PHENOMENA["fg"]), code

    def test_freezing_marker_catches_all_fz_codes(self):
        for code in ("FZRA", "FZDZ", "FZFG", "FZUP"):
            assert _has_marker([code], _PHENOMENA["freezing"]), code

    def test_obscuration_total(self):
        assert _has_marker(["FG"], _OBSCURATION_MARKERS)
        assert _has_marker(["BR"], _OBSCURATION_MARKERS)
        assert _has_marker(["HZ"], _OBSCURATION_MARKERS)
        assert not _has_marker(["RA"], _OBSCURATION_MARKERS)

    def test_precip_total(self):
        assert _has_marker(["RA"], _PRECIP_MARKERS)
        assert _has_marker(["+SHRA"], _PRECIP_MARKERS)
        assert _has_marker(["FZDZ"], _PRECIP_MARKERS)  # FZDZ contains DZ
        assert not _has_marker(["FG"], _PRECIP_MARKERS)

    def test_empty_list(self):
        assert not _has_marker([], _PHENOMENA["ts"])


class TestWorstCategory:
    def test_returns_max_severity(self):
        assert _worst_category(["VFR", "IFR", "MVFR"]) == "IFR"

    def test_lifr_wins(self):
        assert _worst_category(["VFR", "MVFR", "IFR", "LIFR"]) == "LIFR"

    def test_ignores_unknown(self):
        # Unknown values are filtered before max() so they don't crash
        assert _worst_category(["VFR", "UNKNOWN", None]) == "VFR"

    def test_empty(self):
        assert _worst_category([]) is None
        assert _worst_category([None, None]) is None


class TestCategoryChangesWithinDay:
    def test_no_changes(self):
        # All VFR → 0 transitions
        obs = [
            _obs("LFPG", _utc(2026, 4, 1, 6, 0)),
            _obs("LFPG", _utc(2026, 4, 1, 9, 0)),
            _obs("LFPG", _utc(2026, 4, 1, 12, 0)),
        ]
        assert _category_changes_within_day(obs) == 0

    def test_one_transition(self):
        obs = [
            _obs("LFPG", _utc(2026, 4, 1, 6, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 1, 12, 0), category="IFR"),
        ]
        assert _category_changes_within_day(obs) == 1

    def test_flap_counts_twice(self):
        # VFR → IFR → VFR = 2 transitions
        obs = [
            _obs("LFPG", _utc(2026, 4, 1, 6, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 1, 9, 0), category="IFR"),
            _obs("LFPG", _utc(2026, 4, 1, 12, 0), category="VFR"),
        ]
        assert _category_changes_within_day(obs) == 2

    def test_segments_by_utc_day(self):
        # Day 1 has 1 transition; day 2 has 0; cross-day pair NOT counted
        obs = [
            _obs("LFPG", _utc(2026, 4, 1, 12, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 1, 18, 0), category="IFR"),
            # Cross day with same final state — counted? No, day boundary resets.
            _obs("LFPG", _utc(2026, 4, 2, 6, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 2, 12, 0), category="VFR"),
        ]
        assert _category_changes_within_day(obs) == 1

    def test_unknown_category_breaks_chain(self):
        # VFR → None → IFR: no transition counted across the gap
        obs = [
            _obs("LFPG", _utc(2026, 4, 1, 6, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 1, 9, 0), category=None),
            _obs("LFPG", _utc(2026, 4, 1, 12, 0), category="IFR"),
        ]
        assert _category_changes_within_day(obs) == 0


class TestBuildDiurnalJson:
    def test_empty_input(self):
        assert _build_diurnal_json([]) is None

    def test_omits_empty_hours(self):
        # Only one observation at 06Z — only "06" should appear in the output
        obs = [_obs("LFPG", _utc(2026, 4, 1, 6, 30))]
        out = json.loads(_build_diurnal_json(obs))
        assert list(out.keys()) == ["06"]
        assert out["06"]["n"] == 1

    def test_groups_by_hour_of_day(self):
        # Two obs at 06Z (different days), one at 09Z
        obs = [
            _obs("LFPG", _utc(2026, 4, 1, 6, 0), category="IFR", weather=["FG"]),
            _obs("LFPG", _utc(2026, 4, 2, 6, 30), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 1, 9, 0), category="VFR", weather=["TSRA"]),
        ]
        out = json.loads(_build_diurnal_json(obs))
        assert out["06"]["n"] == 2
        assert out["06"]["n_ifr"] == 1
        assert out["06"]["n_fog"] == 1
        assert out["09"]["n"] == 1
        assert out["09"]["n_ts"] == 1
        assert out["09"]["n_precip"] == 1


# ---------------------------------------------------------------------------
# rollup_month — integration against in-memory DB
# ---------------------------------------------------------------------------


class TestRollupMonth:
    def test_empty_month_produces_no_rows(self, db_session):
        rollup_month(db_session, _utc(2026, 4, 1))
        rows = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert rows == []

    def test_single_airport_basic_counts(self, db_session):
        # 4 obs in April for LFPG: 2 VFR, 1 MVFR, 1 IFR. One has TS+RA weather.
        for o in [
            _obs("LFPG", _utc(2026, 4, 5, 6, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 10, 9, 0), category="VFR"),
            _obs("LFPG", _utc(2026, 4, 15, 12, 0), category="MVFR", weather=["BR"]),
            _obs(
                "LFPG", _utc(2026, 4, 20, 15, 0), category="IFR",
                ceiling=400, visibility=2000, weather=["+TSRA"],
            ),
        ]:
            db_session.add(o)
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))

        rows = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert len(rows) == 1
        r = rows[0]
        assert r.icao == "LFPG"
        assert r.n_obs == 4
        assert r.n_vfr == 2
        assert r.n_mvfr == 1
        assert r.n_ifr == 1
        assert r.n_lifr == 0
        # Phenomena
        assert r.n_ts == 1
        assert r.n_ra == 1  # +TSRA contains RA
        assert r.n_br == 1
        assert r.n_obscuration == 1  # the BR obs
        assert r.n_precip == 1  # +TSRA
        # Hours below threshold
        assert r.n_ceiling_below_500 == 1
        assert r.n_ceiling_below_1500 == 1  # only the 400ft one is < 1500 (others 5000)
        assert r.n_visibility_below_5km == 1

    def test_idempotent_rerun_replaces_rows(self, db_session):
        db_session.add(_obs("LFPG", _utc(2026, 4, 5, 6, 0), category="VFR"))
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))
        first = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert len(first) == 1

        # Add another obs and re-run; previous row should be replaced, not duplicated
        db_session.add(_obs("LFPG", _utc(2026, 4, 6, 6, 0), category="IFR"))
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))
        second = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert len(second) == 1
        assert second[0].n_obs == 2
        assert second[0].n_ifr == 1

    def test_separate_rows_per_airport(self, db_session):
        db_session.add(_obs("LFPG", _utc(2026, 4, 5, 6, 0)))
        db_session.add(_obs("EGLL", _utc(2026, 4, 5, 6, 0)))
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))
        rows = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        icaos = {r.icao for r in rows}
        assert icaos == {"LFPG", "EGLL"}

    def test_obs_outside_month_excluded(self, db_session):
        # March obs ignored when rolling up April
        db_session.add(_obs("LFPG", _utc(2026, 3, 31, 23, 30)))
        db_session.add(_obs("LFPG", _utc(2026, 4, 1, 0, 30)))
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))
        rows = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert rows[0].n_obs == 1  # only the April obs

    def test_ceiling_percentiles_when_data_present(self, db_session):
        for ceiling in (1000, 2000, 3000, 4000, 5000):
            db_session.add(_obs(
                "LFPG", _utc(2026, 4, 1, 6, ceiling // 1000),
                ceiling=ceiling, category="VFR",
            ))
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))
        r = db_session.execute(select(AirportMonthlySummaryRow)).scalar_one()
        assert r.ceiling_p50_ft == 3000
        # p10 ≈ 1000 + 0.4*(2000-1000) = 1400
        assert r.ceiling_p10_ft == 1400
        assert r.ceiling_p90_ft == 4600

    def test_no_ceiling_data_yields_null_percentiles(self, db_session):
        db_session.add(_obs("LFPG", _utc(2026, 4, 5, 6, 0), ceiling=None))
        db_session.flush()

        rollup_month(db_session, _utc(2026, 4, 1))
        r = db_session.execute(select(AirportMonthlySummaryRow)).scalar_one()
        assert r.ceiling_p50_ft is None
        assert r.ceiling_p10_ft is None


class TestRollupDay:
    def test_worst_category_recorded(self, db_session):
        # IFR → MVFR → VFR over a single day; worst should be IFR
        for cat, h in [("VFR", 6), ("IFR", 9), ("MVFR", 12), ("VFR", 15)]:
            db_session.add(_obs("LFPG", _utc(2026, 4, 5, h, 0), category=cat))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        r = db_session.execute(select(AirportDailySummaryRow)).scalar_one()
        assert r.worst_category == "IFR"
        assert r.n_obs == 4
        assert r.n_vfr == 2
        assert r.n_ifr == 1
        assert r.n_mvfr == 1

    def test_idempotent(self, db_session):
        db_session.add(_obs("LFPG", _utc(2026, 4, 5, 6, 0)))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        rollup_day(db_session, date(2026, 4, 5))
        rows = db_session.execute(select(AirportDailySummaryRow)).scalars().all()
        assert len(rows) == 1

    def test_only_obs_in_target_date(self, db_session):
        db_session.add(_obs("LFPG", _utc(2026, 4, 4, 23, 30)))
        db_session.add(_obs("LFPG", _utc(2026, 4, 5, 12, 0)))
        db_session.add(_obs("LFPG", _utc(2026, 4, 6, 0, 30)))
        db_session.flush()

        rollup_day(db_session, date(2026, 4, 5))
        r = db_session.execute(select(AirportDailySummaryRow)).scalar_one()
        assert r.n_obs == 1


class TestOrchestrators:
    def test_completed_months_excludes_current_and_summarised(self, db_session):
        from datetime import datetime as dt

        # Drop one obs in February 2026 (definitely completed by 2026-05-10)
        db_session.add(_obs("LFPG", _utc(2026, 2, 15, 6, 0)))
        db_session.add(_obs("LFPG", _utc(2026, 3, 15, 6, 0)))
        db_session.flush()

        months = completed_months(db_session)
        # Should include February + March, not the current month
        labels = {m.strftime("%Y-%m") for m in months}
        assert "2026-02" in labels
        assert "2026-03" in labels

        # After rolling up Feb, it shouldn't be a candidate again
        rollup_month(db_session, _utc(2026, 2, 1))
        labels_after = {m.strftime("%Y-%m") for m in completed_months(db_session)}
        assert "2026-02" not in labels_after
        assert "2026-03" in labels_after

    def test_rollup_all_processes_each_month_once(self, db_session):
        db_session.add(_obs("LFPG", _utc(2026, 2, 15, 6, 0)))
        db_session.add(_obs("LFPG", _utc(2026, 3, 15, 6, 0)))
        db_session.flush()

        rollup_all_complete_months(db_session)
        rows = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert len({r.month for r in rows}) >= 2

        # Re-running shouldn't add any new rows (all months already done)
        before = len(rows)
        rollup_all_complete_months(db_session)
        rows = db_session.execute(select(AirportMonthlySummaryRow)).scalars().all()
        assert len(rows) == before
