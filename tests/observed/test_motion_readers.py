"""Motion-specific provenance does not change existing observation decoding."""
from datetime import datetime, timezone
import shutil

import netCDF4
import numpy as np
import pytest

from weatherbrief.observed import ctth, lightning, opera
from weatherbrief.observed.grid import GridWindow


def test_opera_exposes_actual_acquisition_without_clamping(dbzh_path, tmp_path):
    import h5py
    path = tmp_path / "radar.h5"
    shutil.copyfile(dbzh_path, path)
    with h5py.File(path, "r+") as f:
        f["dataset1/what"].attrs["starttime"] = "150000"
    meta = opera.read_metadata(path, "DBZH")
    assert meta.get("acquisition_start") == "2026-08-25T15:00:00+00:00"
    assert meta.get("acquisition_end") == "2026-08-25T14:05:00+00:00"


def test_ctth_missing_start_is_not_invented(ctth_path):
    meta = ctth.read_metadata(ctth_path)
    assert "acquisition_start" in meta and meta["acquisition_start"] is None
    assert meta["acquisition_end"] == meta["valid_time"]


def test_ctth_open_dataset_decoder_matches_existing_reader(ctth_path):
    block = GridWindow(0, 2, 0, 4)
    with netCDF4.Dataset(ctth_path) as ds:
        actual = getattr(ctth, "read_dataset_window", None)
        assert actual is not None, "bounded open-dataset decoding is absent"
        frame = actual(ds, block, source="eumetsat_ctth", path=ctth_path)
    old = ctth.read_window(ctth_path, block, source="eumetsat_ctth")
    np.testing.assert_equal(frame.values, old.values)
    np.testing.assert_equal(frame.nodata, old.nodata)


@pytest.mark.parametrize("mode,reason", [("missing", "window_only_time"), ("masked", "invalid_flash_time"), ("outside", "out_of_window_time"), ("mismatch", "time_array_mismatch")])
def test_lightning_fallback_has_explicit_precision(tmp_path, mode, reason):
    path = tmp_path / "flashes.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.sensing_start_time = "2026-09-05T11:50:00Z"
        ds.sensing_end_time = "2026-09-05T12:00:00Z"
        ds.createDimension("flash", 2)
        ds.createVariable("latitude", "f8", ("flash",))[:] = [50, 51]
        ds.createVariable("longitude", "f8", ("flash",))[:] = [1, 2]
        if mode != "missing":
            ds.createDimension("time_count", 1 if mode == "mismatch" else 2)
            var = ds.createVariable("flash_time", "f8", ("time_count",), fill_value=-999)
            var.units = "seconds since 2026-09-05 11:50:00"
            var[:] = [100] if mode == "mismatch" else ([100, -999] if mode == "masked" else [100, 700])
    frame = lightning.read_flashes(path, source="eumetsat_li", window_minutes=10)
    precision = getattr(frame, "time_precision", None)
    assert precision is not None, "fallback currently manufactures event precision"
    assert precision[-1] == "window_only"
    assert reason in frame.time_reason_codes[-1]
    assert frame.event_times[-1] is None
    assert len(frame.lats) == 2
    if mode in ("masked", "outside"):
        assert precision[0] == "individual_time"
        assert frame.event_times[0] == datetime(2026, 9, 5, 11, 51, 40, tzinfo=timezone.utc)


def test_lightning_filtered_positions_keep_original_sample_identity(tmp_path):
    path=tmp_path/"positions.nc"
    with netCDF4.Dataset(path,"w") as ds:
        ds.sensing_start_time="2026-09-05T11:50:00Z";ds.sensing_end_time="2026-09-05T12:00:00Z"
        ds.createDimension("flash",3)
        ds.createVariable("latitude","f8",("flash",),fill_value=-999)[:]=[-999,50,51]
        ds.createVariable("longitude","f8",("flash",))[:]=[1,2,3]
    frame=lightning.read_flashes(path,source="eumetsat_li",window_minutes=10)
    assert list(frame.lats)==[50,51]
    assert list(getattr(frame,"sample_ids",[]))==[1,2]
