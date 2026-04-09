"""Tests for the verification dashboard cache builder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def db_engine():
    """Per-test engine — functions under test call session.commit()."""
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()


from weatherbrief.db.models import (
    VerificationCacheRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.cache_builder import (
    _upsert,
    get_cached,
    get_cache_meta,
    get_source_max_time,
    is_stale,
    rebuild_stats_cache,
)

NOW = datetime(2026, 4, 8, 12, 0, tzinfo=timezone.utc)


class TestCacheReadWrite:
    """Test basic cache CRUD operations."""

    def test_get_cached_miss(self, db_session):
        assert get_cached(db_session, "nonexistent") is None

    def test_upsert_and_read(self, db_session):
        _upsert(db_session, "test:key", {"hello": "world"}, NOW)
        db_session.flush()

        result = get_cached(db_session, "test:key")
        assert result == {"hello": "world"}

    def test_upsert_overwrites(self, db_session):
        _upsert(db_session, "test:key", {"v": 1}, NOW)
        db_session.flush()
        _upsert(db_session, "test:key", {"v": 2}, NOW + timedelta(hours=1))
        db_session.flush()

        result = get_cached(db_session, "test:key")
        assert result == {"v": 2}

        computed, source_max = get_cache_meta(db_session, "test:key")
        # SQLite drops tzinfo, so compare naive
        assert source_max.replace(tzinfo=None) == (NOW + timedelta(hours=1)).replace(tzinfo=None)

    def test_get_cache_meta_miss(self, db_session):
        computed, source_max = get_cache_meta(db_session, "nope")
        assert computed is None
        assert source_max is None


class TestStalenessCheck:
    """Test staleness detection."""

    def _insert_score(self, db_session, source: str, obs_time: datetime):
        obs = VerificationObservationRow(
            icao="EGLL", observation_time=obs_time,
            collected_at=obs_time,
        )
        db_session.add(obs)
        db_session.flush()
        db_session.add(VerificationScoreRow(
            observation_id=obs.id, icao="EGLL",
            observation_time=obs_time,
            model="gfs", model_init_time=obs_time - timedelta(hours=6),
            lead_hours=6, days_out=0, source=source,
        ))
        db_session.flush()

    def test_stale_when_no_cache(self, db_session):
        self._insert_score(db_session, "standalone", NOW)
        assert is_stale(db_session, "stats:standalone:24h", "standalone") is True

    def test_not_stale_when_cache_matches(self, db_session):
        self._insert_score(db_session, "standalone", NOW)
        source_max = get_source_max_time(db_session, "standalone")
        _upsert(db_session, "stats:standalone:24h", {"data": 1}, source_max)
        db_session.flush()

        assert is_stale(db_session, "stats:standalone:24h", "standalone") is False

    def test_stale_when_new_data(self, db_session):
        self._insert_score(db_session, "standalone", NOW)
        _upsert(db_session, "stats:standalone:24h", {"data": 1}, NOW)
        db_session.flush()

        # Add newer score
        self._insert_score(db_session, "standalone", NOW + timedelta(hours=1))
        assert is_stale(db_session, "stats:standalone:24h", "standalone") is True

    def test_source_isolation(self, db_session):
        """Flight and standalone sources have independent staleness."""
        self._insert_score(db_session, "flight", NOW)
        self._insert_score(db_session, "standalone", NOW - timedelta(hours=1))

        flight_max = get_source_max_time(db_session, "flight")
        standalone_max = get_source_max_time(db_session, "standalone")

        # SQLite drops tzinfo, so compare naive
        assert flight_max.replace(tzinfo=None) == NOW.replace(tzinfo=None)
        assert standalone_max.replace(tzinfo=None) == (NOW - timedelta(hours=1)).replace(tzinfo=None)


class TestRebuildStatsCache:
    """Test the stats cache rebuild."""

    def _insert_scores(self, db_session):
        obs = VerificationObservationRow(
            icao="EGLL", observation_time=NOW, collected_at=NOW,
            flight_category="VFR",
        )
        db_session.add(obs)
        db_session.flush()
        for source in ("flight", "standalone"):
            db_session.add(VerificationScoreRow(
                observation_id=obs.id, icao="EGLL",
                observation_time=NOW,
                model="gfs", model_init_time=NOW - timedelta(hours=6),
                lead_hours=6, days_out=0, source=source,
                obs_flight_category="VFR", model_flight_category="VFR",
                category_match=True,
            ))
        db_session.flush()

    def test_rebuild_creates_entries(self, db_session):
        self._insert_scores(db_session)
        count = rebuild_stats_cache(db_session)

        # 2 sources × 3 periods = 6 entries
        assert count == 6

        # Verify a specific entry exists and has valid data
        cached = get_cached(db_session, "stats:standalone:24h")
        assert cached is not None
        assert "activity" in cached
        assert "category_accuracy_today" in cached
        assert "notable_misses" in cached

    def test_rebuild_is_idempotent(self, db_session):
        self._insert_scores(db_session)
        rebuild_stats_cache(db_session)
        db_session.flush()

        # Rebuild again — should upsert, not duplicate
        count = rebuild_stats_cache(db_session)
        assert count == 6

        rows = db_session.query(VerificationCacheRow).filter(
            VerificationCacheRow.cache_key.like("stats:%")
        ).all()
        assert len(rows) == 6

    def test_cache_not_stale_after_rebuild(self, db_session):
        self._insert_scores(db_session)
        rebuild_stats_cache(db_session)
        db_session.flush()

        assert is_stale(db_session, "stats:standalone:24h", "standalone") is False
        assert is_stale(db_session, "stats:flight:30d", "flight") is False
