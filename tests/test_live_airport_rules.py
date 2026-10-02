"""What counts as a change at an airport (meteorology-decisions §36).

The live layer reports more than flight-category crossings: convective weather
(TS / VCTS / CB / TCU) appearing, significant weather (FZRA, hail, squall,
heavy showers…) and the airport wind advisory. What is reported, and at which
tier, depends on the airport's role (``AIRPORT_POLICY``), and a change is only
reported while it can still matter (departure until take-off, an en-route
airport until passed).

These rules decide what a pilot gets alerted about, so every cell of the
policy, every trigger and every timing rule is pinned here.
"""

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.models.observations import AirportObservation, RouteObservations
from weatherbrief.tasks.live_significance import (
    AIRPORT_POLICY,
    ClassifierMemory,
    airport_relevant,
    airport_roles,
    airport_tier,
    classify_changes,
    convective_tags,
    significant_weather,
)

T0 = datetime(2026, 10, 2, 6, 30, tzinfo=timezone.utc)  # the briefing's METAR
T1 = T0 + timedelta(minutes=30)
T2 = T0 + timedelta(minutes=60)
DEP = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)

# ZZDP → ZZRT (en route, 100 NM along) → ZZDS; ZZAL is the first alternate.
ROLES = airport_roles(["ZZDP", "ZZDS"], ["ZZAL"])


def apt(icao, raw="", cat="VFR", *, t=T0, wx=(), wind="green", xwind=None, gust=None,
        rwy=None, enroute=None, taf=None, speci=False):
    return AirportObservation(
        icao=icao,
        distance_from_route_nm=1.0,
        enroute_distance_nm=enroute,
        nearest_waypoint_icao=icao,
        metar_raw=raw or f"METAR {icao} 020630Z 05010KT 9999 FEW030 20/15 Q1020",
        metar_time=t,
        metar_report_type="SPECI" if speci else "METAR",
        metar_flight_category=cat,
        metar_weather=list(wx),
        metar_wind_advisory=wind,
        metar_crosswind_kt=xwind,
        metar_wind_gust_kt=gust,
        metar_best_runway_id=rwy,
        taf_flight_category_at_eta=taf,
    )


def obs(*airports):
    return RouteObservations(
        corridor_nm=30.0, fetch_time=T0, airports_found=len(airports),
        airports_with_metar=len(airports), airports_with_taf=0, airports=list(airports),
    )


def classify(base, latest, *, now=T1, departure_at=None, flown_nm=None, memory=None):
    changes, mem = classify_changes(
        baseline_obs=obs(*base), latest_obs=obs(*latest),
        baseline_sigmets=None, latest_sigmets=None,
        roles=ROLES, departure_at=departure_at, flown_nm=flown_nm, memory=memory, now=now,
    )
    return changes.changes, mem


def only(changes, kind):
    found = [c for c in changes if c.kind == kind]
    assert len(found) == 1, [c.message for c in changes]
    return found[0]


# --- Detection --------------------------------------------------------------


@pytest.mark.parametrize("raw,wx,expected", [
    ("METAR ZZRT 020509Z 05009KT 9999 TS FEW015CB BKN035 21/19 Q1024", ["TS"], {"TS", "CB"}),
    ("METAR ZZRT 020539Z 06006KT 9999 VCTS RA FEW015CB 21/19 Q1024", ["VCTS", "RA"], {"VCTS", "CB"}),
    ("METAR ZZRT 020710Z 06009KT 5000 -TSRA BKN014 20/20 Q1025", ["-TSRA"], {"TS"}),
    ("METAR ZZRT 020630Z 32007KT 7000 -RA BKN012 FEW015TCU 20/19 Q1025", ["-RA"], {"TCU"}),
    ("METAR ZZRT 020500Z 04009KT 9999 FEW015CB 21/18 Q1024", [], {"CB"}),
    # Recent thunderstorm (RETS) is history, not present weather.
    ("METAR ZZRT 020730Z 06008KT 5000 RA BKN010 20/20 Q1025 RETS", ["RA", "RETS"], set()),
    # A runway or station code containing "CB"/"TS" is not a cloud type.
    ("METAR ZZRT 020600Z 05010KT 9999 FEW030 20/15 Q1020 RMK CBS", [], set()),
])
def test_convective_tags(raw, wx, expected):
    assert convective_tags(apt("ZZRT", raw, wx=wx)) == expected


@pytest.mark.parametrize("wx,expected", [
    (["FZRA"], {"FZRA"}),
    (["-FZDZ"], {"FZDZ"}),
    (["+SHRA"], {"+SHRA"}),
    (["+TSRA"], set()),              # a thunderstorm is reported once, as convective
    (["TSGR"], {"GR"}),
    (["SQ"], {"SQ"}),
    (["FC"], {"FC"}),
    (["-SHRA", "RA", "BR"], set()),  # ordinary rain and mist are not significant
    (["REFZRA"], set()),             # recent weather excluded
])
def test_significant_weather(wx, expected):
    assert significant_weather(apt("ZZRT", wx=wx)) == expected


