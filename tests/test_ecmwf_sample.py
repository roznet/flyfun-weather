"""Phase 0: Validate ECMWF sample GRIB files.

Run with: pytest tests/test_ecmwf_sample.py -v -s

Set ECMWF_GRIB_DIR to point at the directory containing sample files,
or place them in tests/data/ecmwf_samples/.

These tests are designed to:
1. Inventory all GRIB messages and report what's inside (variables, levels, grid)
2. Verify we can decode with cfgrib (our existing dependency)
3. Test spatial interpolation to a European route point
4. Validate the filename parser against real filenames
5. Check for the cloud microphysics variables we need for icing

All tests skip gracefully if no sample files are found, so this file
is safe to run even before the data arrives.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.fetch.grib.ecmwf_fetch import (
    ECMWFFileInfo,
    find_files_for_run,
    find_latest_ecmwf_run,
    parse_ecmwf_filename,
    scan_ecmwf_files,
)

# ---------------------------------------------------------------------------
# Sample directory resolution
# ---------------------------------------------------------------------------

_SAMPLE_DIR_ENV = os.environ.get("ECMWF_GRIB_DIR")
_SAMPLE_DIR_LOCAL = Path(__file__).parent / "data" / "ecmwf_samples"

SAMPLE_DIR = Path(_SAMPLE_DIR_ENV) if _SAMPLE_DIR_ENV else _SAMPLE_DIR_LOCAL


def _grib_files() -> list[Path]:
    """Collect all ECMWF GRIB files from the sample directory.

    ECMWF ECPDS files may have no extension — we identify them by
    parsing the filename against the ECMWF naming convention.
    """
    if not SAMPLE_DIR.exists():
        return []
    files = [
        p for p in SAMPLE_DIR.rglob("*")
        if p.is_file() and parse_ecmwf_filename(p) is not None
    ]
    return sorted(files)


def _has_samples() -> bool:
    return len(_grib_files()) > 0


skip_no_samples = pytest.mark.skipif(
    not _has_samples(),
    reason=f"No ECMWF sample files in {SAMPLE_DIR}",
)

# ---------------------------------------------------------------------------
# Filename parser tests (run without data files)
# ---------------------------------------------------------------------------


class TestFilenameParser:
    """Test the ECMWF filename parser against the documented convention."""

    def test_parse_standard_operational(self):
        """Parse a standard operational IFS filename."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T120000Z_20260408T000000Z_12"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.destination == "ABC"
        assert info.feed == "XY"
        assert info.model == "ifs"
        assert info.data_class == "od"
        assert info.stream == "oper"
        assert info.data_type == "fc"
        assert info.base_time == datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
        assert info.valid_time == datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc)
        assert info.step_hours == 12
        assert info.experiment is None
        assert info.is_operational
        assert info.init_date == "20260407"
        assert info.init_hour == 12

    def test_parse_with_experiment_version(self):
        """Parse filename with experiment version number."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T000000Z_20260407T060000Z_6_0042"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.experiment == "0042"
        assert not info.is_operational

    def test_parse_operational_expver_1(self):
        """Experiment version '1' is treated as operational."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T000000Z_20260407T060000Z_6_1"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.experiment == "1"
        assert info.is_operational

    def test_parse_operational_expver_0001(self):
        """Experiment version '0001' (ECMWF standard 4-digit) is operational."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T000000Z_20260407T060000Z_6_0001"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.experiment == "0001"
        assert info.is_operational

    def test_parse_aifs_ens(self):
        """Parse AIFS ensemble model filename."""
        path = Path(
            "DST_FD_aifs-ens_od_enfo_fc_20260407T000000Z_20260408T000000Z_24"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.model == "aifs-ens"
        assert info.stream == "enfo"
        assert info.step_hours == 24

    def test_parse_with_grib2_extension(self):
        """Extension is stripped before parsing."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T120000Z_20260407T180000Z_6.grib2"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.step_hours == 6

    def test_parse_compound_extension(self):
        """Compound extensions like .grib2.bz2 are fully stripped."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T120000Z_20260407T180000Z_6.grib2.bz2"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.step_hours == 6

    def test_parse_step_with_h_suffix(self):
        """Step field with 'h' suffix (hours)."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T000000Z_20260408T000000Z_24h"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.step_hours == 24

    def test_parse_step_with_d_suffix(self):
        """Step field with 'd' suffix (days)."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T000000Z_20260410T000000Z_3d"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.step_hours == 72

    def test_parse_ifs_ens_cf_no_extension(self):
        """Parse real sample filename: ifs-ens-cf with no extension."""
        path = Path(
            "brg_a1_ifs-ens-cf_od_oper_fc_20260406T000000Z_20260406T030000Z_3h"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.destination == "brg"
        assert info.feed == "a1"
        assert info.model == "ifs-ens-cf"
        assert info.data_class == "od"
        assert info.stream == "oper"
        assert info.data_type == "fc"
        assert info.step_hours == 3
        assert info.base_time == datetime(2026, 4, 6, 0, 0, 0, tzinfo=timezone.utc)
        assert info.is_surface
        assert not info.is_pressure_level
        # expver not in filename but sample data has 0001 internally
        assert info.experiment is None
        assert info.is_operational

    def test_parse_a2_pressure_level_file(self):
        """Parse a2 (pressure-level) file."""
        path = Path(
            "brg_a2_ifs-ens-cf_od_oper_fc_20260406T000000Z_20260407T000000Z_24h"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.feed == "a2"
        assert info.is_pressure_level
        assert not info.is_surface
        assert info.step_hours == 24

    def test_parse_scda_stream(self):
        """Parse scda (short cut-off data assimilation) stream file."""
        path = Path(
            "brg_a1_ifs-ens-cf_od_scda_fc_20260406T060000Z_20260406T090000Z_3h"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        assert info.stream == "scda"
        assert info.init_hour == 6

    def test_parse_invalid_filename(self):
        """Non-matching filename returns None."""
        assert parse_ecmwf_filename(Path("random_file.grib2")) is None

    def test_init_timestamp(self):
        """Unix timestamp property works correctly."""
        path = Path(
            "ABC_XY_ifs_od_oper_fc_20260407T120000Z_20260408T000000Z_12"
        )
        info = parse_ecmwf_filename(path)
        assert info is not None
        expected = datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)
        assert info.init_timestamp == int(expected.timestamp())


# ---------------------------------------------------------------------------
# Directory scanner tests (run without data files, using tmp dirs)
# ---------------------------------------------------------------------------


class TestDirectoryScanner:
    """Test scan_ecmwf_files and related functions."""

    def test_scan_empty_directory(self, tmp_path):
        """Empty directory returns empty list."""
        files = scan_ecmwf_files(tmp_path)
        assert files == []

    def test_scan_nonexistent_directory(self, tmp_path):
        """Non-existent directory returns empty list."""
        files = scan_ecmwf_files(tmp_path / "nope")
        assert files == []

    def test_scan_finds_grib_files(self, tmp_path):
        """Scanner finds and parses valid GRIB filenames."""
        name = "ABC_XY_ifs_od_oper_fc_20260407T120000Z_20260408T000000Z_12.grib2"
        (tmp_path / name).write_bytes(b"")  # empty file, parser only reads name
        files = scan_ecmwf_files(tmp_path)
        assert len(files) == 1
        assert files[0].model == "ifs"
        assert files[0].step_hours == 12

    def test_scan_filters_by_model(self, tmp_path):
        """Model filter works."""
        for model in ("ifs", "aifs-ens"):
            name = f"A_B_{model}_od_oper_fc_20260407T000000Z_20260407T060000Z_6.grib2"
            (tmp_path / name).write_bytes(b"")
        files = scan_ecmwf_files(tmp_path, model="ifs")
        assert len(files) == 1
        assert files[0].model == "ifs"

    def test_scan_filters_operational(self, tmp_path):
        """Operational filter skips experimental runs."""
        op = "A_B_ifs_od_oper_fc_20260407T000000Z_20260407T060000Z_6.grib2"
        exp = "A_B_ifs_od_oper_fc_20260407T000000Z_20260407T060000Z_6_0042.grib2"
        (tmp_path / op).write_bytes(b"")
        (tmp_path / exp).write_bytes(b"")
        files = scan_ecmwf_files(tmp_path, operational_only=True)
        assert len(files) == 1
        assert files[0].is_operational

    def test_scan_sorted_by_time_and_step(self, tmp_path):
        """Results are sorted by base_time then step_hours."""
        valid_times = {6: "20260407T060000Z", 12: "20260407T120000Z", 24: "20260408T000000Z"}
        for step in (12, 6, 24):
            name = f"A_B_ifs_od_oper_fc_20260407T000000Z_{valid_times[step]}_{step}.grib2"
            (tmp_path / name).write_bytes(b"")
        files = scan_ecmwf_files(tmp_path)
        steps = [f.step_hours for f in files]
        assert steps == [6, 12, 24]

    def test_find_latest_run(self, tmp_path):
        """find_latest_ecmwf_run returns the newest base_time."""
        for hour in (0, 12):
            name = f"A_B_ifs_od_oper_fc_20260407T{hour:02d}0000Z_20260408T000000Z_24.grib2"
            (tmp_path / name).write_bytes(b"")
        latest = find_latest_ecmwf_run(tmp_path)
        assert latest == datetime(2026, 4, 7, 12, 0, 0, tzinfo=timezone.utc)

    def test_find_files_for_run(self, tmp_path):
        """find_files_for_run returns only files from the specified run."""
        bt = datetime(2026, 4, 7, 0, 0, 0, tzinfo=timezone.utc)
        for step in (6, 12):
            name = f"A_B_ifs_od_oper_fc_20260407T000000Z_20260407T{step:02d}0000Z_{step}.grib2"
            (tmp_path / name).write_bytes(b"")
        # Different run
        other = "A_B_ifs_od_oper_fc_20260406T120000Z_20260407T000000Z_12.grib2"
        (tmp_path / other).write_bytes(b"")

        files = find_files_for_run(bt, tmp_path)
        assert len(files) == 2
        assert all(f.base_time == bt for f in files)


# ---------------------------------------------------------------------------
# GRIB content tests (require actual sample files)
# ---------------------------------------------------------------------------


@skip_no_samples
class TestSampleInventory:
    """Inventory the contents of sample GRIB files.

    These tests print diagnostic info (-s flag) and assert basic sanity.
    """

    def test_list_all_files(self):
        """List all sample files and parse their filenames."""
        grib_files = _grib_files()
        print(f"\n{'='*70}")
        print(f"ECMWF Sample Directory: {SAMPLE_DIR}")
        print(f"Total GRIB files: {len(grib_files)}")
        print(f"{'='*70}")

        for path in grib_files:
            info = parse_ecmwf_filename(path)
            size_kb = path.stat().st_size / 1024
            if info:
                print(f"\n  {path.name} ({size_kb:.0f} KB)")
                print(f"    Model: {info.model}  Class: {info.data_class}  "
                      f"Stream: {info.stream}  Type: {info.data_type}")
                print(f"    Base: {info.base_time.isoformat()}  "
                      f"Valid: {info.valid_time.isoformat()}  "
                      f"Step: {info.step_hours}h")
                if info.experiment:
                    print(f"    Experiment: {info.experiment}")
            else:
                print(f"\n  {path.name} ({size_kb:.0f} KB) — UNPARSED FILENAME")

        assert len(grib_files) > 0, "Expected at least one GRIB file"

    def test_decode_messages(self):
        """Decode all sample files with cfgrib and report contents."""
        import cfgrib

        print(f"\n{'='*70}")
        print("GRIB MESSAGE INVENTORY")
        print(f"{'='*70}")

        all_vars: set[str] = set()
        all_levels: set[str] = set()

        for path in _grib_files():
            print(f"\n--- {path.name} ---")
            try:
                datasets = cfgrib.open_datasets(str(path))
            except Exception as e:
                print(f"  cfgrib FAILED: {e}")
                print("  Will need ecCodes fallback — see test_eccodes_fallback")
                continue

            for i, ds in enumerate(datasets):
                var_names = list(ds.data_vars)
                coord_names = list(ds.coords)
                dims = dict(ds.dims)
                all_vars.update(str(v) for v in var_names)

                print(f"  Dataset[{i}]:")
                print(f"    Variables: {var_names}")
                print(f"    Coords:    {coord_names}")
                print(f"    Dims:      {dims}")

                # Report grid info
                if "latitude" in ds.coords and "longitude" in ds.coords:
                    lats = ds.coords["latitude"].values
                    lons = ds.coords["longitude"].values
                    print(f"    Grid:      lat [{lats.min():.2f}, {lats.max():.2f}] "
                          f"({len(lats)} pts, Δ={_grid_spacing(lats):.3f}°)")
                    print(f"               lon [{lons.min():.2f}, {lons.max():.2f}] "
                          f"({len(lons)} pts, Δ={_grid_spacing(lons):.3f}°)")

                # Report pressure levels
                for level_coord in ("isobaricInhPa", "level", "pressure",
                                    "hybrid", "generalVerticalLayer"):
                    if level_coord in ds.coords:
                        levels = ds.coords[level_coord].values
                        all_levels.add(level_coord)
                        print(f"    Levels ({level_coord}): {levels}")

                # Report value ranges for each variable
                for var in var_names:
                    data = ds[var].values
                    finite = data[np.isfinite(data)]
                    if len(finite) > 0:
                        print(f"    {var}: min={finite.min():.6g}  "
                              f"max={finite.max():.6g}  "
                              f"mean={finite.mean():.6g}")

        print(f"\n{'='*70}")
        print(f"ALL VARIABLES FOUND: {sorted(all_vars)}")
        print(f"LEVEL COORDINATES:   {sorted(all_levels)}")
        print(f"{'='*70}")

    def test_check_microphysics_variables(self):
        """Check if cloud microphysics variables are present.

        We need CLWC (cloud liquid water content) and CIWC (cloud ice water
        content) for icing assessment. Report what we find.
        """
        import cfgrib

        # Known ECMWF shortNames for cloud microphysics
        clw_candidates = {"clwc", "clwmr", "clmr", "qliq", "q_liquid", "lwc"}
        ice_candidates = {"ciwc", "icmr", "qice", "q_ice", "iwc"}

        found_clw: dict[str, list[str]] = {}
        found_ice: dict[str, list[str]] = {}

        for path in _grib_files():
            try:
                datasets = cfgrib.open_datasets(str(path))
            except Exception:
                continue
            for ds in datasets:
                var_names = {str(v).lower() for v in ds.data_vars}
                clw = var_names & clw_candidates
                ice = var_names & ice_candidates
                if clw:
                    found_clw[path.name] = sorted(clw)
                if ice:
                    found_ice[path.name] = sorted(ice)

        print(f"\n{'='*70}")
        print("MICROPHYSICS VARIABLE CHECK")
        print(f"{'='*70}")
        print(f"Cloud liquid water found: {found_clw or 'NONE'}")
        print(f"Cloud ice found:          {found_ice or 'NONE'}")
        print(f"{'='*70}")

        if not found_clw and not found_ice:
            print("\nWARNING: No microphysics variables found.")
            print("The sample may only contain standard fields (T, U, V, etc.).")
            print("Check if we need to request specific parameters in the "
                  "service agreement.")

    def test_spatial_interpolation(self):
        """Verify we can interpolate GRIB data to a route point.

        Uses a central European coordinate (near Munich) as a test point.
        """
        import cfgrib

        test_lat, test_lon = 48.0, 11.5  # near Munich, EDDM

        print(f"\n{'='*70}")
        print(f"SPATIAL INTERPOLATION TEST — ({test_lat}, {test_lon})")
        print(f"{'='*70}")

        for path in _grib_files():
            try:
                datasets = cfgrib.open_datasets(str(path))
            except Exception:
                continue

            for i, ds in enumerate(datasets):
                if "latitude" not in ds.coords or "longitude" not in ds.coords:
                    continue

                lats = ds.coords["latitude"].values
                lons = ds.coords["longitude"].values

                # Check if test point is within grid
                if (test_lat < lats.min() or test_lat > lats.max() or
                        test_lon < lons.min() or test_lon > lons.max()):
                    # Try 0-360 convention
                    test_lon_360 = test_lon % 360
                    if (test_lon_360 < lons.min() or test_lon_360 > lons.max()):
                        print(f"  {path.name}[{i}]: test point outside grid")
                        continue

                try:
                    point = ds.sel(
                        latitude=test_lat, longitude=test_lon,
                        method="nearest",
                    )
                    print(f"\n  {path.name}[{i}] at ({test_lat}, {test_lon}):")
                    for var in ds.data_vars:
                        val = point[var].values
                        if np.ndim(val) == 0:
                            print(f"    {var} = {float(val):.6g}")
                        else:
                            # Multi-level variable
                            finite = val[np.isfinite(val)]
                            if len(finite) > 0:
                                print(f"    {var} = [{finite.min():.6g} .. "
                                      f"{finite.max():.6g}] "
                                      f"({len(finite)} levels)")
                except Exception as e:
                    print(f"  {path.name}[{i}]: interpolation failed: {e}")


# ---------------------------------------------------------------------------
# ecCodes fallback test
# ---------------------------------------------------------------------------


@skip_no_samples
class TestEcCodesFallback:
    """Test decoding with ecCodes directly if cfgrib has issues.

    ECMWF recommends ecCodes for their data. If cfgrib works, we prefer
    it for consistency with GFS/ICON-EU. But this confirms ecCodes works
    as a fallback.
    """

    def test_eccodes_message_iteration(self):
        """Iterate GRIB messages with ecCodes low-level API."""
        try:
            import eccodes
        except ImportError:
            pytest.skip("eccodes not installed (pip install eccodes)")

        for path in _grib_files()[:3]:  # just first 3 files
            print(f"\n--- {path.name} (ecCodes) ---")
            with open(path, "rb") as f:
                msg_count = 0
                while True:
                    msgid = eccodes.codes_grib_new_from_file(f)
                    if msgid is None:
                        break
                    try:
                        short_name = eccodes.codes_get(msgid, "shortName")
                        type_of_level = eccodes.codes_get(msgid, "typeOfLevel")
                        level = eccodes.codes_get(msgid, "level")
                        step = eccodes.codes_get(msgid, "stepRange")
                        ni = eccodes.codes_get(msgid, "Ni")
                        nj = eccodes.codes_get(msgid, "Nj")
                        print(f"  msg {msg_count}: {short_name} "
                              f"level={level} ({type_of_level}) "
                              f"step={step} grid={ni}x{nj}")
                        msg_count += 1
                    finally:
                        eccodes.codes_release(msgid)

                print(f"  Total messages: {msg_count}")


# ---------------------------------------------------------------------------
# Integration test: decode through our pipeline
# ---------------------------------------------------------------------------


@skip_no_samples
class TestPipelineDecode:
    """Test that ECMWF files work through our existing decode infrastructure."""

    def test_decode_through_var_map(self):
        """Verify our _VAR_MAP catches ECMWF variable names."""
        import cfgrib
        from weatherbrief.fetch.grib.decode import _VAR_MAP

        mapped: dict[str, str] = {}
        unmapped: set[str] = set()

        for path in _grib_files():
            try:
                datasets = cfgrib.open_datasets(str(path))
            except Exception:
                continue
            for ds in datasets:
                for var in ds.data_vars:
                    var_lower = str(var).lower()
                    if var_lower in _VAR_MAP:
                        mapped[var_lower] = _VAR_MAP[var_lower]
                    else:
                        unmapped.add(var_lower)

        print(f"\n{'='*70}")
        print("DECODE _VAR_MAP COVERAGE")
        print(f"{'='*70}")
        print(f"Mapped:   {mapped}")
        print(f"Unmapped: {sorted(unmapped)}")
        print(f"{'='*70}")

        if unmapped:
            print("\nUnmapped variables may need to be added to _VAR_MAP in "
                  "decode.py if they are useful for our analysis.")


# ---------------------------------------------------------------------------
# ECMWF decode function tests (require sample data)
# ---------------------------------------------------------------------------


@skip_no_samples
class TestECMWFDecode:
    """Test the new ECMWF-specific decode functions against sample data."""

    def _find_a2_file(self) -> Path | None:
        """Find a pressure-level (a2) sample file."""
        for p in _grib_files():
            info = parse_ecmwf_filename(p)
            if info and info.is_pressure_level:
                return p
        return None

    def _find_a1_file(self) -> Path | None:
        """Find a surface (a1) sample file."""
        for p in _grib_files():
            info = parse_ecmwf_filename(p)
            if info and info.is_surface:
                return p
        return None

    def test_pressure_decode_covered_point(self):
        """Decode pressure levels for a point inside the Europe grid."""
        from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point

        a2 = self._find_a2_file()
        if a2 is None:
            pytest.skip("No a2 (pressure-level) file found")

        # Munich — well inside the main Europe grid
        data, covered = decode_ecmwf_pressure_per_point(a2, [48.0], [11.5])
        assert covered[0], "Munich should be covered by the Europe grid"
        assert len(data[0]) == 25, f"Expected 25 pressure levels, got {len(data[0])}"

        # Check expected fields at 700 hPa
        fields_700 = data[0].get(700, {})
        assert "cloud_liquid_water_kg_kg" in fields_700
        assert "ice_mixing_ratio_kg_kg" in fields_700
        assert "cloud_area_fraction_pct" in fields_700
        # Full sounding fields (Phase 3)
        assert "raw_temperature_k" in fields_700
        assert "raw_relative_humidity_pct" in fields_700 or "raw_specific_humidity_kg_kg" in fields_700
        assert "raw_u_wind_m_s" in fields_700
        assert "raw_v_wind_m_s" in fields_700
        # Note: geopotential (z/gh) may not be in all ECMWF orders

    def test_pressure_decode_uncovered_point(self):
        """A point outside all grids returns no data."""
        from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point

        a2 = self._find_a2_file()
        if a2 is None:
            pytest.skip("No a2 file found")

        # Mid-Atlantic — outside all European grids
        data, covered = decode_ecmwf_pressure_per_point(a2, [40.0], [-30.0])
        assert not covered[0], "Mid-Atlantic should not be covered"
        assert len(data[0]) == 0

    def test_pressure_decode_nordic_point(self):
        """A point in the Nordic grid (60-71N) returns data."""
        from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point

        a2 = self._find_a2_file()
        if a2 is None:
            pytest.skip("No a2 file found")

        # Oslo — in the Nordic extension grid
        data, covered = decode_ecmwf_pressure_per_point(a2, [59.9], [10.7])
        assert covered[0], "Oslo should be covered by the Nordic grid"
        assert len(data[0]) > 0

    def test_pressure_decode_cc_scaled(self):
        """Cloud cover fraction (0-1) is scaled to percentage (0-100)."""
        from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point

        a2 = self._find_a2_file()
        if a2 is None:
            pytest.skip("No a2 file found")

        data, _ = decode_ecmwf_pressure_per_point(a2, [48.0], [11.5])
        for p_hpa, fields in data[0].items():
            cc = fields.get("cloud_area_fraction_pct")
            if cc is not None:
                assert 0 <= cc <= 100, (
                    f"CC at {p_hpa} hPa = {cc}, expected 0-100%"
                )

    def test_surface_decode(self):
        """Decode surface fields and build cloud diagnostics."""
        from weatherbrief.fetch.grib.decode import (
            build_ecmwf_cloud_diagnostics,
            decode_ecmwf_surface_per_point,
        )

        a1 = self._find_a1_file()
        if a1 is None:
            pytest.skip("No a1 (surface) file found")

        sfc_data, covered = decode_ecmwf_surface_per_point(a1, [48.0], [11.5])
        assert covered[0], "Munich should be covered"
        raw = sfc_data[0]
        # Should have at least some cloud cover fields
        assert any(k.endswith("_frac") for k in raw), f"Expected cover fields, got {raw.keys()}"

        diag = build_ecmwf_cloud_diagnostics(raw)
        assert diag is not None
        # Cover values should be 0-100% (converted from 0-1 fractions)
        if diag.total_cover_pct is not None:
            assert 0 <= diag.total_cover_pct <= 100

    def test_mixed_coverage(self):
        """Two points: one covered, one not — verify independent coverage."""
        from weatherbrief.fetch.grib.decode import decode_ecmwf_pressure_per_point

        a2 = self._find_a2_file()
        if a2 is None:
            pytest.skip("No a2 file found")

        lats = [48.0, 40.0]   # Munich (in), mid-Atlantic (out)
        lons = [11.5, -30.0]
        data, covered = decode_ecmwf_pressure_per_point(a2, lats, lons)
        assert covered[0] and not covered[1]
        assert len(data[0]) == 25
        assert len(data[1]) == 0

    def test_build_pressure_levels_from_sample(self):
        """Build complete PressureLevelData objects from a real a2 file."""
        from weatherbrief.fetch.grib.decode import (
            build_pressure_levels_from_grib,
            decode_ecmwf_pressure_per_point,
        )

        a2 = self._find_a2_file()
        if a2 is None:
            pytest.skip("No a2 file found")

        data, covered = decode_ecmwf_pressure_per_point(a2, [48.0], [11.5])
        assert covered[0]

        levels = build_pressure_levels_from_grib(data[0])
        assert len(levels) == 25

        # Verify all key fields are populated on a mid-level
        mid = next((pl for pl in levels if pl.pressure_hpa == 700), None)
        assert mid is not None
        assert mid.temperature_c is not None
        assert mid.wind_speed_kt is not None
        assert mid.cloud_liquid_water_kg_kg is not None

        # Sorted descending
        pressures = [pl.pressure_hpa for pl in levels]
        assert pressures == sorted(pressures, reverse=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _grid_spacing(coords) -> float:
    """Compute the grid spacing from a coordinate array."""
    if len(coords) < 2:
        return 0.0
    diffs = np.diff(np.sort(coords))
    return float(np.median(np.abs(diffs)))
