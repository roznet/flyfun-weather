# Cloud Layers Analysis

> Per-model cloud data pipeline, source tracing, interpolation methods, and consistency analysis across all icing methods and visualization.

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

### GRIB2 Enrichment (GFS and ICON-EU only)

| Field | GFS | ICON-EU | ECMWF | MétéoFr | UKMO | GEM |
|-------|-----|---------|-------|---------|------|-----|
| **Low cloud cover %** | Yes (lcc) | Yes (clcl) | **No** | **No** | **No** | **No** |
| **Mid cloud cover %** | Yes (mcc) | Yes (clcm) | **No** | **No** | **No** | **No** |
| **High cloud cover %** | Yes (hcc) | Yes (clch) | **No** | **No** | **No** | **No** |
| **Total cloud cover %** | Yes (tcc) | Yes (clct) | **No** | **No** | **No** | **No** |
| **Boundary cloud %** | Yes | No | **No** | **No** | **No** | **No** |
| **Low base/top (Pa→ft)** | Yes | **No** | **No** | **No** | **No** | **No** |
| **Mid base/top (Pa→ft)** | Yes | **No** | **No** | **No** | **No** | **No** |
| **High base/top (Pa→ft)** | Yes | **No** | **No** | **No** | **No** | **No** |
| **Low/mid/high top temp** | Yes (K→°C) | **No** | **No** | **No** | **No** | **No** |
| **Convective cover %** | Yes | **No** | **No** | **No** | **No** | **No** |
| **Convective base/top** | Yes (Pa→ft) | Yes (m→ft) | **No** | **No** | **No** | **No** |
| **Ceiling height** | Yes (gpm→ft) | Yes (m→ft) | **No** | **No** | **No** | **No** |

**Key distinction:** GFS provides full per-layer boundaries (base_ft, top_ft) for all three ICAO bands. ICON-EU provides cover percentages per band but **no layer boundaries** — only ceiling height and convective base/top.

### Model Classification

| Model | NWP Cloud Diagnostics | Has Layer Boundaries | Has Convective Layer | Effective Cloud Resolution |
|-------|-----------------------|---------------------|---------------------|---------------------------|
| **GFS** | Full | Yes (all 3 bands) | Yes (cover + base/top) | Altitude-precise |
| **Best Match** | Full (via GFS) | Yes | Yes | Altitude-precise |
| **ICON-EU** | Partial | **No** (cover only) | Partial (base/top only) | ICAO-band bulk |
| **ECMWF** | None | No | No | ICAO-band bulk |
| **MétéoFr** | None | No | No | ICAO-band bulk |
| **UKMO** | None | No | No | ICAO-band bulk |
| **GEM** | None | No | No | ICAO-band bulk |

---

## Cloud Data Pipeline

### Stage 1: Fetch & Storage

```
Open-Meteo API ──→ HourlyForecast.cloud_cover_{low,mid,high}_pct  (all models)

GFS GRIB2 ──→ decode_cloud_diag_per_point() ──→ build_cloud_diagnostics()
           ──→ HourlyForecast.nwp_cloud_diagnostics: NWPCloudDiagnostics

ICON-EU GRIB2 ──→ decode_icon_eu_cloud_diag_per_point() ──→ build_icon_cloud_diagnostics()
              ──→ HourlyForecast.nwp_cloud_diagnostics: NWPCloudDiagnostics (partial)
```

### Stage 2: Temporal Interpolation

`fill.py:_forward_fill_cloud_diagnostics()` — forward-fills `nwp_cloud_diagnostics` from native GRIB hours to Open-Meteo interpolated hours. Cloud layer geometry changes slowly between 3-hour GFS steps.

Open-Meteo `cloud_cover_*_pct` values are NOT forward-filled — they have their own hourly interpolation from Open-Meteo. This means cloud cover percentages and cloud diagnostic boundaries can come from different time anchors (up to 3 hours apart at longer lead times).

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
- **Threshold:** DD < 3°C = "in cloud"
- **Coverage:** Mean DD < 1°C → OVC, < 2°C → BKN, < 3°C → SCT
- **Model consistency:** Identical treatment for all 6 models (purely thermodynamic)
- **Output:** Stored in `SoundingAnalysis.dd_cloud_layers` (immutable) and `cloud_layers` (active slot)

