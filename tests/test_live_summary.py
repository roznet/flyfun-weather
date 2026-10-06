"""The compact ``live`` block agents get with a briefing (#641).

``live_layer.live_summary`` is the one helper behind the MCP ``get_briefing``
(via ``GET /api/flights/{id}/live/summary``) and the ChatGPT ``getBriefing``
action (in-process). The endpoint wiring is in ``test_agent_endpoints`` and
``test_mcp_live``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from live_scenario_replay import load_scenario, replay
from weatherbrief.models.live import LiveChange, LiveChanges, LiveLayer
from weatherbrief.models.observations import (
    AirportObservation,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.tasks.live_layer import (
    LIVE_NOTE,
    LIVE_SUMMARY_MAX_CHANGES,
    LIVE_SUMMARY_MAX_SIGMETS,
    commit_live_update,
    live_summary,
    summarize_live,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
PACK_TS = "2026-10-01T07:00:00+00:00"
BRIEFING = {
    "route": {
        "name": "ZZ test",
        "waypoints": [
            {"icao": "ZZDP", "name": "Dep", "lat": 50.0, "lon": 1.0},
            {"icao": "ZZMD", "name": "Mid", "lat": 50.5, "lon": 1.5},
            {"icao": "ZZDS", "name": "Dest", "lat": 51.0, "lon": 2.0},
        ],
        "cruise_altitude_ft": 6000,
        "flight_duration_hours": 1.0,
    },
    "departure_time": (NOW + timedelta(hours=2)).isoformat(),
    "alternates": {"alternates": [{"icao": "ZZA1"}, {"icao": "ZZA2"}]},
}


def _airport(icao, cat="VFR", *, metar=True):
    return AirportObservation(
        icao=icao, distance_from_route_nm=0.0, nearest_waypoint_icao="ZZDS",
        metar_raw=f"METAR {icao} 010850Z 24010KT 9999 FEW030 15/10 Q1015" if metar else None,
        metar_time=NOW - timedelta(minutes=10) if metar else None,
        metar_flight_category=cat if metar else None, has_metar=metar,
    )


def _change(key, *, tier="highlight", direction="worse", role="route", minutes_ago=30):
    return LiveChange(
        key=key, kind="metar_category", source="METAR", direction=direction,
        tier=tier, role=role, icao=key.split(":")[-1], message=key,
        observed_at=NOW - timedelta(minutes=minutes_ago),
    )


def _layer(changes, *, airports=(), sigmets=(), baseline_source="briefing"):
    return LiveLayer(
        flight_id="flight-zz", pack_timestamp=PACK_TS, pack_dir_name="pack",
        live_updated_at=NOW, observations_updated_at=NOW, sigmets_updated_at=NOW,
        route_observations=RouteObservations(
            corridor_nm=30.0, fetch_time=NOW, airports_found=len(airports),
            airports_with_metar=len(airports), airports_with_taf=0, airports=list(airports),
        ),
        route_sigmets=RouteSigmets(corridor_nm=30.0, fetch_time=NOW, sigmets=list(sigmets)),
        changes=LiveChanges(
            baseline_at=NOW - timedelta(hours=2), baseline_source=baseline_source,
            computed_at=NOW, changes=list(changes),
        ),
    )


def test_changes_order_alert_worse_role_then_newest():
    layer = _layer([
        _change("metar:ZZMD", minutes_ago=5),                                  # highlight route
        _change("metar:ZZA1", tier="highlight", role="alternate"),
        _change("metar:ZZDP", tier="alert", role="departure"),
        _change("metar:ZZDS", tier="alert", role="destination", minutes_ago=50),
        _change("metar:ZZD2", tier="alert", role="destination", minutes_ago=10),
        _change("metar:ZZDB", tier="alert", role="destination", direction="better"),
    ])
    out = summarize_live(layer, BRIEFING)
    assert [c["message"] for c in out["changes"]] == [
        "metar:ZZD2",   # alert, worse, destination, newest
        "metar:ZZDS",   # alert, worse, destination, older
        "metar:ZZDP",   # alert, worse, departure
        "metar:ZZDB",   # alert, better
        "metar:ZZA1",   # highlight, alternate before route
        "metar:ZZMD",
    ]
    assert out["alert_count"] == 4
    assert out["worsened_count"] == 5
    assert out["improved_count"] == 1
    assert out["note"] == LIVE_NOTE
    assert out["digest_written_at"] == PACK_TS
    assert out["baseline_source"] == "briefing"


def test_size_limits_keep_totals():
    changes = [_change(f"metar:ZZ{i:02d}") for i in range(LIVE_SUMMARY_MAX_CHANGES + 5)]
    sigmets = [
        SigmetAlongRoute(fir_id=f"ZZ{i:02d}", hazard="TS", qualifier="EMBD", raw_text="x " * 200,
                         coords=[(1.0, 50.0), (2.0, 50.0), (2.0, 51.0)])
        for i in range(LIVE_SUMMARY_MAX_SIGMETS + 3)
    ]
    out = summarize_live(_layer(changes, sigmets=sigmets), BRIEFING)
    assert len(out["changes"]) == LIVE_SUMMARY_MAX_CHANGES
    assert out["changes_total"] == LIVE_SUMMARY_MAX_CHANGES + 5
    assert len(out["sigmets"]) == LIVE_SUMMARY_MAX_SIGMETS
    assert out["sigmets_total"] == LIVE_SUMMARY_MAX_SIGMETS + 3
    # No polygons, no raw text: the block stays small.
    assert "coords" not in out["sigmets"][0] and "raw_text" not in out["sigmets"][0]
    assert len(json.dumps(out)) < 12_000


def test_sigmet_fields():
    sig = SigmetAlongRoute(
        fir_id="ZZZZ", hazard="TS", qualifier="EMBD", base_ft=None, top_ft=38000,
        valid_from=NOW, valid_to=NOW + timedelta(hours=4),
        raw_text="ZZZZ SIGMET 3 VALID 010900/011300 ZZZZ- EMBD TS",
        enroute_distance_from_nm=120.0, enroute_distance_to_nm=180.0,
    )
    (s,) = summarize_live(_layer([], sigmets=[sig]), BRIEFING)["sigmets"]
    assert s == {
        "label": "ZZZZ 3: EMBD TS",
        "fir_id": "ZZZZ", "hazard": "TS", "qualifier": "EMBD",
        "valid_from": NOW.isoformat(), "valid_to": (NOW + timedelta(hours=4)).isoformat(),
        "base_ft": None, "top_ft": 38000,
        "enroute_distance_from_nm": 120.0, "enroute_distance_to_nm": 180.0,
    }


def test_airports_only_departure_destination_alternates_in_role_order():
    airports = [
        _airport("ZZMD"),                   # en-route: excluded
        _airport("ZZA2", "MVFR"),
        _airport("ZZDS", "IFR"),
        _airport("ZZDP"),
        _airport("ZZA1", metar=False),      # no METAR: nothing to show
    ]
    out = summarize_live(_layer([], airports=airports), BRIEFING)
    assert [(a["icao"], a["role"]) for a in out["airports"]] == [
        ("ZZDS", "destination"), ("ZZDP", "departure"), ("ZZA2", "alternate"),
    ]
    dest = out["airports"][0]
    assert dest["flight_category"] == "IFR"
    assert dest["metar_raw"].startswith("METAR ZZDS")


def test_live_start_baseline_is_reported():
    out = summarize_live(_layer([], baseline_source="live_start"), BRIEFING)
    assert out["baseline_source"] == "live_start"
    assert out["baseline_at"] == (NOW - timedelta(hours=2)).isoformat()


def test_absent_layer_is_none(tmp_path):
    pack_dir = tmp_path / "u" / "flight-zz" / "pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.json").write_text(json.dumps(BRIEFING))
    assert live_summary(pack_dir) is None
    assert live_summary(None) is None


def test_layer_of_another_pack_is_none(tmp_path):
    old = tmp_path / "u" / "flight-zz" / "old"
    new = tmp_path / "u" / "flight-zz" / "new"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "briefing.json").write_text(json.dumps(BRIEFING))
    commit_live_update(
        old, briefing_data=BRIEFING,
        observations=RouteObservations(
            corridor_nm=30.0, fetch_time=NOW, airports_found=1, airports_with_metar=1,
            airports_with_taf=0, airports=[_airport("ZZDS")],
        ),
        sigmets=None, observed=None, started_at=NOW, pack_timestamp=PACK_TS, now=NOW,
    )
    assert live_summary(old) is not None
    assert live_summary(new) is None


def test_corrupt_layer_never_raises(tmp_path):
    pack_dir = tmp_path / "u" / "flight-zz" / "pack"
    pack_dir.mkdir(parents=True)
    (pack_dir.parent / "live.json").write_text("{not json")
    assert live_summary(pack_dir) is None


def test_lell_lemi_0830_leads_with_destination_sigmet(tmp_path):
    """The frozen 2026-10-02 morning: at 08:30 the new LECB 3 / LECM 3 EMBD TS
    at the destination is the first thing an agent reads."""
    ticks = replay(load_scenario("2026-10-02_lell_lemi"), tmp_path / "u" / "flight")
    tick = next(t for t in ticks if t.at.strftime("%H:%M") == "08:30")
    # The replay runs the whole window, so the stored layer is the last tick:
    # summarise the 08:30 one with its pack's briefing.
    pack_dir = tmp_path / "u" / "flight" / tick.pack.replace(":", "-")
    out = summarize_live(tick.layer, json.loads((pack_dir / "briefing.json").read_text()))
    first = out["changes"][0]
    assert first["tier"] == "alert"
    assert first["role"] == "destination"
    assert first["kind"] == "sigmet_issued"
    assert first["message"] == "New SIGMET LECB 3 / LECM 3: EMBD TS from 08:35Z (at destination)"
    labels = {s["label"] for s in out["sigmets"]}
    assert any(label.startswith("LECB 3") for label in labels), labels
    # The pack rebuilt at 06:52 carries its own observations: a briefing baseline.
    assert out["baseline_source"] == "briefing"
    assert out["digest_written_at"] == "2026-10-02T06:52:55+00:00"
    roles = {a["icao"]: a["role"] for a in out["airports"]}
    assert roles.get("LEMI") == "destination"


# --- Trails (#669): times_today and recently_cleared -----------------------


def test_times_today_and_recently_cleared_from_the_trails():
    from weatherbrief.models.live import LiveChangeTrail
    from weatherbrief.tasks.live_layer import LIVE_SUMMARY_MAX_CLEARED

    bouncing = _change("conv:ZZDS", tier="alert", role="destination").model_copy(
        update={"trail": LiveChangeTrail(times_today=2)},
    )
    cleared = [
        _change(f"metar:ZZC{i}").model_copy(update={"cleared_at": NOW - timedelta(minutes=i)})
        for i in range(8)
    ]
    trailed = LiveChanges(computed_at=NOW, changes=[bouncing], recently_cleared=cleared)
    out = summarize_live(_layer([bouncing]), BRIEFING, trailed)
    assert out["changes"][0]["times_today"] == 2
    assert LIVE_SUMMARY_MAX_CLEARED == 6
    assert out["recently_cleared"] == [
        {"key": f"metar:ZZC{i}", "message": f"metar:ZZC{i}", "cleared_at": (NOW - timedelta(minutes=i)).isoformat()}
        for i in range(6)
    ]
    # Counts stay "what is true now".
    assert out["alert_count"] == 1 and out["worsened_count"] == 1
    # No strips or spans for agents.
    assert "trail" not in out["changes"][0]


def test_without_trails_no_times_today_and_empty_cleared():
    out = summarize_live(_layer([_change("metar:ZZDS")]), BRIEFING)
    assert "times_today" not in out["changes"][0]
    assert out["recently_cleared"] == []


def test_live_summary_reads_the_history(tmp_path):
    """End to end on wall-clock time: a destination blip that cleared ten
    minutes ago is listed, and the current change counts its recurrence."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    pack_dir = tmp_path / "u" / "flight-zz" / "pack"
    pack_dir.mkdir(parents=True)
    briefing = {
        **BRIEFING,
        "departure_time": (now + timedelta(hours=2)).isoformat(),
        "route_observations": RouteObservations(
            corridor_nm=30.0, fetch_time=now - timedelta(hours=1), airports_found=1, airports_with_metar=1,
            airports_with_taf=0, airports=[_airport_at("ZZDS", "VFR", now - timedelta(hours=1))],
        ).model_dump(mode="json"),
    }
    (pack_dir / "briefing.json").write_text(json.dumps(briefing))
    for minutes, cat in ((40, "IFR"), (30, "VFR"), (20, "IFR"), (10, "VFR")):
        t = now - timedelta(minutes=minutes)
        commit_live_update(
            pack_dir, briefing_data=briefing,
            observations=RouteObservations(
                corridor_nm=30.0, fetch_time=t, airports_found=1, airports_with_metar=1,
                airports_with_taf=0, airports=[_airport_at("ZZDS", cat, t)],
            ),
            sigmets=None, observed=None, started_at=t, pack_timestamp=PACK_TS, now=t,
        )
    out = live_summary(pack_dir)
    assert out["changes"] == []
    [row] = out["recently_cleared"]
    assert row["key"] == "metar:ZZDS"
    assert row["cleared_at"] == (now - timedelta(minutes=10)).isoformat()


def _airport_at(icao, cat, t):
    return AirportObservation(
        icao=icao, distance_from_route_nm=0.0, nearest_waypoint_icao=icao,
        metar_raw=f"METAR {icao} {t:%d%H%M}Z 24010KT 9999 {cat}", metar_time=t,
        metar_flight_category=cat, has_metar=True,
    )
