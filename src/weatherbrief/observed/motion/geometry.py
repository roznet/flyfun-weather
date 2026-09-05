"""Bounded north-increasing AEQD arrays and topology-preserving cell contours."""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
from shapely import intersects_xy
from shapely.geometry import MultiPolygon, Polygon, box, mapping
from shapely.ops import transform, unary_union

from weatherbrief.models.observed_motion import AnalysisDomain, GeometryRecord
from weatherbrief.observed.grid import _transformers, GridWindow
from .policy import DEFAULT_POLICY


@dataclass(frozen=True)
class AnalysisGrid:
    crs: str
    center: tuple[float, float]
    origin_x_m: float
    origin_y_m: float
    width: int
    height: int
    cell_size_m: float

    def project(self, lon, lat):
        return _transformers(self.crs)[0].transform(lon, lat)

    def inverse(self, x, y):
        return _transformers(self.crs)[1].transform(x, y)

    @property
    def shape(self):
        return self.height, self.width

    @property
    def domain(self):
        return box(self.origin_x_m, self.origin_y_m,
                   self.origin_x_m+self.width*self.cell_size_m,
                   self.origin_y_m+self.height*self.cell_size_m)

    def centres(self):
        return np.meshgrid(self.origin_x_m+(np.arange(self.width)+.5)*self.cell_size_m,
                           self.origin_y_m+(np.arange(self.height)+.5)*self.cell_size_m)

    def boundary_lonlat(self):
        # Densified perimeter includes off-corner longitude/latitude extrema.
        n = max(self.width, self.height)+1
        x0, y0, x1, y1 = self.domain.bounds
        xs, ys = np.linspace(x0,x1,n), np.linspace(y0,y1,n)
        return self.inverse(np.concatenate([xs, xs, np.full(n,x0), np.full(n,x1)]),
                            np.concatenate([np.full(n,y0),np.full(n,y1),ys,ys]))

    def to_record(self):
        lon, lat = self.boundary_lonlat()
        if not np.all(np.isfinite(lon)) or not np.all(np.isfinite(lat)) or np.ptp(lon) > 180:
            raise ValueError("region_too_large")
        return AnalysisDomain(center=self.center, crs=self.crs, cell_size_m=self.cell_size_m,
                              width_cells=self.width, height_cells=self.height,
                              origin_x_m=self.origin_x_m, origin_y_m=self.origin_y_m,
                              bounds=(float(min(lon)),float(min(lat)),float(max(lon)),float(max(lat))),
                              reason_codes=["grid_discretization", "projected_translation"])


def route_positions(route):
    points = route.waypoints if hasattr(route, "waypoints") else route
    positions = [(float(p.lon), float(p.lat)) if hasattr(p, "lon") else tuple(map(float,p)) for p in points]
    if len(positions) < 2 or any(len(p) != 2 or not np.isfinite(p).all() or abs(p[0]) > 180 or abs(p[1]) > 90 for p in positions):
        raise ValueError("invalid_route")
    if any(abs(a[0]-b[0]) > 180 for a,b in zip(positions,positions[1:])):
        raise ValueError("region_too_large")
    return positions


