"""The route locator on the live surfaces (#790): ribbon, glance, change rows,
highlight facts, Radar & lightning cards, and the true along-track position
(euro_aip's signed projection) where airports are timed and filtered.

Reuses the glance tests' synthetic ZZDP → ZZDS route along 50°N.
"""

from __future__ import annotations

from datetime import timedelta

from test_live_glance import NOW, ROUTE, _airport, _layer, _obs
from test_live_storms import DEP, cell, storms_at

from weatherbrief.analysis.flight_progress import FlightProgress
from weatherbrief.analysis.route_locator import for_route
from weatherbrief.models.live import LiveStorm, RoutePlace
from weatherbrief.tasks import live_highlight as lh
from weatherbrief.tasks.live_glance import build_glance
from weatherbrief.tasks.live_layer import _place_storms
from weatherbrief.tasks.live_significance import airport_relevant, storm_position_text
from weatherbrief.tasks.route_weather import _interpolate_airport_time


def _towned(a, town):
    return a.model_copy(update={"municipality": town})


def _observations():
    behind = _airport("ZZBH", 29.0)  # legacy enroute distance: wrongly "29 NM along"
    lat, lon = behind.lat, -29.0 / (60.0 * 0.6427876)
    behind = behind.model_copy(update={
        "lat": lat, "lon": lon, "along_track_nm": -29.0, "cross_track_nm": 0.0, "route_end": "departure",
    })
    return _obs(
        _towned(_airport("ZZDP", 0.0), "Depton"),
        _towned(_airport("ZZDS", 154.0), "Destry"),
        _towned(_airport("ZZRT", 80.0, cross=-6.0), "Rotby"),
        _towned(_airport("ZZUA", 40.0), "Upton"),
        _towned(behind, "Behindham"),
    )


def _built(layer, now=NOW):
    progress = FlightProgress.from_route(ROUTE, DEP, now)
    locator = for_route(ROUTE, layer.route_observations, track=progress.track, coast=False)
    _place_storms(layer.storms, locator, None)
    return build_glance(layer, ROUTE, progress, locator=locator), locator


def test_ribbon_station_behind_departure_is_placed_off_it_not_along():
    layer = _layer(observations=_observations())
    (_, ribbon), _ = _built(layer)
    st = {s.icao: s for s in ribbon.stations}
    assert st["ZZBH"].along_nm == -29.0
    assert st["ZZBH"].name == "Behindham"
    assert st["ZZBH"].place.relation == "off_departure"
    assert st["ZZBH"].place.text == "29 NM west of departure"
    # An en-route station reads against the others, never itself.
    assert st["ZZRT"].place.anchor != "ZZRT"
    assert st["ZZRT"].place.lateral == ", 6 NM left of track"
    assert st["ZZDP"].place is None  # the ends are anchors themselves
    assert [a.key for a in ribbon.anchors][:2] == ["ZZDP", "ZZDS"]


def test_glance_nearest_cell_reads_against_an_anchor():
    storms = storms_at(NOW, [cell("core35-a", "core35", along=55, cross=-2, peak=48.0)])
    layer = _layer(observations=_observations(), storms=storms)
    (glance, _), _ = _built(layer)
    enroute = next(line for line in glance.lines if line.phase == "enroute")
    assert "at 55 NM" not in enroute.text
    assert "nearest 2 NM left, 15 NM after ZZUA ~12:" in enroute.text


def test_sigmet_span_in_pilot_words():
    layer = _layer(observations=_observations())
    (glance, ribbon), _ = _built(layer)
    assert ribbon.sigmets[0].span_text == "from Depton (ZZDP) to 20 NM after Rotby (ZZRT)"
    enroute = next(line for line in glance.lines if line.phase == "enroute")
    assert "covers from Depton (ZZDP) to 20 NM after Rotby (ZZRT)" in enroute.text


