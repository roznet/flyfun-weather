# Cloud Layers Analysis

> Per-model cloud data pipeline, source tracing, interpolation methods, and consistency analysis across all icing methods and visualization.

_Code references verified against the repo on 2026-07-11._

> **`nwp_cloud_layers` is strictly model-native.** A server-side synthesized-layer
> fallback for Open-Meteo-only models was added then **reverted** (df4474ff), so
> `build_nwp_cloud_layers` has only two tiers: 3D fraction (`nwp_3d`) and GRIB bulk
> bands (`grib`). Open-Meteo-only models (GEM/UKMO/MétéoFr) return `None`, NOT
> synthesized bands. The `EnhancedCloudLayer.source` enum no longer carries
> `"synthesized"` at runtime.
>
> **Known minor item (code kept as-is, decision 2026-06-06):** `advise.py:112`
> labels any non-`grib` NWP layer set `cloud_method_effective="nwp_synthesized"`,
> which mislabels genuine `nwp_3d` layers (ECMWF/ICON). Cosmetic — affects only
> that metadata string; not corrected in code.

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
- ECMWF `deg0l` is wired onto `hourly.freezing_level_m` during enrichment, so `indices.nwp_freezing_level_ft` carries a model-native value rather than Open-Meteo's.

### Model Classification

| Model | NWP Cloud Diagnostics | Preferred Cloud Source | Effective Resolution |
|-------|-----------------------|------------------------|----------------------|
| **GFS** | Bulk bands w/ base/top + convective | GRIB bulk bands | Altitude-precise (ICAO-bounded) |
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
```

### Stage 2: Temporal Interpolation

`fill.py:_fill_cloud_diagnostics()` (invoked from `propagate_all`) fills `nwp_cloud_diagnostics` on gap hours between native GRIB steps.

- **GFS, when `gfs_init` is provided** — `_interp_gfs_diag_hourly` does **window-midpoint linear interpolation** for low/mid/high cover (LCDC/MCDC/HCDC are averaged-window fields in GFS pgrb2, so each anchor sits at `step - window_length/2`). Layer geometry (`base_ft`, `top_ft`, `top_temp_c`) holds over from the higher-cover endpoint; sub-5 % covers drop the layer entirely. Convective, boundary, total, ceiling, and freezing level interpolate linearly with step-time anchoring. A follow-up `apply_gfs_rh_condensate_gate` drops bands whose pressure-level RH and condensate inside `[base_ft, top_ft]` contradict the averaged cover. See [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).
- **ICON-EU, ECMWF, and the GFS fallback path** (no `gfs_init`) — `_fill_diag_hourly` forward-fills. ICON-EU and ECMWF publish instantaneous cover; persistence is the right semantic.

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

Four-tier approach, tried in this preference order:

**Tier 0 — Per-level 3D cloud fraction (ECMWF `cc`, ICON-EU `clc`):** `build_nwp_cloud_layers_from_fraction(pressure_levels)` scans `cloud_area_fraction_pct` vertically and groups consecutive levels by their METAR coverage category (FEW/SCT/BKN/OVC, with sub-FEW = clear). A category change starts a new layer; sub-FEW levels split layers (clear-air gaps). Each layer's base/top altitudes come from linear threshold-crossing on `cloud_area_fraction_pct` against the boundary CAF separating this layer from its (different-category) neighbor — a model-derived edge instead of pinning to a level altitude. At the column floor/TOA where there's no opposing neighbor, the edge falls back to the altitude midpoint between the deck-edge level and its in-bounds neighbor (or the level's own altitude if there's no neighbor at all). Tagged `source="nwp_3d"`. Preferred when any level carries `cloud_area_fraction_pct` — strictly richer than GRIB-bulk because deck boundaries come from the model's own cloud scheme at sounding-level resolution.

**Tier 1 — GRIB bulk bands (GFS):** `_build_grib_layers()` uses GFS's native per-band boundaries (`HGHL/HGHM/HGHH` → base_ft/top_ft) and `LCDC/MCDC/HCDC` coverage. Each layer tagged `source="grib"`.

**(Historical) Synthesized layers — REMOVED (df4474ff):** a former fallback once
synthesized layers for Open-Meteo-only models by narrowing ICAO bands (DD cloud
envelope + inversion capping ≥2°C + LCL floor, min cover 25%, `source="synthesized"`).
Reverted so `nwp_cloud_layers` is strictly model-native; the tier below now applies.

**Tier 2 — No native source:** Returns None when neither 3D fraction nor GRIB bulk-band boundaries are available (Open-Meteo-only models GEM/UKMO/MétéoFr). Open-Meteo bulk % is intentionally NOT synthesized into layers here (the synth fallback was dropped in refactor df4474ff so nwp_cloud_layers is strictly model-native).

- **Coverage from %:** ≥87.5% → OVC, ≥50% → BKN, ≥25% → SCT, ≥12.5% → FEW (Tier 0 classifies each level individually then splits on category change; Tiers 1–2 use bulk band %)
- **Output:** Stored in `SoundingAnalysis.nwp_cloud_layers`
- **Source tracking:** `EnhancedCloudLayer.source` ∈ {"dd", "nwp_3d", "grib"} at runtime (the "synthesized" source was removed when nwp_cloud_layers became strictly model-native; the model.py comment still lists it as a historical value).
- **Method tracking:** `cloud_method_effective` records "dd" or "nwp". Note `advise.py:112` still emits "nwp_synthesized" for any non-`grib` source set — a stale metadata label that now also catches `nwp_3d` (cosmetic; see banner).
- **Quantitative metadata:** `EnhancedCloudLayer.mean_cloud_cover_pct` carries the underlying numeric — mean `cloud_area_fraction_pct` across the (homogeneous) deck for `nwp_3d`, the band's `cover_pct` for `grib` (incl. convective). Surfaced in the cross-section tooltip as `(CC nn%)`. Null for `dd` (uses `mean_dewpoint_depression_c` instead).

| Model | NWP Cloud Layers Result | Source Tag | Notes |
|-------|------------------------|------------|-------|
| **GFS** | Full layer list with boundaries | `grib` | All 3 bands + convective from GRIB2 |
| **Best Match** | Full layer list (via GFS) | `grib` | Same as GFS |
| **ECMWF** | Per-deck layers from 3D `cc` | `nwp_3d` | Real model cloud scheme, not constrained to ICAO bands |
| **ICON-EU** | Per-deck layers from 3D `clc` + convective from GRIB | `nwp_3d` | Same as ECMWF; convective layer added when present |
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

This function (`icing_common.py:98-145`) is the shared altitude-aware cloud cover lookup used by Ogimet-NWP and SFIP proxy. Two paths:

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
# icing_common.py, in nwp_cloud_cover_at_altitude()
pct = bulk_pct or 0.0  # Uses Open-Meteo cloud_cover_{low,mid,high}_pct
```

