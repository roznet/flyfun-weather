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


def test_one_report_back_at_baseline_clears_the_change_but_not_the_alert_memory():
    """§45 (#722): the row clears, but the airport's worst alerted value is
    kept for the flight, so the relapse shows without pinging again."""
    t1, t2, t3 = (T0 + timedelta(minutes=m) for m in (30, 60, 90))
    base = _obs([_apt("ZZDS", "VFR")])
    _, mem = _classify(base, _obs([_apt("ZZDS", "IFR", t=t1)]))
    cleared, mem = _classify(base, _obs([_apt("ZZDS", "VFR", t=t2, prev="IFR", prev_t=t1)]), memory=mem)
    assert cleared.changes == []
    assert mem.alerted == {"metar:ZZDS": "IFR"}
    relapse, _ = _classify(base, _obs([_apt("ZZDS", "IFR", t=t3)]), memory=mem)
    [c] = relapse.changes
    assert c.tier == "alert" and c.new_alert is False


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


# --- #682: CB/TCU are what was observed, not the trend forecast --------------


def _tags(raw):
    from weatherbrief.tasks.live_significance import convective_tags

    return sorted(convective_tags(_apt("ZZDS", "VFR", raw=raw)))


def test_cb_tcu_read_from_the_observed_part_only():
    """The four reports from #682 (2026-10-03/04 prod history)."""
    # Observed TCU, amount and height unknown (French AUTO): was missed.
    assert _tags("LFLY 031200Z AUTO VRB03KT 9999 ///TCU 20/16 Q1028 NOSIG") == ["TCU"]
    # Forecast TCU in the trend: was reported as observed.
    assert _tags("LFLY 031230Z AUTO VRB03KT CAVOK 21/16 Q1028 TEMPO FEW060TCU") == []
    # Observed CB, amount and height unknown: was missed.
    assert _tags(
        "LFBP 041100Z AUTO 25008KT 9999 FEW018/// SCT037/// BKN066/// ///CB 23/18 Q1016"
    ) == ["CB"]
    # Observed TCU with a forecast CB: was reported as CB.
    assert _tags(
        "LFMT 041230Z AUTO 12005KT 9999 OVC096/// ///TCU 24/16 Q1024 TEMPO SHRA FEW045CB"
    ) == ["TCU"]


def test_cb_tcu_cloud_group_forms():
    assert _tags("ZZDS 041230Z 12005KT 9999 FEW022CB 24/16 Q1024") == ["CB"]
    assert _tags("ZZDS 041230Z 12005KT 9999 BKN///TCU 24/16 Q1024") == ["TCU"]
    assert _tags("ZZDS 041230Z 12005KT 9999 //////CB 24/16 Q1024") == ["CB"]
    assert _tags("ZZDS 041230Z 12005KT 9999 FEW030 24/16 Q1024 BECMG FEW030CB") == []
    assert _tags("ZZDS 041230Z 12005KT 9999 FEW030 24/16 Q1024 PROB30 FEW030CB") == []
    assert _tags("ZZDS 041230Z 12005KT 9999 FEW030 24/16 Q1024 RMK CB DIST NE") == []


def test_trend_tcu_coming_and_going_is_no_change():
    """LFMT on 2026-10-04 flip-flopped CB → cleared → CB as the TEMPO group
    came and went; the observed ///TCU never changed."""
    t1 = T0 + timedelta(minutes=30)
    base = _apt("ZZDS", "VFR", raw="ZZDS 011200Z AUTO 12005KT 9999 ///TCU 24/16 Q1024 NOSIG")
    latest = _apt("ZZDS", "VFR", t=t1,
                  raw="ZZDS 011230Z AUTO 12005KT 9999 ///TCU 24/16 Q1024 TEMPO SHRA FEW045CB")
    changes, _ = _classify(_obs([base]), _obs([latest]))
    assert changes.changes == []


# --- #682: radar / lightning identity is the condition, not its detail -------


