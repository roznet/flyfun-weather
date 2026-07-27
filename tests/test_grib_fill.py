"""Tests for unified GRIB forward-fill (time axis) and diagnostics spatial interpolation."""

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.spatial_interpolation import (
    interpolate_diagnostics_spatially,
    interpolate_all_spatially,
)
from weatherbrief.fetch.grib.fill import (
    _gfs_window_length_hours,
    _lerp_wind,
    apply_gfs_rh_condensate_gate,
    propagate_all,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)


# ── Helpers ──────────────────────────────────────────────────────────

T0 = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)


def _make_route_points(n: int, spacing_nm: float = 10.0) -> list[RoutePoint]:
    return [
        RoutePoint(lat=50.0, lon=i * 0.1, distance_from_origin_nm=i * spacing_nm)
        for i in range(n)
    ]


def _make_diag(
    low_pct: float = 50, mid_pct: float = 30, high_pct: float = 10,
    low_base: float = 1000, low_top: float = 5000,
    mid_base: float = 6500, mid_top: float = 15000,
    ceiling: float = 1000,
) -> NWPCloudDiagnostics:
    return NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=low_pct, base_ft=low_base, top_ft=low_top),
        mid=NWPCloudLayerDiag(cover_pct=mid_pct, base_ft=mid_base, top_ft=mid_top),
        high=NWPCloudLayerDiag(cover_pct=high_pct),
        ceiling_ft=ceiling,
    )


def _make_hourly(
    time: datetime,
    diag: NWPCloudDiagnostics | None = None,
    clw: float | None = None,
    icmr: float | None = None,
) -> HourlyForecast:
    levels = [PressureLevelData(
        pressure_hpa=500,
        cloud_liquid_water_kg_kg=clw,
        ice_mixing_ratio_kg_kg=icmr,
    )] if clw is not None or icmr is not None else [
        PressureLevelData(pressure_hpa=500),
    ]
    return HourlyForecast(
        time=time,
        nwp_cloud_diagnostics=diag,
        pressure_levels=levels,
    )


def _make_cs_with_hourly(
    point_hourly: list[list[HourlyForecast]],
) -> RouteCrossSection:
    now = datetime.now(tz=timezone.utc)
    wpt = Waypoint(icao="XXXX", name="Test", lat=50.0, lon=0.0)
    point_forecasts = [
        WaypointForecast(
            waypoint=wpt, model=ModelSource.GFS, fetched_at=now,
            hourly=hourly_list,
        )
        for hourly_list in point_hourly
    ]
    return RouteCrossSection(
        model=ModelSource.GFS, route_points=[], fetched_at=now,
        point_forecasts=point_forecasts,
    )


# ── Forward-fill: cloud diagnostics ─────────────────────────────────

