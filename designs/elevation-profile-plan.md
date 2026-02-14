# SRTM Elevation Profile Along Route

> Add terrain elevation data to the cross-section visualization, providing a high-resolution ground profile along the flight route

## 1. Problem

The cross-section chart shows weather layers (clouds, icing, CAT, etc.) but has no terrain reference. Pilots need to see the ground elevation profile to assess:
- Minimum safe altitude relative to terrain
- Weather layers relative to ground (e.g., cloud base AGL vs MSL)
- Mountain passes, valleys, and terrain hazards along the route

Currently, route points are spaced every ~20nm — far too coarse for a meaningful terrain profile. We need much finer granularity (every ~1km / 0.5nm) to capture terrain features like ridges, valleys, and mountain passes.

## 2. Data Source: SRTM

**Shuttle Radar Topography Mission** provides near-global elevation data at 1 arc-second (~30m) and 3 arc-second (~90m) resolution. For European GA routes, SRTM3 (90m) is more than sufficient — terrain features at route scale are well-captured.

### Package Choice: `srtm.py` (PyPI: `SRTM.py`)

| Criteria | Assessment |
|----------|-----------|
| Resolution | SRTM1 (30m) with SRTM3 (90m) fallback |
| Auto-download | Yes — tiles cached in `~/.cache/srtm/` |
| Dependencies | Minimal — only `requests` |
| Docker fit | Excellent — cache dir mounted as volume |
| API | Simple: `get_data().get_elevation(lat, lon)` |
| Maintenance | Stable (v0.3.7, 2021) — SRTM data is static |

**Why `srtm.py` over alternatives:**
- **vs `python-srtm`**: No auto-download — requires pre-downloading tiles. Has a built-in `get_elevation_profile()` but the manual tile management is a non-starter for Docker.
- **vs `elevation`/`rasterio`**: Requires GDAL — massive dependency, overkill for point lookups.
- **vs `NASADEM`**: Better data quality (void-filled) but requires NASA Earthdata credentials — operational complexity.
- **vs Open-Topo-Data (self-hosted)**: Production-grade but adds a sidecar Docker container — overkill for our scale.

**Data source note**: `srtm.py` defaults to viewfinderpanoramas.org as tile source (NASA put SRTM behind auth in 2021). This is a well-known mirror used by many open-source projects. For a GA weather app, the accuracy is more than sufficient. If needed later, we can point it at official tiles.

### Alternative considered: API-based approach

An Open-Elevation or Open-Topo-Data public API would avoid local tile management entirely. However:
- Public APIs are rate-limited and unreliable
- Adds external dependency for every briefing refresh
- Self-hosting adds a container

The local `srtm.py` approach is self-contained, fast, and works offline after first tile download.

## 3. Architecture

### 3.1 Where Elevation Fits in the Pipeline

Elevation is **route geometry data**, not weather data. It should be:
- Computed once per route (not per model, not per refresh)
- Much higher resolution than weather route points (~1km vs ~20nm)
- Served as a separate lightweight endpoint
- Cached — terrain doesn't change between refreshes

```
                          Route Definition
                               │
                    ┌──────────┴──────────┐
                    │                     │
            interpolate_route()    interpolate_elevation()
            (~20nm spacing)        (~1km / 0.5nm spacing)
                    │                     │
            Weather pipeline         Elevation profile
            (per-model fetch)        (SRTM lookup)
                    │                     │
              route_analyses.json    elevation_profile.json
                    │                     │
                    └──────────┬──────────┘
                               │
                     Cross-section canvas
                     (terrain layer drawn first)
```

### 3.2 Elevation Profile Generation

New module: `src/weatherbrief/fetch/elevation.py`

```python
"""Terrain elevation profile along a route using SRTM data."""

import srtm

def get_elevation_profile(
    route: RouteConfig,
    spacing_nm: float = 0.5,
) -> ElevationProfile:
    """Generate a high-resolution elevation profile along a route.

    Uses the same great-circle interpolation as route_points.py but at
    much finer spacing (~0.5nm ≈ 1km) for terrain detail.

    Returns ElevationProfile with ~800-1000 points for a 400nm route.
    """
    elevation_data = srtm.get_data()
    points = []

    # Reuse NavPoint great-circle math from route_points.py
    # Walk each leg at fine spacing, query SRTM for each point
    for each interpolated (lat, lon, distance_nm):
        elev_m = elevation_data.get_elevation(lat, lon)
        elev_ft = elev_m * 3.28084 if elev_m is not None else 0
        points.append(ElevationPoint(
            distance_nm=distance_nm,
            elevation_ft=elev_ft,
            lat=lat,
            lon=lon,
        ))

    return ElevationProfile(
        route_name=route.name,
        points=points,
        max_elevation_ft=max(p.elevation_ft for p in points),
        total_distance_nm=points[-1].distance_nm,
    )
```

