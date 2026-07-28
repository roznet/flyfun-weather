"""Tests for NWP cloud cover fallthrough fix and ceiling fields in Key Altitudes."""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.advisories import _nwp_cloud_cover_at
from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
    SoundingAnalysis,
    ThermodynamicIndices,
)


# --- _nwp_cloud_cover_at regression fix tests ---


class TestNWPCloudCoverFallthrough:
    """Regression: ICON-EU diag with ceiling but no layer cover should fall through."""

    def test_icon_diag_no_cover_falls_through_to_icao(self):
        """Diag with ceiling_ft only (no cover_pct) → uses ICAO band cloud cover."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=80.0,
            cloud_cover_mid_pct=40.0,
            cloud_cover_high_pct=10.0,
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                ceiling_ft=2000,
                # No cover_pct on any layer — ICON-EU case
            ),
        )
        # Low band (< 6500 ft) should get Open-Meteo cloud cover
        result = _nwp_cloud_cover_at(3000, analysis)
        assert result == 80.0

    def test_icon_diag_no_cover_mid_band(self):
        """ICON-EU diag → mid altitude uses mid cloud cover."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=80.0,
            cloud_cover_mid_pct=45.0,
            cloud_cover_high_pct=10.0,
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                ceiling_ft=2000,
            ),
        )
        result = _nwp_cloud_cover_at(10000, analysis)
        assert result == 45.0

    def test_icon_diag_no_cover_high_band(self):
        """ICON-EU diag → high altitude uses high cloud cover."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=80.0,
            cloud_cover_mid_pct=45.0,
            cloud_cover_high_pct=15.0,
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                ceiling_ft=2000,
            ),
        )
        result = _nwp_cloud_cover_at(25000, analysis)
        assert result == 15.0

    def test_gfs_diag_with_cover_still_works(self):
        """GFS diag with full cover data uses actual cloud boundaries."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=80.0,
            cloud_cover_mid_pct=40.0,
            cloud_cover_high_pct=10.0,
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=2000, top_ft=5000),
                mid=NWPCloudLayerDiag(cover_pct=30.0, base_ft=10000, top_ft=15000),
                high=NWPCloudLayerDiag(cover_pct=5.0),
            ),
        )
        # Within low cloud layer → uses GFS cover
        result = _nwp_cloud_cover_at(3000, analysis)
        assert result == 90.0

        # Above all cloud layers → returns 0.0 (no cloud at this altitude)
        result = _nwp_cloud_cover_at(8000, analysis)
        assert result == 0.0

    def test_gfs_diag_convective_layer(self):
        """GFS diag with convective cover works correctly."""
        analysis = SoundingAnalysis(
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                low=NWPCloudLayerDiag(cover_pct=0.0),
                mid=NWPCloudLayerDiag(cover_pct=0.0),
                high=NWPCloudLayerDiag(cover_pct=0.0),
                convective_cover_pct=60.0,
                convective_base_ft=5000,
                convective_top_ft=30000,
            ),
        )
        result = _nwp_cloud_cover_at(15000, analysis)
        assert result == 60.0

    def test_no_diag_uses_icao_bands(self):
        """No diagnostics at all → uses ICAO band fallback."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=70.0,
            cloud_cover_mid_pct=30.0,
            cloud_cover_high_pct=5.0,
        )
        result = _nwp_cloud_cover_at(3000, analysis)
        assert result == 70.0

    def test_no_diag_no_cloud_data(self):
        """No diagnostics and no ICAO bands → returns None."""
        analysis = SoundingAnalysis()
        result = _nwp_cloud_cover_at(3000, analysis)
        assert result is None

    def test_cover_without_geometry_uses_icao_bands(self):
        """Band cover with NO base/top must fall back to the ICAO bands.

        This is the shape ECMWF (lcc/mcc/hcc) and HRRR publish, and the
        partial-ICON case. The branch keys on GEOMETRY, not on cover: without
        boundaries there is no way to answer "is this altitude inside the
        deck?", so the honest answer is the ICAO band, not a confident 0 %.

        This test previously asserted `0.0` — its own name said "uses icao
        bands" while the assertion pinned the opposite, documenting the
        defect as if it were the design (PR #508 review). Under that
        behaviour an overcast ECMWF column reported 0 % cover at every
        altitude.
        """
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=50.0,  # ICAO band fallback
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                low=NWPCloudLayerDiag(cover_pct=85.0),
                mid=NWPCloudLayerDiag(cover_pct=20.0),
                high=NWPCloudLayerDiag(cover_pct=5.0),
                ceiling_ft=2000,
            ),
        )
        assert _nwp_cloud_cover_at(3000, analysis) == 50.0

    def test_geometry_present_still_uses_boundaries(self):
        """A GFS-shaped diag (real base/top) keeps the boundary behaviour,
        including the 0 % answer for an altitude outside every diagnosed
        layer — that 0 % is meaningful because the geometry supports it."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=50.0,
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                low=NWPCloudLayerDiag(cover_pct=85.0, base_ft=1000, top_ft=5000),
                mid=NWPCloudLayerDiag(cover_pct=20.0),
                high=NWPCloudLayerDiag(cover_pct=5.0),
            ),
        )
        assert _nwp_cloud_cover_at(3000, analysis) == 85.0   # inside the deck
        assert _nwp_cloud_cover_at(12000, analysis) == 0.0   # outside, supported


