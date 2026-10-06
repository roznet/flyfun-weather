"""Replay a frozen flight morning through the live layer (tests helper).

A scenario fixture (tests/fixtures/live_scenarios/*.json, built by
scripts/build_live_scenario.py) holds, per airport, every observation the
corridor fetch produced, the SIGMETs matched to the route with their issue
time, and each briefing pack with the baseline it carried. :func:`replay`
walks the live window on the tick cadence and commits every tick through the
real :func:`commit_live_update` — seeding, classification, tiers, alert memory
and the pack switch are all the production path, only the fetch is replayed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from weatherbrief.models.live import LiveChange, LiveLayer
from weatherbrief.models.observations import (
    AirportObservation,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.tasks.live_layer import change_identity, commit_live_update

SCENARIOS = Path(__file__).parent / "fixtures" / "live_scenarios"


def load_scenario(name: str) -> dict:
    return json.loads((SCENARIOS / f"{name}.json").read_text())


@dataclass
class Tick:
    at: datetime
    pack: str
    layer: LiveLayer
    # Changes that appeared / disappeared since the previous tick.
    appeared: list[LiveChange] = field(default_factory=list)
    cleared: list[LiveChange] = field(default_factory=list)
    # The pack the tick committed to (its flight dir holds the history).
    pack_dir: Path | None = None
    briefing: dict | None = None


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _known_by(o: dict, at: datetime) -> bool:
    taf = o.get("taf_issue_time")
    return _dt(o["metar_time"]) <= at and (taf is None or _dt(taf) <= at)


def observations_at(scenario: dict, at: datetime) -> RouteObservations:
    """The corridor fetch at ``at``: each airport's latest METAR and TAF so far
    (the builder stores one observation per METAR or TAF, in time order)."""
    derived = scenario["derived"]
    airports = []
    for icao in derived["corridor"]:
        seen = [o for o in derived["observations"].get(icao, []) if _known_by(o, at)]
        if seen:
            airports.append(AirportObservation.model_validate(seen[-1]))
    return RouteObservations(
        corridor_nm=scenario["inputs"]["corridor_nm"], fetch_time=at,
        airports_found=len(derived["corridor"]), airports_with_metar=len(airports),
        airports_with_taf=sum(1 for a in airports if a.taf_raw), airports=airports,
    )


def sigmets_at(scenario: dict, at: datetime) -> RouteSigmets:
    """SIGMETs on the route at ``at``: issued, and not yet expired."""
    active = []
    for s in scenario["derived"]["sigmets"]:
        if s["sigmet"] is None or _dt(s["issued_at"]) > at:
            continue
        sig = SigmetAlongRoute.model_validate(s["sigmet"])
        if sig.valid_to is None or sig.valid_to > at:
            active.append(sig)
    return RouteSigmets(
        corridor_nm=scenario["inputs"]["sigmet_corridor_nm"], fetch_time=at, sigmets=active,
    )


def _briefing(scenario: dict, pack_index: int) -> dict:
    inputs = scenario["inputs"]
    briefing = {
        "route": inputs["route"],
        "departure_time": inputs["departure_time"],
        "days_out": 0,
        "alternates": {"alternates": [{"icao": i} for i in inputs["alternates"]]},
    }
    baseline = scenario["derived"]["baselines"][pack_index]
    if baseline is not None:
        briefing.update(baseline)
    return briefing


def replay(scenario: dict, flight_dir: Path) -> list[Tick]:
    """Commit every tick of the live window; return what each one showed."""
    inputs = scenario["inputs"]
    packs = []
    for i, p in enumerate(inputs["packs"]):
        pack_dir = flight_dir / p["timestamp"].replace(":", "-")
        pack_dir.mkdir(parents=True, exist_ok=True)
        briefing = _briefing(scenario, i)
        (pack_dir / "briefing.json").write_text(json.dumps(briefing))
        packs.append((_dt(p["active_from"]), p["timestamp"], pack_dir, briefing))

    window = inputs["window"]
    at, end = _dt(window["start"]), _dt(window["end"])
    step = timedelta(minutes=window["tick_minutes"])
    ticks: list[Tick] = []
    shown: dict[tuple, LiveChange] = {}
    while at <= end:
        _, ts, pack_dir, briefing = [p for p in packs if p[0] <= at][-1]
        layer = commit_live_update(
            pack_dir, briefing_data=briefing,
            observations=observations_at(scenario, at), sigmets=sigmets_at(scenario, at),
            observed=None, started_at=at, pack_timestamp=ts, now=at,
        )
        assert layer is not None, f"tick {at} refused"
        # A change is the same change while its key, direction, value and tier
        # hold — the message may still move (a SPECI suffix, a new detail).
        # The live history (#643) uses the same identity.
        current = {change_identity(c): c for c in layer.changes.changes}
        ticks.append(Tick(
            at=at, pack=ts, layer=layer,
            appeared=[c for k, c in current.items() if k not in shown],
            cleared=[c for k, c in shown.items() if k not in current],
            pack_dir=pack_dir, briefing=briefing,
        ))
        shown = current
        at += step
    return ticks


def timeline(ticks: list[Tick]) -> list[tuple[str, str, str, str]]:
    """("HH:MM", "+"/"-", tier, message) for every change appearing or clearing."""
    out = []
    for t in ticks:
        hhmm = t.at.strftime("%H:%M")
        out += [(hhmm, "+", c.tier, c.message) for c in t.appeared]
        out += [(hhmm, "-", c.tier, c.message) for c in t.cleared]
    return out


# --- iOS UI-test fixtures ---------------------------------------------------

#: The iOS mock flight a live tick is served for (FixtureBriefingRepository):
#: the app only applies a layer whose pack is the flight's latest.
IOS_FLIGHT_ID = "fixture-1"
IOS_PACK_TIMESTAMP = "2099-06-30T06:00:00+00:00"
#: Ticks exported per scenario: the moments a pilot would want to see.
IOS_TICKS = {
    "2026-10-02_lell_lemi": [
        # 06:00 and 10:20 were dropped from the UI test (2026-10-05): it
        # relaunches the app per tick, and neither added a case the others lack.
        "05:10",  # starting point taken at 05:00; LECB 2 + LEVC TS alert
        "07:10",  # after the 06:52 rebuild: LEVC TS under the route, before departure
        "08:30",  # LECB 3 / LECM 3 EMBD TS at the destination
        "09:00",  # LECB 2 reissued as LECB 4 (a replacement, NEW) beside LECB 3 / LECM 3
    ],
}
IOS_SCENARIOS = (
    Path(__file__).parent.parent / "app" / "flyfun-weather" / "flyfun-weatherUITests" / "LiveScenarios"
)


def live_response(tick: Tick) -> dict:
    """The tick as a ``GET /flights/{id}/live`` body, rebased onto the iOS
    mock flight (same fields as ``api/packs.py::get_live_layer``)."""
    from weatherbrief.models.live import LiveLayerResponse
    from weatherbrief.tasks.live_trail import trails_for_pack

    layer = tick.layer
    return LiveLayerResponse(
        flight_id=IOS_FLIGHT_ID,
        pack_timestamp=IOS_PACK_TIMESTAMP,
        live_updated_at=layer.live_updated_at,
        route_observations=layer.route_observations,
        observations_updated_at=layer.observations_updated_at,
        route_sigmets=layer.route_sigmets,
        sigmets_updated_at=layer.sigmets_updated_at,
        observed_conditions=layer.observed_conditions,
        observed_updated_at=layer.observed_updated_at,
        storms=layer.storms,
        glance=layer.glance,
        ribbon=layer.ribbon,
        # With the trails as of this tick (#669), as /live serves them.
        changes=trails_for_pack(tick.pack_dir, layer.changes, now=tick.at, briefing_data=tick.briefing),
        last_refresh_delta=layer.last_refresh_delta,
    ).model_dump(mode="json")


def ios_tick_files(name: str, ticks: list[Tick], at: list[str]) -> dict[Path, str]:
    """``LiveScenarios/<name>_<HHMM>.json`` contents for the chosen ticks
    (flat names: the test bundle copies resources without their folders)."""
    by_hhmm = {t.at.strftime("%H:%M"): t for t in ticks}
    return {
        IOS_SCENARIOS / f"{name}_{hhmm.replace(':', '')}.json":
            json.dumps(live_response(by_hhmm[hhmm]), indent=1, sort_keys=True) + "\n"
        for hhmm in at
    }
