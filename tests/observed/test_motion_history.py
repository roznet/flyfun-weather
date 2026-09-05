"""Retained local inventories must not turn later/corrupt inputs into history."""
from datetime import datetime, timedelta, timezone

import pytest
import importlib
import shutil

from weatherbrief.observed.frames import FrameStore

NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)


def put(store, minute, **overrides):
    when = NOW + timedelta(minutes=minute)
    meta = dict(valid_time=when.isoformat(), motion_valid_time=when.isoformat(), received_at=when.isoformat(),
                acquisition_start=(when-timedelta(minutes=10)).isoformat(),
                acquisition_end=when.isoformat(), quantity="DBZH",
                product_id="OPERA:DBZH", decoder_version="odim_v1",
                grid=dict(proj4="+proj=laea +lat_0=50 +lon_0=10 +datum=WGS84 +units=m", nx=3, ny=3, x0=1000., y0=5000., dx=2000., dy=-2000.))
    meta.update(overrides)
    return store.write("opera_dbzh", when, b"synthetic retained payload", meta)


def test_sidecar_preserves_decoded_time_separate_from_slot(tmp_path):
    store = FrameStore(tmp_path)
    frame = put(store, 0, valid_time=(NOW+timedelta(seconds=32)).isoformat())
    assert frame.meta["valid_time"] == "2026-09-05T12:00:32+00:00"


def test_asof_excludes_future_receipt_and_retains_corrupt_barrier(tmp_path):
    store = FrameStore(tmp_path)
    for minute in (-15, -10, -5, 0):
        put(store, minute, **({"received_at": (NOW+timedelta(minutes=1)).isoformat()} if minute == 0 else {}))
    store.sidecar_path("opera_dbzh", NOW-timedelta(minutes=10)).write_text("{broken")
    result = store.as_of_inventory("opera_dbzh", NOW)
    assert [item.valid_time for item in result] == [NOW-timedelta(minutes=n) for n in (5, 10, 15)]
    assert result[1].reason_codes == ("unreadable_frame",)
    assert result[1].stored is None


def test_naive_cutoff_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="aware"):
        FrameStore(tmp_path).as_of_inventory("opera_dbzh", NOW.replace(tzinfo=None))


def selector():
    try:
        return importlib.import_module("weatherbrief.observed.motion.history").select_history
    except ModuleNotFoundError:
        pytest.fail("cutoff-safe history selection is absent")


def radar_store(tmp_path, dbzh_path, minutes=(-10, -5, 0)):
    import h5py
    from weatherbrief.observed import opera
    store = FrameStore(tmp_path)
    for minute in minutes:
        when = NOW+timedelta(minutes=minute)
        path = store.payload_path("opera_dbzh", when)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(dbzh_path, path)
        with h5py.File(path, "r+") as f:
            f["what"].attrs["date"] = when.strftime("%Y%m%d")
            f["what"].attrs["time"] = when.strftime("%H%M%S")
            for prefix, at in (("start", when-timedelta(minutes=10)), ("end", when-timedelta(minutes=1))):
                f["dataset1/what"].attrs[prefix+"date"] = at.strftime("%Y%m%d")
                f["dataset1/what"].attrs[prefix+"time"] = at.strftime("%H%M%S")
        store.write_sidecar("opera_dbzh", when, {**opera.read_metadata(path, "DBZH"), "received_at": when.isoformat()})
    return store


def test_history_is_pinned_and_replacement_is_detected(tmp_path, dbzh_path):
    selected = selector()(radar_store(tmp_path, dbzh_path), "opera_dbzh", NOW)
    assert len(selected.frames) == 3
    assert selected.reason_codes == ()
    latest = selected.frames[-1]
    assert latest.reference_at == NOW
    assert latest.record.valid_at == NOW
    assert latest.record.acquisition_window.end_at == NOW-timedelta(minutes=1)
    assert latest.received_at == NOW
    assert latest.recheck()
    latest.stored.path.write_bytes(b"replaced")
    assert not latest.recheck()


def test_history_stops_at_corrupt_middle(tmp_path, dbzh_path):
    store = radar_store(tmp_path, dbzh_path)
    store.sidecar_path("opera_dbzh", NOW-timedelta(minutes=5)).write_text("{corrupt")
    selected = selector()(store, "opera_dbzh", NOW)
    assert len(selected.frames) == 1
    assert {"unreadable_frame", "insufficient_history"} <= set(selected.reason_codes)
    assert selected.inventory_count == 3 and selected.inspected_count == 1
    assert not selected.selection_complete


def test_missing_publication_gap_is_disclosed(tmp_path, dbzh_path):
    selected = selector()(radar_store(tmp_path, dbzh_path, (-15, -10, 0)), "opera_dbzh", NOW)
    assert len(selected.frames) == 3
    assert selected.gaps[-1].missing_nominal_publications == 1
    assert selected.gaps[-1].elapsed_seconds == 600