#### Method 2: NWP (Model Diagnostics) — Alternative

`clouds.py:build_nwp_cloud_layers()` → `list[EnhancedCloudLayer] | None`

Three-tier approach ensures all models produce NWP cloud layers when cloud cover data exists:

**Tier 1 — GRIB diagnostics (GFS):** Uses native model boundaries (base_ft, top_ft) and coverage from GRIB2. Each layer tagged `source="grib"`.

**Tier 2 — Synthesized layers (ICON-EU, ECMWF, MétéoFr, UKMO, GEM):** When GRIB boundaries are absent but Open-Meteo cloud cover percentages exist, synthesizes layers by narrowing ICAO bands using:
- DD cloud envelope (sounding cloud layers overlapping the ICAO band constrain base/top)
- Inversion capping (strong inversions ≥2°C cap cloud tops within the band)
- LCL floor (low band base raised to LCL when available)
- Minimum cloud cover threshold: 25% (bands below this are skipped)
Each layer tagged `source="synthesized"`.

**Tier 3 — No data:** Returns None only when no cloud cover data exists at all.

- **Coverage from %:** ≥87.5% → OVC, ≥50% → BKN, ≥25% → SCT
- **Includes convective layer** when base/top are available (both tiers)
- **Output:** Stored in `SoundingAnalysis.nwp_cloud_layers`
- **Source tracking:** Each `EnhancedCloudLayer` carries a `source` field ("dd", "grib", or "synthesized")
- **Method tracking:** `SoundingAnalysis.cloud_method_effective` records what was actually used ("dd", "nwp", or "nwp_synthesized")

| Model | NWP Cloud Layers Result | Source Tag | Notes |
|-------|------------------------|------------|-------|
| **GFS** | Full layer list with boundaries | `grib` | All 3 bands + convective from GRIB2 |
| **Best Match** | Full layer list (via GFS) | `grib` | Same as GFS |
| **ICON-EU** | Synthesized bands + convective | `synthesized` | Low/mid/high narrowed by DD+inversions; convective from GRIB |
| **ECMWF** | Synthesized bands | `synthesized` | Open-Meteo cloud %, narrowed by DD+inversions |
| **MétéoFr** | Synthesized bands | `synthesized` | Open-Meteo cloud %, narrowed by DD+inversions |
| **UKMO** | Synthesized bands | `synthesized` | Open-Meteo cloud %, narrowed by DD+inversions |
| **GEM** | Synthesized bands | `synthesized` | Open-Meteo cloud %, narrowed by DD+inversions |

### Stage 5: Cloud Top Uncertainty

`clouds.py:enrich_cloud_top_uncertainty()` — adds `theoretical_max_top_ft` to each cloud layer:
- CAPE > 500 J/kg → Equilibrium Level (convective overshoot)
- Stratiform → −20°C level (glaciation limit)
- Only set when exceeding sounding-derived cloud top

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

This function (`icing_common.py:73-120`) is the shared altitude-aware cloud cover lookup used by Ogimet-NWP and SFIP proxy. Two paths:

**Path A — With diagnostics (GFS):**
1. Checks low/mid/high diagnostic layers regardless of ICAO band
2. For each layer with `base_ft` and `top_ft`, checks if altitude falls within range (±500ft margin)
3. Returns highest matching cloud cover percentage

**Path B — Without diagnostics (ECMWF, ICON-EU, MétéoFr, UKMO, GEM):**
- Simple ICAO band mapping: `< 6500ft → low`, `< 20000ft → mid`, `≥ 20000ft → high`
- No vertical constraint within the band

---

## Bugs Found

### 1. `nwp_cloud_cover_at_altitude` uses Open-Meteo bulk percentage instead of GRIB diagnostic cover_pct

