"""Tests for ECMWF GRIB full sounding conversion and pressure level replacement."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from weatherbrief.fetch.grib.decode import (
    _convert_raw_sounding,
    build_pressure_levels_from_grib,
)


class TestConvertRawSounding:
    """Unit tests for _convert_raw_sounding()."""

    def test_temperature_k_to_c(self):
        result = _convert_raw_sounding({"raw_temperature_k": 273.15}, 850)
        assert result["temperature_c"] == pytest.approx(0.0)

        result = _convert_raw_sounding({"raw_temperature_k": 300.0}, 850)
        assert result["temperature_c"] == pytest.approx(26.85)

    def test_geopotential_to_height(self):
        result = _convert_raw_sounding({"raw_geopotential_m2_s2": 9806.65}, 850)
        assert result["geopotential_height_m"] == pytest.approx(1000.0)

    def test_gh_passthrough(self):
        """ECMWF-delivered gh (gpm) flows through unchanged — no /g division."""
        result = _convert_raw_sounding(
            {"geopotential_height_m": 1456.0, "raw_temperature_k": 278.0}, 850,
        )
        assert result["geopotential_height_m"] == pytest.approx(1456.0)

    def test_gh_wins_over_z(self):
        """At level 1 hPa both gh and z arrive; gh is authoritative."""
        result = _convert_raw_sounding(
            {"geopotential_height_m": 1500.0, "raw_geopotential_m2_s2": 9806.65},
            850,
        )
        # Would be 1000.0 via z/g; gh wins.
        assert result["geopotential_height_m"] == pytest.approx(1500.0)

    def test_wind_north(self):
        """Pure northerly wind: u=0, v=-10 → direction=360 (or 0)."""
        result = _convert_raw_sounding(
            {"raw_u_wind_m_s": 0.0, "raw_v_wind_m_s": -10.0}, 500,
        )
        assert result["wind_speed_kt"] == pytest.approx(10.0 * 1.94384)
        assert result["wind_direction_deg"] == pytest.approx(360.0, abs=0.1) or \
               result["wind_direction_deg"] == pytest.approx(0.0, abs=0.1)

    def test_wind_west(self):
        """Pure westerly wind (from west): u=+10, v=0 → direction=270."""
        result = _convert_raw_sounding(
            {"raw_u_wind_m_s": 10.0, "raw_v_wind_m_s": 0.0}, 500,
        )
        assert result["wind_direction_deg"] == pytest.approx(270.0, abs=0.1)

    def test_wind_south(self):
        """Pure southerly wind: u=0, v=10 → direction=180."""
        result = _convert_raw_sounding(
            {"raw_u_wind_m_s": 0.0, "raw_v_wind_m_s": 10.0}, 500,
        )
        assert result["wind_direction_deg"] == pytest.approx(180.0, abs=0.1)

    def test_wind_calm(self):
        result = _convert_raw_sounding(
            {"raw_u_wind_m_s": 0.0, "raw_v_wind_m_s": 0.0}, 500,
        )
        assert result["wind_speed_kt"] == pytest.approx(0.0)
        assert result["wind_direction_deg"] == 0.0

    def test_relative_humidity_passthrough(self):
        result = _convert_raw_sounding({"raw_relative_humidity_pct": 75.0}, 850)
        assert result["relative_humidity_pct"] == 75.0

    def test_dewpoint_from_t_and_rh(self):
        result = _convert_raw_sounding(
            {"raw_temperature_k": 293.15, "raw_relative_humidity_pct": 50.0}, 850,
        )
        # 20°C at 50% RH → dewpoint ~9.3°C
        assert "dewpoint_c" in result
        assert 8.0 < result["dewpoint_c"] < 11.0

    def test_rh_from_specific_humidity(self):
        """When r is absent, derive RH from q + T + P."""
        result = _convert_raw_sounding(
            {"raw_temperature_k": 293.15, "raw_specific_humidity_kg_kg": 0.007}, 1000,
        )
        assert "relative_humidity_pct" in result
        assert 0 < result["relative_humidity_pct"] <= 100

    def test_vertical_velocity_passthrough(self):
        result = _convert_raw_sounding({"vertical_velocity_pa_s": -0.5}, 500)
        assert result["vertical_velocity_pa_s"] == -0.5

    def test_cloud_water_passthrough(self):
        result = _convert_raw_sounding(
            {"cloud_liquid_water_kg_kg": 1e-4, "ice_mixing_ratio_kg_kg": 2e-5}, 700,
        )
        assert result["cloud_liquid_water_kg_kg"] == 1e-4
        assert result["ice_mixing_ratio_kg_kg"] == 2e-5

    def test_empty_input(self):
        result = _convert_raw_sounding({}, 500)
        assert result == {}


class TestBuildEcmwfPressureLevels:
    """Unit tests for build_pressure_levels_from_grib()."""

    def _make_raw_level(self, t_k: float = 280.0, rh: float = 60.0) -> dict[str, float]:
        return {
            "raw_temperature_k": t_k,
            "raw_relative_humidity_pct": rh,
            "raw_u_wind_m_s": 5.0,
            "raw_v_wind_m_s": -10.0,
            "raw_geopotential_m2_s2": 15000.0,
            "vertical_velocity_pa_s": -0.1,
            "cloud_liquid_water_kg_kg": 1e-5,
            "ice_mixing_ratio_kg_kg": 2e-6,
            "cloud_area_fraction_pct": 30.0,
        }

    def test_builds_correct_count(self):
        point_data = {p: self._make_raw_level() for p in [1000, 850, 700, 500, 300]}
        levels = build_pressure_levels_from_grib(point_data)
        assert len(levels) == 5

    def test_sorted_descending_pressure(self):
        point_data = {p: self._make_raw_level() for p in [300, 700, 1000, 500, 850]}
        levels = build_pressure_levels_from_grib(point_data)
        pressures = [pl.pressure_hpa for pl in levels]
        assert pressures == [1000, 850, 700, 500, 300]

    def test_all_fields_populated(self):
        point_data = {850: self._make_raw_level()}
        levels = build_pressure_levels_from_grib(point_data)
        pl = levels[0]
        assert pl.pressure_hpa == 850
        assert pl.temperature_c is not None
        assert pl.relative_humidity_pct is not None
        assert pl.dewpoint_c is not None
        assert pl.wind_speed_kt is not None
        assert pl.wind_direction_deg is not None
        assert pl.geopotential_height_m is not None
        assert pl.vertical_velocity_pa_s is not None
        assert pl.cloud_liquid_water_kg_kg is not None
        assert pl.ice_mixing_ratio_kg_kg is not None
        assert pl.cloud_area_fraction_pct is not None

    def test_empty_point_data(self):
        levels = build_pressure_levels_from_grib({})
        assert levels == []

    def test_25_levels(self):
        """Simulate a realistic 25-level ECMWF sounding."""
        ecmwf_levels = [
            1000, 925, 850, 700, 600, 500, 400, 300, 250, 200,
            150, 100, 50, 975, 950, 900, 875, 825, 800, 775,
            750, 725, 675, 650, 625,
        ]
        point_data = {p: self._make_raw_level() for p in ecmwf_levels}
        levels = build_pressure_levels_from_grib(point_data)
        assert len(levels) == 25
        pressures = [pl.pressure_hpa for pl in levels]
        assert pressures == sorted(ecmwf_levels, reverse=True)


class TestHypsometricGeopotentialFallback:
    """`build_pressure_levels_from_grib` derives GH when `z` is absent."""

    def _raw_no_z(self, t_k: float) -> dict[str, float]:
        """Raw level without geopotential (simulates ECMWF feed missing `z`)."""
        return {"raw_temperature_k": t_k, "raw_relative_humidity_pct": 50.0}

    def test_fills_gh_when_all_missing(self):
        """Column with no `z` anywhere gets GH derived hypsometrically."""
        # Isothermal-ish 260 K column, 3 levels.
        point_data = {
            1000: self._raw_no_z(t_k=288.0),
            850:  self._raw_no_z(t_k=278.0),
            500:  self._raw_no_z(t_k=255.0),
        }
        levels = build_pressure_levels_from_grib(point_data)
        heights = {pl.pressure_hpa: pl.geopotential_height_m for pl in levels}
        # All levels should now have GH populated.
        assert all(h is not None for h in heights.values())
        # Surface anchor near ISA ~110m at 1000 hPa.
        assert 50 < heights[1000] < 200
        # Altitudes must be monotonically increasing with decreasing pressure.
        assert heights[1000] < heights[850] < heights[500]
        # 500 hPa should be near the ISA ~5570m (±20% is plenty for a dry-air
        # hypsometric approximation with isothermal-ish mean T).
        assert 4500 < heights[500] < 6500

    def test_preserves_real_gh_when_present(self):
        """When `z` is present at some levels, those values are not overwritten."""
        point_data = {
            1000: {
                "raw_temperature_k": 288.0,
                "raw_geopotential_m2_s2": 9806.65,  # → 1000 m (unrealistic but distinct)
            },
            850: {"raw_temperature_k": 278.0},
        }
        levels = build_pressure_levels_from_grib(point_data)
        heights = {pl.pressure_hpa: pl.geopotential_height_m for pl in levels}
        # Anchor stays at the GRIB-provided 1000 m (not ISA-overridden).
        assert heights[1000] == pytest.approx(1000.0, abs=1.0)
        # Level above derived on top of that anchor.
        assert heights[850] > heights[1000]

    def test_skipped_when_temperature_missing(self):
        """If any level lacks T, hypsometric fill is skipped entirely."""
        point_data = {
            1000: {"raw_relative_humidity_pct": 50.0},  # no T
            850:  self._raw_no_z(t_k=278.0),
        }
        levels = build_pressure_levels_from_grib(point_data)
        # Both levels present but GH stays None because column can't be integrated.
        heights = {pl.pressure_hpa: pl.geopotential_height_m for pl in levels}
        assert heights[1000] is None
        assert heights[850] is None

    def test_noop_when_all_levels_have_real_gh(self):
        """Real GH values are preserved exactly when every level has `z`."""
        point_data = {
            1000: {"raw_temperature_k": 288.0, "raw_geopotential_m2_s2": 981.0},
            850:  {"raw_temperature_k": 278.0, "raw_geopotential_m2_s2": 14000.0},
        }
        levels = build_pressure_levels_from_grib(point_data)
        heights = {pl.pressure_hpa: pl.geopotential_height_m for pl in levels}
        assert heights[1000] == pytest.approx(981.0 / 9.80665, abs=1e-6)
        assert heights[850] == pytest.approx(14000.0 / 9.80665, abs=1e-6)

    def test_gh_full_coverage_no_t_required(self):
        """When every level has delivered `gh`, no T is needed and values are preserved."""
        # Simulate the post-amendment ECMWF full-coverage path: every level
        # carries geopotential_height_m directly from the GRIB, no temperature.
        point_data = {
            1000: {"geopotential_height_m": 110.0},
            850:  {"geopotential_height_m": 1475.0},
            500:  {"geopotential_height_m": 5570.0},
        }
        levels = build_pressure_levels_from_grib(point_data)
        heights = {pl.pressure_hpa: pl.geopotential_height_m for pl in levels}
        assert heights[1000] == pytest.approx(110.0)
        assert heights[850] == pytest.approx(1475.0)
        assert heights[500] == pytest.approx(5570.0)


class TestModelSurfaceHeight:
    """#487: the model's own orography, read off its geopotential column."""

    @staticmethod
    def _levels():
        from weatherbrief.models.analysis import PressureLevelData

        return [
            PressureLevelData(pressure_hpa=1000, temperature_c=15.0,
                              relative_humidity_pct=50.0,
                              geopotential_height_m=110.0),
            PressureLevelData(pressure_hpa=925, temperature_c=10.0,
                              relative_humidity_pct=50.0,
                              geopotential_height_m=760.0),
            PressureLevelData(pressure_hpa=850, temperature_c=5.0,
                              relative_humidity_pct=50.0,
                              geopotential_height_m=1460.0),
        ]

    def test_interpolates_between_bracketing_levels(self):
        from weatherbrief.fetch.grib.decode import model_surface_height_m

        # sp = 925 hPa → exactly the 925 level's height.
        assert model_surface_height_m(self._levels(), 925.0) == pytest.approx(
            760.0, abs=1.0
        )
        # Between 1000 and 925: strictly inside the bracket, monotone.
        mid = model_surface_height_m(self._levels(), 960.0)
        assert 110.0 < mid < 760.0

    def test_extends_below_the_lowest_level(self):
        """Sea-level terrain: sp is higher-pressure than any level, so the
        column is extended downward hypsometrically."""
        from weatherbrief.fetch.grib.decode import model_surface_height_m

        z = model_surface_height_m(self._levels(), 1013.0)
        assert z is not None
        assert z < 110.0  # below the 1000 hPa level
        assert z > -100.0  # ~13 hPa is ~110 m, not kilometres

    def test_none_without_heights(self):
        from weatherbrief.fetch.grib.decode import model_surface_height_m
        from weatherbrief.models.analysis import PressureLevelData

        levels = [PressureLevelData(pressure_hpa=1000, temperature_c=15.0)]
        assert model_surface_height_m(levels, 1013.0) is None
        assert model_surface_height_m(self._levels(), 0.0) is None


