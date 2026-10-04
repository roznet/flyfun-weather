"""Change trails (#669): each live change's recent history, from the flight's
``live_history.jsonl``. Display only: the counts, tiers and alerts are what
they were.

Synthetic histories pin each rule (spans, recurrence, the report strip, the
60-min cleared window and the three traps: key vs identity, pack switches,
relevance drop-outs). The LELL → LEMI replay pins them on a real morning.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from live_scenario_replay import load_scenario, replay
from test_live_scenarios import LELL_LEMI
from weatherbrief.models.analysis import RouteConfig
from weatherbrief.models.live import LiveChange, LiveChanges
from weatherbrief.tasks.live_layer import load_live_history
from weatherbrief.tasks.live_trail import (
    RECENTLY_CLEARED_MINUTES,
    TRAIL_MAX_REPORTS,
    change_trails,
    route_context,
)

DAY = datetime(2026, 10, 4, tzinfo=timezone.utc)
PACK1, PACK2 = "2026-10-04T09:00:00+00:00", "2026-10-04T12:30:00+00:00"


def at(hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return DAY.replace(hour=int(h), minute=int(m))


def _change(key, *, kind="metar_category", direction="worse", to_value="MVFR", tier="alert",
            role="destination", icao=None, observed_at=None, enroute=None, message=None, from_value="VFR"):
    icao = icao if icao is not None else (key.split(":", 1)[1] if kind.startswith(("metar", "taf")) else None)
    return LiveChange(
        key=key, kind=kind, source={"taf_category": "TAF"}.get(kind, "SIGMET" if kind.startswith("sigmet") else "METAR"),
        direction=direction, tier=tier, role=role, icao=icao,
        from_value=from_value, to_value=to_value,
        observed_at=at(observed_at) if observed_at else None,
        enroute_distance_nm=enroute,
        message=message or f"{key} {from_value} → {to_value}",
    )


class History:
    """Builds history records the way ``_history_records`` writes them."""

    def __init__(self):
        self.records: list[dict] = []
        self.pack_ts = PACK1

    def _base(self, tick):
        return {"tick_at": at(tick).isoformat(), "pack_timestamp": self.pack_ts}

    def pack(self, tick, ts, *, has_observations=True):
        self.pack_ts = ts
        self.records.append({**self._base(tick), "type": "pack", "pack_dir_name": ts,
                             "has_observations": has_observations})
        return self

    def metar(self, tick, icao, observed, category, *, report_type="METAR", raw=None, with_category=True):
        rec = {**self._base(tick), "type": "report", "kind": "metar", "icao": icao,
               "report_type": report_type, "observed_at": at(observed).isoformat(),
               "raw": raw or f"{report_type} {icao} {category}"}
        if with_category:
            rec["flight_category"] = category
        self.records.append(rec)
        return self

    def taf(self, tick, icao, issued):
        self.records.append({**self._base(tick), "type": "report", "kind": "taf", "icao": icao,
                             "issued_at": at(issued).isoformat(), "raw": f"TAF {icao}"})
        return self

    def appeared(self, tick, change):
        self.records.append({**self._base(tick), "type": "event", "event": "appeared",
                             "change": change.model_dump(mode="json", exclude_none=True)})
        return self

    def cleared(self, tick, change):
        self.records.append({**self._base(tick), "type": "event", "event": "cleared",
                             "change": change.model_dump(mode="json", exclude_none=True)})
        return self


def _changes(*current: LiveChange, baseline_source="briefing") -> LiveChanges:
    return LiveChanges(computed_at=DAY, baseline_source=baseline_source, changes=list(current))


def _spans(change):
    return [(s.start.strftime("%H:%M"), s.end.strftime("%H:%M") if s.end else None) for s in change.trail.spans]


def _cleared(out):
    return [(c.key, c.cleared_at.strftime("%H:%M")) for c in out.recently_cleared]


# --- Spans and recurrence ---------------------------------------------------


def test_on_off_spans_and_times_today():
    conv = _change("conv:ZZMT", kind="metar_convective", to_value="TS", from_value="none",
                   role="route", tier="alert", observed_at="12:40", enroute=200.0)
    h = (History().pack("12:00", PACK1)
         .metar("12:40", "ZZMT", "12:40", "VFR").appeared("12:40", conv)
         .metar("13:00", "ZZMT", "13:00", "VFR").cleared("13:00", conv)
         .metar("13:30", "ZZMT", "13:30", "VFR").appeared("13:30", conv))
    out = change_trails(h.records, _changes(conv), now=at("13:40"))
    [c] = out.changes
    assert _spans(c) == [("12:40", "13:00"), ("13:30", None)]
    assert c.trail.times_today == 2
    assert c.trail.reports is None  # only category rows get a strip
    assert out.recently_cleared == []  # on screen again: no cleared row


def test_first_time_is_one():
    c = _change("metar:ZZDS", observed_at="11:00")
    h = History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
    [row] = change_trails(h.records, _changes(c), now=at("11:10")).changes
    assert row.trail.times_today == 1 and _spans(row) == [("11:00", None)]


def test_change_not_yet_in_history_still_counts_once():
    """The history lags (a failed write): the row is on screen, so it counts."""
    c = _change("metar:ZZDS", observed_at="11:00")
    [row] = change_trails([], _changes(c), now=at("11:10")).changes
    assert row.trail.times_today == 1 and row.trail.spans == []


def test_records_after_now_are_ignored():
    c = _change("metar:ZZDS", observed_at="11:00")
    h = (History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
         .metar("11:30", "ZZDS", "11:30", "VFR").cleared("11:30", c))
    [row] = change_trails(h.records, _changes(c), now=at("11:10")).changes
    assert _spans(row) == [("11:00", None)]


# --- Trap 1: key, not identity ---------------------------------------------


def test_value_change_on_the_same_key_continues_the_span():
    """MVFR → IFR is a new identity on the same key: one span, once today."""
    mvfr = _change("metar:ZZDP", role="departure", observed_at="11:00")
    ifr = _change("metar:ZZDP", role="departure", to_value="IFR", observed_at="11:30")
    h = (History().pack("10:00", PACK1)
         .metar("11:00", "ZZDP", "11:00", "MVFR").appeared("11:00", mvfr)
         .metar("11:30", "ZZDP", "11:30", "IFR").appeared("11:30", ifr).cleared("11:30", mvfr))
    out = change_trails(h.records, _changes(ifr), now=at("11:40"))
    [row] = out.changes
    assert _spans(row) == [("11:00", None)] and row.trail.times_today == 1
    assert out.recently_cleared == []


def test_direction_flip_is_a_different_row():
    """A SIGMET "new" then (after a rebuild absorbed it) "no longer active" is
    not the same change coming back: not a "2nd time today"."""
    new = _change("sigmet:ZZZZ|2", kind="sigmet_issued", to_value="EMBD TS", from_value=None, role="route")
    gone = _change("sigmet:ZZZZ|2", kind="sigmet_cancelled", direction="better", tier="highlight",
                   to_value=None, from_value="EMBD TS", role="route")
    h = (History().pack("10:00", PACK1).appeared("10:10", new)
         .pack("11:00", PACK2).cleared("11:00", new)
         .appeared("13:00", gone))
    [row] = change_trails(h.records, _changes(gone), now=at("13:10")).changes
    assert row.trail.times_today == 1 and _spans(row) == [("13:00", None)]


def test_a_better_row_replaced_by_a_worse_one_does_not_resurface():
    """LECH IFR → VFR, then IFR → LIFR on the same key; the LIFR row ends by
    a drop-out. The earlier VFR row must not pop up as cleared."""
    route = _route(dist_nm=200.0, hours=2.0)
    vfr = _change("metar:ZZEN", direction="better", tier="highlight", role="route", from_value="IFR",
                  to_value="VFR", observed_at="10:00", enroute=50.0)
    lifr = _change("metar:ZZEN", tier="highlight", role="route", from_value="IFR", to_value="LIFR",
                   observed_at="10:30", enroute=50.0)
    h = (History().pack("09:00", PACK1)
         .metar("10:00", "ZZEN", "10:00", "VFR").appeared("10:00", vfr)
         .metar("10:30", "ZZEN", "10:30", "LIFR").appeared("10:30", lifr).cleared("10:30", vfr)
         .cleared("11:00", lifr))  # passed at ~10:30 (50 NM of 200, dep 10:00)
    out = change_trails(h.records, _changes(), now=at("11:10"), route=route, departure=at("10:00"))
    assert out.recently_cleared == []


# --- Trap 2: a pack switch is not weather -----------------------------------


def test_pack_switch_reshowing_the_change_is_one_span():
    c1 = _change("metar:ZZDS", from_value="VFR", to_value="MVFR", observed_at="11:00")
    c2 = _change("metar:ZZDS", from_value="VFR", to_value="MVFR", tier="alert", observed_at="11:00",
                 message="ZZDS METAR: VFR → MVFR (new briefing)")
    h = (History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c1)
         .pack("12:30", PACK2).appeared("12:30", c2).cleared("12:30", c1))
    out = change_trails(h.records, _changes(c2), now=at("12:40"))
    [row] = out.changes
    assert _spans(row) == [("11:00", None)] and row.trail.times_today == 1
    assert out.recently_cleared == []


def test_change_absorbed_by_a_new_briefing_is_not_a_cleared_row():
    c = _change("metar:ZZDS", observed_at="11:00")
    h = (History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
         .pack("12:30", PACK2).metar("12:30", "ZZDS", "12:30", "MVFR").cleared("12:30", c))
    assert change_trails(h.records, _changes(), now=at("12:40")).recently_cleared == []


def test_absorbed_then_back_is_a_second_time():
    """Absorbed at the switch, then on screen again later against the new
    briefing: it did come back."""
    c = _change("conv:ZZDS", kind="metar_convective", to_value="TS", from_value="none", observed_at="11:00")
    h = (History().pack("10:00", PACK1).appeared("11:00", c)
         .pack("12:30", PACK2).cleared("12:30", c)
         .appeared("13:30", c))
    [row] = change_trails(h.records, _changes(c), now=at("13:40")).changes
    assert row.trail.times_today == 2


def test_clear_before_a_pack_switch_stays_a_cleared_row():
    c = _change("metar:ZZDS", observed_at="11:00")
    h = (History().pack("10:00", PACK1)
         .metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
         .metar("11:30", "ZZDS", "11:30", "VFR").cleared("11:30", c)
         .pack("12:00", PACK2))
    out = change_trails(h.records, _changes(), now=at("12:10"))
    assert _cleared(out) == [("metar:ZZDS", "11:30")]


# --- Trap 3: relevance drop-outs are not weather -----------------------------


def _route(dist_nm: float, hours: float) -> RouteConfig:
    # Two waypoints ~dist_nm apart along a meridian (1° lat = 60 NM).
    return RouteConfig.model_validate({
        "name": "ZZ", "cruise_altitude_ft": 6000, "flight_duration_hours": hours,
        "waypoints": [
            {"icao": "ZZDP", "name": "Dep", "lat": 45.0, "lon": 1.0},
            {"icao": "ZZDS", "name": "Dest", "lat": 45.0 + dist_nm / 60.0, "lon": 1.0},
        ],
    })


def test_departure_dropping_out_at_take_off_is_not_cleared():
    c = _change("metar:ZZDP", role="departure", observed_at="09:30")
    h = (History().pack("09:00", PACK1).metar("09:30", "ZZDP", "09:30", "MVFR").appeared("09:30", c)
         .metar("10:00", "ZZDP", "10:00", "MVFR").cleared("10:00", c))
    out = change_trails(h.records, _changes(), now=at("10:10"),
                        route=_route(100.0, 1.0), departure=at("10:00"))
    assert out.recently_cleared == []


def test_departure_clearing_on_the_weather_before_take_off_is_kept():
    c = _change("metar:ZZDP", role="departure", observed_at="09:30")
    h = (History().pack("09:00", PACK1).metar("09:30", "ZZDP", "09:30", "MVFR").appeared("09:30", c)
         .metar("09:50", "ZZDP", "09:50", "VFR").cleared("09:50", c))
    out = change_trails(h.records, _changes(), now=at("10:10"),
                        route=_route(100.0, 1.0), departure=at("10:00"))
    assert _cleared(out) == [("metar:ZZDP", "09:50")]


def test_en_route_airport_passed_is_not_cleared():
    c = _change("conv:ZZEN", kind="metar_convective", role="route", to_value="TS", from_value="none",
                observed_at="10:00", enroute=50.0)
    h = (History().pack("09:00", PACK1).metar("10:00", "ZZEN", "10:00", "VFR").appeared("10:00", c)
         .metar("10:40", "ZZEN", "10:40", "VFR").cleared("10:40", c))
    # 100 NM in 1 h from 10:00: 50 NM passed at 10:30.
    out = change_trails(h.records, _changes(), now=at("10:50"),
                        route=_route(100.0, 1.0), departure=at("10:00"))
    assert out.recently_cleared == []


def test_clear_without_a_newer_report_is_a_gap_and_a_return_continues():
    """The corridor fetch missed the airport for a tick: no cleared row, and
    when it comes back it is the same span, not a "2nd time"."""
    c = _change("metar:ZZDS", observed_at="11:00")
    h = (History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
         .cleared("11:10", c))
    assert change_trails(h.records, _changes(), now=at("11:15")).recently_cleared == []
    h.appeared("11:20", c)
    [row] = change_trails(h.records, _changes(c), now=at("11:25")).changes
    assert _spans(row) == [("11:00", None)] and row.trail.times_today == 1


def test_taf_change_clears_on_a_newer_taf():
    c = _change("taf:ZZDS", kind="taf_category", observed_at="09:00")
    h = (History().pack("08:00", PACK1).taf("09:00", "ZZDS", "09:00").appeared("09:10", c)
         .taf("11:10", "ZZDS", "11:00").cleared("11:10", c))
    assert _cleared(change_trails(h.records, _changes(), now=at("11:20"))) == [("taf:ZZDS", "11:10")]


# --- Trap 4: expiry is weather news ------------------------------------------


def test_sigmet_expiry_is_a_cleared_row():
    s = _change("sigmet:ZZZZ|T01", kind="sigmet_issued", to_value="EMBD TS", from_value=None, role="route",
                message="New SIGMET ZZZZ T01: EMBD TS")
    h = History().pack("10:00", PACK1).appeared("10:10", s).cleared("12:30", s)
    out = change_trails(h.records, _changes(), now=at("12:40"))
    [row] = out.recently_cleared
    assert row.message == "New SIGMET ZZZZ T01: EMBD TS" and row.cleared_at == at("12:30")


# --- The 60-min window and what a cleared row is ------------------------------


@pytest.mark.parametrize("minutes, shown", [(59, True), (60, True), (61, False)])
def test_cleared_row_stays_an_hour(minutes, shown):
    c = _change("metar:ZZDS", observed_at="11:00")
    h = (History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
         .metar("11:30", "ZZDS", "11:30", "VFR").cleared("11:30", c))
    out = change_trails(h.records, _changes(), now=at("11:30") + timedelta(minutes=minutes))
    assert bool(out.recently_cleared) is shown
    assert RECENTLY_CLEARED_MINUTES == 60


def test_cleared_row_is_never_an_alert_nor_counted():
    c = _change("metar:ZZDS", observed_at="11:00").model_copy(update={"new_alert": True})
    still = _change("wind:ZZDS", kind="metar_wind", from_value="green", to_value="amber", observed_at="11:00")
    h = (History().pack("10:00", PACK1).metar("11:00", "ZZDS", "11:00", "MVFR")
         .appeared("11:00", c).appeared("11:00", still)
         .metar("11:30", "ZZDS", "11:30", "VFR").cleared("11:30", c))
    before = _changes(still)
    out = change_trails(h.records, before, now=at("11:40"))
    [row] = out.recently_cleared
    assert row.new_alert is False and row.tier == "alert"  # tier kept as it was shown
    assert (out.worsened_count, out.improved_count, out.alert_count) == (1, 0, 1)
    assert (before.changes[0].trail, before.recently_cleared) == (None, None)  # input untouched


def test_cleared_rows_newest_first():
    a = _change("metar:ZZAA", observed_at="11:00", role="alternate", tier="highlight")
    b = _change("metar:ZZBB", observed_at="11:00", role="alternate", tier="highlight")
    h = (History().pack("10:00", PACK1)
         .metar("11:00", "ZZAA", "11:00", "MVFR").metar("11:00", "ZZBB", "11:00", "MVFR")
         .appeared("11:00", a).appeared("11:00", b)
         .metar("11:20", "ZZAA", "11:20", "VFR").cleared("11:20", a)
         .metar("11:40", "ZZBB", "11:40", "VFR").cleared("11:40", b))
    assert _cleared(change_trails(h.records, _changes(), now=at("11:50"))) == [
        ("metar:ZZBB", "11:40"), ("metar:ZZAA", "11:20"),
    ]


# --- The metar_category report strip ------------------------------------------


def test_category_strip_from_the_first_showing_report():
    c = _change("metar:ZZDP", role="departure", observed_at="11:00")
    h = (History().pack("10:00", PACK1)
         .metar("10:00", "ZZDP", "10:30", "VFR")
         .metar("11:00", "ZZDP", "11:00", "MVFR").appeared("11:00", c)
         .metar("11:20", "ZZDP", "11:12", "MVFR", report_type="SPECI")
         .metar("11:30", "ZZDP", "11:30", "VFR").cleared("11:30", c))
    [row] = change_trails(h.records, _changes(), now=at("11:40")).recently_cleared
    assert [(r.at.strftime("%H:%M"), r.category, r.report_type) for r in row.trail.reports] == [
        ("11:00", "MVFR", "METAR"), ("11:12", "MVFR", "SPECI"), ("11:30", "VFR", "METAR"),
    ]
    assert row.from_value == "VFR" and row.trail.baseline_source == "briefing"


def test_category_strip_keeps_the_latest_reports():
    c = _change("metar:ZZDS", observed_at="10:00")
    h = History().pack("09:00", PACK1).appeared("10:00", c)
    for i in range(10):
        hhmm = f"{10 + i // 2}:{30 * (i % 2):02d}"
        h.metar(hhmm, "ZZDS", hhmm, "MVFR")
    [row] = change_trails(h.records, _changes(c), now=at("15:00")).changes
    assert len(row.trail.reports) == TRAIL_MAX_REPORTS == 6
    assert row.trail.reports[-1].at == at("14:30")


def test_category_strip_reparses_reports_recorded_before_the_field():
    """Files written 2026-10-03/04 have no flight_category: parsed from raw."""
    c = _change("metar:ZZDS", observed_at="11:00", to_value="IFR")
    h = (History().pack("10:00", PACK1)
         .metar("11:00", "ZZDS", "11:00", None, with_category=False,
                raw="METAR ZZDS 041100Z 24010KT 9999 OVC007 12/10 Q1015")
         .appeared("11:00", c))
    [row] = change_trails(h.records, _changes(c), now=at("11:10")).changes
    assert [r.category for r in row.trail.reports] == ["IFR"]


def test_category_strip_baseline_follows_the_pack_it_was_shown_under():
    c = _change("metar:ZZDS", observed_at="11:00")
    h = (History().pack("10:00", PACK1, has_observations=False)
         .metar("11:00", "ZZDS", "11:00", "MVFR").appeared("11:00", c)
         .metar("11:30", "ZZDS", "11:30", "VFR").cleared("11:30", c)
         .pack("12:00", PACK2))
    [row] = change_trails(h.records, _changes(), now=at("12:10")).recently_cleared
    assert row.trail.baseline_source == "live_start"


# --- A real day: LFBZ → LFMD, 2026-10-04 (from the issue's prod history) ------


def test_lfbz_lfmd_day():
    """LFMT TS/CB on 12:42–13:02 and again from 13:33; the departure's
    one-report MVFR blip 11:05–11:41; SIGMET T01 → T02 → T03 in turn."""
    lfmt = _change("conv:LFMT", kind="metar_convective", role="route", to_value="TS", from_value="none",
                   observed_at="12:30", enroute=150.0, message="LFMT METAR: TS, CB reported")
    lfbz = _change("metar:LFBZ", role="departure", observed_at="11:00", message="LFBZ METAR: VFR → MVFR")

    def sig(n):
        return _change(f"sigmet:LFMM|T0{n}", kind="sigmet_issued", to_value="EMBD TS", from_value=None,
                       role="route", message=f"New SIGMET LFMM T0{n}: EMBD TS")

    h = (History().pack("10:30", PACK1)
         .metar("10:30", "LFBZ", "10:30", "VFR")
         .metar("11:05", "LFBZ", "11:00", "MVFR").appeared("11:05", lfbz).appeared("11:05", sig(1))
         .metar("11:41", "LFBZ", "11:30", "VFR").cleared("11:41", lfbz)
         .appeared("12:32", sig(2)).cleared("12:32", sig(1))
         .metar("12:42", "LFMT", "12:30", "VFR").appeared("12:42", lfmt)
         .metar("13:02", "LFMT", "13:00", "VFR").cleared("13:02", lfmt)
         .metar("13:33", "LFMT", "13:30", "VFR").appeared("13:33", lfmt.model_copy(update={"observed_at": at("13:30")})))
    route, dep = _route(300.0, 2.0), at("14:30")  # departure later: LFBZ still relevant

    at_1340 = change_trails(h.records, _changes(lfmt, sig(2)), now=at("13:40"), route=route, departure=dep)
    row = next(c for c in at_1340.changes if c.key == "conv:LFMT")
    assert _spans(row) == [("12:42", "13:02"), ("13:33", None)] and row.trail.times_today == 2
    # T01 (ended 12:32) and the 11:41 blip are past the hour; LFMT is back on.
    assert _cleared(at_1340) == []

    at_1310 = change_trails(h.records, _changes(sig(2)), now=at("13:10"), route=route, departure=dep)
    assert _cleared(at_1310) == [("conv:LFMT", "13:02"), ("sigmet:LFMM|T01", "12:32")]

    at_1200 = change_trails(h.records, _changes(sig(1)), now=at("12:00"), route=route, departure=dep)
    [blip] = at_1200.recently_cleared
    assert blip.key == "metar:LFBZ" and blip.cleared_at == at("11:41")
    assert [(r.at.strftime("%H:%M"), r.category) for r in blip.trail.reports] == [("11:00", "MVFR"), ("11:30", "VFR")]

    h.appeared("14:03", sig(3)).cleared("14:03", sig(2)).metar("14:03", "LFMT", "14:00", "VFR").cleared("14:03", lfmt)
    at_1410 = change_trails(h.records, _changes(sig(3)), now=at("14:10"), route=route, departure=dep)
    assert _cleared(at_1410) == [("conv:LFMT", "14:03"), ("sigmet:LFMM|T02", "14:03")]
    lfmt_row = at_1410.recently_cleared[0]
    assert lfmt_row.trail.times_today == 2 and _spans(lfmt_row)[-1] == ("13:33", "14:03")


# --- LELL → LEMI, replayed --------------------------------------------------


@pytest.fixture(scope="module")
def lell_lemi(tmp_path_factory):
    scenario = load_scenario(LELL_LEMI)
    flight_dir = tmp_path_factory.mktemp("live") / "u" / "flight"
    ticks = replay(scenario, flight_dir)
    route, dep = route_context({
        "route": scenario["inputs"]["route"], "departure_time": scenario["inputs"]["departure_time"],
    })
    history = load_live_history(flight_dir)

    def read(hhmm):
        t = next(t for t in ticks if t.at.strftime("%H:%M") == hhmm)
        return change_trails(history, t.layer.changes, now=t.at, route=route, departure=dep)

    return read


def test_lell_lemi_destination_blip_is_a_cleared_row_for_an_hour(lell_lemi):
    """06:00 LEMI MVFR, back to VFR on the 06:30 METAR: a cleared row from
    that tick, through the 06:52 rebuild (it cleared before the switch), for
    an hour."""
    def blip(hhmm):
        return [c for c in lell_lemi(hhmm).recently_cleared if c.key == "metar:LEMI"]

    assert blip("06:20") == []  # still on screen as a current row
    for hhmm in ("06:30", "07:00", "07:20", "07:30"):
        [row] = blip(hhmm)
        assert row.cleared_at.strftime("%H:%M") == "06:30" and row.message == "LEMI METAR: VFR → MVFR"
        assert row.tier == "alert" and row.new_alert is False
    assert blip("07:40") == []
    [row] = blip("07:20")
    assert [(r.at.strftime("%H:%M"), r.category) for r in row.trail.reports][:2] == [("06:00", "MVFR"), ("06:30", "VFR")]
    assert row.trail.baseline_source == "live_start"  # shown under the 30 Sep pack


def test_lell_lemi_rebuild_absorbs_without_cleared_rows(lell_lemi):
    """At the 07:00 tick (06:52 rebuild) LECB 2 and LERI VFR → MVFR left the
    screen because the baseline moved: no cleared row for either."""
    keys = {c.key for c in lell_lemi("07:00").recently_cleared}
    assert "sigmet:LECB|2" not in keys and "metar:LERI" not in keys


def test_lell_lemi_levc_thunderstorm_is_the_second_time(lell_lemi):
    [row] = [c for c in lell_lemi("07:10").changes if c.key == "conv:LEVC"]
    assert row.trail.times_today == 2
    assert _spans(row) == [("05:10", "06:00"), ("07:00", None)]


def test_lell_lemi_levc_passed_is_not_cleared(lell_lemi):
    """~09:15 VLC is passed: LEVC leaves the screen by the aircraft's
    position, not the weather."""
    assert "conv:LEVC" in {c.key for c in lell_lemi("09:10").changes}
    assert "conv:LEVC" not in {c.key for c in lell_lemi("09:20").recently_cleared}


def test_lell_lemi_sigmet_end_after_arrival_is_a_cleared_row(lell_lemi):
    assert ("sigmet:LECB|3+sigmet:LECM|3", "10:40") in _cleared(lell_lemi("11:00"))


def test_lell_lemi_counts_unchanged(lell_lemi):
    for hhmm in ("06:30", "07:10", "09:00"):
        out = lell_lemi(hhmm)
        assert out.alert_count == sum(1 for c in out.changes if c.tier == "alert")


# --- Storage: never in live.json, never in the overlay ------------------------


def test_trail_fields_stay_out_of_live_json_and_the_overlay(tmp_path):
    from weatherbrief.tasks.live_layer import live_for_pack, overlay_live

    flight_dir = tmp_path / "u" / "flight"
    ticks = replay(load_scenario(LELL_LEMI), flight_dir)
    text = (flight_dir / "live.json").read_text()
    assert "trail" not in text and "recently_cleared" not in text and "cleared_at" not in text
    layer = live_for_pack(ticks[-1].pack_dir)
    overlaid = json.dumps(overlay_live({}, layer))
    assert "trail" not in overlaid and "recently_cleared" not in overlaid and "cleared_at" not in overlaid


# --- Route cache: only good reads are kept (review on #670) ------------------


def test_failed_briefing_read_is_not_cached(tmp_path, monkeypatch):
    from weatherbrief.tasks import artifacts, live_trail

    pack_dir = tmp_path / "u" / "flight" / "pack"
    pack_dir.mkdir(parents=True)
    calls = []
    good = {"route": _route(100.0, 1.0).model_dump(mode="json"), "departure_time": at("10:00").isoformat()}

    def load(path):
        calls.append(path)
        return None if len(calls) == 1 else good  # first read races the pack write

    monkeypatch.setattr(artifacts, "load_briefing", load)
    monkeypatch.setattr(live_trail, "_ROUTE_CACHE", {})
    assert live_trail._route_context(str(pack_dir)) == (None, None)
    route, dep = live_trail._route_context(str(pack_dir))
    assert route is not None and dep == at("10:00")
    # Now cached: no further read.
    live_trail._route_context(str(pack_dir))
    assert len(calls) == 2
