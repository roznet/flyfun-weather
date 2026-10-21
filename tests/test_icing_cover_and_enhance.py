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

    def test_outside_layers_band_without_geometry_uses_its_diag_cover(self):
        """The mid band has no geometry to rule on 10,000 ft — its own band
        cover answers (was 0 %, then the bulk value until #844)."""
        diag = self._diag(
            low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=1000, top_ft=3000),
            mid=NWPCloudLayerDiag(cover_pct=40.0),
        )
        assert nwp_cloud_cover_at_altitude(10000, 90.0, 70.0, None, diag) == 40.0

    def test_band_missing_from_diag_is_unknown_not_bulk(self):
        """Diagnostics carry band covers but not this band's → unknown. The
        bulk triple is measured over a different slab and is never mixed in
        per band (#844, the condensate builder's rule)."""
        diag = self._diag(
            low=NWPCloudLayerDiag(cover_pct=90.0, base_ft=1000, top_ft=3000),
        )
        assert nwp_cloud_cover_at_altitude(10000, 90.0, 70.0, None, diag) is None

    def test_cover_without_geometry_reads_diag_not_bulk(self):
        """ECMWF/ICON/HRRR shape: band covers, no base/top (#844). The GRIB
        band cover answers; the Open-Meteo bulk value is ignored."""
        ecmwf = self._diag(
            low=NWPCloudLayerDiag(cover_pct=50.0, base_ft=1000),
            mid=NWPCloudLayerDiag(cover_pct=80.0),
            high=NWPCloudLayerDiag(cover_pct=0.0),
        )
        assert nwp_cloud_cover_at_altitude(10000, 50.0, 0.0, None, ecmwf) == 80.0
        icon = self._diag(
            low=NWPCloudLayerDiag(cover_pct=100.0),
            mid=NWPCloudLayerDiag(cover_pct=10.0),
        )
        assert nwp_cloud_cover_at_altitude(4000, 0.0, 0.0, None, icon) == 100.0

    def test_ncep_bands_pick_by_pressure(self):
        """GFS/HRRR covers are NCEP pressure bands: ~8,000 ft at 750 hPa is
        LOW cloud (surface–642 hPa), not the ICAO mid band (#834)."""
        diag = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=100.0),
            mid=NWPCloudLayerDiag(cover_pct=0.0),
            high=NWPCloudLayerDiag(cover_pct=0.0),
            band_definition="ncep",
        )
        assert nwp_cloud_cover_at_altitude(
            8000, 100.0, 0.0, 0.0, diag, pressure_hpa=750,
        ) == 100.0
        # No pressure to hand → the ICAO slab is the honest approximation.
        assert nwp_cloud_cover_at_altitude(8000, 100.0, 0.0, 0.0, diag) == 0.0

    def test_no_diag_falls_back_to_bulk(self):
        assert nwp_cloud_cover_at_altitude(10000, 50.0, 30.0, None, None) == 30.0
        assert nwp_cloud_cover_at_altitude(10000, 50.0, None, None, None) is None
        empty = self._diag()  # diagnostics present, but no band cover at all
        assert nwp_cloud_cover_at_altitude(10000, 50.0, 30.0, None, empty) == 30.0


# ── Envelope cover first (#844) ─────────────────────────────────────


class TestEnvelopeCover:
    """Inside an NWP deck, icing scales by max(deck cover, band cover) — a band
    lookup that says 0 % no longer drops a level the model's deck contains."""

    def _deck_3d(self, pct: float | None) -> list[EnhancedCloudLayer]:
        return [EnhancedCloudLayer(
            base_ft=8500, top_ft=11500, coverage=CloudCoverage.BKN,
            mean_cloud_cover_pct=pct,
        )]

    def test_known_zero_band_cover_no_longer_erases_a_drawn_deck(self):
        from weatherbrief.analysis.sounding.icing import assess_icing_zones_ieng

        for assess in (assess_icing_zones_ogimet_nwp, assess_icing_zones_ieng):
            zones = assess(
                _saturated_cold_levels(), self._deck_3d(70.0),
                nwp_cloud_low_pct=100.0, nwp_cloud_mid_pct=0.0,
            )
            assert zones, assess.__name__

    def test_deck_cover_scales_the_index_when_band_says_less(self):
        thin = assess_icing_zones_ogimet_nwp(
            _saturated_cold_levels(), self._deck_3d(30.0), nwp_cloud_mid_pct=0.0,
        )
        thick = assess_icing_zones_ogimet_nwp(
            _saturated_cold_levels(), self._deck_3d(90.0), nwp_cloud_mid_pct=0.0,
        )
        assert thin and thick
        assert thick[0].mean_icing_index > thin[0].mean_icing_index

    def test_thin_deck_never_scales_below_the_band_cover(self):
        """max(deck, band): a deck's mean fraction alone never lowers icing."""
        band_only = assess_icing_zones_ogimet_nwp(
            _saturated_cold_levels(), self._deck_3d(None), nwp_cloud_mid_pct=100.0,
        )
        thin_deck = assess_icing_zones_ogimet_nwp(
            _saturated_cold_levels(), self._deck_3d(30.0), nwp_cloud_mid_pct=100.0,
        )
        assert thin_deck[0].mean_icing_index == pytest.approx(
            band_only[0].mean_icing_index,
        )

    def test_deck_without_cover_falls_back_to_lookup(self):
        """No deck cover → the band lookup still decides; a known 0 % skips."""
        zones = assess_icing_zones_ogimet_nwp(
            _saturated_cold_levels(), self._deck_3d(None), nwp_cloud_mid_pct=0.0,
        )
        assert zones == []


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
