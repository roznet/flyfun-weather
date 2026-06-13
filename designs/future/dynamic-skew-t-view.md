# Dynamic Canvas Skew-T View

> **Status**: Phases 1–4 implemented, including the multi-model overlay (Compare mode — `compare-renderer.ts`) that was originally listed as future here. See [designs/skewt-canvas.md](../skewt-canvas.md) for the current implementation design doc — that doc is authoritative. This file retains the original planning detail only. The only items still genuinely future are **vertical zoom/pan**, **theme integration** (`CrossSectionTheme` → `VizTheme` rename), and **method sync** (icing/cloud preferred-method sync with the cross-section).

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

### Server-Side — All Thermodynamics on Server

The backend already computes everything needed via `analyze_sounding()`:

- **`DerivedLevel`** — per-pressure-level: T, Td, RH, DD, wet bulb, θe, lapse rate, icing indices (Ogimet-DD, Ogimet-NWP, SFIP), wind, omega/w, Richardson number, N², CLW, ICE mixing ratio, precip phase
- **`ThermodynamicIndices`** — LCL, LFC, EL, CAPE (surface/MU/ML), CIN, LI, Showalter, K-index, Total Totals, precipitable water, freezing/−10/−20°C levels, bulk shear, ceiling
- **`SoundingAnalysis`** — cloud layers (DD + NWP), icing zones (4 methods), inversions, convective assessment, vertical motion
- **Parcel path** — captured from the existing parcel profile computation in `analyze_sounding()` (already computed for CAPE/CIN/LCL/LFC/EL — just needs to be persisted rather than discarded)

The client receives JSON and only does rendering. No atmospheric physics in TypeScript.

### Unified Sounding Profile Endpoint

Extend the existing `GET /{timestamp}/sounding-profile/{point_index}/{model}` endpoint with the additional fields needed for the dynamic Skew-T. This serves both web and iOS from a single endpoint, avoiding schema divergence.

All addressing uses `point_index` (matching cross-section indexing). No ICAO-based variant — the cross-section already maps waypoints to point indices, so the client always knows the index.

**Extended `SoundingProfileResponse`**:

```python
class SoundingProfileLevel(BaseModel):
    """A single pressure level in a sounding profile."""
    pressure_hpa: int
    altitude_ft: float | None = None
    temperature_c: float
    dewpoint_c: float | None = None
    wind_speed_kt: float | None = None
    wind_direction_deg: float | None = None
    # New: full DerivedLevel fields for side panels
    relative_humidity_pct: float | None = None
    dewpoint_depression_c: float | None = None
    wet_bulb_c: float | None = None
    theta_e_k: float | None = None
    lapse_rate_c_per_km: float | None = None
    icing_index: float | None = None        # Ogimet-DD
    icing_index_nwp: float | None = None    # Ogimet-NWP
    sfip_100: float | None = None
    cloud_liquid_water_g_m3: float | None = None
    ice_mixing_ratio_g_kg: float | None = None
    richardson_number: float | None = None
    omega_pa_s: float | None = None
    w_fpm: float | None = None

class SoundingProfileResponse(BaseModel):
    """Sounding profile data for client-side Skew-T rendering (web + iOS)."""
    point_index: int
    lat: float
    lon: float
    distance_from_origin_nm: float
    waypoint_icao: str | None = None
    track_deg: float                       # route leg heading for headwind/crosswind
    model: str
    time: str
    levels: list[SoundingProfileLevel]
    cruise_altitude_ft: int | None = None
    # Thermodynamic indices
    indices: dict | None = None
    # Parcel path for CAPE/CIN shading (new)
    parcel_path: list[dict] | None = None  # [{pressure_hpa, temperature_c}, ...]
    # Overlay data from sounding analysis
    cloud_layers: list[dict] = []
    nwp_cloud_layers: list[dict] = []      # new
    icing_zones: list[dict] = []
    icing_ogimet_nwp_zones: list[dict] = []  # new
    sfip_zones: list[dict] = []              # new
    inversion_layers: list[dict] = []
    # Future: ieng_icing_zones, sld_zones (Phase 4)
    convective: dict | None = None           # new
    label: str | None = None                 # ICAO or route point name
```

The iOS app consumes only the fields it knows about — new fields are additive and ignored by older app versions.

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

**Bundle strategy**: The Skew-T code lives in the `briefing` bundle but is loaded via dynamic `import()` on first waypoint click. This avoids increasing initial page load while keeping a single bundle (no separate chunk to manage). esbuild supports code splitting with `--splitting --format=esm`.

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

Same preferred-method grouping as cross-section (clouds: DD vs NWP, icing: 4 methods). A pilot toggling icing method on the cross-section should see the same method on the Skew-T.

