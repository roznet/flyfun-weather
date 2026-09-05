"""Hand-checked north-increasing ground cells and corrected-footprint sampling."""
from datetime import datetime, timezone
import importlib

import numpy as np
import pytest

from weatherbrief.observed.frames import GridFrame
from weatherbrief.observed.grid import GridSpec, GridWindow

CRS = "+proj=aeqd +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs"


def geometry():
    try:
        return importlib.import_module("weatherbrief.observed.motion.geometry")
    except ModuleNotFoundError:
        pytest.fail("common ground geometry is absent")


def grid3():
    return geometry().AnalysisGrid(CRS, (0., 0.), 0., 0., 3, 3, 2000.)


def test_unknown_hole_is_not_filled():
    from shapely.geometry import Point
    shape = geometry().footprint(np.array([[1,1,1],[1,0,1],[1,1,1]], dtype=bool), grid3())
    assert shape.area == 32_000_000
    assert not shape.covers(Point(3000, 3000))
    record = geometry().display_geometry(shape, grid3())
    assert record.status == "available"
    assert len(record.geometry.coordinates[0]) == 2


def test_radar_signed_source_rows_and_three_states():
    g = grid3()
    source = GridSpec(CRS, 3, 3, 1000., 5000., 2000., -2000.)
    values = np.array([[65., 35., 5.], [10., np.nan, np.nan], [4., 20., 30.]])
    nodata = np.zeros((3,3), dtype=bool); nodata[1,1] = True
    undetect = np.zeros((3,3), dtype=bool); undetect[1,2] = True
    frame = GridFrame("opera_dbzh", "DBZH", "dBZ", datetime(2026,9,5,tzinfo=timezone.utc), 10,
                      source, GridWindow(0,3,0,3), values, nodata, undetect)
    result = geometry().sample_radar(frame, g)
    assert result.values[2,0] == 65
    assert result.descriptor[2,0] == 1
    assert result.descriptor[2,1] == .5
    assert not result.known[1,1]
    assert result.known[1,2] and not result.detected[1,2]
    assert np.isnan(result.values[1,2])
    assert not result.detected[0,0]


def test_quadrilateral_not_bounding_rectangle_and_own_winner():
    g = grid3()
    out = geometry().GroundSamples.empty(g)
    # Diamond bounds include (1000,1000), but its polygon does not.
    diamond = np.array([[3000,0], [6000,3000], [3000,6000], [0,3000]])
    geometry().sample_quadrilateral(out, g, diamond, value=6000, temperature_k=240,
                                    sample_id=17, quality=2)
    assert not out.known[0,0]
    assert out.values[1,1] == 6000
    geometry().sample_quadrilateral(out, g, diamond, value=9000, temperature_k=220,
                                    sample_id=21, quality=8)
    assert out.values[1,1] == 9000
    assert out.temperature_k[1,1] == 220
    assert out.sample_ids[1,1] == 21
    assert out.quality[1,1] == 8
    geometry().sample_quadrilateral(out, g, diamond, value=None, temperature_k=None,
                                    sample_id=25, quality=0, clear=True)
    assert out.values[1,1] == 9000
    assert out.descriptor[1,1] == 1


def test_grid_padding_and_refusal_do_not_clip_route():
    g = geometry().build_analysis_grid([(0., 50.), (1., 50.)], history_span_seconds=600)
    x, y = g.project([0., 1.], [50., 50.])
    assert min(x)-g.origin_x_m >= 157040  # 20 NM +54km +36km +30km
    assert min(y)-g.origin_y_m >= 157040
    with pytest.raises(ValueError, match="region_too_large"):
        geometry().build_analysis_grid([(-10., 50.), (30., 50.)], history_span_seconds=2700)


def test_production_registration_rejects_synthetic_evidence():
    try:
        module = importlib.import_module("weatherbrief.observed.motion.validation")
    except ModuleNotFoundError:
        pytest.fail("registration gate is absent")
    evidence = module.RegistrationEvidence("synthetic-check", "v1", "product", "grid", "decoder", "domain", True)
    record = module.registration_for("eumetsat_ctth", "product", "grid", "decoder", "domain", evidence=evidence)
    assert record.status == "unverified"
    assert record.evidence_id is None


