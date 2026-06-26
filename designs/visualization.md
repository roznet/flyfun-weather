# Visualization System

> Four synchronized visualizations: canvas cross-section, canvas route graph, Leaflet route map, and dynamic canvas Skew-T

## Intent

Provide interactive visual analysis of weather along a flight route through four coordinated views: a vertical cross-section (clouds, icing, turbulence, terrain), a scalar route graph (wind, temperature, CAPE), a geographic route map (metric-colored segments on a Leaflet map), and a dynamic Skew-T log-P diagram (per-point sounding inspection with overlay bands and side panels). The first three share `VizRouteData` and synchronize hover/selection through the Zustand store. The Skew-T loads on-demand when a route point is clicked, fetching its own `SoundingProfileData`, and links hover altitude with the cross-section.

See [skewt-canvas.md](./skewt-canvas.md) for the full Skew-T design.

## Layout Modes

The visualization supports four layout modes (`VizLayout` in `visualization/types.ts`, persisted to localStorage):
- **cross-section**: Vertical profile only (cross-section + route graph)
- **map**: Geographic view only (Leaflet route map)
- **split**: Side-by-side (cross-section left, map right)
- **compare**: Single layer across all models simultaneously (see [Compare Mode](#compare-mode) below)

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
│  ├── layer-registry   │  ├── metrics.ts (12) │  ├── metrics.ts (13) │
│  ├── layers/*.ts (19) │  ├── interaction.ts  │  ├── segment-style   │
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

Rendered back-to-front, per `ALL_LAYERS` order in `layer-registry.ts`:

Rendering order: **night shading → obscuration → clouds → convection → icing → CAT/E-Shear/inversions → terrain (covers below-surface artifacts) → current conditions → front markers → lines → reference**.

| Layer | Name | Group | File | Default | Description |
|-------|------|-------|------|---------|-------------|
| Night shading | Night / Twilight | sun | `night-shading.ts` | **on** | Full-height column tint behind the weather (#227): light wash for civil twilight, darker for night. Reads `VizRouteData.nightIntervals` (from `manifest.sun.night_intervals`); empty on daytime flights / old packs → no-op. Two tones from the theme's `nightShading` colours. Registered first (very back of the stack); terrain masks the below-surface tint. |
| Soft NWP clouds | Soft NWP | clouds | `cloud-bands-factory.ts` | **on** | Gradient-edge fills with coverage-proportional opacity (GRAMET style) |
| Soft DD clouds | Soft DD | clouds | `cloud-bands-factory.ts` | off | Same soft rendering using DD-derived cloud layers |
| Natural NWP clouds | NWP Natural | clouds | `cloud-bands-factory.ts` | off | Flat-bottom puffs with bumpy tops; coverage encoded as horizontal fill fraction (SCT = gaps, OVC = continuous blanket) |
| Natural DD clouds | DD Natural | clouds | `cloud-bands-factory.ts` | off | Same puff rendering using DD-derived cloud layers |
| Square NWP clouds | Square NWP | clouds | `cloud-bands-factory.ts` | off | Solid filled cells per zone, opacity from cover% (ForeFlight-like) |
| Square DD clouds | Square DD | clouds | `cloud-bands-factory.ts` | off | Same square cells using DD-derived cloud layers |
| NWP Convective | NWP Convective | convection | `nwp-convective-bg.ts` | **on** | Model convective scheme output (base/top/coverage); full-height **depth-unresolved ghost column** when risk ≥ LOW but no base/top (ECMWF `nwp_precip` / GFS cover-only) |
| Thermo Convective | Thermo Convective | convection | `thermo-convective-bg.ts` | off | CAPE/CIN tower columns LFC→EL (LCL fallback), hatching, TCU/CB/+TS labels |
| Icing bands | Ogimet-DD | icing | `icing-bands.ts` | off | DD-attenuated Ogimet index |
| Ogimet-NWP bands | Ogimet-NWP | icing | `icing-ogimet-nwp-bands.ts` | **on** | NWP cloud-fraction-scaled Ogimet index with glaciation |
| SFIP bands | SFIP-NWP | icing | `sfip-bands.ts` | off | Fuzzy-logic SFIP icing index |
| IENG bands | IENG | icing | `ieng-icing-bands.ts` | off | Cloud-fraction-weighted Ogimet without glaciation (CloudPath method) |
| SLD bands | SLD | icing | `sld-bands.ts` | off | SLD from warm-nose freezing rain (experimental, all models) |
| CAT bands | CAT (Ri) | turbulence | `cat-bands.ts` | on | Richardson number turbulence |
| E-Shear bands | CAT (E-Shear) | turbulence | `e-shear-bands.ts` | off | Vertical + horizontal wind shear E parameter (CloudPath method) |
| Inversion bands | Inversions | stability | `inversion-bands.ts` | on | Purple bands by strength |
| Surface obscuration | Surface obscuration | obscuration | `surface-obscuration-bands.ts` | off† | Diagonal-hatched fog/LIFR band synthesised from surface vis / low-cloud + DD; severity drives flight-category color (LIFR purple, IFR red, MVFR amber). †Default ON in airport-profile drawer, OFF on briefing — context-aware via `getDefaultEnabled('airport-profile')`. |
| Terrain fill | Terrain | terrain | `terrain-fill.ts` | on | SRTM elevation, earth-tone gradient |
| Current conditions | Current conditions | conditions | `current-conditions.ts` | off | D-0 overlay: METAR airport columns (flight-category color, ±2 nm, 5000 ft tall) + route SIGMET hatched zones; model-independent, projected from the snapshot |
| Air-mass boundary | Air-mass boundary (experimental) | fronts | `fronts-markers.ts` | off | Vertical marker at each on-track Hewson front crossing (#196), colored by kind (cold=blue/warm=red/quasi=purple), weighted by intensity, solid/dashed by wet/dry, opacity by persistence, triangle for convective. Reads `VizRouteData.fronts`; skipped in single-airport time-axis view. Advisory-only free-atmosphere boundary. |
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

Four groups support multiple methods with a preferred method setting:

```typescript
PREFERRED_METHOD_LAYER = {
  clouds:     { soft_nwp, soft_dd, nwp, dd },
  icing:      { ogimet_nwp, ogimet_dd, sfip_nwp, ieng },
  turbulence: { ri, e_shear },
  convection: { nwp, thermo },
}
```

**Compact mode** collapses each group to the user's preferred method. Defaults (for new users and migrated existing users): Soft NWP clouds, Ogimet-NWP icing, CAT (Ri) turbulence, NWP Convective. User preferences from the backend override these defaults. Legacy values (`dd`, `ogimet_dd`, `thermo`) are auto-upgraded to GRAMET-aligned defaults in `_parse_service_toggles()`.

The compact-mode invariant — *only* the preferred layer in each group is enabled — is enforced by `getCompactLayerOverrides(preferredMethods)` (`layer-registry.ts`) and applied in two places: on the mode transition (`displayMode → compact`) and once the async `fetchPreferences()` settles. Both paths are needed: clicking compact before prefs load, or booting straight into compact (the persisted default) with stale extras in localStorage, would otherwise leave non-preferred layers rendering invisibly with no UI to toggle them off (the panel only renders the preferred layer's checkbox in compact mode). When `preferredMethods` is empty, the override falls back to each group's `defaultEnabled` layer rather than disabling all.

## Preset System

Layer presets provide one-click configurations. Three presets (`PRESETS` in `layer-registry.ts`):

- **GRAMET** — Autorouter-style: Natural NWP clouds + Ogimet-NWP icing + CAT (Ri) + NWP Convective + freezing level + terrain + cruise altitude. Switches to the `gramet` theme.
- **Windy** — light theme, Natural NWP clouds + SFIP-NWP icing + NWP Convective + CAT (Ri) + freezing level + terrain + cruise.
- **ForeFlight** — high-contrast theme, Square DD clouds + Ogimet-DD icing + CAT (Ri) + NWP Convective + freezing level + terrain + cruise.

(SLD is excluded from all presets — experimental.) Presets defined as `LayerPreset` objects: `{ id, label, themeId, enabledLayers }`. Preset dropdown in controls panel next to theme selector. Store action `setVizPreset()` applies theme + layer overrides.

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
- `CrossSectionTheme` interface controls all visualization colors: sky background, axes, terrain, temperature/stability/reference line styles, cloud colors + hatch config, icing/CAT/convective risk colors, inversion appearance, and `nightShading` (twilight/night column-tint colours for the night-shading layer, #227)
- Themes registered in `THEMES` map, accessed via `getActiveTheme()` / `setActiveTheme(id)`
- Theme selector dropdown + preview button in the controls panel (both standard and compare mode)
- `'theme-changed'` window event triggers re-renders in all renderers

**Available themes:**
- `'standard'` — Light blue sky (#7395DB), default, designed for readability
- `'high-contrast'` — Dark navy sky (#1B3060), optimized for visibility in varying lighting
- `'gramet'` — Deep blue sky (#2B5DA8), CloudPath-inspired. Blue-tinted icing, prominent red freezing level, warm brown terrain. Optimized for soft cloud rendering. Applied automatically by the GRAMET preset.

**Cloud rendering styles:**
- **Natural** (DD Natural, NWP Natural): flat-bottom puffs with bumpy tops, drawn procedurally on canvas (no PNG assets). Coverage is encoded as horizontal fill fraction — SCT shows discrete puffs with sky gaps, BKN shows mostly-touching puffs with valleys between humps, OVC shows a continuous bumpy blanket. Each puff is a closed path: flat base + chain of quadratic-Bezier humps whose peaks reach the band-top profile, with per-hump amplitude jitter from a stable per-band hash. Puff slots are anchored on a global x grid so adjacent matched-zone segments tile coherently; the gap pattern is deterministic so the same band keeps a stable shape across redraws. Tunable knobs live in `DEFAULT_NATURAL_CONFIG` in `cloud-bands-factory.ts` (fill-fraction per coverage class, puff/hump width, min band width before falling back to continuous fill, amplitude jitter, edge overflow, fill alpha).
- **Soft** (Soft DD, Soft NWP): gradient-edge fills with coverage-proportional opacity. Each band has feathered edges (top/bottom 15% fades to transparent). Opacity: OVC ~0.85, BKN ~0.65, SCT ~0.45, modulated by dewpoint depression for density. GRAMET-like aesthetic. Rendering uses `onBand` callback in `renderMatchedZones` to draw vertical `CanvasGradient` fills — no changes to `base.ts` core primitives needed. Theme config: `softClouds: { fillRgb, coverageAlpha, featherFraction }`.

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

Convective towers are rendered by two layers — `thermo-convective-bg.ts` (CAPE/CIN thermodynamic scheme) and `nwp-convective-bg.ts` (model convective scheme base/top).

**NWP depth-unresolved ghost column.** The NWP layer needs a model base/top to draw a tower. The `nwp_precip` path (ECMWF firing on `cp` with `hcct` sentinel) and a GFS cover-only point both carry a real risk (≥ LOW) but *no* drawable base/top. Rather than skip them (which silently dropped real convection from the section while the route map + advisory still flagged it), `drawUnresolvedColumn()` renders a full-height **ghost column**: the risk-tinted `bgWash` spanning terrain→section-top, **dashed vertical sides**, and a **"?" marker** near the top — deliberately distinct from a resolved tower (no solid body / anvil / edge box) so it never reads as a known full-height CB. The tooltip row shows `Tower: depth unresolved (cp X mm/h)` with the firing evidence. `convective_precip_mm_h` is plumbed through `VizPoint.nwpConvectivePrecipMmH` for that evidence string.

The thermo layer is the more complex:
- **Marginal risk skipped**: points with `convectiveRisk === 'none' | 'marginal'` skip tower rendering entirely (marginal CAPE <100 J/kg produces misleading visual towers for noise-level instability)
- **Tower columns**: drawn from base (LFC, falling back to LCL) → estimated tower top for each route point with risk ≥ LOW
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
- **Tooltip**: shows waypoint name (if named) + distance + altitude at hover position, plus per-layer rows for every enabled band/zone the cursor altitude intersects.

### Per-layer tooltip registry (`tooltip-formatters.ts`)

Per-layer tooltip content lives in a declarative registry consumed by `interaction.ts`. Each entry:

```typescript
{ id, enabledBy?, header?, getZones(p), formatLine(z), swatch?(z) }
```

- `id` — primary layer toggle that owns the data.
- `enabledBy` — alias toggles that should also activate this row (e.g. `soft-cloud-bands` and `square-cloud-bands` for the DD-derived row, `soft-nwp-cloud-bands` and `square-nwp-cloud-bands` for the NWP row). Without this the soft/square/natural renderings produce visuals but no tooltip text.
- `header` — optional section title above the lines (e.g. `Icing (Ogimet-DD)`).
- `getZones` — returns the relevant zone array from `VizPoint` (or synthesizes a single pseudo-zone from per-point fields, used by Thermo/NWP convective).
- `formatLine` — produces one tooltip line per zone, including any per-layer extras (DD, CC, T, icing index, Ri, SLD tag, source tag, etc.).
- `swatch(zone)` — optional fill colour for a small square key drawn next to the row, **keyed to the band's risk/coverage so it matches the on-chart fill** (returns null to omit). Each entry reuses the *same* color function as the renderer (`cloudFillFromDD`, `nwpCloudFill`, `icingRiskColor`, `sldRiskColor`, `catRiskColor`, `inversionSwatchColor`, theme `sfipIcing`/`convective.towerFill`/`obscuration`) so the tooltip key never drifts from the chart. The header row also carries a line-style key, and the point header is rendered single-line.

The registry includes 14 entries: cloud DD, cloud NWP, Ogimet-DD, Ogimet-NWP, SFIP, IENG, SLD, CAT (Ri), E-Shear, Thermo Convective, NWP Convective, Inversions, Surface obscuration, Night shading. Adding a new layer = one new entry; changing what a layer shows = edit one `formatLine`.

**Per-layer extras shown:**
- Cloud DD: `(DD x.x°C, T n°C)`
- Cloud NWP: `(CC nn%, T n°C)`; trailing `[band]` only when `source==='grib'` (i.e. GFS GRIB-bulk path) — no tag for `nwp_3d` (ECMWF/ICON per-level).
- Ogimet-DD / Ogimet-NWP / IENG: `(idx N, T n°C)` plus `+SLD` suffix when `sld_risk`.
- SFIP: `SFIP nn/100 type (T n°C)` plus trailing `[proxy]` for any non-`full` variant.
- CAT / E-Shear: `(Ri n.nn)` when populated.
- Thermo Conv: `(CAPE x, CIN -y)` plus `LCL→EL: FLxxx–FLyyy` (or `Tower:` fallback).
- NWP Conv: `(nn% cover)` plus `Tower: FLxxx–FLyyy [method-tag]` where method-tag is `nwp` / `nwp_lcl_top` / `nwp_hybrid` / `nwp_precip`. When depth is unresolved (no base/top) the row reads `Tower: depth unresolved (cp X mm/h)` and matches the full ghost column at any cursor altitude.
- Inversions: ` (sfc)` suffix for surface-based.

Header/terrain/temperature/stability rows stay inline in `interaction.ts` (different shape — proximity-based not band-based).

## Cloud Bands Factory

All six cloud layers (DD/NWP × Soft/Natural/Square) come from a single `cloudLayer({source, style, ...})` factory in `cloud-bands-factory.ts`. The two axes are orthogonal:

- **Source** picks the data feed and the continuous color function:
  - `dd` → `p.cloudLayers`, color from `cloudFillFromDD(dd, coverage)` (gray ↔ white by DD, alpha by coverage class).
  - `nwp` → `p.nwpCloudLayers`, color from `nwpCloudFill(coverPct)` (theme bright ↔ dark by cover %, alpha 0.30→0.85).
- **Style** picks the painter applied per matched-zone band:
  - `soft` → `paintSoft`, vertical-gradient feathered fills (top/bottom 15% fade).
  - `natural` → `paintNatural`, flat-bottom puffs with bumpy quadratic-Bezier tops; coverage encoded as horizontal fill fraction (SCT gaps, BKN touching, OVC continuous blanket).
  - `square` → `drawColumnBand`, solid filled rectangles per zone (no puffs, no feathering).

Server-computed layers come from Python (`clouds.py:_synthesize_nwp_layers()`); the frontend receives ready-to-render `EnhancedCloudLayer` objects with base/top boundaries. Adjacent route points are matched by altitude overlap via `renderMatchedZones`; unmatched zones taper to midpoint. Single-point routes fall back to column bands.

The factory exposes `CLOUD_LAYER_BY_AXES`, `ALL_CLOUD_LAYER_IDS`, and `parseCloudLayerId(id)` helpers for the panel's compound source-checkbox + style-dropdown control (`controls/panel.ts:cloudCompoundHtml`).

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

~60 metrics (including `sounding_ceiling_ft` and `nwp_ceiling_ft` for Key Altitudes) with catalog-driven contextual help:

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

`controls/panel.ts` renders checkboxes grouped by category. `getLayerGroups()` returns them in the order `reference, temperature, clouds, obscuration, icing, stability, turbulence, convection, conditions, sun, fronts` — the `terrain` group is intentionally omitted from the panel (terrain always renders, force-on at render time, so it has no UI toggle). Layers with a `metricId` get an info button that opens the layer info popup. The **Clouds** and **Icing** group headers show an info button explaining the available methods. A **theme selector dropdown** and **preview button** appear in the toolbar (both standard and compare mode) for switching cross-section themes.

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
- **Provider-aware annotation:** each model badge shows the *primary* provider (Open-Meteo or direct GRIB) so users can tell at a glance whether a given model was sourced via OM or fetched directly. When `pack.grib_init_times[model]` differs from `pack.model_init_times[model]`, the run hour also surfaces (e.g. `"GFS 12Z (GRIB 18Z)"`).
- **Per-source (i) popover:** clicking the info icon opens a table grouped by model (`renderSourcesPopupContent`), one row per source detailing provider, run hour, observed publish time, and next-expected update. Comes from `pack.model_sources` joined with the live marker store data on `/freshness`.
- **States:** current (muted), stale (amber), refreshing (animated dots spinner)
- **Force refresh link:** shown for admins when data is stale

## Route Graph

A separate canvas-based chart rendered below the cross-section for scalar weather metrics along the route. See [route-graph.md](./route-graph.md) for full design.

- **X-axis aligned** with cross-section (same `distanceToX` transform and margins)
- **Dual Y-axes**: left and right metrics independently selectable
- **12 metrics**: headwind, crosswind, temperature, ISA deviation, precipitation, cloud cover, CAPE, CIN, QNH (region-aware unit), freezing level, ceiling-DD (sounding AGL), ceiling-NWP (NWP AGL)
- **Render types**: line (monotone cubic spline) and bar charts
- **State**: `VizSettings` extended with `routeGraphVisible`, `routeGraphLeftMetric`, `routeGraphRightMetric`, persisted to localStorage
- **Controls**: dropdown selectors below the graph, integrated into the controls panel

## Route Map

Leaflet-based geographic visualization showing weather metrics as colored route segments on an interactive map. Located in `web/ts/visualization/route-map/`.

### Components

| File | Purpose |
|------|---------|
| `renderer.ts` | Leaflet map lifecycle: lazy init, segment polylines, waypoint markers, highlight |
| `metrics.ts` | 13-metric `MAP_METRICS` registry: `MapMetric` objects with `getValue`, `getColor`, `getWidth`, `formatValue` |
| `segment-style.ts` | Pure function: `computeSegmentStyles()` → `{color, weight}[]` from metric + points |
| `interaction.ts` | Hover (highlight + tooltip + sync), click (select point), event attach/detach |
| `altitude-slider.ts` | Range input for level-dependent metrics (0 → ceiling, 500ft steps, FL labels) |
| `legend.ts` | DOM gradient bar with color stops and labels |

### Metric Registry (13 metrics)

| Metric (id) | Label | Alt-Dependent | Color Scale |
|-------------|-------|:---:|-------------|
| cloud-cover-total | Cloud Cover (Total) | | Gray 0-100% |
| cloud-cover-low | Cloud Cover (Low) | | Gray 0-100% |
| convective-risk | Convective Risk | | Green→Yellow→Red discrete |
| headwind | Headwind | | Green (tailwind) ↔ Red (headwind) |
| tailwind | Tailwind | | Separate tailwind view |
| cape | CAPE | | Green→Yellow→Red continuous (0–2000 J/kg) |
| nwp-ceiling | NWP Ceiling | | Purple (LIFR)→Red (IFR)→Amber (MVFR)→Green (VFR) |
| temp-at-level | Temperature at FL | Yes | Blue→White→Red diverging |
| model-agreement | Model Agreement | | Green/Orange/Red discrete |
| icing-risk-at-level | Icing Risk at FL | Yes | Green→Yellow→Orange→Red |
| sfip-at-level | SFIP at FL | Yes | Green→Yellow→Orange→Red |
| cat-risk-at-level | CAT Risk at FL | Yes | Green→Yellow→Orange→Red |
| cloud-at-level | Cloud at FL | Yes | Gray 0-100% |

**Width variation**: Route map segments now vary in width as well as color. Each metric defines a `getWidth(point)` function. Width communicates a secondary dimension (e.g., nwp-ceiling uses inverted width so low ceilings appear thick/dangerous).

Altitude-dependent metrics use helpers (`worstRiskAtAlt()`, `sfipAtAlt()`, `cloudAtAlt()`, `tempAtAltitude()`) to find the relevant value at the slider's flight level.

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
- `layout`: `'cross-section' | 'compare' | 'split' | 'map'`
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

## Compare Mode

Fourth layout mode (`'compare'`): renders a single layer across all models simultaneously, highlighting where models agree or disagree. Located in `web/ts/visualization/cross-section/compare-renderer.ts`.

### Comparable Layers (`compare-layers.ts`)

Two types of comparable layers:
- **Band layers**: clouds (DD/NWP), icing (Ogimet-DD/NWP, SFIP), CAT, inversions, convective — use zone data from `compare-zone-access.ts`
- **Line layers**: freezing level, −10°C, −20°C, LCL, LFC, EL — use `lineAccessor` to extract altitude values; rendered as min/max envelope + mean line

### Band Rendering Modes (`CompareBandMode`)

| Mode | Technique | Agreement signal |
|------|-----------|-----------------|
| **Overlay** | Each model's hatched layer drawn at `2.0/N` alpha on offscreen canvas, composited onto main | Overlapping areas compound darker naturally |
| **Overlay Soft** | Each model's zones drawn as feathered gradient fills (`1.4/N` alpha, 15% edge feather) directly on canvas | Soft fills compound where models overlap — denser = more agreement; GRAMET-like aesthetic |
| **Consensus** | Sweep-line merges zones across models into consensus intervals; fill alpha = `0.08 + 0.82 × ratio²` | Non-linear: single-model areas faint, full-agreement vivid |
| **Consensus+Outlines** | Same as consensus, plus per-model zone boundary lines in distinct colors from `theme.compareModelColors` | Consensus fill + colored outlines show each model's exact boundaries |

### Consensus Fill Alpha Curve

`alpha = 0.08 + 0.82 × ratio²` where `ratio = agreeing_models / total_models`. With 3 models:
- 1/3 agreement → alpha ≈ 0.17 (faint wash)
- 2/3 agreement → alpha ≈ 0.44 (moderate)
- 3/3 agreement → alpha ≈ 0.90 (vivid)

### Layer Base Colors

Consensus and overlay-soft modes use a single base RGB per layer from the theme. Both DD and NWP cloud layers use `theme.nwpClouds.brightRgb` for consistent appearance.

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
