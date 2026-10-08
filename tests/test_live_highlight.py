"""The Observed tab's model-written highlight (#697).

The grounding check carries the safety weight here: nothing else stands between
a model sentence and a cockpit screen, so every rule gets both a case it must
reject and a case it must let through. The rest pins the wiring that keeps the
model off the tick's critical path — carry-forward on unchanged facts, a
refused patch when the layer moved on, and a tick that survives the API
failing.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weatherbrief.models.live import LiveGlance, LiveHighlight
from weatherbrief.tasks import live_highlight as lh

SCENARIOS = Path(__file__).resolve().parent.parent / "app/flyfun-weather/flyfun-weatherUITests/LiveScenarios"


def _fg(**over) -> tuple[dict, dict]:
    """(facts, gate) for the wiring tests: the gate is the facts minus the
    clock, which is all the orchestration paths need to tell states apart."""
    f = _facts_with(**over)
    return f, {"phase": "en route", **{k: v for k, v in f.items() if k not in ("now", "flight")}}


def _facts_with(**over) -> dict:
    """A minimal facts block in the real shape, with overrides."""
    f = {
        "now": "09:00Z",
        "route": "LELL to LEMI, 276 NM",
        "flight": "en route, about 120 of 276 NM flown, arrival planned 10:30Z",
        "destination": {"icao": "LEMI", "metar_now": "VFR at 08:50Z", "taf_at_eta": "VFR"},
        "airports_along_route_ahead": {"notable": "none", "other_airports_ahead_all_VFR": 4},
        "sigmets_ahead": "none",
        "rain_ahead": "none within 10 NM of the track",
        "cells_ahead": f"none within 30 NM of the track",
        "changes_since_briefing": {"worse": "none", "improved_count": 0},
    }
    f.update(over)
    return f


# --- Grounding: figures and codes -------------------------------------------


def test_passes_a_grounded_line():
    f = _facts_with()
    assert lh.check_grounding("Quiet route ahead: VFR at both ends, no cells near the track.", f) is None


def test_rejects_an_invented_icao():
    f = _facts_with()
    assert "LFMD" in lh.check_grounding("LFMD showing IFR at arrival.", f)


def test_rejects_an_invented_figure():
    """The one error a pilot cannot catch: a distance or time that is not real."""
    f = _facts_with(cells_ahead={"count": 1, "nearest_to_track": [
        {"peak_dBZ": 48, "at_route_nm": 180, "off_track_nm": 6, "side": "right"}]})
    reason = lh.check_grounding("Cell 48 dBZ at 234 NM, 6 NM right of track.", f)
    assert "234" in reason


def test_accepts_figures_the_facts_carry():
    f = _facts_with(cells_ahead={"count": 1, "nearest_to_track": [
        {"peak_dBZ": 48, "at_route_nm": 180, "off_track_nm": 6, "side": "right"}]})
    assert lh.check_grounding("Cell at 180 NM, 6 NM right of track, peak 48 dBZ.", f) is None


def test_accepts_a_time_from_the_facts():
    f = _facts_with(cells_ahead={"count": 1, "nearest_to_track": [
        {"at_route_nm": 180, "off_track_nm": 4, "side": "left", "abeam_at": "09:47Z"}]})
    assert lh.check_grounding("Cell 4 NM left of track, abeam 09:47Z.", f) is None


# --- Grounding: place binding (#697 lesson 1) -------------------------------
# The failure this check exists for: a route airport's condition moved onto the
# destination. Both codes and both conditions appear *somewhere* in the facts,
# so the plain "is it in the facts" rule passes it.


def test_rejects_a_condition_moved_to_the_wrong_airport():
    f = _facts_with(
        destination={"icao": "LEMI", "metar_now": "VFR at 08:50Z", "taf_at_eta": "VFR"},
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "LIFR at 08:50Z, visibility 800 m"}],
            "other_airports_ahead_all_VFR": 3},
    )
    # Every token is in the facts; the attribution is not.
    reason = lh.check_grounding("LEMI reporting LIFR with 800 m visibility.", f)
    assert reason is not None and "LEMI" in reason and "LIFR" in reason


def test_accepts_the_same_condition_at_its_own_airport():
    f = _facts_with(
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "LIFR at 08:50Z, visibility 800 m"}],
            "other_airports_ahead_all_VFR": 3},
    )
    assert lh.check_grounding("LECH LIFR with 800 m visibility mid-route.", f) is None


def test_accepts_a_paraphrase_of_a_metar_code():
    """The facts say TSRA; the model may say thunderstorm. Same condition."""
    f = _facts_with(
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "IFR at 08:50Z, TSRA"}],
            "other_airports_ahead_all_VFR": 3},
    )
    assert lh.check_grounding("Thunderstorms reported at LECH mid-route.", f) is None


def test_skips_binding_on_a_negative_clause():
    """"no cell near LEMI" claims nothing about LEMI's own conditions."""
    f = _facts_with()
    assert lh.check_grounding("No cells within 30 NM of LEMI.", f) is None


def test_a_sigmet_span_may_name_the_airport_at_its_edge():
    """Measured false positive: the model named LEMI as the *end of a SIGMET
    span*, not as an airport reporting thunderstorms. The span does reach
    LEMI and LEMI itself is VFR, so the line is accurate."""
    f = _facts_with(
        destination={"icao": "LEMI", "metar_now": "VFR at 08:50Z", "taf_at_eta": "VFR"},
        sigmets_ahead=[{"what": "EMBD TS", "id": "LECB 3", "covers_route_nm": [235, 276],
                        "new_since_briefing": True}],
    )
    assert lh.check_grounding(
        "Two new SIGMETs: embedded thunderstorms from 235 NM to destination (LEMI).", f) is None
    # The guard is narrow: without the SIGMET framing the same claim is caught.
    assert lh.check_grounding("LEMI reporting thunderstorms.", f) is not None


# --- Grounding: voice rules -------------------------------------------------


@pytest.mark.parametrize("text", [
    "Thunderstorm mid-route, recommend a diversion.",
    "Destination LEMI VFR, safe to continue.",
    "Cell closing on track — avoid the last 50 NM.",
])
def test_rejects_verdict_words(text):
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 1, "nearest_to_track": [
        {"at_route_nm": 180, "off_track_nm": 4, "side": "left", "lightning_flashes": 12}]})
    reason = lh.check_grounding(text, f)
    assert reason is not None and "verdict" in reason


