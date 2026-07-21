"""Tests for ICON-D2 explicit-convection diagnostics (issue #462).

D2 is convection-permitting: deep convection lives in explicit storm fields
(simulated reflectivity, echo top, LPI, updrafts, graupel) rather than a
parameterized scheme's diagnostics. These tests cover the variant config,
the corridor-extrema decode (message selection, sentinel rules, sub-hourly
echo-top construction, graupel de-accumulation), the payload builder, the
firing decision table + assessment contract, the evaluator's UNAVAILABLE
handling, the fill hold-over, and the domain-mask gate.

The eccodes plumbing of ``_read_d2_explicit_messages`` was verified against
the live DWD feed (2026-07-21); here it is replaced by synthetic messages so
every reduction rule is tested deterministically without GRIB fixtures.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import numpy as np
import pytest

from weatherbrief.fetch.grib.decode import (
    D2_CORRIDOR_RADIUS_KM,
    _D2Message,
    _d2_corridor_cells,
    _isa_pressure_to_ft,
    build_icon_d2_explicit_diagnostics,
    corridor_fully_valid,
    decode_icon_d2_explicit,
)
from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_D2,
    ICON_EU,
    IconVariant,
    icon_cloud_diag_cache_key,
)


# ---------------------------------------------------------------------------
# Synthetic grid + message helpers
# ---------------------------------------------------------------------------

LAT0, LON0, STEP = 45.0, 0.0, 0.1
N_SIDE = 61


def _grid_vecs(n: int = N_SIDE):
    lat_vec = LAT0 + np.arange(n) * STEP
    lon_vec = LON0 + np.arange(n) * STEP
    return lat_vec, lon_vec


CENTRE = (LAT0 + 10 * STEP, LON0 + 10 * STEP)  # well inside the grid


def _msg(var, values, valid_minutes, on_hour_accum=False):
    return _D2Message(
        var=var,
        values=np.asarray(values, dtype=np.float64),
        valid_minutes=valid_minutes,
        on_hour_accum=on_hour_accum,
    )


def _patch_messages(main_msgs, prev_msgs=None):
    """Patch the eccodes reader with synthetic message lists."""
    lat_vec, lon_vec = _grid_vecs()
    main_msgs = list(main_msgs)
    prev_msgs = list(prev_msgs or [])

    def fake(grib_bytes):
        if grib_bytes == b"main":
            return main_msgs, lat_vec, lon_vec, b"std"
        if grib_bytes == b"prev":
            return prev_msgs, lat_vec, lon_vec, b""
        raise AssertionError(f"unexpected blob {grib_bytes!r}")

    return patch(
        "weatherbrief.fetch.grib.decode._read_d2_explicit_messages",
        side_effect=fake,
    )


def _field(value: float, n: int = N_SIDE) -> np.ndarray:
    return np.full((n, n), value, dtype=np.float64)


def _cell_idx(lat: float, lon: float) -> tuple[int, int]:
    return round((lat - LAT0) / STEP), round((lon - LON0) / STEP)


# ---------------------------------------------------------------------------
# Variant config
# ---------------------------------------------------------------------------


class TestVariantConfig:
    def test_d2_carries_explicit_variables(self):
        for var in ("dbz_ctmax", "echotop", "lpi_max", "w_ctmax", "uh_max_med", "grau_gsp"):
            assert var in ICON_D2.cloud_diag_variables

    def test_d2_still_excludes_parameterized_scheme_fields(self):
        # No deep-convection scheme on D2: these must stay absent (#456).
        for var in ("hbas_con", "htop_con", "rain_con"):
            assert var not in ICON_D2.cloud_diag_variables

    def test_eu_list_unchanged(self):
        assert "uh_max_med" not in ICON_EU.cloud_diag_variables
        assert "rain_con" in ICON_EU.cloud_diag_variables

    def test_cache_key_bumped_for_d2_only(self):
        assert icon_cloud_diag_cache_key(ICON_D2) == "ICON_D2_CLOUD_DIAG_V3"
        assert icon_cloud_diag_cache_key(ICON_EU) == "ICON_EU_CLOUD_DIAG_V2"

    def test_needs_predecessor_step(self):
        # EU: rain_con de-accumulation; D2: grau_gsp + echotop quarters.
        assert ICON_EU.needs_predecessor_step is True
        assert ICON_D2.needs_predecessor_step is True
        bare = IconVariant(**{**ICON_D2.__dict__, "cloud_diag_variables": ("ceiling",)})
        assert bare.needs_predecessor_step is False


# ---------------------------------------------------------------------------
# Corridor geometry
# ---------------------------------------------------------------------------


class TestCorridorCells:
    def test_disc_centred_and_radius_respected(self):
        lat_vec, lon_vec = _grid_vecs()
        rows, cols, clipped = _d2_corridor_cells(lat_vec, lon_vec, *CENTRE)
        assert not clipped
        assert rows.size > 4  # a real disc, not just the nearest cell
        dlat_km = STEP * 111.195
        dlon_km = STEP * 111.195 * np.cos(np.radians(CENTRE[0]))
        ci, cj = _cell_idx(*CENTRE)
        dist = np.sqrt(((rows - ci) * dlat_km) ** 2 + ((cols - cj) * dlon_km) ** 2)
        assert dist.max() <= D2_CORRIDOR_RADIUS_KM + 1e-9
        # Nearest cell included; a cell clearly outside the radius excluded.
        assert (rows == ci).any() and (cols == cj).any()
        assert not ((rows == ci) & (cols == cj + 10)).any()

    def test_point_outside_grid_returns_none(self):
        lat_vec, lon_vec = _grid_vecs()
        assert _d2_corridor_cells(lat_vec, lon_vec, 60.0, 1.0) is None

    def test_edge_point_is_clipped(self):
        lat_vec, lon_vec = _grid_vecs()
        cells = _d2_corridor_cells(lat_vec, lon_vec, LAT0 + STEP, LON0 + STEP)
        assert cells is not None
        assert cells[2] is True  # clipped flag

    def test_corridor_fully_valid(self):
        lat_vec, lon_vec = _grid_vecs()
        mask = np.ones((N_SIDE, N_SIDE), dtype=bool)
        assert corridor_fully_valid(lat_vec, lon_vec, mask, *CENTRE)
        # One masked cell inside the disc → corridor not fully valid.
        ci, cj = _cell_idx(*CENTRE)
        mask[ci + 1, cj] = False
        assert not corridor_fully_valid(lat_vec, lon_vec, mask, *CENTRE)
        # Outside the grid / clipped at the boundary → not valid.
        assert not corridor_fully_valid(lat_vec, lon_vec, mask, 60.0, 1.0)
        assert not corridor_fully_valid(
            lat_vec, lon_vec, np.ones((N_SIDE, N_SIDE), dtype=bool),
            LAT0 + STEP, LON0 + STEP,
        )


# ---------------------------------------------------------------------------
# Explicit decode — channel reductions
# ---------------------------------------------------------------------------


class TestDecodeChannels:
    def test_corridor_max_catches_cell_off_centreline(self):
        lat0, lon0 = CENTRE
        dbz = _field(-150.0)
        ci, cj = _cell_idx(lat0, lon0)
        dbz[ci + 1, cj + 1] = 50.0  # ~9 km away, inside the corridor
        with _patch_messages([_msg("dbz_ctmax", dbz, 720)]):
            raws, std = decode_icon_d2_explicit(b"main", None, 12, [lat0], [lon0])
        raw = raws[0]
        assert std == b"std"  # standard remainder passes through untouched
        assert raw["reflectivity_hour_max_dbz"] == 50.0
        # Centreline stays quiet — the point-vs-corridor discriminator.
        assert raw["reflectivity_point_dbz"] == -150.0
        assert raw["dbz_valid_frac"] == 1.0

    def test_quiet_floor_is_valid_data_not_missing(self):
        # All cells at the −150 dBZ quiet floor + some bitmap-masked (NaN):
        # max must be the floor (quiet), and only bitmap cells reduce the frac.
        dbz = _field(-150.0)
        ci, cj = _cell_idx(*CENTRE)
        dbz[ci, cj] = np.nan
        dbz[ci + 1, cj + 1] = np.nan
        with _patch_messages([_msg("dbz_ctmax", dbz, 720)]):
            raws, _ = decode_icon_d2_explicit(b"main", None, 12, [CENTRE[0]], [CENTRE[1]])
        raw = raws[0]
        assert raw["reflectivity_hour_max_dbz"] == -150.0
        assert 0.0 < raw["dbz_valid_frac"] < 1.0
        # Centreline None on a masked corner cell, but not "quiet by value".
        assert raw["reflectivity_point_dbz"] is None

    def test_uh_signed_argmax(self):
        uh = _field(0.0)
        ci, cj = _cell_idx(*CENTRE)
        uh[ci, cj] = 10.0
        uh[ci + 1, cj + 1] = -40.0  # larger amplitude, negative sense
        with _patch_messages([_msg("uh_max_med", uh, 720)]):
            raws, _ = decode_icon_d2_explicit(b"main", None, 12, [CENTRE[0]], [CENTRE[1]])
        assert raws[0]["updraft_helicity_2_5km_hour_max_m2s2"] == -40.0

    def test_lpi_and_w_corridor_max(self):
        lpi = _field(0.0)
        w = _field(0.0)
        ci, cj = _cell_idx(*CENTRE)
        lpi[ci + 1, cj] = 7.5
        w[ci, cj + 1] = 12.0
        with _patch_messages([_msg("lpi_max", lpi, 720), _msg("w_ctmax", w, 720)]):
            raws, _ = decode_icon_d2_explicit(b"main", None, 12, [CENTRE[0]], [CENTRE[1]])
        assert raws[0]["lightning_potential_hour_max_jkg"] == 7.5
        assert raws[0]["updraft_hour_max_ms"] == 12.0

    def test_point_outside_grid_all_none_clipped(self):
        with _patch_messages([_msg("dbz_ctmax", _field(45.0), 720)]):
            raws, _ = decode_icon_d2_explicit(b"main", None, 12, [60.0], [1.0])
        raw = raws[0]
        assert raw["reflectivity_hour_max_dbz"] is None
        assert raw["corridor_clipped"] is True
        assert raw["dbz_valid_frac"] == 0.0


class TestEchoTopConstruction:
    def _quarters(self, base_p: float, hot: dict[int, float] | None = None):
        """Four quarter grids (all sentinel except *hot* cells by end-minute)."""
        qs = {}
        for end in (675, 690, 705, 720):
            grid = _field(-999.0)
            if hot and end in hot:
                ci, cj = _cell_idx(*CENTRE)
                grid[ci, cj] = hot[end]
            qs[end] = grid
        return qs

    def test_hourly_min_over_four_quarters(self):
        # 3 quarters from the previous file + 1 from this file (#462 rule).
        qs = self._quarters(-999.0, hot={690: 40000.0, 720: 30000.0})
        main = [_msg("echotop", qs[720], 720)]
        prev = [_msg("echotop", qs[e], e) for e in (675, 690, 705)]
        with _patch_messages(main, prev):
            raws, _ = decode_icon_d2_explicit(b"main", b"prev", 12, [CENTRE[0]], [CENTRE[1]])
        raw = raws[0]
        assert raw["echo_top_quarters_valid"] == 4
        assert raw["echo_top_18dbz_pa"] == 30000.0  # min pressure = highest echo

    def test_all_quiet_quarters_complete_but_none(self):
        # All four quarters valid but all-sentinel (no echo): complete AND
        # None — quiet, not unavailable.
        qs = self._quarters(-999.0)
        main = [_msg("echotop", qs[720], 720)]
        prev = [_msg("echotop", qs[e], e) for e in (675, 690, 705)]
        with _patch_messages(main, prev):
            raws, _ = decode_icon_d2_explicit(b"main", b"prev", 12, [CENTRE[0]], [CENTRE[1]])
        raw = raws[0]
        assert raw["echo_top_quarters_valid"] == 4
        assert raw["echo_top_18dbz_pa"] is None

    def test_missing_quarter_degrades_no_partial_min(self):
        qs = self._quarters(-999.0, hot={720: 30000.0})
        main = [_msg("echotop", qs[720], 720)]
        prev = [_msg("echotop", qs[e], e) for e in (675, 690)]  # 705 missing
        with _patch_messages(main, prev):
            raws, _ = decode_icon_d2_explicit(b"main", b"prev", 12, [CENTRE[0]], [CENTRE[1]])
        raw = raws[0]
        assert raw["echo_top_quarters_valid"] == 3
        # A partial min must NOT be presented as the hourly value.
        assert raw["echo_top_18dbz_pa"] is None

    def test_no_predecessor_blob(self):
        qs = self._quarters(-999.0, hot={720: 25000.0})
        main = [_msg("echotop", qs[720], 720)]
        with _patch_messages(main):
            raws, _ = decode_icon_d2_explicit(b"main", None, 12, [CENTRE[0]], [CENTRE[1]])
        assert raws[0]["echo_top_quarters_valid"] == 1
        assert raws[0]["echo_top_18dbz_pa"] is None


class TestGraupelDeaccum:
    def test_on_the_hour_per_cell_difference_then_corridor_max(self):
        ci, cj = _cell_idx(*CENTRE)
        accum_prev = _field(0.5)
        accum_h = _field(0.5)
        accum_h[ci + 1, cj + 1] = 3.0  # +2.5 mm this hour, one cell
        main = [
            _msg("grau_gsp", accum_h, 720, on_hour_accum=True),
            _msg("grau_gsp", _field(99.0), 735, on_hour_accum=False),  # ignored
        ]
        prev = [_msg("grau_gsp", accum_prev, 660, on_hour_accum=True)]
        with _patch_messages(main, prev):
            raws, _ = decode_icon_d2_explicit(b"main", b"prev", 12, [CENTRE[0]], [CENTRE[1]])
        raw = raws[0]
        assert raw["graupel_hour_mm"] == pytest.approx(2.5)
        assert raw["grau_valid_frac"] == 1.0

    def test_missing_predecessor_means_unknown_not_zero(self):
        main = [_msg("grau_gsp", _field(3.0), 720, on_hour_accum=True)]
        with _patch_messages(main):
            raws, _ = decode_icon_d2_explicit(b"main", None, 12, [CENTRE[0]], [CENTRE[1]])
        assert raws[0]["graupel_hour_mm"] is None
        assert raws[0]["grau_valid_frac"] == 0.0

    def test_reset_accumulation_clips_negative(self):
        accum_prev = _field(4.0)
        accum_h = _field(1.0)  # would be −3 without the clip
        main = [_msg("grau_gsp", accum_h, 720, on_hour_accum=True)]
        prev = [_msg("grau_gsp", accum_prev, 660, on_hour_accum=True)]
        with _patch_messages(main, prev):
            raws, _ = decode_icon_d2_explicit(b"main", b"prev", 12, [CENTRE[0]], [CENTRE[1]])
        assert raws[0]["graupel_hour_mm"] == 0.0


# ---------------------------------------------------------------------------
# Payload builder (completeness + ISA conversion)
# ---------------------------------------------------------------------------


class TestBuilder:
    def _raw(self, **over):
        raw = {
            "reflectivity_hour_max_dbz": 45.0,
            "reflectivity_point_dbz": 20.0,
            "dbz_valid_frac": 1.0,
            "echo_top_18dbz_pa": 30000.0,
            "echo_top_quarters_valid": 4,
            "lightning_potential_hour_max_jkg": 2.0,
            "lpi_valid_frac": 1.0,
            "updraft_hour_max_ms": 12.0,
            "w_valid_frac": 1.0,
            "updraft_helicity_2_5km_hour_max_m2s2": -30.0,
            "uh_valid_frac": 1.0,
            "graupel_hour_mm": 0.8,
            "grau_valid_frac": 1.0,
            "corridor_clipped": False,
        }
        raw.update(over)
        return raw

    def test_complete_payload(self):
        p = build_icon_d2_explicit_diagnostics(self._raw(), lead_hours=9.0)
        assert p.source == "icon_d2"
        assert p.detection_complete and p.strength_complete and p.echo_top_complete
        assert p.lead_hours == 9.0
        assert p.echo_top_18dbz_ft == pytest.approx(
            _isa_pressure_to_ft(30000.0), abs=1.0,
        )

    def test_low_valid_fraction_marks_channels_incomplete(self):
        p = build_icon_d2_explicit_diagnostics(
            self._raw(dbz_valid_frac=0.3, grau_valid_frac=0.1), 1.0,
        )
        assert not p.detection_complete
        assert not p.strength_complete
        assert p.echo_top_complete  # unaffected channel stays complete

    def test_clipped_corridor_marks_everything_incomplete(self):
        p = build_icon_d2_explicit_diagnostics(
            self._raw(corridor_clipped=True), 1.0,
        )
        assert not p.detection_complete
        assert not p.strength_complete
        assert not p.echo_top_complete

    def test_isa_pressure_to_ft(self):
        assert _isa_pressure_to_ft(101325.0) == pytest.approx(0.0, abs=1.0)
        # ~11 km tropopause break and stratospheric extension above it.
        assert _isa_pressure_to_ft(22632.06) == pytest.approx(36089.0, abs=50)
        assert _isa_pressure_to_ft(20000.0) > _isa_pressure_to_ft(22632.06)


# ---------------------------------------------------------------------------
# Decision table + assessment contract
# ---------------------------------------------------------------------------


class TestDecisionTable:
    @pytest.mark.parametrize(
        "dbz,corroborators,expected",
        [
            (None, 2, "NONE"),
            (20.0, 2, "NONE"),
            (34.9, 2, "NONE"),
            (36.0, 0, "NONE"),       # uncorroborated 35–44: no fire
            (36.0, 1, "MARGINAL"),
            (36.0, 2, "MODERATE"),
            (45.0, 0, "MODERATE"),
            (47.0, 1, "MODERATE"),
            (47.0, 2, "HIGH"),
            (52.0, 0, "HIGH"),       # ≥50 fires HIGH regardless
        ],
    )
    def test_table_rows(self, dbz, corroborators, expected):
        from weatherbrief.analysis.sounding.convective import _explicit_risk_from_table
        from weatherbrief.models import ConvectiveRisk

        assert _explicit_risk_from_table(dbz, corroborators) is ConvectiveRisk[expected]


def _payload(**over) -> "object":
    from weatherbrief.models import NWPExplicitConvectiveDiagnostics

    kw = dict(
        source="icon_d2",
        reflectivity_hour_max_dbz=None,
        detection_complete=True,
        strength_complete=True,
        echo_top_complete=True,
    )
    kw.update(over)
    return NWPExplicitConvectiveDiagnostics(**kw)


def _indices(**over):
    from weatherbrief.models import ThermodynamicIndices

    kw = dict(
        cape_surface_jkg=800.0,
        cin_surface_jkg=-50.0,
        freezing_level_ft=9000.0,
    )
    kw.update(over)
    return ThermodynamicIndices(**kw)


class TestAssessConvectiveExplicit:
    def test_incomplete_detection_returns_none(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        assert assess_convective_explicit(
            _indices(), None, _payload(detection_complete=False),
        ) is None

    def test_quiet_but_complete_is_real_none_not_none(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        a = assess_convective_explicit(
            _indices(), None,
            _payload(reflectivity_hour_max_dbz=-150.0),
        )
        assert a is not None
        assert a.risk_level.value == "none"
        assert a.method == "nwp_explicit"
        assert a.explicit_source == "icon_d2"

    def test_top_ft_never_set_even_with_echo_top(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        a = assess_convective_explicit(
            _indices(), None,
            _payload(
                reflectivity_hour_max_dbz=55.0,
                echo_top_18dbz_ft=38000.0,
            ),
        )
        assert a.top_ft is None  # clearance filter can never consume echo top
        assert a.echo_top_18dbz_ft == 38000.0  # carried as character detail
        assert any("not for overfly planning" in d for d in a.drivers)

    def test_fired_cell_with_corroborators(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit
        from weatherbrief.models import NWPCloudDiagnostics

        a = assess_convective_explicit(
            _indices(),
            NWPCloudDiagnostics(ml_cape_jkg=1200.0),
            _payload(
                reflectivity_hour_max_dbz=47.0,
                lightning_potential_hour_max_jkg=6.0,  # counts as 2
                updraft_hour_max_ms=15.0,
                graupel_hour_mm=1.2,
            ),
        )
        assert a.risk_level.value == "high"  # 45–49 row, |C| ≥ 2
        assert any("47 dBZ" in d for d in a.drivers)
        assert not any("hail" in d for d in a.drivers)
        assert any("mm this hour" in d for d in a.drivers)  # accumulation, not rate

    def test_incomplete_channels_never_upgrade(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        # 36 dBZ with ALL corroborator channels None → counts 0 → no fire.
        a = assess_convective_explicit(
            _indices(), None, _payload(reflectivity_hour_max_dbz=36.0),
        )
        assert a.risk_level.value == "none"
        assert any("unavailable" in d for d in a.drivers)

    def test_hail_wording_rephrased_to_graupel(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        a = assess_convective_explicit(
            _indices(freezing_level_ft=13000.0, cape_surface_jkg=1500.0),
            None,
            _payload(reflectivity_hour_max_dbz=52.0),
        )
        joined = " ".join(a.severe_modifiers + a.drivers)
        assert "hail" not in joined
        assert "graupel / strong mixed-phase core potential" in joined

    def test_uh_note_signed_and_narrative_only(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        a = assess_convective_explicit(
            _indices(), None,
            _payload(
                reflectivity_hour_max_dbz=40.0,  # |C|=0 row → stays NONE
                updraft_helicity_2_5km_hour_max_m2s2=-30.0,
            ),
        )
        assert a.risk_level.value == "none"  # UH never a tier input in v1
        # …and the note only appears once fired.
        b = assess_convective_explicit(
            _indices(), None,
            _payload(
                reflectivity_hour_max_dbz=50.0,
                updraft_helicity_2_5km_hour_max_m2s2=-30.0,
            ),
        )
        assert any("anticyclonic" in d for d in b.drivers)

    @pytest.mark.parametrize(
        "lead,fragment",
        [
            (3.0, "radar-nudged"),
            (12.0, "placement approximate"),
            (36.0, "environment-level signal"),
        ],
    )
    def test_lead_time_wording(self, lead, fragment):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        a = assess_convective_explicit(
            _indices(), None,
            _payload(reflectivity_hour_max_dbz=50.0, lead_hours=lead),
        )
        assert any(fragment in d for d in a.drivers)

    def test_cell_vs_shield_geometry_notes(self):
        from weatherbrief.analysis.sounding.convective import assess_convective_explicit

        cell = assess_convective_explicit(
            _indices(), None,
            _payload(reflectivity_hour_max_dbz=48.0, reflectivity_point_dbz=20.0),
        )
        assert any("Discrete cell geometry" in d for d in cell.drivers)

        shield = assess_convective_explicit(
            _indices(), None,
            _payload(reflectivity_hour_max_dbz=38.0, reflectivity_point_dbz=35.0),
        )
        assert any("widespread" in s for s in shield.suppressors)


class TestExplicitCrossCheck:
    def _nwp(self, risk, dbz):
        from weatherbrief.models import ConvectiveAssessment, ConvectiveRisk

        return ConvectiveAssessment(
            risk_level=ConvectiveRisk[risk],
            method="nwp_explicit",
            explicit_source="icon_d2",
            reflectivity_hour_max_dbz=dbz,
        )

    def _thermo(self, risk):
        from weatherbrief.models import ConvectiveAssessment, ConvectiveRisk

        return ConvectiveAssessment(risk_level=ConvectiveRisk[risk], method="thermo")

    def test_dd_not_corroborated(self):
        from weatherbrief.analysis.sounding.convective import convective_cross_check

        xc = convective_cross_check(self._thermo("MODERATE"), self._nwp("NONE", -150.0))
        assert xc is not None and xc.direction == "dd_not_corroborated"
        assert "explicit convection is quiet" in xc.note

    def test_model_active_dd_quiet(self):
        from weatherbrief.analysis.sounding.convective import convective_cross_check

        xc = convective_cross_check(self._thermo("NONE"), self._nwp("MODERATE", 47.0))
        assert xc is not None and xc.direction == "model_active_dd_quiet"
        assert "explicitly develops a cell" in xc.note

    def test_marginal_explicit_is_silent(self):
        from weatherbrief.analysis.sounding.convective import convective_cross_check

        assert convective_cross_check(self._thermo("NONE"), self._nwp("MARGINAL", 36.0)) is None
        assert convective_cross_check(self._thermo("MODERATE"), self._nwp("MARGINAL", 36.0)) is None


# ---------------------------------------------------------------------------
# Evaluator: explicit-unavailable renders UNAVAILABLE, never GREEN
# ---------------------------------------------------------------------------


def _make_rpa(point_index: int, distance_nm: float, sounding=None):
    from weatherbrief.models import RoutePointAnalysis

    return RoutePointAnalysis(
        point_index=point_index,
        lat=48.0 + point_index * 0.5,
        lon=2.0 + point_index * 0.5,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=135.0,
        sounding=sounding or {},
        model_divergence=[],
    )


def _make_elevation():
    from weatherbrief.models import ElevationPoint, ElevationProfile

    points = [
        ElevationPoint(
            distance_nm=i * 200 / 19, elevation_ft=500,
            lat=48.0 + i * 0.1, lon=2.0 + i * 0.1,
        )
        for i in range(20)
    ]
    return ElevationProfile(
        route_name="test", points=points,
        max_elevation_ft=500, total_distance_nm=200,
    )


def _eval_context(soundings: list) -> "object":
    from weatherbrief.analysis.advisories import RouteContext

    analyses = [
        _make_rpa(i, i * 20.0, sounding={"icon": s} if s is not None else {})
        for i, s in enumerate(soundings)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["icon"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


class TestEvaluatorExplicitUnavailable:
    def test_unavailable_points_render_unavailable_not_green(self):
        from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
        from weatherbrief.models import (
            ConvectiveAssessment,
            HighlightSeverity,
            SoundingAnalysis,
            ThermodynamicIndices,
        )

        unavailable = SoundingAnalysis(
            indices=ThermodynamicIndices(),
            convective=None,
            convective_explicit_unavailable=True,
        )
        quiet = SoundingAnalysis(
            indices=ThermodynamicIndices(),
            convective=ConvectiveAssessment(method="nwp_explicit"),
        )
        ctx = _eval_context([unavailable, unavailable, quiet, quiet, quiet])
        result = ConvectiveEvaluator.evaluate(ctx, {})
        per_model = result.per_model[0]
        assert per_model.status.value == "green"  # quiet points still grade
        assert per_model.total_points == 3  # unavailable excluded from extent
        ribbon = per_model.highlights.ribbon  # list[RibbonSegment]
        assert any(
            seg.severity == HighlightSeverity.UNAVAILABLE for seg in ribbon
        )

    def test_all_unavailable_means_advisory_unavailable(self):
        from weatherbrief.analysis.advisories.convective import ConvectiveEvaluator
        from weatherbrief.models import SoundingAnalysis, ThermodynamicIndices

        unavailable = SoundingAnalysis(
            indices=ThermodynamicIndices(),
            convective_explicit_unavailable=True,
        )
        ctx = _eval_context([unavailable, unavailable])
        result = ConvectiveEvaluator.evaluate(ctx, {})
        assert result.per_model[0].status.value == "unavailable"


# ---------------------------------------------------------------------------
# Fill hold-over (covering-interval semantics)
# ---------------------------------------------------------------------------


class TestFillHoldOver:
    def _hourly(self, hour, payload=None):
        from weatherbrief.models import HourlyForecast

        h = HourlyForecast(time=datetime(2026, 7, 21, hour, 0, tzinfo=timezone.utc))
        h.explicit_convective_diagnostics = payload
        return h

    def test_gap_hour_gets_next_anchor_payload_trailing_gets_last(self):
        from weatherbrief.fetch.grib.fill import _fill_explicit_hourly
        from weatherbrief.models import NWPExplicitConvectiveDiagnostics

        p12 = NWPExplicitConvectiveDiagnostics(
            source="icon_d2", reflectivity_hour_max_dbz=40.0,
        )
        p13 = NWPExplicitConvectiveDiagnostics(
            source="icon_d2", reflectivity_hour_max_dbz=50.0,
        )
        hours = [
            self._hourly(12, p12),
            self._hourly(13, None),  # gap — inside the (12,13] window
            self._hourly(14, p13),
            self._hourly(15, None),  # trailing — last completed interval
        ]
        # Rename for clarity: hours[1] sits between anchors 12:00 and 14:00.
        filled = _fill_explicit_hourly(hours)
        assert filled == 2
        # Gap hour inherits the NEXT anchor's window, not the previous storm.
        assert hours[1].explicit_convective_diagnostics.reflectivity_hour_max_dbz == 50.0
        # Trailing hour holds the last completed interval.
        assert hours[3].explicit_convective_diagnostics.reflectivity_hour_max_dbz == 50.0
        # Anchors untouched.
        assert hours[0].explicit_convective_diagnostics is p12

    def test_no_payloads_is_noop(self):
        from weatherbrief.fetch.grib.fill import _fill_explicit_hourly

        assert _fill_explicit_hourly([self._hourly(12), self._hourly(13)]) == 0


# ---------------------------------------------------------------------------
# Domain-mask gate in _prepare_icon_eu
# ---------------------------------------------------------------------------


class TestDomainMaskGate:
    def _icon_cross_sections(self):
        from weatherbrief.models import ModelSource, RouteCrossSection

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

    def test_masked_corridor_falls_back_to_eu(self, tmp_path):
        from weatherbrief.fetch.grib import _prepare_icon_eu
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_EU
        from weatherbrief.models import RoutePoint

        route = [
            RoutePoint(lat=48.35, lon=11.79, distance_from_origin_nm=0),
            RoutePoint(lat=52.52, lon=13.4, distance_from_origin_nm=300),
        ]
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ), patch(
            "weatherbrief.fetch.grib._icon_d2_route_corridors_valid",
            return_value=False,
        ):
            ctx, skip = _prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert skip is None
        assert ctx is not None
        assert ctx.variant is ICON_EU

    def test_valid_corridor_keeps_d2(self, tmp_path):
        from weatherbrief.fetch.grib import _prepare_icon_eu
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
        from weatherbrief.models import RoutePoint

        route = [RoutePoint(lat=48.35, lon=11.79, distance_from_origin_nm=0)]
        dep = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        with patch(
            "weatherbrief.fetch.grib.icon_eu_fetch.find_latest_icon_eu_run",
            self._run_finder(d2_run=("20260720", 12), eu_run=("20260720", 12)),
        ), patch(
            "weatherbrief.fetch.grib._icon_d2_route_corridors_valid",
            return_value=True,
        ):
            ctx, skip = _prepare_icon_eu(
                self._icon_cross_sections(), route, dep,
                data_dir=tmp_path, flight_duration_hours=2.0,
            )
        assert ctx is not None
        assert ctx.variant is ICON_D2


# ---------------------------------------------------------------------------
# Total-D2-failure → ICON-EU re-run (carried from PR #461 review)
# ---------------------------------------------------------------------------


class TestTotalD2FailureFallback:
    def test_decode_failure_reruns_icon_slot_on_eu(self, tmp_path):
        import contextlib
        from types import SimpleNamespace
        from unittest.mock import Mock

        import weatherbrief.fetch.grib as grib
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2, ICON_EU

        d2_ctx = SimpleNamespace(variant=ICON_D2)
        eu_ctx = SimpleNamespace(variant=ICON_EU)
        prepare_calls = []
        decode_calls = []

        def _prepare(*args, **kwargs):
            prepare_calls.append(kwargs.get("force_variant"))
            return (d2_ctx, None) if len(prepare_calls) == 1 else (eu_ctx, None)

        def _decode(ctx, *args):
            decode_calls.append(ctx.variant.slug)
            if ctx.variant.slug == "icon-d2":
                return None, None  # total D2 failure
            return 2026072012, None

        timer = Mock()
        with patch.object(grib, "_prepare_icon_eu", side_effect=_prepare), \
             patch.object(grib, "_decode_and_merge_icon_eu", side_effect=_decode), \
             patch.object(grib, "_prefetch_icon_eu_data", lambda *a, **k: None), \
             patch.object(grib, "_enrich_gfs", lambda *a, **k: None), \
             patch.object(grib, "_enrich_ecmwf", lambda *a, **k: None), \
             patch.object(grib, "_grib_time", lambda *a, **k: contextlib.nullcontext()):
            init_times, sources, skips = grib._enrich_forecasts_inner(
                timer, [], [], [],
                datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc),
                data_dir=tmp_path, flight_duration_hours=2.0,
            )

        assert decode_calls == ["icon-d2", "icon-eu"]
        assert prepare_calls[1] is ICON_EU
        assert init_times.get("icon") == 2026072012
