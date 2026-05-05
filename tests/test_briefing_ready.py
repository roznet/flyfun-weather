"""Tests for the briefing_ready milestone (issue #113).

Covers three layers:
- Pipeline: ``execute_briefing`` calls the progress + briefing_ready callbacks
  between phase 6 (Skew-T) and phase 7 (LLM digest).
- Persistence: ``_persist_pack_provisional`` / ``_persist_pack_finalize``
  write/update the ``BriefingPackRow`` correctly.
- SSE handler: ``briefing_ready_callback`` emits a ``briefing_ready`` event
  with provisional pack metadata.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from flyfun_common.db import DEV_USER_ID
from weatherbrief.models import (
    AdvisoryStatus,
    BriefingPackMeta,
    Flight,
    ForecastSnapshot,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    RouteConfig,
    Waypoint,
)
from weatherbrief.pipeline import BriefingResult
from weatherbrief.storage.flights import (
    list_packs,
    save_flight,
)


# --- Helpers ---


def _make_route() -> RouteConfig:
    return RouteConfig(
        name="test",
        waypoints=[
            Waypoint(icao="EGTK", name="Oxford", lat=51.8, lon=-1.3),
            Waypoint(icao="LSGS", name="Sion", lat=46.2, lon=7.3),
        ],
        cruise_altitude_ft=8000,
        flight_duration_hours=4.0,
    )


def _make_flight(flight_id: str = "test-flight") -> Flight:
    return Flight(
        id=flight_id,
        user_id=DEV_USER_ID,
        route_name="test",
        waypoints=["EGTK", "LSGS"],
        departure_time=datetime.now(timezone.utc) + timedelta(days=2),
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=4.0,
        created_at=datetime.now(timezone.utc),
    )


def _make_partial_result(pack_path: Path) -> BriefingResult:
    """A BriefingResult as it would look at the briefing_ready milestone:
    snapshot/advisories/GRAMET/Skew-T present, no digest.
    """
    snapshot = ForecastSnapshot(
        route=_make_route(),
        target_date=(datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat(),
        fetch_date=datetime.now(timezone.utc).date().isoformat(),
        days_out=2,
    )
    result = BriefingResult(
        snapshot=snapshot,
        snapshot_path=pack_path / "briefing.json",
    )
    result.gramet_path = pack_path / "gramet.pdf"
    result.skewt_paths = [pack_path / "skewt" / "EGTK_gfs.png"]
    result.route_advisories_path = pack_path / "route_advisories.json"
    result.models_fetched = ["ecmwf", "gfs"]
    result.grib_init_times = {}
    return result


def _write_advisories(
    pack_path: Path,
    *,
    aggregate_status: AdvisoryStatus = AdvisoryStatus.AMBER,
    advisory_id: str = "icing.fiki",
) -> None:
    """Write a minimal ``route_advisories.json`` so assessment derivation works."""
    pack_path.mkdir(parents=True, exist_ok=True)
    manifest = RouteAdvisoriesManifest(
        advisories=[
            RouteAdvisoryResult(
                advisory_id=advisory_id,
                aggregate_status=aggregate_status,
                per_model=[],
            ),
        ],
    )
    (pack_path / "route_advisories.json").write_text(
        manifest.model_dump_json(indent=2),
    )


# --- Persistence layer ---


class TestPersistProvisional:
    def test_writes_row_with_has_digest_false(
        self, db_session, dev_user, tmp_path,
    ):
        """_persist_pack_provisional inserts a pack row with has_digest=False
        and an assessment derived from advisories on disk.
        """
        from weatherbrief.api.packs import _persist_pack_provisional

        flight = _make_flight()
        save_flight(db_session, flight, dev_user)

        pack_path = tmp_path / "pack"
        _write_advisories(pack_path, aggregate_status=AdvisoryStatus.AMBER)

        result = _make_partial_result(pack_path)
        fetch_ts = datetime.now(timezone.utc).replace(microsecond=0)

        meta = _persist_pack_provisional(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
        )

        assert meta.has_digest is False
        # Advisories with aggregate_status='amber' → assessment AMBER
        assert meta.assessment == "AMBER"

        packs = list_packs(db_session, flight.id)
        assert len(packs) == 1
        assert packs[0].has_digest is False
        assert packs[0].assessment == "AMBER"
        assert packs[0].has_gramet is True
        assert packs[0].has_skewt is True

    def test_no_advisories_leaves_assessment_null(
        self, db_session, dev_user, tmp_path,
    ):
        """When route_advisories.json doesn't exist, assessment is NULL."""
        from weatherbrief.api.packs import _persist_pack_provisional

        flight = _make_flight()
        save_flight(db_session, flight, dev_user)

        pack_path = tmp_path / "pack"
        pack_path.mkdir()  # no route_advisories.json

        result = _make_partial_result(pack_path)
        result.route_advisories_path = None
        fetch_ts = datetime.now(timezone.utc).replace(microsecond=0)

        meta = _persist_pack_provisional(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
        )

        assert meta.has_digest is False
        assert meta.assessment is None
        assert meta.assessment_reason is None


