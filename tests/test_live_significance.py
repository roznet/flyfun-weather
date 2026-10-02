"""Significance classifier for the live layer (#637).

Baseline is the briefing; both directions; a METAR category crossing counts on
the first report (no hysteresis, meteorology-decisions §35); alert tier for the
destination and for every SIGMET change on the route; one alert per value.
"""

from datetime import datetime, timedelta, timezone

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
from weatherbrief.tasks.live_significance import (
    ClassifierMemory,
    airport_roles,
    classify_changes,
    worsening_delta,
)

T0 = datetime(2026, 10, 1, 7, 20, tzinfo=timezone.utc)  # briefing's METAR


def _obs(airports):
    return RouteObservations(
        corridor_nm=30.0,
        fetch_time=T0,
        airports_found=len(airports),
        airports_with_metar=len(airports),
        airports_with_taf=len(airports),
        airports=airports,
    )


def _apt(icao, metar=None, taf=None, *, t=T0, prev=None, prev_t=None, speci=False, enroute=None,
         raw=None, wx=(), wind=None, xwind=None, gust=None, rwy=None):
    return AirportObservation(
        icao=icao,
        distance_from_route_nm=1.0,
        enroute_distance_nm=enroute,
        nearest_waypoint_icao=icao,
        metar_raw=raw,
        metar_flight_category=metar,
        metar_time=t,
        metar_report_type="SPECI" if speci else "METAR",
        metar_previous_flight_category=prev,
        metar_previous_time=prev_t,
        metar_weather=list(wx),
        metar_wind_advisory=wind,
        metar_crosswind_kt=xwind,
        metar_wind_gust_kt=gust,
        metar_best_runway_id=rwy,
        taf_flight_category_at_eta=taf,
    )


def _sigmets(sigmets):
    return RouteSigmets(corridor_nm=50.0, fetch_time=T0, sigmets=sigmets)


def _sig(seq, qualifier="EMBD", hazard="TS", fir="LTBB", coords=None, valid_from=None):
    return SigmetAlongRoute(
        fir_id=fir, hazard=hazard, qualifier=qualifier,
        raw_text=f"{fir} SIGMET {seq} VALID 211644/211918",
        coords=coords or [], valid_from=valid_from,
    )


def _box(lon0, lat0, lon1, lat1):
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]


ROLES = airport_roles(["ZZDP", "ZZDS"], ["ZZAL"])


def _classify(base, latest, memory=None, **kw):
    return classify_changes(
        baseline_obs=base, latest_obs=latest,
        baseline_sigmets=kw.pop("bs", None), latest_sigmets=kw.pop("ls", None),
        roles=kw.pop("roles", ROLES), memory=memory, **kw,
    )


# --- METAR crossings (no hysteresis) ----------------------------------------


def test_single_crossing_report_is_a_change_at_once():
    t1 = T0 + timedelta(minutes=30)
    changes, _ = _classify(
        _obs([_apt("ZZDS", "VFR")]),
        _obs([_apt("ZZDS", "IFR", t=t1, prev="VFR", prev_t=T0)]),
    )
    [c] = changes.changes
    assert (c.from_value, c.to_value) == ("VFR", "IFR")
    assert c.tier == "alert" and c.new_alert is True


def test_two_consecutive_reports_confirm_the_crossing():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    changes, _ = _classify(
        _obs([_apt("ZZDS", "VFR")]),
        _obs([_apt("ZZDS", "IFR", t=t2, prev="MVFR", prev_t=t1)]),
    )
    [c] = changes.changes
    assert c.kind == "metar_category"
    assert c.direction == "worse"
    assert (c.from_value, c.to_value) == ("VFR", "IFR")
    assert c.role == "destination" and c.tier == "alert"
    assert c.message == "ZZDS METAR: VFR → IFR"


def test_speci_confirms_on_its_own():
    t1 = T0 + timedelta(minutes=10)
    changes, _ = _classify(
        _obs([_apt("ZZDP", "IFR")]),
        _obs([_apt("ZZDP", "VFR", t=t1, prev="IFR", prev_t=T0, speci=True)]),
    )
    [c] = changes.changes
    assert c.direction == "better"
    assert c.source == "SPECI"
    assert c.role == "departure" and c.tier == "highlight"
    assert c.message.endswith("(SPECI)")


def test_improvement_is_reported_both_directions():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    changes, _ = _classify(
        _obs([_apt("ZZDS", "LIFR")]),
        _obs([_apt("ZZDS", "MVFR", t=t2, prev="VFR", prev_t=t1)]),
    )
    [c] = changes.changes
    assert c.direction == "better"
    assert changes.improved_count == 1 and changes.worsened_count == 0


def test_same_report_as_briefing_is_no_change():
    changes, _ = _classify(_obs([_apt("ZZDS", "IFR")]), _obs([_apt("ZZDS", "IFR")]))
    assert changes.changes == []


