# Dynamic Canvas Skew-T

> Client-rendered Skew-T log-P diagram with overlay bands, dual-axis side panel, hover tooltip, and linked cursor with the cross-section

## Intent

Replace the static MetPy-generated PNG Skew-T with an interactive canvas-based view. The server computes all thermodynamics; the client only renders. Clicking any route point on the cross-section loads its sounding profile and renders the Skew-T with overlays and side panels. A Dynamic/Static toggle preserves the MetPy fallback.

## Architecture

```
web/ts/visualization/skewt/
├── types.ts              # SoundingProfileData, config types
├── atmo-utils.ts         # Shared altitude↔pressure conversions
├── skewt-transform.ts    # (T,p) ↔ pixel mapping (log-P Y, skewed X)
├── thermodynamics.ts     # Background line generators (isotherms, adiabats, mixing ratios)
├── background-lines.ts   # Cached offscreen rendering of background grid
├── profile-curves.ts     # T, Td, parcel path lines + CAPE/CIN shading
├── axes.ts               # Pressure/FL axes, temperature labels, level markers, indices panel
├── overlay-bands.ts      # Cloud/icing/inversion/convective altitude bands
├── overlay-controls.ts   # Checkbox toggles + side panel dropdown UI
├── variable-panel.ts     # Dual-axis side panel with 12+ variable registry
├── interaction.ts        # Hover tooltip + linked cursor
└── renderer.ts           # Main orchestrator — dual canvas, layout, render pipeline
```

**Dual canvas pattern** (same as cross-section): main canvas for layers, overlay canvas for hover crosshair. The overlay redraws cheaply on mouse move without re-rendering the full diagram.

## Data Flow

```
User clicks route point on cross-section
        ↓
briefing-main.ts: loadSkewtData() fetches sounding-profile JSON
        ↓
API: GET /{timestamp}/sounding-profile/{point_index}/{model}
        ↓
Backend: reads route_analyses.json + cross_section.json
         assembles SoundingProfileResponse (levels + indices + overlays)
         runs analyze_sounding() on-the-fly for derived_levels (~30ms)
         computes DD, RH, lapse rate, θe, CLW, omega/w inline
        ↓
Client: SkewTRenderer.setData(data)
        → background grid (cached offscreen canvas, blit at 1:1 pixels)
        → overlay bands (clouds, icing, inversions, convective)
        → level markers (LCL, LFC, EL, freezing, cruise)
        → profile curves + CAPE/CIN shading (LCL→EL only)
        → axes + indices panel
        → side panel (dual-axis variable plot)
```

## Coordinate Transform

Ported from rzskewt `SkewTTransform.swift`:

```
Y = bottom - (ln(pBottom) - ln(p)) / (ln(pBottom) - ln(pTop)) × height
X = left + ((T - Tmin) / tRange + logFrac × skewFactor) × width
skewFactor = tan(45°) × height / width
```

Default ranges: pBottom=1050, pTop=250, T=-60..+40°C, skew=45°.

## Overlay Bands

Semi-transparent horizontal bands drawn behind the profile curves:

| Overlay | Source Field | Default On |
|---------|-------------|-----------|
| Clouds (NWP) | `nwp_cloud_layers` | Yes |
| Clouds (DD) | `cloud_layers` | No |
| Icing (Ogimet-NWP) | `icing_ogimet_nwp_zones` | Yes |
| Icing (Ogimet-DD) | `icing_zones` | No |
| Icing (SFIP) | `sfip_zones` | No |
| Inversions | `inversion_layers` | Yes |
| Convective zone | LFC→EL from `indices` | No |

Layers without pressure bounds fall back to standard-atmosphere altitude→pressure conversion. Toggle state persisted to `localStorage('wb_skewtOverlays')`.

## Side Panel

Single fixed-width panel (110px) with dual-axis support:
- **Primary variable**: line + bottom X-axis labels (default: HW/XW)
- **Secondary variable**: line + top X-axis labels (default: none)

Two dropdowns in the controls. Selection persisted to `localStorage('wb_skewtSidePanels')`.

**Variable registry** (14 variables, displayed in `<optgroup>` blocks via `VARIABLE_GROUPS`):

| Variable | Group | Source | Computed |
|----------|-------|--------|----------|
| HW/XW (headwind/crosswind) | Wind | wind + track_deg | Client-side trig |
| Wind speed | Wind | `wind_speed_kt` | Raw data |
| DD (dewpoint depression) | Moisture & Cloud | T - Td | Inline in endpoint |
| CC (cloud cover) | Moisture & Cloud | `cloud_area_fraction_pct` (ECMWF `cc` / ICON `clc`; null for GFS) | GRIB per-level |
| RH (relative humidity) | Moisture & Cloud | Magnus formula | Inline in endpoint |
| CLW (cloud liquid water) | Moisture & Cloud | GRIB CLWMR | Inline conversion |
| ICE (ice mixing ratio) | Moisture & Cloud | GRIB ICMR | Inline conversion |
| Icing (Ogimet-DD) | Icing | `icing_index` | On-the-fly analyze_sounding |
| Icing (Ogimet-NWP) | Icing | `icing_index_nwp` | On-the-fly analyze_sounding |
| SFIP | Icing | `sfip_100` | On-the-fly analyze_sounding |
| Γ (lapse rate) | Stability & Vertical | Adjacent levels | Inline in endpoint |
| θe (equiv. pot. temp) | Stability & Vertical | Bolton approximation | Inline in endpoint |
| Ri (Richardson number) | Stability & Vertical | `richardson_number` | On-the-fly analyze_sounding |
| w (vertical velocity) | Stability & Vertical | omega Pa/s | Inline conversion |