def test_radar_and_lightning_values_are_categorical():
    """Peak dBZ and hit count move every tick; the change's value must not."""
    base = _observed(dbz=[20.0, 20.0], flashes=[0, 0])
    values = set()
    messages = set()
    for dbz, flashes in (([46.0, 20.0], [3, 0]), ([51.0, 54.0], [3, 9]), ([20.0, 49.0], [0, 2])):
        changes, _ = classify_changes(
            baseline_obs=None, latest_obs=None, baseline_sigmets=None, latest_sigmets=None,
            baseline_observed=base, latest_observed=_observed(dbz=dbz, flashes=flashes),
            flown_nm=0.0,
        )
        values |= {(c.kind, c.from_value, c.to_value) for c in changes.changes}
        messages |= {c.message for c in changes.changes}
    assert values == {("radar", "none", "heavy"), ("lightning", "none", "present")}
    # The detail stays in the message.
    assert any("peak 54 dBZ" in m for m in messages)


# --- #682: a SIGMET reissue is a replacement ---------------------------------


def _poly(text):
    """'N4200 E00400 - N4215 E00230 - …' → [(lon, lat), …]."""
    out = []
    for pt in text.split(" - "):
        lat_s, lon_s = pt.split()
        lat = int(lat_s[1:3]) + int(lat_s[3:5]) / 60
        lon = int(lon_s[1:4]) + int(lon_s[4:6]) / 60
        out.append((lon if lon_s[0] == "E" else -lon, lat if lat_s[0] == "N" else -lat))
    return out


def _at(day, hhmm):
    return datetime(2026, 10, day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)


def _real_sigmet(fir, seq, day, valid, area, qualifier="EMBD", hazard="TS"):
    start, end = valid.split("/")
    return SigmetAlongRoute(
        fir_id=fir, hazard=hazard, qualifier=qualifier,
        valid_from=_at(day, start[2:]), valid_to=_at(day, end[2:]),
        raw_text=f"{fir} SIGMET {seq} VALID {valid} LEVA- {qualifier} {hazard}",
        coords=_poly(area),
    )


# LFMM, 2026-10-04 (LFBZ→LFMD): embedded TS over the Gulf of Lion.
LFMM_T01 = _real_sigmet("LFMM", "T01", 4, "041050/041230",
                        "N4200 E00400 - N4215 E00230 - N4330 E00245 - N4315 E00400 - N4200 E00400")
LFMM_T02 = _real_sigmet("LFMM", "T02", 4, "041230/041400",
                        "N4145 E00445 - N4145 E00430 - N4200 E00430 - N4215 E00230 - N4315 E00245"
                        " - N4300 E00430 - N4145 E00445")
LFMM_T03 = _real_sigmet("LFMM", "T03", 4, "041415/041600",
                        "N4100 E00430 - N4200 E00430 - N4215 E00300 - N4315 E00315 - N4300 E00445"
                        " - N4115 E00530 - N4100 E00430")
# LECB, 2026-10-05 (LEPA→ELLX). LECB 5 is "E OF LINE": only the line.
LECB_2 = _real_sigmet("LECB", "2", 5, "050530/050700",
                      "N4059 E00037 - N4132 E00109 - N4212 E00208 - N4052 E00344 - N3949 E00232"
                      " - N3926 E00131 - N4002 E00040 - N4059 E00037")
LECB_3 = _real_sigmet("LECB", "3", 5, "050700/051000",
                      "N4115 E00036 - N4204 E00131 - N4207 E00314 - N4048 E00357 - N3952 E00303"
                      " - N3929 E00159 - N4031 E00029 - N4115 E00036")
LECB_4 = _real_sigmet("LECB", "4", 5, "051000/051200",
                      "N3946 E00201 - N3924 E00420 - N4157 E00441 - N4223 E00314 - N4222 E00232"
                      " - N4114 E00140 - N3946 E00201")
LECB_5 = _real_sigmet("LECB", "5", 5, "051200/051400", "N3925 E00330 - N4226 E00232")