**Problem:** When GFS diagnostics are available, the function uses the GRIB diagnostic layer boundaries (base_ft/top_ft) for altitude matching but uses the Open-Meteo bulk percentage for the cloud cover value:

```python
# icing_common.py:108
pct = bulk_pct or 0.0  # Uses Open-Meteo cloud_cover_{low,mid,high}_pct
```

Meanwhile, `build_nwp_cloud_layers()` in `clouds.py:163` correctly prefers the GRIB diagnostic cover_pct:
```python
cover_pct = diag.cover_pct if diag.cover_pct is not None else fallback_pct
```

And the frontend `bandCoverPct()` also correctly uses diagnostic cover_pct:
```typescript
return diag[band].coverPct ?? 0;  // Uses GRIB diagnostic value
```

**Impact:** Icing assessment (Ogimet-NWP, SFIP proxy) and cloud layer visualization can use different cloud cover values for the same band at the same location. The GRIB diagnostic `cover_pct` is the native model output; Open-Meteo values are post-processed and may diverge.

**Fix:** Prefer `diag_layer.cover_pct` when available, fall back to `bulk_pct`:
```python
pct = diag_layer.cover_pct if diag_layer.cover_pct is not None else (bulk_pct or 0.0)
```

### 2. Convective cloud layer not checked in `nwp_cloud_cover_at_altitude`

**Problem:** The function only checks low/mid/high diagnostic layers. GFS convective cloud data (`convective_cover_pct`, `convective_base_ft`, `convective_top_ft`) is ignored for the altitude lookup.

Convective cloud can exist at any altitude and carries significant icing risk. A level within a convective cloud cell should get the convective cover percentage, but currently it doesn't.

**Impact:** Ogimet-NWP may underestimate icing in convective cloud regions when the convective cell doesn't coincide with a stratiform layer. SFIP proxy may also miss convective cloud signal.

**Fix:** Add convective cloud as a fourth candidate in the altitude check:
```python
if nwp_cloud_diagnostics.convective_base_ft is not None and nwp_cloud_diagnostics.convective_top_ft is not None:
    # Check if altitude falls within convective cloud
    ...
```

### 3. `_nwp_cloud_for_zone` doesn't pass `nwp_cloud_high_pct`

**Problem:** The severity enhancement function `_nwp_cloud_for_zone` (icing.py:475-485) doesn't accept or pass `nwp_cloud_high_pct`. For models without diagnostics, icing zones at FL200+ get `None` for NWP cloud cover, preventing severity enhancement.

```python
def _nwp_cloud_for_zone(
    base_ft, nwp_cloud_low_pct, nwp_cloud_mid_pct,  # Missing: nwp_cloud_high_pct
    nwp_cloud_diagnostics=None,
) -> float | None:
```

**Impact:** Low — most icing occurs below FL200 (0 to −20°C band). For GFS, the diagnostics path works correctly regardless. Only affects models without diagnostics at high altitude.

**Fix:** Add `nwp_cloud_high_pct` parameter and pass it through from `_build_zone` and `assess_icing_zones`.

### 4. High cloud not rendered for non-GFS models in visualization

**Problem:** The frontend `bandCoverPct()` returns 0 for high cloud when diagnostics are absent:
```typescript
return 0; // No high-cloud data without diagnostics
```

And `cloudCoverHighPct` is not even on the `VizPoint` type — only low and mid are passed through. Open-Meteo provides `cloud_cover_high_pct` for all models, but it's discarded in the visualization pipeline.

Additionally, the `hasData` check in the NWP cloud layer render function only checks low, mid, and diagnostics — not high:
```typescript
p.cloudCoverLowPct > 0 || p.cloudCoverMidPct > 0 || p.nwpCloudDiag !== null
```

**Impact:** Models without GRIB enrichment (ECMWF, ICON, MétéoFr, UKMO, GEM) show no high cloud in the NWP cloud band visualization, even when Open-Meteo reports high cloud cover.

**Mitigation:** This may be partially intentional — high cloud (cirrus) is typically not aviation-relevant for icing, and rendering the full 20,000+ ft ICAO band without vertical boundaries is misleading. If high cloud rendering is desired, pass `cloudCoverHighPct` to `VizPoint` and use sounding cloud envelope for vertical narrowing (same heuristic as mid band).