def test_real_acquisition_after_cutoff_is_not_admitted_by_slot(tmp_path, dbzh_path):
    import h5py
    store = radar_store(tmp_path, dbzh_path)
    with h5py.File(store.payload_path("opera_dbzh", NOW), "r+") as f:
        f["dataset1/what"].attrs["endtime"] = "120100"
    selected = selector()(store, "opera_dbzh", NOW)
    assert not selected.frames
    assert "future_acquisition" in selected.reason_codes


def test_load_history_returns_bounded_primary_frames_and_source_reasons(tmp_path, dbzh_path):
    store=radar_store(tmp_path,dbzh_path)
    module=importlib.import_module("weatherbrief.observed.motion.history")
    loader=getattr(module,"load_history",None)
    assert loader is not None,"source history decoding is absent"
    result=loader(store,[(1.,50.),(2.,51.)],NOW)
    assert len(result.frames_by_source["opera_dbzh"])==3
    frame=result.frames_by_source["opera_dbzh"][-1]
    assert frame.reference_at==NOW
    assert frame.known.shape==result.grid.shape
    assert frame.geolocation.status=="validated"
    assert {s.source_id for s in result.sources}=={"opera_dbzh","opera_rate","eumetsat_ctth","eumetsat_li"}
    assert not result.rate_frames and not result.lightning_frames


def test_load_history_deadline_is_explicit_not_no_detections(tmp_path):
    module=importlib.import_module("weatherbrief.observed.motion.history")
    loader=getattr(module,"load_history",None)
    assert loader is not None,"source history decoding is absent"
    result=loader(FrameStore(tmp_path),[(1.,50.),(2.,51.)],NOW,deadline=0)
    assert "compute_deadline" in result.reason_codes
    assert not result.frames_by_source


def test_future_receipt_cannot_supply_third_history_frame(tmp_path,dbzh_path):
    import json
    store=radar_store(tmp_path,dbzh_path)
    sidecar=store.sidecar_path("opera_dbzh",NOW)
    meta=json.loads(sidecar.read_text()); meta["received_at"]=(NOW+timedelta(seconds=1)).isoformat()
    sidecar.write_text(json.dumps(meta))
    selected=selector()(store,"opera_dbzh",NOW)
    assert len(selected.frames)==2
    assert "insufficient_history" in selected.reason_codes
    assert all(f.received_at<=NOW for f in selected.frames)


@pytest.mark.parametrize("field,value,reason",[("starttime","130000","invalid_time"),("starttime","garbled","missing_acquisition")])
def test_bad_real_window_is_not_reconstructed_from_sidecar(tmp_path,dbzh_path,field,value,reason):
    import h5py
    store=radar_store(tmp_path,dbzh_path)
    with h5py.File(store.payload_path("opera_dbzh",NOW),"r+") as f:
        f["dataset1/what"].attrs[field]=value
    selected=selector()(store,"opera_dbzh",NOW)
    assert not selected.frames
    assert reason in selected.reason_codes


def test_context_cap_applies_after_observed_history_window_filter(tmp_path):
    import netCDF4
    from weatherbrief.observed import lightning
    store=FrameStore(tmp_path)
    for minute in (-60,-50,-40,-30,-20,-10,0):
        end=NOW+timedelta(minutes=minute)
        path=store.payload_path("eumetsat_li",end);path.parent.mkdir(parents=True,exist_ok=True)
        with netCDF4.Dataset(path,"w") as ds:
            ds.sensing_start_time=(end-timedelta(minutes=10)).isoformat()
            ds.sensing_end_time=end.isoformat()
            ds.createDimension("flash",1)
            ds.createVariable("latitude","f8",("flash",))[:]=[50]
            ds.createVariable("longitude","f8",("flash",))[:]=[1]
        store.write_sidecar("eumetsat_li",end,{**lightning.read_metadata(path),"received_at":end.isoformat()})
    selected=selector()(store,"eumetsat_li",NOW,observed_intervals=((NOW-timedelta(minutes=55),NOW-timedelta(minutes=45)),))
    assert [f.reference_at for f in selected.frames]==[NOW-timedelta(minutes=50),NOW-timedelta(minutes=40)]


def test_sidecar_replacement_during_metadata_read_cannot_pass_pin(tmp_path,dbzh_path,monkeypatch):
    import json
    from weatherbrief.observed import opera
    store=radar_store(tmp_path,dbzh_path)
    original=opera.read_metadata
    def replacing(path,quantity):
        sidecar=path.with_suffix(".json")
        data=json.loads(sidecar.read_text()); data["received_at"]=(NOW+timedelta(minutes=1)).isoformat()
        sidecar.write_text(json.dumps(data))
        return original(path,quantity)
    monkeypatch.setattr(opera,"read_metadata",replacing)
    selected=selector()(store,"opera_dbzh",NOW)
    assert not selected.frames and "frame_changed" in selected.reason_codes


