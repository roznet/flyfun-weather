# Convective Assessment Analysis

> Per-model convective data pipeline, dual-method assessment, source inconsistencies, and recommendations.

_Code references verified against the repo on 2026-06-20._

> 📐 Convective design rationale (realizable-CAPE/regime tiers, DD-stays-pure, NWP-cover vs CAPE risk) is decided in [meteorology-decisions.md](./meteorology-decisions.md) §4–§5 — read before changing thresholds or the DD/NWP boundary.

**Key code paths** (bare filenames below refer to these):
- `analysis/sounding/convective.py` — `assess_convective_thermo`, `assess_convective_nwp`, `classify_regime`, `effective_cape` (alias `_effective_cape`), `convection_realized`, `convective_cross_check` / `ConvectiveCrossCheck`
- `analysis/sounding/thermodynamics.py` — `compute_indices_core` (all three CAPE variants), `compute_derived_levels_core` (omega 700)
- `analysis/sounding/__init__.py` — orchestrates the lite/full passes; sets `cape_raw_vs_calc_divergent`
- `analysis/advisories/convective.py` — `ConvectiveEvaluator` (reads resolved slot + runs the cross-check)
- `fetch/variables.py` (`NWP_CAPE_TYPE`, line 83), `fetch/grib/fill.py`, `analysis/spatial_interpolation.py`, `analysis/comparison.py`, `tasks/advise.py`

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

Tracked in `NWP_CAPE_TYPE` dict (`variables.py:83`), stored as `nwp_cape_type` on `ThermodynamicIndices`.

### GRIB2 Enrichment (GFS, ICON-EU, and ECMWF)

| Field | GFS | ICON-EU | ECMWF | Others |
|-------|-----|---------|-------|--------|
| **Convective cloud cover %** | ✓ (TCDC on convectiveCloudLayer) | ✗ | ✗ | ✗ |
| **Convective cloud base** | ✓ (PRES → ft via std atm) | ✓ (hbas_con, m → ft) | ✗ | ✗ |
| **Convective cloud top** | ✓ (PRES → ft via std atm) | ✓ (htop_con, m → ft) | ✓ (hcct, m → ft) | ✗ |

Stored in `NWPCloudDiagnostics.convective_{cover_pct,base_ft,top_ft}`.

### MetPy-Derived Thermodynamic Indices (all models with pressure levels)

Computed from pressure-level profiles via MetPy (`thermodynamics.py`):
- **CAPE:** Surface-based, most-unstable, and mixed-layer — all three always attempted
- **CIN:** Surface-based
- **LCL / LFC / EL:** Altitudes and pressures
- **Lifted index, Showalter index, K-index, Total Totals**
- **Bulk wind shear:** 0–6km and 0–1km
- **Precipitable water**

These are derived from 13–28 pressure levels (model-dependent resolution). Available for all models that provide pressure-level temperature + dewpoint data.

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

`fill.py:_fill_cloud_diagnostics()` — forward-fills the entire `NWPCloudDiagnostics` object (including convective fields) from native GRIB hours to Open-Meteo interpolated hours.

### Stage 3: Spatial Interpolation

`spatial_interpolation.py:_lerp_diagnostics()` — linearly interpolates `convective_cover_pct`, `convective_base_ft`, `convective_top_ft` between neighboring route points.

### Stage 4: Analysis (Two Methods)

#### Method 1: Thermo (Thermodynamic) — Default

`convective.py:assess_convective_thermo(indices, omega_700_pa_s=None)` → `ConvectiveAssessment`

- **Input:** `ThermodynamicIndices` (MetPy-derived from pressure levels) + optional 700 hPa omega (large-scale ascent trigger, read from `derived_levels` by the caller via `_omega_near_700()`)
- **Base risk:** `_effective_cape()` thresholds (European-calibrated):

| CAPE (J/kg) | Risk |
|-------------|------|
| > 2000 | EXTREME |
| > 1000 | HIGH |
| > 300 | MODERATE |
| > 50 | LOW |
| > 0 + LFC + EL | MARGINAL |

- **Regime discrimination** (`classify_regime(cape, cin)` → `ConvectiveRegime`, classified from **potential** CAPE = `_effective_cape()` = max(SB,MU,ML)): the base CAPE risk is refined per regime, so a single CAPE threshold no longer misjudges a capped "loaded gun" or mislabels thermally driven convection:

| Regime | Boundary | Risk behaviour |
|--------|----------|----------------|
| **THERMAL** | CAPE < 300 | Potential CAPE risk kept; labelled + annotated only (full thermal scoring needs PBLH / terrain / diurnal — deferred) |
| **WEAK_INSTABILITY** | 300 ≤ CAPE < 800 | Potential CAPE risk kept; ascent/subsidence noted as driver/suppressor |
| **LOADED_GUN** | CAPE ≥ 800 & CIN ≤ −50 | Scored on **potential** CAPE (never softened by low ML — that is the dangerous case). ω₇₀₀ ascent (≤ −0.1 Pa/s) → risk kept (cap may erode). Omega present, no ascent → **down one level** (cap holds). Omega **unavailable** → risk kept with an honest "no ascent data" note, **except** a very strong cap (CIN < −200) which suppresses regardless. ω₇₀₀ read from `derived_levels` (populated in `compute_derived_levels_core`, the lite pass where the assessment runs) |
| **ACTIVE** | CAPE ≥ 800 & CIN > −50 | Scored on **realizable ML-CAPE** (`_realizable_risk`), floored at **one level below** the potential tier, **unless** ω₇₀₀ ascent keeps the full potential tier or ML is unavailable (stay conservative). This is where surface CAPE over-reads a dry, poorly-mixed column — see meteorology-decisions.md §4 |

- **Elevated-convection flag:** `elevated_convection` (bool + driver) fires when MU − SB ≥ 200 J/kg & MU ≥ 300 — most-unstable parcel above the surface, convection possible aloft. Additive warning, independent of the surface tier and regime.
- **Generic CIN suppression:** preserved for THERMAL / WEAK_INSTABILITY only (CIN < −200 J/kg → one level down). LOADED_GUN models the cap via the omega gate above; ACTIVE is weak-cap by definition and tempered on ML instead.
- **CAPE parcels:** MU/ML-CAPE are computed in `compute_indices_core` (not `_extended`) so the tier — run in the lite pass, used by briefings **and** standalone verification — always sees all three variants.
- **Explanation outputs:** `regime`, `drivers` (factors raising risk), and `suppressors` (factors holding it down) are populated and consumed by `digest/prompt_builder.py` (LLM narrative) and `digest/text.py` (plain-text digest). They are serialized into `briefing.json` but not yet rendered in the web/iOS UI.
- **Severity modifiers:** shear >40kt (supercell), >25kt (multicell), high freezing + CAPE >1000 (hail), K >35, TT >55, LI < −6
- **Tower bounds:** base = LFC (LCL fallback), top = EL
- **Output:** `method="thermo"`, `cover_pct=None`

#### Method 2: NWP (Model Diagnostics) — Alternative

`convective.py:assess_convective_nwp()` → `ConvectiveAssessment | None`

