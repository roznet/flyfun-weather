"""Debrief report (#1023): pure rules, the answer mapping, the end-to-end
build on a recorded scenario, the API routes, and the window-close job."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.debriefs.report import (
    _metar_conditions,
    airport_at,
    airport_line,
    build_headline,
    build_interest,
    build_questions,
    build_report,
    build_summary,
    flight_alerts,
    flown_altitude,
    forecast_at,
    forecast_at_level,
    hazard_stretches,
    observed_class,
    outcomes_from_answers,
    push_text,
    validate_answer,
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


def test_airport_worse_than_the_taf_the_pilot_had():
    hist = [
        {"type": "report", "kind": "taf", "icao": "EHRD", "issued_at": "2026-11-14T05:00:00+00:00",
         "raw": TAF_EHRD, "tick_at": "2026-11-14T07:00:00+00:00"},
        {"type": "report", "kind": "metar", "icao": "EHRD", "observed_at": "2026-11-14T11:45:00+00:00",
         "raw": METAR_EHRD, "tick_at": "2026-11-14T11:50:00+00:00"},
    ]
    a = airport_at(hist, "EHRD", datetime(2026, 11, 14, 11, 40, tzinfo=UTC), datetime(2026, 11, 14, 10, 0, tzinfo=UTC))
    assert a["metar_category"] == "IFR"
    assert a["taf_category"] == "VFR"
    assert a["match"] == "worse"
    line = airport_line(a, "Arrival")
    # Both sides written out, no "as forecast" verdict.
    assert line.startswith("Arrival EHRD: 11:45Z IFR") and "TAF: VFR" in line
    assert line.endswith("Worse than the TAF allowed.")
    assert airport_at([], "EHRD", datetime(2026, 11, 14, 11, 40, tzinfo=UTC), None)["match"] == "unverifiable"


def _metar(icao, hhmm, raw_tail):
    return {"type": "report", "kind": "metar", "icao": icao,
            "observed_at": f"2026-10-31T{hhmm[:2]}:{hhmm[2:]}:00+00:00",
            "raw": f"METAR {icao} 31{hhmm}Z AUTO {raw_tail}"}


def test_airport_reads_the_proxy_and_the_fog_before_departure():
    """EGTF has no reports: EGLL's are read. The fog that lifted before
    departure is shown, and a TAF inside a BECMG window says it was lifting."""
    taf = ("TAF AMD EGLL 310738Z 3107/0112 10005KT 1500 BR BKN002 "
           "BECMG 3108/3111 9999 NSW SCT020")
    hist = [
        _metar("EGLL", "0820", "10006KT 3100 BR OVC003 09/09 Q1032"),
        _metar("EGLL", "1050", "VRB02KT 9000 NCD 11/09 Q1032"),
        {"type": "report", "kind": "taf", "icao": "EGLL", "issued_at": "2026-10-31T07:38:00+00:00", "raw": taf},
        # Issued before departure but valid only from 12Z: the older one is read.
        {"type": "report", "kind": "taf", "icao": "EGLL", "issued_at": "2026-10-31T10:55:00+00:00",
         "raw": "TAF EGLL 311055Z 3112/0118 08005KT 9999 SCT020"},
    ]
    dep = datetime(2026, 10, 31, 10, 57, tzinfo=UTC)
    a = airport_at(hist, "EGTF", dep, dep, metar_station="EGLL", taf_station="EGLL", around="before")
    assert a["metar_station"] == "EGLL" and a["metar_category"] == "VFR"
    assert a["worst_around"]["category"] == "LIFR"
    assert a["taf_becoming"]["category"] == "VFR"
    line = airport_line(a, "Departure")
    assert line.startswith("Departure EGTF (via EGLL): 10:50Z VFR, 9000 NCD.")
    assert "Before departure: LIFR at 08:20Z, 3100 BR OVC003." in line
    assert "becoming VFR between 08 and 11Z" in line


def test_airport_cell_after_landing_without_a_category_change():
    hist = [
        _metar("EGLL", "1250", "05012KT 9999 SCT040 14/04 Q1029"),
        _metar("EGLL", "1320", "04015G30KT 350V070 9999 -RA SCT037/// BKN043/// //////CB 13/05 Q1029"),
    ]
    a = airport_at(hist, "EGTF", datetime(2026, 10, 31, 12, 58, tzinfo=UTC), None,
                   metar_station="EGLL", around="after")
    assert a["worst_around"]["conditions"] == "9999 -RA SCT037 BKN043 CB gusts 30 kt"


@pytest.mark.parametrize("raw, expected", [
    ("METAR LFAT 311200Z AUTO 07007KT 040V120 CAVOK 13/09 Q1031 NOSIG", "CAVOK"),
    ("METAR LFAT 231000Z AUTO 16021G35KT 120V210 3000 RA BR OVC008/// ///TCU 12/11 Q0979 TEMPO 25025G50KT 1200",
     "3000 RA BR OVC008 TCU gusts 35 kt"),
])
def test_metar_conditions(raw, expected):
    assert _metar_conditions(raw) == expected


# --- Questions and summary -------------------------------------------------------


def test_questions_icing_first_one_per_kind_at_most_three():
    m = _manifest({
        "vmc_cruise": {"ecmwf": [_region(0, 30, 6000, 8000, kind="cruise_imc")],
                       "gfs": [_region(0, 30, 6000, 8000, kind="cruise_imc")]},
        "turbulence": {"gfs": [_region(10, 40, 5000, 9000, kind="cat_layer")],
                       "icon": [_region(10, 40, 5000, 9000, kind="cat_layer")]},
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


def test_one_model_alone_asks_nothing_and_pushes_nothing():
    m = _manifest({"fiki_icing": {"gfs": [_region(0, 40, 4400, 6800)]}})
    segs = [{"from_nm": 0, "to_nm": 40, "status": "observed", "radar_status": "measured"}]
    flown = {"ft": 3900, "source": "fixes"}
    assert build_questions(m, segs, flown, _Progress(), None) == []
    assert forecast_at_level(m, 3900) == []
    assert build_interest([], [], [], [], [])["interesting"] is False


def test_no_question_above_or_below_the_hazard_nor_where_radar_saw():
    m = _manifest({"aircraft_icing": {"ecmwf": [_region(40, 70, 3000, 5000)]},
                   "convective": {"ecmwf": [_region(0, 50, None, None, kind="tower")]}})
    segs = [{"from_nm": 0, "to_nm": 50, "status": "observed", "radar_status": "measured", "radar_max_dbz": 30}]
    assert build_questions(m, segs, {"ft": 9000, "source": "planned"}, _Progress(), None) == []


def test_summary_writes_both_sides_and_never_says_as_forecast():
    strip = [{"from_nm": 0, "to_nm": 10, "observed": "no_data", "forecast_hazards": []}]
    ap = {"role": "arrival", "icao": "EHRD", "match": "within", "metar_category": "VFR",
          "metar_time": "2026-11-14T11:45:00+00:00", "metar_conditions": "CAVOK", "taf_category": "VFR"}
    s = build_summary(strip, [ap], [], None, flown={"ft": 5000, "source": "fixes"})
    assert [line["kind"] for line in s["lines"]] == ["forecast", "radar", "arrival"]
    assert "as forecast" not in s["text"].lower() and "as the taf" not in s["text"].lower()
    assert "Arrival EHRD: 11:45Z VFR, CAVOK. TAF: VFR." in s["text"]
    assert "Radar: no coverage along the route." in s["text"]
    assert "nothing most models agreed on" in s["text"]


def _seg(obs):
    return {"from_nm": 0, "to_nm": 10, "observed": obs, "forecast_hazards": []}


SHOWERS = [{"kind": "showers", "stretch": {}}]


@pytest.mark.parametrize("obs, hazards, text, differs", [
    ("clear", [], "Clear, as forecast.", False),
    ("rain", [], "Mostly clear with a few light showers.", False),
    ("rain", SHOWERS, "Some showers en route, as forecast.", False),
    ("heavy", SHOWERS, "Heavy showers en route, as forecast.", False),
    ("heavy", [], "Heavy showers en route that the forecast did not have.", True),
    ("clear", SHOWERS, "Clear: the forecast showers did not show.", True),
    ("no_data", [], "No radar along the route.", False),
])
def test_headline_judges_only_what_radar_sees(obs, hazards, text, differs):
    h = build_headline([_seg(obs)], [], hazards)
    assert h == {"text": text, "differs_from_forecast": differs}


def test_headline_airports_only_when_they_matter():
    vfr = {"role": "departure", "icao": "EGTF", "metar_category": "VFR", "match": "within"}
    mvfr = {"role": "arrival", "icao": "LFAT", "metar_category": "MVFR", "match": "within"}
    assert build_headline([_seg("clear")], [vfr, mvfr], [])["text"] == \
        "Clear, as forecast. MVFR arrival at LFAT, as the TAF had it."
    fog = {**vfr, "worst_around": {"when": "before", "category": "LIFR", "conditions": "3100 BR OVC003"}}
    assert build_headline([_seg("clear")], [fog], [])["text"] == \
        "Clear, as forecast. The fog at EGTF lifted before you left."
    cell = {**vfr, "role": "arrival", "worst_around": {"when": "after", "category": "VFR", "raw": "METAR EGLL 181320Z 9999 BKN043CB"}}
    assert build_headline([_seg("rain")], [cell], [])["text"].endswith("A storm cell reached EGTF after you landed.")
    ifr = [{"role": r, "icao": i, "metar_category": "IFR", "match": "within"} for r, i in (("departure", "EGTF"), ("arrival", "LFAT"))]
    assert build_headline([_seg("heavy")], ifr, SHOWERS, cancelled=True)["text"] == \
        "Cancelled. Heavy showers en route, as forecast. IFR at both ends, as the TAFs had it."
    worse = {**mvfr, "metar_category": "IFR", "match": "worse"}
    assert build_headline([_seg("clear")], [worse], [])["differs_from_forecast"] is True


def test_alerts_count_only_between_departure_and_landing():
    alerts = [{"at": f"2026-10-31T{h}:00:00+00:00", "tier": "alert", "event": "raised", "message": h}
              for h in ("07", "11", "13")]
    got = flight_alerts(alerts, "2026-10-31T10:57:00+00:00", "2026-10-31T12:00:00+00:00")
    assert [a["message"] for a in got] == ["11"]


def test_cancelled_flight_has_no_push_and_says_so():
    s = build_summary([], [], [], None, cancelled=True)
    assert s["lines"][0]["kind"] == "cancelled"
    it = build_interest([{"kind": "icing"}], [{"observed": "heavy"}], [], [{"tier": "alert"}], [], cancelled=True)
    assert it == {"interesting": False, "reasons": [], "cancelled": True}


# --- Answers ---------------------------------------------------------------------


@pytest.mark.parametrize("answers, expected", [
    ([{"kind": "icing", "answer": "none"}], {"ICE": "better"}),
    ([{"kind": "icing", "answer": "light"}], {"ICE": "consistent"}),
    ([{"kind": "icing", "answer": "moderate_plus"}], {"ICE": "worse"}),
    ([{"kind": "icing", "answer": "not_in_cloud"}], {}),
    ([{"kind": "icing", "answer": "moderate_plus", "avoided": True}], {}),
    ([{"kind": "cloud", "answer": "in_and_out"}], {"IMC": "consistent"}),
    ([{"kind": "cloud", "answer": "on_top"}], {"IMC": "better"}),
    ([{"kind": "turbulence", "answer": "none"}, {"kind": "turbulence", "answer": "moderate_plus"}],
     {"TURB": "worse"}),
    ([{"kind": "showers", "answer": "build_ups"}], {}),
])
def test_answers_map_onto_the_old_outcome_categories(answers, expected):
    assert outcomes_from_answers(answers) == expected


def test_validate_answer_refuses_unknown_options_and_avoided_cloud():
    q = {"id": "icing-40-80", "kind": "icing", "options": [{"id": "none"}, {"id": "light"}], "avoidable": True}
    validate_answer(q, "light", True)
    with pytest.raises(ValueError):
        validate_answer(q, "severe", False)
    with pytest.raises(ValueError):
        validate_answer({**q, "kind": "cloud", "avoidable": False}, "light", True)


def test_push_text_needs_interest_and_a_question_or_a_different_headline():
    q = [{"kind": "icing"}, {"kind": "icing"}, {"kind": "turbulence"}]
    interesting = {"interesting": True, "reasons": [{"kind": "questions"}]}
    assert push_text({"interest": interesting, "questions": q[:2]}) == \
        "Two quick questions about the icing forecast."
    assert push_text({"interest": interesting, "questions": q}) == \
        "Three quick questions about the icing and turbulence forecast."
    differs = {"headline": "Heavy showers en route that the forecast did not have.", "differs_from_forecast": True}
    assert push_text({"interest": interesting, "questions": [], "summary": differs}) == differs["headline"]
    assert push_text({"interest": interesting, "questions": [],
                      "summary": {**differs, "differs_from_forecast": False}}) is None
    assert push_text({"interest": {"interesting": False}, "questions": q}) is None
    assert push_text({"interest": interesting, "questions": q, "cancelled": True}) is None


def test_stretch_keeps_each_model_forecast_for_the_answer_row():
    m = _manifest({"aircraft_icing": {"ecmwf": [_region(10, 40, 5000, 9000, "red")],
                                      "icon": [_region(12, 38, 6000, 8000)]}})
    (s,) = hazard_stretches(m, ("aircraft_icing",), 7000)
    assert s["per_model"] == {"ecmwf": {"severity": "red", "base_ft": 5000, "top_ft": 9000},
                              "icon": {"severity": "amber", "base_ft": 6000, "top_ft": 8000}}


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
    manifest = _manifest({"convective": {"ecmwf": [_region(0, total / 2, None, None, "red", "tower")],
                                         "icon": [_region(0, total / 2, None, None, "red", "tower")]}})
    lane = {"aggregation": "majority", "cruise_altitude_ft": route.cruise_altitude_ft, "advisories": [
        {"advisory_id": "convective", "aggregate_status": "red", "per_model": [
            {"model": "ecmwf", "status": "red", "ribbon": [[0, total / 2, "red"], [total / 2, total, "green"]]}]}]}
    r = build_report(recap, lane=lane, manifest=manifest, history=history, progress=progress,
                     route=route, locator=for_route(route))
    assert r["status"] == "ok" and r["route"][0] == "LELL" and r["route"][-1] == "LEMI"
    assert len(r["strip"]) == len(recap["segments"])
    assert {a["role"] for a in r["airports"]} == {"departure", "arrival"}
    # No radar in the scenario: the only question is the showers one, and
    # the summary never calls the flight "as forecast".
    assert [q["kind"] for q in r["questions"]] == ["showers"]
    assert "as forecast" not in r["summary"]["text"].lower()
    assert r["interest"]["interesting"] is True


# --- API ---------------------------------------------------------------------------


from test_debrief_api import app_db, client, past_flight  # noqa: E402,F401  (fixtures)

_Q = {"id": "icing-40-80", "kind": "icing", "text": "Icing was forecast. What did you get?",
      "options": [{"id": "none"}, {"id": "light"}, {"id": "moderate_plus"}, {"id": "not_in_cloud"}],
      "avoidable": True, "stretch": {"from_nm": 40, "to_nm": 80}, "at": "10:28Z",
      "forecast": {"ecmwf": {"severity": "amber", "base_ft": 5000, "top_ft": 9000}},
      "flown_ft": 7000, "flown_source": "fixes"}


@pytest.fixture
def one_question(monkeypatch):
    """The report asks one icing question, whatever the flight's files."""
    from weatherbrief.debriefs import report as report_mod

    def fake(db, flight_id, now=None):
        from weatherbrief.storage.debriefs import load_answers

        answers = load_answers(db, flight_id)
        return {"flight_id": flight_id, "status": "ok", "cancelled": False,
                "questions": [{**_Q, "answer": answers.get(_Q["id"])}],
                "summary": {"headline": "Clear, as forecast.", "differs_from_forecast": False},
                "interest": {"interesting": True, "reasons": [{"kind": "questions", "count": 1}]}}

    monkeypatch.setattr(report_mod, "report_for_flight", fake)
    return fake