def test_context_bad_newest_frame_is_not_skipped_as_missing_publication(tmp_path):
    store=FrameStore(tmp_path)
    when=NOW-timedelta(minutes=10)
    store.write("eumetsat_li",when,b"broken",{"valid_time":when.isoformat(),"received_at":when.isoformat()})
    selected=selector()(store,"eumetsat_li",NOW)
    assert not selected.frames and "unreadable_frame" in selected.reason_codes


def test_receipt_replacement_between_inventory_and_pin_is_rejected(tmp_path,dbzh_path,monkeypatch):
    import json
    store=radar_store(tmp_path,dbzh_path)
    inventory=store.as_of_inventory
    def changed(source,cutoff):
        entries=inventory(source,cutoff)
        sidecar=entries[0].stored.path.with_suffix(".json")
        meta=json.loads(sidecar.read_text());meta["received_at"]=(NOW+timedelta(seconds=1)).isoformat()
        sidecar.write_text(json.dumps(meta))
        return entries
    monkeypatch.setattr(store,"as_of_inventory",changed)
    selected=selector()(store,"opera_dbzh",NOW)
    assert not selected.frames
    assert "frame_changed" in selected.reason_codes


def test_loader_reports_known_inventory_omissions_without_inventing_unknown_counts(tmp_path,dbzh_path):
    from weatherbrief.observed.motion.history import load_history
    store=radar_store(tmp_path,dbzh_path,(-25,-20,-15,-10,-5,0))
    result=load_history(store,[(1.,50.),(2.,51.)],NOW)
    counts=getattr(result,"input_counts",())
    assert counts,"input selection omissions are not exposed"
    radar=next(c for c in counts if c.source_id=="opera_dbzh")
    assert radar.considered_count==6 and radar.emitted_count==4 and radar.omitted_count==2
    assert radar.selected_count==4 and radar.inspected_count==4
    aborted=load_history(store,[(1.,50.),(2.,51.)],NOW,deadline=0)
    assert all(c.considered_count is None and c.omitted_count is None for c in aborted.input_counts)
    assert len(aborted.input_counts)==4


def test_sidecar_source_identity_cannot_disagree_with_inventory(tmp_path,dbzh_path):
    import json
    store=radar_store(tmp_path,dbzh_path)
    sidecar=store.sidecar_path("opera_dbzh",NOW)
    meta=json.loads(sidecar.read_text());meta["source"]="opera_rate"
    sidecar.write_text(json.dumps(meta))
    selected=selector()(store,"opera_dbzh",NOW)
    assert not selected.frames and "frame_changed" in selected.reason_codes


def test_asof_uses_nominal_target_not_acquisition_end(tmp_path):
    store = FrameStore(tmp_path)
    put(store, -5, motion_valid_time=(NOW+timedelta(minutes=1)).isoformat())
    assert store.as_of_inventory("opera_dbzh", NOW) == []


def test_nominal_target_before_acquisition_end_stays_reference(tmp_path, dbzh_path):
    import h5py
    from weatherbrief.observed import opera
    store = radar_store(tmp_path, dbzh_path, (-5,))
    target = NOW-timedelta(minutes=5)
    path = store.payload_path("opera_dbzh", target)
    with h5py.File(path, "r+") as f:
        f["dataset1/what"].attrs["endtime"] = "120000"
    store.write_sidecar("opera_dbzh", target, {**opera.read_metadata(path, "DBZH"), "received_at": NOW.isoformat()})
    selected = selector()(store, "opera_dbzh", NOW)
    assert selected.frames[0].reference_at == target
    assert selected.frames[0].record.acquisition_window.end_at == NOW


def test_missing_nominal_target_is_not_inferred_from_acquisition(tmp_path, dbzh_path):
    import h5py
    from weatherbrief.observed import opera
    store = radar_store(tmp_path, dbzh_path, (0,))
    path = store.payload_path("opera_dbzh", NOW)
    with h5py.File(path, "r+") as f:
        del f["what"].attrs["time"]
    store.write_sidecar("opera_dbzh", NOW, {**opera.read_metadata(path, "DBZH"), "received_at": NOW.isoformat()})
    selected = selector()(store, "opera_dbzh", NOW)
    assert not selected.frames and "invalid_time" in selected.reason_codes


def test_changed_nominal_target_cannot_bypass_pinned_inventory(tmp_path, dbzh_path):
    import h5py
    store = radar_store(tmp_path, dbzh_path, (0,))
    with h5py.File(store.payload_path("opera_dbzh", NOW), "r+") as f:
        f["what"].attrs["time"] = "115500"
    selected = selector()(store, "opera_dbzh", NOW)
    assert not selected.frames and "frame_changed" in selected.reason_codes
