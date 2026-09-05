"""FCI Table 10 flags must not turn a failed retrieval into clear sky."""

import shutil

import netCDF4
import numpy as np
import pytest

from weatherbrief.observed import ctth
from weatherbrief.observed.grid import GridWindow


@pytest.mark.parametrize(
    "method,status,overall,height,want",
    [
        (0, 0, 0, np.nan, "nodata"),
        (0, None, None, np.nan, "nodata"),
        (0, -128, None, np.nan, "nodata"),
        (0, 1, 0, np.nan, "undetect"),
        (0, 1, None, np.nan, "undetect"),  # legacy granule without overall flag
        (0, 1, 1, np.nan, "nodata"),  # clear requires overall not processed
        (0, 1, 2, np.nan, "nodata"),
        (0, 1, -128, np.nan, "nodata"),  # masked overall is unknown, not clear
        (0, 1, 0, 1000.0, "nodata"),  # contradictory clear/height
        (0, 3, 2, 1000.0, "nodata"),
        (10, 3, 2, 1000.0, "nodata"),
        (-128, 3, 2, 1000.0, "nodata"),
        (11, 3, 2, 1000.0, "nodata"),
        (9, 2, 2, 1000.0, "nodata"),
        (9, 0, 2, 1000.0, "nodata"),
        (9, -128, 2, 1000.0, "nodata"),
        (9, 5, 2, 1000.0, "nodata"),  # dust is not a cloud top
        (9, 4, 2, 1000.0, "nodata"),
        (9, 6, 2, 1000.0, "nodata"),
        (9, 7, 2, 1000.0, "nodata"),  # nor successful ash retrieval
        (9, 8, 2, 1000.0, "nodata"),  # unknown status
        (9, 3, 0, 1000.0, "nodata"),
        (9, 3, -128, 1000.0, "nodata"),
        (9, 3, 1, 1000.0, "detected"),  # poor != unprocessed
        (9, 3, 2, 1000.0, "detected"),
        (6, None, None, 1000.0, "detected"),  # legacy positive evidence
        (9, 3, 2, np.inf, "nodata"),
    ],
)
def test_ctth_classification_uses_retrieval_status(
    ctth_path, tmp_path, method, status, overall, height, want
):
    """Ignoring the independent status/method gate misclassifies these pixels."""
    path = tmp_path / "quality.nc"
    shutil.copyfile(ctth_path, path)
    with netCDF4.Dataset(path, "a") as ds:
        # Make absence explicit even after the committed fixture gains flags.
        for name in ("quality_status", "quality_overall_processing"):
            if name in ds.variables:
                ds.renameVariable(name, "unused_" + name)
        for name, value in (
            ("quality_status", status),
            ("quality_overall_processing", overall),
        ):
            if value is not None:
                var = ds.createVariable(name, "i1", ("y", "x"), fill_value=-128)
                var[:] = value
        ds["quality_method"][0, 0] = method
        ds["cloud_top_height"][0, 0] = height
        ds["delta_latitude"][0, 0] = 0
        ds["delta_longitude"][0, 0] = 0
    frame = ctth.read_window(path, GridWindow(0, 1, 0, 1), source="eumetsat_ctth")
    assert bool(getattr(frame, want)[0, 0])
    assert sum(bool(getattr(frame, state)[0, 0]) for state in ("nodata", "undetect", "detected")) == 1


def test_ctth_accepts_enum_status_and_documented_spelling(ctth_path, tmp_path):
    """Real netCDF categorical enums, including the guide's spelling, decode."""
    path = tmp_path / "enum.nc"
    shutil.copyfile(ctth_path, path)
    with netCDF4.Dataset(path, "a") as ds:
        if "quality_status" in ds.variables:
            ds.renameVariable("quality_status", "unused_status")
        enum = ds.createEnumType(np.uint8, "status_enum", {"bad": 0, "clear": 1, "cloudy": 3, "missing": 255})
        var = ds.createVariable("qualiy_status", enum, ("y", "x"), fill_value=255)
        var[:] = 0
        var[0, 1] = 1
        ds["quality_method"][0, :2] = 0
        ds["cloud_top_height"][0, :2] = np.nan
    frame = ctth.read_window(path, GridWindow(0, 1, 0, 2), source="eumetsat_ctth")
    assert frame.nodata[0, 0]
    assert frame.undetect[0, 1]


def test_effective_cloudiness_preserves_netcdf_packing_without_guessed_rescaling(ctth_path, tmp_path):
    """A second percent conversion would silently change decoded values."""
    path = tmp_path / "cloudiness.nc"
    shutil.copyfile(ctth_path, path)
    with netCDF4.Dataset(path, "a") as ds:
        ds.renameVariable("effective_cloudiness", "unused_cloudiness")
        var = ds.createVariable("effective_cloudiness", "i2", ("y", "x"), fill_value=-32768)
        var.scale_factor = 0.01
        var.units = "%"  # Table 9 alone is not evidence for another /100.
        var.set_auto_maskandscale(False)
        var[0:1, 0:4] = np.array([[35, 98, -32768, 3500]], dtype=np.int16)
    frame = ctth.read_window(path, GridWindow(0, 1, 0, 4), source="eumetsat_ctth")
    values = frame.aux["effective_cloudiness"][0]
    assert values[:2] == pytest.approx([0.35, 0.98])
    assert np.isnan(values[2])
    assert values[3] == 35  # Different scale is unresolved, not silently /100.
