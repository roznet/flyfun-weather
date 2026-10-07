"""The Observed tab's nutshell, route ribbon and map focus (#690).

Synthetic route ZZDP (50 N, 0 E) → ZZDS (50 N, 4 E), as in
``test_live_storms``: due east, ~154 NM, 1.5 h, departing 12:00Z; north of
the track is left. The main scenario mirrors the LPPR→LPPT test flight of
2026-10-06 (observed-tab-presentation §3/§7): one storm just off the
departure moving away, storms 25–28 NM inland en route moving away, a
briefed TS SIGMET over the first stretch, VFR at both ends with a PROB30
TSRA in the destination TAF.
"""

from __future__ import annotations

import math
from datetime import timedelta

from test_live_storms import DEP, NM_LAT, NM_LON, ROUTE, at_nm, cell, frame, storms_at

from weatherbrief.models.live import LiveChange, LiveChanges, LiveLayer, LiveStorms
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
from weatherbrief.observed.storms import CellFrames
from weatherbrief.tasks.live_glance import build_glance
from weatherbrief.tasks.live_layer import summarize_live

NOW = DEP - timedelta(minutes=30)


def _airport(icao, along, *, cat="VFR", raw=None, taf=None, cross=0.0, metar=True):
    lat, lon = at_nm(along, cross)
    a = AirportObservation(
        icao=icao, distance_from_route_nm=abs(cross), enroute_distance_nm=along,
        nearest_waypoint_icao=icao, lat=lat, lon=lon,
        metar_raw=(raw or f"METAR {icao} 061120Z 27010KT 9999 FEW040 19/12 Q1013") if metar else None,
        metar_time=NOW - timedelta(minutes=10) if metar else None,
        metar_flight_category=cat if metar else None, has_metar=metar,
    )
    if taf:
        a = a.model_copy(update={"has_taf": True, "taf_raw": "TAF ...", "taf_valid_at_eta": True, **taf})
    return a


def _obs(*airports):
    return RouteObservations(corridor_nm=30.0, fetch_time=NOW, airports_found=len(airports),
                             airports_with_metar=len(airports), airports_with_taf=0, airports=list(airports))


def _sigmet(seq, lo, hi, *, direction=None, speed=None):
    # A box north of the track over [lo, hi] NM; coords are (lon, lat).
    w, e = lo * NM_LON, hi * NM_LON
    return SigmetAlongRoute(
        fir_id="ZZZZ", hazard="TS", qualifier="EMBD",
        valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(hours=3),
        direction=direction, speed_kt=speed, raw_text=f"ZZZZ SIGMET {seq} VALID 061030/061430",
        min_distance_nm=0.0, enroute_distance_from_nm=lo, enroute_distance_to_nm=hi,
        coords=[(w, 49.9), (e, 49.9), (e, 50.5), (w, 50.5), (w, 49.9)],
    )


def _observed(*, flashes=0, radar=None):
    """Route points every 20 NM; radar ``{along: dBZ}`` in the 10 NM ring
    (absent = nothing detected), lightning in the 20 NM ring."""
    radar = radar or {}
    alongs = [float(a) for a in range(0, 161, 20)]
    refs = [ObservedStationRef(id=f"p{int(a)}", lat=50.0, lon=a * NM_LON, enroute_distance_nm=min(a, 154.0))
            for a in alongs]

    def refl(a):
        dbz = radar.get(int(a))
        return ObservedAnnulus(radius_nm=10, total_px=100, valid_px=100, nodata_px=0,
                               undetect_px=0 if dbz else 100, detected_px=10 if dbz else 0, max_value=dbz)

    return ObservedConditions(
        computed_at=NOW, corridor_nm=20, radii_nm=[5, 10, 20], stations=refs,
        reflectivity=ObservedField(
            source="opera", quantity="dbzh", valid_time=NOW - timedelta(minutes=5), age_minutes=5,
            stations=[ObservedStationSamples(station_id=r.id, annuli=[refl(a)]) for r, a in zip(refs, alongs)],
        ),
        lightning=ObservedFlashField(
            source="mtg-li", quantity="flashes", valid_time=NOW, age_minutes=2, window_minutes=10,
            stations=[ObservedFlashStationSamples(station_id=r.id, annuli=[
                ObservedFlashAnnulus(radius_nm=20, flash_count=flashes, area_km2=1000, window_minutes=10,
                                     nearest_flash_nm=12.0 if flashes else None)])
                for r in refs],
        ),
    )