def test_thunderstorm_needs_lightning_in_the_facts():
    """§41: a radar core is a "cell"; the word thunderstorm needs lightning."""
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 0, "nearest_to_track": [
        {"peak_dBZ": 52, "at_route_nm": 180, "off_track_nm": 4, "side": "left"}]})
    reason = lh.check_grounding("Thunderstorm 4 NM left of track at 180 NM.", f)
    assert reason is not None and "lightning" in reason
    # The same tick, worded as a cell, is fine.
    assert lh.check_grounding("Cell 4 NM left of track at 180 NM, peak 52 dBZ.", f) is None


def test_a_stations_TS_does_not_make_a_radar_core_a_thunderstorm():
    """§41. An airport reporting TSRA licenses "thunderstorm at LECH"; it does
    not license "thunderstorm at 180 NM" about a core with no lightning. The
    place-binding rule cannot catch this one — there is no ICAO in the clause
    to bind to."""
    f = _facts_with(
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "IFR at 08:50Z, TSRA"}],
            "other_airports_ahead_all_VFR": 2},
        cells_ahead={"count": 1, "with_lightning": 0, "nearest_to_track": [
            {"peak_dBZ": 52, "at_route_nm": 180, "off_track_nm": 4, "side": "left"}]},
    )
    reason = lh.check_grounding("Thunderstorm 4 NM left of track at 180 NM.", f)
    assert reason is not None and "only a station reports TS" in reason
    # The same facts, said about the station that actually reports it: fine.
    assert lh.check_grounding("LECH reporting thunderstorms mid-route.", f) is None
    # ...and the core, correctly called a cell: fine.
    assert lh.check_grounding("Cell 4 NM left of track at 180 NM, peak 52 dBZ.", f) is None


def test_a_TS_sigmet_licenses_a_positional_thunderstorm():
    """A SIGMET for embedded TS over the route does carry the word."""
    f = _facts_with(
        sigmets_ahead=[{"what": "EMBD TS", "id": "LECB 2", "covers_route_nm": [10, 276]}],
        cells_ahead={"count": 1, "with_lightning": 0, "nearest_to_track": [
            {"peak_dBZ": 52, "at_route_nm": 180, "off_track_nm": 4, "side": "left"}]},
    )
    assert lh.check_grounding("SIGMET embedded thunderstorms from 10 NM to destination.", f) is None


def test_thunderstorm_allowed_when_a_cell_has_flashes():
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 1, "nearest_to_track": [
        {"at_route_nm": 180, "off_track_nm": 4, "side": "left", "lightning_flashes": 12}]})
    assert lh.check_grounding("Thunderstorm with lightning 4 NM left of track at 180 NM.", f) is None


def test_rejects_over_length():
    f = _facts_with()
    long = " ".join(["VFR"] * (lh.MAX_WORDS + 1))
    reason = lh.check_grounding(long, f)
    assert reason is not None and "too long" in reason


def test_rejects_empty():
    assert lh.check_grounding("   ", _facts_with()) == "empty"


def test_max_words_is_the_agreed_ceiling():
    """30 → 40 (owner, 2026-10-07, #697) → 60 with the move to Haiku 5.5
    (owner, 2026-10-08, #715). The prompt must state the same hard limit."""
    assert lh.MAX_WORDS == 60
    assert f"{lh.MAX_WORDS} words is a hard limit" in lh.SYSTEM


def test_a_line_under_the_ceiling_passes_and_one_over_fails():
    f = _facts_with()
    assert lh.check_grounding(" ".join(["VFR"] * 55), f) is None
    assert "too long" in lh.check_grounding(" ".join(["VFR"] * 61), f)


# --- Grounding: #715 false positives ----------------------------------------


def test_a_stated_absence_of_lightning_is_not_a_thunderstorm():
    """Haiku 5.5 writes "(no lightning)" after a cell; that is the opposite of
    claiming a thunderstorm, and the rule used to reject it."""
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 0, "nearest_to_track": [
        {"peak_dBZ": 40, "at_route_nm": 92, "off_track_nm": 3, "side": "right"}]})
    assert lh.check_grounding("Cell 3 NM right of track at 92 NM (no lightning).", f) is None
    assert lh.check_grounding("Cell 3 NM right of track at 92 NM, without thunderstorms.", f) is None
    # The claim itself is still caught, even next to a negation.
    reason = lh.check_grounding("Thunderstorm 3 NM right of track at 92 NM, no lightning seen yet.", f)
    assert reason is not None and "lightning" in reason


def test_low_ifr_spelled_out_binds_as_lifr():
    f = _facts_with(destination={"icao": "KGKY", "metar_now": "LIFR at 11:53Z", "taf_at_eta": "LIFR"})
    assert lh.check_grounding("Low IFR at destination KGKY now and at ETA.", f) is None
    reason = lh.check_grounding("KGKY reporting IFR.", f)
    assert reason is not None and "IFR" in reason


def test_an_airport_anchoring_a_distance_is_not_bound():
    """"85-125 NM from EGBJ" places the rain on the route; it says nothing about
    EGBJ's own weather."""
    f = _facts_with(
        route="EGBJ to EGNS, 159 NM",
        departure={"icao": "EGBJ", "metar_now": "VFR at 06:20Z"},
        rain_ahead={"stretches_where_radar_rain_lies_over_the_track_itself_nm": [[85, 125]]},
    )
    assert lh.check_grounding("Rain lies over the track 85-125 NM from EGBJ.", f) is None
    # The same airport as the subject is still bound.
    reason = lh.check_grounding("EGBJ reporting rain.", f)
    assert reason is not None and "EGBJ" in reason


@pytest.mark.parametrize("text", [
    "IFR 20 NM before LFMD.",
    "Fog 10 NM of LFMD.",
    "Showers 5 NM after LFMD.",
])
def test_before_after_and_of_still_bind_the_airport(text):
    """Review on PR #716: only from/past/beyond mark a distance anchor. With
    before/after/of the airport can be the subject, and LFMD is VFR here."""
    f = _facts_with(destination={"icao": "LFMD", "metar_now": "VFR at 08:50Z", "taf_at_eta": "VFR"},
                    rain_ahead={"stretches_where_radar_rain_lies_over_the_track_itself_nm": [[5, 20]]})
    reason = lh.check_grounding(text, f)
    assert reason is not None and "LFMD not given as" in reason
    # The same claim anchored with "from" is a position, not LFMD's weather.
    anchored = text.replace(" before ", " from ").replace(" of ", " from ").replace(" after ", " from ")
    assert lh.check_grounding(anchored, f) is None


