"""The departure card's block and its fold rule (#880).

Same synthetic route as ``test_live_glance_strip``: ZZDP → ZZDS, ~154 NM,
1.5 h, departing 12:00Z. The fold is by evidence only: a take-off entered or
inferred from the fixes, or the app's Start fix 15 min old with the flight
past the take-off it implies. Never by the planned time.
"""

from __future__ import annotations

from datetime import timedelta

from test_flight_progress import fix, mins
from test_live_glance import _airport, _layer, _obs
from test_live_storms import DEP, ROUTE

from weatherbrief.analysis.flight_progress import FlightProgress, ProgressEvent
from weatherbrief.models.live import LiveLayerResponse
from weatherbrief.tasks.live_glance import build_glance
from weatherbrief.tasks.live_layer import redact_live, redact_summary, summarize_live


def _layer_with_wind():
    dep = _airport("ZZDP", 0.0, taf={
        "taf_flight_category_at_eta": "VFR", "taf_prevailing_category_at_eta": "VFR",
        "taf_wind_dir": 290, "taf_wind_speed_kt": 16, "taf_wind_gust_kt": 27,
        "taf_best_runway_id": "27", "taf_crosswind_kt": -6.0, "taf_wind_advisory": "amber",
    }).model_copy(update={"metar_wind_dir": 290, "metar_wind_speed_kt": 15, "metar_wind_gust_kt": 25})
    return _layer(observations=_obs(dep, _airport("ZZDS", 154.0)))


def _departure(now, *events, layer=None):
    layer = layer or _layer_with_wind()
    glance, ribbon = build_glance(layer, ROUTE, FlightProgress.from_route(ROUTE, DEP, now, events))
    layer.glance, layer.ribbon = glance, ribbon
    return glance.departure, layer


def _entry(kind, at_min):
    return ProgressEvent(kind=kind, at=mins(at_min), recorded_at=mins(at_min))


def test_departure_block_reads_at_the_etd():
    d, _ = _departure(DEP - timedelta(minutes=30))
    assert d.icao == "ZZDP" and d.etd == DEP
    assert d.metar_text == "ZZDP VFR" and d.metar_category == "VFR"
    assert d.taf_text == "TAF at ETD VFR" and d.taf_category == "VFR"
    assert d.wind_text == "Wind at ETD 290° 16G27 kt · RWY 27 crosswind 6 kt"
    assert d.wind_advisory == "amber"
    # The default layer's cell near the departure, with its map focus.
    assert d.cells_text.startswith("nearest cell 9 NM NE")
    assert len(d.cells) == 1 and d.cells[0].focus is not None
    assert d.airport is not None and d.airport.icao == "ZZDP"
    assert d.airport.metar_wind_short == "290/15G25"
    assert d.focus.id == "ZZDP"
    assert not d.folded and d.folded_text is None


def test_the_plan_alone_never_folds():
    # Past the planned departure, and past the planned arrival: nothing observed.
    for now in (mins(20), mins(120)):
        d, _ = _departure(now)
        assert not d.folded and d.folded_at is None and d.folded_source is None


def test_an_entered_takeoff_folds():
    d, _ = _departure(mins(60), _entry("takeoff", 40))
    assert d.folded and d.folded_source == "takeoff" and d.folded_at == mins(40)
    assert d.folded_text == "ZZDP 290/15G25 · departed 12:40Z"


def test_start_folds_fifteen_minutes_on_once_past_the_takeoff_it_implies():
    start = fix(5, 0.0, gs=0.0)  # Start pressed on the ground at ZZDP, 12:05Z
    assert not _departure(mins(8), start)[0].folded  # 3 min, on the ground
    assert not _departure(mins(19), start)[0].folded  # past ~12:15Z take-off, 14 min
    d, _ = _departure(mins(21), start)
    assert d.folded and d.folded_source == "fixes" and d.folded_at == mins(15)
    assert d.folded_text == "ZZDP 290/15G25 · departed ~12:15Z"


def test_still_on_the_ground_after_start_stays_open():
    # A newer ground fix moves the implied take-off: not under way yet.
    d, _ = _departure(mins(25), fix(5, 0.0, gs=0.0), fix(22, 0.0, gs=0.0))
    assert not d.folded


def test_an_airborne_fix_folds_from_the_inferred_takeoff():
    p = FlightProgress.from_route(ROUTE, DEP, mins(50), [fix(40, 0.5)])
    d, _ = _departure(mins(50), fix(40, 0.5))
    assert d.folded and d.folded_source == "fixes" and d.folded_at == p.takeoff_at
    assert d.folded_text.startswith("ZZDP 290/15G25 · departed ~")


