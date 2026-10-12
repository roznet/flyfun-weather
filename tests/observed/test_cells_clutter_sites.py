"""The clutter site memory (#768): a flag is acted on only at a recurring site.

A newborn isolated storm and a clutter block look the same on one frame; what
separates them is that clutter comes back to the same ground on other days.
These tests pin that the decision is causal (earlier days only), bounded (the
window), follows the lightning amend (last line per frame wins), and seeds
itself from catalogues that predate the memory.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from weatherbrief.observed.cells import clutter_sites
from weatherbrief.observed.cells.catalogue import catalogue_path, write_catalogue
from weatherbrief.observed.cells.clutter import rescore
from weatherbrief.observed.cells.clutter_eval import track_leaks
from weatherbrief.observed.cells.policy import ClutterPolicy

POLICY = ClutterPolicy()
T = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
# A fictional site in the North Sea, 0.1° grid indices (535, 53).
LAT, LON = 53.5, 5.3
SITE = clutter_sites.site_key(LAT, LON, POLICY.site_precision_deg)


def core(level="suspect", lat=LAT, lon=LON, **extra):
    c = {"id": "core41-x", "tier": "core41", "lat": lat, "lon": lon, "peak_dbz": 55.0}
    if level is not None:
        c["clutter"] = {"level": level, "score": 3.0, "features": {"ring_rain": 0.0, "rain_ratio": 1.0}}
    c.update(extra)
    return c


def flag_on(root, when, cells=None):
    clutter_sites.record(root, when, cells if cells is not None else [core()], POLICY)


def days_for(root, when=T, memory=None):
    memory = memory or clutter_sites.SiteMemory()
    return memory.site_days(root, when, [SITE], POLICY)[SITE]


# --- Keys and the flagged set ---------------------------------------------------


def test_site_key_buckets_nearby_centroids_together():
    assert clutter_sites.site_key(53.51, 5.29, 0.1) == clutter_sites.site_key(53.49, 5.31, 0.1) == SITE
    assert clutter_sites.site_key(53.7, 5.3, 0.1) != SITE


def test_only_flagged_cells_count():
    cells = [core("suspect"), core("confirmed", lat=50.0), core("clear", lat=48.0), core(None, lat=47.0)]
    assert clutter_sites.flagged_sites(cells, POLICY) == sorted(
        {SITE, clutter_sites.site_key(50.0, LON, 0.1)})


# --- Causal and bounded ---------------------------------------------------------


def test_the_frames_own_day_never_counts(tmp_path):
    flag_on(tmp_path, T - timedelta(hours=2))
    assert days_for(tmp_path) == 0


def test_distinct_earlier_days_are_counted_once_each(tmp_path):
    for back in (1, 1, 3):
        flag_on(tmp_path, T - timedelta(days=back, hours=back))
    assert days_for(tmp_path) == 2


def test_the_window_bounds_the_memory(tmp_path):
    flag_on(tmp_path, T - timedelta(days=POLICY.site_window_days))
    flag_on(tmp_path, T - timedelta(days=POLICY.site_window_days + 1))
    assert days_for(tmp_path) == 1


def test_annotate_sets_the_decision_on_flagged_cells_only():
    cells = [core("suspect"), core("clear", lat=48.0), core(None, lat=47.0)]
    clutter_sites.annotate(cells, {SITE: 2}, POLICY)
    assert cells[0]["clutter"]["site_days"] == 2 and cells[0]["clutter"]["suppress"] is True
    assert "suppress" not in cells[1]["clutter"] and "clutter" not in cells[2]
    newborn = [core("confirmed")]
    clutter_sites.annotate(newborn, {SITE: 1}, POLICY)
    assert newborn[0]["clutter"]["suppress"] is False


# --- The amend, and the rebuild -------------------------------------------------


def test_the_last_line_for_a_frame_wins(tmp_path):
    """A late flash clears the flag; the amend's line takes the site back out."""
    when = T - timedelta(days=1)
    flag_on(tmp_path, when)
    flag_on(tmp_path, when, [core("clear")])
    assert days_for(tmp_path) == 0


def test_a_torn_last_line_is_ignored(tmp_path):
    flag_on(tmp_path, T - timedelta(days=1))
    with clutter_sites.day_path(tmp_path, (T - timedelta(days=1)).date()).open("a") as handle:
        handle.write('{"valid_time": "2026-10-19T')
    assert days_for(tmp_path) == 1


def test_a_missing_day_is_rebuilt_from_its_catalogues(tmp_path):
    """The seed path: catalogues from before #768 (any policy) carry the level."""
    when = T - timedelta(days=2)
    write_catalogue(catalogue_path(tmp_path, when),
                    {"policy_version": "cells-3+be0ded40", "valid_time": when.isoformat(),
                     "cells": [core("confirmed")]})
    assert not clutter_sites.day_path(tmp_path, when.date()).exists()
    assert days_for(tmp_path) == 1
    rows = clutter_sites.read_day(clutter_sites.day_path(tmp_path, when.date()))
    assert rows == {when.isoformat(): [SITE]}


