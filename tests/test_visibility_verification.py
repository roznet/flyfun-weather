"""Scoring the derived visibility / fog methods against METARs (#979 phase 1)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select

from weatherbrief.analysis.visibility.methods import METHODS_VERSION
from weatherbrief.db.models import VerificationScoreRow
from weatherbrief.tasks.airport_watchlist import WatchlistAirport
from weatherbrief.tasks.standalone_verification import _score_cycle
from weatherbrief.tasks.visibility_verification import (
    ScoredPair,
    backfill_visibility_methods,
    load_pairs,
    observed_fog,
    observed_visibility_m,
    pairs_from_rows,
    promotion_verdict,
    visibility_report,
)
from tests.test_standalone_verification import (
    GFS_INIT,
    NOW,
    _insert_observation,
    _insert_snapshot,
)

FOGGY = dict(
    model="icon", temperature_2m_c=4.0, dewpoint_2m_c=3.9, visibility_m=300.0,
    wind_speed_10m_kt=2.0, cloud_cover_low_pct=10.0, surface_pressure_hpa=1010.0,
    lowest_level_cloud_water_kg_kg=2e-4, lowest_level_cloud_cover_pct=100.0,
    t850_c=6.0, td850_c=-2.0, wind_speed_850_kt=8.0,
)


class TestObserved:
    def test_visibility_cap(self):
        assert observed_visibility_m(9999) == 10000
        assert observed_visibility_m(4000) == 4000
        assert observed_visibility_m(None) is None

    @pytest.mark.parametrize("vis,wx,expected", [
        (800, [], True),
        (3000, ["FG"], True),
        (3000, ["FZFG"], True),
        (3000, ["BCFG"], False),
        (3000, ["MIFG", "BR"], False),
        (1000, [], False),
        (None, [], None),
        (None, ["FG"], True),
    ])
    def test_fog(self, vis, wx, expected):
        assert observed_fog(vis, wx) is expected


class TestScoreCycleWritesMethods:
    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_payload_on_the_score(self, _rwy, db_session):
        _insert_snapshot(db_session, **FOGGY)
        _insert_observation(db_session, visibility_m=200, weather=json.dumps(["FG"]))
        airports = [WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55)]

        _score_cycle(NOW, airports, "/fake/db", db_session)

        row = db_session.execute(select(VerificationScoreRow)).scalar_one()
        data = json.loads(row.visibility_methods)
        assert data["v"] == METHODS_VERSION
        assert data["night"] is False  # 12Z at LFPG
        assert data["m"]["native"] == 300
        assert data["m"]["sw99"] < 1000
        assert data["m"]["fog_tier"] == "LIKELY"
        assert data["m"]["fog_radiation"] is False  # daytime

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_unknown_airport_has_no_night_flag(self, _rwy, db_session):
        _insert_snapshot(db_session)
        _insert_observation(db_session)

        _score_cycle(NOW, [], "/fake/db", db_session)

        data = json.loads(db_session.execute(select(VerificationScoreRow)).scalar_one()
                          .visibility_methods)
        assert data["night"] is None
        assert "fog_radiation" not in data["m"]
        # GFS row without lowest-level inputs: only what it has.
        assert "sw99" not in data["m"] and "rh_haze" in data["m"]


@pytest.fixture
def own_db():
    """Private in-memory DB: the backfill commits, which the shared
    rolled-back session must not see."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from weatherbrief.db.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


class TestBackfill:
    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_fills_null_rows_from_the_snapshot(self, _rwy, own_db):
        _insert_snapshot(own_db, **{**FOGGY, "model": "gfs"})
        _insert_observation(own_db, visibility_m=200)
        _score_cycle(NOW, [], "/fake/db", own_db)
        row = own_db.execute(select(VerificationScoreRow)).scalar_one()
        row.visibility_methods = None  # as written before migration 117
        own_db.flush()

        stats = backfill_visibility_methods(
            own_db, NOW - timedelta(days=1), {"LFPG": (49.01, 2.55)},
        )

        assert stats == {"scores": 1, "filled": 1, "no_snapshot": 0, "failed": 0}
        assert json.loads(row.visibility_methods)["m"]["sw99"] < 1000
        # Idempotent: nothing left to fill.
        assert backfill_visibility_methods(
            own_db, NOW - timedelta(days=1), {},
        )["scores"] == 0

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_pruned_snapshot_is_counted(self, _rwy, own_db):
        snap = _insert_snapshot(own_db)
        _insert_observation(own_db)
        _score_cycle(NOW, [], "/fake/db", own_db)
        row = own_db.execute(select(VerificationScoreRow)).scalar_one()
        row.visibility_methods = None
        own_db.delete(snap)
        own_db.flush()

        stats = backfill_visibility_methods(own_db, NOW - timedelta(days=1), {})

        assert stats == {"scores": 1, "filled": 0, "no_snapshot": 1, "failed": 0}
        assert row.visibility_methods is None

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_a_failing_row_does_not_abort_the_run(self, _rwy, own_db):
        _insert_snapshot(own_db)
        _insert_observation(own_db)
        _score_cycle(NOW, [], "/fake/db", own_db)
        row = own_db.execute(select(VerificationScoreRow)).scalar_one()
        row.visibility_methods = None
        own_db.flush()

        with patch("weatherbrief.tasks.visibility_verification.score_payload",
                   side_effect=ValueError("bad row")):
            stats = backfill_visibility_methods(own_db, NOW - timedelta(days=1), {})

        assert stats == {"scores": 1, "filled": 0, "no_snapshot": 0, "failed": 1}
        assert row.visibility_methods is None

    @patch("weatherbrief.airports.get_runway_ends", return_value={})
    def test_load_pairs_round_trip(self, _rwy, own_db):
        _insert_snapshot(own_db, **FOGGY)
        _insert_observation(own_db, visibility_m=200, weather=json.dumps(["FG"]))
        _score_cycle(NOW, [], "/fake/db", own_db)

        pairs = load_pairs(own_db, NOW - timedelta(days=1), model="icon")

        assert len(pairs) == 1
        assert pairs[0].obs_vis_m == 200 and pairs[0].obs_fog is True
        assert pairs[0].methods["native"] == 300
        assert pairs[0].obs_day == pairs[0].obs_day.__class__(NOW.year, NOW.month, NOW.day)