def _sigmet_ticks(ticks, baseline=(), destination=None, arrival_at=None, band=(None, None), full=False):
    """Classify a sequence of (time, latest SIGMETs) with the memory carried
    from tick to tick; returns [(hhmm, changes)] (the whole ``LiveChanges``
    with ``full``)."""
    memory = ClassifierMemory()
    out = []
    for at, latest in ticks:
        changes, memory = classify_changes(
            baseline_obs=None, latest_obs=None,
            baseline_sigmets=RouteSigmets(corridor_nm=50.0, fetch_time=ticks[0][0] - timedelta(hours=1),
                                          sigmets=list(baseline)),
            latest_sigmets=RouteSigmets(corridor_nm=50.0, fetch_time=at, sigmets=list(latest),
                                        altitude_low_ft=band[0], altitude_high_ft=band[1]),
            destination=destination, arrival_at=arrival_at, memory=memory, now=at,
        )
        out.append((at.strftime("%H%M"), changes if full else changes.changes))
    return out


def _rows(changes):
    return [(c.tier, c.new_alert, c.message) for c in changes]


def test_lfmm_reissues_are_one_continuing_row():
    """LFMM T01 → T02 → T03 on 2026-10-04, none in the briefing: one alert,
    then replacements that keep the row's alert tier without alerting again,
    across the 12:32 tick where T01 had expired and T02 was not out yet."""
    ticks = _sigmet_ticks([
        (_at(4, "1100"), [LFMM_T01]),
        (_at(4, "1232"), []),
        (_at(4, "1242"), [LFMM_T02]),
        (_at(4, "1420"), [LFMM_T03]),
    ])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1100", [("alert", True, "New SIGMET LFMM T01: EMBD TS")]),
        ("1232", []),
        ("1242", [("alert", False, "SIGMET LFMM T02 replaces T01: EMBD TS")]),
        ("1420", [("alert", False, "SIGMET LFMM T03 replaces T02: EMBD TS")]),
    ]
    keys = [c.key for _, cs in ticks for c in cs]
    assert keys == ["sigmet:LFMM|T01", "sigmet:LFMM|T01+sigmet:LFMM|T02", "sigmet:LFMM|T01+sigmet:LFMM|T03"]
    assert [c.replaces for _, cs in ticks for c in cs] == [None, "LFMM T01", "LFMM T02"]


def test_reissue_of_a_briefing_sigmet_is_highlight_and_hides_its_clear():
    ticks = _sigmet_ticks([
        (_at(4, "1220"), [LFMM_T01]),
        (_at(4, "1232"), []),
        (_at(4, "1242"), [LFMM_T02]),
    ], baseline=[LFMM_T01])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1220", []),
        # T01 expired and its reissue is not out yet: honest at the time.
        ("1232", [("highlight", False, "SIGMET LFMM T01: EMBD TS no longer active")]),
        ("1242", [("highlight", False, "SIGMET LFMM T02 replaces T01: EMBD TS")]),
    ]


def test_end_of_a_reissue_chain_from_the_briefing_says_so():
    """The briefing's T01 → T02 → T03: while a reissue is listed, T01's
    "no longer active" is hidden; once the last one expires with no
    successor, it shows (the phenomenon ended)."""
    ticks = _sigmet_ticks([
        (_at(4, "1242"), [LFMM_T02]),
        (_at(4, "1420"), [LFMM_T03]),
        (_at(4, "1605"), []),
    ], baseline=[LFMM_T01])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1242", [("highlight", False, "SIGMET LFMM T02 replaces T01: EMBD TS")]),
        ("1420", [("highlight", False, "SIGMET LFMM T03 replaces T02: EMBD TS")]),
        ("1605", [("highlight", False, "SIGMET LFMM T01: EMBD TS no longer active")]),
    ]


