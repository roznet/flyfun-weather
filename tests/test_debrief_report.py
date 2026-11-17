"""Debrief report, phase 0 prototype: pure rules, answers, the end-to-end
build on a recorded scenario, and the API routes."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.debriefs.report import (
    ANSWERS_FILE,
    airport_at,
    build_questions,
    build_report,
    build_summary,
    flown_altitude,
    forecast_at,
    hazard_stretches,
    load_answers,
    observed_class,
    save_answer,
)

UTC = timezone.utc


def _region(lo, hi, base, top, sev="amber", kind="icing_band"):
    return {"dist_from_nm": lo, "dist_to_nm": hi, "base_ft": base, "top_ft": top, "kind": kind, "severity": sev}


def _manifest(advisories: dict[str, dict[str, list[dict]]], *, models=("ecmwf", "icon", "gfs")) -> dict:
    """``{advisory_id: {model: [regions]}}``; models not listed are green."""
    out = []
    for aid, per in advisories.items():
        out.append({
            "advisory_id": aid,
            "aggregate_status": "amber" if any(per.values()) else "green",
            "per_model": [
                {"model": m, "status": "amber" if per.get(m) else "green",
                 "highlights": {"ribbon": [], "regions": per.get(m, [])}}
                for m in models
            ],
        })
    return {"aggregation": "majority", "cruise_altitude_ft": 7000, "advisories": out}


class _Progress:
    """Linear 120 kt from 10:00Z."""

    t0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)

    def eta(self, along):
        return None if along is None else self.t0 + timedelta(hours=along / 120.0)


# --- Forecast side ---------------------------------------------------------------


def test_stretches_merge_models_and_mark_the_flown_level():
    m = _manifest({"aircraft_icing": {
        "ecmwf": [_region(40, 70, 5000, 9000)],
        "icon": [_region(45, 80, 6000, 10000, "red")],
    }})
    [s] = hazard_stretches(m, ("aircraft_icing",), 7000)
    assert (s["from_nm"], s["to_nm"], s["base_ft"], s["top_ft"]) == (40, 80, 5000, 10000)
    assert s["severity"] == "red" and s["models"] == ["ecmwf", "icon"] and s["models_total"] == 3
    assert s["at_level"] is True
    [s] = hazard_stretches(m, ("aircraft_icing",), 15000)
    assert s["at_level"] is False


def test_separate_stretches_stay_separate():
    m = _manifest({"aircraft_icing": {"ecmwf": [_region(10, 20, 5000, 9000), _region(60, 70, 5000, 9000)]}})
    assert len(hazard_stretches(m, ("aircraft_icing",), 7000)) == 2


def test_forecast_at_uses_the_pack_aggregation():
    lane = {"aggregation": "majority", "advisories": [{"advisory_id": "aircraft_icing", "per_model": [
        {"model": "a", "ribbon": [[0, 50, "amber"], [50, 100, "green"]]},
        {"model": "b", "ribbon": [[0, 100, "green"]]},
        {"model": "c", "ribbon": [[0, 100, "green"]]},
    ]}]}
    assert forecast_at(lane, 20)["severity"] == "green"
    lane["aggregation"] = "worst"
    assert forecast_at(lane, 20) == {"severity": "amber", "hazards": ["icing"]}


# --- Observed side ---------------------------------------------------------------


@pytest.mark.parametrize("seg,expected", [
    ({"status": "observed", "radar_status": "measured", "radar_max_dbz": 45}, "heavy"),
    ({"status": "observed", "radar_status": "measured", "radar_max_dbz": 25}, "rain"),
    ({"status": "observed", "radar_status": "measured", "radar_max_dbz": 10, "lightning": {"n": 2}}, "heavy"),
    ({"status": "observed", "radar_status": "measured", "radar_max_dbz": None}, "clear"),
    ({"status": "observed", "radar_status": "no_coverage"}, "no_data"),
    ({"status": "not_reached"}, "not_recorded"),
])
def test_observed_class_never_reads_missing_radar_as_clear(seg, expected):
    assert observed_class(seg) == expected


def test_flown_altitude_from_mid_route_airborne_fixes_else_planned():
    fix = lambda along, alt, terrain=200: {"type": "progress", "event": "fix", "along_nm": along,
                                           "alt_ft": alt, "terrain_ft": terrain}
    hist = [fix(5, 1500), fix(40, 7100), fix(50, 6900), fix(60, 7000), fix(95, 2000)]
    assert flown_altitude(hist, 100, 8000) == {"ft": 7000, "source": "fixes", "fixes": 3}
    assert flown_altitude([], 100, 8000)["source"] == "planned"


METAR_EHRD = "METAR EHRD 141145Z 24012KT 6000 -RA BKN009 09/08 Q1008"
TAF_EHRD = "TAF EHRD 140500Z 1406/1512 24010KT 9999 SCT020 BKN035 TEMPO 1410/1414 6000 -RA BKN012"


def test_airport_lower_than_the_taf_the_pilot_had():
    hist = [
        {"type": "report", "kind": "taf", "icao": "EHRD", "issued_at": "2026-11-14T05:00:00+00:00",
         "raw": TAF_EHRD, "tick_at": "2026-11-14T07:00:00+00:00"},
        {"type": "report", "kind": "metar", "icao": "EHRD", "observed_at": "2026-11-14T11:45:00+00:00",
         "raw": METAR_EHRD, "tick_at": "2026-11-14T11:50:00+00:00"},
    ]
    a = airport_at(hist, "EHRD", datetime(2026, 11, 14, 11, 40, tzinfo=UTC), datetime(2026, 11, 14, 10, 0, tzinfo=UTC))
    assert a["metar_category"] == "IFR"
    assert a["taf_category"] == "VFR"
    assert a["match"] == "lower"
    assert airport_at([], "EHRD", datetime(2026, 11, 14, 11, 40, tzinfo=UTC), None)["match"] == "unverifiable"


# --- Questions and summary -------------------------------------------------------


def test_questions_icing_first_one_per_kind_at_most_three():
    m = _manifest({
        "vmc_cruise": {"ecmwf": [_region(0, 30, 6000, 8000, kind="cruise_imc")]},
        "turbulence": {"gfs": [_region(10, 40, 5000, 9000, kind="cat_layer")]},
        "aircraft_icing": {"ecmwf": [_region(40, 70, 5000, 9000)], "icon": [_region(42, 72, 5000, 9000)]},
        "fiki_icing": {"ecmwf": [_region(80, 90, 5000, 9000)]},
        "convective": {"ecmwf": [_region(0, 100, None, None, kind="tower")]},
    })
    segs = [{"from_nm": i * 10, "to_nm": i * 10 + 10, "status": "observed", "radar_status": "no_coverage"}
            for i in range(10)]
    qs = build_questions(m, segs, {"ft": 7000, "source": "fixes"}, _Progress(), None)
    assert [q["kind"] for q in qs] == ["icing", "turbulence", "cloud"]
    ice = qs[0]
    assert "between 5,000 ft and 9,000 ft" in ice["text"] and "2 of 3 models" in ice["text"]
    assert "10:28Z" in ice["text"] and "You were at 7,000 ft." in ice["text"]
    assert [o["id"] for o in ice["options"]] == ["none", "light", "moderate_plus", "not_in_cloud"]


def test_no_question_above_or_below_the_hazard_nor_where_radar_saw():
    m = _manifest({"aircraft_icing": {"ecmwf": [_region(40, 70, 3000, 5000)]},
                   "convective": {"ecmwf": [_region(0, 50, None, None, kind="tower")]}})
    segs = [{"from_nm": 0, "to_nm": 50, "status": "observed", "radar_status": "measured", "radar_max_dbz": 30}]
    assert build_questions(m, segs, {"ft": 9000, "source": "planned"}, _Progress(), None) == []


def test_summary_never_claims_as_forecast_when_nothing_was_checked():
    strip = [{"from_nm": 0, "to_nm": 10, "observed": "no_data", "forecast_hazards": []}]
    s = build_summary(strip, [{"role": "arrival", "icao": "EHRD", "match": "unverifiable"}], [], None)
    assert s["text"].startswith("Little the instruments could check")
    s = build_summary(strip, [{"role": "arrival", "icao": "EHRD", "match": "lower",
                               "metar_category": "IFR", "taf_category": "VFR"}], [], None)
    assert s["text"].startswith("Some differences") and "lower than the TAF (IFR against VFR)" in s["text"]


# --- Answers ---------------------------------------------------------------------


def test_answers_round_trip_with_the_question_as_asked(tmp_path):
    q = {"id": "icing-40-80", "kind": "icing", "text": "Icing?", "options": [{"id": "none"}, {"id": "light"}],
         "stretch": {"from_nm": 40}, "at": "10:28Z", "flown_ft": 7000, "flown_source": "fixes"}
    save_answer(tmp_path, q, "light", avoided=True)
    got = load_answers(tmp_path)["icing-40-80"]
    assert got["answer"] == "light" and got["avoided"] is True and got["question"]["text"] == "Icing?"
    with pytest.raises(ValueError):
        save_answer(tmp_path, q, "severe")
    save_answer(tmp_path, q, None)
    assert json.loads((tmp_path / ANSWERS_FILE).read_text()) == {}


# --- End to end on a recorded flight ------------------------------------------


def test_report_on_the_lell_lemi_replay(tmp_path):
    from live_scenario_replay import load_scenario, replay
    from test_live_scenarios import LELL_LEMI

    from weatherbrief.analysis.route_locator import for_route
    from weatherbrief.tasks.live_layer import flight_progress, load_live_history, load_progress_events
    from weatherbrief.tasks.live_recap import build_recap
    from weatherbrief.tasks.live_snapshots import load_snapshots
    from weatherbrief.tasks.live_trail import route_context

    flight_dir = tmp_path / "u" / "flight"
    ticks = replay(load_scenario(LELL_LEMI), flight_dir)
    history = load_live_history(flight_dir)
    route, dep = route_context(ticks[-1].briefing)
    progress = flight_progress(route, dep, ticks[-1].at, load_progress_events(flight_dir))
    recap = build_recap(load_snapshots(flight_dir), history, progress)
    total = recap["route_nm"]
    manifest = _manifest({"convective": {"ecmwf": [_region(0, total / 2, None, None, "red", "tower")]}})
    lane = {"aggregation": "majority", "cruise_altitude_ft": route.cruise_altitude_ft, "advisories": [
        {"advisory_id": "convective", "aggregate_status": "red", "per_model": [
            {"model": "ecmwf", "status": "red", "ribbon": [[0, total / 2, "red"], [total / 2, total, "green"]]}]}]}
    r = build_report(recap, lane=lane, manifest=manifest, history=history, progress=progress,
                     route=route, locator=for_route(route))
    assert r["status"] == "ok" and r["route"][0] == "LELL" and r["route"][-1] == "LEMI"
    assert len(r["strip"]) == len(recap["segments"])
    assert {a["role"] for a in r["airports"]} == {"departure", "arrival"}
    # No radar in the scenario: the only question is the showers one, and
    # the summary does not call the flight "as forecast".
    assert [q["kind"] for q in r["questions"]] == ["showers"]
    assert not r["summary"]["text"].startswith("Mostly as forecast")
    assert r["interest"]["interesting"] is True


# --- API ---------------------------------------------------------------------------

from test_debrief_api import app_db, client, past_flight  # noqa: E402,F401  (fixtures)


def test_api_report_for_a_flight_without_packs(client, past_flight):
    r = client.get(f"/api/flights/{past_flight.id}/debrief/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_live_window" and body["questions"] == []
    assert body["interest"] == {"interesting": False, "reasons": []}


def test_api_report_404_for_an_unknown_flight_and_unknown_question(client, past_flight):
    assert client.get("/api/flights/nope/debrief/report").status_code == 404
    r = client.put(f"/api/flights/{past_flight.id}/debrief/report/answers",
                   json={"question_id": "icing-0-10", "answer": "none"})
    assert r.status_code == 404
