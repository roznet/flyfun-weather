"""The Observed tab's flight strip and destination at ETA (#816).

Same synthetic route as ``test_live_glance``: ZZDP (50 N, 0 E) → ZZDS
(50 N, 4 E), due east, ~154 NM, 1.5 h, departing 12:00Z. Plan-only, app-fix,
estimated, on-the-ground and landed cases, each pinned to its exact text:
the strip is server-built and both clients show it verbatim.
"""

from __future__ import annotations

from datetime import timedelta

from test_flight_progress import fix, mins
from test_live_glance import NOW, _airport, _layer, _obs
from test_live_storms import DEP, ROUTE, cell, storms_at

from weatherbrief.analysis.flight_progress import FlightProgress, ProgressEvent
from weatherbrief.models.live import LiveLayerResponse
from weatherbrief.tasks.live_glance import build_glance
from weatherbrief.tasks.live_layer import redact_live, redact_summary, summarize_live


def _progress(now, *events):
    return FlightProgress.from_route(ROUTE, DEP, now, events)


def _strip(now, *events, layer=None):
    glance, _ = build_glance(layer or _layer(), ROUTE, _progress(now, *events))
    return glance.flight


def _entry(kind, at_min, rec_min=None):
    return ProgressEvent(kind=kind, at=mins(at_min), recorded_at=mins(rec_min if rec_min is not None else at_min))


# --- Plan only ------------------------------------------------------------------


def test_plan_only_before_during_and_after():
    before = _strip(DEP - timedelta(minutes=30))
    assert before.phase == "planned" and before.source == "plan"
    assert before.text == "Planned · departure 12:00Z · ETA ZZDS 13:30Z"
    assert before.marker_label == "Planned position"
    assert before.eta_delay_min is None and before.estimated

    during = _strip(mins(45))
    assert during.text == "Planned · 77 of 154 NM · ETA ZZDS 13:30Z"
    assert during.along_nm == during.route_nm / 2

    # Past the planned arrival with nothing observed: never "Landed".
    after = _strip(mins(120))
    assert after.phase == "planned"
    assert after.text == "Planned · arrival ZZDS 13:30Z"
    assert after.landed_at is None


def test_untimed_flight_has_no_strip():
    glance, _ = build_glance(_layer(), ROUTE, FlightProgress.from_route(ROUTE, None, NOW))
    assert glance.flight is None


# --- App fixes --------------------------------------------------------------------


def test_fresh_app_fix_reads_position_age_and_delay():
    f = fix(40, 0.5)  # ~19 NM along at +40: ~29 min late
    p = _progress(mins(50), f)
    strip = _strip(mins(50), f)
    delay = round((p.arrival - p.planned_arrival).total_seconds() / 60)
    assert delay > 0
    assert strip.phase == "enroute" and strip.source == "app"
    assert strip.text == (
        f"{p.flown_nm:.0f} of 154 NM · ETA ZZDS {p.arrival:%H:%MZ} (+{delay} min vs plan) · position 10 min ago"
    )
    assert strip.marker_label == "You · 10 min ago"
    assert strip.eta_delay_min == delay and not strip.estimated
    assert strip.departed_at == p.takeoff_at


def test_stale_fix_is_estimated():
    strip = _strip(mins(80), fix(40, 0.5))
    assert strip.text.endswith(" · position 40 min ago, estimated")
    assert strip.marker_label == "Estimated position"
    assert strip.estimated


def test_on_the_ground_at_the_departure_then_estimated_from_that_fix():
    g = fix(5, 0.0, gs=0.0)  # parked at ZZDP at 12:05Z: take-off 12:15Z at the earliest
    ground = _strip(mins(8), g)
    assert ground.phase == "ground"
    assert ground.text == "On the ground at ZZDP 12:05Z · take-off ~12:15Z · ETA ZZDS 13:45Z (+15 min vs plan)"
    assert ground.marker_label == "You · 3 min ago"
    assert ground.departed_at is None  # nothing observed the take-off

    # Past that take-off, nothing airborne since: the plan runs on from it,
    # and the strip says so rather than "position N min ago".
    later = _strip(mins(30), g)
    assert later.phase == "enroute"
    assert later.text.endswith("· last fix on the ground 12:05Z, estimated")
    assert later.marker_label == "Estimated position"


# --- Manual entries -----------------------------------------------------------------


def test_manual_takeoff_shifts_the_eta():
    strip = _strip(mins(60), _entry("takeoff", 40))
    assert strip.source == "plan+takeoff" and strip.phase == "enroute"
    assert strip.text == "34 of 154 NM · ETA ZZDS 14:10Z (+40 min vs plan) · estimated from take-off 12:40Z"
    assert strip.marker_label == "Estimated position"