def test_one_report_back_at_baseline_clears_the_change_and_alert_memory():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    base = _obs([_apt("ZZDS", "VFR")])
    _, mem = _classify(base, _obs([_apt("ZZDS", "IFR", t=t1)]))
    cleared, mem = _classify(base, _obs([_apt("ZZDS", "VFR", t=t2, prev="IFR", prev_t=t1)]), memory=mem)
    assert cleared.changes == []
    assert mem.alerted == {}


def test_report_without_category_neither_raises_nor_clears():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    base = _obs([_apt("ZZDS", "VFR")])
    _, mem = _classify(base, _obs([_apt("ZZDS", "IFR", t=t1)]))
    gap, mem = _classify(base, _obs([_apt("ZZDS", None, t=t2)]), memory=mem)
    assert gap.changes == []
    # Not a return: the alert memory survives, so the next IFR report does not
    # alert a second time.
    assert mem.alerted == {"metar:ZZDS": "IFR"}
    again, _ = _classify(base, _obs([_apt("ZZDS", "IFR", t=t2 + timedelta(minutes=30))]), memory=mem)
    assert again.changes[0].new_alert is False


def test_airport_missing_from_baseline_is_skipped():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    changes, _ = _classify(_obs([]), _obs([_apt("ZZXX", "IFR", t=t2, prev="IFR", prev_t=t1)]))
    assert changes.changes == []


# --- Tiers and alert memory -------------------------------------------------


def test_corridor_airport_is_highlight_not_alert():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    changes, _ = _classify(
        _obs([_apt("ZZMD", "VFR")]),
        _obs([_apt("ZZMD", "IFR", t=t2, prev="IFR", prev_t=t1)]),
    )
    [c] = changes.changes
    assert c.role == "route" and c.tier == "highlight"
    assert c.new_alert is False


def test_terminal_airports_alert_alternates_highlight():
    t1 = T0 + timedelta(minutes=30)
    changes, _ = _classify(
        _obs([_apt("ZZAL", "MVFR"), _apt("ZZDP", "VFR"), _apt("ZZDS", "VFR")]),
        _obs([_apt("ZZAL", "LIFR", t=t1), _apt("ZZDP", "IFR", t=t1), _apt("ZZDS", "IFR", t=t1)]),
    )
    tiers = {c.icao: (c.role, c.tier) for c in changes.changes}
    assert tiers == {
        "ZZDS": ("destination", "alert"),
        "ZZDP": ("departure", "alert"),  # before take-off: departure_at unset
        "ZZAL": ("alternate", "highlight"),
    }
    assert changes.alert_count == 2


def test_alert_fires_once_per_value():
    t1, t2, t3 = (T0 + timedelta(minutes=m) for m in (30, 60, 90))
    base = _obs([_apt("ZZDS", "VFR")])
    first, mem = _classify(base, _obs([_apt("ZZDS", "IFR", t=t2, prev="IFR", prev_t=t1)]))
    assert first.changes[0].new_alert is True
    again, mem = _classify(base, _obs([_apt("ZZDS", "IFR", t=t3, prev="IFR", prev_t=t2)]), memory=mem)
    assert again.changes[0].new_alert is False
    worse, mem = _classify(
        base, _obs([_apt("ZZDS", "LIFR", t=t3 + timedelta(minutes=30), prev="LIFR", prev_t=t3)]),
        memory=mem,
    )
    assert worse.changes[0].new_alert is True  # value moved: alert again


def test_roles_destination_and_departure_win_over_alternate():
    roles = airport_roles(["ZZAA", "ZZBB"], ["ZZBB", "ZZC1", "ZZC2", "ZZC3", "ZZC4"])
    assert roles["ZZAA"] == "departure"
    assert roles["ZZBB"] == "destination"
    # Only the top ALERT_ALTERNATES candidates are alternates.
    assert "ZZC3" not in roles


# --- TAF --------------------------------------------------------------------


def test_taf_at_eta_change_both_directions_without_hysteresis():
    changes, _ = _classify(
        _obs([_apt("ZZDS", taf="VFR"), _apt("ZZDP", taf="IFR")]),
        _obs([_apt("ZZDS", taf="IFR"), _apt("ZZDP", taf="MVFR")]),
    )
    by = {c.icao: c for c in changes.changes}
    assert by["ZZDS"].kind == "taf_category" and by["ZZDS"].direction == "worse"
    assert by["ZZDP"].direction == "better"
    assert by["ZZDS"].message == "ZZDS TAF at ETA: VFR → IFR"


def test_taf_appearing_or_lapsing_is_not_a_crossing():
    changes, _ = _classify(_obs([_apt("ZZDS", taf=None)]), _obs([_apt("ZZDS", taf="IFR")]))
    assert changes.changes == []


