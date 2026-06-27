# Dynamic Canvas Skew-T

> Client-rendered Skew-T log-P diagram with overlay bands, dual-axis side panel, hover tooltip, and linked cursor with the cross-section

## Intent

Replace the static MetPy-generated PNG Skew-T with an interactive canvas-based view. The server computes all thermodynamics; the client only renders. Clicking any route point on the cross-section loads its sounding profile and renders the Skew-T with overlays and side panels. A Dynamic/Static toggle preserves the MetPy fallback.

## Architecture

```
web/ts/visualization/skewt/
├── types.ts              # SoundingProfileData, config types
├── skewt-transform.ts    # (T,p) ↔ pixel mapping (log-P Y, skewed X)
├── thermodynamics.ts     # Background line generators (isotherms, adiabats, mixing ratios)
├── background-lines.ts   # Cached offscreen rendering of background grid
├── profile-curves.ts     # T, Td, parcel path lines + CAPE/CIN shading
├── axes.ts               # Pressure/FL axes, temperature labels, level markers, indices panel
├── overlay-bands.ts      # Cloud/icing/inversion/convective altitude bands
├── overlay-controls.ts   # Checkbox toggles + side panel dropdown UI
├── variable-panel.ts     # Dual-axis side panel registry + single & compare renderers
├── interaction.ts        # Hover tooltip + linked cursor (single + compare)
├── renderer.ts           # Main orchestrator — dual canvas, layout, render pipeline
└── compare-renderer.ts   # Multi-model overlay: T/Td per model on one diagram
```

Shared altitude↔pressure conversions (`altitudeToPressure`, `pressureToAltitudeFt`) live in `web/ts/utils/atmo.ts` and are imported by `overlay-bands.ts`, `axes.ts`, and `interaction.ts`.

**Dual canvas pattern** (same as cross-section): main canvas for layers, overlay canvas for hover crosshair. The overlay redraws cheaply on mouse move without re-rendering the full diagram.

**Three modes** (toggle in viz controls): Dynamic (canvas, single model), Compare (multi-model overlay), Static (MetPy PNG fallback). `briefing-main.ts` drives all three.

## Data Flow

