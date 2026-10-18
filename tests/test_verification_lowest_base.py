"""Lowest-cloud-base verification on the flight path and in the rollups (#806).

``verification_scores.lowest_base_delta_ft`` (#793) was filled only by the
standalone cycle and never reached an aggregate. These tests cover:

- the flight path (``score_flight``) computing it the same way as standalone —
  model base converted to AGL with the model's own datum, minus the METAR's
  lowest FEW/SCT/BKN/OVC base — on a FEW-only observation the ceiling delta
  cannot see;
- the daily / global-daily / monthly rollups carrying additive sums whose MAE
  and bias equal a direct computation over the raw scores, whatever the
  aggregation path (day → month, airport → global).
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from weatherbrief.db.models import (
    FlightRow,
    FlightVerificationMapRow,
    VerificationDailyStatsRow,
    VerificationGlobalDailyStatsRow,
    VerificationMonthlyStatsRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.models.analysis import (
    HourlyForecast,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
)
from weatherbrief.tasks.scoring import (
    lowest_base_delta,
    model_cloud_base_agl,
    score_flight,
)
from weatherbrief.tasks.verification_daily_rollup import rollup_day
from weatherbrief.tasks.verification_rollup import rollup_month


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = _utc(2026, 4, 1, 12, 0)
FIELD_ELEV_FT = 500.0
MODEL_BASE_FT = 2500.0   # the model's own number, in its own datum
OBS_FEW_BASE_FT = 1500   # FEW015, no ceiling


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


class TestHelpers:

    def test_msl_model_is_converted_with_field_elevation(self):
        assert model_cloud_base_agl(2500.0, "gfs", 500.0) == pytest.approx(2000.0)
        assert model_cloud_base_agl(2500.0, "icon", 500.0) == pytest.approx(2000.0)

    def test_ecmwf_base_is_already_agl(self):
        assert model_cloud_base_agl(2500.0, "ecmwf", 500.0) == pytest.approx(2500.0)

    def test_delta_needs_both_sides(self):
        assert lowest_base_delta(2000.0, 1500) == pytest.approx(500.0)
        assert lowest_base_delta(None, 1500) is None
        assert lowest_base_delta(2000.0, None) is None


# ---------------------------------------------------------------------------
# Flight path
# ---------------------------------------------------------------------------


def _hourly(model_base_ft: float | None) -> HourlyForecast:
    return HourlyForecast(
        time=NOW - timedelta(hours=2),
        temperature_2m_c=11.0,
        dewpoint_2m_c=6.0,
        visibility_m=10000.0,
        wind_speed_10m_kt=8.0,
        wind_direction_10m_deg=260.0,
        precipitation_mm=0.0,
        snowfall_cm=0.0,
        nwp_cloud_diagnostics=NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=20.0, base_ft=model_base_ft),
        ),
    )


def _flight_with_few_only_obs(db, dev_user, flight_id: str):
    db.add(FlightRow(
        id=flight_id, user_id=dev_user, route_name="ZZAA-ZZBB",
        waypoints_json="[]", departure_time=NOW - timedelta(hours=3),
        cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        flight_duration_hours=2.0, verification_status="complete",
    ))
    db.flush()
    obs = VerificationObservationRow(
        icao="ZZAA", observation_time=NOW - timedelta(hours=2),
        collected_at=NOW - timedelta(hours=1), flight_category="VFR",
        ceiling_ft=None,                         # FEW only: no ceiling
        lowest_cloud_base_ft=OBS_FEW_BASE_FT,
        cloud_layers=json.dumps([{"cover": "FEW", "base_ft": OBS_FEW_BASE_FT}]),
        visibility_m=9999, wind_dir=270, wind_speed_kt=10, temperature_c=12,
        weather=json.dumps([]),
    )
    db.add(obs)
    db.flush()
    db.add(FlightVerificationMapRow(
        flight_id=flight_id, icao="ZZAA", observation_id=obs.id,
        distance_from_route_nm=0.0,
    ))
    db.flush()
    return db.get(FlightRow, flight_id)


def _score_through_flight_path(db, flight, model: str, hourly: HourlyForecast):
    init_ts = int((NOW - timedelta(hours=6)).timestamp())
    pack = SimpleNamespace()
    apt = MagicMock(latitude_deg=51.0, longitude_deg=-1.0, elevation_ft=FIELD_ELEV_FT)
    with patch(
        "weatherbrief.airports._load_airport_model",
        return_value=MagicMock(get_airport=MagicMock(return_value=apt)),
    ), patch(
        "weatherbrief.airports.get_runway_ends", return_value={"ZZAA": []},
    ), patch(
        "weatherbrief.tasks.scoring._select_packs_for_scoring", return_value=[pack],
    ), patch(
        "weatherbrief.tasks.scoring._load_pack_data",
        return_value=(SimpleNamespace(forecasts=[]), None, {model: init_ts}),
    ), patch(
        "weatherbrief.tasks.scoring._find_nearest_waypoint_forecasts",
        return_value={model: SimpleNamespace(at_time=lambda _t: hourly)},
    ):
        result = score_flight(flight, db, "/fake/airports.db")
    assert result["model_scores"] == 1
    return db.execute(
        select(VerificationScoreRow).where(
            VerificationScoreRow.source == "flight",
            VerificationScoreRow.model == model,
        )
    ).scalars().one()


class TestFlightPathLowestBase:

    @pytest.mark.parametrize(("model", "expected"), [
        # MSL base → AGL: 2500 - 500 = 2000; minus FEW015 → +500
        ("gfs", MODEL_BASE_FT - FIELD_ELEV_FT - OBS_FEW_BASE_FT),
        ("icon", MODEL_BASE_FT - FIELD_ELEV_FT - OBS_FEW_BASE_FT),
        # ECMWF cbh is AGL already: 2500 - 1500 → +1000
        ("ecmwf", MODEL_BASE_FT - OBS_FEW_BASE_FT),
    ])
    def test_few_only_observation_gets_lowest_base_delta(
        self, db_session, dev_user, model, expected,
    ):
        flight = _flight_with_few_only_obs(db_session, dev_user, f"zz-{model}")
        row = _score_through_flight_path(
            db_session, flight, model, _hourly(MODEL_BASE_FT),
        )
        assert row.ceiling_delta_ft is None       # the ceiling path sees nothing
        assert row.lowest_base_delta_ft == pytest.approx(expected)

    def test_no_model_base_leaves_it_null(self, db_session, dev_user):
        flight = _flight_with_few_only_obs(db_session, dev_user, "zz-nobase")
        row = _score_through_flight_path(db_session, flight, "gfs", _hourly(None))
        assert row.lowest_base_delta_ft is None


# ---------------------------------------------------------------------------
# Rollups: aggregation-invariant
# ---------------------------------------------------------------------------


def _seed_scores(db, *, seed: int = 806) -> list[VerificationScoreRow]:
    """Two days of March, two airports, two models, some NULL deltas."""
    rng = random.Random(seed)
    rows: list[VerificationScoreRow] = []
    for day in (3, 17):
        for icao in ("ZZAA", "ZZBB"):
            for hour in range(0, 24, 3):
                when = _utc(2026, 3, day, hour, 0)
                obs = VerificationObservationRow(
                    icao=icao, observation_time=when, collected_at=when,
                )
                db.add(obs)
                db.flush()
                for model in ("gfs", "ecmwf"):
                    delta = (
                        None if rng.random() < 0.3
                        else round(rng.uniform(-3000.0, 4000.0), 1)
                    )
                    row = VerificationScoreRow(
                        observation_id=obs.id, icao=icao, observation_time=when,
                        model=model, model_init_time=when - timedelta(hours=6),
                        lead_hours=6, days_out=0, source="standalone",
                        lowest_base_delta_ft=delta,
                    )
                    db.add(row)
                    rows.append(row)
    db.flush()
    return rows


def _direct(rows, **key) -> tuple[int, float | None, float | None]:
    deltas = [
        r.lowest_base_delta_ft for r in rows
        if r.lowest_base_delta_ft is not None
        and all(getattr(r, k) == v for k, v in key.items())
    ]
    if not deltas:
        return 0, None, None
    return (
        len(deltas),
        sum(abs(d) for d in deltas) / len(deltas),
        sum(deltas) / len(deltas),
    )


class TestRollupLowestBase:

    @pytest.fixture
    def rolled(self, db_session):
        rows = _seed_scores(db_session)
        rollup_day(db_session, date(2026, 3, 3))
        rollup_day(db_session, date(2026, 3, 17))
        rollup_month(db_session, _utc(2026, 3, 1))
        db_session.flush()
        return rows

    def test_daily_sums_match_raw(self, db_session, rolled):
        for d in db_session.execute(select(VerificationDailyStatsRow)).scalars():
            day_rows = [r for r in rolled if r.observation_time.date() == d.date]
            n, mae, bias = _direct(day_rows, model=d.model, icao=d.icao)
            assert d.n_lowest_base == n
            if n:
                assert d.sum_abs_lowest_base_delta_ft / n == pytest.approx(mae)
                assert d.sum_lowest_base_delta_ft / n == pytest.approx(bias)
            else:
                assert d.sum_lowest_base_delta_ft is None

    def test_global_daily_matches_raw(self, db_session, rolled):
        globals_ = db_session.execute(
            select(VerificationGlobalDailyStatsRow)
        ).scalars().all()
        assert globals_
        for g in globals_:
            day_rows = [r for r in rolled if r.observation_time.date() == g.date]
            n, mae, bias = _direct(day_rows, model=g.model)
            assert g.n_lowest_base == n
            assert g.sum_abs_lowest_base_delta_ft / n == pytest.approx(mae)
            assert g.sum_lowest_base_delta_ft / n == pytest.approx(bias)

    def test_monthly_mae_and_bias_match_raw(self, db_session, rolled):
        """Month composed from days == one pass over the month's raw scores."""
        months = db_session.execute(
            select(VerificationMonthlyStatsRow)
        ).scalars().all()
        assert len(months) == 4  # 2 models x 2 airports
        for m in months:
            n, mae, bias = _direct(rolled, model=m.model, icao=m.icao)
            assert n > 0
            assert m.n_lowest_base == n
            assert m.lowest_base_mae_ft == pytest.approx(mae)
            assert m.lowest_base_bias_ft == pytest.approx(bias)

    def test_month_with_no_deltas_is_null_not_zero(self, db_session):
        when = _utc(2026, 2, 10, 12, 0)
        obs = VerificationObservationRow(icao="ZZCC", observation_time=when, collected_at=when)
        db_session.add(obs)
        db_session.flush()
        db_session.add(VerificationScoreRow(
            observation_id=obs.id, icao="ZZCC", observation_time=when,
            model="gfs", model_init_time=when, lead_hours=0, days_out=0,
            source="standalone",
        ))
        db_session.flush()
        rollup_day(db_session, date(2026, 2, 10))
        rollup_month(db_session, _utc(2026, 2, 1))
        m = db_session.execute(select(VerificationMonthlyStatsRow)).scalars().one()
        assert m.n_lowest_base == 0
        assert m.lowest_base_mae_ft is None
        assert m.lowest_base_bias_ft is None