def _departure_storm():
    """9 NM NE of ZZDP, moving NE at 11 kt: 8 kt away from the track."""
    lat, lon = 50.0 + 9 * math.cos(math.radians(45)) * NM_LAT, 9 * math.sin(math.radians(45)) * NM_LON
    c = cell("core35-dep", "core35", peak=46.0, speed=11.0, toward=45.0)
    c["lat"], c["lon"] = lat, lon
    return c


def _layer(*, storms=None, sigmets=None, observations=None, observed=None, changes=None, new_sigmets=()):
    if storms is None:
        storms = storms_at(NOW, [
            _departure_storm(),
            cell("core35-a", "core35", along=60, cross=-25, peak=48.0, speed=15.0, toward=0.0),
            cell("core35-b", "core35", along=90, cross=-28, peak=44.0, speed=15.0, toward=0.0),
        ])
    return LiveLayer(
        flight_id="flight-zz", pack_timestamp="2026-10-06T10:00:00+00:00", pack_dir_name="pack",
        live_updated_at=NOW, observations_updated_at=NOW, sigmets_updated_at=NOW, observed_updated_at=NOW,
        route_observations=observations if observations is not None else _obs(
            _airport("ZZDP", 0.0),
            _airport("ZZDS", 154.0, taf={
                "taf_flight_category_at_eta": "MVFR", "taf_prevailing_category_at_eta": "VFR",
                "taf_temporary_category_at_eta": "MVFR", "taf_temporary_type": "PROB30",
                "taf_significant_weather": ["TS"],
            }),
            _airport("ZZRT", 80.0, cross=-6.0),
        ),
        route_sigmets=sigmets if sigmets is not None else RouteSigmets(
            corridor_nm=30.0, fetch_time=NOW, sigmets=[_sigmet(6, 0.0, 100.0)]),
        observed_conditions=observed if observed is not None else _observed(),
        storms=storms,
        changes=changes if changes is not None else LiveChanges(
            baseline_at=NOW - timedelta(hours=1), computed_at=NOW, changes=[], new_sigmets=list(new_sigmets)),
    )


def _glance(layer, now=NOW):
    return build_glance(layer, ROUTE, DEP, now=now)


# --- Nutshell -------------------------------------------------------------------


def test_lppr_lppt_like_nutshell():
    glance, _ = _glance(_layer())
    assert glance.headline == "Observed 11:30Z · as briefed"
    assert glance.comparison == "as_briefed"
    assert [ln.phase for ln in glance.lines] == ["departure", "enroute", "arrival"]
    dep, enr, arr = (ln.text for ln in glance.lines)
    assert dep == "ZZDP VFR · nearest cell 9 NM NE (46 dBZ), moving away 8 kt · no lightning ≤20 NM"
    assert enr == (
        "2 cells 25–28 NM left of track, all moving away; nearest 25 NM left at 60 NM ~12:35Z (48 dBZ), "
        "moving away 15 kt · EMBD TS SIGMET ZZZZ 6 covers first 100 NM (briefed)"
    )
    assert arr == "ZZDS VFR · TAF at ETA VFR, PROB30 MVFR TS · no cell within 20 NM now · no lightning ≤20 NM"
    assert all(not ln.unavailable for ln in glance.lines)
    assert glance.lines[1].sources == ["storm:core35-a", "storm:core35-b", "sigmet:ZZZZ|6"]


def test_the_estimate_never_reaches_the_nutshell():
    # A storm closing on the track has an estimate; the line says the
    # observed motion only.
    layer = _layer(storms=storms_at(NOW, [cell("core35-c", "core35", along=70, cross=12, speed=20.0, toward=0.0)]))
    assert layer.storms.storms[0].estimate is not None
    glance, _ = _glance(layer)
    text = glance.lines[1].text
    assert "closing 20 kt" in text
    assert "estimate" not in text.lower() and "cpa" not in text.lower()