def test_api_report_for_a_flight_without_packs(client, past_flight):
    r = client.get(f"/api/flights/{past_flight.id}/debrief/report")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_live_window" and body["questions"] == []
    assert body["interest"] == {"interesting": False, "reasons": []}


def test_api_report_404_for_an_unknown_flight_and_unknown_question(client, past_flight):
    assert client.get("/api/flights/nope/debrief/report").status_code == 404
    r = client.put(f"/api/flights/{past_flight.id}/debrief/answers",
                   json={"answers": [{"question_id": "icing-0-10", "answer": "none"}]})
    assert r.status_code == 404


def test_api_answers_fold_into_the_debrief(client, past_flight, one_question, app_db):
    from weatherbrief.db.models import FlightDebriefAnswerRow

    url = f"/api/flights/{past_flight.id}/debrief/answers"
    bad = client.put(url, json={"answers": [{"question_id": _Q["id"], "answer": "severe"}]})
    assert bad.status_code == 422
    r = client.put(url, json={"answers": [{"question_id": _Q["id"], "answer": "moderate_plus", "note": " iced "}]})
    assert r.status_code == 200, r.text
    assert r.json()["questions"][0]["answer"]["answer"] == "moderate_plus"
    d = client.get(f"/api/flights/{past_flight.id}/debrief").json()
    assert d["decision"] == "flown" and d["form"] == "questions" and d["outcomes"] == {"ICE": "worse"}

    s = app_db()
    row = s.query(FlightDebriefAnswerRow).one()
    assert row.note == "iced" and row.rules_version == "1" and row.kind == "icing"
    assert json.loads(row.question_json)["text"] == _Q["text"]
    assert json.loads(row.forecast_json) == _Q["forecast"]
    s.close()

    # Avoided: no outcome for the category any more; clearing removes the row.
    client.put(url, json={"answers": [{"question_id": _Q["id"], "answer": "light", "avoided": True}]})
    assert client.get(f"/api/flights/{past_flight.id}/debrief").json()["outcomes"] == {}
    client.put(url, json={"answers": [{"question_id": _Q["id"], "answer": None}]})
    s = app_db()
    assert s.query(FlightDebriefAnswerRow).count() == 0
    s.close()


