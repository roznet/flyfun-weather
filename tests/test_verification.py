"""Tests for METAR/TAF verification collection system (Phase 1).

Covers:
- find_verifiable_flights: flight window logic
- gather_airports: dedup, caching, status transitions
- store_observations: dedup, hourly filter, flight linkage
- finalize_completed_flights: status lifecycle
- collect_and_store: end-to-end orchestration
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select


@pytest.fixture
def db_engine():
    """Per-test engine — functions under test call session.commit()."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    FlightVerificationMapRow,
    VerificationObservationRow,
)
from weatherbrief.models.verification import VerificationObservation
from weatherbrief.tasks.verification import (
    _should_skip_for_bucket_dedup,
    collect_and_store,
    fetch_observations_batch,
    finalize_completed_flights,
    find_verifiable_flights,
    gather_airports,
    store_observations,
)


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


NOW = _utc(2026, 4, 1, 12, 0)


def _make_flight(db_session, dev_user, **overrides) -> FlightRow:
    """Insert a FlightRow with sensible defaults."""
    defaults = dict(
        id="flight-1",
        user_id=dev_user,
        route_name="EGTK-LFPB",
        waypoints_json=json.dumps([
            {"icao": "EGTK", "lat": 51.8361, "lon": -1.32},
            {"icao": "LFPB", "lat": 48.9694, "lon": 2.4414},
        ]),
        departure_time=NOW - timedelta(hours=1),
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=2.0,
        verification_status=None,
    )
    defaults.update(overrides)
    row = FlightRow(**defaults)
    db_session.add(row)
    db_session.flush()
    # Add a briefing pack (required for find_verifiable_flights join)
    db_session.add(BriefingPackRow(
        flight_id=row.id,
        fetch_timestamp=NOW - timedelta(hours=2),
        days_out=0,
        artifact_path="/tmp/test-pack",
    ))
    db_session.flush()
    return row


def _make_observation(**overrides) -> VerificationObservation:
    defaults = dict(
        icao="EGTK",
        observation_time=NOW,
        collected_at=NOW,
        metar_raw="METAR EGTK 011200Z 27010KT 9999 FEW040 12/05 Q1013",
        flight_category="VFR",
        ceiling_ft=4000,
        visibility_m=9999,
        wind_dir=270,
        wind_speed_kt=10,
        wind_gust_kt=None,
        temperature_c=12,
        dewpoint_c=5,
        qnh=1013.0,
        weather=[],
    )
    defaults.update(overrides)
    return VerificationObservation(**defaults)


# ---------------------------------------------------------------------------
# find_verifiable_flights
# ---------------------------------------------------------------------------


class TestFindVerifiableFlights:

    @patch("weatherbrief.tasks.verification.datetime")
    def test_finds_flight_in_window(self, mock_dt, db_session, dev_user):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        flight = _make_flight(db_session, dev_user)
        result = find_verifiable_flights(db_session)
        assert len(result) == 1
        assert result[0].id == flight.id

    @patch("weatherbrief.tasks.verification.datetime")
    def test_excludes_flight_past_window(self, mock_dt, db_session, dev_user):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        _make_flight(
            db_session, dev_user,
            departure_time=NOW - timedelta(hours=10),
            flight_duration_hours=2.0,
        )
        result = find_verifiable_flights(db_session)
        assert len(result) == 0

    @patch("weatherbrief.tasks.verification.datetime")
    def test_excludes_already_scored(self, mock_dt, db_session, dev_user):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        _make_flight(db_session, dev_user, verification_status="scored")
        result = find_verifiable_flights(db_session)
        assert len(result) == 0

    @patch("weatherbrief.tasks.verification.datetime")
    def test_includes_collecting_status(self, mock_dt, db_session, dev_user):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        _make_flight(db_session, dev_user, verification_status="collecting")
        result = find_verifiable_flights(db_session)
        assert len(result) == 1

    @patch("weatherbrief.tasks.verification.datetime")
    def test_excludes_flight_without_pack(self, mock_dt, db_session, dev_user):
        """Flight must have at least one briefing pack."""
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        row = FlightRow(
            id="no-pack",
            user_id=dev_user,
            route_name="test",
            waypoints_json="[]",
            departure_time=NOW - timedelta(hours=1),
            cruise_altitude_ft=8000,
            flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
        )
        db_session.add(row)
        db_session.flush()
        result = find_verifiable_flights(db_session)
        assert len(result) == 0

    @patch("weatherbrief.tasks.verification.datetime")
    def test_future_departure_within_1h(self, mock_dt, db_session, dev_user):
        """A flight departing within 1h should be included."""
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        _make_flight(
            db_session, dev_user,
            departure_time=NOW + timedelta(minutes=30),
        )
        result = find_verifiable_flights(db_session)
        assert len(result) == 1

    @patch("weatherbrief.tasks.verification.datetime")
    def test_future_departure_beyond_1h(self, mock_dt, db_session, dev_user):
        """A flight departing >1h from now should be excluded."""
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        _make_flight(
            db_session, dev_user,
            departure_time=NOW + timedelta(hours=2),
        )
        result = find_verifiable_flights(db_session)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# gather_airports
