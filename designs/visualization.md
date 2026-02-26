# Visualization System

> Three synchronized visualizations: canvas cross-section, canvas route graph, and Leaflet route map

## Intent

Provide interactive visual analysis of weather along a flight route through three coordinated views: a vertical cross-section (clouds, icing, turbulence, terrain), a scalar route graph (wind, temperature, CAPE), and a geographic route map (metric-colored segments on a Leaflet map). All views share the same `VizRouteData` and synchronize hover/selection through the Zustand store.

## Layout Modes

The visualization supports three layout modes (persisted to localStorage):
- **cross-section**: Vertical profile only (cross-section + route graph)
- **map**: Geographic view only (Leaflet route map)
- **split**: Side-by-side (cross-section left, map right)

Toggled via buttons in the control panel. Components are created/destroyed on layout change.

## Cross-Section

Vertical cross-section showing weather layers along the route. All data comes from the `RouteAnalysesManifest` (per-model sounding analysis at each route point) and `ElevationProfile`.

## Architecture

```
briefing-store (Zustand)
  ├── routeAnalyses: RouteAnalysesManifest
  ├── elevationProfile: ElevationProfile
  └── vizSettings: VizSettings (persisted to localStorage)
        ↓
data-extract.ts  → extractVizData() → VizRouteData
        ↓
┌───────────────────────┬──────────────────────┬──────────────────────┐
│  CrossSectionRenderer │  RouteGraphRenderer  │  RouteMapRenderer    │
│  ├── axes.ts          │  ├── axes.ts         │  ├── renderer.ts     │
│  ├── layer-registry   │  ├── metrics.ts      │  ├── metrics.ts (14) │
│  ├── layers/*.ts (13) │  ├── interaction.ts  │  ├── segment-style   │
│  └── interaction.ts   │  └── constants.ts    │  ├── interaction.ts  │
│                       │                      │  ├── altitude-slider │
│                       │                      │  └── legend.ts       │
└───────────────────────┴──────────────────────┴──────────────────────┘
        ↓                       ↓                       ↓
       Hover sync (via callbacks in briefing-main.ts)
        ↓
controls/panel.ts  (layer toggles, model selector, render mode, layout, map metric selectors)
scales.ts          (shared color/opacity functions for all three renderers)
```

**Two canvases:** main (layers) + overlay (crosshair/selection indicator). Overlay redraws on mouse move without re-rendering expensive layers.

## Layers

Rendered back-to-front in this order:

Rendering order: **bands → terrain (covers below-surface artifacts) → lines → reference**.

| Layer | Group | File | Default | Description |
|-------|-------|------|---------|-------------|
| Convective BG | convection | `convective-bg.ts` | on | Tower columns LCL→EL, hatching, CB labels, anvil strip |
| Cloud bands | clouds | `cloud-bands.ts` | on | Opacity from coverage (SCT/BKN/OVC) |
| NWP cloud bands | clouds | `nwp-cloud-bands.ts` | on | NWP cloud cover at ICAO altitude bands (low/mid), terrain-aware |
| Icing bands | icing | `icing-bands.ts` | on | Colored by risk (light→severe) |
| CAT bands | turbulence | `cat-bands.ts` | on | Orange-red bands by Richardson number |
| Inversion bands | turbulence | `inversion-bands.ts` | on | Purple bands by strength |
| Terrain fill | terrain | `terrain-fill.ts` | on | SRTM elevation, earth-tone gradient (drawn after bands to mask below-surface artifacts) |
| Freezing level | temperature | `temperature-lines.ts` | on | Blue dashed line (0°C) |
| −10°C level | temperature | `temperature-lines.ts` | off | Cyan dashed line |
| −20°C level | temperature | `temperature-lines.ts` | off | Navy dashed line |
| LCL | stability | `stability-lines.ts` | off | Green dotted (lifting condensation) |
| LFC | stability | `stability-lines.ts` | off | Orange dotted (level of free convection) |
| EL | stability | `stability-lines.ts` | off | Red dotted (equilibrium level) |
| Cruise altitude | reference | `reference-lines.ts` | on | Dark gray dashed + flight ceiling (purple) |

## Render Modes

- **Smooth**: Monotone cubic spline (Fritsch-Carlson) interpolation between route points. Used for terrain fill and altitude lines.
- **Columns**: Step function — each route point's data extends halfway to neighbors. Used for bands (clouds, icing, CAT).

