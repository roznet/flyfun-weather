"""Interactive review map: one frame's radar and cells over a real basemap.

A self-contained HTML page (Leaflet from cdnjs, data inline) for checking the
analysis against another viewer such as Windy.  Layers, each toggleable:

* radar reflectivity in the shared intensity ramp, nodata as a grey wash —
  resampled onto **Web Mercator rows**, because a plate-carrée image stretched
  over Europe (35–70°N) lands tens of km off at the northern edge;
* cell outlines per tier (rain20 grey, core35 white, core41 black), traced
  from the detection masks;
* a marker per core, coloured by trend, with a popup (peak, rain rate,
  lightning, age, trend, speed and heading) and its 30-minute motion arrow;
* the lightning flashes of the frame's LI slot.

A development tool, not a product surface.
"""

from __future__ import annotations

import base64
import io
import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from ..imagery import _DBZ_STOPS, DETECTION_ALPHA, FAINT_ALPHA, FAINT_ECHO_DBZ, NODATA_RGBA, _colourise
from .catalogue import catalogue_path, read_catalogue
from .policy import DEFAULT_POLICY, CellPolicy
from .runner import FrameCache, Workspace, _li_frame

MAX_WIDTH = 2400
_TREND_COLOUR = {"developing": "#d7263d", "decaying": "#1b6ca8", "steady": "#7a7a7a",
                 "mixed": "#f18f01", "new": "#ffffff"}
_OUTLINE = {"rain20": ("#8a8a8a", 1), "core35": ("#ffffff", 1.5), "core41": ("#000000", 1.5)}


def _merc_y(lat):
    return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))


