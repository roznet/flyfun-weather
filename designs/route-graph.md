# Route Graph

> 2D chart below the cross-section for plotting scalar weather values along the route

## Intent

Add a configurable 2D graph below the cross-section visualization. The graph shares the same X-axis (distance along route) so any given X position maps to the same geographic location in both views. Users choose what to plot on the left Y-axis and right Y-axis via dropdowns, enabling comparison of two metrics simultaneously (e.g., headwind on the left, temperature on the right).

## Requirements (Issue #17)

1. **X-axis alignment** — The graph's X-axis must be pixel-aligned with the cross-section above it. Same left/right margins, same `distanceToX()` transform.
2. **Dual Y-axes** — Left Y-axis and right Y-axis, each driven by a dropdown selector.
3. **Initial metrics** — Head/tailwind at cruise (left, line) and temperature at cruise (right, line).
4. **Extensible metric registry** — Adding a new metric = one registry entry (id, unit, getValue, renderType, color) plus a `graph.<id>` i18n key. No renderer changes needed. The display name is resolved from i18n via `getMetricLabel(id)` (there is no `label` field on the metric).
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
  id: string;                                    // unique identifier (also the i18n key suffix)
  unit: string;                                  // axis label (e.g., "kt", "°C", "mm"); a getter for region-aware units (QNH)
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
| `isa-dev` | ISA Deviation (cruise) | °C | line | `#ea580c` (orange) | `VizPoint.isaDevC` (cruise temp − ISA std) |
| `precipitation` | Precipitation | mm | bar | `#0ea5e9` (sky) | `model_divergence["precipitation_mm"]` |
| `cloud-cover` | Cloud Cover | % | bar | `#6b7280` (gray) | `VizPoint.cloudCoverTotalPct` |
| `cape` | CAPE | J/kg | bar | `#f59e0b` (amber) | `VizPoint.capeSurfaceJkg` |
| `cin` | CIN | J/kg | bar | `#0d9488` (teal) | `VizPoint.cinSurfaceJkg` |
| `qnh` | QNH / Altimeter | hPa / inHg | line | `#475569` (slate) | `model_divergence["pressure_msl_hpa"]` |
| `freezing-level` | Freezing Level | ft | line | `#06b6d4` (cyan) | `altitudeLines.freezingLevelFt` |
| `ceiling-dd` | Ceiling DD | ft AGL | line | `#8b5cf6` (violet) | `soundingCeilingFt` − terrain elevation |
| `ceiling-nwp` | Ceiling NWP | ft AGL | line | `#d946ef` (fuchsia) | `nwpCloudDiag.ceilingFt` − terrain elevation |

**ISA deviation** plots cruise-level temperature minus the ISA standard at that level — the zero line is "on-ISA", above = warmer (degraded density altitude / climb / TAS). It is derived in `data-extract.ts` from a separate `VizPoint.temperatureCruiseC` (cruise-level temperature, kept distinct from the surface `temperatureC`), so the surface and cruise temperatures stay independent.

**Ceiling metrics** display height above ground level (AGL), not MSL. Both use terrain elevation from `ElevationProfile` for the AGL conversion and cap display at 5000ft AGL.

**CIN** is convention-negative (it inhibits convection), so its bars hang *below* the zero line as the "cap" beside CAPE's upward bars; it sets `showZeroLine` so the reference line is drawn at the top of the `[-300, 0]` range.

**QNH is region-aware** (the only such metric). It carries canonical hPa on `VizPoint.qnhHpa` and converts at the display edge via `units.ts` (`qnhDisplayValue` / `qnhUnitLabel`): Europe shows **QNH in hPa**, the US shows **Altimeter in inHg**. Both the `unit` field (a getter) and the name (`getMetricLabel`, key `graph.altimeter` vs `graph.qnh`) switch on `getUnitsRegion()`, so the axis label, ticks, tooltip, and dropdown all agree. The model-comparison table keeps it as canonical-hPa "QNH" (advanced tier, hidden by default).

### Future Metrics (no code changes to renderer needed)

- Wind speed at cruise, visibility, precipitable water, K-index, lifted index, dewpoint depression, snowfall, rain probability — each is one registry entry.

## Data Flow