class TestForwardFillDiagnostics:
    def test_fills_gap_after_native_hour(self):
        """Diagnostics at T0 should forward-fill (value-equal copies, not
        shared references) to T0+1h."""
        diag = _make_diag()
        hourly = [
            _make_hourly(T0, diag=diag),
            _make_hourly(T0 + timedelta(hours=1)),  # gap
            _make_hourly(T0 + timedelta(hours=2)),  # gap
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        # Each gap hour is value-equal to the anchor but a distinct object so
        # downstream in-place mutation cannot leak across hours.
        hf = cs.point_forecasts[0].hourly
        assert hf[1].nwp_cloud_diagnostics == diag
        assert hf[2].nwp_cloud_diagnostics == diag
        assert hf[1].nwp_cloud_diagnostics is not diag
        assert hf[2].nwp_cloud_diagnostics is not diag
        assert hf[1].nwp_cloud_diagnostics is not hf[2].nwp_cloud_diagnostics

    def test_does_not_overwrite_existing(self):
        """Existing diagnostics should not be overwritten."""
        diag1 = _make_diag(low_pct=10)
        diag2 = _make_diag(low_pct=90)
        hourly = [
            _make_hourly(T0, diag=diag1),
            _make_hourly(T0 + timedelta(hours=1), diag=diag2),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert cs.point_forecasts[0].hourly[1].nwp_cloud_diagnostics is diag2

    def test_no_fill_before_first_anchor(self):
        """Hours before the first enriched hour stay None."""
        diag = _make_diag()
        hourly = [
            _make_hourly(T0),  # gap — no anchor yet
            _make_hourly(T0 + timedelta(hours=1), diag=diag),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert cs.point_forecasts[0].hourly[0].nwp_cloud_diagnostics is None


# ── Forward-fill: cloud water ────────────────────────────────────────

class TestForwardFillCloudWater:
    def test_fills_clw_gap(self):
        """CLW at T0 should forward-fill to T0+1h."""
        hourly = [
            _make_hourly(T0, clw=0.001, icmr=0.0002),
            _make_hourly(T0 + timedelta(hours=1)),  # gap
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        pl = cs.point_forecasts[0].hourly[1].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg == 0.001
        assert pl.ice_mixing_ratio_kg_kg == 0.0002

    def test_no_fill_before_first_anchor(self):
        """Pressure levels before the first enriched hour stay None."""
        hourly = [
            _make_hourly(T0),  # gap — no anchor
            _make_hourly(T0 + timedelta(hours=1), clw=0.001),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        pl = cs.point_forecasts[0].hourly[0].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg is None

    def test_updates_anchor_on_new_value(self):
        """A later native hour's CLW should replace the fill value."""
        hourly = [
            _make_hourly(T0, clw=0.001),
            _make_hourly(T0 + timedelta(hours=1), clw=0.005),
            _make_hourly(T0 + timedelta(hours=2)),  # gap
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        # Should fill with 0.005, not 0.001
        pl = cs.point_forecasts[0].hourly[2].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg == 0.005


# ── Spatial interpolation: diagnostics ───────────────────────────────

class TestSpatialDiagnostics:
    def test_interpolates_middle_point(self):
        """Point 1 has no diagnostics; points 0 and 2 do → linear interp."""
        rps = _make_route_points(3)
        diag0 = _make_diag(low_pct=40, ceiling=2000)
        diag2 = _make_diag(low_pct=80, ceiling=4000)

        cs = _make_cs_with_hourly([
            [_make_hourly(T0, diag=diag0)],
            [_make_hourly(T0)],  # gap
            [_make_hourly(T0, diag=diag2)],
        ])
        filled = interpolate_diagnostics_spatially([cs], rps)

        assert filled == 1
        result = cs.point_forecasts[1].hourly[0].nwp_cloud_diagnostics
        assert result is not None
        assert result.low.cover_pct == pytest.approx(60.0)  # midpoint
        assert result.ceiling_ft == pytest.approx(3000.0)

    def test_no_fill_edge_gap(self):
        """First point missing, only right neighbor → skip."""
        rps = _make_route_points(3)
        diag = _make_diag()
        cs = _make_cs_with_hourly([
            [_make_hourly(T0)],  # gap
            [_make_hourly(T0, diag=diag)],
            [_make_hourly(T0, diag=diag)],
        ])
        filled = interpolate_diagnostics_spatially([cs], rps)
        assert filled == 0

    def test_respects_max_gap(self):
        """Gap > max_gap_nm → skip."""
        rps = _make_route_points(3, spacing_nm=60.0)  # 120nm total gap
        diag = _make_diag()
        cs = _make_cs_with_hourly([
            [_make_hourly(T0, diag=diag)],
            [_make_hourly(T0)],  # gap
            [_make_hourly(T0, diag=diag)],
        ])
        filled = interpolate_diagnostics_spatially([cs], rps, max_gap_nm=100.0)
        assert filled == 0

    def test_interpolates_layer_base_top(self):
        """Verify layer base/top are linearly interpolated."""
        rps = _make_route_points(3)
        diag0 = _make_diag(low_base=1000, low_top=5000)
        diag2 = _make_diag(low_base=2000, low_top=7000)
        cs = _make_cs_with_hourly([
            [_make_hourly(T0, diag=diag0)],
            [_make_hourly(T0)],
            [_make_hourly(T0, diag=diag2)],
        ])
        interpolate_diagnostics_spatially([cs], rps)

        result = cs.point_forecasts[1].hourly[0].nwp_cloud_diagnostics
        assert result is not None
        assert result.low.base_ft == pytest.approx(1500.0)
        assert result.low.top_ft == pytest.approx(6000.0)


# ── Unified entry point ──────────────────────────────────────────────

class TestInterpolateAllSpatially:
    def test_fills_both_clw_and_diagnostics(self):
        """interpolate_all_spatially fills both field types."""
        rps = _make_route_points(3)
        diag = _make_diag()
        cs = _make_cs_with_hourly([
            [HourlyForecast(
                time=T0, nwp_cloud_diagnostics=diag,
                pressure_levels=[PressureLevelData(pressure_hpa=500, cloud_liquid_water_kg_kg=0.001)],
            )],
            [HourlyForecast(
                time=T0,
                pressure_levels=[PressureLevelData(pressure_hpa=500)],
            )],
            [HourlyForecast(
                time=T0, nwp_cloud_diagnostics=diag,
                pressure_levels=[PressureLevelData(pressure_hpa=500, cloud_liquid_water_kg_kg=0.003)],
            )],
        ])
        interpolate_all_spatially([cs], rps)

        # CLW filled
        pl = cs.point_forecasts[1].hourly[0].pressure_levels[0]
        assert pl.cloud_liquid_water_kg_kg == pytest.approx(0.002)

        # Diagnostics filled
        diag_result = cs.point_forecasts[1].hourly[0].nwp_cloud_diagnostics
        assert diag_result is not None


# ---------------------------------------------------------------------------
# Pressure-level forward-fill (3h gap fill for GRIB sounding replacement)
# ---------------------------------------------------------------------------


def _make_grib_levels(n: int = 24) -> list[PressureLevelData]:
    """Create a GRIB-replaced pressure level list with more levels than Open-Meteo."""
    return [
        PressureLevelData(
            pressure_hpa=1000 - i * 25,
            temperature_c=15.0 - i * 2,
            cloud_liquid_water_kg_kg=1e-5,
        )
        for i in range(n)
    ]


def _make_open_meteo_levels(n: int = 19) -> list[PressureLevelData]:
    """Create an Open-Meteo pressure level list (fewer levels, no CLW)."""
    levels = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500,
              400, 300, 250, 200, 150, 100, 70, 50, 30]
    return [PressureLevelData(pressure_hpa=p, temperature_c=10.0) for p in levels[:n]]


class TestForwardFillPressureLevels:
    """Tests for _forward_fill_pressure_levels (3h gap fill)."""

    def test_fills_gap_between_grib_hours(self):
        """Hours between two GRIB hours get the preceding GRIB levels."""
        hourly = [
            HourlyForecast(time=T0 + timedelta(hours=0), pressure_levels=_make_grib_levels()),
            HourlyForecast(time=T0 + timedelta(hours=1), pressure_levels=_make_open_meteo_levels()),
            HourlyForecast(time=T0 + timedelta(hours=2), pressure_levels=_make_open_meteo_levels()),
            HourlyForecast(time=T0 + timedelta(hours=3), pressure_levels=_make_grib_levels()),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert len(cs.point_forecasts[0].hourly[1].pressure_levels) == 24
        assert len(cs.point_forecasts[0].hourly[2].pressure_levels) == 24

    def test_no_fill_before_first_grib_hour(self):
        """Hours before the first GRIB hour keep Open-Meteo levels."""
        hourly = [
            HourlyForecast(time=T0 + timedelta(hours=0), pressure_levels=_make_open_meteo_levels()),
            HourlyForecast(time=T0 + timedelta(hours=1), pressure_levels=_make_open_meteo_levels()),
            HourlyForecast(time=T0 + timedelta(hours=2), pressure_levels=_make_grib_levels()),
            HourlyForecast(time=T0 + timedelta(hours=3), pressure_levels=_make_grib_levels()),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert len(cs.point_forecasts[0].hourly[0].pressure_levels) == 19
        assert len(cs.point_forecasts[0].hourly[1].pressure_levels) == 19

    def test_no_fill_after_last_grib_hour(self):
        """Hours after the last GRIB hour keep Open-Meteo levels."""
        hourly = [
            HourlyForecast(time=T0 + timedelta(hours=0), pressure_levels=_make_grib_levels()),
            HourlyForecast(time=T0 + timedelta(hours=1), pressure_levels=_make_grib_levels()),
            HourlyForecast(time=T0 + timedelta(hours=2), pressure_levels=_make_open_meteo_levels()),
            HourlyForecast(time=T0 + timedelta(hours=3), pressure_levels=_make_open_meteo_levels()),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert len(cs.point_forecasts[0].hourly[2].pressure_levels) == 19
        assert len(cs.point_forecasts[0].hourly[3].pressure_levels) == 19

    def test_no_fill_when_all_same_levels(self):
        """No fill when all hours have the same level count."""
        hourly = [
            HourlyForecast(time=T0 + timedelta(hours=i), pressure_levels=_make_open_meteo_levels())
            for i in range(4)
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        for h in cs.point_forecasts[0].hourly:
            assert len(h.pressure_levels) == 19

    def test_single_grib_hour_no_fill(self):
        """A single GRIB hour with no second anchor doesn't fill anything."""
        hourly = [
            HourlyForecast(time=T0 + timedelta(hours=0), pressure_levels=_make_open_meteo_levels()),
            HourlyForecast(time=T0 + timedelta(hours=1), pressure_levels=_make_grib_levels()),
            HourlyForecast(time=T0 + timedelta(hours=2), pressure_levels=_make_open_meteo_levels()),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])

        assert len(cs.point_forecasts[0].hourly[0].pressure_levels) == 19
        assert len(cs.point_forecasts[0].hourly[2].pressure_levels) == 19


# ---------------------------------------------------------------------------
# Issue #148 — GFS window-midpoint cloud-diag interp + RH/condensate gate
# ---------------------------------------------------------------------------


class TestGfsWindowLength:
    """GFS averaging windows reset at each multiple of 6 and grow to the next.

    Expected values below are transcribed from a live NCEP index
    (``gfs.20260722/00/atmos/gfs.t00z.pgrb2.0p25.fNNN.idx``,
    ``LCDC:low cloud layer``), not derived from the implementation (#480).
    """

    @pytest.mark.parametrize("fhour,expected", [
        # First 6-hour cycle: 0-1, 0-2, … 0-6.
        (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6),
        # Second cycle restarts at 6: 6-7, 6-8, 6-9, … 6-12.
        (7, 1), (8, 2), (9, 3), (12, 6),
        # Hourly region, end of range.
        (118, 4), (119, 5), (120, 6),
        # 3-hourly region past f120 — widths alternate 3 / 6.
        (123, 3), (126, 6), (129, 3), (132, 6),
        # Far lead times, also read off the live index rather than extrapolated
        # from the formula: f180 = 174-180, f240 = 234-240, f384 = 378-384.
        (180, 6), (240, 6), (384, 6),
        (0, 0),  # analysis — no window
    ])
    def test_window_length(self, fhour, expected):
        assert _gfs_window_length_hours(fhour) == expected

    @pytest.mark.parametrize("fhour", list(range(1, 200)))
    def test_window_never_exceeds_six_hours(self, fhour):
        """The window always starts at a multiple of 6, so it can never be
        wider than 6 h nor narrower than 1 h."""
        assert 1 <= _gfs_window_length_hours(fhour) <= 6

    @pytest.mark.parametrize("fhour", [6, 12, 60, 120, 126, 132])
    def test_window_start_is_a_multiple_of_six(self, fhour):
        start = fhour - _gfs_window_length_hours(fhour)
        assert start % 6 == 0


def _make_cs_with_hourly_model(
    point_hourly: list[list[HourlyForecast]],
    model: ModelSource,
) -> RouteCrossSection:
    now = datetime.now(tz=timezone.utc)
    wpt = Waypoint(icao="XXXX", name="Test", lat=50.0, lon=0.0)
    point_forecasts = [
        WaypointForecast(
            waypoint=wpt, model=model, fetched_at=now,
            hourly=hourly_list,
        )
        for hourly_list in point_hourly
    ]
    return RouteCrossSection(
        model=model, route_points=[], fetched_at=now,
        point_forecasts=point_forecasts,
    )


class TestGfsMidpointInterp:
    """pt11-style regression: midpoint anchoring collapses the phantom layer.

    init = 2026-05-12 00z, target hour = 134 → step f132 anchor at 12z
    (window 126-132, i.e. 06-12z, midpoint 09:00z) reading 100% mid, step
    f135 at 15z (window 132-135, i.e. 12-15z, midpoint 13:30z) reading 0%.
    Forward-fill would smear 100% across 13z/14z; midpoint interp collapses
    to 0% at 14z (past the f135 midpoint).

    Note the two windows are NOT the same width. NCEP resets the averaging
    window at each multiple of 6 and lets it grow, so f132 spans 6 h while
    f135 spans 3 h — verified against a live index (#480). The original
    version of this test assumed a uniform 3 h window and therefore put the
    f132 midpoint at 10:30z.
    """

    def _build_pt11_section(self) -> tuple[datetime, RouteCrossSection]:
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        # 12z (f132, 100%), 13z gap, 14z gap, 15z (f135, 0%), 16z gap, 17z gap,
        # 18z (f138, 0%). Step span = 3 h; window midpoints at 09:00 (f132,
        # window 126-132) and 13:30 (f135, window 132-135).
        diag_high = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=100.0, base_ft=18000, top_ft=22200),
        )
        diag_zero = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=0.0))
        diag_zero_2 = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=0.0))
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=132), diag=diag_high),
            _make_hourly(gfs_init + timedelta(hours=133)),
            _make_hourly(gfs_init + timedelta(hours=134)),
            _make_hourly(gfs_init + timedelta(hours=135), diag=diag_zero),
            _make_hourly(gfs_init + timedelta(hours=136)),
            _make_hourly(gfs_init + timedelta(hours=137)),
            _make_hourly(gfs_init + timedelta(hours=138), diag=diag_zero_2),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        return gfs_init, cs

    def test_phantom_layer_collapsed_at_14z(self):
        gfs_init, cs = self._build_pt11_section()
        propagate_all([cs], [], gfs_init=gfs_init)
        hourly = cs.point_forecasts[0].hourly

        # 12z (native f132): the published 100% is the mean over 06-12z,
        # centred on 09:00z. Since #481 the native step is re-labelled too —
        # 12z sits 3.0 h past that midpoint on the 4.5 h span to f135's
        # 13:30z midpoint → 100 - 100*(3.0/4.5) = 33.3%.
        assert hourly[0].nwp_cloud_diagnostics.mid.cover_pct == pytest.approx(
            100 - 100 * 3.0 / 4.5, abs=0.1
        )
        # 14z (gap, past f135 midpoint 13:30): below the 5% drop threshold.
        mid_14z = hourly[2].nwp_cloud_diagnostics.mid
        assert mid_14z.cover_pct is None  # dropped layer
        assert mid_14z.base_ft is None
        # 15z native (f135): its own 0% and the next knot's 0% agree.
        assert hourly[3].nwp_cloud_diagnostics.mid.cover_pct == 0.0
        # 18z is the last anchor — no upper knot, so its published value
        # stands rather than being extrapolated.
        assert hourly[6].nwp_cloud_diagnostics.mid.cover_pct == 0.0

    def test_intermediate_hour_interpolates_between_midpoints(self):
        """13z sits between midpoints 09:00 (100%) and 13:30 (0%) →
        frac = 4.0/4.5 = 0.889 → 11.1%. Above drop threshold → layer kept."""
        gfs_init, cs = self._build_pt11_section()
        propagate_all([cs], [], gfs_init=gfs_init)
        mid_13z = cs.point_forecasts[0].hourly[1].nwp_cloud_diagnostics.mid
        assert mid_13z.cover_pct == pytest.approx(100 - 100 * 4.0 / 4.5, abs=0.1)
        # Geometry held over from higher-cover (prev) endpoint.
        assert mid_13z.base_ft == 18000
        assert mid_13z.top_ft == 22200

    def test_fallback_path_unchanged_when_gfs_init_none(self):
        """Without gfs_init, behavior matches forward-fill (back-compat)."""
        gfs_init, cs = self._build_pt11_section()
        propagate_all([cs], [])  # no gfs_init
        # 14z forward-filled with 100% (the bug we're fixing).
        assert (
            cs.point_forecasts[0].hourly[2].nwp_cloud_diagnostics.mid.cover_pct
            == 100.0
        )

    def test_ercoz_low_layer_survives(self):
        """Negative control: stable low cover between two equal anchors stays."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        # Both anchors carry the same 86.9% low layer @ 4781-10383 ft.
        diag_a = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=86.9, base_ft=4781, top_ft=10383),
        )
        diag_b = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=86.9, base_ft=4781, top_ft=10383),
        )
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=129), diag=diag_a),
            _make_hourly(gfs_init + timedelta(hours=130)),
            _make_hourly(gfs_init + timedelta(hours=131)),
            _make_hourly(gfs_init + timedelta(hours=132), diag=diag_b),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        for h in cs.point_forecasts[0].hourly:
            assert h.nwp_cloud_diagnostics is not None
            assert h.nwp_cloud_diagnostics.low.cover_pct == pytest.approx(86.9)
            assert h.nwp_cloud_diagnostics.low.base_ft == 4781

    def test_ecmwf_section_uses_forward_fill_not_midpoint(self):
        """ICON / ECMWF cloud cover is instantaneous — midpoint interp must
        NOT fire for them even when gfs_init is provided."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        diag = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=100.0, base_ft=18000, top_ft=22000),
        )
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=132), diag=diag),
            _make_hourly(gfs_init + timedelta(hours=133)),  # gap
            _make_hourly(gfs_init + timedelta(hours=134)),  # gap
            _make_hourly(gfs_init + timedelta(hours=135),
                         diag=NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=0.0))),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.ECMWF)
        propagate_all([cs], [], gfs_init=gfs_init)
        # Forward-fill semantics: gap hours inherit prev anchor's 100%.
        assert cs.point_forecasts[0].hourly[1].nwp_cloud_diagnostics.mid.cover_pct == 100.0
        assert cs.point_forecasts[0].hourly[2].nwp_cloud_diagnostics.mid.cover_pct == 100.0

    def test_post_last_anchor_forward_fills(self):
        """Hours past the last anchor have no upper bracket → forward-fill."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        diag = NWPCloudDiagnostics(
            high=NWPCloudLayerDiag(cover_pct=50.0, base_ft=30000, top_ft=40000),
        )
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=132), diag=diag),
            _make_hourly(gfs_init + timedelta(hours=133)),
            _make_hourly(gfs_init + timedelta(hours=134)),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        for h in cs.point_forecasts[0].hourly[1:]:
            assert h.nwp_cloud_diagnostics.high.cover_pct == 50.0

    def test_post_last_anchor_forward_fill_does_not_share_object(self):
        """Each forward-filled hour gets its own copy, so an in-place mutation
        on one hour cannot leak into the others."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        diag = NWPCloudDiagnostics(
            high=NWPCloudLayerDiag(cover_pct=50.0, base_ft=30000, top_ft=40000),
        )
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=132), diag=diag),
            _make_hourly(gfs_init + timedelta(hours=133)),
            _make_hourly(gfs_init + timedelta(hours=134)),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        hf = cs.point_forecasts[0].hourly
        assert hf[1].nwp_cloud_diagnostics is not hf[2].nwp_cloud_diagnostics
        assert hf[1].nwp_cloud_diagnostics is not hf[0].nwp_cloud_diagnostics

    def test_pre_f120_mixed_window_clamp(self):
        """f001 (window 0-1) → f003 (window 0-3), gap hour at f002.

        The two published windows are NESTED, so f003's knot is the mean over
        (1, 3] — ``(40·3 − 80·1)/2 = 20 %`` — centred at f002. f002 therefore
        sits ON the last knot: nothing above it to interpolate toward, so it
        clamps wholesale onto that knot, taking the cover AND the geometry of
        the step the cover came from. Catches a regression where short-haul
        flights in the first few forecast hours mis-handle the cadence
        transition.

        The covers are chosen to be physically REALIZABLE as nested means:
        mean(0-1)=80 forces mean(0-3) into [26.7, 93.3], since the two
        remaining hours can only contribute 0-100 % each. The earlier form of
        this test used 80 → 10, which implies hours 1-3 averaged −25 % cover
        and cannot arise from real GRIB.
        """
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        diag_f001 = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=80.0, base_ft=10000, top_ft=18000),
        )
        diag_f003 = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=40.0, base_ft=11000, top_ft=19000),
        )
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=1), diag=diag_f001),
            _make_hourly(gfs_init + timedelta(hours=2)),  # gap
            _make_hourly(gfs_init + timedelta(hours=3), diag=diag_f003),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        mid_f002 = cs.point_forecasts[0].hourly[1].nwp_cloud_diagnostics.mid
        assert mid_f002.cover_pct == pytest.approx(20.0)
        assert mid_f002.base_ft == 11000
        assert mid_f002.top_ft == 19000


