# Cross-Cutting Review: Icing × Clouds × Convection

> Pipeline interdependencies, method coupling, inconsistencies, and simplification opportunities across the three subsystems.

## Pipeline Order & Data Flow

```
prepare → thermodynamics → enrich_lwc → detect_cloud_layers (DD)
       → inversions → build_nwp_cloud_layers → sfip → ogimet_dd → ogimet_nwp
       → convective_thermo → convective_nwp → vertical_motion → ceiling
```

All methods computed eagerly at analysis time with fixed inputs. User method preferences only apply later during **resolution** (`_resolve_analyses`), which swaps pre-computed results into active slots via `model_copy()`.

---

## Dependency Matrix

### What each icing method uses from clouds & convection

| Input | Ogimet-DD | Ogimet-NWP | SFIP |
|-------|-----------|------------|------|
| **DD cloud_layers** | ✓ proximity gate (BKN/OVC) | ✓ fallback gate when no GRIB diag | ✓ proxy variant gate (SCT+) |
| **NWP cloud cover %** | ✗ never | ✓ cloud fraction scaling | ✓ proxy M_CLW membership |
| **NWP cloud diagnostics** | ✗ never | ✓ altitude-aware cloud cover + glaciation | ✓ altitude-aware cloud + glaciation |
| **CAPE** | ✓ `cape_surface_jkg` → layered/convective split | ✓ same | ✗ not used |
| **CLW/ICMR** | ✓ LWC direct (pass 1 gate) | ✗ | ✓ full variant M_CLW |
| **Convective assessment** | ✗ | ✗ | ✗ |

### What altitude advisories use

| Input | Source | Method-Aware? |
|-------|--------|---------------|
| `analysis.cloud_layers` | Active slot (resolved) | ✓ Yes — reads after `_resolve_analyses` swaps |
| `analysis.icing_zones` | Active slot (resolved) | ✓ Yes |
| `analysis.nwp_cloud_diagnostics` | Raw GRIB diagnostics | ✗ **Always raw** — bypasses method choice |
| `analysis.cloud_cover_{low,mid,high}_pct` | Raw Open-Meteo | ✗ Always raw |
| `analysis.indices.cape_surface_jkg` | MetPy thermo | ✗ Always thermo |

### What route advisory evaluators use

All evaluators (`ConvectiveEvaluator`, `IcingEscapeEvaluator`, etc.) read the **resolved active slots** from `SoundingAnalysis` after `_resolve_analyses` has run. This is correct — they respect user method choices.

---

## Inconsistencies Found

### 1. Icing CAPE uses only `cape_surface_jkg`, not `_effective_cape()` ⚠️ Medium

**Problem:** The layered/convective icing split (`_cape_to_cloud_split`) receives `indices.cape_surface_jkg` (MetPy SB-CAPE only):

```python
# __init__.py:224
icing_zones = assess_icing_zones_ogimet_dd(
    derived_levels, cloud_layers,
    cape_jkg=indices.cape_surface_jkg,  # ← SB only
)
```

Meanwhile, convective assessment uses `_effective_cape()` = `max(SB, MU, ML, NWP)`. This means:
- Convective risk says HIGH (from NWP CAPE = 1200 J/kg)
- Icing split says "80% layered / 20% convective" (from SB-CAPE = 100 J/kg)

The icing formula underweights convective icing when SB-CAPE is low but elevated/mixed-layer instability is high — exactly the European maritime scenario the MU/ML CAPE expansion was designed to address.

**Fix:** Pass `_effective_cape(indices)` to the icing methods instead of `indices.cape_surface_jkg`. Or import and reuse the same function from `convective.py`.

**Impact:** Moderate. In elevated convection scenarios (common in European maritime environments), icing zones may undercount convective icing severity.

### 2. Altitude advisories computed before method resolution ⚠️ Medium

**Problem:** `compute_altitude_advisories()` is called in `analyze.py:153` during the analysis stage, **before** `_resolve_analyses()` runs in the advisory stage. The altitude advisories (vertical regimes, descend/climb advisories) always use:
- DD cloud layers (default `cloud_layers`)
- Ogimet-DD icing zones (default `icing_zones`)

When a user selects `cloud_method="nwp"` + `icing_method="sfip_nwp"`, the altitude advisories still reflect DD clouds and Ogimet-DD icing — potentially showing different cloud boundaries and icing zones than what the user sees in other views.

**Impact:** The descend-below-icing and climb-above-icing recommendations may reference different cloud/icing boundaries than the user's selected method shows. The vertical regime labels ("In cloud, icing MOD") could disagree with the cross-section visualization.

**Fix options:**
1. **Move** `compute_altitude_advisories()` to the advisory stage, after resolution
2. **Recompute** altitude advisories in `_resolve_analyses()` when a swap occurs
3. **Accept** as documented behavior — altitude advisories use the "best available" (DD + Ogimet-DD) regardless

Option 3 is reasonable because altitude advisories are meant to be conservative guidance, and DD + Ogimet-DD is the most-tested combination. But it should be documented.

### 3. Altitude advisories use raw `nwp_cloud_diagnostics` for convective transitions ⚠️ Low

**Problem:** In `advisories.py:156-161`, convective cloud transitions are added to vertical regimes only when `diag.convective_cover_pct > 0`:

```python
if (diag.convective_base_ft is not None
        and diag.convective_cover_pct and diag.convective_cover_pct > 0):
    transitions.add(_round_alt(diag.convective_base_ft))
```

ICON-EU has `convective_base_ft`/`convective_top_ft` but no `convective_cover_pct`, so its convective transitions are **never added** to the regime computation. With the new hybrid NWP convective assessment providing ICON-EU convective data, there's now a disconnect: `convective_nwp` has base/top data, but altitude advisories don't use it for transition boundaries.

