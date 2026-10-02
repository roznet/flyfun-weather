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

Usage:
    python scripts/build_live_scenario.py tests/fixtures/live_scenarios/<name>.json
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
    """The METARs/SPECIs observed by ``at`` (the 3 h window the live fetch asks for)."""

    def __init__(self, reports, at: datetime) -> None:
        self._reports, self._at = reports, at

    def fetch_weather(self, icaos, metar_hours: float = 3):
        lo = self._at - timedelta(hours=metar_hours)
        want = {i.upper() for i in icaos}
        return [
            r for r in self._reports
            if r.icao.upper() in want and r.observation_time and lo <= r.observation_time <= self._at
        ]


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

    # One observation per (airport, report): evaluate at every report's own time.
    by_airport: dict[str, dict[str, dict]] = {}
    for t in sorted({r.observation_time for r in metars if r.observation_time}):
        for a in observations_at(t).airports:
            if a.metar_time is None:
                continue
            by_airport.setdefault(a.icao, {})[a.metar_time.isoformat()] = a.model_dump(mode="json")

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture", type=Path)
    args = ap.parse_args()
    airports_db = os.environ.get("AIRPORTS_DB")
    if not airports_db:
        print("AIRPORTS_DB is not set", file=sys.stderr)
        return 1
    data = json.loads(args.fixture.read_text())
    data["derived"] = build_derived(data["inputs"], airports_db)
    args.fixture.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{args.fixture}: {len(data['derived']['observations'])} airports, "
          f"{sum(len(v) for v in data['derived']['observations'].values())} observations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
