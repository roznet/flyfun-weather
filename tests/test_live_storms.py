"""Radar storms against the route (#688, meteorology-decisions §41).

Synthetic route ZZDP (50 N, 0 E) → ZZDS (50 N, 4 E): due east, ~154 NM,
1.5 h at constant speed (~103 kt), departing 12:00Z. North of the track is
left, south is right.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.models.analysis import RouteConfig, Waypoint
from weatherbrief.models.live import LiveStorms
from weatherbrief.models.observations import AirportObservation, RouteObservations
from weatherbrief.models.observed import (
    ObservedAnnulus,
    ObservedConditions,
    ObservedField,
    ObservedStationRef,
    ObservedStationSamples,
)
from weatherbrief.observed.storms import CellFrames, Schedule, build_storms, group_storms, load_cell_frames
from weatherbrief.tasks.live_significance import classify_changes

DEP = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
ROUTE = RouteConfig(
    name="ZZDP-ZZDS",
    waypoints=[Waypoint(icao="ZZDP", name="ZZDP", lat=50.0, lon=0.0),
               Waypoint(icao="ZZDS", name="ZZDS", lat=50.0, lon=4.0)],
    flight_duration_hours=1.5,
)
TRACK = RouteTrack.from_route(ROUTE)
SCHEDULE = Schedule(TRACK, DEP, 1.5)
NM_LAT = 1.0 / 60.0  # degrees of latitude per NM
NM_LON = 1.0 / (60.0 * 0.6427876)  # degrees of longitude per NM at 50 N


def at_nm(along: float, cross: float = 0.0) -> tuple[float, float]:
    """(lat, lon) ``along`` NM down the track, ``cross`` NM right of it (south)."""
    return 50.0 - cross * NM_LAT, along * NM_LON


def cell(cid, tier="core41", *, along=50.0, cross=0.0, peak=44.0, trend="steady", flashes=0,
         motion="available", speed=0.0, toward=None, within=None, pending=False):
    lat, lon = at_nm(along, cross)
    c = {
        "id": cid, "tier": tier, "lat": lat, "lon": lon, "area_km2": 60.0, "peak_dbz": peak,
        "rate_peak_mm_h": 20.0, "flashes": None if pending else flashes, "top_fl": None,
        "truncated": False, "age_min": 20.0, "event": "continued",
        "trend": {"state": trend, "window_min": 30.0, "d_peak_db": 0.0, "area_ratio": 1.0, "d_flashes": 0},
        "motion": {"status": motion, "reason": None, "speed_kt": speed if motion == "available" else None,
                   "toward_deg": toward if motion == "available" else None},
        "arrow": None,
    }
    if within:
        c["within"] = within
    if pending:
        c["flashes_pending"] = True
    return c


def frame(t, cells):
    return {"valid_time": t.isoformat(), "cells": cells, "pending": [], "policy_version": "cells-2+abcd"}


def storms_at(now, cells, *, earlier=(), flown=None):
    frames = CellFrames("available", newest=frame(now - timedelta(minutes=5), cells), earlier=list(earlier))
    if flown is None:
        flown = max(0.0, (now - DEP).total_seconds() / 3600 / 1.5) * TRACK.total_nm
    return build_storms(frames, SCHEDULE, flown_nm=flown, now=now, end_icaos=("ZZDP", "ZZDS"))


def rows(storms, *, now, latest_obs=None, observed=None, departure_at=DEP):
    baseline = None
    if latest_obs is not None:
        baseline = _obs(*(a.model_copy(update={"metar_raw": "METAR ZZRT 061130Z 27010KT 9999 FEW040 19/12 Q1013",
                                               "metar_weather": [], "metar_time": DEP - timedelta(minutes=30)})
                          for a in latest_obs.airports))
    changes, _ = classify_changes(
        baseline_obs=baseline, latest_obs=latest_obs,
        baseline_sigmets=None, latest_sigmets=None,
        latest_observed=observed, departure_at=departure_at, now=now, storms=storms,
    )
    return changes.changes


def _obs(*airports):
    return RouteObservations(corridor_nm=30.0, fetch_time=DEP, airports_found=len(airports),
                             airports_with_metar=len(airports), airports_with_taf=0, airports=list(airports))


# --- Geometry ----------------------------------------------------------------


def test_projection_side_along_and_ends():
    lat, lon = at_nm(60, cross=-8)  # 8 NM north = left of an eastbound track
    p = TRACK.project(lat, lon)
    assert p.along_nm == pytest.approx(60, abs=0.5)
    assert p.offtrack_nm == pytest.approx(8, abs=0.2)
    assert p.cross_nm < 0 and p.end is None
    assert TRACK.project(*at_nm(60, cross=5)).cross_nm == pytest.approx(5, abs=0.2)
    before = TRACK.project(50.0, -0.2)
    assert before.end == "departure" and before.along_nm == 0
    past = TRACK.project(50.1, 4.2)
    assert past.end == "destination" and past.along_nm == pytest.approx(TRACK.total_nm)


def test_grouping_follows_within_then_falls_back_to_distance():
    base = cell("core35-a", "core35", along=50)
    inside = cell("core41-a", along=50.5, within="core35-a")
    # An older node's file: no "within", but sits inside core35-a's radius.
    old = cell("core41-b", along=51)
    far = cell("core41-c", along=90)
    rain = cell("rain20-x", "rain20", along=50)
    groups = group_storms([base, inside, old, far, rain])
    assert [[c["id"] for c in g] for g in groups] == [["core35-a", "core41-a", "core41-b"], ["core41-c"]]


def test_storm_geometry_eta_and_relative_motion():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [
        cell("core35-a", "core35", along=60, cross=-8, speed=12, toward=0.0),  # north: away from track
        cell("core35-b", "core35", along=90, cross=6, speed=10, toward=0.0),   # south of track, moving north: closing
        cell("core35-c", "core35", along=120, cross=5, speed=10, toward=90.0),  # moving with the track
        cell("core35-d", "core35", along=40, cross=4, motion="withheld"),
        cell("core35-f", "core35", along=80, cross=-4, speed=0.4),
        cell("core35-e", "core35", along=70, cross=45),  # outside the 30 NM corridor
    ])
    by = {s.id: s for s in st.storms}
    assert set(by) == {"core35-a", "core35-b", "core35-c", "core35-d", "core35-f"}
    assert (by["core35-f"].relative_motion, by["core35-f"].closing_kt) == ("stationary", 0.0)
    a = by["core35-a"]
    assert (a.side, a.relative_motion) == ("left", "moving_away")
    assert a.closing_kt == pytest.approx(-12, abs=0.5)
    expected = DEP + timedelta(hours=1.5 * a.along_nm / TRACK.total_nm)
    assert abs((a.abeam_eta - expected).total_seconds()) < 60
    assert by["core35-b"].relative_motion == "closing" and by["core35-b"].side == "right"
    assert by["core35-c"].relative_motion == "parallel"
    d = by["core35-d"]
    assert d.relative_motion == "unknown" and d.closing_kt is None and d.estimate is None


def test_passed_storm_is_listed_but_not_ahead():
    now = DEP + timedelta(minutes=45)  # ~77 NM flown
    [s] = storms_at(now, [cell("core35-a", "core35", along=40, cross=3)]).storms
    assert not s.ahead
    assert rows(storms_at(now, [cell("core35-a", "core35", along=40, cross=3, trend="developing")]),
                now=now) == []


def test_history_reads_earlier_frames_by_id():
    now = DEP + timedelta(minutes=10)
    earlier = [frame(now - timedelta(minutes=m), [cell("core35-a", "core35", along=60, cross=-d)])
               for m, d in ((35, 3), (25, 5), (15, 7))]
    [s] = storms_at(now, [cell("core35-a", "core35", along=60, cross=-9)], earlier=earlier).storms
    assert [p.offtrack_nm for p in s.history] == pytest.approx([3, 5, 7], abs=0.2)
    assert all(p.cross_nm < 0 for p in s.history)


def test_estimate_closest_approach_at_current_motion():
    now = DEP - timedelta(minutes=30)
    # 10 NM right of the 77 NM point, moving north (toward the track) at 20 kt.
    [s] = storms_at(now, [cell("core35-a", "core35", along=77, cross=10, speed=20, toward=0.0)]).storms
    e = s.estimate
    assert e is not None
    # Abeam 80 min after the frame (12:45Z): 20 kt × 80 min ≈ 26.7 NM north,
    # so 16.7 NM left of track by then. It crosses the track long before the
    # aircraft gets there: the closest approach is no nearer than that.
    assert e.at_eta_offtrack_nm == pytest.approx(16.7, abs=0.5)
    assert e.cpa_nm <= e.at_eta_offtrack_nm
    assert DEP - timedelta(minutes=30) <= e.cpa_time <= s.abeam_eta
    assert e.horizon_min == pytest.approx((e.cpa_time - (now - timedelta(minutes=5))).total_seconds() / 60, abs=0.1)


# --- The alert rule (§41) --------------------------------------------------------


@pytest.mark.parametrize("kw,tier", [
    ({"trend": "developing"}, "alert"),
    ({"flashes": 4}, "alert"),
    ({"trend": "steady"}, "highlight"),
    ({"trend": "decaying"}, "highlight"),
    ({"trend": "developing", "cross": 15}, "highlight"),   # beyond the 10 NM alert band
    ({"trend": "developing", "cross": 8}, "highlight"),    # developing alone alerts only within 5 NM
    ({"flashes": 1, "cross": 8}, "alert"),                 # lightning alerts out to 10 NM
    ({"trend": "developing", "peak": 38.0}, None),          # not heavy, no lightning: no row
    ({"peak": 38.0, "flashes": 2}, "highlight"),            # lightning in a weaker cell
    ({"trend": "developing", "cross": 25}, None),           # beyond the 20 NM highlight band
])
def test_storm_tier(kw, tier):
    now = DEP + timedelta(minutes=10)
    kw = {"along": 50, "cross": 5, **kw}
    got = rows(storms_at(now, [cell("core35-a", "core35", **kw)]), now=now)
    assert [c.tier for c in got if c.kind == "storm"] == ([tier] if tier else [])


def test_closing_counts_only_within_the_motion_horizon():
    now = DEP + timedelta(minutes=10)  # ~17 NM flown; 60 NM/h → 30 min ≈ 51 NM
    near = cell("core35-a", "core35", along=40, cross=6, speed=10, toward=0.0)
    far = cell("core35-b", "core35", along=110, cross=6, speed=10, toward=0.0)
    got = {c.key: c.tier for c in rows(storms_at(now, [near, far]), now=now)}
    assert got == {"storm:core35-a": "alert", "storm:core35-b": "highlight"}


def test_storms_reached_after_an_hour_share_one_highlight():
    now = DEP - timedelta(minutes=40)
    st = storms_at(now, [
        cell("core35-a", "core35", along=100, cross=3, trend="developing"),
        cell("core35-b", "core35", along=130, cross=-6, peak=55.0),
        cell("core35-c", "core35", along=20, cross=2, trend="developing"),  # ~52 min out
    ])
    got = {c.key: c for c in rows(st, now=now)}
    assert set(got) == {"storm:core35-c", "storms:later"}
    later = got["storms:later"]
    assert later.tier == "highlight"
    assert later.message.startswith("Convective activity 100–130 NM along route, reached ~")
    assert later.message.endswith("2 heavy storms, peak 55 dBZ")


def test_storm_row_message_and_identity():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [cell("core35-a", "core35", along=60, cross=8, peak=52.4, trend="developing",
                              speed=11, toward=180.0),
                         cell("core41-a", along=60, cross=8, peak=52.4, within="core35-a")])
    [c] = rows(st, now=now)
    assert (c.key, c.kind, c.source, c.role, c.direction) == ("storm:core35-a", "storm", "RADAR", "route", "worse")
    assert (c.from_value, c.to_value, c.icao) == ("none", "heavy", None)
    assert c.message == "Extreme cell (52 dBZ, developing) 8 NM right of track at 60 NM, abeam ~12:35Z, moving away 11 kt"
    assert c.storm_ids == ["core35-a"]
    # Developing 8 NM off and moving away: a highlight since the calibration.
    assert (c.tier, c.new_alert) == ("highlight", False)


def test_storm_near_destination_is_the_approach():
    now = DEP + timedelta(minutes=50)
    lat, lon = 50.05, 4.1  # just past ZZDS, north-east of it
    c = cell("core35-a", "core35", flashes=3)
    c.update(lat=lat, lon=lon)
    [row] = rows(storms_at(now, [c]), now=now)
    assert (row.role, row.tier) == ("destination", "alert")
    assert "of ZZDS, arrival ~13:30Z" in row.message


def test_lightning_pending_and_withheld_motion_wording():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [cell("core35-a", "core35", along=60, cross=-4, pending=True, motion="withheld")])
    [c] = rows(st, now=now)
    assert c.message == "Heavy cell (44 dBZ, lightning pending) 4 NM left of track at 60 NM, abeam ~12:35Z"


# --- Ring rows and station reports ---------------------------------------------


def _observed(dbz, enroute=60.0):
    return ObservedConditions(
        computed_at=DEP, corridor_nm=20, radii_nm=[5, 10, 20],
        stations=[ObservedStationRef(id="p", lat=50.0, lon=1.0, enroute_distance_nm=enroute)],
        reflectivity=ObservedField(
            source="opera", quantity="dbzh", valid_time=DEP, age_minutes=5,
            stations=[ObservedStationSamples(station_id="p", annuli=[ObservedAnnulus(
                radius_nm=5, total_px=100, valid_px=100, nodata_px=0, max_value=dbz)])],
        ),
    )


def test_cells_replace_the_radar_ring_rows_and_dark_feed_restores_them():
    now = DEP + timedelta(minutes=10)
    base, latest = _observed(20.0), _observed(50.0)
    kw = dict(baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
              baseline_observed=base, latest_observed=latest, flown_nm=10.0, now=now)
    up, _ = classify_changes(**kw, storms=storms_at(now, [cell("core35-a", "core35", along=60, cross=2)]))
    assert [c.kind for c in up.changes] == ["storm"]
    dark, _ = classify_changes(**kw, storms=LiveStorms(status="stale", corridor_nm=30.0))
    assert [c.kind for c in dark.changes] == ["radar"]


def _station(raw, *, enroute=60.0, cross=1.0):
    lat, lon = at_nm(enroute, cross)
    return AirportObservation(
        icao="ZZRT", distance_from_route_nm=abs(cross), enroute_distance_nm=enroute,
        nearest_waypoint_icao="ZZDP", metar_raw=raw, metar_time=DEP,
        metar_flight_category="VFR", lat=lat, lon=lon,
    )


def test_station_cb_backs_the_storm_near_it():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [cell("core35-a", "core35", along=62, cross=4, trend="developing")])
    latest = _obs(_station("METAR ZZRT 061210Z AUTO 27010KT 9999 ///CB 19/17 Q1013"))
    got = rows(st, now=now, latest_obs=latest, observed=_observed(30.0))
    assert [c.key for c in got] == ["storm:core35-a"]
    assert got[0].message.endswith("; ZZRT reports CB")
    assert st.storms[0].backing == ["ZZRT reports CB"]


def test_station_thunderstorm_keeps_its_alert_and_backs_the_storm():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [cell("core35-a", "core35", along=62, cross=4)])
    latest = _obs(_station("METAR ZZRT 061210Z 27010KT 9999 TS FEW030CB 19/17 Q1013"))
    latest.airports[0].metar_weather = ["TS"]
    got = {c.key: c for c in rows(st, now=now, latest_obs=latest, observed=_observed(30.0))}
    assert got["conv:ZZRT"].tier == "alert"
    assert got["storm:core35-a"].message.endswith("; ZZRT reports CB, TS")


def test_station_cb_far_from_any_storm_stays_a_highlight():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [cell("core35-a", "core35", along=120, cross=4)])
    latest = _obs(_station("METAR ZZRT 061210Z AUTO 27010KT 9999 ///CB 19/17 Q1013"))
    got = {c.key: c for c in rows(st, now=now, latest_obs=latest, observed=_observed(30.0))}
    assert got["conv:ZZRT"].tier == "highlight"
    assert got["conv:ZZRT"].message == "ZZRT METAR: CB reported"


def test_station_cb_beside_a_storm_without_its_own_row_keeps_its_row():
    """A 38 dBZ storm (no lightning) gets no row, so it absorbs nothing: the
    CB report stays a highlight instead of vanishing (follow-up to #692)."""
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [cell("core35-a", "core35", along=62, cross=4, peak=38.0)])
    latest = _obs(_station("METAR ZZRT 061210Z AUTO 27010KT 9999 ///CB 19/17 Q1013"))
    got = {c.key: c for c in rows(st, now=now, latest_obs=latest, observed=_observed(30.0))}
    assert "storm:core35-a" not in got
    assert got["conv:ZZRT"].tier == "highlight"
    assert st.storms[0].backing == []