def test_api_old_form_is_marked_legacy_and_questions_form_derives_outcomes(client, past_flight, one_question):
    url = f"/api/flights/{past_flight.id}/debrief"
    old = client.put(url, json={"decision": "flown", "outcomes": {"ICE": "consistent"}}).json()
    assert old["form"] == "legacy" and old["outcomes"] == {"ICE": "consistent"}
    client.put(f"{url}/answers", json={"answers": [{"question_id": _Q["id"], "answer": "none"}]})
    # The new section's flown tap: outcomes sent are ignored, derived instead.
    new = client.put(url, json={"decision": "flown", "form": "questions", "outcomes": {"ICE": "worse"}}).json()
    assert new["form"] == "questions" and new["outcomes"] == {"ICE": "better"}
    cancelled = client.put(url, json={"decision": "cancelled", "form": "questions", "reasons": ["IMC"]}).json()
    assert cancelled["outcomes"] == {} and cancelled["reasons"] == ["IMC"]


# --- Window close (#1023) ------------------------------------------------------------


class _CloseProgress:
    def __init__(self, started=True, airborne=True):
        self.start_fix = object() if started else None
        self.takeoff_at = datetime(2026, 11, 20, 10, tzinfo=UTC) if airborne else None


def _flight_row(app_db, flight_id):
    from weatherbrief.db.models import FlightRow

    s = app_db()
    return s, s.get(FlightRow, flight_id)