class TestGfsNativeStepRelabelling:
    """#481: hourly GFS anchors get midpoint-aligned too.

    Inside f120 GFS publishes every hour, so there are no gap hours and the
    old implementation was a complete no-op — every native step carried its
    window mean labelled at the window's END. The lag sawtooths with the
    window width: 3 h at f006/f012, 0.5 h at f001/f007.
    """

    @staticmethod
    def _hourly_anchors(
        gfs_init: datetime, covers: dict[int, float],
    ) -> RouteCrossSection:
        hourly = [
            _make_hourly(
                gfs_init + timedelta(hours=f),
                diag=NWPCloudDiagnostics(
                    mid=NWPCloudLayerDiag(
                        cover_pct=c, base_ft=10000, top_ft=18000,
                    ),
                ),
            )
            for f, c in sorted(covers.items())
        ]
        return _make_cs_with_hourly_model([hourly], ModelSource.GFS)

    def test_hourly_anchors_are_realigned(self):
        """f004..f007 hourly, covers 100/100/100/20.

        The windows are nested (0-4, 0-5, 0-6) then reset (6-7), so the
        DISJOINT knots are ``[0,4]@2.0``, ``(4,5]@4.5``, ``(5,6]@5.5`` and
        ``[6,7]@6.5``, carrying 100/100/100/20.

        f004 and f005 must stay at 100: three nested means of 100 % ending at
        4, 5 and 6 pin the instantaneous cover at 100 % across the whole
        0-6 h span (a mean of 100 cannot be reached with any hour below it).
        f006 is the discontinuity — it sits halfway between the (5,6] and
        [6,7] knots, so it splits them: 60 %.
        """
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        cs = self._hourly_anchors(
            gfs_init, {4: 100.0, 5: 100.0, 6: 100.0, 7: 20.0},
        )
        propagate_all([cs], [], gfs_init=gfs_init)
        covers = [
            h.nwp_cloud_diagnostics.mid.cover_pct
            for h in cs.point_forecasts[0].hourly
        ]
        assert covers == pytest.approx([100.0, 100.0, 60.0, 20.0], abs=0.1)

    def test_reset_boundary_does_not_pull_cloud_backwards(self):
        """#481 regression guard: a deck arriving at the cycle reset must not
        appear BEFORE it exists.

        Covers are 0 % for f001..f006 (windows 0-1 … 0-6) then 100 % from
        f007. A published mean of 0 % over 0-6 h proves the cover is 0
        throughout that span — cover cannot go negative to compensate. So
        f004/f005 must report 0 % and carry no geometry.

        Anchoring the NESTED means at their own midpoints put f001..f006's
        knots at 0.5-3.0 h and the next cycle's first knot at 6.5 h, leaving
        hours 4, 5 and 6 to be interpolated across that 3.5 h hole — which
        reported 28.6 / 57.1 / 85.7 % with an invented base, three hours of
        phantom overcast. De-averaging removes the hole.
        """
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        covers = {f: 0.0 for f in range(1, 7)}
        covers.update({f: 100.0 for f in range(7, 13)})
        cs = self._hourly_anchors(gfs_init, covers)
        propagate_all([cs], [], gfs_init=gfs_init)
        hourly = cs.point_forecasts[0].hourly
        for i in range(0, 5):  # f001..f005
            band = hourly[i].nwp_cloud_diagnostics.mid
            assert band.cover_pct in (None, 0.0), f"f{i + 1:03d} invented cloud"
            assert band.base_ft is None, f"f{i + 1:03d} invented geometry"
        # f006 is the discontinuity itself and splits the two knots.
        assert hourly[5].nwp_cloud_diagnostics.mid.cover_pct == pytest.approx(50.0)
        assert hourly[6].nwp_cloud_diagnostics.mid.cover_pct == pytest.approx(100.0)

    def test_mid_cycle_transition_is_recovered(self):
        """A change strictly INSIDE a cycle is recoverable from the nested
        means, and de-averaging recovers it.

        Overcast that clears at t=4 publishes mean(0-4)=100, mean(0-5)=80,
        mean(0-6)=66.7. Differencing gives (4,5] = 0 % and (5,6] = 0 %, so
        f005/f006 correctly read clear. Reading the published nested means at
        face value instead reports 80 % and 66.7 % — a deck held ~2 h past
        its own forecast clearance.

        f003 is still fully inside the overcast (100 %); f004 is the
        transition instant itself and splits its two knots (50 %).
        """
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        cs = self._hourly_anchors(
            gfs_init,
            {1: 100.0, 2: 100.0, 3: 100.0, 4: 100.0, 5: 80.0, 6: 200.0 / 3.0},
        )
        propagate_all([cs], [], gfs_init=gfs_init)
        hourly = cs.point_forecasts[0].hourly
        assert hourly[2].nwp_cloud_diagnostics.mid.cover_pct == pytest.approx(
            100.0, abs=0.1
        )
        assert hourly[3].nwp_cloud_diagnostics.mid.cover_pct == pytest.approx(
            50.0, abs=0.1
        )
        for i in (4, 5):  # f005, f006 — cleared, so the band drops out
            band = hourly[i].nwp_cloud_diagnostics.mid
            assert band.cover_pct in (None, 0.0)
            assert band.base_ft is None

    def test_deaveraged_value_is_clamped_to_range(self):
        """Differencing amplifies packing noise, so the result is clamped.

        mean(0-1)=80 with mean(0-3)=10 is not physically realizable (it
        implies −25 % over hours 1-3). The knot clamps to 0 rather than
        emitting a negative cover into the analysis stage.
        """
        from weatherbrief.fetch.grib.fill import _deaverage_diag

        prev = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=80.0))
        cur = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=10.0))
        assert _deaverage_diag(prev, cur, 1, 3).mid.cover_pct == 0.0

        prev = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=0.0))
        cur = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=90.0))
        assert _deaverage_diag(prev, cur, 1, 3).mid.cover_pct == 100.0

    def test_deaveraging_preserves_published_geometry(self):
        """Cover is differenced; base/top are not — they have no de-averaged
        form and are carried from the published anchor."""
        from weatherbrief.fetch.grib.fill import _deaverage_diag

        prev = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=50.0, base_ft=3000, top_ft=9000),
        )
        cur = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=60.0, base_ft=4000, top_ft=11000),
        )
        out = _deaverage_diag(prev, cur, 1, 2)
        assert out.mid.cover_pct == pytest.approx(70.0)  # 60*2 - 50*1
        assert (out.mid.base_ft, out.mid.top_ft) == (4000, 11000)

    def test_missing_neighbour_falls_back_to_published(self):
        """A decode gap on the previous step makes the difference undefined;
        the anchor keeps what NCEP published rather than inventing a value."""
        from weatherbrief.fetch.grib.fill import _deaverage_diag

        prev = NWPCloudDiagnostics(mid=NWPCloudLayerDiag())
        cur = NWPCloudDiagnostics(mid=NWPCloudLayerDiag(cover_pct=60.0))
        assert _deaverage_diag(prev, cur, 1, 2).mid.cover_pct is None

    def test_steady_state_is_unchanged(self):
        """Negative control: when neighbouring windows agree, re-labelling
        must not perturb anything."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        cs = self._hourly_anchors(
            gfs_init, {4: 86.9, 5: 86.9, 6: 86.9, 7: 86.9},
        )
        propagate_all([cs], [], gfs_init=gfs_init)
        for h in cs.point_forecasts[0].hourly:
            assert h.nwp_cloud_diagnostics.mid.cover_pct == pytest.approx(86.9)
            assert h.nwp_cloud_diagnostics.mid.base_ft == 10000

    def test_instantaneous_fields_survive_at_native_steps(self):
        """Only the averaged half moves. ceiling_ft / total_cover_pct are
        instantaneous in pgrb2 and are published AT the step, so a native
        hour must report exactly what NCEP filed."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        hourly = [
            _make_hourly(
                gfs_init + timedelta(hours=f),
                diag=NWPCloudDiagnostics(
                    mid=NWPCloudLayerDiag(cover_pct=cover, base_ft=10000,
                                          top_ft=18000),
                    ceiling_ft=ceiling,
                    total_cover_pct=cover,
                ),
            )
            for f, cover, ceiling in (
                (4, 100.0, 1200.0), (5, 100.0, 1500.0),
                (6, 100.0, 4000.0), (7, 20.0, 9000.0),
            )
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        hf = cs.point_forecasts[0].hourly
        assert [h.nwp_cloud_diagnostics.ceiling_ft for h in hf] == [
            1200.0, 1500.0, 4000.0, 9000.0,
        ]
        assert [h.nwp_cloud_diagnostics.total_cover_pct for h in hf] == [
            100.0, 100.0, 100.0, 20.0,
        ]
        # …while the averaged band at f006 did move.
        assert hf[2].nwp_cloud_diagnostics.mid.cover_pct < 100.0

    def test_missing_neighbour_cover_keeps_published_band(self):
        """A decode gap on the next step must not delete a decoded band."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        hourly = [
            _make_hourly(
                gfs_init + timedelta(hours=4),
                diag=NWPCloudDiagnostics(
                    mid=NWPCloudLayerDiag(cover_pct=90.0, base_ft=10000,
                                          top_ft=18000),
                ),
            ),
            _make_hourly(
                gfs_init + timedelta(hours=5),
                diag=NWPCloudDiagnostics(mid=NWPCloudLayerDiag()),
            ),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        mid_f004 = cs.point_forecasts[0].hourly[0].nwp_cloud_diagnostics.mid
        assert mid_f004.cover_pct == 90.0
        assert mid_f004.base_ft == 10000

    def test_no_realignment_without_gfs_init(self):
        """Back-compat: the non-GFS / no-init path leaves anchors alone."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        cs = self._hourly_anchors(
            gfs_init, {4: 100.0, 5: 100.0, 6: 100.0, 7: 20.0},
        )
        propagate_all([cs], [])
        covers = [
            h.nwp_cloud_diagnostics.mid.cover_pct
            for h in cs.point_forecasts[0].hourly
        ]
        assert covers == [100.0, 100.0, 100.0, 20.0]


