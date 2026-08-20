"""GFS convective ingredients from GRIB (#566).

GFS was the only model with no CAPE, CIN or convective precipitation from
GRIB, so it alone fell back to Open-Meteo's *surface-based* CAPE while ECMWF
and ICON supply mixed-layer values natively — a cross-model comparison was
partly comparing parcel definitions rather than model skill.

The `.idx` fixtures below are copied verbatim from a live index
(gfs.20260820/06z f006). Level strings are not guessable: DWD's RAIN_CON
decodes under `crr`, not `rain_con`, and a key that matches nothing fails
silently by producing no field at all.
"""

from __future__ import annotations

import pytest

from weatherbrief.fetch.grib.decode import (
    _CLOUD_DIAG_FIELD_MAP,
    _kg_m2_s_to_mm_h,
)
from weatherbrief.fetch.grib.gfs_idx import (
    _CLOUD_DIAG_PAIRS,
    parse_cloud_diag_idx,
)

# Verbatim from the live index, including the instantaneous/averaged twins.
_IDX = """\
1:0:d=2026082006:CPRAT:surface:6 hour fcst:
2:100:d=2026082006:CPRAT:surface:0-6 hour ave fcst:
3:200:d=2026082006:ACPCP:surface:0-6 hour acc fcst:
4:300:d=2026082006:CAPE:surface:6 hour fcst:
5:400:d=2026082006:CIN:surface:6 hour fcst:
6:500:d=2026082006:CAPE:180-0 mb above ground:6 hour fcst:
7:600:d=2026082006:CIN:180-0 mb above ground:6 hour fcst:
8:700:d=2026082006:CAPE:90-0 mb above ground:6 hour fcst:
9:800:d=2026082006:TCDC:convective cloud layer:6 hour fcst:
10:900:d=2026082006:PRES:convective cloud top level:6 hour fcst:
11:1000:d=2026082006:HGT:cloud ceiling:6 hour fcst:
"""


class TestRequestedPairs:
    def test_mixed_layer_cape_and_cin_are_requested(self):
        assert ("CAPE", "180-0 mb above ground") in _CLOUD_DIAG_PAIRS
        assert ("CIN", "180-0 mb above ground") in _CLOUD_DIAG_PAIRS

    def test_convective_precip_rate_is_requested(self):
        assert ("CPRAT", "surface") in _CLOUD_DIAG_PAIRS

    def test_surface_based_cape_is_not_requested(self):
        """Surface-based CAPE is the parcel we are moving *away* from.

        Requesting it would reintroduce the ambiguity `nwp_cape_type` exists to
        remove, and cost byte ranges for a field nothing reads.
        """
        assert ("CAPE", "surface") not in _CLOUD_DIAG_PAIRS


class TestIdxSelection:
    def test_mixed_layer_cape_is_selected_not_the_surface_one(self):
        entries = parse_cloud_diag_idx(_IDX)
        capes = [e for e in entries if e.variable == "CAPE"]
        assert len(capes) == 1
        assert capes[0].level_str == "180-0 mb above ground"

    def test_cprat_resolves_to_the_instantaneous_twin(self):
        """CPRAT appears twice at `surface` — instantaneous and 0-6 h averaged.

        The instantaneous form is the one that needs no de-accumulation, unlike
        ECMWF `cp` and ICON `crr`.
        """
        entries = parse_cloud_diag_idx(_IDX)
        cprat = [e for e in entries if e.variable == "CPRAT"]
        assert len(cprat) == 1, "the averaged twin was not filtered out"
        assert "ave" not in cprat[0].forecast_step

    def test_ninety_mb_cape_is_ignored(self):
        entries = parse_cloud_diag_idx(_IDX)
        assert not any(e.level_str == "90-0 mb above ground" for e in entries)


class TestFieldMapping:
    def test_cape_and_cin_map_to_the_mixed_layer_fields(self):
        assert _CLOUD_DIAG_FIELD_MAP[("cape", "pressureFromGroundLayer")] == "ml_cape_jkg"
        assert _CLOUD_DIAG_FIELD_MAP[("cin", "pressureFromGroundLayer")] == "ml_cin_jkg"

    def test_hrrr_and_gfs_agree_on_the_key(self):
        """HRRR already decoded CAPE under this exact key — the precedent that
        made the GFS side safe to write without a live GRIB to test against."""
        from weatherbrief.fetch.grib.decode import _HRRR_CLOUD_DIAG_FIELD_MAP

        key = ("cape", "pressureFromGroundLayer")
        assert _HRRR_CLOUD_DIAG_FIELD_MAP[key] == _CLOUD_DIAG_FIELD_MAP[key]

    def test_both_cprat_forms_map_to_one_field(self):
        assert _CLOUD_DIAG_FIELD_MAP[("cprat", "surface")] == "conv_precip_rate_kg_m2_s"
        assert _CLOUD_DIAG_FIELD_MAP[("avg_cprat", "surface")] == "conv_precip_rate_kg_m2_s"


class TestRateConversion:
    def test_kg_m2_s_to_mm_h(self):
        # 1 kg/m2 == 1 mm of water, so only the time base changes.
        assert _kg_m2_s_to_mm_h(0.0005) == pytest.approx(1.8)
        assert _kg_m2_s_to_mm_h(0.0) == pytest.approx(0.0)

    def test_none_passes_through(self):
        assert _kg_m2_s_to_mm_h(None) is None


class TestDiagnosticsReachTheSnapshot:
    """The decode already produced these; the DTO threw them away (#565/#566)."""

    def test_builder_emits_the_convective_ingredients(self):
        from weatherbrief.fetch.grib.decode import build_cloud_diagnostics

        diag = build_cloud_diagnostics({
            "ceiling_gpm": 500.0,
            "convective_cover_pct": 40.0,
            "convective_base_pa": 85000.0,
            "convective_top_pa": 25000.0,
            "conv_precip_rate_kg_m2_s": 0.0005,
            "ml_cape_jkg": 1300.0,
            "ml_cin_jkg": -20.0,
        })

        assert diag is not None
        assert diag.convective_precip_mm_h == pytest.approx(1.8)
        assert diag.ml_cape_jkg == pytest.approx(1300.0)
        assert diag.ml_cin_jkg == pytest.approx(-20.0)
        assert diag.convective_cover_pct == pytest.approx(40.0)

    def test_dto_carries_them_through(self):
        from weatherbrief.fetch.grib.decode import build_cloud_diagnostics
        from weatherbrief.tasks.standalone_grib import _diagnostics_from

        diag = build_cloud_diagnostics({
            "ceiling_gpm": 500.0,
            "convective_top_pa": 25000.0,
            "conv_precip_rate_kg_m2_s": 0.001,
            "ml_cape_jkg": 900.0,
        })
        carried = _diagnostics_from(diag)

        assert carried.ml_cape_jkg == pytest.approx(900.0)
        assert carried.convective_precip_mm_h == pytest.approx(3.6)
        assert carried.convective_top_ft is not None
        assert carried.nwp_ceiling_ft is not None