class TestEnrichmentWiring:
    """The two helpers that connect decode to enrichment (#486/#487).

    Both encode ordering invariants that are load-bearing but were only
    documented in comments — the kind that survive review and then quietly
    break in a later refactor. These pin them.
    """

    @staticmethod
    def _section(gh_by_p: dict[int, float], valid: datetime):
        from weatherbrief.models.analysis import (
            HourlyForecast, ModelSource, PressureLevelData, RouteCrossSection,
            Waypoint, WaypointForecast,
        )

        hourly = HourlyForecast(
            time=valid,
            pressure_levels=[
                PressureLevelData(
                    pressure_hpa=p, temperature_c=15.0 - (1000 - p) * 0.05,
                    relative_humidity_pct=50.0, geopotential_height_m=z,
                )
                for p, z in sorted(gh_by_p.items(), reverse=True)
            ],
        )
        wp = Waypoint(icao="RP000", name="RP000", lat=51.0, lon=0.0)
        return RouteCrossSection(
            model=ModelSource.ECMWF, route_points=[], fetched_at=valid,
            point_forecasts=[
                WaypointForecast(
                    waypoint=wp, model=ModelSource.ECMWF,
                    fetched_at=valid, hourly=[hourly],
                )
            ],
        )

    def test_geopotential_reference_reads_the_outgoing_levels(self):
        from weatherbrief.fetch.grib import _geopotential_reference

        valid = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
        cs = self._section({1000: 110.0, 850: 1460.0}, valid)
        hourly = cs.point_forecasts[0].hourly[0]
        assert _geopotential_reference(hourly) == {1000: 110.0, 850: 1460.0}

    def test_reference_is_read_before_the_replacement_overwrites_it(self):
        """#486's load-bearing ordering invariant: the reference must be
        harvested from the hourly BEFORE its levels are replaced. If it ever
        reads post-replacement, an ICON column (no delivered geopotential)
        silently falls back to the ISA anchor."""
        from weatherbrief.fetch.grib import _replace_pressure_levels_from_grib
        from weatherbrief.models.analysis import ModelSource

        valid = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
        # Open-Meteo says 1000 hPa sits at 500 m — far from its ISA ~110 m.
        cs = self._section({1000: 500.0, 850: 1850.0}, valid)
        # Incoming GRIB column carries NO geopotential (the ICON case).
        decoded = [{
            1000: {"raw_temperature_k": 288.0, "raw_relative_humidity_pct": 50.0},
            850: {"raw_temperature_k": 278.0, "raw_relative_humidity_pct": 50.0},
        }]
        replaced = _replace_pressure_levels_from_grib(
            [cs], [], [], decoded,
            valid_utc=valid, model_source=ModelSource.ICON,
        )
        assert replaced == 1
        heights = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in cs.point_forecasts[0].hourly[0].pressure_levels
        }
        # Anchored on the reference, not on ISA (~110 m at 1000 hPa).
        assert heights[1000] == pytest.approx(500.0)
        assert heights[850] > 1000.0

    def test_fill_ecmwf_orography_computes_and_memoizes(self):
        from weatherbrief.fetch.grib import _fill_ecmwf_orography

        valid = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
        cs = self._section({1000: 110.0, 925: 760.0, 850: 1460.0}, valid)
        cache: list[float | None] = [None]
        _fill_ecmwf_orography(
            cache, [cs], [{"surface_pressure_pa": 92500.0}], [True], valid,
        )
        assert cache[0] == pytest.approx(760.0, abs=1.0)

        # Terrain is time-invariant: a later step must not recompute it.
        _fill_ecmwf_orography(
            cache, [cs], [{"surface_pressure_pa": 100000.0}], [True], valid,
        )
        assert cache[0] == pytest.approx(760.0, abs=1.0)

    def test_fill_ecmwf_orography_leaves_unresolvable_points_none(self):
        """An uncovered point stays None, and build_ecmwf_cloud_diagnostics
        then drops the AGL freezing level rather than mislabelling it."""
        from weatherbrief.fetch.grib import _fill_ecmwf_orography

        valid = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)
        cs = self._section({1000: 110.0, 925: 760.0}, valid)
        cache: list[float | None] = [None]
        _fill_ecmwf_orography(cache, [cs], [{}], [False], valid)
        assert cache[0] is None
        # Covered but no surface pressure in the payload → still None.
        _fill_ecmwf_orography(cache, [cs], [{}], [True], valid)
        assert cache[0] is None