def test_passed_storm_has_no_estimate():
    now = DEP + timedelta(minutes=30)
    st = storms_at(now, [cell("core35-a", "core35", along=10, cross=3, speed=20.0, toward=90.0)], flown=40.0)
    assert not st.storms[0].ahead
    assert st.storms[0].estimate is None


# --- The feed, the layer and the history ----------------------------------------


def _write_display(root, t, cells):
    import gzip

    from weatherbrief.observed.cells.display import DISPLAY_SCHEMA
    from weatherbrief.observed.frames import frame_stamp

    doc = {"schema": DISPLAY_SCHEMA, "policy_version": "cells-2+abcd1234", "valid_time": t.isoformat(),
           "outlines": {}, "cells": cells, "pending": []}
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{frame_stamp(t)}.json.gz").write_bytes(gzip.compress(json.dumps(doc).encode()))


def test_load_cell_frames_newest_stale_and_history(tmp_path):
    from weatherbrief.observed.cells_display import DisplayStore

    store = DisplayStore(tmp_path)
    assert load_cell_frames(DEP, store).status == "unavailable"
    for m in (0, 10, 20, 30):
        _write_display(tmp_path, DEP + timedelta(minutes=m), [cell("core35-a", "core35")])
    f = load_cell_frames(DEP + timedelta(minutes=32), store)
    assert f.status == "available" and f.newest["valid_time"] == (DEP + timedelta(minutes=30)).isoformat()
    assert [e["valid_time"][11:16] for e in f.earlier] == ["12:00", "12:10", "12:20"]
    # A frame from after "now" is never used (replay as of a tick).
    assert load_cell_frames(DEP + timedelta(minutes=12), store).newest["valid_time"][11:16] == "12:10"
    stale = load_cell_frames(DEP + timedelta(minutes=60), store)
    assert stale.status == "stale" and stale.unavailable_since == DEP + timedelta(minutes=30)


