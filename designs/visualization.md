# Cross-Section Visualization

> Canvas-rendered interactive cross-section showing weather layers along a flight route

## Intent

Provide a visual vertical cross-section of the route, showing clouds, icing, turbulence, inversions, convective towers, terrain, and key altitude references. All data comes from the `RouteAnalysesManifest` (per-model sounding analysis at each route point) and `ElevationProfile`.

## Architecture

```
briefing-store (Zustand)
  ├── routeAnalyses: RouteAnalysesManifest
  ├── elevationProfile: ElevationProfile
  └── vizSettings: VizSettings (persisted to localStorage)
        ↓
data-extract.ts  → extractVizData() → VizRouteData
        ↓
CrossSectionRenderer
  ├── axes.ts          (distance/altitude axes, grid lines, waypoint markers)
  ├── layer-registry.ts (ordered list of all layers)
  ├── layers/*.ts      (13 individual layer renderers)
  └── interaction.ts   (hover crosshair, click-to-select point, tooltip)
        ↓
controls/panel.ts  (layer toggles, model selector, render mode)
```

**Two canvases:** main (layers) + overlay (crosshair/selection indicator). Overlay redraws on mouse move without re-rendering expensive layers.

## Layers

Rendered back-to-front in this order:

| Layer | Group | File | Default | Description |
|-------|-------|------|---------|-------------|
| Terrain fill | terrain | `terrain-fill.ts` | on | SRTM elevation, earth-tone gradient |
| Convective BG | convection | `convective-bg.ts` | on | Tower columns LCL→EL, hatching, CB labels, anvil strip |
| Cloud bands | clouds | `cloud-bands.ts` | on | Opacity from coverage (SCT/BKN/OVC) |
| Icing bands | icing | `icing-bands.ts` | on | Colored by risk (light→severe) |
| CAT bands | turbulence | `cat-bands.ts` | on | Orange-red bands by Richardson number |
| Inversion bands | turbulence | `inversion-bands.ts` | on | Purple bands by strength |
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
- **Separate overlay canvas** — hover/crosshair redraws cheaply without re-rendering all layers
- **ResizeObserver** — responsive sizing with device pixel ratio handling for crisp rendering
- **Clip to plot area** — all layer renders are clipped so bands/fills don't overflow axes
- **Monotone cubic for terrain** — Fritsch-Carlson tangents prevent overshoot (important for elevation data)

## Convective Tower Rendering

The convective background layer (`convective-bg.ts`) is the most complex:
- **Tower columns**: drawn from LCL → estimated tower top for each route point with risk ≥ LOW
- **Tower top estimation**: uses thermodynamic EL if available and reliable (>3000ft above LCL), else estimates from `max(freezingLevel, −10°C, −20°C)` altitude lines as fallbacks
- **Anvil strip**: 500ft strip at tower top (darker shade)
- **Hatching**: diagonal lines on HIGH/EXTREME risk
- **CB labels**: "CB" text at tower top for HIGH+ risk
- **Color gradient**: marginal (light green) → low (yellow) → moderate (orange) → high (red) → extreme (purple)

## Interaction

- **Hover**: vertical crosshair line follows mouse, shows distance/time at cursor
- **Click**: selects closest route point, highlights with indicator on overlay canvas
- **Tooltip**: shows waypoint name (if named) + distance + altitude at hover position

## Metrics UI System

Alongside the visualization, a catalog-driven metrics system provides contextual help:

- `data/metrics-catalog.json`: 25+ metrics with thresholds, units, vibes, guidance
- `helpers/metrics-helper.ts`: threshold matching, HTML rendering for annotation rows
- `components/info-popup.ts`: modal popup with metric details and threshold scale
- Briefing page shows metrics in tiered display (essential/detailed/diagnostic) with info icons

## Gotchas

- Y-axis is altitude in feet (0 at bottom), not pressure — `altitudeToPressureHpa()` in scales.ts for any pressure conversions
- `VizPoint.altitudeLines` values can be `null` (model doesn't provide that level)
- Convective tower top fallback logic is deliberately conservative — prefers undersized towers over misleading oversized ones
- Layer rendering must handle empty arrays gracefully (no data for that layer/model)

## References

- Data models: [data-models.md](./data-models.md) (RouteAnalysesManifest, ElevationProfile)
- Fetch layer: [fetch.md](./fetch.md) (elevation.py, route_walk.py)
- Analysis: [analysis.md](./analysis.md) (sounding analysis pipeline)
- Implementation plans: [visualization-plan.md](./visualization-plan.md), [elevation-profile-plan.md](./elevation-profile-plan.md)
