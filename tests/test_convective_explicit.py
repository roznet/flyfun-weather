"""Tests for the ICON-D2 explicit-convection assessment (issue #462).

Covers the v1 decision table (dbz × corroborators), the completeness
semantics (incomplete detection → unavailable, never quiet; incomplete
corroborator channels never downgrade or upgrade), the structural safety
properties (top_ft always None so the overfly-clearance filter cannot consume
the echo top; no CIN suppression of a simulated echo), the explicit
DD-vs-model cross-check, and the analyze_sounding wiring (payload presence
selects the explicit track; incomplete detection sets the unavailability flag
instead of a fake quiet assessment).
"""

from __future__ import annotations

from datetime import datetime, timezone

from weatherbrief.analysis.sounding import analyze_sounding_lite
from weatherbrief.analysis.sounding.convective import (
    assess_convective_explicit,
    convective_cross_check,
)
from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRisk,
    HourlyForecast,
    NWPCloudDiagnostics,
    NWPExplicitConvectiveDiagnostics,
    PressureLevelData,
    ThermodynamicIndices,
)


def _explicit(
    dbz: float | None = None,
    *,
    detection: bool = True,
    lpi: float | None = None,
    w: float | None = None,
    uh: float | None = None,
    echo_top_ft: float | None = None,
    echo_complete: bool = False,
) -> NWPExplicitConvectiveDiagnostics:
    return NWPExplicitConvectiveDiagnostics(
        source="icon_d2",
        reflectivity_hour_max_dbz=dbz,
        lightning_potential_hour_max_jkg=lpi,
        updraft_hour_max_ms=w,
        updraft_helicity_2_8km_hour_max_m2s2=uh,
        echo_top_18dbz_ft=echo_top_ft,
        detection_complete=detection,
        strength_complete=(lpi is not None and w is not None),
        echo_top_complete=echo_complete,
    )


def _assess(explicit, indices=None, diag=None):
    return assess_convective_explicit(
        indices or ThermodynamicIndices(), diag, explicit,
    )


# ---------------------------------------------------------------------------
# Availability semantics — incomplete detection is UNAVAILABLE, never quiet
# ---------------------------------------------------------------------------


class TestExplicitAvailability:
    def test_incomplete_detection_returns_none_not_quiet(self):
        # A failed dbz channel must NOT become an NWP NONE ("scheme quiet"),
        # nor a CAPE fallback presented as the explicit verdict.
        assert _assess(_explicit(None, detection=False)) is None

    def test_incomplete_detection_with_strong_cape_still_none(self):
        diag = NWPCloudDiagnostics(ml_cape_jkg=1500.0)
        indices = ThermodynamicIndices(cape_surface_jkg=1500.0)
        assert _assess(_explicit(None, detection=False), indices, diag) is None

    def test_complete_and_quiet_is_a_real_none_assessment(self):
        # Detection complete + no echo = genuinely quiet: a real assessment
        # (so DD-vs-model cross-checks can fire), risk NONE.
        result = _assess(_explicit(None, detection=True))
        assert result is not None
        assert result.risk_level == ConvectiveRisk.NONE
        assert result.method == "nwp_explicit"
        assert result.explicit_source == "icon_d2"


# ---------------------------------------------------------------------------
# v1 decision table — dbz rows × corroborator columns
# ---------------------------------------------------------------------------


class TestExplicitDecisionTable:
    def test_below_35_never_fires_even_with_corroborators(self):
        result = _assess(_explicit(30.0, lpi=8.0, w=15.0))
        assert result.risk_level == ConvectiveRisk.NONE

    def test_35_44_uncorroborated_no_fire_with_stratiform_note(self):
        result = _assess(_explicit(40.0, lpi=0.0, w=2.0))
        assert result.risk_level == ConvectiveRisk.NONE
        assert any("stratiform" in s.lower() for s in result.suppressors)

    def test_35_44_one_corroborator_marginal(self):
        result = _assess(_explicit(40.0, lpi=2.0, w=2.0))
        assert result.risk_level == ConvectiveRisk.MARGINAL

    def test_35_44_two_corroborators_moderate(self):
        result = _assess(_explicit(40.0, lpi=2.0, w=12.0))
        assert result.risk_level == ConvectiveRisk.MODERATE

    def test_45_49_uncorroborated_moderate(self):
        result = _assess(_explicit(47.0, lpi=0.0, w=2.0))
        assert result.risk_level == ConvectiveRisk.MODERATE

    def test_45_49_two_corroborators_high(self):
        # LPI (1) + strong updraft (1) → 2 corroborators → HIGH.
        result = _assess(_explicit(47.0, lpi=2.0, w=12.0))
        assert result.risk_level == ConvectiveRisk.HIGH

    def test_50_plus_high_even_uncorroborated(self):
        result = _assess(_explicit(55.0, lpi=0.0, w=0.0))
        assert result.risk_level == ConvectiveRisk.HIGH

    def test_strong_lpi_counts_double(self):
        # LPI ≥ 5 alone counts as 2 corroborators → 45–49 row jumps to HIGH.
        result = _assess(_explicit(47.0, lpi=6.0, w=0.0))
        assert result.risk_level == ConvectiveRisk.HIGH

    def test_cape_ml_from_parameterized_diag_is_a_corroborator(self):
        diag = NWPCloudDiagnostics(ml_cape_jkg=800.0)
        # dbz 40 + ML-CAPE (1) + LPI (1) → 2 corroborators → MODERATE.
        result = _assess(_explicit(40.0, lpi=2.0, w=0.0), diag=diag)
        assert result.risk_level == ConvectiveRisk.MODERATE


