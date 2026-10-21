"""Area outlook (#836): selection, grading, aggregation, pairing, endpoints."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.airport_consensus import consensus as map_consensus
from weatherbrief.tasks import area_outlook as ao

# Fictional-but-plausible watchlist around a base at 51.0N 0.0E.
BASE = ("ZZBA", 51.0, 0.0)
COORDS = {
    "ZZBA": (51.0, 0.0),
    "ZZNO": (51.5, 0.0),     # 30 nm N
    "ZZEA": (51.0, 0.8),     # ~30 nm E
    "ZZSO": (50.5, 0.0),     # 30 nm S
    "ZZWE": (51.0, -0.8),    # ~30 nm W
    "ZZFA": (53.0, 0.0),     # 120 nm N, outside 60 nm
    "ZZDE": (51.0, 2.0),     # destination ~75 nm E
    "ZZMI": (51.05, 1.0),    # near the BA→DE line
}
SUMMER_NOON = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)


def _model(cat="VFR", ceiling=4000.0, vis=20000.0, wind=8.0, wdir=240.0, gust=15.0,
           xwind=5.0, risk="none", alt=False, fzl=6000.0, temp=12.0):
    return {
        "flight_category": cat, "ceiling_ft": ceiling, "visibility_m": vis,
        "wind_speed_kt": wind, "wind_dir_deg": wdir, "wind_gust_kt": gust,
        "crosswind_kt": xwind, "headwind_kt": 5.0, "cape_jkg": 10.0,
        "cloud_cover_pct": 50.0, "convective_risk": risk,
        "alt_required": {"faa": alt, "easa": alt},
        "freezing_level_ft": fzl, "temperature_c": temp,
    }


def _entry(icao, models=None, approach=None, **kw):
    lat, lon = COORDS[icao]
    models = models or {m: _model(**kw) for m in ("gfs", "icon", "ecmwf")}
    return {
        "icao": icao, "lat": lat, "lon": lon, "approach_type": approach,
        "models": models,
        "consensus": map_consensus(models, "worst"),
        "consensus_majority": map_consensus(models, "majority"),
    }


def _slot(entries):
    return {"model_init_times": {"gfs": "x", "icon": "x", "ecmwf": "x"},
            "airports": entries}


def _vfr():
    return ao.minima_for("vfr")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def test_sector_for_quadrants():
    assert [ao.sector_for(b) for b in (0, 44, 46, 134, 136, 224, 226, 314, 316)] == [
        "N", "N", "E", "E", "S", "S", "W", "W", "N",
    ]


def test_select_radius_within_radius_sectored_and_excludes_base():
    base = ao.Place(*BASE)
    picked = ao.select_radius(base, 60, COORDS, exclude={"ZZDE"})
    icaos = {c.icao for c in picked}
    assert "ZZBA" not in icaos and "ZZFA" not in icaos and "ZZDE" not in icaos
    assert {c.sector for c in picked if c.icao in {"ZZNO", "ZZEA", "ZZSO", "ZZWE"}} == {"N", "E", "S", "W"}
    assert [c.distance_nm for c in picked] == sorted(c.distance_nm for c in picked)


def test_select_radius_cap_keeps_every_sector():
    # 10 airports bunched to the north, one in each other sector.
    coords = {f"ZN{i:02d}": (51.1 + i * 0.02, 0.0) for i in range(10)}
    coords.update({"ZZEA": COORDS["ZZEA"], "ZZSO": COORDS["ZZSO"], "ZZWE": COORDS["ZZWE"]})
    picked = ao.select_radius(ao.Place(*BASE), 60, coords, cap=6)
    assert len(picked) == 6
    assert {c.sector for c in picked} == {"N", "E", "S", "W"}


def test_select_corridor_orders_along_track_and_skips_beyond_ends():
    pts = [COORDS["ZZBA"], COORDS["ZZDE"]]
    found = ao.select_corridor(pts, 20, COORDS, exclude={"ZZBA", "ZZDE"})
    icaos = [c.icao for c in found]
    assert icaos[:2] == ["ZZEA", "ZZMI"]  # ordered by along-track distance
    assert "ZZWE" not in icaos  # behind the departure
    assert "ZZNO" not in icaos  # 30 nm off track


def test_nearest_monitored_substitution_flags_unrepresentative():
    near = ao.nearest_monitored(51.0, 0.5, COORDS)
    assert near[0] == "ZZEA"
    p = ao._place("ZZXX", 52.0, 0.0, COORDS)
    d = p.describe()
    assert d["icao"] == "ZZNO" and d["requested"] == "ZZXX" and d["unrepresentative"] is True


# ---------------------------------------------------------------------------
# Grading
# ---------------------------------------------------------------------------


def _grade(entry, rules="vfr", mode="worst", when=SUMMER_NOON, minima=None):
    return ao.grade_airport(entry, rules=rules, mode=mode,
                            minima=minima or ao.minima_for(rules), valid_time=when)


def test_vfr_bands():
    assert _grade(_entry("ZZBA"))["grade"] == ao.PROMISING
    assert _grade(_entry("ZZBA", cat="MVFR", ceiling=1200.0))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", cat="IFR", ceiling=800.0))["grade"] == ao.UNLIKELY
    assert _grade(_entry("ZZBA", vis=4000.0, cat="MVFR"))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", vis=2000.0, cat="IFR"))["grade"] == ao.UNLIKELY


def test_wind_thresholds_come_from_airport_wind_advisory():
    from weatherbrief.analysis.advisories.airport_wind import AirportWindEvaluator

    defaults = {p.key: p.default for p in AirportWindEvaluator.catalog_entry().parameters}
    m = ao.minima_for("vfr")
    assert m.crosswind_green_kt == defaults["crosswind_green_kt"]
    assert m.crosswind_red_kt == defaults["crosswind_red_kt"]
    assert m.gust_red_kt == defaults["gust_red_kt"]
    assert _grade(_entry("ZZBA", xwind=m.crosswind_green_kt))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", xwind=m.crosswind_red_kt))["grade"] == ao.UNLIKELY
    assert _grade(_entry("ZZBA", gust=m.gust_red_kt, wind=20.0))["grade"] == ao.UNLIKELY


def test_overrides_move_lines_without_inverting_band():
    m = ao.minima_for("vfr", min_ceiling_ft=800, max_crosswind_kt=10)
    assert m.ceiling_promising_ft == 800 and m.ceiling_floor_ft == 800
    assert m.crosswind_red_kt == 10 and m.crosswind_green_kt <= 10
    g = _grade(_entry("ZZBA", cat="IFR", ceiling=900.0), minima=m)
    assert g["grade"] == ao.PROMISING


def test_convective_risk():
    assert _grade(_entry("ZZBA", risk="marginal"))["grade"] == ao.PROMISING
    assert _grade(_entry("ZZBA", risk="low"))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", risk="moderate"))["grade"] == ao.UNLIKELY


def test_vfr_night_is_labelled_night_ifr_only_flagged():
    winter_dawn = datetime(2026, 12, 21, 6, tzinfo=timezone.utc)
    g = _grade(_entry("ZZBA"), when=winter_dawn)
    assert g["grade"] == ao.NIGHT and "night" in g["reasons"]
    # NIGHT overrides the weather grade; the weather reasons still show.
    g = _grade(_entry("ZZBA", cat="IFR", ceiling=800.0), when=winter_dawn)
    assert g["grade"] == ao.NIGHT and "ceiling 800ft" in g["reasons"]
    g = _grade(_entry("ZZBA", approach="ILS"), rules="ifr", when=winter_dawn)
    assert g["grade"] == ao.PROMISING and g["phase"] == "night"


def test_ifr_uses_approach_minima_and_alternate_trigger():
    # ILS: UNLIKELY below the class's worst-case DH (300 ft).
    assert _grade(_entry("ZZBA", approach="ILS", cat="LIFR", ceiling=250.0, alt=True),
                  rules="ifr")["grade"] == ao.UNLIKELY
    # Above minima but an alternate would be required → MARGINAL.
    assert _grade(_entry("ZZBA", approach="ILS", cat="IFR", ceiling=700.0, alt=True),
                  rules="ifr")["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", approach="ILS", cat="MVFR", ceiling=2500.0, alt=False),
                  rules="ifr")["grade"] == ao.PROMISING
    # No approach → graded on the VFR lines.
    assert _grade(_entry("ZZBA", approach=None, cat="IFR", ceiling=800.0),
                  rules="ifr")["grade"] == ao.UNLIKELY


def test_confidence_from_agreement_and_model_count():
    split = {"gfs": _model(), "icon": _model(cat="IFR", ceiling=800.0),
             "ecmwf": _model(cat="LIFR", ceiling=300.0)}
    assert _grade(_entry("ZZBA", models=split))["conf"] == "low"
    two = {"gfs": _model(), "ecmwf": _model()}
    assert _grade(_entry("ZZBA", models=two))["conf"] == "medium"
    assert _grade(_entry("ZZBA"))["conf"] == "high"


def test_category_matches_map_consensus_in_both_modes():
    models = {"gfs": _model(), "icon": _model(cat="MVFR", ceiling=1800.0),
              "ecmwf": _model(cat="IFR", ceiling=900.0)}
    e = _entry("ZZBA", models=models)
    assert _grade(e, mode="worst")["cat"] == e["consensus"]["flight_category"] == "IFR"
    assert _grade(e, mode="majority")["cat"] == e["consensus_majority"]["flight_category"]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _radius_query(slots, destination=None):
    q = ao.OutlookQuery(mode="radius", rules="vfr", consensus="worst", minima=_vfr(),
                        slots=slots, base=ao.Place(*BASE), radius_nm=60)
    exclude = {"ZZBA"}
    if destination:
        q.destination = ao.Place(destination, *COORDS[destination])
        exclude.add(destination)
    q.candidates = ao.select_radius(q.base, 60, COORDS, exclude)
    return q


def _times(slots):
    return {s: datetime(2026, 6, 20 + s[0], s[1], tzinfo=timezone.utc) for s in slots}


def test_radius_sectors_show_bad_west():
    slots = [(0, 12)]
    entries = [_entry(i) for i in COORDS if i != "ZZWE"] + [_entry("ZZWE", cat="IFR", ceiling=600.0)]
    out = ao.compute_outlook(_radius_query(slots), {(0, 12): _slot(entries)}, _times(slots))
    sectors = out["slots"][0]["sectors"]
    assert sectors["W"]["promising/marginal/unlikely"] == "0/0/1"
    assert sectors["E"]["promising/marginal/unlikely"].endswith("/0")
    assert sectors["W"]["worst"] == "ZZWE UNLIKELY"
    assert "worst" not in sectors["E"]  # all PROMISING: no worst line


def test_best_slots_and_trend_improving():
    slots = [(0, 9), (0, 12), (0, 15)]
    bad = _slot([_entry(i, cat="IFR", ceiling=700.0) for i in COORDS])
    good = _slot([_entry(i) for i in COORDS])
    out = ao.compute_outlook(_radius_query(slots), {(0, 9): bad, (0, 12): good, (0, 15): good},
                             _times(slots))
    assert out["best_slots"][0]["slot"] == "Sa12"
    assert all(b["slot"] != "Sa09" for b in out["best_slots"])
    assert out["trend"] == {"Sa": "improving"}


def test_missing_slot_reported_not_fatal():
    slots = [(0, 12), (0, 15)]
    out = ao.compute_outlook(_radius_query(slots), {(0, 12): _slot([_entry("ZZBA")])}, _times(slots))
    assert out["missing_slots"] == ["D+0 15Z"]
    assert [s["slot"] for s in out["slots"]] == ["Sa12"]


def test_pairs_and_one_way_days():
    slots = [(0, 9), (0, 15), (1, 9), (1, 15)]
    good = _slot([_entry(i) for i in COORDS])
    dest_bad = _slot([_entry(i) for i in COORDS if i != "ZZDE"] + [_entry("ZZDE", cat="IFR", ceiling=600.0)])
    base_bad = _slot([_entry(i) for i in COORDS if i != "ZZBA"] + [_entry("ZZBA", cat="IFR", ceiling=600.0)])
    data = {(0, 9): good, (0, 15): good, (1, 9): good, (1, 15): base_bad}
    out = ao.compute_outlook(_radius_query(slots, destination="ZZDE"), data, _times(slots))
    assert out["round_trip"] == {"Sa": "possible", "Su": "outbound only"}
    assert out["pairs"][0] == {"out": "Sa09", "back": "Sa15", "grade": ao.PROMISING, "conf": "high"}
    # Destination bad all day → no.
    data = {s: dest_bad for s in slots}
    out = ao.compute_outlook(_radius_query(slots, destination="ZZDE"), data, _times(slots))
    assert out["round_trip"] == {"Sa": "no", "Su": "no"} and out["pairs"] == []


def test_route_mode_corridor_blocks_leg():
    slots = [(0, 9), (0, 15)]
    q = ao.OutlookQuery(mode="route", rules="vfr", consensus="worst", minima=_vfr(), slots=slots,
                        departure=ao.Place(*BASE), destination=ao.Place("ZZDE", *COORDS["ZZDE"]),
                        corridor_nm=20, route_points=[COORDS["ZZBA"], COORDS["ZZDE"]])
    q.candidates = ao.select_corridor(q.route_points, 20, COORDS, {"ZZBA", "ZZDE"})
    good = _slot([_entry(i) for i in COORDS])
    mid_bad = _slot([_entry(i) for i in COORDS if i != "ZZMI"] + [_entry("ZZMI", cat="IFR", ceiling=500.0)])
    out = ao.compute_outlook(q, {(0, 9): mid_bad, (0, 15): good}, _times(slots))
    assert out["slots"][0]["corridor"]["worst"] == "ZZMI UNLIKELY"
    assert out["best_slots"][0]["slot"] == "Sa15"
    # Outbound at 09 blocked by the corridor; at 15 there is no later return.
    assert out["round_trip"] == {"Sa": "outbound only"}


def test_weekend_payload_stays_compact():
    """Radius 60 nm, two days, all hours, 16 airports → well under ~4k tokens."""
    coords = dict(COORDS)
    for i in range(16):
        coords[f"ZQ{i:02d}"] = (51.0 + 0.6 * ((i % 4) - 1.5) / 1.5, 0.8 * ((i // 4) - 1.5) / 1.5)
    slots = [(d, h) for d in (0, 1) for h in (6, 9, 12, 15, 18)]
    q = ao.OutlookQuery(mode="radius", rules="vfr", consensus="worst", minima=_vfr(), slots=slots,
                        base=ao.Place(*BASE), radius_nm=60,
                        destination=ao.Place("ZZDE", *COORDS["ZZDE"]))
    q.candidates = ao.select_radius(q.base, 60, coords, {"ZZBA", "ZZDE"})
    entries = []
    for icao, (lat, lon) in coords.items():
        e = _entry("ZZBA", wind=14.0, gust=24.0)
        e.update(icao=icao, lat=lat, lon=lon)
        entries.append(e)
    data = {s: _slot(entries) for s in slots}
    out = {"query": ao.describe_query(q),
           **ao.compute_outlook(q, data, _times(slots), map_base_url="https://weather.flyfun.aero"),
           "legend": ao.LEGEND, "note": ao.NOTE, "next_step": ao.NEXT_STEP,
           "guidance": ao.GUIDANCE}
    size = len(json.dumps(out, separators=(",", ":")))
    # ~4 chars/token. Was 16 KB; #851's longer grade words and the guidance
    # line put the case at ~17 KB.
    assert size < 18_000, size


def test_ifr_weekend_payload_with_freezing_level_stays_compact():
    coords = dict(COORDS)
    for i in range(16):
        coords[f"ZQ{i:02d}"] = (51.0 + 0.6 * ((i % 4) - 1.5) / 1.5, 0.8 * ((i // 4) - 1.5) / 1.5)
    slots = [(d, h) for d in (0, 1) for h in (6, 9, 12, 15, 18)]
    q = ao.OutlookQuery(mode="radius", rules="ifr", consensus="worst", minima=ao.minima_for("ifr"),
                        slots=slots, base=ao.Place(*BASE), radius_nm=60,
                        destination=ao.Place("ZZDE", *COORDS["ZZDE"]))
    q.candidates = ao.select_radius(q.base, 60, coords, {"ZZBA", "ZZDE"})
    entries = []
    for icao, (lat, lon) in coords.items():
        e = _entry("ZZBA", approach="ILS", wind=14.0, gust=24.0, fzl=3150.0)
        e.update(icao=icao, lat=lat, lon=lon)
        entries.append(e)
    data = {s: _slot(entries) for s in slots}
    out = {"query": ao.describe_query(q),
           **ao.compute_outlook(q, data, _times(slots), map_base_url="https://weather.flyfun.aero"),
           "legend": ao.LEGEND, "note": ao.NOTE + " " + ao.IFR_SCOPE_NOTE, "next_step": ao.NEXT_STEP,
           "guidance": ao.GUIDANCE}
    assert out["slots"][0]["min_fzl_ft"] == 3200
    size = len(json.dumps(out, separators=(",", ":")))
    # Was 16 KB; #851's grade words and guidance line put it at ~18.2 KB.
    assert size < 19_000, size


# ---------------------------------------------------------------------------
# Freezing level (#841): IFR, informative only
# ---------------------------------------------------------------------------


def test_ifr_freezing_level_follows_map_consensus_and_never_grades():
    models = {"gfs": _model(fzl=5000.0), "icon": _model(fzl=3000.0), "ecmwf": _model(fzl=7000.0)}
    e = _entry("ZZBA", models=models, approach="ILS")
    worst = _grade(e, rules="ifr", mode="worst")
    assert worst["fzl_ft"] == 3000.0 == e["consensus"]["freezing_level_ft"]
    assert _grade(e, rules="ifr", mode="majority")["fzl_ft"] == 5000.0
    low = {m: _model(fzl=500.0) for m in models}
    assert _grade(_entry("ZZBA", models=low, approach="ILS"), rules="ifr")["grade"] == worst["grade"]
    # VFR rows carry no freezing level.
    assert "fzl_ft" not in _grade(e, rules="vfr")


def test_freezing_level_reads_surface_when_sub_zero_at_2m():
    # A sub-zero surface under a warm layer: the sounding's first crossing is
    # the warm-layer base (4000 ft), but it is freezing at the surface.
    models = {"gfs": _model(temp=-2.0, fzl=4000.0), "icon": _model(temp=3.0),
              "ecmwf": _model(temp=4.0, fzl=None)}
    e = _entry("ZZBA", models=models, approach="ILS")
    assert _grade(e, rules="ifr", mode="worst")["fzl_ft"] == ao.FZL_SURFACE
    # Majority: median 2 m temperature is +3 °C → the consensus number.
    assert _grade(e, rules="ifr", mode="majority")["fzl_ft"] == e["consensus_majority"]["freezing_level_ft"]
    # Whole column sub-zero: no crossing at all, still the surface.
    cold = {m: _model(temp=-5.0, fzl=None) for m in models}
    assert _grade(_entry("ZZBA", models=cold, approach="ILS"), rules="ifr")["fzl_ft"] == ao.FZL_SURFACE


def test_lowest_freezing_level():
    assert ao.lowest_freezing_level([None, None]) is None
    assert ao.lowest_freezing_level([4120.0, None, 3260.0]) == 3300
    assert ao.lowest_freezing_level([4120.0, ao.FZL_SURFACE]) == ao.FZL_SURFACE


def test_ifr_outlook_shows_min_freezing_level_per_slot_sector_and_corridor():
    slots = [(0, 9)]
    entries = [_entry(i, approach="ILS") for i in COORDS if i != "ZZNO"]
    entries.append(_entry("ZZNO", approach="ILS", fzl=2400.0))
    q = _radius_query(slots)
    q.rules = "ifr"
    q.minima = ao.minima_for("ifr")
    row = ao.compute_outlook(q, {(0, 9): _slot(entries)}, _times(slots))["slots"][0]
    assert row["min_fzl_ft"] == 2400
    assert row["sectors"]["N"]["min_fzl_ft"] == 2400
    assert row["sectors"]["S"]["min_fzl_ft"] == 6000

    rq = ao.OutlookQuery(mode="route", rules="ifr", consensus="worst", minima=ao.minima_for("ifr"),
                         slots=slots, departure=ao.Place(*BASE),
                         destination=ao.Place("ZZDE", *COORDS["ZZDE"]),
                         corridor_nm=20, route_points=[COORDS["ZZBA"], COORDS["ZZDE"]])
    rq.candidates = ao.select_corridor(rq.route_points, 20, COORDS, {"ZZBA", "ZZDE"})
    entries = [_entry(i, approach="ILS") for i in COORDS if i != "ZZMI"]
    entries.append(_entry("ZZMI", approach="ILS", temp=-1.0))
    row = ao.compute_outlook(rq, {(0, 9): _slot(entries)}, _times(slots))["slots"][0]
    assert row["corridor"]["min_fzl_ft"] == ao.FZL_SURFACE == row["min_fzl_ft"]


def test_vfr_outlook_has_no_freezing_level():
    slots = [(0, 9)]
    out = ao.compute_outlook(_radius_query(slots), {(0, 9): _slot([_entry(i) for i in COORDS])},
                             _times(slots))
    assert "min_fzl_ft" not in json.dumps(out)


def test_ifr_note_no_longer_claims_snapshots_lack_freezing_level():
    assert "no freezing level" not in ao.IFR_SCOPE_NOTE
    assert "min_fzl_ft" in ao.IFR_SCOPE_NOTE and "NOT graded" in ao.IFR_SCOPE_NOTE


# ---------------------------------------------------------------------------
# Slots & endpoints
# ---------------------------------------------------------------------------


def test_resolve_slots_snaps_and_bounds():
    from weatherbrief.tasks.forecast_grid import MAX_FORECAST_DAY

    assert ao.resolve_slots([3], [11, 13]) == [(3, 12)]
    assert ao.resolve_slots([6], None) == [(6, 6), (6, 12), (6, 18)]
    with pytest.raises(ao.OutlookInputError):
        ao.resolve_slots([MAX_FORECAST_DAY + 1], None)


@pytest.fixture
def api(monkeypatch):
    from fastapi.testclient import TestClient

    from airport_mocks import mock_airport, mock_model
    from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
    from weatherbrief.api.app import create_app
    from weatherbrief.tasks import map_queries

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    airports = {i: mock_airport(i, i, lat, lon) for i, (lat, lon) in COORDS.items()}
    airports["ZZXX"] = mock_airport("ZZXX", "Unmonitored", 51.02, 0.02)
    monkeypatch.setattr("weatherbrief.airports._load_airport_model",
                        lambda *a, **k: mock_model(airports))
    monitored = {k: v for k, v in COORDS.items()}
    monkeypatch.setattr(map_queries, "watchlist_coords", lambda _db: monitored)
    monkeypatch.setattr(map_queries, "approach_classes", lambda _db: {})
    calls: list[datetime] = []

    def _slots_loader(db, targets, airports_db, *, project, project_id, max_live=1):
        calls.extend(targets.values())
        data = project(_slot([_entry(i) for i in COORDS]))
        return {k: data for k in targets}, {"stale": [], "live": [], "missing": []}

    monkeypatch.setattr(map_queries, "load_forecast_slots", _slots_loader)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: iter([None])
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    app.state.db_path = "/fake/db"
    c = TestClient(app, raise_server_exceptions=False)
    c.calls = calls
    return c


def test_maps_endpoint_radius_with_substituted_base(api):
    r = api.get("/api/maps/area-outlook", params={"base": "ZZXX", "days": [1], "hours": [12]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"]["base"] == {"icao": "ZZBA", "requested": "ZZXX", "substituted_nm": 1.4}
    assert body["slots"][0]["sectors"]
    assert "create_flight" in body["next_step"]
    assert "/maps.html?tab=forecast&fc.day=1&fc.hour=12&fc.date=" in body["map_url"]
    # #851: framing leads the payload, and no go/no-go wording anywhere.
    assert next(iter(body)) == "guidance" and "not a go/no-go decision" in body["guidance"]
    text = json.dumps({k: v for k, v in body.items() if k != "guidance"})
    assert not re.search(r"\bGO\b|NO-GO|NOGO|\bgo_|_go\b|nogo", text), text
    assert [t.hour for t in api.calls] == [12] and len(api.calls) == 1


def test_agent_endpoint_route_mode(api):
    r = api.get("/agent/v1/area-outlook",
                params={"route": "ZZBA ZZDE", "days": [0, 1], "hours": [9, 15], "rules": "ifr"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"]["scope"] == "terminal_only"
    assert "icing" in body["note"]
    assert set(body["round_trip"]) and body["pairs"]


def test_endpoint_rejects_bad_input(api):
    r = api.get("/api/maps/area-outlook", params={"base": "ZZBA", "route": "ZZBA ZZDE", "days": [1]})
    assert r.status_code == 400
    r = api.get("/api/maps/area-outlook", params={"base": "QQQQ", "days": [1]})
    assert r.status_code == 400 and "Unknown airport" in r.json()["detail"]


def test_corridor_minority_unlikely_is_marginal_majority_blocks():
    ok, out = {"grade": ao.PROMISING, "conf": "high"}, {"grade": ao.UNLIKELY, "conf": "medium"}
    assert ao.corridor_point([ok, ok, out]) == {"grade": ao.MARGINAL, "conf": "medium"}
    assert ao.corridor_point([ok, out])["grade"] == ao.UNLIKELY
    assert ao.corridor_point([ok, ok]) == {"grade": ao.PROMISING, "conf": "high"}
    # NIGHT counts like UNLIKELY.
    night = {"grade": ao.NIGHT, "conf": "high"}
    assert ao.corridor_point([ok, ok, night])["grade"] == ao.MARGINAL
    assert ao.corridor_point([ok, night])["grade"] == ao.UNLIKELY
    assert ao.corridor_point([]) is None


# ---------------------------------------------------------------------------
# MCP tool + prompt (thin proxy; the client is faked)
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self):
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def area_outlook(self, **params):
        self.params = params
        return {"best_slots": [], "query": {}}


def test_mcp_tool_forwards_params(monkeypatch):
    from weatherbrief.mcp import server

    fake = _FakeClient()
    monkeypatch.setattr(server, "_get_client", lambda: fake)
    out = server.area_outlook(days=[3, 4], route="EGTF LFAT", rules="vfr", consensus="worst")
    assert out == {"best_slots": [], "query": {}}
    assert fake.params["route"] == "EGTF LFAT" and fake.params["days"] == [3, 4]


def test_mcp_day_cap_matches_map_horizon():
    from typing import get_type_hints

    from weatherbrief.mcp import server
    from weatherbrief.tasks.forecast_grid import MAX_FORECAST_DAY

    hint = get_type_hints(server.get_airport_weather, include_extras=True)["day"]
    le = [m.le for m in hint.__metadata__[0].metadata if hasattr(m, "le")]
    assert le == [MAX_FORECAST_DAY]


def test_plan_local_flight_prompt_points_at_area_outlook():
    from weatherbrief.mcp import server

    text = server.plan_local_flight(base="EGTF", destination="LFAT", days="this weekend")
    assert "area_outlook" in text and "route='EGTF LFAT'" in text and "not a briefing" in text
    assert "base='EGTF'" in server.plan_local_flight(base="EGTF")


# ---------------------------------------------------------------------------
# Forecast-map links
# ---------------------------------------------------------------------------


def test_map_url_uses_forecast_tab_contract():
    from urllib.parse import parse_qs, urlparse

    q = _radius_query([(3, 15)], destination="ZZDE")
    url = ao.map_url("https://wx.example/", q, (3, 15), datetime(2026, 6, 23, 15, tzinfo=timezone.utc))
    u = urlparse(url)
    assert u.path == "/maps.html"
    qs = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert qs["tab"] == "forecast" and qs["fc.day"] == "3" and qs["fc.hour"] == "15"
    # Absolute date alongside the relative day, so the link can't drift (#840).
    assert qs["fc.date"] == "2026-06-23"
    assert qs["fc.apt"] == "ZZDE" and "fc.model" not in qs
    lat, lon, zoom = qs["fc.view"].split(",")
    # Destination (~75 nm) is outside the 60 nm radius: framed between the two.
    assert (lat, lon) == ("51.00", "1.00") and 4 <= int(zoom) <= 10


def test_map_view_zooms_out_for_larger_area():
    small = _radius_query([(0, 12)])
    big = _radius_query([(0, 12)])
    small.radius_nm, big.radius_nm = 20, 150
    assert int(ao.map_view(small).split(",")[2]) > int(ao.map_view(big).split(",")[2])


def test_map_urls_on_best_slots_pairs_and_top_level():
    slots = [(0, 9), (0, 15)]
    good = _slot([_entry(i) for i in COORDS])
    q = _radius_query(slots, destination="ZZDE")
    q.consensus = "majority"
    out = ao.compute_outlook(q, {s: good for s in slots}, _times(slots), map_base_url="https://wx.example")
    assert "fc.day=0&fc.hour=9&fc.date=2026-06-20" in out["map_url"] and "fc.model=majority" in out["map_url"]
    assert all("maps.html" in b["map_url"] and "fc.date=2026-06-20" in b["map_url"] for b in out["best_slots"])
    assert out["pairs"] and "fc.hour=9" in out["pairs"][0]["map_url"]
    # No base URL → no links (pure core stays usable without config).
    out = ao.compute_outlook(q, {s: good for s in slots}, _times(slots))
    assert "map_url" not in out and "map_url" not in out["best_slots"][0]


# ---------------------------------------------------------------------------
# Review round 1: has_iap, IFR minima labelling, corridor spread, past slots
# ---------------------------------------------------------------------------


def test_ifr_unknown_approach_class_uses_conservative_proxy_not_vfr():
    # approach_type None but an IAP exists: non-precision estimate (DH hi 900 ft),
    # so 950 ft with an alternate required is MARGINAL, not VFR-line UNLIKELY.
    e = _entry("ZZBA", approach=None, cat="IFR", ceiling=950.0, alt=True)
    assert _grade(e, rules="ifr")["grade"] == ao.UNLIKELY  # no IAP → VFR lines
    g = ao.grade_airport(e, rules="ifr", mode="worst", minima=ao.minima_for("ifr"),
                         valid_time=SUMMER_NOON, has_iap=True)
    assert g["grade"] == ao.MARGINAL
    e = _entry("ZZBA", approach=None, cat="IFR", ceiling=800.0, alt=True)
    g = ao.grade_airport(e, rules="ifr", mode="worst", minima=ao.minima_for("ifr"),
                         valid_time=SUMMER_NOON, has_iap=True)
    assert g["grade"] == ao.UNLIKELY and "below est. approach minima" in g["reasons"]


def test_describe_query_ifr_does_not_present_vfr_lines_as_minima():
    q = _radius_query([(0, 12)])
    q.rules = "ifr"
    m = ao.describe_query(q)["minima"]
    assert "ceiling_promising_ft" not in m
    assert m["no_approach_airports_only"]["ceiling_promising_ft"] == 1500


def test_select_corridor_over_cap_keeps_middle_of_route():
    pts = [(51.0, 0.0), (51.0, 6.0)]  # ~226 nm east
    coords = {f"ZE{i:02d}": (51.01, 0.3 + i * 0.05) for i in range(10)}  # bunched near start
    coords["ZMID"] = (51.15, 3.0)  # one in the middle, further off track
    picked = ao.select_corridor(pts, 20, coords, cap=4)
    assert "ZMID" in {c.icao for c in picked}


def test_past_slots_are_skipped_and_reported(monkeypatch):
    from weatherbrief.tasks import map_queries

    monkeypatch.setattr(map_queries, "watchlist_coords", lambda _db: dict(COORDS))
    monkeypatch.setattr(map_queries, "approach_classes", lambda _db: {})
    seen: list = []

    def _loader(db, targets, airports_db, *, project, project_id, max_live=1):
        seen.extend(targets)
        return {}, {"stale": [], "live": [], "missing": list(targets)}

    monkeypatch.setattr(map_queries, "load_forecast_slots", _loader)

    class _Noon(datetime):  # pin the clock: 06Z is gone, 18Z is still ahead
        @classmethod
        def now(cls, tz=None):
            return SUMMER_NOON

    monkeypatch.setattr(ao, "datetime", _Noon)
    out = ao.build_area_outlook(None, "/fake", days=[0], hours=[6, 18], base="ZZBA")
    assert "D+0 06Z" in out["past_slots_skipped"] and (0, 6) not in seen
    assert (0, 18) in seen


# ---------------------------------------------------------------------------
# load_forecast_slots: bounded queries against a real (SQLite) database
# ---------------------------------------------------------------------------


@pytest.fixture
def slot_db(monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker

    from conftest import make_app_engine
    from weatherbrief.tasks import map_queries

    map_queries._SLOT_LRU.clear()
    engine = make_app_engine()
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute",
                 lambda conn, cur, stmt, *a: statements.append(stmt))
    session = sessionmaker(bind=engine)()
    live_calls: list[datetime] = []

    def _live(db, forecast_hour, airports_db):
        live_calls.append(forecast_hour)
        return {"forecast_time": forecast_hour.isoformat(), "model_init_times": {},
                "airports": [_entry("ZZBA")]}

    monkeypatch.setattr(map_queries, "get_forecast_map_data", _live)
    session.statements = statements
    session.live_calls = live_calls
    yield session
    session.close()
    map_queries._SLOT_LRU.clear()


_COMPUTED = datetime(2026, 6, 19, 7, 30, tzinfo=timezone.utc)  # yesterday's cycle
_FETCHED = datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc)


def _seed(db, *, day, hour, computed=_COMPUTED, source_max=_FETCHED, forecast_time=None):
    from datetime import timedelta

    from weatherbrief.db.models import VerificationCacheRow
    from weatherbrief.tasks.cache_builder import forecast_map_cache_key

    t = forecast_time or datetime(
        *(computed.date() + timedelta(days=day)).timetuple()[:3], hour, tzinfo=timezone.utc)
    db.add(VerificationCacheRow(
        cache_key=forecast_map_cache_key(day, hour), computed_at=computed,
        source_max_time=source_max,
        data_json=json.dumps({"forecast_time": t.isoformat(), "model_init_times": {"gfs": "x"},
                              "airports": [_entry("ZZBA")]}),
    ))


def _snapshot(db, fetched=_FETCHED):
    from weatherbrief.db.models import AirportForecastSnapshotRow

    db.add(AirportForecastSnapshotRow(
        icao="ZZBA", model="gfs", model_init_time=fetched, forecast_hour=fetched,
        fetched_at=fetched,
    ))


def _load(db, targets, **kw):
    from weatherbrief.tasks import map_queries

    return map_queries.load_forecast_slots(
        db, targets, "/fake", project=ao.project_slot, project_id="t", **kw)


def test_rollover_reuses_yesterdays_cache_without_live_queries(slot_db):
    # Computed yesterday (06-19): day=4 holds 06-23, which is today's D+3.
    for h in (9, 12, 15):
        _seed(slot_db, day=4, hour=h)
    _snapshot(slot_db)
    slot_db.commit()
    targets = {h: datetime(2026, 6, 23, h, tzinfo=timezone.utc) for h in (9, 12, 15)}
    slot_db.statements.clear()
    data, status = _load(slot_db, targets)
    assert set(data) == {9, 12, 15} and slot_db.live_calls == []
    assert status == {"stale": [], "live": [], "missing": []}
    # Fixed budget: MAX(fetched_at) + one meta read + one blob read.
    assert len(slot_db.statements) == 3, slot_db.statements


def test_repeat_call_reads_no_blobs(slot_db):
    _seed(slot_db, day=1, hour=12)
    _snapshot(slot_db)
    slot_db.commit()
    targets = {"a": datetime(2026, 6, 20, 12, tzinfo=timezone.utc)}
    _load(slot_db, targets)
    slot_db.statements.clear()
    data, _ = _load(slot_db, targets)
    assert "a" in data
    assert len(slot_db.statements) == 2  # MAX + meta; blob served from the LRU
    assert not any("data_json" in s for s in slot_db.statements)


def test_stale_cache_served_and_reported(slot_db):
    _seed(slot_db, day=1, hour=12, source_max=_FETCHED)
    _snapshot(slot_db, fetched=datetime(2026, 6, 19, 19, tzinfo=timezone.utc))  # newer run landed
    slot_db.commit()
    data, status = _load(slot_db, {"a": datetime(2026, 6, 20, 12, tzinfo=timezone.utc)})
    assert "a" in data and status["stale"] == ["a"] and slot_db.live_calls == []


def test_uncovered_slots_live_capped_rest_missing(slot_db):
    _snapshot(slot_db)
    slot_db.commit()
    targets = {h: datetime(2026, 6, 25, h, tzinfo=timezone.utc) for h in (6, 12, 18)}
    data, status = _load(slot_db, targets, max_live=1)
    assert len(slot_db.live_calls) == 1 and status["live"] == [6]
    assert status["missing"] == [12, 18] and set(data) == {6}


def test_payload_time_mismatch_is_not_trusted(slot_db):
    wrong = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
    _seed(slot_db, day=1, hour=12, forecast_time=wrong)  # claims 06-20 by key, holds 06-21
    _snapshot(slot_db)
    slot_db.commit()
    data, status = _load(slot_db, {"a": datetime(2026, 6, 20, 12, tzinfo=timezone.utc)}, max_live=0)
    assert status["missing"] == ["a"] and data == {}


def test_projection_keeps_grading_identical():
    models = {"gfs": _model(), "icon": _model(cat="MVFR", ceiling=1800.0, gust=28.0, fzl=3200.0),
              "ecmwf": _model(cat="IFR", ceiling=900.0, alt=True, temp=-1.0)}
    full = _entry("ZZBA", models=models, approach="ILS")
    slim = ao.project_slot(_slot([full]))["airports"][0]
    for rules in ("vfr", "ifr"):
        for mode in ("worst", "majority"):
            assert _grade(full, rules=rules, mode=mode) == _grade(slim, rules=rules, mode=mode)


# ---------------------------------------------------------------------------
# Review round 2: shared-LRU safety
# ---------------------------------------------------------------------------


def test_compute_outlook_does_not_mutate_shared_slot_data():
    import copy

    slots = [(0, 9), (0, 15)]
    shared = ao.project_slot(_slot([_entry(i) for i in COORDS]))
    data = {s: shared for s in slots}
    before = copy.deepcopy(shared)
    for rules in ("vfr", "ifr"):
        q = _radius_query(slots, destination="ZZDE")
        q.rules = rules
        ao.compute_outlook(q, data, _times(slots), map_base_url="https://wx.example")
    assert shared == before


def test_slot_lru_holds_whole_grid_and_is_thread_safe():
    import threading

    from weatherbrief.tasks import map_queries
    from weatherbrief.tasks.forecast_grid import day_hour_pairs

    map_queries._SLOT_LRU.clear()
    n = len(day_hour_pairs())
    for i in range(n):
        map_queries._lru_put(("p", f"k{i}", "t"), {"i": i})
    assert len(map_queries._SLOT_LRU) == n and map_queries._lru_get(("p", "k0", "t")) == {"i": 0}

    errors: list[BaseException] = []

    def hammer(offset):
        try:
            for i in range(2000):
                k = ("p", f"k{(i + offset) % (n + 5)}", "t")
                if map_queries._lru_get(k) is None:
                    map_queries._lru_put(k, {"i": i})
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=hammer, args=(o,)) for o in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(map_queries._SLOT_LRU) <= n
    map_queries._SLOT_LRU.clear()


# ---------------------------------------------------------------------------
# Review round 3: radius mode with a destination ranks on both ends
# ---------------------------------------------------------------------------


def test_radius_best_slot_never_unlikely_at_destination():
    slots = [(0, 9), (0, 15)]
    good = _slot([_entry(i) for i in COORDS])
    # 09Z: base and area perfect, destination IFR. 15Z: everything MARGINAL-ish.
    dest_bad = _slot([_entry(i) for i in COORDS if i != "ZZDE"]
                     + [_entry("ZZDE", cat="IFR", ceiling=600.0)])
    meh = _slot([_entry(i, cat="MVFR", ceiling=1200.0) for i in COORDS])
    q = _radius_query(slots, destination="ZZDE")
    out = ao.compute_outlook(q, {(0, 9): dest_bad, (0, 15): meh}, _times(slots))
    assert [b["slot"] for b in out["best_slots"]] == ["Sa15"]
    assert out["best_slots"][0]["grade"] == ao.MARGINAL
    # Without a destination, 09Z wins on the base alone.
    q = _radius_query(slots)
    out = ao.compute_outlook(q, {(0, 9): dest_bad, (0, 15): meh}, _times(slots))
    assert out["best_slots"][0]["slot"] == "Sa09"
    # And a PROMISING destination keeps 09Z on top in the trip case.
    q = _radius_query(slots, destination="ZZDE")
    out = ao.compute_outlook(q, {(0, 9): good, (0, 15): meh}, _times(slots))
    assert out["best_slots"][0]["slot"] == "Sa09"


# ---------------------------------------------------------------------------
# Review round 4 (follow-up on main)
# ---------------------------------------------------------------------------


def test_great_circle_matches_euro_aip_navpoint():
    from euro_aip.models.navpoint import NavPoint

    b, d = ao.great_circle(51.348, -0.559, 50.518, 1.622)
    nb, nd = NavPoint(latitude=51.348, longitude=-0.559).haversine_distance(
        NavPoint(latitude=50.518, longitude=1.622))
    assert (b, d) == (nb, nd) and 96 < d < 97


def test_trend_ignores_night_slots():
    # Winter: 06Z is night (every VFR grade NIGHT), 09..15Z identical PROMISING.
    slots = [(0, 6), (0, 9), (0, 12), (0, 15)]
    times = {s: datetime(2026, 12, 19, s[1], tzinfo=timezone.utc) for s in slots}
    good = _slot([_entry(i) for i in COORDS])
    out = ao.compute_outlook(_radius_query(slots), {s: good for s in slots}, times)
    assert out["trend"] == {"Sa": "steady"}


def test_night_slot_ranks_like_unlikely():
    # #851: a VFR night slot is labelled NIGHT and ranks, scores and pairs
    # exactly like an UNLIKELY one. Winter 06Z is night everywhere.
    slots = [(0, 6), (0, 12)]
    times = {s: datetime(2026, 12, 19, s[1], tzinfo=timezone.utc) for s in slots}
    good = _slot([_entry(i) for i in COORDS])
    q = _radius_query(slots, destination="ZZDE")
    out = ao.compute_outlook(q, {s: good for s in slots}, times)
    dawn = out["slots"][0]
    assert dawn["base"].startswith("NIGHT ")
    assert all(sec["night"] and sec["promising/marginal/unlikely"] == "0/0/0"
               for sec in dawn["sectors"].values())
    assert "night" not in out["slots"][1]["sectors"]["E"]
    assert [b["slot"] for b in out["best_slots"]] == ["Sa12"]
    assert all(p["out"] != "Sa06" for p in out["pairs"])
    # Same order an UNLIKELY dawn would give.
    bad = _slot([_entry(i, cat="IFR", ceiling=600.0) for i in COORDS])
    times_day = {s: datetime(2026, 6, 20, s[1], tzinfo=timezone.utc) for s in slots}
    ref = ao.compute_outlook(q, {(0, 6): bad, (0, 12): good}, times_day)
    assert [b["slot"] for b in ref["best_slots"]] == ["Sa12"]
    assert ao._GRADE_RANK[ao.NIGHT] == ao._GRADE_RANK[ao.UNLIKELY]
    assert ao._leg_ok({"grade": ao.NIGHT, "conf": "high"}) is None


def test_best_slot_confidence_from_ends_not_far_area():
    split = {"gfs": _model(), "icon": _model(cat="IFR", ceiling=800.0),
             "ecmwf": _model(cat="LIFR", ceiling=300.0)}
    entries = [_entry(i) for i in COORDS if i != "ZZNO"] + [_entry("ZZNO", models=split)]
    slots = [(0, 12)]
    out = ao.compute_outlook(_radius_query(slots, destination="ZZDE"),
                             {(0, 12): _slot(entries)}, _times(slots))
    assert out["best_slots"][0]["conf"] == "high"


def test_map_view_frames_destination_outside_radius():
    q = _radius_query([(0, 12)], destination="ZZDE")  # ~75 nm east, radius 60
    q.radius_nm = 20
    lat, lon, _ = ao.map_view(q).split(",")
    assert 0.5 < float(lon) < 1.5  # centred between base (0E) and destination (2E)
    q.radius_nm = 120  # destination inside: stays on the base
    assert ao.map_view(q).startswith("51.00,0.00,")


def test_all_past_slots_is_input_error(monkeypatch):
    from weatherbrief.tasks import map_queries

    monkeypatch.setattr(map_queries, "watchlist_coords", lambda _db: dict(COORDS))
    monkeypatch.setattr(map_queries, "approach_classes", lambda _db: {})
    monkeypatch.setattr(ao, "PAST_SLOT_GRACE", __import__("datetime").timedelta(hours=-48))
    with pytest.raises(ao.OutlookInputError, match="in the past"):
        ao.build_area_outlook(None, "/fake", days=[0], hours=[18], base="ZZBA")


def test_endpoint_rejects_too_many_days(api):
    r = api.get("/api/maps/area-outlook", params={"base": "ZZBA", "days": list(range(9))})
    assert r.status_code == 422