# ---------------------------------------------------------------------------


class TestGatherAirports:

    def test_first_cycle_resolves_and_caches(self, db_session, dev_user):
        """First cycle should spatial-query and cache in flight_verification_map."""
        flight = _make_flight(db_session, dev_user)

        mock_airports = [
            {"airport": SimpleNamespace(ident="EGTK"), "segment_distance_nm": 0.0},
            {"airport": SimpleNamespace(ident="EGBJ"), "segment_distance_nm": 10.5},
        ]

        with patch(
            "weatherbrief.tasks.verification._resolve_corridor_airports",
            return_value=[("EGTK", 0.0), ("EGBJ", 10.5)],
        ):
            result = gather_airports([flight], db_session, "/fake/db.sqlite")

        assert "EGTK" in result
        assert "EGBJ" in result
        assert flight.id in result["EGTK"]
        assert flight.verification_status == "collecting"

        # Check cache rows
        cache = db_session.execute(
            select(FlightVerificationMapRow)
            .where(FlightVerificationMapRow.flight_id == flight.id)
        ).scalars().all()
        assert len(cache) == 2
        assert all(c.observation_id is None for c in cache)

    def test_subsequent_cycle_reads_cache(self, db_session, dev_user):
        """Collecting flights should read from cache, not re-query."""
        flight = _make_flight(
            db_session, dev_user, verification_status="collecting",
        )
        # Pre-populate cache
        db_session.add(FlightVerificationMapRow(
            flight_id=flight.id, icao="EGTK", distance_from_route_nm=0.0,
        ))
        db_session.add(FlightVerificationMapRow(
            flight_id=flight.id, icao="LFPB", distance_from_route_nm=0.0,
        ))
        db_session.flush()

        result = gather_airports([flight], db_session, "/fake/db.sqlite")

        assert set(result.keys()) == {"EGTK", "LFPB"}
        assert flight.id in result["EGTK"]

    def test_dedup_across_flights(self, db_session, dev_user):
        """Same airport from two flights should only appear once in output."""
        f1 = _make_flight(db_session, dev_user, id="f1")
        f2 = _make_flight(db_session, dev_user, id="f2")

        with patch(
            "weatherbrief.tasks.verification._resolve_corridor_airports",
            return_value=[("EGTK", 0.0)],
        ):
            result = gather_airports([f1, f2], db_session, "/fake/db.sqlite")

        assert "EGTK" in result
        assert result["EGTK"] == {"f1", "f2"}

    def test_resolve_failure_skips_flight(self, db_session, dev_user):
        """If spatial query fails, flight is skipped but others proceed."""
        f1 = _make_flight(db_session, dev_user, id="f1")
        f2 = _make_flight(db_session, dev_user, id="f2")

        call_count = 0

        def _mock_resolve(flight_row, db_path, corridor):
            nonlocal call_count
            call_count += 1
            if flight_row.id == "f1":
                raise RuntimeError("spatial query failed")
            return [("LFPB", 0.0)]

        with patch(
            "weatherbrief.tasks.verification._resolve_corridor_airports",
            side_effect=_mock_resolve,
        ):
            result = gather_airports([f1, f2], db_session, "/fake/db.sqlite")

        assert "LFPB" in result
        assert f1.verification_status is None  # not promoted to collecting
        assert f2.verification_status == "collecting"