# --- SIGMETs ----------------------------------------------------------------


def test_sigmet_issued_and_cancelled():
    changes, _ = _classify(
        None, None,
        bs=_sigmets([_sig("12")]),
        ls=_sigmets([_sig("13", qualifier="SEV", hazard="TURB")]),
    )
    by = {c.message: c for c in changes.changes}
    assert by["New SEV SIGMET LTBB 13: SEV TURB"].tier == "alert"
    # A SIGMET ending is good news: highlight, never an alert.
    assert by["SIGMET LTBB 12: EMBD TS no longer active"].tier == "highlight"
    assert all(c.role == "route" for c in changes.changes)


# Destination at (37.80 N, 1.13 W) — LEMI on 2026-10-02.
DEST = (37.80, -1.13)
VF = datetime(2026, 10, 2, 8, 35, tzinfo=timezone.utc)


def test_sigmet_over_or_near_destination_takes_the_destination_role():
    near = _sig("3", fir="LECM", coords=_box(-1.6, 37.3, -1.35, 37.6), valid_from=VF)   # ~15 NM SW
    far = _sig("9", fir="LECB", hazard="TURB", qualifier="SEV",
               coords=_box(1.0, 40.0, 2.0, 41.0), valid_from=VF)
    changes, _ = _classify(
        None, None, bs=_sigmets([]), ls=_sigmets([near, far]), destination=DEST,
    )
    by = {c.key: c for c in changes.changes}
    assert by["sigmet:LECM|3"].role == "destination"
    assert by["sigmet:LECM|3"].message.endswith("(at destination)")
    assert by["sigmet:LECB|9"].role == "route"
    assert all(c.tier == "alert" for c in changes.changes)


def test_same_phenomenon_from_two_firs_is_one_change():
    """LECB 3 + LECM 3, 2026-10-02: one TS cell issued by both FIRs."""
    lecb = _sig("3", fir="LECB", coords=_box(-1.8, 36.7, -0.7, 37.8), valid_from=VF)
    lecm = _sig("3", fir="LECM", coords=_box(-2.2, 36.7, -1.3, 37.8), valid_from=VF)
    other = _sig("4", fir="LECM", coords=_box(-6.0, 40.0, -5.0, 41.0), valid_from=VF)
    changes, _ = _classify(
        None, None, bs=_sigmets([]), ls=_sigmets([lecb, lecm, other]), destination=DEST,
    )
    msgs = sorted(c.message for c in changes.changes)
    assert msgs == [
        "New SIGMET LECB 3 / LECM 3: EMBD TS (at destination)",
        "New SIGMET LECM 4: EMBD TS",
    ]


def test_sigmet_escalation_to_sev():
    changes, _ = _classify(
        None, None,
        bs=_sigmets([_sig("13", qualifier="EMBD")]),
        ls=_sigmets([_sig("13", qualifier="SEV")]),
    )
    [c] = changes.changes
    assert "escalated to SEV" in c.message


def test_reissued_sigmet_same_sequence_is_not_new():
    changes, _ = _classify(None, None, bs=_sigmets([_sig("13")]), ls=_sigmets([_sig("13")]))
    assert changes.changes == []


def test_missing_side_skips_dimension():
    changes, _ = _classify(None, None, bs=None, ls=_sigmets([_sig("13")]))
    assert changes.changes == []


# --- Observed lightning / radar --------------------------------------------


def _observed(*, flashes=None, dbz=None, coverage=1.0):
    """Two route points at 10 and 80 NM along track."""
    stations = [
        ObservedStationRef(id="p0", lat=50.0, lon=1.0, enroute_distance_nm=10.0),
        ObservedStationRef(id="p1", lat=50.5, lon=2.0, enroute_distance_nm=80.0),
    ]
    lightning = None
    if flashes is not None:
        lightning = ObservedFlashField(
            source="mtg_li", quantity="flashes", valid_time=T0, age_minutes=2,
            stations=[
                ObservedFlashStationSamples(
                    station_id=sid,
                    annuli=[ObservedFlashAnnulus(radius_nm=5, flash_count=n)],
                )
                for sid, n in zip(("p0", "p1"), flashes)
            ],
        )
    reflectivity = None
    if dbz is not None:
        total = 100
        valid = int(total * coverage)
        reflectivity = ObservedField(
            source="opera", quantity="dbzh", valid_time=T0, age_minutes=5,
            stations=[
                ObservedStationSamples(
                    station_id=sid,
                    annuli=[ObservedAnnulus(
                        radius_nm=5, total_px=total, valid_px=valid,
                        nodata_px=total - valid, max_value=v,
                    )],
                )
                for sid, v in zip(("p0", "p1"), dbz)
            ],
        )
    return ObservedConditions(
        computed_at=T0, corridor_nm=20, radii_nm=[5, 10, 20],
        stations=stations, lightning=lightning, reflectivity=reflectivity,
    )