def test_a_short_day_file_is_rebuilt(tmp_path):
    """Upgraded mid-day: the file holds the afternoon, the catalogues the morning too."""
    day = T - timedelta(days=1)
    morning, afternoon = day.replace(hour=6), day.replace(hour=15)
    write_catalogue(catalogue_path(tmp_path, morning),
                    {"valid_time": morning.isoformat(), "cells": [core("suspect")]})
    write_catalogue(catalogue_path(tmp_path, afternoon),
                    {"valid_time": afternoon.isoformat(), "cells": [core("clear")]})
    flag_on(tmp_path, afternoon, [core("clear")])
    assert days_for(tmp_path) == 1


def test_z_and_offset_spellings_are_one_frame(tmp_path):
    """A catalogue written as ``…Z`` and a line as ``…+00:00`` are the same frame,
    so a complete day is not mistaken for a short one, nor a short one hidden."""
    day = T - timedelta(days=1)
    morning, afternoon = day.replace(hour=6), day.replace(hour=15)
    for when, level in ((morning, "suspect"), (afternoon, "clear")):
        write_catalogue(catalogue_path(tmp_path, when),
                        {"valid_time": when.isoformat().replace("+00:00", "Z"), "cells": [core(level)]})
    path = clutter_sites.day_path(tmp_path, day.date())
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"valid_time": afternoon.isoformat().replace("+00:00", "Z"), "sites": []}) + "\n")
    flag_on(tmp_path, afternoon, [core("clear")])
    assert len(clutter_sites.read_day(path)) == 1  # one frame, two spellings
    assert days_for(tmp_path) == 1  # still short (morning missing) -> rebuilt
    assert set(clutter_sites.read_day(path)) == {morning.isoformat(), afternoon.isoformat()}


def test_a_settled_day_is_cached_and_an_open_one_is_reread(tmp_path):
    memory = clutter_sites.SiteMemory(settle_after=timedelta(minutes=30))
    yesterday_late = T.replace(hour=23, minute=55) - timedelta(days=1)
    just_after_midnight = T.replace(hour=0, minute=10)
    flag_on(tmp_path, yesterday_late, [core("clear")])
    assert days_for(tmp_path, just_after_midnight, memory) == 0
    # Its lightning amend lands (here: the flag stands) — still re-read.
    flag_on(tmp_path, yesterday_late)
    assert days_for(tmp_path, just_after_midnight, memory) == 1
    # Once settled, later writes to that day are not seen by this process.
    assert days_for(tmp_path, T, memory) == 1
    flag_on(tmp_path, yesterday_late, [core("clear")])
    assert days_for(tmp_path, T, memory) == 1


# --- The lightning amend keeps or drops the decision ----------------------------


def test_rescore_keeps_the_site_count_on_a_cell_still_flagged():
    cell = core("suspect", flashes=0)
    cell["clutter"].update({"site_days": 3, "suppress": True})
    revised = rescore(cell, POLICY)
    assert revised["level"] != "clear"
    assert revised["site_days"] == 3 and revised["suppress"] is True


def test_rescore_drops_the_decision_when_lightning_clears_the_flag():
    cell = core("suspect", flashes=4)
    cell["clutter"].update({"site_days": 3, "suppress": True})
    revised = rescore(cell, POLICY)
    assert revised["level"] == "clear"
    assert "site_days" not in revised and "suppress" not in revised


# --- The harness sees the leak a per-frame check cannot -------------------------


def _row(id_, level="clear", suppress=False, label="unknown"):
    return {"id": id_, "level": level, "suppress": suppress, "label": label}


def test_track_leaks_counts_storms_flagged_and_suppressed_on_other_frames():
    rows = [
        _row("storm-a", "suspect"), _row("storm-a", label="weather"),           # flagged at birth
        _row("storm-b", "suspect", suppress=True), _row("storm-b", label="weather"),
        _row("clutter", "confirmed", suppress=True), _row("clutter", "confirmed", suppress=True),
        _row("rain", label="weather"),
    ]
    tracks = track_leaks(rows)
    assert tracks["weather_tracks"] == 3
    assert tracks["flagged_tracks"] == 3 and tracks["suppressed_tracks"] == 2
    assert tracks["storms_flagged"] == 2 and tracks["storm_frames_flagged"] == 2
    assert tracks["storms_suppressed"] == 1 and tracks["storms_suppressed_ids"] == ["storm-b"]
    json.dumps(tracks)  # goes into the --json report