class TestGfsClwLinearInterp:
    """CLW/ICMR are instantaneous — linear interp between native step times."""

    def test_clw_interpolated_between_anchors(self):
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        # f120 native at 12z with clw=1e-4; f123 native at 15z with clw=4e-4;
        # 13z and 14z are gap hours → linear interp 2e-4 and 3e-4.
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=120), clw=1e-4, icmr=0.0),
            _make_hourly(gfs_init + timedelta(hours=121)),
            _make_hourly(gfs_init + timedelta(hours=122)),
            _make_hourly(gfs_init + timedelta(hours=123), clw=4e-4, icmr=0.0),
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        pl1 = cs.point_forecasts[0].hourly[1].pressure_levels[0]
        pl2 = cs.point_forecasts[0].hourly[2].pressure_levels[0]
        assert pl1.cloud_liquid_water_kg_kg == pytest.approx(2e-4)
        assert pl2.cloud_liquid_water_kg_kg == pytest.approx(3e-4)

    def test_clw_forward_fills_when_gfs_init_none(self):
        """Without gfs_init, falls back to forward-fill (back-compat)."""
        hourly = [
            _make_hourly(T0, clw=1e-4),
            _make_hourly(T0 + timedelta(hours=1)),
            _make_hourly(T0 + timedelta(hours=2), clw=4e-4),
        ]
        cs = _make_cs_with_hourly([hourly])
        propagate_all([cs], [])  # no gfs_init → forward-fill
        pl1 = cs.point_forecasts[0].hourly[1].pressure_levels[0]
        assert pl1.cloud_liquid_water_kg_kg == pytest.approx(1e-4)

    def test_clw_post_last_anchor_forward_fills(self):
        """Hours past the last CLW anchor have no upper bracket → carry the
        last known value forward (mirrors the diag post-last-anchor path)."""
        gfs_init = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
        hourly = [
            _make_hourly(gfs_init + timedelta(hours=120), clw=2e-4, icmr=5e-5),
            _make_hourly(gfs_init + timedelta(hours=123), clw=4e-4, icmr=1e-4),
            _make_hourly(gfs_init + timedelta(hours=124)),  # past last anchor
            _make_hourly(gfs_init + timedelta(hours=125)),  # past last anchor
        ]
        cs = _make_cs_with_hourly_model([hourly], ModelSource.GFS)
        propagate_all([cs], [], gfs_init=gfs_init)
        pl_trailing_1 = cs.point_forecasts[0].hourly[2].pressure_levels[0]
        pl_trailing_2 = cs.point_forecasts[0].hourly[3].pressure_levels[0]
        assert pl_trailing_1.cloud_liquid_water_kg_kg == pytest.approx(4e-4)
        assert pl_trailing_1.ice_mixing_ratio_kg_kg == pytest.approx(1e-4)
        assert pl_trailing_2.cloud_liquid_water_kg_kg == pytest.approx(4e-4)


