# Cloud Layers Analysis

> Per-model cloud data pipeline, source tracing, interpolation methods, and consistency analysis across all icing methods and visualization.

_Code references verified against the repo on 2026-08-15._

> **`nwp_cloud_layers` is strictly model-native.** A server-side synthesized-layer
> fallback for Open-Meteo-only models was added then **reverted** (df4474ff).
> `build_nwp_cloud_layers` now has **three** native tiers: 3D fraction (`nwp_3d`),
> GRIB bulk bands (`grib`), and per-level condensate (`nwp_condensate`, HRRR —
> #457 / PR #508). Open-Meteo-only models (GEM/UKMO/MétéoFr) return `None`, NOT
> synthesized bands. `"synthesized"` is documented in `models/analysis.py` as a
> historical value but is never produced at runtime.
>
> **Nativeness has one source of truth:** `NATIVE_NWP_CLOUD_SOURCES`
> (`models/analysis.py`) = `{"grib", "nwp_3d", "nwp_condensate"}`. Consumers that
> branch on "is this native" — the provenance badge in `tasks/advise.py` and
> `advisories/dd_nwp_agreement.py` — must use that set, never a hand-written list
> (each missed a source once doing so, PR #508 rounds 3 and 5). The old
> `advise.py` mislabel of `nwp_3d` as `"nwp_synthesized"` is **fixed**.

## Overview

Cloud layer data flows through three stages: **fetch** (Open-Meteo API + GRIB2 enrichment), **analysis** (cloud detection + icing gating), and **output** (visualization + advisories). Two parallel cloud detection methods produce independent cloud layer sets that the user can switch between.

---

## Per-Model Cloud Data Sources

### Open-Meteo API (all models)

Every model receives hourly cloud cover percentages via Open-Meteo:

| Variable | ICAO Band | Altitude Range | All Models? |
|----------|-----------|----------------|-------------|
| `cloud_cover_low` | Low | SFC – 6,500 ft | Yes |
| `cloud_cover_mid` | Mid | 6,500 – 20,000 ft | Yes |
| `cloud_cover_high` | High | 20,000 ft + | Yes |
| `cloud_cover` | Total | Full column | Yes |

These are **bulk band percentages** — a single value per ICAO band with no vertical placement within the band.

### GRIB2 Enrichment (GFS, ICON-EU, ECMWF)

| Field | GFS | ICON-EU | ECMWF | MétéoFr | UKMO | GEM |
|-------|-----|---------|-------|---------|------|-----|
| **Low cloud cover %** | Yes (lcc) | Yes (clcl) | Yes (lcc) | **No** | **No** | **No** |
| **Mid cloud cover %** | Yes (mcc) | Yes (clcm) | Yes (mcc) | **No** | **No** | **No** |
| **High cloud cover %** | Yes (hcc) | Yes (clch) | Yes (hcc) | **No** | **No** | **No** |
| **Total cloud cover %** | Yes (tcc) | Yes (clct) | Yes (tcc) | **No** | **No** | **No** |
| **Boundary cloud %** | Yes | No | **No** | **No** | **No** | **No** |
| **Per-level cloud fraction** | **No** | Yes (clc, 3D) | Yes (cc, 3D) | **No** | **No** | **No** |
| **Low base/top (Pa→ft)** | Yes | **No** | **No** | **No** | **No** | **No** |
| **Mid base/top (Pa→ft)** | Yes | **No** | **No** | **No** | **No** | **No** |
| **High base/top (Pa→ft)** | Yes | **No** | **No** | **No** | **No** | **No** |
| **Low/mid/high top temp** | Yes (K→°C) | **No** | **No** | **No** | **No** | **No** |
| **Convective cover %** | Yes | **No** | **No** | **No** | **No** | **No** |
| **Convective base/top** | Yes (Pa→ft) | Yes (m→ft) | **Top only** (hcct) | **No** | **No** | **No** |
| **Ceiling height** | Yes (gpm→ft) | Yes (m→ft) | Yes (ceil/cbh) | **No** | **No** | **No** |
| **Freezing level (surface)** | **No** | **No** | Yes (deg0l) | **No** | **No** | **No** |

**Key distinctions:**
- GFS: full per-layer boundaries (base_ft, top_ft) for all three ICAO bands — altitude-precise.
- ICON-EU and ECMWF: per-level 3D cloud fraction (`clc` / `cc`) lets us extract real deck base/top from the model's own cloud scheme — **richer than GFS's pre-computed bands** (not confined to ICAO altitude bins).
- ECMWF has no convective base and no per-band tops; convective assessment uses `hcct` anchored at LCL (see [convective-analysis.md](./convective-analysis.md)).
- ECMWF `deg0l` is wired onto `hourly.freezing_level_m` during enrichment, so `indices.nwp_freezing_level_ft` carries a model-native value rather than Open-Meteo's. `deg0l` is metres **AGL** as delivered and is raised onto MSL at decode (#487) — see `decode.py` around the `_ECMWF_*` field map.

**HRRR (US routes) substitutes for GFS enrichment.** `_try_enrich_gfs_from_hrrr`
(`fetch/grib/__init__.py`) replaces the GFS GRIB enrichment with HRRR when the
route lies inside the HRRR domain and the window is in range; `grib_sources["gfs"]`
then reads `"hrrr:noaa"`. There is no `ModelSource.HRRR` — HRRR fills the GFS slot.
Its diagnostic shape is closer to ECMWF's than to GFS's: per-band covers **without**
per-band base/top/temp, plus ceiling and one overall cloud base (mapped onto
`low.base_ft`, as for ECMWF `cbh`), ML CAPE/CIN, and `convective_scheme_absent=True`
(HRRR runs no parameterized deep convection). Its per-level `CLMR`/`CIMIXR` is what
feeds the `nwp_condensate` tier. Band covers are NCEP **pressure** bands
(`band_definition="ncep"`, sfc–642 / 642–350 / 350–top hPa) — same as GFS, and NOT
the ICAO altitude bands the Open-Meteo bulk fields use.

### Model Classification

| Model | NWP Cloud Diagnostics | Preferred Cloud Source | Effective Resolution |
|-------|-----------------------|------------------------|----------------------|
| **GFS** | Bulk bands w/ base/top + convective | GRIB bulk bands | Altitude-precise (ICAO-bounded) |
| **GFS slot on a US route (HRRR)** | Band covers (no base/top) + ceiling + 3D condensate | Per-level condensate | Altitude-precise (per-level geometry, band-level amount) |
| **Best Match** | Full (via GFS) | GRIB bulk bands | Altitude-precise |
| **ECMWF** | 3D `cc` + bulk bands + hcct + deg0l | **3D cloud fraction** | Altitude-precise (per-level) |
| **ICON-EU** | 3D `clc` + bulk bands + ceiling + convective | **3D cloud fraction** | Altitude-precise (per-level) |
| **MétéoFr** | None | Open-Meteo bulk % (icing only); `nwp_cloud_layers=None` | ICAO-band bulk |
| **UKMO** | None | Open-Meteo bulk % (icing only); `nwp_cloud_layers=None` | ICAO-band bulk |
| **GEM** | None | Open-Meteo bulk % (icing only); `nwp_cloud_layers=None` | ICAO-band bulk |

---

## Cloud Data Pipeline

### Stage 1: Fetch & Storage

```
Open-Meteo API ──→ HourlyForecast.cloud_cover_{low,mid,high}_pct  (all models)

GFS GRIB2 ──→ decode_cloud_diag_per_point() ──→ build_cloud_diagnostics()
           ──→ HourlyForecast.nwp_cloud_diagnostics: NWPCloudDiagnostics

ICON-EU GRIB2 ──→ decode_icon_eu_cloud_diag_per_point() ──→ build_icon_cloud_diagnostics()
              ──→ HourlyForecast.nwp_cloud_diagnostics: NWPCloudDiagnostics (partial)

HRRR GRIB2 ──→ decode_hrrr_cloud_diag_per_point() ──→ build_hrrr_cloud_diagnostics()
           ──→ same slot as GFS (US routes only; band covers, no band geometry)
```

### Stage 2: Temporal Interpolation

`fill.py:_fill_cloud_diagnostics()` (invoked from `propagate_all`) fills `nwp_cloud_diagnostics` on gap hours between native GRIB steps.

- **GFS, when `gfs_init` is provided** — `_interp_gfs_diag_hourly` does **window-midpoint linear interpolation** for low/mid/high cover (LCDC/MCDC/HCDC are averaged-window fields in GFS pgrb2, so each anchor sits at `step - window_length/2`). Layer geometry (`base_ft`, `top_ft`, `top_temp_c`) holds over from the higher-cover endpoint; sub-5 % covers drop the layer entirely. Convective, boundary, total, ceiling, and freezing level interpolate linearly with step-time anchoring. A follow-up `apply_gfs_rh_condensate_gate` drops bands whose pressure-level RH and condensate inside `[base_ft, top_ft]` contradict the averaged cover. See [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).
- **ICON-EU, ECMWF, HRRR, and the GFS fallback path** (no `gfs_init`) — `_fill_diag_hourly` forward-fills. These models publish instantaneous cover; persistence is the right semantic.
- **HRRR gotcha:** when the GFS slot was sourced from HRRR, the enrich path deliberately passes `gfs_init=None` and skips `apply_gfs_rh_condensate_gate` — every HRRR field is instantaneous, so window-midpoint resampling would mis-place values and the gate's averaged-phantom-layer premise doesn't exist. Don't "restore" the GFS init here.

Open-Meteo `cloud_cover_*_pct` values are NOT temporally smoothed by this module — they have their own hourly interpolation from Open-Meteo. This means cloud cover percentages and cloud diagnostic boundaries can come from different time anchors (up to ~3 hours apart on the GFS path with window-midpoint interp, since NCEP averaging windows reach 6 h — see #480; up to 3 hours on the forward-fill path).

### Stage 3: Spatial Interpolation

`spatial_interpolation.py:_lerp_diagnostics()` — linear interpolation of all `NWPCloudDiagnostics` fields between neighboring route points.

- All numeric sub-fields (cover_pct, base_ft, top_ft, etc.) are interpolated linearly
- One-sided fallback: if only one neighbor has data, uses that value (nearest-neighbor)
- Max gap: 100nm between enriched neighbors
- `_lerp_layer()` interpolates each `NWPCloudLayerDiag` independently

Open-Meteo `cloud_cover_*_pct` values are NOT spatially interpolated — each point has its own API response.

### Stage 4: Analysis (Two Cloud Detection Methods)

#### Method 1: DD (Dewpoint Depression) — Default

`clouds.py:detect_cloud_layers()` → `list[EnhancedCloudLayer]`

- **Input:** DerivedLevel dewpoint depression profile
- **Algorithm:** Walk levels surface→TOA, classify each in-cloud level by DD (OVC < 1°C ≤ BKN < 2°C ≤ SCT < 3°C), group consecutive same-category levels into a deck. A category change starts a new deck; DD ≥ 3°C splits decks (clear-air gaps).
- **Layer edges:** Linear threshold-crossing on `dewpoint_depression_c` against the boundary DD separating the layer from its different-category neighbor — a moisture-defined edge instead of pinning to a level altitude. At the column floor/TOA where there's no opposing neighbor, the edge falls back to the altitude midpoint between the deck-edge level and its in-bounds neighbor (or the level's own altitude if there's no neighbor at all). Mirrors the NWP-3D path with three categories instead of four (no FEW analog).
- **Model consistency:** Identical treatment for all 6 models (purely thermodynamic)
- **Output:** Stored in `SoundingAnalysis.dd_cloud_layers` (immutable) and `cloud_layers` (active slot)

#### Method 2: NWP (Model Diagnostics) — Alternative

`clouds.py:build_nwp_cloud_layers()` → `list[EnhancedCloudLayer] | None`

Three native sources, tried in this preference order (then `None`):

**Tier 0 — Per-level 3D cloud fraction (ECMWF `cc`, ICON-EU `clc`):** `build_nwp_cloud_layers_from_fraction(pressure_levels)` scans `cloud_area_fraction_pct` vertically and groups consecutive levels by their METAR coverage category (FEW/SCT/BKN/OVC, with sub-FEW = clear). A category change starts a new layer; sub-FEW levels split layers (clear-air gaps). Each layer's base/top altitudes come from linear threshold-crossing on `cloud_area_fraction_pct` against the boundary CAF separating this layer from its (different-category) neighbor — a model-derived edge instead of pinning to a level altitude. At the column floor/TOA where there's no opposing neighbor, the edge falls back to the altitude midpoint between the deck-edge level and its in-bounds neighbor (or the level's own altitude if there's no neighbor at all). Tagged `source="nwp_3d"`. Preferred when any level carries `cloud_area_fraction_pct` — strictly richer than GRIB-bulk because deck boundaries come from the model's own cloud scheme at sounding-level resolution.

**Tier 1 — GRIB bulk bands (GFS):** `_build_grib_layers()` uses GFS's native per-band boundaries (`HGHL/HGHM/HGHH` → base_ft/top_ft) and `LCDC/MCDC/HCDC` coverage. Each layer tagged `source="grib"`.

**Tier 2 — Per-level condensate (HRRR `CLMR` + `CIMIXR`):** `build_nwp_cloud_layers_from_condensate(pressure_levels, …)`. Runs of contiguous levels at or above `_CONDENSATE_CLOUDY_KG_KG` become layers, edges by the same midpoint convention as the other builders — **geometry from the microphysics, amount from the model's own band covers**, never invented from condensate. Tagged `source="nwp_condensate"`. Deliberately placed AFTER the GRIB-band tier so GFS (which has both) keeps its existing envelope; today only HRRR reaches it.
  - The amount/membership pairing must describe the same vertical slice: with NCEP diagnostic band covers present (`band_definition == "ncep"`) it carves by **pressure** (sfc–642 / 642–350 / 350–top hPa) and uses diagnostic amounts only — a band whose diagnostic is missing degrades to BKN/`pct=None` rather than borrowing an ICAO-slab bulk %. With no diagnostic band cover at all it carves by ICAO altitude bands and uses bulk Open-Meteo %. **Never mix the two** (PR #508 rounds 4–6 fixed that bug twice).
  - Returns `None` only when NO level carries either condensate field; `[]` means condensate present and every level below threshold — a genuine clear column.

**No native source → `None`:** returned when none of the three tiers applies (Open-Meteo-only models GEM/UKMO/MétéoFr). Open-Meteo bulk % is intentionally NOT synthesized into layers (the synth fallback, `source="synthesized"` with DD-envelope narrowing, was dropped in df4474ff). Callers must read `None` as "no NWP layer data", never "model says clear sky" — downstream it gates the Ogimet-NWP/SFIP-native availability and the cross-section NWP toggle.

- **Coverage from %:** ≥87.5% → OVC, ≥50% → BKN, ≥25% → SCT, ≥12.5% → FEW (`_NWP_COVERAGE_THRESHOLDS`; Tier 0 classifies each level individually then splits on category change, Tiers 1–2 use band %). `_NWP_CATEGORY_LOWER_BOUNDS` is derived from the same table so edge interpolation and classification can't drift apart.
- **Output:** Stored in `SoundingAnalysis.nwp_cloud_layers`
- **Source tracking:** `EnhancedCloudLayer.source` ∈ {"dd", "nwp_3d", "grib", "nwp_condensate"} at runtime; the last three are `NATIVE_NWP_CLOUD_SOURCES`. ("synthesized" is a historical value listed in the model comment only.)
- **Method tracking:** `cloud_method_effective` records "dd" or "nwp" — `advise.py` derives it from `NATIVE_NWP_CLOUD_SOURCES`, so all three native builders read as plain "nwp"; "nwp_synthesized" is reserved for genuinely synthesized layers and is unreachable today.
- **Quantitative metadata:** `EnhancedCloudLayer.mean_cloud_cover_pct` carries the underlying numeric — mean `cloud_area_fraction_pct` across the (homogeneous) deck for `nwp_3d`, the band's `cover_pct` for `grib` (incl. convective) and for `nwp_condensate`. Surfaced in the cross-section tooltip as `CC nn%`. Null for `dd` (uses `mean_dewpoint_depression_c` instead).

| Model | NWP Cloud Layers Result | Source Tag | Notes |
|-------|------------------------|------------|-------|
| **GFS** | Full layer list with boundaries | `grib` | All 3 bands + convective from GRIB2 |
| **Best Match** | Full layer list (via GFS) | `grib` | Same as GFS |
| **ECMWF** | Per-deck layers from 3D `cc` | `nwp_3d` | Real model cloud scheme, not constrained to ICAO bands |
| **ICON-EU** | Per-deck layers from 3D `clc` | `nwp_3d` | Same as ECMWF. **No convective layer** — the 3D tier returns immediately, so ICON's GRIB convective base/top never becomes a cloud layer (it is still used by convective assessment) |
| **HRRR (US, GFS slot)** | Condensate runs, band-cover amounts | `nwp_condensate` | Pressure-band carving when NCEP diagnostics present, else ICAO/bulk |
| **MétéoFr / UKMO / GEM** | `None` (no native source) | — | `build_nwp_cloud_layers` returns None; Open-Meteo bulk % is NOT synthesized into layers |

### Stage 5: Cloud Top Uncertainty

`clouds.py:enrich_cloud_top_uncertainty()` — adds `theoretical_max_top_ft` to each cloud layer:
- CAPE > 500 J/kg → Equilibrium Level (convective overshoot)
- Stratiform → −20°C level (glaciation limit)
- Only set when exceeding sounding-derived cloud top

### Stage 6: Active `cloud_layers` slot

`SoundingAnalysis` exposes three cloud-layer fields:
- `dd_cloud_layers` — immutable DD-derived layers (Method 1).
- `nwp_cloud_layers` — immutable NWP-derived layers (Method 2), or `None` if the model has no native NWP source.
- `cloud_layers` — the **active slot** consumed by Skew-T, cross-section, ceiling computation, and downstream icing/IFR feasibility. Today it is set to `list(dd_cloud_layers)` verbatim.

#### ICAO band overlay (disabled)

Historically the active slot was `apply_nwp_coverage(dd_cloud_layers, …)` — an overlay that splits DD decks at ICAO band boundaries (6500 / 20000 ft) and re-classifies each segment's coverage from the model's bulk band % (`cloud_cover_low/mid/high_pct`). The function and its tests are kept callable but the call site is gated by `_APPLY_NWP_COVERAGE_OVERLAY` in `analysis/sounding/__init__.py`, hard-coded to `False`.

**Why disabled:** the category-split DD detector (PR #142) already produces good per-deck OVC/BKN/SCT classes with moisture-defined edges. The overlay degrades that signal in two ways:
1. Splitting at 6500 / 20000 ft turns a single moisture-defined deck (e.g. 4351–10892 ft OVC) into two band-bounded segments with different coverages.
2. Split segments inherit `base_pressure_hpa=None` or `top_pressure_hpa=None` from the band-cut edges, which causes the Skew-T (a pressure-axis view) to skip them entirely.

**To re-enable** (e.g. if NWP bulk bands later re-prove useful for ceiling adjustment), flip `_APPLY_NWP_COVERAGE_OVERLAY = True` and run `tests/test_clouds.py` — existing tests for the overlay still cover its semantics. Consider exposing as a per-user preference rather than re-enabling globally.

---

## Cloud Data in Icing Assessment

### How Each Method Uses Cloud Cover

#### Ogimet-DD

**No NWP cloud input.** Cloud signal comes entirely from DD attenuation:
```
effective_index = ogimet_index(T) × dd_attenuation_factor(DD)
```
DD < 2.0 → factor near 1.0 (in cloud). DD > 2.0 → factor = 0 (clear). Consistent across all models.

#### Ogimet-NWP

**Primary cloud input.** Cloud fraction scales the icing index:
```
effective = ogimet_index(T) × cloud_fraction(alt) × glaciation(CLW, ICMR)
```
Cloud fraction comes from `nwp_cloud_cover_at_altitude()`.

#### SFIP

**Cloud input varies by variant:**
- **Full/interp:** `membership_clw(clw_g_kg)` — direct GRIB2 cloud water measurement
- **Proxy:** `membership_clw_proxy(DD, RH, cloud_cover_at_band)` — uses `nwp_cloud_cover_at_altitude()` as one of three proxy signals

### `nwp_cloud_cover_at_altitude()` — Central Cloud Lookup

This function (`icing_common.py:134-181`) is the shared altitude-aware cloud cover lookup used by Ogimet-NWP and SFIP proxy. Two paths:

**Path A — With diagnostics carrying band base/top (GFS only):**
1. Checks low/mid/high diagnostic layers regardless of ICAO band
2. For each layer with `base_ft` and `top_ft`, checks if altitude falls within range (±500ft margin)
3. Returns highest matching cloud cover percentage

**Path B — Without band base/top (ECMWF, ICON-EU, HRRR, MétéoFr, UKMO, GEM):**
- Simple ICAO band mapping: `< 6500ft → low`, `< 20000ft → mid`, `≥ 20000ft → high`
- No vertical constraint within the band

---

## Bugs Found

### 1. `nwp_cloud_cover_at_altitude` uses Open-Meteo bulk percentage instead of GRIB diagnostic cover_pct

**Still open (re-verified 2026-08-15).** With GFS diagnostics the function matches altitude against the GRIB layer boundaries but takes the *amount* from Open-Meteo (`pct = bulk_pct or 0.0`), while `_build_grib_layers` prefers `diag.cover_pct` with the bulk value only as fallback. So icing and the drawn cloud layer can carry different cover % for the same band and point.

**Status (decision 2026-06-06, unchanged):** **code kept as-is** — not a briefing-correctness defect (both numbers are "cloud cover", from slightly different post-processing). The one-line fix is `pct = diag_layer.cover_pct if diag_layer.cover_pct is not None else (bulk_pct or 0.0)`, to apply only if calibration ever shows it matters.

### 2. Convective cloud layer not checked in `nwp_cloud_cover_at_altitude`

**Still open.** Only the low/mid/high diagnostic layers are checked; GFS `convective_{cover_pct,base_ft,top_ft}` is ignored for the altitude lookup, so Ogimet-NWP (and SFIP proxy) can underestimate icing inside a convective cell that doesn't coincide with a stratiform band. Fix would add the convective triple as a fourth candidate in the same altitude/margin check.

### 3. ~~`_nwp_cloud_for_zone` doesn't pass `nwp_cloud_high_pct`~~ ✓ FIXED

`_nwp_cloud_for_altitude` (icing.py:291) now takes and forwards `nwp_cloud_high_pct`, and `assess_icing_zones_ogimet_nwp`/`_ieng` thread it through. Impact was low anyway (most icing is below FL200). Left here only so the "Future Considerations" note about severity-enhancement calibration at high altitude still has its antecedent.

### 4. ~~High cloud not rendered for non-GFS models in visualization~~ ✓ OBSOLETE

The client-side `bandCoverPct()` band-synthesis path no longer exists. The cross-section NWP cloud layer now renders `p.nwpCloudLayers` — the server-computed `nwp_cloud_layers` shipped verbatim through `data-extract.ts` — so band coverage, high cloud included, comes from whichever native tier produced the layers. `cloudCoverLowPct`/`cloudCoverMidPct` survive on `VizPoint` only as **map scalars**, not as cloud-band geometry. `nwpCloudLayers === null` (never `[]`) is what gates the NWP toggle.

---

## Inconsistencies

### 1. Cloud cover percentage source mismatch

Two server-side consumers of cloud cover use different sources for the same data
(visualization no longer computes its own — it renders the server layers):

| Consumer | With GFS Diagnostics | Without Diagnostics |
|----------|---------------------|---------------------|
| **`build_nwp_cloud_layers`** (cloud detection) | GRIB `diag.cover_pct` (preferred) → Open-Meteo fallback | N/A (returns None, or condensate tier for HRRR) |
| **`nwp_cloud_cover_at_altitude`** (icing) | Open-Meteo `bulk_pct` (always) | Open-Meteo `bulk_pct` |

Both should prefer GRIB diagnostic cover_pct when available, falling back to Open-Meteo (see bug 1 — kept as-is by decision).

### 2. Ogimet-DD pass-2 NWP fallback uses different cloud source than Ogimet-NWP

(Historical) Ogimet-DD previously had a two-pass approach with a pass-2 NWP fallback using `nwp_cloud_cover_at_altitude()`. This was removed (see icing_common.py comment 'old pass-1/pass-2 hybrid, no longer'); current `assess_icing_zones_ogimet_dd` (icing.py:362) is single-pass, gated by `is_in_cloud_layer` + DD attenuation. But the two methods can produce different results because:

- Ogimet-DD pass 1 uses cloud proximity (DD + EnhancedCloudLayer altitude check)
- Ogimet-DD pass 2 uses NWP cloud cover as binary gate (> 50%)
- Ogimet-NWP uses NWP cloud cover as continuous multiplier

This is by design (different methods) but means the same atmospheric level can be "in cloud" for one method and "not in cloud" for another.

### 3. ICON-EU diagnostic object barely feeds the cloud path

ICON-EU gets an `NWPCloudDiagnostics` object, but its low/mid/high bands lack `base_ft`/`top_ft`:
- `build_nwp_cloud_layers` — never reaches the GRIB tier at all for ICON: the 3D `clc` tier returns first, so nothing from the diag object (convective included) becomes a cloud layer
- `nwp_cloud_cover_at_altitude` — `any_diag = False`, falls through to ICAO band fallback

The ceiling and convective base/top from ICON-EU diagnostics ARE used elsewhere (convective assessment, ceiling display) — not wasted, just not used for cloud layers or the cloud-cover altitude lookup.

### 4. Temporal desynchronization between cover % and boundaries

Open-Meteo provides hourly-interpolated cloud cover percentages. GRIB2 native-step diagnostics arrive at 1h or 3h intervals.

- **GFS path (with `gfs_init`)** — low/mid/high cover is now window-midpoint linearly interpolated and the RH/condensate gate drops phantom layers (see [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate) and Stage 2). The remaining desync is between the freshly-interpolated cover and held-over layer geometry — bounded by the bracketing native steps' window length (≤ 3 h on either side past f120). *(Previously stated as ≤ 1.5 h, derived from the old capped-at-3h window model. NCEP windows actually reach 6 h, alternating 3 / 6 past f120 — corrected in #480, see [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).)*
- **ICON-EU / ECMWF / HRRR / GFS fallback** — forward-fill leaves boundaries stale up to one full native step behind (3 h at longer lead times; ~1 h for HRRR). Cover and boundaries can disagree by that much. Accepted as a reasonable approximation; cover here is instantaneous, so persisting cover and geometry together is consistent.

---

## Per-Model Cloud Pipeline Summary

### GFS / Best Match (Full Pipeline)

```
Open-Meteo → cloud_cover_{low,mid,high}_pct (hourly interpolated)
GRIB2      → NWPCloudDiagnostics (native steps, forward-filled)
             ├─ low:  cover_pct + base_ft + top_ft + top_temp_c
             ├─ mid:  cover_pct + base_ft + top_ft + top_temp_c
             ├─ high: cover_pct + base_ft + top_ft + top_temp_c
             ├─ convective: cover_pct + base_ft + top_ft
             ├─ total_cover_pct, boundary_cover_pct
             └─ ceiling_ft
Analysis:
  DD cloud layers     → always available (sounding-derived)
  NWP cloud layers    → full layer list (3 bands + convective)
  nwp_cloud_at_alt    → altitude-precise (diagnostic boundaries, ±500ft margin)
Visualization:
  DD cloud bands      → gray gradient (always)
  NWP cloud bands     → blue tint, precise boundaries (all 3 bands + convective)
```

### ECMWF / ICON-EU (3D Cloud Fraction)

```
Open-Meteo → cloud_cover_{low,mid,high}_pct (hourly, fallback)
GRIB2      → per-level cc/clc at each pressure level (0–100%)
             NWPCloudDiagnostics:
             ├─ low/mid/high: bulk cover_pct
             ├─ ECMWF: cbh, hcct (convective top only), deg0l (→ hourly.freezing_level_m)
             ├─ ICON: convective base+top, ceiling
             └─ total_cover_pct
Analysis:
  DD cloud layers     → always available (sounding-derived)
  NWP cloud layers    → per-deck from 3D cc/clc scan (source="nwp_3d")
                         (no convective layer — 3D tier returns before the GRIB tier)
  nwp_cloud_at_alt    → ICAO-band bulk fallback (cc not yet plumbed here)
Visualization:
  DD cloud bands      → gray gradient (always)
  NWP cloud bands     → real per-deck boundaries (blue tint)
```

### HRRR (US routes, occupies the GFS slot)

```
Open-Meteo → cloud_cover_{low,mid,high}_pct (hourly)
HRRR GRIB2 → per-level CLMR + CIMIXR; band covers (NCEP pressure bands, no
             base/top), ceiling, one cloud base → low.base_ft, ML CAPE/CIN,
             convective_scheme_absent=True
Analysis:
  DD cloud layers     → always available
  NWP cloud layers    → condensate runs, band-cover amounts (source="nwp_condensate")
  nwp_cloud_at_alt    → ICAO-band bulk fallback (no band geometry)
Timing:
  gfs_init=None       → forward-fill, RH/condensate gate skipped (all fields instantaneous)
```

### MétéoFr / UKMO / GEM (Open-Meteo Only)

```
Open-Meteo → cloud_cover_{low,mid,high}_pct (hourly)
             No GRIB enrichment
Analysis:
  DD cloud layers     → always available
  NWP cloud layers    → None (no native source; not synthesized from Open-Meteo)
  nwp_cloud_at_alt    → ICAO-band bulk fallback (icing only)
Visualization:
  DD cloud bands      → gray gradient (always)
  NWP cloud bands     → none server-side (nwp_cloud_layers is None)
```

---

## Cloud Usage Across Icing Methods

### Cloud Gating Comparison

| Method | Cloud Signal | Threshold | Behavior Without GRIB |
|--------|-------------|-----------|----------------------|
| **Ogimet-DD** | DD attenuation factor | DD < 2.0 → factor > 0 | Identical (no GRIB dependency) |
| **Ogimet-NWP** | NWP cloud fraction | > 0% (continuous) | Falls to bulk % + DD gate |
| **SFIP full** | CLW membership | CLW > 0 g/kg | N/A (requires CLW) |
| **SFIP proxy** | CLW proxy (DD + RH + NWP cloud) | Near-cloud gate | Falls to bulk % |

### DD Gate Differences (When GRIB Diagnostics Absent)

| Method | DD Threshold | Includes SCT | Proximity Check |
|--------|-------------|-------------|-----------------|
| **Ogimet-DD** | 2.0°C | No (skip_sct=True) | ±500ft of BKN/OVC |
| **Ogimet-NWP** | 2.0°C | No (skip_sct=True) | ±500ft of BKN/OVC |
| **SFIP proxy** | 3.0°C | Yes (skip_sct=False) | ±500ft of all clouds |

SFIP proxy uses a wider gate (3°C, includes SCT) because it has no NWP fallback to catch missed cloud (the historical Ogimet pass-2 hybrid is gone — see Inconsistency 2).

### NWP Cloud Cover at Altitude — Path Comparison

| Scenario | GFS | ICON-EU / ECMWF / HRRR | Other Models |
|----------|-----|------------------------|-------------|
| Diagnostics available? | Yes (full) | Yes (partial — no band base/top) | No |
| Layer boundaries? | Yes | No | No |
| Altitude check used? | Yes (±500ft margin) | No (no boundaries) | No |
| Cloud cover source | Open-Meteo bulk (BUG 1) | Open-Meteo bulk | Open-Meteo bulk |
| DD gate needed? | No | Yes (`need_dd_gate=True`) | Yes |
| Convective checked? | No (BUG 2) | No | N/A |

---

## Interpolation Summary

| Axis | What | Method | Notes |
|------|------|--------|-------|
| **Temporal** | GFS cloud diagnostics (low/mid/high cover) | Window-midpoint linear interp between native steps (requires `gfs_init`); geometry held from higher-cover endpoint; sub-5 % drops layer; RH/condensate gate drops phantom layers post-interp | See [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate) |
| **Temporal** | GFS cloud diagnostics (instantaneous: convective, boundary, total, ceiling, freezing level) | Step-time linear interp (requires `gfs_init`) | Instantaneous fields in GFS pgrb2 — no midpoint offset |
| **Temporal** | ICON-EU / ECMWF / HRRR cloud diagnostics + GFS fallback path | Forward-fill from preceding native GRIB hour | Up to 3h gap at longer lead times; HRRR is hourly so the gap is minimal |
| **Temporal** | Cloud water (CLW/ICMR) | GFS: step-time linear interp (requires `gfs_init`), otherwise forward-fill; ECMWF / ICON-EU rebuild via `_linear_interp_pressure_levels` | Instantaneous mixing ratios — step-time anchoring |
| **Temporal** | Cloud cover % (`cloud_cover_low/mid/high_pct`) | Open-Meteo hourly interpolation | Independent of GRIB timing |
| **Spatial** | Cloud diagnostics | Linear between route points | One-sided fallback; max 100nm gap |
| **Spatial** | Cloud water (CLW/ICMR) | Linear between route points | Sets `clw_interpolated=True` |
| **Spatial** | Cloud cover % | Not interpolated | Each point has own API response |
| **Vertical** | Cloud water (CLW/ICMR) | Linear in pressure-space | Between 50hPa GRIB levels to fill 25hPa gaps |

---

## Future Considerations

### 1. Boundary estimation for models without band geometry — solved natively

ICON-EU/ECMWF get real per-deck boundaries from 3D `clc`/`cc` (Tier 0); HRRR gets them from condensate (Tier 2, #457). A synthesized-layer fallback for the remaining Open-Meteo-only models was added then deliberately reverted (df4474ff) — GEM/UKMO/MétéoFr return `None` rather than synthesized bands, and that is the intended end state, not a gap to fill.

### 2. Weight redistribution for missing high cloud in severity enhancement

The severity enhancement (`_enhance_severity`) checks NWP cloud cover at the zone's base altitude. High-altitude zones get `None` due to bug #3 (missing `nwp_cloud_high_pct` parameter). Even after fixing, severity enhancement at high altitude may need different calibration since icing at FL200+ is less common.

### 3. Cloud cover validation

When both GRIB diagnostic cover_pct and Open-Meteo bulk percentage are available, logging their divergence would help validate data consistency and identify cases where one source is significantly more accurate.