def test_landed_entered_and_inferred():
    entered = _strip(mins(120), _entry("takeoff", 5), _entry("landed", 100))
    assert entered.phase == "landed"
    assert entered.text == "Landed ZZDS 13:40Z (+10 min vs plan)"
    assert entered.marker_label == "Landed"

    # A fix on the apron at the destination: inferred, so "~".
    apron = fix(95, 4.0, gs=0.0)
    inferred = _strip(mins(100), fix(30, 1.0), apron)
    assert inferred.phase == "landed"
    assert inferred.text.startswith("Landed ZZDS ~")


# --- Destination at ETA ----------------------------------------------------------------


def test_destination_block_reads_metar_taf_wind_and_cells():
    dest = _airport("ZZDS", 154.0, taf={
        "taf_flight_category_at_eta": "MVFR", "taf_prevailing_category_at_eta": "VFR",
        "taf_temporary_category_at_eta": "MVFR", "taf_temporary_type": "PROB30",
        "taf_significant_weather": ["TS"],
        "taf_wind_dir": 240, "taf_wind_speed_kt": 18, "taf_wind_gust_kt": 28,
        "taf_best_runway_id": "27", "taf_crosswind_kt": -12.4, "taf_wind_advisory": "amber",
    })
    storms = storms_at(NOW, [
        cell("core41-near", "core41", along=148, cross=4, peak=52.0, flashes=3, speed=10.0, toward=0.0),
        cell("core35-far", "core35", along=60, cross=-25, peak=44.0),
    ])
    layer = _layer(observations=_obs(_airport("ZZDP", 0.0), dest), storms=storms)
    glance, _ = build_glance(layer, ROUTE, _progress(NOW))
    d = glance.destination
    assert d.icao == "ZZDS" and d.eta == mins(90)
    assert d.metar_text == "ZZDS VFR" and d.metar_category == "VFR"
    assert d.taf_text == "TAF at ETA VFR, PROB30 MVFR TS" and d.taf_category == "VFR"
    assert d.wind_text == "Wind at ETA 240° 18G28 kt · RWY 27 crosswind 12 kt"
    assert d.wind_advisory == "amber"
    # Only the cell within 20 NM of the field, with its map focus.
    assert [c.id for c in d.cells] == ["core41-near"]
    assert d.cells[0].text.startswith("7 NM ") and "52 dBZ, lightning" in d.cells[0].text
    assert d.cells[0].focus is not None and d.cells[0].focus.id == "core41-near"
    assert d.cells_text.startswith("nearest cell 7 NM")
    assert d.lightning_text == "no lightning ≤20 NM"
    assert d.unavailable == [] and d.focus.id == "ZZDS"


def test_destination_block_says_unavailable_never_clear():
    layer = _layer(observations=_obs(_airport("ZZDP", 0.0), _airport("ZZDS", 154.0, metar=False)))
    layer.storms = None
    layer.observed_conditions = None
    glance, _ = build_glance(layer, ROUTE, _progress(NOW))
    d = glance.destination
    assert d.metar_text == "ZZDS METAR unavailable"
    assert d.taf_text == "no TAF for ETA"
    assert d.cells_text == "radar cells unavailable" and d.cells == []
    assert d.unavailable == ["metar", "taf", "storms", "lightning"]
    assert d.wind_text is None


def test_metar_wind_when_no_taf_covers_the_eta():
    dest = _airport("ZZDS", 154.0).model_copy(update={
        "metar_wind_dir": 270, "metar_wind_speed_kt": 10, "metar_best_runway_id": "27",
        "metar_crosswind_kt": 0.0, "metar_wind_advisory": "green",
    })
    glance, _ = build_glance(_layer(observations=_obs(_airport("ZZDP", 0.0), dest)), ROUTE, _progress(NOW))
    assert glance.destination.wind_text == "Wind now 270° 10 kt · RWY 27 crosswind 0 kt"
    assert glance.destination.wind_advisory == "green"


# --- Agent block and redaction -------------------------------------------------------


def test_agent_block_quotes_the_strip_and_redaction_drops_it():
    layer = _layer()
    layer.glance, layer.ribbon = build_glance(layer, ROUTE, _progress(mins(50), fix(40, 0.5)))
    block = summarize_live(layer, {})
    assert block["glance"]["flight"] == {"phase": "enroute", "text": layer.glance.flight.text}
    assert redact_summary(block, "along_route")["glance"]["flight"] == block["glance"]["flight"]
    assert redact_summary(block, "none")["glance"]["flight"] is None

    resp = LiveLayerResponse(flight_id=layer.flight_id, pack_timestamp=layer.pack_timestamp,
                             glance=layer.glance, ribbon=layer.ribbon)
    assert redact_live(resp, "along_route").glance.flight == layer.glance.flight
    hidden = redact_live(resp, "none").glance
    assert hidden.flight is None and hidden.destination.eta is None
    assert hidden.destination.metar_text == layer.glance.destination.metar_text