# --- Policy table -----------------------------------------------------------


def test_policy_improvements_never_alert():
    for role, kinds in AIRPORT_POLICY.items():
        for kind, tiers in kinds.items():
            assert tiers["better"] in (None, "highlight"), (role, kind)


@pytest.mark.parametrize("role,kind,expected", [
    ("destination", "metar_category", "alert"),
    ("destination", "metar_convective", "alert"),
    ("destination", "metar_weather", "alert"),
    ("destination", "metar_wind", "alert"),
    ("destination", "taf_category", "alert"),
    ("departure", "metar_wind", "alert"),
    ("alternate", "metar_category", "highlight"),
    ("alternate", "metar_convective", "highlight"),
    ("alternate", "metar_wind", "highlight"),
    ("route", "metar_category", "highlight"),
    ("route", "metar_convective", "alert"),
    ("route", "metar_weather", "alert"),
    ("route", "metar_wind", None),
])
def test_policy_worse(role, kind, expected):
    assert airport_tier(role, kind, "worse") == expected


# --- Convective / significant weather / wind changes ------------------------


def test_thunderstorm_appearing_en_route_alerts():
    """LEVC on 2026-10-02: TS FEW015CB under the route, category still VFR."""
    base = [apt("ZZRT", enroute=100)]
    latest = [apt("ZZRT", "SPECI ZZRT 020709Z 05009KT 9999 TS FEW015CB BKN035 21/19 Q1024",
                  wx=["TS"], t=T1, enroute=100, speci=True)]
    changes, _ = classify(base, latest)
    c = only(changes, "metar_convective")
    assert (c.role, c.tier, c.direction) == ("route", "alert", "worse")
    assert (c.from_value, c.to_value) == ("none", "TS")
    assert c.message == "ZZRT METAR: CB, TS reported (SPECI)"
    assert c.key == "conv:ZZRT" and c.source == "SPECI"


def test_step_up_from_cb_to_ts_is_a_change_and_step_down_is_better():
    cb = "METAR ZZDS 020630Z 05010KT 9999 FEW015CB 20/15 Q1020"
    ts = "METAR ZZDS 020700Z 05010KT 9999 TS FEW015CB 20/15 Q1020"
    up, _ = classify([apt("ZZDS", cb)], [apt("ZZDS", ts, wx=["TS"], t=T1)])
    assert only(up, "metar_convective").to_value == "TS"
    down, _ = classify([apt("ZZDS", ts, wx=["TS"])], [apt("ZZDS", t=T1)])
    c = only(down, "metar_convective")
    assert (c.direction, c.tier) == ("better", "highlight")
    assert c.message == "ZZDS METAR: CB, TS no longer reported"


def test_same_convective_level_is_not_a_change():
    a = "METAR ZZDS 020630Z 05010KT 9999 FEW015CB 20/15 Q1020"
    b = "METAR ZZDS 020700Z 05010KT 9999 SCT020CB 20/15 Q1020"
    changes, _ = classify([apt("ZZDS", a)], [apt("ZZDS", b, t=T1)])
    assert [c for c in changes if c.kind == "metar_convective"] == []


def test_significant_weather_appearing_at_destination_alerts():
    changes, _ = classify([apt("ZZDS")], [apt("ZZDS", wx=["-FZDZ"], t=T1)])
    c = only(changes, "metar_weather")
    assert (c.tier, c.to_value, c.message) == ("alert", "FZDZ", "ZZDS METAR: FZDZ reported")


def test_significant_weather_clearing_is_a_highlight():
    changes, _ = classify([apt("ZZDS", wx=["+SHRA"])], [apt("ZZDS", wx=["-SHRA"], t=T1)])
    c = only(changes, "metar_weather")
    assert (c.direction, c.tier) == ("better", "highlight")


@pytest.mark.parametrize("before,after,direction", [
    ("green", "amber", "worse"),
    ("green", "red", "worse"),
    ("amber", "red", "worse"),
    ("red", "green", "better"),
])
def test_wind_advisory_crossing_at_destination(before, after, direction):
    changes, _ = classify(
        [apt("ZZDS", wind=before)],
        [apt("ZZDS", wind=after, xwind=18.4, gust=28, rwy="05", t=T1)],
    )
    c = only(changes, "metar_wind")
    assert c.direction == direction
    assert c.tier == ("alert" if direction == "worse" else "highlight")
    assert c.message == f"ZZDS wind: {before} → {after} (crosswind 18 kt RWY 05, gust 28 kt)"


def test_wind_en_route_is_not_reported():
    changes, _ = classify([apt("ZZRT", enroute=100)], [apt("ZZRT", wind="red", t=T1, enroute=100)])
    assert changes == []


def test_one_report_can_raise_several_kinds():
    latest = apt("ZZDS", "METAR ZZDS 020700Z 22025G38KT 3000 +TSRA BKN008CB 18/17 Q1012",
                 "IFR", wx=["+TSRA"], wind="red", t=T1)
    changes, _ = classify([apt("ZZDS")], [latest])
    # +TSRA is convective only — one alert for the thunderstorm, not two.
    assert {c.kind for c in changes} == {"metar_category", "metar_convective", "metar_wind"}
    assert all(c.tier == "alert" for c in changes)