class TestGfsRhCondensateGate:
    """The dry-band gate drops phantom layers with no RH or condensate
    support. Only fires for GFS sections."""

    def _build_dry_pt11_hourly(self) -> HourlyForecast:
        """pt11 at 14z native: mid 100% @ 18-22 kft, but pressure-level RH
        and condensate inside the band are dry. The gate should drop it."""
        # Pressure levels covering 18-22 kft (~500 hPa = 18 kft, ~450 hPa = 20 kft).
        pls = [
            PressureLevelData(pressure_hpa=500, relative_humidity_pct=11.0,
                              cloud_liquid_water_kg_kg=0.0, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=18000 / 3.28084),
            PressureLevelData(pressure_hpa=450, relative_humidity_pct=26.0,
                              cloud_liquid_water_kg_kg=0.0, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=20000 / 3.28084),
        ]
        diag = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=100.0, base_ft=18000, top_ft=22200),
        )
        return HourlyForecast(
            time=T0, nwp_cloud_diagnostics=diag, pressure_levels=pls,
        )

    def test_drops_dry_mid_layer(self):
        h = self._build_dry_pt11_hourly()
        cs = _make_cs_with_hourly_model([[h]], ModelSource.GFS)
        apply_gfs_rh_condensate_gate([cs], [])
        assert h.nwp_cloud_diagnostics.mid.cover_pct is None
        assert h.nwp_cloud_diagnostics.mid.base_ft is None

    def test_keeps_humid_low_layer(self):
        """ERCOZ negative control: low @ 4781-10383 ft, RH 80-98%, CLW > 0."""
        pls = [
            PressureLevelData(pressure_hpa=850, relative_humidity_pct=80.0,
                              cloud_liquid_water_kg_kg=1e-4, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=4900 / 3.28084),
            PressureLevelData(pressure_hpa=700, relative_humidity_pct=92.0,
                              cloud_liquid_water_kg_kg=2e-4, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=10000 / 3.28084),
        ]
        diag = NWPCloudDiagnostics(
            low=NWPCloudLayerDiag(cover_pct=86.9, base_ft=4781, top_ft=10383),
        )
        h = HourlyForecast(time=T0, nwp_cloud_diagnostics=diag, pressure_levels=pls)
        cs = _make_cs_with_hourly_model([[h]], ModelSource.GFS)
        apply_gfs_rh_condensate_gate([cs], [])
        assert h.nwp_cloud_diagnostics.low.cover_pct == 86.9
        assert h.nwp_cloud_diagnostics.low.base_ft == 4781

    def test_keeps_dry_layer_when_clw_data_absent(self):
        """No CLMR/ICMR observations inside the band → cannot prove sum==0,
        so don't drop. Conservative behavior."""
        pls = [
            PressureLevelData(pressure_hpa=500, relative_humidity_pct=20.0,
                              geopotential_height_m=18000 / 3.28084),
            PressureLevelData(pressure_hpa=450, relative_humidity_pct=22.0,
                              geopotential_height_m=20000 / 3.28084),
        ]
        diag = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=100.0, base_ft=18000, top_ft=22200),
        )
        h = HourlyForecast(time=T0, nwp_cloud_diagnostics=diag, pressure_levels=pls)
        cs = _make_cs_with_hourly_model([[h]], ModelSource.GFS)
        apply_gfs_rh_condensate_gate([cs], [])
        # Layer preserved (gate can't run without condensate observations).
        assert h.nwp_cloud_diagnostics.mid.cover_pct == 100.0

    def test_keeps_dry_layer_when_condensate_present(self):
        """RH below threshold but CLW > 0 anywhere in band → keep."""
        pls = [
            PressureLevelData(pressure_hpa=500, relative_humidity_pct=20.0,
                              cloud_liquid_water_kg_kg=1e-5, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=18000 / 3.28084),
            PressureLevelData(pressure_hpa=450, relative_humidity_pct=22.0,
                              cloud_liquid_water_kg_kg=0.0, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=20000 / 3.28084),
        ]
        diag = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=100.0, base_ft=18000, top_ft=22200),
        )
        h = HourlyForecast(time=T0, nwp_cloud_diagnostics=diag, pressure_levels=pls)
        cs = _make_cs_with_hourly_model([[h]], ModelSource.GFS)
        apply_gfs_rh_condensate_gate([cs], [])
        assert h.nwp_cloud_diagnostics.mid.cover_pct == 100.0

    def test_no_op_for_ecmwf(self):
        """ECMWF cloud cover is instantaneous → gate must not fire even on a
        synthetic dry layer matching the GFS pathology pattern."""
        h = self._build_dry_pt11_hourly()
        cs = _make_cs_with_hourly_model([[h]], ModelSource.ECMWF)
        apply_gfs_rh_condensate_gate([cs], [])
        assert h.nwp_cloud_diagnostics.mid.cover_pct == 100.0

    def test_no_op_for_icon(self):
        """ICON-EU cloud cover is instantaneous → gate must not fire."""
        h = self._build_dry_pt11_hourly()
        cs = _make_cs_with_hourly_model([[h]], ModelSource.ICON)
        apply_gfs_rh_condensate_gate([cs], [])
        assert h.nwp_cloud_diagnostics.mid.cover_pct == 100.0

    def test_does_not_touch_convective_band(self):
        """Convective cover is instantaneous in GFS pgrb2 → not gated."""
        pls = [
            PressureLevelData(pressure_hpa=500, relative_humidity_pct=20.0,
                              cloud_liquid_water_kg_kg=0.0, ice_mixing_ratio_kg_kg=0.0,
                              geopotential_height_m=18000 / 3.28084),
        ]
        diag = NWPCloudDiagnostics(
            mid=NWPCloudLayerDiag(cover_pct=100.0, base_ft=18000, top_ft=22200),
            convective_cover_pct=55.0,
            convective_base_ft=2665.0,
            convective_top_ft=12022.0,
        )
        h = HourlyForecast(time=T0, nwp_cloud_diagnostics=diag, pressure_levels=pls)
        cs = _make_cs_with_hourly_model([[h]], ModelSource.GFS)
        apply_gfs_rh_condensate_gate([cs], [])
        # Mid dropped, convective unchanged.
        assert h.nwp_cloud_diagnostics.mid.cover_pct is None
        assert h.nwp_cloud_diagnostics.convective_cover_pct == 55.0
        assert h.nwp_cloud_diagnostics.convective_base_ft == 2665.0