@pytest.mark.parametrize("progress, notify_push, devices, expected", [
    (_CloseProgress(), True, True, "shadow"),
    (_CloseProgress(started=False), True, True, "skipped:not_started"),
    (_CloseProgress(airborne=False), True, True, "skipped:not_airborne"),
    (_CloseProgress(), False, True, "skipped:push_off"),
    (_CloseProgress(), True, False, "skipped:no_device"),
])
def test_close_flight_stores_the_report_once_and_decides_the_push(
    app_db, past_flight, one_question, monkeypatch, progress, notify_push, devices, expected,
):
    from weatherbrief.api import preferences
    from weatherbrief.notify import push
    from weatherbrief.tasks import debrief_close

    monkeypatch.setattr(preferences, "load_notify_prefs", lambda db, uid: {"notify_push": notify_push})
    monkeypatch.setattr(push, "_load_devices", lambda db, uid: ["dev"] if devices else [])
    monkeypatch.delenv("WB_DEBRIEF_PUSH", raising=False)
    s, flight = _flight_row(app_db, past_flight.id)
    row = debrief_close.close_flight(s, flight, progress, datetime(2026, 11, 20, 12, tzinfo=UTC))
    assert row.push_outcome == expected
    assert row.interesting and row.headline == "Clear, as forecast."
    assert json.loads(row.questions_json) == [{"id": _Q["id"], "kind": "icing", "text": _Q["text"]}]
    if expected == "shadow":
        assert row.push_reason == "One quick question about the icing forecast."
    summary = debrief_close.debrief_summary(s, past_flight.id)
    assert summary["questions_open"][0]["id"] == _Q["id"] and summary["questions_answered"] == 0
    s.close()