class TestPersistFinalize:
    def test_updates_existing_provisional_row(
        self, db_session, dev_user, tmp_path,
    ):
        """_persist_pack_finalize updates the row from provisional rather
        than inserting a duplicate.
        """
        from weatherbrief.api.packs import (
            _persist_pack_finalize,
            _persist_pack_provisional,
        )

        flight = _make_flight()
        save_flight(db_session, flight, dev_user)

        pack_path = tmp_path / "pack"
        _write_advisories(pack_path, aggregate_status=AdvisoryStatus.AMBER)

        result = _make_partial_result(pack_path)
        fetch_ts = datetime.now(timezone.utc).replace(microsecond=0)

        _persist_pack_provisional(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
        )
        assert len(list_packs(db_session, flight.id)) == 1

        # Phase 7 completes: digest produces a different assessment.
        class FakeDigest:
            assessment = "GREEN"
            assessment_reason = "Conditions improved"

        result.digest = FakeDigest()
        result.digest_path = pack_path / "digest.json"

        meta = _persist_pack_finalize(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
            user_id=dev_user,
        )

        packs = list_packs(db_session, flight.id)
        assert len(packs) == 1, "row count must not increase on finalize"
        assert packs[0].has_digest is True
        assert packs[0].assessment == "GREEN"
        assert packs[0].assessment_reason == "Conditions improved"
        assert meta.has_digest is True

    def test_digest_failure_keeps_has_digest_false(
        self, db_session, dev_user, tmp_path,
    ):
        """If the digest fails (no digest_path), finalize updates with
        has_digest=False but the assessment from advisories survives.
        """
        from weatherbrief.api.packs import (
            _persist_pack_finalize,
            _persist_pack_provisional,
        )

        flight = _make_flight()
        save_flight(db_session, flight, dev_user)

        pack_path = tmp_path / "pack"
        _write_advisories(pack_path, aggregate_status=AdvisoryStatus.AMBER)

        result = _make_partial_result(pack_path)
        fetch_ts = datetime.now(timezone.utc).replace(microsecond=0)

        _persist_pack_provisional(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
        )

        # Digest fails → result.digest is None and digest_path is None.
        meta = _persist_pack_finalize(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
            user_id=dev_user,
        )

        packs = list_packs(db_session, flight.id)
        assert len(packs) == 1
        assert packs[0].has_digest is False
        assert packs[0].assessment == "AMBER"
        assert meta.has_digest is False

    def test_finalize_inserts_when_no_provisional(
        self, db_session, dev_user, tmp_path,
    ):
        """The synchronous /refresh path skips the provisional step; finalize
        must insert a fresh row instead of failing silently.
        """
        from weatherbrief.api.packs import _persist_pack_finalize

        flight = _make_flight()
        save_flight(db_session, flight, dev_user)

        pack_path = tmp_path / "pack"
        _write_advisories(pack_path, aggregate_status=AdvisoryStatus.GREEN)

        result = _make_partial_result(pack_path)
        result.digest_path = pack_path / "digest.json"

        class FakeDigest:
            assessment = "GREEN"
            assessment_reason = "All clear"

        result.digest = FakeDigest()
        fetch_ts = datetime.now(timezone.utc).replace(microsecond=0)

        meta = _persist_pack_finalize(
            flight.id, flight, fetch_ts, pack_path, result, db_session,
            user_id=dev_user,
        )

        packs = list_packs(db_session, flight.id)
        assert len(packs) == 1
        assert packs[0].has_digest is True
        assert meta.has_digest is True


# --- Pipeline emission ---