### 2. Headwind/Crosswind Side Panel (Default-On)

A vertical strip panel to the right of the Skew-T, sharing the same pressure/altitude Y-axis, showing **headwind/tailwind** and **crosswind** at each pressure level relative to the route leg heading at that point.

**Computation** (client-side, trivial trig):
```typescript
const relativeWind = (windDir - trackDeg) * Math.PI / 180;
const headwind = windSpeed * Math.cos(relativeWind);   // positive = headwind
const crosswind = windSpeed * Math.sin(relativeWind);  // positive = from right
```

`track_deg` comes from the `SoundingProfileResponse` (route leg heading at the selected point, already computed in `RoutePointAnalysis`).

**Display**: Two line plots in the same panel — headwind (green when tailwind, red when headwind) and crosswind (magnitude, amber when exceeding configurable threshold). Zero line clearly marked. This replaces traditional wind barbs with information that's directly actionable for GA flight planning: "what altitude gives me the best tailwind?" and "does crosswind exceed my limits?"

### 3. Additional Side Variable Panels

Vertical strip panels sharing the Skew-T's pressure/altitude Y-axis. Each panel has its own X-axis scaled to the variable's range.

**Variable registry** (all from `SoundingProfileLevel`, no extra computation needed):

| Variable | Unit | Why Useful |
|----------|------|-----------|
| Dewpoint depression | °C | Cloud proxy — low DD = cloud. Simpler than reading T/Td gap |
| Relative humidity | % | Smoother moisture profile than DD |
| Wind speed | kt | Jet stream, LLJ, approach winds |
| Icing index (Ogimet) | 0–100 | Continuous severity, shows exactly where risk peaks |
| SFIP index | 0–100 | Alternative icing view, shows CLW contribution |
| Cloud liquid water | g/m³ | SLD and icing severity driver |
| Ice mixing ratio | g/kg | Glaciation indicator |
| Lapse rate | °C/km | Stability — superadiabatic/isothermal/inversion at a glance |
| Richardson number | — | CAT turbulence, log-scale |
| Vertical velocity (ω) | ft/min | Lift/sink regions, convective cores |
| θe (equiv. pot. temp) | K | Airmass identification, frontal boundaries |

**UX**: A "+" button or dropdown to add panels. Each panel is ~60–80px wide. Max 3–4 visible at once (headwind/crosswind counts as one), scrollable or collapsible. Variable panels auto-sync their Y-axis with the Skew-T (zoom/pan carries over).

### 4. Layer Overlays on the Skew-T

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

These map directly to the cross-section layer colors/semantics via the unified `VizTheme`, reinforcing the visual language across views.

### 5. Multi-Model Overlay

Plot T/Td curves from multiple models on the same Skew-T:

- Each model gets a distinct color (consistent with cross-section model colors from `VizTheme`)
- Toggle individual models on/off
- Divergence zones highlighted — where model T or Td differs by >2°C, shade the gap
- One model is "primary" (full opacity), others are secondary (reduced opacity, thinner lines)
- Overlay bands come from primary model only (to avoid visual chaos)

For multi-model, the client fetches `sounding-profile` for each model individually via `Promise.all()`. No batch endpoint needed — payloads are ~6–10KB each, and parallel fetches add negligible latency.

### 6. Interaction

**UX entry point**: The Skew-T section below the cross-section starts **empty** (placeholder text: "Click a waypoint on the cross-section to view its Skew-T"). When the user clicks a **waypoint** on the cross-section, the Skew-T loads for that `point_index` and selected model. Clicking a different waypoint updates the Skew-T. Interpolated mid-route points do not trigger a Skew-T — only named waypoints. This matches the iOS app's tap-to-inspect pattern.

**Click-to-inspect**: Click or long-hover on any pressure level → tooltip showing all values at that level:
- T, Td, DD, RH, wind speed/direction
- Headwind/crosswind components
- Icing index, SFIP, CLW
- Lapse rate, Ri, θe
- Altitude (ft + FL)
- Which model (if multi-model)

**Linked cursor**: Hover on the Skew-T at a pressure level → horizontal line appears on the cross-section at the same altitude (and vice versa). The cross-section already has a crosshair overlay canvas for this — extend with a horizontal altitude line when the Skew-T is active.

**Zoom/pan**: Optional — vertical zoom to focus on a specific altitude range (e.g., 850–500 hPa for approach). Pan along pressure axis. Side panels follow.

### 7. MetPy Toggle

Keep the existing MetPy static image as a fallback/reference:

- Toggle: "Static (MetPy)" vs "Dynamic (Canvas)"
- Default to dynamic once stable
- MetPy view retains the hodograph companion for reference during development

## Data Flow

