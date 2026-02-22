"""Tests for NWP cloud cover fallthrough fix and ceiling fields in Key Altitudes."""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.advisories import _nwp_cloud_cover_at
from weatherbrief.models import (
    CloudCoverage,
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

    def test_icon_with_cloud_cover_uses_icao_bands(self):
        """ICON-EU diag with CLCL/CLCM/CLCH cover_pct populated uses GFS-style path."""
        analysis = SoundingAnalysis(
            cloud_cover_low_pct=50.0,  # ICAO band fallback
            nwp_cloud_diagnostics=NWPCloudDiagnostics(
                low=NWPCloudLayerDiag(cover_pct=85.0),
                mid=NWPCloudLayerDiag(cover_pct=20.0),
                high=NWPCloudLayerDiag(cover_pct=5.0),
                ceiling_ft=2000,
            ),
        )
        # has_layer_cover is True → enters the GFS-style branch.
        # But low layer has no base_ft/top_ft, so no boundary match.
        # Returns 0.0 (treated as: altitude not within any diagnosed layer).
        result = _nwp_cloud_cover_at(3000, analysis)
        assert result == 0.0


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
