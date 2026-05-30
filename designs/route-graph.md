# Route Graph

> 2D chart below the cross-section for plotting scalar weather values along the route

## Intent

Add a configurable 2D graph below the cross-section visualization. The graph shares the same X-axis (distance along route) so any given X position maps to the same geographic location in both views. Users choose what to plot on the left Y-axis and right Y-axis via dropdowns, enabling comparison of two metrics simultaneously (e.g., headwind on the left, temperature on the right).

## Requirements (Issue #17)

1. **X-axis alignment** — The graph's X-axis must be pixel-aligned with the cross-section above it. Same left/right margins, same `distanceToX()` transform.
2. **Dual Y-axes** — Left Y-axis and right Y-axis, each driven by a dropdown selector.
3. **Initial metrics** — Head/tailwind at cruise (left, line) and temperature at cruise (right, line).
4. **Extensible metric registry** — Adding a new metric = one registry entry (id, label, unit, getValue, renderType, color). No renderer changes needed.
5. **Line and bar render types** — Lines for continuous values (temperature, wind), bars for discrete/cumulative values (precipitation).
6. **Hover overlay** — Synced with cross-section. Shows numeric value(s) at cursor position.
7. **Show/hide toggle** — Button below the cross-section to expand/collapse the route graph.
8. **Model-aware** — Uses the same selected model as the cross-section.

## Architecture

```
briefing-main.ts
  └── renderVisualization()
        ├── CrossSectionRenderer          (existing)
        ├── RouteGraphRenderer            (new — below cross-section)
        │   ├── route-graph/renderer.ts   (canvas, coordinate transform, dual Y-axes)
        │   ├── route-graph/axes.ts       (X grid aligned with cross-section, left/right Y-axes)
        │   ├── route-graph/metrics.ts    (metric registry — extensible definitions)
        │   └── route-graph/interaction.ts(hover sync, tooltip)
        └── controls/panel.ts             (extended — route graph dropdowns + toggle)
```

**Shared X-axis transform:** Both renderers use the same `MARGIN.left` (60px) and `MARGIN.right` (50px). The route graph's `distanceToX()` is identical to the cross-section's. Alignment is guaranteed by construction.

## Metric Registry

Each metric is a self-contained definition. Adding a new metric requires only adding one object to the registry array.

```typescript
interface RouteGraphMetric {
  id: string;                                    // unique identifier
  label: string;                                 // display name for dropdown
  unit: string;                                  // axis label (e.g., "kt", "°C", "mm")
  renderType: 'line' | 'bar';                    // how to render
  color: string;                                 // line/bar color
  getValue: (point: VizPoint) => number | null;  // extract value from data
  /** Optional: format value for tooltip display. Defaults to rounding. */
  formatValue?: (v: number) => string;
  /** Optional: suggested Y-axis range [min, max]. Auto-scaled if omitted. */
  suggestedRange?: [number, number];
  /** Optional: zero-line — draw a reference line at y=0 (useful for headwind). */
  showZeroLine?: boolean;
  /** Optional: labels drawn above/below the zero line, e.g. ["Headwind ↑", "Tailwind ↓"]. */
  zeroLineLabels?: [string, string];
}
```

### Initial Metrics

| ID | Label | Unit | Type | Color | Source |
|----|-------|------|------|-------|--------|
| `headwind` | Head/Tailwind | kt | line | `#2563eb` (blue) | `VizPoint.headwindKt` |
| `crosswind` | Crosswind | kt | line | `#7c3aed` (purple) | `VizPoint.crosswindKt` |
| `temperature` | Temperature (2m) | °C | line | `#dc2626` (red) | `model_divergence["temperature_c"]` |
| `precipitation` | Precipitation | mm | bar | `#0ea5e9` (sky) | `model_divergence["precipitation_mm"]` |
| `cloud-cover` | Cloud Cover | % | bar | `#6b7280` (gray) | `VizPoint.cloudCoverTotalPct` |
| `cape` | CAPE | J/kg | bar | `#f59e0b` (amber) | `VizPoint.capeSurfaceJkg` |
| `freezing-level` | Freezing Level | ft | line | `#06b6d4` (cyan) | `altitudeLines.freezingLevelFt` |
| `ceiling-dd` | Ceiling DD | ft AGL | line | `#8b5cf6` (violet) | `soundingCeilingFt` − terrain elevation |
| `ceiling-nwp` | Ceiling NWP | ft AGL | line | `#d946ef` (fuchsia) | `nwpCloudDiag.ceilingFt` − terrain elevation |

**Ceiling metrics** display height above ground level (AGL), not MSL. Both use terrain elevation from `ElevationProfile` for the AGL conversion and cap display at 5000ft AGL.

### Future Metrics (no code changes to renderer needed)

- Wind speed at cruise, visibility, precipitable water, K-index, lifted index, dewpoint depression, QNH, snowfall, rain probability — each is one registry entry.

## Data Flow

```
VizRouteData.points[]          (existing — headwind, crosswind, CAPE, cloud cover)
  + RoutePointAnalysis          (model_divergence → temperature_c, precipitation_mm)
      ↓
RouteGraphMetric.getValue(point) → number | null
      ↓
RouteGraphRenderer.render()
  ├── left metric  → left Y-axis scale + line/bars
  ├── right metric → right Y-axis scale + line/bars
  └── hover overlay (synced with cross-section)
```

**Data extraction:** `VizPoint` is extended with two new optional fields extracted from `model_divergence`:
- `temperatureC: number | null` — surface temperature for the selected model
- `precipitationMm: number | null` — precipitation for the selected model