class TestLerpWind:
    """#441 finding #7: temporal wind interp uses U/V components, not scalar
    speed + circular direction."""

    def test_opposing_winds_pass_through_calm(self):
        # 10 kt @ 090° → 10 kt @ 270°: the U/V midpoint is near-calm, NOT 10 kt
        # at some intermediate bearing (which naive scalar interp would give).
        spd, _ = _lerp_wind(10.0, 90.0, 10.0, 270.0, 0.5)
        assert spd < 1.0

    def test_same_direction_preserves(self):
        spd, wd = _lerp_wind(10.0, 270.0, 20.0, 270.0, 0.5)
        assert spd == pytest.approx(15.0)
        assert wd == pytest.approx(270.0)

    def test_veering_midpoint(self):
        # 10 kt from 270° (westerly) → 10 kt from 360° (northerly): the vector
        # midpoint is a NW wind of reduced speed, bearing between 270 and 360.
        spd, wd = _lerp_wind(10.0, 270.0, 10.0, 360.0, 0.5)
        assert spd == pytest.approx(10.0 * (2 ** 0.5) / 2, abs=0.1)  # ~7.07 kt
        assert 270.0 < wd < 360.0

    def test_none_endpoints(self):
        assert _lerp_wind(None, 90.0, 10.0, 90.0, 0.5) == (None, None)
        # Missing direction on one side → scalar speed, keep defined direction.
        spd, wd = _lerp_wind(10.0, None, 20.0, 90.0, 0.5)
        assert spd == pytest.approx(15.0)
        assert wd == 90.0


