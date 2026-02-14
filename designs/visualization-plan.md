# Advanced Visualization: Cross-Section Plot & Route Map

> Interactive 2D cross-section (GRAMET-inspired) and geographic map to visualize weather metrics along the route

## 1. Overview

Two new visualization panels on the briefing page:

1. **Cross-Section Plot** — Canvas-rendered vertical cross-section above the route slider. X-axis = distance/time along route, Y-axis = altitude (ft) / pressure (hPa). Displays weather metrics as lines (single altitude values) and bands (altitude ranges with fill).

2. **Route Map** — Leaflet map showing the route with per-segment color and thickness encoding weather metrics at each point.

Both share a unified control panel where users toggle individual metrics on/off via checkboxes, and select which model's data to display.

---

## 2. Data Sources

### What we already have (no backend changes needed initially)

**`route_analyses.json`** (fetched via existing `/packs/{ts}/route-analyses` endpoint) contains per-route-point:

| Data | Source field | Viz use |
|------|-------------|---------|
| Freezing level (0°C) | `indices.freezing_level_ft` | Line |
| −10°C level | `indices.minus10c_level_ft` | Line |
| −20°C level | `indices.minus20c_level_ft` | Line |
| LCL | `indices.lcl_altitude_ft` | Line |
| LFC | `indices.lfc_altitude_ft` | Line (where exists) |
| EL | `indices.el_altitude_ft` | Line (where exists) |
| Cloud layers | `cloud_layers[].{base_ft, top_ft, coverage}` | Bands (gray, opacity ∝ coverage) |
| Icing zones | `icing_zones[].{base_ft, top_ft, risk, type}` | Bands (blue→red by risk) |
| CAT risk layers | `vertical_motion.cat_risk_layers[].{base_ft, top_ft, risk}` | Bands (amber→red by risk) |
| Inversion layers | `inversion_layers[].{base_ft, top_ft, strength_c}` | Bands (warm color, opacity ∝ strength) |
| Convective risk | `convective.risk_level` | Background color column |
| Vertical regimes | `altitude_advisories.regimes[model][]` | Reference overlay |
| Cloud cover (NWP) | `cloud_cover_{low,mid,high}_pct` | Map metric |
| Wind | `wind_components[model].{headwind_kt, crosswind_kt}` | Map metric |
| CAPE | `indices.cape_surface_jkg` | Map metric |
| Route geometry | `lat, lon, distance_from_origin_nm` | Map route |
| Timing | `interpolated_time` | X-axis secondary label |

**Key insight**: All assessed/aggregated data (cloud layers, icing zones, CAT layers, thermodynamic indices) is already in `route_analyses.json`. We do **not** need `derived_levels` (which are excluded) for the initial implementation — lines and bands are fully described by the assessed outputs.

### Future enhancement: per-level data

For contour-style visualizations (temperature field, humidity field, wind barbs at levels — like a true GRAMET), we'd need `derived_levels` data. Options for later:
1. New lightweight API endpoint returning only visualization-relevant per-level fields
2. Include derived_levels in route_analyses with a query param (`?include_levels=true`)
3. Fetch cross_section.json and run derivations client-side

Not needed for Phase 1.

---

## 3. Cross-Section Plot Design

### 3.1 Coordinate System

```
Y-axis (left): Altitude in feet        Y-axis (right): Pressure in hPa
  ┌─────────────────────────────────────────────┐
  │ FL180 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │ 500
  │                                             │
  │         ~~~~~ cloud band (gray) ~~~~~       │
  │  ---- freezing level (blue line) ----       │
  │         ░░░░ icing zone (red) ░░░░░         │ 700
  │  - - - LCL (dashed green) - - - - -        │
  │                                             │
  │  ═══════ cruise altitude (ref line) ════════│ 850
  │                                             │
  │ SFC ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ │ 1000
  └─────────────────────────────────────────────┘
    EGTK ──────── LFPB ──────────────── LSGS
    0nm           150nm                  450nm
    09:00Z        10:30Z                 13:30Z
```