def test_sigmet_and_its_reissue_first_seen_together_alert_once():
    """Review round 2: T01 and T02 both new in one pass (the first tick, or
    after failed fetches). T01 never gets a row, so T02 must alert; the next
    reissue then does not."""
    ticks = _sigmet_ticks([
        (_at(4, "1225"), [LFMM_T01, LFMM_T02]),
        (_at(4, "1235"), [LFMM_T02]),
        (_at(4, "1420"), [LFMM_T03]),
    ])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1225", [("alert", True, "SIGMET LFMM T02 replaces T01 from 12:30Z: EMBD TS")]),
        ("1235", [("alert", False, "SIGMET LFMM T02 replaces T01: EMBD TS")]),
        ("1420", [("alert", False, "SIGMET LFMM T03 replaces T02: EMBD TS")]),
    ]


def test_reissue_listed_past_its_validity_keeps_its_row():
    """Review round 3: a feed still listing T02 an hour after it ended must
    not turn it back into a fresh "New SIGMET" that alerts again."""
    ticks = _sigmet_ticks([
        (_at(4, "1100"), [LFMM_T01]),
        (_at(4, "1242"), [LFMM_T02]),
        (_at(4, "1505"), [LFMM_T02]),  # T02 valid to 14:00
    ])
    assert _rows(ticks[2][1]) == [("alert", False, "SIGMET LFMM T02 replaces T01: EMBD TS")]
    assert ticks[2][1][0].key == "sigmet:LFMM|T01+sigmet:LFMM|T02"


def test_predecessor_and_reissue_listed_together_are_one_row():
    [(_, rows)] = _sigmet_ticks([(_at(4, "1225"), [LFMM_T01, LFMM_T02])], baseline=[LFMM_T01])
    assert _rows(rows) == [("highlight", False, "SIGMET LFMM T02 replaces T01 from 12:30Z: EMBD TS")]


def test_lecb_reissues_including_an_e_of_line_area():
    """LECB 2 → 3 → 4 → 5 on 2026-10-05; LECB 5 is 'E OF LINE'."""
    ticks = _sigmet_ticks([
        (_at(5, "0600"), [LECB_2]),
        (_at(5, "0709"), [LECB_3]),
        (_at(5, "1005"), [LECB_4]),
        (_at(5, "1205"), [LECB_5]),
    ])
    assert [m for _, cs in ticks for (_, _, m) in _rows(cs)] == [
        "New SIGMET LECB 2: EMBD TS",
        "SIGMET LECB 3 replaces 2: EMBD TS",
        "SIGMET LECB 4 replaces 3: EMBD TS",
        "SIGMET LECB 5 replaces 4: EMBD TS",
    ]
    assert [c.new_alert for _, cs in ticks for c in cs] == [True, False, False, False]


def test_sigmet_without_geometry_is_never_a_reissue():
    no_area = LFMM_T02.model_copy(update={"coords": []})
    ticks = _sigmet_ticks([(_at(4, "1100"), [LFMM_T01]), (_at(4, "1242"), [no_area])])
    assert _rows(ticks[1][1]) == [("alert", True, "New SIGMET LFMM T02: EMBD TS")]


def test_reissue_reaching_the_destination_alerts():
    # A destination inside T02's area and ~29 NM outside T01's.
    dest = (41.8, 4.6)
    assert not any(
        c.role == "destination"
        for _, cs in _sigmet_ticks([(_at(4, "1100"), [LFMM_T01])], destination=dest) for c in cs
    )
    ticks = _sigmet_ticks(
        [(_at(4, "1100"), [LFMM_T01]), (_at(4, "1242"), [LFMM_T02])], baseline=[LFMM_T01], destination=dest,
    )
    [c] = ticks[1][1]
    assert (c.tier, c.new_alert, c.role) == ("alert", True, "destination")
    assert c.message == "SIGMET LFMM T02 replaces T01: EMBD TS (at destination)"


def test_escalation_to_sev_is_a_new_sigmet_not_a_reissue():
    sev = LFMM_T02.model_copy(update={"qualifier": "SEV", "raw_text": LFMM_T02.raw_text.replace("EMBD", "SEV")})
    ticks = _sigmet_ticks([(_at(4, "1100"), [LFMM_T01]), (_at(4, "1242"), [sev])], baseline=[LFMM_T01])
    assert _rows(ticks[1][1]) == [
        ("alert", True, "New SEV SIGMET LFMM T02: SEV TS"),
        ("highlight", False, "SIGMET LFMM T01: EMBD TS no longer active"),
    ]


