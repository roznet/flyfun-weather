"""Per-flight live store (#637): baseline switch, merge, overlay, retention."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.models.observations import (
    AirportObservation,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.tasks.live_layer import (
    commit_live_update,
    live_for_pack,
    live_updated_at_for_pack,
    load_briefing_with_live,
    load_live,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ROUTE = {
    "name": "ZZ test",
    "waypoints": [
        {"icao": "ZZDP", "name": "Dep", "lat": 50.0, "lon": 1.0},
        {"icao": "ZZDS", "name": "Dest", "lat": 51.0, "lon": 2.0},
    ],
    "cruise_altitude_ft": 6000,
    "flight_duration_hours": 1.0,
}


def _obs(cat, t, *, prev=None, prev_t=None):
    return RouteObservations(
        corridor_nm=30.0, fetch_time=t, airports_found=1, airports_with_metar=1,
        airports_with_taf=0,
        airports=[AirportObservation(
            icao="ZZDS", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZDS",
            metar_flight_category=cat, metar_time=t, metar_report_type="METAR",
            metar_previous_flight_category=prev, metar_previous_time=prev_t,
        )],
    )


def _pack(tmp_path: Path, name: str, base_cat="VFR") -> tuple[Path, dict]:
    pack_dir = tmp_path / "user" / "flight-zz" / name
    pack_dir.mkdir(parents=True)
    briefing = {
        "route": ROUTE,
        "departure_time": (NOW + timedelta(hours=2)).isoformat(),
        "days_out": 0,
        "route_observations": _obs(base_cat, NOW - timedelta(hours=1)).model_dump(mode="json"),
    }
    (pack_dir / "briefing.json").write_text(json.dumps(briefing))
    return pack_dir, briefing


def _commit(pack_dir, briefing, obs, *, ts, started=None, now=None, sigmets=None):
    now = now or NOW
    return commit_live_update(
        pack_dir, briefing_data=briefing, observations=obs, sigmets=sigmets,
        observed=None, started_at=started or now, pack_timestamp=ts, now=now,
    )


def test_confirmed_destination_change_alerts_once(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    t1, t2 = NOW - timedelta(minutes=30), NOW
    layer = _commit(pack, briefing, _obs("IFR", t2, prev="IFR", prev_t=t1), ts="2026-10-01T07:00:00+00:00")
    [c] = layer.changes.changes
    assert c.role == "destination" and c.new_alert
    assert layer.last_refresh_delta.worsened
    again = _commit(
        pack, briefing, _obs("IFR", t2, prev="IFR", prev_t=t1),
        ts="2026-10-01T07:00:00+00:00", now=NOW + timedelta(minutes=10),
    )
    assert again.changes.changes[0].new_alert is False


def test_pack_without_observations_seeds_its_baseline_from_the_first_tick(tmp_path):
    """A pack built the day before carries no observations (they are D-0 only):
    the first live fetch becomes the starting point, without touching the pack."""
    pack, briefing = _pack(tmp_path, "p1")
    del briefing["route_observations"]
    (pack / "briefing.json").write_text(json.dumps(briefing))
    ts = "2026-10-01T07:00:00+00:00"
    t1 = NOW - timedelta(minutes=20)

    first = _commit(pack, briefing, _obs("VFR", t1), ts=ts)
    assert first.changes.changes == []
    assert first.changes.baseline_source == "live_start"
    assert first.changes.baseline_at == NOW
    assert first.seeded_at == NOW

    later = NOW + timedelta(minutes=30)
    second = _commit(pack, briefing, _obs("IFR", later), ts=ts, now=later)
    [c] = second.changes.changes
    assert (c.from_value, c.to_value) == ("VFR", "IFR") and c.tier == "alert"
    assert second.changes.baseline_at == NOW  # the starting point does not move
    assert "route_observations" not in json.loads((pack / "briefing.json").read_text())


def test_pack_with_observations_is_its_own_baseline(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    layer = _commit(pack, briefing, _obs("VFR", NOW), ts="2026-10-01T07:00:00+00:00")
    assert layer.changes.baseline_source == "briefing"
    assert layer.seeded_observations is None


def test_none_block_keeps_previous_value(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    sig = RouteSigmets(corridor_nm=50, fetch_time=NOW, sigmets=[SigmetAlongRoute(fir_id="ZZZZ")])
    first = _commit(pack, briefing, _obs("VFR", NOW), ts="2026-10-01T07:00:00+00:00", sigmets=sig)
    later = NOW + timedelta(minutes=10)
    second = _commit(pack, briefing, _obs("VFR", later), ts="2026-10-01T07:00:00+00:00", now=later)
    assert second.route_sigmets.count == 1
    assert second.sigmets_updated_at == first.sigmets_updated_at
    assert second.observations_updated_at == later


def test_write_for_an_older_pack_is_refused(tmp_path):
    new_pack, new_briefing = _pack(tmp_path, "p2")
    old_pack, old_briefing = _pack(tmp_path, "p1")
    assert _commit(new_pack, new_briefing, _obs("VFR", NOW), ts="2026-10-01T08:00:00+00:00")
    stale = _commit(old_pack, old_briefing, _obs("IFR", NOW), ts="2026-10-01T07:00:00+00:00")
    assert stale is None
    assert load_live(new_pack.parent).pack_dir_name == "p2"
    assert live_for_pack(old_pack) is None


def test_new_pack_resets_baseline_and_alert_memory(tmp_path):
    p1, b1 = _pack(tmp_path, "p1")
    t1, t2 = NOW - timedelta(minutes=30), NOW
    _commit(p1, b1, _obs("IFR", t2, prev="IFR", prev_t=t1), ts="2026-10-01T07:00:00+00:00")
    # A full refresh lands p2, whose own briefing already saw IFR.
    p2, b2 = _pack(tmp_path, "p2", base_cat="IFR")
    layer = _commit(
        p2, b2, _obs("IFR", t2, prev="IFR", prev_t=t1),
        ts="2026-10-01T08:00:00+00:00", now=NOW + timedelta(minutes=1),
    )
    assert layer.pack_dir_name == "p2"
    assert layer.changes.changes == []
    assert layer.alerted == {}


def test_write_started_before_a_newer_commit_is_dropped(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    ts = "2026-10-01T07:00:00+00:00"
    _commit(pack, briefing, _obs("VFR", NOW), ts=ts, now=NOW + timedelta(minutes=5))
    slow = _commit(
        pack, briefing, _obs("IFR", NOW), ts=ts,
        started=NOW, now=NOW + timedelta(minutes=6),
    )
    assert slow is None


def test_overlay_and_meta(tmp_path):
    pack, briefing = _pack(tmp_path, "p1")
    layer = _commit(pack, briefing, _obs("MVFR", NOW), ts="2026-10-01T07:00:00+00:00")
    data = load_briefing_with_live(pack)
    assert data["route_observations"]["airports"][0]["metar_flight_category"] == "MVFR"
    assert data["live_updated_at"] == layer.live_updated_at.isoformat()
    # On disk the pack is still the briefing's view.
    on_disk = json.loads((pack / "briefing.json").read_text())
    assert on_disk["route_observations"]["airports"][0]["metar_flight_category"] == "VFR"
    assert live_updated_at_for_pack(pack) == layer.live_updated_at


def test_corrupt_live_file_is_ignored(tmp_path):
    pack, _ = _pack(tmp_path, "p1")
    (pack.parent / "live.json").write_text("{not json")
    assert load_live(pack.parent) is None
    assert load_briefing_with_live(pack)["route_observations"] is not None


def test_retention_purges_live_layer(tmp_path):
    from weatherbrief.tasks.retention import _purge_live_layer

    pack, briefing = _pack(tmp_path, "p1")
    _commit(pack, briefing, _obs("VFR", NOW), ts="2026-10-01T07:00:00+00:00")
    other, _ = _pack(tmp_path, "p0")
    assert _purge_live_layer(other, dry_run=False) == 0  # not this pack's layer
    assert _purge_live_layer(pack, dry_run=True) > 0
    assert (pack.parent / "live.json").exists()
    assert _purge_live_layer(pack, dry_run=False) > 0
    assert not (pack.parent / "live.json").exists()