---

## Inconsistencies

### 1. Cloud cover percentage source mismatch

Three consumers of cloud cover use different sources for the same data:

| Consumer | With GFS Diagnostics | Without Diagnostics |
|----------|---------------------|---------------------|
| **`build_nwp_cloud_layers`** (cloud detection) | GRIB `diag.cover_pct` (preferred) → Open-Meteo fallback | N/A (returns None) |
| **`nwp_cloud_cover_at_altitude`** (icing) | Open-Meteo `bulk_pct` (always) | Open-Meteo `bulk_pct` |
| **Frontend `bandCoverPct`** (visualization) | GRIB `diag.coverPct` (always) | Open-Meteo low/mid (no high) |

All three should prefer GRIB diagnostic cover_pct when available, falling back to Open-Meteo.

### 2. Ogimet-DD pass-2 NWP fallback uses different cloud source than Ogimet-NWP

The Ogimet-DD two-pass approach (icing.py:413-443) has a pass-2 NWP fallback that uses `nwp_cloud_cover_at_altitude()` identically to how Ogimet-NWP uses it. But the two methods can produce different results because:

- Ogimet-DD pass 1 uses cloud proximity (DD + EnhancedCloudLayer altitude check)
- Ogimet-DD pass 2 uses NWP cloud cover as binary gate (> 50%)
- Ogimet-NWP uses NWP cloud cover as continuous multiplier

This is by design (different methods) but means the same atmospheric level can be "in cloud" for one method and "not in cloud" for another.

### 3. ICON-EU diagnostic object created but largely unused

ICON-EU gets an `NWPCloudDiagnostics` object, but since low/mid/high layers lack `base_ft`/`top_ft`:
- `build_nwp_cloud_layers` — skips all three bands, only convective layer (if present) is usable
- `nwp_cloud_cover_at_altitude` — `any_diag = False`, falls through to ICAO band fallback
- Net effect: ICON-EU cloud diagnostics object is created but has minimal impact vs. not having diagnostics at all

The ceiling and convective base/top from ICON-EU diagnostics ARE used elsewhere (convective assessment, ceiling display) — not wasted, just not used for cloud cover altitude lookup.

### 4. Temporal desynchronization between cover % and boundaries

Open-Meteo provides hourly-interpolated cloud cover percentages. GRIB2 provides native-step diagnostics (1h or 3h intervals) that are forward-filled. Between native steps, the percentage can be freshly interpolated while the boundaries are stale (up to 3 hours old). This is documented in `fill.py` and accepted as a reasonable approximation.

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

### ICON-EU (Partial GRIB)

```
Open-Meteo → cloud_cover_{low,mid,high}_pct (hourly)
GRIB2      → NWPCloudDiagnostics (partial)
             ├─ low/mid/high: cover_pct only (no base/top)
             ├─ convective: base_ft + top_ft (no cover_pct)
             ├─ ceiling_ft
             └─ total_cover_pct
Analysis:
  DD cloud layers     → always available
  NWP cloud layers    → synthesized from Open-Meteo + DD envelope + inversions (source="synthesized")
                         + convective layer from GRIB (when available)
  nwp_cloud_at_alt    → ICAO-band bulk fallback (no layer boundaries)
Visualization:
  DD cloud bands      → gray gradient (always)
  NWP cloud bands     → server-computed synthesized layers (blue tint)
```

### ECMWF / MétéoFr / UKMO / GEM (Open-Meteo Only)

```
Open-Meteo → cloud_cover_{low,mid,high}_pct (hourly)
             No GRIB enrichment
Analysis:
  DD cloud layers     → always available
  NWP cloud layers    → synthesized from Open-Meteo + DD envelope + inversions (source="synthesized")
  nwp_cloud_at_alt    → ICAO-band bulk fallback
Visualization:
  DD cloud bands      → gray gradient (always)
  NWP cloud bands     → server-computed synthesized layers (blue tint, all 3 bands)
```