`VARIABLE_GROUPS` defines display order; `renderGroupedOptions()` in `overlay-controls.ts` emits one `<optgroup label="…">` block per group, filtering registry by `VariableDef.group`. CC plots only for ECMWF/ICON (per-level cc not delivered by GFS — line gap, no fallback).

## Interaction

- **Hover tooltip**: horizontal crosshair + tooltip with all values at nearest pressure level (T, Td, DD, RH, **CC**, wind, HW/XW, θe, lapse rate, icing, SFIP, w, altitude/FL)
- **Linked cursor (Skew-T → cross-section)**: `onHoverAltitude` callback fires → cross-section draws horizontal line at that altitude
- **Linked cursor (cross-section → Skew-T)**: cross-section `onHoverAltitude` callback → `skewtInteraction.setExternalHoverAlt()` draws blue dashed line

## Backend: Sounding Profile Endpoint

`GET /{timestamp}/sounding-profile/{point_index}/{model}` — extended for dynamic Skew-T:

- `SoundingProfileLevel`: all `DerivedLevel` fields (RH, DD, θe, lapse rate, icing indices, CLW, Ri, omega, w)
- `parcel_path`: captured from MetPy parcel profile computation (was previously discarded)
- `track_deg`, `label`: from route point analysis
- `nwp_cloud_layers`, `icing_ogimet_nwp_zones`, `sfip_zones`, `convective`: from sounding analysis
- **On-the-fly analysis**: when `derived_levels` aren't in stored JSON (excluded from `route_analyses.json` to save space), runs `analyze_sounding()` on-the-fly (~30ms) to get icing indices, Richardson number, etc.

`ParcelPathPoint` model added to `SoundingAnalysis` — captures the MetPy parcel profile array (previously computed for CAPE/CIN but discarded). Zero extra CPU cost.

## Key Choices

- **Server-side thermodynamics**: all physics stays in Python/MetPy. Client does only rendering + trivial trig (HW/XW)
- **On-the-fly analysis**: `derived_levels` excluded from `route_analyses.json` (saves ~50KB per model per point). Endpoint runs `analyze_sounding()` lazily — ~30ms, fast enough
- **Background grid caching**: isotherms/adiabats rendered once to offscreen `HTMLCanvasElement`, blitted at 1:1 pixel ratio (bypasses main canvas DPR transform)
- **CAPE/CIN shading bounds**: only between LCL and EL (no shading below LCL or above EL)
- **Any route point**: clicking any point (waypoint or interpolated) loads its Skew-T — not restricted to named waypoints
- **Dual-axis side panel**: single fixed-width panel avoids squeezing the Skew-T diagram. Primary + secondary variable with separate scales

## Still Future

- **Multi-model overlay**: plot T/Td from 2-3 models on same Skew-T with color coding and divergence highlighting
- **Vertical zoom/pan**: focus on specific altitude ranges
- **Theme integration**: rename `CrossSectionTheme` → `VizTheme` with Skew-T-specific color groups across all three themes
- **Method sync**: sync icing/cloud preferred method between cross-section and Skew-T overlay toggles

See [future/dynamic-skew-t-view.md](./future/dynamic-skew-t-view.md) for the original design including these planned features.

## Gotchas

- Background grid cache key must include transform params (pBottom, pTop, tMin, tMax), not just canvas size — matters when zoom/pan is added
- MetPy `parcel_profile()` returns Kelvin, not Celsius — conversion needed before storing in `ParcelPathPoint`
- Cloud/icing layers from API may have `base_pressure_hpa: null` — must fall back to altitude→pressure via standard atmosphere
- API returns lowercase risk strings (`none`, `light`) — color lookups need both cases
- FL labels rendered between plot right edge and side panel — `FL_LABEL_WIDTH` (40px) gap reserved in layout

## References

- Visualization system: [visualization.md](./visualization.md)
- Data models: [data-models.md](./data-models.md) (SoundingAnalysis, ParcelPathPoint)
- Analysis: [analysis.md](./analysis.md) (analyze_sounding pipeline)
- iOS Skew-T: rzskewt package (github.com/roznet/rzskewt)
- Original design: [future/dynamic-skew-t-view.md](./future/dynamic-skew-t-view.md)