**Key design decisions:**
- **0.5nm spacing** (~1km): ~800 points for a 400nm route. Fine enough to capture ridges/valleys, coarse enough to keep payloads small (~20KB JSON).
- **Reuse `NavPoint` great-circle math** from `euro_aip` — same library already used by `route_points.py`.
- **Handle SRTM voids**: Over water or missing tiles, `get_elevation()` returns `None` → default to 0ft (sea level). This is correct for overwater segments.
- **Factor out common interpolation logic**: The point-by-point walk along a route is shared with `route_points.py`. Extract a common `walk_route()` generator that both can use.

### 3.3 Data Model

New Pydantic models in `models/analysis.py`:

```python
class ElevationPoint(BaseModel):
    """Single elevation sample along the route."""
    distance_nm: float
    elevation_ft: float
    lat: float
    lon: float

class ElevationProfile(BaseModel):
    """High-resolution terrain profile along a route."""
    route_name: str
    points: list[ElevationPoint]
    max_elevation_ft: float
    total_distance_nm: float
```

### 3.4 Storage & Caching

**File-based, alongside existing pack artifacts:**

```
data/packs/{user_id}/{flight_id}/{timestamp}/
├── snapshot.json
├── cross_section.json
├── elevation_profile.json    ← NEW
├── route_analyses.json
└── ...
```

**Caching strategy**: Elevation depends only on the route geometry (waypoints), not on the date/time/weather. Two approaches:

**Option A — Per-pack (simpler, chosen):** Generate and save `elevation_profile.json` alongside each pack. The ~20KB file is trivial. Regenerating on each refresh takes <2s (SRTM tiles are cached in memory after first load). This keeps the storage model consistent — everything for a pack is in one directory.

**Option B — Route-level cache (optimization if needed later):** Cache at `data/elevation/{route_hash}.json` keyed by waypoint coordinates. Only regenerate if waypoints change. More complex but avoids redundant computation.

### 3.5 Pipeline Integration

In `pipeline.py`, elevation fetching runs **in parallel with** or **after** route interpolation, before weather fetch:

```python
# In execute_briefing():
_notify("route_interpolation")
route_points = interpolate_route(route, spacing_nm=20.0)

_notify("elevation_profile")
try:
    elevation_profile = get_elevation_profile(route, spacing_nm=0.5)
except Exception as e:
    logger.warning("Elevation profile failed: %s", e)
    elevation_profile = None  # graceful degradation
```

Elevation failure should never block the briefing. If SRTM tiles can't be downloaded (network issue, missing coverage), the cross-section simply won't show terrain.

Save in the pack directory alongside other artifacts:
```python
if elevation_profile and options.output_dir:
    save_elevation_profile(elevation_profile, options.output_dir)
```

### 3.6 API Endpoint

New endpoint in `api/packs.py`:

```
GET /api/flights/{id}/packs/{ts}/elevation
```

Returns `ElevationProfile` JSON. Follows the same pattern as the existing `/snapshot`, `/route-analyses` endpoints.

### 3.7 SRTM Tile Caching in Docker

The SRTM tile cache directory needs to persist across container restarts:

```python
# In elevation.py
SRTM_CACHE_DIR = Path(os.environ.get("SRTM_CACHE_DIR", "data/.cache/srtm"))

elevation_data = srtm.get_data(
    local_cache_dir=str(SRTM_CACHE_DIR),
    # Use SRTM3 by default (90m, smaller tiles, sufficient for route-scale)
    srtm1=False,
    srtm3=True,
)
```

In Docker, `data/` is already a mounted volume, so `data/.cache/srtm/` persists automatically. European coverage (~15 tiles for typical routes) is ~30MB total.

**First-run behavior**: The first briefing for a new route region will be slower (~10-30s) as tiles download. Subsequent runs use cached tiles (<2s for 800 points).

## 4. Frontend: Terrain Layer

### 4.1 New Cross-Section Layer

New file: `web/ts/visualization/cross-section/layers/terrain-fill.ts`

The terrain layer renders as a **filled area from ground level to the bottom of the chart**, creating a solid ground profile:

```
  FL180 ─────────────────────────────────────
          ~~~~~ clouds ~~~~~
      ---- freezing level ----
          ░░░ icing ░░░
  ═══════ cruise altitude ════════════


           ▓▓▓▓▓▓                    ▓▓▓▓
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
  ████████████████████████████████████████████  ← terrain fill
  EGTK ──────── LFPB ──────────────── LSGS
```

**Rendering approach:**
- Smooth mode: Monotone cubic spline through elevation points (reuse `drawSmoothLine` from `base.ts`), then fill down to chart bottom.
- Column mode: Step function with filled rectangles.
- **Color**: Earthy gradient — green at low elevations, brown/tan at higher elevations. Or a simple solid earth tone with darker outline.
- **Rendering order**: First layer (behind everything else). The terrain fill sits at the very back, giving all weather layers a ground reference.

```typescript
export const terrainFillLayer: CrossSectionLayer = {
  id: 'terrain',
  name: 'Terrain',
  group: 'reference',  // or new 'terrain' group
  defaultEnabled: true,

  render(ctx, transform, data, mode) {
    if (!data.terrainProfile) return;

    const { plotArea } = transform;
    const points = data.terrainProfile;

    // Draw filled area from terrain surface to chart bottom
    ctx.fillStyle = '#8B7355';  // earth tone
    ctx.beginPath();

    // Start at bottom-left
    ctx.moveTo(plotArea.left, plotArea.top + plotArea.height);

    // Draw terrain profile (left to right)
    if (mode === 'smooth') {
      // Use spline interpolation for smooth terrain
      drawTerrainSpline(ctx, points, transform);
    } else {
      // Step function
      for (const p of points) {
        ctx.lineTo(
          transform.distanceToX(p.distanceNm),
          transform.altitudeToY(p.elevationFt),
        );
      }
    }

    // Close at bottom-right
    ctx.lineTo(plotArea.left + plotArea.width, plotArea.top + plotArea.height);
    ctx.closePath();
    ctx.fill();

    // Terrain outline
    ctx.strokeStyle = '#6B5B45';
    ctx.lineWidth = 1.5;
    // ... stroke the terrain profile line
  },
};
```

### 4.2 Data Integration

**Extend `VizRouteData`** to carry terrain data:

```typescript
// In types.ts
export interface TerrainPoint {
  distanceNm: number;
  elevationFt: number;
}

export interface VizRouteData {
  // ... existing fields
  terrainProfile: TerrainPoint[] | null;  // NEW — null if not available
}
```

**Fetch elevation data** in the briefing page alongside route analyses:

```typescript
// In briefing-main.ts or store
const [routeAnalyses, elevation] = await Promise.all([
  fetchRouteAnalyses(flightId, timestamp),
  fetchElevationProfile(flightId, timestamp),  // NEW
]);
```

The elevation profile is fetched separately from route analyses because:
1. It has different granularity (~800 points vs ~20 points)
2. It's optional — the page works fine without it
3. It's small and fast to load (~20KB)

### 4.3 Y-Axis Range Adjustment

Currently the Y-axis starts at 0ft (sea level). With terrain, we should keep this — the terrain fill naturally shows the ground rising above 0ft. No axis change needed.

However, if a route crosses high terrain (e.g., Alps), we may want to ensure the flight ceiling extends high enough to show weather above the terrain. The current `flightCeilingFt = cruise_altitude + 5000ft` should handle this, but we could also consider `max(cruise + 5000, max_terrain + 3000)`.

### 4.4 Terrain Coloring Options