def test_close_flight_sends_when_enabled_and_never_for_a_cancelled_flight(
    app_db, past_flight, one_question, monkeypatch,
):
    from weatherbrief.api import preferences
    from weatherbrief.debriefs.taxonomy import Decision
    from weatherbrief.notify import push
    from weatherbrief.storage.debriefs import upsert_debrief
    from weatherbrief.tasks import debrief_close

    sent = []
    monkeypatch.setattr(preferences, "load_notify_prefs", lambda db, uid: {"notify_push": True})
    monkeypatch.setattr(push, "_load_devices", lambda db, uid: ["dev"])
    monkeypatch.setattr(push, "send_debrief_push", lambda db, uid, payload: sent.append(payload) or 1)
    monkeypatch.setenv("WB_DEBRIEF_PUSH", "1")
    now = datetime(2026, 11, 20, 12, tzinfo=UTC)
    s, flight = _flight_row(app_db, past_flight.id)
    row = debrief_close.close_flight(s, flight, _CloseProgress(), now)
    assert row.push_outcome == "sent" and row.pushed_at is not None
    assert sent[0]["type"] == "debrief" and sent[0]["aps"]["alert"]["title"] == "EGTK → LFAT · debrief"
    s.close()

    s = app_db()
    from weatherbrief.db.models import FlightDebriefReportRow

    s.delete(s.get(FlightDebriefReportRow, past_flight.id))
    upsert_debrief(s, flight_id=past_flight.id, decision=Decision.CANCELLED)
    s.commit()
    s.close()
    s, flight = _flight_row(app_db, past_flight.id)
    row = debrief_close.close_flight(s, flight, _CloseProgress(), now)
    assert row.push_outcome == "skipped:cancelled_or_monitoring" and len(sent) == 1
    s.close()


