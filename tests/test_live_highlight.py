"""The Observed tab's model-written highlight (#697).

The grounding check carries the safety weight here: nothing else stands between
a model sentence and a cockpit screen, so every rule gets both a case it must
reject and a case it must let through. The rest pins the wiring that keeps the
model off the tick's critical path — carry-forward on unchanged facts, a
refused patch when the layer moved on, and a tick that survives the API
failing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weatherbrief.models.live import LiveGlance, LiveHighlight
from weatherbrief.tasks import live_highlight as lh

SCENARIOS = Path(__file__).resolve().parent.parent / "app/flyfun-weather/flyfun-weatherUITests/LiveScenarios"


def _facts_with(**over) -> dict:
    """A minimal facts block in the real shape, with overrides."""
    f = {
        "now": "09:00Z",
        "route": "LELL to LEMI, 276 NM",
        "flight": "en route, about 120 of 276 NM flown, arrival planned 10:30Z",
        "destination": {"icao": "LEMI", "metar_now": "VFR at 08:50Z", "taf_at_eta": "VFR"},
        "airports_along_route_ahead": {"notable": "none", "other_airports_ahead_all_VFR": 4},
        "sigmets_ahead": "none",
        "rain_ahead": "none within 10 NM of the track",
        "cells_ahead": f"none within 30 NM of the track",
        "changes_since_briefing": {"worse": "none", "improved_count": 0},
    }
    f.update(over)
    return f


# --- Grounding: figures and codes -------------------------------------------


def test_passes_a_grounded_line():
    f = _facts_with()
    assert lh.check_grounding("Quiet route ahead: VFR at both ends, no cells near the track.", f) is None


def test_rejects_an_invented_icao():
    f = _facts_with()
    assert "LFMD" in lh.check_grounding("LFMD showing IFR at arrival.", f)


def test_rejects_an_invented_figure():
    """The one error a pilot cannot catch: a distance or time that is not real."""
    f = _facts_with(cells_ahead={"count": 1, "nearest_to_track": [
        {"peak_dBZ": 48, "at_route_nm": 180, "off_track_nm": 6, "side": "right"}]})
    reason = lh.check_grounding("Cell 48 dBZ at 234 NM, 6 NM right of track.", f)
    assert "234" in reason


def test_accepts_figures_the_facts_carry():
    f = _facts_with(cells_ahead={"count": 1, "nearest_to_track": [
        {"peak_dBZ": 48, "at_route_nm": 180, "off_track_nm": 6, "side": "right"}]})
    assert lh.check_grounding("Cell at 180 NM, 6 NM right of track, peak 48 dBZ.", f) is None


def test_accepts_a_time_from_the_facts():
    f = _facts_with(cells_ahead={"count": 1, "nearest_to_track": [
        {"at_route_nm": 180, "off_track_nm": 4, "side": "left", "abeam_at": "09:47Z"}]})
    assert lh.check_grounding("Cell 4 NM left of track, abeam 09:47Z.", f) is None


# --- Grounding: place binding (#697 lesson 1) -------------------------------
# The failure this check exists for: a route airport's condition moved onto the
# destination. Both codes and both conditions appear *somewhere* in the facts,
# so the plain "is it in the facts" rule passes it.


def test_rejects_a_condition_moved_to_the_wrong_airport():
    f = _facts_with(
        destination={"icao": "LEMI", "metar_now": "VFR at 08:50Z", "taf_at_eta": "VFR"},
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "LIFR at 08:50Z, visibility 800 m"}],
            "other_airports_ahead_all_VFR": 3},
    )
    # Every token is in the facts; the attribution is not.
    reason = lh.check_grounding("LEMI reporting LIFR with 800 m visibility.", f)
    assert reason is not None and "LEMI" in reason and "LIFR" in reason


def test_accepts_the_same_condition_at_its_own_airport():
    f = _facts_with(
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "LIFR at 08:50Z, visibility 800 m"}],
            "other_airports_ahead_all_VFR": 3},
    )
    assert lh.check_grounding("LECH LIFR with 800 m visibility mid-route.", f) is None


def test_accepts_a_paraphrase_of_a_metar_code():
    """The facts say TSRA; the model may say thunderstorm. Same condition."""
    f = _facts_with(
        airports_along_route_ahead={
            "notable": [{"icao": "LECH", "role": "route", "where": "150 NM along, on track",
                         "metar_now": "IFR at 08:50Z, TSRA"}],
            "other_airports_ahead_all_VFR": 3},
    )
    assert lh.check_grounding("Thunderstorms reported at LECH mid-route.", f) is None


def test_skips_binding_on_a_negative_clause():
    """"no cell near LEMI" claims nothing about LEMI's own conditions."""
    f = _facts_with()
    assert lh.check_grounding("No cells within 30 NM of LEMI.", f) is None


