"""The refresh gate has exactly one entry point (issue #558).

``decide_refresh`` answers a question purely about *model runs*; it is blind to
the flight's own parameters having changed. ``apply_params_change_override``
supplies that half, and for a while it was opt-in at six separate call sites —
three of which only became correct via follow-up commits after the fact. A
missed site fails silently: the pilot is shown a briefing computed for their
previous departure time, labelled up to date, with nothing raising an error.

``gated_data_status`` is now the single boundary, and the guard test below is
what stops a seventh caller from reintroducing the gap.
"""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"

# Callable directly only from the module that owns the boundary.
GUARDED = {"decide_refresh", "apply_params_change_override"}
BOUNDARY_MODULE = SRC_ROOT / "weatherbrief" / "api" / "packs.py"


def _called_names(tree):
    """Every function name invoked in *tree* (bare and attribute calls alike)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            yield func.id, node.lineno
        elif isinstance(func, ast.Attribute):
            yield func.attr, node.lineno


class TestGateHasOneEntryPoint:
    def test_no_production_code_calls_the_gate_halves_directly(self):
        """Only ``api/packs.py`` may call the two halves of the gate.

        Everything else goes through ``gated_data_status``, which applies both.
        Calling ``decide_refresh`` alone compiles, reads as complete and passes
        its own tests — the bug only appears when a pilot edits a flight and
        that one surface reports stale data as current. AST-based so comments
        and docstrings mentioning the names don't trip it.
        """
        offenders = []
        for path in SRC_ROOT.rglob("*.py"):
            if path == BOUNDARY_MODULE:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for name, lineno in _called_names(tree):
                if name in GUARDED:
                    rel = path.relative_to(SRC_ROOT)
                    offenders.append(f"{rel}:{lineno} calls {name}()")

        assert not offenders, (
            "The refresh gate must be reached through "
            "weatherbrief.api.packs.gated_data_status(), which applies the "
            "params-change override (#552/#558). Calling these directly "
            "reintroduces the silent stale-briefing bug:\n  "
            + "\n  ".join(offenders)
        )

    def test_boundary_is_exported_where_the_callers_import_it_from(self):
        from weatherbrief.api.packs import gated_data_status

        assert callable(gated_data_status)


def _ms(state, covers=True, next_expected="2026-05-20T12:00:00+00:00"):
    from weatherbrief.api.packs import ModelStatus

    return ModelStatus(
        source="m:openmeteo", pack_init=1, latest_available=2,
        next_expected=next_expected, state=state, covers_horizon=covers,
    )


def _status(*models):
    """DataStatus from (state, covers) tuples — mirrors test_packs.py's helper."""
    from weatherbrief.api.packs import DataStatus

    out = {f"m{i}": _ms(s, c) for i, (s, c) in enumerate(models)}
    stale = [m for m, ms in out.items() if ms.state == "stale"]
    return DataStatus(fresh=not stale, stale_models=stale, models=out)


DEPARTURE = datetime(2026, 5, 25, 9, 0, tzinfo=timezone.utc)


def _flight():
    from weatherbrief.models import Flight

    return Flight(
        id="f1", user_id="u", route_name="EGTF-LFAT",
        waypoints=["EGTF", "LFAT"],
        departure_time=DEPARTURE,
        cruise_altitude_ft=8000, flight_duration_hours=2.0,
        created_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )


def _pack(flight, params_hash):
    from weatherbrief.models import BriefingPackMeta

    return BriefingPackMeta(
        flight_id=flight.id,
        fetch_timestamp=datetime(2026, 5, 20, 20, 0, tzinfo=timezone.utc),
        days_out=5,
        model_init_times={}, grib_init_times={}, model_sources={},
        flight_params_hash=params_hash,
    )


@pytest.fixture
def quiet_models():
    """No covering model has moved — the bare gate answers "none" at D-5."""
    with patch(
        "weatherbrief.api.packs._build_data_status",
        return_value=_status(("current", True), ("current", True), ("current", True)),
    ) as m:
        yield m


