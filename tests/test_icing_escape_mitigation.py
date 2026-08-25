"""Tests for icing-escape mitigations via the shared vertical-profile solver (#335).

Icing is consumer #2 of the solver (soft wall: crossable at a penalty, warm air below the
freezing level is free, SLD is a hard wall). These mitigations are additive — the icing
grade is unchanged — and they add the maneuver the old single-transition code could not
express (climb on-top; climb-over-then-descend). Advice only.
"""

from __future__ import annotations

from datetime import datetime

import math

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator, _icing_cell_cost
from weatherbrief.models import (
    AdvisoryStatus,
    ElevationPoint,
    ElevationProfile,
    IcingRisk,
    IcingType,
    IcingZone,
    MitigationKind,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

_ICING_DEFAULTS = {
    "terrain_margin_ft": 1000,
    "tight_margin_ft": 2000,
    "icing_altitude_buffer_ft": 2000,
    "extent_pct_amber": 20,
    "extent_pct_red": 15,
}


def _sounding(zones: list[IcingZone], fz_ft: float | None) -> SoundingAnalysis:
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=fz_ft),
        icing_zones=zones,
    )


def _rpa(i: int, distance_nm: float, zones: list[IcingZone], fz_ft: float | None) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i, lat=48.0 + i * 0.1, lon=2.0 + i * 0.1,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=90.0,
        sounding={"gfs": _sounding(zones, fz_ft)},
    )


def _elevation(max_elev_ft: float, total_nm: float = 200.0, n: int = 20) -> ElevationProfile:
    pts = [
        ElevationPoint(distance_nm=i * total_nm / (n - 1), elevation_ft=max_elev_ft,
                       lat=48.0 + i * 0.1, lon=2.0 + i * 0.1)
        for i in range(n)
    ]
    return ElevationProfile(route_name="t", points=pts, max_elevation_ft=max_elev_ft, total_distance_nm=total_nm)


def _ctx(analyses, *, terrain_ft=500.0, cruise_altitude_ft=8000, total_distance_nm=200.0) -> RouteContext:
    return RouteContext(
        analyses=analyses, cross_sections=[],
        elevation=_elevation(terrain_ft, total_nm=total_distance_nm),
        models=["gfs"], cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=18000, total_distance_nm=total_distance_nm,
    )


def _mits(result, model="gfs"):
    per = next(m for m in result.per_model if m.model == model)
    return per.mitigations


def test_icing_cell_cost_only_light_rime_is_soft(  ):
    """Decision 8: ONLY thin/light RIME is finite-crossable; heavier icing is a hard wall.

    Guards against pricing MODERATE/SEVERE (or non-rime light, or SLD) as a cheap
    "climb-through" for a non-FIKI aircraft (#338 review finding 1).
    """
    def snd(zone):
        return SoundingAnalysis(indices=ThermodynamicIndices(freezing_level_ft=3000), icing_zones=[zone])

    light_rime = IcingZone(base_ft=6000, top_ft=8000, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME)
    light_clear = IcingZone(base_ft=6000, top_ft=8000, risk=IcingRisk.LIGHT, icing_type=IcingType.CLEAR)
    moderate = IcingZone(base_ft=6000, top_ft=8000, risk=IcingRisk.MODERATE, icing_type=IcingType.RIME)
    severe = IcingZone(base_ft=6000, top_ft=8000, risk=IcingRisk.SEVERE, icing_type=IcingType.RIME)
    sld = IcingZone(base_ft=6000, top_ft=8000, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME, sld_risk=True)

    # Thin/light rime → the one soft wall (finite, > 0).
    c = _icing_cell_cost(snd(light_rime), 7000)
    assert 0 < c < math.inf
    # Everything heavier → hard wall.
    assert _icing_cell_cost(snd(light_clear), 7000) == math.inf   # non-rime light
    assert _icing_cell_cost(snd(moderate), 7000) == math.inf
    assert _icing_cell_cost(snd(severe), 7000) == math.inf
    assert _icing_cell_cost(snd(sld), 7000) == math.inf           # SLD regardless of type
    # Warm air below the freezing level is always free, even inside a zone.
    assert _icing_cell_cost(snd(severe), 2000) == 0.0
    # Outside every zone → free.
    assert _icing_cell_cost(snd(light_rime), 9000) == 0.0


def test_descend_below_icing_mitigation():
    """Cruise near the icing base, clear air below reachable → 'descend' escape.

    Icing 6000–10000 on the middle points only (ends clear give a free climb/descent);
    cruise 6500 sits low in the layer so the nearest ice-free band is *below* it.
    """
    zone = IcingZone(base_ft=6000, top_ft=10000, risk=IcingRisk.MODERATE)
    analyses = [_rpa(i, i * 20.0, [zone] if 3 <= i <= 6 else [], fz_ft=2000) for i in range(10)]
    result = IcingEscapeEvaluator.evaluate(_ctx(analyses, cruise_altitude_ft=6500), _ICING_DEFAULTS)

    assert result.aggregate_status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)  # icing flagged
    mits = [m for m in _mits(result) if m.addresses == "icing_escape"]
    assert len(mits) == 1
    m = mits[0]
    assert m.kind == MitigationKind.ALTITUDE
    assert m.altitude_ft is not None and m.altitude_ft < 6500  # a descent
    assert "descend" in m.detail.lower()
    assert m.profile is not None and len(m.profile.segments) >= 1  # structured profile attached


def test_climb_on_top_mitigation():
    """Cruise near the icing top → the nearest ice-free band is on top (the upgrade).

    The old icing_escape could only model descending to warm air; here it climbs over.
    """
    zone = IcingZone(base_ft=6000, top_ft=10000, risk=IcingRisk.MODERATE)
    analyses = [_rpa(i, i * 20.0, [zone] if 3 <= i <= 6 else [], fz_ft=2000) for i in range(10)]
    result = IcingEscapeEvaluator.evaluate(_ctx(analyses, cruise_altitude_ft=9000), _ICING_DEFAULTS)

    mits = [m for m in _mits(result) if m.addresses == "icing_escape"]
    assert len(mits) == 1
    m = mits[0]
    assert m.altitude_ft is not None and m.altitude_ft > 9000  # climbed on top
    assert "climb" in m.detail.lower()


def test_no_escape_when_icing_walls_the_column():
    """SLD from the terrain floor upward → no continuous ice-free band → no mitigation.

    A hard wall (SLD) spanning floor→high leaves no reachable escape; the solver returns
    a blockage and no advice is offered (the RED is genuine).
    """
    sld = IcingZone(base_ft=1000, top_ft=13000, risk=IcingRisk.MODERATE, sld_risk=True)
    analyses = [_rpa(i, i * 20.0, [sld], fz_ft=500) for i in range(10)]
    result = IcingEscapeEvaluator.evaluate(_ctx(analyses, cruise_altitude_ft=8000), _ICING_DEFAULTS)

    assert not any(m.addresses == "icing_escape" for m in _mits(result))
