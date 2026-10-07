"""The symbolic map's rain/core bands from the cells feed's outlines (#690).

Synthetic route ZZDP (50 N, 0 E) → ZZDS (50 N, 4 E) as in ``test_live_storms``:
due east, ~154 NM; north of the track is left, south is right.
"""

from __future__ import annotations

from datetime import timedelta

from test_live_storms import DEP, TRACK, at_nm, cell, frame, storms_at

from weatherbrief.observed.route_bands import BIN_NM, build_weather_bands, relative_deg


def box(along0, along1, cross0, cross1):
    """An outline ring (lat, lon) over [along0, along1] NM, [cross0, cross1] NM right of track."""
    pts = [at_nm(along0, cross0), at_nm(along1, cross0), at_nm(along1, cross1), at_nm(along0, cross1)]
    return [[lat, lon] for lat, lon in pts] + [[pts[0][0], pts[0][1]]]


def display(outlines, cells=()):
    f = frame(DEP, list(cells))
    f["outlines"] = outlines
    return f


def test_a_rain_area_left_of_track():
    (band,) = build_weather_bands(display({"rain20": [box(40, 60, -20, -10)]}), TRACK)
    assert band.tier == "rain" and band.side == "left"
    assert (band.from_nm, band.to_nm) == (40.0, 60.0)
    assert (band.near_nm, band.far_nm) == (10.0, 20.0)
    # No listed cell inside: the outline's own floor, no motion.
    assert band.peak_dbz == 20.0 and band.motion_rel_deg is None
    assert all(lo == -20.0 and hi == -10.0 for _, lo, hi in band.profile)


def test_an_area_across_the_track_covers_it_and_out_to_the_corridor():
    # 25 NM either side within a 30 NM corridor: across, both edges inside.
    (band,) = build_weather_bands(display({"rain20": [box(20, 80, -25, 25)]}), TRACK)
    assert band.side == "both" and band.near_nm == 0.0
    inner = [b for b in band.profile if 25 < b[0] < 75]
    assert inner and all(lo == -25.0 and hi == 25.0 for _, lo, hi in inner)
    # Wider than the corridor: bins without a boundary inside are covered
    # out to the corridor, never left as holes.
    (wide,) = build_weather_bands(display({"rain20": [box(20, 78, -60, 60)]}), TRACK)
    bins = [b[0] for b in wide.profile]
    assert bins == [(k + 0.5) * BIN_NM for k in range(4, 16)]
    # Out to the corridor (the ends' own edges, densified, stop just inside it).
    assert all(lo <= -29.0 and hi >= 29.0 for _, lo, hi in wide.profile)
    assert all(lo == -30.0 and hi == 30.0 for _, lo, hi in wide.profile[1:-1])


def test_outside_the_corridor_is_dropped():
    assert build_weather_bands(display({"core35": [box(40, 60, 35, 45)]}), TRACK) == []


def test_a_core_takes_its_strength_motion_and_storm_from_the_cells_inside():
    # A core 10 NM right, moving north (toward the track and the left): on an
    # eastbound course that is -90°.
    core = cell("core35-a", "core35", along=50, cross=10, peak=52.0, flashes=4, speed=20.0, toward=0.0)
    inner = cell("core41-a", "core41", along=50, cross=10, peak=55.0, within="core35-a")
    storms = storms_at(DEP - timedelta(minutes=30), [core, inner])
    frame_ = display({"core35": [box(45, 55, 5, 15)]}, [core, inner])
    (band,) = build_weather_bands(frame_, TRACK, storms.storms)
    assert band.tier == "core" and band.side == "right"
    assert band.peak_dbz == 55.0 and band.intensity
    assert band.flashes == 4
    assert band.motion_rel_deg == -90.0 and band.speed_kt == 20.0
    assert band.storm_id == "core35-a"


def test_rain_is_listed_before_cores():
    bands = build_weather_bands(
        display({"core35": [box(10, 12, 2, 4)], "rain20": [box(60, 70, -5, 5)]}), TRACK,
    )
    assert [b.tier for b in bands] == ["rain", "core"]


def test_relative_direction():
    assert relative_deg(90.0, 90.0) == 0.0
    assert relative_deg(180.0, 90.0) == 90.0
    assert relative_deg(0.0, 90.0) == -90.0
    assert relative_deg(270.0, 90.0) == 180.0
    assert relative_deg(10.0, 350.0) == 20.0


def test_an_outline_enclosing_the_route_with_no_edge_in_the_corridor_is_kept():
    # A frontal rain shield far wider than the corridor: no boundary point
    # within 30 NM of the track anywhere, the flight is inside it all along.
    (band,) = build_weather_bands(display({"rain20": [box(-60, 220, -60, 60)]}), TRACK)
    assert band.side == "both" and band.near_nm == 0.0 and band.far_nm == 30.0
    assert band.from_nm == 0.0 and band.to_nm >= TRACK.total_nm - 0.1
    assert all(lo == -30.0 and hi == 30.0 for _, lo, hi in band.profile)
    assert len(band.profile) == int(-(-TRACK.total_nm // BIN_NM))