# ---------------------------------------------------------------------------
# 30-min bucket dedup
# ---------------------------------------------------------------------------


class TestBucketDedup:

    def test_empty_db_no_skip(self, db_session):
        assert not _should_skip_for_bucket_dedup(
            db_session, "EGTK", NOW,
        )

    def test_same_bucket_skips(self, db_session):
        # 12:05 and 12:25 both fall in the [12:00, 12:30) bucket
        db_session.add(VerificationObservationRow(
            icao="EGTK",
            observation_time=_utc(2026, 4, 1, 12, 5),
            collected_at=NOW,
        ))
        db_session.flush()

        assert _should_skip_for_bucket_dedup(
            db_session, "EGTK", _utc(2026, 4, 1, 12, 25),
        )

    def test_different_bucket_within_hour_no_skip(self, db_session):
        # 12:20 is in [12:00, 12:30); 12:35 is in [12:30, 13:00) — different
        # buckets, both should be storable.
        db_session.add(VerificationObservationRow(
            icao="EGTK",
            observation_time=_utc(2026, 4, 1, 12, 20),
            collected_at=NOW,
        ))
        db_session.flush()

        assert not _should_skip_for_bucket_dedup(
            db_session, "EGTK", _utc(2026, 4, 1, 12, 35),
        )

    def test_different_hour_no_skip(self, db_session):
        db_session.add(VerificationObservationRow(
            icao="EGTK",
            observation_time=NOW - timedelta(hours=1),
            collected_at=NOW,
        ))
        db_session.flush()

        assert not _should_skip_for_bucket_dedup(db_session, "EGTK", NOW)

    def test_different_airport_no_skip(self, db_session):
        db_session.add(VerificationObservationRow(
            icao="LFPB",
            observation_time=NOW,
            collected_at=NOW,
        ))
        db_session.flush()

        assert not _should_skip_for_bucket_dedup(db_session, "EGTK", NOW)


# ---------------------------------------------------------------------------
# store_observations
# ---------------------------------------------------------------------------


class TestStoreObservations:

    def test_inserts_new_observation(self, db_session, dev_user):
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        obs = _make_observation()
        icao_map = {"EGTK": {flight.id}}

        count = store_observations([obs], icao_map, db_session)
        db_session.flush()

        assert count == 1
        rows = db_session.execute(
            select(VerificationObservationRow)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].icao == "EGTK"
        assert rows[0].ceiling_ft == 4000

    def test_exact_dedup_no_double_insert(self, db_session, dev_user):
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        obs = _make_observation()
        icao_map = {"EGTK": {flight.id}}

        store_observations([obs], icao_map, db_session)
        db_session.flush()

        # Same observation again
        count = store_observations([obs], icao_map, db_session)
        db_session.flush()

        assert count == 0
        rows = db_session.execute(
            select(VerificationObservationRow)
        ).scalars().all()
        assert len(rows) == 1

    def test_bucket_dedup_skips_second_obs_same_bucket(self, db_session, dev_user):
        # Both fall in the [12:00, 12:30) bucket — only one stored.
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        obs1 = _make_observation(observation_time=_utc(2026, 4, 1, 12, 5))
        obs2 = _make_observation(observation_time=_utc(2026, 4, 1, 12, 25))
        icao_map = {"EGTK": {flight.id}}

        count = store_observations([obs1, obs2], icao_map, db_session)
        db_session.flush()

        assert count == 1

    def test_bucket_dedup_keeps_obs_in_different_buckets(self, db_session, dev_user):
        # 12:05 in [12:00, 12:30); 12:35 in [12:30, 13:00) — both stored.
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        obs1 = _make_observation(observation_time=_utc(2026, 4, 1, 12, 5))
        obs2 = _make_observation(observation_time=_utc(2026, 4, 1, 12, 35))
        icao_map = {"EGTK": {flight.id}}

        count = store_observations([obs1, obs2], icao_map, db_session)
        db_session.flush()

        assert count == 2

    def test_creates_flight_linkage(self, db_session, dev_user):
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        # Pre-populate cache row
        db_session.add(FlightVerificationMapRow(
            flight_id=flight.id, icao="EGTK", distance_from_route_nm=5.0,
        ))
        db_session.flush()

        obs = _make_observation()
        icao_map = {"EGTK": {flight.id}}
        store_observations([obs], icao_map, db_session)
        db_session.flush()

        links = db_session.execute(
            select(FlightVerificationMapRow)
            .where(FlightVerificationMapRow.flight_id == flight.id)
            .where(FlightVerificationMapRow.observation_id.isnot(None))
        ).scalars().all()
        assert len(links) == 1
        assert links[0].distance_from_route_nm == 5.0

    def test_weather_json_stored(self, db_session):
        obs = _make_observation(weather=["RA", "BR"])
        icao_map = {"EGTK": set()}
        store_observations([obs], icao_map, db_session)
        db_session.flush()

        row = db_session.execute(
            select(VerificationObservationRow)
        ).scalar_one()
        assert json.loads(row.weather) == ["RA", "BR"]