def test_ctth_centre_parallax_moves_corners_before_sampling():
    g = grid3()
    source = GridSpec(CRS, 1,1,1000.,1000.,2000.,2000.)
    frame = GridFrame("eumetsat_ctth","cloud_top_height","m",datetime(2026,9,5,tzinfo=timezone.utc),0,
                      source,GridWindow(0,1,0,1),np.array([[6000.]]),np.array([[False]]),np.array([[False]]),
                      aux={"delta_longitude":np.array([[.0179663]]),"delta_latitude":np.array([[0.]]),
                           "cloud_top_temperature":np.array([[230.]]),"quality_method":np.array([[3]])})
    out=geometry().GroundSamples.empty(g)
    geometry().sample_ctth_block(frame,g,out)
    assert not out.known[0,0]
    assert out.values[0,1] == 6000
    assert out.temperature_k[0,1] == 230


def test_ctth_stream_opens_once_and_bounds_every_block(ctth_path, monkeypatch):
    import netCDF4
    from weatherbrief.observed import ctth
    g = geometry().build_analysis_grid([(1.,50.),(2.,51.)])
    opens=[]; windows=[]
    original=netCDF4.Dataset
    decoder=ctth.read_dataset_window
    def counted(*args,**kwargs):
        opens.append(args[0]); return original(*args,**kwargs)
    def decoded(ds,window,**kwargs):
        windows.append(window); return decoder(ds,window,**kwargs)
    monkeypatch.setattr(netCDF4,"Dataset",counted)
    monkeypatch.setattr(ctth,"read_dataset_window",decoded)
    geometry().decode_ctth(ctth_path,g,GridWindow(0,120,0,80,full_width=True))
    assert len(opens)==1
    assert len(windows)==3
    assert all(w.shape[0]<=46 and w.size<=262144 and w.shape[1]==80 for w in windows)


def test_display_component_limit_keeps_analytical_positive_evidence():
    from shapely.geometry import box
    from shapely.ops import unary_union
    shape=unary_union([box(i*4000,0,i*4000+2000,2000) for i in range(9)])
    record=geometry().display_geometry(shape,grid3())
    assert record.status=="unavailable" and record.geometry is None
    assert record.reason_codes==["geometry_limit"]
    assert shape.area==36_000_000


def test_radar_source_window_limit_precedes_pixel_decode():
    source=GridSpec(CRS,4000,4000,1000.,1000.,2000.,2000.)
    g=geometry().AnalysisGrid(CRS,(0.,0.),0.,0.,2050,2,2000.)
    with pytest.raises(ValueError,match="source_window_limit"):
        geometry().radar_window(source,g)


def test_lower_cloud_is_matching_background_not_zero_height():
    out=geometry().GroundSamples.empty(grid3())
    geometry().sample_quadrilateral(out,grid3(),[[0,0],[2000,0],[2000,2000],[0,2000]],
                                    value=2000,temperature_k=280,sample_id=1,quality=2)
    assert out.known[0,0] and not out.detected[0,0]
    assert out.descriptor[0,0]==0 and out.values[0,0]==2000
    assert not out.known[0,1] and np.isnan(out.values[0,1])


def test_radar_spacing_is_rejected_before_source_read(tmp_path,dbzh_path,monkeypatch):
    import h5py
    from tests.observed.test_motion_history import radar_store,NOW
    from weatherbrief.observed import opera
    from weatherbrief.observed.motion.history import load_history
    store=radar_store(tmp_path,dbzh_path)
    path=store.payload_path("opera_dbzh",NOW)
    with h5py.File(path,"r+") as f:
        f["where"].attrs["xscale"]=1000.
    store.write_sidecar("opera_dbzh",NOW,{**opera.read_metadata(path,"DBZH"),"received_at":NOW.isoformat()})
    def forbidden(*args,**kwargs):
        raise AssertionError("unsupported spacing reached pixel decoding")
    monkeypatch.setattr(opera,"read_window",forbidden)
    result=load_history(store,[(1.,50.),(2.,51.)],NOW)
    assert not result.frames_by_source["opera_dbzh"]
    assert "unsupported_grid_spacing" in result.reason_codes


def test_domain_dateline_crossing_is_refused():
    with pytest.raises(ValueError,match="region_too_large"):
        geometry().build_analysis_grid([(179.,0.),(-179.,0.)])


def test_rate_context_is_not_filtered_by_dbzh_contour_threshold():
    source=GridSpec(CRS,1,1,1000.,1000.,2000.,2000.)
    frame=GridFrame("opera_rate","RATE","mm/h",datetime(2026,9,5,tzinfo=timezone.utc),15,
                    source,GridWindow(0,1,0,1),np.array([[3.]]),np.array([[False]]),np.array([[False]]))
    result=geometry().sample_radar(frame,grid3())
    assert result.detected[0,0]
    assert result.values[0,0]==3
    assert result.descriptor[0,0]==0