def test_missing_sources_say_unavailable_never_clear():
    layer = _layer(
        storms=LiveStorms(status="stale", corridor_nm=30.0,
                          unavailable_since=NOW - timedelta(minutes=40)),
        observations=_obs(_airport("ZZDP", 0.0, metar=False)),
        observed=ObservedConditions(computed_at=NOW, corridor_nm=20),
    )
    layer.route_sigmets = None
    layer.changes = None
    glance, _ = _glance(layer)
    dep, enr, arr = glance.lines
    assert dep.text == "ZZDP METAR unavailable · radar cells unavailable since 10:50Z · lightning unavailable"
    assert set(dep.unavailable) == {"metar", "storms", "lightning"}
    assert enr.text == "radar cells unavailable since 10:50Z · SIGMETs unavailable"
    assert set(enr.unavailable) == {"storms", "sigmets"}
    assert arr.text.startswith("ZZDS METAR unavailable · no TAF for ETA · radar cells unavailable")
    assert "taf" in arr.unavailable
    assert glance.comparison == "unavailable"
    for ln in glance.lines:
        assert "clear" not in ln.text and "no storm" not in ln.text


def test_headline_counts_what_moved_by_phase():
    rows = [
        LiveChange(key="metar:ZZDS", kind="metar_category", source="METAR", direction="worse",
                   tier="alert", role="destination", icao="ZZDS", message="m"),
        LiveChange(key="storm:x", kind="storm", source="RADAR", direction="worse",
                   tier="highlight", role="route", message="m"),
        LiveChange(key="metar:ZZDP", kind="metar_category", source="METAR", direction="better",
                   tier="highlight", role="departure", icao="ZZDP", message="m"),
        LiveChange(key="sigmet:ZZZZ|5+sigmet:ZZZZ|6", kind="sigmet_issued", source="SIGMET",
                   direction="updated", tier="highlight", role="route", message="m"),
    ]
    glance, _ = _glance(_layer(changes=LiveChanges(computed_at=NOW, changes=rows)))
    assert glance.headline == (
        "Observed 11:30Z · 2 worse since the briefing (en route, arrival), departure improving, "
        "1 SIGMET reissued"
    )
    assert glance.comparison == "mixed"
    assert [ln.alert for ln in glance.lines] == [False, False, True]


def test_plain_reissue_reads_as_briefed():
    rows = [LiveChange(key="sigmet:ZZZZ|5+sigmet:ZZZZ|6", kind="sigmet_issued", source="SIGMET",
                       direction="updated", tier="highlight", role="route", message="m")]
    glance, _ = _glance(_layer(changes=LiveChanges(computed_at=NOW, changes=rows)))
    assert glance.headline == "Observed 11:30Z · as briefed, 1 SIGMET reissued"


def test_live_start_baseline_never_says_as_briefed():
    glance, _ = _glance(_layer(changes=LiveChanges(computed_at=NOW, baseline_source="live_start")))
    assert glance.headline == "Observed 11:30Z · no significant change since live tracking began"


def test_new_pending_sigmet_moving_toward_the_route():
    s = _sigmet(7, 110.0, 154.0, direction="S", speed=10)
    s = s.model_copy(update={"valid_from": NOW + timedelta(hours=1)})
    layer = _layer(sigmets=RouteSigmets(corridor_nm=30.0, fetch_time=NOW, sigmets=[s]),
                   new_sigmets=["sigmet:ZZZZ|7"])
    glance, ribbon = _glance(layer)
    assert "EMBD TS SIGMET ZZZZ 7 covers last 44 NM from 12:30Z, moving toward the route (new)" in glance.lines[1].text
    [rs] = ribbon.sigmets
    assert rs.motion == "toward" and rs.pending and rs.new is True
    assert rs.focus.time == s.valid_from


def test_old_metar_and_stale_blocks_are_flagged_inline():
    layer = _layer()
    layer.route_observations.airports[0].metar_time = NOW - timedelta(minutes=100)
    layer.observations_updated_at = NOW - timedelta(minutes=45)
    glance, _ = _glance(layer)
    assert glance.lines[0].text.startswith("ZZDP VFR (METAR 09:50Z) (METARs as of 10:45Z) · ")


def test_departure_and_arrival_lines_pass_at_plan():
    glance, _ = _glance(_layer(), now=DEP + timedelta(minutes=10))
    assert [ln.passed for ln in glance.lines] == [True, False, False]


# --- Ribbon -----------------------------------------------------------------------