```
User clicks waypoint on cross-section
        ↓
Client: dynamic import() of skewt module (first time only)
        ↓
API: GET /{timestamp}/sounding-profile/{point_index}/{model}
        ↓
Backend: reads route_analyses.json + cross_section.json from pack
         assembles SoundingProfileResponse (levels + indices + overlays)
         includes parcel_path from SoundingAnalysis (or computes on-the-fly for old packs)
         includes track_deg from route point
        ↓
Client: SkewTRenderer.setData(data)
        → runs through layer registry
        → renders enabled layers back-to-front on main canvas
        → headwind/crosswind panel renders from levels + track_deg
        → additional side panels render from same level data
        ↓
Hover/click → overlay canvas redraws (cheap)
Cross-section cursor sync via shared event bus
```

For multi-model: fetch `sounding-profile` for each selected model via `Promise.all()`, renderer composites curves.

## Phases

### Phase 1 — Core Canvas Skew-T ✅ Implemented
- Port `SkewTTransform` to TypeScript
- Background line rendering (isotherms, adiabats, mixing ratios)
- T/Td profile curves + parcel path
- CAPE/CIN shading (LCL→EL bounds)
- Level markers (LCL, LFC, EL, freezing)
- Indices panel
- Headwind/crosswind side panel (default-on)
- Axes (pressure left, FL right, temp bottom)
- Extend `sounding-profile` endpoint with parcel path, NWP clouds, full icing zones, track_deg, derived level fields
- Add `parcel_path` to `SoundingAnalysis` model (capture from existing `analyze_sounding()` computation)
- Route point click triggers Skew-T load (any point, not just named waypoints)
- Toggle between MetPy PNG and canvas view (Dynamic/Static buttons)

### Phase 2 — Layer Overlays ✅ Implemented
- Cloud bands (DD + NWP) as toggleable overlays
- Icing zones (Ogimet-NWP, Ogimet-DD, SFIP) as toggleable overlays
- Inversion bands
- Convective zone highlight (LFC→EL)
- Checkbox toggle UI with state persisted to localStorage
- Note: preferred method sync with cross-section not yet implemented

### Phase 3 — Side Variable Panels ✅ Implemented (partial)
- Dual-axis side panel (primary + secondary variable, single fixed-width 110px panel)
- Variable registry with 12 variables (HW/XW, DD, RH, Wind, Ice-DD, Ice-NWP, SFIP, CLW, ICE, Γ, Ri, w, θe)
- Dropdown selectors for primary/secondary variable
- On-the-fly `analyze_sounding()` in endpoint for derived variables (~30ms)
- Inline computation of DD, RH, lapse rate, θe, CLW, ICE, omega/w from raw data
- **Not yet implemented**: Unified `VizTheme` rename + Skew-T theme property groups

### Phase 4 — Interaction ✅ Implemented
- Hover tooltip with all values at pressure level
- Linked cursor: Skew-T ↔ cross-section (altitude-based)
- Multi-model overlay (Compare mode) — `SkewTCompareRenderer` in `compare-renderer.ts`; overlays 2–3 models' T/Td on one diagram, per-model side-panel lines, optional CAPE/CIN + level markers (default off)
- **Not yet implemented**: model divergence shading (gap >2°C), vertical zoom/pan

## Reuse from Cross-Section

| Component | Reuse Strategy |
|-----------|---------------|
| `LayerRegistry` pattern | Same interface, Skew-T-specific layers |
| `CoordTransform` concept | New `SkewTTransform` but same API shape |
| Two-canvas pattern | Main + overlay, same as cross-section |
| Theme system | Rename to `VizTheme`, add `skewt` property group for Skew-T-specific colors (isotherms, adiabats, profile curves, overlay bands) |
| Method group preferences | Shared state — icing/cloud method synced |
| Zustand store | Extend `vizSettings` with Skew-T toggle state |
| Event bus / hover sync | Extend existing hover mechanism with altitude-based crosshair |
| DPI handling | Same `devicePixelRatio` + `ResizeObserver` pattern |
| Smooth rendering | Reuse `drawSmoothLine()` / spline utilities from `layers/base.ts` |

## Reuse from rzskewt (iOS)

| Component | Port Strategy |
|-----------|--------------|
| `SkewTTransform` | Direct port — same math, TS syntax |
| `BackgroundLinesRenderer` | Direct port — precompute line arrays, cache to offscreen canvas |
| `ProfileRenderer` | Simplified — no client-side parcel computation |
| `SkewTConfiguration` | Merge into `VizTheme` system |
| `SoundingProfile` model | Replace with `SoundingProfileResponse` (richer, server-computed) |

The iOS `Thermodynamics.swift` (parcel path, CAPE/CIN integration, LCL search) stays server-side only — the web client receives pre-computed results.

## Backend Changes Needed