def test_other_fir_or_late_issue_is_not_a_reissue():
    other_fir = LFMM_T02.model_copy(update={"fir_id": "LECB", "raw_text": "LECB SIGMET 9 VALID"})
    late = LFMM_T02.model_copy(update={
        "valid_from": LFMM_T01.valid_to + timedelta(minutes=90),
        "valid_to": LFMM_T01.valid_to + timedelta(hours=3),
    })
    for latest in (other_fir, late):
        ticks = _sigmet_ticks([(_at(4, "1100"), [LFMM_T01]), (_at(4, "1300"), [latest])])
        [c] = ticks[1][1]
        assert c.message.startswith("New SIGMET") and c.new_alert is True


# --- Pending SIGMETs: issued, not yet valid (#683) ---------------------------


def test_pending_sigmet_alerts_once_with_its_start_time():
    """The fetch looks ahead for SIGMETs issued before their validity: the row
    says when it starts, alerts at once, and does not alert again (nor make a
    new trail event) when its validity begins."""
    from weatherbrief.tasks.live_layer import change_identity

    ticks = _sigmet_ticks([
        (_at(4, "1020"), [LFMM_T01]),  # valid 10:50
        (_at(4, "1055"), [LFMM_T01]),
    ])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1020", [("alert", True, "New SIGMET LFMM T01: EMBD TS from 10:50Z")]),
        ("1055", [("alert", False, "New SIGMET LFMM T01: EMBD TS")]),
    ]
    assert change_identity(ticks[0][1][0]) == change_identity(ticks[1][1][0])


def test_lepa_ellx_lecb_3_alerts_before_departure():
    """LEPA→ELLX 2026-10-05, dep 07:05Z: LECB 3 (EMBD TS over LEPA) was
    issued 06:32 to start 07:00. With the lookahead the 06:36 tick lists it
    beside LECB 2, whose reissue it is: one alert before departure, none at
    07:09 once it is valid."""
    ticks = _sigmet_ticks([
        (_at(5, "0636"), [LECB_2, LECB_3]),
        (_at(5, "0709"), [LECB_3]),
    ])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("0636", [("alert", True, "SIGMET LECB 3 replaces 2 from 07:00Z: EMBD TS")]),
        ("0709", [("alert", False, "SIGMET LECB 3 replaces 2: EMBD TS")]),
    ]


def test_pending_sigmet_missing_for_a_tick_does_not_alert_twice():
    """A failed lookahead query drops a pending SIGMET for one tick. Its row
    goes (nothing to show), but its return is not a second alert."""
    ticks = _sigmet_ticks([
        (_at(4, "1020"), [LFMM_T01]),
        (_at(4, "1030"), []),
        (_at(4, "1040"), [LFMM_T01]),
    ])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1020", [("alert", True, "New SIGMET LFMM T01: EMBD TS from 10:50Z")]),
        ("1030", []),
        ("1040", [("alert", False, "New SIGMET LFMM T01: EMBD TS from 10:50Z")]),
    ]


def test_briefing_sigmet_that_never_started_is_not_no_longer_active():
    """A pending SIGMET the briefing had, missing from a tick before its
    start, was never active: no "no longer active" row. Once its validity
    has started, missing does mean ended."""
    ticks = _sigmet_ticks([
        (_at(4, "1030"), []),
        (_at(4, "1100"), []),
    ], baseline=[LFMM_T01])
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1030", []),
        ("1100", [("highlight", False, "SIGMET LFMM T01: EMBD TS no longer active")]),
    ]