def test_ribbon_lanes():
    layer = _layer(observed=_observed(flashes=0, radar={40: 47.0}))
    layer.observed_conditions.reflectivity.stations[-1].annuli[0] = ObservedAnnulus(
        radius_nm=10, total_px=100, valid_px=10, nodata_px=90)
    _, ribbon = _glance(layer)
    assert ribbon.route_nm == round(layer.storms.route_nm, 1)
    assert len(ribbon.segments) == 15 and math.isclose(ribbon.segment_nm, ribbon.route_nm / 15, abs_tol=0.1)
    assert [w.icao for w in ribbon.waypoints] == ["ZZDP", "ZZDS"]
    assert ribbon.waypoints[0].eta == DEP and ribbon.waypoints[-1].eta == DEP + timedelta(hours=1.5)
    by = {s.index: s for s in ribbon.segments}
    seg40 = next(s for s in ribbon.segments if s.from_nm <= 40 < s.to_nm)
    assert seg40.radar_status == "measured" and seg40.radar_max_dbz == 47.0
    assert seg40.radar_intensity
    seg20 = next(s for s in ribbon.segments if s.from_nm <= 20 < s.to_nm)
    assert seg20.radar_status == "measured" and seg20.radar_max_dbz is None  # looked, nothing there
    assert by[14].radar_status == "no_coverage"
    assert any(s.radar_status == "no_sample" for s in ribbon.segments)
    assert all(s.lightning in (False, None) for s in ribbon.segments)
    # The SIGMET band and the storms abeam each stretch.
    assert all(("sigmet:ZZZZ|6" in s.sigmet_ids) == (s.from_nm <= 100.0) for s in ribbon.segments)
    assert "core35-a" in next(s for s in ribbon.segments if s.from_nm <= 60 < s.to_nm).storm_ids
    # Stations: role, side and the TAF at ETA.
    st = {s.icao: s for s in ribbon.stations}
    assert st["ZZDP"].role == "departure" and st["ZZDP"].along_nm == 0.0
    assert st["ZZRT"].role == "route" and st["ZZRT"].cross_nm < 0  # north = left
    assert st["ZZDS"].taf_temporary_type == "PROB30" and st["ZZDS"].taf_weather == ["TS"]
    assert st["ZZRT"].focus.kind == "station" and st["ZZRT"].focus.layers == ["route", "metar"]


def test_storm_focus_frames_the_storm_and_the_track_abeam():
    layer = _layer()
    _glance(layer)
    st = next(s for s in layer.storms.storms if s.id == "core35-a")
    f = st.focus
    assert f.kind == "storm" and f.id == "core35-a"
    assert f.layers == ["route", "radar", "cells", "lightning"]
    assert f.time == layer.storms.frame_time
    min_lon, min_lat, max_lon, max_lat = f.bbox
    assert min_lat < 50.0 < max_lat  # the track
    assert min_lat < st.lat < max_lat and min_lon < st.lon < max_lon


def test_sigmet_focus_uses_lon_lat_coords():
    _, ribbon = _glance(_layer())
    min_lon, min_lat, max_lon, max_lat = ribbon.sigmets[0].focus.bbox
    assert (min_lat, max_lat) == (49.9, 50.5)
    assert min_lon == 0.0 and max_lon > 2.0


def test_glance_failure_leaves_the_tick_alone(monkeypatch, tmp_path):
    from weatherbrief.tasks import live_glance
    from weatherbrief.tasks.live_layer import commit_live_update

    def boom(*a, **k):
        raise RuntimeError("x")

    # Fails part-way, after the storms are read: they keep no focus.
    monkeypatch.setattr(live_glance, "_ribbon_segments", boom)
    pack = tmp_path / "user" / "flight-zz" / "2026-10-06T10-00-00"
    pack.mkdir(parents=True)
    layer = commit_live_update(
        pack, briefing_data={"route": ROUTE.model_dump(mode="json"), "departure_time": DEP.isoformat()},
        observations=_obs(_airport("ZZDP", 0.0)), sigmets=None, observed=None,
        started_at=NOW, now=NOW,
        cells=CellFrames("available", newest=frame(NOW - timedelta(minutes=5), [
            cell("core35-a", "core35", along=60, cross=-8)])),
    )
    assert layer.storms.storms  # the storms were built
    assert layer is not None and layer.glance is None and layer.ribbon is None
    assert all(st.focus is None for st in layer.storms.storms)


# --- Stored, served, and in the agent block -------------------------------------


