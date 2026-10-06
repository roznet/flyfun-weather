"""Summary clauses on synthetic fields (#689): tops never "clear" under a
convective echo, and the radar peak says where it is."""

from __future__ import annotations

from datetime import datetime, timezone

from weatherbrief.models.observed import (
    ObservedAnnulus,
    ObservedConditions,
    ObservedField,
    ObservedStationRef,
    ObservedStationSamples,
    ObservedTopsAnnulus,
    ObservedTopsField,
    ObservedTopsStationSamples,
)
from weatherbrief.observed.summary import build_summary

T = datetime(2026, 10, 6, 14, 10, tzinfo=timezone.utc)
STATIONS = [
    ObservedStationRef(id="ZZDP", name="ZZDP", lat=41.2, lon=-8.7, enroute_distance_nm=0.0),
    ObservedStationRef(id="r1", lat=40.5, lon=-8.9, enroute_distance_nm=45.0),
    ObservedStationRef(id="ZZDS", name="ZZDS", lat=38.8, lon=-9.1, enroute_distance_nm=150.0),
]


def _radar(peaks: dict[str, float | None], *, at=None):
    """Full coverage; ``peaks`` = station -> max dBZ in the 20 NM disc."""
    def annulus(v):
        detected = 50 if v is not None else 0
        return ObservedAnnulus(
            radius_nm=20.0, total_px=1000, valid_px=1000, undetect_px=1000 - detected,
            detected_px=detected, max_value=v,
            **((at or {}) if v is not None else {}),
        )
    return ObservedField(
        source="opera", quantity="DBZH", valid_time=T, age_minutes=5.0,
        stations=[ObservedStationSamples(station_id=s, annuli=[annulus(v)]) for s, v in peaks.items()],
    )


def _tops(tops: dict[str, float | None]):
    """``tops`` = station -> highest FL, None = no cloud found (clear)."""
    def annulus(fl):
        detected = 40 if fl is not None else 0
        return ObservedTopsAnnulus(
            radius_nm=20.0, total_px=1000, valid_px=1000, undetect_px=1000 - detected,
            detected_px=detected, highest_fl=fl,
        )
    return ObservedTopsField(
        source="ctth", quantity="CTH", valid_time=T, age_minutes=10.0,
        stations=[ObservedTopsStationSamples(station_id=s, annuli=[annulus(v)]) for s, v in tops.items()],
    )


def _conditions(radar=None, tops=None):
    return ObservedConditions(
        computed_at=T, corridor_nm=20.0, radii_nm=[20.0], stations=STATIONS,
        reflectivity=radar, cloud_tops=tops,
    )


def _tops_line(conditions):
    return next(line for line in build_summary(conditions) if line.startswith("Cloud tops"))


def test_tops_say_unavailable_not_clear_under_a_convective_echo():
    """LPPR→LPPT 14:11Z: no top anywhere, 49 dBZ within 20 NM."""
    line = _tops_line(_conditions(
        _radar({"ZZDP": 49.0, "r1": 38.0, "ZZDS": 40.0}),
        _tops({"ZZDP": None, "r1": None, "ZZDS": None}),
    ))
    assert line.startswith("Cloud tops unavailable")
    assert "clear" not in line


def test_tops_partly_contradicted_never_claim_the_whole_corridor():
    line = _tops_line(_conditions(
        _radar({"ZZDP": 49.0, "r1": None, "ZZDS": 12.0}),
        _tops({"ZZDP": None, "r1": None, "ZZDS": None}),
    ))
    assert line == (
        "Cloud tops: none found at 2 of 3 points, unavailable at 1 of 3 points "
        "(radar echo of 35 dBZ or more, no cloud top found) (observed 14:10Z)."
    )


def test_tops_with_a_top_and_a_contradicted_point():
    line = _tops_line(_conditions(
        _radar({"ZZDP": 49.0, "r1": None, "ZZDS": None}),
        _tops({"ZZDP": None, "r1": 250.0, "ZZDS": None}),
    ))
    assert line.startswith("Cloud tops to FL250 near 45 NM along the route")
    assert "clear at 1 of 3 points" in line and "unavailable at 1 of 3 points" in line


def test_tops_clear_where_the_radar_agrees():
    for radar in (None, _radar({"ZZDP": 30.0, "r1": None, "ZZDS": None})):
        line = _tops_line(_conditions(radar, _tops({"ZZDP": None, "r1": None, "ZZDS": None})))
        assert line == "Cloud tops: clear over the whole corridor (observed 14:10Z)."


def test_radar_peak_says_where_it_is():
    """#689: "peak 49 dBZ 8 NM NE of LPPR", not "within 20 NM of LPPR"."""
    lines = build_summary(_conditions(
        _radar({"ZZDP": 49.0, "r1": 20.5, "ZZDS": None}, at={"max_at_nm": 8.3, "max_bearing_deg": 40.0}),
    ))
    assert "Radar: very heavy echo, peak 49 dBZ 8 NM NE of ZZDP (observed 14:10Z)." in lines


def test_radar_peak_without_a_position_keeps_the_disc_wording():
    lines = build_summary(_conditions(_radar({"ZZDP": 49.0})))
    assert any("peak 49 dBZ within 20 NM of ZZDP" in line for line in lines)


def test_tops_mixed_coverage_and_contradiction():
    """A point the satellite could not see is left out; a contradicted one is
    unavailable; the rest say what they saw."""
    tops = _tops({"ZZDP": None, "r1": None, "ZZDS": None})
    blind = tops.stations[2].annuli[0]
    tops.stations[2].annuli[0] = blind.model_copy(update={"valid_px": 0, "undetect_px": 0, "nodata_px": 1000})
    line = _tops_line(_conditions(_radar({"ZZDP": 49.0, "r1": None, "ZZDS": 50.0}), tops))
    assert line == (
        "Cloud tops: none found at 1 of 2 points, unavailable at 1 of 2 points "
        "(radar echo of 35 dBZ or more, no cloud top found) (observed 14:10Z)."
    )