class TestGatedDataStatus:
    """The behaviour the six call sites now inherit instead of re-deriving."""

    def test_params_change_forces_full_when_no_model_moved(self, quiet_models):
        """The #552 scenario: pilot edits departure, no new run exists.

        Bare gate says "none" and the caller would hand back the pre-edit pack
        as complete. The boundary must upgrade it to "full".
        """
        from weatherbrief.api.packs import gated_data_status

        flight = _flight()
        status = gated_data_status(
            _pack(flight, "stamped-for-the-old-departure"),
            flight,
            now=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),  # D-5
        )

        assert status.refresh_decision.mode == "full"
        assert "parameters changed" in status.refresh_decision.reason
        # A "full" decision that still lists a model wait is self-contradictory.
        assert status.refresh_decision.pending_models == []
        assert status.refresh_decision.eta_useful is None

    def test_unchanged_params_leave_the_model_decision_alone(self, quiet_models):
        """No spurious full refresh when the flight hasn't been touched."""
        from weatherbrief.api.packs import gated_data_status
        from weatherbrief.storage.flights import compute_flight_params_hash

        flight = _flight()
        status = gated_data_status(
            _pack(flight, compute_flight_params_hash(flight)),
            flight,
            now=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        )

        assert status.refresh_decision.mode == "none"

    def test_legacy_pack_without_a_stamp_is_left_alone(self, quiet_models):
        """Pre-migration-088 packs must not each force one full refresh."""
        from weatherbrief.api.packs import gated_data_status

        flight = _flight()
        status = gated_data_status(
            _pack(flight, None), flight,
            now=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
        )

        assert status.refresh_decision.mode == "none"

    def test_params_override_false_is_the_schedulers_deliberate_opt_out(
        self, quiet_models
    ):
        """The routine scheduler cycle skips the override on purpose.

        An edit there is already followed by a client-driven refresh, so forcing
        a full run per edited flight would only buy a second one.
        """
        from weatherbrief.api.packs import gated_data_status

        flight = _flight()
        status = gated_data_status(
            _pack(flight, "stamped-for-the-old-departure"),
            flight,
            now=datetime(2026, 5, 20, 8, 0, tzinfo=timezone.utc),
            params_override=False,
        )

        assert status.refresh_decision.mode == "none"

    def test_now_is_threaded_into_the_lead_time(self, quiet_models):
        """The resume path passes its own clock; it must reach ``_days_out_now``.

        Same flight and same (quiet) models: at D-5 the gate is a no-op, on the
        day of departure it still owes the pilot fresh METAR/TAF.
        """
        from weatherbrief.api.packs import gated_data_status

        flight = _flight()
        pack = _pack(flight, None)  # unstamped, so only the clock is in play

        far = gated_data_status(
            pack, flight, now=DEPARTURE - timedelta(days=5),
        ).refresh_decision
        day_of = gated_data_status(
            pack, flight, now=DEPARTURE - timedelta(hours=3),
        ).refresh_decision

        assert far.days_out == 5
        assert far.mode == "none"
        assert day_of.days_out == 0
        assert day_of.mode == "realtime"

    def test_decision_is_attached_to_the_returned_status(self, quiet_models):
        """Callers read ``.refresh_decision``; it must never come back None."""
        from weatherbrief.api.packs import gated_data_status

        flight = _flight()
        status = gated_data_status(_pack(flight, None), flight)

        assert status.refresh_decision is not None
        assert status.models  # the per-model detail survives the wrapping