**Option A — Solid fill (recommended for v1):**
Simple earth-tone fill (#8B7355) with slightly darker outline. Clean, doesn't compete with weather layers.

**Option B — Elevation gradient (future enhancement):**
Color varies by elevation: green (<2000ft) → brown (2000-6000ft) → gray (>6000ft). More informative but more complex.

**Option C — Hypsometric tints (future):**
Classic cartographic coloring. Beautiful but overkill for a weather app.

### 4.5 Layer Registration

Add to `layer-registry.ts`:

```typescript
import { terrainFillLayer } from './layers/terrain-fill';

const ALL_LAYERS: CrossSectionLayer[] = [
  terrainFillLayer,       // ← FIRST (drawn behind everything)
  convectiveBgLayer,
  cloudBandsLayer,
  // ... rest unchanged
];
```

Add `'terrain'` to the `LayerGroup` type, or use `'reference'` group.

## 5. Refactoring: Common Route Walking

Both `route_points.py` (20nm spacing) and the new elevation code (0.5nm spacing) need to walk a multi-leg route with great-circle interpolation. Extract a common generator:

```python
# fetch/route_walk.py
def walk_route(
    route: RouteConfig,
    spacing_nm: float,
) -> Iterator[tuple[float, float, float, str | None, str | None]]:
    """Yield (lat, lon, distance_nm, waypoint_icao, waypoint_name) along a route.

    Walks each leg using great-circle math, yielding points at the
    specified spacing. Named waypoints are always included.
    """
    # ... same logic as interpolate_route but as a generator
```

Then `interpolate_route()` becomes:
```python
def interpolate_route(route, spacing_nm=20.0):
    return [
        RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=dist,
                   waypoint_icao=icao, waypoint_name=name)
        for lat, lon, dist, icao, name in walk_route(route, spacing_nm)
    ]
```

And `get_elevation_profile()` uses:
```python
def get_elevation_profile(route, spacing_nm=0.5):
    elevation_data = srtm.get_data(local_cache_dir=..., srtm1=False, srtm3=True)
    points = []
    for lat, lon, dist, _, _ in walk_route(route, spacing_nm):
        elev_m = elevation_data.get_elevation(lat, lon)
        points.append(ElevationPoint(
            distance_nm=round(dist, 2),
            elevation_ft=round(elev_m * 3.28084) if elev_m is not None else 0,
            lat=round(lat, 5),
            lon=round(lon, 5),
        ))
    return ElevationProfile(...)
```

This avoids duplicating the great-circle walk logic.

## 6. Performance

| Operation | Time | Notes |
|-----------|------|-------|
| SRTM tile download (first time) | 10-30s per tile | ~3-5 tiles for a European route, cached after |
| Elevation lookup (cached tiles) | ~2ms per point | Tile loaded in memory |
| 800-point profile (cached) | ~1.5s | Dominated by Python overhead, not I/O |
| JSON serialization | <50ms | ~20KB payload |
| Canvas rendering (800 points) | <2ms | Single filled path |

**Bottleneck**: First-run tile download. Mitigation: pre-warm common European tiles on container startup (optional), or accept the one-time delay.

## 7. Implementation Steps

### Phase 1: Backend — Elevation Profile Generation
1. Add `SRTM.py` to `pyproject.toml` dependencies
2. Extract `walk_route()` generator from `route_points.py` → `fetch/route_walk.py`
3. Refactor `interpolate_route()` to use `walk_route()`
4. Add `ElevationPoint` and `ElevationProfile` models
5. Implement `fetch/elevation.py` with `get_elevation_profile()`
6. Add elevation to pipeline (graceful degradation on failure)
7. Save `elevation_profile.json` in pack directory
8. Add API endpoint `GET /packs/{ts}/elevation`

### Phase 2: Frontend — Terrain Layer
1. Add `TerrainPoint` type and `terrainProfile` field to `VizRouteData`
2. Fetch elevation data in briefing page
3. Implement `terrain-fill.ts` layer
4. Register in layer registry (first position)
5. Add toggle in control panel (default: enabled)

### Phase 3: Polish
1. Elevation gradient coloring based on height
2. Terrain label showing max elevation
3. Hover tooltip shows terrain elevation at cursor position
4. Consider terrain-aware Y-axis range adjustment

## 8. Docker Considerations

- **SRTM cache volume**: Already covered by `data/` mount. Tiles go to `data/.cache/srtm/`.
- **No new system dependencies**: `SRTM.py` is pure Python + `requests`.
- **Image size**: No change. Tiles are downloaded at runtime, not baked into the image.
- **Offline operation**: After tiles are cached, elevation works fully offline.

## 9. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| viewfinderpanoramas.org goes down | Tiles are cached locally. Only first download needs the server. Could pre-bundle European tiles (~30MB). |
| SRTM voids over water | Default to 0ft. Correct behavior for overwater segments. |
| Large elevation payload | 800 points × 4 fields = ~20KB JSON. Negligible. |
| Slow first-run tile download | Accept one-time delay. Could pre-warm on startup. |
| `srtm.py` unmaintained | Library is simple and stable. SRTM data format is fixed. Easy to fork or replace if needed. |

## 10. Future Extensions

- **Terrain awareness in analysis**: Use terrain elevation to compute AGL cloud bases, terrain clearance at cruise altitude, CFIT risk assessment.
- **MEF (Maximum Elevation Figure)**: Show the highest terrain + obstacle in each grid cell along the route.
- **Obstacle data**: Overlay antenna/tower data from AIP obstacle databases.
- **3D terrain on route map**: Use Leaflet terrain tiles for a 3D-ish map view.