```
VizRouteData.points[]          (existing — headwind, crosswind, CAPE, CIN, cloud cover)
  + RoutePointAnalysis          (model_divergence → temperature_c, precipitation_mm, pressure_msl_hpa)
      ↓
RouteGraphMetric.getValue(point) → number | null
      ↓
RouteGraphRenderer.render()
  ├── left metric  → left Y-axis scale + line/bars
  ├── right metric → right Y-axis scale + line/bars
  └── hover overlay (synced with cross-section)
```

**Data extraction:** `VizPoint` carries surface fields extracted from `model_divergence` (per-model values picked by `selectedModel`):
- `temperatureC: number | null` — surface temperature
- `precipitationMm: number | null` — precipitation
- `qnhHpa: number | null` — mean-sea-level pressure (the QNH proxy), canonical hPa

These come from `RoutePointAnalysis.model_divergence` entries where `variable` is `"temperature_c"`, `"precipitation_mm"`, or `"pressure_msl_hpa"`, using `model_values[selectedModel]`. `model_divergence` is the only per-model surface store at a route point, so any new per-model surface metric is collected there in `tasks/analyze.py` (with an agreement threshold in `analysis/comparison.py`). CAPE and CIN instead come straight off the selected model's sounding indices (`VizPoint.capeSurfaceJkg` / `cinSurfaceJkg`).

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
- Dropdowns populated from the metric registry automatically (`getMetricOptions`) — new metrics appear without UI code changes. Option labels (and the tooltip name) come from `getMetricLabel(id)` → i18n key `graph.<id>`, so adding a metric also means adding its translation key. `getMetricLabel` is the single place that can vary a name by region (QNH→Altimeter); both the dropdown and `interaction.ts` tooltip call it so they never drift.

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
│   ├── metrics.ts          # Metric registry + getMetricLabel (region-aware names)
│   ├── renderer.ts         # RouteGraphRenderer class (canvas, transform, render)
│   ├── axes.ts             # Y-axis drawing (left + right), X grid lines
│   └── interaction.ts      # Hover/click synced with cross-section (tooltip via getMetricLabel)
├── types.ts                # Extended VizSettings, VizPoint (temperatureC, temperatureCruiseC, isaDevC, precipitationMm, qnhHpa)
├── data-extract.ts         # Extract temperature/precipitation/pressure_msl from model_divergence; derive isaDevC from cruise temp
├── route-graph/constants.ts # MARGIN (left 60 / right 50 / top 8 / bottom 24) shared by renderer + axes
└── controls/panel.ts       # Extended with route graph toggle + dropdowns

web/ts/units.ts             # Region-aware QNH conversion (qnhDisplayValue / qnhUnitLabel) — shared, not visualization-local
```

Backend per-model surface plumbing: `tasks/analyze.py` collects the divergence variable, `analysis/comparison.py` holds its agreement threshold, and the comparison-table registration lives in `web/ts/data/metrics-{catalog,display}.json` + `helpers/metrics-helper.ts` (`VARIABLE_TO_METRIC`).

## Key Choices

- **Same canvas library (native Canvas 2D)** — Guarantees pixel-level X-axis alignment with the cross-section. No framework mismatch.
- **Registry pattern** — Same philosophy as cross-section layers. One array of metric definitions. Zero renderer changes for new metrics.
- **Compact height (150px)** — The cross-section is the primary visualization. The route graph is supplementary and shouldn't dominate vertical space.
- **No X-axis labels** — Avoids duplicate labels. The cross-section's distance axis serves both.
- **Dual Y-axis** — Allows comparing metrics with different units/scales (wind in kt vs temperature in °C).
- **model_divergence for surface values** — Temperature, precipitation, and QNH (`pressure_msl_hpa`) are carried per-model in model_divergence. Adding QNH there meant one collector line in `analyze.py` rather than a new per-point store, at the cost of it also appearing in the comparison table (parked at advanced tier).
- **Canonical units on VizPoint, convert at the edge** — `qnhHpa` stays in hPa; region conversion happens in `getValue`/`unit`/`getMetricLabel` via `units.ts`, so a units-preference change needs no data re-extraction and the comparison table can keep showing canonical hPa.

## References

- Cross-section implementation: [visualization.md](./visualization.md)
- Cross-section design: [visualization.md](./visualization.md)
- Data models: [data-models.md](./data-models.md)
- Issue: #17
