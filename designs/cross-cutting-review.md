# Cross-Cutting Review: Icing × Clouds × Convection

> Pipeline interdependencies, method coupling, inconsistencies, and simplification opportunities across the three subsystems.

_Original review 2026-06-06; re-verified 2026-08-15. Status: **#1/#4/#5/#6/A/B/C fixed**, **#2/#3 still open**. Live anchors (the in-text line numbers below the header are from the original review and no longer match — trust these):_
- _`analyze_sounding_lite` / `_analyze_sounding_heavy` split `__init__.py` (~291 / ~455). NWP CAPE is attached in the **lite** pass (`__init__.py:339`) before both convective assessments (`~390`, `~404-411`) — #6/B fixed._
- _`eff_cape = effective_cape(indices)` at `__init__.py:504`; feeds `enrich_cloud_top_uncertainty` (505) and all three CAPE-consuming icing calls (545/552/563) — #1/#5/A fixed._
- _ICON-EU convective-transition gate: `advisories.py:166-171` — **still gated on `convective_cover_pct > 0`** (#3 open)._
- _`compute_altitude_advisories` still called at `tasks/analyze.py:160`, in the analysis stage before `_resolve_analyses` (`tasks/advise.py:63`) — #2 open._
- _`icing_method_effective` / `convective_method_effective` now exist (`models/analysis.py:1165-1166`, stamped in `advise.py:156-199`) — #4 fixed by #408._

## Pipeline Order & Data Flow

Since the original review the pipeline is split into two passes (`__init__.py`):

```
lite  : prepare → compute_indices_core → attach NWP values (CAPE/CIN/LI/FL/KX/TT)
        → detect_cloud_layers (DD) → inversions
        → convective_thermo → convective_nwp | convective_explicit → ceiling
heavy : compute_indices_extended → enrich_lwc → effective_cape
        → enrich_cloud_top_uncertainty → inversions → build_nwp_cloud_layers
        → sfip → ogimet_dd → ogimet_nwp → ieng → precipitation → sld
        → stability → vertical_motion
```

The NWP-value attach moved into the lite pass **ahead of** every consumer — that is the structural fix behind #6/B/A.

All methods computed eagerly at analysis time with fixed inputs. User method preferences only apply later during **resolution** (`_resolve_analyses`), which swaps pre-computed results into active slots via `model_copy()`.

Two additions the original review predates: a fourth icing method **IENG** (`assess_icing_zones_ieng`, `icing.py:511`) and an **explicit-convection** track (`assess_convective_explicit`, used when the hour carries a convection-permitting payload such as ICON-D2, #462). IENG is computed and stored on `ieng_icing_zones` but is **not yet selectable** in `_resolve_analyses` — it can never reach `icing_zones`.

---

## Dependency Matrix

### What each icing method uses from clouds & convection

| Input | Ogimet-DD | Ogimet-NWP | IENG | SFIP |
|-------|-----------|------------|------|------|
| **DD cloud_layers** | ✓ proximity gate (BKN/OVC) | ✗ never | ✗ never | ✓ proxy variant gate (SCT+) |
| **NWP cloud layers** | ✗ | ✓ hard gate — `[]` if absent | ✓ hard gate — `[]` if absent | ✓ full variant gate |
| **NWP cloud cover %** | ✗ never | ✓ cloud fraction scaling | ✓ cloud fraction scaling | ✓ proxy M_CLW membership |
| **NWP cloud diagnostics** | ✗ never | ✓ altitude-aware cloud cover + glaciation | ✓ altitude-aware cloud cover (no glaciation) | ✓ altitude-aware cloud + glaciation |
| **CAPE** | ✓ `effective_cape` → layered/convective split | ✓ same | ✓ convective component above 100 J/kg | ✗ not used |
| **CLW/ICMR** | ✓ LWC direct (pass 1 gate) | ✗ | ✗ | ✓ full variant M_CLW |
| **Convective assessment** | ✗ | ✗ | ✗ | ✗ |

Note the corrected Ogimet-NWP row: it does **not** fall back to a DD proximity gate. `assess_icing_zones_ogimet_nwp` (and IENG) return `[]` outright when no model-native cloud envelope exists, and `_resolve_analyses` translates that into `active_icing_available=False` + `icing_method_effective=None` so evaluators grade UNAVAILABLE rather than clear-by-absence (#391).

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

### 1. ~~Icing CAPE uses only `cape_surface_jkg`, not `_effective_cape()`~~ ✅ FIXED

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

**Problem:** `compute_altitude_advisories()` is called in `tasks/analyze.py:160` during the analysis stage, **before** `_resolve_analyses()` runs in the advisory stage (`tasks/advise.py:63`). The altitude advisories (vertical regimes, descend/climb advisories) always use:
- DD cloud layers (default `cloud_layers`)
- Ogimet-DD icing zones (default `icing_zones`)

When a user selects `cloud_source="nwp"` + `icing_method="sfip_nwp"`, the altitude advisories still reflect DD clouds and Ogimet-DD icing — potentially showing different cloud boundaries and icing zones than what the user sees in other views.

**Impact:** The descend-below-icing and climb-above-icing recommendations may reference different cloud/icing boundaries than the user's selected method shows. The vertical regime labels ("In cloud, icing MOD") could disagree with the cross-section visualization.

**Fix options:**
1. **Move** `compute_altitude_advisories()` to the advisory stage, after resolution
2. **Recompute** altitude advisories in `_resolve_analyses()` when a swap occurs
3. **Accept** as documented behavior — altitude advisories use the "best available" (DD + Ogimet-DD) regardless

Option 3 is reasonable because altitude advisories are meant to be conservative guidance, and DD + Ogimet-DD is the most-tested combination. But it should be documented.

### 3. Altitude advisories use raw `nwp_cloud_diagnostics` for convective transitions ⚠️ Low

**Problem:** In `advisories.py:166-171`, convective cloud transitions are added to vertical regimes only when `diag.convective_cover_pct > 0`:

```python
if (diag.convective_base_ft is not None
        and diag.convective_cover_pct and diag.convective_cover_pct > 0):
    transitions.add(_round_alt(diag.convective_base_ft))
```

ICON-EU has `convective_base_ft`/`convective_top_ft` but no `convective_cover_pct`, so its convective transitions are **never added** to the regime computation. With the new hybrid NWP convective assessment providing ICON-EU convective data, there's now a disconnect: `convective_nwp` has base/top data, but altitude advisories don't use it for transition boundaries.

**Fix:** Check `convective_base_ft is not None` directly, rather than gating on `convective_cover_pct > 0`.

**Still open as of 2026-08-15.** Only the GRIB decoder populates `convective_cover_pct` (`fetch/grib/decode.py:115/122` — `tcc` on `convectiveCloudLayer`), so any model without that field keeps its convective base/top invisible to the regime computation. The ICON-D2 explicit-convection track (#462) widens the gap further: it produces convective structure with no `convective_cover_pct` at all.

### D. IENG icing is computed but unreachable ⚠️ Low

`assess_icing_zones_ieng` runs on every heavy pass and its output is stored on `ieng_icing_zones`, but `_resolve_analyses` has no `icing_method == "ieng"` branch — only `ogimet_nwp`, `sfip_nwp`, and the implicit `ogimet_dd`. So the fourth method can never be resolved into the active `icing_zones` slot, and every sounding pays its cost. Either wire the branch (plus the settings-page option and `icing_method_effective` label) or stop computing it.

### 4. ~~No `convective_method_effective` or `icing_method_effective` tracking~~ ✅ FIXED

Delivered by #408. Both fields now live on `SoundingAnalysis` and are stamped by `_resolve_analyses` **on every branch**, including the no-swap DD/thermo path — deliberately, so "graded on DD" cannot read the same as "this advisory has no method axis". `icing_method_effective` is left `None` only when the method could not run at all (pairs with `active_icing_available=False`). Evidence regions and `primary_method_id` badge from these fields.

### 5. ~~Cloud top uncertainty uses `cape_surface_jkg` not `_effective_cape()`~~ ✅ FIXED

**Problem:** In both `clouds.py:385` and `advisories.py:695`, cloud top uncertainty logic checks `cape_surface_jkg > 500` to determine if a cloud is convective:

```python
if cape_jkg is not None and cape_jkg > 500 and indices.el_altitude_ft is not None:
    cl.theoretical_max_top_ft = indices.el_altitude_ft
```

Same issue as #1 — for elevated convection with low SB-CAPE, this misses the convective signal.

**Fix:** Use `_effective_cape()` consistently.

### 6. ~~`cape_raw_vs_calc_divergent` set AFTER convective assessment uses CAPE~~ ✅ FIXED

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

All consumers now route through one function, `convective.effective_cape(indices)` → max(SB, MU, ML, NWP) (`convective.py:58`; `_effective_cape` is a back-compat alias):
- **Convective:** ✅ · **Icing (all CAPE-using methods):** ✅ via `eff_cape` · **Cloud top uncertainty:** ✅ · **Cloud-top-uncertainty source label** in `advisories.py:794`: ✅

Residual (minor, not scheduled): it is still a function called at ~6 sites rather than a field on `ThermodynamicIndices`. Fine as-is — the ordering hazard that motivated a cached field is gone now that NWP attach happens in the lite pass.

### B. ~~Move NWP enrichment earlier in the pipeline~~ ✅ FIXED

The raw-NWP attach block (`nwp_cape_jkg`, `nwp_cape_type`, `nwp_cin_jkg`, `nwp_lifted_index`, `nwp_k_index`, `nwp_total_totals`, `nwp_freezing_level_ft`) now runs in `analyze_sounding_lite` immediately after `compute_indices_core`, ahead of every consumer. `cape_raw_vs_calc_divergent` is computed in the same block.

### C. ~~`_is_near_cloud` duplication~~ ✅ DONE

The `_is_near_cloud` wrappers in `icing.py` and `sfip.py` have been removed; all call sites now use `icing_common.is_near_cloud` directly.

---

## What's Working Well

1. **Eager computation + lazy resolution** — All methods computed once, resolution is just a swap. Clean separation of concerns.
2. **Cloud source independence** — Each icing method uses its natural cloud signal (DD attenuation for Ogimet-DD, NWP fraction for Ogimet-NWP, CLW for SFIP). The user's `cloud_source` choice correctly doesn't affect icing computation.
3. **`_resolve_analyses` is non-mutating** — Uses `model_copy()`, original data preserved. (Its docstring still claims an early return for the all-DD case; there deliberately isn't one any more — provenance is stamped on every path.)
4. **Ogimet-NWP refuses to fabricate** — With no model-native cloud envelope it returns `[]` rather than gating on bulk ICAO band percentages, and the caller marks that as *unavailable* rather than *clear*. Good defensive design; note this replaced the DD-fallback behaviour the original review praised.
5. **Shared icing utilities** — `icing_common.py` centralizes cloud proximity (`is_near_cloud`, `is_in_cloud_layer`), icing type classification, and zone grouping. All four methods use consistent type thresholds.

---

## Priority Summary

| # | Issue | Severity | Effort |
|---|-------|----------|--------|
| **6** | ~~NWP CAPE attached after convective reads it~~ | ~~**High (bug)**~~ | ✅ FIXED |
| **1** | ~~Icing CAPE uses SB only, not effective CAPE~~ | ~~Medium~~ | ✅ FIXED |
| **2** | Altitude advisories use pre-resolution data | Medium | Medium |
| **5** | ~~Cloud top uncertainty uses SB-CAPE only~~ | ~~Low~~ | ✅ FIXED |
| **3** | ICON-EU convective transitions skipped in regimes | Low | Low |
| **4** | ~~No icing/convective method_effective tracking~~ | ~~Low~~ | ✅ FIXED (#408) |
| **D** | IENG computed but unreachable — no `_resolve_analyses` branch | Low | Low |
| **B** | ~~Move NWP enrichment earlier (fixes #6, enables #1 and #5)~~ | ~~**High**~~ | ✅ FIXED |
| **A** | ~~Unify CAPE selection~~ (done via `_effective_cape()`) | ~~Medium~~ | ✅ FIXED |
| **C** | ~~Deduplicate `_is_near_cloud` wrappers~~ | ~~Low~~ | ✅ DONE |
