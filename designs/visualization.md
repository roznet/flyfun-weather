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
│  ├── layers/*.ts (20) │  ├── interaction.ts  │  ├── segment-style   │
│  ├── nwp-fallback.ts  │  └── constants.ts    │  ├── interaction.ts  │
│  └── interaction.ts   │                      │  ├── altitude-slider │
│                       │                      │  ├── forecast-overlay│
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

The **Default** column is each layer's `defaultEnabled` — the fresh-install state
from `getDefaultEnabled(context)`. Presets, compact mode and the NWP fallback all
override it at runtime, so "on" here is not what a returning user necessarily sees.

| Layer | Name | Group | File | Default | Description |
|-------|------|-------|------|---------|-------------|
| Night shading | Night / Twilight | sun | `night-shading.ts` | **on** | Full-height column tint behind the weather (#227): light wash for civil twilight, darker for night. Reads `VizRouteData.nightIntervals` (from `manifest.sun.night_intervals`); empty on daytime flights / old packs → no-op. Two tones from the theme's `nightShading` colours. Registered first (very back of the stack); terrain masks the below-surface tint. |
| Soft NWP clouds | Soft NWP | clouds | `cloud-bands-factory.ts` | off | Gradient-edge fills with coverage-proportional opacity (GRAMET style) |
| Soft DD clouds | Soft DD | clouds | `cloud-bands-factory.ts` | off | Same soft rendering using DD-derived cloud layers |
| Natural NWP clouds | NWP Natural | clouds | `cloud-bands-factory.ts` | off | Flat-bottom puffs with bumpy tops; coverage encoded as horizontal fill fraction (SCT = gaps, OVC = continuous blanket) |
| Natural DD clouds | DD Natural | clouds | `cloud-bands-factory.ts` | off | Same puff rendering using DD-derived cloud layers |
| Square NWP clouds | Square NWP | clouds | `cloud-bands-factory.ts` | **on** | Solid filled cells per zone, opacity from cover% (ForeFlight-like). The one default-on cloud layer. |
| Square DD clouds | Square DD | clouds | `cloud-bands-factory.ts` | off | Same square cells using DD-derived cloud layers |
| NWP Convective | NWP Convective | convection | `nwp-convective-bg.ts` | **on** | Model convective scheme output (base/top/coverage); full-height **depth-unresolved ghost column** when risk ≥ LOW but no base/top (ECMWF `nwp_precip` / GFS cover-only) |
| Thermo Convective | Thermo Convective | convection | `thermo-convective-bg.ts` | off | CAPE/CIN tower columns LFC→EL (LCL fallback), hatching, TCU/CB/+TS labels |
| Icing bands | Ogimet-DD | icing | `icing-bands.ts` | off | DD-attenuated Ogimet index |
| Ogimet-NWP bands | Ogimet-NWP | icing | `icing-ogimet-nwp-bands.ts` | **on** | NWP cloud-fraction-scaled Ogimet index with glaciation |
| SFIP bands | SFIP-NWP | icing | `sfip-bands.ts` | off | Fuzzy-logic SFIP icing index |
| IENG bands | IENG | icing | `ieng-icing-bands.ts` | off | Cloud-fraction-weighted Ogimet without glaciation (CloudPath method) |
| SLD bands | SLD | icing | `sld-bands.ts` | off | SLD from warm-nose freezing rain (experimental, all models) |
| CAT bands | CAT (Ri) | turbulence | `cat-bands.ts` | off | Richardson number turbulence. No layer in the `turbulence` group is `defaultEnabled`, so method resolution falls back to `layers[0]` = this one. |
| E-Shear bands | CAT (E-Shear) | turbulence | `e-shear-bands.ts` | off | Vertical + horizontal wind shear E parameter (CloudPath method) |
| Inversion bands | Inversions | stability | `inversion-bands.ts` | off | Purple bands by strength |
| Surface obscuration | Surface obscuration | obscuration | `surface-obscuration-bands.ts` | off† | Diagonal-hatched fog/LIFR band synthesised from surface vis / low-cloud + DD; severity drives flight-category color (LIFR purple, IFR red, MVFR amber). †Default ON in airport-profile drawer, OFF on briefing — context-aware via `getDefaultEnabled('airport-profile')`. |
| Terrain fill | Terrain | terrain | `terrain-fill.ts` | on | SRTM elevation, earth-tone gradient |
| Current conditions | Current conditions | conditions | `current-conditions.ts` | off | D-0 overlay: METAR airport columns (flight-category color, ±2 nm, 5000 ft tall) + route SIGMET hatched zones; model-independent, projected from the snapshot |
| Air-mass boundary | Air-mass boundary (experimental) | fronts | `fronts-markers.ts` | off | Vertical marker at each on-track Hewson front crossing (#196), colored by kind (cold=blue/warm=red/quasi=purple), weighted by intensity, solid/dashed by wet/dry, opacity by persistence, triangle for convective. Reads `VizRouteData.fronts`; skipped in single-airport time-axis view. Advisory-only free-atmosphere boundary. |
| Freezing level | 0°C | temperature | `temperature-lines.ts` | on | Blue dashed line (0°C) |
| −10°C level | −10°C | temperature | `temperature-lines.ts` | on | Cyan dashed line |
| −20°C level | −20°C | temperature | `temperature-lines.ts` | on | Navy dashed line |
| LCL | LCL | stability | `stability-lines.ts` | on | Id is `lcl` (not `lcl-line`). Lifting condensation level. **Dotted** `[2,4]` — see [Colour-blind line encoding](#colour-blind-line-encoding). |
| LFC | LFC | stability | `stability-lines.ts` | on | Id `lfc`. Level of free convection. **Dashed** `[6,4]`. |
| EL | EL | stability | `stability-lines.ts` | on | Id `el`. Equilibrium level. **Dash-dot** `[9,3,2,3]`. |
| Cruise altitude | Cruise | reference | `reference-lines.ts` | on | Dark gray dashed + flight ceiling (purple) |
| Advisory highlight | Highlight | highlight | `highlight-layer.ts` | off | Scrim (dim wash with severity-outlined cutouts where the hazard is) + verdict ribbon (6px strip in the bottom margin grading the whole route green/amber/red/gray). Registered **last** (very top of stack). Reads the derived `VizRouteData.advisoryHighlights`; no-ops when absent. `clipToPlot: false` so the ribbon draws in the margin. See [Advisory Highlights](#advisory-highlights-373). |

## Render Mode

All layers use **smooth** rendering: monotone cubic spline (Fritsch-Carlson) interpolation between route points. The columns render mode was removed — smooth rendering is always used for terrain, bands, and lines alike.

## Layer Groups, Preferred Methods & Compact Mode

Method-bearing groups resolve a *method id* to a concrete layer id via
`PREFERRED_METHOD_LAYER` (`layer-registry.ts`):

```typescript
PREFERRED_METHOD_LAYER = {
  icing:      { ogimet_dd, ogimet_nwp, sfip_nwp, ieng },
  turbulence: { ri, e_shear },
  convection: { thermo, nwp },
}
```

**Clouds are deliberately NOT in that map (#410).** A cloud preference is a bare
*source* (`dd` / `nwp`, plus the backend's `nwp_synthesized` which renders on the
NWP band); the render *style* (`natural` / `soft` / `square`) is a client-only
preference in `vizSettings.cloudStyle`. The two axes are fused into a layer id at
resolution time via `CLOUD_LAYER_BY_AXES[source][style]` — hence the extra
`cloudStyle` parameter on `getPreferredLayerForGroup()` /
`getCompactLayerOverrides()`. iOS mirrors this in `cloudLayerId(source:style:)`.

**Where `preferredMethods` comes from (#410).** Account-level engine methods are
retired — `_parse_service_toggles()` no longer surfaces `icing_method` /
`cloud_method` / `convective_method` (they were empty for every user and the
pipeline grades off the flight *profile*). `briefing-main` instead derives them
with `deriveGradedMethods(routeAdvisories, engineDefaults)`
(`cross-section/advisory-highlights.ts`): for each method-bearing advisory it
takes the `primary_method_id` its representative model actually graded on —
already reflecting any backend fallback — and falls back to the catalog's
`engine_method_defaults` (`ogimet_nwp` / `nwp` clouds / `nwp` convection; the
client fallback constant is `ENGINE_METHOD_DEFAULTS_FALLBACK`) when the pack is
silent. So the compact view always shows the evidence the grade was made on.
`refreshPreferredMethods()` re-runs it whenever the manifest changes and re-applies
the compact collapse.

The compact-mode invariant — *only* the preferred layer in each group is enabled —
is enforced by `getCompactLayerOverrides(preferredMethods, cloudStyle)` and applied
both on the `displayMode → compact` transition and from `refreshPreferredMethods()`
when the manifest lands. Both paths are needed: entering compact before the
manifest loads, or booting straight into compact with stale extras in
localStorage, would otherwise leave non-preferred layers rendering invisibly with
no UI to toggle them off (the panel renders only the preferred layer's checkbox in
compact mode). With an empty `preferredMethods` the override falls back to each
group's `defaultEnabled` layer (turbulence has none → `layers[0]`, CAT/Ri) rather
than disabling all.

`advisoryMethodOverrides(adv, model, preferredMethods)` narrows this one step
further for the chip/deep-link path: only the single group the advisory speaks for
(`ADVISORY_METHOD_GROUP`) is swapped to that advisory's effective method, so the
chart paints the evidence *that grade* used rather than whatever else is selected.

## Model Availability & NWP Fallback

Not every model carries native NWP fields, so two halves keep the chart honest —
and both are mirrored on iOS in `NwpFallback.swift`. **Keep all three in lockstep.**

- **Availability** — `data-extract.ts: getUnavailableLayers(data)` returns the layer
  ids the *rendered model* cannot supply, probing the extracted `VizPoint`s (no
  native NWP cloud data ⇒ `nwp-cloud-bands` + `icing-ogimet-nwp-bands` +
  `ieng-icing-bands`; empty SFIP/SLD/E-Shear zone arrays; no NWP convective; no
  `currentConditions`; no on-track front crossings). `nwp-cloud-bands` (the
  natural-style id) is the canonical "NWP source unavailable" signal covering all
  three NWP cloud styles.
- **Substitution** — `cross-section/nwp-fallback.ts: applyNwpFallback(enabled, unavailable)`
  builds a **throwaway** effective-enable map: unavailable layers off, then the
  fallback method on in their place (NWP clouds → same-style DD clouds, Ogimet-NWP
  and SFIP → Ogimet-DD, NWP convective → thermo; IENG has no pair so it just goes
  off). The stored `enabledLayers` preference is never mutated, so switching back to
  an NWP-capable model auto-restores NWP with no persisted "downgraded" flag. This
  mirrors the backend's `_resolve_analyses` (advise.py) so chart and advisory agree.
- **UI** — `getSubstitutedLayers()` diffs stored vs effective; `controls/panel.ts`
  dims unavailable checkboxes (`viz-layer-unavailable`, "not available for this
  model") and marks auto-substitutes (`viz-layer-substituted`), and drops a whole
  group when its layer is unavailable via `hiddenGroups`.

## Preset System

Layer presets provide one-click configurations. Three presets (`PRESETS` in `layer-registry.ts`):

- **GRAMET** — Autorouter-style: Natural NWP clouds + Ogimet-NWP icing + CAT (Ri) + NWP Convective + freezing level + terrain + cruise altitude. Switches to the `gramet` theme.
- **Windy** — light theme, Natural NWP clouds + SFIP-NWP icing + NWP Convective + CAT (Ri) + freezing level + terrain + cruise.
- **ForeFlight** — high-contrast theme, Square DD clouds + Ogimet-DD icing + CAT (Ri) + NWP Convective + freezing level + terrain + cruise.

(SLD is excluded from all presets — experimental.) Presets defined as `LayerPreset` objects: `{ id, label, themeId, enabledLayers }`. Preset dropdown in controls panel next to theme selector. Store action `setVizPreset()` applies theme + layer overrides (merge, not clean-slate).

**SYNC with iOS.** Four hand-copied surfaces carry `SYNC:` comments in the TS and
must move together (the `sync-ios-web` skill audits them): the three layer presets
→ `CrossSectionPresets.swift`; the cloud source×style id mapping
(`CLOUD_LAYER_BY_AXES` / `parseCloudLayerId`) → the same file; the NWP fallback
(`applyNwpFallback` / `getDdSubstituteId` / `SINGLE_LAYER_FALLBACK` +
`getUnavailableLayers`) → `NwpFallback.swift`; and `ADVISORY_PRESETS` /
`ADVISORY_TO_PRESET`. Stability-line dashes are a fifth (see below).

### Advisory presets / lenses (#219, #308)

A second, hazard-oriented preset family lives in `cross-section/advisory-presets.ts`
(`ADVISORY_PRESETS`): **Basic/Learn, Icing, Clouds, Convective, Turbulence, VFR, IFR**.
Each is a *bag of optional view directives* — the resolver/applier dispatches over
whichever are present, so adding a new effect kind is one field + one store branch:

- `groups` / `lines` — method-resolved cross-section layers (clean-slate: reset groups OFF, enable the preferred layer of each named group).
- `routeGraph` / `map` — companion route-graph metrics + route-map color metric/altitude.
- `skewtOverlays` (#308) — overlay bands to pre-enable on the Skew-T; **clean-slate** like `groups` (listed bands ON, all others OFF). An empty array = "all bands off" (the Basic/Learn view).
- `skewtSidePanel` (#308) — primary dual-axis side-panel variable. Convective gets omega (`vertical_velocity` = `w_fpm`, positive = up) — it distinguishes "high CAPE but capped/subsiding" from "CAPE + synoptic lift = going off".
- `interpretation` (#308) — a short "how to read it" blurb behind the caption's (i) / "Help me read this graph" button; the **same text seeds the MCP explanation**. Getter `advisoryPresetInterpretation()`; falls back to `caption`.

`resolveAdvisoryPreset(preset, preferredMethods)` → `ResolvedView` (concrete layer
map + skewt overlay map + side-panel id). The store's `applyAdvisoryPreset()` writes
the resolved view into `vizSettings` (incl. `skewtOverlays` / `skewtPrimaryVar` +
`activePreset`); `briefing-main` then pushes the Skew-T lens into the live
`SkewTRenderer` via `renderer.applyPreset()` whenever `activePreset` changes, so one
lens configures map + cross-section + Skew-T coherently. A manual Skew-T overlay/var
edit calls `markVizCustom()` → the dropdown reflects "Custom" (mirrors the cross-section).

`ADVISORY_TO_PRESET` maps advisory ids → preset ids for card chips; `ADVISORY_OVERRIDES`
layers per-advisory extras (e.g. FIKI warm-nose lines) onto a shared preset.

### Deep-link (#308 Phase C)

`briefing-main.applyDeepLink()` honors `?point=&model=&view=&preset=|advisory=` on load
(after `routeAnalyses` is ready): selects the model + route point, applies the lens
(explicit `preset` wins; otherwise `advisory` resolves via `getPresetForAdvisory` — single
source of truth, no server-side copy of the mapping), and focuses the requested surface
(`view=skewt` scrolls to the Skew-T). The MCP `get_advisory_detail` builds this link in
its `web_url` (`_advisory_web_url`), pointing convective at the highest-CAPE peak point.

## Advisory Highlights (#373)

When a user clicks an advisory chip (or opens an `?advisory=` deep-link), the
cross-section shows **where the advisory's verdict comes from**. Two elements, each
one job:

- **Scrim (focus, 2D)** — a translucent dim wash over the plot with cutouts punched
  out where the hazard physically is, each framed by a thin severity-colored
  outline. Dimming means "not the focus", never a verdict. **No scrim at all when
  nothing is flagged** (the all-green case — never dim a clean chart).
- **Verdict ribbon (judgement, 1D)** — a ~6px strip in the bottom margin (below
  `plotArea.bottom`, above the distance labels) partitioning the whole route into
  green/amber/red/gray(unavailable). Renders even all-green (an explicit "checked:
  clear the whole way").

**Backend owns the geometry** (`analysis/advisories`, see [advisories.md](./advisories.md));
the client only renders it. Data path & state:

- **State** (`vizSettings.activeHighlightAdvisoryId`, persisted): stores **only** the
  advisory id, never a copy of the geometry. No-ops gracefully if the advisory no
  longer exists in the manifest.
- **Derived, never stored** (`cross-section/advisory-highlights.ts`): at render time
  `deriveHighlights(getEffectiveAdvisories(state), activeHighlightAdvisoryId, selectedModel)`
  looks up the advisory × the rendered model's `per_model.highlights`. Missing / old
  pack / model without data → `null` → no scrim, no ribbon, toggle hidden. Because it
  re-derives, model switches / recalcs / altitude changes update the highlight with no
  stale-copy bugs. briefing-main attaches the result onto `VizRouteData.advisoryHighlights`
  before `setData` (precedent: `fronts`, `nightIntervals`).
- **Chip / deep-link** (`briefing-main.handleAdvisoryChip` / `applyDeepLink`): on top of
  the Phase-1 preset, switch to the **representative model** (`representativeModel` — first
  `per_model` whose status equals `aggregate_status`, mirroring the Python policy),
  `setHighlightAdvisory(id)` (force-enables the Highlight toggle — fresh intent).
  Same-chip re-click toggles the highlight off (lens stays). An explicit `?model=` wins
  over the representative switch. Old packs (no highlight data) behave exactly as Phase 1.
- **Clearing**: a bare dropdown/deep-link preset (`applyAdvisoryPreset` / `setVizPreset`),
  a manual layer/overlay edit (`markVizCustom`, or any non-highlight `toggleVizLayer`) →
  `activeHighlightAdvisoryId = null`. Model/point changes do **not** clear it.
  **Exemption**: toggling the Highlight layer checkbox itself is a visibility control,
  not a lens edit — it neither marks the view Custom nor clears the highlight.
- **Rendering** (`layers/highlight-layer.ts`): registered last (top of stack) in a new
  `highlight` `LayerGroup`. The scrim composes on an **offscreen canvas** (fill wash →
  `destination-out` punch cutouts → draw onto main → stroke severity outlines) so
  `destination-out` never erases the sky/axes beneath (compare-mode precedent). The
  ribbon draws in the bottom margin, so the layer sets `clipToPlot: false` (honored by
  the render loop in `renderer.ts`). Severity colors come from the advisory-status CSS
  vars (`--red`/`--amber`/`--green`, theme-aware); unavailable = neutral gray; the dim
  wash has light/dark variants via `isDarkTheme()`.
- **Panel toggle**: the `highlight` group appears in the layer panel **only while**
  `activeHighlightAdvisoryId` is set and the selected model has highlight data (gated via
  the panel's `hiddenGroups`). One checkbox ("Highlight"), i18n `viz.layer.advisory-highlight`.
  Compare mode is out of scope — highlights render only in single-model layouts.

Highlight geometry deliberately does **not** flow through the static advisory-preset
config (`advisory-presets.ts`) — a bare dropdown lens has no advisory instance — it
flows through the chip/deep-link path via `activeHighlightAdvisoryId`. The scrim also
subsumes the old reserved `emphasize` directive (it dims by region, so even the relevant
layer's non-affected extent is de-emphasized).

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
  departureTime: string; flightDurationHours: number;
  terrainProfile: TerrainPoint[] | null;
  timeAxisMode?: boolean;       // airport-profile drawer: X becomes time, not distance
  // Nullable extras attached by briefing-main before setData; each layer no-ops
  // when its slice is null/empty, so old packs degrade silently.
  currentConditions: VizCurrentConditions | null;   // D-0 METAR + SIGMET
  fronts: VizFronts | null;                          // Hewson, #196
  nightIntervals: VizNightInterval[];                // #227
  sunSide: VizSunSide | null;                        // seating note, #227
  advisoryHighlights: AdvisoryHighlights | null;     // scrim + ribbon, #373
  advisoryHighlightName?: string | null;             // ribbon tooltip, #412
}

interface CrossSectionLayer {
  id: string; name: string; group: LayerGroup; defaultEnabled: boolean;
  metricId?: string;            // opens the layer-info popup when present
  clipToPlot?: boolean;         // false ⇒ may draw in the margins (verdict ribbon)
  render(ctx, transform, data): void;   // no render-mode arg — smooth always
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

## Colour-blind line encoding

The cross-section carries eight horizontal lines (three isotherms, three parcel
levels, cruise, ceiling). Hue was originally the only thing telling them apart,
which fails for a colour-blind pilot: simulating protanopia/deuteranopia over
the palettes put **LCL and EL at ΔE ≈ 9** in the Light theme and **LFC and EL at
ΔE ≈ 19** in Standard — i.e. the same colour — while all three shared the
identical `[6, 4]` dash.

The rule now:

> **Dash pattern carries identity; hue is a secondary cue.** Two lines may share
> a colour when something structural separates them; they may not share both.

The parcel triplet is the acute case (green / orange / red is exactly the
red-green confusion set), so it gets three structurally distinct strokes,
readable in monochrome:

| Line | Dash | Reads as |
|------|------|----------|
| LCL | `[2, 4]` | dotted |
| LFC | `[6, 4]` | dashed |
| EL | `[9, 3, 2, 3]` | dash-dot |

Two deliberate same-colour pairs remain, both safe because the levels are
*always stacked in a known order* and the dash differs anyway:

- GRAMET draws −10 °C and −20 °C in the same autorouter green (`#22CC44`),
  separated by `[6,4]` vs `[4,4]`.
- 0 °C and −10 °C are both solid in Standard / High-Contrast / Light and are
  colour-confusable under simulation; −10 °C is always above 0 °C, and enabling
  only one of them makes the question moot. GRAMET is the one theme that also
  dash-separates them — the more robust treatment if this is ever revisited.

GRAMET additionally overrides LCL to cyan `#00E5FF`: Standard's green LCL would
have been a *third* green line against that theme's green isotherms, with no
stacking order to fall back on.

Enforced by `tests/unit/theme-line-distinctness.test.ts`, which fails if any two
lines in any theme share both colour and dash, or if the parcel triplet loses
its three distinct dashes. **SYNC:** dashes are mirrored in iOS
`StabilityLinesLayer.swift` (`dash(for:)`) — unlike colours, iOS hardcodes line
dashes rather than theming them.

## Cross-Section Theme System

Switchable visual themes for the cross-section via `cross-section/theme.ts`. Separate from the page-level dark/light theme.

**Architecture:**
- `CrossSectionTheme` interface controls all visualization colors: sky background, axes, terrain, temperature/stability/reference line styles, cloud colors + hatch config, icing/CAT/convective risk colors, inversion appearance, and `nightShading` (twilight/night column-tint colours for the night-shading layer, #227)
- Themes registered in `THEMES` map, accessed via `getActiveTheme()` / `setActiveTheme(id)`
- Theme selector dropdown + preview button in the controls panel (both standard and compare mode)
- `'theme-changed'` window event triggers re-renders in all renderers

**Available themes** (`ThemeId`, registered in the `THEMES` map):
- `'standard'` — Light blue sky (#7395DB), default, designed for readability
- `'high-contrast'` — Dark navy sky (#1B3060), optimized for visibility in varying lighting. Applied by the ForeFlight preset.
- `'gramet'` — Deep blue sky (#2B5DA8), CloudPath-inspired. Blue-tinted icing, prominent red freezing level, warm brown terrain. Optimized for soft cloud rendering. Applied automatically by the GRAMET preset.
- `'light'` — Light cross-section variant applied by the Windy preset.

**Cloud rendering styles:**
- **Natural** (DD Natural, NWP Natural): flat-bottom puffs with bumpy tops, drawn procedurally on canvas (no PNG assets). Coverage is encoded as horizontal fill fraction — SCT shows discrete puffs with sky gaps, BKN shows mostly-touching puffs with valleys between humps, OVC shows a continuous bumpy blanket. Each puff is a closed path: flat base + chain of quadratic-Bezier humps whose peaks reach the band-top profile, with per-hump amplitude jitter from a stable per-band hash. Puff slots are anchored on a global x grid so adjacent matched-zone segments tile coherently; the gap pattern is deterministic so the same band keeps a stable shape across redraws. Tunable knobs live in `DEFAULT_NATURAL_CONFIG` in `cloud-bands-factory.ts` (fill-fraction per coverage class, puff/hump width, min band width before falling back to continuous fill, amplitude jitter, edge overflow, fill alpha).
- **Soft** (Soft DD, Soft NWP): gradient-edge fills with coverage-proportional opacity. Each band has feathered edges (top/bottom 15% fades to transparent). Opacity: OVC ~0.85, BKN ~0.65, SCT ~0.45, modulated by dewpoint depression for density. GRAMET-like aesthetic. Rendering uses `onBand` callback in `renderMatchedZones` to draw vertical `CanvasGradient` fills — no changes to `base.ts` core primitives needed. Theme config: `softClouds: { fillRgb, coverageAlpha, featherFraction }`.

**Theme preview:** `theme-preview.ts` splits the popup into two halves with one job each — the old single-canvas version crammed both into 520×320 and the labels collided with the bands they named.

- **Scene** (canvas, no text) — every colour family drawn together over the theme's sky and terrain, in plausible vertical order and at real opacities: "what will my chart look like". A `SCENE` constant holds the whole layout as fractions of plot height, with disjoint x-ranges where families would otherwise stack (bands left, convective tower right). Everything is clipped to the frame; natural cloud rows get one generous row-level clip (`NATURAL_OVERHANG_PX`, from the factory's blob radius) rather than a per-cell clip, so blobs blend into a continuous deck instead of being sliced.
- **Key** (DOM, grouped) — "which colour means what", built from `getLayerLegend()` so it can't drift from the per-layer info popups. Lines live in their own full-width block, each with its own colour + dash sample and its own label.

The preview follows `VizSettings.cloudStyle` (passed from `controls/panel.ts`), so previewing with Square clouds selected shows square cells — it no longer hardcodes the natural style.

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
- `fmtFL()`, `altInBand()`, `altNearLine(hoverAlt, lineAlt, tol=1500)` — the band /
  line hit-tests every tooltip formatter shares

Both cross-section and route-graph interaction modules import from this shared utility.

## Interaction

- **Hover**: vertical crosshair line follows mouse, shows distance/time at cursor
- **Click**: selects closest route point, highlights with indicator on overlay canvas
- **Tooltip**: shows waypoint name (if named) + distance + altitude at hover position, plus per-layer rows for every enabled band/zone the cursor altitude intersects.
- **Verdict-ribbon hover (#412)**: hovering the bottom-margin ribbon yields its own
  tooltip — advisory name (`VizRouteData.advisoryHighlightName`) + the verdict from
  `ribbonSeverityAt()` + *why*, from `reasonCodeAt(regions, dist, severity)`. The
  severity filter is load-bearing: adjacent same-kind regions of differing severity
  share an exact boundary, and an unfiltered lookup would print a RED reason under
  an AMBER verdict. Only `icing_escape` / `fiki_icing` / `convective` emit
  `reason_code`s, and only the codes in `RIBBON_REASON_CODES` have phrasing —
  anything else shows the verdict alone, never invented text.

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

- `LegendEntry`: `{ label, color, meaning, hatchStyle?, line? }` — human-readable, visual, and contextual. `line` carries the layer's real `LineStyle` (colour + width + dash), so a line legend reproduces the stroke instead of a generic 3px rule — the only way to separate −10°C from −20°C in themes that give them the same hue (GRAMET).
- Colors pulled dynamically from a theme at render time (theme-aware, not cached). `getLayerLegend(layerId, theme?)` and the `scales.ts` colour functions all default to `getActiveTheme()` but accept an explicit theme, so the theme preview can render a not-yet-applied theme through the same formulas the renderers use.
- Cloud legend swatches include CSS `repeating-linear-gradient` hatch overlay matching canvas rendering. `hatchStyle` is **always** a CSS `<image>` (a flat colour becomes `linear-gradient(c, c)`) — it is composed into a `background: <image>, <color>` shorthand, where a bare colour in the first slot invalidates the whole declaration.
- Risk-based legends for icing, CAT, convective bands; METAR-style for cloud bands; percentage-based for NWP clouds. Every group is ordered **least → most** (SCT→OVC, 25%→75%, light→severe) so a reader scanning several groups never reverses direction.
- Reference-line widths/dashes come from `referenceLineStyles()` in `layers/reference-lines.ts` — exported from the layer that draws them, not re-declared in the legend.
- Swatch markup is shared via `legend-render.ts` (`legendRowsHtml` for the info popup, `legendChipsHtml` for the preview grid), so the two surfaces cannot disagree. Swatches sit on a sky-coloured backing plate because nearly every fill is semi-transparent.
- Displayed in info popups via `renderLayerLegend()` in metrics-helper.ts

## Info Popup & Metrics UI System

Shared modal infrastructure (`components/info-popup.ts`) used by three popup types:

- **Metric info** (`showMetricInfo`): Full explanation with threshold scale bar, vibe, goal, limitations
- **Layer info** (`showLayerInfo`): Metric details + color legend for the specific layer
- **Advisory info** (`showPopupContent`): Custom HTML (used by advisory system)

Keyboard (ESC) and click-outside close the popup.

### Metrics Catalog (`web/ts/data/metrics-catalog.json`)

~64 metrics (including `sounding_ceiling_ft` and `nwp_ceiling_ft` for Key Altitudes) with catalog-driven contextual help (the iOS app mirrors this file at `app/.../Resources/metrics-catalog.json`):

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

`controls/panel.ts` renders checkboxes grouped by category. `getLayerGroups()` returns them in the order `reference, temperature, clouds, obscuration, icing, stability, turbulence, convection, conditions, sun, fronts, highlight` — the `terrain` group is intentionally omitted from the panel (terrain always renders, force-on at render time, so it has no UI toggle), and the `highlight` group is hidden via `hiddenGroups` unless an advisory highlight is active with data for the selected model (#373). Layers with a `metricId` get an info button that opens the layer info popup. The **Clouds** and **Icing** group headers show an info button explaining the available methods. A **theme selector dropdown** and **preview button** appear in the toolbar (both standard and compare mode) for switching cross-section themes.

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
- Cruise icing banner displayed at top when applicable. When an altitude override is
  active, `recomputeCruiseIcing()` re-derives the banner client-side from each model's
  `icing_zones` so it tracks the user's probe rather than the manifest's baked value
- Uses `AltitudeAdvisories.regimes` per model (dict of model → list of `VerticalRegime`)

## Windy Meteogram Link

The cross-section toolbar includes a dynamic Windy link ("Open selected location") for the currently selected route point and model:

- **URL builder** (`utils.ts: buildWindyUrl()`): constructs `https://www.windy.com/{lat}/{lon}/{model}/meteogram?...` URLs
- **Model mapping**: GFS → `gfs`, ICON → `icon`, others → ECMWF (Windy's default)
- **Updates dynamically** when the user changes route point or model selection
- Placed next to the model selector in the cross-section toolbar

## Display Mode (Compact / Full Details)

The briefing UI supports two display modes. `displayMode` is a **top-level store
field**, not part of `VizSettings` — persisted separately under
`localStorage('wb_displayMode')` (alongside `wb_tierVisibility`, the `key` /
`useful` / `advanced` metric-tier toggles, and `wb_selectedModel`):
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
| `forecast-overlay.ts` | Pure helpers for the airport forecast overlay (#424): day/hour snapping, deep-link building. No DOM/Leaflet, so it is unit-testable |
| `legend.ts` | DOM gradient bar with color stops and labels |

### Overlays on top of the segments

Two optional overlays live in `renderer.ts` beside the route segments:

- **Hewson fronts (#196)** — `setFrontLines()` draws the gated 2-D front axes
  (`GET /api/hewson-map/fronts`, same `FrontGateConfig` as the advisory) for every
  stored pressure level, styled by `front-style.ts` and faded with altitude, plus a
  marker per on-track crossing. Toggle: `vizSettings.mapFrontsVisible` (default off).
- **Airport forecast overlay (#424)** — the same per-airport forecast markers the
  full forecast map draws, for the snapshot time nearest departure. The fetched
  `ForecastMapResponse` holds every model, so switching model/metric is a recolour,
  not a refetch. Toggles: `mapForecastOverlayVisible` (default **on**) and
  `mapForecastMetric` (default `flight_category`). The forecast horizon and the
  sample hours each day offers come from the server grid (`fetchAvailableDays`) and
  are never hardcoded here — see [forecast-page.md](./forecast-page.md).

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
- `mapAltitudeFt`: for level-dependent metrics (`null` = cruise altitude)
- `mapFrontsVisible` (off), `mapForecastOverlayVisible` (on), `mapForecastMetric`
  (`flight_category`) — the two overlays above
- `cloudStyle`, `activePreset`, `skewtOverlays`, `skewtPrimaryVar`,
  `activeHighlightAdvisoryId` — cross-section/Skew-T lens state, described above

### Key Choices

- **Leaflet over Canvas** — built-in pan/zoom, waypoint tooltips, basemap tiles; simpler for geographic rendering
- **Opaque map colors** — better visibility on complex basemap vs. semi-transparent cross-section bands
- **Metric-driven styling** — no hardcoded thresholds; all defined in `MapMetric` objects
- **Debounced altitude slider** (50ms) — prevents expensive re-renders during drag
- **Segment recoloring** — segments cleared and recreated on metric change (clean event handler lifecycle)

### Shared Scales Module (`scales.ts`)

Single source of truth for all color/opacity functions used by cross-section bands, route graph, and route map:
- Risk colors: icing (cornflower→red), CAT (amber→red), convection (gray→dark red)
- Map colors: `riskMapColor()`, `cloudCoverMapColor()`, `headwindMapColor()`, `capeMapColor()`, `freezingLevelMapColor()`, `ceilingMapColor()`, `temperatureMapColor()`, `crosswindMapColor()`, `agreementMapColor()`, `flightCategoryColor()`
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
- **Two id namespaces for the parcel lines.** The cross-section layers are `lcl` /
  `lfc` / `el`; `lcl-line` / `lfc-line` / `el-line` are the *compare-mode* display
  registry's ids (`compare-layers.ts`). `advisory-presets.ts` uses the correct
  short ids, but `GRAMET_ENABLED` / `WINDY_ENABLED` / `FOREFLIGHT_ENABLED` in
  `layer-registry.ts` still key the suffixed ones — those entries are inert, and
  since all six line layers are `defaultEnabled: true`, the three layer presets do
  **not** turn the parcel lines off. Fix the preset keys (not the layer ids) if this
  is revisited.
- `setVizPreset()` merges (`{...current, ...preset.enabledLayers}`) while
  `resolveAdvisoryPreset()` is **clean-slate** (reset every resettable group off,
  then enable). Don't assume either behaviour when adding a preset family.
- All six horizontal-line layers (0/−10/−20 °C, LCL/LFC/EL) default **on** because
  their factories (`makeTemperatureLayer` / `makeStabilityLayer`) hardcode
  `defaultEnabled: true` for every instance — changing one means parameterising the
  factory, not editing the instance.

## References

- Data models: [data-models.md](./data-models.md) (RouteAnalysesManifest, ElevationProfile)
- Fetch layer: [fetch.md](./fetch.md) (elevation.py, route_walk.py)
- Analysis: [analysis.md](./analysis.md) (sounding analysis pipeline)
- Historical plans: [archive/visualization-plan.md](./archive/visualization-plan.md), [archive/elevation-profile-plan.md](./archive/elevation-profile-plan.md)