# --- Grounding: voice rules -------------------------------------------------


@pytest.mark.parametrize("text", [
    "Thunderstorm mid-route, recommend a diversion.",
    "Destination LEMI VFR, safe to continue.",
    "Cell closing on track — avoid the last 50 NM.",
])
def test_rejects_verdict_words(text):
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 1, "nearest_to_track": [
        {"at_route_nm": 180, "off_track_nm": 4, "side": "left", "lightning_flashes": 12}]})
    reason = lh.check_grounding(text, f)
    assert reason is not None and "verdict" in reason


def test_thunderstorm_needs_lightning_in_the_facts():
    """§41: a radar core is a "cell"; the word thunderstorm needs lightning."""
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 0, "nearest_to_track": [
        {"peak_dBZ": 52, "at_route_nm": 180, "off_track_nm": 4, "side": "left"}]})
    reason = lh.check_grounding("Thunderstorm 4 NM left of track at 180 NM.", f)
    assert reason is not None and "lightning" in reason
    # The same tick, worded as a cell, is fine.
    assert lh.check_grounding("Cell 4 NM left of track at 180 NM, peak 52 dBZ.", f) is None


def test_thunderstorm_allowed_when_a_cell_has_flashes():
    f = _facts_with(cells_ahead={"count": 1, "with_lightning": 1, "nearest_to_track": [
        {"at_route_nm": 180, "off_track_nm": 4, "side": "left", "lightning_flashes": 12}]})
    assert lh.check_grounding("Thunderstorm with lightning 4 NM left of track at 180 NM.", f) is None


def test_rejects_over_length():
    f = _facts_with()
    long = " ".join(["VFR"] * (lh.MAX_WORDS + 1))
    reason = lh.check_grounding(long, f)
    assert reason is not None and "too long" in reason


def test_rejects_empty():
    assert lh.check_grounding("   ", _facts_with()) == "empty"


def test_max_words_is_the_agreed_ceiling():
    """Raised from 30 to 40 by the owner on 2026-10-07 (issue #697)."""
    assert lh.MAX_WORDS == 40


# --- Facts ------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "2026-10-02_lell_lemi_0510.json",
    "2026-10-02_lell_lemi_0710.json",
    "2026-10-02_lell_lemi_0830.json",
    "2026-10-02_lell_lemi_0900.json",
])
def test_facts_from_a_real_layer(name):
    """Every acceptance scenario produces a block with a real route and a
    bounded size — the experiment found the model drops items once it grows."""
    layer = json.loads((SCENARIOS / name).read_text())
    f = lh.facts(layer.get("body", layer))
    assert f["route"].startswith("LELL to LEMI")
    assert f["now"] and f["now"].endswith("Z")
    # Keep it "not too much": the calibrated blocks run 1.1–1.5 kB.
    assert len(json.dumps(f)) < 4000
    for key in ("sigmets_ahead", "rain_ahead", "cells_ahead", "changes_since_briefing"):
        assert key in f


def test_facts_hash_ignores_the_tick_time():
    f = _facts_with()
    assert lh.facts_hash(f) == lh.facts_hash(_facts_with(now="09:10Z"))


def test_facts_hash_ignores_progress_flown_on_the_clock():
    """The gate's whole point. ``flown_nm`` is interpolated from departure, so
    on a 276 NM / 1.5 h plan it advances ~30 NM every 10-minute tick: hashing
    it meant paying for an identical highlight every tick of every flight."""
    at_69 = _facts_with(flight="en route, about 69 of 276 NM flown, arrival planned 10:00Z")
    at_100 = _facts_with(flight="en route, about 100 of 276 NM flown, arrival planned 10:00Z")
    assert lh.facts_hash(at_69) == lh.facts_hash(at_100)


def test_facts_hash_ignores_the_drifting_rain_percentage():
    """Same cause: the denominator is ``route_nm - flown``."""
    a = _facts_with(rain_ahead={"stretches_where_radar_rain_lies_over_the_track_itself_nm": [[10, 40]],
                                "rain_within_10_NM_either_side": "on 45% of the route ahead"})
    b = _facts_with(rain_ahead={"stretches_where_radar_rain_lies_over_the_track_itself_nm": [[10, 40]],
                                "rain_within_10_NM_either_side": "on 52% of the route ahead"})
    assert lh.facts_hash(a) == lh.facts_hash(b)