Controlled via `VizSettings.renderMode`, toggled from the control panel.

## Data Flow

1. `extractVizData(manifest, elevationProfile, model)` transforms server data into `VizRouteData`
2. For each route point: extracts altitude lines, cloud layers, icing zones, CAT layers, inversions, convective risk from the selected model's `SoundingAnalysis`
3. Terrain profile mapped from `ElevationProfile.points` into `TerrainPoint[]`
4. `VizRouteData` also carries waypoint markers, cruise altitude, flight ceiling, total distance

## Key Types

```typescript
interface VizRouteData {
  points: VizPoint[];           // per route point analysis data
  cruiseAltitudeFt: number;
  ceilingAltitudeFt: number;    // actual ceiling from route config
  flightCeilingFt: number;      // Y-axis max = max(ceiling, cruise) + 5000
  totalDistanceNm: number;
  waypointMarkers: WaypointMarker[];
  terrainProfile: TerrainPoint[] | null;
}

interface CrossSectionLayer {
  id: string; name: string; group: LayerGroup; defaultEnabled: boolean;
  render(ctx, transform, data, mode): void;
}

interface CoordTransform {
  distanceToX(nm): number;  altitudeToY(ft): number;
  xToDistance(x): number;   yToAltitude(y): number;
  plotArea: PlotArea;
}
```

## Key Choices

- **Canvas over SVG** — hundreds of data points and complex fills; canvas is faster and doesn't bloat the DOM
- **Layer registry pattern** — each layer is self-contained with `render()` method; registry controls order and defaults
- **Terrain drawn mid-stack** — after weather bands but before lines, masking below-surface artifacts
- **Separate overlay canvas** — hover/crosshair redraws cheaply without re-rendering all layers
- **ResizeObserver** — responsive sizing with device pixel ratio handling for crisp rendering
- **Clip to plot area** — all layer renders are clipped so bands/fills don't overflow axes
- **Monotone cubic for terrain** — Fritsch-Carlson tangents prevent overshoot (important for elevation data)
- **Theme-aware canvas colors** — axes, grid lines, borders, and backgrounds read from `isDarkTheme()` / `cssVar()` at render time; both renderers listen for `theme-changed` events to re-render automatically

## Dark / Light / System Theme

Three-way theme support via `web/ts/theme.ts`:

- **Persistence**: `localStorage('wb_theme')` → `'light' | 'dark' | 'system'` (default: `system`)
- **Resolution**: `system` checks `matchMedia('(prefers-color-scheme: dark)')` with a live listener
- **Application**: sets `document.documentElement.dataset.theme` to `'light'` or `'dark'`; CSS custom properties in `[data-theme="dark"]` override all colors
- **FOUC prevention**: inline `<script>` in every HTML `<head>` (before stylesheets) reads localStorage and sets `data-theme` before first paint
- **Toggle UI**: 3-segment button (☀ ◐ ☾) injected into `.header-right` by `initTheme()`
- **Canvas re-rendering**: `CrossSectionRenderer` and `RouteGraphRenderer` listen for `theme-changed` custom event to re-render with updated colors
- **Map tiles**: `RouteMapRenderer` switches between OSM (light) and CartoDB Dark Matter (dark) tiles on theme change
- **Server-generated images**: Skew-T and hodograph PNGs get CSS `filter: invert(0.88) hue-rotate(180deg)` in dark mode; GRAMET images are left unchanged (work on both backgrounds)
- **Cross-section sky background**: stays `#87CEEB` in both themes (contextual sky color); grid lines on it remain white

## Convective Tower Rendering

The convective background layer (`convective-bg.ts`) is the most complex:
- **Marginal risk skipped**: points with `convectiveRisk === 'marginal'` skip tower rendering entirely (marginal CAPE <100 J/kg produces misleading visual towers for noise-level instability)
- **Tower columns**: drawn from LCL → estimated tower top for each route point with risk ≥ LOW
- **Tower top estimation**: uses thermodynamic EL if available and reliable (>3000ft above LCL), else estimates from `max(freezingLevel, −10°C, −20°C)` altitude lines as fallbacks
- **Anvil strip**: 500ft strip at tower top (darker shade)
- **Hatching**: diagonal lines on HIGH/EXTREME risk
- **CB labels**: "CB" text at tower top for HIGH+ risk
- **Color gradient**: marginal (light green) → low (yellow) → moderate (orange) → high (red) → extreme (purple)