def build_analysis_grid(route, history_span_seconds=0, policy=DEFAULT_POLICY):
    from euro_aip.models.navpoint import NavPoint
    positions = route_positions(route)
    lon, lat = np.asarray(positions).T
    center = (float((min(lon)+max(lon))/2), float((min(lat)+max(lat))/2))
    crs = f"+proj=aeqd +lat_0={center[1]:.12g} +lon_0={center[0]:.12g} +datum=WGS84 +units=m +no_defs"
    probe = AnalysisGrid(crs, center, 0., 0., 1, 1, policy.analysis_cell_size_m)
    x,y = probe.project(lon, lat)
    if np.any(np.hypot(x,y) > policy.max_distance_from_projection_center_km*1000):
        raise ValueError("region_too_large")
    dense = [positions[0]]
    for a,b in zip(positions,positions[1:]):
        first, last = NavPoint(latitude=a[1], longitude=a[0]), NavPoint(latitude=b[1], longitude=b[0])
        bearing, distance = first.haversine_distance(last)
        steps = max(1, math.ceil(distance/policy.max_route_segment_nm))
        if len(dense)+steps > policy.max_route_segments+1:
            raise ValueError("region_too_large")
        for i in range(1, steps):
            p = first.point_from_bearing_distance(bearing, distance*i/steps)
            dense.append((p.longitude,p.latitude))
        dense.append(b)
    lon,lat = np.asarray(dense).T
    x,y = probe.project(lon,lat)
    padding = (policy.route_capture_corridor_nm*1852 + policy.max_search_speed_mps *
               (policy.projection_horizon_minutes*60 + min(max(history_span_seconds,0),policy.max_history_span_minutes*60))
               + (policy.template_size_cells//2)*policy.analysis_cell_size_m)
    cell = policy.analysis_cell_size_m
    x0,y0 = math.floor((min(x)-padding)/cell)*cell, math.floor((min(y)-padding)/cell)*cell
    width,height = math.ceil((max(x)+padding-x0)/cell),math.ceil((max(y)+padding-y0)/cell)
    if max(width,height) > policy.max_domain_dimension_cells or width*height > policy.max_domain_cells:
        raise ValueError("region_too_large")
    grid = AnalysisGrid(crs, center, x0, y0, width,height,cell)
    if max(math.hypot(xx,yy) for xx in (x0,x0+width*cell) for yy in (y0,y0+height*cell)) > policy.max_distance_from_projection_center_km*1000:
        raise ValueError("region_too_large")
    grid.to_record()  # Reject unsupported geographic wrapping too.
    return grid


def footprint(mask, grid):
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != grid.shape:
        raise ValueError("invalid_geometry")
    runs = []
    cell = grid.cell_size_m
    for row, occupied in enumerate(mask):
        edges = np.diff(np.concatenate(([False],occupied,[False])).astype(np.int8))
        for lo,hi in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)):
            runs.append(box(grid.origin_x_m+lo*cell,grid.origin_y_m+row*cell,
                            grid.origin_x_m+hi*cell,grid.origin_y_m+(row+1)*cell))
    return unary_union(runs)


def display_geometry(shape, grid, policy=DEFAULT_POLICY):
    def unavailable(reason):
        return GeometryRecord(status="unavailable", reason_codes=[reason], geometry=None,
                              provenance="grid_contour", simplification_tolerance_m=0.)
    if shape.is_empty or not shape.is_valid or shape.geom_type not in ("Polygon","MultiPolygon"):
        return unavailable("invalid_geometry")
    tolerance = policy.max_display_simplification_tolerance_m
    simplified = shape.simplify(tolerance, preserve_topology=True)
    polygons = [simplified] if simplified.geom_type == "Polygon" else list(simplified.geoms)
    holes = sum(len(p.interiors) for p in polygons)
    positions = sum(len(p.exterior.coords)+sum(len(r.coords) for r in p.interiors) for p in polygons)
    if len(polygons)>policy.max_polygon_components_per_footprint or holes>policy.max_holes_per_footprint or positions>policy.max_positions_per_footprint:
        return unavailable("geometry_limit")
    geographic = transform(grid.inverse, MultiPolygon(polygons))
    coords = mapping(geographic)
    if not geographic.is_valid or any(not np.isfinite(v) for p in geographic.geoms for r in [p.exterior,*p.interiors] for xy in r.coords for v in xy):
        return unavailable("invalid_geometry")
    if geographic.bounds[2]-geographic.bounds[0] > 180:
        return unavailable("invalid_geometry")
    return GeometryRecord(status="available", reason_codes=["grid_discretization"], geometry=coords,
                          provenance="grid_contour", simplification_tolerance_m=tolerance)


@dataclass
class GroundSamples:
    descriptor: np.ndarray
    known: np.ndarray
    detected: np.ndarray
    values: np.ndarray
    temperature_k: np.ndarray
    sample_ids: np.ndarray
    quality: np.ndarray
    sample_positions: np.ndarray
    collisions: int = 0

    @classmethod
    def empty(cls, grid):
        return cls(np.zeros(grid.shape,dtype=np.float32),np.zeros(grid.shape,dtype=bool),
                   np.zeros(grid.shape,dtype=bool),np.full(grid.shape,np.nan,dtype=np.float32),
                   np.full(grid.shape,np.nan,dtype=np.float32),np.full(grid.shape,-1,dtype=np.int64),
                   np.full(grid.shape,-1,dtype=np.int16),np.full((*grid.shape,2),np.nan))


