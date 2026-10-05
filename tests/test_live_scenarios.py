"""Real flight mornings replayed through the live layer, tick by tick.

Each scenario is a frozen window of public METARs/SPECIs and SIGMETs along a
real route (tests/fixtures/live_scenarios/, built by
scripts/build_live_scenario.py). The replay commits every 10-minute tick
through the production ``commit_live_update``, so the pinned timeline is what
a pilot would have seen. A rule change that moves it shows up here as a diff
to review, not as a silent behaviour change.
"""

import os
from pathlib import Path

import pytest

from live_scenario_replay import IOS_TICKS, ios_tick_files, load_scenario, replay, timeline

LELL_LEMI = "2026-10-02_lell_lemi"


@pytest.fixture(scope="module")
def lell_lemi(tmp_path_factory):
    return replay(load_scenario(LELL_LEMI), tmp_path_factory.mktemp("live") / "u" / "flight")


def _at(ticks, hhmm):
    return next(t for t in ticks if t.at.strftime("%H:%M") == hhmm)


# --- 2026-10-02 LELL → LEMI (dep 08:00Z, 2 h) ------------------------------


EXPECTED_LELL_LEMI = [
    # 05:00 — pack built 30 Sep (no observations): the first tick is the starting point.
    ('05:10', '+', 'alert', 'New SIGMET LECB 2: EMBD TS'),
    ('05:10', '+', 'alert', 'LEVC METAR: CB, TS reported (SPECI)'),
    ('05:30', '+', 'highlight', 'LERI METAR: VFR → MVFR'),
    ('05:30', '+', 'highlight', 'LELC METAR: VFR → MVFR'),
    ('05:40', '+', 'highlight', 'LELC wind: green → amber (crosswind 1 kt RWY 05L, gust 26 kt)'),
    ('06:00', '+', 'alert', 'LEMI METAR: VFR → MVFR'),
    ('06:00', '+', 'highlight', 'LEAL wind: green → amber (crosswind 7 kt RWY 10, gust 25 kt)'),
    ('06:00', '+', 'highlight', 'LEVC METAR: VFR → IFR'),
    ('06:00', '-', 'alert', 'LEVC METAR: CB, VCTS reported (SPECI)'),
    ('06:00', '-', 'highlight', 'LELC METAR: VFR → MVFR (SPECI)'),
    ('06:00', '-', 'highlight', 'LELC wind: green → amber (crosswind 1 kt RWY 05L, gust 26 kt)'),
    ('06:20', '+', 'highlight', 'LEVC METAR: CB no longer reported (SPECI)'),
    ('06:20', '-', 'highlight', 'LEVC METAR: VFR → IFR'),
    ('06:30', '+', 'highlight', 'LEVC METAR: CB now TCU'),
    ('06:30', '-', 'alert', 'LEMI METAR: VFR → MVFR'),
    ('06:30', '-', 'highlight', 'LEAL wind: green → amber (crosswind 7 kt RWY 10, gust 25 kt)'),
    ('06:30', '-', 'highlight', 'LEVC METAR: CB no longer reported (SPECI)'),
    # 06:52 — briefing rebuilt on the day: its own observations are the baseline.
    ('07:00', '+', 'alert', 'LEVC METAR: CB reported'),
    ('07:00', '+', 'highlight', 'LERI METAR: MVFR → VFR'),
    ('07:00', '+', 'highlight', 'LECH METAR: IFR → MVFR'),
    ('07:00', '-', 'alert', 'New SIGMET LECB 2: EMBD TS'),
    ('07:00', '-', 'highlight', 'LERI METAR: VFR → MVFR'),
    ('07:00', '-', 'highlight', 'LEVC METAR: CB now TCU'),
    ('07:10', '+', 'alert', 'LEVC METAR: CB, TS reported (SPECI)'),
    ('07:10', '-', 'alert', 'LEVC METAR: CB reported'),
    ('07:20', '+', 'highlight', 'LEVC METAR: MVFR → IFR (SPECI)'),
    ('07:30', '+', 'alert', 'LEVC METAR: CB reported'),
    ('07:30', '-', 'alert', 'LEVC METAR: CB, VCTS reported (SPECI)'),
    ('07:30', '-', 'highlight', 'LEVC METAR: MVFR → IFR (SPECI)'),
    # 08:00 — departure: LELL no longer matters.
    ('08:00', '+', 'highlight', 'LECH METAR: IFR → VFR'),
    ('08:00', '+', 'highlight', 'LEVC METAR: TCU no longer reported'),
    ('08:00', '-', 'alert', 'LEVC METAR: CB reported'),
    ('08:00', '-', 'highlight', 'LECH METAR: IFR → MVFR'),
    ('08:30', '+', 'alert', 'New SIGMET LECB 3 / LECM 3: EMBD TS (at destination)'),
    ('08:30', '+', 'highlight', 'LECH METAR: IFR → LIFR'),
    ('08:30', '-', 'highlight', 'LECH METAR: IFR → VFR'),
    # LECB 4 (valid 09:00, over LECB 2's area) is LECB 2's reissue, and LECB 2
    # was in the 06:52 briefing: one highlight row, no "no longer active" (#682).
    ('08:40', '+', 'highlight', 'SIGMET LECB 4 replaces 2: EMBD TS'),
    ('09:00', '-', 'highlight', 'LECH METAR: IFR → LIFR'),
    # ~09:15 — VLC passed: LEVC no longer matters.
    ('09:20', '-', 'highlight', 'LEVC METAR: TCU no longer reported'),
    ('10:00', '+', 'highlight', 'LELC METAR: VFR → MVFR'),
    ('10:20', '+', 'highlight', 'LELC METAR: CB, TS reported (SPECI)'),
    ('10:40', '-', 'alert', 'New SIGMET LECB 3 / LECM 3: EMBD TS (at destination)'),
    # LECB 4 ends: the briefing's LECB 2 storm area is gone.
    ('11:00', '+', 'highlight', 'SIGMET LECB 2: EMBD TS no longer active'),
    ('11:00', '-', 'highlight', 'SIGMET LECB 4 replaces 2: EMBD TS'),
]


