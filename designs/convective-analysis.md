# Convective Assessment Analysis

> Per-model convective data pipeline, dual-method assessment, source inconsistencies, and recommendations.

## Overview

Convective assessment flows through four stages: **fetch** (Open-Meteo API + GRIB2 enrichment), **analysis** (thermo + NWP dual-method assessment), **resolution** (user-selectable active method), and **output** (visualization towers + advisory evaluators). Two parallel assessment methods produce independent `ConvectiveAssessment` objects that the user can switch between.

---

## Per-Model Convective Data Sources

### Open-Meteo API (variable per model)

| Variable | GFS | Best Match | ECMWF | ICON-EU | UKMO | MétéoFr | GEM |
|----------|-----|------------|-------|---------|------|---------|-----|
| `cape` (J/kg) | ✓ SB | ✓ SB | ✓ MU | ✓ ML | ✓ | ✗ | ✗ |
| `convective_inhibition` (J/kg) | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `lifted_index` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |

**Key issue:** Open-Meteo's `cape` field represents **different CAPE types** depending on the model:
- GFS / Best Match: **Surface-Based (SB)** CAPE
- ECMWF: **Most-Unstable (MU)** CAPE
- ICON-EU: **Mixed-Layer (ML)** CAPE
- UKMO: type unknown
- MétéoFr / GEM: not available

Tracked in `NWP_CAPE_TYPE` dict (`variables.py:84`), stored as `nwp_cape_type` on `ThermodynamicIndices`.

### GRIB2 Enrichment (GFS and ICON-EU only)

| Field | GFS | ICON-EU | Others |
|-------|-----|---------|--------|
| **Convective cloud cover %** | ✓ (TCDC on convectiveCloudLayer) | ✗ | ✗ |
| **Convective cloud base** | ✓ (PRES → ft via std atm) | ✓ (hbas_con, m → ft) | ✗ |
| **Convective cloud top** | ✓ (PRES → ft via std atm) | ✓ (htop_con, m → ft) | ✗ |

Stored in `NWPCloudDiagnostics.convective_{cover_pct,base_ft,top_ft}`.

### MetPy-Derived Thermodynamic Indices (all models with pressure levels)

Computed from pressure-level profiles via MetPy (`thermodynamics.py`):
- **CAPE:** Surface-based, most-unstable, and mixed-layer — all three always attempted
- **CIN:** Surface-based
- **LCL / LFC / EL:** Altitudes and pressures
- **Lifted index, Showalter index, K-index, Total Totals**
- **Bulk wind shear:** 0–6km and 0–1km
- **Precipitable water**

These are derived from 8–28 pressure levels (model-dependent resolution). Available for all models that provide pressure-level temperature + dewpoint data.

### Model Classification

| Model | NWP CAPE Source | CAPE Type | CIN | LI | Convective Cloud Diag | MetPy Indices | Effective Convective Data |
|-------|----------------|-----------|-----|----|-----------------------|---------------|--------------------------|
| **GFS** | Open-Meteo | SB | ✓ | ✓ | Full (cover + base/top) | All | Complete |
| **Best Match** | Open-Meteo | SB | ✓ | ✓ | Full (via GFS) | All | Complete |
| **ECMWF** | Open-Meteo | MU | ✗ | ✗ | None | All | Good (MU-CAPE + MetPy) |
| **ICON-EU** | Open-Meteo | ML | ✗ | ✗ | Partial (base/top only) | All | Good (base/top + MetPy) |
| **UKMO** | Open-Meteo | ? | ✓ | ✗ | None | All | Fair (MetPy-only risk) |
| **MétéoFr** | ✗ | — | ✗ | ✗ | None | All | MetPy-only |
| **GEM** | ✗ | — | ✗ | ✗ | None | All | MetPy-only |

---

## Convective Data Pipeline

### Stage 1: Fetch & Storage

```
Open-Meteo API ──→ HourlyForecast.cape_jkg                    (GFS/ECMWF/ICON/UKMO)
               ──→ HourlyForecast.convective_inhibition_jkg   (GFS/UKMO)
               ──→ HourlyForecast.lifted_index_raw             (GFS only)

GFS GRIB2 ──→ decode_cloud_diag_per_point()
           ──→ NWPCloudDiagnostics.convective_{cover_pct,base_ft,top_ft}

ICON-EU GRIB2 ──→ decode_icon_eu_cloud_diag_per_point()
              ──→ NWPCloudDiagnostics.convective_{base_ft,top_ft}  (no cover_pct)
```