# --- Ceiling fields in ThermodynamicIndices tests ---


class TestCeilingFields:
    """Test ceiling fields on ThermodynamicIndices."""

    def test_ceiling_fields_default_none(self):
        """Ceiling fields default to None."""
        indices = ThermodynamicIndices()
        assert indices.sounding_ceiling_ft is None
        assert indices.nwp_ceiling_ft is None

    def test_ceiling_fields_populated(self):
        """Ceiling fields can be set."""
        indices = ThermodynamicIndices(
            sounding_ceiling_ft=3000.0,
            nwp_ceiling_ft=2500.0,
        )
        assert indices.sounding_ceiling_ft == 3000.0
        assert indices.nwp_ceiling_ft == 2500.0

    def test_ceiling_fields_serialization(self):
        """Ceiling fields survive serialization roundtrip."""
        indices = ThermodynamicIndices(
            sounding_ceiling_ft=4000.0,
            nwp_ceiling_ft=3500.0,
            freezing_level_ft=8000.0,
        )
        data = indices.model_dump()
        restored = ThermodynamicIndices.model_validate(data)
        assert restored.sounding_ceiling_ft == 4000.0
        assert restored.nwp_ceiling_ft == 3500.0
        assert restored.freezing_level_ft == 8000.0


# --- LCL floor for sounding ceiling tests ---


class TestLCLFloor:
    """Test LCL floor logic in analyze_sounding ceiling computation."""

    def _run_ceiling(
        self,
        cloud_layers: list[EnhancedCloudLayer],
        derived_levels: list[DerivedLevel],
        lcl_altitude_ft: float | None,
    ) -> float | None:
        """Call the production ceiling computation used by analyze_sounding.

        Exercises the real ``compute_sounding_ceiling_ft`` so a regression in
        the production logic actually fails these tests (previously this helper
        re-implemented the logic locally and could not catch such a change).
        """
        from weatherbrief.analysis.sounding import compute_sounding_ceiling_ft

        return compute_sounding_ceiling_ft(
            cloud_layers, derived_levels, lcl_altitude_ft,
        )

    def test_lcl_floor_applied(self):
        """Cloud at first level (1000 hPa), LCL at 1400ft → ceiling = 1400."""
        cloud_layers = [
            EnhancedCloudLayer(
                base_ft=430, top_ft=2000,
                base_pressure_hpa=1000,
                coverage=CloudCoverage.BKN,
            ),
        ]
        derived_levels = [
            DerivedLevel(pressure_hpa=1000, altitude_ft=430),
            DerivedLevel(pressure_hpa=975, altitude_ft=1200),
            DerivedLevel(pressure_hpa=950, altitude_ft=1800),
        ]
        result = self._run_ceiling(cloud_layers, derived_levels, lcl_altitude_ft=1400.0)
        assert result == 1400

    def test_lcl_floor_not_applied_mid_level(self):
        """Cloud at 850 hPa (not first level) → ceiling unchanged."""
        cloud_layers = [
            EnhancedCloudLayer(
                base_ft=5000, top_ft=8000,
                base_pressure_hpa=850,
                coverage=CloudCoverage.BKN,
            ),
        ]
        derived_levels = [
            DerivedLevel(pressure_hpa=1000, altitude_ft=430),
            DerivedLevel(pressure_hpa=975, altitude_ft=1200),
            DerivedLevel(pressure_hpa=850, altitude_ft=5000),
        ]
        result = self._run_ceiling(cloud_layers, derived_levels, lcl_altitude_ft=1400.0)
        assert result == 5000

    def test_lcl_floor_not_applied_no_lcl(self):
        """No LCL → ceiling unchanged."""
        cloud_layers = [
            EnhancedCloudLayer(
                base_ft=430, top_ft=2000,
                base_pressure_hpa=1000,
                coverage=CloudCoverage.BKN,
            ),
        ]
        derived_levels = [
            DerivedLevel(pressure_hpa=1000, altitude_ft=430),
        ]
        result = self._run_ceiling(cloud_layers, derived_levels, lcl_altitude_ft=None)
        assert result == 430

    def test_lcl_floor_not_applied_lcl_below(self):
        """LCL below cloud base → ceiling unchanged."""
        cloud_layers = [
            EnhancedCloudLayer(
                base_ft=1500, top_ft=3000,
                base_pressure_hpa=1000,
                coverage=CloudCoverage.OVC,
            ),
        ]
        derived_levels = [
            DerivedLevel(pressure_hpa=1000, altitude_ft=1500),
        ]
        result = self._run_ceiling(cloud_layers, derived_levels, lcl_altitude_ft=1200.0)
        assert result == 1500


