"""Holding suspect echoes back from the route products (#696).

The droplet never decides what is clutter — the node measured that and wrote
it on the cell. All the droplet does is choose whether to speak about such a
cell, and **by default it does**: ``WB_CELLS_CLUTTER_SUPPRESS`` is off, so
this ships as annotation and the live layer is byte-for-byte what it was.

One gate (``storms.operational_cells``) covers the storm rows, the §41 alerts
that read them, and the ribbon's weather bands, so the three cannot disagree
about which echoes exist.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from weatherbrief.observed.cells_display import CELLS_CLUTTER_SUPPRESS_ENV
from weatherbrief.observed.route_bands import build_weather_bands
from weatherbrief.observed.storms import operational_cells
from .test_live_storms import DEP, SCHEDULE, TRACK, at_nm, cell, frame, storms_at


@pytest.fixture
def suppressing(monkeypatch):
    monkeypatch.setenv(CELLS_CLUTTER_SUPPRESS_ENV, "1")


def flagged(c, level="confirmed", score=6.0):
    """``cell`` marked by the node, with the reasons it would really carry."""
    c["clutter"] = {
        "level": level,
        "score": score,
        "reasons": [{"feature": "ring_rain", "value": 0.0, "points": 3.0,
                     "note": "no precipitation around the core"},
                    {"feature": "rain_ratio", "value": 1.0, "points": 2.0,
                     "note": "the core is the whole rain area"}],
    }
    return c


def ring_around(along, cross, half=0.25):
    """A closed outline box around a point on the track, in [lat, lon] pairs."""
    lat, lon = at_nm(along, cross)
    return [[lat - half, lon - half], [lat - half, lon + half],
            [lat + half, lon + half], [lat + half, lon - half], [lat - half, lon - half]]


# --- Off by default -----------------------------------------------------------


def test_nothing_is_held_back_by_default():
    cells = [flagged(cell("core41-a", peak=57.0)), cell("core41-b", along=80.0, peak=44.0)]
    assert operational_cells(cells) == cells


def test_a_suspect_echo_still_becomes_a_storm_by_default():
    layer = storms_at(DEP + timedelta(minutes=30),
                      [flagged(cell("core41-a", along=60.0, peak=57.0))])
    assert [s.id for s in layer.storms] == ["core41-a"]
    assert layer.storms[0].peak_dbz == pytest.approx(57.0)


# --- On: storms, alerts and bands all stop speaking about it ------------------


def test_the_gate_drops_both_levels(suppressing):
    cells = [
        flagged(cell("core41-a"), level="confirmed"),
        flagged(cell("core41-b", along=60.0), level="suspect", score=3.0),
        cell("core41-c", along=80.0),
        flagged(cell("core41-d", along=90.0), level="clear", score=0.0),
    ]
    kept = {c["id"] for c in operational_cells(cells)}
    assert kept == {"core41-c", "core41-d"}


def test_a_suspect_echo_is_not_a_storm(suppressing):
    layer = storms_at(DEP + timedelta(minutes=30),
                      [flagged(cell("core41-a", along=60.0, peak=57.0))])
    assert layer.storms == []


def test_a_genuine_storm_beside_a_suspect_one_survives(suppressing):
    layer = storms_at(DEP + timedelta(minutes=30), [
        flagged(cell("core41-phantom", along=60.0, cross=9.0, peak=57.0)),
        cell("core41-real", along=70.0, cross=4.0, peak=48.0),
    ])
    assert [s.id for s in layer.storms] == ["core41-real"]
    assert layer.storms[0].peak_dbz == pytest.approx(48.0)


def test_no_alert_row_mentions_a_suppressed_echo(suppressing):
    """The §41 rows read the storms, so the gate reaches them too.

    A 57 dBZ core 5 NM off track and closing is exactly what the issue saw
    alerted as "Extreme cell"; with the gate on there is no storm to alert on.
    """
    layer = storms_at(DEP + timedelta(minutes=30), [
        flagged(cell("core41-phantom", along=60.0, cross=5.0, peak=57.0,
                     speed=16.0, toward=180.0)),
    ])
    assert layer.storms == []


def test_the_suspect_core_sets_no_band_intensity(suppressing):
    """A mixed outline keeps its band, at the peak of what is left of it."""
    ring = [[49.0, 0.5], [49.0, 1.5], [51.0, 1.5], [51.0, 0.5], [49.0, 0.5]]
    cells = [
        flagged(cell("core41-phantom", along=50.0, peak=57.0)),
        cell("core41-real", along=55.0, peak=43.0),
    ]
    bands = build_weather_bands({"outlines": {"core35": [ring]}, "cells": cells}, TRACK)
    assert len(bands) == 1
    assert bands[0].peak_dbz == pytest.approx(43.0)


def test_a_band_whose_only_member_is_suspect_is_dropped(suppressing):
    """Otherwise the band comes back at the tier floor — the same echo, relabelled."""
    ring = ring_around(50.0, 0.0)
    cells = [flagged(cell("core41-phantom", along=50.0, peak=57.0))]
    frame_data = {"outlines": {"core35": [ring]}, "cells": cells}
    assert build_weather_bands(frame_data, TRACK) == []
    # The rain tier is not assessed, so its band is untouched by design.
    rain = build_weather_bands({"outlines": {"rain20": [ring]}, "cells": cells}, TRACK)
    assert len(rain) == 1


def test_an_empty_core_band_is_untouched_when_nothing_was_suppressed(suppressing):
    """A core outline with no member cell at all is the node's own data, not a
    suppression artefact, and must keep behaving as it did."""
    ring = ring_around(50.0, 0.0)
    bands = build_weather_bands({"outlines": {"core35": [ring]}, "cells": []}, TRACK)
    assert len(bands) == 1


def test_suppression_also_clears_the_storms_own_history(suppressing):
    """A suppressed cell must not reappear through an earlier frame's history."""
    now = DEP + timedelta(minutes=30)
    layer = storms_at(now, [cell("core41-real", along=70.0, cross=4.0, peak=48.0)],
                      earlier=[frame(now - timedelta(minutes=10),
                                     [flagged(cell("core41-phantom", along=60.0, peak=57.0)),
                                      cell("core41-real", along=68.0, cross=4.0, peak=47.0)])])
    assert [s.id for s in layer.storms] == ["core41-real"]