def test_load_cell_frames_disabled_without_ingest(monkeypatch):
    monkeypatch.delenv("WB_CELLS_INGEST_ENABLED", raising=False)
    assert load_cell_frames(DEP).status == "disabled"


def test_commit_stores_storms_and_logs_each_estimate_once(tmp_path):
    from weatherbrief.tasks.live_layer import commit_live_update, load_live, load_live_history

    pack = tmp_path / "u" / "flight" / "2026-10-06T10-00-00+00-00"
    pack.mkdir(parents=True)
    briefing = {"route": ROUTE.model_dump(mode="json"), "departure_time": DEP.isoformat(), "days_out": 0}
    (pack / "briefing.json").write_text(json.dumps(briefing))
    moving = cell("core35-a", "core35", along=60, cross=6, speed=10, toward=0.0, trend="developing")
    frames = CellFrames("available", newest=frame(DEP - timedelta(minutes=5), [moving]))
    for minutes in (0, 5):  # a second write on the same cell frame (a ↻)
        now = DEP + timedelta(minutes=minutes)
        commit_live_update(pack, briefing_data=briefing, observations=None, sigmets=None, observed=None,
                           started_at=now, pack_timestamp="2026-10-06T10:00:00+00:00", now=now, cells=frames)
    layer = load_live(pack.parent)
    assert layer.storms.status == "available" and [s.id for s in layer.storms.storms] == ["core35-a"]
    assert [c.key for c in layer.changes.changes] == ["storm:core35-a"]
    history = load_live_history(pack.parent)
    estimates = [r for r in history if r["type"] == "estimate"]
    assert len(estimates) == 1
    e = estimates[0]
    assert e["storm_id"] == "core35-a" and e["frame_time"] == (DEP - timedelta(minutes=5)).isoformat()
    assert {"cpa_nm", "cpa_time", "at_eta_offtrack_nm", "horizon_min", "speed_kt", "toward_deg"} <= set(e)