def sample_radar(frame, grid, policy=DEFAULT_POLICY):
    if not np.allclose([abs(frame.grid.dx),abs(frame.grid.dy)], policy.supported_radar_spacing_m, rtol=0,atol=1e-6):
        raise ValueError("unsupported_grid_spacing")
    out = GroundSamples.empty(grid)
    lon,lat = grid.inverse(*grid.centres())
    col,row = frame.grid.lonlat_to_colrow(lon,lat)
    finite = np.isfinite(col)&np.isfinite(row)
    col = np.floor(np.where(finite,col,0)+.5).astype(int)-frame.window.col0
    row = np.floor(np.where(finite,row,0)+.5).astype(int)-frame.window.row0
    inside = finite & (col>=0)&(row>=0)&(col<frame.values.shape[1])&(row<frame.values.shape[0])
    rr,cc = row[inside],col[inside]
    out.known[inside] = ~frame.nodata[rr,cc]
    out.values[inside] = frame.values[rr,cc]
    if frame.quantity == "DBZH":
        out.detected[inside] = frame.detected[rr,cc] & (frame.values[rr,cc]>=policy.radar_threshold_dbz)
        out.descriptor[inside] = np.where(frame.detected[rr,cc],np.clip((frame.values[rr,cc]-policy.radar_threshold_dbz)/60,0,1),0)
    else:
        # RATE is separately timed scalar context, never DBZH matching texture.
        out.detected[inside] = frame.detected[rr,cc]
    out.sample_ids[inside] = (rr+frame.window.row0)*frame.grid.nx+cc+frame.window.col0
    slon,slat = frame.grid.colrow_to_lonlat(cc+frame.window.col0,rr+frame.window.row0)
    out.sample_positions[inside] = np.column_stack([slon,slat])
    return out


def sample_quadrilateral(out, grid, corners, *, value, temperature_k, sample_id, quality,
                         clear=False, sample_position=None, policy=DEFAULT_POLICY):
    corners = np.asarray(corners)
    if corners.shape != (4,2) or not np.isfinite(corners).all():
        return
    shape = Polygon(corners)
    if shape.is_empty or not shape.is_valid or shape.area <= 0:
        return
    minx,miny,maxx,maxy = shape.bounds
    c0=max(0,math.ceil((minx-grid.origin_x_m)/grid.cell_size_m-.5))
    c1=min(grid.width,math.floor((maxx-grid.origin_x_m)/grid.cell_size_m-.5)+1)
    r0=max(0,math.ceil((miny-grid.origin_y_m)/grid.cell_size_m-.5))
    r1=min(grid.height,math.floor((maxy-grid.origin_y_m)/grid.cell_size_m-.5)+1)
    if c0>=c1 or r0>=r1:
        return
    x,y=np.meshgrid(grid.origin_x_m+(np.arange(c0,c1)+.5)*grid.cell_size_m,
                    grid.origin_y_m+(np.arange(r0,r1)+.5)*grid.cell_size_m)
    inside = intersects_xy(shape,x,y)
    target = np.s_[r0:r1,c0:c1]
    if clear:
        out.known[target] |= inside
        return
    if value is None or not np.isfinite(value):
        return
    out.collisions += int(np.count_nonzero(inside & np.isfinite(out.values[target])))
    win=inside & (~np.isfinite(out.values[target]) | (value>out.values[target]))
    out.known[target] |= inside
    for name,val in (("values",value),("temperature_k",temperature_k if temperature_k is not None else np.nan),
                     ("sample_ids",sample_id),("quality",quality),("detected",value>=policy.cloud_threshold_m_msl),
                     ("descriptor",float(value>=policy.cloud_threshold_m_msl))):
        getattr(out,name)[target][win] = val
    if sample_position is not None:
        out.sample_positions[target][win] = sample_position


