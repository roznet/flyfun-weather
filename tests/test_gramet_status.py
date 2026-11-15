"""Why a pack has no GRAMET, and fetching it after linking Autorouter (#994).

The apps hid the GRAMET section with no word when a pack lacked one, and a
Refresh right after linking Autorouter answered "already fresh" until a model
updated. The server now records the reason on the pack, reports it as
``gramet_status``, and the refresh gate runs once when a refresh can fix it.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.models import BriefingPackMeta, Flight


def _flight(user_id="owner", departure=None):
    return Flight(
        id="f-gramet", user_id=user_id, route_name="EGTF-LFAT",
        waypoints=["EGTF", "LFAT"],
        departure_time=departure or datetime.now(timezone.utc) + timedelta(days=3),
        cruise_altitude_ft=8000, flight_duration_hours=2.0,
        created_at=datetime.now(timezone.utc),
    )


def _pack(has_gramet=False, reason=None):
    return BriefingPackMeta(
        flight_id="f-gramet",
        fetch_timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        days_out=3,
        has_gramet=has_gramet,
        gramet_skip_reason=reason,
        model_init_times={}, grib_init_times={}, model_sources={},
    )


class TestGrametStatus:
    def test_pack_with_gramet_has_no_status(self):
        from weatherbrief.api.packs import _gramet_status

        assert _gramet_status(_pack(has_gramet=True), owner_can_fetch=True) is None

    @pytest.mark.parametrize("reason", [
        "not_linked", "disabled", "historical", "daily_limit", "failed", "expired",
    ])
    def test_stored_reason_is_reported_when_owner_state_unknown(self, reason):
        from weatherbrief.api.packs import _gramet_status

        assert _gramet_status(_pack(reason=reason)) == reason

    @pytest.mark.parametrize("reason", ["not_linked", "disabled"])
    def test_fixable_reason_becomes_refresh_to_fetch(self, reason):
        """The user's case: linked Autorouter after the pack was built."""
        from weatherbrief.api.packs import _gramet_status

        assert _gramet_status(_pack(reason=reason), owner_can_fetch=True) == "refresh_to_fetch"
        assert _gramet_status(_pack(reason=reason), owner_can_fetch=False) == reason

    @pytest.mark.parametrize("reason", ["failed", "daily_limit", "historical", "expired"])
    def test_other_reasons_are_not_refresh_to_fetch(self, reason):
        from weatherbrief.api.packs import _gramet_status

        assert _gramet_status(_pack(reason=reason), owner_can_fetch=True) == reason

    def test_legacy_pack_without_reason_stays_silent(self):
        """Pre-#994 packs (and the provisional pass) don't know why: no note."""
        from weatherbrief.api.packs import _gramet_status

        assert _gramet_status(_pack(reason=None), owner_can_fetch=True) is None

    def test_other_viewers_get_the_stored_reason_only(self):
        """A shared flight's viewer can't link the owner's Autorouter."""
        from weatherbrief.api.packs import _owner_can_fetch_for_viewer

        with patch("weatherbrief.api.packs._gramet_owner_can_fetch", return_value=True) as m:
            assert _owner_can_fetch_for_viewer(None, _flight("owner"), "someone-else") is None
            m.assert_not_called()
            assert _owner_can_fetch_for_viewer(None, _flight("owner"), "owner") is True


class TestOwnerCanFetch:
    def test_needs_link_and_profile_on(self):
        from weatherbrief.api.packs import _gramet_owner_can_fetch
        from weatherbrief.api.profiles import ProfileContext

        flight = _flight()
        with patch("weatherbrief.api.preferences.has_autorouter_link", return_value=True), \
             patch("weatherbrief.api.profiles.load_profile_context",
                   return_value=ProfileContext(settings={})):
            assert _gramet_owner_can_fetch(None, flight) is True
        with patch("weatherbrief.api.preferences.has_autorouter_link", return_value=False):
            assert _gramet_owner_can_fetch(None, flight) is False
        with patch("weatherbrief.api.preferences.has_autorouter_link", return_value=True), \
             patch("weatherbrief.api.profiles.load_profile_context",
                   return_value=ProfileContext(settings={"gramet_enabled": False})):
            assert _gramet_owner_can_fetch(None, flight) is False

    def test_historical_flight_never(self):
        from weatherbrief.api.packs import _gramet_owner_can_fetch

        past = _flight(departure=datetime.now(timezone.utc) - timedelta(days=5))
        with patch("weatherbrief.api.preferences.has_autorouter_link", return_value=True):
            assert _gramet_owner_can_fetch(None, past) is False