def test_commit_without_cells_is_a_dark_feed(tmp_path):
    from weatherbrief.tasks.live_layer import commit_live_update, load_live

    pack = tmp_path / "u" / "flight" / "2026-10-06T10-00-00+00-00"
    pack.mkdir(parents=True)
    briefing = {"route": ROUTE.model_dump(mode="json"), "departure_time": DEP.isoformat(), "days_out": 0}
    commit_live_update(pack, briefing_data=briefing, observations=None, sigmets=None, observed=None,
                       started_at=DEP, pack_timestamp="2026-10-06T10:00:00+00:00", now=DEP)
    assert load_live(pack.parent).storms.status == "unavailable"


def test_score_estimates_joins_the_logged_estimate_with_later_frames(tmp_path):
    """review.py score-estimates (scripts/replay_live_history.py): a storm
    that moved exactly as estimated scores ~0 NM; persistence does worse."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from replay_live_history import as_of_store, score_estimates

    from weatherbrief.observed.storms import _move
    from weatherbrief.tasks.live_layer import commit_live_update

    cells_dir = tmp_path / "cells"
    t0 = DEP - timedelta(minutes=20)
    start = at_nm(60, cross=12)

    def storm_at(t):
        lat, lon = _move(*start, 0.0, 20.0 * (t - t0).total_seconds() / 3600)  # north, 20 kt
        c = cell("core35-a", "core35", speed=20.0, toward=0.0)
        c.update(lat=lat, lon=lon)
        return c

    for m in range(0, 120, 5):
        _write_display(cells_dir, t0 + timedelta(minutes=m), [storm_at(t0 + timedelta(minutes=m))])

    flight = tmp_path / "live" / "u" / "flight"
    pack = flight / "2026-10-06T10-00-00+00-00"
    pack.mkdir(parents=True)
    briefing = {"route": ROUTE.model_dump(mode="json"), "departure_time": DEP.isoformat(), "days_out": 0}
    (pack / "briefing.json").write_text(json.dumps(briefing))
    store = as_of_store(cells_dir)
    now = t0 + timedelta(minutes=1)
    store.now = None  # files written just now: no mtime gating in this test
    commit_live_update(pack, briefing_data=briefing, observations=None, sigmets=None, observed=None,
                       started_at=now, pack_timestamp="2026-10-06T10:00:00+00:00", now=now,
                       cells=load_cell_frames(now, store))

    report = score_estimates(tmp_path / "live", cells_dir, [])
    scored = [v for v in report.values() if v["n"]]
    assert len(scored) == 1 and scored[0]["n"] == 1 and scored[0]["lost"] == 0
    assert scored[0]["median_cpa_err_nm"] <= 1.5
    assert scored[0]["median_persist_err_nm"] > scored[0]["median_cpa_err_nm"] + 3

# --- Calibration (2026-10-06 replay): one row per cluster, one alert per storm ---


def test_nearby_storms_share_one_row_named_after_the_oldest():
    now = DEP + timedelta(minutes=10)
    st = storms_at(now, [
        cell("core35-20261006T1200-0002", "core35", along=60, cross=3, flashes=2),
        cell("core35-20261006T1150-0001", "core35", along=75, cross=-6, peak=55.0, trend="developing"),
        cell("core35-20261006T1205-0003", "core35", along=105, cross=4, peak=45.0),  # 30 NM gap: own row
    ])
    got = {c.key: c for c in rows(st, now=now) if c.kind == "storm"}
    assert set(got) == {"storm:core35-20261006T1150-0001", "storm:core35-20261006T1205-0003"}
    cluster = got["storm:core35-20261006T1150-0001"]
    assert cluster.tier == "alert" and cluster.new_alert
    assert cluster.storm_ids == ["core35-20261006T1200-0002", "core35-20261006T1150-0001"]
    assert cluster.message.startswith("2 cells (peak 55 dBZ, developing, 2 flashes) 3–6 NM either side of track at 60–75 NM")
    assert "; nearest 3 NM right of track at 60 NM" in cluster.message


def test_a_storm_alerts_once_through_tier_bounces_and_regrouping():
    from weatherbrief.tasks.live_significance import STORM_ALERTED_PREFIX

    t0 = DEP + timedelta(minutes=10)
    kw = dict(baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None, flown_nm=10.0)
    lit = cell("core35-a", "core35", along=100, cross=3, flashes=2)
    first, mem = classify_changes(**kw, now=t0, storms=storms_at(t0, [lit]))
    assert [c.new_alert for c in first.changes] == [True]
    assert STORM_ALERTED_PREFIX + "core35-a" in mem.alerted
    # Lightning stops: highlight. Then it flashes again, now clustered with a
    # newer storm under another key: no second alert for core35-a.
    t1 = t0 + timedelta(minutes=10)
    quiet, mem = classify_changes(**kw, now=t1, memory=mem,
                                  storms=storms_at(t1, [cell("core35-a", "core35", along=100, cross=3)]))
    assert [c.tier for c in quiet.changes] == ["highlight"]
    t2 = t1 + timedelta(minutes=10)
    again, mem = classify_changes(**kw, now=t2, memory=mem, storms=storms_at(t2, [
        cell("core35-a", "core35", along=100, cross=3, flashes=1),
        cell("core35-0", "core35", along=110, cross=2),  # older id: the cluster's new anchor
    ]))
    [row] = again.changes
    assert (row.key, row.tier, row.new_alert) == ("storm:core35-0", "alert", False)
    # A different storm meeting the rule, in another stretch, does alert.
    t3 = t2 + timedelta(minutes=10)
    new, _ = classify_changes(**kw, now=t3, memory=mem, storms=storms_at(t3, [
        cell("core35-a", "core35", along=100, cross=3, flashes=1),
        cell("core35-b", "core35", along=140, cross=1, flashes=3),
    ]))
    assert sorted(c.new_alert for c in new.changes) == [False, True]


def test_new_cells_in_a_stretch_that_already_alerted_do_not_alert_again():
    t0 = DEP + timedelta(minutes=10)
    kw = dict(baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None)
    first, mem = classify_changes(**kw, now=t0, storms=storms_at(t0, [
        cell("core35-a", "core35", along=100, cross=3, flashes=2)]))
    assert [c.new_alert for c in first.changes] == [True]
    # The first cell decays; a new one fires 8 NM further along: same stretch.
    t1 = t0 + timedelta(minutes=10)
    same, mem = classify_changes(**kw, now=t1, memory=mem, storms=storms_at(t1, [
        cell("core35-b", "core35", along=108, cross=2, flashes=4)]))
    assert [(c.tier, c.new_alert) for c in same.changes] == [("alert", False)]
    # A cell far down the route is a different phenomenon: it alerts.
    t2 = t1 + timedelta(minutes=5)
    far, _ = classify_changes(**kw, now=t2, memory=mem, storms=storms_at(t2, [
        cell("core35-b", "core35", along=108, cross=2, flashes=4),
        cell("core35-c", "core35", along=135, cross=1, flashes=1)]))
    assert sorted(c.new_alert for c in far.changes) == [False, True]


def test_suppressed_rows_do_not_extend_the_alerted_stretch():
    """Only a stretch that pinged is remembered: a line creeping along the route
    in suppressed steps cannot chain the suppression (review, #694)."""
    from weatherbrief.tasks.live_significance import STORM_SPAN_PREFIX

    t0 = DEP + timedelta(minutes=10)
    kw = dict(baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None)
    _, mem = classify_changes(**kw, now=t0, storms=storms_at(t0, [cell("core35-a", "core35", along=100, cross=3, flashes=2)]))
    t1 = t0 + timedelta(minutes=5)
    step, mem = classify_changes(**kw, now=t1, memory=mem, storms=storms_at(t1, [cell("core35-b", "core35", along=110, cross=2, flashes=1)]))
    assert [(c.tier, c.new_alert) for c in step.changes] == [("alert", False)]
    assert [k for k in mem.alerted if k.startswith(STORM_SPAN_PREFIX)] == [STORM_SPAN_PREFIX + "100:100"]
    t2 = t1 + timedelta(minutes=5)
    beyond, _ = classify_changes(**kw, now=t2, memory=mem, storms=storms_at(t2, [cell("core35-c", "core35", along=118, cross=2, flashes=1)]))
    assert [c.new_alert for c in beyond.changes] == [True]  # 18 NM past the pinged stretch