```
User clicks route point on cross-section
        ↓
briefing-main.ts: loadSkewtData() fetches sounding-profile JSON
        ↓
API: GET /{timestamp}/sounding-profile/{point_index}/{model}
        ↓
Backend: load_or_build_sounding_profile() —
         reads the gzipped sidecar (sounding_profiles.json.gz) if present,
         else falls back to _build_sounding_profile() which reads
         route_analyses.json + cross_section.json and recomputes on the fly
         assembles SoundingProfileResponse (levels + indices + overlays)
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

Each variable and overlay carries a `metricId` into the shared metric catalog; `overlay-controls.ts` renders info (ⓘ) buttons next to the dropdowns, overlay toggles, and a fixed indices row (CAPE/CIN/LI/PW/0°C). The primary/secondary info button's `data-metric` updates live on dropdown change.

## Advisory-preset lenses (#308)

The hazard-oriented advisory presets (Icing / Clouds / Convective / Turbulence / VFR /
IFR, plus a neutral **Basic/Learn**) extend across the Skew-T too, so one lens configures
map + cross-section + Skew-T coherently (see [visualization.md](./visualization.md)).
`SkewTRenderer.applyPreset({ overlays, primaryVar })` applies a clean-slate overlay map
(only the lens's bands shaded; Basic = all off) + the primary side-panel variable in one
shot, persisting to the renderer's own localStorage so a later manual toggle starts from
the lens. `briefing-main.applySkewtPresetState()` pushes the store's `vizSettings.skewtOverlays`
/ `skewtPrimaryVar` into the renderer whenever an advisory preset becomes active (incl. on
first render / deep-link). A manual overlay/var edit fires `onUserEdit` → `markVizCustom()`,
dropping the preset label to "Custom".

**"Help me read this graph"** button (`overlay-controls.ts` `onHelp`) opens a popup with
the active lens's `interpretation` blurb + this sounding's key computed values (CAPE/CIN,
0 °C level, LCL/LFC/EL) + which bands are currently shaded. The same interpretation text
is reused verbatim as MCP explanation context — write once, human and assistant read the
same words. Omega belongs on **Convective's** side panel (`w_fpm`, positive = up).

## Interaction

- **Hover tooltip**: horizontal crosshair + tooltip with all values at nearest pressure level (T, Td, DD, RH, **CC**, wind, HW/XW, θe, lapse rate, icing, SFIP, w, altitude/FL)
- **Linked cursor (Skew-T → cross-section)**: `onHoverAltitude` callback fires → cross-section draws horizontal line at that altitude
- **Linked cursor (cross-section → Skew-T)**: cross-section `onHoverAltitude` callback → `skewtInteraction.setExternalHoverAlt()` draws blue dashed line

## Compare Mode

`SkewTCompareRenderer` (+ `attachSkewTCompareInteraction`, `renderSkewtCompareControls`) overlays T/Td curves from 2-3 models on a single diagram, each in a distinct color (solid = T, dashed = Td). Differs from the single-model renderer:

- No overlay bands (clouds/icing/inversions) — would be ambiguous across models
- Level markers (LCL/LFC/EL) and CAPE/CIN are optional toggles, **default off**; when on they use the primary model's indices
- Side panel (`renderCompareSidePanel`) draws one line per model on a unified per-variable range; primary thicker/opaque, secondaries thinner/translucent
- Controls: model chips (toggle + star marks primary), CAPE/CIN + levels checkboxes, same side-panel dropdowns
- Persistence keyed separately: `wb_skewtCompareSidePanels`, `wb_skewtCompareCapeCin`, `wb_skewtCompareLevelMarkers`

## Backend: Sounding Profile Endpoint

`GET /{timestamp}/sounding-profile/{point_index}/{model}` — serves the dynamic Skew-T (web + iOS). The models, builder, and sidecar I/O all live in `storage/sounding_profiles.py` (deliberately neutral — imported by both `api/packs.py` and `tasks/artifacts.py`, no `tasks → api` cycle).

- `SoundingProfileResponse` / `SoundingProfileLevel`: all `DerivedLevel` fields (RH, DD, θe, lapse rate, icing indices, CLW, ICE, CC, Ri, omega, w)
- `parcel_path` (`ParcelPathPointResponse`): captured from MetPy parcel profile computation (was previously discarded)
- `track_deg`, `label`: from route point analysis
- `nwp_cloud_layers`, `icing_ogimet_nwp_zones`, `sfip_zones`, `convective`: from sounding analysis

**Sidecar-first, recompute-fallback** (`load_or_build_sounding_profile`):

- At refresh time, `tasks/artifacts.py` writes the gzipped sidecar `sounding_profiles.json.gz` from the in-memory route-analyses manifest — while `derived_levels` are still intact, before they're stripped from `route_analyses.json` for the online viewer.
- The endpoint reads the sidecar and returns the pre-shaped profile without recompute.
- When the sidecar is absent (old packs, or after T1 retention), `_build_sounding_profile()` reads `route_analyses.json` + `cross_section.json` and recomputes the MetPy sounding analysis (`analyze_sounding()`) on the fly (~30ms). Nothing hard-depends on the sidecar.

`ParcelPathPoint` model added to `SoundingAnalysis` — captures the MetPy parcel profile array (previously computed for CAPE/CIN but discarded). Zero extra CPU cost.

## Key Choices

- **Server-side thermodynamics**: all physics stays in Python/MetPy. Client does only rendering + trivial trig (HW/XW)
- **Sidecar over recompute**: `derived_levels` are excluded from `route_analyses.json` (saves space for the viewer) but persisted to a gzipped sidecar at refresh time, so the endpoint serves pre-shaped profiles without re-running `analyze_sounding()`. Recompute (~30ms) remains the fallback for packs that predate the sidecar or have aged past T1 retention
- **Background grid caching**: isotherms/adiabats rendered once to offscreen `HTMLCanvasElement`, blitted at 1:1 pixel ratio (bypasses main canvas DPR transform)
- **CAPE/CIN shading bounds**: only between LCL and EL (no shading below LCL or above EL)
- **Any route point**: clicking any point (waypoint or interpolated) loads its Skew-T — not restricted to named waypoints
- **Dual-axis side panel**: single fixed-width panel avoids squeezing the Skew-T diagram. Primary + secondary variable with separate scales

## Still Future

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