def test_sharing_none_keeps_only_an_entered_takeoffs_fold():
    _, layer = _departure(mins(50), fix(40, 0.5))
    resp = LiveLayerResponse(flight_id=layer.flight_id, pack_timestamp=layer.pack_timestamp,
                             glance=layer.glance, ribbon=layer.ribbon)
    assert redact_live(resp, "along_route").glance.departure.folded
    hidden = redact_live(resp, "none").glance.departure
    assert not hidden.folded and hidden.folded_at is None and hidden.folded_text is None
    assert hidden.etd is None
    block = summarize_live(layer, {})
    assert block["glance"]["departure"]["folded"] is True
    assert redact_summary(block, "none")["glance"]["departure"]["folded"] is False

    _, layer = _departure(mins(60), _entry("takeoff", 40))
    resp = LiveLayerResponse(flight_id=layer.flight_id, pack_timestamp=layer.pack_timestamp,
                             glance=layer.glance, ribbon=layer.ribbon)
    kept = redact_live(resp, "none").glance.departure
    assert kept.folded and kept.folded_text == "ZZDP 290/15G25 · departed 12:40Z"
    assert redact_summary(summarize_live(layer, {}), "none")["glance"]["departure"]["folded"] is True


def test_agent_block_quotes_both_cards():
    _, layer = _departure(mins(60), _entry("takeoff", 40))
    g = summarize_live(layer, {})["glance"]
    dep, dest = layer.glance.departure, layer.glance.destination
    assert g["departure"] == {
        "icao": "ZZDP", "metar_text": dep.metar_text, "taf_text": dep.taf_text,
        "wind_text": dep.wind_text, "folded": True, "folded_source": "takeoff",
    }
    assert g["destination"] == {
        "icao": "ZZDS", "metar_text": dest.metar_text, "taf_text": dest.taf_text,
        "wind_text": dest.wind_text, "cells_text": dest.cells_text,
    }


def test_a_failing_departure_leaves_the_rest(monkeypatch):
    import weatherbrief.tasks.live_glance as lg

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(lg, "departure_block", boom)
    glance, _ = build_glance(_layer(), ROUTE, FlightProgress.from_route(ROUTE, DEP, mins(10)))
    assert glance.departure is None and glance.destination is not None


def test_sharing_none_hides_the_eta_hour_on_the_metar_taf_rows():
    """Review round 1: the rows' ``eta_hour_offset`` and the strip's
    ``is_eta`` follow the live ETA, so ``none`` clears them everywhere."""
    from weatherbrief.models.observations import TafHour

    _, layer = _departure(mins(50), fix(40, 0.5))
    for block in (layer.glance.departure, layer.glance.destination):
        block.airport.eta_hour_offset = 1
        block.airport.taf_hours = [TafHour(time=mins(60), prevailing_category="VFR", is_eta=True)]
    obs = layer.route_observations.model_copy(deep=True)
    for a in obs.airports:
        a.eta_hour_offset = 1
        a.taf_hours = [TafHour(time=mins(60), prevailing_category="VFR", is_eta=True)]
    resp = LiveLayerResponse(flight_id=layer.flight_id, pack_timestamp=layer.pack_timestamp,
                             glance=layer.glance, ribbon=layer.ribbon, route_observations=obs)
    hidden = redact_live(resp, "none")
    rows = [*hidden.route_observations.airports, hidden.glance.departure.airport, hidden.glance.destination.airport]
    assert all(r.eta_hour_offset is None and not any(h.is_eta for h in r.taf_hours) for r in rows)
    assert [h.prevailing_category for h in rows[0].taf_hours] == ["VFR"]  # the strip itself stays
    # The owner's and along-route views keep the mark.
    kept = redact_live(resp, "along_route")
    assert kept.route_observations.airports[0].taf_hours[0].is_eta


def _change(kind, tier, role, storm_ids=None):
    from weatherbrief.models.live import LiveChange

    return LiveChange(
        key=f"{kind}:x", kind=kind, source="RADAR" if kind in ("storm", "radar") else "LIGHTNING",
        direction="worse", tier=tier, role=role, message="m", storm_ids=storm_ids,
    )


def test_card_rows_take_the_alert_tier_of_their_own_change_rows():
    """#881: a card row is toned only when an alert-tier change of its kind
    stands behind it: same terminal role, or (storms) a cell the card lists."""
    from weatherbrief.models.live import LiveChanges, LiveDestinationCell
    from weatherbrief.tasks.live_glance import _row_alerts

    cells = [LiveDestinationCell(id="core41-a", text="9 NM SW")]
    changes = lambda *cs: LiveChanges(computed_at=DEP, changes=list(cs))  # noqa: E731

    assert _row_alerts(None, "destination", cells) == (False, False)
    assert _row_alerts(changes(_change("storm", "alert", "destination")), "destination", []) == (True, False)
    # A highlight never tones; another terminal's alert neither.
    assert _row_alerts(changes(_change("storm", "highlight", "destination")), "destination", cells) == (False, False)
    assert _row_alerts(changes(_change("storm", "alert", "departure")), "destination", []) == (False, False)
    # A route-role storm row naming a listed cell tones the cells row.
    assert _row_alerts(changes(_change("storm", "alert", "route", ["core41-a"])), "destination", cells) == (True, False)
    assert _row_alerts(changes(_change("lightning", "alert", "departure")), "departure", []) == (False, True)
    assert _row_alerts(changes(_change("lightning", "alert", "route")), "departure", []) == (False, False)