def test_facts_hash_still_moves_on_a_phase_change():
    """Coarse phase is kept: taking off and landing must regenerate."""
    before = _facts_with(flight="before departure (departure planned 08:00Z)")
    airborne = _facts_with(flight="en route, about 69 of 276 NM flown, arrival planned 10:00Z")
    arrived = _facts_with(flight="arrived (at plan)")
    assert len({lh.facts_hash(before), lh.facts_hash(airborne), lh.facts_hash(arrived)}) == 3


def test_facts_hash_still_moves_when_the_route_length_differs():
    """Only the flown figure is dropped, not the route it is measured against."""
    a = _facts_with(flight="en route, about 69 of 276 NM flown, arrival planned 10:00Z")
    b = _facts_with(flight="en route, about 69 of 300 NM flown, arrival planned 10:00Z")
    assert lh.facts_hash(a) != lh.facts_hash(b)


@pytest.mark.parametrize("changed", [
    {"rain_ahead": {"stretches_where_radar_rain_lies_over_the_track_itself_nm": [[10, 40]]}},
    {"sigmets_ahead": [{"what": "EMBD TS", "id": "LECB 2", "covers_route_nm": [10, 276]}]},
    {"cells_ahead": {"count": 1, "with_lightning": 1, "closing_on_track": 1,
                     "within_10_NM_of_track": 1, "nearest_to_track": []}},
    {"destination": {"icao": "LEMI", "metar_now": "IFR at 09:20Z", "taf_at_eta": "IFR"}},
    {"changes_since_briefing": {"worse": [{"where": "destination LEMI", "what": "VFR to IFR"}],
                                "improved_count": 0}},
])
def test_facts_hash_moves_on_real_weather(changed):
    """What progress actually decides is captured here, and still hashed: a
    cell passing behind you drops out of ``cells_ahead``, a SIGMET falls out
    of ``sigmets_ahead``. Dropping the flown figure loses none of that."""
    assert lh.facts_hash(_facts_with()) != lh.facts_hash(_facts_with(**changed))


def test_hash_fix_on_the_real_tick_that_proved_the_bug():
    """The LELL→LEMI 08:30 tick, replayed 10 minutes later with the weather
    byte-identical: before the fix the hash changed and we paid again."""
    layer = json.loads((SCENARIOS / "2026-10-02_lell_lemi_0830.json").read_text())
    layer = layer.get("body", layer)
    base = lh.facts(layer)
    later = json.loads(json.dumps(layer))
    rate = later["ribbon"]["route_nm"] / 1.5  # NM per hour at plan
    later["ribbon"]["flown_nm"] = later["ribbon"]["flown_nm"] + rate / 6
    moved = lh.facts(later)
    assert base["flight"] != moved["flight"], "the facts the model sees must keep the real figure"
    assert lh.facts_hash(base) == lh.facts_hash(moved)


def test_call_cost_prices_a_real_usage_block():
    """Measured: ~1450 in / ~45 out is about $0.0016 on Haiku 4.5."""
    cost = lh.call_cost({"model": "claude-haiku-4-5", "input_tokens": 1450, "output_tokens": 45})
    assert cost == pytest.approx(0.001675, abs=1e-5)
    assert lh.call_cost(None) is None
    assert lh.call_cost({"input_tokens": 10}) is None


# --- Wiring -----------------------------------------------------------------


class _Layer:
    """Enough of a LiveLayer for the orchestration paths."""

    def __init__(self, *, glance, ribbon=object(), flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00"):
        self.glance = glance
        self.ribbon = ribbon
        self.flight_id = flight_id
        self.pack_timestamp = pack_timestamp

    def model_dump(self, mode="json"):
        return {}


def _glance(as_of=None, highlight=None) -> LiveGlance:
    return LiveGlance(
        as_of=as_of or datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc),
        headline="Observed 09:00Z · as briefed",
        comparison="as_briefed",
        lines=[],
        highlight=highlight,
    )


def _highlight(facts_hash="abc", text="Quiet route ahead.") -> LiveHighlight:
    return LiveHighlight(text=text, model="claude-haiku-4-5", facts_hash=facts_hash,
                         generated_at=datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc))