class TestAgentFreshnessDict:
    """``api/agent.py::_freshness_dict`` — the connector's copy of ``/packs/freshness``.

    Item 3 of #558: this helper mirrors two endpoints that are covered, but had
    no test of its own, so it could drift back off the boundary silently. The
    AST guard above only proves it doesn't call the *halves* directly — nothing
    proved it calls ``gated_data_status`` at all, and a helper that returned a
    bare ``decide_refresh`` status would still pass it.
    """

    @staticmethod
    def _flight():
        """A flight departing in the future.

        ``_freshness_dict`` takes no ``now`` — it is a live HTTP path — so the
        module-level ``_flight()`` (a fixed 2026-05-25 departure) would read as
        day-of and answer "realtime" once that date passes.
        """
        from weatherbrief.models import Flight

        return Flight(
            id="egtf_lfat-future", user_id="user-1", route_name="egtf_lfat",
            waypoints=["EGTF", "LFAT"],
            departure_time=datetime.now(timezone.utc) + timedelta(days=5),
            cruise_altitude_ft=8000, flight_duration_hours=2.0,
            created_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        )

    def _patched_agent(self, flight, packs):
        """Pin the two lookups ``_freshness_dict`` does before the gate."""
        return (
            patch("weatherbrief.api.agent.flights_api._load_flight_or_404",
                  return_value=flight),
            patch("weatherbrief.api.agent.packs_api.list_packs", return_value=packs),
        )

    def test_an_edited_flight_reads_as_needing_a_full_refresh(self, quiet_models):
        """The #552 scenario, seen through the connector.

        No covering model has moved, so the bare gate says "none" and the agent
        would tell the pilot their briefing is current — for a departure time
        they have since changed.
        """
        from weatherbrief.api.agent import _freshness_dict

        flight = self._flight()
        pack = _pack(flight, "stamped-for-the-old-departure")
        load, packs = self._patched_agent(flight, [pack])
        with load, packs:
            result = _freshness_dict(object(), "user-1", flight.id)

        assert result["refresh_decision"]["mode"] == "full"
        assert "parameters changed" in result["refresh_decision"]["reason"]

    def test_an_untouched_flight_is_left_alone(self, quiet_models):
        from weatherbrief.api.agent import _freshness_dict
        from weatherbrief.storage.flights import compute_flight_params_hash

        flight = self._flight()
        pack = _pack(flight, compute_flight_params_hash(flight))
        load, packs = self._patched_agent(flight, [pack])
        with load, packs:
            result = _freshness_dict(object(), "user-1", flight.id)

        assert result["fresh"] is True
        assert result["refresh_decision"]["mode"] == "none"

    def test_a_flight_with_no_packs_is_not_fresh(self, quiet_models):
        """Nothing to grade against — the connector must not claim "ready"."""
        from weatherbrief.api.agent import _freshness_dict

        flight = self._flight()
        load, packs = self._patched_agent(flight, [])
        with load, packs:
            result = _freshness_dict(object(), "user-1", flight.id)

        assert result == {"fresh": False}

    def test_a_throwing_gate_degrades_to_fresh_rather_than_500(self, quiet_models):
        """The documented fallback: the briefing still renders.

        ``briefing_freshness_status`` reads ``fresh`` → "ready", which is the
        deliberate choice — an unexpected gate failure must not take the whole
        agent briefing down with it.
        """
        from weatherbrief.api.agent import _freshness_dict

        flight = self._flight()
        load, packs = self._patched_agent(flight, [_pack(flight, None)])
        with load, packs, patch(
            "weatherbrief.api.packs.gated_data_status",
            side_effect=RuntimeError("gate exploded"),
        ):
            result = _freshness_dict(object(), "user-1", flight.id)

        assert result == {"fresh": True}

    def test_an_ownership_error_still_propagates(self, quiet_models):
        """Only the *gate* is caught. A 403/404 must reach the caller."""
        from fastapi import HTTPException

        from weatherbrief.api.agent import _freshness_dict

        with patch("weatherbrief.api.agent.flights_api._load_flight_or_404",
                   side_effect=HTTPException(status_code=404, detail="nope")):
            with pytest.raises(HTTPException) as exc:
                _freshness_dict(object(), "user-1", "flt-1")
        assert exc.value.status_code == 404
