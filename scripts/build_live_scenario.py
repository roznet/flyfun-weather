"""Build the `derived` block of a live-layer scenario fixture from its `inputs`.

A scenario (tests/fixtures/live_scenarios/*.json) is a real flight morning,
frozen: the public METARs/SPECIs and international SIGMETs of the window, the
route, and when each briefing pack became the flight's latest. The live tick's
corridor discovery and wind advisory need the airport database, which tests do
not have — so this script runs them here, once, and stores their output:

- ``observations``: per airport, every distinct ``AirportObservation`` the
  corridor fetch produced over the window (one per METAR/SPECI), exactly as
  ``run_route_weather`` builds it (category, wind advisory, crosswind…).
- ``baselines``: the pack's own ``route_observations`` / ``route_sigmets`` for
  packs built on flight day (fetched at the pack's timestamp), else null.
- ``sigmets``: each SIGMET as matched to the route (``SigmetAlongRoute``) with
  the time it was issued — null when it does not touch the corridor.

tests/test_live_scenarios.py replays the window tick by tick through the real
``commit_live_update`` from this block, and (when AIRPORTS_DB is set) checks
that the stored block still matches what this script would build today.

``--from-history`` builds the ``inputs`` themselves from a flight's
``live_history.jsonl`` (#643) instead of re-fetched reports: the raw
METAR/SPECI/TAF texts it recorded, its SIGMETs, the pack switches, and the
route from the pack. A reviewed flight becomes a regression scenario without
re-fetching anything.

Usage:
    python scripts/build_live_scenario.py tests/fixtures/live_scenarios/<name>.json
    python scripts/build_live_scenario.py --from-history DATA_DIR/packs/<user>/<flight> \\
        tests/fixtures/live_scenarios/<name>.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class _MetarSource:
    """The METARs/SPECIs observed by ``at`` (the 3 h window the live fetch asks
    for), plus every TAF issued by then (the fetch keeps the latest)."""

    def __init__(self, reports, at: datetime) -> None:
        self._reports, self._at = reports, at

    def fetch_weather(self, icaos, metar_hours: float = 3):
        from euro_aip.briefing.weather.models import WeatherType

        lo = self._at - timedelta(hours=metar_hours)
        want = {i.upper() for i in icaos}

        def keep(r) -> bool:
            if r.icao.upper() not in want or not r.observation_time or r.observation_time > self._at:
                return False
            return r.report_type == WeatherType.TAF or r.observation_time >= lo

        return [r for r in self._reports if keep(r)]


class _SigmetSource:
    def __init__(self, reports) -> None:
        self._reports = reports

    def fetch_isigmet(self, region="eur", hazard=None, level=None, date=None):
        return list(self._reports)


def build_derived(inputs: dict, airports_db: str) -> dict:
    from euro_aip.briefing.weather.models import WeatherReport
    from euro_aip.briefing.weather.sigmet import SigmetReport

    from weatherbrief.models.analysis import RouteConfig
    from weatherbrief.tasks.route_weather import run_route_sigmets, run_route_weather

    route = RouteConfig.model_validate(inputs["route"])
    departure = _dt(inputs["departure_time"])
    metars = [WeatherReport.from_dict(d) for d in inputs["metars"]]
    sigmets = [(_dt(s["issued_at"]), SigmetReport.from_dict(s["report"])) for s in inputs["sigmets"]]
    corridor, sig_corridor = inputs["corridor_nm"], inputs["sigmet_corridor_nm"]

    def observations_at(t: datetime):
        return run_route_weather(
            route=route, target_time=departure, corridor_nm=corridor,
            airports_db_path=airports_db, source=_MetarSource(metars, t),
        )

    def sigmets_at(t: datetime, reports):
        return run_route_sigmets(
            route=route, target_time=departure, corridor_nm=sig_corridor,
            airports_db_path=airports_db, source=_SigmetSource(reports), now=t,
        )

    # One observation per (airport, METAR, TAF): evaluate at every report's
    # own time. tests/live_scenario_replay.py serves the latest one whose
    # METAR and TAF are both known at the tick.
    by_airport: dict[str, dict[tuple, dict]] = {}
    for t in sorted({r.observation_time for r in metars if r.observation_time}):
        for a in observations_at(t).airports:
            if a.metar_time is None:
                continue
            key = (a.metar_time.isoformat(), a.taf_issue_time.isoformat() if a.taf_issue_time else None)
            by_airport.setdefault(a.icao, {})[key] = a.model_dump(mode="json")

    baselines = []
    for pack in inputs["packs"]:
        if not pack["has_observations"]:
            baselines.append(None)
            continue
        at = _dt(pack["timestamp"])
        active = [r for issued, r in sigmets if issued <= at and (r.valid_to is None or r.valid_to > at)]
        # Stamped with the pack's own time (the fetch stamps the wall clock),
        # so a rebuild is byte-for-byte reproducible.
        baselines.append({
            "route_observations": observations_at(at).model_copy(update={"fetch_time": at}).model_dump(mode="json"),
            "route_sigmets": sigmets_at(at, active).model_copy(update={"fetch_time": at}).model_dump(mode="json"),
        })

    matched = []
    for issued, report in sigmets:
        hit = sigmets_at(issued, [report]).sigmets
        matched.append({
            "issued_at": issued.isoformat(),
            "sigmet": hit[0].model_dump(mode="json") if hit else None,
        })

    window_obs = observations_at(_dt(inputs["window"]["start"]))
    return {
        "corridor": [a.icao for a in window_obs.airports],
        "observations": {icao: list(v.values()) for icao, v in sorted(by_airport.items())},
        "baselines": baselines,
        "sigmets": matched,
    }


def inputs_from_history(flight_dir: Path, *, tick_minutes: int = 10) -> dict:
    """A scenario's ``inputs`` from a flight's live history (#643).

    METAR/TAF raw texts are parsed back with euro_aip's own parser (anchored
    on the report's time so the day-of-month resolves to the right month);
    SIGMETs come from their recorded structured fields, issued when first
    seen. Each pack becomes active at the tick that first recorded it. The
    window runs from the first recorded tick to the last live update.
    """
    from euro_aip.briefing.weather.parser import WeatherParser

    from weatherbrief.tasks.artifacts import load_briefing
    from weatherbrief.tasks.live_layer import load_live_history, load_live_meta

    history = load_live_history(flight_dir)
    packs = [r for r in history if r.get("type") == "pack"]
    if not packs:
        raise ValueError(f"{flight_dir}: no live history")
    briefing = load_briefing(flight_dir / packs[-1]["pack_dir_name"])
    if briefing is None:
        raise ValueError(f"{flight_dir}: pack {packs[-1]['pack_dir_name']} has no briefing.json")

    def seen_at(r: dict) -> str:
        return r.get("seen_at") or r["tick_at"]

    metars, sigmets = [], []
    for r in history:
        if r.get("type") != "report":
            continue
        if r["kind"] == "metar":
            rep = WeatherParser.parse_metar(r["raw"], source="live_history", reference=_dt(r["observed_at"]))
        elif r["kind"] == "taf":
            rep = WeatherParser.parse_taf(
                r["raw"], source="live_history", reference=_dt(r.get("issued_at") or seen_at(r)),
            )
        else:
            sigmets.append({"issued_at": seen_at(r), "report": {**r["sigmet"], "raw_text": r["raw"]}})
            continue
        if rep is None:
            print(f"unparseable {r['kind']} skipped: {r['raw']!r}", file=sys.stderr)
            continue
        metars.append(rep.to_dict())
    metars.sort(key=lambda m: (m["icao"], m["observation_time"] or ""))

    ticks = [_dt(r["tick_at"]) for r in history if r.get("tick_at")]
    meta = load_live_meta(flight_dir) or {}
    if meta.get("live_updated_at"):
        ticks.append(_dt(meta["live_updated_at"]))
    alternates = (briefing.get("alternates") or {}).get("alternates") or []
    return {
        "route": briefing["route"],
        "departure_time": briefing["departure_time"],
        "alternates": [a["icao"] for a in alternates if isinstance(a, dict) and a.get("icao")],
        "corridor_nm": packs[0].get("corridor_nm") or 30.0,
        "sigmet_corridor_nm": packs[0].get("sigmet_corridor_nm") or 50.0,
        "window": {
            "start": min(ticks).isoformat(), "end": max(ticks).isoformat(),
            "tick_minutes": tick_minutes,
        },
        "packs": [
            {"timestamp": p["pack_timestamp"], "active_from": p["tick_at"],
             "has_observations": bool(p.get("has_observations"))}
            for p in packs
        ],
        "metars": metars,
        "sigmets": sigmets,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture", type=Path)
    ap.add_argument("--from-history", type=Path, metavar="FLIGHT_DIR",
                    help="build the inputs from this flight's live_history.jsonl (overwrites FIXTURE)")
    args = ap.parse_args()
    airports_db = os.environ.get("AIRPORTS_DB")
    if not airports_db:
        print("AIRPORTS_DB is not set", file=sys.stderr)
        return 1
    if args.from_history:
        data = {"inputs": inputs_from_history(args.from_history)}
    else:
        data = json.loads(args.fixture.read_text())
    data["derived"] = build_derived(data["inputs"], airports_db)
    args.fixture.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{args.fixture}: {len(data['derived']['observations'])} airports, "
          f"{sum(len(v) for v in data['derived']['observations'].values())} observations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