@pytest.mark.parametrize("text", [
    "Quiet route ahead.\n\nWait, that contains a banned word. Corrected highlight: Quiet route ahead.",
    "Quiet route ahead. Corrected to avoid the banned word.",
    "Arrived, so the facts describe nothing ahead.",
])
def test_drafting_text_is_not_a_highlight(text):
    """#715: with thinking off, Haiku 5.5 sometimes wrote its self-correction
    or its reasoning into the reply. Short enough leaks would pass every other
    rule, so the shape itself rejects."""
    reason = lh.check_grounding(text, _facts_with())
    assert reason is not None and "not a highlight" in reason
    # The quiet line on its own is fine.
    assert lh.check_grounding("Quiet route ahead.", _facts_with()) is None


@pytest.mark.parametrize("text", ["Watch LFAC, MVFR at 130 NM along.", "Monitor the destination TAF."])
def test_watch_and_monitor_are_advice(text):
    f = _facts_with(airports_along_route_ahead={
        "notable": [{"icao": "LFAC", "role": "route", "where": "130 NM along, 19 NM left of track",
                     "metar_now": "MVFR at 11:00Z"}], "other_airports_ahead_all_VFR": 3})
    reason = lh.check_grounding(text, f)
    assert reason is not None and reason.startswith("advice word")


# --- Facts ------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "2026-10-02_lell_lemi_0510.json",
    "2026-10-02_lell_lemi_0710.json",
    "2026-10-02_lell_lemi_0830.json",
    "2026-10-02_lell_lemi_0900.json",
])
def test_facts_from_a_real_layer(name):
    """Every acceptance scenario produces a block with a real route and a
    bounded size — the experiment found the model drops items once it grows."""
    layer = json.loads((SCENARIOS / name).read_text())
    f = lh.facts(layer.get("body", layer))
    assert f["route"].startswith("LELL to LEMI")
    assert f["now"] and f["now"].endswith("Z")
    # Keep it "not too much": the calibrated blocks run 1.1–1.5 kB.
    assert len(json.dumps(f)) < 4000
    for key in ("sigmets_ahead", "rain_ahead", "cells_ahead", "changes_since_briefing"):
        assert key in f


# --- Regeneration gate (#706) -----------------------------------------------
#
# A synthetic tick in the serialized ``live.json`` shape, so the gate is
# exercised through ``facts_and_gate`` exactly as the tick runs it. Fictional
# ZZ.. aerodromes throughout.

ROUTE_NM = 300


def _obs(icao, along, *, cat="VFR", ceil=None, vis=9999, wx=(), cloud="FEW040",
         time="2026-10-08T09:00:00Z", gust=None, wind="green", rwy="27"):
    return {
        "icao": icao, "distance_from_route_nm": 5.0, "enroute_distance_nm": along,
        "nearest_waypoint_icao": icao, "has_metar": True,
        "metar_raw": f"METAR {icao} 080900Z 27010KT 9999 {' '.join(wx)} {cloud} 15/10 Q1015",
        "metar_time": time, "metar_flight_category": cat, "metar_ceiling_ft": ceil,
        "metar_visibility_m": vis, "metar_weather": list(wx), "metar_wind_gust_kt": gust,
        "metar_wind_advisory": wind, "metar_best_runway_id": rwy,
    }


def _station(icao, along, role="route", cross=4.0):
    return {"icao": icao, "role": role, "along_nm": along, "cross_nm": cross,
            "eta": "2026-10-08T10:00:00Z", "convective": []}


def _live(*, flown=100.0, airports=(), dest=None, sigmets=(), weather=None, storms=None, changes=()):
    """One tick. ``airports`` are (station, obs) pairs along the route."""
    dest_obs = dest or _obs("ZZDS", ROUTE_NM)
    return {
        "glance": {"as_of": "2026-10-08T09:00:00Z"},
        "ribbon": {
            "route_nm": ROUTE_NM, "flown_nm": flown,
            "departure_at": "2026-10-08T08:00:00Z", "arrival_at": "2026-10-08T10:30:00Z",
            "waypoints": [{"icao": "ZZDP"}, {"icao": "ZZDS"}],
            "stations": [_station("ZZDP", 0, "departure"), _station("ZZDS", ROUTE_NM, "destination")]
                        + [st for st, _o in airports],
            "sigmets": list(sigmets),
            "weather": list(weather or []), "weather_status": "available" if weather is not None else "unavailable",
            "segments": [],
        },
        "route_observations": {"airports": [_obs("ZZDP", 0), dest_obs] + [o for _s, o in airports]},
        "storms": storms if storms is not None else {"status": "unavailable"},
        "changes": {"changes": list(changes)},
    }


def _gate(live):
    return lh.facts_and_gate(live)[1]


def _changed(a, b) -> list[str]:
    return lh.gate_changes(_gate(a), _gate(b))


def _dest(**kw):
    return _live(dest=_obs("ZZDS", ROUTE_NM, **kw))


def test_metar_observation_time_alone_does_not_regenerate():
    assert _changed(_dest(time="2026-10-08T09:00:00Z"), _dest(time="2026-10-08T09:30:00Z")) == []


@pytest.mark.parametrize("cat,lo,hi", [("MVFR", 2400, 2900), ("IFR", 600, 800)])
def test_a_ceiling_move_inside_its_category_does_not_regenerate(cat, lo, hi):
    assert _changed(_dest(cat=cat, ceil=lo), _dest(cat=cat, ceil=hi)) == []


def test_a_gust_below_the_threshold_does_not_regenerate():
    """No runway data, so no advisory: gusts count from GUST_NOTABLE_KT."""
    assert _changed(_dest(wind=None, gust=18), _dest(wind=None, gust=22)) == []
    assert _changed(_dest(wind=None, gust=18), _dest(wind=None, gust=28)) == ["destination"]


def test_a_runway_change_inside_the_wind_band_does_not_regenerate():
    assert _changed(_dest(wind="amber", rwy="27"), _dest(wind="amber", rwy="09")) == []


@pytest.mark.parametrize("codes,words", [
    (["-SHRA", "BR"], ["showers"]),          # mist left out; showers say the rain
    (["FZFG"], ["freezing fog"]),
    (["+RASN"], ["heavy rain", "heavy snow"]),
    (["VCSH"], ["showers nearby"]),
    (["TSRA"], []),                          # said once, as TS (convective)
])
def test_weather_is_named_by_family(codes, words):
    from weatherbrief.models.observations import AirportObservation

    obs = AirportObservation(icao="ZZAA", distance_from_route_nm=0, nearest_waypoint_icao="ZZAA",
                             metar_weather=codes)
    assert lh._weather_families(obs) == words


