"""#807: NWP cover lookup (unknown ≠ 0 %, diag cover first) and the wired
``icing_severity_enhance`` upgrade."""

from __future__ import annotations

import pytest

from weatherbrief.analysis.sounding.icing import (
    SeverityEnhanceInputs,
    assess_icing_zones_ogimet_dd,
    assess_icing_zones_ogimet_nwp,
)
from weatherbrief.analysis.sounding.icing_common import nwp_cloud_cover_at_altitude
from weatherbrief.models import (
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    IcingRisk,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
)


# ── nwp_cloud_cover_at_altitude ─────────────────────────────────────


class TestCoverLookup:
    def _diag(self, low=None, mid=None, high=None) -> NWPCloudDiagnostics:
        return NWPCloudDiagnostics(
            low=low or NWPCloudLayerDiag(),
            mid=mid or NWPCloudLayerDiag(),
            high=high or NWPCloudLayerDiag(),
        )

    def test_missing_cover_inside_diag_layer_is_none_not_zero(self):
        diag = self._diag(low=NWPCloudLayerDiag(base_ft=2000, top_ft=9000))
        assert nwp_cloud_cover_at_altitude(5000, None, None, None, diag) is None

    def test_diag_cover_preferred_over_bulk(self):
        """NCEP band geometry pairs with its own band cover, not the ICAO slab."""
        diag = self._diag(
            low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=2000, top_ft=9000),
        )
        assert nwp_cloud_cover_at_altitude(5000, 10.0, None, None, diag) == 90.0

    def test_bulk_used_when_diag_layer_has_no_cover(self):
        diag = self._diag(low=NWPCloudLayerDiag(base_ft=2000, top_ft=9000))
        assert nwp_cloud_cover_at_altitude(5000, 40.0, None, None, diag) == 40.0

    def test_outside_layers_band_with_geometry_is_clear(self):
        diag = self._diag(
            low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=1000, top_ft=3000),
        )
        assert nwp_cloud_cover_at_altitude(5000, 90.0, None, None, diag) == 0.0

    def test_outside_layers_band_without_geometry_uses_its_bulk(self):
        """The mid band has no geometry to rule on 10,000 ft — its bulk value
        stands (was 0 %), and absent bulk is unknown (None)."""
        diag = self._diag(
            low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=1000, top_ft=3000),
        )
        assert nwp_cloud_cover_at_altitude(10000, 90.0, 70.0, None, diag) == 70.0
        assert nwp_cloud_cover_at_altitude(10000, 90.0, None, None, diag) is None

    def test_no_diag_geometry_falls_back_to_bulk(self):
        diag = self._diag(low=NWPCloudLayerDiag(cover_pct=50.0, base_ft=1000))
        assert nwp_cloud_cover_at_altitude(10000, 50.0, 30.0, None, diag) == 30.0
        assert nwp_cloud_cover_at_altitude(10000, 50.0, None, None, None) is None


# ── icing_severity_enhance ──────────────────────────────────────────


def _saturated_cold_levels() -> list[DerivedLevel]:
    """Three saturated levels around −7 °C: a pure-stratiform MODERATE zone."""
    return [
        DerivedLevel(pressure_hpa=p, altitude_ft=alt, temperature_c=-7.0,
                     dewpoint_c=-7.2, dewpoint_depression_c=0.2,
                     relative_humidity_pct=98.0)
        for p, alt in ((725, 9000), (700, 10000), (675, 11000))
    ]


def _deck() -> list[EnhancedCloudLayer]:
    return [EnhancedCloudLayer(base_ft=8500, top_ft=11500, coverage=CloudCoverage.OVC)]


class TestSeverityEnhance:
    def test_off_by_default_stratiform_caps_at_moderate(self):
        zones = assess_icing_zones_ogimet_dd(_saturated_cold_levels(), _deck())
        assert [z.risk for z in zones] == [IcingRisk.MODERATE]

    def test_enhance_upgrades_moderate_to_severe_with_nwp_corroboration(self):
        enhance = SeverityEnhanceInputs(nwp_cloud_mid_pct=90.0)
        zones = assess_icing_zones_ogimet_dd(
            _saturated_cold_levels(), _deck(), severity_enhance=enhance,
        )
        assert [z.risk for z in zones] == [IcingRisk.SEVERE]

    def test_enhance_without_nwp_corroboration_does_nothing(self):
        """Unknown cover never corroborates (no upgrade on missing data)."""
        enhance = SeverityEnhanceInputs()
        zones = assess_icing_zones_ogimet_dd(
            _saturated_cold_levels(), _deck(), severity_enhance=enhance,
        )
        assert [z.risk for z in zones] == [IcingRisk.MODERATE]

    def test_enhance_applies_to_ogimet_nwp(self):
        levels = _saturated_cold_levels()
        base = assess_icing_zones_ogimet_nwp(levels, _deck(), nwp_cloud_mid_pct=100.0)
        enhanced = assess_icing_zones_ogimet_nwp(
            _saturated_cold_levels(), _deck(), nwp_cloud_mid_pct=100.0,
            severity_enhance=SeverityEnhanceInputs(nwp_cloud_mid_pct=100.0),
        )
        assert [z.risk for z in base] == [IcingRisk.MODERATE]
        assert [z.risk for z in enhanced] == [IcingRisk.SEVERE]
        # The index is left as computed: an enhanced SEVERE is recognisable.
        assert enhanced[0].mean_icing_index == pytest.approx(base[0].mean_icing_index)

    def test_analyze_sounding_flag_changes_output(self, monkeypatch):
        """The preference reaches the zone builders (was a silent no-op)."""
        import weatherbrief.analysis.sounding.icing as icing_mod

        seen: list[object] = []
        real = icing_mod.assess_icing_zones_ogimet_dd

        def spy(*args, **kwargs):
            seen.append(kwargs.get("severity_enhance"))
            return real(*args, **kwargs)

        monkeypatch.setattr(icing_mod, "assess_icing_zones_ogimet_dd", spy)

        from weatherbrief.models import PressureLevelData
        from weatherbrief.analysis.sounding import analyze_sounding

        # Minimal moist standard-ish column.
        levels = []
        for p, z_m, t in (
            (1000, 110, 12.0), (925, 760, 7.0), (850, 1460, 2.0),
            (700, 3010, -7.0), (600, 4200, -14.0), (500, 5570, -22.0),
            (400, 7180, -32.0), (300, 9160, -45.0),
        ):
            levels.append(PressureLevelData(
                pressure_hpa=p, temperature_c=t, relative_humidity_pct=97.0,
                geopotential_height_m=z_m, wind_speed_kt=10.0,
                wind_direction_deg=270.0,
            ))
        off = analyze_sounding(levels, None)
        on = analyze_sounding(levels, None, icing_severity_enhance=True)
        if off is None or on is None:
            pytest.skip("profile preparation rejected the synthetic column")
        assert seen[0] is None
        assert isinstance(seen[1], SeverityEnhanceInputs)
