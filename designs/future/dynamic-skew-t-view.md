# Dynamic Canvas Skew-T View

> Interactive, client-rendered Skew-T log-P diagram for the web app, replacing the static MetPy PNG with a canvas-based view that supports layer overlays, side variable panels, multi-model comparison, and linked interaction with the cross-section.

## Motivation

The current Skew-T is a static MetPy-generated PNG served from the backend. It works but:

- No interactivity (no hover, no click-to-inspect, no layer toggling)
- No multi-model overlay — each model is a separate image
- No connection to the cross-section view (can't hover one and see the other respond)
- Generates on-demand server-side (slow, resource-heavy)
- Can't display the rich per-level derived data already computed by the analysis pipeline

The iOS app already has a custom canvas Skew-T renderer (`rzskewt`). This design ports the same approach to the web, extending it with features that make sense for the interactive web context.

## Architecture

### Server-Side (Option A — all thermodynamics on server)

The backend already computes everything needed via `analyze_sounding()`:

- **`DerivedLevel`** — per-pressure-level: T, Td, RH, DD, wet bulb, θe, lapse rate, icing indices (Ogimet-DD, Ogimet-NWP, SFIP), wind, omega/w, Richardson number, N², CLW, ICE mixing ratio, precip phase
- **`ThermodynamicIndices`** — LCL, LFC, EL, CAPE (surface/MU/ML), CIN, LI, Showalter, K-index, Total Totals, precipitable water, freezing/−10/−20°C levels, bulk shear, ceiling
- **`SoundingAnalysis`** — cloud layers (DD + NWP), icing zones (4 methods), inversions, convective assessment, vertical motion
- **Parcel path** — needs a new endpoint or field: array of (pressure, temperature) points for the parcel ascent curve

The client receives JSON and only does rendering. No atmospheric physics in TypeScript.

**New API endpoint**: `GET /{timestamp}/skewt-data/{location}/{model}` returning:

```typescript
interface SkewTData {
  levels: DerivedLevel[];           // full per-level data
  indices: ThermodynamicIndices;
  parcelPath: {pressureHPa: number, temperatureC: number}[];
  cloudLayers: EnhancedCloudLayer[];
  nwpCloudLayers: EnhancedCloudLayer[];
  icingZones: IcingZone[];          // DD method
  icingOgimetNwpZones: IcingZone[]; // NWP method
  sfipZones: SfipZone[];
  inversions: InversionLayer[];
  convective: ConvectiveAssessment;
  cruiseAltitudeFt: number | null;
  label: string;                    // ICAO or route point name
  modelName: string;
  timeUtc: string;
}
```

This is essentially the existing `SoundingAnalysis` serialized to JSON, plus the raw levels and parcel path.

### Client-Side Rendering

Port the `rzskewt` rendering architecture to TypeScript/Canvas:

```
web/ts/visualization/skewt/
├── skewt-transform.ts          # (T,p) ↔ pixel mapping (log-P Y, skewed X)
├── renderer.ts                 # Orchestrator — two-canvas (main + overlay)
├── layers/
│   ├── background-lines.ts     # Isotherms, isobars, dry/moist adiabats, mixing ratios
│   ├── profile-curves.ts       # T, Td, parcel path lines
│   ├── cape-cin-shading.ts     # CAPE/CIN fill between parcel and environment
│   ├── cloud-overlay.ts        # Cloud layer bands (DD + NWP, toggleable)
│   ├── icing-overlay.ts        # Icing zone bands (multiple methods, toggleable)
│   ├── inversion-overlay.ts    # Inversion bands
│   ├── convective-overlay.ts   # LFC→EL convective zone highlight
│   ├── wind-barbs.ts           # WMO wind barb column
│   ├── level-markers.ts        # LCL/LFC/EL/freezing level markers
│   └── indices-panel.ts        # CAPE, CIN, LI, etc. text display
├── side-panels/
│   ├── variable-panel.ts       # Generic vertical variable plot (shared Y-axis)
│   └── variable-registry.ts    # Registry of plottable variables
├── interaction/
│   ├── hover-inspect.ts        # Click/hover tooltip with all values at level
│   └── cursor-sync.ts          # Linked cursor with cross-section
└── skewt-layer-registry.ts     # Layer toggle registry (reuse cross-section pattern)
```

### Coordinate Transform

Port from `SkewTTransform.swift`:

```
Y = height × (log(pBottom) - log(p)) / (log(pBottom) - log(pTop))
X_base = width × (T - Tmin) / (Tmax - Tmin)
skewFactor = tan(45°) × height / width
X = X_base + skewFactor × (1 - Y/height)
```

Default ranges (matching rzskewt): pBottom=1050, pTop=250, T=−60..+40°C, skew=45°.

## Features

### 1. Layer Toggle System

Reuse the cross-section's `LayerRegistry` pattern. Skew-T layers grouped as:

| Group | Layers | Default On |
|-------|--------|-----------|
| Background | Isotherms, dry adiabats, moist adiabats, mixing ratios | All on |
| Profile | T curve, Td curve, parcel path | All on |
| Shading | CAPE/CIN | On |
| Clouds | DD cloud bands, NWP cloud bands | NWP on |
| Icing | Ogimet-DD, Ogimet-NWP, SFIP, SLD | Ogimet-NWP on |
| Stability | Inversions, convective zone | Inversions on |
| Reference | Freezing level, cruise altitude, LCL/LFC/EL markers | All on |
| Wind | Wind barbs | On |

Same preferred-method grouping as cross-section (clouds: DD vs NWP, icing: 4 methods). A pilot toggling icing method on the cross-section should see the same method on the Skew-T.

### 2. Side Variable Panels

Vertical strip panels to the right of the Skew-T, sharing the same pressure/altitude Y-axis. Each panel has its own X-axis scaled to the variable's range.

**Variable registry** (all from `DerivedLevel`, no extra computation needed):

| Variable | Unit | Why Useful |
|----------|------|-----------|
| Dewpoint depression | °C | Cloud proxy — low DD = cloud. Simpler than reading T/Td gap |
| Relative humidity | % | Smoother moisture profile than DD |
| Cloud fraction (NWP) | % | Direct model cloud prediction vs DD inference |
| Wind speed | kt | Jet stream, LLJ, approach winds |
| Wind shear | kt/1000ft | Turbulence risk (derived from wind speed/direction profile) |
| Icing index (Ogimet) | 0–100 | Continuous severity, shows exactly where risk peaks |
| SFIP index | 0–100 | Alternative icing view, shows CLW contribution |
| Cloud liquid water | g/m³ | SLD and icing severity driver |
| Ice mixing ratio | g/kg | Glaciation indicator |
| Lapse rate | °C/km | Stability — superadiabatic/isothermal/inversion at a glance |
| Richardson number | — | CAT turbulence, log-scale |
| Vertical velocity (ω) | ft/min | Lift/sink regions, convective cores |
| θe (equiv. pot. temp) | K | Airmass identification, frontal boundaries |

**UX**: A "+" button or dropdown to add panels. Each panel is ~60–80px wide. Max 3–4 visible at once, scrollable or collapsible. Variable panels auto-sync their Y-axis with the Skew-T (zoom/pan carries over).

### 3. Layer Overlays on the Skew-T

Semi-transparent altitude bands drawn behind the T/Td curves:

| Overlay | Color | Source | Behavior |
|---------|-------|--------|----------|
| Cloud (DD) | Gray, opacity by coverage (SCT→OVC) | `cloud_layers` | Horizontal bands |
| Cloud (NWP) | Blue-gray, opacity by coverage | `nwp_cloud_layers` | Horizontal bands |
| Icing | Cyan/blue, opacity by severity | `icing_*_zones` | Horizontal bands |
| SLD risk | Orange | `sld_zones` | Horizontal bands |
| Inversions | Purple, opacity by strength | `inversion_layers` | Horizontal bands |
| Convective | Red/orange tint | LFC→EL region | Single band |
| Freezing rain | Yellow | Warm nose above 0°C + precip | Conditional |
| Turbulence (CAT) | Amber | Low Ri zones | Horizontal bands |

These map directly to the cross-section layer colors/semantics, reinforcing the visual language across views.

### 4. Multi-Model Overlay

Plot T/Td curves from multiple models on the same Skew-T:

- Each model gets a distinct color (consistent with cross-section model colors)
- Toggle individual models on/off
- Divergence zones highlighted — where model T or Td differs by >2°C, shade the gap
- One model is "primary" (full opacity), others are secondary (reduced opacity, thinner lines)
- Overlay bands come from primary model only (to avoid visual chaos)

This is the Skew-T equivalent of the cross-section compare mode.

### 5. Interaction

**Click-to-inspect**: Click or long-hover on any pressure level → tooltip showing all values at that level:
- T, Td, DD, RH, wind speed/direction
- Icing index, SFIP, CLW
- Lapse rate, Ri, θe
- Altitude (ft + FL)
- Which model (if multi-model)

**Linked cursor**: Hover on the Skew-T at a pressure level → horizontal line appears on the cross-section at the same altitude (and vice versa). The cross-section already has a crosshair overlay canvas for this.

**Zoom/pan**: Optional — vertical zoom to focus on a specific altitude range (e.g., 850–500 hPa for approach). Pan along pressure axis. Side panels follow.

### 6. MetPy Toggle

Keep the existing MetPy static image as a fallback/reference:

- Toggle: "Static (MetPy)" vs "Dynamic (Canvas)"
- Default to dynamic once stable
- MetPy view retains the hodograph companion (not ported to dynamic view — not prioritized for GA)

## Data Flow

```
User selects waypoint + model
        ↓
API: GET /{timestamp}/skewt-data/{icao}/{model}
        ↓
Backend: reads forecast + sounding analysis from pack
         computes parcel path (new helper in analysis module)
         returns SkewTData JSON
        ↓
Client: SkewTRenderer.setData(data)
        → runs through layer registry
        → renders enabled layers back-to-front on main canvas
        → side panels render from same DerivedLevel array
        ↓
Hover/click → overlay canvas redraws (cheap)
Cross-section cursor sync via shared event bus
```

For multi-model: fetch SkewTData for each selected model, renderer composites curves.

## Phases

### Phase 1 — Core Canvas Skew-T
- Port `SkewTTransform` to TypeScript
- Background line rendering (isotherms, adiabats, mixing ratios)
- T/Td profile curves + parcel path
- CAPE/CIN shading
- Wind barb column
- Level markers (LCL, LFC, EL, freezing)
- Indices panel
- Axes (pressure left, FL right, temp bottom)
- New backend endpoint serving `SkewTData` JSON
- Toggle between MetPy PNG and canvas view
- **Milestone**: feature parity with current MetPy Skew-T

### Phase 2 — Layer Overlays
- Cloud bands (DD + NWP) as toggleable overlays
- Icing zones (Ogimet-NWP, Ogimet-DD, SFIP) as toggleable overlays
- Inversion bands
- Convective zone highlight
- Layer registry with toggle UI (same pattern as cross-section)
- Sync preferred method with cross-section (icing/cloud method selection)
- **Milestone**: overlays match cross-section layer semantics

### Phase 3 — Side Variable Panels
- Generic `VariablePanel` component (vertical plot, shared Y-axis)
- Variable registry with all `DerivedLevel` fields
- Panel add/remove UI
- Start with: DD, wind speed, icing index, lapse rate
- Expand to: RH, CLW, Ri, θe, vertical velocity
- **Milestone**: side panels operational with 4+ variables

### Phase 4 — Interaction & Multi-Model
- Click-to-inspect tooltip
- Linked cursor with cross-section (shared event bus)
- Multi-model overlay (2–3 models on same Skew-T)
- Model divergence highlighting
- Optional: vertical zoom/pan
- **Milestone**: full interactive Skew-T with model comparison

## Reuse from Cross-Section

| Component | Reuse Strategy |
|-----------|---------------|
| `LayerRegistry` pattern | Same interface, Skew-T-specific layers |
| `CoordTransform` concept | New `SkewTTransform` but same API shape |
| Two-canvas pattern | Main + overlay, same as cross-section |
| Theme system | Extend `CrossSectionTheme` with Skew-T colors |
| Method group preferences | Shared state — icing/cloud method synced |
| Zustand store | Extend `vizSettings` with Skew-T toggle state |
| Event bus / hover sync | Extend existing hover mechanism |

## Reuse from rzskewt (iOS)

| Component | Port Strategy |
|-----------|--------------|
| `SkewTTransform` | Direct port — same math, TS syntax |
| `BackgroundLinesRenderer` | Direct port — precompute line arrays |
| `ProfileRenderer` | Simplified — no client-side parcel computation |
| `WindBarbRenderer` | Direct port — WMO barb geometry |
| `SkewTConfiguration` | Merge into theme system |
| `SoundingProfile` model | Replace with `SkewTData` (richer, server-computed) |

The iOS `Thermodynamics.swift` (parcel path, CAPE/CIN integration, LCL search) stays server-side only — the web client receives pre-computed results.

## Backend Changes Needed

1. **Parcel path in `SoundingAnalysis`**: Add `parcel_path: list[AtmosphericPoint]` field. Compute in `analyze_sounding()` using MetPy's `parcel_profile()`. Optional field — old packs without it fall back to on-the-fly computation in the endpoint.
2. **New endpoint**: `GET /{timestamp}/skewt-data/{icao}/{model}` — returns `SkewTData` JSON (levels + indices + parcel path + overlays). Assembles from existing `SoundingAnalysis` + `DerivedLevel` data already in the pack.
3. **Route point variant**: `GET /{timestamp}/skewt-data/route/{point_index}/{model}` — same data for interpolated route points.
4. **Multi-model batch** (Phase 4): `GET /{timestamp}/skewt-data/{icao}?models=icon_eu,ecmwf_ifs` — returns multiple models in one response to avoid waterfall requests for multi-model overlay.

## Resolved Decisions

### Parcel path: pipeline-time storage (Option A)

Add `parcel_path: list[{pressure_hpa, temperature_c}]` to `SoundingAnalysis`, computed during `analyze_sounding()`.

**Rationale**: Current pressure level counts per model: GFS 28, UKMO/GEM 20, ICON/MF 19, ECMWF 13 (with GRIB enrichment interpolating to the 28-level extended grid). A parcel path follows these same levels — 28 points × 16 bytes = **~0.5KB** per model per waypoint. Current briefing.json is ~250KB, so this is negligible. The new Skew-T JSON endpoint serves ~6–10KB total (levels + indices + overlays + parcel path) vs the current 50–200KB PNG — a net bandwidth reduction. Computing once at pipeline-time means both web and iOS can consume it without duplicating MetPy/Swift thermodynamics. No migration needed for existing packs — the field is optional and the endpoint can fall back to on-the-fly computation for old packs.

The `route_analyses.json` artifact already excludes `derived_levels` to save space — parcel path would be included since it's a profile-level field, not per-level bloat.

### CAPE/CIN shading: client-side comparison (Option A)

The client compares `parcelPath[i].temperatureC` vs `levels[i].temperature_c` at each pressure level. Where parcel T > environment T → CAPE fill (red); where parcel T < environment T → CIN fill (blue). This is what rzskewt does in Swift — it's just array comparison, not thermodynamics. Keeps the API simple with no extra fields.

### Side panel persistence: localStorage (Option A)

Selected side panel variables stored in localStorage, same as cross-section layer toggles. If we later unify all viz preferences server-side, we do it for cross-section + Skew-T + side panels together as one effort.

### Mobile/responsive: desktop-focused, iOS for mobile (Option A)

On narrow viewports, side panels hidden behind a "Variables" button that opens a bottom drawer. The Skew-T itself renders full-width. However, the primary mobile experience is the iOS companion app (which has its own native Skew-T via rzskewt), so we don't over-invest in mobile web Skew-T UI — functional but minimal.

### View mode toggle

The Skew-T section offers a toggle: **"Static (MetPy)"** vs **"Dynamic (Canvas)"**. This lets us validate the canvas renderer against the reference MetPy implementation during development and gives users a fallback. Default switches to Dynamic once Phase 1 is stable. The static mode retains the hodograph companion image; the dynamic mode drops the hodograph (not prioritized for GA).