def sample_ctth_block(frame, grid, out, policy=DEFAULT_POLICY, *, deadline=None):
    """Project one source row at a time: bounded corner batches, no block backlog."""
    from .history import check_deadline
    for row in range(frame.values.shape[0]):
        check_deadline(deadline)
        cols=np.flatnonzero(~frame.nodata[row])
        if not cols.size:
            continue
        absolute_cols=cols+frame.window.col0
        absolute_row=row+frame.window.row0
        corner_cols=absolute_cols[:,None]+np.array([-.5,.5,.5,-.5])
        corner_rows=np.full((cols.size,4),absolute_row)+np.array([-.5,-.5,.5,.5])
        lon,lat=frame.grid.colrow_to_lonlat(corner_cols,corner_rows)
        detected=frame.detected[row,cols]
        dlon=np.where(detected,frame.aux["delta_longitude"][row,cols],0)
        dlat=np.where(detected,frame.aux["delta_latitude"][row,cols],0)
        lon,lat=lon+dlon[:,None],lat+dlat[:,None]
        x,y=grid.project(lon,lat)
        center_lon,center_lat=frame.grid.colrow_to_lonlat(absolute_cols,np.full(cols.size,absolute_row))
        center_lon,center_lat=center_lon+dlon,center_lat+dlat
        temperatures=frame.aux.get("cloud_top_temperature")
        quality=frame.aux.get("quality_method")
        for index,col in enumerate(cols):
            if index % 128 == 0:
                check_deadline(deadline)
            sample_quadrilateral(out,grid,np.column_stack([x[index],y[index]]),
                                 value=float(frame.values[row,col]) if detected[index] else None,
                                 temperature_k=float(temperatures[row,col]) if temperatures is not None else None,
                                 sample_id=absolute_row*frame.grid.nx+int(absolute_cols[index]),
                                 quality=int(quality[row,col]) if quality is not None else -1,
                                 clear=bool(frame.undetect[row,col]),
                                 sample_position=(center_lon[index],center_lat[index]),policy=policy)


def decode_ctth(path, grid, window, policy=DEFAULT_POLICY, *, deadline=None):
    """Stream full-width CTTH strips through one open granule."""
    import netCDF4
    from weatherbrief.observed import ctth
    from .history import check_deadline
    out=GroundSamples.empty(grid)
    with netCDF4.Dataset(str(path)) as dataset:
        source_grid=ctth.read_grid(dataset)
        rows=min(policy.max_ctth_decode_rows,policy.max_ctth_decode_cells//source_grid.nx)
        if rows < 1:
            raise ValueError("source_window_limit")
        for start in range(max(0,window.row0),min(window.row1,source_grid.ny),rows):
            check_deadline(deadline)
            block=ctth.read_dataset_window(dataset,GridWindow(start,min(start+rows,window.row1,source_grid.ny),
                                           0,source_grid.nx,full_width=True),source="eumetsat_ctth",path=path)
            sample_ctth_block(block,grid,out,policy,deadline=deadline)
            del block
    return out


def radar_window(source_grid, grid, policy=DEFAULT_POLICY):
    """Bound the nearest-cell gather before any radar pixel decoding."""
    if not np.allclose([abs(source_grid.dx),abs(source_grid.dy)],policy.supported_radar_spacing_m,rtol=0,atol=1e-6):
        raise ValueError("unsupported_grid_spacing")
    lon,lat=grid.inverse(*grid.centres())
    col,row=source_grid.lonlat_to_colrow(lon,lat)
    finite=np.isfinite(col)&np.isfinite(row)
    if not finite.any():
        return GridWindow(0,0,0,0)
    c0=max(0,min(source_grid.nx,math.floor(float(np.min(col[finite]))+.5)))
    c1=max(c0,min(source_grid.nx,math.floor(float(np.max(col[finite]))+.5)+1))
    r0=max(0,min(source_grid.ny,math.floor(float(np.min(row[finite]))+.5)))
    r1=max(r0,min(source_grid.ny,math.floor(float(np.max(row[finite]))+.5)+1))
    window=GridWindow(r0,r1,c0,c1)
    if window.size>policy.max_radar_window_cells or max(window.shape)>policy.max_radar_window_dimension_cells:
        raise ValueError("source_window_limit")
    return window
