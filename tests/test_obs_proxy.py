"""Observation proxy for a terminal with no METAR/TAF of its own (#767).

Fictional-free geography: the reference pairs from the issue (EGTF/EGLL,
EGLK/EGLF/EGVO) with their real positions and elevations, since the rules
were agreed on exactly those numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.obs_proxy import (
    apply_obs_proxies,
    load_overrides,
    parse_overrides,
    pins_from,
    resolve_obs_proxy,
)
from weatherbrief.models.observations import AirportObservation, ObsProxy, RouteObservations
from weatherbrief.models.runway_wind import RunwayEndInfo, RunwayInfo, RunwayWindPicture

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)

# (lat, lon, elevation ft)
GEO = {
    "EGTF": (51.3481, -0.5589, 80),
    "EGLL": (51.4706, -0.4619, 83),
    "EGLF": (51.2758, -0.7763, 238),
    "EGLK": (51.3239, -0.8475, 325),
    "EGVO": (51.2341, -0.9428, 405),
}
ELEV = {k: float(v[2]) for k, v in GEO.items()}


def _row(icao, *, metar=True, taf=True, cat="VFR", age_min=20, taf_ok=True, **kw):
    lat, lon, _ = GEO.get(icao, (None, None, None))
    a = AirportObservation(
        icao=icao, distance_from_route_nm=0.0, nearest_waypoint_icao="EGTF",
        lat=lat, lon=lon, **kw,
    )
    if metar:
        a.has_metar = True
        a.metar_raw = f"{icao} 120840Z 24008KT 9999 SCT030 15/09 Q1012"
        a.metar_time = NOW - timedelta(minutes=age_min)
        a.metar_flight_category = cat
        a.metar_wind_dir, a.metar_wind_speed_kt = 240, 8
    if taf:
        a.has_taf = True
        a.taf_raw = f"TAF {icao} 120500Z 1206/1312 24010KT 9999 SCT030"
        a.taf_valid_at_eta = taf_ok
        a.taf_flight_category_at_eta = "VFR" if taf_ok else None
        a.taf_prevailing_category_at_eta = "VFR" if taf_ok else None
    return a


def _obs(*rows):
    return RouteObservations(
        corridor_nm=30, fetch_time=NOW, airports_found=len(rows),
        airports_with_metar=0, airports_with_taf=0, airports=list(rows),
    )


# --- The resolver: the issue's reference pairs ------------------------------


def test_egtf_resolves_to_egll():
    field = _row("EGTF", metar=False, taf=False)
    p = resolve_obs_proxy(field, [field, _row("EGLL"), _row("EGLF")], kind="metar",
                          elevations=ELEV, now=NOW)
    assert p.icao == "EGLL" and p.source == "auto"
    assert 7.5 < p.distance_nm < 9  # 8.2 NM in the issue
    assert p.elevation_diff_ft == 3
    assert p.where.endswith("NE")


def test_egtf_never_auto_resolves_to_eglf():
    """EGLF is close enough but 158 ft higher: ceilings would mislead."""
    field = _row("EGTF", metar=False, taf=False)
    assert resolve_obs_proxy(field, [field, _row("EGLF")], kind="metar",
                             elevations=ELEV, now=NOW) is None


def test_eglk_resolves_to_eglf_via_the_seed_override_never_egvo():
    field = _row("EGLK", metar=False, taf=False)
    overrides = load_overrides()
    assert overrides["EGLK"] == {"metar": ["EGLF"], "taf": ["EGLF"]}
    corridor = [field, _row("EGVO"), _row("EGLF")]
    p = resolve_obs_proxy(field, corridor, kind="metar", elevations=ELEV, now=NOW, overrides=overrides)
    assert p.icao == "EGLF" and p.source == "override"
    # EGLF closed overnight: the override is exclusive, Odiham is not tried.
    corridor = [field, _row("EGVO"), _row("EGLF", age_min=200)]
    assert resolve_obs_proxy(field, corridor, kind="metar", elevations=ELEV, now=NOW,
                             overrides=overrides) is None


def test_stale_metar_is_skipped_for_the_next_candidate():
    field = _row("EGTF", metar=False, taf=False)
    elev = dict(ELEV, EGLF=85.0)  # pretend EGLF passes, to have a second candidate
    corridor = [field, _row("EGLL", age_min=91), _row("EGLF")]
    assert resolve_obs_proxy(field, corridor, kind="metar", elevations=elev, now=NOW).icao == "EGLF"


def test_distance_limits_differ_for_metar_and_taf():
    field = _row("EGTF", metar=False, taf=False)
    far = _row("EGLL")
    far.lat = GEO["EGTF"][0] + 12 / 60  # 12 NM north
    far.lon = GEO["EGTF"][1]
    assert resolve_obs_proxy(field, [field, far], kind="metar", elevations=ELEV, now=NOW) is None
    assert resolve_obs_proxy(field, [field, far], kind="taf", elevations=ELEV, now=NOW).icao == "EGLL"


def test_taf_proxy_needs_a_taf_valid_at_eta():
    field = _row("EGTF", metar=False, taf=False)
    assert resolve_obs_proxy(field, [field, _row("EGLL", taf_ok=False)], kind="taf",
                             elevations=ELEV, now=NOW) is None


def test_unknown_elevation_fails_the_automatic_gate():
    field = _row("EGTF", metar=False, taf=False)
    assert resolve_obs_proxy(field, [field, _row("EGLL")], kind="metar",
                             elevations={"EGLL": 83.0}, now=NOW) is None


def test_a_pinned_station_is_kept_while_it_qualifies():
    field = _row("EGTF", metar=False, taf=False)
    elev = dict(ELEV, EGLF=85.0)
    corridor = [field, _row("EGLL"), _row("EGLF")]
    # Nearest by default...
    nearest = resolve_obs_proxy(field, corridor, kind="metar", elevations=elev, now=NOW).icao
    other = "EGLF" if nearest == "EGLL" else "EGLL"
    # ...but the flight keeps the one it started with.
    assert resolve_obs_proxy(field, corridor, kind="metar", elevations=elev, now=NOW,
                             pinned=other).icao == other


def test_per_kind_override_leaves_the_other_kind_automatic():
    overrides = parse_overrides({"EGTF": {"metar": ["EGLF"]}})
    assert overrides == {"EGTF": {"metar": ["EGLF"]}}
    field = _row("EGTF", metar=False, taf=False)
    corridor = [field, _row("EGLL"), _row("EGLF")]
    obs = _obs(*corridor)
    apply_obs_proxies(obs, ["EGTF"], elevations=ELEV, now=NOW, overrides=overrides)
    assert field.metar_proxy.icao == "EGLF" and field.metar_proxy.source == "override"
    assert field.taf_proxy.icao == "EGLL" and field.taf_proxy.source == "auto"


def test_metar_and_taf_proxies_are_independent():
    field = _row("EGTF", metar=False, taf=False)
    elev = dict(ELEV, EGLF=85.0)
    eglf = _row("EGLF", taf=False)
    obs = _obs(field, _row("EGLL", age_min=120), eglf)
    apply_obs_proxies(obs, ["EGTF"], elevations=elev, now=NOW)
    assert field.metar_proxy.icao == "EGLF"
    assert field.taf_proxy.icao == "EGLL"
    assert obs.terminal("EGTF").caption.startswith("METAR EGLF (")
    assert " · TAF EGLL (" in obs.terminal("EGTF").caption


def test_a_field_with_its_own_reports_is_unaffected():
    field = _row("EGTF")
    obs = _obs(field, _row("EGLL"))
    assert apply_obs_proxies(obs, ["EGTF"], elevations=ELEV, now=NOW) == []
    assert field.metar_proxy is None and field.taf_proxy is None
    term = obs.terminal("EGTF")
    assert not term.proxied and term.effective() is field and term.caption is None


def test_a_field_with_an_expired_own_taf_gets_no_taf_proxy():
    field = _row("EGTF", metar=False, taf_ok=False)
    obs = _obs(field, _row("EGLL"))
    apply_obs_proxies(obs, ["EGTF"], elevations=ELEV, now=NOW)
    assert field.metar_proxy.icao == "EGLL"
    assert field.taf_proxy is None


def test_pins_round_trip():
    field = _row("EGTF", metar=False, taf=False)
    obs = _obs(field, _row("EGLL"))
    apply_obs_proxies(obs, ["EGTF"], elevations=ELEV, now=NOW)
    assert pins_from(obs) == {"EGTF": ("EGLL", "EGLL")}
    assert pins_from(RouteObservations.model_validate(obs.model_dump(mode="json"))) == pins_from(obs)


# --- The accessor ------------------------------------------------------------


def _proxied_egtf(**egll_kw):
    field = _row("EGTF", metar=False, taf=False)
    # EGTF's runway 07/25 (true headings), EGLL's 09/27 — the advisory must
    # be on the field's.
    field.runway_wind = RunwayWindPicture(icao="EGTF", runways=[RunwayInfo(
        id="07/25", ends=[RunwayEndInfo(ident="07", heading_true=66.0),
                          RunwayEndInfo(ident="25", heading_true=246.0)],
    )])
    egll = _row("EGLL", **egll_kw)
    egll.metar_best_runway_id = "27L"
    obs = _obs(field, egll)
    apply_obs_proxies(obs, ["EGTF"], elevations=ELEV, now=NOW)
    return obs


def test_effective_reads_the_station_but_keeps_the_field():
    obs = _proxied_egtf(cat="IFR")
    term = obs.terminal("EGTF")
    eff = term.effective()
    assert eff.icao == "EGTF" and eff.metar_flight_category == "IFR"
    assert eff.has_metar and eff.taf_flight_category_at_eta == "VFR"
    assert eff.metar_best_runway_id == "25"  # the field's runway, not EGLL's 27L
    assert eff.metar_proxy.icao == "EGLL" and eff.taf_proxy.icao == "EGLL"  # provenance kept
    assert term.label == "EGTF (via EGLL)"
    assert term.caption.startswith("using EGLL as proxy · ")
    # The station's own row is untouched (never relabelled, decision 8).
    assert obs.airport("EGLL").icao == "EGLL" and obs.airport("EGTF").metar_raw is None
    assert term.metar_station == "EGLL"


def test_proxy_note_for_the_digests():
    note = _proxied_egtf().terminal("EGTF").proxy_note
    assert note.startswith("EGTF has no METAR or TAF; EGLL (")
    assert "elevation +3 ft) used as proxy" in note


def test_old_packs_without_proxy_fields_still_load():
    a = AirportObservation.model_validate({"icao": "EGTF", "distance_from_route_nm": 0, "nearest_waypoint_icao": "EGTF"})
    assert a.metar_proxy is None and a.taf_proxy is None


def test_obs_proxy_where():
    assert ObsProxy(icao="EGLL", distance_nm=8.2, bearing_deg=28).where == "8 NM NE"
    assert ObsProxy(icao="EGLL", distance_nm=8.2).where == "8 NM"


# --- Significance: terminal alerting on the proxy ----------------------------


def _classify(base, latest, roles=None):
    from weatherbrief.tasks.live_significance import classify_changes

    changes, _ = classify_changes(
        baseline_obs=base, latest_obs=latest, baseline_sigmets=None, latest_sigmets=None,
        roles=roles or {"EGTF": "departure", "EGKA": "destination"}, now=NOW - timedelta(hours=1),
    )
    return changes.changes


def test_a_proxied_departure_alerts_on_its_station_labelled():
    base = _proxied_egtf(cat="VFR", age_min=60)
    latest = _proxied_egtf(cat="IFR", age_min=10)
    rows = _classify(base, latest)
    metar = [c for c in rows if c.key == "metar:EGTF"]
    assert len(metar) == 1
    assert metar[0].tier == "alert" and metar[0].role == "departure"
    assert metar[0].message == "EGTF (via EGLL) METAR: VFR → IFR"
    # The station is not reported a second time as a route airport.
    assert not [c for c in rows if c.icao == "EGLL"]


def test_a_proxied_departure_alerts_on_wind_over_its_own_runways():
    base = _proxied_egtf(age_min=60)
    latest = _proxied_egtf(age_min=10)
    st = latest.airport("EGLL")
    st.metar_wind_dir, st.metar_wind_speed_kt = 160, 30  # ~90° across EGTF's 07/25
    rows = [c for c in _classify(base, latest) if c.key == "wind:EGTF"]
    assert len(rows) == 1 and rows[0].tier == "alert"
    assert rows[0].message.startswith("EGTF (via EGLL) wind: green → red")


def test_a_proxy_switch_is_not_a_change():
    """Briefing read EGLL; now EGLL is stale and EGLF stands in. EGLF is
    compared with EGLF as the briefing saw it: same report, no row."""
    elev = dict(ELEV, EGLF=85.0)

    def obs_with(egll_age, eglf_cat):
        field = _row("EGTF", metar=False, taf=False)
        egll = _row("EGLL", cat="VFR", age_min=egll_age)
        eglf = _row("EGLF", cat=eglf_cat, age_min=30)
        o = _obs(field, egll, eglf)
        apply_obs_proxies(o, ["EGTF"], elevations=elev, now=NOW, pinned=pins_from(None))
        return o

    base = obs_with(10, "MVFR")  # EGLL picked (nearer? pin the briefing on it)
    base.airport("EGTF").metar_proxy = base.airport("EGTF").metar_proxy.model_copy(update={"icao": "EGLL"})
    latest = obs_with(200, "MVFR")
    assert latest.airport("EGTF").metar_proxy.icao == "EGLF"
    assert not [c for c in _classify(base, latest) if c.icao == "EGTF"]


# --- The proxy-switch replay on a real flight morning ------------------------


def _lemi_variant(stations: list[str], drop_leri_from: str | None):
    """2026-10-02 LELL→LEMI with LEMI's own reports withheld and a curated
    proxy list. ``drop_leri_from`` ("HH:MM"): LERI stops reporting then."""
    from weatherbrief.analysis.obs_proxy import apply_obs_proxies, pins_from

    overrides = parse_overrides({"LEMI": stations})

    def transform(obs, at, previous):
        rows = []
        for a in obs.airports:
            if a.icao == "LEMI":
                a = AirportObservation(
                    icao="LEMI", name=a.name, distance_from_route_nm=a.distance_from_route_nm,
                    enroute_distance_nm=a.enroute_distance_nm, nearest_waypoint_icao=a.nearest_waypoint_icao,
                    lat=a.lat, lon=a.lon, eta_hour_offset=a.eta_hour_offset,
                )
            elif a.icao == "LERI" and drop_leri_from and at.strftime("%H:%M") >= drop_leri_from:
                continue
            rows.append(a)
        obs = obs.model_copy(update={"airports": rows})
        apply_obs_proxies(obs, ["LEMI"], elevations={}, now=at, overrides=overrides,
                          pinned=pins_from(previous))
        return obs

    return transform


def _lemi_rows(ticks):
    return [
        (t.at.strftime("%H:%M"), sorted((c.key, c.direction, c.to_value) for c in t.layer.changes.changes
                                        if c.icao == "LEMI"))
        for t in ticks
    ]


@pytest.fixture(scope="module")
def lemi_replays(tmp_path_factory):
    from live_scenario_replay import load_scenario, replay

    sc = load_scenario("2026-10-02_lell_lemi")
    steady = replay(sc, tmp_path_factory.mktemp("a") / "u" / "f", _lemi_variant(["LELC"], None))
    switched = replay(sc, tmp_path_factory.mktemp("b") / "u" / "f", _lemi_variant(["LERI", "LELC"], "08:00"))
    return steady, switched


def test_proxied_destination_replays_labelled(lemi_replays):
    steady, _ = lemi_replays
    msgs = [c.message for t in steady for c in t.layer.changes.changes if c.icao == "LEMI"]
    assert msgs and all(m.startswith("LEMI (via LELC) ") for m in msgs)
    # LELC goes MVFR at 10:00: an alert at the destination, via its proxy.
    at_10 = next(t for t in steady if t.at.strftime("%H:%M") == "10:00")
    assert any(c.key == "metar:LEMI" and c.tier == "alert" and c.to_value == "MVFR"
               for c in at_10.layer.changes.changes)
    line = next(ln for ln in at_10.layer.glance.lines if ln.phase == "arrival")
    assert line.text.startswith("LEMI (via LELC) MVFR")
    assert line.proxy_caption == "METAR LELC (15 NM E) · TAF none"  # LELC issues no TAF


def test_proxy_switch_causes_no_spurious_rows(lemi_replays):
    """From the switch on, LEMI's rows are exactly those of a flight that
    read LELC all along: the switch itself produces nothing."""
    steady, switched = lemi_replays
    assert switched[-1].layer.route_observations.airport("LEMI").metar_proxy.icao == "LELC"
    before = next(t for t in switched if t.at.strftime("%H:%M") == "07:50")
    assert before.layer.route_observations.airport("LEMI").metar_proxy.icao == "LERI"
    a = [r for r in _lemi_rows(steady) if r[0] >= "08:00"]
    b = [r for r in _lemi_rows(switched) if r[0] >= "08:00"]
    assert a == b


# --- Surfaces -----------------------------------------------------------------


def test_live_summary_lists_the_field_with_the_station_report():
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import summarize_live

    obs = _proxied_egtf()
    layer = LiveLayer(flight_id="f", pack_timestamp="t", pack_dir_name="p", live_updated_at=NOW, route_observations=obs)
    briefing = {"route": {"waypoints": [{"icao": "EGTF"}, {"icao": "EGKA"}]}}
    out = summarize_live(layer, briefing)
    dep = next(a for a in out["airports"] if a["role"] == "departure")
    assert dep["icao"] == "EGTF" and dep["metar_station"] == "EGLL"
    assert dep["metar_raw"].startswith("EGLL ")
    assert dep["proxy_caption"].startswith("using EGLL as proxy")

    from weatherbrief.notify.email import _metar_text

    assert _metar_text(dep).startswith("EGTF (via EGLL) ")


def test_digest_states_the_proxy_and_marks_terminals():
    from types import SimpleNamespace

    from weatherbrief.digest.format_utils import terminal_proxy_lines, terminal_roles
    from weatherbrief.digest.prompt_builder import _format_observations_context

    obs = _proxied_egtf()
    snap = SimpleNamespace(route=SimpleNamespace(waypoints=[SimpleNamespace(icao="EGTF"), SimpleNamespace(icao="EGLL")]))
    roles = terminal_roles(snap)
    assert terminal_proxy_lines(obs, roles)[0].startswith("DEPARTURE EGTF has no METAR or TAF; EGLL (")
    text = _format_observations_context(obs, roles)
    assert "DEPARTURE EGTF has no METAR or TAF" in text
    assert "EGLL DESTINATION (" in text


def test_alternate_requirement_reads_the_proxy_taf_with_a_caveat(monkeypatch):
    from types import SimpleNamespace

    from weatherbrief.models.alternates import RouteAlternates
    from weatherbrief.tasks import alternate_requirement as ar

    field = _row("EGTF", metar=False, taf=False)
    egll = _row("EGLL")
    obs = _obs(field, egll)
    apply_obs_proxies(obs, ["EGTF"], elevations=ELEV, now=NOW)
    seen = {}

    def window(taf_raw, eta, ceiling, vis):
        seen["taf"] = taf_raw
        return SimpleNamespace(source="taf", main_body_ceiling_ft=None, main_body_visibility_m=None), []

    monkeypatch.setattr(ar, "_build_destination_window", window)
    monkeypatch.setattr(ar, "_destination_approach_class", lambda *_: (None, False))
    monkeypatch.setattr(ar, "compute_faa_trigger", lambda w: None)
    monkeypatch.setattr(ar, "compute_easa_trigger", lambda *a, **k: None)
    monkeypatch.setattr(ar, "AlternateRequirement", lambda **kw: SimpleNamespace(**kw))
    alts = RouteAlternates(destination_icao="EGTF", destination_category="VFR", corridor_nm=40,
                           radius_nm=50, eta=NOW, alternates=[])
    snap = SimpleNamespace(route=SimpleNamespace(destination=SimpleNamespace(icao="EGTF")),
                           alternates=alts, route_observations=obs)
    ar.run_alternate_requirement(snap, "unused.db", now=NOW)
    assert seen["taf"] == egll.taf_raw
    assert any(c.startswith("EGTF publishes no TAF: the destination trigger reads EGLL's TAF")
               for c in alts.alternate_requirement.caveats)


def test_run_route_weather_wires_the_proxy_and_the_field_dials(monkeypatch):
    """The terminal step in run_route_weather: proxy set, dials rebuilt with
    the station's wind over the field's runways."""
    from weatherbrief.models.analysis import RouteConfig, Waypoint
    from weatherbrief.tasks import route_weather as rw

    route = RouteConfig(name="t", waypoints=[
        Waypoint(icao="EGTF", name="Fairoaks", lat=GEO["EGTF"][0], lon=GEO["EGTF"][1]),
        Waypoint(icao="EGKA", name="Shoreham", lat=50.8356, lon=-0.2972),
    ])
    obs = _proxied_egtf()
    field = obs.airport("EGTF")
    field.metar_proxy = field.taf_proxy = None
    # The pipeline step judges freshness on the wall clock.
    obs.airport("EGLL").metar_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    monkeypatch.setattr("weatherbrief.airports.get_airport_elevations", lambda icaos, db: ELEV)
    rw._apply_terminal_proxies(obs, route, "unused.db", {"EGTF": NOW}, None)
    assert field.metar_proxy.icao == "EGLL"
    winds = field.runway_wind.winds
    assert [w.wind.source for w in winds] == ["metar"]  # the fixture TAF carries no wind
    assert winds[0].wind.speed_kt == 8 and winds[0].best_end == "25"


def test_proxy_line_names_the_field_for_the_observations_table():
    """#776: the line above the observations table is server-built, so web,
    iOS and the PDF report read one string; the field's own row stays None."""
    obs = _proxied_egtf()
    egtf = obs.airport("EGTF")
    assert egtf.proxy_line == f"EGTF: {egtf.proxy_caption}"
    assert egtf.proxy_line.startswith("EGTF: using EGLL as proxy")
    assert obs.airport("EGLL").proxy_line is None
    assert obs.model_dump(mode="json")["airports"][0].keys() >= {"proxy_line"}