def test_commit_stores_glance_and_the_agent_block_quotes_it(tmp_path):
    from weatherbrief.tasks.live_layer import commit_live_update, load_live

    pack = tmp_path / "user" / "flight-zz" / "2026-10-06T10-00-00"
    pack.mkdir(parents=True)
    briefing = {"route": ROUTE.model_dump(mode="json"), "departure_time": DEP.isoformat()}
    layer = commit_live_update(
        pack, briefing_data=briefing,
        observations=_obs(_airport("ZZDP", 0.0), _airport("ZZDS", 154.0)), sigmets=None, observed=None,
        started_at=NOW, now=NOW,
    )
    assert layer.glance is not None and layer.ribbon is not None
    stored = load_live(pack.parent)
    assert stored.glance == layer.glance and stored.ribbon == layer.ribbon
    block = summarize_live(stored, briefing)
    assert block["glance"]["headline"] == layer.glance.headline
    assert [ln["text"] for ln in block["glance"]["lines"]] == [ln.text for ln in layer.glance.lines]
    # The cells feed is dark in this commit: the lines say so.
    assert "radar cells unavailable" in block["glance"]["lines"][1]["text"]


# --- Review round 1 on #695 ------------------------------------------------------


def test_ribbon_never_reads_another_ring_than_it_states():
    # Only a 5 NM ring sampled: the ribbon claims 10 NM, so it reads nothing.
    layer = _layer(observed=_observed(radar={40: 47.0}))
    for st in layer.observed_conditions.reflectivity.stations:
        st.annuli = [a.model_copy(update={"radius_nm": 5}) for a in st.annuli]
    _, ribbon = _glance(layer)
    assert ribbon.radar_radius_nm == 10.0
    assert all(s.radar_status == "no_sample" and s.radar_max_dbz is None for s in ribbon.segments)


def test_only_sigmet_rows_read_as_reissued():
    rows = [LiveChange(key="x", kind="metar_category", source="METAR", direction="updated",
                       tier="highlight", role="route", message="m")]
    glance, _ = _glance(_layer(changes=LiveChanges(computed_at=NOW, changes=rows)))
    assert glance.headline == "Observed 11:30Z · as briefed, 1 updated"


def test_enroute_line_after_planned_arrival():
    glance, _ = _glance(_layer(), now=DEP + timedelta(hours=2))
    assert glance.lines[1].text.startswith("flight arrived at plan, no route ahead · ")
    assert glance.lines[1].passed


def test_convective_tags_share_one_order():
    raw = "METAR ZZDP 061120Z 27010KT 9999 TS FEW040CB 19/12 Q1013"
    dep = _airport("ZZDP", 0.0, raw=raw).model_copy(update={"metar_weather": ["TS"]})
    layer = _layer(observations=_obs(dep, _airport("ZZDS", 154.0)))
    glance, ribbon = _glance(layer)
    assert glance.lines[0].text.startswith("ZZDP VFR CB TS · ")
    assert next(s for s in ribbon.stations if s.icao == "ZZDP").convective == ["CB", "TS"]


# --- Symbolic map: weather bands --------------------------------------------------


def test_ribbon_carries_the_weather_bands_only_while_the_feed_is_available():
    from test_route_bands import box

    layer = _layer()
    f = frame(NOW - timedelta(minutes=5), [])
    f["outlines"] = {"rain20": [box(40, 60, -20, -10)]}
    _, ribbon = build_glance(layer, ROUTE, DEP, cell_frame=f, now=NOW)
    assert ribbon.weather_status == "available" and ribbon.weather_corridor_nm == 30.0
    assert [b.tier for b in ribbon.weather] == ["rain"] and ribbon.weather_bin_nm == 5.0

    dark = _layer(storms=LiveStorms(status="unavailable", corridor_nm=30.0))
    _, ribbon = build_glance(dark, ROUTE, DEP, cell_frame=f, now=NOW)
    assert ribbon.weather_status == "unavailable" and ribbon.weather == []


def test_a_cell_past_the_route_end_beyond_the_terminal_disc_is_named():
    # 25 NM past ZZDS, in the corridor but on no other line (#695 review).
    from test_live_storms import cell

    past = cell("core35-x", "core35", along=154 + 25, cross=0, peak=47.0)
    glance, _ = _glance(_layer(storms=storms_at(NOW, [past])))
    arr = glance.lines[2]
    assert "no cell within 20 NM now (1 at 25 NM E)" in arr.text
    assert "storm:core35-x" in arr.sources
    near = cell("core35-n", "core35", along=154 + 8, cross=0, peak=45.0)
    glance, _ = _glance(_layer(storms=storms_at(NOW, [past, near])))
    assert "nearest cell 8 NM E (45 dBZ), nearly stationary now, +1 at 25 NM E" in glance.lines[2].text