- **X-axis**: Distance (nm) primary, time (UTC) secondary. Linear scale.
- **Y-axis**: Altitude (ft) primary, pressure (hPa) secondary. Linear in altitude (not log-pressure), since GA interest is SFC–FL180.
- **Default range**: SFC to `flight_ceiling_ft` (from Flight model, typically FL180). Expandable via zoom/scroll.
- **Pressure ticks**: Mapped via standard atmosphere (1000→360ft, 925→2500ft, 850→5000ft, 700→10000ft, 500→18000ft).

### 3.2 Layer Types

Each visualizable metric implements a common layer interface. Three rendering types:

#### Line Layers
Single altitude value per route point, connected as a smooth line.

| Metric | Color | Style | Group |
|--------|-------|-------|-------|
| Freezing level (0°C) | Cyan | Solid 2px | Temperature |
| −10°C level | Blue | Solid 1.5px | Temperature |
| −20°C level | Navy | Dashed 1px | Temperature |
| LCL | Green | Dashed 2px | Stability |
| LFC | Orange | Dashed 1.5px | Stability |
| EL | Red | Dashed 1.5px | Stability |
| Cruise altitude | White/gray | Dotted 1px | Reference |

Lines are drawn by iterating route points and connecting `(distance, altitude_ft)` pairs. Points where the value is `null` create gaps.

**Two render modes** (togglable by user):
- **Smooth**: Monotone cubic spline interpolation between points. Produces fluid, natural-looking curves. Uses Canvas `bezierCurveTo()` with tangents computed from adjacent points (Fritsch-Carlson monotone interpolation to prevent overshoot).
- **Columns**: Step-function centered on each point. Each point's value fills a column from halfway-to-previous to halfway-to-next. Crisp, unambiguous about what data exists vs what's interpolated.

Both modes are implemented — smooth is the default, columns available for comparison and for cases where discrete representation is preferred.

#### Band Layers
Altitude range (base→top) per route point, filled between two altitude curves.