def _merc_lat(y):
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def _radar_png(frame, south, west, north, east) -> str:
    """Base64 PNG of the radar, rows evenly spaced in Mercator y."""
    from PIL import Image

    grid = frame.grid
    y0, y1 = _merc_y(north), _merc_y(south)
    aspect = math.radians(east - west) / (y0 - y1)
    width = MAX_WIDTH if aspect >= 1 else max(1, int(MAX_WIDTH * aspect))
    height = max(1, int(round(width / aspect)))
    lats = _merc_lat(y0 + (np.arange(height) + 0.5) * (y1 - y0) / height)
    lons = west + (np.arange(width) + 0.5) * (east - west) / width
    lon_mesh, lat_mesh = np.meshgrid(lons, lats)
    cols, rows = grid.lonlat_to_colrow(lon_mesh, lat_mesh)
    rows = np.rint(np.asarray(rows)).astype(np.int64) - frame.window.row0
    cols = np.rint(np.asarray(cols)).astype(np.int64) - frame.window.col0
    ny, nx = frame.values.shape
    inside = (rows >= 0) & (rows < ny) & (cols >= 0) & (cols < nx)
    r = np.clip(rows, 0, ny - 1)
    c = np.clip(cols, 0, nx - 1)
    values = np.asarray(frame.values)[r, c]
    detected = frame.detected[r, c] & inside
    nodata = (np.asarray(frame.nodata)[r, c] & inside) | ~inside

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    rgb = _colourise(np.nan_to_num(values, nan=-9999.0), _DBZ_STOPS)
    rgba[detected, :3] = rgb[detected]
    rgba[detected, 3] = DETECTION_ALPHA
    faint = detected & (values < FAINT_ECHO_DBZ)
    rgba[faint, 3] = FAINT_ALPHA
    rgba[nodata] = NODATA_RGBA
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _outlines(det, grid, r0, r1, c0, c1, step: int) -> list:
    """Cell boundaries in the crop as [[lat, lon], ...] polylines."""
    import contourpy

    mask = (det.labels[r0:r1, c0:c1] > 0)
    if step > 1:
        h = (mask.shape[0] // step) * step
        w = (mask.shape[1] // step) * step
        mask = mask[:h, :w].reshape(h // step, step, w // step, step).any(axis=(1, 3))
    if not mask.any():
        return []
    padded = np.pad(mask.astype(np.float32), 1)
    lines = contourpy.contour_generator(z=padded).lines(0.5)
    out = []
    for line in lines:
        if len(line) < 4:
            continue
        cc = (line[:, 0] - 1) * step + (step - 1) / 2 + c0 + det.col0
        rr = (line[:, 1] - 1) * step + (step - 1) / 2 + r0 + det.row0
        lons, lats = grid.colrow_to_lonlat(cc, rr)
        out.append([[round(float(a), 3), round(float(b), 3)] for a, b in zip(lats, lons)])
    return out


def render_map(
    ws: Workspace,
    valid_time: datetime,
    out_path: Path,
    *,
    policy: CellPolicy = DEFAULT_POLICY,
    bbox: tuple[float, float, float, float] | None = None,
) -> Path:
    cache = FrameCache(ws.frames)
    frame = cache.dbzh(valid_time)
    if frame is None:
        raise FileNotFoundError(f"no DBZH frame at {valid_time.isoformat()}")
    catalogue = read_catalogue(catalogue_path(ws.root, valid_time))
    if catalogue is None:
        raise FileNotFoundError(f"no catalogue at {valid_time.isoformat()} — run the loop first")
    dets = cache.detections(valid_time, policy)
    grid = frame.grid

    if bbox is None:
        # The radar network's own extent.
        covered = ~np.asarray(frame.nodata)
        rr, cc = np.nonzero(covered[::20, ::20])
        lons, lats = grid.colrow_to_lonlat(cc * 20, rr * 20)
        bbox = (float(np.min(lats)), float(np.min(lons)), float(np.max(lats)), float(np.max(lons)))
    south, west, north, east = bbox

    corners_lon = np.array([west, east, west, east, (west + east) / 2, (west + east) / 2])
    corners_lat = np.array([south, south, north, north, south, north])
    cols, rows = grid.lonlat_to_colrow(corners_lon, corners_lat)
    ny, nx = frame.values.shape
    r0 = max(0, int(np.floor(np.min(rows))) - 2)
    r1 = min(ny, int(np.ceil(np.max(rows))) + 2)
    c0 = max(0, int(np.floor(np.min(cols))) - 2)
    c1 = min(nx, int(np.ceil(np.max(cols))) + 2)
    big = (r1 - r0) * (c1 - c0) > 4_000_000  # whole-Europe view: coarser outlines

    outlines = {
        tier.name: _outlines(dets[tier.name], grid, r0, r1, c0, c1,
                             step=(4 if tier.name == "rain20" else 2) if big else (2 if tier.name == "rain20" else 1))
        for tier in policy.tiers
    }

    def inside(lat, lon):
        return south <= lat <= north and west <= lon <= east

    cells = []
    for cell in catalogue["cells"]:
        if not inside(cell["lat"], cell["lon"]):
            continue
        if cell["tier"] == "rain20" and cell["area_km2"] < 2000:
            continue
        m = cell["motion"]
        arrow = None
        if m["status"] == "available":
            lon2, lat2 = grid.colrow_to_lonlat(cell["col"] + m["dcol_per_min"] * 30,
                                               cell["row"] + m["drow_per_min"] * 30)
            arrow = [round(float(lat2), 4), round(float(lon2), 4)]
        cells.append({
            "id": cell["id"], "tier": cell["tier"], "lat": cell["lat"], "lon": cell["lon"],
            "area": cell["area_km2"], "peak": cell["peak_dbz"], "rate": cell["rate_peak_mm_h"],
            "flashes": cell["flashes"], "top": cell["top_fl"], "truncated": cell["truncated"],
            "age": cell["lineage"]["age_min"], "event": cell["lineage"]["event"],
            "trend": cell["trend"], "motion": {k: m[k] for k in ("status", "reason", "speed_kt", "toward_deg")},
            "arrow": arrow,
        })

    flashes = []
    li, _ = _li_frame(ws.frames, valid_time)
    if li is not None:
        for lat, lon in zip(li.lats, li.lons):
            if inside(lat, lon):
                flashes.append([round(float(lat), 3), round(float(lon), 3)])

    data = {
        "time": valid_time.strftime("%Y-%m-%d %H:%MZ"),
        "policy": catalogue["policy_version"],
        "inputs": catalogue["inputs"],
        "unavailable": catalogue["unavailable"],
        "bounds": [[south, west], [north, east]],
        "radar": _radar_png(frame, south, west, north, east),
        "outlines": outlines,
        "cells": cells,
        "flashes": flashes,
        "trendColour": _TREND_COLOUR,
        "outlineStyle": _OUTLINE,
    }
    html = _TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Observed cells</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
html,body{margin:0;height:100%;font:13px -apple-system,system-ui,sans-serif;background:#eee}
#map{position:absolute;inset:0}
.panel{position:absolute;z-index:1000;top:10px;left:50px;background:#fff;padding:8px 10px;
 border-radius:6px;box-shadow:0 1px 4px rgba(0,0,0,.3);max-width:340px}
.panel b{font-size:14px}.sw{display:inline-block;width:10px;height:10px;border-radius:50%;
 border:1px solid #333;margin:0 3px 0 8px;vertical-align:middle}
.leaflet-popup-content{font-size:12px;line-height:1.45}
</style></head><body><div id="map"></div><div class="panel" id="panel"></div>
<script>
const D=__DATA__;
const map=L.map('map',{preferCanvas:true,zoomControl:true}).fitBounds(D.bounds);
const gray=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
 {maxZoom:16,attribution:'Basemap &copy; Esri'}).addTo(map);
const labels=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
 {maxZoom:16,pane:'shadowPane'}).addTo(map);
const radar=L.imageOverlay('data:image/png;base64,'+D.radar,D.bounds,{opacity:.85}).addTo(map);
const layers={'Radar reflectivity':radar,'Place names':labels};
for(const [tier,lines] of Object.entries(D.outlines)){
  const [col,w]=D.outlineStyle[tier]||['#555',1];
  const lg=L.polyline(lines,{color:col,weight:w,opacity:.95,interactive:false});
  layers['Outline '+tier]=lg; if(tier!=='rain20') lg.addTo(map);
}
const comp=d=>d==null?'–':['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'][Math.round(d/22.5)%16];
const f=(v,u='')=>v==null?'–':v+u;
function popup(c){
  const t=c.trend,m=c.motion;
  let trend=t.state; if(t.window_min) trend+=` over ${t.window_min} min (peak ${t.d_peak_db>0?'+':''}${t.d_peak_db} dB, area ×${f(t.area_ratio)}${t.d_flashes!=null?`, flashes ${t.d_flashes>0?'+':''}${t.d_flashes}`:''})`;
  const mv=m.status==='available'?`moving <b>${comp(m.toward_deg)}</b> (${f(m.toward_deg,'°')}) at <b>${m.speed_kt} kt</b>`:`motion ${m.status}${m.reason?': '+m.reason:''}`;
  return `<b>${c.tier}</b> ${c.truncated?'(truncated by coverage edge)':''}<br>
  peak <b>${c.peak} dBZ</b> · area ${c.area} km²<br>rain rate peak ${f(c.rate,' mm/h')} · lightning <b>${f(c.flashes)}</b>${c.top!=null?` · top FL${c.top}`:''}<br>
  age ${c.age} min (${c.event}) · <b>${trend}</b><br>${mv}<br><span style="color:#888">${c.id}</span>`;
}
const cellLayers={core41:L.layerGroup(),core35:L.layerGroup(),rain20:L.layerGroup()};
const arrows=L.layerGroup();
for(const c of D.cells){
  const r=c.tier==='core41'?7:c.tier==='core35'?5:6;
  const mk=L.circleMarker([c.lat,c.lon],{radius:r,color:'#222',weight:1,fillOpacity:.9,
    fillColor:D.trendColour[c.trend.state]||'#ccc',dashArray:c.tier==='rain20'?'3':null}).bindPopup(popup(c));
  mk.addTo(cellLayers[c.tier]);
  if(c.arrow&&c.tier!=='rain20'){
    L.polyline([[c.lat,c.lon],c.arrow],{color:'#b000b5',weight:2.5}).addTo(arrows);
    L.circleMarker(c.arrow,{radius:2.5,color:'#b000b5',fillOpacity:1}).addTo(arrows);
  }
}
cellLayers.core41.addTo(map);arrows.addTo(map);
layers['Cells ≥41 dBZ']=cellLayers.core41;layers['Cells ≥35 dBZ']=cellLayers.core35;
layers['Rain areas ≥2000 km²']=cellLayers.rain20;layers['Motion (30 min)']=arrows;
const lt=L.layerGroup(D.flashes.map(p=>L.circleMarker(p,{radius:1.6,color:'#e0a800',weight:0,fillColor:'#ffd400',fillOpacity:.9,interactive:false})));
lt.addTo(map);layers['Lightning ('+D.flashes.length+')']=lt;
L.control.layers(null,layers,{collapsed:false}).addTo(map);
const un=D.unavailable.map(u=>u.what).join(', ');
document.getElementById('panel').innerHTML=`<b>Observed cells · ${D.time}</b><br>
radar ${D.inputs.opera_dbzh.slice(11,16)}Z · rain rate ${f(D.inputs.opera_rate&&D.inputs.opera_rate.slice(11,16))}Z · lightning ${f(D.inputs.eumetsat_li&&D.inputs.eumetsat_li.slice(11,16))}Z${un?'<br>unavailable: '+un:''}<br>
marker colour = trend:${Object.entries(D.trendColour).map(([k,v])=>`<span class="sw" style="background:${v}"></span>${k}`).join('')}<br>
magenta = where the cell would be in 30 min · yellow = lightning<br><span style="color:#888">${D.policy} · click a marker for details</span>`;
</script></body></html>
"""
