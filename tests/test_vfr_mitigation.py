"""Tests for VFR Feasibility mitigations (issue #328).

A mitigation surfaces a decision that would improve a flagged sub-issue
(fly lower to clear cruise IMC, reposition the climb/descent around a corridor
deck). Mitigations are **advice only** — they never change the advisory grade.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.vfr_feasibility import VFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    ElevationPoint,
    ElevationProfile,
    EnhancedCloudLayer,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

# Default VFR params (mirror the catalog defaults).
_VFR_DEFAULTS = {
    "cloud_clearance_ft": 1000,
    "extent_pct_amber": 15,
    "extent_pct_red": 30,
    "terminal_corridor_nm": 5,
    "mitigation_min_base_agl_ft": 3000,
    "mitigation_max_reposition_nm": 25,
}

# The along-route tests build 200nm routes on a 20nm grid, so their VMC break
# falls 40-60nm out — past the 25nm reposition cap. Tests that exercise the
# split / under-deck / beyond-grade-corridor logic (not the cap) raise it so the
# cap doesn't suppress the mitigation under test; the cap has its own test.
_NO_REPOSITION_CAP = {"mitigation_max_reposition_nm": 1000}


# ---------------------------------------------------------------------------
# Builders (self-contained — the advisory conftest fixtures live in a
# different test package and are not visible here).
# ---------------------------------------------------------------------------

def _sounding(layers: list[EnhancedCloudLayer] | None = None) -> SoundingAnalysis:
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        cloud_layers=layers or [],
    )


def _rpa(
    point_index: int,
    distance_nm: float,
    layers_by_model: dict[str, list[EnhancedCloudLayer]],
) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=point_index,
        lat=48.0 + point_index * 0.1,
        lon=2.0 + point_index * 0.1,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=90.0,
        sounding={m: _sounding(layers) for m, layers in layers_by_model.items()},
    )


def _elevation(max_elev_ft: float = 500.0, n_points: int = 20, total_nm: float = 200.0) -> ElevationProfile:
    points = [
        ElevationPoint(
            distance_nm=i * total_nm / (n_points - 1),
            elevation_ft=max_elev_ft,
            lat=48.0 + i * 0.1,
            lon=2.0 + i * 0.1,
        )
        for i in range(n_points)
    ]
    return ElevationProfile(
        route_name="test",
        points=points,
        max_elevation_ft=max_elev_ft,
        total_distance_nm=total_nm,
    )


def _ctx(
    analyses: list[RoutePointAnalysis],
    *,
    elevation: ElevationProfile | None = None,
    models: tuple[str, ...] = ("gfs",),
    cruise_altitude_ft: int = 8000,
    total_distance_nm: float = 200.0,
) -> RouteContext:
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=elevation if elevation is not None else _elevation(total_nm=total_distance_nm),
        models=list(models),
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=18000,
        total_distance_nm=total_distance_nm,
    )


def _mitigations(result: RouteAdvisoryResult, model: str = "gfs") -> list[Mitigation]:
    per = next(m for m in result.per_model if m.model == model)
    return per.mitigations


# ---------------------------------------------------------------------------
# Vertical mitigation (addresses cruise_imc)
# ---------------------------------------------------------------------------

def test_vertical_happy_path():
    """Cruise inside an OVC deck, clear band below well above terrain.

    One ALTITUDE mitigation reporting the highest clear altitude, GREEN, while
    the advisory itself stays RED (the grade is independent of the mitigation).
    """
    # OVC 7000–12000 contains cruise (8000) at every point → IMC everywhere.
    # Highest clear altitude = base 7000 − 1000 clearance = 6000ft (terrain 500).
    deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(_ctx(analyses), _VFR_DEFAULTS)

    assert result.aggregate_status == AdvisoryStatus.RED  # grade unchanged
    mits = _mitigations(result)
    alt = [m for m in mits if m.kind == MitigationKind.ALTITUDE]
    assert len(alt) == 1
    m = alt[0]
    assert m.addresses == "cruise_imc"
    assert m.altitude_ft == 6000
    assert m.mitigated_status == AdvisoryStatus.GREEN  # axis status, not overall
    assert m.detail  # localized phrasing populated


def test_vertical_blocked_by_terrain():
    """The only clear band sits below the terrain floor → no vertical mitigation."""
    # OVC 5000–12000 contains cruise; terrain 5500 → floor 6500, so every
    # candidate altitude down to the floor is still inside the deck.
    deck = EnhancedCloudLayer(base_ft=5000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses, elevation=_elevation(max_elev_ft=5500)), _VFR_DEFAULTS
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    assert not any(m.kind == MitigationKind.ALTITUDE for m in _mitigations(result))


def test_vertical_amber_band_uses_marginal_phrasing():
    """When the best reachable band is only AMBER (cruise RED, no clear band above
    the floor), the detail must NOT claim 'VMC available' — it says 'marginal' to
    match the AMBER axis status."""
    # OVC 7000–12000 → cruise (8000) IMC/RED. Terrain 3500 → floor 6500 (unified
    # terrain + mitigation_min_base_agl_ft, #335): the clear band (≤6000) is below
    # the floor, but the marginal band 6500 (within 1000ft of base 7000) clears it →
    # best candidate is AMBER at 6500.
    deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses, elevation=_elevation(max_elev_ft=3500)), _VFR_DEFAULTS
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    alt = [m for m in _mitigations(result) if m.kind == MitigationKind.ALTITUDE]
    assert len(alt) == 1
    m = alt[0]
    assert m.mitigated_status == AdvisoryStatus.AMBER
    assert m.altitude_ft == 6500
    assert "marginal" in m.detail.lower()
    assert "VMC available" not in m.detail  # the GREEN-only phrasing


def test_vertical_staircase_deck_scans_for_flat_altitude():
    """A deck whose base VARIES along the route → the cruise_imc tip must scan for the
    best flat altitude, not just re-check the profile's top band (#338 review finding 1).

    First half deck base 7000, second half base 8000, cruise 10000 (RED everywhere). The
    min-cost profile staircases (≈6000 then ≈7000), so its top band is 7000 — which is
    *inside* the first-half deck when flown flat. A single re-check at 7000 would drop the
    tip; scanning finds 6000, clear the whole way (GREEN).
    """
    first = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    second = EnhancedCloudLayer(base_ft=8000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [first if i < 5 else second]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses, cruise_altitude_ft=10000), _VFR_DEFAULTS
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    alt = [m for m in _mitigations(result) if m.kind == MitigationKind.ALTITUDE]
    assert len(alt) == 1
    m = alt[0]
    assert m.altitude_ft == 6000  # highest flat altitude clear over the WHOLE route
    assert m.mitigated_status == AdvisoryStatus.GREEN


def test_vertical_tight_terrain_gap_suppresses_mitigation():
    """Under the unified conservative floor (terrain + 3000, #335), a marginal band
    that doesn't clear the floor yields NO 'fly lower' tip — scud-running into a tight
    terrain-to-deck gap is not recommended, even though the old 1000ft-floor scan would
    have offered it.

    OVC 7000–12000, terrain 5200 → floor 8200 sits inside the deck; the only
    non-IMC air (marginal 6000–7000) is below the floor → the RED is genuine, no tip.
    """
    deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses, elevation=_elevation(max_elev_ft=5200)), _VFR_DEFAULTS
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    assert not any(m.kind == MitigationKind.ALTITUDE for m in _mitigations(result))


# ---------------------------------------------------------------------------
# Along-route mitigation (addresses climb_deck / descent_deck)
# ---------------------------------------------------------------------------

def test_along_route_climb_happy_path():
    """OVC deck below cruise over the corridor points nearest departure,
    clear beyond → one ROUTE_POSITION climb mitigation."""
    # base 4000 over 500ft terrain = 3500ft AGL, clears the mitigation base gate.
    deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    # Wide corridor (60nm) so points 0,20,40,60 are in the climb corridor.
    # Blocked at 0 & 20nm, clear at 40 & 60nm → climb after ~40nm.
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (0, 1) else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # OVC corridor deck
    climb = [m for m in _mitigations(result) if m.addresses == "climb_deck"]
    assert len(climb) == 1
    m = climb[0]
    assert m.kind == MitigationKind.ROUTE_POSITION
    assert m.distance_nm == 40.0
    assert m.reference == "departure"
    assert m.mitigated_status == AdvisoryStatus.GREEN
    # Cruise is clear (deck top below cruise) → no vertical mitigation.
    assert not any(m.kind == MitigationKind.ALTITUDE for m in _mitigations(result))


def test_along_route_descent_happy_path():
    """Symmetric to climb: deck near arrival, clear before → descent mitigation."""
    # base 4000 over 500ft terrain = 3500ft AGL, clears the mitigation base gate.
    deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    # Blocked at 160 & 180nm (near arrival), clear at 140nm → descend before
    # ~60nm from arrival (200 − 140).
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (8, 9) else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    descent = [m for m in _mitigations(result) if m.addresses == "descent_deck"]
    assert len(descent) == 1
    m = descent[0]
    assert m.kind == MitigationKind.ROUTE_POSITION
    assert m.distance_nm == 60.0
    assert m.reference == "arrival"
    assert m.mitigated_status == AdvisoryStatus.GREEN


def test_along_route_terminal_deck_past_midpoint_emits():
    """A clean terminal deck extending PAST the route midpoint still earns its tip when the
    break is within the reposition cap — the half-route knife-edge is gone (#342 Bug B).

    Arrival deck from 100nm inward (past the 100nm midpoint) on a 200nm route; clear before.
    The profile stays on top until it must descend under the deck ~80nm out, so the descent
    completes 120nm before arrival — ``before`` (120) > ``total/2`` (100). The old
    ``before <= total/2`` gate dropped this correct tip on exactly that kind of overshoot,
    letting only a spurious departure tip survive. ``clean_terminal`` already guarantees the
    interior is clear, so with the split removed the tip is offered whenever the break is
    within ``max_reposition_nm`` (raised here to admit the 120nm break the deck's size
    dictates; the default cap has its own test).
    """
    deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i >= 5 else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses),
        {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, "mitigation_max_reposition_nm": 150},
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # terminal OVC deck
    descent = [m for m in _mitigations(result) if m.addresses == "descent_deck"]
    assert len(descent) == 1
    assert descent[0].distance_nm == 120.0  # before = 200 − 80, past the 100nm midpoint
    assert descent[0].reference == "arrival"
    assert descent[0].mitigated_status == AdvisoryStatus.GREEN


def test_along_route_single_point_terminal_deck_suppressed():
    """A lone terminal-field cloud at cruise → NO spurious corridor tip (#342 Bug A).

    A thin cloud sitting at the planned cruise altitude over the departure field ONLY
    makes the min-cost profile start below cruise (as every climb-out does) and climb
    up once past it — which the profile-shape gate alone reads as a ``climb_deck``. But
    nothing forced the flight low beyond the field's own cloud, so the tip is noise. The
    real-deck gate (≥2 route points / ≥15nm of deck) suppresses it; a genuine multi-point
    departure deck (the happy-path test) still emits.
    """
    # Thin OVC 7900–8100 straddles cruise (8000) at the departure field (nm 0) only.
    field_cloud = EnhancedCloudLayer(base_ft=7900, top_ft=8100, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [field_cloud] if i == 0 else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )

    # A lone field cloud is not a RED-worthy deck — the grade stays GREEN (10% minor
    # clearance). Pinned explicitly so a future grading-threshold change can't silently
    # alter what this test exercises (per PR #343 review).
    assert result.aggregate_status == AdvisoryStatus.GREEN
    # The profile is forced below cruise at nm 0 and climbs after — absent the real-deck
    # gate this would emit a spurious "climb to cruise after ~20nm". It must not.
    assert not any(m.addresses == "climb_deck" for m in _mitigations(result))


def test_along_route_descent_clear_beyond_grade_corridor():
    """The mitigation looks past the terminal grade corridor to the midpoint.

    Regression for the GFS/EGTF case: a narrow ``terminal_corridor_nm`` (5nm)
    grades the terminal deck RED, but the clear point that threads it sits well
    beyond 5nm. The mitigation must still find it (its search runs to the route
    midpoint, not the grade corridor).
    """
    # Route 180nm (points 0..180 every 20nm), midpoint 90nm. Deck near arrival at
    # 160 & 180nm; clear at 140nm and inland. Grade corridor only 5nm → grades on
    # the d=180 point alone, but the VMC break is 40nm out.
    deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (8, 9) else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses, total_distance_nm=180.0),
        {**_VFR_DEFAULTS, "terminal_corridor_nm": 5, **_NO_REPOSITION_CAP},
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # terminal OVC deck
    descent = [m for m in _mitigations(result) if m.addresses == "descent_deck"]
    assert len(descent) == 1
    assert descent[0].distance_nm == 40.0  # descend before ~40nm (180 − 140)
    assert descent[0].reference == "arrival"
    assert descent[0].mitigated_status == AdvisoryStatus.GREEN


def test_along_route_reposition_distance_capped():
    """A VMC break beyond ``mitigation_max_reposition_nm`` → no mitigation.

    The deck near departure runs 0–40nm with clear air at 60nm. Flying under it
    for 60nm is not a useful "climb after" maneuver, so the default 25nm cap
    suppresses the tip; raising the cap past 60nm re-enables it.
    """
    deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    # Blocked 0..40nm (i=0,1,2), clear from 60nm on → climb break is 60nm out.
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (0, 1, 2) else []}) for i in range(10)]

    capped = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60}  # default 25nm cap
    )
    assert capped.aggregate_status == AdvisoryStatus.RED  # deck still grades RED
    assert not any(m.addresses == "climb_deck" for m in _mitigations(capped))

    # Raise the cap above the 60nm break → the mitigation returns.
    uncapped = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, "mitigation_max_reposition_nm": 80}
    )
    climb = [m for m in _mitigations(uncapped) if m.addresses == "climb_deck"]
    assert len(climb) == 1
    assert climb[0].distance_nm == 60.0


def test_along_route_low_base_not_reachable():
    """A clear/blocked split exists, but the blocked-stretch deck base is too low
    to fly under (1500ft base over 500ft terrain = 1000ft AGL < 3000 default) →
    no along-route mitigation: the clear air beyond is unreachable VFR.

    Tunable: lowering the threshold below the deck's AGL base re-enables it.
    """
    low_deck = EnhancedCloudLayer(base_ft=1500, top_ft=5000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [low_deck] if i in (0, 1) else []}) for i in range(10)]

    blocked = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )
    assert blocked.aggregate_status == AdvisoryStatus.RED  # deck still grades RED
    assert not any(m.addresses == "climb_deck" for m in _mitigations(blocked))

    # With the gate lowered below the deck's 1000ft AGL base, the mitigation returns.
    relaxed = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses),
        {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, "mitigation_min_base_agl_ft": 500,
         **_NO_REPOSITION_CAP},
    )
    assert any(m.addresses == "climb_deck" for m in _mitigations(relaxed))


# ---------------------------------------------------------------------------
# Co-occurrence
# ---------------------------------------------------------------------------

def test_cruise_imc_suppresses_corridor_mitigation():
    """Cruise IMC en route → corridor mitigation suppressed, only the vertical.

    When cruise is itself IMC, "climb to cruise after ~X nm" is useless (you'd
    re-enter cloud at cruise). The corridor mitigation is gated on the en-route
    axis being GREEN, so only the vertical "fly lower" mitigation is offered —
    the two are mutually exclusive by construction.
    """
    cruise_deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    # Low deck base 4000 over 500ft terrain = 3500ft AGL, clears the base gate.
    low_deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    # Every point has the cruise-level deck (IMC at cruise → enroute RED); points
    # 0,20 also carry a low corridor deck that WOULD support a climb mitigation
    # were cruise clear.
    analyses = [
        _rpa(i, i * 20.0, {"gfs": [cruise_deck, low_deck] if i in (0, 1) else [cruise_deck]})
        for i in range(10)
    ]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # worst(...) unchanged
    addresses = {m.addresses for m in _mitigations(result)}
    assert "cruise_imc" in addresses       # the relevant lever: fly lower
    assert "climb_deck" not in addresses   # suppressed — cruise is IMC anyway


def test_interior_deck_suppresses_corridor_mitigation():
    """An INTERIOR deck (away from both terminals) must NOT yield a corridor tip (#338).

    The profile reaches cruise, dips under a mid-route deck, climbs back to cruise, then
    descends for the arrival deck — a genuine climb/dip/climb interior excursion. A thin
    at-cruise interior deck (8000–8500) is what actually triggers the dip: a departure
    deck instead traps the profile low for the whole route (one contiguous below-cruise
    run, no interior dip), which is a distance-from-airport concern the reposition cap
    handles, not the interior-dip guard. Here the guard fires (``clean_terminal`` False)
    and suppresses the arrival ``descent_deck`` tip that would otherwise be misleading —
    the flight has to descend at the interior deck too, not just at arrival.
    """
    # Thin at-cruise interior deck → the profile dips just under it, then climbs back.
    interior = EnhancedCloudLayer(base_ft=8000, top_ft=8500, coverage=CloudCoverage.OVC)
    # Arrival deck below cruise → a descent_deck candidate absent the interior dip.
    arr_deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    analyses = [
        _rpa(i, i * 20.0, {"gfs": [interior] if i in (3, 4) else ([arr_deck] if i in (8, 9) else [])})
        for i in range(10)
    ]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # arrival OVC deck
    addresses = {m.addresses for m in _mitigations(result)}
    assert "climb_deck" not in addresses      # departure at cruise → no climb candidate
    assert "descent_deck" not in addresses    # suppressed — interior dip, must descend twice


def test_interior_deck_suppresses_climb_deck():
    """Climb-side companion to the descent case above: keep BOTH directions of the
    interior-dip guard (#338) covered (per PR #343 review).

    A GENUINE 2-point departure deck yields a real ``climb_deck`` candidate (the departure
    happy-path emits it), and an interior at-cruise deck must still suppress it. The interior
    dip is the ONLY difference from that happy-path fixture, so the assertion is non-vacuous —
    unlike the departure-at-cruise case above where no climb transition ever exists.
    """
    # Real 2-point departure deck below cruise → the profile flies under it and climbs to
    # cruise: a climb_deck candidate that passes the real-deck gate.
    dep_deck = EnhancedCloudLayer(base_ft=4000, top_ft=5500, coverage=CloudCoverage.OVC)
    # Thin at-cruise interior deck → forces a dip below cruise mid-route → clean_terminal False.
    interior = EnhancedCloudLayer(base_ft=8000, top_ft=8500, coverage=CloudCoverage.OVC)
    analyses = [
        _rpa(i, i * 20.0, {"gfs": [dep_deck] if i in (0, 1) else ([interior] if i in (5, 6) else [])})
        for i in range(10)
    ]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60, **_NO_REPOSITION_CAP}
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # departure OVC deck
    # climb_deck candidate exists (happy-path fixture emits it); the interior dip suppresses it.
    assert "climb_deck" not in {m.addresses for m in _mitigations(result)}


# ---------------------------------------------------------------------------
# Aggregation — representative-model policy
# ---------------------------------------------------------------------------

def test_aggregate_uses_representative_model_mitigations():
    """``aggregate_mitigations`` = the mitigations of the FIRST per-model result
    whose status equals the aggregate status (representative-model policy).

    This is an explicit assertion of the current policy: a future switch to a
    conservative all-or-nothing merge must change this test deliberately.
    """
    mit_a = Mitigation(
        kind=MitigationKind.ALTITUDE, addresses="cruise_imc", detail="A",
        mitigated_status=AdvisoryStatus.GREEN, altitude_ft=6000,
    )
    mit_b = Mitigation(
        kind=MitigationKind.ALTITUDE, addresses="cruise_imc", detail="B",
        mitigated_status=AdvisoryStatus.GREEN, altitude_ft=5000,
    )
    per_model = [
        ModelAdvisoryResult.build(
            model="gfs", status=AdvisoryStatus.RED, detail="", affected=5, total=10,
            total_distance_nm=200, mitigations=[mit_a],
        ),
        ModelAdvisoryResult.build(
            model="ecmwf", status=AdvisoryStatus.RED, detail="", affected=5, total=10,
            total_distance_nm=200, mitigations=[mit_b],
        ),
        ModelAdvisoryResult.build(
            model="icon", status=AdvisoryStatus.AMBER, detail="", affected=2, total=10,
            total_distance_nm=200,
        ),
    ]
    result = RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, {})

    assert result.aggregate_status == AdvisoryStatus.RED
    # Representative is the FIRST RED model (gfs) — NOT a merge of gfs+ecmwf.
    assert result.aggregate_mitigations == [mit_a]


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_backward_compat_model_result_without_mitigations():
    """An old ModelAdvisoryResult JSON without the field → empty mitigations."""
    old = ModelAdvisoryResult.model_validate_json('{"model": "gfs", "status": "green"}')
    assert old.mitigations == []


def test_backward_compat_route_result_without_mitigations():
    """An old RouteAdvisoryResult JSON without the field → empty aggregate."""
    old = RouteAdvisoryResult.model_validate_json(
        '{"advisory_id": "vfr_feasibility", "aggregate_status": "green"}'
    )
    assert old.aggregate_mitigations == []