def test_heavy_shower_with_hail_is_significant_weather():
    changes, _ = classify([apt("ZZDS")], [apt("ZZDS", wx=["+SHRAGR"], t=T1)])
    c = only(changes, "metar_weather")
    assert c.to_value == "+SHRAGR, GR"


# --- Category at an en-route airport ----------------------------------------


@pytest.mark.parametrize("before,after,reported", [
    ("VFR", "MVFR", False),  # en-route MVFR is noise
    ("MVFR", "VFR", False),
    ("MVFR", "IFR", True),   # into IFR
    ("IFR", "MVFR", True),   # out of IFR
    ("VFR", "LIFR", True),
])
def test_en_route_category_only_around_ifr(before, after, reported):
    changes, _ = classify([apt("ZZRT", cat=before, enroute=100)], [apt("ZZRT", cat=after, t=T1, enroute=100)])
    cat = [c for c in changes if c.kind == "metar_category"]
    assert bool(cat) is reported
    if reported:
        assert cat[0].tier == "highlight"


# --- Only while it can still matter -----------------------------------------


def test_departure_stops_mattering_at_take_off():
    base, latest = [apt("ZZDP")], [apt("ZZDP", cat="IFR", wx=["+SHRA"], t=T1)]
    before, _ = classify(base, latest, now=DEP - timedelta(minutes=10), departure_at=DEP)
    assert {c.kind for c in before} == {"metar_category", "metar_weather"}
    assert {c.tier for c in before} == {"alert"}
    after, _ = classify(base, latest, now=DEP + timedelta(minutes=10), departure_at=DEP)
    assert after == []


def test_en_route_airport_stops_mattering_once_passed():
    ts = "METAR ZZRT 020900Z 05010KT 9999 TS FEW015CB 20/15 Q1020"
    base, latest = [apt("ZZRT", enroute=100)], [apt("ZZRT", ts, wx=["TS"], t=T1, enroute=100)]
    ahead, _ = classify(base, latest, flown_nm=60)
    assert len(ahead) == 1
    passed, _ = classify(base, latest, flown_nm=140)
    assert passed == []


def test_destination_and_alternate_matter_until_arrival():
    base = [apt("ZZDS", enroute=200), apt("ZZAL")]
    latest = [apt("ZZDS", cat="IFR", t=T1, enroute=200), apt("ZZAL", cat="IFR", t=T1)]
    changes, _ = classify(base, latest, now=DEP + timedelta(hours=1), departure_at=DEP, flown_nm=199)
    assert {c.icao for c in changes} == {"ZZDS", "ZZAL"}


# --- Reports that say nothing -----------------------------------------------


def test_briefing_report_itself_is_not_a_change():
    changes, _ = classify([apt("ZZDS", wind="green")], [apt("ZZDS", wind="red", t=T0)])
    assert changes == []


def test_missing_report_keeps_every_alert_memory():
    mem = ClassifierMemory(alerted={"conv:ZZDS": "TS", "wind:ZZDS": "red", "metar:ZZDS": "IFR"})
    missing = AirportObservation(
        icao="ZZDS", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZDS", metar_time=T1,
    )
    changes, new_mem = classify([apt("ZZDS")], [missing], memory=mem)
    assert changes == []
    assert new_mem.alerted == mem.alerted


def test_alert_fires_once_per_value_per_kind():
    ts = "METAR ZZDS 020700Z 05010KT 9999 TS FEW015CB 20/15 Q1020"
    base = [apt("ZZDS")]
    first, mem = classify(base, [apt("ZZDS", ts, wx=["TS"], t=T1, wind="amber")])
    assert all(c.new_alert for c in first)
    again, mem = classify(base, [apt("ZZDS", ts, wx=["TS"], t=T2, wind="amber")], memory=mem)
    assert not any(c.new_alert for c in again)
    worse, _ = classify(base, [apt("ZZDS", ts, wx=["TS"], t=T2 + timedelta(minutes=30), wind="red")], memory=mem)
    assert {c.kind: c.new_alert for c in worse} == {"metar_convective": False, "metar_wind": True}


def test_old_pack_baseline_without_cloud_types_reads_the_raw_report():
    """A pack written before §36 has metar_raw and metar_weather but nothing
    else new: a CB already in its report is not a new CB."""
    raw = "METAR ZZRT 020630Z 05010KT 9999 FEW015CB 20/15 Q1020"
    changes, _ = classify([apt("ZZRT", raw, enroute=100)], [apt("ZZRT", raw.replace("0630Z", "0700Z"), t=T1, enroute=100)])
    assert changes == []


def test_relevance_helper_keeps_unknown_positions():
    assert airport_relevant("route", None, departed=True, flown_nm=500.0) is True
    assert airport_relevant("route", 10.0, departed=True, flown_nm=None) is True
