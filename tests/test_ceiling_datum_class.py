"""#441 finding #3: AGL ceiling datum conversion across the shared engine.

Covers airport_consensus.best_ceiling / snap_to_dict, which the forecast map
(map_queries) and route-alternates (alternates) both consume — so the same
datum fix reaches all three surfaces.
"""
from weatherbrief.analysis.airport_consensus import best_ceiling, flight_category


def _snap(model, **kw):
    base = {"model": model, "sounding_ceiling_ft": None, "nwp_ceiling_ft": None,
            "cloud_base_ft": None, "lcl_ft": None, "visibility_m": 20000.0}
    base.update(kw)
    return base


def test_icon_nwp_msl_converted_to_agl():
    # ICON ceiling 5000 MSL at a 3000 ft field → 2000 AGL.
    snap = _snap("icon", nwp_ceiling_ft=5000.0)
    assert best_ceiling(snap, field_elevation_ft=3000.0) == 2000.0


def test_ecmwf_nwp_agl_not_double_subtracted():
    # ECMWF ceiling is already AGL — must NOT have elevation subtracted.
    snap = _snap("ecmwf", nwp_ceiling_ft=2000.0)
    assert best_ceiling(snap, field_elevation_ft=3000.0) == 2000.0


def test_min_of_sounding_and_nwp_in_agl():
    # sounding 6000 MSL→3000 AGL; icon nwp 5000 MSL→2000 AGL → min 2000.
    snap = _snap("icon", sounding_ceiling_ft=6000.0, nwp_ceiling_ft=5000.0)
    assert best_ceiling(snap, field_elevation_ft=3000.0) == 2000.0


def test_below_field_clamped_to_zero():
    snap = _snap("gfs", nwp_ceiling_ft=2500.0)
    assert best_ceiling(snap, field_elevation_ft=3000.0) == 0.0


def test_legacy_unchanged_without_elevation():
    snap = _snap("icon", sounding_ceiling_ft=6000.0, nwp_ceiling_ft=5000.0)
    assert best_ceiling(snap) == 5000.0  # datum-naive min, unchanged


def test_flight_category_uses_agl():
    # 5000 MSL over a 3000 ft field = 2000 AGL → MVFR, not VFR.
    snap = _snap("icon", nwp_ceiling_ft=5000.0)
    assert flight_category(snap, field_elevation_ft=3000.0) == "MVFR"
    assert flight_category(snap) == "VFR"  # legacy over-reads


def test_lcl_fallback_is_agl_not_subtracted():
    # lcl_ft (Espy surface T/Td approximation) is already AGL — must pass
    # through unchanged even at an elevated field, NOT double-subtracted to 0.
    snap = _snap("gfs", lcl_ft=2000.0)  # only the LCL fallback rung is present
    assert best_ceiling(snap, field_elevation_ft=3000.0) == 2000.0
    assert flight_category(snap, field_elevation_ft=3000.0) == "MVFR"  # not LIFR


def test_cloud_base_fallback_follows_model_datum():
    # cloud_base_ft follows the NWP datum: MSL for ICON → subtract elevation.
    snap = _snap("icon", cloud_base_ft=5000.0)
    assert best_ceiling(snap, field_elevation_ft=3000.0) == 2000.0
    # ECMWF cloud base is AGL → unchanged.
    snap_e = _snap("ecmwf", cloud_base_ft=2000.0)
    assert best_ceiling(snap_e, field_elevation_ft=3000.0) == 2000.0


def test_ecmwf_model_read_from_snap():
    # model comes from the snap dict; missing model → treated as MSL.
    assert best_ceiling(_snap("ecmwf", nwp_ceiling_ft=2000.0), field_elevation_ft=1000.0) == 2000.0
    assert best_ceiling(_snap(None, nwp_ceiling_ft=2000.0), field_elevation_ft=1000.0) == 1000.0