def test_storm_position_text_uses_the_place():
    base = dict(id="s", cell_ids=["s"], lat=50.0, lon=1.0, peak_dbz=45.0, along_nm=60.0,
                offtrack_nm=8.0, cross_nm=8.0, side="right")
    place = RoutePlace(text="20 NM after Upton (ZZUA)", short="20 NM after ZZUA", relation="after")
    assert storm_position_text(LiveStorm(**base, place=place)) == "8 NM right of track, 20 NM after Upton (ZZUA)"
    assert storm_position_text(LiveStorm(**base)) == "8 NM right of track at 60 NM"  # pre-#790 layer
    off = RoutePlace(text="12 NM west of departure", short="12 NM west of ZZDP", relation="off_departure")
    assert storm_position_text(LiveStorm(**{**base, "end": "departure"}, place=off)) == "12 NM west of departure"


def test_storm_keeps_its_anchor_tick_to_tick():
    layer = _layer(observations=_observations(),
                   storms=storms_at(NOW, [cell("core35-a", "core35", along=58, cross=0.0)]))
    (_, _), locator = _built(layer)
    first = layer.storms.storms[0].place
    assert first.anchor == "ZZUA"
    # Next tick it has moved past the Upton–Rotby midpoint (60 NM).
    nxt = storms_at(NOW, [cell("core35-a", "core35", along=61, cross=0.0)])
    _place_storms(nxt, locator, layer.storms)
    assert nxt.storms[0].place.anchor == "ZZUA"


def test_an_airport_behind_departure_is_relevant_until_take_off():
    assert airport_relevant("route", -29.0, departed=False, flown_nm=0.0)
    assert not airport_relevant("route", -29.0, departed=True, flown_nm=5.0)


def test_eta_of_an_airport_behind_departure_is_the_departure():
    assert _interpolate_airport_time(DEP, 1.5, -29.0, 154.0) == DEP
    assert _interpolate_airport_time(DEP, 1.5, 77.0, 154.0) == DEP + timedelta(minutes=45)


def test_highlight_facts_carry_the_locator_words():
    layer = _layer(observations=_observations(), storms=storms_at(NOW, []))
    (glance, ribbon), _ = _built(layer)
    layer.glance, layer.ribbon = glance, ribbon
    facts = lh.facts(layer.model_dump(mode="json"))
    blob = str(facts)
    assert "NM along" not in blob
    assert facts["sigmets_ahead"][0]["covers"] == "from Depton (ZZDP) to 20 NM after Rotby (ZZRT)"


def test_radar_and_lightning_cards_read_against_anchors():
    from test_live_glance import _observed

    from weatherbrief.observed.summary import build_summary

    observed = _observed(flashes=3, radar={60: 48.0})
    locator = for_route(ROUTE, _observations(), coast=False)
    lines = build_summary(observed, locator)
    lightning = next(x for x in lines if x.startswith("Lightning:"))
    assert "nearest 12 NM from the route, near Depton (ZZDP)" in lightning
    # The radar clause reads the widest ring; this fixture's radar is in the 10 NM one.
    radar = next(x for x in build_summary(observed.model_copy(update={"radii_nm": [10]}), locator)
                 if x.startswith("Radar:"))
    assert "peak 48 dBZ within 10 NM of the route between Upton (ZZUA) and Rotby (ZZRT)" in radar
    assert not any("NM along" in x for x in lines)
    # Without a locator (a pack built before #790's wording): as before.
    assert any("NM along the route" in x for x in build_summary(observed))


def test_radar_peak_with_a_known_position_is_placed_itself():
    from test_live_glance import _observed

    from weatherbrief.observed.summary import build_summary

    observed = _observed(radar={60: 48.0}).model_copy(update={"radii_nm": [10]})
    st = next(s for s in observed.reflectivity.stations if s.station_id == "p60")
    st.annuli[0] = st.annuli[0].model_copy(update={"max_at_nm": 6.0, "max_bearing_deg": 180.0})
    locator = for_route(ROUTE, _observations(), coast=False)
    radar = next(x for x in build_summary(observed, locator) if x.startswith("Radar:"))
    # 6 NM south of the 60 NM point: right of an eastbound track.
    assert "peak 48 dBZ between Upton (ZZUA) and Rotby (ZZRT), 6 NM right of track" in radar