# ---------------------------------------------------------------------------
# finalize_completed_flights
# ---------------------------------------------------------------------------


class TestFinalizeCompletedFlights:

    def test_marks_past_flights_complete(self, db_session, dev_user):
        row = _make_flight(
            db_session, dev_user, id="fin-1",
            departure_time=_utc(2026, 4, 1, 6, 0),
            verification_status="collecting",
        )
        with patch(
            "weatherbrief.tasks.verification.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = _utc(2026, 4, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            count = finalize_completed_flights(db_session)

        assert count == 1
        assert row.verification_status == "complete"

    def test_does_not_finalize_active_flight(self, db_session, dev_user):
        row = _make_flight(
            db_session, dev_user, id="fin-2",
            departure_time=_utc(2026, 4, 1, 11, 0),
            verification_status="collecting",
        )
        with patch(
            "weatherbrief.tasks.verification.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = _utc(2026, 4, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            count = finalize_completed_flights(db_session)

        assert count == 0
        assert row.verification_status == "collecting"

    def test_handles_naive_departure_time(self, db_session, dev_user):
        row = _make_flight(
            db_session, dev_user, id="fin-3",
            departure_time=datetime(2026, 4, 1, 6, 0),  # naive
            verification_status="collecting",
        )
        with patch(
            "weatherbrief.tasks.verification.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = _utc(2026, 4, 1, 12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            count = finalize_completed_flights(db_session)

        assert count == 1

    # NOTE: test_skips_none_departure removed — departure_time has a NOT NULL
    # constraint in the DB, so NULL departures are impossible.  The query in
    # finalize_completed_flights also filters with isnot(None) as a safety net.


# ---------------------------------------------------------------------------
# VerificationObservation Pydantic model
# ---------------------------------------------------------------------------


class TestVerificationObservationModel:

    def test_weather_json_roundtrip(self):
        obs = _make_observation(weather=["RA", "BR", "OVC"])
        json_str = obs.weather_json()
        assert json.loads(json_str) == ["RA", "BR", "OVC"]

        recovered = VerificationObservation.weather_from_json(json_str)
        assert recovered == ["RA", "BR", "OVC"]

    def test_weather_from_json_empty(self):
        assert VerificationObservation.weather_from_json(None) == []
        assert VerificationObservation.weather_from_json("") == []

    def test_weather_from_json_invalid(self):
        assert VerificationObservation.weather_from_json("not json") == []


# ---------------------------------------------------------------------------
# Naive-datetime boundary (#520)
# ---------------------------------------------------------------------------
#
# The verification columns are `TZDateTime`, which rejects naive datetimes at
# the bind. The euro_aip parser's awareness is not guaranteed, so the two
# defences below are what stand between it and a failed ingestion cycle:
# normalise at the parser boundary, and survive it if one slips through.


class _FakeReports:
    """Minimal stand-in for euro_aip's WeatherCollection.

    Only the chain `fetch_observations_batch` actually uses:
    `.metars().chronological().all()`.
    """

    def __init__(self, reports):
        self._reports = list(reports)

    def metars(self):
        return _FakeReports(self._reports)

    def chronological(self):
        return _FakeReports(
            sorted(self._reports, key=lambda r: r.observation_time)
        )

    def all(self):
        return list(self._reports)


def _fake_airport(icao, metars, taf=None):
    """Airport stub exposing the `reports` collection the fetch loop reads."""
    return SimpleNamespace(
        icao=icao,
        reports=_FakeReports(metars),
        latest_taf=taf,
    )


def _fake_metar(observation_time, report_type="METAR", raw_text=None):
    return SimpleNamespace(
        observation_time=observation_time,
        report_type=report_type,
        raw_text=raw_text or "METAR EGTK 011200Z 27010KT 9999 FEW040 12/05 Q1013",
        ceiling_ft=4000,
        visibility_meters=9999,
        wind_direction=270,
        wind_speed=10,
        wind_gust=None,
        temperature=12,
        dewpoint=5,
        altimeter=1013.0,
        weather_conditions=[],
        flight_category=None,
    )


class TestFetchObservationsBatchNormalisesParserTimes:
    """`fetch_observations_batch` is the parser boundary — nothing naive past it.

    `VerificationObservation` normalises via a field validator, but Pydantic
    runs validators on *assignment* only under `validate_assignment=True`,
    which the model does not set. `taf_issue_time` is assigned after
    construction, so the explicit `_as_utc` at that call site is the only thing
    covering it — and nothing exercised that call site until this test.
    """

    def _run(self, taf_issue_time):
        now = datetime.now(timezone.utc)
        metar = _fake_metar(now - timedelta(minutes=20))
        taf = SimpleNamespace(
            observation_time=taf_issue_time,
            raw_text="TAF EGTK 011100Z 0112/0212 27010KT 9999 FEW040",
        )
        airport = _fake_airport("EGTK", [metar], taf=taf)

        fake_service = MagicMock()
        fake_service.fetch_route_weather.return_value = SimpleNamespace(
            airports=[airport],
        )

        with patch(
            "euro_aip.briefing.weather.route_weather.RouteWeatherService",
            return_value=fake_service,
        ), patch(
            "weatherbrief.airports._load_airport_model", return_value=object(),
        ), patch(
            "euro_aip.briefing.weather.analysis.WeatherAnalyzer.find_applicable_taf",
            return_value=None,
        ):
            return fetch_observations_batch(["EGTK"], "unused.db")

    def test_naive_taf_issue_time_is_made_aware(self):
        """Regression guard: drop the `_as_utc` at the call site and this fails."""
        naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)

        observations = self._run(naive)

        assert len(observations) == 1
        issued = observations[0].taf_issue_time
        assert issued is not None
        assert issued.tzinfo is not None, (
            "naive taf_issue_time escaped the parser boundary — it will raise "
            "at the TZDateTime bind and fail the whole batch"
        )
        assert issued.utcoffset() == timedelta(0)

    def test_aware_taf_issue_time_passes_through_unchanged(self):
        aware = datetime.now(timezone.utc) - timedelta(hours=1)

        observations = self._run(aware)

        assert observations[0].taf_issue_time == aware


class TestStoreObservationsSurvivesBadValue:
    """One unstorable observation must not take down the whole chunk.

    The flush is wrapped in `except IntegrityError` for the insert race;
    a naive datetime raises `StatementError`, which that clause does not
    match, so before the `except StatementError` handler this failed every
    airport in the batch rather than skipping the offending row.
    """

    def test_naive_taf_issue_time_skips_only_that_row(self, db_session, dev_user):
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        icao_map = {"EGTK": {flight.id}, "LFPB": {flight.id}}

        bad = _make_observation(icao="EGTK")
        # Assignment, not construction — the one path the validator misses.
        bad.taf_issue_time = datetime(2026, 4, 1, 11, 0)
        assert bad.taf_issue_time.tzinfo is None, "fixture must stay naive"

        good = _make_observation(icao="LFPB")

        # Bad one first, so a surviving second insert proves the batch continued.
        count = store_observations([bad, good], icao_map, db_session)
        db_session.flush()

        assert count == 1
        rows = db_session.execute(
            select(VerificationObservationRow)
        ).scalars().all()
        assert [r.icao for r in rows] == ["LFPB"]


class TestSpeciPreservation:
    """SPECIs must survive ingest, and must not disturb the routine baseline.

    A SPECI is issued *because* conditions changed significantly — thunderstorm
    onset, ceiling collapse, visibility crossing a threshold. Two gates used to
    discard them: the fetch kept only `latest_metar` out of the three hours
    euro_aip returns, and the 30-min bucket filter then kept whichever report
    arrived first, which is almost always the routine one (#562).
    """

    def _fetch(self, metars, taf=None):
        airport = _fake_airport("EGTK", metars, taf=taf)
        fake_service = MagicMock()
        fake_service.fetch_route_weather.return_value = SimpleNamespace(
            airports=[airport],
        )
        with patch(
            "euro_aip.briefing.weather.route_weather.RouteWeatherService",
            return_value=fake_service,
        ), patch(
            "weatherbrief.airports._load_airport_model", return_value=object(),
        ):
            return fetch_observations_batch(["EGTK"], "unused.db")

    def test_every_report_in_the_window_is_returned(self):
        """Not just the latest — that was the first of the two gates."""
        now = datetime.now(timezone.utc)
        metars = [
            _fake_metar(now - timedelta(minutes=90)),
            _fake_metar(now - timedelta(minutes=60)),
            _fake_metar(now - timedelta(minutes=30)),
        ]

        observations = self._fetch(metars)

        assert len(observations) == 3
        assert all(o.report_type == "METAR" for o in observations)

    def test_speci_report_type_is_carried_through(self):
        now = datetime.now(timezone.utc)
        metars = [
            _fake_metar(now - timedelta(minutes=30)),
            _fake_metar(
                now - timedelta(minutes=13),
                report_type="SPECI",
                raw_text="SPECI EGTK 011213Z 27025G40KT 3000 TSRA BKN008CB 12/11 Q1005",
            ),
        ]

        observations = self._fetch(metars)

        by_type = {o.report_type for o in observations}
        assert by_type == {"METAR", "SPECI"}

    def test_speci_survives_a_bucket_already_claimed_by_a_metar(self, db_session):
        """The original bug: routine report arrives first and blocks the SPECI."""
        bucket = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        routine = _make_observation(observation_time=bucket)
        speci = _make_observation(
            observation_time=bucket + timedelta(minutes=13),
            report_type="SPECI",
            metar_raw="SPECI EGTK 011213Z 27025G40KT 3000 TSRA BKN008CB 12/11 Q1005",
        )

        stored = store_observations([routine, speci], {}, db_session)
        db_session.flush()

        assert stored == 2
        rows = db_session.execute(
            select(VerificationObservationRow).order_by(
                VerificationObservationRow.observation_time
            )
        ).scalars().all()
        assert [r.report_type for r in rows] == ["METAR", "SPECI"]

    def test_a_speci_does_not_claim_the_bucket(self, db_session):
        """The same bug inverted — an early SPECI must not block the METAR.

        Without this, the filter would still keep one report per bucket, just a
        different one, and the routine baseline every existing score is built
        on would start disappearing.
        """
        bucket = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        speci = _make_observation(
            observation_time=bucket + timedelta(minutes=3),
            report_type="SPECI",
            metar_raw="SPECI EGTK 011203Z 27025G40KT 3000 TSRA BKN008CB 12/11 Q1005",
        )
        routine = _make_observation(observation_time=bucket + timedelta(minutes=20))

        # SPECI first, so a surviving METAR proves the bucket was never claimed.
        stored = store_observations([speci], {}, db_session)
        db_session.flush()
        assert stored == 1

        assert not _should_skip_for_bucket_dedup(
            db_session, "EGTK", bucket + timedelta(minutes=20),
        ), "a SPECI alone in a bucket must leave it open for the routine report"

        assert store_observations([routine], {}, db_session) == 1

    def test_routine_reports_still_dedup_within_a_bucket(self, db_session):
        """The volume guard the filter exists for is unchanged."""
        bucket = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        first = _make_observation(observation_time=bucket)
        second = _make_observation(observation_time=bucket + timedelta(minutes=20))

        stored = store_observations([first, second], {}, db_session)
        db_session.flush()

        assert stored == 1

    def test_speci_is_not_linked_to_flights(self, db_session, dev_user):
        """score_flight scores one row per linked observation.

        Linking SPECIs would add extra, bad-weather-biased scores to every
        flight — the artefact this whole design exists to avoid.
        """
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        icao_map = {"EGTK": {flight.id}}
        bucket = NOW  # inside the flight window, so linkage is what is tested

        routine = _make_observation(observation_time=bucket)
        speci = _make_observation(
            observation_time=bucket + timedelta(minutes=13),
            report_type="SPECI",
            metar_raw="SPECI EGTK 011213Z 27025G40KT 3000 TSRA BKN008CB 12/11 Q1005",
        )

        store_observations([routine, speci], icao_map, db_session)
        db_session.flush()

        links = db_session.execute(
            select(FlightVerificationMapRow).where(
                FlightVerificationMapRow.observation_id.isnot(None)
            )
        ).scalars().all()
        linked_ids = {link.observation_id for link in links}

        rows = {
            r.report_type: r.id
            for r in db_session.execute(
                select(VerificationObservationRow)
            ).scalars().all()
        }
        assert rows["METAR"] in linked_ids
        assert rows["SPECI"] not in linked_ids


class TestRoutineObservationFilter:
    """NULL report_type reads as routine — what makes 091 behaviour-preserving."""

    def test_null_and_metar_are_routine_speci_is_not(self, db_session):
        from weatherbrief.tasks.verification import (
            is_routine,
            routine_observation_filter,
        )

        assert is_routine(None)
        assert is_routine("METAR")
        assert not is_routine("SPECI")

        base = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        for i, report_type in enumerate((None, "METAR", "SPECI")):
            db_session.add(
                VerificationObservationRow(
                    icao="EGTK",
                    observation_time=base + timedelta(hours=i),
                    collected_at=base,
                    report_type=report_type,
                )
            )
        db_session.flush()

        kept = db_session.execute(
            select(VerificationObservationRow).where(routine_observation_filter())
        ).scalars().all()

        assert sorted(r.report_type or "NULL" for r in kept) == ["METAR", "NULL"]


class TestFlightLinkageStaysInsideTheWindow:
    """The fetch returns ~3 h of history; linkage must not.

    `fetch_observations_batch` is shared by the flight and standalone paths.
    Once it returns every report in euro_aip's window rather than only
    `latest_metar`, linking whatever comes back would attach observations
    from before a flight's window ever opened — and, where two flights'
    corridors share an airport, attach one flight's earlier observations to
    the other. `score_flight` scores one row per linked observation and
    applies no window filter of its own, so those would be scored against
    forecast hours that have nothing to do with the flight.
    """

    def test_observation_before_the_window_is_stored_but_not_linked(
        self, db_session, dev_user,
    ):
        # Flight departs NOW-1h, runs 2h → window [NOW-2h, NOW+3h].
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        icao_map = {"EGTK": {flight.id}}

        stale = _make_observation(observation_time=NOW - timedelta(hours=3))
        inside = _make_observation(observation_time=NOW)

        store_observations([stale, inside], icao_map, db_session)
        db_session.flush()

        stored = {
            r.observation_time: r.id
            for r in db_session.execute(
                select(VerificationObservationRow)
            ).scalars().all()
        }
        assert len(stored) == 2, "both observations must still be stored"

        linked = {
            link.observation_id
            for link in db_session.execute(
                select(FlightVerificationMapRow).where(
                    FlightVerificationMapRow.observation_id.isnot(None)
                )
            ).scalars().all()
        }
        assert stored[NOW] in linked
        assert stored[NOW - timedelta(hours=3)] not in linked

    def test_observation_after_the_window_is_not_linked(self, db_session, dev_user):
        flight = _make_flight(db_session, dev_user, verification_status="collecting")
        icao_map = {"EGTK": {flight.id}}

        late = _make_observation(observation_time=NOW + timedelta(hours=5))

        store_observations([late], icao_map, db_session)
        db_session.flush()

        links = db_session.execute(
            select(FlightVerificationMapRow).where(
                FlightVerificationMapRow.observation_id.isnot(None)
            )
        ).scalars().all()
        assert links == []