class TestCondensateCloudEnvelope:
    """HRRR's native cloud envelope, derived from CLMR + CIMIXR (#508 review).

    HRRR ships 3D microphysics but no 3D cloud fraction and no per-band
    base/top, so it satisfied neither existing source and
    `build_nwp_cloud_layers` returned None. That marked the default
    `ogimet_nwp` icing method unavailable and gated native SFIP off — the
    3 km microphysics never reached the advisory that most needs it.
    """

    @staticmethod
    def _hrrr_diag():
        from weatherbrief.fetch.grib.decode import build_hrrr_cloud_diagnostics

        return build_hrrr_cloud_diagnostics({
            "low_cover_pct": 90.0, "mid_cover_pct": 10.0, "high_cover_pct": 0.0,
            "total_cover_pct": 92.0, "ceiling_gpm": 300.0, "cloud_base_gpm": 250.0,
        })

    @staticmethod
    def _lv(p, z, clw=None, ice=None, t=None):
        from weatherbrief.models.analysis import PressureLevelData

        return PressureLevelData(
            pressure_hpa=p, geopotential_height_m=z, temperature_c=t,
            cloud_liquid_water_kg_kg=clw, ice_mixing_ratio_kg_kg=ice,
        )

    def _layers(self, levels):
        from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers

        return build_nwp_cloud_layers(
            self._hrrr_diag(), 90.0, 10.0, 0.0, pressure_levels=levels,
        )

    def test_condensate_profile_produces_a_native_layer(self):
        layers = self._layers([
            self._lv(1000, 110, 0.0, 0.0, 5.0),
            self._lv(925, 760, 3e-4, 0.0, -2.0),
            self._lv(850, 1460, 2e-4, 5e-5, -8.0),
            self._lv(700, 3010, 0.0, 0.0, -15.0),
        ])
        assert len(layers) == 1
        deck = layers[0]
        assert deck.source == "nwp_condensate"
        # Bounded by the dry levels on either side, not pinned to a level.
        assert 110 * 3.28084 < deck.base_ft < 760 * 3.28084
        assert 1460 * 3.28084 < deck.top_ft < 3010 * 3.28084
        # Amount comes from the model's published band, not from condensate.
        assert deck.mean_cloud_cover_pct == 90.0

    def test_clear_column_is_empty_not_none(self):
        """Condensate present and everywhere zero is a real clear forecast."""
        layers = self._layers([
            self._lv(1000, 110, 0.0, 0.0), self._lv(925, 760, 0.0, 0.0),
        ])
        assert layers == []

    def test_missing_condensate_is_none_not_clear(self):
        """No condensate fields at all is "no data" — never "clear sky"."""
        assert self._layers([self._lv(1000, 110), self._lv(925, 760)]) is None

    def test_background_noise_does_not_make_a_deck(self):
        layers = self._layers([
            self._lv(1000, 110, 1e-7, 0.0), self._lv(925, 760, 2e-7, 0.0),
        ])
        assert layers == []

    def test_vertical_gap_splits_layers(self):
        layers = self._layers([
            self._lv(1000, 110, 3e-4, 0.0), self._lv(925, 760, 0.0, 0.0),
            self._lv(850, 1460, 3e-4, 0.0),
        ])
        assert len(layers) == 2

    def test_ice_only_layer_counts(self):
        layers = self._layers([
            self._lv(1000, 110, 0.0, 0.0), self._lv(500, 5570, None, 2e-4, -30.0),
        ])
        assert len(layers) == 1

    def test_level_without_height_cannot_anchor_a_deck(self):
        """Condensate with no geopotential height has nowhere to be placed."""
        from weatherbrief.models.analysis import PressureLevelData

        layers = self._layers([
            self._lv(1000, 110, 0.0, 0.0),
            PressureLevelData(pressure_hpa=925, cloud_liquid_water_kg_kg=3e-4),
        ])
        assert layers == []

    def test_gfs_still_uses_its_band_geometry(self):
        """GFS carries BOTH band geometry and condensate — the condensate
        source must not displace its existing envelope."""
        from weatherbrief.analysis.sounding.clouds import build_nwp_cloud_layers
        from weatherbrief.models.analysis import (
            NWPCloudDiagnostics, NWPCloudLayerDiag,
        )

        gfs = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=1000, top_ft=5000),
            mid=NWPCloudLayerDiag(cover_pct=10.0), high=NWPCloudLayerDiag(),
        )
        layers = build_nwp_cloud_layers(gfs, 90.0, 10.0, 0.0, pressure_levels=[
            self._lv(925, 760, 3e-4, 0.0), self._lv(850, 1460, 2e-4, 0.0),
        ])
        assert [(lay.base_ft, lay.top_ft, lay.source) for lay in layers] == [
            (1000.0, 5000.0, "grib")
        ]

    def test_default_icing_method_grades_hrrr_supercooled_liquid(self):
        """The end-to-end point of the whole fix: a supercooled HRRR deck must
        reach the DEFAULT ogimet_nwp method and native SFIP. Both returned []
        before, because their cloud gate was None."""
        from datetime import datetime, timezone

        from weatherbrief.analysis.sounding import analyze_sounding
        from weatherbrief.models.analysis import HourlyForecast, PressureLevelData

        def lv(p, z, t, rh, clw=0.0, ice=0.0):
            return PressureLevelData(
                pressure_hpa=p, geopotential_height_m=z, temperature_c=t,
                relative_humidity_pct=rh, cloud_liquid_water_kg_kg=clw,
                ice_mixing_ratio_kg_kg=ice, wind_speed_kt=20.0,
                wind_direction_deg=270.0,
            )

        levels = [
            lv(1000, 110, 2.0, 90), lv(950, 540, -1.0, 98, 4e-4),
            lv(925, 760, -3.0, 99, 5e-4), lv(850, 1460, -8.0, 97, 3e-4, 4e-5),
            lv(700, 3010, -16.0, 60), lv(500, 5570, -30.0, 40),
        ]
        hourly = HourlyForecast(
            time=datetime(2026, 1, 15, 12, tzinfo=timezone.utc),
            pressure_levels=levels, cloud_cover_low_pct=90.0,
            cloud_cover_mid_pct=10.0, cloud_cover_high_pct=0.0,
            temperature_2m_c=2.0, nwp_cloud_diagnostics=self._hrrr_diag(),
        )
        result = analyze_sounding(levels, hourly=hourly)
        assert result.nwp_cloud_layers
        assert result.nwp_cloud_layers[0].source == "nwp_condensate"
        assert result.icing_ogimet_nwp_zones, "default ogimet_nwp must grade HRRR"
        assert result.sfip_zones, "native SFIP must grade HRRR CLW"