def test_pending_alert_memory_clears_once_it_should_have_started():
    """The kept memory is only for a SIGMET still before its start: one
    missing after it (cancelled, or ended) is forgotten as before."""
    memory = ClassifierMemory()
    for at, latest in ((_at(4, "1020"), [LFMM_T01]), (_at(4, "1030"), [])):
        _, memory = classify_changes(
            baseline_obs=None, latest_obs=None,
            baseline_sigmets=_sigmets([]), latest_sigmets=_sigmets(latest),
            memory=memory, now=at,
        )
    assert "sigmet:LFMM|T01" in memory.alerted
    _, memory = classify_changes(
        baseline_obs=None, latest_obs=None,
        baseline_sigmets=_sigmets([]), latest_sigmets=_sigmets([]),
        memory=memory, now=_at(4, "1055"),
    )
    assert "sigmet:LFMM|T01" not in memory.alerted


# --- #689: after arrival, reissue direction, one NEW rule ---------------------


def test_pending_sigmet_starting_after_arrival_is_a_highlight():
    """#687/#689: dep 07:05Z, arrival 09:10Z; a SIGMET issued 08:30Z valid
    from 10:00Z starts after landing (+ the 30 min margin): highlight, with
    its start time, no alert."""
    after = LECB_3.model_copy(update={"valid_from": _at(5, "1000"), "valid_to": _at(5, "1200")})
    [(_, rows)] = _sigmet_ticks([(_at(5, "0830"), [after])], arrival_at=_at(5, "0910"))
    assert _rows(rows) == [("highlight", False, "New SIGMET LECB 3: EMBD TS from 10:00Z")]


def test_pending_sigmet_starting_within_the_arrival_margin_alerts():
    within = LECB_3.model_copy(update={"valid_from": _at(5, "0935"), "valid_to": _at(5, "1200")})
    [(_, rows)] = _sigmet_ticks([(_at(5, "0830"), [within])], arrival_at=_at(5, "0910"))
    assert _rows(rows) == [("alert", True, "New SIGMET LECB 3: EMBD TS from 09:35Z")]


def test_without_an_arrival_time_a_pending_sigmet_alerts_as_before():
    [(_, rows)] = _sigmet_ticks([(_at(4, "1020"), [LFMM_T01])])
    assert _rows(rows) == [("alert", True, "New SIGMET LFMM T01: EMBD TS from 10:50Z")]


def test_after_arrival_chain_stays_highlight_and_unalerted():
    """A chain shown only as an after-arrival highlight has not alerted (its
    trace says so), and its reissue, also after arrival, stays a highlight."""
    ticks = _sigmet_ticks([
        (_at(4, "1020"), [LFMM_T01]),   # valid 10:50, arrival 10:00
        (_at(4, "1225"), [LFMM_T02]),   # valid 12:30
    ], arrival_at=_at(4, "1000"))
    assert [(t, _rows(c)) for t, c in ticks] == [
        ("1020", [("highlight", False, "New SIGMET LFMM T01: EMBD TS from 10:50Z")]),
        ("1225", [("highlight", False, "SIGMET LFMM T02 replaces T01 from 12:30Z: EMBD TS")]),
    ]
    _, memory = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=_sigmets([]),
        latest_sigmets=_sigmets([LFMM_T01]), arrival_at=_at(4, "1000"), now=_at(4, "1020"),
    )
    assert memory.sigmets["sigmet:LFMM|T01"].chain_alerted is False
    assert memory.alerted == {}


def test_plain_reissue_of_a_briefing_sigmet_is_updated_not_worse():
    """#689: "SIGMET LECM 6 replaces 4" of a briefed SIGMET drove "1 worse
    since briefing". A plain reissue is "updated" and not counted."""
    [(_, changes)] = _sigmet_ticks([(_at(4, "1242"), [LFMM_T02])], baseline=[LFMM_T01], full=True)
    [c] = changes.changes
    assert (c.direction, c.tier, c.message) == ("updated", "highlight", "SIGMET LFMM T02 replaces T01: EMBD TS")
    assert changes.worsened_count == 0 and changes.improved_count == 0
    assert worsening_delta(changes).worsened is False