class TestGustWindowMax:
    """#441 finding #6: ECMWF gust (10fg) is a window maximum, held over the
    covering interval on gap hours — not linearly interpolated."""

    def test_gust_held_not_interpolated(self):
        from weatherbrief.fetch.grib.fill import _interp_surface_hourly

        t0 = datetime(2026, 7, 16, 12, tzinfo=timezone.utc)
        # Anchors at index 0 and 2 (diag set), gap hour at index 1.
        anchor0 = HourlyForecast(
            time=t0, nwp_cloud_diagnostics=NWPCloudDiagnostics(total_cover_pct=50.0),
            wind_gusts_10m_kt=20.0, temperature_2m_c=15.0,
        )
        gap = HourlyForecast(time=t0 + timedelta(hours=1))
        anchor2 = HourlyForecast(
            time=t0 + timedelta(hours=2),
            nwp_cloud_diagnostics=NWPCloudDiagnostics(total_cover_pct=50.0),
            wind_gusts_10m_kt=35.0, temperature_2m_c=17.0,
        )
        filled = _interp_surface_hourly([anchor0, gap, anchor2])
        assert filled == 1
        # Gust holds the covering interval's max (next anchor), NOT ~27.5 lerp.
        assert gap.wind_gusts_10m_kt == 35.0
        # A genuinely instantaneous scalar IS linearly interpolated.
        assert gap.temperature_2m_c == pytest.approx(16.0)


class TestBoundaryCoverAlignment:
    """#441 finding #5: boundary-layer cover is averaged-only, so it aligns at
    the window midpoint (mid_frac), while total cover is instantaneous
    (step_frac)."""

    def test_boundary_uses_mid_frac_total_uses_step_frac(self):
        from weatherbrief.fetch.grib.fill import _interp_diag_at

        prev = NWPCloudDiagnostics(boundary_cover_pct=0.0, total_cover_pct=0.0)
        nxt = NWPCloudDiagnostics(boundary_cover_pct=100.0, total_cover_pct=100.0)
        out = _interp_diag_at(prev, nxt, mid_frac=0.25, step_frac=0.75)
        assert out.boundary_cover_pct == pytest.approx(25.0)  # averaged → mid_frac
        assert out.total_cover_pct == pytest.approx(75.0)     # instant → step_frac