Meanwhile, `_build_grib_layers()` in `clouds.py:573` correctly prefers the GRIB diagnostic cover_pct:
```python
cover_pct = diag.cover_pct if diag.cover_pct is not None else fallback_pct
```

And the frontend `bandCoverPct()` also correctly uses diagnostic cover_pct:
```typescript
return diag[band].coverPct ?? 0;  // Uses GRIB diagnostic value
```

**Impact:** Icing assessment (Ogimet-NWP, SFIP proxy) and cloud layer visualization can use different cloud cover values for the same band at the same location. The GRIB diagnostic `cover_pct` is the native model output; Open-Meteo values are post-processed and may diverge.

**Status (2026-06-06):** Known minor inconsistency, **code kept as-is** by decision — not a briefing-correctness defect (both numbers are "cloud cover" from slightly different sources). A one-line fix (`pct = diag_layer.cover_pct or bulk_pct`) is available if calibration ever shows it matters.

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

### 3. ~~`_nwp_cloud_for_zone` doesn't pass `nwp_cloud_high_pct`~~ ✓ FIXED

**Was:** The function (now `_nwp_cloud_for_altitude`, icing.py:284) previously didn't accept or pass `nwp_cloud_high_pct`. It now takes and forwards `nwp_cloud_high_pct`, and `assess_icing_zones_ogimet_nwp`/`_ieng` thread it through. For models without diagnostics, icing zones at FL200+ get `None` for NWP cloud cover, preventing severity enhancement.

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

