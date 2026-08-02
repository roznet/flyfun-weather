"""ICON-D2 precipitating hydrometeors + ICON-EU convective single-level (#530).

Covers the four things that can silently regress here:
- the model-level variable list is PER VARIANT (D2 gets qr/qs/qg, EU must not);
- the new species survive every carrier (GRIB merge, spatial interp, time interp);
- ICON-EU's sounding fetch is byte-for-byte unchanged;
- tke stays off unless explicitly configured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_D2,
    ICON_D2_VARIABLES,
    ICON_EU,
    ICON_EU_CLOUD_DIAG_VARIABLES,
    ICON_EU_VARIABLES,
    ICON_TKE_ENV_VAR,
    icon_model_level_fetch_variables,
    icon_tke_enabled,
)
from weatherbrief.models import HourlyForecast, PressureLevelData
from weatherbrief.models.analysis import CONDENSATE_LEVEL_FIELDS

_NEW_SPECIES = ("rain_water_kg_kg", "snow_water_kg_kg", "graupel_water_kg_kg")


# ---------------------------------------------------------------------------
# Per-variant variable lists
# ---------------------------------------------------------------------------


class TestPerVariantVariableList:
    def test_d2_fetches_the_precipitating_species(self):
        for var in ("qr", "qs", "qg"):
            assert var in ICON_D2.model_level_variables
        # ...on top of everything it fetched before, not instead of.
        for var in ICON_EU_VARIABLES:
            assert var in ICON_D2.model_level_variables

    def test_icon_eu_does_not(self):
        """ICON-EU's moisture set is qc/qi/qv only — qr/qs/qg 404 on that feed.

        Also the download-size half of "an ICON-EU-only briefing is unchanged":
        the model-level list it fetches is exactly what it fetched before #530.
        """
        assert ICON_EU.model_level_variables == ICON_EU_VARIABLES
        assert ICON_EU_VARIABLES == ("qc", "qi", "clc", "p", "t", "qv", "u", "v", "w")
        for var in ("qr", "qs", "qg"):
            assert var not in ICON_EU.model_level_variables

    def test_every_model_level_variable_has_a_decode_mapping(self):
        """A fetched variable with no mapping is bytes downloaded and dropped."""
        from weatherbrief.fetch.grib.decode import _ICON_FULL_VAR_MAP

        for variant in (ICON_EU, ICON_D2):
            for var in variant.model_level_variables:
                assert var in _ICON_FULL_VAR_MAP or var in ("p", "pres"), (
                    f"{variant.slug}: {var} is fetched but never decoded"
                )

    def test_new_species_map_to_the_model_agnostic_field_names(self):
        """AROME (#529) publishes the same ICE3 species and will fill these."""
        from weatherbrief.fetch.grib.decode import _ICON_FULL_VAR_MAP

        assert _ICON_FULL_VAR_MAP["qr"] == "rain_water_kg_kg"
        assert _ICON_FULL_VAR_MAP["qs"] == "snow_water_kg_kg"
        assert _ICON_FULL_VAR_MAP["qg"] == "graupel_water_kg_kg"

    def test_chunked_decode_covers_every_mapped_species(self):
        """The chunked path walks a hard-coded (var, field) list — a species
        added to the map but not to that list is fetched and silently dropped."""
        import inspect

        from weatherbrief.fetch.grib import decode as decode_mod

        src = inspect.getsource(decode_mod.decode_icon_eu_per_point_chunked)
        for var in ("qr", "qs", "qg"):
            assert f'("{var}"' in src

    def test_interpolation_bounds_clamp_the_new_species_non_negative(self):
        from weatherbrief.fetch.grib.icon_eu_levels import bounds_for_field

        for name in _NEW_SPECIES:
            assert bounds_for_field(name) == (0.0, None)


# ---------------------------------------------------------------------------
# Download volume (#530 acceptance: D2 addition stays under ~25 MB / 12 h)
# ---------------------------------------------------------------------------


class TestDownloadVolume:
    # Measured on the live DWD feed, run 00z step 003 (issue #530, 2026-07-30).
    # qs was not published individually in that audit; it is taken as the
    # remainder of the measured aggregate "the three together cost ~2.5x a
    # single qc", with qc = 13.6 KB.
    _KB_PER_LEVEL_FILE = {"qr": 18.7, "qg": 2.4, "qs": 2.5 * 13.6 - 18.7 - 2.4}
    _D2_LEVELS = ICON_D2.level_max - ICON_D2.level_min + 1  # 50
    _WINDOW_HOURS = 12

    def test_d2_species_addition_stays_under_25_mb(self):
        added_kb = (
            sum(self._KB_PER_LEVEL_FILE.values())
            * self._D2_LEVELS
            * self._WINDOW_HOURS
        )
        assert added_kb / 1024 < 25.0

    def test_the_estimate_covers_every_species_actually_added(self):
        """Guards the arithmetic above against a fourth species slipping in
        un-costed — the estimate is only meaningful if it prices the real list."""
        added = set(ICON_D2.model_level_variables) - set(ICON_EU_VARIABLES)
        assert added == set(self._KB_PER_LEVEL_FILE)


# ---------------------------------------------------------------------------
# TKE — opt-in, off by default
# ---------------------------------------------------------------------------


class TestTkeOptIn:
    def test_off_by_default_for_both_variants(self, monkeypatch):
        monkeypatch.delenv(ICON_TKE_ENV_VAR, raising=False)
        for variant in (ICON_EU, ICON_D2):
            assert not icon_tke_enabled(variant)
            assert "tke" not in icon_model_level_fetch_variables(variant)

    def test_enabling_d2_leaves_the_expensive_eu_field_off(self, monkeypatch):
        monkeypatch.setenv(ICON_TKE_ENV_VAR, "icon-d2")
        assert "tke" in icon_model_level_fetch_variables(ICON_D2)
        assert "tke" not in icon_model_level_fetch_variables(ICON_EU)

    def test_all_enables_both(self, monkeypatch):
        monkeypatch.setenv(ICON_TKE_ENV_VAR, "all")
        for variant in (ICON_EU, ICON_D2):
            assert "tke" in icon_model_level_fetch_variables(variant)

    def test_tke_is_never_part_of_the_required_decode_set(self, monkeypatch):
        """An optional field with no consumer must not be able to trip the #478
        "incomplete column -> skip the whole hour" rule that guards the sounding."""
        monkeypatch.setenv(ICON_TKE_ENV_VAR, "all")
        for variant in (ICON_EU, ICON_D2):
            assert "tke" not in variant.model_level_variables


# ---------------------------------------------------------------------------
# ICON-EU convective single-level
# ---------------------------------------------------------------------------


class TestIconEuConvectiveSingleLevel:
    def test_lpi_con_max_and_cape_con_are_fetched_for_eu_only(self):
        assert "lpi_con_max" in ICON_EU_CLOUD_DIAG_VARIABLES
        assert "cape_con" in ICON_EU_CLOUD_DIAG_VARIABLES
        # D2 is convection-permitting — it runs no scheme to diagnose.
        assert "lpi_con_max" not in ICON_D2.cloud_diag_variables
        assert "cape_con" not in ICON_D2.cloud_diag_variables

    def test_decoded_onto_the_diagnostics_model(self):
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        diag = build_icon_cloud_diagnostics(
            {"lpi_con_max_j_kg": 4.2, "conv_cape_jkg": 850.0},
        )
        assert diag is not None
        assert diag.lpi_con_max_j_kg == 4.2
        assert diag.conv_cape_jkg == 850.0

    def test_undefined_sentinel_is_dropped_not_propagated(self):
        from weatherbrief.fetch.grib.decode import build_icon_cloud_diagnostics

        diag = build_icon_cloud_diagnostics(
            {"lpi_con_max_j_kg": -999.9, "conv_cape_jkg": -999.9, "ceiling_m": 300.0},
        )
        assert diag is not None
        assert diag.lpi_con_max_j_kg is None
        assert diag.conv_cape_jkg is None

    def test_lpi_is_classified_as_a_window_max_not_an_instant_value(self):
        """It is a max over the output interval, so a gap hour must take the
        COVERING interval's value rather than forward-fill the previous one."""
        from weatherbrief.models.analysis import (
            NWP_CLOUD_DIAG_INSTANT_SCALARS,
            NWP_CLOUD_DIAG_RATE_SCALARS,
            NWP_CLOUD_DIAG_SCALARS,
        )

        assert "lpi_con_max_j_kg" in NWP_CLOUD_DIAG_RATE_SCALARS
        assert "lpi_con_max_j_kg" not in NWP_CLOUD_DIAG_INSTANT_SCALARS
        # conv_cape IS instantaneous.
        assert "conv_cape_jkg" in NWP_CLOUD_DIAG_INSTANT_SCALARS
        # Both axes must cover both.
        assert {"lpi_con_max_j_kg", "conv_cape_jkg"} <= set(NWP_CLOUD_DIAG_SCALARS)


# ---------------------------------------------------------------------------
# The species survive every carrier
# ---------------------------------------------------------------------------


class TestCarriers:
    def test_shared_inventory_lists_every_species(self):
        for name in ("cloud_liquid_water_kg_kg", "ice_mixing_ratio_kg_kg", *_NEW_SPECIES):
            assert name in CONDENSATE_LEVEL_FIELDS
        # Anchor first — every carrier keys "has data" on it.
        assert CONDENSATE_LEVEL_FIELDS[0] == "cloud_liquid_water_kg_kg"

    def test_grib_merge_copies_them_onto_the_pressure_level(self):
        from weatherbrief.fetch.grib import _apply_merge_level_fields

        pl = PressureLevelData(pressure_hpa=850)
        enriched = _apply_merge_level_fields(
            pl,
            {
                "cloud_liquid_water_kg_kg": 1e-4,
                "rain_water_kg_kg": 2e-4,
                "snow_water_kg_kg": 3e-4,
                "graupel_water_kg_kg": 4e-4,
            },
        )
        assert enriched
        assert pl.rain_water_kg_kg == 2e-4
        assert pl.snow_water_kg_kg == 3e-4
        assert pl.graupel_water_kg_kg == 4e-4

    def test_full_sounding_replacement_carries_them(self):
        """THE path ICON actually takes.

        ICON/ECMWF/HRRR do not patch fields onto existing levels — they
        rebuild `pressure_levels` wholesale from the decoded dict via
        `build_pressure_levels_from_grib`, whose passthrough list is separate
        from the merge list above. A species missing there is decoded,
        vertically interpolated, and then dropped before any consumer sees it.
        """
        from weatherbrief.fetch.grib.decode import build_pressure_levels_from_grib

        levels = build_pressure_levels_from_grib({
            850: {
                "raw_temperature_k": 268.15,
                "raw_specific_humidity_kg_kg": 3e-3,
                "cloud_liquid_water_kg_kg": 1e-4,
                "ice_mixing_ratio_kg_kg": 2e-5,
                "rain_water_kg_kg": 3e-4,
                "snow_water_kg_kg": 4e-4,
                "graupel_water_kg_kg": 0.0,
            },
        })
        assert len(levels) == 1
        assert levels[0].rain_water_kg_kg == 3e-4
        assert levels[0].snow_water_kg_kg == 4e-4
        assert levels[0].graupel_water_kg_kg == 0.0

    def test_every_condensate_species_survives_the_replacement_path(self):
        """Inventory-driven guard: whatever ends up in CONDENSATE_LEVEL_FIELDS
        must pass through, so a future species cannot be half-wired."""
        from weatherbrief.fetch.grib.decode import build_pressure_levels_from_grib

        raw = {name: 1e-4 for name in CONDENSATE_LEVEL_FIELDS}
        raw["raw_temperature_k"] = 268.15
        levels = build_pressure_levels_from_grib({700: raw})
        for name in CONDENSATE_LEVEL_FIELDS:
            assert getattr(levels[0], name) == 1e-4, f"dropped {name}"

    def test_time_axis_forward_fill_carries_them(self):
        from weatherbrief.fetch.grib.fill import _fill_clw_hourly

        t0 = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
        anchor = HourlyForecast(
            time=t0,
            pressure_levels=[PressureLevelData(
                pressure_hpa=850,
                cloud_liquid_water_kg_kg=1e-4,
                rain_water_kg_kg=2e-5,
                snow_water_kg_kg=3e-5,
                graupel_water_kg_kg=4e-5,
            )],
        )
        gap = HourlyForecast(
            time=t0 + timedelta(hours=1),
            pressure_levels=[PressureLevelData(pressure_hpa=850)],
        )

        assert _fill_clw_hourly([anchor, gap]) == 1
        filled = gap.pressure_levels[0]
        assert filled.rain_water_kg_kg == 2e-5
        assert filled.snow_water_kg_kg == 3e-5
        assert filled.graupel_water_kg_kg == 4e-5

    def test_time_axis_interp_carries_them(self):
        from weatherbrief.fetch.grib.fill import _interp_levels_at

        def _lvl(scale):
            return PressureLevelData(
                pressure_hpa=850,
                temperature_c=-5.0,
                cloud_liquid_water_kg_kg=1e-4 * scale,
                rain_water_kg_kg=2e-5 * scale,
                snow_water_kg_kg=3e-5 * scale,
                graupel_water_kg_kg=4e-5 * scale,
            )

        out = _interp_levels_at([_lvl(1.0)], {850: _lvl(3.0)}, 0.5)
        assert out[0].rain_water_kg_kg == pytest.approx(4e-5)
        assert out[0].snow_water_kg_kg == pytest.approx(6e-5)
        assert out[0].graupel_water_kg_kg == pytest.approx(8e-5)

    def test_spatial_interp_fills_a_middle_point(self):
        cs, distances = _three_point_section(
            outer={"cloud_liquid_water_kg_kg": 1e-4, "rain_water_kg_kg": 2e-5,
                   "snow_water_kg_kg": 4e-5, "graupel_water_kg_kg": 0.0},
        )
        from weatherbrief.analysis.spatial_interpolation import _interpolate_level

        filled = _interpolate_level(
            cs, hour_idx=0, n_points=3, pressure=850,
            distances=distances, max_gap_nm=100.0,
        )
        assert filled == 1
        mid = cs.point_forecasts[1].hourly[0].pressure_levels[0]
        assert mid.rain_water_kg_kg == pytest.approx(2e-5)
        assert mid.snow_water_kg_kg == pytest.approx(4e-5)
        assert mid.graupel_water_kg_kg == pytest.approx(0.0)
        assert mid.clw_interpolated is True

    def test_spatial_interp_never_invents_a_one_sided_species(self):
        """CLW present on both sides but qr on one: qr must stay None.

        Interpolating from one endpoint would put rain on the far side of a gap
        nothing measured — the same rule the cloud species already followed.
        """
        cs, distances = _three_point_section(
            outer={"cloud_liquid_water_kg_kg": 1e-4},
        )
        cs.point_forecasts[0].hourly[0].pressure_levels[0].rain_water_kg_kg = 5e-5
        from weatherbrief.analysis.spatial_interpolation import _interpolate_level

        _interpolate_level(
            cs, hour_idx=0, n_points=3, pressure=850,
            distances=distances, max_gap_nm=100.0,
        )
        mid = cs.point_forecasts[1].hourly[0].pressure_levels[0]
        assert mid.cloud_liquid_water_kg_kg is not None
        assert mid.rain_water_kg_kg is None


def _three_point_section(outer: dict[str, float]):
    """Three route points; the outer two carry ``outer``, the middle is empty."""
    from weatherbrief.models import (
        ModelSource,
        RouteCrossSection,
        Waypoint,
        WaypointForecast,
    )

    t0 = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)

    def _wf(idx: int, fields: dict[str, float]):
        return WaypointForecast(
            waypoint=Waypoint(icao=f"PT{idx}", name=f"pt{idx}", lat=48.0, lon=2.0 + idx),
            model=ModelSource.ICON,
            fetched_at=t0,
            hourly=[HourlyForecast(
                time=t0,
                pressure_levels=[PressureLevelData(pressure_hpa=850, **fields)],
            )],
        )

    cs = RouteCrossSection(
        model=ModelSource.ICON,
        route_points=[],
        fetched_at=t0,
        point_forecasts=[_wf(0, outer), _wf(1, {}), _wf(2, outer)],
    )
    return cs, [0.0, 25.0, 50.0]