def test_lightning_appearing_ahead_is_highlighted():
    changes, _ = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
        baseline_observed=_observed(flashes=[0, 0]),
        latest_observed=_observed(flashes=[0, 3]),
        flown_nm=0.0,
    )
    [c] = changes.changes
    assert c.kind == "lightning" and c.direction == "worse" and c.tier == "highlight"
    assert "at 80 NM along route" in c.message


def test_lightning_behind_the_aircraft_is_ignored():
    changes, _ = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
        baseline_observed=_observed(flashes=[0, 0]),
        latest_observed=_observed(flashes=[5, 0]),
        flown_nm=40.0,
    )
    assert changes.changes == []


def test_heavy_radar_echo_appearing_and_insufficient_coverage():
    base = _observed(dbz=[20.0, 25.0])
    changes, _ = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
        baseline_observed=base, latest_observed=_observed(dbz=[20.0, 44.0]), flown_nm=0.0,
    )
    [c] = changes.changes
    assert c.kind == "radar" and "44 dBZ" in c.message
    # Same echo on a radar that cannot see there is not asserted.
    blind, _ = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
        baseline_observed=base, latest_observed=_observed(dbz=[20.0, 44.0], coverage=0.2),
        flown_nm=0.0,
    )
    assert blind.changes == []


def test_observed_missing_on_baseline_is_skipped():
    changes, _ = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
        baseline_observed=None, latest_observed=_observed(flashes=[3, 3]),
    )
    assert changes.changes == []


# --- Compatibility delta ----------------------------------------------------


def test_worsening_delta_keeps_only_worse_messages():
    t1, t2 = T0 + timedelta(minutes=30), T0 + timedelta(minutes=60)
    changes, _ = _classify(
        _obs([_apt("ZZDS", "VFR"), _apt("ZZDP", "IFR")]),
        _obs([
            _apt("ZZDS", "IFR", t=t2, prev="IFR", prev_t=t1),
            _apt("ZZDP", "VFR", t=t2, prev="VFR", prev_t=t1),
        ]),
    )
    delta = worsening_delta(changes)
    assert delta.worsened is True
    assert delta.messages == ["ZZDS METAR: VFR → IFR"]


def test_fetch_failure_keeps_alert_memory():
    mem = ClassifierMemory(alerted={"metar:ZZDS": "IFR"})
    _, new_mem = classify_changes(
        baseline_obs=_obs([_apt("ZZDS", "VFR")]), latest_obs=None,
        baseline_sigmets=None, latest_sigmets=None, memory=mem,
    )
    # The next good tick does not alert again.
    assert new_mem.alerted == {"metar:ZZDS": "IFR"}


# --- Merged SIGMET alert memory (review of #642) ----------------------------


def _merged(fir, seq, coords, vf):
    return _sig(seq, fir=fir, coords=coords, valid_from=vf)


def test_partner_fir_issuing_late_does_not_re_alert():
    """LECB 3 issued first, LECM 3 for the same cell a tick later: the merged
    change keeps its alert (one phenomenon, already alerted)."""
    lecb = _merged("LECB", "3", _box(-1.8, 36.7, -0.7, 37.8), VF)
    lecm = _merged("LECM", "3", _box(-2.2, 36.7, -1.3, 37.8), VF)
    first, mem = _classify(None, None, bs=_sigmets([]), ls=_sigmets([lecb]), destination=DEST)
    assert [c.new_alert for c in first.changes] == [True]
    both, mem = _classify(None, None, bs=_sigmets([]), ls=_sigmets([lecb, lecm]), memory=mem, destination=DEST)
    [c] = both.changes
    assert c.key == "sigmet:LECB|3+sigmet:LECM|3" and c.new_alert is False
    assert set(mem.alerted) == {"sigmet:LECB|3+sigmet:LECM|3"}
    # One of the pair lapses: still the same phenomenon, still no new alert.
    one, mem = _classify(None, None, bs=_sigmets([]), ls=_sigmets([lecm]), memory=mem, destination=DEST)
    [c] = one.changes
    assert c.key == "sigmet:LECM|3" and c.new_alert is False


def test_a_different_sigmet_still_alerts():
    lecb = _merged("LECB", "3", _box(-1.8, 36.7, -0.7, 37.8), VF)
    other = _merged("LECB", "4", _box(1.0, 40.0, 2.0, 41.0), VF)
    _, mem = _classify(None, None, bs=_sigmets([]), ls=_sigmets([lecb]))
    again, _ = _classify(None, None, bs=_sigmets([]), ls=_sigmets([lecb, other]), memory=mem)
    by = {c.key: c.new_alert for c in again.changes}
    assert by == {"sigmet:LECB|3": False, "sigmet:LECB|4": True}