def test_light_and_moderate_showers_are_the_same_state():
    assert _changed(_dest(wx=["-SHRA"]), _dest(wx=["SHRA"])) == []


@pytest.mark.parametrize("before,after", [
    ({"cat": "VFR"}, {"cat": "MVFR", "ceil": 2500}),          # category change
    ({}, {"cloud": "BKN030CB"}),                             # new CB
    ({"wind": "green"}, {"wind": "amber"}),                  # wind band change
    ({}, {"wx": ["+SHRA"]}),                                  # heavy showers
    ({}, {"wx": ["TSRA"]}),                                   # thunderstorm
])
def test_a_real_metar_change_regenerates(before, after):
    assert _changed(_dest(**before), _dest(**after)) == ["destination"]


def test_a_new_sigmet_regenerates_and_so_does_a_reissue():
    s2 = {"label": "ZZFR 2: EMBD TS", "hazard": "TS", "qualifier": "EMBD", "from_nm": 150, "to_nm": 300}
    s3 = dict(s2, label="ZZFR 3: EMBD TS")
    assert _changed(_live(), _live(sigmets=[s2])) == ["sigmets"]
    # #706 item 10: the highlight quotes the id, so a reissue must regenerate.
    assert _changed(_live(sigmets=[s2]), _live(sigmets=[s3])) == ["sigmets"]


def _sim(ticks: list[dict]) -> int:
    """How many generations a run of ticks costs through the real
    ``carry_forward``: a tick with no carried highlight generates one."""
    gens, stored = 0, None
    for live in ticks:
        layer = _Layer(glance=_glance(), dump=live)
        if not lh.carry_forward(stored, layer):
            f, gate = lh.facts_and_gate(live)
            gens += 1
            layer.glance.highlight = _highlight(facts_hash=lh.gate_hash(gate), gate=gate)
        stored = layer
    return gens


def test_drift_across_a_category_boundary_regenerates_once():
    """Baseline is the state at the last generation: three small ceiling steps
    that only together cross VFR → MVFR regenerate once, at the crossing — and
    the in-category steps either side of it never do."""
    ceilings = [3400, 3200, 3050, 2900, 2700, 2500]
    ticks = [_dest(cat="VFR" if c >= 3000 else "MVFR", ceil=c) for c in ceilings]
    assert _sim(ticks) == 2  # the first tick, then the crossing


def test_a_quiet_flight_does_not_regenerate_on_progress_or_metar_cadence():
    times = ["09:00", "09:30", "10:00", "10:30"]
    ticks = [_live(flown=60 + 20 * i, dest=_obs("ZZDS", ROUTE_NM, time=f"2026-10-08T{t}:00Z"))
             for i, t in enumerate(times)]
    assert _sim(ticks) == 1


def _notable_airport(icao, along, **kw):
    return _station(icao, along), _obs(icao, along, cat="IFR", ceil=800, **kw)


def test_passing_a_notable_airport_does_not_regenerate():
    ap = _notable_airport("ZZRA", 120)
    before = _live(flown=100, airports=[ap])
    after = _live(flown=130, airports=[ap])  # 120 < 130 - 5: behind
    assert "ZZRA" not in json.dumps(lh.facts(after))
    assert _changed(before, after) == []


def test_an_airport_turning_quiet_ahead_regenerates():
    ap = _notable_airport("ZZRA", 200)
    quiet = (_station("ZZRA", 200), _obs("ZZRA", 200))
    assert _changed(_live(airports=[ap]), _live(airports=[quiet])) == ["airport ZZRA no longer notable"]
    assert _changed(_live(airports=[quiet]), _live(airports=[ap])) == ["airport ZZRA now notable"]


def test_the_next_airport_sliding_into_the_cap_does_not_regenerate():
    """13 notable airports, 12 shown: passing the first slides the 13th in.
    The gate tracks all of them, so that is not "now notable"."""
    aps = [_notable_airport(f"ZZ{chr(65 + i)}{chr(65 + i)}", 110 + 10 * i) for i in range(13)]
    before, after = _live(flown=100, airports=aps), _live(flown=120, airports=aps)
    assert len(lh.facts(before)["airports_along_route_ahead"]["notable"]) == 12
    assert _changed(before, after) == []


def _band(lo, hi, cross=(-2, 2), motion=None, tier="rain"):
    prof = [(a + 2.5, *cross) for a in range(int(lo), int(hi), 5)]
    return {"id": f"r{lo}", "tier": tier, "from_nm": lo, "to_nm": hi, "side": "both",
            "near_nm": 0, "far_nm": 2, "profile": prof, "motion_rel_deg": motion}


def test_rain_edges_moving_inside_a_bin_do_not_regenerate():
    assert _changed(_live(weather=[_band(130, 160)]), _live(weather=[_band(130, 165)])) == []


def test_rain_moving_into_a_new_bin_regenerates():
    assert _changed(_live(weather=[_band(130, 160)]), _live(weather=[_band(130, 190)])) == ["rain"]


def test_flank_rain_coverage_crossing_a_band_regenerates():
    """#706 item 9: 5 % → 80 % of the route ahead must regenerate on its own."""
    small = _live(weather=[_band(150, 155, cross=(6, 9))])
    wide = _live(weather=[_band(110, 280, cross=(6, 9))])
    assert _changed(small, wide) == ["rain"]


def test_the_main_rain_area_word_is_not_gated_and_ignores_rain_behind():
    behind = _band(20, 60, cross=(15, 25), motion=170)
    ahead = _band(150, 160, cross=(15, 25), motion=10)
    f = lh.facts(_live(weather=[behind, ahead]))
    # Rain behind the aircraft no longer reads as "moving toward the flight".
    assert f["rain_ahead"]["main_rain_area"].startswith("moving along the route")
    assert lh.facts(_live(weather=[behind]))["rain_ahead"].get("main_rain_area") is None
    assert _changed(_live(weather=[ahead]), _live(weather=[dict(ahead, motion_rel_deg=170)])) == []


def _storm(off, *, along=150, dbz=42, flashes=None, motion="parallel", abeam="2026-10-08T09:40:00Z"):
    return {"id": f"s{along}", "ahead": True, "peak_dbz": dbz, "along_nm": along, "offtrack_nm": off,
            "side": "left", "abeam_eta": abeam, "flashes": flashes, "relative_motion": motion,
            "closing_kt": 12 if motion == "closing" else None, "trend": "steady"}