def test_reissue_of_a_chain_new_since_the_briefing_stays_worse():
    ticks = _sigmet_ticks([(_at(4, "1100"), [LFMM_T01]), (_at(4, "1242"), [LFMM_T02])])
    assert [c.direction for _, cs in ticks for c in cs] == ["worse", "worse"]


def test_reissue_whose_area_now_reaches_the_route_is_worse():
    near = LFMM_T01.model_copy(update={"min_distance_nm": 12.0})
    crossing = LFMM_T02.model_copy(update={"min_distance_nm": 0.0})
    [(_, [c])] = _sigmet_ticks([(_at(4, "1242"), [crossing])], baseline=[near])
    assert (c.direction, c.tier, c.new_alert) == ("worse", "highlight", False)
    # Already on the route before: a plain update.
    on = LFMM_T01.model_copy(update={"min_distance_nm": 0.0})
    [(_, [c])] = _sigmet_ticks([(_at(4, "1242"), [crossing])], baseline=[on])
    assert c.direction == "updated"


def test_reissue_whose_tops_now_reach_the_flight_band_is_worse():
    band = (0, 9000)
    above = LFMM_T01.model_copy(update={"base_ft": 12000, "top_ft": 30000})
    lower = LFMM_T02.model_copy(update={"base_ft": 6000, "top_ft": 30000})
    [(_, [c])] = _sigmet_ticks([(_at(4, "1242"), [lower])], baseline=[above], band=band)
    assert c.direction == "worse"
    # Unknown levels on either side: no evidence of a change.
    unknown = LFMM_T01.model_copy(update={"base_ft": None, "top_ft": None})
    [(_, [c])] = _sigmet_ticks([(_at(4, "1242"), [lower])], baseline=[unknown], band=band)
    assert c.direction == "updated"


def test_reissue_direction_is_decided_once():
    """The direction is fixed when the reissue is first seen: a later tick
    with a different min distance does not flip the row."""
    near = LFMM_T01.model_copy(update={"min_distance_nm": 12.0})
    still_near = LFMM_T02.model_copy(update={"min_distance_nm": 12.0})
    crossing = LFMM_T02.model_copy(update={"min_distance_nm": 0.0})
    ticks = _sigmet_ticks([(_at(4, "1242"), [still_near]), (_at(4, "1300"), [crossing])], baseline=[near])
    assert [c.direction for _, cs in ticks for c in cs] == ["updated", "updated"]


def test_new_sigmets_follow_the_chain_not_the_change_rows():
    """#689: Area Hazards badged LECM 6 NEW while the list said "replaces 4".
    NEW = a listed SIGMET whose chain did not start in the baseline."""
    [(_, changes)] = _sigmet_ticks(
        [(_at(5, "1005"), [LFMM_T02, LECB_4])], baseline=[LFMM_T01], full=True,
    )
    assert changes.new_sigmets == ["sigmet:LECB|4"]
    # A reissue of a chain new since the briefing is still new.
    ticks = _sigmet_ticks([(_at(4, "1100"), [LFMM_T01]), (_at(4, "1242"), [LFMM_T02])], full=True)
    assert ticks[1][1].new_sigmets == ["sigmet:LFMM|T02"]


def test_new_sigmets_not_computed_without_a_sigmet_baseline():
    changes, _ = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=None,
        latest_sigmets=_sigmets([_sig(3)]),
    )
    assert changes.new_sigmets is None


def test_every_listed_sigmet_has_a_trace():
    """new_sigmets reads a trace per listed SIGMET: _trace_sigmets must give
    every one a trace, superseded predecessors included."""
    _, memory = classify_changes(
        baseline_obs=None, latest_obs=None, baseline_sigmets=_sigmets([LFMM_T01]),
        latest_sigmets=_sigmets([LFMM_T01, LFMM_T02, LECB_4, _sig(3)]), now=_at(4, "1225"),
    )
    from weatherbrief.tasks.live_significance import _sigmet_key_str
    for s in (LFMM_T01, LFMM_T02, LECB_4, _sig(3)):
        assert _sigmet_key_str(s) in memory.sigmets