### Stage 2: Temporal Forward-Fill

`fill.py:_forward_fill_cloud_diagnostics()` — forward-fills the entire `NWPCloudDiagnostics` object (including convective fields) from native GRIB hours to Open-Meteo interpolated hours.

### Stage 3: Spatial Interpolation

`spatial_interpolation.py:_lerp_diagnostics()` — linearly interpolates `convective_cover_pct`, `convective_base_ft`, `convective_top_ft` between neighboring route points.

### Stage 4: Analysis (Two Methods)

#### Method 1: Thermo (Thermodynamic) — Default

`convective.py:assess_convective_thermo()` → `ConvectiveAssessment`

- **Input:** `ThermodynamicIndices` (MetPy-derived from pressure levels)
- **Primary driver:** `_effective_cape()` = `max(SB-CAPE, MU-CAPE)` — catches elevated convection
- **Risk thresholds** (European-calibrated):

| CAPE (J/kg) | Risk |
|-------------|------|
| > 2000 | EXTREME |
| > 1000 | HIGH |
| > 300 | MODERATE |
| > 50 | LOW |
| > 0 + LFC + EL | MARGINAL |

- **CIN suppression:** CIN < −200 J/kg → risk reduced by one level
- **Severity modifiers:** shear >40kt (supercell), >25kt (multicell), high freezing + CAPE >1000 (hail), K >35, TT >55, LI < −6
- **Tower bounds:** base = LFC (LCL fallback), top = EL
- **Output:** `method="thermo"`, `cover_pct=None`

#### Method 2: NWP (Model Diagnostics) — Alternative

`convective.py:assess_convective_nwp()` → `ConvectiveAssessment | None`

- **Input:** `ThermodynamicIndices` + `NWPCloudDiagnostics`
- **Returns None when:** no diagnostics, or `convective_cover_pct` is None
- **Primary driver:** `convective_cover_pct` thresholds:

| Cover % | Risk |
|---------|------|
| ≥ 75% | HIGH |
| ≥ 50% | MODERATE |
| ≥ 25% | LOW |
| ≥ 10% | MARGINAL |

- **Tower bounds:** `convective_base_ft`, `convective_top_ft` from GRIB2
- **Severity modifiers:** same as thermo (computed from same indices)
- **Output:** `method="nwp"`, `cover_pct` set

| Model | NWP Convective Result | Notes |
|-------|----------------------|-------|
| **GFS** | Full (risk from cover %, base/top from GRIB) | Only model with convective cover % |
| **ICON-EU** | None | Has base/top but no cover_pct → returns None |
| **Others** | None | No diagnostics at all |

### Stage 5: Resolution

`tasks/advise.py:_resolve_analyses()` resolves `convective_method` user preference:
- `"thermo"` (default): no swap needed (`convective = convective_thermo`)
- `"nwp"`: swaps `convective_nwp` into `convective` slot (falls back to `convective_thermo` if NWP is None)

### Stage 6: Visualization

Two independent cross-section layers render convective towers:

| Layer | ID | Source | Default | Tower Bounds |
|-------|----|--------|---------|-------------|
| **Thermo Convective** | `thermo-convective-bg` | `convectiveRisk/BaseFt/TopFt` | on | LFC→EL (estimated if shallow) |
| **NWP Convective** | `nwp-convective-bg` | `nwpConvectiveRisk/BaseFt/TopFt` | off | GRIB convective base/top |

**Thermo tower top estimation** (`estimateTowerTop()`): When MetPy EL is unreliably close to LFC (<3000ft depth), uses fallback:
- Low risk: freezing level + 2000ft
- Moderate+: −20°C or −10°C level
- Last resort: base + 4000ft

**NWP towers** use model boundaries directly — no estimation needed.

Both layers share the same color palette (via exports from `thermo-convective-bg.ts`): bgWash, towerFill, hatching, anvil strip, CB labels.

### Stage 7: Advisory Evaluation

`advisories/convective.py:ConvectiveEvaluator` reads `sounding.convective` (the resolved active slot):
- HIGH/EXTREME at any point → RED
- Below-threshold points counted; percentage determines AMBER vs GREEN
- Parameters: `min_risk` (default 2 = LOW), `affected_pct_amber` (20%), `affected_pct_red` (50%)

---

## Bugs Found

### 1. ICON-EU convective NWP assessment always returns None