**Fix:** Check `convective_base_ft is not None` directly, rather than gating on `convective_cover_pct > 0`.

### 4. No `convective_method_effective` or `icing_method_effective` tracking ⚠️ Low

**Problem:** Cloud method resolution tracks `cloud_method_effective` ("dd", "nwp", "nwp_synthesized") to indicate fallbacks. Neither icing nor convective resolution has equivalent tracking:
- When `convective_method="nwp"` but NWP is None, silently falls back to thermo
- When `icing_method="ogimet_nwp"`, there's no flag indicating whether the NWP method had full diagnostics or fell back to DD proximity gating

The convective `method` field partially covers this (set at construction: "thermo", "nwp", "nwp_hybrid"), but icing has nothing.

**Fix:** Add `icing_method_effective` and `convective_method_effective` to `SoundingAnalysis`, populated during `_resolve_analyses()`.

### 5. Cloud top uncertainty uses `cape_surface_jkg` not `_effective_cape()` ⚠️ Low

**Problem:** In both `clouds.py:385` and `advisories.py:695`, cloud top uncertainty logic checks `cape_surface_jkg > 500` to determine if a cloud is convective:

```python
if cape_jkg is not None and cape_jkg > 500 and indices.el_altitude_ft is not None:
    cl.theoretical_max_top_ft = indices.el_altitude_ft
```

Same issue as #1 — for elevated convection with low SB-CAPE, this misses the convective signal.

**Fix:** Use `_effective_cape()` consistently.

### 6. `cape_raw_vs_calc_divergent` set AFTER convective assessment uses CAPE ⚠️ Low

**Problem:** In `__init__.py`, the pipeline order is:
1. Line 247: `assess_convective_thermo(indices)` — uses `indices.nwp_cape_jkg` (still None)
2. Line 291: `indices.nwp_cape_jkg = hourly.cape_jkg` — sets NWP CAPE
3. Line 304: `indices.cape_raw_vs_calc_divergent = ...` — computes divergence

The NWP CAPE is attached to `indices` **after** the convective assessment has already read it. But `_effective_cape()` reads `indices.nwp_cape_jkg`... Let me check the timing more carefully.

Actually, looking at line 247-250:
```python
convective = assess_convective_thermo(indices)
convective_nwp = assess_convective_nwp(
    indices, hourly.nwp_cloud_diagnostics if hourly else None,
)
```

Both convective assessments run at line 247-250. NWP CAPE is set at line 291. So **`_effective_cape()` always sees `nwp_cape_jkg=None`** during convective assessment.

**This is a bug.** The new `_effective_cape()` includes `indices.nwp_cape_jkg`, but it's always None when the function runs because the NWP value isn't attached yet.

**Fix:** Move the NWP value preservation block (lines 288-306) to **before** the convective assessment (before line 247).

---

## Simplification Opportunities

### A. Unify CAPE selection for icing + convection + cloud uncertainty

Three places independently decide which CAPE to use:
- **Convective:** `_effective_cape(indices)` → max(SB, MU, ML, NWP)
- **Icing:** `indices.cape_surface_jkg` → SB only
- **Cloud top uncertainty:** `indices.cape_surface_jkg` → SB only

Create a single `effective_cape` field on `ThermodynamicIndices` (computed once after NWP enrichment) and use it everywhere.

### B. Consider moving NWP enrichment earlier in the pipeline

The "Raw NWP value preservation" block (lines 288-306) attaches `nwp_cape_jkg`, `nwp_cape_type`, `nwp_cin_jkg`, `nwp_lifted_index`, `nwp_freezing_level_ft` to indices. This should happen **before** any consumer reads those fields. Currently it's at the end, meaning convective assessment, icing, and cloud uncertainty all see stale (None) NWP values.

Move it to right after `compute_indices()` (after line 180).

### C. `_is_near_cloud` duplication

`icing.py:_is_near_cloud` and `sfip.py:_is_near_cloud` are thin wrappers around `icing_common.is_near_cloud` with different parameters. These could be inlined or the parameters could be passed directly at call sites, removing two wrapper functions.

---

## What's Working Well

1. **Eager computation + lazy resolution** — All methods computed once, resolution is just a swap. Clean separation of concerns.
2. **Cloud source independence** — Each icing method uses its natural cloud signal (DD attenuation for Ogimet-DD, NWP fraction for Ogimet-NWP, CLW for SFIP). The user's `cloud_method` choice correctly doesn't affect icing computation.
3. **`_resolve_analyses` is non-mutating** — Uses `model_copy()`, original data preserved.
4. **DD fallback in Ogimet-NWP** — When NWP diagnostics are absent, the DD proximity gate prevents false positives from bulk ICAO band percentages. Good defensive design.
5. **Shared icing utilities** — `icing_common.py` centralizes cloud proximity, icing type classification, and zone grouping. All three methods use consistent type thresholds.

---

## Priority Summary

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| **6** | NWP CAPE attached after convective reads it → `_effective_cape()` always sees None for nwp_cape_jkg | **High (bug)** | Low |
| **1** | Icing CAPE uses SB only, not effective CAPE | Medium | Low |
| **2** | Altitude advisories use pre-resolution data | Medium | Medium |
| **5** | Cloud top uncertainty uses SB-CAPE only | Low | Low |
| **3** | ICON-EU convective transitions skipped in regimes | Low | Low |
| **4** | No icing/convective method_effective tracking | Low | Low |
| **B** | Move NWP enrichment earlier (fixes #6, enables #1 and #5) | **High** | Low |
| **A** | Unify CAPE selection | Medium | Low |
| **C** | Deduplicate `_is_near_cloud` wrappers | Low | Trivial |