- **Input:** `ThermodynamicIndices` + `NWPCloudDiagnostics`
- **Returns None when:** no diagnostics (`nwp_diagnostics is None` — e.g. AROME/UKMO/Météo-France, Open-Meteo-only). With diagnostics present it always returns a real assessment (quiet scheme → `risk=NONE`), so the DD-vs-NWP cross-check can fire.
- **Risk is model-native, NOT CAPE (#283).** The tier comes from the model's own convective-scheme output, making the NWP track genuinely independent of the DD (parcel-CAPE) track:
  - **Primary scale — convective tower top** (`convective_top_ft`, the one native field common to GFS/ECMWF/ICON): `_CONV_TOP_FL_THRESHOLDS` (FL380 EXTREME / FL280 HIGH / FL200 MODERATE / FL120 LOW / present-but-shallow MARGINAL).
  - **Cover modifier** (GFS, where `convective_cover_pct` is present): numerous cover (≥35%) bumps the top-derived tier up one level, capped at HIGH. Cover-only (no top) sets a depth-unknown tier capped at MODERATE. The MODERATE cap is on the *base* tier only — Phase-2 corroboration (below) can still raise a strongly-realized cover-only MODERATE to HIGH when native precip/indices are strong. This can't fire today (GFS exposes no native `k_index`/`total_totals` and ECMWF takes the tower-top path), but the cap is intentionally not a hard ceiling against corroboration.
  - **Precip-rate fallback (Phase 3, method `"nwp_precip"`):** when the model emits *no* tower top and *no* cover fraction but `convective_precip_mm_h > 0.1` — the ECMWF marine / elevated-convection case, where `cp` lands but `hcct` is sentinel/absent — the tier comes from a convective-precip-rate ladder (`_CONV_PRECIP_MM_H_THRESHOLDS`: ≥2.0 → MODERATE, ≥0.5 → LOW, ≥0.1 → MARGINAL) instead of the old false NONE. Tower top stays primary whenever present; this is the geometry-absent fallback only. Depth is unknown from rate alone, so the ladder is capped at MODERATE. Realized by construction, so it skips the firing gate hold-down and the precip corroboration (which would double-count `cp`), and the strong-CIN suppression below (penalising a demonstrably-firing scheme on surface/ML CIN is circular). Rate is resolution-dependent → synoptic-scale v1.
  - **Firing gate (Phase 2):** a MODERATE+ tower is only kept there when the scheme *realized* convection (`convective_precip_mm_h > 0.1` OR `convective_cover_pct > 15`); a deep-but-dry tower is held down one level. Missing-data-safe — held down only on positive dry evidence. **Known gap (ICON-EU):** ICON exposes neither `convective_cover_pct` nor (yet) `convective_precip_mm_h`, so realization can't be evaluated and every ICON tower is kept at full native tier — no dry-tower suppression until `rain_con` is decoded. Tracked as a calibration gap.
  - **Native corroboration (Phase 2):** a realized MODERATE+ cell with strong native `k_index`/`total_totals`/conv-precip bumps up one level (capped HIGH). Thresholds (`_CORROB_K_INDEX=35`, `_CORROB_TOTAL_TOTALS=50`) are North-American severe-convection defaults; European environments (lower lapse rates, more modest moisture) often need TT ≥55, so this may fire readily over Europe — flagged for eval-digest calibration.
  - **Strong-CIN suppression** kept (prefers the model's own `ml_cin_jkg`, else DD `cin_surface_jkg`) — but *not* applied on the `"nwp_precip"` path (see Phase 3 above).

**Method strings** (preserved; consumed by `dd_nwp_agreement` history and front co-location's `method != "thermo"`), set by which geometry the model exposes:

- **`"nwp"`** (GFS — has `convective_cover_pct`): cover present; `base_ft`/`top_ft` from GRIB. Quiet native points (no top/cover geometry) also use this method with `risk=NONE`.
- **`"nwp_hybrid"`** (ICON-EU — has base+top, no cover): tower bounds from GRIB2 `convective_base_ft`/`convective_top_ft`.
- **`"nwp_lcl_top"`** (ECMWF — has `hcct` top only): tower base = `indices.lcl_altitude_ft` (LCL proxy), top = `convective_top_ft`; the `top > LCL` guard rejects a sub-LCL hcct artefact (→ quiet NONE).
- **`"nwp_precip"`** (Phase 3 — any native model firing `cp` with no tower top and no cover, in practice ECMWF over marine / elevated convection): risk from the precip-rate ladder, `base_ft`/`top_ft` both None. See the precip-rate fallback above.
- **`"nwp_cape_fallback"`**: only when the diagnostics carry *no* native cloud content at all (defensive — production builders return `None`, not an empty diag). CAPE-scored like DD, marked distinctly so the cross-check skips the circular comparison.

- **Severity modifiers:** same as thermo (computed from the same indices, descriptive text only — separate from the native risk tier).

| Model | NWP Convective Result | Notes |
|-------|----------------------|-------|
| **GFS** | `method="nwp"` — risk from tower top + cover modifier; cover/base/top from GRIB | Only model with convective cover % |
| **ICON-EU** | `method="nwp_hybrid"` — risk from tower top; GRIB base/top; `cape_ml`/`cin_ml` native | — |
| **ECMWF** | `method="nwp_lcl_top"` when `hcct` present; `method="nwp_precip"` (Phase 3) when `hcct` sentinel but `cp` fires — risk from the precip-rate ladder; `cp`/`kx`/`totalx`/`mlcape`/`mlcin` native | `hcct` is sparsely delivered (sentinel over marine/elevated convection), so `cp` is often the only firing signal |
| **Others** | None | No diagnostics at all |

### Stage 5: Resolution

`tasks/advise.py:_resolve_analyses()` resolves `convective_method` user preference:
- `"thermo"` (default): no swap needed (`convective = convective_thermo`)
- `"nwp"`: swaps `convective_nwp` into `convective` slot (falls back to `convective_thermo` if NWP is None)

### Stage 6: Visualization

Two independent cross-section layers render convective towers:

| Layer | ID | Source | Default | Tower Bounds |
|-------|----|--------|---------|-------------|
| **Thermo Convective** | `thermo-convective-bg` | `convectiveRisk/BaseFt/TopFt` | off | LFC→EL (estimated if shallow) |
| **NWP Convective** | `nwp-convective-bg` | `nwpConvectiveRisk/BaseFt/TopFt` | on | GRIB convective base/top |

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
- **DD-floor guardrail (#283):** `convective_method` defaults to `"nwp"`, so the active slot is the model-native track. A quiet NWP must never *suppress* a DD HIGH (capped loaded gun — where models under-fire), so the graded risk floors at the DD (thermo) tier: `graded_risk = max(active, convective_thermo)`. The native divergence is surfaced via the cross-check below, not blended into the DD tier. The below-cruise altitude filter falls back to the thermo EL when the DD floor raised the grade and the quiet NWP track has no geometry (`top_ft=None`) — otherwise `top_ft=None` would bypass the filter (review I1).

**Orthogonal axis — convective *character* (avoidability).** Separate from this severity evaluator, `advisories/convective_character.py` grades whether route convection is circumnavigable VFR (ISOLATED/SCATTERED→AMBER, WIDESPREAD/ORGANIZED/EMBEDDED→RED), never changing the severity colour. Severity is altitude-aware *above* (the overfly filter here). #298 adds the mirror *below*: an **annotate-only** below-base clearance note on ISOLATED/SCATTERED that reports the model-native cell base vs cruise (`base_clearance_ft`, default 2000 ft, mirroring `top_clearance_ft`), **gated on the sub-base layer being VMC** (no BKN/OVC deck to descend into) and degrading to "depth unresolved" when the tower/base is `None`. Full rationale + the safety-first precedence in `designs/meteorology-decisions.md` §15.

### Stage 8: DD-vs-Model Cross-Check

`convective.py:convective_cross_check(thermo, nwp)` → `ConvectiveCrossCheck | None`. The evaluator runs this per route point using `convective_thermo` explicitly (never the resolved slot — it must stay a DD-vs-NWP comparison even if the user picked NWP). It compares the DD (parcel-CAPE) thermo risk against the model's native **firing** signal (#283): convective precip > the gate, cover ≥ 25%, or a tower ≥ FL200. Bare convective-geometry presence is **not** enough — a shallow Cu top would otherwise spuriously read "active". (The model's `risk_level` is not compared directly here; the risk-level comparison that used to live in `dd_nwp_agreement` was removed for convective — this inline cross-check is the single source of truth, see `designs/advisories.md`.)

Fires only on two material divergences (else `None`):
- `dd_not_corroborated` — thermo MODERATE+ but the scheme is quiet (no convective precip, low/no cover, no deep tower)
- `model_active_dd_quiet` — thermo NONE/MARGINAL but the scheme fired (precip / cover ≥ 25% / tower ≥ FL200)

LOW thermo is intentionally in neither band; the gap between the quiet and active bands (e.g. cover 10–25%, tower FL120–200) is also neither. The result never changes the grade — it is surfaced in the advisory popup and LLM digest only. Thresholds are tunable module constants (`_FIRING_PRECIP_MM_H`, `_XCHECK_MODEL_QUIET_COVER_PCT`, `_XCHECK_MODEL_ACTIVE_COVER_PCT`, `_XCHECK_DEEP_TOP_FL`, `_XCHECK_QUIET_TOP_FL`).

Note: this is distinct from the unused `cape_raw_vs_calc_divergent` flag (Bugs #3/#5) — that compares raw NWP CAPE vs MetPy CAPE magnitudes; this cross-check compares the thermo *tier* against the model's *convective scheme*.

---

## Bugs Found

### 1. ~~ICON-EU convective NWP assessment always returns None~~ ✅ FIXED

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

### 2. ~~`_effective_cape()` ignores ML-CAPE~~ ✅ FIXED

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

The divergence can be significant: NWP models compute CAPE internally with 50–140 vertical levels, while MetPy re-derives it from 13–28 pressure levels. When pressure data is coarse, MetPy can show convective instability when the model's own CAPE is near zero (or vice versa).

**Impact:** Medium. Users may see misleading convective risk when MetPy CAPE diverges significantly from the model's own CAPE. The divergence flag exists precisely for this purpose but goes unused.

**Potential use:**
- When `cape_raw_vs_calc_divergent` is True and raw CAPE < computed CAPE, prefer raw (model has better vertical resolution)
- Display a "CAPE disagreement" warning in the UI
- Use it to modulate risk confidence

### 4. ~~Thermo method CAPE is always MetPy-derived, ignores raw NWP CAPE~~ ✅ FIXED

**Problem:** `_effective_cape()` uses `cape_surface_jkg` (MetPy) and `cape_most_unstable_jkg` (MetPy). The raw NWP CAPE (`nwp_cape_jkg`) is stored but **not considered** for the thermo risk classification.

For models where NWP CAPE is available (GFS, ECMWF, ICON, UKMO), the model's own CAPE computation is arguably more reliable — it uses the full model vertical resolution, not the 8–28 levels available via Open-Meteo.

**Impact:** Medium. CAPE from 13–28 pressure levels can over- or under-estimate compared to the model's native 50–140 level calculation. The `cape_raw_vs_calc_divergent` flag confirms this divergence exists in practice.

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

### 6. ~~NWP convective layer boundary inconsistency: visualization vs assessment~~ ✅ FIXED (by bug #1 fix)

**Problem:** The NWP convective visualization (`nwp-convective-bg.ts`) uses `nwpConvectiveBaseFt`/`nwpConvectiveTopFt` which come from `sounding.convective_nwp.base_ft/top_ft`. Previously `convective_nwp` was None for ICON-EU, so the visualization showed nothing.

**Fix:** The hybrid NWP path (bug #1 fix) now returns a `ConvectiveAssessment` with `method="nwp_hybrid"` for ICON-EU, populating `base_ft`/`top_ft` from GRIB data. The NWP visualization layer now renders ICON-EU convective towers.

### 7. Advisory evaluator reads resolved `convective` slot — NWP fallback is silent

**Problem:** The `ConvectiveEvaluator` reads `sounding.convective` (the active slot). When `convective_method="nwp"` but NWP is None (the 3 models without diagnostics — UKMO/MétéoFr/GEM), the resolution falls back to `convective_thermo` silently. The advisory doesn't indicate that it fell back.

This is the same silent fallback pattern identified in the cloud layers analysis. The user selects "NWP" convective method but gets thermo results for most models without knowing it.

**Impact:** Low — the fallback to thermo is reasonable behavior. But there's no transparency about which method was actually used per model.

---

## Inconsistencies

### 1. Asymmetric information between thermo and NWP methods — by design (#283)

The two methods are now *deliberately* independent: thermo risk is parcel-CAPE-driven, NWP risk is model-native (tower top + cover/precip, NOT CAPE). The MetPy severity modifiers are still copied into the NWP output as descriptive text (separate from the native risk tier). These can disagree — high native cover/tower but low MetPy CAPE, or vice versa — and that disagreement is the **product goal**, surfaced via the inline cross-check (Stage 8). The historical "NWP risk is CAPE-driven, cover informational" framing no longer holds (#283).

### 2. CIN suppression in the NWP method (#283)

Both methods now apply strong-CIN suppression (CIN < −200 J/kg → one level down). The NWP method prefers the model's own `ml_cin_jkg` when present (ECMWF `mlcin100`, ICON `cin_ml`), falling back to the DD `cin_surface_jkg`. The realized-convection **firing gate** is the native-side analogue of the cap (a deep-but-dry tower is held down one level), so a capped tower the scheme reports but won't realize is no longer left at full risk.

### 3. MARGINAL risk handling differs between methods

- **Thermo:** MARGINAL = any CAPE > 0 with defined LFC and EL (shallow convection possible)
- **NWP (#283):** MARGINAL = a convective tower present but shallow (< FL120), or scattered cover (15–35%) when no tower top is reported

The thermo MARGINAL is very sensitive — any small positive CAPE with LFC/EL triggers it. The NWP MARGINAL requires the model's convective scheme to actually produce a (shallow) tower or scattered cover. In practice, thermo produces many more MARGINAL points than NWP.

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
  Thermo assessment  → risk from max(SB,MU,ML,NWP) CAPE, CIN suppression, severity modifiers
  NWP assessment     → risk from tower top (FL tiers) + cover modifier + firing gate (#283), GRIB base/top
Visualization:
  Thermo towers      → LFC→EL (estimated if shallow), always available
  NWP towers         → GRIB convective base/top, available when GRIB enriched
Advisory:
  ConvectiveEvaluator → reads active slot (user choice)
```

### ICON-EU (Partial GRIB — Hybrid NWP)

```
Open-Meteo → cape_jkg (ML)
GRIB2      → NWPCloudDiagnostics.convective_{base_ft,top_ft}  (no cover_pct)
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT
Analysis:
  Thermo assessment  → risk from max(SB,MU,ML,NWP) CAPE, CIN suppression
  NWP assessment     → Hybrid: tower-top risk + GRIB base/top + cape_ml/cin_ml (method="nwp_hybrid", #283)
Visualization:
  Thermo towers      → LFC→EL (estimated if shallow), always available
  NWP towers         → GRIB convective base/top, available via hybrid path
Advisory:
  ConvectiveEvaluator → reads active slot (user choice, both methods available)
```

### ECMWF (Full GRIB — IFS sounding + a1 surface diagnostics)

```
Open-Meteo → cape_jkg (MU)   [surface fields; pressure levels replaced by IFS GRIB]
GRIB2 (a1) → NWPCloudDiagnostics.convective_top_ft (hcct), cp→convective_precip_mm_h,
             kx/totalx→k_index/total_totals, mlcape100/mlcin100→ml_cape/ml_cin
MetPy      → SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT
Analysis:
  Thermo assessment  → risk from max(SB,MU,ML) CAPE, CIN suppression
  NWP assessment     → hcct present: LCL-anchored tower-top risk + LCL base + firing
                       gate/corroboration from cp/kx/totalx/mlcin (method="nwp_lcl_top", #283);
                       hcct sentinel but cp>0.1: precip-rate ladder (method="nwp_precip",
                       Phase 3) — the marine/elevated-convection case Open-Meteo & hcct both miss
Visualization:
  Thermo towers      → LFC→EL, always available
  NWP towers         → LCL→hcct, available when GRIB enriched
Advisory:
  ConvectiveEvaluator → reads active slot (user choice, both methods available)
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
Note: Entirely dependent on MetPy quality from 13-28 pressure levels.
      No validation possible against model-native CAPE.
```

---

## Recommendations

### 1. ~~Use NWP raw CAPE when available~~ ✅ IMPLEMENTED (MetPy-primary, NWP fallback)

`effective_cape()` takes `max(SB, MU, ML)` over the **MetPy** variants and uses `nwp_cape_jkg` **only as a last-resort fallback when no MetPy variant is available** — NWP raw CAPE is *not* folded into the max pool. Rationale (see the `effective_cape` docstring): model-native CAPE can diverge significantly from sounding-derived values (different parcel selection, virtual-temperature corrections), so including it in the max pool could inflate convective risk when the sounding doesn't support it. `test_effective_cape_nwp_raw_fallback_only` pins this behaviour.

```python
def effective_cape(indices) -> float | None:
    metpy = [v for v in (indices.cape_surface_jkg,
                         indices.cape_most_unstable_jkg,
                         indices.cape_mixed_layer_jkg) if v is not None]
    if metpy:
        return max(metpy)
    return indices.nwp_cape_jkg  # fallback only
```

### 2. ~~Enable ICON-EU NWP convective assessment~~ ✅ IMPLEMENTED

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

The NWP convective method works for GFS (full), ICON-EU (hybrid), and ECMWF (LCL-anchored) — 3 of 6 active models. For UKMO/MétéoFr/GEM, NWP falls back to thermo silently. The user-facing "thermo vs NWP" choice is effectively a "MetPy CAPE vs GFS convective cover %" choice.

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
| ICON-EU | ✓ | ML | ✓ | ✓ | ✓ | 19 | Good |
| UKMO | ✓ | ? | ✓ | ✓ | ✓ | 20 | Fair (unknown NWP type) |
| MétéoFr | ✗ | — | ✓ | ✓ | ✓ | 19 | Fair (MetPy-only, no validation) |
| GEM | ✗ | — | ✓ | ✓ | ✓ | 20 | Fair (MetPy-only, no validation) |

The number of pressure levels significantly affects MetPy CAPE quality. ECMWF's 13 levels can miss thin unstable layers that GFS's 28 levels resolve. This is why the `cape_raw_vs_calc_divergent` flag was introduced — and why raw NWP CAPE should be preferred when available.
