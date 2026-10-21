"""Area outlook (#836): selection, grading, aggregation, pairing, endpoints."""

from __future__ import annotations

import json
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
           xwind=5.0, risk="none", alt=False):
    return {
        "flight_category": cat, "ceiling_ft": ceiling, "visibility_m": vis,
        "wind_speed_kt": wind, "wind_dir_deg": wdir, "wind_gust_kt": gust,
        "crosswind_kt": xwind, "headwind_kt": 5.0, "cape_jkg": 10.0,
        "cloud_cover_pct": 50.0, "convective_risk": risk,
        "alt_required": {"faa": alt, "easa": alt},
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
    assert _grade(_entry("ZZBA"))["grade"] == ao.GO
    assert _grade(_entry("ZZBA", cat="MVFR", ceiling=1200.0))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", cat="IFR", ceiling=800.0))["grade"] == ao.NOGO
    assert _grade(_entry("ZZBA", vis=4000.0, cat="MVFR"))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", vis=2000.0, cat="IFR"))["grade"] == ao.NOGO


def test_wind_thresholds_come_from_airport_wind_advisory():
    from weatherbrief.analysis.advisories.airport_wind import AirportWindEvaluator

    defaults = {p.key: p.default for p in AirportWindEvaluator.catalog_entry().parameters}
    m = ao.minima_for("vfr")
    assert m.crosswind_green_kt == defaults["crosswind_green_kt"]
    assert m.crosswind_red_kt == defaults["crosswind_red_kt"]
    assert m.gust_red_kt == defaults["gust_red_kt"]
    assert _grade(_entry("ZZBA", xwind=m.crosswind_green_kt))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", xwind=m.crosswind_red_kt))["grade"] == ao.NOGO
    assert _grade(_entry("ZZBA", gust=m.gust_red_kt, wind=20.0))["grade"] == ao.NOGO


def test_overrides_move_lines_without_inverting_band():
    m = ao.minima_for("vfr", min_ceiling_ft=800, max_crosswind_kt=10)
    assert m.ceiling_go_ft == 800 and m.ceiling_floor_ft == 800
    assert m.crosswind_red_kt == 10 and m.crosswind_green_kt <= 10
    g = _grade(_entry("ZZBA", cat="IFR", ceiling=900.0), minima=m)
    assert g["grade"] == ao.GO


def test_convective_risk():
    assert _grade(_entry("ZZBA", risk="marginal"))["grade"] == ao.GO
    assert _grade(_entry("ZZBA", risk="low"))["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", risk="moderate"))["grade"] == ao.NOGO


def test_vfr_night_is_nogo_ifr_only_flagged():
    winter_dawn = datetime(2026, 12, 21, 6, tzinfo=timezone.utc)
    g = _grade(_entry("ZZBA"), when=winter_dawn)
    assert g["grade"] == ao.NOGO and "night" in g["reasons"]
    g = _grade(_entry("ZZBA", approach="ILS"), rules="ifr", when=winter_dawn)
    assert g["grade"] == ao.GO and g["phase"] == "night"


def test_ifr_uses_approach_minima_and_alternate_trigger():
    # ILS: NO-GO below the class's worst-case DH (300 ft).
    assert _grade(_entry("ZZBA", approach="ILS", cat="LIFR", ceiling=250.0, alt=True),
                  rules="ifr")["grade"] == ao.NOGO
    # Above minima but an alternate would be required → MARGINAL.
    assert _grade(_entry("ZZBA", approach="ILS", cat="IFR", ceiling=700.0, alt=True),
                  rules="ifr")["grade"] == ao.MARGINAL
    assert _grade(_entry("ZZBA", approach="ILS", cat="MVFR", ceiling=2500.0, alt=False),
                  rules="ifr")["grade"] == ao.GO
    # No approach → graded on the VFR lines.
    assert _grade(_entry("ZZBA", approach=None, cat="IFR", ceiling=800.0),
                  rules="ifr")["grade"] == ao.NOGO


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
    assert sectors["W"]["go/marg/nogo"] == "0/0/1"
    assert sectors["E"]["go/marg/nogo"].endswith("/0")
    assert sectors["W"]["worst"] == "ZZWE NO-GO"
    assert "worst" not in sectors["E"]  # all GO: no worst line


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
    assert out["pairs"][0] == {"out": "Sa09", "back": "Sa15", "grade": ao.GO, "conf": "high"}
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
    assert out["slots"][0]["corridor"]["worst"] == "ZZMI NO-GO"
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
    out = {"query": ao.describe_query(q), **ao.compute_outlook(q, data, _times(slots)),
           "legend": ao.LEGEND, "note": ao.NOTE, "next_step": ao.NEXT_STEP}
    size = len(json.dumps(out, separators=(",", ":")))
    assert size < 16_000, size  # ~4 chars/token


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
    calls: list[tuple[int, int]] = []

    def _slot_loader(db, day, hour, airports_db, **kw):
        calls.append((day, hour))
        return _slot([_entry(i) for i in COORDS])

    monkeypatch.setattr(map_queries, "load_forecast_slot", _slot_loader)
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
    assert api.calls == [(1, 12)]


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


def test_corridor_minority_nogo_is_marginal_majority_blocks():
    go, nogo = {"grade": ao.GO, "conf": "high"}, {"grade": ao.NOGO, "conf": "medium"}
    assert ao.corridor_point([go, go, nogo]) == {"grade": ao.MARGINAL, "conf": "medium"}
    assert ao.corridor_point([go, nogo])["grade"] == ao.NOGO
    assert ao.corridor_point([go, go]) == {"grade": ao.GO, "conf": "high"}
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
