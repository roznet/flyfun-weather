"""Debrief slice A1 (#711): per-tick display snapshots, single-tick replay
(``/live/at``), the observed recap and its forecast lane."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from live_scenario_replay import live_response, load_scenario, replay
from test_live_scenarios import LELL_LEMI
from weatherbrief.analysis.flight_progress import FlightProgress
from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.live import LiveHighlight
from weatherbrief.tasks.live_layer import load_live_history, patch_highlight
from weatherbrief.tasks.live_recap import (
    alert_markers,
    build_recap,
    default_forecast_pack,
    forecast_lane_summary,
    load_forecast_lane,
    summarize_recap,
)
from weatherbrief.tasks.live_snapshots import (
    LIVE_SNAPSHOTS_FILE,
    SNAPSHOT_MAX_BYTES,
    append_snapshot,
    find_tick,
    load_snapshots,
    tick_index,
    tick_view,
)

UTC = timezone.utc

#: Exactly what a snapshot line carries (#711 §1).
SNAPSHOT_KEYS = {
    "type", "v", "tick_at", "pack_timestamp", "pack_dir_name", "radar_time", "progress",
    "live_updated_at", "observations_updated_at", "sigmets_updated_at", "observed_updated_at",
    "glance", "ribbon", "storms", "changes", "last_refresh_delta", "route_sigmets",
    "observed_conditions", "observations",
}


@pytest.fixture(scope="module")
def lell_lemi(tmp_path_factory):
    flight_dir = tmp_path_factory.mktemp("recap") / "u" / "flight"
    ticks = replay(load_scenario(LELL_LEMI), flight_dir)
    return flight_dir, ticks


# --- Snapshots ---------------------------------------------------------------


def test_one_snapshot_per_tick_with_the_listed_blocks_only(lell_lemi):
    flight_dir, ticks = lell_lemi
    snaps = load_snapshots(flight_dir)
    assert [s["tick_at"] for s in snaps] == [t.at.isoformat() for t in ticks]
    for s in snaps:
        assert set(s) == SNAPSHOT_KEYS
        assert s["progress"]["source"] == "plan"
        # Only geometry: the METAR/TAF rows are rebuilt from the history.
        for a in (s["observations"] or {}).get("airports", []):
            assert "metar_raw" not in a and "taf_raw" not in a
    # Flown distance follows the plan through the window.
    flown = [s["progress"]["flown_nm"] for s in snaps]
    assert flown == sorted(flown) and flown[0] == 0.0 and flown[-1] > 0


def test_snapshot_file_stays_small(lell_lemi):
    flight_dir, ticks = lell_lemi
    size = (flight_dir / LIVE_SNAPSHOTS_FILE).stat().st_size
    # Busy SIGMET morning, no radar in the scenario: far under the ~6 KB a
    # tick measured with radar and cells, and the hard cap.
    assert size / len(ticks) < 6_000
    assert size < SNAPSHOT_MAX_BYTES


def test_observed_conditions_keep_summary_and_metadata_not_samples(tmp_path):
    from test_live_history import ROUTE, TS1, _observed
    from weatherbrief.tasks.live_layer import commit_live_update

    pack = tmp_path / "u" / "flight" / TS1.replace(":", "-")
    pack.mkdir(parents=True)
    now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    briefing = {"route": ROUTE, "departure_time": now.isoformat()}
    commit_live_update(pack, briefing_data=briefing, observations=None, sigmets=None,
                       observed=_observed(now, flashes=(3, 0), dbz=(45.0, 20.0)),
                       started_at=now, pack_timestamp=TS1, now=now)
    snap = load_snapshots(pack.parent)[0]
    oc = snap["observed_conditions"]
    assert oc["stations"] and oc["computed_at"]
    for name in ("reflectivity", "lightning"):
        assert oc[name]["valid_time"] and oc[name]["stations"] == []
    assert "summary" in oc and "sources" in oc


def test_cap_stops_appends(tmp_path, monkeypatch, lell_lemi):
    from weatherbrief.tasks import live_snapshots

    _, ticks = lell_lemi
    monkeypatch.setattr(live_snapshots, "SNAPSHOT_MAX_BYTES", 1)
    assert append_snapshot(tmp_path, ticks[0].layer, tick_at=ticks[0].at)
    assert not append_snapshot(tmp_path, ticks[1].layer, tick_at=ticks[1].at)
    assert len(load_snapshots(tmp_path)) == 1


def test_truncated_member_keeps_earlier_ticks(tmp_path, lell_lemi):
    _, ticks = lell_lemi
    for t in ticks[:3]:
        append_snapshot(tmp_path, t.layer, tick_at=t.at)
    path = tmp_path / LIVE_SNAPSHOTS_FILE
    path.write_bytes(path.read_bytes() + gzip.compress(b'{"type":"tick"}\n')[:-6])
    assert len(load_snapshots(tmp_path)) == 3


def test_generated_highlight_is_merged_into_its_tick(tmp_path, lell_lemi):
    from weatherbrief.tasks.live_layer import LIVE_FILE

    _, ticks = lell_lemi
    tick = ticks[5]
    (tmp_path / LIVE_FILE).write_text(tick.layer.model_dump_json())
    append_snapshot(tmp_path, tick.layer, tick_at=tick.at)
    hl = LiveHighlight(text="LEMI now IFR.", model="fixture", facts_hash="x",
                       gate={"g": 1}, generated_at=tick.at + timedelta(seconds=20))
    assert patch_highlight(tmp_path, hl, pack_timestamp=tick.layer.pack_timestamp, as_of=tick.layer.glance.as_of)
    snap = load_snapshots(tmp_path)[0]
    assert snap["glance"]["highlight"]["text"] == "LEMI now IFR."
    assert "gate" not in snap["glance"]["highlight"]


# --- /live/at round trip -----------------------------------------------------


@pytest.mark.parametrize("hhmm", ["05:10", "07:10", "08:30", "09:00"])
def test_live_at_returns_what_live_returned_at_that_tick(lell_lemi, hhmm):
    from weatherbrief.models.live import LiveTickResponse

    flight_dir, ticks = lell_lemi
    tick = next(t for t in ticks if t.at.strftime("%H:%M") == hhmm)
    snap = find_tick(load_snapshots(flight_dir), tick.at)
    replayed = LiveTickResponse(flight_id="fixture-1", **tick_view(flight_dir, snap)).model_dump(mode="json")
    served = live_response(tick)
    for key in ("live_updated_at", "route_sigmets", "storms", "glance", "ribbon", "changes",
                "last_refresh_delta", "observed_conditions", "sigmets_updated_at", "observations_updated_at"):
        assert replayed[key] == served[key], key

    def rows(body):
        return [
            (a["icao"], a["metar_raw"], a["metar_time"], a["metar_flight_category"], a["taf_raw"])
            for a in body["route_observations"]["airports"]
        ]

    assert rows(replayed) == rows(served)


def test_find_tick_takes_the_last_at_or_before(lell_lemi):
    flight_dir, ticks = lell_lemi
    snaps = load_snapshots(flight_dir)
    assert find_tick(snaps, ticks[3].at + timedelta(minutes=4))["tick_at"] == ticks[3].at.isoformat()
    assert find_tick(snaps, ticks[0].at - timedelta(minutes=1)) is None
    assert len(tick_index(snaps)) == len(ticks)


# --- Recap: the diagonal -----------------------------------------------------

DEP = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
ROUTE = RouteConfig.model_validate({
    "name": "ZZ recap",
    "waypoints": [
        {"icao": "ZZAA", "name": "A", "lat": 50.0, "lon": 0.0},
        {"icao": "ZZBB", "name": "B", "lat": 50.0, "lon": 2.593},  # 100 NM
    ],
    "cruise_altitude_ft": 6000,
    "flight_duration_hours": 1.0,
})


def _plan(departure=DEP, as_of=None) -> FlightProgress:
    return FlightProgress.from_route(ROUTE, departure, as_of or departure + timedelta(hours=3))


def _snap(tick_at: datetime, radar_lag_min: float, dbz: list[float | None], *, status="measured") -> dict:
    """A snapshot whose radar frame is ``radar_lag_min`` older than the tick,
    with one segment per ``dbz`` value over a 100 NM route."""
    n = len(dbz)
    seg_nm = 100.0 / n
    return {
        "type": "tick", "tick_at": tick_at.isoformat(), "pack_timestamp": "p1",
        "radar_time": (tick_at - timedelta(minutes=radar_lag_min)).isoformat(),
        "progress": {"source": "plan", "flown_nm": None},
        "ribbon": {
            "route_nm": 100.0, "segment_nm": seg_nm, "radar_radius_nm": 5.0,
            "waypoints": [{"icao": "ZZAA", "along_nm": 0.0}, {"icao": "ZZBB", "along_nm": 100.0}],
            "segments": [
                {
                    "index": i, "from_nm": i * seg_nm, "to_nm": (i + 1) * seg_nm,
                    "radar_max_dbz": v, "radar_status": status if v is not None else "no_sample",
                    "lightning": None, "sigmet_ids": [], "storm_ids": [],
                }
                for i, v in enumerate(dbz)
            ],
            "weather": [], "weather_status": "available",
        },
        "storms": {"status": "available", "corridor_nm": 20.0, "storms": []},
    }


def _ticks(values_by_tick: dict[int, list[float]], *, lag=12) -> list[dict]:
    """Ticks every 15 min from DEP - 30 min; each value list is that tick's
    radar per segment."""
    return [_snap(DEP + timedelta(minutes=m), lag, v) for m, v in sorted(values_by_tick.items())]


def test_recap_picks_each_segment_by_radar_time_not_tick_time():
    # Each tick's radar frame is 12 min older than the tick (ticks 09:45,
    # 10:15, ... 11:15; frames 09:33, 10:03, ... 11:03).
    snaps = _ticks({
        -15: [1, 1, 1, 1],
        15: [10, 10, 10, 10],
        30: [20, 20, 20, 20],
        45: [30, 30, 30, 30],
        60: [40, 40, 40, 40],
        75: [50, 50, 50, 50],
    })
    progress = _plan()
    recap = build_recap(snaps, [], progress)
    assert recap["status"] == "ok" and recap["time_base"] == "planned"
    segs = recap["segments"]
    etas = [progress.eta((s["from_nm"] + s["to_nm"]) / 2) for s in segs]

    def nearest(key):
        return [min(snaps, key=lambda sn: abs((datetime.fromisoformat(sn[key]) - t).total_seconds()))
                ["ribbon"]["segments"][0]["radar_max_dbz"] for t in etas]

    assert [s["radar_max_dbz"] for s in segs] == nearest("radar_time") == [10, 20, 30, 40]
    # Matching on the tick time would have shown the second segment as the
    # 10:15 tick's (10:03) frame: the lag is more than a segment.
    assert nearest("tick_at") != nearest("radar_time")
    assert all(s["status"] == "observed" for s in segs)
    assert all(abs(s["gap_min"]) <= 7.5 for s in segs)


@dataclass(frozen=True)
class _LateProgress:
    """An observed-source progress (as #760 will give): took off 30 min late."""

    plan: FlightProgress
    late_min: int = 30
    source: str = "manual"

    @property
    def time_base(self):
        return "observed"

    @property
    def timed(self):
        return True

    @property
    def departed_at(self):
        return self.plan.departed_at + timedelta(minutes=self.late_min)

    @property
    def arrival(self):
        return self.plan.arrival + timedelta(minutes=self.late_min)

    def eta(self, along):
        return replace(self.plan, planned_departure=self.departed_at).eta(along)

    def flown_at(self, t):
        return replace(self.plan, planned_departure=self.departed_at).flown_at(t)

    def planned(self):
        return self.plan


def test_recap_follows_a_late_departure_from_flight_progress():
    # Segment midpoints are passed 7.5, 22.5, 37.5 and 52.5 min after take-off.
    snaps = _ticks({m: [m, m, m, m] for m in range(5, 126, 15)}, lag=0)
    on_time = build_recap(snaps, [], _plan())
    late = build_recap(snaps, [], _LateProgress(_plan()))
    assert late["time_base"] == "observed"
    assert late["departed_at"] == (DEP + timedelta(minutes=30)).isoformat()
    # Each segment comes from about 30 min later.
    assert [s["radar_max_dbz"] for s in on_time["segments"]] == [5, 20, 35, 50]
    assert [s["radar_max_dbz"] for s in late["segments"]] == [35, 50, 65, 80]


def test_cancelled_flight_gets_a_planned_recap():
    snaps = _ticks({m: [m, m, m, m] for m in range(0, 121, 15)}, lag=0)
    recap = build_recap(snaps, [], _LateProgress(_plan()), cancelled=True)
    assert recap["cancelled"] is True
    assert recap["time_base"] == "planned"
    assert recap["departed_at"] == DEP.isoformat()
    assert summarize_recap(recap)["cancelled"] is True


def test_segments_outside_the_recorded_window_are_not_guessed():
    # Recording stops at 10:15; the second half of the route is flown later.
    snaps = _ticks({-30: [5, 5, 5, 5], -15: [5, 5, 5, 5], 0: [5, 5, 5, 5], 15: [5, 5, 5, 5]}, lag=0)
    segs = build_recap(snaps, [], _plan())["segments"]
    assert [s["status"] for s in segs] == ["observed", "observed", "not_reached", "not_reached"]
    assert "radar_max_dbz" not in segs[3]


def test_no_coverage_and_no_sample_stay_distinct_from_nothing_detected():
    snaps = [
        _snap(DEP + timedelta(minutes=m), 0, [None, 0.0, 0.0, 0.0], status="measured")
        for m in range(0, 61, 15)
    ]
    for s in snaps:
        s["ribbon"]["segments"][2]["radar_status"] = "no_coverage"
        s["ribbon"]["segments"][2]["radar_max_dbz"] = None
    recap = build_recap(snaps, [], _plan())
    statuses = [s["radar_status"] for s in recap["segments"]]
    assert statuses == ["no_sample", "measured", "no_coverage", "measured"]
    summary = summarize_recap(recap)
    assert summary["segments_radar_unavailable"] == 2
    assert summary["max_radar"]["dbz"] == 0.0


def test_status_without_recording():
    assert build_recap([], [], _plan())["status"] == "no_live_window"
    rec = build_recap([], [{"type": "pack", "tick_at": DEP.isoformat()}], _plan())
    assert rec["status"] == "not_recorded" and "not recorded" in rec["message"]
    assert build_recap(_ticks({0: [1]}), [], None)["status"] == "untimed"
    assert summarize_recap(build_recap([], [], _plan())) is None


# --- Alert markers -----------------------------------------------------------


def test_alert_markers_match_the_change_records(lell_lemi):
    flight_dir, ticks = lell_lemi
    history = load_live_history(flight_dir)
    progress = FlightProgress.from_route(
        RouteConfig.model_validate(ticks[0].briefing["route"]),
        datetime.fromisoformat(ticks[0].briefing["departure_time"]), ticks[-1].at,
    )
    markers = alert_markers(history, progress)
    events = [r for r in history if r.get("type") == "event"]
    assert len(markers) == len(events) > 0
    for m, r in zip(markers, events):
        assert m["at"] == r["tick_at"]
        assert m["message"] == r["change"]["message"]
        assert m["tier"] == r["change"]["tier"]
        assert m["event"] == ("raised" if r["event"] == "appeared" else "cleared")
        assert m["aircraft_nm"] == pytest.approx(round(progress.flown_at(datetime.fromisoformat(r["tick_at"])), 1))


# --- Forecast lane -----------------------------------------------------------


def _manifest():
    from weatherbrief.models.advisories import RouteAdvisoriesManifest

    return RouteAdvisoriesManifest.model_validate({
        "total_distance_nm": 100.0, "models": ["gfs", "ecmwf"], "aggregation": "worst",
        "advisories": [{
            "advisory_id": "icing", "aggregate_status": "amber", "representative_model": "gfs",
            "per_model": [
                {"model": "gfs", "status": "amber", "highlights": {"ribbon": [
                    {"dist_from_nm": 0, "dist_to_nm": 40, "severity": "green"},
                    {"dist_from_nm": 40, "dist_to_nm": 100, "severity": "amber"},
                ]}},
                {"model": "ecmwf", "status": "green"},
            ],
        }],
    })


def test_forecast_lane_is_written_with_the_advisories(tmp_path):
    from weatherbrief.tasks.artifacts import save_advisory_artifacts

    save_advisory_artifacts(tmp_path, _manifest())
    lane = json.loads((tmp_path / "forecast_lane.json").read_text())
    assert lane == forecast_lane_summary(_manifest())
    icing = lane["advisories"][0]
    assert icing["per_model"][0]["ribbon"] == [[0, 40, "green"], [40, 100, "amber"]]
    assert icing["per_model"][1]["ribbon"] == []
    assert (tmp_path / "forecast_lane.json").stat().st_size < 5_000


def test_forecast_lane_falls_back_to_the_manifest_for_older_packs(tmp_path):
    (tmp_path / "route_advisories.json").write_text(_manifest().model_dump_json())
    assert load_forecast_lane(tmp_path) == forecast_lane_summary(_manifest())
    assert load_forecast_lane(tmp_path / "missing") is None


def test_default_pack_is_newest_before_departure():
    p = [(DEP - timedelta(hours=h), None) for h in (30, 5, 1)] + [(DEP + timedelta(hours=1), None)]
    assert default_forecast_pack(p, DEP)[0] == DEP - timedelta(hours=1)
    assert default_forecast_pack(p, DEP - timedelta(days=3)) is None
    assert default_forecast_pack(p, None) is None


def test_summary_only_after_departure():
    snaps = _ticks({m: [m, m, m, m] for m in range(5, 126, 15)}, lag=0)
    recap = build_recap(snaps, [], _plan())
    s = summarize_recap(recap)
    assert s["status"] == "ok" and s["segments_observed"] == 4
    assert s["max_radar"]["dbz"] == 50
    assert s["time_base"] == "planned"


def test_partial_member_mid_file_loses_only_itself(tmp_path, lell_lemi):
    """A crash mid-append leaves a cut-short member; the next append lands
    right after it. Later ticks must still read (review on #773)."""
    _, ticks = lell_lemi
    path = tmp_path / LIVE_SNAPSHOTS_FILE
    append_snapshot(tmp_path, ticks[0].layer, tick_at=ticks[0].at)
    path.write_bytes(path.read_bytes() + gzip.compress(b'{"type":"tick","tick_at":"x"}\n' * 50)[:-20])
    append_snapshot(tmp_path, ticks[1].layer, tick_at=ticks[1].at)
    append_snapshot(tmp_path, ticks[2].layer, tick_at=ticks[2].at)
    assert [s["tick_at"] for s in load_snapshots(tmp_path)] == [t.at.isoformat() for t in ticks[:3]]


def test_failed_append_is_rolled_back(tmp_path, lell_lemi, monkeypatch):
    from weatherbrief.tasks import live_snapshots

    _, ticks = lell_lemi
    append_snapshot(tmp_path, ticks[0].layer, tick_at=ticks[0].at)
    size = (tmp_path / LIVE_SNAPSHOTS_FILE).stat().st_size

    def boom(data, compresslevel=6):
        raise OSError("disk full")

    monkeypatch.setattr(live_snapshots.gzip, "compress", boom)
    assert not append_snapshot(tmp_path, ticks[1].layer, tick_at=ticks[1].at)
    assert (tmp_path / LIVE_SNAPSHOTS_FILE).stat().st_size == size


def test_tick_view_without_pack_name_does_not_read_the_flight_dir(lell_lemi):
    flight_dir, _ = lell_lemi
    snap = dict(load_snapshots(flight_dir)[3], pack_dir_name=None)
    view = tick_view(flight_dir, snap)
    assert view["changes"] is not None


def test_tick_view_degrades_on_a_snapshot_the_models_no_longer_accept(lell_lemi):
    flight_dir, _ = lell_lemi
    snap = dict(load_snapshots(flight_dir)[3], changes={"changes": [{"key": "x"}]})
    assert tick_view(flight_dir, snap)["changes"] is None


def test_recap_response_tolerates_an_off_shape_weather_profile():
    """#769: a stored band whose profile entries are not the ribbon's
    [along, lo, hi] triples must not 500 the typed /recap."""
    from weatherbrief.models.live import RecapResponse

    body = {
        "flight_id": "zz", "note": "", "status": "ok",
        "weather": [{"id": "rain-1", "tier": "rain", "from_nm": 0, "to_nm": 10,
                     "profile": [[5.0, 1.0], [6.0, 1.0, 2.0, 3.0]], "tick_at": "2026-06-12T10:00:00+00:00"}],
    }
    resp = RecapResponse.model_validate(body)
    assert resp.weather[0].profile == [[5.0, 1.0], [6.0, 1.0, 2.0, 3.0]]