@pytest.fixture
def quiet_gate():
    """No model moved and flight params unchanged: the bare gate says "none"."""
    from weatherbrief.api.packs import DataStatus, ModelStatus

    ms = ModelStatus(
        source="m:openmeteo", pack_init=1, latest_available=1,
        next_expected="2026-05-20T12:00:00+00:00", state="current", covers_horizon=True,
    )
    status = DataStatus(fresh=True, stale_models=[], models={"m0": ms})
    with patch("weatherbrief.api.packs._build_data_status", return_value=status), \
         patch("weatherbrief.api.packs.SessionLocal"):
        yield


class TestRefreshGateOverride:
    def _decide(self, pack, can_fetch, **kw):
        from weatherbrief.api.packs import gated_data_status

        with patch("weatherbrief.api.packs._gramet_owner_can_fetch", return_value=can_fetch):
            return gated_data_status(pack, _flight(), params_override=False, **kw).refresh_decision

    def test_linked_since_pack_forces_full(self, quiet_gate):
        decision = self._decide(_pack(reason="not_linked"), can_fetch=True)
        assert decision.mode == "full"
        assert "GRAMET" in decision.reason
        assert decision.pending_models == []
        assert decision.eta_useful is None

    def test_still_not_linked_leaves_gate_alone(self, quiet_gate):
        assert self._decide(_pack(reason="not_linked"), can_fetch=False).mode != "full"

    @pytest.mark.parametrize("reason", ["failed", "daily_limit", None])
    def test_failures_and_legacy_never_loop_refreshes(self, quiet_gate, reason):
        """A failing Autorouter must not turn every Refresh into a paid run."""
        assert self._decide(_pack(reason=reason), can_fetch=True).mode != "full"

    def test_pack_with_gramet_untouched(self, quiet_gate):
        assert self._decide(_pack(has_gramet=True), can_fetch=True).mode != "full"

    def test_scheduler_opt_out(self, quiet_gate):
        decision = self._decide(_pack(reason="not_linked"), can_fetch=True, gramet_override=False)
        assert decision.mode != "full"


class TestRunGrametReason:
    def test_no_token_records_not_linked(self):
        from weatherbrief.tasks.outputs import GRAMET_NOT_LINKED, run_gramet

        result = run_gramet(route=None, departure_time=datetime.now(timezone.utc))
        assert result.path is None
        assert result.skip_reason == GRAMET_NOT_LINKED


class TestNoTokenReason:
    """A link that yields no token is a failure, so the gate never loops (#994 review)."""

    def test_linked_but_no_token_is_failed(self):
        from weatherbrief.api.packs import _no_token_gramet_reason

        with patch("weatherbrief.api.preferences.has_autorouter_link", return_value=True):
            assert _no_token_gramet_reason(object(), "owner") == "failed"

    def test_not_linked(self):
        from weatherbrief.api.packs import _no_token_gramet_reason

        with patch("weatherbrief.api.preferences.has_autorouter_link", return_value=False):
            assert _no_token_gramet_reason(object(), "owner") == "not_linked"

    def test_no_db_is_not_linked(self):
        from weatherbrief.api.packs import _no_token_gramet_reason

        assert _no_token_gramet_reason(None, "owner") == "not_linked"


class TestHistoryRefreshToFetch:
    """Only the latest pack can say "refresh to fetch" (#994 review)."""

    def test_older_packs_keep_their_stored_reason(self):
        from weatherbrief.api import packs as packs_api

        history = [_pack(reason="not_linked"), _pack(reason="not_linked")]
        seen = []

        def fake_meta(p, **kw):
            seen.append(kw["gramet_owner_can_fetch"])
            return packs_api._gramet_status(p, kw["gramet_owner_can_fetch"])

        with patch.object(packs_api, "_load_flight_or_404", return_value=_flight()), \
             patch.object(packs_api, "list_packs", return_value=history), \
             patch.object(packs_api, "_owner_can_fetch_for_viewer", return_value=True), \
             patch.object(packs_api, "_meta_to_response", side_effect=fake_meta):
            out = packs_api.list_flight_packs("f-gramet", user_id="owner", db=None)
        assert seen == [True, None]
        assert out == ["refresh_to_fetch", "not_linked"]