(Historical) Ogimet-DD previously had a two-pass approach with a pass-2 NWP fallback using `nwp_cloud_cover_at_altitude()`. This was removed (see icing_common.py comment 'old pass-1/pass-2 hybrid, no longer'); current `assess_icing_zones_ogimet_dd` (icing.py:362) is single-pass, gated by `is_in_cloud_layer` + DD attenuation. But the two methods can produce different results because:

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

Open-Meteo provides hourly-interpolated cloud cover percentages. GRIB2 native-step diagnostics arrive at 1h or 3h intervals.

- **GFS path (with `gfs_init`)** — low/mid/high cover is now window-midpoint linearly interpolated and the RH/condensate gate drops phantom layers (see [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate) and Stage 2). The remaining desync is between the freshly-interpolated cover and held-over layer geometry — bounded by the bracketing native steps' window length (≤ 3 h on either side past f120). *(Previously stated as ≤ 1.5 h, derived from the old capped-at-3h window model. NCEP windows actually reach 6 h, alternating 3 / 6 past f120 — corrected in #480, see [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate).)*
- **ICON-EU / ECMWF / GFS fallback** — forward-fill leaves boundaries stale up to one full native step behind (3 h at longer lead times). Cover and boundaries can disagree by that much. Accepted as a reasonable approximation; ICON-EU and ECMWF cover is instantaneous, so persistence of both cover and geometry together is consistent.

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
                         + convective layer from ICON GRIB (when present)
  nwp_cloud_at_alt    → ICAO-band bulk fallback (cc not yet plumbed here)
Visualization:
  DD cloud bands      → gray gradient (always)
  NWP cloud bands     → real per-deck boundaries (blue tint)
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
| **Temporal** | GFS cloud diagnostics (low/mid/high cover) | Window-midpoint linear interp between native steps (requires `gfs_init`); geometry held from higher-cover endpoint; sub-5 % drops layer; RH/condensate gate drops phantom layers post-interp | See [meteorology-decisions.md §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate) |
| **Temporal** | GFS cloud diagnostics (instantaneous: convective, boundary, total, ceiling, freezing level) | Step-time linear interp (requires `gfs_init`) | Instantaneous fields in GFS pgrb2 — no midpoint offset |
| **Temporal** | ICON-EU / ECMWF cloud diagnostics + GFS fallback path | Forward-fill from preceding native GRIB hour | Up to 3h gap at longer lead times |
| **Temporal** | Cloud water (CLW/ICMR) | GFS: step-time linear interp (requires `gfs_init`), otherwise forward-fill; ECMWF / ICON-EU rebuild via `_linear_interp_pressure_levels` | Instantaneous mixing ratios — step-time anchoring |
| **Temporal** | Cloud cover % (`cloud_cover_low/mid/high_pct`) | Open-Meteo hourly interpolation | Independent of GRIB timing |
| **Spatial** | Cloud diagnostics | Linear between route points | One-sided fallback; max 100nm gap |
| **Spatial** | Cloud water (CLW/ICMR) | Linear between route points | Sets `clw_interpolated=True` |
| **Spatial** | Cloud cover % | Not interpolated | Each point has own API response |
| **Vertical** | Cloud water (CLW/ICMR) | Linear in pressure-space | Between 50hPa GRIB levels to fill 25hPa gaps |

---

## Future Considerations

### 1. ICON-EU boundary estimation — solved via 3D fraction, synthesis reverted

ICON-EU gets real per-deck boundaries from its 3D `clc` field (Tier 0, `source="nwp_3d"`). A server-side synthesized-layer fallback for Open-Meteo-only models was added then deliberately reverted (commit df4474ff) so `nwp_cloud_layers` is strictly model-native; GEM/UKMO/MétéoFr now return `None` (no NWP layers) rather than synthesized bands.

### 2. Weight redistribution for missing high cloud in severity enhancement

The severity enhancement (`_enhance_severity`) checks NWP cloud cover at the zone's base altitude. High-altitude zones get `None` due to bug #3 (missing `nwp_cloud_high_pct` parameter). Even after fixing, severity enhancement at high altitude may need different calibration since icing at FL200+ is less common.

### 3. Cloud cover validation

When both GRIB diagnostic cover_pct and Open-Meteo bulk percentage are available, logging their divergence would help validate data consistency and identify cases where one source is significantly more accurate.