1. **Parcel path in `SoundingAnalysis`**: Add `parcel_path: list[dict]` field (`[{pressure_hpa, temperature_c}]`). Capture from the existing parcel profile computation in `analyze_sounding()` — this array is already computed for CAPE/CIN/LCL/LFC/EL but currently discarded after use. Optional field — old packs without it fall back to on-the-fly computation in the endpoint.
2. **Extend `sounding-profile` endpoint**: Add `parcel_path`, `nwp_cloud_layers`, `icing_ogimet_nwp_zones`, `sfip_zones`, `ieng_icing_zones`, `sld_zones`, `convective`, `track_deg`, `label`, and full `DerivedLevel` fields to `SoundingProfileLevel`. Assembles from existing `SoundingAnalysis` data already in the pack.
3. **Extend `SoundingProfileLevel`**: Add all `DerivedLevel` fields (RH, DD, θe, lapse rate, icing indices, CLW, Ri, omega, etc.) so side panels can render without a second API call.

No new endpoints needed. No batch endpoint — multi-model uses parallel individual fetches.

## Resolved Decisions

### Parcel path: capture from existing computation

The parcel profile is already computed inside `analyze_sounding()` for CAPE/CIN/LCL/LFC/EL determination. Rather than adding a new computation, capture the intermediate array and persist it in `SoundingAnalysis.parcel_path`. This is zero additional CPU cost — just saving what was previously discarded.

Storage impact: 28 points × 16 bytes = ~0.5KB per model per waypoint. Current briefing.json is ~250KB, so this is negligible. The endpoint serves ~6–10KB total vs the current 50–200KB PNG — a net bandwidth reduction.

The `route_analyses.json` artifact already excludes `derived_levels` to save space — parcel path would be included since it's a profile-level field, not per-level bloat.

### CAPE/CIN shading: client-side comparison

The client compares `parcelPath[i].temperatureC` vs `levels[i].temperature_c` at each pressure level. Where parcel T > environment T → CAPE fill (red); where parcel T < environment T → CIN fill (blue). This is what rzskewt does in Swift — it's just array comparison, not thermodynamics. Keeps the API simple with no extra fields.

### Wind display: headwind/crosswind panel replaces wind barbs

Traditional wind barbs are a meteorologist's tool — they show raw wind direction/speed but require mental math to determine operational impact. For GA cross-country pilots, the actionable questions are:
- "What altitude gives me the best tailwind?"
- "Does crosswind exceed my limits at any level?"

The headwind/crosswind side panel answers these directly, computed client-side from `wind_speed_kt`, `wind_direction_deg`, and `track_deg`. This is more useful than a hodograph (which shows wind shear for severe convective meteorology — not GA planning) or wind barbs (which require the pilot to mentally decompose vectors against their heading).

### Hodograph: not included

A hodograph visualizes wind shear direction and storm-relative helicity — tools for severe convective forecasting. GA pilots flying cross-country don't use hodographs operationally. The headwind/crosswind panel provides the wind profile information GA pilots actually need. The MetPy static view retains its hodograph companion as a reference during development.

### Side panel persistence: localStorage

Selected side panel variables stored in localStorage, same as cross-section layer toggles. If we later unify all viz preferences server-side, we do it for cross-section + Skew-T + side panels together as one effort.

### Mobile/responsive: desktop-focused, iOS for mobile

On narrow viewports, side panels hidden behind a "Variables" button that opens a bottom drawer. The Skew-T itself renders full-width. However, the primary mobile experience is the iOS companion app (which has its own native Skew-T via rzskewt), so we don't over-invest in mobile web Skew-T UI — functional but minimal.

### View mode toggle

The Skew-T section offers a toggle: **"Static (MetPy)"** vs **"Dynamic (Canvas)"**. This lets us validate the canvas renderer against the reference MetPy implementation during development and gives users a fallback. Default switches to Dynamic once Phase 1 is stable.

### Background grid caching

Isotherms, dry adiabats, moist adiabats, and mixing ratio lines are static for a given viewport size. Render them once to an offscreen canvas and blit on each frame. Re-render only on resize or zoom/pan. This avoids redundant line drawing on every data update.

### Unified theme system

Rename `CrossSectionTheme` → `VizTheme`. Add a `skewt` property group containing colors for:
- Background lines: isotherms, dry adiabats, moist adiabats, mixing ratio lines
- Profile curves: T (red), Td (green), parcel path (black dashed)
- CAPE/CIN fills
- Overlay bands (reuse cross-section overlay colors)
- Headwind/crosswind panel colors (tailwind green, headwind red, crosswind amber)
- Axes and label colors

All three themes (standard, high-contrast, gramet) define these values. Theme switching applies uniformly to cross-section, Skew-T, and side panels.