---

## Cloud Usage Across Icing Methods

### Cloud Gating Comparison

| Method | Cloud Signal | Threshold | Behavior Without GRIB |
|--------|-------------|-----------|----------------------|
| **Ogimet-DD** | DD attenuation factor | DD < 2.0 → factor > 0 | Identical (no GRIB dependency) |
| **Ogimet-DD pass 2** | NWP cloud cover | > 50% at altitude | Falls to bulk ICAO band % |
| **Ogimet-NWP** | NWP cloud fraction | > 0% (continuous) | Falls to bulk % + DD gate |
| **SFIP full** | CLW membership | CLW > 0 g/kg | N/A (requires CLW) |
| **SFIP proxy** | CLW proxy (DD + RH + NWP cloud) | Near-cloud gate | Falls to bulk % |

### DD Gate Differences (When GRIB Diagnostics Absent)

| Method | DD Threshold | Includes SCT | Proximity Check |
|--------|-------------|-------------|-----------------|
| **Ogimet (pass 1)** | 2.0°C | No (skip_sct=True) | ±500ft of BKN/OVC |
| **Ogimet-NWP** | 2.0°C | No (skip_sct=True) | ±500ft of BKN/OVC |
| **SFIP proxy** | 3.0°C | Yes (skip_sct=False) | ±500ft of all clouds |

SFIP proxy uses a wider gate (3°C, includes SCT) because it has no pass-2 NWP fallback to catch missed cloud.

### NWP Cloud Cover at Altitude — Path Comparison

| Scenario | GFS | ICON-EU | Other Models |
|----------|-----|---------|-------------|
| Diagnostics available? | Yes (full) | Yes (partial) | No |
| Layer boundaries? | Yes | No | No |
| Altitude check used? | Yes (±500ft margin) | No (no boundaries) | No |
| Cloud cover source | Open-Meteo bulk (BUG) | Open-Meteo bulk | Open-Meteo bulk |
| DD gate needed? | No | Yes (`need_dd_gate=True`) | Yes |
| Convective checked? | No (BUG) | No | N/A |

---

## Interpolation Summary

| Axis | What | Method | Notes |
|------|------|--------|-------|
| **Temporal** | Cloud diagnostics | Forward-fill from native GRIB hours | Up to 3h gap at longer lead times |
| **Temporal** | Cloud water (CLW/ICMR) | Forward-fill from native GRIB hours | Same timing as diagnostics |
| **Temporal** | Cloud cover % | Open-Meteo hourly interpolation | Independent of GRIB timing |
| **Spatial** | Cloud diagnostics | Linear between route points | One-sided fallback; max 100nm gap |
| **Spatial** | Cloud water (CLW/ICMR) | Linear between route points | Sets `clw_interpolated=True` |
| **Spatial** | Cloud cover % | Not interpolated | Each point has own API response |
| **Vertical** | Cloud water (CLW/ICMR) | Linear in pressure-space | Between 50hPa GRIB levels to fill 25hPa gaps |

---

## Future Considerations

### 1. ~~ICON-EU boundary estimation~~ ✓ DONE

ICON-EU (and all models without GRIB boundaries) now get synthesized NWP cloud layers via `_synthesize_nwp_layers()` in `clouds.py`. The heuristic narrowing logic (DD envelope + inversion capping + LCL) was moved from the TypeScript frontend to the Python backend, producing `EnhancedCloudLayer` objects with `source="synthesized"`. This provides consistent cloud layers across all models for both visualization and advisory evaluation.

### 2. Weight redistribution for missing high cloud in severity enhancement

The severity enhancement (`_enhance_severity`) checks NWP cloud cover at the zone's base altitude. High-altitude zones get `None` due to bug #3 (missing `nwp_cloud_high_pct` parameter). Even after fixing, severity enhancement at high altitude may need different calibration since icing at FL200+ is less common.

### 3. Cloud cover validation

When both GRIB diagnostic cover_pct and Open-Meteo bulk percentage are available, logging their divergence would help validate data consistency and identify cases where one source is significantly more accurate.