## Shared Interaction Helpers (`interaction-utils.ts`)

Common utilities extracted from cross-section and route-graph to avoid duplication:
- `getCanvasX()` — mouse X relative to canvas element
- `findNearestPointIndex()` — find closest route point by distance
- `chooseDistanceTickInterval()` — smart axis tick spacing
- `ensureTooltip()`, `positionTooltip()`, `hideTooltip()` — tooltip lifecycle
- `findNearbyWaypoint()` — find waypoint within 1nm
- `cssVar(name, fallback)` — read a CSS custom property from document root
- `isDarkTheme()` — check if `data-theme="dark"` is set on `<html>`

Both cross-section and route-graph interaction modules import from this shared utility.

## Interaction

- **Hover**: vertical crosshair line follows mouse, shows distance/time at cursor
- **Click**: selects closest route point, highlights with indicator on overlay canvas
- **Tooltip**: shows waypoint name (if named) + distance + altitude at hover position

## NWP Cloud Bands

The NWP cloud bands layer (`nwp-cloud-bands.ts`) renders numerical weather prediction cloud cover:

- **Three-tier altitude model**: Low (terrain → 6500ft), mid (6500ft → 20000ft), high (20000ft+)
- **Per-band hybrid rendering**: Each band independently uses model diagnostics (GFS base/top) when available, or falls back to sounding-derived heuristics (LCL, inversions, cloud envelope) when boundaries are missing (e.g. ICON-EU provides cover % but not base/top)
- **Sounding-corroborated collapse**: For mid/high bands without diagnostic boundaries, if the sounding finds NO cloud layers in that altitude range, the band collapses to zero height (prevents false fills from NWP-only coverage)
- **Heuristic narrowing** (when diagnostics unavailable): LCL raises band floor, inversions (≥2°C strength) cap band top, sounding cloud envelopes constrain bounds
- **Terrain-aware**: Interpolates terrain elevation at each point to set low band base dynamically
- **Dual render modes**: Columns (discrete NWP grid) or Smooth (interpolated trapezoids)
- **Opacity capping**: Cloud cover % converted to opacity with `min(0.7, pct/100 * 0.8)` for readability
- **Model-run consistency**: When GRIB diagnostics are available, they override Open-Meteo cloud cover values so all cloud data comes from the same model run
- Renders on sky-blue background (set in `axes.ts`)

## Layer Legends

`visualization/layer-legends.ts` provides a unified legend system for all layers:

- `LegendEntry`: `{ label, color, meaning }` — human-readable, visual, and contextual
- Colors imported from `scales.ts` (single source of truth, not duplicated)
- Risk-based legends for icing, CAT, convective bands; METAR-style for cloud bands; percentage-based for NWP clouds
- `getLayerLegend(layerId)` returns legend or null
- Displayed in info popups via `renderLayerLegend()` in metrics-helper.ts

## Info Popup & Metrics UI System

Shared modal infrastructure (`components/info-popup.ts`) used by three popup types:

- **Metric info** (`showMetricInfo`): Full explanation with threshold scale bar, vibe, goal, limitations
- **Layer info** (`showLayerInfo`): Metric details + color legend for the specific layer
- **Advisory info** (`showPopupContent`): Custom HTML (used by advisory system)

Keyboard (ESC) and click-outside close the popup.

### Metrics Catalog (`data/metrics-catalog.json`)

40+ metrics (including `sounding_ceiling_ft` and `nwp_ceiling_ft` for Key Altitudes) with catalog-driven contextual help:

- `vibe`: One-liner analogy (e.g., "The atmosphere's battery level" for CAPE)
- `primary_goal`, `best_used_for`, `limitations`: Aviation-focused guidance
- `theory`: Mathematical/physical explanation with formulas
- `wikipedia`: External reference link
- `llm_prompt`: Context for "Discuss with AI" feature
- Thresholds with `min/max/label/risk/meaning` tuples

### "Discuss with AI" Buttons

Info popups include buttons for Claude, ChatGPT, and Gemini that copy a context-aware prompt to clipboard:
- Prompt includes metric name + catalog context + aviation relevance
- Toast notification confirms copy
- Enabled via `llm_prompt` field in metrics catalog