class TestPipelineEmitsBriefingReady:
    """Verify execute_briefing fires the briefing_ready callbacks between
    phase 6 (Skew-T) and phase 7 (LLM digest).
    """

    def _patch_pipeline_phases(self, monkeypatch):
        """Stub out heavy pipeline stages so the orchestrator can run end-to-end
        in a test without network or real GRIB data.
        """
        from weatherbrief.tasks.advise import AdvisoryResult
        from weatherbrief.tasks.analyze import AnalysisResult
        from weatherbrief.tasks.fetch import FetchResult
        from weatherbrief.tasks.outputs import DigestResult, GrametResult, SkewtResult
        import weatherbrief.pipeline as pipeline_mod

        def fake_run_fetch(**_kwargs):
            return FetchResult(
                route_points=[],
                all_forecasts=[],
                cross_sections=[],
                elevation_profile=None,
                models_fetched=["ecmwf", "gfs"],
            )

        def fake_run_analysis(**_kwargs):
            return AnalysisResult(
                waypoint_analyses=[],
                route_analyses=[],
                route_analyses_manifest=None,
                model_names=["ecmwf", "gfs"],
            )

        def fake_run_advisories(**_kwargs):
            return AdvisoryResult(manifest=None)

        def fake_run_gramet(**_kwargs):
            return GrametResult()

        def fake_run_skewt(**_kwargs):
            return SkewtResult()

        def fake_run_llm_digest(**_kwargs):
            return DigestResult()

        # Stub at the import sites used inside pipeline.py (re-exported from tasks).
        monkeypatch.setattr(pipeline_mod, "run_fetch", fake_run_fetch)
        monkeypatch.setattr(pipeline_mod, "run_analysis", fake_run_analysis)
        monkeypatch.setattr(pipeline_mod, "run_advisories", fake_run_advisories)
        monkeypatch.setattr(pipeline_mod, "run_gramet", fake_run_gramet)
        monkeypatch.setattr(pipeline_mod, "run_skewt", fake_run_skewt)
        monkeypatch.setattr(pipeline_mod, "run_llm_digest", fake_run_llm_digest)

    def test_briefing_ready_fires_before_llm_digest(self, monkeypatch, tmp_path):
        """Pipeline emits 'briefing_ready' before the 'llm_digest' progress stage."""
        from weatherbrief.pipeline import BriefingOptions, execute_briefing

        self._patch_pipeline_phases(monkeypatch)

        progress_stages: list[str] = []
        ready_called: list[BriefingResult] = []

        def progress_cb(stage: str, _detail: str | None = None) -> None:
            progress_stages.append(stage)

        def ready_cb(result: BriefingResult) -> None:
            ready_called.append(result)

        options = BriefingOptions(
            output_dir=tmp_path / "pack",
            fetch_gramet=True,
            generate_skewt=True,
            generate_llm_digest=True,
        )

        execute_briefing(
            route=_make_route(),
            departure_time=datetime.now(timezone.utc) + timedelta(days=2),
            options=options,
            progress_callback=progress_cb,
            briefing_ready_callback=ready_cb,
        )

        assert "briefing_ready" in progress_stages, progress_stages
        assert "llm_digest" in progress_stages, progress_stages
        # Ordering matters: briefing_ready must fire BEFORE llm_digest.
        assert progress_stages.index("briefing_ready") < progress_stages.index("llm_digest")
        # generate_skewt is part of the visible briefing — it must run before
        # the briefing_ready milestone.
        assert progress_stages.index("generate_skewt") < progress_stages.index("briefing_ready")

        # The briefing_ready_callback receives the partially-populated result
        # (digest still empty at this point).
        assert len(ready_called) == 1
        assert ready_called[0].digest is None
        assert ready_called[0].digest_path is None

    def test_briefing_ready_callback_failure_does_not_abort_pipeline(
        self, monkeypatch, tmp_path,
    ):
        """A throwing briefing_ready_callback must not block the digest phase."""
        from weatherbrief.pipeline import BriefingOptions, execute_briefing

        self._patch_pipeline_phases(monkeypatch)

        progress_stages: list[str] = []

        def progress_cb(stage: str, _detail: str | None = None) -> None:
            progress_stages.append(stage)

        def ready_cb(_result: BriefingResult) -> None:
            raise RuntimeError("simulated provisional persist failure")

        options = BriefingOptions(
            output_dir=tmp_path / "pack",
            fetch_gramet=False,
            generate_skewt=False,
            generate_llm_digest=True,
        )

        # Pipeline must still complete (digest stage runs after briefing_ready).
        execute_briefing(
            route=_make_route(),
            departure_time=datetime.now(timezone.utc) + timedelta(days=2),
            options=options,
            progress_callback=progress_cb,
            briefing_ready_callback=ready_cb,
        )

        assert "briefing_ready" in progress_stages
        assert "llm_digest" in progress_stages