def test_a_suspect_core41_inside_a_genuine_core35_leaves_the_storm_standing(suppressing):
    """The nesting case the review asked to pin (#696 round 1).

    A storm is a `core35` plus the `core41`s inside it (`within`, #688). If the
    node flags only the inner core, the storm must survive on its lower tier
    rather than vanish — and its peak must come from what is left, not from the
    suspect core.
    """
    base = cell("core35-a", tier="core35", along=60.0, peak=42.0)
    inner = flagged(cell("core41-a", along=60.0, peak=57.0, within="core35-a"))
    layer = storms_at(DEP + timedelta(minutes=30), [base, inner])
    assert [s.id for s in layer.storms] == ["core35-a"]
    assert layer.storms[0].cell_ids == ["core35-a"]
    assert layer.storms[0].peak_dbz == pytest.approx(42.0)


def test_a_suspect_core35_takes_its_genuine_core41_with_it(suppressing):
    """The converse: the node flagged the enclosing cell, not the inner one.

    The inner `core41` loses its parent and stands alone, so it is still
    reported — dropping it would hide a 50 dBZ echo because of a verdict passed
    on a different cell.
    """
    base = flagged(cell("core35-b", tier="core35", along=60.0, peak=57.0))
    inner = cell("core41-b", along=60.0, peak=50.0, within="core35-b")
    layer = storms_at(DEP + timedelta(minutes=30), [base, inner])
    assert [s.id for s in layer.storms] == ["core41-b"]
    assert layer.storms[0].peak_dbz == pytest.approx(50.0)