def _pair(model="icon", days_out=0, night=False, obs=10000.0, fog=False, **methods):
    return ScoredPair(model, days_out, night, methods, obs, fog)


class TestReport:
    def test_bands_and_error(self):
        pairs = [
            _pair(native=800, sw99=9000, obs=600.0, fog=True),
            _pair(native=9000, sw99=900, obs=10000.0),
        ]
        cells = visibility_report(pairs)
        native = cells[("icon", "native", "D0", "all")]
        assert native.n == 2
        assert native.bands[1500.0].hits == 1 and native.bands[1500.0].false_alarms == 0
        assert native.fog.csi == 1.0
        sw = cells[("icon", "sw99", "all", "day")]
        assert sw.bands[1500.0].misses == 1 and sw.bands[1500.0].false_alarms == 1
        assert sw.mae_log10 > native.mae_log10

    def test_fog_tier_rates(self):
        pairs = [
            _pair(fog_tier="LIKELY", fog=True),
            _pair(fog_tier="LIKELY", fog=False),
            _pair(fog_tier="NONE", fog=False),
        ]
        st = visibility_report(pairs)[("icon", "fog_tier", "all", "all")]
        assert st.tiers == {"LIKELY": [2, 1], "NONE": [1, 0]}

    def test_other_version_is_left_out(self):
        rows = [
            ("icon", 0, json.dumps({"v": METHODS_VERSION, "night": True, "m": {"native": 500}}), 400, "[]"),
            ("icon", 0, json.dumps({"v": "old", "night": True, "m": {"native": 500}}), 400, "[]"),
            ("icon", 0, None, 400, "[]"),
        ]
        dropped: dict[str, int] = {}
        assert len(pairs_from_rows(rows, stats=dropped)) == 1
        assert dropped == {"bad_json": 0, "other_version": 1}
        assert len(pairs_from_rows(rows, version=None)) == 2


class TestPromotion:
    @staticmethod
    def _sample(cand_ok: bool):
        pairs = []
        # 50 hours < 5 km of which 20 < 1.5 km, 200 clear hours.
        for i in range(50):
            obs = 1000.0 if i < 20 else 3000.0
            pairs.append(_pair(native=obs, sw99=obs if cand_ok else 9000, obs=obs))
        for _ in range(200):
            pairs.append(_pair(native=10000, sw99=10000, obs=10000.0))
        return pairs

    def test_passes_when_as_good_as_native(self):
        v = promotion_verdict(self._sample(True), "icon", "sw99")
        assert v.passed and v.enough_data

    def test_fails_when_it_misses_the_events(self):
        v = promotion_verdict(self._sample(False), "icon", "sw99")
        assert not v.passed and v.enough_data
        assert any("CSI" in r for r in v.reasons)

    def test_not_enough_data(self):
        v = promotion_verdict(self._sample(True)[:30], "icon", "sw99")
        assert not v.passed and not v.enough_data

    def test_events_count_distinct_observations(self):
        # The same METARs scored by four runs are still the same events.
        one_run = self._sample(True)[:30]
        pairs = []
        for run in range(4):
            pairs += [dataclasses.replace(p, obs_key=i) for i, p in enumerate(one_run)]
        v = promotion_verdict(pairs, "icon", "sw99")
        assert not v.enough_data
        assert any("observed hours" in r for r in v.reasons)

    def test_one_day_is_not_enough(self):
        # Plenty of observed hours, but all on one fog morning.
        day = datetime(2026, 11, 9).date()
        one_day = [dataclasses.replace(p, obs_key=i, obs_day=day)
                   for i, p in enumerate(self._sample(True))]
        v = promotion_verdict(one_day, "icon", "sw99")
        assert not v.enough_data
        assert any("1/3 days < 1.5 km" in r for r in v.reasons)
        spread = [dataclasses.replace(p, obs_day=day + timedelta(days=p.obs_key % 5))
                  for p in one_day]
        assert promotion_verdict(spread, "icon", "sw99").passed

    def test_false_ifr_fails(self):
        pairs = self._sample(True)
        pairs += [_pair(native=10000, sw99=1000, obs=10000.0) for _ in range(30)]
        v = promotion_verdict(pairs, "icon", "sw99")
        assert not v.passed
        assert any("FAR" in r for r in v.reasons)

    def test_only_d0_to_d2_and_same_hours(self):
        pairs = self._sample(True)
        pairs += [_pair(days_out=3, native=10000, sw99=500, obs=10000.0) for _ in range(100)]
        pairs += [_pair(sw99=500, obs=10000.0) for _ in range(100)]  # no native
        assert promotion_verdict(pairs, "icon", "sw99").passed