These are extracted from `RoutePointAnalysis.model_divergence` entries where `variable === "temperature_c"` and `variable === "precipitation_mm"`, using `model_values[selectedModel]`.

## Renderer Design

### Canvas Setup

```
┌─────────────────────────────────────────────────┐
│  Cross-Section Canvas (existing)                │
│  MARGIN: { left: 60, right: 50, top: 20, bottom: 50 }
└─────────────────────────────────────────────────┘
  [▼ Route Graph]  ← toggle button
┌─────────────────────────────────────────────────┐
│  Route Graph Canvas (new)                       │
│  MARGIN: { left: 60, right: 50, top: 8, bottom: 24 }
│                                                 │
│  Left Y │                              │ Right Y│
│  (kt)   │   ~~~~ line ~~~~             │ (°C)   │
│         │          ████ bars           │        │
│    0 ───│──────────────────────────────│────    │
│         │                              │        │
│         └──────────────────────────────┘        │
│           (no X labels — shared with above)     │
└─────────────────────────────────────────────────┘
```

- **Height:** Fixed 150px (compact, not an aspect ratio — the cross-section dominates).
- **X-axis labels omitted** — the cross-section above already draws distance labels. The graph draws only vertical grid lines (aligned) for reference.
- **Left Y-axis:** Scaled to the left metric's data range. Ticks + unit label.
- **Right Y-axis:** Scaled to the right metric's data range. Ticks + unit label.
- **Zero reference line:** Drawn as a dashed horizontal line at Y=0 when `showZeroLine` is true (e.g., headwind where negative = tailwind).

### Coordinate Transform

```typescript
interface RouteGraphTransform {
  distanceToX(distanceNm: number): number;      // same as cross-section
  leftValueToY(value: number): number;           // left metric scale
  rightValueToY(value: number): number;          // right metric scale
  xToDistance(x: number): number;                // inverse
  readonly plotArea: PlotArea;                   // same left/width as cross-section
}
```

### Rendering

**Lines:** Smooth monotone cubic spline (Fritsch-Carlson) connecting `(distanceNm, value)` pairs. Gaps where value is `null`. Consistent with cross-section line rendering.

**Bars:** Drawn from the zero line to the value, centered on each route point, width = half-step to each neighbor. Fill color from metric definition. Opacity 0.6 for readability. Zero/null values skipped.

**Dual rendering:** Left metric drawn first, right metric drawn second. Both clipped to the plot area.

### Interaction

- **Hover:** Vertical crosshair line at cursor X, synced with cross-section hover. Shows a tooltip with both metric values at the nearest point.
- **Click:** Selects route point (same `setSelectedPoint` as cross-section).
- **Selected point indicator:** Blue vertical line matching cross-section style.
- Cross-section and route graph hover events are coordinated: moving the mouse over either canvas updates the crosshair on both.

## Controls

The route graph controls are rendered between the cross-section and the graph:

```
[▼ Route Graph]  Left: [Head/Tailwind ▾]  Right: [Temperature ▾]
```

- **Toggle button** (`▼ Route Graph` / `▶ Route Graph`): Shows/hides the graph canvas. State persisted to localStorage.
- **Left dropdown:** Selects the left Y-axis metric from the registry.
- **Right dropdown:** Selects the right Y-axis metric. Can be set to "None" to show only one metric.
- Dropdowns populated from the metric registry automatically (`getMetricOptions`) — new metrics appear without UI code changes. Option labels come from i18n keys `graph.<id>`, so adding a metric also means adding its translation key.

## State Management

Add to `VizSettings`:

```typescript
interface VizSettings {
  // ... existing fields ...
  routeGraphVisible: boolean;       // show/hide toggle
  routeGraphLeftMetric: string;     // metric id for left Y-axis
  routeGraphRightMetric: string;    // metric id for right Y-axis (or 'none')
}
```

Defaults: `routeGraphVisible: true`, `routeGraphLeftMetric: 'headwind'`, `routeGraphRightMetric: 'temperature'`.

Persisted to localStorage via existing `wb_vizSettings` mechanism.

## File Organization

```
web/ts/visualization/
├── route-graph/
│   ├── metrics.ts          # Metric registry (RouteGraphMetric[])
│   ├── renderer.ts         # RouteGraphRenderer class (canvas, transform, render)
│   ├── axes.ts             # Y-axis drawing (left + right), X grid lines
│   └── interaction.ts      # Hover/click synced with cross-section
├── types.ts                # Extended VizSettings, VizPoint (add temperatureC, precipitationMm)
├── data-extract.ts         # Extract temperature/precipitation from model_divergence
└── controls/panel.ts       # Extended with route graph toggle + dropdowns
```

## Key Choices

- **Same canvas library (native Canvas 2D)** — Guarantees pixel-level X-axis alignment with the cross-section. No framework mismatch.
- **Registry pattern** — Same philosophy as cross-section layers. One array of metric definitions. Zero renderer changes for new metrics.
- **Compact height (150px)** — The cross-section is the primary visualization. The route graph is supplementary and shouldn't dominate vertical space.
- **No X-axis labels** — Avoids duplicate labels. The cross-section's distance axis serves both.
- **Dual Y-axis** — Allows comparing metrics with different units/scales (wind in kt vs temperature in °C).
- **model_divergence for surface values** — Temperature and precipitation are available in model_divergence with per-model values, avoiding backend changes.

## References

- Cross-section implementation: [visualization.md](./visualization.md)
- Cross-section design: [visualization.md](./visualization.md)
- Data models: [data-models.md](./data-models.md)
- Issue: #17