def test_lell_lemi_timeline(lell_lemi):
    assert timeline(lell_lemi) == EXPECTED_LELL_LEMI


def test_lell_lemi_thunderstorm_under_the_route_alerts_before_departure(lell_lemi):
    """LEVC (under VLC) reported TS/CB all morning at VFR/MVFR: category rules
    alone never said so (§36)."""
    t = _at(lell_lemi, "07:10")
    [c] = [c for c in t.appeared if c.icao == "LEVC"]
    assert (c.kind, c.tier, c.role, c.to_value) == ("metar_convective", "alert", "route", "TS")
    assert c.new_alert is True


def test_lell_lemi_destination_sigmet_is_one_alert(lell_lemi):
    t = _at(lell_lemi, "08:30")
    sig = [c for c in t.appeared if c.source == "SIGMET"]
    assert [(c.tier, c.role, c.message) for c in sig] == [
        ("alert", "destination", "New SIGMET LECB 3 / LECM 3: EMBD TS (at destination)"),
    ]


def test_lell_lemi_departure_silent_after_take_off(lell_lemi):
    """LELL went CB / -SHRA / IFR from 09:30 — after the 08:00 departure."""
    after = [t for t in lell_lemi if t.at.strftime("%H:%M") >= "08:00"]
    assert not [c for t in after for c in t.layer.changes.changes if c.icao == "LELL"]


def test_lell_lemi_first_pack_measured_from_live_start(lell_lemi):
    first, rebuilt = _at(lell_lemi, "05:10"), _at(lell_lemi, "07:00")
    assert first.layer.changes.baseline_source == "live_start"
    assert first.layer.changes.baseline_at.strftime("%H:%M") == "05:00"
    assert rebuilt.layer.changes.baseline_source == "briefing"


def test_lell_lemi_improvements_never_alert(lell_lemi):
    for t in lell_lemi:
        for c in t.layer.changes.changes:
            assert not (c.direction == "better" and c.tier == "alert"), (t.at, c.message)


# --- The frozen derived data still matches the code -------------------------


@pytest.mark.skipif(not os.environ.get("AIRPORTS_DB"), reason="needs the airport database")
def test_lell_lemi_fixture_matches_current_build():
    """Rebuilding `derived` from the raw inputs reproduces the fixture: if the
    corridor, the wind advisory or the observation model changes, rerun
    scripts/build_live_scenario.py and review the timeline diff."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from build_live_scenario import build_derived

    scenario = load_scenario(LELL_LEMI)
    assert build_derived(scenario["inputs"], os.environ["AIRPORTS_DB"]) == scenario["derived"]


# --- The iOS UI-test fixtures are what the server produces -------------------


@pytest.mark.parametrize("name", sorted(IOS_TICKS))
def test_ios_live_fixtures_match_the_server(name, tmp_path):
    """flyfun-weatherUITests/LiveScenarios/ feeds the UI test that screenshots
    each tick. If the rules change, these files must follow: rerun
    scripts/export_live_scenario_ios.py and review the diff."""
    files = ios_tick_files(name, replay(load_scenario(name), tmp_path / "u" / "flight"), IOS_TICKS[name])
    for path, text in files.items():
        assert path.exists(), f"missing {path} — run scripts/export_live_scenario_ios.py"
        assert path.read_text() == text, f"{path.name} is stale — run scripts/export_live_scenario_ios.py"