def _cells(*storms):
    return {"status": "available", "corridor_nm": 30, "storms": list(storms)}


def test_cell_figures_moving_do_not_regenerate():
    a = _live(storms=_cells(_storm(6, along=150, dbz=40)))
    b = _live(storms=_cells(_storm(7, along=152, dbz=44, abeam="2026-10-08T09:38:00Z")))
    assert _changed(a, b) == []


@pytest.mark.parametrize("after", [
    _storm(6, flashes=3),               # lightning appears
    _storm(2),                          # nearest band ≤ 10 → ≤ 3 NM
    _storm(6, motion="closing"),        # closing within 10 NM
])
def test_a_real_cell_change_regenerates(after):
    assert _changed(_live(storms=_cells(_storm(6))), _live(storms=_cells(after))) == ["cells"]


def test_a_cell_exactly_on_track_counts_as_closing():
    """``offtrack_nm == 0`` is on the track, not unknown."""
    assert _gate(_live(storms=_cells(_storm(0, motion="closing"))))["cells"]["closing_on_track"]


def test_the_gate_survives_a_json_round_trip():
    """The gate is stored in ``live.json`` and compared with ``!=`` after a
    reload: a tuple turned list would regenerate every tick."""
    live = _live(
        airports=[(_station("ZZRA", 150), _obs("ZZRA", 150, cat="IFR", ceil=400, wx=("SHRA",), wind="amber"))],
        sigmets=[{"label": "ZZ1: TS", "hazard": "TS", "from_nm": 120, "to_nm": 180, "new": True}],
        weather=[_band(140, 190, motion=90)],
        storms=_cells(_storm(2, flashes=3, motion="closing")),
        changes=[_row("ZZRA:cat", "metar_category", "ZZRA VFR → IFR", icao="ZZRA", to="IFR")],
    )
    g = _gate(live)
    assert lh.gate_changes(json.loads(json.dumps(g)), g) == []


def test_a_malformed_airport_row_does_not_drop_the_facts():
    bad = _obs("ZZRB", 150)
    bad["metar_visibility_m"] = "not a number"
    f, g = lh.facts_and_gate(_live(airports=[(_station("ZZRB", 150), bad)]))
    assert f["airports_along_route_ahead"]["notable"] == "none"


def _row(key, kind, message, *, icao=None, role="route", to=None, tier="alert", direction="worse"):
    return {"key": key, "kind": kind, "message": message, "icao": icao, "role": role,
            "to_value": to, "tier": tier, "direction": direction, "enroute_distance_nm": 120.0}


def test_storm_rows_are_left_out_of_the_facts_and_the_gate():
    storm = _row("storm:s1", "storm", "Convective activity at 102 NM ahead, peak 42 dBZ")
    f, gate = lh.facts_and_gate(_live(changes=[storm]))
    assert f["changes_since_briefing"]["worse"] == "none"
    assert gate["changes"] == []


def test_a_change_row_reworded_with_the_same_identity_does_not_regenerate():
    pending = _row("sigmet:ZZFR|3", "sigmet_issued", "New SIGMET ZZFR 3: EMBD TS from 08:35Z", to="EMBD TS")
    valid = dict(pending, message="New SIGMET ZZFR 3: EMBD TS")
    assert _changed(_live(changes=[pending]), _live(changes=[valid])) == []
    cleared = dict(pending, kind="sigmet_cancelled", direction="better", to=None)
    assert _changed(_live(changes=[pending]), _live(changes=[cleared])) == ["changes"]


def test_improvements_and_quiet_airports_are_flags_out_of_the_gate():
    better = _row("metar:ZZRB", "metar_category", "ZZRB METAR: IFR → VFR", icao="ZZRB",
                  direction="better", tier="highlight")
    q1 = (_station("ZZRB", 200), _obs("ZZRB", 200))
    q2 = (_station("ZZRC", 220), _obs("ZZRC", 220))
    a = _live(airports=[q1, q2])
    b = _live(airports=[q2], changes=[better])
    assert _changed(a, b) == []
    f = lh.facts(b)
    assert f["changes_since_briefing"]["improved_since_briefing"] == "some"
    assert f["airports_along_route_ahead"]["other_airports_ahead"] == "all VFR"


def _busy(**over) -> dict:
    """A busy tick with every figure the gate ignores set to one value."""
    t = over.get("time", "2026-10-08T09:00:00Z")
    return _live(
        flown=over.get("flown", 100),
        dest=_obs("ZZDS", ROUTE_NM, cat="IFR", ceil=over.get("ceil", 700), vis=over.get("vis", 4000),
                  wx=["-SHRA"], time=t, wind="amber", rwy=over.get("rwy", "27"), gust=over.get("gust", 27)),
        airports=[(_station("ZZRA", 200), _obs("ZZRA", 200, cat="MVFR", ceil=over.get("ceil_ra", 2400),
                                               wx=["BR"], time=t))],
        sigmets=[{"label": "ZZFR 2: EMBD TS", "hazard": "TS", "qualifier": "EMBD",
                  "from_nm": 150, "to_nm": 300}],
        weather=[_band(130, over.get("rain_to", 160), motion=over.get("motion", 10))],
        storms=_cells(_storm(over.get("off", 6), along=over.get("along", 150), dbz=over.get("dbz", 42),
                             flashes=4, abeam=over.get("abeam", "2026-10-08T09:40:00Z"))),
        changes=[_row("metar:ZZDS", "metar_category", "ZZDS METAR: VFR → IFR", icao="ZZDS",
                      role="destination", to="IFR"),
                 _row("wind:ZZDS", "metar_wind", f"ZZDS wind: green → amber (crosswind {over.get('xw', 14)} kt RWY 27)",
                      icao="ZZDS", role="destination", to="amber")],
    )


def test_the_facts_never_show_a_figure_the_gate_ignores():
    """#706 item 5. Move every figure the gate ignores at once: the gate holds,
    and the facts the model reads are then identical too, apart from the
    clock, the flown figure and the rain-motion word (all three named in
    ``facts_and_gate``) — so a carried-forward highlight cannot quote a figure
    that has since moved."""
    base = _busy()
    moved = _busy(time="2026-10-08T09:30:00Z", flown=104, ceil=800, vis=3500, ceil_ra=2800, gust=29,
                  rwy="09", rain_to=165, motion=150, off=7, along=153, dbz=45,
                  abeam="2026-10-08T09:37:00Z", xw=17)
    assert _changed(base, moved) == []

    def comparable(f):
        f = json.loads(json.dumps(f))
        for k in ("now", "flight"):
            f.pop(k)
        f["rain_ahead"].pop("main_rain_area", None)
        return f

    assert comparable(lh.facts(base)) == comparable(lh.facts(moved))