### Layer Control Panel

`controls/panel.ts` renders checkboxes grouped by category (terrain, temperature, clouds, icing, stability, turbulence, convection, reference). Layers with a `metricId` get an info button (ⓘ) that opens the layer info popup.

## Unified Atmospheric Profile Table

The briefing UI renders a single "Atmospheric Profile" table (`renderAtmosphericProfile()` in `briefing-ui.ts`) that merges 5 previously separate altitude tables:

1. Cloud layers (coverage, dewpoint depression, temperature)
2. Icing zones (severity, type, wet-bulb, RH, Ogimet index)
3. Inversions (strength, surface-based flag)
4. CAT risk layers
5. Strong vertical motion

**How it works:**
- Collects all transition altitudes across models from `VerticalRegime` data
- Builds a unified altitude column (top-down) with per-model columns
- Each cell shows multi-line content: cloud status, icing risk/type, inversion label, and diagnostic values
- Cruise icing banner displayed at top when applicable
- Uses `AltitudeAdvisories.regimes` per model (dict of model → list of `VerticalRegime`)

## Windy Meteogram Link

The briefing page includes a dynamic Windy link that opens the meteogram for the currently selected route point and model:

- **URL builder** (`utils.ts: buildWindyUrl()`): constructs `https://www.windy.com/{lat}/{lon}/{model}/meteogram?...` URLs
- **Model mapping**: GFS → `gfs`, ICON → `icon`, others → ECMWF (Windy's default)
- **Updates dynamically** when the user changes route point or model selection
- Displayed as inline text in the external links area of the briefing

## Display Mode (Compact / Full Details)

The briefing UI supports two display modes (persisted to localStorage via `VizSettings.displayMode`):
- **Compact**: Shows essential info only — hides sounding analysis, model comparison, secondary advisories (model confidence), and shows only synoptic + trend in the synopsis
- **Full Details**: Shows everything including all analysis tables and advisory details

Toggled via a button pair in the briefing page header. The mode is `'compact' | 'full'` (backward compat maps old `'annotated'` → `'full'`).

## Freshness Bar

The freshness bar (`renderFreshnessBar()` in `briefing-ui.ts`) shows data age and model basis:

- **Basis line:** `"Based on GFS 12Z, ECMWF 00Z, ..."` from `pack.model_init_times`
- **GRIB annotation:** When `pack.grib_init_times[model]` differs from `pack.model_init_times[model]`, displays `"GFS 12Z (GRIB 18Z)"` — indicates Open-Meteo data from one cycle, GRIB enrichment from another
- **States:** current (muted), stale (amber), refreshing (animated dots spinner)
- **Force refresh link:** shown for admins when data is stale

## Route Graph

A separate canvas-based chart rendered below the cross-section for scalar weather metrics along the route. See [route-graph.md](./route-graph.md) for full design.

- **X-axis aligned** with cross-section (same `distanceToX` transform and margins)
- **Dual Y-axes**: left and right metrics independently selectable
- **7 metrics**: headwind, crosswind, temperature, precipitation, cloud cover, CAPE, freezing level
- **Render types**: line (monotone cubic spline) and bar charts
- **State**: `VizSettings` extended with `routeGraphVisible`, `routeGraphLeftMetric`, `routeGraphRightMetric`, persisted to localStorage
- **Controls**: dropdown selectors below the graph, integrated into the controls panel

## Route Map

Leaflet-based geographic visualization showing weather metrics as colored route segments on an interactive map. Located in `web/ts/visualization/route-map/`.

### Components

| File | Purpose |
|------|---------|
| `renderer.ts` | Leaflet map lifecycle: lazy init, segment polylines, waypoint markers, highlight |
| `metrics.ts` | 14-metric registry: `MapMetric` objects with `getValue`, `getColor`, `getWidth`, `formatValue` |
| `segment-style.ts` | Pure function: `computeSegmentStyles()` → `{color, weight}[]` from metric + points |
| `interaction.ts` | Hover (highlight + tooltip + sync), click (select point), event attach/detach |
| `altitude-slider.ts` | Range input for level-dependent metrics (0 → ceiling, 500ft steps, FL labels) |
| `legend.ts` | DOM gradient bar with color stops and labels |

### Metric Registry (14 metrics)

| Metric | Alt-Dependent | Color Scale |
|--------|:---:|-------------|
| cloud-cover-total | | Gray 0-100% |
| cloud-cover-low | | Gray 0-100% |
| convective-risk | | Green→Yellow→Red discrete |
| headwind | | Green (tailwind) ↔ Red (headwind) diverging |
| crosswind | | White→Purple |
| cape | | Green→Yellow→Red continuous |
| freezing-level | | Dark→Light blue |
| nwp-ceiling | | Purple (LIFR)→Red (IFR)→Amber (MVFR)→Green (VFR) |
| temperature | | Blue→White→Red diverging |
| model-agreement | | Green/Orange/Red discrete |
| icing-risk-at-level | Yes | Green→Yellow→Orange→Red |
| sfip-at-level | Yes | Green→Yellow→Orange→Red |
| cat-risk-at-level | Yes | Green→Yellow→Orange→Red |
| cloud-at-level | Yes | Gray 0-100% |

Altitude-dependent metrics use helpers (`worstRiskAtAlt()`, `sfipAtAlt()`, `cloudAtAlt()`) to find the relevant value at the slider's flight level.

### Rendering

- **Segments**: One `L.polyline` per adjacent point pair, colored/sized by metric via `computeSegmentStyles()`. Midpoint averaging of endpoint values for stable visuals.
- **Waypoints**: Circle markers (theme-aware colors) with ICAO tooltip on hover.
- **Tiles**: OSM standard (light) or CartoDB Dark Matter (dark), switched via `theme-changed` event.
- **Highlight**: Temporary weight increase (+3px) on hovered segment.
- **Auto-fit**: Bounds fit route with 30px padding on data load.

### Hover Sync

All three visualizations synchronize through callbacks in `briefing-main.ts`:
- Map hover → cross-section `renderOverlay(x)` + route-graph overlay
- Cross-section/route-graph hover → map `highlightSegment(index)`
- Click in any view → `setSelectedPoint(index)` in store → all views update

### Store State

`VizSettings` (persisted to localStorage) includes:
- `layout`: `'cross-section' | 'map' | 'split'`
- `mapColorMetric`: default `'icing-risk-at-level'`
- `mapWidthMetric`: default `'cloud-cover-total'`
- `mapAltitudeFt`: for level-dependent metrics (default = cruise altitude)

### Key Choices

- **Leaflet over Canvas** — built-in pan/zoom, waypoint tooltips, basemap tiles; simpler for geographic rendering
- **Opaque map colors** — better visibility on complex basemap vs. semi-transparent cross-section bands
- **Metric-driven styling** — no hardcoded thresholds; all defined in `MapMetric` objects
- **Debounced altitude slider** (50ms) — prevents expensive re-renders during drag
- **Segment recoloring** — segments cleared and recreated on metric change (clean event handler lifecycle)

### Shared Scales Module (`scales.ts`)

Single source of truth for all color/opacity functions used by cross-section bands, route graph, and route map:
- Risk colors: icing (cornflower→red), CAT (amber→red), convection (gray→dark red)
- Map colors: `riskMapColor()`, `cloudCoverMapColor()`, `headwindMapColor()`, `capeMapColor()`, `freezingLevelMapColor()`, `ceilingMapColor()`, `temperatureMapColor()`, `crosswindMapColor()`, `agreementMapColor()`
- Width: `linearWidth(value, max, minW, maxW)` — linear interpolation for segment width
- Cloud opacity: modulated by coverage so SCT layers render lighter than OVC

## Gotchas

- Y-axis is altitude in feet (0 at bottom), not pressure — `altitudeToPressureHpa()` in scales.ts for any pressure conversions
- `VizPoint.altitudeLines` values can be `null` (model doesn't provide that level)
- Convective tower top fallback logic is deliberately conservative — prefers undersized towers over misleading oversized ones
- Layer rendering must handle empty arrays gracefully (no data for that layer/model)

## References

- Data models: [data-models.md](./data-models.md) (RouteAnalysesManifest, ElevationProfile)
- Fetch layer: [fetch.md](./fetch.md) (elevation.py, route_walk.py)
- Analysis: [analysis.md](./analysis.md) (sounding analysis pipeline)
- Historical plans: [archive/visualization-plan.md](./archive/visualization-plan.md), [archive/elevation-profile-plan.md](./archive/elevation-profile-plan.md)
