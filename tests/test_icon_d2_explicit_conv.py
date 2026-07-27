"""Tests for the ICON-D2 explicit-convection pipeline (issue #462).

Fetch config → message-level decode (sub-hourly stepRange selection, corridor
extrema, sentinels, validity mask) → enrichment semantics (hourly echo-top
construction over exactly four quarter-windows, interval attachment,
completeness degradation) → the domain-gate hardening and the total-D2-failure
→ ICON-EU re-run path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest

from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_D2,
    ICON_D2_EXPLICIT_CONV_VARIABLES,
    ICON_EU,
    icon_explicit_conv_cache_key,
)
from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    RouteCrossSection,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)


def _rp(lat: float, lon: float, nm: float = 0.0) -> RoutePoint:
    return RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=nm)


# ---------------------------------------------------------------------------
# Variant config + helpers
# ---------------------------------------------------------------------------


class TestExplicitConvConfig:
    def test_d2_explicit_variable_list(self):
        assert ICON_D2.explicit_conv_variables == ICON_D2_EXPLICIT_CONV_VARIABLES
        for var in ("dbz_ctmax", "echotop", "lpi_max", "w_ctmax", "uh_max"):
            assert var in ICON_D2.explicit_conv_variables
        # grau_gsp dropped in v1 (#468 — surface graupel, ~always 0 on a
        # warm-season corridor); dbz_cmax (optional) and tcond10_mx (deferred
        # replacement) stay out of v1.
        assert "grau_gsp" not in ICON_D2.explicit_conv_variables
        assert "dbz_cmax" not in ICON_D2.explicit_conv_variables
        assert "tcond10_mx" not in ICON_D2.explicit_conv_variables

    def test_eu_has_no_explicit_variables(self):
        assert ICON_EU.explicit_conv_variables == ()

    def test_needs_predecessor_flag_generalized(self):
        # EU: rain_con de-accumulation. D2: the three prior-file echo-top
        # quarters. One variant-level flag (#462).
        assert ICON_EU.needs_predecessor_step is True
        assert ICON_D2.needs_predecessor_step is True

    def test_explicit_cache_key_per_variable(self):
        assert (
            icon_explicit_conv_cache_key("dbz_ctmax", ICON_D2)
            == "ICON_D2_EXPL_DBZ_CTMAX_V1"
        )


# ---------------------------------------------------------------------------
# GRIB synthesis helper (eccodes) for decode tests
# ---------------------------------------------------------------------------

# Grid: 47–49°N, 10–13°E, 5×7 cells (~30 nm lat spacing — coarse but exercises
# the corridor geometry exactly like the real 2.2 km grid).
_LAT0, _LAT1, _LON0, _LON1 = 47.0, 49.0, 10.0, 13.0
_MISSING = -1.0e10


def _make_msg(
    values2d: np.ndarray,
    start_min: int,
    end_min: int,
    step_type: str = "max",
    lon0: float = _LON0,
    lon1: float = _LON1,
) -> bytes:
    """Encode a regular-ll GRIB2 message with a minutes step range + bitmap.

    ``lon0``/``lon1`` may be given in the 0–360 convention DWD actually
    delivers (see ``test_zero_to_360_longitude_axis_is_normalized``).
    """
    import eccodes

    gid = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
    nj, ni = values2d.shape
    eccodes.codes_set(gid, "Ni", ni)
    eccodes.codes_set(gid, "Nj", nj)
    eccodes.codes_set(gid, "latitudeOfFirstGridPointInDegrees", _LAT1)
    eccodes.codes_set(gid, "latitudeOfLastGridPointInDegrees", _LAT0)
    eccodes.codes_set(gid, "longitudeOfFirstGridPointInDegrees", lon0)
    eccodes.codes_set(gid, "longitudeOfLastGridPointInDegrees", lon1)
    span = (lon1 - 360.0 if lon1 > 180.0 else lon1) - (
        lon0 - 360.0 if lon0 > 180.0 else lon0
    )
    eccodes.codes_set(gid, "iDirectionIncrementInDegrees", span / (ni - 1))
    eccodes.codes_set(gid, "jDirectionIncrementInDegrees", (_LAT1 - _LAT0) / (nj - 1))
    eccodes.codes_set(gid, "jScansPositively", 0)
    eccodes.codes_set(gid, "stepUnits", "m")
    eccodes.codes_set(gid, "stepType", step_type)
    eccodes.codes_set(gid, "startStep", start_min)
    eccodes.codes_set(gid, "endStep", end_min)
    vals = values2d[::-1, :].copy()  # north→south scan order
    eccodes.codes_set(gid, "missingValue", _MISSING)
    eccodes.codes_set(gid, "bitmapPresent", 1)
    vals = np.where(np.isnan(vals), _MISSING, vals)
    eccodes.codes_set_values(gid, vals.flatten())
    out = eccodes.codes_get_message(gid)
    eccodes.codes_release(gid)
    return bytes(out)


def _grid(fill: float) -> np.ndarray:
    return np.full((5, 7), fill)


# ---------------------------------------------------------------------------
# Decode — corridor extrema, sentinels, sub-hourly selection
# ---------------------------------------------------------------------------


class TestExplicitConvDecode:
    def _decode(self, var_bytes, lats, lons, radius_nm=10.0):
        from weatherbrief.fetch.grib.decode import (
            decode_icon_d2_explicit_conv_per_point,
        )

        return decode_icon_d2_explicit_conv_per_point(
            var_bytes, lats, lons, radius_nm=radius_nm,
        )

    def test_corridor_max_catches_cell_between_route_points(self):
        # Cell at grid node (48°N, 11.5°E); the route point sits ~8 nm away —
        # bilinear interpolation from surrounding quiet nodes would dilute the
        # cell to near-floor, but the corridor max must catch it whole.
        vals = _grid(-150.0)
        vals[2, 3] = 48.0  # 48°N, 11.5°E
        msg = _make_msg(vals, 660, 720)
        res = self._decode({"dbz_ctmax": msg}, [48.13], [11.5])
        value, valid = res[0]["dbz_ctmax"]
        assert valid is True
        assert value == pytest.approx(48.0)

    def test_quiet_floor_normalizes_to_none_but_valid(self):
        # −150 dBZ encoder floor = genuinely QUIET: value None, corridor valid.
        msg = _make_msg(_grid(-150.0), 660, 720)
        res = self._decode({"dbz_ctmax": msg}, [48.0], [11.5])
        assert res[0]["dbz_ctmax"] == (None, True)

    def test_masked_corridor_is_invalid_not_quiet(self):
        # Point whose whole corridor is bitmap-masked → (None, False):
        # UNAVAILABLE, never "no echo". Sited well inside the grid extent so
        # this exercises the BITMAP path, not the outer-edge guard below.
        vals = _grid(-150.0)
        vals[2, 3] = np.nan  # the only cell in this point's 10 nm buffer
        msg = _make_msg(vals, 660, 720)
        res = self._decode({"dbz_ctmax": msg}, [48.0], [11.5])
        assert res[0]["dbz_ctmax"] == (None, False)

    def test_point_outside_grid_is_invalid(self):
        msg = _make_msg(_grid(-150.0), 660, 720)
        res = self._decode({"dbz_ctmax": msg}, [55.0], [11.5])
        assert res[0]["dbz_ctmax"] == (None, False)

    def test_zero_to_360_longitude_axis_is_normalized(self):
        # DWD delivers `longitudeOfFirstGridPointInDegrees` in the 0–360
        # convention: the real D2 domain's western edge (3.94°W) reads 356.06,
        # verified live. Route points are −180..+180, so without normalization
        # NO point in the domain matches the axis — the corridor gate rejects
        # every route and D2 is silently never selected. cfgrib normalizes for
        # us on the other decode paths; this eccodes path must do it itself.
        # 9 columns spanning 4°W..4°E, 5 rows spanning 47..49°N.
        vals = np.full((5, 9), -150.0)
        vals[2, 2] = 52.0  # 48.0°N, 2.0°W
        msg = _make_msg(vals, 660, 720, lon0=356.0, lon1=4.0)

        from weatherbrief.fetch.grib.decode import _d2_iter_messages, _d2_read_message_grid

        for gid in _d2_iter_messages(msg):
            lats_axis, lons_axis, _grid = _d2_read_message_grid(gid)
        assert lons_axis[0] == pytest.approx(-4.0)
        assert lons_axis[-1] == pytest.approx(4.0)
        assert list(lons_axis) == sorted(lons_axis)  # ascending, not mirrored

        res = self._decode({"dbz_ctmax": msg}, [48.0], [-2.0], radius_nm=25.0)
        value, valid = res[0]["dbz_ctmax"]
        assert valid is True
        assert value == pytest.approx(52.0)

    def test_corridor_clipped_by_outer_grid_edge_is_invalid(self):
        # The point is INSIDE the grid and every cell it can see is valid, but
        # its 10 nm buffer runs past the western boundary (10°E). searchsorted
        # would silently clip the window and report a complete corridor over a
        # truncated buffer — the corridor must read UNAVAILABLE instead
        # (external #462 review of PR #463).
        vals = _grid(-150.0)
        vals[2, 0] = 48.0
        msg = _make_msg(vals, 660, 720)
        res = self._decode({"dbz_ctmax": msg}, [48.0], [10.1])
        assert res[0]["dbz_ctmax"] == (None, False)

    def test_echotop_quarters_keyed_by_end_minute_with_sentinel(self):
        # Quarter 1 has an echo down to 40000 Pa; quarter 2 is all-sentinel
        # (no echo) — a VALID quiet quarter, not a missing one.
        q1 = _grid(-999.0)
        q1[2, 3] = 40000.0
        q2 = _grid(-999.0)
        blob = _make_msg(q1, 705, 720, "min") + _make_msg(q2, 720, 735, "min")
        res = self._decode({"echotop": blob}, [48.0], [11.5])
        assert res[0]["echotop_quarters"][720] == (pytest.approx(40000.0), True)
        assert res[0]["echotop_quarters"][735] == (None, True)

    def test_uh_keeps_signed_value_at_absmax(self):
        vals = _grid(0.0)
        vals[2, 3] = -60.0  # strongest amplitude, anticyclonic
        vals[2, 4] = 40.0
        msg = _make_msg(vals, 660, 720)
        res = self._decode({"uh_max": msg}, [48.0], [11.75], radius_nm=40.0)
        value, valid = res[0]["uh_max"]
        assert valid is True
        assert value == pytest.approx(-60.0)


class TestD2ValidityMask:
    def test_mask_and_corridor_gate(self):
        from weatherbrief.fetch.grib.decode import (
            build_d2_validity_mask,
            d2_corridor_fully_valid,
        )

        vals = _grid(1.0)
        vals[2, 3] = np.nan  # masked cell at 48°N, 11.5°E
        mask = build_d2_validity_mask(_make_msg(vals, 660, 720))
        assert mask is not None
        assert int(mask["valid"].sum()) == 34
        # Point whose corridor holds only valid cells.
        assert d2_corridor_fully_valid(mask, [48.0], [12.0], radius_nm=10.0)
        # Point whose buffer clips the masked cell: fails, and the rule is
        # all-or-nothing — one bad point fails the whole route.
        assert not d2_corridor_fully_valid(
            mask, [48.0, 48.0], [12.0, 11.5], radius_nm=10.0,
        )
        # Point outside the grid entirely: fails.
        assert not d2_corridor_fully_valid(mask, [55.0], [11.5], radius_nm=10.0)
        # Point inside the grid, seeing only valid cells, but whose buffer runs
        # past the western boundary. Both gathers clip identically there, so the
        # valid-vs-all count can never catch it — the window guard must
        # (external #462 review of PR #463).
        assert not d2_corridor_fully_valid(mask, [48.0], [10.1], radius_nm=10.0)


# ---------------------------------------------------------------------------
# Enrichment semantics — echo-top construction, de-accumulation, attachment
# ---------------------------------------------------------------------------


def _icon_cross_section(n_points: int, hours: list[int]) -> RouteCrossSection:
    """ICON cross-section with hourly entries at init 2026-07-21 00z + f{hours}."""
    pfs = []
    for i in range(n_points):
        pfs.append(WaypointForecast(
            waypoint=Waypoint(icao=f"P{i}", name=f"P{i}", lat=48.0 + 0.1 * i, lon=11.0),
            model=ModelSource.ICON,
            fetched_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
            hourly=[
                HourlyForecast(time=datetime(2026, 7, 21, h, tzinfo=timezone.utc))
                for h in hours
            ],
        ))
    return RouteCrossSection(
        model=ModelSource.ICON,
        route_points=[],
        fetched_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        point_forecasts=pfs,
    )


# Per-fhour synthetic decode results for two route points. Point 0 exercises
# the happy path + degradations; point 1 is fully masked (never gets data).
_DECODED_BY_FHOUR = {
    # Lead step f011 — state seeding only (the three quarter windows ending
    # 675/690/705 that hour 12 needs).
    11: [
        {
            "dbz_ctmax": (44.0, True),
            "lpi_max": (0.5, True),
            "w_ctmax": (5.0, True),
            "uh_max": (2.0, True),
            "echotop_quarters": {
                660: (50000.0, True),
                675: (45000.0, True),
                690: (46000.0, True),
                705: (47000.0, True),
            },
        },
        {"echotop_quarters": {}},
    ],
    # f012 — complete hour: fires with corroborators, echo top constructible.
    12: [
        {
            "dbz_ctmax": (48.0, True),
            "lpi_max": (3.0, True),
            "w_ctmax": (12.0, True),
            "uh_max": (30.0, True),
            "echotop_quarters": {
                720: (40000.0, True),
                735: (None, True),
                750: (None, True),
                765: (None, True),
            },
        },
        {"echotop_quarters": {}},
    ],
    # f013 — degraded hour: the 780' quarter (ending 13:00) is missing → echo
    # top must degrade, never a partial min.
    13: [
        {
            "dbz_ctmax": (36.0, True),
            "lpi_max": (0.0, True),
            "w_ctmax": (2.0, True),
            "uh_max": (1.0, True),
            "echotop_quarters": {
                795: (None, True),
            },
        },
        {"echotop_quarters": {}},
    ],
}


class TestExplicitConvEnrichment:
    def _run(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        cs = _icon_cross_section(2, hours=[11, 12, 13])
        route_points = [_rp(48.0, 11.0), _rp(48.1, 11.0, 10.0)]

        def _fake_dispatch(worker_name, var_paths, lats, lons):
            assert worker_name == "decode_icon_d2_explicit_conv"
            # Recover the fhour from any cache filename (f011_..., f012_...).
            fname = next(iter(var_paths.values()))
            fhour = int(fname.split("f")[-1][:3])
            return _DECODED_BY_FHOUR[fhour]

        with patch.object(grib_mod, "_dispatch_decode", side_effect=_fake_dispatch), \
                patch.object(grib_mod, "is_cached", return_value=True):
            grib_mod._enrich_icon_d2_explicit_convective(
                [cs], [], route_points,
                "20260721", 0, [12, 13],
                tmp_path, [rp.lat for rp in route_points],
                [rp.lon for rp in route_points], None,
                variant=ICON_D2,
            )
        return cs

    def _hourly(self, cs, point: int, hour: int) -> HourlyForecast:
        return next(
            h for h in cs.point_forecasts[point].hourly if h.time.hour == hour
        )

    def test_complete_hour_payload(self, tmp_path):
        cs = self._run(tmp_path)
        diag = self._hourly(cs, 0, 12).explicit_convective_diagnostics
        assert diag is not None
        assert diag.source == "icon_d2"
        assert diag.reflectivity_hour_max_dbz == pytest.approx(48.0)
        assert diag.detection_complete is True
        # lpi_max (3.0) and w_ctmax (12.0) both decoded → strength complete.
        assert diag.strength_complete is True
        # Hourly echo top = min pressure over EXACTLY the four windows ending
        # 11:15/11:30/11:45 (prev file) + 12:00 (this file): min = 40000 Pa.
        # The 660' quarter (ending 11:00) must NOT participate.
        assert diag.echo_top_complete is True
        # 40000 Pa ≈ FL236 via the ISA column fallback (no geopotential
        # heights on these synthetic hours).
        assert 22000 < diag.echo_top_18dbz_ft < 25500

    def test_degraded_hour_never_presents_partial_echo_top(self, tmp_path):
        cs = self._run(tmp_path)
        diag = self._hourly(cs, 0, 13).explicit_convective_diagnostics
        assert diag is not None
        assert diag.detection_complete is True
        # The quarter ending 13:00 is missing → completeness degrades and NO
        # partial min is presented as the hourly echo top.
        assert diag.echo_top_complete is False
        assert diag.echo_top_18dbz_ft is None
        # Strength stays complete — lpi_max (0.0) and w_ctmax (2.0) both
        # decoded; only the echo-top channel degraded this hour.
        assert diag.strength_complete is True

    def test_lead_step_is_never_attached(self, tmp_path):
        cs = self._run(tmp_path)
        assert self._hourly(cs, 0, 11).explicit_convective_diagnostics is None

    def test_masked_point_gets_unavailable_payload(self, tmp_path):
        # A point whose every channel failed still gets a payload — with all
        # channels None and every completeness flag False. Payload presence is
        # the explicit-convection mode signal: with no payload the analysis
        # layer would silently fall back to the parameterized path, which on
        # D2 structurally reads as a QUIET scheme — a fetch hiccup must read
        # "explicit assessment unavailable", never fake-quiet (#463 review).
        cs = self._run(tmp_path)
        for hour in (12, 13):
            diag = self._hourly(cs, 1, hour).explicit_convective_diagnostics
            assert diag is not None
            assert diag.detection_complete is False
            assert diag.strength_complete is False
            assert diag.echo_top_complete is False
            assert diag.reflectivity_hour_max_dbz is None


# ---------------------------------------------------------------------------
# Echo-top pressure → feet: the hour's own column, one datum throughout
# ---------------------------------------------------------------------------


class TestEchoTopPressureToFeet:
    """The Pa→ft conversion keeps the column datum instead of flipping to ISA.

    Adopted from PR #465's review of the same issue: a metre-datum model
    column and an ISA pressure altitude disagree by hundreds of feet, so an
    echo above the aviation slice must be extrapolated along the column rather
    than silently switched to the other datum — otherwise echo tops are not
    comparable between route points.
    """

    def _hourly(self, levels: list[tuple[float, float | None]]) -> HourlyForecast:
        from weatherbrief.models import PressureLevelData

        return HourlyForecast(
            time=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
            pressure_levels=[
                PressureLevelData(pressure_hpa=p, geopotential_height_m=z)
                for p, z in levels
            ],
        )

    def _convert(self, pa: float, levels) -> float:
        from weatherbrief.fetch.grib import _echo_top_pa_to_ft

        return _echo_top_pa_to_ft(pa, self._hourly(levels))

    _COLUMN = [(500.0, 6000.0), (400.0, 7800.0), (300.0, 10100.0)]

    def test_interpolates_within_the_column(self):
        # 450 hPa sits between 500/400 → ~6.9 km ≈ 22.6 kft, well away from
        # ISA's ~20.8 kft for the same pressure.
        assert 22000 < self._convert(45000.0, self._COLUMN) < 23500

    def test_extrapolates_above_the_slice_on_the_column_datum(self):
        # 250 hPa is above the top level: keep extrapolating the 400/300 slope
        # instead of returning an ISA altitude on a different datum.
        ft = self._convert(25000.0, self._COLUMN)
        assert ft > 10100.0 * 3.28084

    def test_falls_back_to_isa_without_a_usable_column(self):
        from weatherbrief.models.analysis import pressure_pa_to_altitude_ft

        assert self._convert(45000.0, [(500.0, 6000.0)]) == round(
            pressure_pa_to_altitude_ft(45000.0)
        )
        assert self._convert(45000.0, [(500.0, None), (400.0, None)]) == round(
            pressure_pa_to_altitude_ft(45000.0)
        )

    def test_duplicate_pressures_never_raise(self):
        # A degenerate column must not abort the enrichment pass — the attach
        # loop that calls this has no try/except above it (#463 review r2).
        ft = self._convert(45000.0, [(450.0, 6900.0), (450.0, 6950.0)])
        assert isinstance(ft, int | float)


# ---------------------------------------------------------------------------
# Prefetch — per-variable explicit blobs + generalized lead step
# ---------------------------------------------------------------------------


class TestExplicitConvPrefetch:
    def test_prefetch_fetches_explicit_blobs_and_lead_step(self, tmp_path, monkeypatch):
        import weatherbrief.fetch.grib as grib_mod
        import weatherbrief.fetch.grib.icon_eu_fetch as icon_fetch_mod

        single_level_calls: list[tuple[int, tuple[str, ...] | None]] = []

        def fake_single_level(init_date, init_hour, fhours, variables=None,
                              session=None, max_workers=8, variant=ICON_D2):
            (fhour,) = fhours
            single_level_calls.append(
                (fhour, tuple(variables) if variables is not None else None)
            )
            key = variables[0] if variables else "diag"
            return {fhour: f"{key}-{fhour}".encode()}

        def fake_per_variable(init_date, init_hour, fhour, levels, variables,
                              session=None, max_workers=8, variant=ICON_D2):
            (var,) = variables
            return {var: f"{fhour}-{var}".encode()}

        def fake_per_level(init_date, init_hour, fhour, levels, variables,
                           session=None, max_workers=8, variant=ICON_D2):
            # ICON-D2 caches per (variable, level) (#469); return one blob per file.
            (var,) = variables
            return {(var, level): f"{fhour}-{var}-{level}".encode() for level in levels}

        monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_single_level", fake_single_level)
        monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_per_variable", fake_per_variable)
        monkeypatch.setattr(icon_fetch_mod, "fetch_icon_eu_per_level", fake_per_level)
        monkeypatch.setenv("GRIB_ICON_PREFETCH_WORKERS", "1")

        ctx = grib_mod._IconEuContext(
            init_date="20260721", init_hour=0, forecast_hours=[12, 13],
            run_dir=tmp_path, levels=[60],
            point_lats=[48.0], point_lons=[11.0],
            session=None, variant=ICON_D2,
        )
        grib_mod._prefetch_icon_eu_data_inner(ctx)

        explicit_calls = [c for c in single_level_calls if c[1] is not None]
        # Every explicit variable fetched per window hour AND for the lead
        # step f011 (the prior-file echo-top quarters hour 12 needs).
        for fhour in (11, 12, 13):
            fetched_vars = {c[1][0] for c in explicit_calls if c[0] == fhour}
            assert fetched_vars == set(ICON_D2_EXPLICIT_CONV_VARIABLES)
        # D2 fetches no rain_con, so the cloud-diag blob needs NO lead step:
        # diag fetches (variables=None) cover only the window hours.
        diag_hours = sorted(c[0] for c in single_level_calls if c[1] is None)
        assert diag_hours == [12, 13]


# ---------------------------------------------------------------------------
# Domain-gate hardening in _prepare_icon_eu
# ---------------------------------------------------------------------------


class TestD2MaskGate:
    def _icon_cross_sections(self):
        return [RouteCrossSection(
            model=ModelSource.ICON,
            route_points=[],
            fetched_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            point_forecasts=[],
        )]

    def _run_finder(self, d2_run, eu_run):
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2 as _D2

        def _fake(target_time, session=None, as_of_time=None,
                  cover_until=None, variant=None):
            return d2_run if variant is _D2 else eu_run

        return _fake

    def test_corridor_clip_falls_back_to_eu(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(48.35, 11.79), _rp(52.52, 13.4, 300)]  # inside D2 bbox
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ), patch.object(grib_mod, "_d2_corridor_mask_ok", return_value=False):
            ctx, skip = grib_mod._prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert skip is None
        assert ctx is not None
        assert ctx.variant is ICON_EU

    def test_mask_ok_keeps_d2(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        route = [_rp(48.35, 11.79), _rp(52.52, 13.4, 300)]
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ), patch.object(grib_mod, "_d2_corridor_mask_ok", return_value=True):
            ctx, skip = grib_mod._prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert ctx is not None
        assert ctx.variant is ICON_D2


# ---------------------------------------------------------------------------
# Total-D2-failure → ICON-EU re-run (carried from PR #461 review)
# ---------------------------------------------------------------------------


class TestD2TotalFailureFallback:
    def test_total_d2_failure_reruns_on_eu(self, tmp_path):
        import weatherbrief.fetch.grib as grib_mod

        cs = _icon_cross_section(1, hours=[12])
        route_points = [_rp(48.0, 11.0)]
        dep = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)

        def _fake_prepare(cross_sections, rps, departure_time, *, data_dir,
                          flight_duration_hours=0.0,
                          as_of_time=None, force_variant=None):
            variant = force_variant or ICON_D2
            ctx = grib_mod._IconEuContext(
                init_date="20260721", init_hour=0, forecast_hours=[12],
                run_dir=tmp_path, levels=[60],
                point_lats=[48.0], point_lons=[11.0],
                session=None, variant=variant,
            )
            return ctx, None

        decode_variants: list[str] = []

        def _fake_decode(ctx, cross_sections, all_forecasts, rps):
            decode_variants.append(ctx.variant.slug)
            # First (D2) attempt: nothing enriched. Second (EU): success.
            if ctx.variant.slug == "icon-d2":
                return None, None
            return 1753056000, None

        with patch.object(grib_mod, "_prepare_icon_eu", side_effect=_fake_prepare), \
                patch.object(grib_mod, "_prefetch_icon_eu_data"), \
                patch.object(grib_mod, "_decode_and_merge_icon_eu", side_effect=_fake_decode), \
                patch.object(grib_mod, "_enrich_gfs", return_value=(None, None)), \
                patch.object(grib_mod, "_enrich_ecmwf", return_value=None):
            init_times, skips, sources = grib_mod.enrich_forecasts(
                [cs], [], route_points, dep,
                data_dir=tmp_path, flight_duration_hours=1.0,
            )

        assert decode_variants == ["icon-d2", "icon-eu"]
        assert init_times.get("icon") == 1753056000
        assert sources.get("icon") == "icon_eu:dwd"