| Metric | Fill color | Opacity logic | Group |
|--------|-----------|---------------|-------|
| Cloud layers | Gray (#888) | SCT=0.25, BKN=0.5, OVC=0.75 | Clouds |
| Icing zones | Risk-based: LIGHT=blue, MOD=orange, SEV=red | 0.3–0.6 | Icing |
| CAT layers | Amber to red | LIGHT=0.2, MOD=0.4, SEV=0.6 | Turbulence |
| Inversion layers | Warm pink | 0.2 × (strength_c / 10) | Stability |

**Two render modes** (same toggle as lines):
- **Smooth**: Match layers between adjacent points by altitude overlap. Draw filled areas with spline-interpolated base and top curves → `(x1,base1)→bezier→(x2,base2)→(x2,top2)→bezier→(x1,top1)`. Layers that appear/disappear between points taper to zero thickness.
- **Columns**: Per-point filled rectangles from `(x-halfStep, base_ft)` to `(x+halfStep, top_ft)`. Produces a stacked column chart effect — visually boxy but unambiguous about per-point data.

**Cloud layer continuity** (smooth mode): At each route point, cloud layers may have different base/top altitudes. Matching algorithm:
1. For each point pair, match cloud layers by altitude proximity (overlapping ranges)
2. Draw a filled area with smooth base and top curves connecting the matched layers
3. If a layer appears/disappears between points, taper to zero thickness at the midpoint

#### Background Columns
Full-height colored background behind each route segment, indicating a scalar risk.

| Metric | Color scheme | Group |
|--------|-------------|-------|
| Convective risk | NONE=transparent, LOW=yellow/0.1, MOD=orange/0.15, HIGH=red/0.2 | Convection |

### 3.3 Rendering Order (back to front)

1. Grid lines and axes
2. Background columns (convective risk)
3. Band layers: clouds → icing → CAT → inversions (most opaque on top)
4. Line layers: temperature levels → stability levels → cruise reference
5. Waypoint markers (vertical dashed lines at named waypoints)
6. Hover crosshair and tooltip
7. Selected point indicator (synced with slider)

### 3.4 Interaction

- **Hover**: Vertical line follows cursor. Tooltip shows distance, time, and all active metric values at that position (interpolated between points).
- **Click**: Selects the nearest route point — syncs with the existing route slider (`store.setSelectedPoint()`).
- **Slider sync**: Moving the route slider highlights the corresponding position on the cross-section.
- **Canvas sizing**: Responsive width (fills container), fixed aspect ratio (~4:1). Retina-aware (`devicePixelRatio`).

---

## 4. Route Map Design

### 4.1 Map Setup

- **Library**: Leaflet (~40KB gzipped). Loaded via CDN. Lightweight, no-framework, well-established.
- **Tiles**: OpenStreetMap (default) or OpenTopoMap (better terrain context for GA). User could toggle.
- **Initial view**: Auto-fit to route bounds with padding.

### 4.2 Route Rendering

The route is drawn as **individual segments** between adjacent route points. Each segment has two visual encodings:

- **Color**: Encodes one metric (user-selectable). Interpolated between point values.
- **Thickness**: Encodes another metric (user-selectable). Range: 3px (min) to 12px (max).

Default assignments (user can change):
- **Color** → Icing risk at cruise: green (NONE) → yellow (LIGHT) → orange (MOD) → red (SEV)
- **Thickness** → Cloud cover total: thin (clear) → thick (overcast)

### 4.3 Map Color Scales

| Metric category | Scale | Example |
|----------------|-------|---------|
| Risk levels (icing, convective, CAT) | Green → Yellow → Orange → Red | Discrete 4-step |
| Cloud cover | Light gray → Dark gray | Continuous 0–100% |
| Wind (headwind) | Green (tailwind) → White (calm) → Red (headwind) | Diverging |
| Wind (crosswind) | Thin → Thick (absolute value) | Size encoding |
| Temperature | Blue (cold) → Red (warm) | Continuous |
| CAPE | Green (0) → Yellow (500) → Red (3000) | Continuous |
| Model agreement | Green (good) → Orange (moderate) → Red (poor) | Discrete 3-step |

### 4.4 Map Metrics Available

**Color-encodable** (scalar per point):

| Metric | Source | Default scale |
|--------|--------|--------------|
| Icing risk (worst at cruise) | `icing_zones` filtered by cruise alt | Risk (G→R) |
| Cloud cover total | `cloud_cover_low+mid+high` or NWP total | Gray scale |
| Cloud cover low | `cloud_cover_low_pct` | Gray scale |
| Convective risk | `convective.risk_level` | Risk (G→R) |
| Headwind | `wind_components.headwind_kt` | Diverging (green=TW, red=HW) |
| Crosswind (abs) | `wind_components.crosswind_kt` | Magnitude |
| CAPE | `indices.cape_surface_jkg` | Continuous |
| Freezing level | `indices.freezing_level_ft` | Altitude color |
| Worst CAT risk | `vertical_motion.cat_risk_layers` max risk | Risk (G→R) |
| Model agreement (worst) | `model_divergence` worst agreement | Discrete |

**Thickness-encodable** (same list, mapped to line width instead).

### 4.5 Map Interaction

- **Hover segment**: Tooltip with point details (waypoint name if any, distance, all metric values).
- **Click segment**: Selects route point — syncs with slider and cross-section.
- **Waypoint markers**: Circle markers at named waypoints with ICAO labels.
- **Legend**: Color scale bar + thickness scale bar showing current metric mappings.

---

## 5. Controls & UI Layout

### 5.1 Page Layout — Modal Display

The visualization area has **three layout modes**, toggled by the user via buttons:

| Mode | Icon | Description |
|------|------|-------------|
| **Cross-Section** | `[━━]` | Cross-section only, full width. Best for detailed vertical analysis. |
| **Map** | `[🗺]` | Map only, full width. Best for geographic overview. |
| **Split** | `[━┃🗺]` | Side-by-side, 50/50. Both visible simultaneously. |

Default: **Cross-Section** (the primary new visualization). Mode persisted to localStorage.

```
┌──────────────────────────────────────────────────┐
│  [Existing header, assessment, synopsis, GRAMET]  │
├──────────────────────────────────────────────────┤
│  Layout: [━━] [🗺] [━┃🗺]    Render: [Smooth|Columns]  │
│  ┌──────────────────────────────────────────────┐ │
│  │  Cross-Section (full width)                  │ │
│  │  [canvas]                                    │ │
│  │                                              │ │
│  └──────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────┐ │
│  │  ◄━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━━━━━━━► │ │  ← Route slider
│  │  EGTK        LFPB                      LSGS │ │
│  └──────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────┐ │
│  │  Viz Controls                                │ │
│  │  Model: [GFS ▾]                              │ │
│  │                                              │ │
│  │  Cross-Section Layers:                       │ │
│  │  ☑ Temperature Lines  ☑ Cloud Bands          │ │
│  │  ☑ Icing Zones        ☐ CAT Turbulence       │ │
│  │  ☑ Stability (LCL/LFC/EL)  ☐ Inversions    │ │
│  │  ☐ Convective Risk Background                │ │
│  │                                              │ │
│  │  Map Encoding:                               │ │
│  │  Color: [Icing Risk ▾]  Width: [Cloud ▾]    │ │
│  └──────────────────────────────────────────────┘ │
│  [Existing Skew-T, Sounding Analysis, etc.]      │
└──────────────────────────────────────────────────┘
```

In **Split** mode:
```
│  ┌───────────────────────┐┌──────────────────────┐ │
│  │  Cross-Section (50%)  ││  Route Map (50%)     │ │
│  │  [canvas]             ││  [leaflet map]       │ │
│  └───────────────────────┘└──────────────────────┘ │
```

On narrow screens (<768px), Split mode stacks vertically (cross-section on top, map below).

### 5.2 Control Panel

The control panel sits between the slider and the rest of the analysis. It contains:

1. **Model selector**: Dropdown (already exists for Skew-T — extend to control visualizations too). Selecting a model updates both cross-section and map.

2. **Cross-section layer toggles**: Grouped checkboxes. Each toggle shows/hides a layer on the cross-section canvas. Groups:
   - **Temperature**: Freezing level, −10°C, −20°C
   - **Clouds**: Cloud bands
   - **Icing**: Icing zones
   - **Stability**: LCL, LFC, EL, Inversions
   - **Turbulence**: CAT layers
   - **Convection**: Background risk coloring
   - **Reference**: Cruise altitude line

3. **Map metric selectors**: Two dropdowns:
   - **Color**: Which metric maps to line color
   - **Width**: Which metric maps to line thickness

4. **Layout mode toggle**: Three buttons (Cross-Section / Map / Split) above the visualization area.

5. **Render mode toggle**: Smooth / Columns toggle next to the layout buttons. Affects all cross-section layers.

6. **Collapsible**: Control panel can be collapsed to save space once configured.

### 5.3 Interaction Sync

All three components (cross-section, map, slider) stay in sync:
- Moving the slider → cross-section highlights that position, map highlights that segment
- Clicking cross-section → slider moves, map highlights
- Clicking map segment → slider moves, cross-section highlights
- All trigger the existing `setSelectedPoint()` which re-renders sounding analysis tables below

---

## 6. Technical Architecture

### 6.1 File Organization

```
web/ts/
├── visualization/
│   ├── types.ts                  # Shared types, interfaces, color scales
│   ├── scales.ts                 # Color/size scale functions
│   ├── data-extract.ts           # Extract viz-ready data from RouteAnalysesManifest
│   │
│   ├── cross-section/
│   │   ├── renderer.ts           # Main canvas renderer, coordinate transform
│   │   ├── axes.ts               # Axis drawing (distance/time + altitude/pressure)
│   │   ├── interaction.ts        # Hover, click, cursor tracking
│   │   ├── layer-registry.ts     # Layer registration + toggle management
│   │   └── layers/
│   │       ├── base.ts           # CrossSectionLayer interface + helpers
│   │       ├── temperature-lines.ts   # 0°C, −10°C, −20°C
│   │       ├── stability-lines.ts     # LCL, LFC, EL
│   │       ├── cloud-bands.ts         # Cloud layer fills
│   │       ├── icing-bands.ts         # Icing zone fills
│   │       ├── cat-bands.ts           # CAT turbulence fills
│   │       ├── inversion-bands.ts     # Inversion layer fills
│   │       ├── convective-bg.ts       # Convective risk background
│   │       └── reference-lines.ts     # Cruise altitude, waypoint markers
│   │
│   ├── route-map/
│   │   ├── renderer.ts           # Leaflet map setup + route rendering
│   │   ├── segment-style.ts      # Color + width computation per segment
│   │   ├── legend.ts             # Color/width legend rendering
│   │   └── metrics.ts            # Available map metric definitions
│   │
│   └── controls/
│       ├── panel.ts              # Control panel DOM builder
│       ├── layer-toggles.ts      # Cross-section checkbox group
│       └── map-selectors.ts      # Map metric dropdowns
│
├── store/
│   └── briefing-store.ts         # Add: vizSettings slice (enabled layers, map metrics)
│
├── managers/
│   └── briefing-ui.ts            # Add: renderVisualization() calling into viz modules
```

### 6.2 Core Interfaces

```typescript
// --- Coordinate Transform ---
interface CoordTransform {
  distanceToX(distance_nm: number): number;
  altitudeToY(altitude_ft: number): number;
  xToDistance(x: number): number;
  yToAltitude(y: number): number;
  readonly plotArea: { left: number; top: number; width: number; height: number };
}

// --- Cross-Section Layer ---
interface CrossSectionLayer {
  readonly id: string;
  readonly name: string;
  readonly group: string;
  readonly defaultEnabled: boolean;
  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode): void;
}

// --- Spline helpers (in base.ts) ---
// drawSmoothLine(ctx, points, transform) — monotone cubic through (distance, altitude) pairs
// drawSmoothBand(ctx, basePoints, topPoints, transform) — filled area between two smooth curves
// drawColumnLine(ctx, points, transform) — step function centered on each point
// drawColumnBand(ctx, basePoints, topPoints, transform) — filled rectangles per point

// --- Viz Data (extracted from RouteAnalysesManifest for one model) ---
interface VizRouteData {
  points: VizPoint[];            // One per route point, in order
  cruiseAltitudeFt: number;
  totalDistanceNm: number;
  waypointMarkers: { distanceNm: number; icao: string }[];
}

interface VizPoint {
  distanceNm: number;
  time: string;                  // ISO time
  altitudeLines: {               // Single-altitude metrics
    freezingLevelFt: number | null;
    minus10cLevelFt: number | null;
    minus20cLevelFt: number | null;
    lclAltitudeFt: number | null;
    lfcAltitudeFt: number | null;
    elAltitudeFt: number | null;
  };
  cloudLayers: { baseFt: number; topFt: number; coverage: string; meanDd: number }[];
  icingZones: { baseFt: number; topFt: number; risk: string; type: string }[];
  catLayers: { baseFt: number; topFt: number; risk: string }[];
  inversions: { baseFt: number; topFt: number; strengthC: number }[];
  convectiveRisk: string;        // NONE | LOW | MODERATE | HIGH | EXTREME
  // Map-specific scalars
  cloudCoverTotalPct: number;
  cloudCoverLowPct: number;
  headwindKt: number;
  crosswindKt: number;
  capeSurfaceJkg: number;
  worstModelAgreement: string;   // GOOD | MODERATE | POOR
}

// --- Map Metric ---
interface MapMetricDef {
  id: string;
  name: string;
  getValue(point: VizPoint): number | null;
  scale: ColorScale | SizeScale;
}
```

### 6.3 Dependencies

| Dependency | Purpose | Size | Integration |
|-----------|---------|------|-------------|
| Leaflet | Route map | ~40KB gz | CDN `<script>` + `<link>` in briefing.html |
| (none) | Cross-section | 0 | Native Canvas 2D API |

No new npm dependencies. Leaflet loaded via CDN. Canvas is native browser API.

Leaflet types: `@types/leaflet` as dev dependency for TypeScript, or declare a minimal ambient type.

### 6.4 Data Flow

```
Store (routeAnalyses, selectedModel, vizSettings)
  ↓
data-extract.ts: extractVizData(routeAnalyses, model) → VizRouteData
  ↓
  ├→ CrossSectionRenderer.render(canvas, vizData, enabledLayers)
  │     ↓
  │     CoordTransform setup
  │     ↓
  │     for each enabledLayer: layer.render(ctx, transform, vizData)
  │     ↓
  │     Axes, waypoint markers, hover overlay
  │
  └→ RouteMapRenderer.render(map, vizData, colorMetric, widthMetric)
        ↓
        for each segment: compute color + width from metrics
        ↓
        L.Polyline segments + waypoint markers + legend

Controls → store.setVizSettings() → triggers re-render
Slider ↔ Cross-section ↔ Map (bidirectional sync via store.setSelectedPoint)
```

### 6.5 State Management

Add to briefing store:

```typescript
type VizLayout = 'cross-section' | 'map' | 'split';
type RenderMode = 'smooth' | 'columns';

interface VizSettings {
  layout: VizLayout;                        // which panels are visible
  renderMode: RenderMode;                   // smooth splines vs column-based
  enabledLayers: Record<string, boolean>;   // layer.id → enabled
  mapColorMetric: string;                   // metric id
  mapWidthMetric: string;                   // metric id
}
```

Default `enabledLayers` from each layer's `defaultEnabled`. Persisted to localStorage so user preferences survive page reloads.

### 6.6 Rendering Strategy

- **Canvas**: Re-render fully on any data/setting change. No incremental updates. At ~20 points with ~5 layers, full render takes <5ms — no optimization needed.
- **Retina**: Set `canvas.width = container.width * devicePixelRatio`, then `ctx.scale(dpr, dpr)`.
- **Resize**: `ResizeObserver` on container → re-render on size change.
- **Map**: Leaflet handles its own rendering. We clear and recreate route layers on data/metric change.

---

## 7. Implementation Phases

### Phase 1: Cross-Section Foundation
- [ ] Set up `visualization/` directory structure and types
- [ ] Implement `CoordTransform` with distance↔X and altitude↔Y mapping
- [ ] Implement `data-extract.ts` to transform `RouteAnalysesManifest` → `VizRouteData`
- [ ] Implement axis renderer (grid, altitude labels, distance labels, pressure ticks)
- [ ] Implement canvas setup in `briefing-ui.ts` (responsive, retina-aware)
- [ ] Add canvas element to `briefing.html` above the slider

### Phase 2: Cross-Section Layers
- [ ] Implement layer interface and registry
- [ ] **Temperature lines**: freezing level, −10°C, −20°C
- [ ] **Cloud bands**: filled areas with coverage-based opacity
- [ ] **Icing bands**: filled areas with risk-based color
- [ ] **Stability lines**: LCL, LFC, EL
- [ ] **Reference lines**: cruise altitude, waypoint vertical markers
- [ ] **CAT bands**: turbulence zone fills
- [ ] **Inversion bands**: warm-colored fills
- [ ] **Convective background**: per-segment risk coloring

### Phase 3: Cross-Section Interaction
- [ ] Hover: vertical crosshair + tooltip with metric values
- [ ] Click: select route point (sync with slider)
- [ ] Slider sync: highlight position on cross-section when slider moves
- [ ] Smooth cursor tracking

### Phase 4: Controls
- [ ] Control panel HTML + CSS
- [ ] Layer toggle checkboxes with grouped layout
- [ ] Model selector integration (extend existing dropdown)
- [ ] Wire controls → store → re-render
- [ ] Persist settings to localStorage

### Phase 5: Route Map
- [ ] Add Leaflet to briefing.html (CDN)
- [ ] Map renderer: tile layer + auto-fit to route bounds
- [ ] Segment rendering with per-segment color
- [ ] Segment rendering with per-segment width
- [ ] Color + width scale legend
- [ ] Map metric selector dropdowns
- [ ] Waypoint markers with labels

### Phase 6: Map Interaction & Polish
- [ ] Hover tooltips on segments
- [ ] Click → select point (sync with slider + cross-section)
- [ ] Responsive layout (side-by-side desktop, stacked mobile)
- [ ] Loading states (show placeholder while data loads)
- [ ] Performance profiling and optimization if needed

### Future Enhancements (not in initial scope)
- Derived-level contour fills (temperature field, humidity field — like true GRAMET)
- Wind barbs at pressure levels along the cross-section
- Animation: play through time (for multi-pack history)
- Touch gestures for mobile (pinch-zoom on cross-section)
- Map tile selector (OSM / terrain / satellite)
- Export cross-section as PNG

---

## 8. Decisions (Resolved)

### D1: Layout — Modal (three modes)
**Decision**: User toggles between Cross-Section only (full width), Map only (full width), or Split (50/50 side-by-side). Default: Cross-Section. On narrow screens, Split stacks vertically.

### D2: Band rendering — Both smooth and columns
**Decision**: Implement both render modes. Smooth (monotone cubic spline) is default — with ~20nm spacing, columns would look very boxy. Columns available via toggle for comparison and when discrete representation is preferred. The render mode is a single toggle that affects all layers (lines and bands alike).

**Smooth interpolation approach** (not much harder than straight lines):
- Lines: Monotone cubic spline (Fritsch-Carlson) — prevents overshoot, preserves monotonicity between points. Canvas `bezierCurveTo()` with computed control points.
- Bands: Same spline for both base and top curves. Fill the area between with `ctx.fill()`. Layer matching between points handles appearing/disappearing bands via taper.

### D3: Y-axis range — flight_ceiling_ft + margin
**Decision**: SFC to `flight_ceiling_ft + 2000ft`. All GA-relevant weather is in this range.

### D4: Multiple cloud/icing layers — natural array iteration
Each layer in the array renders as its own filled region. Multiple layers at the same point stack visually.

### D5: Leaflet types — @types/leaflet
Dev dependency only, zero runtime cost, much better DX.

### D6: Missing data — null gaps
Lines have gaps where values are `null`. Bands only drawn where they exist. Data extraction normalizes this.

---

## 9. Key Design Principles

1. **Layer composability**: Each metric is an independent, self-contained layer. Adding a new metric = adding one file. No changes to the renderer core.

2. **Data extraction separation**: Raw analysis data → viz-ready data is a distinct step (`data-extract.ts`). The renderer never touches `RouteAnalysesManifest` directly. This makes testing easy and keeps the renderer model-agnostic.

3. **Shared scales**: Color and size scales are reusable functions in `scales.ts`. Same risk color scale used in cross-section bands, map segments, and legend.

4. **Minimal dependencies**: Canvas for cross-section (zero deps), Leaflet for map (CDN, ~40KB). No heavy charting framework.

5. **Progressive enhancement**: Visualization section only appears when route analyses are available. Falls back gracefully to the existing text-based display.

6. **Sync over duplication**: Cross-section, map, and slider share state through the store. No separate data copies.