def test_an_airport_condition_is_named_without_its_figures():
    words = lh.facts(_busy())["destination"]["metar_now"]
    assert words == "IFR (low ceiling, low visibility), showers, wind advisory amber"
    assert not any(ch.isdigit() for ch in words)


def test_the_facts_still_bind_for_the_grounding_check():
    """The new wording must still license the words the model will use."""
    f = lh.facts(_busy())
    assert lh.check_grounding("ZZDS IFR with showers and a gusty wind.", f) is None
    assert lh.check_grounding("Cells near the track ahead, one with lightning: thunderstorm risk.", f) is None


def test_no_highlight_after_planned_arrival(monkeypatch, tmp_path):
    """#706 item 6: nothing generated, and nothing carried forward either."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "generate", lambda *a, **k: pytest.fail("should not call the model"))
    arrived = _live(flown=ROUTE_NM)
    out = lh.ensure_highlight(tmp_path, _Layer(glance=_glance(), dump=arrived))
    assert out.outcome == "skipped" and out.reason == "after planned arrival"
    gate = _gate(_live(flown=290))
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash=lh.gate_hash(gate), gate=gate)))
    fresh = _Layer(glance=_glance(), dump=arrived)
    assert lh.carry_forward(stored, fresh) is False
    assert fresh.glance.highlight is None


def test_a_highlight_from_before_the_gate_regenerates_once():
    """No stored gate state to compare with: regenerate, then gate."""
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash="old-shape")))
    assert lh.carry_forward(stored, _Layer(glance=_glance(), dump=_live())) is False


def test_a_carried_highlight_keeps_its_baseline():
    """The kept highlight's gate is the one it was written from, so the next
    comparison is still against that state, not this tick's."""
    g0 = _gate(_dest(cat="VFR", ceil=3400))
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash=lh.gate_hash(g0), gate=g0)))
    fresh = _Layer(glance=_glance(), dump=_dest(cat="VFR", ceil=3200, time="2026-10-08T09:30:00Z"))
    assert lh.carry_forward(stored, fresh) is True
    assert fresh.glance.highlight.gate == g0


def test_gate_from_a_real_layer():
    """The real LELL→LEMI 08:30 tick, replayed 10 minutes later with the
    weather byte-identical: same gate state, and the facts keep the real
    flown figure."""
    layer = json.loads((SCENARIOS / "2026-10-02_lell_lemi_0830.json").read_text())
    layer = layer.get("body", layer)
    later = json.loads(json.dumps(layer))
    rate = later["ribbon"]["route_nm"] / 1.5
    later["ribbon"]["flown_nm"] = later["ribbon"]["flown_nm"] + rate / 6
    assert lh.facts(layer)["flight"] != lh.facts(later)["flight"]
    assert lh.gate_changes(_gate(layer), _gate(later)) == []


def test_haiku_5_5_is_priced():
    """#715: an unpriced model raises, which would blank the review log's
    cost_usd and fail the ledger charge. ~1750 in / ~85 out on the A/B."""
    from weatherbrief.costs import compute_call_cost

    assert lh.DEFAULT_MODEL == "claude-haiku-5-5"
    cost = compute_call_cost("claude-haiku-5-5", input_tokens=1750, output_tokens=85)
    assert cost == pytest.approx(0.0002175, abs=1e-6)


def test_haiku_5_5_long_prompts_take_the_higher_tier():
    """Above 100k input tokens the whole call bills at $0.50/$2.50 per MTok;
    the flat short rate would under-bill it silently (review on PR #716)."""
    from weatherbrief.costs import compute_call_cost

    assert compute_call_cost("claude-haiku-5-5", input_tokens=100_000, output_tokens=1000) == pytest.approx(0.0105)
    assert compute_call_cost("claude-haiku-5-5", input_tokens=100_001, output_tokens=1000) == pytest.approx(0.0525, abs=1e-5)
    # Other models are untouched by the tier table.
    assert compute_call_cost("claude-haiku-4-5", input_tokens=200_000, output_tokens=0) == pytest.approx(0.2)


def test_call_cost_prices_a_real_usage_block():
    """Measured: ~1450 in / ~45 out is about $0.0016 on Haiku 4.5."""
    cost = lh.call_cost({"model": "claude-haiku-4-5", "input_tokens": 1450, "output_tokens": 45})
    assert cost == pytest.approx(0.001675, abs=1e-5)
    assert lh.call_cost(None) is None
    assert lh.call_cost({"input_tokens": 10}) is None


# --- Wiring -----------------------------------------------------------------


class _Layer:
    """Enough of a LiveLayer for the orchestration paths."""

    def __init__(self, *, glance, ribbon=object(), flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00",
                 dump=None):
        self.glance = glance
        self._dump = dump or {}
        self.ribbon = ribbon
        self.flight_id = flight_id
        self.pack_timestamp = pack_timestamp

    def model_dump(self, mode="json"):
        return self._dump


def _glance(as_of=None, highlight=None) -> LiveGlance:
    return LiveGlance(
        as_of=as_of or datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        headline="Observed 09:00Z · as briefed",
        comparison="as_briefed",
        lines=[],
        highlight=highlight,
    )


def _highlight(facts_hash="abc", text="Quiet route ahead.", gate=None) -> LiveHighlight:
    return LiveHighlight(text=text, model="claude-haiku-4-5", facts_hash=facts_hash, gate=gate,
                         generated_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))


def test_carry_forward_keeps_the_previous_highlight_on_unchanged_facts(monkeypatch):
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    digest = lh.gate_hash(_fg()[1])
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash=digest, gate=_fg()[1])))
    fresh = _Layer(glance=_glance())
    assert lh.carry_forward(stored, fresh) is True
    assert fresh.glance.highlight is not None
    assert fresh.glance.highlight.facts_hash == digest


def test_carry_forward_drops_a_highlight_whose_facts_moved(monkeypatch):
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    old = _fg(sigmets_ahead=[{"what": "EMBD TS", "id": "LECB 2"}])[1]
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash=lh.gate_hash(old), gate=old)))
    fresh = _Layer(glance=_glance())
    assert lh.carry_forward(stored, fresh) is False
    assert fresh.glance.highlight is None


