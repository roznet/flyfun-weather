"""Smoke tests for the airport profile SSE helpers.

The SSE endpoint itself depends on Open-Meteo and the airports DB so it's
not easy to exercise in unit tests; what we can pin down here is:

  1. Purely-functional helpers (hour-window construction, time-key matching).
  2. The JSON-encoder discipline that protects the SSE stream from
     silently dying on a non-JSON-trivial field — a class of bug the
     briefing pipeline shipped once (PR #107: ``Diagnostic.error_id: UUID``
     killed every refresh stream). See ``test_api.py::TestRefreshStreamEncoder``
     for the canonical version of this guard.
  3. The ``_grib_enrich_levels`` return-shape contract (string-vs-dict
     regression guard from the PR review of #122).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from weatherbrief.api.airport_profile import _build_hours, _DEFAULT_WINDOW_H


def test_build_hours_default_window():
    start = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    hours = _build_hours(start, _DEFAULT_WINDOW_H)
    assert len(hours) == _DEFAULT_WINDOW_H + 1
    assert hours[0] == start
    assert hours[-1].hour == start.hour + _DEFAULT_WINDOW_H
    # All hours are aware UTC.
    assert all(h.tzinfo is not None for h in hours)


def test_build_hours_zero_window_is_single_point():
    start = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    hours = _build_hours(start, 0)
    assert hours == [start]


def test_build_hours_crosses_midnight():
    start = datetime(2026, 5, 7, 22, 0, tzinfo=timezone.utc)
    hours = _build_hours(start, 3)  # 22, 23, 00, 01
    assert [h.hour for h in hours] == [22, 23, 0, 1]
    assert hours[0].day == 7
    assert hours[-1].day == 8


# ---------------------------------------------------------------------------
# JSON-encoder discipline (mirrors test_api.py::TestRefreshStreamEncoder).
#
# The /api/maps/airport-profile SSE generator builds events as raw dicts
# and serialises with ``json_mod.dumps(payload, default=str)``. The
# ``derived`` event embeds a ``SoundingAnalysis.model_dump(mode="json")``
# payload, which is a much larger Pydantic structure than the briefing's
# pack response — every datetime, UUID, Enum, or other non-JSON-trivial
# field added to ``SoundingAnalysis`` (or any of its nested models) flows
# through this encoder.
#
# Two layers of defense, each tested below:
#   1. The route uses ``model_dump(mode="json")`` so Pydantic stringifies
#      UUID/datetime/Enum on its way out.
#   2. The route uses ``json.dumps(..., default=str)`` as a backstop in
#      case mode="json" misses something (or someone adds a fresh
#      json_mod.dumps call without it — see the structural lint test).
# ---------------------------------------------------------------------------


class TestAirportProfileEncoder:
    """The same class of regression that killed /refresh/stream once."""

    def test_derived_event_round_trips_through_sse_encoder(self):
        """Build a real SoundingAnalysis (the heaviest payload the
        airport-profile stream emits), run it through the exact
        two-step encode the route does, and decode back. Catches any
        future field on SoundingAnalysis (or its nested models) that
        plain ``json.dumps`` can't handle.
        """
        from weatherbrief.models.analysis import (
            ConvectiveAssessment, IcingRisk, SoundingAnalysis, ThermodynamicIndices,
        )

        # Construct a SoundingAnalysis with the kind of fields most likely
        # to surface a non-trivial encoder issue: enums (IcingRisk),
        # nested Pydantic models, datetimes if/when added.
        sa = SoundingAnalysis(
            indices=ThermodynamicIndices(
                lcl_pressure_hpa=950.0,
                lcl_altitude_ft=2000.0,
                cape_surface_jkg=350.0,
                cin_surface_jkg=-25.0,
                lifted_index=2.5,
                freezing_level_ft=10000.0,
            ),
            convective=ConvectiveAssessment(risk_level=IcingRisk.NONE),
        )

        # Step 1: same model_dump call the route makes.
        payload = {
            "type": "derived",
            "points": [{
                "point_index": 0,
                "time": "2026-05-07T12:00:00+00:00",
                "sounding": sa.model_dump(mode="json"),
            }],
        }
        # Step 2: same json.dumps call the route makes (default=str backstop).
        encoded = json.dumps(payload, default=str)
        parsed = json.loads(encoded)

        assert parsed["type"] == "derived"
        assert len(parsed["points"]) == 1
        # Key indices fields survived through to the JSON payload.
        survived = parsed["points"][0]["sounding"]
        assert survived["indices"]["cape_surface_jkg"] == 350.0
        # Enum was stringified, not left as a Python repr.
        assert survived["convective"]["risk_level"] == IcingRisk.NONE.value

    def test_meta_event_is_json_safe(self):
        """The meta event contains the only datetime-derived strings the
        endpoint emits (start_hour, hours[i]). They go through
        ``_iso_utc()`` so they're already strings — but lock that in so
        a future change that emits a raw datetime breaks here, not at
        runtime."""
        from weatherbrief.api.airport_profile import _build_hours, _iso_utc

        start = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
        hours = _build_hours(start, _DEFAULT_WINDOW_H)
        meta = {
            "type": "meta",
            "icao": "EGLL",
            "lat": 51.4775,
            "lon": -0.4614,
            "elevation_ft": 83.0,
            "model": "ecmwf",
            "start_hour": _iso_utc(start),
            "window_h": _DEFAULT_WINDOW_H,
            "hours": [_iso_utc(h) for h in hours],
        }
        encoded = json.dumps(meta, default=str)
        parsed = json.loads(encoded)
        # All time strings carry the +00:00 suffix — same as derived/surface
        # so the client's strict === match works across phases.
        assert parsed["start_hour"].endswith("+00:00")
        assert all(h.endswith("+00:00") for h in parsed["hours"])

    def test_airport_profile_module_sse_encoders_are_json_safe(self):
        """Structural lint, mirrored from test_api.py::TestRefreshStreamEncoder.

        Every ``json_mod.dumps(...)`` call in ``airport_profile.py`` must
        include ``default=str`` on the same line. This is the only test in
        this class that catches a *route-level* regression — the encoder
        round-trip tests above only lock in encoder behavior, but a future
        patch removing ``default=str`` from a new SSE event would silently
        revert the production guard.
        """
        src = (
            Path(__file__).parent.parent
            / "src" / "weatherbrief" / "api" / "airport_profile.py"
        ).read_text()

        bad_dumps = [
            (i, line.strip())
            for i, line in enumerate(src.splitlines(), 1)
            if "json_mod.dumps(" in line and "default=str" not in line
        ]
        assert not bad_dumps, (
            "Found json_mod.dumps() without default=str in airport_profile.py — "
            "the SSE stream will silently die on any non-JSON-trivial "
            "field (UUID, datetime, Path, Decimal, …). Add default=str.\n"
            + "\n".join(f"  L{i}: {line}" for i, line in bad_dumps)
        )

    def test_airport_profile_model_dump_uses_json_mode(self):
        """The ``derived`` payload embeds ``sa.model_dump(mode="json")``.
        Without ``mode="json"``, UUID/datetime/Enum stay as Python objects
        and die in the json.dumps downstream — the exact bug PR #107
        shipped on /refresh/stream.
        """
        import re

        src = (
            Path(__file__).parent.parent
            / "src" / "weatherbrief" / "api" / "airport_profile.py"
        ).read_text()

        # Look for any .model_dump(...) call inside this file. The lint
        # is intentionally broad — every payload that goes into a SSE
        # event should be json-mode-dumped.
        pattern = re.compile(r"\.model_dump\(([^)]*)\)")
        for match in pattern.finditer(src):
            args = match.group(1)
            line_no = src[: match.start()].count("\n") + 1
            assert 'mode="json"' in args or "mode='json'" in args, (
                f"airport_profile.py:{line_no} — .model_dump() must use "
                f"mode='json' (got args: {args!r}). Without it, UUID/"
                f"datetime/Enum fields stay as Python objects and die in "
                f"the json_mod.dumps() call downstream."
            )


# ---------------------------------------------------------------------------
# Contract: _grib_enrich_levels.skipped is always Dict[str, str].
#
# The TS adapter declares ``skipped: Record<string, string>``. The Python
# function had four return paths — three returned dicts, one returned the
# bare string ``"exception"`` (caught in PR review). Lock it in.
# ---------------------------------------------------------------------------


class TestGribEnrichSkippedShape:
    def test_no_levels_returns_dict_skipped(self):
        from weatherbrief.api.airport_profile import _grib_enrich_levels

        result = _grib_enrich_levels(
            None, 51.4775, -0.4614, "ecmwf", [], Path("/tmp"),
        )
        assert isinstance(result["skipped"], dict)
        assert result["skipped"] == {"all": "no_levels"}
        assert result["sources"] == {}

    def test_no_grib_dir_returns_dict_skipped(self, tmp_path, monkeypatch):
        """Force the preflight to skip by pointing ECMWF_GRIB_DIR at an
        empty (or nonexistent) directory."""
        from weatherbrief.api.airport_profile import _grib_enrich_levels
        from weatherbrief.fetch.grib import ecmwf_fetch as ecmwf_mod

        empty = tmp_path / "ecmwf_empty_dir"  # never created
        monkeypatch.setattr(ecmwf_mod, "ecmwf_grib_dir", lambda: empty)

        # Construct a minimal fake WaypointForecast — only used as a
        # truthy-ness check before the preflight.
        class _FakeWf:
            hourly = []

        hours = [datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)]
        result = _grib_enrich_levels(
            _FakeWf(), 51.4775, -0.4614, "ecmwf", hours, tmp_path,
        )
        assert isinstance(result["skipped"], dict)
        assert result["skipped"] == {"all": "no_local_grib_configured"}


# ---------------------------------------------------------------------------
# Concurrency: per-user / global stream limiter (PR #122 design discussion).
#
# Bursty right-clicks on the forecast map could otherwise saturate the thread
# pool. The limiter is light (just two int counters) — no queue, no progress
# polling, no flight_id dedup like packs._RefreshRegistry. Tests below pin
# the cap behavior + acquire/release symmetry.
# ---------------------------------------------------------------------------


class TestStreamLimiter:
    def test_acquire_release_cycle(self):
        from weatherbrief.api.airport_profile import _StreamLimiter

        limiter = _StreamLimiter(max_per_user=3, max_global=10)
        assert limiter.snapshot()["global"] == 0

        limiter.acquire("alice")
        limiter.acquire("alice")
        assert limiter.snapshot() == {"global": 2, "users": 1}

        limiter.release("alice")
        limiter.release("alice")
        assert limiter.snapshot() == {"global": 0, "users": 0}

    def test_per_user_cap_returns_429(self):
        from fastapi import HTTPException

        from weatherbrief.api.airport_profile import _StreamLimiter

        limiter = _StreamLimiter(max_per_user=2, max_global=10)
        limiter.acquire("alice")
        limiter.acquire("alice")

        with pytest.raises(HTTPException) as exc_info:
            limiter.acquire("alice")
        assert exc_info.value.status_code == 429
        assert "limit 2" in str(exc_info.value.detail)

        # Other users are unaffected by alice's cap.
        limiter.acquire("bob")
        assert limiter.snapshot()["global"] == 3

    def test_global_cap_returns_429(self):
        from fastapi import HTTPException

        from weatherbrief.api.airport_profile import _StreamLimiter

        limiter = _StreamLimiter(max_per_user=10, max_global=2)
        limiter.acquire("alice")
        limiter.acquire("bob")

        with pytest.raises(HTTPException) as exc_info:
            limiter.acquire("carol")
        assert exc_info.value.status_code == 429
        assert "Server busy" in exc_info.value.detail

    def test_release_below_zero_is_safe(self):
        """Defensive: a doubled release shouldn't underflow into negative
        counts. Could happen if the synchronous release-on-construction-
        error path collides with the generator's finally — both should
        be tolerated."""
        from weatherbrief.api.airport_profile import _StreamLimiter

        limiter = _StreamLimiter(max_per_user=2, max_global=5)
        limiter.acquire("alice")
        limiter.release("alice")
        limiter.release("alice")  # extra release
        assert limiter.snapshot() == {"global": 0, "users": 0}


class TestGribRunDedup:
    """Two simultaneous panels for the same (model, run) should serialise
    their enrichment phase. Locks for different keys must NOT serialise."""

    def test_same_key_returns_same_lock(self):
        from weatherbrief.api.airport_profile import _GribRunDedup

        dedup = _GribRunDedup()
        a = dedup.lock_for("ecmwf", 1717000000)
        b = dedup.lock_for("ecmwf", 1717000000)
        assert a is b

    def test_different_keys_return_different_locks(self):
        from weatherbrief.api.airport_profile import _GribRunDedup

        dedup = _GribRunDedup()
        a = dedup.lock_for("ecmwf", 1717000000)
        b = dedup.lock_for("ecmwf", 1717003600)  # different run
        c = dedup.lock_for("gfs", 1717000000)    # different model
        assert a is not b
        assert a is not c
        assert b is not c

    def test_none_init_uses_sentinel_key(self):
        """Before the GRIB run resolves we don't know its init time. The
        dedup pool buckets all None-init requests under one sentinel so a
        burst still serialises rather than racing the cold cache."""
        from weatherbrief.api.airport_profile import _GribRunDedup

        dedup = _GribRunDedup()
        a = dedup.lock_for("ecmwf", None)
        b = dedup.lock_for("ecmwf", None)
        assert a is b

    @pytest.mark.asyncio
    async def test_lock_serialises_concurrent_acquirers(self):
        """Mechanical: two coroutines acquiring the same lock interleave
        their critical sections in order, not in parallel."""
        from weatherbrief.api.airport_profile import _GribRunDedup

        dedup = _GribRunDedup()
        lock = dedup.lock_for("ecmwf", 1717000000)
        order: list[str] = []

        async def worker(name: str, hold_s: float) -> None:
            async with lock:
                order.append(f"{name}:enter")
                await asyncio.sleep(hold_s)
                order.append(f"{name}:exit")

        await asyncio.gather(worker("a", 0.01), worker("b", 0.01))

        # Either a→a→b→b or b→b→a→a (no interleave like a→b→a→b).
        assert order in (
            ["a:enter", "a:exit", "b:enter", "b:exit"],
            ["b:enter", "b:exit", "a:enter", "a:exit"],
        )


class TestSurfaceCeilingDatum:
    """``_surface_from_cache`` emits ceilings on the AGL datum. (#441 #3)

    The chart plots this series against AGL METAR ceilings, and every other
    ceiling surface (map, alternates, advisories) already converts — this was
    the last datum-naive read.
    """

    @staticmethod
    def _insert(db_session, **overrides):
        from weatherbrief.db.models import AirportForecastSnapshotRow

        hour = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
        defaults = dict(
            icao="LSGS", region="eu", model="gfs",
            model_init_time=datetime(2026, 5, 7, 6, 0, tzinfo=timezone.utc),
            forecast_hour=hour,
            fetched_at=hour,
            nwp_ceiling_ft=4000.0,
            sounding_ceiling_ft=5000.0,
        )
        defaults.update(overrides)
        db_session.add(AirportForecastSnapshotRow(**defaults))
        db_session.flush()
        return hour

    def test_msl_model_ceiling_converted_to_agl(self, db_session):
        from weatherbrief.api.airport_profile import _surface_from_cache

        hour = self._insert(db_session, model="gfs")
        rows = _surface_from_cache(
            db_session, "LSGS", "gfs", [hour], field_elevation_ft=1500.0,
        )
        assert rows[0]["ceiling_ft"] == pytest.approx(2500.0)  # 4000 MSL - 1500

    def test_ecmwf_ceiling_already_agl_passes_through(self, db_session):
        from weatherbrief.api.airport_profile import _surface_from_cache

        hour = self._insert(db_session, model="ecmwf")
        rows = _surface_from_cache(
            db_session, "LSGS", "ecmwf", [hour], field_elevation_ft=1500.0,
        )
        assert rows[0]["ceiling_ft"] == pytest.approx(4000.0)

    def test_sounding_fallback_is_always_msl(self, db_session):
        """The sounding ceiling is geopotential-height MSL for every model —
        including ECMWF, whose *NWP* ceiling is AGL."""
        from weatherbrief.api.airport_profile import _surface_from_cache

        hour = self._insert(db_session, model="ecmwf", nwp_ceiling_ft=None)
        rows = _surface_from_cache(
            db_session, "LSGS", "ecmwf", [hour], field_elevation_ft=1500.0,
        )
        assert rows[0]["ceiling_ft"] == pytest.approx(3500.0)  # 5000 MSL - 1500

    def test_zero_nwp_ceiling_does_not_fall_through_to_sounding(self, db_session):
        """A literal 0 ft ceiling (ground-level fog) is a real value, not a
        missing one — the None-check must survive the AGL conversion."""
        from weatherbrief.api.airport_profile import _surface_from_cache

        hour = self._insert(db_session, model="gfs", nwp_ceiling_ft=0.0)
        rows = _surface_from_cache(
            db_session, "LSGS", "gfs", [hour], field_elevation_ft=1500.0,
        )
        # 0 MSL clamps to 0 AGL; it must NOT become 5000-1500 from the sounding.
        assert rows[0]["ceiling_ft"] == pytest.approx(0.0)

    def test_without_elevation_stays_datum_naive(self, db_session):
        from weatherbrief.api.airport_profile import _surface_from_cache

        hour = self._insert(db_session, model="gfs")
        rows = _surface_from_cache(db_session, "LSGS", "gfs", [hour])
        assert rows[0]["ceiling_ft"] == pytest.approx(4000.0)
