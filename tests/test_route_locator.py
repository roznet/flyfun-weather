"""The route locator (#790): route positions in pilot words.

Synthetic route ZZDP → ZZMD → ZZAR along 50°N (fictional codes, made-up
towns), anchors placed at known distances, coast data off unless the test is
about the coast. 1° of longitude at 50°N ≈ 38.6 NM.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.coastline import coast_crossings, is_land
from weatherbrief.analysis.route_geometry import RouteTrack
from weatherbrief.analysis.route_locator import (
    HYSTERESIS_NM,
    RouteLocator,
    StationAnchor,
    when_text,
)

LAT = 50.0
NM_PER_DEG = 60.0 * 0.6427876  # cos(50°)


def _lon(nm: float) -> float:
    return nm / NM_PER_DEG


def _track(*nms: float) -> RouteTrack:
    return RouteTrack([(LAT, _lon(n)) for n in nms], [float(n) for n in nms])


def _locator(stations=(), coast=False, towns=None):
    track = _track(0, 100, 200)
    wps = [("ZZDP", LAT, _lon(0)), ("ZZMD", LAT, _lon(100)), ("ZZAR", LAT, _lon(200))]
    sts = [StationAnchor(icao, LAT, _lon(nm), nm, town) for icao, nm, town in stations]
    return RouteLocator.build(track, wps, sts, towns=towns or {"ZZDP": "Depton", "ZZAR": "Arrford"},
                              coast=coast)


# --- The rules table ----------------------------------------------------------


def test_near_an_anchor_names_it_with_its_town():
    loc = _locator([("ZZUA", 40, "Upton")])
    p = loc.locate_along(43)
    assert (p.text, p.short, p.relation, p.anchor_icao, p.anchor_name) == (
        "near Upton (ZZUA)", "near ZZUA", "near", "ZZUA", "Upton")


def test_before_and_after_the_closer_anchor():
    loc = _locator([("ZZUA", 40, "Upton"), ("ZZRJ", 70, "Rydal")])
    assert loc.locate_along(52).text == "12 NM after Upton (ZZUA)"
    assert loc.locate_along(60).text == "10 NM before Rydal (ZZRJ)"
    assert loc.locate_along(60).distance_nm == -10


def test_an_anchor_without_a_town_reads_by_icao():
    assert _locator().locate_along(90).text == "10 NM before ZZMD"


def test_between_two_anchors_when_none_is_within_20_nm():
    loc = _locator([("ZZUA", 20, "Upton")])
    p = loc.locate_along(55)
    assert (p.relation, p.text) == ("between", "between Upton (ZZUA) and ZZMD")


def test_lateral_only_past_3_nm_and_kept_apart():
    loc = _locator()
    assert loc.locate_along(50, -2.5).lateral is None
    p = loc.locate_along(50, -5.2)
    assert (p.lateral, p.side) == (", 5 NM left of track", "left")
    assert "left" not in p.text
    assert loc.locate_along(50, 7).side == "right"


def test_lat_lon_waypoints_are_not_anchors():
    track = _track(0, 50, 100)
    wps = [("ZZDP", LAT, _lon(0)), ("5000N00100E", LAT, _lon(50)), ("ZZAR", LAT, _lon(100))]
    loc = RouteLocator.build(track, wps, coast=False)
    assert [a.key for a in loc.anchors] == ["ZZDP", "ZZAR"]


def test_stations_outside_the_route_are_not_anchors():
    loc = _locator([("ZZBH", -29, "Behind"), ("ZZPS", 215, "Pastham")])
    assert {a.key for a in loc.anchors} == {"ZZDP", "ZZMD", "ZZAR"}


# --- Off the route's ends -----------------------------------------------------


def test_before_departure_reads_from_departure_with_a_compass_word():
    loc = _locator()
    p = loc.locate(LAT, _lon(-29))
    assert p.relation == "off_departure"
    assert p.text == "29 NM west of departure"
    assert p.short == "29 NM west of ZZDP"
    assert p.along_nm == pytest.approx(-29, abs=0.5)


def test_past_destination():
    loc = _locator()
    p = loc.locate(LAT, _lon(215))
    assert (p.relation, p.text) == ("past_destination", "15 NM past the destination")


def test_just_off_an_end_is_near_the_airport():
    assert _locator().locate(LAT, _lon(-3)).text == "near Depton (ZZDP)"


# --- Stretches ----------------------------------------------------------------


def test_span_names_both_ends():
    loc = _locator([("ZZUA", 40, "Upton"), ("ZZRJ", 70, "Rydal")])
    assert loc.span(41, 69) == "from Upton (ZZUA) to Rydal (ZZRJ)"
    assert loc.span(0, 200) == "along the whole route"
    assert loc.span(2, 52) == "from Depton (ZZDP) to 12 NM after Upton (ZZUA)"
    # Both ends at one anchor: the point, not "from X to X".
    assert loc.span(39, 42) == "near Upton (ZZUA)"


# --- Hysteresis -----------------------------------------------------------------


def test_a_moving_cell_keeps_its_anchor_across_the_midpoint():
    loc = _locator([("ZZUA", 50, "Upton"), ("ZZRJ", 70, "Rydal")])
    first = loc.locate_along(58)
    assert first.anchor == "ZZUA"
    # Past the midpoint, but within HYSTERESIS_NM of being the nearest.
    kept = loc.locate_along(61, prior=first)
    assert (kept.anchor, kept.text) == ("ZZUA", "11 NM after Upton (ZZUA)")
    assert loc.locate_along(61).anchor == "ZZRJ"  # without memory it flips
    moved = loc.locate_along(60 + HYSTERESIS_NM + 1, prior=kept)
    assert moved.anchor == "ZZRJ"


def test_near_holds_a_little_past_the_near_radius():
    loc = _locator([("ZZUA", 50, "Upton")])
    near = loc.locate_along(54)
    assert near.relation == "near"
    assert loc.locate_along(57, prior=near).relation == "near"
    assert loc.locate_along(57).relation == "after"


# --- A route that doubles back -----------------------------------------------


def test_doubling_back_anchors_each_pass():
    # ZZDP → ZZMD → ZZDP: out 100 NM east and back.
    track = RouteTrack([(LAT, _lon(0)), (LAT, _lon(100)), (LAT + 0.0001, _lon(0))], [0.0, 100.0, 200.0])
    wps = [("ZZDP", LAT, _lon(0)), ("ZZMD", LAT, _lon(100)), ("ZZDP", LAT + 0.0001, _lon(0))]
    loc = RouteLocator.build(track, wps, towns={"ZZDP": "Depton"}, coast=False)
    assert [a.key for a in loc.anchors] == ["ZZDP", "ZZMD", "ZZDP@200"]
    assert loc.dest.along_nm == 200
    assert loc.locate_along(190).text == "10 NM before Depton (ZZDP)"
    assert loc.locate_along(10).text == "10 NM after Depton (ZZDP)"
    assert loc.span(150, 200) == "from 50 NM after ZZMD to Depton (ZZDP)"


# --- Serialized ladder ----------------------------------------------------------


def test_ladder_round_trip_words_the_same():
    loc = _locator([("ZZUA", 40, "Upton")])
    back = RouteLocator.from_ladder([a.model_dump() for a in loc.ladder()], loc.total_nm)
    for x in (3, 25, 47, 80, 150, 199):
        assert back.locate_along(x).text == loc.locate_along(x).text
    assert back.span(41, 120) == loc.span(41, 120)
    with pytest.raises(ValueError):
        back.locate(LAT, _lon(10))


def test_round_trip_ladder_keeps_the_departure_rung():
    # ZZDP → ZZMD → ZZDP, the out-and-back the review asked about: the two
    # ends are separate rungs, so a reader of the ladder words it the same.
    track = RouteTrack([(LAT, _lon(0)), (LAT, _lon(100)), (LAT + 0.0001, _lon(0))], [0.0, 100.0, 200.0])
    wps = [("ZZDP", LAT, _lon(0)), ("ZZMD", LAT, _lon(100)), ("ZZDP", LAT + 0.0001, _lon(0))]
    loc = RouteLocator.build(track, wps, towns={"ZZDP": "Depton"}, coast=False)
    assert loc.dep.key != loc.dest.key
    back = RouteLocator.from_ladder([a.model_dump() for a in loc.ladder()], loc.total_nm)
    assert [a.key for a in back.anchors] == [a.key for a in loc.anchors]
    for x in (2, 10, 60, 100, 150, 190, 199):
        assert back.locate_along(x).text == loc.locate_along(x).text


def test_a_route_without_two_waypoints_is_refused():
    with pytest.raises(ValueError):
        RouteLocator.build(_track(0, 100), [("ZZDP", LAT, _lon(0))], coast=False)


def test_unreadable_coast_data_keeps_the_other_anchors(monkeypatch):
    import weatherbrief.analysis.coastline as coastline

    def broken(*_a, **_k):
        raise OSError("europe_land.json.gz missing")

    monkeypatch.setattr(coastline, "coast_crossings", broken)
    loc = _locator([("ZZUA", 40, "Upton")], coast=True)
    assert [a.key for a in loc.anchors] == ["ZZDP", "ZZUA", "ZZMD", "ZZAR"]
    assert loc.locate_along(42).text == "near Upton (ZZUA)"


# --- Time ------------------------------------------------------------------------


def test_when_text_is_the_clock_before_departure_and_minutes_in_flight():
    now = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    t = now + timedelta(minutes=23)
    assert when_text(t, now, departed=False) == "~10:23Z"
    assert when_text(t, now, departed=True) == "in ~25 min"
    assert when_text(now + timedelta(minutes=7), now, departed=True) == "in ~7 min"
    assert when_text(now + timedelta(minutes=1), now, departed=True) == "now"
    assert when_text(None, now, departed=True) == ""


# --- Coast (real data) ---------------------------------------------------------


EDLD, EHMZ, EGTF = (51.616, 6.865), (51.512, 3.731), (51.348, -0.559)


def _example_track() -> RouteTrack:
    from euro_aip.models.navpoint import NavPoint

    pts = [EDLD, EHMZ, EGTF]
    d = [0.0]
    for a, b in zip(pts, pts[1:]):
        d.append(d[-1] + NavPoint(*a).haversine_distance(NavPoint(*b))[1])
    return RouteTrack(pts, d)


def test_land_and_sea():
    assert is_land(51.616, 6.865)  # Dinslaken
    assert not is_land(51.5, 2.5)  # North Sea
    assert is_land(10.0, 100.0)  # outside the data: land, never a sea everywhere


def test_coast_crossings_on_the_north_sea_route():
    track = _example_track()
    out, in_ = coast_crossings(tuple(track.points), tuple(track.distances))
    assert out.kind == "coast_out" and 118 < out.along_nm < 135  # Walcheren
    assert in_.kind == "coast_in" and 210 < in_.along_nm < 235  # Kent / Thames estuary


def test_short_water_is_not_a_crossing():
    # Across the Westerschelde (a few NM of water) from Zeeland's south bank
    # to Walcheren: an estuary, not a sea crossing.
    track = RouteTrack([(51.33, 3.80), (51.50, 3.73)], [0.0, 10.5])
    assert coast_crossings(tuple(track.points), tuple(track.distances)) == ()


def test_example_flight_wording():
    """#790's acceptance on its example route EDLD → EHMZ → EGTF: Dortmund
    reads off the departure, the 143 NM cell over the sea after coast-out."""
    track = _example_track()
    wps = [("EDLD", *EDLD), ("EHMZ", *EHMZ), ("EGTF", *EGTF)]
    loc = RouteLocator.build(track, wps, towns={"EDLD": "Dinslaken", "EHMZ": "Middelburg", "EGTF": "Woking"})
    dortmund = loc.locate(51.518, 7.612)
    assert dortmund.relation == "off_departure"
    assert dortmund.text in ("28 NM east of departure", "29 NM east of departure")
    cell = loc.locate_along(143)
    assert cell.relation == "over_sea"
    assert cell.text.startswith("over the sea, ") and "after coast-out (Middelburg)" in cell.text