**Problem:** ICON-EU GRIB2 provides `convective_base_ft` and `convective_top_ft` (from `hbas_con`/`htop_con`) but **not** `convective_cover_pct`. The NWP assessment (`assess_convective_nwp`) returns None when `cover_pct is None`:

```python
cover = nwp_diagnostics.convective_cover_pct
if cover is None:
    return None  # ← ICON-EU always hits this
```

Yet ICON-EU's convective base/top are valuable model outputs (from the convective parameterization scheme). The base/top data is wasted — it only flows to the NWP cloud layer synthesis (as a convective layer) but not to convective risk assessment.

**Impact:** Medium. ICON-EU users never get NWP convective assessment even though the model has convective cloud boundary data. Only GFS benefits from the NWP method.

**Fix options:**
1. **Hybrid approach:** When cover_pct is absent but base/top exist, derive risk from `_effective_cape()` (thermo) but use GRIB base/top bounds. This gives the best of both: MetPy risk classification + model-native tower geometry.
2. **Synthesize cover from CAPE:** Map effective CAPE to an approximate convective cover percentage using the thermo risk thresholds, then use the NWP flow.

### 2. `_effective_cape()` ignores ML-CAPE

**Problem:** The function uses `max(SB-CAPE, MU-CAPE)`:

```python
values = [
    v for v in (indices.cape_surface_jkg, indices.cape_most_unstable_jkg)
    if v is not None
]
```

Mixed-layer CAPE (`cape_mixed_layer_jkg`) is excluded. For ICON-EU, the NWP model specifically provides ML-CAPE via Open-Meteo, and MetPy also computes it. Excluding it means ICON-EU's native CAPE signal has no direct path into the thermo risk assessment.

In practice, MU-CAPE ≥ ML-CAPE ≥ SB-CAPE for most profiles (MU is the max over all levels). But there are edge cases where the MetPy MU-CAPE computation fails or is lower than ML-CAPE due to level resolution.

**Impact:** Low in practice, but inconsistent with the goal of using the best available CAPE signal. Adding ML-CAPE to the max would be trivially correct.

**Fix:** `max(SB, MU, ML)` — add `indices.cape_mixed_layer_jkg` to the list.

### 3. Raw NWP CAPE vs MetPy CAPE divergence not used for convective assessment

**Problem:** The pipeline carefully computes `cape_raw_vs_calc_divergent` by comparing `nwp_cape_jkg` (Open-Meteo) vs `cape_surface_jkg` (MetPy). This flag is stored on `ThermodynamicIndices` but **never consumed** — neither the thermo assessment nor any advisory evaluator uses it.

The divergence can be significant: NWP models compute CAPE internally with 50–140 vertical levels, while MetPy re-derives it from 8–28 pressure levels. When pressure data is coarse, MetPy can show convective instability when the model's own CAPE is near zero (or vice versa).

**Impact:** Medium. Users may see misleading convective risk when MetPy CAPE diverges significantly from the model's own CAPE. The divergence flag exists precisely for this purpose but goes unused.

**Potential use:**
- When `cape_raw_vs_calc_divergent` is True and raw CAPE < computed CAPE, prefer raw (model has better vertical resolution)
- Display a "CAPE disagreement" warning in the UI
- Use it to modulate risk confidence

### 4. Thermo method CAPE is always MetPy-derived, ignores raw NWP CAPE

**Problem:** `_effective_cape()` uses `cape_surface_jkg` (MetPy) and `cape_most_unstable_jkg` (MetPy). The raw NWP CAPE (`nwp_cape_jkg`) is stored but **not considered** for the thermo risk classification.

For models where NWP CAPE is available (GFS, ECMWF, ICON, UKMO), the model's own CAPE computation is arguably more reliable — it uses the full model vertical resolution, not the 8–28 levels available via Open-Meteo.

**Impact:** Medium. CAPE from 8–28 pressure levels can over- or under-estimate compared to the model's native 50–140 level calculation. The `cape_raw_vs_calc_divergent` flag confirms this divergence exists in practice.

**Fix options:**
1. **Prefer NWP when available:** Use `nwp_cape_jkg` as the primary CAPE, fall back to MetPy max(SB, MU) when NWP unavailable
2. **Conservative approach:** Use `max(nwp_cape, metpy_effective_cape)` — never underestimate
3. **Type-aware hybrid:** When `nwp_cape_type` is "mu", compare with MetPy MU-CAPE specifically; when "sb", compare with SB-CAPE; use the higher of the two for safety

