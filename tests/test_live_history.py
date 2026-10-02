"""Per-flight live history (#643): change events, raw reports, radar/lightning
evidence, appended by ``commit_live_update`` to ``live_history.jsonl``."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from live_scenario_replay import load_scenario, observations_at, replay, timeline
from test_live_scenarios import EXPECTED_LELL_LEMI, LELL_LEMI
from weatherbrief.models.observations import (
    AirportObservation,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.models.observed import (
    ObservedAnnulus,
    ObservedConditions,
    ObservedField,
    ObservedFlashAnnulus,
    ObservedFlashField,
    ObservedFlashStationSamples,
    ObservedStationRef,
    ObservedStationSamples,
)
from weatherbrief.tasks import live_layer
from weatherbrief.tasks.live_layer import (
    LIVE_HISTORY_FILE,
    commit_live_update,
    load_live,
    load_live_history,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
TS1, TS2 = "2026-10-01T07:00:00+00:00", "2026-10-01T09:30:00+00:00"
ROUTE = {
    "name": "ZZ test",
    "waypoints": [
        {"icao": "ZZDP", "name": "Dep", "lat": 50.0, "lon": 1.0},
        {"icao": "ZZDS", "name": "Dest", "lat": 51.0, "lon": 2.0},
    ],
    "cruise_altitude_ft": 6000,
    "flight_duration_hours": 1.0,
}
TAF = "TAF ZZDS 010500Z 0106/0206 24010KT 9999 SCT030"


def _obs(cat, t, *, report_type="METAR", taf=None, taf_t=None, fetch=None):
    return RouteObservations(
        corridor_nm=30.0, fetch_time=fetch or t, airports_found=1, airports_with_metar=1,
        airports_with_taf=1 if taf else 0,
        airports=[AirportObservation(
            icao="ZZDS", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZDS",
            metar_raw=f"{report_type} ZZDS {t:%d%H%M}Z 24010KT 9999 {cat}",
            metar_flight_category=cat, metar_time=t, metar_report_type=report_type,
            taf_raw=taf, taf_issue_time=taf_t,
        )],
    )


def _sigmets(t, *seqs):
    return RouteSigmets(corridor_nm=50.0, fetch_time=t, sigmets=[
        SigmetAlongRoute(
            fir_id="ZZZZ", hazard="TS", qualifier="EMBD",
            valid_from=t - timedelta(minutes=5), valid_to=t + timedelta(hours=4),
            raw_text=f"ZZZZ SIGMET {seq} VALID EMBD TS", coords=[(1.0, 50.0), (2.0, 51.0), (1.0, 51.0)],
            enroute_distance_from_nm=10.0, enroute_distance_to_nm=20.0,
        )
        for seq in seqs
    ])


def _observed(t, *, flashes=(0, 0), dbz=(20.0, 20.0)):
    """Two route points at 10 and 80 NM along track."""
    return ObservedConditions(
        computed_at=t, corridor_nm=20, radii_nm=[5, 10, 20],
        stations=[
            ObservedStationRef(id="p0", lat=50.0, lon=1.0, enroute_distance_nm=10.0),
            ObservedStationRef(id="p1", lat=50.5, lon=2.0, enroute_distance_nm=80.0),
        ],
        lightning=ObservedFlashField(
            source="mtg_li", quantity="flashes", valid_time=t, age_minutes=2,
            stations=[
                ObservedFlashStationSamples(
                    station_id=sid, annuli=[ObservedFlashAnnulus(radius_nm=5, flash_count=n)],
                )
                for sid, n in zip(("p0", "p1"), flashes)
            ],
        ),
        reflectivity=ObservedField(
            source="opera", quantity="dbzh", valid_time=t, age_minutes=5,
            stations=[
                ObservedStationSamples(
                    station_id=sid,
                    annuli=[ObservedAnnulus(radius_nm=5, total_px=100, valid_px=90, nodata_px=10, max_value=v)],
                )
                for sid, v in zip(("p0", "p1"), dbz)
            ],
        ),
    )


def _pack(tmp_path: Path, name: str, *, observed=None) -> tuple[Path, dict]:
    pack_dir = tmp_path / "user" / "flight-zz" / name
    pack_dir.mkdir(parents=True)
    briefing = {
        "route": ROUTE,
        "departure_time": (NOW + timedelta(hours=2)).isoformat(),
        "days_out": 0,
        "route_observations": _obs("VFR", NOW - timedelta(hours=1)).model_dump(mode="json"),
        "route_sigmets": _sigmets(NOW - timedelta(hours=1)).model_dump(mode="json"),
    }
    if observed is not None:
        briefing["observed_conditions"] = observed.model_dump(mode="json")
    (pack_dir / "briefing.json").write_text(json.dumps(briefing))
    return pack_dir, briefing


def _commit(pack_dir, briefing, obs=None, *, at, ts=TS1, sigmets=None, observed=None):
    return commit_live_update(
        pack_dir, briefing_data=briefing, observations=obs, sigmets=sigmets,
        observed=observed, started_at=at, pack_timestamp=ts, now=at,
    )


def _of(history, type_):
    return [r for r in history if r["type"] == type_]


def _events(history):
    return [(r["tick_at"][11:16], r["event"], r["change"]["key"], r["change"].get("to_value"))
            for r in _of(history, "event")]


def test_events_for_appear_and_clear_not_for_message_only_changes(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    t = [NOW + timedelta(minutes=10 * i) for i in range(4)]
    _commit(pack, briefing, _obs("IFR", t[0]), at=t[0])
    # Same category on a SPECI: the message gains "(SPECI)", the change is the same.
    layer = _commit(pack, briefing, _obs("IFR", t[1], report_type="SPECI"), at=t[1])
    assert "(SPECI)" in layer.changes.changes[0].message
    _commit(pack, briefing, _obs("VFR", t[2]), at=t[2])
    history = load_live_history(pack.parent)
    assert _events(history) == [
        ("09:00", "appeared", "metar:ZZDS", "IFR"),
        ("09:20", "cleared", "metar:ZZDS", "IFR"),
    ]
    appeared, cleared = _of(history, "event")
    assert appeared["change"]["new_alert"] is True and appeared["pack_timestamp"] == TS1
    # The clear carries the last message the pilot saw.
    assert cleared["change"]["message"].endswith("(SPECI)")


def test_each_report_is_written_once(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    t0, t1, t2 = NOW, NOW + timedelta(minutes=10), NOW + timedelta(minutes=20)
    taf_t = NOW - timedelta(hours=4)
    _commit(pack, briefing, _obs("VFR", t0, taf=TAF, taf_t=taf_t), sigmets=_sigmets(t0, "1"), at=t0)
    _commit(pack, briefing, _obs("VFR", t0, taf=TAF, taf_t=taf_t), sigmets=_sigmets(t0, "1"), at=t1)
    _commit(pack, briefing, _obs("VFR", t2, taf=TAF, taf_t=taf_t), sigmets=_sigmets(t0, "1", "2"), at=t2)
    reports = _of(load_live_history(pack.parent), "report")
    kinds = [(r["kind"], r["tick_at"][11:16], r.get("observed_at") or r.get("issued_at") or r["raw"])
             for r in reports]
    assert kinds == [
        # The pack's own baseline, recorded with the pack (seen at its fetch time).
        ("metar", "09:00", (NOW - timedelta(hours=1)).isoformat()),
        ("metar", "09:00", t0.isoformat()),
        ("taf", "09:00", taf_t.isoformat()),
        ("sigmet", "09:00", "ZZZZ SIGMET 1 VALID EMBD TS"),
        ("metar", "09:20", t2.isoformat()),
        ("sigmet", "09:20", "ZZZZ SIGMET 2 VALID EMBD TS"),
    ]
    assert reports[0]["seen_at"] == (NOW - timedelta(hours=1)).isoformat()
    assert "seen_at" not in reports[1]  # == tick_at
    assert reports[1]["raw"].startswith("METAR ZZDS") and reports[1]["report_type"] == "METAR"
    assert reports[2]["raw"] == TAF
    sig = reports[3]
    assert sig["fir_id"] == "ZZZZ" and sig["sigmet"]["coords"] and "raw_text" not in sig["sigmet"]


def test_radar_and_lightning_events_carry_evidence(tmp_path):
    pack, briefing = _pack(tmp_path, "p1", observed=_observed(NOW - timedelta(hours=1)))
    t0, t1 = NOW, NOW + timedelta(minutes=10)
    layer = _commit(pack, briefing, at=t0, observed=_observed(t0, flashes=(0, 4), dbz=(48.0, 20.0)))
    assert {c.kind for c in layer.changes.changes} == {"lightning", "radar"}
    _commit(pack, briefing, _obs("IFR", t1), at=t1, observed=_observed(t1, flashes=(0, 4), dbz=(48.0, 20.0)))
    history = load_live_history(pack.parent)
    types = [(r["type"], r.get("kind") or r.get("change", {}).get("kind")) for r in history if r["type"] != "report"]
    assert types == [
        ("pack", None),
        ("event", "radar"), ("evidence", "radar"),
        ("event", "lightning"), ("evidence", "lightning"),
        ("event", "metar_category"),  # no evidence for a METAR change
    ]
    radar, lightning = _of(history, "evidence")
    assert lightning["observed_at"] == t0.isoformat()
    assert lightning["points"] == [{
        "station_id": "p1", "enroute_distance_nm": 80.0, "radius_nm": 5.0, "flash_count": 4,
        "max_dbz": None, "valid_px": None, "total_px": None,
    }]
    [pt] = radar["points"]
    assert (pt["station_id"], pt["max_dbz"], pt["valid_px"], pt["total_px"]) == ("p0", 48.0, 90, 100)


def test_evidence_stays_out_of_the_live_layer(tmp_path):
    pack, briefing = _pack(tmp_path, "p1", observed=_observed(NOW - timedelta(hours=1)))
    _commit(pack, briefing, at=NOW, observed=_observed(NOW, flashes=(0, 4)))
    assert "evidence" not in (pack.parent / "live.json").read_text()
    [c] = load_live(pack.parent).changes.changes
    assert c.kind == "lightning" and c.evidence is None


def test_pack_switch_is_recorded_and_the_timeline_continues(tmp_path):
    p1, b1 = _pack(tmp_path, "p1")
    t0, t1 = NOW, NOW + timedelta(minutes=10)
    _commit(p1, b1, _obs("IFR", t0), at=t0)
    p2 = p1.parent / "p2"
    p2.mkdir()
    b2 = {**b1, "route_observations": _obs("IFR", t0).model_dump(mode="json")}
    (p2 / "briefing.json").write_text(json.dumps(b2))
    # The new pack already saw IFR: that change clears in the same timeline.
    _commit(p2, b2, _obs("IFR", t0), at=t1, ts=TS2)
    history = load_live_history(p1.parent)
    packs = _of(history, "pack")
    assert [(p["pack_dir_name"], p["previous_pack_timestamp"], p["has_observations"]) for p in packs] == [
        ("p1", None, True), ("p2", TS1, True),
    ]
    assert packs[0]["corridor_nm"] == 30.0 and packs[0]["sigmet_corridor_nm"] == 50.0
    assert _events(history) == [
        ("09:00", "appeared", "metar:ZZDS", "IFR"),
        ("09:10", "cleared", "metar:ZZDS", "IFR"),
    ]
    assert _of(history, "event")[1]["pack_timestamp"] == TS2


def test_refused_write_records_nothing(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    _commit(pack, briefing, _obs("IFR", NOW), at=NOW)
    before = (pack.parent / LIVE_HISTORY_FILE).read_text()
    # Computed before the stored write landed: dropped, and not in history.
    assert commit_live_update(
        pack, briefing_data=briefing, observations=_obs("LIFR", NOW), sigmets=None, observed=None,
        started_at=NOW - timedelta(minutes=1), pack_timestamp=TS1, now=NOW + timedelta(minutes=5),
    ) is None
    assert (pack.parent / LIVE_HISTORY_FILE).read_text() == before


def test_history_starting_mid_flight_records_what_is_on_screen(tmp_path):
    """A flight already live when the history shipped: no history file yet,
    so the changes on screen are recorded as appearing on the first write."""
    pack, briefing = _pack(tmp_path, "p1")
    _commit(pack, briefing, _obs("IFR", NOW), at=NOW)
    (pack.parent / LIVE_HISTORY_FILE).unlink()
    _commit(pack, briefing, _obs("IFR", NOW), at=NOW + timedelta(minutes=10))
    assert _events(load_live_history(pack.parent)) == [("09:10", "appeared", "metar:ZZDS", "IFR")]


def test_history_write_failure_does_not_fail_the_commit(tmp_path, monkeypatch, caplog):
    pack, briefing = _pack(tmp_path, "p1")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(live_layer, "_append_history", boom)
    layer = _commit(pack, briefing, _obs("IFR", NOW), at=NOW)
    assert layer is not None and load_live(pack.parent).changes.changes
    assert "Live history write failed" in caplog.text


def test_events_of_a_failed_history_write_land_on_the_next_tick(tmp_path, monkeypatch):
    """The diff is against what the history last recorded, not the stored
    layer: a tick whose write failed shows up late, not never."""
    pack, briefing = _pack(tmp_path, "p1")
    _commit(pack, briefing, _obs("VFR", NOW), at=NOW)
    real = live_layer._append_history

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(live_layer, "_append_history", boom)
    _commit(pack, briefing, _obs("IFR", NOW + timedelta(minutes=10)), at=NOW + timedelta(minutes=10))
    monkeypatch.setattr(live_layer, "_append_history", real)
    _commit(pack, briefing, _obs("IFR", NOW + timedelta(minutes=10)), at=NOW + timedelta(minutes=20))
    assert _events(load_live_history(pack.parent)) == [("09:20", "appeared", "metar:ZZDS", "IFR")]


def test_corrected_metar_with_the_same_observation_time_is_recorded(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    _commit(pack, briefing, _obs("VFR", NOW), at=NOW)
    _commit(pack, briefing, _obs("IFR", NOW, report_type="METAR COR"), at=NOW + timedelta(minutes=10))
    metars = [r for r in _of(load_live_history(pack.parent), "report")
              if r["kind"] == "metar" and r["observed_at"] == NOW.isoformat()]
    assert [m["raw"].endswith(cat) for m, cat in zip(metars, ("VFR", "IFR"))] == [True, True]


def test_truncated_line_is_skipped_and_not_glued_to_the_next(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    _commit(pack, briefing, _obs("IFR", NOW), at=NOW)
    path = pack.parent / LIVE_HISTORY_FILE
    with path.open("a") as f:
        f.write('{"type": "event", "tick_')  # a write cut short
    _commit(pack, briefing, _obs("VFR", NOW + timedelta(minutes=10)), at=NOW + timedelta(minutes=10))
    assert _events(load_live_history(pack.parent)) == [
        ("09:00", "appeared", "metar:ZZDS", "IFR"),
        ("09:10", "cleared", "metar:ZZDS", "IFR"),
    ]


def test_load_live_history_absent_is_empty(tmp_path):
    assert load_live_history(tmp_path) == []


def test_replay_serves_the_latest_taf_known_at_the_tick():
    """The builder stores one observation per (METAR, TAF); a tick sees the
    latest whose METAR and TAF had both been issued."""
    t = [NOW + timedelta(minutes=m) for m in (0, 20, 30)]
    obs = [
        _obs("VFR", t[0], taf=TAF, taf_t=t[0] - timedelta(hours=3)),
        _obs("VFR", t[0], taf=TAF + " TEMPO 0110/0114 BKN008", taf_t=t[1]),
        _obs("MVFR", t[2], taf=TAF + " TEMPO 0110/0114 BKN008", taf_t=t[1]),
    ]
    scenario = {
        "inputs": {"corridor_nm": 30.0},
        "derived": {"corridor": ["ZZDS"], "observations": {
            "ZZDS": [o.airports[0].model_dump(mode="json") for o in obs],
        }},
    }
    served = [observations_at(scenario, NOW + timedelta(minutes=m)) for m in (10, 20, 30)]
    assert [s.airports[0].taf_issue_time for s in served] == [t[0] - timedelta(hours=3), t[1], t[1]]
    assert [s.airports[0].metar_flight_category for s in served] == ["VFR", "VFR", "MVFR"]
    assert served[0].airports_with_taf == 1


# --- The LELL → LEMI morning, through the history ---------------------------


@pytest.fixture(scope="module")
def lell_lemi_history(tmp_path_factory):
    flight_dir = tmp_path_factory.mktemp("live") / "u" / "flight"
    ticks = replay(load_scenario(LELL_LEMI), flight_dir)
    return flight_dir, ticks


def _history_timeline(history):
    return [
        (r["tick_at"][11:16], "+" if r["event"] == "appeared" else "-", r["change"]["tier"], r["change"]["message"])
        for r in _of(history, "event")
    ]


def test_lell_lemi_history_is_the_pinned_timeline(lell_lemi_history):
    flight_dir, ticks = lell_lemi_history
    history = load_live_history(flight_dir)
    assert _history_timeline(history) == EXPECTED_LELL_LEMI == timeline(ticks)
    assert [p["pack_timestamp"] for p in _of(history, "pack")] == [
        "2026-09-30T16:31:25+00:00", "2026-10-02T06:52:55+00:00",
    ]


def test_lell_lemi_history_size(lell_lemi_history):
    flight_dir, _ = lell_lemi_history
    assert (flight_dir / LIVE_HISTORY_FILE).stat().st_size < 100_000


def _builder():
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    import build_live_scenario

    return build_live_scenario


def test_lell_lemi_inputs_rebuilt_from_history(lell_lemi_history):
    """Every report the history kept parses back to the very report the
    scenario was built from; packs, window and route come back too."""
    flight_dir, _ = lell_lemi_history
    original = load_scenario(LELL_LEMI)["inputs"]
    rebuilt = _builder().inputs_from_history(flight_dir)

    def strip(m):
        return {k: v for k, v in m.items() if k != "source"}

    by_key = {(m["icao"], m["observation_time"]): strip(m) for m in original["metars"]}
    assert rebuilt["metars"]
    for m in rebuilt["metars"]:
        assert strip(m) == by_key[(m["icao"], m["observation_time"])]
    assert {s["report"]["raw_text"] for s in rebuilt["sigmets"]} <= {
        s["report"]["raw_text"] for s in original["sigmets"]
    }
    for key in ("route", "departure_time", "alternates", "corridor_nm", "sigmet_corridor_nm"):
        assert rebuilt[key] == original[key], key
    assert rebuilt["window"] == original["window"]
    assert [(p["timestamp"], p["has_observations"]) for p in rebuilt["packs"]] == [
        (p["timestamp"], p["has_observations"]) for p in original["packs"]
    ]


@pytest.mark.skipif(not os.environ.get("AIRPORTS_DB"), reason="needs the airport database")
def test_lell_lemi_round_trip_reproduces_the_timeline(lell_lemi_history, tmp_path):
    """History → inputs → derived → replay gives the same timeline."""
    flight_dir, _ = lell_lemi_history
    builder = _builder()
    inputs = builder.inputs_from_history(flight_dir)
    scenario = {"inputs": inputs, "derived": builder.build_derived(inputs, os.environ["AIRPORTS_DB"])}
    assert timeline(replay(scenario, tmp_path / "u" / "flight")) == EXPECTED_LELL_LEMI