def test_ensure_highlight_reuses_without_calling_the_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    called = []
    monkeypatch.setattr(lh, "generate", lambda *a, **k: called.append(1) or ("x", {}, 1))
    layer = _Layer(glance=_glance(highlight=_highlight(text="carried")))
    out = lh.ensure_highlight("/tmp", layer)
    assert out.outcome == "reused" and out.text == "carried"
    assert not called


def test_ensure_highlight_skips_a_layer_without_a_ribbon(monkeypatch):
    """A pre-#695 layer has no route to lead with — never pay for that call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "generate", lambda *a, **k: pytest.fail("should not call the model"))
    layer = _Layer(glance=_glance(), ribbon=None)
    assert lh.ensure_highlight("/tmp", layer).outcome == "skipped"


def test_ensure_highlight_is_off_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(lh, "generate", lambda *a, **k: pytest.fail("should not call the model"))
    assert lh.ensure_highlight("/tmp", _Layer(glance=_glance())).outcome == "skipped"


def test_kill_switch_stops_generation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("DISABLE_LIVE_HIGHLIGHT", "1")
    assert lh.highlight_enabled() is False


def test_api_failure_leaves_the_tick_intact(monkeypatch, tmp_path):
    """The acceptance criterion: a failed call must not raise into the tick."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())

    def boom(*a, **k):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(lh, "generate", boom)
    out = lh.ensure_highlight(tmp_path, _Layer(glance=_glance()))
    assert out.outcome == "call_failed" and out.text is None
    # and the attempt is on record, so a run of failures is visible
    log = (tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()
    assert json.loads(log[0])["outcome"] == "call_failed"


def test_a_rejected_line_is_logged_with_its_text(monkeypatch, tmp_path):
    """A false rejection must be reviewable, not silent: the log keeps the
    text and the facts behind it, which is what the calibration period reads."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    monkeypatch.setattr(lh, "generate", lambda *a, **k: ("LFMD is IFR.", {"model": "claude-haiku-4-5"}, 900))
    out = lh.ensure_highlight(tmp_path, _Layer(glance=_glance()))
    assert out.outcome == "rejected"
    rec = json.loads((tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()[0])
    assert rec["text"] == "LFMD is IFR."
    assert rec["facts"]["route"].startswith("LELL")
    assert "LFMD" in rec["reason"]


def test_a_rejected_facts_state_is_retried_once_then_given_up(monkeypatch, tmp_path):
    """The tick retries any flight with no stored highlight and a rejection
    stores nothing, so uncapped this bought a rejected sentence every tick for
    the whole window. One retry, because the model is stochastic and a bad
    draw deserves a second chance; then stop paying."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    calls = []

    def bad(*a, **k):
        calls.append(1)
        return "LFMD is IFR.", {"model": "claude-haiku-4-5", "input_tokens": 1000, "output_tokens": 10}, 900

    monkeypatch.setattr(lh, "generate", bad)
    layer = _Layer(glance=_glance())

    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert len(calls) == 2
    # Third tick on the same facts: no further billed call.
    out = lh.ensure_highlight(tmp_path, layer)
    assert out.outcome == "skipped" and "rejected attempts" in out.reason
    assert len(calls) == 2
    # ...but the skip is still on record, so the frequency stays visible.
    records = [json.loads(l) for l in (tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()]
    assert [r["outcome"] for r in records] == ["rejected", "rejected", "skipped_rejected"]
    # and the marker is cheap — no facts block repeated every tick
    assert "facts" not in records[-1]


def test_a_different_facts_state_generates_again(monkeypatch, tmp_path):
    """The cap is per facts state, not per flight: when the weather moves, the
    flight gets a fresh go."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "generate",
                        lambda *a, **k: ("LFMD is IFR.", {"model": "claude-haiku-4-5"}, 900))
    layer = _Layer(glance=_glance())
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "skipped"
    # weather moves -> new facts hash -> allowed to try again
    monkeypatch.setattr(lh, "facts_and_gate_for",
                        lambda layer: _fg(sigmets_ahead=[{"what": "EMBD TS", "id": "LECB 2"}]))
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"


def test_a_failed_call_does_not_count_against_the_retry_cap(monkeypatch, tmp_path):
    """A timeout costs nothing and is right to retry; only billed rejections
    count."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())

    def boom(*a, **k):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(lh, "generate", boom)
    layer = _Layer(glance=_glance())
    for _ in range(5):
        assert lh.ensure_highlight(tmp_path, layer).outcome == "call_failed"
    assert lh.rejected_attempts(tmp_path, lh.gate_hash(_fg()[1])) == 0


def test_a_failed_call_is_logged_without_the_facts_block(monkeypatch, tmp_path):
    """There is no text to judge against the facts, so they add nothing to the
    review — and a timeout retries every tick by design, which with the block
    attached wrote ~1.8 kB per flight per tick for as long as an outage ran."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())

    def boom(*a, **k):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(lh, "generate", boom)
    lh.ensure_highlight(tmp_path, _Layer(glance=_glance()))
    rec = json.loads((tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()[0])
    assert rec["outcome"] == "call_failed"
    assert "facts" not in rec
    # the hash is still there, so a later rejection for the same state is
    # still countable
    assert rec["facts_hash"] == lh.gate_hash(_fg()[1])
    assert len(json.dumps(rec)) < 400


def test_skipped_markers_are_not_counted_as_rejections(tmp_path):
    """``rejected_attempts`` pre-filters lines on the raw text before parsing
    them; ``skipped_rejected`` must not satisfy that filter, or the cap would
    tighten itself every tick."""
    digest = "abc123"
    path = tmp_path / lh.LIVE_HIGHLIGHT_LOG
    path.write_text("".join(json.dumps(r) + "\n" for r in [
        {"facts_hash": digest, "outcome": "rejected"},
        {"facts_hash": digest, "outcome": "skipped_rejected", "attempts": 1},
        {"facts_hash": digest, "outcome": "skipped_rejected", "attempts": 1},
        {"facts_hash": digest, "outcome": "call_failed"},
        {"facts_hash": "other", "outcome": "rejected"},
        {"facts_hash": digest, "outcome": "written"},
    ]))
    assert lh.rejected_attempts(tmp_path, digest) == 1


def test_rejected_attempts_survives_a_truncated_line(tmp_path):
    digest = "abc123"
    path = tmp_path / lh.LIVE_HIGHLIGHT_LOG
    path.write_text(
        json.dumps({"facts_hash": digest, "outcome": "rejected"}) + "\n"
        + '{"facts_hash": "abc123", "outcome": "rejec\n'
    )
    assert lh.rejected_attempts(tmp_path, digest) == 1


def test_the_anthropic_client_is_built_once(monkeypatch):
    """The tick fans out across threads; a client per call meant a new HTTP
    pool per flight per tick."""
    monkeypatch.setattr(lh, "_client", None)
    built = []

    class FakeAnthropic:
        def __init__(self, **kw):
            built.append(kw)

    monkeypatch.setitem(sys.modules, "anthropic", type("m", (), {"Anthropic": FakeAnthropic}))
    first = lh._anthropic_client()
    second = lh._anthropic_client()
    assert first is second
    assert len(built) == 1
    assert built[0] == {"timeout": lh.REQUEST_TIMEOUT_S, "max_retries": lh.MAX_RETRIES}
    monkeypatch.setattr(lh, "_client", None)


def test_charge_is_skipped_without_a_session():
    """Called from a worker thread with db=None — must be a no-op, not a crash."""
    lh.charge_highlight(None, "u1", "f1", {"model": "claude-haiku-4-5", "input_tokens": 1000})
    lh.charge_highlight(None, None, "f1", None)


# --- The patch write --------------------------------------------------------


def test_patch_refuses_when_a_newer_tick_committed(tmp_path):
    """The highlight was written from the older facts; attaching it to a newer
    glance is the stale-text bug the facts hash exists to prevent."""
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import LIVE_FILE, patch_highlight

    as_of = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    stored = LiveLayer(
        flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00", pack_dir_name="p",
        glance=_glance(as_of=as_of + timedelta(minutes=10)),
    )
    (tmp_path / LIVE_FILE).write_text(stored.model_dump_json())
    assert patch_highlight(tmp_path, _highlight(), pack_timestamp=stored.pack_timestamp, as_of=as_of) is False


def test_patch_writes_and_round_trips(tmp_path):
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import LIVE_FILE, load_live, patch_highlight

    as_of = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    stored = LiveLayer(
        flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00", pack_dir_name="p",
        glance=_glance(as_of=as_of),
    )
    (tmp_path / LIVE_FILE).write_text(stored.model_dump_json())
    assert patch_highlight(tmp_path, _highlight(text="Cell closing at 180 NM."),
                           pack_timestamp=stored.pack_timestamp, as_of=as_of) is True
    back = load_live(tmp_path)
    assert back.glance.highlight.text == "Cell closing at 180 NM."


def test_patch_refuses_a_different_pack(tmp_path):
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import LIVE_FILE, patch_highlight

    as_of = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    stored = LiveLayer(flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00",
                       pack_dir_name="p", glance=_glance(as_of=as_of))
    (tmp_path / LIVE_FILE).write_text(stored.model_dump_json())
    assert patch_highlight(tmp_path, _highlight(), pack_timestamp="2026-10-02T06:00:00+00:00", as_of=as_of) is False


def test_highlight_log_is_removed_with_the_layer(tmp_path):
    """Otherwise a deleted flight leaves its highlights (and their facts) behind."""
    from weatherbrief.tasks.live_layer import LIVE_HIGHLIGHT_LOG, remove_live

    (tmp_path / LIVE_HIGHLIGHT_LOG).write_text("{}\n")
    remove_live(tmp_path)
    assert not (tmp_path / LIVE_HIGHLIGHT_LOG).exists()


def test_highlight_stays_out_of_the_agent_block():
    """Not displayed anywhere yet (owner, 2026-10-07) — an agent quoting it
    would be a user-facing surface by the back door."""
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import summarize_live

    layer = LiveLayer(
        flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00", pack_dir_name="p",
        glance=_glance(highlight=_highlight(text="SECRET HIGHLIGHT")),
    )
    blob = json.dumps(summarize_live(layer, {}), default=str)
    assert "SECRET HIGHLIGHT" not in blob
    assert "Observed 09:00Z" in blob  # the nutshell headline is exposed, as before


# --- Model request (#715) ---------------------------------------------------


class _Resp:
    def __init__(self, text="Quiet route ahead.", stop_reason="end_turn", category=None):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.stop_reason = stop_reason
        self.stop_details = type("D", (), {"category": category})() if category else None
        self.usage = type("U", (), {"input_tokens": 1750, "output_tokens": 85,
                                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})()


class _Client:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def test_generate_turns_thinking_off_at_low_effort(monkeypatch):
    """Thinking eats max_tokens on Haiku 5.5 and bought nothing on the A/B."""
    client = _Client(_Resp())
    monkeypatch.setattr(lh, "_anthropic_client", lambda: client)
    text, usage, _ = lh.generate(_facts_with())
    assert text == "Quiet route ahead."
    assert client.kwargs["model"] == "claude-haiku-5-5"
    assert client.kwargs["thinking"] == {"type": "disabled"}
    assert client.kwargs["output_config"] == {"effort": "low"}
    assert client.kwargs["max_tokens"] == lh.MAX_TOKENS
    assert usage["output_tokens"] == 85


def test_a_refusal_is_a_billed_rejection_under_the_retry_cap(monkeypatch, tmp_path):
    """A refusal is billed, so it counts like a rejection: logged with its
    category and cost, retried once, then left alone for that state."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    client = _Client(_Resp(text="", stop_reason="refusal", category="general_harms"))
    monkeypatch.setattr(lh, "_anthropic_client", lambda: client)
    layer = _Layer(glance=_glance())

    out = lh.ensure_highlight(tmp_path, layer)
    assert out.outcome == "rejected" and out.reason == "refusal (general_harms)"
    assert out.usage["input_tokens"] == 1750
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "skipped"
    records = [json.loads(l) for l in (tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()]
    assert [r["outcome"] for r in records] == ["rejected", "rejected", "skipped_rejected"]
    assert records[0]["reason"] == "refusal (general_harms)" and records[0]["cost_usd"] > 0
    assert layer.glance.highlight is None


def test_a_reply_cut_at_max_tokens_is_rejected_as_truncated(monkeypatch, tmp_path):
    """Unfinished text is not graded: it gets its own reason in the log."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_and_gate_for", lambda layer: _fg())
    monkeypatch.setattr(lh, "_anthropic_client", lambda: _Client(_Resp(text="Quiet route", stop_reason="max_tokens")))
    out = lh.ensure_highlight(tmp_path, _Layer(glance=_glance()))
    assert out.outcome == "rejected" and out.reason.startswith("truncated")