### 5. CAPE type mismatch in cross-model comparison

**Problem:** The model divergence system compares `cape_surface_jkg` (MetPy SB-CAPE) across all models. But `nwp_cape_jkg` is also compared separately. Neither comparison accounts for the fact that different models report different CAPE types:
- GFS `nwp_cape_jkg` is SB-CAPE
- ECMWF `nwp_cape_jkg` is MU-CAPE
- ICON-EU `nwp_cape_jkg` is ML-CAPE

Comparing GFS SB-CAPE = 200 J/kg against ECMWF MU-CAPE = 600 J/kg would show "poor agreement" when the disagreement is partly methodological, not meteorological.

**Impact:** Low for MetPy comparison (all models get the same MetPy computation). Medium for `nwp_cape_jkg` comparison — the divergence metric conflates CAPE type differences with genuine model disagreement.

**Fix:** Filter `nwp_cape_jkg` comparison to only compare models with the same `nwp_cape_type`. Or better: don't compare raw NWP CAPE across models at all — it's not an apples-to-apples comparison.

### 6. NWP convective layer boundary inconsistency: visualization vs assessment

**Problem:** The NWP convective visualization (`nwp-convective-bg.ts`) uses `nwpConvectiveBaseFt`/`nwpConvectiveTopFt` which come from `sounding.convective_nwp.base_ft/top_ft`. But `convective_nwp` is None for ICON-EU (bug #1), so the visualization shows nothing — even though `nwpCloudDiagnostics.convective_base_ft/top_ft` exist and are used for cloud layer synthesis.

There's a disconnect: ICON-EU convective boundaries flow to NWP cloud layers (via `build_nwp_cloud_layers`) but NOT to the NWP convective visualization layer.

**Impact:** ICON-EU has convective tower geometry but it's only visible in the NWP cloud bands layer (as a synthesized layer), never as an actual convective tower.

### 7. Advisory evaluator reads resolved `convective` slot — NWP fallback is silent

**Problem:** The `ConvectiveEvaluator` reads `sounding.convective` (the active slot). When `convective_method="nwp"` but NWP is None (5 of 7 models), the resolution falls back to `convective_thermo` silently. The advisory doesn't indicate that it fell back.

This is the same silent fallback pattern identified in the cloud layers analysis. The user selects "NWP" convective method but gets thermo results for most models without knowing it.

**Impact:** Low — the fallback to thermo is reasonable behavior. But there's no transparency about which method was actually used per model.

---

## Inconsistencies

### 1. Asymmetric information between thermo and NWP methods

The thermo method gets rich MetPy indices (CAPE, CIN, LCL, LFC, EL, shear, K-index, TT, LI, precipitable water) and produces severity modifiers. The NWP method gets only `convective_cover_pct` but then copies the same MetPy severity modifiers into its output. This means:
- NWP risk is driven by cover % (grid-scale parameterization)
- NWP severity modifiers are driven by MetPy indices (sounding analysis)
- These can disagree: high cover % (from model convective scheme) but low MetPy CAPE (from coarse pressure levels), or vice versa

### 2. No CIN suppression in NWP method

The thermo method suppresses risk by one level when CIN < −200 J/kg. The NWP method doesn't — if the model's convective scheme reports 50% convective cloud cover, the risk is MODERATE regardless of CIN. This is arguably correct (the model already accounts for CIN internally in its convective parameterization), but it means the two methods can disagree on risk level for the same atmospheric state.

### 3. MARGINAL risk handling differs between methods

- **Thermo:** MARGINAL = any CAPE > 0 with defined LFC and EL (shallow convection possible)
- **NWP:** MARGINAL = 10–25% convective cover

The thermo MARGINAL is very sensitive — any small positive CAPE with LFC/EL triggers it. The NWP MARGINAL requires the model's convective scheme to actually activate (10%+ cover). In practice, thermo produces many more MARGINAL points than NWP.

Both the thermo visualization and the NWP visualization skip MARGINAL towers (render only LOW+), so this is mainly an advisory-level difference.

### 4. Tower top estimation only applies to thermo, not needed for NWP

The thermo method has elaborate tower-top estimation logic (`estimateTowerTop()`) because MetPy EL can be unreliably shallow on coarse pressure levels. The NWP method uses model boundaries directly. This is correct behavior (model-native boundaries are authoritative), but means towers from the two methods can look very different for the same point.

### 5. Convective assessment vs altitude advisories use different cloud data

- `ConvectiveEvaluator` reads `sounding.convective.risk_level` — pure threshold on CAPE or cover %
- Altitude advisories (`advisories.py`) use `nwp_cloud_diagnostics.convective_{base_ft,top_ft,cover_pct}` directly for regime transitions — this is always from GRIB regardless of the user's convective method preference
- Vertical regime labels don't reflect the convective method choice

### 6. `VizPoint.capeSurfaceJkg` always uses MetPy SB-CAPE

The route graph CAPE metric (`capeSurfaceJkg`) and the thermo tooltip show MetPy surface-based CAPE. For models where the NWP provides a different CAPE type (ECMWF MU, ICON ML), the displayed CAPE may underrepresent the model's own convective signal. The NWP raw CAPE is not exposed in the route graph.

---

## Per-Model Convective Pipeline Summary

### GFS / Best Match (Full Pipeline)

```
Open-Meteo → cape_jkg (SB), convective_inhibition_jkg, lifted_index_raw
GRIB2      → NWPCloudDiagnostics.convective_{cover_pct,base_ft,top_ft}
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT, LI
Analysis:
  Thermo assessment  → risk from max(SB,MU) CAPE, CIN suppression, severity modifiers
  NWP assessment     → risk from cover_pct thresholds, GRIB base/top
Visualization:
  Thermo towers      → LFC→EL (estimated if shallow), always available
  NWP towers         → GRIB convective base/top, available when GRIB enriched
Advisory:
  ConvectiveEvaluator → reads active slot (user choice)
```

### ICON-EU (Partial GRIB)

```
Open-Meteo → cape_jkg (ML)
GRIB2      → NWPCloudDiagnostics.convective_{base_ft,top_ft}  (no cover_pct!)
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT
Analysis:
  Thermo assessment  → risk from max(SB,MU) CAPE, CIN suppression
  NWP assessment     → None (cover_pct is None → returns None)
Visualization:
  Thermo towers      → LFC→EL (estimated if shallow), always available
  NWP towers         → NOT rendered (convective_nwp is None)
Advisory:
  ConvectiveEvaluator → always uses thermo (NWP fallback)
```

### ECMWF (Open-Meteo Only, MU-CAPE)

```
Open-Meteo → cape_jkg (MU)
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT
Analysis:
  Thermo assessment  → risk from max(SB,MU) CAPE
  NWP assessment     → None (no diagnostics)
Visualization:
  Thermo towers      → LFC→EL, always available
  NWP towers         → NOT rendered
Advisory:
  ConvectiveEvaluator → always uses thermo
Note: ECMWF's MU-CAPE catches elevated convection that SB-CAPE misses.
      MetPy also computes MU-CAPE, so _effective_cape() benefits from both sources.
```

### UKMO

```
Open-Meteo → cape_jkg (?), convective_inhibition_jkg
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT
Analysis:
  Thermo assessment  → risk from max(SB,MU) CAPE, CIN suppression
  NWP assessment     → None (no diagnostics)
Note: UKMO is one of few models providing CIN via Open-Meteo.
```

### MétéoFr / GEM (Minimal Convective Data)

```
Open-Meteo → No convective indices at all
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT
Analysis:
  Thermo assessment  → MetPy-only (no raw NWP CAPE to compare/validate)
  NWP assessment     → None
Note: Entirely dependent on MetPy quality from 8-28 pressure levels.
      No validation possible against model-native CAPE.
```

---

## Recommendations

### 1. Use NWP raw CAPE when available (Priority: High)

The model's own CAPE computation uses full vertical resolution (50–140 levels) vs MetPy's 8–28 levels. When `nwp_cape_jkg` is available, it should at minimum participate in `_effective_cape()`:

```python
def _effective_cape(indices: ThermodynamicIndices) -> float | None:
    values = [
        v for v in (
            indices.cape_surface_jkg,
            indices.cape_most_unstable_jkg,
            indices.cape_mixed_layer_jkg,  # Fix bug #2
            indices.nwp_cape_jkg,          # Use raw NWP
        )
        if v is not None
    ]
    return max(values) if values else None
```

This is conservative (never underestimates) and handles the CAPE type heterogeneity by taking the maximum.

### 2. Enable ICON-EU NWP convective assessment (Priority: Medium)

ICON-EU has convective base/top from GRIB but no cover_pct. A hybrid approach would use CAPE-based risk + GRIB base/top:

```python
def assess_convective_nwp(indices, nwp_diagnostics):
    if nwp_diagnostics is None:
        return None

    cover = nwp_diagnostics.convective_cover_pct
    base_ft = nwp_diagnostics.convective_base_ft
    top_ft = nwp_diagnostics.convective_top_ft

    if cover is not None:
        # Full NWP path (GFS)
        risk = risk_from_cover(cover)
    elif base_ft is not None and top_ft is not None:
        # Hybrid path (ICON-EU): CAPE risk + GRIB bounds
        cape = _effective_cape(indices)
        risk = risk_from_cape(cape)  # reuse thermo thresholds
    else:
        return None

    return ConvectiveAssessment(
        risk_level=risk,
        base_ft=base_ft,
        top_ft=top_ft,
        cover_pct=cover,
        method="nwp" if cover is not None else "nwp_hybrid",
        ...
    )
```

### 3. Add `convective_method_effective` tracking (Priority: Low)

Same pattern as `cloud_method_effective` — record which method was actually used per model after fallback:

```python
if swap_convective:
    nwp = sounding.convective_nwp
    if nwp is not None:
        updates["convective"] = nwp
        updates["convective_method_effective"] = "nwp"
    else:
        updates["convective"] = sounding.convective_thermo
        updates["convective_method_effective"] = "thermo"  # fallback
```

### 4. Don't compare `nwp_cape_jkg` across models (Priority: Low)

Remove `nwp_cape_jkg` from the cross-model divergence comparison, or add type-aware grouping. Comparing SB-CAPE (GFS) against MU-CAPE (ECMWF) is not meaningful.

### 5. Consider simplifying to a single convective method (Priority: Discussion)

The NWP convective method only works for GFS (the only model with convective cover %). ICON-EU has partial data that's wasted. For all other models, NWP falls back to thermo silently. The user-facing "thermo vs NWP" choice is effectively a "MetPy CAPE vs GFS convective cover %" choice.

Options:
- **Keep dual methods** but make the NWP method hybrid (recommendation #2 above)
- **Merge into one method** that uses all available signals: max(MetPy CAPE, NWP CAPE) for risk, GRIB base/top when available otherwise MetPy LFC→EL, convective cover % as an additional confidence signal
- **Deprecate NWP method** since it only works for one model and the thermo method produces reasonable results across all models

---

## Interpolation Summary

| Axis | What | Method | Notes |
|------|------|--------|-------|
| **Temporal** | Convective diagnostics | Forward-fill from native GRIB hours | Carries base/top/cover as a unit |
| **Temporal** | Open-Meteo CAPE/CIN/LI | Hourly interpolation by Open-Meteo | Independent of GRIB timing |
| **Spatial** | Convective diagnostics | Linear between route points | base_ft, top_ft, cover_pct each lerped |
| **Spatial** | Open-Meteo CAPE/CIN/LI | Not interpolated | Each route point has own API response |

---

## Cross-Model CAPE Comparison

### What each model provides (CAPE signal quality)

| Model | NWP CAPE | NWP Type | MetPy SB | MetPy MU | MetPy ML | Pressure Levels | CAPE Quality |
|-------|----------|----------|----------|----------|----------|----------------|-------------|
| GFS | ✓ | SB | ✓ | ✓ | ✓ | 28 | Excellent (NWP + MetPy) |
| Best Match | ✓ | SB | ✓ | ✓ | ✓ | 28 | Excellent |
| ECMWF | ✓ | MU | ✓ | ✓ | ✓ | 13 | Good (fewer levels → coarser MetPy) |
| ICON-EU | ✓ | ML | ✓ | ✓ | ✓ | 24 | Good |
| UKMO | ✓ | ? | ✓ | ✓ | ✓ | 16 | Fair (unknown NWP type) |
| MétéoFr | ✗ | — | ✓ | ✓ | ✓ | 16 | Fair (MetPy-only, no validation) |
| GEM | ✗ | — | ✓ | ✓ | ✓ | 15 | Fair (MetPy-only, no validation) |

The number of pressure levels significantly affects MetPy CAPE quality. ECMWF's 13 levels can miss thin unstable layers that GFS's 28 levels resolve. This is why the `cape_raw_vs_calc_divergent` flag was introduced — and why raw NWP CAPE should be preferred when available.