def test_carry_forward_keeps_the_previous_highlight_on_unchanged_facts(monkeypatch):
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())
    digest = lh.facts_hash(_facts_with())
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash=digest)))
    fresh = _Layer(glance=_glance())
    assert lh.carry_forward(stored, fresh) is True
    assert fresh.glance.highlight is not None
    assert fresh.glance.highlight.facts_hash == digest


def test_carry_forward_drops_a_highlight_whose_facts_moved(monkeypatch):
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())
    stored = _Layer(glance=_glance(highlight=_highlight(facts_hash="stale")))
    fresh = _Layer(glance=_glance())
    assert lh.carry_forward(stored, fresh) is False
    assert fresh.glance.highlight is None


def test_ensure_highlight_reuses_without_calling_the_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    called = []
    monkeypatch.setattr(lh, "generate", lambda *a, **k: called.append(1) or ("x", {}, 1))
    layer = _Layer(glance=_glance(highlight=_highlight(text="carried")))
    out = lh.ensure_highlight("/tmp", layer)
    assert out.outcome == "reused" and out.text == "carried"
    assert not called


def test_ensure_highlight_skips_a_layer_without_a_ribbon(monkeypatch):
    """A pre-#695 layer has no route to lead with — never pay for that call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "generate", lambda *a, **k: pytest.fail("should not call the model"))
    layer = _Layer(glance=_glance(), ribbon=None)
    assert lh.ensure_highlight("/tmp", layer).outcome == "skipped"


def test_ensure_highlight_is_off_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(lh, "generate", lambda *a, **k: pytest.fail("should not call the model"))
    assert lh.ensure_highlight("/tmp", _Layer(glance=_glance())).outcome == "skipped"


def test_kill_switch_stops_generation(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("DISABLE_LIVE_HIGHLIGHT", "1")
    assert lh.highlight_enabled() is False


def test_api_failure_leaves_the_tick_intact(monkeypatch, tmp_path):
    """The acceptance criterion: a failed call must not raise into the tick."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())

    def boom(*a, **k):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(lh, "generate", boom)
    out = lh.ensure_highlight(tmp_path, _Layer(glance=_glance()))
    assert out.outcome == "call_failed" and out.text is None
    # and the attempt is on record, so a run of failures is visible
    log = (tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()
    assert json.loads(log[0])["outcome"] == "call_failed"


def test_a_rejected_line_is_logged_with_its_text(monkeypatch, tmp_path):
    """A false rejection must be reviewable, not silent: the log keeps the
    text and the facts behind it, which is what the calibration period reads."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())
    monkeypatch.setattr(lh, "generate", lambda *a, **k: ("LFMD is IFR.", {"model": "claude-haiku-4-5"}, 900))
    out = lh.ensure_highlight(tmp_path, _Layer(glance=_glance()))
    assert out.outcome == "rejected"
    rec = json.loads((tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()[0])
    assert rec["text"] == "LFMD is IFR."
    assert rec["facts"]["route"].startswith("LELL")
    assert "LFMD" in rec["reason"]


def test_a_rejected_facts_state_is_retried_once_then_given_up(monkeypatch, tmp_path):
    """The tick retries any flight with no stored highlight and a rejection
    stores nothing, so uncapped this bought a rejected sentence every tick for
    the whole window. One retry, because the model is stochastic and a bad
    draw deserves a second chance; then stop paying."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())
    calls = []

    def bad(*a, **k):
        calls.append(1)
        return "LFMD is IFR.", {"model": "claude-haiku-4-5", "input_tokens": 1000, "output_tokens": 10}, 900

    monkeypatch.setattr(lh, "generate", bad)
    layer = _Layer(glance=_glance())

    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert len(calls) == 2
    # Third tick on the same facts: no further billed call.
    out = lh.ensure_highlight(tmp_path, layer)
    assert out.outcome == "skipped" and "rejected attempts" in out.reason
    assert len(calls) == 2
    # ...but the skip is still on record, so the frequency stays visible.
    records = [json.loads(l) for l in (tmp_path / lh.LIVE_HIGHLIGHT_LOG).read_text().splitlines()]
    assert [r["outcome"] for r in records] == ["rejected", "rejected", "skipped_rejected"]
    # and the marker is cheap — no facts block repeated every tick
    assert "facts" not in records[-1]


def test_a_different_facts_state_generates_again(monkeypatch, tmp_path):
    """The cap is per facts state, not per flight: when the weather moves, the
    flight gets a fresh go."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "generate",
                        lambda *a, **k: ("LFMD is IFR.", {"model": "claude-haiku-4-5"}, 900))
    layer = _Layer(glance=_glance())
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"
    assert lh.ensure_highlight(tmp_path, layer).outcome == "skipped"
    # weather moves -> new facts hash -> allowed to try again
    monkeypatch.setattr(lh, "facts_for",
                        lambda layer: _facts_with(sigmets_ahead=[{"what": "EMBD TS", "id": "LECB 2"}]))
    assert lh.ensure_highlight(tmp_path, layer).outcome == "rejected"


def test_a_failed_call_does_not_count_against_the_retry_cap(monkeypatch, tmp_path):
    """A timeout costs nothing and is right to retry; only billed rejections
    count."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(lh, "facts_for", lambda layer: _facts_with())

    def boom(*a, **k):
        raise TimeoutError("read timeout")

    monkeypatch.setattr(lh, "generate", boom)
    layer = _Layer(glance=_glance())
    for _ in range(5):
        assert lh.ensure_highlight(tmp_path, layer).outcome == "call_failed"
    assert lh.rejected_attempts(tmp_path, lh.facts_hash(_facts_with())) == 0


def test_charge_is_skipped_without_a_session():
    """Called from a worker thread with db=None — must be a no-op, not a crash."""
    lh.charge_highlight(None, "u1", "f1", {"model": "claude-haiku-4-5", "input_tokens": 1000})
    lh.charge_highlight(None, None, "f1", None)


# --- The patch write --------------------------------------------------------


def test_patch_refuses_when_a_newer_tick_committed(tmp_path):
    """The highlight was written from the older facts; attaching it to a newer
    glance is the stale-text bug the facts hash exists to prevent."""
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import LIVE_FILE, patch_highlight

    as_of = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    stored = LiveLayer(
        flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00", pack_dir_name="p",
        glance=_glance(as_of=as_of + timedelta(minutes=10)),
    )
    (tmp_path / LIVE_FILE).write_text(stored.model_dump_json())
    assert patch_highlight(tmp_path, _highlight(), pack_timestamp=stored.pack_timestamp, as_of=as_of) is False


def test_patch_writes_and_round_trips(tmp_path):
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import LIVE_FILE, load_live, patch_highlight

    as_of = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    stored = LiveLayer(
        flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00", pack_dir_name="p",
        glance=_glance(as_of=as_of),
    )
    (tmp_path / LIVE_FILE).write_text(stored.model_dump_json())
    assert patch_highlight(tmp_path, _highlight(text="Cell closing at 180 NM."),
                           pack_timestamp=stored.pack_timestamp, as_of=as_of) is True
    back = load_live(tmp_path)
    assert back.glance.highlight.text == "Cell closing at 180 NM."


def test_patch_refuses_a_different_pack(tmp_path):
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import LIVE_FILE, patch_highlight

    as_of = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    stored = LiveLayer(flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00",
                       pack_dir_name="p", glance=_glance(as_of=as_of))
    (tmp_path / LIVE_FILE).write_text(stored.model_dump_json())
    assert patch_highlight(tmp_path, _highlight(), pack_timestamp="2026-10-02T06:00:00+00:00", as_of=as_of) is False


def test_highlight_log_is_removed_with_the_layer(tmp_path):
    """Otherwise a deleted flight leaves its highlights (and their facts) behind."""
    from weatherbrief.tasks.live_layer import LIVE_HIGHLIGHT_LOG, remove_live

    (tmp_path / LIVE_HIGHLIGHT_LOG).write_text("{}\n")
    remove_live(tmp_path)
    assert not (tmp_path / LIVE_HIGHLIGHT_LOG).exists()


def test_highlight_stays_out_of_the_agent_block():
    """Not displayed anywhere yet (owner, 2026-10-07) — an agent quoting it
    would be a user-facing surface by the back door."""
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import summarize_live

    layer = LiveLayer(
        flight_id="f1", pack_timestamp="2026-10-02T05:00:00+00:00", pack_dir_name="p",
        glance=_glance(highlight=_highlight(text="SECRET HIGHLIGHT")),
    )
    blob = json.dumps(summarize_live(layer, {}), default=str)
    assert "SECRET HIGHLIGHT" not in blob
    assert "Observed 09:00Z" in blob  # the nutshell headline is exposed, as before