class TestExplicitCompletenessRules:
    def test_incomplete_channels_never_downgrade_below_dbz_row(self):
        # All corroborator channels unavailable: the dbz-alone row stands.
        result = _assess(_explicit(47.0))  # lpi/w all None
        assert result.risk_level == ConvectiveRisk.MODERATE
        assert any("unavailable" in d for d in result.drivers)

    def test_incomplete_channels_never_upgrade(self):
        # 45–49 with only missing channels must not reach HIGH.
        result = _assess(_explicit(47.0, lpi=None, w=None))
        assert result.risk_level == ConvectiveRisk.MODERATE

    def test_zero_on_one_channel_never_suppresses_another(self):
        # Quiet LPI, but a strong updraft still counts.
        result = _assess(_explicit(40.0, lpi=0.0, w=14.0))
        assert result.risk_level == ConvectiveRisk.MARGINAL


class TestExplicitStructuralSafety:
    def test_top_ft_always_none_echo_top_is_detail_only(self):
        # The 18 dBZ echo top must be structurally unreachable by the
        # overfly-clearance filter (top_ft + clearance <= cruise): it travels
        # only as the dedicated character/detail field.
        result = _assess(_explicit(50.0, echo_top_ft=25000.0, echo_complete=True))
        assert result.top_ft is None
        assert result.base_ft is None
        assert result.echo_top_18dbz_ft == 25000.0
        assert result.reflectivity_hour_max_dbz == 50.0

    def test_no_cin_suppression_of_a_simulated_echo(self):
        # The model already convected — penalising the echo on CIN would be
        # circular (nwp_precip precedent). A strong cap must not pull HIGH down.
        indices = ThermodynamicIndices(cin_surface_jkg=-400.0)
        diag = NWPCloudDiagnostics(ml_cin_jkg=-400.0)
        result = _assess(_explicit(55.0), indices, diag)
        assert result.risk_level == ConvectiveRisk.HIGH

    def test_uh_is_narrative_only(self):
        # |UH| ≥ 25 adds a rotation note but never changes the tier.
        base = _assess(_explicit(47.0, lpi=0.0, w=0.0))
        with_uh = _assess(_explicit(47.0, lpi=0.0, w=0.0, uh=-60.0))
        assert with_uh.risk_level == base.risk_level
        assert any("anticyclonic" in d for d in with_uh.drivers)

    def test_graupel_wording_never_hail(self):
        # Rule 4 (#462): the explicit track never says "hail" — including via
        # _severity_modifiers' freezing-level+CAPE heuristic, which emits
        # "hail risk" and is plausible exactly when dbz reaches the HIGH band
        # (#463 review). Indices below trigger that modifier. (The grau_gsp
        # corroborator note was dropped in #468; the wording rule stays because
        # D2's mixed-phase signal still never discriminates hail.)
        indices = ThermodynamicIndices(
            cape_surface_jkg=1500.0, freezing_level_ft=12500.0,
        )
        result = _assess(_explicit(52.0, lpi=0.0, w=0.0), indices)
        text = " ".join(
            result.drivers + result.suppressors + result.severe_modifiers
        ).lower()
        assert "graupel" in text
        assert "hail" not in text
        # The rephrased modifier keeps the freezing-level+CAPE signal itself.
        assert any("mixed-phase" in m for m in result.severe_modifiers)


# ---------------------------------------------------------------------------
# Explicit DD-vs-model cross-check
# ---------------------------------------------------------------------------


