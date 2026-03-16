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
controls/panel.ts  (layer toggles, model selector, layout, map metric selectors, Windy link)
scales.ts          (shared color/opacity functions for all three renderers)
```

**Two canvases:** main (layers) + overlay (crosshair/selection indicator). Overlay redraws on mouse move without re-rendering expensive layers.

## Layers

Rendered back-to-front in this order:

Rendering order: **bands → terrain (covers below-surface artifacts) → lines → reference**.

| Layer | Name | Group | File | Default | Description |
|-------|------|-------|------|---------|-------------|
| Convective BG | Convective | convection | `convective-bg.ts` | on | Tower columns LCL→EL, hatching, CB labels, anvil strip |
| Cloud bands | DD Layers | clouds | `cloud-bands.ts` | on | Hatch lines by coverage (SCT/BKN/OVC line widths) |
| NWP cloud bands | NWP Layers | clouds | `nwp-cloud-bands.ts` | on | Server-computed NWP cloud layers (GRIB or synthesized), blue-tinted with proportional hatching |
| Icing bands | Ogimet-DD | icing | `icing-bands.ts` | on | Colored by risk (light→severe), DD-attenuated Ogimet index |
| Ogimet-NWP bands | Ogimet-NWP | icing | `icing-ogimet-nwp-bands.ts` | off | NWP cloud-scaled Ogimet index |
| SFIP bands | SFIP-NWP | icing | `sfip-bands.ts` | off | Fuzzy-logic SFIP icing index |
| CAT bands | CAT | turbulence | `cat-bands.ts` | on | Orange-red bands by Richardson number |
| Inversion bands | Inversions | turbulence | `inversion-bands.ts` | on | Purple bands by strength |
| Terrain fill | Terrain | terrain | `terrain-fill.ts` | on | SRTM elevation, earth-tone gradient (drawn after bands to mask below-surface artifacts) |
| Freezing level | 0°C | temperature | `temperature-lines.ts` | on | Blue dashed line (0°C) |
| −10°C level | −10°C | temperature | `temperature-lines.ts` | off | Cyan dashed line |
| −20°C level | −20°C | temperature | `temperature-lines.ts` | off | Navy dashed line |
| LCL | LCL | stability | `stability-lines.ts` | off | Green dotted (lifting condensation) |
| LFC | LFC | stability | `stability-lines.ts` | off | Orange dotted (level of free convection) |
| EL | EL | stability | `stability-lines.ts` | off | Red dotted (equilibrium level) |
| Cruise altitude | Cruise | reference | `reference-lines.ts` | on | Dark gray dashed + flight ceiling (purple) |

## Render Mode

All layers use **smooth** rendering: monotone cubic spline (Fritsch-Carlson) interpolation between route points. The columns render mode was removed — smooth rendering is always used for terrain, bands, and lines alike.

## Layer Groups & Compact Mode

The **clouds** and **icing** groups support multiple methods with a preferred method setting:

```typescript
PREFERRED_METHOD_LAYER = {
  clouds: { dd: 'cloud-bands', nwp: 'nwp-cloud-bands' },
  icing:  { ogimet_dd: 'icing-bands', ogimet_nwp: 'icing-ogimet-nwp-bands', sfip_nwp: 'sfip-bands' }
}
```

**Compact mode** (`VizSettings.compactLayers`): When enabled, only the user's preferred method layer is shown per group; alternative layers are hidden. In full mode, all layers remain toggleable. Group headers show an info button (ⓘ) explaining the methods available.

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
- **Theme-aware canvas colors** — all cross-section colors (sky, clouds, icing, lines, terrain) come from the active `CrossSectionTheme` via `getActiveTheme()`; renderers listen for `theme-changed` events to re-render automatically

## Cross-Section Theme System

Switchable visual themes for the cross-section via `cross-section/theme.ts`. Separate from the page-level dark/light theme.

**Architecture:**
- `CrossSectionTheme` interface controls all visualization colors: sky background, axes, terrain, temperature/stability/reference line styles, cloud colors + hatch config, icing/CAT/convective risk colors, inversion appearance
- Themes registered in `THEMES` map, accessed via `getActiveTheme()` / `setActiveTheme(id)`
- Theme selector dropdown + preview button in the controls panel (both standard and compare mode)
- `'theme-changed'` window event triggers re-renders in all renderers

**Available themes:**
- `'standard'` — Light blue sky (#7395DB), default, designed for readability
- `'high-contrast'` — Dark navy sky (#1B3060), optimized for visibility in varying lighting

**Cloud hatch patterns:**
- Clouds rendered with horizontal hatch lines instead of solid fills, improving readability
- Fixed-grid alignment (`hatchGridPx = 8px`) so hatching aligns across adjacent bands
- DD clouds: line width per coverage class (`sct: 2, bkn: 5, ovc: 8` — solid at full grid width)
- NWP clouds: line width proportional to coverage percentage (`gridPx * coverPct/100`)
- Core drawing primitive: `drawBandHatch()` in `layers/base.ts` clips hatch lines to band shape

**Theme preview:** `theme-preview.ts` renders a popup canvas showing all visual elements (NWP clouds, DD clouds, icing, CAT, convective tower, inversion, temperature/stability/reference lines, terrain) for the selected theme.

**Theme-aware legends:** `layer-legends.ts` generates legend entries dynamically from the active theme. Cloud legend swatches use CSS `repeating-linear-gradient` to replicate canvas hatch patterns.

## Dark / Light / System Theme (Page-Level)

Three-way page theme support via `web/ts/theme.ts` (separate from cross-section themes):

- **Persistence**: `localStorage('wb_theme')` → `'light' | 'dark' | 'system'` (default: `system`)
- **Resolution**: `system` checks `matchMedia('(prefers-color-scheme: dark)')` with a live listener
- **Application**: sets `document.documentElement.dataset.theme` to `'light'` or `'dark'`; CSS custom properties in `[data-theme="dark"]` override all colors
- **FOUC prevention**: inline `<script>` in every HTML `<head>` (before stylesheets) reads localStorage and sets `data-theme` before first paint
- **Toggle UI**: 3-segment button injected into `.header-right` by `initTheme()`
- **Canvas re-rendering**: `CrossSectionRenderer` and `RouteGraphRenderer` listen for `theme-changed` custom event to re-render with updated colors
- **Map tiles**: `RouteMapRenderer` switches between OSM (light) and CartoDB Dark Matter (dark) tiles on theme change
- **Server-generated images**: Skew-T and hodograph PNGs get CSS `filter: invert(0.88) hue-rotate(180deg)` in dark mode; GRAMET images are left unchanged

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

The NWP cloud bands layer (`nwp-cloud-bands.ts`) renders pre-computed NWP cloud layers from the backend:

- **Server-computed layers**: All heuristic narrowing (DD envelope, inversion capping, LCL) now happens in Python (`clouds.py:_synthesize_nwp_layers()`). The frontend receives ready-to-render `EnhancedCloudLayer` objects with base/top boundaries for all models.
- **Layer matching**: Adjacent points' NWP cloud layers are matched by altitude overlap (same approach as DD cloud bands). Matched pairs are rendered as smooth bands; unmatched layers taper to midpoint.
- **Coverage-proportional fill**: Blue-tinted fill with opacity proportional to coverage (OVC→90%, BKN→65%, SCT→35%) via `coverageToPct()`.
- **Hatching**: Horizontal hatch lines with width proportional to coverage percentage, matching DD cloud band style.
- **Source tagging**: Tooltip shows source context — "(synth)" for synthesized layers, no tag for GRIB layers.
- **Single-point fallback**: When only one route point exists, draws column bands instead of smooth bands.
- Renders on sky-blue background (set in `axes.ts`)

## Layer Legends

`visualization/layer-legends.ts` provides a unified legend system for all layers:

- `LegendEntry`: `{ label, color, meaning, hatchStyle? }` — human-readable, visual, and contextual
- Colors pulled dynamically from `getActiveTheme()` at render time (theme-aware, not cached)
- Cloud legend swatches include CSS `repeating-linear-gradient` hatch overlay matching canvas rendering
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

`controls/panel.ts` renders checkboxes grouped by category (terrain, temperature, clouds, icing, stability, turbulence, convection, reference). Layers with a `metricId` get an info button that opens the layer info popup. The **Clouds** and **Icing** group headers show an info button explaining the available methods. A **theme selector dropdown** and **preview button** appear in the toolbar (both standard and compare mode) for switching cross-section themes.

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

The cross-section toolbar includes a dynamic Windy link ("Open selected location") for the currently selected route point and model:

- **URL builder** (`utils.ts: buildWindyUrl()`): constructs `https://www.windy.com/{lat}/{lon}/{model}/meteogram?...` URLs
- **Model mapping**: GFS → `gfs`, ICON → `icon`, others → ECMWF (Windy's default)
- **Updates dynamically** when the user changes route point or model selection
- Placed next to the model selector in the cross-section toolbar

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