class TestGeopotentialReferenceAnchor:
    """#486: the reconstruction anchors on a real height, not ISA.

    DWD ships no geopotential on ICON model levels, so the whole ICON column
    is reconstructed. Anchoring it on the ISA altitude of its surface-most
    pressure level leaves the column offset by that surface's deviation from
    ISA — 100–300 m in a deep low or a cold column. The offset is uniform:
    thicknesses come from the integration and are unaffected.
    """

    @staticmethod
    def _column() -> dict[int, dict[str, float]]:
        return {
            1000: {"raw_temperature_k": 288.0, "raw_relative_humidity_pct": 50.0},
            850: {"raw_temperature_k": 278.0, "raw_relative_humidity_pct": 50.0},
            500: {"raw_temperature_k": 255.0, "raw_relative_humidity_pct": 50.0},
        }

    def test_reference_replaces_isa_anchor(self):
        isa = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in build_pressure_levels_from_grib(self._column())
        }
        # A deep low: the 1000 hPa surface sits 250 m below its ISA height.
        anchored = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in build_pressure_levels_from_grib(
                self._column(), {1000: isa[1000] - 250.0},
            )
        }
        assert anchored[1000] == pytest.approx(isa[1000] - 250.0)
        # Uniform offset — every level shifts by the same amount, so all
        # layer thicknesses are preserved exactly.
        for p in (850, 500):
            assert anchored[p] == pytest.approx(isa[p] - 250.0, abs=1e-6)

    def test_reference_above_the_surface_level_anchors_downward_too(self):
        """The datum need not be the surface-most level: levels BELOW it are
        integrated downward rather than left on the ISA datum."""
        isa = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in build_pressure_levels_from_grib(self._column())
        }
        anchored = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in build_pressure_levels_from_grib(
                self._column(), {850: isa[850] - 250.0},
            )
        }
        assert anchored[850] == pytest.approx(isa[850] - 250.0)
        assert anchored[1000] == pytest.approx(isa[1000] - 250.0, abs=1e-6)
        assert anchored[500] == pytest.approx(isa[500] - 250.0, abs=1e-6)

    def test_only_the_surface_most_reference_level_is_used(self):
        """The reference sets WHERE the column sits, never how thick its
        layers are — so a second, deliberately inconsistent reference level
        must not bend the profile."""
        isa = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in build_pressure_levels_from_grib(self._column())
        }
        anchored = {
            pl.pressure_hpa: pl.geopotential_height_m
            for pl in build_pressure_levels_from_grib(
                self._column(),
                {1000: isa[1000] - 250.0, 850: isa[850] + 900.0},
            )
        }
        for p in (1000, 850, 500):
            assert anchored[p] == pytest.approx(isa[p] - 250.0, abs=1e-6)

    def test_reference_never_overrides_a_delivered_height(self):
        """A real GRIB height beats the borrowed reference at the same level."""
        point_data = {
            1000: {"raw_temperature_k": 288.0, "raw_geopotential_m2_s2": 9806.65},
            850: {"raw_temperature_k": 278.0},
        }
        levels = build_pressure_levels_from_grib(point_data, {1000: 5.0})
        heights = {pl.pressure_hpa: pl.geopotential_height_m for pl in levels}
        assert heights[1000] == pytest.approx(1000.0, abs=1.0)

    def test_moist_column_is_thicker_than_dry(self):
        """Virtual temperature in the layer mean (#486): the same column at
        95 % RH must integrate thicker than at 5 %."""
        def _heights(rh: float) -> dict[int, float]:
            column = {
                p: {"raw_temperature_k": t, "raw_relative_humidity_pct": rh}
                for p, t in ((1000, 300.0), (850, 292.0), (500, 268.0))
            }
            return {
                pl.pressure_hpa: pl.geopotential_height_m
                for pl in build_pressure_levels_from_grib(column, {1000: 100.0})
            }

        dry, moist = _heights(5.0), _heights(95.0)
        assert moist[500] > dry[500]
        # Warm moist air, surface→500 hPa: tens of metres, not hundreds.
        assert 10.0 < (moist[500] - dry[500]) < 120.0
