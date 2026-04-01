"""Tests for METAR/TAF verification scoring (Phase 2).

Covers:
- Delta helpers: _delta, _circular_delta, _has_precip
- Nearest route point matching: _haversine_nm, _find_nearest_route_point, _find_nearest_waypoint_forecasts
- _score_model_vs_metar: model-vs-METAR scoring using advisory pipeline functions
- _score_taf_vs_metar: TAF-vs-METAR scoring
- _select_packs_for_scoring: one-per-days_out selection
- score_flight: end-to-end scoring for a flight
- score_completed_flights / backfill_scores: batch operations
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    FlightVerificationMapRow,
    TafVerificationScoreRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.scoring import (
    _circular_delta,
    _delta,
    _find_nearest_route_point,
    _find_nearest_waypoint_forecasts,
    _has_precip,
    _distance_nm,
    _score_model_vs_metar,
    _score_taf_vs_metar,
    _select_packs_for_scoring,
    score_completed_flights,
    score_flight,
    backfill_scores,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = _utc(2026, 4, 1, 12, 0)


# ---------------------------------------------------------------------------
# Delta helpers
# ---------------------------------------------------------------------------


class TestDelta:

    def test_both_present(self):
        assert _delta(10.0, 7.0) == 3.0

    def test_negative_delta(self):
        assert _delta(5.0, 8.0) == -3.0

    def test_model_none(self):
        assert _delta(None, 5.0) is None

    def test_obs_none(self):
        assert _delta(5.0, None) is None

    def test_both_none(self):
        assert _delta(None, None) is None

    def test_zero_delta(self):
        assert _delta(5.0, 5.0) == 0.0


class TestCircularDelta:

    def test_simple_positive(self):
        assert _circular_delta(90.0, 80.0) == 10.0

    def test_simple_negative(self):
        assert _circular_delta(80.0, 90.0) == -10.0

    def test_wrap_around_positive(self):
        # 350 - 10 should be -20, not 340
        assert _circular_delta(350.0, 10.0) == -20.0

    def test_wrap_around_negative(self):
        # 10 - 350 should be +20, not -340
        assert _circular_delta(10.0, 350.0) == 20.0

    def test_exactly_180(self):
        result = _circular_delta(270.0, 90.0)
        assert result == 180.0 or result == -180.0

    def test_none_input(self):
        assert _circular_delta(None, 90.0) is None
        assert _circular_delta(90.0, None) is None

    def test_same_direction(self):
        assert _circular_delta(180.0, 180.0) == 0.0


class TestHasPrecip:

    def test_rain(self):
        assert _has_precip(["RA"])

    def test_heavy_rain(self):
        assert _has_precip(["+RA"])

    def test_light_snow(self):
        assert _has_precip(["-SN"])

    def test_freezing_rain(self):
        assert _has_precip(["FZRA"])

    def test_thunderstorm_rain(self):
        assert _has_precip(["TSRA"])

    def test_shower(self):
        assert _has_precip(["SHRA"])

    def test_no_precip_fog(self):
        assert not _has_precip(["FG"])

    def test_no_precip_haze(self):
        assert not _has_precip(["HZ"])

    def test_no_precip_empty(self):
        assert not _has_precip([])

    def test_mixed_weather(self):
        assert _has_precip(["BR", "RA"])  # BR is not precip, RA is

    def test_thunderstorm_only(self):
        # TS alone is not in the precip set (it's convection, not precip)
        assert not _has_precip(["TS"])


# ---------------------------------------------------------------------------
# Nearest route point matching
# ---------------------------------------------------------------------------


class TestDistanceNm:

    def test_same_point(self):
        assert _distance_nm(51.0, -1.0, 51.0, -1.0) == 0.0

    def test_known_distance(self):
        # London (51.5, -0.1) to Paris (48.9, 2.4) ≈ ~183 nm
        d = _distance_nm(51.5, -0.1, 48.9, 2.4)
        assert 180 < d < 190

    def test_symmetric(self):
        d1 = _distance_nm(51.0, -1.0, 48.0, 2.0)
        d2 = _distance_nm(48.0, 2.0, 51.0, -1.0)
        assert abs(d1 - d2) < 0.001


class TestFindNearestRoutePoint:

    def test_finds_closest(self):
        analyses = [
            SimpleNamespace(lat=51.8, lon=-1.3),   # idx 0 — near EGTK
            SimpleNamespace(lat=48.9, lon=2.4),     # idx 1 — near LFPB
            SimpleNamespace(lat=46.2, lon=7.3),     # idx 2 — near LSGS
        ]
        # Airport near Paris
        idx = _find_nearest_route_point(49.0, 2.5, analyses)
        assert idx == 1

    def test_empty_analyses(self):
        assert _find_nearest_route_point(51.0, -1.0, []) is None

    def test_single_point(self):
        analyses = [SimpleNamespace(lat=51.8, lon=-1.3)]
        assert _find_nearest_route_point(0.0, 0.0, analyses) == 0


class TestFindNearestWaypointForecasts:

    def test_picks_nearest_per_model(self):
        wp_egtk = SimpleNamespace(lat=51.8, lon=-1.3)
        wp_lfpb = SimpleNamespace(lat=48.9, lon=2.4)

        forecasts = [
            SimpleNamespace(model=SimpleNamespace(value="gfs"), waypoint=wp_egtk),
            SimpleNamespace(model=SimpleNamespace(value="gfs"), waypoint=wp_lfpb),
            SimpleNamespace(model=SimpleNamespace(value="ecmwf"), waypoint=wp_lfpb),
        ]

        # Airport near Paris
        result = _find_nearest_waypoint_forecasts(49.0, 2.5, forecasts)
        assert "gfs" in result
        assert "ecmwf" in result
        # GFS should pick LFPB waypoint (closer to 49.0, 2.5)
        assert result["gfs"].waypoint.lat == pytest.approx(48.9)

    def test_empty_forecasts(self):
        assert _find_nearest_waypoint_forecasts(51.0, -1.0, []) == {}


# ---------------------------------------------------------------------------
# _score_model_vs_metar
# ---------------------------------------------------------------------------


class TestScoreModelVsMetar:

    def _make_obs_row(self, **overrides):
        defaults = dict(
            id=1,
            icao="EGTK",
            observation_time=NOW,
            flight_category="VFR",
            ceiling_ft=4000,
            visibility_m=9999,
            wind_dir=270,
            wind_speed_kt=10,
            wind_gust_kt=None,
            temperature_c=12,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _make_hourly(self, **overrides):
        defaults = dict(
            visibility_m=10000,
            wind_direction_10m_deg=260,
            wind_speed_10m_kt=12,
            wind_gusts_10m_kt=None,
            precipitation_mm=0.0,
            snowfall_cm=0.0,
            temperature_2m_c=11.5,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_basic_scoring(self):
        obs_row = self._make_obs_row()
        hourly = self._make_hourly()
        init_time = NOW - timedelta(hours=6)

        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling", return_value=3800.0,
        ), patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
            return_value=SimpleNamespace(value="VFR"),
        ), patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            side_effect=[
                ("green", "R27", 2.0, 12.0),  # model
                ("green", "R27", 1.5, 10.0),  # obs
            ],
        ):
            result = _score_model_vs_metar(
                obs_row=obs_row,
                obs_weather=[],
                sounding=None,
                hourly=hourly,
                runway_ends=[SimpleNamespace(id="R27", heading=270)],
                model="gfs",
                model_init_time=init_time,
                days_out=0,
            )

        assert result is not None
        assert result.model == "gfs"
        assert result.lead_hours == 6
        assert result.days_out == 0
        assert result.obs_flight_category == "VFR"
        assert result.model_flight_category == "VFR"
        assert result.category_match is True
        assert result.ceiling_delta_ft == pytest.approx(-200.0)  # 3800 - 4000
        assert result.advisory_match is True

    def test_returns_none_without_hourly(self):
        obs_row = self._make_obs_row()
        result = _score_model_vs_metar(
            obs_row=obs_row,
            obs_weather=[],
            sounding=None,
            hourly=None,
            runway_ends=[],
            model="gfs",
            model_init_time=NOW - timedelta(hours=6),
            days_out=0,
        )
        assert result is None

    def test_precipitation_detection(self):
        obs_row = self._make_obs_row()
        hourly = self._make_hourly(precipitation_mm=2.5)

        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling", return_value=None,
        ), patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
            return_value=SimpleNamespace(value="VFR"),
        ), patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            return_value=(None, None, None, None),
        ):
            result = _score_model_vs_metar(
                obs_row=obs_row,
                obs_weather=["RA"],
                sounding=None,
                hourly=hourly,
                runway_ends=[],
                model="gfs",
                model_init_time=NOW - timedelta(hours=6),
                days_out=0,
            )

        assert result.model_has_precipitation is True
        assert result.obs_has_precipitation is True

    def test_convection_detection(self):
        from weatherbrief.models.analysis import ConvectiveRisk

        obs_row = self._make_obs_row()
        hourly = self._make_hourly()
        sounding = SimpleNamespace(
            convective=SimpleNamespace(risk_level=ConvectiveRisk.MODERATE),
        )

        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling", return_value=None,
        ), patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
            return_value=SimpleNamespace(value="VFR"),
        ), patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            return_value=(None, None, None, None),
        ):
            result = _score_model_vs_metar(
                obs_row=obs_row,
                obs_weather=["TS", "RA"],
                sounding=sounding,
                hourly=hourly,
                runway_ends=[],
                model="gfs",
                model_init_time=NOW - timedelta(hours=6),
                days_out=0,
            )

        assert result.model_has_convection is True
        assert result.obs_has_convection is True

    def test_convection_none_risk(self):
        from weatherbrief.models.analysis import ConvectiveRisk

        obs_row = self._make_obs_row()
        hourly = self._make_hourly()
        sounding = SimpleNamespace(
            convective=SimpleNamespace(risk_level=ConvectiveRisk.NONE),
        )

        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling", return_value=None,
        ), patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
            return_value=SimpleNamespace(value="VFR"),
        ), patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            return_value=(None, None, None, None),
        ):
            result = _score_model_vs_metar(
                obs_row=obs_row,
                obs_weather=[],
                sounding=sounding,
                hourly=hourly,
                runway_ends=[],
                model="gfs",
                model_init_time=NOW - timedelta(hours=6),
                days_out=0,
            )

        assert result.model_has_convection is False
        assert result.obs_has_convection is False

    def test_wind_direction_circular_delta(self):
        obs_row = self._make_obs_row(wind_dir=350)
        hourly = self._make_hourly(wind_direction_10m_deg=10)

        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling", return_value=None,
        ), patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
            return_value=SimpleNamespace(value="VFR"),
        ), patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            return_value=(None, None, None, None),
        ):
            result = _score_model_vs_metar(
                obs_row=obs_row,
                obs_weather=[],
                sounding=None,
                hourly=hourly,
                runway_ends=[],
                model="gfs",
                model_init_time=NOW - timedelta(hours=6),
                days_out=0,
            )

        # 10 - 350 should wrap to +20, not -340
        assert result.wind_dir_delta_deg == pytest.approx(20.0)

    def test_visibility_conversion_to_statute_miles(self):
        """Category classification should use statute miles."""
        obs_row = self._make_obs_row(visibility_m=8000)
        hourly = self._make_hourly(visibility_m=6000)

        with patch(
            "weatherbrief.analysis.airport_conditions.reconcile_ceiling", return_value=None,
        ) as mock_ceil, patch(
            "weatherbrief.analysis.airport_conditions.classify_flight_category",
        ) as mock_cat, patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            return_value=(None, None, None, None),
        ):
            mock_cat.return_value = SimpleNamespace(value="VFR")
            _score_model_vs_metar(
                obs_row=obs_row,
                obs_weather=[],
                sounding=None,
                hourly=hourly,
                runway_ends=[],
                model="gfs",
                model_init_time=NOW - timedelta(hours=6),
                days_out=0,
            )
            # classify_flight_category should be called with statute miles
            call_args = mock_cat.call_args
            _, vis_sm = call_args[0]
            assert vis_sm == pytest.approx(6000 / 1609.34, abs=0.1)


# ---------------------------------------------------------------------------
# _score_taf_vs_metar
# ---------------------------------------------------------------------------


class TestScoreTafVsMetar:

    def _make_obs_row(self, **overrides):
        defaults = dict(
            id=1,
            icao="EGTK",
            observation_time=NOW,
            flight_category="VFR",
            ceiling_ft=4000,
            visibility_m=9999,
            wind_dir=270,
            wind_speed_kt=10,
            wind_gust_kt=None,
            taf_issue_time=NOW - timedelta(hours=3),
            taf_flight_category="VFR",
            taf_ceiling_ft=3500,
            taf_visibility_m=9999,
            taf_wind_dir=260,
            taf_wind_speed_kt=12,
            taf_wind_gust_kt=None,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_basic_taf_scoring(self):
        obs_row = self._make_obs_row()

        with patch(
            "weatherbrief.tasks.route_weather.compute_wind_advisory",
            side_effect=[
                ("green", "R27", 1.5, 10.0),  # obs
                ("green", "R27", 2.0, 12.0),  # taf
            ],
        ):
            result = _score_taf_vs_metar(
                obs_row, [], [SimpleNamespace(id="R27", heading=270)],
            )

        assert result is not None
        assert result.lead_hours == 3
        assert result.category_match is True
        assert result.ceiling_delta_ft == pytest.approx(-500.0)  # 3500 - 4000
        assert result.advisory_match is True

    def test_returns_none_without_taf(self):
        obs_row = self._make_obs_row(taf_issue_time=None)
        result = _score_taf_vs_metar(obs_row, [], [])
        assert result is None

    def test_returns_none_without_taf_category(self):
        obs_row = self._make_obs_row(taf_flight_category=None)
        result = _score_taf_vs_metar(obs_row, [], [])
        assert result is None


# ---------------------------------------------------------------------------
# _select_packs_for_scoring
# ---------------------------------------------------------------------------


class TestSelectPacksForScoring:

    def test_picks_latest_per_days_out(self, db_session, dev_user):
        flight = FlightRow(
            id="flight-1",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
        )
        db_session.add(flight)
        db_session.flush()

        # Two packs for D-0 (earlier and later), one for D-1
        db_session.add(BriefingPackRow(
            flight_id="flight-1", fetch_timestamp=NOW - timedelta(hours=12),
            days_out=0, artifact_path="/p1",
        ))
        db_session.add(BriefingPackRow(
            flight_id="flight-1", fetch_timestamp=NOW - timedelta(hours=2),
            days_out=0, artifact_path="/p2",
        ))
        db_session.add(BriefingPackRow(
            flight_id="flight-1", fetch_timestamp=NOW - timedelta(hours=24),
            days_out=1, artifact_path="/p3",
        ))
        db_session.flush()

        result = _select_packs_for_scoring(db_session, "flight-1")

        assert len(result) == 2
        days_out_set = {p.days_out for p in result}
        assert days_out_set == {0, 1}
        # D-0 should be the latest
        d0_pack = next(p for p in result if p.days_out == 0)
        assert d0_pack.artifact_path == "/p2"

    def test_empty_for_nonexistent_flight(self, db_session):
        result = _select_packs_for_scoring(db_session, "nonexistent")
        assert result == []


# ---------------------------------------------------------------------------
# score_flight (integration with DB)
# ---------------------------------------------------------------------------


class TestScoreFlight:

    def _setup_flight_with_observations(self, db_session, dev_user, tmpdir):
        """Create a flight with linked observations and a briefing pack."""
        flight = FlightRow(
            id="score-test",
            user_id=dev_user,
            route_name="EGTK-LFPB",
            waypoints_json=json.dumps([
                {"icao": "EGTK", "lat": 51.8361, "lon": -1.32},
                {"icao": "LFPB", "lat": 48.9694, "lon": 2.4414},
            ]),
            departure_time=NOW - timedelta(hours=3),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="complete",
        )
        db_session.add(flight)
        db_session.flush()

        obs = VerificationObservationRow(
            icao="EGTK",
            observation_time=NOW - timedelta(hours=2),
            collected_at=NOW - timedelta(hours=1),
            flight_category="VFR",
            ceiling_ft=4000,
            visibility_m=9999,
            wind_dir=270,
            wind_speed_kt=10,
            temperature_c=12,
            weather=json.dumps([]),
        )
        db_session.add(obs)
        db_session.flush()

        db_session.add(FlightVerificationMapRow(
            flight_id="score-test", icao="EGTK",
            observation_id=obs.id, distance_from_route_nm=0.0,
        ))
        db_session.flush()

        # Create pack directory with minimal forecast data
        pack_dir = tmpdir / "pack"
        pack_dir.mkdir()

        db_session.add(BriefingPackRow(
            flight_id="score-test",
            fetch_timestamp=NOW - timedelta(hours=4),
            days_out=0,
            artifact_path=str(pack_dir),
            model_init_times_json=json.dumps({"gfs": int((NOW - timedelta(hours=6)).timestamp())}),
        ))
        db_session.flush()

        return flight, obs, pack_dir

    def test_score_flight_no_observations(self, db_session, dev_user):
        flight = FlightRow(
            id="empty-flight",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="complete",
        )
        db_session.add(flight)
        db_session.flush()

        result = score_flight(flight, db_session, "/fake/db.sqlite")
        assert result == {"model_scores": 0, "taf_scores": 0}

    def test_score_flight_taf_only(self, db_session, dev_user):
        """Flight with TAF obs but no forecast packs should score TAF."""
        flight = FlightRow(
            id="taf-only",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW - timedelta(hours=3),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="complete",
        )
        db_session.add(flight)
        db_session.flush()

        obs = VerificationObservationRow(
            icao="EGTK",
            observation_time=NOW - timedelta(hours=2),
            collected_at=NOW,
            flight_category="VFR",
            ceiling_ft=4000,
            visibility_m=9999,
            wind_dir=270,
            wind_speed_kt=10,
            temperature_c=12,
            weather=json.dumps([]),
            taf_issue_time=NOW - timedelta(hours=5),
            taf_flight_category="VFR",
            taf_ceiling_ft=3500,
            taf_visibility_m=9999,
            taf_wind_dir=260,
            taf_wind_speed_kt=12,
        )
        db_session.add(obs)
        db_session.flush()

        db_session.add(FlightVerificationMapRow(
            flight_id="taf-only", icao="EGTK",
            observation_id=obs.id, distance_from_route_nm=0.0,
        ))
        db_session.flush()

        with patch(
            "weatherbrief.airports._load_airport_model",
        ) as mock_model, patch(
            "weatherbrief.airports.get_runway_ends",
            return_value={"EGTK": []},
        ):
            mock_airport = MagicMock()
            mock_airport.latitude_deg = 51.8361
            mock_airport.longitude_deg = -1.32
            mock_model.return_value = MagicMock(
                get_airport=MagicMock(return_value=mock_airport),
            )

            result = score_flight(flight, db_session, "/fake/db.sqlite")

        assert result["taf_scores"] == 1
        taf_rows = db_session.execute(
            select(TafVerificationScoreRow)
        ).scalars().all()
        assert len(taf_rows) == 1
        assert taf_rows[0].category_match is True

    def test_score_dedup_prevents_duplicates(self, db_session, dev_user):
        """Scoring the same flight twice should not create duplicate scores."""
        flight = FlightRow(
            id="dedup-test",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW - timedelta(hours=3),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="complete",
        )
        db_session.add(flight)
        db_session.flush()

        obs = VerificationObservationRow(
            icao="EGTK",
            observation_time=NOW - timedelta(hours=2),
            collected_at=NOW,
            flight_category="VFR",
            ceiling_ft=4000,
            visibility_m=9999,
            wind_dir=270,
            wind_speed_kt=10,
            temperature_c=12,
            weather=json.dumps([]),
            taf_issue_time=NOW - timedelta(hours=5),
            taf_flight_category="VFR",
        )
        db_session.add(obs)
        db_session.flush()

        db_session.add(FlightVerificationMapRow(
            flight_id="dedup-test", icao="EGTK",
            observation_id=obs.id, distance_from_route_nm=0.0,
        ))
        db_session.flush()

        with patch(
            "weatherbrief.airports._load_airport_model",
        ) as mock_model, patch(
            "weatherbrief.airports.get_runway_ends",
            return_value={"EGTK": []},
        ):
            mock_airport = MagicMock()
            mock_airport.latitude_deg = 51.8361
            mock_airport.longitude_deg = -1.32
            mock_model.return_value = MagicMock(
                get_airport=MagicMock(return_value=mock_airport),
            )

            result1 = score_flight(flight, db_session, "/fake/db.sqlite")
            db_session.flush()
            result2 = score_flight(flight, db_session, "/fake/db.sqlite")

        # Second run should add 0 new scores
        assert result2["taf_scores"] == 0


# ---------------------------------------------------------------------------
# score_completed_flights
# ---------------------------------------------------------------------------


class TestScoreCompletedFlights:

    def test_scores_and_marks_scored(self, db_session, dev_user):
        flight = FlightRow(
            id="complete-1",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW - timedelta(hours=5),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="complete",
        )
        db_session.add(flight)
        db_session.flush()

        with patch(
            "weatherbrief.tasks.scoring.score_flight",
            return_value={"model_scores": 3, "taf_scores": 1},
        ):
            result = score_completed_flights(db_session, "/fake/db.sqlite")

        assert result["flights_scored"] == 1
        assert result["model_scores"] == 3
        assert result["taf_scores"] == 1
        assert flight.verification_status == "scored"

    def test_skips_non_complete_flights(self, db_session, dev_user):
        FlightRow(
            id="collecting-1",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="collecting",
        )
        db_session.add(FlightRow(
            id="collecting-1",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW,
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
            verification_status="collecting",
        ))
        db_session.flush()

        with patch("weatherbrief.tasks.scoring.score_flight") as mock_score:
            result = score_completed_flights(db_session, "/fake/db.sqlite")

        assert result["flights_scored"] == 0
        mock_score.assert_not_called()

    def test_continues_on_failure(self, db_session, dev_user):
        """If scoring one flight fails, others should still be scored."""
        db_session.add(FlightRow(
            id="fail-1", user_id=dev_user, route_name="t",
            waypoints_json="[]", departure_time=NOW - timedelta(hours=5),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, verification_status="complete",
        ))
        db_session.add(FlightRow(
            id="ok-1", user_id=dev_user, route_name="t",
            waypoints_json="[]", departure_time=NOW - timedelta(hours=5),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, verification_status="complete",
        ))
        db_session.flush()

        call_count = 0

        def _mock_score(flight_row, db, path):
            nonlocal call_count
            call_count += 1
            if flight_row.id == "fail-1":
                raise RuntimeError("scoring failed")
            return {"model_scores": 1, "taf_scores": 0}

        with patch("weatherbrief.tasks.scoring.score_flight", side_effect=_mock_score):
            result = score_completed_flights(db_session, "/fake/db.sqlite")

        assert result["flights_scored"] == 1
        assert call_count == 2


# ---------------------------------------------------------------------------
# backfill_scores
# ---------------------------------------------------------------------------


class TestBackfillScores:

    def test_backfill_specific_flight(self, db_session, dev_user):
        db_session.add(FlightRow(
            id="bf-1", user_id=dev_user, route_name="t",
            waypoints_json="[]", departure_time=NOW - timedelta(hours=5),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, verification_status="scored",
        ))
        db_session.flush()

        with patch(
            "weatherbrief.tasks.scoring.score_flight",
            return_value={"model_scores": 5, "taf_scores": 2},
        ):
            result = backfill_scores(db_session, "/fake/db.sqlite", flight_id="bf-1")

        assert result["flights_scored"] == 1
        assert result["model_scores"] == 5

    def test_backfill_nonexistent_flight_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            backfill_scores(db_session, "/fake/db.sqlite", flight_id="nope")

    def test_backfill_all_scored_and_complete(self, db_session, dev_user):
        db_session.add(FlightRow(
            id="s1", user_id=dev_user, route_name="t",
            waypoints_json="[]", departure_time=NOW - timedelta(hours=5),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, verification_status="scored",
        ))
        db_session.add(FlightRow(
            id="c1", user_id=dev_user, route_name="t",
            waypoints_json="[]", departure_time=NOW - timedelta(hours=5),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, verification_status="complete",
        ))
        db_session.add(FlightRow(
            id="x1", user_id=dev_user, route_name="t",
            waypoints_json="[]", departure_time=NOW,
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, verification_status="collecting",
        ))
        db_session.flush()

        with patch(
            "weatherbrief.tasks.scoring.score_flight",
            return_value={"model_scores": 1, "taf_scores": 0},
        ):
            result = backfill_scores(db_session, "/fake/db.sqlite")

        # Only scored + complete, not collecting
        assert result["flights_scored"] == 2
