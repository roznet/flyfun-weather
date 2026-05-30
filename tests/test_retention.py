"""Tests for the tiered disk-space retention system."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flyfun_common.db import DEV_USER_ID
from flyfun_common.db.models import UserRow
from weatherbrief.db.models import BriefingPackRow, BriefingUsageRow, FlightRow
from weatherbrief.tasks.retention import (
    RetentionConfig,
    RetentionStats,
    _inactive_user_ids,
    _purge_full_pack,
    _purge_heavy_artifacts,
    prune_raw_observations,
    run_retention,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_pack_dir(tmp_path: Path, *, heavy: bool = True) -> Path:
    """Create a realistic pack directory with both heavy and light files."""
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()

    # Light files (always present)
    for name in (
        "briefing.json", "route_analyses.json", "route_advisories.json",
        "elevation_profile.json", "route_points.json", "fetch_meta.json",
        "digest.json", "digest.md",
    ):
        (pack_dir / name).write_text(f'{{"file": "{name}"}}')

    if heavy:
        # Heavy files
        (pack_dir / "cross_section.json").write_bytes(b"x" * 10_000)
        (pack_dir / "forecasts.json").write_bytes(b"x" * 2_000)
        (pack_dir / "gramet.pdf").write_bytes(b"%PDF" + b"x" * 5_000)
        (pack_dir / "gramet.png").write_bytes(b"\x89PNG" + b"x" * 3_000)
        # Sounding-profile sidecar — derived from cross_section.json, so it must
        # be stripped in the same T1 sweep (issue #188).
        (pack_dir / "sounding_profiles.json.gz").write_bytes(b"\x1f\x8b" + b"x" * 4_000)

        skewt = pack_dir / "skewt" / "route"
        skewt.mkdir(parents=True)
        for i in range(3):
            (skewt / f"pt{i:02d}_gfs.png").write_bytes(b"\x89PNG" + b"x" * 1_000)

    return pack_dir


def _insert_flight(db, user_id, flight_id="flight-1", departure_days_ago=60):
    """Insert a flight with departure N days ago."""
    dep = _now() - timedelta(days=departure_days_ago)
    row = FlightRow(
        id=flight_id,
        user_id=user_id,
        route_name="test",
        waypoints_json="[]",
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=2.0,
    )
    db.add(row)
    db.flush()
    return row


def _insert_pack(db, flight_id, artifact_path, has_skewt=True, has_gramet=True):
    """Insert a briefing pack row."""
    row = BriefingPackRow(
        flight_id=flight_id,
        fetch_timestamp=_now() - timedelta(days=1),
        days_out=3,
        has_gramet=has_gramet,
        has_skewt=has_skewt,
        has_digest=True,
        assessment="GREEN",
        artifact_path=str(artifact_path),
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# RetentionConfig
# ---------------------------------------------------------------------------


class TestRetentionConfig:

    def test_defaults(self):
        cfg = RetentionConfig()
        assert cfg.t1_days == 30
        assert cfg.t2_active_days == 180
        assert cfg.t2_inactive_days == 90
        assert cfg.inactive_threshold_days == 30
        assert cfg.dry_run is False

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("RETENTION_T1_DAYS", "15")
        monkeypatch.setenv("RETENTION_T2_ACTIVE_DAYS", "120")
        monkeypatch.setenv("RETENTION_T2_INACTIVE_DAYS", "60")
        monkeypatch.setenv("RETENTION_INACTIVE_DAYS", "14")
        monkeypatch.setenv("RETENTION_DRY_RUN", "1")

        cfg = RetentionConfig.from_env()
        assert cfg.t1_days == 15
        assert cfg.t2_active_days == 120
        assert cfg.t2_inactive_days == 60
        assert cfg.inactive_threshold_days == 14
        assert cfg.dry_run is True

    def test_from_env_defaults(self, monkeypatch):
        # Ensure no retention env vars are set
        for key in ("RETENTION_T1_DAYS", "RETENTION_T2_ACTIVE_DAYS",
                     "RETENTION_T2_INACTIVE_DAYS", "RETENTION_INACTIVE_DAYS",
                     "RETENTION_DRY_RUN"):
            monkeypatch.delenv(key, raising=False)

        cfg = RetentionConfig.from_env()
        assert cfg.t1_days == 30
        assert cfg.dry_run is False


# ---------------------------------------------------------------------------
# T1 — purge heavy artifacts
# ---------------------------------------------------------------------------


class TestPurgeHeavyArtifacts:

    def test_removes_heavy_files_and_dirs(self, db_session, dev_user, tmp_path):
        pack_dir = _make_pack_dir(tmp_path)
        _insert_flight(db_session, dev_user, departure_days_ago=35)
        pack = _insert_pack(db_session, "flight-1", pack_dir)

        freed = _purge_heavy_artifacts(pack, pack_dir, dry_run=False)

        assert freed > 0
        # Heavy files gone
        assert not (pack_dir / "cross_section.json").exists()
        assert not (pack_dir / "forecasts.json").exists()
        assert not (pack_dir / "gramet.pdf").exists()
        assert not (pack_dir / "gramet.png").exists()
        assert not (pack_dir / "sounding_profiles.json.gz").exists()
        assert not (pack_dir / "skewt").exists()
        # Light files preserved
        assert (pack_dir / "briefing.json").exists()
        assert (pack_dir / "route_analyses.json").exists()
        assert (pack_dir / "route_advisories.json").exists()
        assert (pack_dir / "digest.json").exists()
        assert (pack_dir / "elevation_profile.json").exists()
        # DB flags updated
        assert pack.has_skewt is False
        assert pack.has_gramet is False

    def test_skips_already_stripped(self, db_session, dev_user, tmp_path):
        # No heavy files left on disk → nothing to free (idempotent re-run).
        pack_dir = _make_pack_dir(tmp_path, heavy=False)
        _insert_flight(db_session, dev_user, departure_days_ago=35)
        pack = _insert_pack(db_session, "flight-1", pack_dir, has_skewt=False, has_gramet=False)

        freed = _purge_heavy_artifacts(pack, pack_dir, dry_run=False)
        assert freed == 0

    def test_strips_heavy_files_when_flags_false(self, db_session, dev_user, tmp_path):
        # Regression: forecasts.json / cross_section.json / the sounding sidecar
        # exist independently of has_skewt/has_gramet. With generate_skewt=False
        # (the default) both flags are commonly False — the purge must still
        # reclaim those heavy files rather than skip the pack and leak them to T2.
        pack_dir = _make_pack_dir(tmp_path)
        _insert_flight(db_session, dev_user, departure_days_ago=35)
        pack = _insert_pack(db_session, "flight-1", pack_dir, has_skewt=False, has_gramet=False)

        freed = _purge_heavy_artifacts(pack, pack_dir, dry_run=False)

        assert freed > 0
        assert not (pack_dir / "forecasts.json").exists()
        assert not (pack_dir / "cross_section.json").exists()
        assert not (pack_dir / "sounding_profiles.json.gz").exists()

    def test_handles_missing_dir(self, db_session, dev_user, tmp_path):
        nonexistent = tmp_path / "gone"
        _insert_flight(db_session, dev_user, departure_days_ago=35)
        pack = _insert_pack(db_session, "flight-1", nonexistent)

        freed = _purge_heavy_artifacts(pack, nonexistent, dry_run=False)
        assert freed == 0

    def test_dry_run_preserves_files(self, db_session, dev_user, tmp_path):
        pack_dir = _make_pack_dir(tmp_path)
        _insert_flight(db_session, dev_user, departure_days_ago=35)
        pack = _insert_pack(db_session, "flight-1", pack_dir)

        freed = _purge_heavy_artifacts(pack, pack_dir, dry_run=True)

        assert freed > 0  # reports what would be freed
        # But files are still there
        assert (pack_dir / "cross_section.json").exists()
        assert (pack_dir / "skewt").exists()
        # DB flags NOT updated
        assert pack.has_skewt is True
        assert pack.has_gramet is True


# ---------------------------------------------------------------------------
# T2 — purge full pack
# ---------------------------------------------------------------------------


class TestPurgeFullPack:

    def test_removes_dir_and_db_row(self, db_session, dev_user, tmp_path):
        pack_dir = _make_pack_dir(tmp_path)
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id

        freed = _purge_full_pack(pack, pack_dir, dry_run=False)
        db_session.flush()

        assert freed > 0
        assert not pack_dir.exists()
        assert db_session.get(BriefingPackRow, pack_id) is None

    def test_keeps_flight_row(self, db_session, dev_user, tmp_path):
        pack_dir = _make_pack_dir(tmp_path)
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)

        _purge_full_pack(pack, pack_dir, dry_run=False)
        db_session.flush()

        assert db_session.get(FlightRow, "flight-1") is not None

    def test_dry_run_preserves_everything(self, db_session, dev_user, tmp_path):
        pack_dir = _make_pack_dir(tmp_path)
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id

        freed = _purge_full_pack(pack, pack_dir, dry_run=True)

        assert freed > 0
        assert pack_dir.exists()
        assert db_session.get(BriefingPackRow, pack_id) is not None

    def test_handles_missing_dir(self, db_session, dev_user, tmp_path):
        nonexistent = tmp_path / "gone"
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", nonexistent)
        pack_id = pack.id

        freed = _purge_full_pack(pack, nonexistent, dry_run=False)
        db_session.flush()

        assert freed == 0
        # DB row still deleted even if directory is gone
        assert db_session.get(BriefingPackRow, pack_id) is None


# ---------------------------------------------------------------------------
# Inactive user detection
# ---------------------------------------------------------------------------


class TestInactiveUsers:

    def test_active_user_with_recent_login(self, db_session, dev_user):
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now() - timedelta(days=5)
        db_session.flush()

        inactive = _inactive_user_ids(db_session, _now(), threshold_days=30)
        assert dev_user not in inactive

    def test_inactive_user_old_login(self, db_session, dev_user):
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now() - timedelta(days=60)
        db_session.flush()

        inactive = _inactive_user_ids(db_session, _now(), threshold_days=30)
        assert dev_user in inactive

    def test_user_with_no_login_is_inactive(self, db_session, dev_user):
        # dev_user has last_login_at=None by default
        inactive = _inactive_user_ids(db_session, _now(), threshold_days=30)
        assert dev_user in inactive

    def test_recent_usage_keeps_user_active(self, db_session, dev_user):
        # Old login but recent briefing usage
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now() - timedelta(days=60)
        db_session.flush()

        db_session.add(BriefingUsageRow(
            user_id=dev_user,
            flight_id="test",
            timestamp=_now() - timedelta(days=5),
        ))
        db_session.flush()

        inactive = _inactive_user_ids(db_session, _now(), threshold_days=30)
        assert dev_user not in inactive


# ---------------------------------------------------------------------------
# Integration: run_retention
# ---------------------------------------------------------------------------


class TestRunRetention:

    def test_t1_applied_to_eligible_pack(self, db_session, dev_user, tmp_path):
        """Pack 35 days old → T1 strips heavy artifacts."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()  # active user
        _insert_flight(db_session, dev_user, departure_days_ago=35)
        _insert_pack(db_session, "flight-1", pack_dir)

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t1 == 1
        assert stats.packs_t2 == 0
        assert stats.bytes_freed > 0
        assert not (pack_dir / "cross_section.json").exists()
        assert (pack_dir / "briefing.json").exists()

    def test_t2_applied_to_old_active_user_pack(self, db_session, dev_user, tmp_path):
        """Pack 200 days old, active user → T2 deletes everything."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t2 == 1
        assert not pack_dir.exists()
        db_session.expire_all()
        assert db_session.get(BriefingPackRow, pack_id) is None
        assert db_session.get(FlightRow, "flight-1") is not None

    def test_t2_inactive_applied_earlier(self, db_session, dev_user, tmp_path):
        """Pack 100 days old, inactive user → T2 at 90 days applies."""
        pack_dir = _make_pack_dir(tmp_path)
        # User has no recent login → inactive
        _insert_flight(db_session, dev_user, departure_days_ago=100)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id

        config = RetentionConfig(
            t1_days=30, t2_active_days=180, t2_inactive_days=90,
            inactive_threshold_days=30,
        )
        stats = run_retention(db_session, config)

        assert stats.packs_t2 == 1
        db_session.expire_all()
        assert db_session.get(BriefingPackRow, pack_id) is None

    def test_recent_pack_untouched(self, db_session, dev_user, tmp_path):
        """Pack only 10 days old → no action."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=10)
        _insert_pack(db_session, "flight-1", pack_dir)

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t1 == 0
        assert stats.packs_t2 == 0
        assert (pack_dir / "cross_section.json").exists()

    def test_dry_run_changes_nothing(self, db_session, dev_user, tmp_path):
        """Dry-run mode reports stats but makes no changes."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id

        config = RetentionConfig(
            t1_days=30, t2_active_days=180, t2_inactive_days=90, dry_run=True,
        )
        stats = run_retention(db_session, config)

        assert stats.packs_t2 == 1
        assert stats.bytes_freed > 0
        # Nothing actually deleted
        assert pack_dir.exists()
        assert db_session.get(BriefingPackRow, pack_id) is not None

    def test_multiple_packs_mixed_tiers(self, db_session, dev_user, tmp_path):
        """Two flights: one at T1 age, one at T2 age."""
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()

        (tmp_path / "a").mkdir()
        dir1 = _make_pack_dir(tmp_path / "a")

        (tmp_path / "b").mkdir()
        dir2 = _make_pack_dir(tmp_path / "b")

        _insert_flight(db_session, dev_user, flight_id="f-t1", departure_days_ago=40)
        _insert_pack(db_session, "f-t1", dir1)

        _insert_flight(db_session, dev_user, flight_id="f-t2", departure_days_ago=200)
        _insert_pack(db_session, "f-t2", dir2)

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t1 == 1
        assert stats.packs_t2 == 1
        assert stats.errors == 0


class TestDebriefExemption:
    """Flights with a debrief skip T2 entirely; T1 still applies."""

    def _add_debrief(self, db_session, flight_id, decision="cancelled"):
        from weatherbrief.debriefs.taxonomy import ConditionTag, Decision
        from weatherbrief.storage.debriefs import upsert_debrief

        upsert_debrief(
            db_session,
            flight_id=flight_id,
            decision=Decision(decision),
            reasons=[ConditionTag.IMC] if decision == "cancelled" else None,
        )

    def test_t2_age_pack_kept_when_debriefed(self, db_session, dev_user, tmp_path):
        """A pack older than t2 with a debrief survives — only T1 applies."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id
        self._add_debrief(db_session, "flight-1")

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        # Pack row + dir survive (T2 skipped); heavy artifacts removed (T1 applied).
        assert stats.packs_t2 == 0
        assert stats.packs_t1 == 1
        db_session.expire_all()
        assert db_session.get(BriefingPackRow, pack_id) is not None
        assert pack_dir.exists()
        assert (pack_dir / "briefing.json").exists()
        assert not (pack_dir / "cross_section.json").exists()

    def test_t1_still_strips_when_debriefed(self, db_session, dev_user, tmp_path):
        """At T1 age (no T2 yet), debriefed packs lose heavy artifacts as normal."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=40)
        _insert_pack(db_session, "flight-1", pack_dir)
        self._add_debrief(db_session, "flight-1", decision="flown")

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t1 == 1
        assert stats.packs_t2 == 0
        assert not (pack_dir / "cross_section.json").exists()
        assert (pack_dir / "briefing.json").exists()

    def test_undebriefed_old_pack_still_purged(self, db_session, dev_user, tmp_path):
        """Sanity: without a debrief, the same old pack still gets T2."""
        pack_dir = _make_pack_dir(tmp_path)
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=200)
        pack = _insert_pack(db_session, "flight-1", pack_dir)
        pack_id = pack.id

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t2 == 1
        db_session.expire_all()
        assert db_session.get(BriefingPackRow, pack_id) is None

    def test_debrief_exempts_all_packs_for_flight(self, db_session, dev_user, tmp_path):
        """Multiple packs (refreshes) for the same flight all survive T2."""
        user = db_session.get(UserRow, dev_user)
        user.last_login_at = _now()
        _insert_flight(db_session, dev_user, departure_days_ago=200)

        (tmp_path / "p1").mkdir()
        (tmp_path / "p2").mkdir()
        dir1 = _make_pack_dir(tmp_path / "p1")
        dir2 = _make_pack_dir(tmp_path / "p2")
        p1 = _insert_pack(db_session, "flight-1", dir1)
        p2 = _insert_pack(db_session, "flight-1", dir2)
        p1_id, p2_id = p1.id, p2.id

        self._add_debrief(db_session, "flight-1")

        config = RetentionConfig(t1_days=30, t2_active_days=180, t2_inactive_days=90)
        stats = run_retention(db_session, config)

        assert stats.packs_t2 == 0
        db_session.expire_all()
        assert db_session.get(BriefingPackRow, p1_id) is not None
        assert db_session.get(BriefingPackRow, p2_id) is not None


# ---------------------------------------------------------------------------
# prune_raw_observations
# ---------------------------------------------------------------------------


class TestPruneRawObservations:
    """Verify the raw-obs retention pruner — disabled-by-default, safety
    belts, and accurate per-table counters when actually pruning."""

    def test_disabled_default_does_nothing(self, db_session):
        from weatherbrief.db.models import VerificationObservationRow

        # Insert one obs from years ago — would absolutely be pruned if
        # retention were active.
        old = VerificationObservationRow(
            icao="LFPG",
            observation_time=_utc(2020, 1, 1, 0, 0),
            collected_at=_utc(2020, 1, 1, 0, 0),
        )
        db_session.add(old)
        db_session.flush()

        result = prune_raw_observations(db_session)
        assert result == {
            "observations": 0, "scores": 0, "taf_scores": 0, "map_rows": 0,
        }

    def test_no_summarised_months_refuses_to_delete(self, db_session):
        """Safety belt: even if retain_days is short, refuse to delete when
        no months have been rolled up — could indicate a broken rollup loop."""
        from weatherbrief.db.models import VerificationObservationRow

        old = VerificationObservationRow(
            icao="LFPG",
            observation_time=_utc(2020, 1, 1, 0, 0),
            collected_at=_utc(2020, 1, 1, 0, 0),
        )
        db_session.add(old)
        db_session.flush()

        # Force an aggressive retention but no AirportMonthlySummary rows exist
        result = prune_raw_observations(db_session, retain_days=1)
        assert result["observations"] == 0
        assert db_session.get(VerificationObservationRow, old.id) is not None

    def test_deletes_children_before_parent_with_accurate_counters(self, db_session):
        """Children must be deleted before parent so per-table counters
        reflect what was actually removed (with CASCADE FKs the parent-first
        order would silently zero the child counters)."""
        from weatherbrief.db.models import (
            AirportMonthlySummaryRow,
            VerificationObservationRow,
            VerificationScoreRow,
        )

        # One obs in Feb 2026 with a score row pointing at it
        feb_obs = VerificationObservationRow(
            icao="LFPG",
            observation_time=_utc(2026, 2, 15, 12, 0),
            collected_at=_utc(2026, 2, 15, 12, 0),
        )
        db_session.add(feb_obs)
        db_session.flush()
        feb_id = feb_obs.id  # capture before prune (which expires the session)
        score = VerificationScoreRow(
            icao="LFPG",
            observation_id=feb_id,
            observation_time=feb_obs.observation_time,
            model="gfs",
            model_init_time=_utc(2026, 2, 15, 0, 0),
            lead_hours=12,
            days_out=0,
            source="standalone",
        )
        db_session.add(score)
        # Mark Feb 2026 as summarised so retention will prune it.
        db_session.add(AirportMonthlySummaryRow(
            month=_utc(2026, 2, 1), icao="LFPG", n_obs=1,
        ))
        db_session.flush()

        # retain_days=1 with cutoff way in the future from Feb 2026 → prune
        result = prune_raw_observations(db_session, retain_days=1)
        assert result["observations"] == 1
        assert result["scores"] == 1  # would be 0 if parent-first w/ CASCADE
        assert db_session.get(VerificationObservationRow, feb_id) is None

    def test_unsummarised_month_left_alone(self, db_session):
        """Obs from a month that's never been rolled up stays put even when
        the cutoff has passed — protects against a broken rollup loop."""
        from weatherbrief.db.models import (
            AirportMonthlySummaryRow,
            VerificationObservationRow,
        )

        # Mar 2026 is summarised; Feb 2026 is NOT summarised.
        feb_obs = VerificationObservationRow(
            icao="LFPG",
            observation_time=_utc(2026, 2, 15, 12, 0),
            collected_at=_utc(2026, 2, 15, 12, 0),
        )
        mar_obs = VerificationObservationRow(
            icao="LFPG",
            observation_time=_utc(2026, 3, 15, 12, 0),
            collected_at=_utc(2026, 3, 15, 12, 0),
        )
        db_session.add_all([feb_obs, mar_obs])
        db_session.add(AirportMonthlySummaryRow(
            month=_utc(2026, 3, 1), icao="LFPG", n_obs=1,
        ))
        db_session.flush()
        feb_id, mar_id = feb_obs.id, mar_obs.id  # capture before prune

        result = prune_raw_observations(db_session, retain_days=1)
        # Only March (the summarised month past cutoff) gets pruned
        assert result["observations"] == 1
        assert db_session.get(VerificationObservationRow, feb_id) is not None
        assert db_session.get(VerificationObservationRow, mar_id) is None
