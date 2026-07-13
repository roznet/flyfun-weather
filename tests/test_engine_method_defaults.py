"""Tests for the declared engine grading-method defaults (#403).

The three engine methods (icing / cloud / convective) previously had no declared
default: an absent method silently fell through falsy checks in
``tasks/advise.py::_resolve_analyses`` to DD icing / DD cloud / thermo convective,
while the settings page displayed the NWP methods. #403 introduces
``ENGINE_METHOD_DEFAULTS`` as the single source of truth, resolves absence through
it, and exposes it to the client so the UI default and the runtime default cannot
drift.
"""

from __future__ import annotations

from datetime import datetime, timezone

from weatherbrief.analysis.advisories.engine_methods import ENGINE_METHOD_DEFAULTS
from weatherbrief.models import (
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveRisk,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
    RoutePointAnalysis,
    SoundingAnalysis,
)
from weatherbrief.tasks.advise import _resolve_analyses


def _rpa_with_all_slots() -> RoutePointAnalysis:
    """A route point whose DD and NWP cloud/icing/convective slots differ, so we
    can tell which track a resolution selected."""
    sounding = SoundingAnalysis(
        # cloud: DD base 3000 vs NWP base 5000
        cloud_layers=[EnhancedCloudLayer(base_ft=3000, top_ft=8000, coverage=CloudCoverage.BKN)],
        nwp_cloud_layers=[EnhancedCloudLayer(base_ft=5000, top_ft=12000, coverage=CloudCoverage.OVC)],
        # icing: DD active zone vs NWP zone
        icing_zones=[IcingZone(base_ft=5000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)],
        icing_ogimet_nwp_zones=[IcingZone(base_ft=6000, top_ft=9000, risk=IcingRisk.LIGHT, icing_type=IcingType.RIME)],
        # convective: thermo LOW vs NWP HIGH
        convective=ConvectiveAssessment(risk_level=ConvectiveRisk.LOW),
        convective_thermo=ConvectiveAssessment(risk_level=ConvectiveRisk.LOW),
        convective_nwp=ConvectiveAssessment(risk_level=ConvectiveRisk.HIGH),
    )
    return RoutePointAnalysis(
        point_index=0, lat=48.0, lon=2.0, distance_from_origin_nm=0.0,
        interpolated_time=datetime.now(timezone.utc),
        forecast_hour=datetime.now(timezone.utc), track_deg=90.0,
        sounding={"gfs": sounding},
    )


def test_absent_methods_resolve_to_declared_nwp_defaults():
    """None icing/cloud/convective → the NWP defaults, not the old DD/thermo."""
    rpa = _rpa_with_all_slots()
    result = _resolve_analyses([rpa], None, None, None)
    s = result[0].sounding["gfs"]
    # cloud → NWP layers (base 5000, OVC)
    assert s.cloud_layers[0].base_ft == 5000
    assert s.cloud_layers[0].coverage == CloudCoverage.OVC
    # icing → Ogimet-NWP zones (LIGHT/RIME)
    assert s.icing_zones[0].risk == IcingRisk.LIGHT
    assert s.icing_zones[0].icing_type == IcingType.RIME
    # convective → NWP assessment (HIGH)
    assert s.convective.risk_level == ConvectiveRisk.HIGH
    # original untouched
    assert rpa.sounding["gfs"].cloud_layers[0].base_ft == 3000
    assert rpa.sounding["gfs"].convective.risk_level == ConvectiveRisk.LOW


def test_explicit_dd_thermo_methods_are_honoured_unchanged():
    """Explicit DD/DD/thermo keeps the DD/thermo tracks — behaviour-preserving
    for the ~94% of profiles carrying explicit method keys."""
    rpa = _rpa_with_all_slots()
    original = [rpa]
    result = _resolve_analyses(original, "ogimet_dd", "dd", "thermo")
    # No *data* swap — every DD/thermo track is kept verbatim. It is no longer
    # object-identity (#409 follow-up): the no-swap path still stamps provenance,
    # so all three axes badge the method that actually graded them.
    s = result[0].sounding["gfs"]
    assert s.cloud_layers[0].base_ft == 3000
    assert s.icing_zones[0].risk == IcingRisk.MODERATE
    assert s.convective.risk_level == ConvectiveRisk.LOW
    assert s.cloud_method_effective == "dd"
    assert s.icing_method_effective == "ogimet_dd"
    assert s.convective_method_effective == "thermo"


def test_engine_method_defaults_constant_values():
    """The declared defaults match the settings-page placeholders.

    #410 split the cloud axis: the key is now ``cloud_source`` (bare ``"nwp"``),
    with the render style owned by the client.
    """
    assert ENGINE_METHOD_DEFAULTS == {
        "icing_method": "ogimet_nwp",
        "cloud_source": "nwp",
        "convective_method": "nwp",
    }


def test_catalog_endpoint_exposes_engine_method_defaults():
    """The advisory-catalog endpoint serves the constant so the UI default and the
    backend default cannot drift (guards against re-drift)."""
    from weatherbrief.api.preferences import get_advisory_catalog

    resp = get_advisory_catalog()
    assert resp["engine_method_defaults"] == ENGINE_METHOD_DEFAULTS


def test_service_toggles_no_longer_expose_engine_methods():
    """#410 retired the account-level engine methods entirely (they were empty
    for every user, never written, never read by the pipeline). The legacy
    service-toggle parser must no longer surface them."""
    from weatherbrief.api.preferences import _parse_service_toggles

    toggles = _parse_service_toggles("")  # no stored blob → all defaults
    assert "icing_method" not in toggles
    assert "cloud_method" not in toggles
    assert "convective_method" not in toggles
