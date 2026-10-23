"""Write the airport-card fixture the web tests and screenshots render (#881).

Two invented flight-day moments on the EGTF → EGLF test flight (the weather is
made up; the airports and runways are real): ``before`` (both cards open, a
gusty departure, a TEMPO thunderstorm at the destination) and ``after`` (the
departure folded after the take-off). Every card reading is produced by the
server's own models and helpers (``AirportObservation``'s computed fields,
``build_runway_wind_picture``), so the clients draw exactly what the server
would send. Times are anchored at ``T0``; the Playwright spec shifts them to
the test's "now". tests/test_airport_card_fixture.py fails when the file
drifts from what the server would produce: rerun this script then.

Usage:
    python scripts/export_airport_card_fixtures.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.analysis.runway_wind import build_runway_wind_picture, wind_sample
from weatherbrief.models.live import (
    LiveDestinationCell,
    LiveFocus,
    LiveGlanceDeparture,
    LiveGlanceDestination,
)
from weatherbrief.models.observations import AirportObservation, TafHour
from weatherbrief.models.runway_wind import RunwayEndInfo, RunwayInfo

OUT = Path(__file__).parent.parent / "web" / "tests" / "fixtures" / "airport_cards.json"
T0 = datetime(2026, 10, 23, 9, 40, tzinfo=timezone.utc)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def runway(rid: str, a: tuple[str, float], b: tuple[str, float], length: int) -> RunwayInfo:
    return RunwayInfo(id=rid, length_ft=length, surface="ASPH", hard=True,
                      ends=[RunwayEndInfo(ident=a[0], heading_true=a[1]),
                            RunwayEndInfo(ident=b[0], heading_true=b[1])])


def hours(start: int, cats: list[tuple[str, str | None, str | None]], eta_index: int) -> list[TafHour]:
    base = T0.replace(minute=0)
    return [TafHour(time=base + timedelta(hours=start + i), prevailing_category=p,
                    temporary_category=tc, temporary_type=tt, is_eta=i == eta_index)
            for i, (p, tc, tt) in enumerate(cats)]


def focus(kind: str, ident: str, lat: float, lon: float, layers: list[str]) -> LiveFocus:
    return LiveFocus(kind=kind, id=ident, bbox=[lon - 0.3, lat - 0.2, lon + 0.3, lat + 0.2],
                     layers=layers, time=None)


def egtf() -> AirportObservation:
    obs = AirportObservation(
        icao="EGTF", name="Fairoaks", distance_from_route_nm=0, nearest_waypoint_icao="EGTF",
        metar_raw="EGTF 230930Z 29015G25KT 9999 -RA BKN025 12/08 Q1009",
        metar_time=at(-10), metar_flight_category="MVFR", metar_ceiling_ft=2500,
        metar_visibility_m=9999, metar_wind_dir=290, metar_wind_speed_kt=15, metar_wind_gust_kt=25,
        metar_weather=["-RA"], metar_report_type="METAR",
        metar_previous_flight_category="VFR", metar_previous_time=at(-40),
        taf_raw="TAF EGTF 230500Z 2306/2406 28012KT 9999 SCT035 TEMPO 2309/2313 29018G28KT 6000 -RA BKN018",
        taf_valid_at_eta=True, taf_prevailing_category_at_eta="VFR",
        taf_temporary_category_at_eta="MVFR", taf_temporary_type="TEMPO",
        taf_flight_category_at_eta="MVFR", taf_wind_dir=290, taf_wind_speed_kt=18, taf_wind_gust_kt=28,
        has_metar=True, has_taf=True,
        taf_hours=hours(0, [("VFR", "MVFR", "TEMPO"), ("VFR", "MVFR", "TEMPO"),
                            ("VFR", "MVFR", "TEMPO"), ("VFR", "MVFR", "TEMPO"), ("VFR", None, None)], 1),
    )
    obs.runway_wind = build_runway_wind_picture("EGTF", [runway("06/24", ("06", 58), ("24", 238), 2664)], [
        wind_sample("metar", obs.metar_time, 290, 15, 25, None, None),
        wind_sample("taf", at(20), 290, 18, 28, None, None),
    ])
    advisory = next(w for w in obs.runway_wind.winds if w.wind.source == "metar")
    obs.metar_wind_advisory, obs.metar_best_runway_id = advisory.advisory, advisory.best_end
    return obs


def eglf() -> AirportObservation:
    obs = AirportObservation(
        icao="EGLF", name="Farnborough", distance_from_route_nm=0, nearest_waypoint_icao="EGLF",
        metar_raw="EGLF 230920Z 27012KT 9999 VCTS SCT035CB 13/09 Q1010",
        metar_time=at(-20), metar_flight_category="VFR", metar_ceiling_ft=None,
        metar_visibility_m=9999, metar_wind_dir=270, metar_wind_speed_kt=12, metar_wind_gust_kt=None,
        metar_weather=["VCTS"], metar_report_type="SPECI",
        metar_previous_flight_category="VFR", metar_previous_time=at(-50),
        taf_raw="TAF EGLF 230500Z 2306/2406 27012KT 9999 BKN020 TEMPO 2310/2314 TSRA BKN008CB",
        taf_valid_at_eta=True, taf_prevailing_category_at_eta="MVFR",
        taf_temporary_category_at_eta="IFR", taf_temporary_type="TEMPO",
        taf_flight_category_at_eta="IFR", taf_wind_dir=270, taf_wind_speed_kt=14, taf_wind_gust_kt=24,
        has_metar=True, has_taf=True,
        taf_hours=hours(0, [("VFR", None, None), ("MVFR", "IFR", "TEMPO"), ("MVFR", "IFR", "TEMPO"),
                            ("MVFR", "IFR", "TEMPO"), ("MVFR", "IFR", "TEMPO"), ("VFR", None, None)], 2),
    )
    obs.runway_wind = build_runway_wind_picture("EGLF", [runway("06/24", ("06", 62), ("24", 242), 8005)], [
        wind_sample("metar", obs.metar_time, 270, 12, None, None, None),
        wind_sample("taf", at(80), 270, 14, 24, None, None),
    ])
    advisory = next(w for w in obs.runway_wind.winds if w.wind.source == "metar")
    obs.metar_wind_advisory, obs.metar_best_runway_id = advisory.advisory, advisory.best_end
    return obs


STATION_LAYERS = ["route", "radar", "cells", "lightning", "sigmets"]


def departure(folded: bool) -> LiveGlanceDeparture:
    a = egtf()
    return LiveGlanceDeparture(
        icao="EGTF", name="Fairoaks", etd=at(20) if not folded else at(24),
        metar_text="EGTF MVFR (BKN 2,500 ft) · worse since 09:00Z", metar_category="MVFR",
        metar_time=a.metar_time, taf_text="TAF at ETD VFR, TEMPO MVFR", taf_category="VFR",
        wind_text="Wind at ETD 290° 18G28 kt · RWY 24 crosswind 14 kt", wind_advisory="amber",
        cells_text="no cells within 30 NM", cells=[], lightning_text="no lightning ≤20 NM",
        focus=focus("station", "EGTF", 51.348, -0.559, STATION_LAYERS), airport=a,
        folded=folded, folded_at=at(24) if folded else None,
        folded_source="takeoff" if folded else None,
        folded_text="EGTF 290/15G25 · RWY 24 · departed 10:04Z" if folded else None,
    )


def destination() -> LiveGlanceDestination:
    a = eglf()
    cell = "core41-zz1"
    return LiveGlanceDestination(
        icao="EGLF", name="Farnborough", eta=at(80),
        metar_text="EGLF VFR · VCTS", metar_category="VFR", metar_time=a.metar_time,
        taf_text="TAF at ETA MVFR, TEMPO IFR TSRA", taf_category="MVFR",
        wind_text="Wind at ETA 270° 14G24 kt · RWY 24 crosswind 6 kt", wind_advisory="green",
        cells_text="nearest cell 9 NM SW (48 dBZ, lightning), closing 6 kt",
        cells=[LiveDestinationCell(id=cell, text="9 NM SW · 48 dBZ, lightning · closing 6 kt",
                                   focus=focus("storm", cell, 51.2, -0.9, ["route", "radar", "cells"]))],
        lightning_text="lightning ≤20 NM (4 flashes, nearest 9 NM)",
        focus=focus("station", "EGLF", 51.276, -0.776, STATION_LAYERS), airport=a,
    )


def build() -> str:
    """The fixture's text (tests/test_airport_card_fixture.py compares it)."""
    dump = lambda m: m.model_dump(mode="json")  # noqa: E731
    data = {
        "t0": T0.isoformat().replace("+00:00", "Z"),
        "before": {"departure": dump(departure(False)), "destination": dump(destination())},
        "after": {"departure": dump(departure(True)), "destination": dump(destination())},
    }
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def main() -> int:
    OUT.write_text(build())
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