class TestExplicitCrossCheck:
    def _thermo(self, risk: ConvectiveRisk, cape: float = 1200.0) -> ConvectiveAssessment:
        return ConvectiveAssessment(risk_level=risk, cape_jkg=cape, method="thermo")

    def test_dd_loaded_but_explicit_quiet(self):
        nwp = _assess(_explicit(None, detection=True))
        xc = convective_cross_check(self._thermo(ConvectiveRisk.HIGH), nwp)
        assert xc is not None
        assert xc.direction == "dd_not_corroborated"
        assert "ICON-D2" in xc.note

    def test_explicit_fired_but_dd_quiet(self):
        nwp = _assess(_explicit(50.0))
        xc = convective_cross_check(self._thermo(ConvectiveRisk.NONE), nwp)
        assert xc is not None
        assert xc.direction == "model_active_dd_quiet"
        assert "50 dBZ" in xc.note

    def test_marginal_explicit_sits_in_neither_band(self):
        nwp = _assess(_explicit(40.0, lpi=2.0, w=0.0))
        assert nwp.risk_level == ConvectiveRisk.MARGINAL
        assert convective_cross_check(self._thermo(ConvectiveRisk.HIGH), nwp) is None
        assert convective_cross_check(self._thermo(ConvectiveRisk.NONE), nwp) is None

    def test_agreeing_tracks_stay_silent(self):
        nwp = _assess(_explicit(50.0))
        assert convective_cross_check(self._thermo(ConvectiveRisk.HIGH), nwp) is None


# ---------------------------------------------------------------------------
# analyze_sounding wiring — payload presence IS the mode signal
# ---------------------------------------------------------------------------


def _make_levels() -> list[PressureLevelData]:
    return [
        PressureLevelData(pressure_hpa=1000, temperature_c=15.0, dewpoint_c=10.0,
                          relative_humidity_pct=72.0, wind_speed_kt=10,
                          wind_direction_deg=270, geopotential_height_m=110),
        PressureLevelData(pressure_hpa=925, temperature_c=10.0, dewpoint_c=5.0,
                          relative_humidity_pct=68.0, wind_speed_kt=15,
                          wind_direction_deg=280, geopotential_height_m=790),
        PressureLevelData(pressure_hpa=850, temperature_c=5.0, dewpoint_c=-1.0,
                          relative_humidity_pct=55.0, wind_speed_kt=20,
                          wind_direction_deg=290, geopotential_height_m=1500),
        PressureLevelData(pressure_hpa=700, temperature_c=-5.0, dewpoint_c=-12.0,
                          relative_humidity_pct=45.0, wind_speed_kt=30,
                          wind_direction_deg=300, geopotential_height_m=3100),
        PressureLevelData(pressure_hpa=500, temperature_c=-20.0, dewpoint_c=-30.0,
                          relative_humidity_pct=30.0, wind_speed_kt=45,
                          wind_direction_deg=310, geopotential_height_m=5600),
        PressureLevelData(pressure_hpa=300, temperature_c=-45.0, dewpoint_c=-55.0,
                          relative_humidity_pct=20.0, wind_speed_kt=60,
                          wind_direction_deg=320, geopotential_height_m=9300),
    ]


def _make_hourly(explicit=None) -> HourlyForecast:
    return HourlyForecast(
        time=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
        temperature_2m_c=15.0,
        dewpoint_2m_c=10.0,
        surface_pressure_hpa=1013.0,
        pressure_levels=_make_levels(),
        explicit_convective_diagnostics=explicit,
    )


class TestAnalyzeSoundingWiring:
    def test_payload_presence_selects_explicit_track(self):
        hourly = _make_hourly(_explicit(50.0))
        result = analyze_sounding_lite(hourly.pressure_levels, hourly, model_key="icon")
        assert result is not None
        assert result.convective_nwp is not None
        assert result.convective_nwp.method == "nwp_explicit"
        assert result.convective_nwp.risk_level == ConvectiveRisk.HIGH
        assert result.convective_explicit_unavailable is False
        # The thermo track stays independent.
        assert result.convective_thermo is not None
        assert result.convective_thermo.method == "thermo"

    def test_incomplete_detection_marks_unavailable_not_quiet(self):
        hourly = _make_hourly(_explicit(None, detection=False))
        result = analyze_sounding_lite(hourly.pressure_levels, hourly, model_key="icon")
        assert result is not None
        assert result.convective_nwp is None
        assert result.convective_explicit_unavailable is True

    def test_no_payload_keeps_parameterized_path(self):
        hourly = _make_hourly(None)
        result = analyze_sounding_lite(hourly.pressure_levels, hourly, model_key="icon")
        assert result is not None
        assert result.convective_explicit_unavailable is False
        if result.convective_nwp is not None:
            assert result.convective_nwp.method != "nwp_explicit"