def test_flights_list_carries_the_report_flags(client, past_flight, one_question, app_db, monkeypatch):
    from weatherbrief.tasks import debrief_close

    flights = {f["id"]: f for f in client.get("/api/flights").json()}
    assert flights[past_flight.id]["debrief_ready"] is False
    s, flight = _flight_row(app_db, past_flight.id)
    debrief_close.close_flight(s, flight, _CloseProgress(started=False), datetime(2026, 11, 20, 12, tzinfo=UTC))
    s.close()
    f = {f["id"]: f for f in client.get("/api/flights").json()}[past_flight.id]
    assert f["debrief_ready"] is True and f["questions_open"] == 1
    client.put(f"/api/flights/{past_flight.id}/debrief/answers",
               json={"answers": [{"question_id": _Q["id"], "answer": "light"}]})
    f = {f["id"]: f for f in client.get("/api/flights").json()}[past_flight.id]
    assert f["questions_open"] == 0
    assert client.get(f"/api/flights/{past_flight.id}/debrief/summary").json()["debrief"]["questions_answered"] == 1


def test_find_closed_flights_takes_each_window_once(app_db, past_flight, monkeypatch):
    """The flight's window closed (planned arrival + 1 h) inside the lookback:
    picked once; with a report row, never again; outside the lookback, never."""
    from weatherbrief.models import BriefingPackMeta
    from weatherbrief.storage.flights import save_pack_meta
    from weatherbrief.tasks import debrief_close

    s = app_db()
    save_pack_meta(s, BriefingPackMeta(
        flight_id=past_flight.id, fetch_timestamp=past_flight.departure_time - timedelta(hours=2),
        days_out=0, has_gramet=False, has_skewt=False, has_digest=False, assessment="GREEN",
    ))
    s.commit()
    closes = past_flight.departure_time + timedelta(hours=past_flight.flight_duration_hours + 1)
    assert [f.id for f, _ in debrief_close.find_closed_flights(s, closes + timedelta(minutes=10))] == [past_flight.id]
    assert debrief_close.find_closed_flights(s, closes - timedelta(minutes=10)) == []
    assert debrief_close.find_closed_flights(s, closes + debrief_close.LOOKBACK + timedelta(minutes=1)) == []
    s.close()
