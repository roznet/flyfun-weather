# Convective Assessment Analysis

> Per-model convective data pipeline, dual-method assessment, source inconsistencies, and recommendations.

_Code references verified against the repo on 2026-08-15._

> 📐 Convective design rationale (realizable-CAPE/regime tiers, DD-stays-pure, NWP-cover vs CAPE risk) is decided in [meteorology-decisions.md](./meteorology-decisions.md) §4–§5; the NWP-native grade + DD-trigger amber cap in §18, the ICON-D2 explicit-convection firing table in §19, the single-grading-formula extraction in §22, and the absent-NWP-track cap + embedded population floor in §26 — read before changing thresholds or the DD/NWP boundary.

**Key code paths** (bare filenames below refer to these):
- `analysis/sounding/convective.py` — `assess_convective_thermo`, `assess_convective_nwp`, `assess_convective_explicit` (#462), `classify_regime`, `effective_cape` (alias `_effective_cape`), `convection_realized`, `convective_cross_check` / `ConvectiveCrossCheck` / `_explicit_cross_check`, `classify_convective_character`
- `analysis/sounding/thermodynamics.py` — `compute_indices_core` (all three CAPE variants), `compute_derived_levels_core` (omega 700)
- `analysis/sounding/__init__.py` — orchestrates the lite/full passes; picks the explicit-vs-parameterized NWP track; sets `cape_raw_vs_calc_divergent`
- `analysis/advisories/convective_grading.py` — **`grade_convective_model()`, the single convective-colour formula** (§22). `ConvectiveEvaluator` and `IFRFeasibilityEvaluator` both come through it; it also owns `CONVECTIVE_PARAM_DEFAULTS` and `resolve_convective_params`
- `analysis/advisories/convective.py` — `ConvectiveEvaluator` (locale wording + cross-model aggregate only)
- `analysis/advisories/convective_character.py` — the orthogonal avoidability axis
- `fetch/variables.py` (`NWP_CAPE_TYPE`, line 84), `fetch/grib/fill.py`, `analysis/spatial_interpolation.py`, `analysis/comparison.py`, `tasks/advise.py`

## Overview

Convective assessment flows through four stages: **fetch** (Open-Meteo API + GRIB2 enrichment), **analysis** (a DD/thermo track and a model-native NWP track), **resolution** (user-selectable active method, default `"nwp"`), and **output** (visualization towers + advisory evaluators). Two parallel assessment methods produce independent `ConvectiveAssessment` objects that the user can switch between; the NWP slot is filled by *either* the parameterized-scheme assessor or — on a convection-permitting source (ICON-D2) — the explicit-convection assessor.

---

## Per-Model Convective Data Sources

### Open-Meteo API (variable per model)

| Variable | GFS | Best Match | ECMWF | ICON-EU | UKMO | MétéoFr | GEM |
|----------|-----|------------|-------|---------|------|---------|-----|
| `cape` (J/kg) | ✓ SB | ✓ SB | ✓ MU | ✓ ML | ✓ | ✗ | ✗ |
| `convective_inhibition` (J/kg) | ✓ | ✓ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `lifted_index` | ✓ | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |

**Key issue:** Open-Meteo's single `cape` field is a **different CAPE type per model** (SB / MU / ML as marked above; UKMO's type is unknown). Tracked in the `NWP_CAPE_TYPE` dict (`variables.py:84`, keyed only for gfs/ecmwf/icon), stored as `nwp_cape_type` on `ThermodynamicIndices`. This is the root of bug #5.

The GFS `✓ SB` above is the *Open-Meteo* column and still reads SB — but since #566 GFS also carries a GRIB-native mixed-layer pair (next table), so the Open-Meteo value is no longer the only CAPE available for that model.

### GRIB2 Enrichment (GFS, ICON-EU, and ECMWF)

| Field | GFS | ICON-EU | ECMWF | Others |
|-------|-----|---------|-------|--------|
| **Convective cloud cover %** | ✓ (TCDC on convectiveCloudLayer) | ✗ | ✗ | ✗ |
| **Convective cloud base** | ✓ (PRES → ft via std atm) | ✓ (hbas_con, m → ft) | ✗ | ✗ |
| **Convective cloud top** | ✓ (PRES → ft via std atm) | ✓ (htop_con, m → ft) | ✓ (hcct, m → ft) | ✗ |
| **Convective precip rate** | ✓ (CPRAT, kg/m²/s, instantaneous) | ✓ (`crr`, kg/m², de-accum) | ✓ (cp, m we ×1000, de-accum) | ✗ |
| **ML CAPE / ML CIN** | ✓ (CAPE/CIN `180-0 mb above ground`) | ✓ (cape_ml / cin_ml) | ✓ (mlcape100 / mlcin100) | ✗ |

Stored in `NWPCloudDiagnostics.convective_{cover_pct,base_ft,top_ft}`, `convective_precip_mm_h` and `ml_{cape,cin}_jkg`.

**Three traps in that precip row, each of which has bitten once:**

- **DWD's RAIN_CON decodes under cfgrib shortName `crr`, not `rain_con`** (paramId 228218). Despite the "rate" in the name it is *accumulated since init*, in kg/m² ≡ mm, so it is de-accumulated and **not** ×1000 — unlike ECMWF `cp`, which is metres water equivalent. A plausible key matching nothing fails silently, which is exactly what `rain_con` did.
- **GFS `CPRAT` is an instantaneous rate in kg/m²/s** — alone among the three it needs no de-accumulation, only `_kg_m2_s_to_mm_h`. It has an averaged twin at the same level (`CPRAT:surface:0-6 hour ave fcst`); `parse_cloud_diag_idx`'s `_PREFER_AVERAGED_PAIRS` resolves the pair.
- **GFS/HRRR CIN packs negative** (verified live on `gfs.20260822/06z f006`: −1027.9…+0.1, 33.5% negative and 66.5% barely positive). Both route through the shared `_ncep_cin_jkg`, which floors packing noise to 0 and returns `None` for a clearly positive value rather than trusting a flipped sign — CIN gates `eff_cin < -200`, so a silent flip would turn a strong cap into "no inhibition". ICON and ECMWF use the opposite (non-negative-magnitude) convention and go through `_normalize_model_cin`.

GFS's mixed-layer pair (#566) closed the last gap where a model with GRIB enrichment still fell back to Open-Meteo's surface-based CAPE. `180-0 mb above ground` is GFS's mixed-layer parcel; ECMWF's `mlcape100` is the lowest 100 hPa — related but not identical parcels, which is why `nwp_cape_type` records provenance rather than assuming.

**Two slot-substituting variants change what the table above means** (see [analysis-metrics.md](./analysis-metrics.md)):
- **ICON-D2** (#456/#462) serves the `icon` slot when the whole route fits its domain. D2 runs **no** deep-convection parameterization, so every parameterized field above is structurally `None`; instead the hour carries `HourlyForecast.explicit_convective_diagnostics: NWPExplicitConvectiveDiagnostics` (`dbz_ctmax`, `echotop`, `lpi_max`, `w_ctmax`, `uh_max`) — corridor extrema over a ~10 NM route buffer, never centreline bilinear. Its **presence is the mode signal** for the explicit track.
- **HRRR** (#457) serves the `gfs` slot on CONUS routes with `convective_scheme_absent=True` — no convective-realization channel at all, which routes the NWP track to the CAPE fallback rather than reading quiet band covers as "scheme present, nothing happening".

### MetPy-Derived Thermodynamic Indices (all models with pressure levels)

Computed from pressure-level profiles via MetPy (`thermodynamics.py`):
- **CAPE:** Surface-based, most-unstable, and mixed-layer — all three always attempted
- **CIN:** Surface-based
- **LCL / LFC / EL:** Altitudes and pressures
- **Lifted index, Showalter index, K-index, Total Totals**
- **Bulk wind shear:** 0–6km and 0–1km
- **Precipitable water**

These are derived from 13–28 pressure levels (model-dependent resolution). Available for all models that provide pressure-level temperature + dewpoint data.

See **Per-Model Convective Pipeline Summary** below for the consolidated per-model view.

---

## Convective Data Pipeline

### Stage 1: Fetch & Storage

Open-Meteo surface fields land on `HourlyForecast` (`cape_jkg`, `convective_inhibition_jkg`, `lifted_index_raw`). GRIB decoders (`decode_cloud_diag_per_point` for GFS, `decode_icon_eu_cloud_diag_per_point` for ICON, the ECMWF a1 path, `decode_icon_d2_explicit_conv_per_point` for D2) populate `NWPCloudDiagnostics` and — for D2 only — the separate `explicit_convective_diagnostics` object. See the source tables above for which fields each model actually delivers.

### Stage 2: Temporal Fill

`fill.py:_fill_cloud_diagnostics()` — fills the `NWPCloudDiagnostics` object from native GRIB hours to Open-Meteo interpolated hours. **Not a uniform forward-fill since #485:** the field list is split in `models/analysis.py` into `NWP_CLOUD_DIAG_INSTANT_SCALARS` (forward-filled/persisted) vs window-max/rate scalars, plus `NWP_CLOUD_DIAG_META_FIELDS` (`convective_scheme_absent`, `band_definition`) which are capability markers carried through unchanged. **`convective_precip_mm_h` is a de-accumulated rate describing `(N−w, N]`,** so a gap hour strictly between anchors takes the *next* anchor's value (covering-interval hold) — forward-filling it presented the previous window's rate inside a firing window and read "dry".

**Registered SKIP:** `explicit_convective_diagnostics` is deliberately **not** interpolated on either axis (#462) — its values are already corridor maxima computed at decode, and linear interpolation of logarithmic dBZ is invalid. A failed corridor decode stays an honest per-hour "unavailable".

### Stage 3: Spatial Interpolation

`spatial_interpolation.py:_lerp_diagnostics()` — linearly interpolates **every** scalar in `NWP_CLOUD_DIAG_SCALARS` (convective cover/base/top and precip rate among them) between neighbouring route points; meta fields are carried, not lerped. Deriving the field list from the shared tuple rather than a hand-written one is deliberate (#485): the two axes' hand-written lists drifted and silently dropped `freezing_level_ft`. The spatial axis makes no averaged-vs-instantaneous distinction — neighbouring route points share a valid time.

### Stage 4: Analysis (Three Assessors, Two Tracks)

The **DD/thermo track** always runs. The **NWP track** is filled by exactly one of two assessors: `assess_convective_explicit` when the hour carries an explicit-convection payload, else `assess_convective_nwp`.

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
  - **Firing gate (Phase 2):** a MODERATE+ tower is only kept there when the scheme *realized* convection (`convective_precip_mm_h > 0.1` OR `convective_cover_pct > 15`); a deep-but-dry tower is held down one level. Missing-data-safe — held down only on positive dry evidence. All three GRIB models can now be evaluated here: ICON gained a `convective_precip_mm_h` signal in #421 (DWD `RAIN_CON`, de-accumulated), so a deep-but-dry ICON tower is held down like GFS/ECMWF rather than kept at full native tier. Its only residual blind spot is a cold-air-mass **convective snow** shower (`RAIN_CON ≈ 0` but real `SNOW_CON`), which is safe by construction — the gate holds down only on positive dry evidence, so such a tower simply keeps its current tier (`SNOW_CON` deferred, see the code note in `icon_eu_fetch.py`).
  - **Native corroboration (Phase 2):** a realized MODERATE+ cell with strong native `k_index`/`total_totals`/conv-precip bumps up one level (capped HIGH). Thresholds (`_CORROB_K_INDEX=35`, `_CORROB_TOTAL_TOTALS=50`) are North-American severe-convection defaults; European environments (lower lapse rates, more modest moisture) often need TT ≥55, so this may fire readily over Europe — flagged for eval-digest calibration.
  - **Strong-CIN suppression** kept (prefers the model's own `ml_cin_jkg`, else DD `cin_surface_jkg`) — but *not* applied on the `"nwp_precip"` path (see Phase 3 above).

**Method strings** (preserved; consumed by `dd_nwp_agreement` history and front co-location's `method != "thermo"`), set by which geometry the model exposes:

- **`"nwp"`** (GFS — has `convective_cover_pct`): cover present; `base_ft`/`top_ft` from GRIB. Quiet native points (no top/cover geometry) also use this method with `risk=NONE`.
- **`"nwp_hybrid"`** (ICON-EU — has base+top, no cover): tower bounds from GRIB2 `convective_base_ft`/`convective_top_ft`.
- **`"nwp_lcl_top"`** (ECMWF — has `hcct` top only): tower base = `indices.lcl_altitude_ft` (LCL proxy), top = `convective_top_ft`; the `top > LCL` guard rejects a sub-LCL hcct artefact (→ quiet NONE).
- **`"nwp_precip"`** (Phase 3 — any native model firing `cp` with no tower top and no cover, in practice ECMWF over marine / elevated convection): risk from the precip-rate ladder, `base_ft`/`top_ft` both None. See the precip-rate fallback above.
- **`"nwp_explicit"`** (ICON-D2, #462): not produced by this function at all — see Method 3 below.
- **`"nwp_cape_fallback"`**: when the diagnostics carry *no* native cloud content at all (defensive — production builders return `None`, not an empty diag) **or** the diag declares `convective_scheme_absent=True`. The latter is a *live production path* since #457: HRRR is convection-allowing with no parameterized scheme output ingested, and reading its generic band covers as "scheme present but quiet" graded a CAPE-2000 column NONE. On this path CAPE/CIN prefer the model's **own** ML pair (`ml_cape_jkg`/`ml_cin_jkg`, HRRR's 90-0 mb MLCAPE/MLCIN), each falling back independently to the sounding value — grading HRRR on MetPy CAPE made the "NWP" track a relabelled copy of the DD track. Marked distinctly so the cross-check skips the circular comparison. (The `_nwp_cape_fallback_risk` docstring's "production never reaches this path" predates #457.)

- **Severity modifiers:** same as thermo (computed from the same indices, descriptive text only — separate from the native risk tier).

| Model | NWP Convective Result | Notes |
|-------|----------------------|-------|
| **GFS** | `method="nwp"` — risk from tower top + cover modifier; cover/base/top from GRIB | Only model with convective cover % |
| **ICON-EU** | `method="nwp_hybrid"` — risk from tower top; GRIB base/top; `cape_ml`/`cin_ml` + `crr`→conv-precip native | Firing gate + native corroboration now evaluate ICON (#421). ICON stays `nwp_hybrid` in practice: it emits `htop_con` whenever its scheme fires, so a firing point always has tower geometry and never falls through to the geometry-absent `nwp_precip` ladder (that branch is generic on field presence, not reachable for ICON when `crr` > 0). |
| **ECMWF** | `method="nwp_lcl_top"` when `hcct` present; `method="nwp_precip"` (Phase 3) when `hcct` sentinel but `cp` fires — risk from the precip-rate ladder; `cp`/`kx`/`totalx`/`mlcape`/`mlcin` native | `hcct` is sparsely delivered (sentinel over marine/elevated convection), so `cp` is often the only firing signal |
| **HRRR** (`gfs` slot, CONUS) | `method="nwp_cape_fallback"` — `convective_scheme_absent=True`, graded on HRRR's own ML-CAPE/CIN | No realization channel ingested; NOT a quiet scheme (#457) |
| **ICON-D2** (`icon` slot, in-domain) | `method="nwp_explicit"` — see Method 3 | Parameterized fields structurally None |
| **Others** (UKMO / Météo-France / GEM) | None | No GRIB diagnostics at all |

#### Method 3: Explicit Convection (ICON-D2) — replaces the NWP slot, not a user choice

`convective.py:assess_convective_explicit(indices, nwp_diagnostics, explicit)` → `ConvectiveAssessment | None`, `method="nwp_explicit"` (#462; decision table in meteorology-decisions §19).

Selected in `sounding/__init__.py` purely on `hourly.explicit_convective_diagnostics is not None`. The parameterized path must **not** run for D2 — it would read D2's structurally-absent scheme fields as a quiet scheme exactly when the model sees a storm.

Grading is a reflectivity × corroborator table: `reflectivity_hour_max_dbz` against `_EXPLICIT_DBZ_{FIRE=35, CONVECTIVE=45, SEVERE=50}`, with a corroborator count |C| from LPI (`_EXPLICIT_LPI_CORROB_JKG=1`, ≥5 counts double), updraft (`_EXPLICIT_UPDRAFT_CORROB_MS=10`) and updraft helicity.

Structural safety properties — do not "clean these up":
- **`top_ft` is ALWAYS `None`.** The 18 dBZ echo top sits *below* the physical storm top (anvil ice reflects weakly), so letting the overfly-clearance filter consume it would err toward "safe to overfly". It travels only as the character/detail field `echo_top_18dbz_ft`, and the advisory's flagged cells (Stage 7) fall back to the thermo track's bounds as a `tower_estimated` cutout or, failing that, to a depth-unknown `tower_unresolved` marker — never a full-height box (#592).
- **`detection_complete=False` → `None`**, never a quiet `NONE` and never a CAPE fallback dressed as D2's verdict. The caller records `SoundingAnalysis.convective_explicit_unavailable=True` and grading falls back to thermo, truthfully badged. Unknown is not quiet.
- **No CIN suppression.** A simulated echo *is* realized convection; penalising it on surface/ML CIN is circular (same reasoning as `nwp_precip`).
- **Never says "hail".** D2's mixed-phase signal is graupel and does not discriminate hail, so `_severity_modifiers`' "hail risk" string is rewritten to graupel / mixed-phase framing here.
- **Bright-band gate** (§19 amendment #466/#467): an 18 dBZ echo top less than `_EXPLICIT_BRIGHT_BAND_DELTA_FT` (10 000 ft) above the freezing level **with |C| = 0** is a melting-layer bright band in stratiform rain → suppressed to `NONE`. Gated on |C| = 0 so positive electrification/updraft evidence can never be overridden, and an unevaluable gate (missing echo top or freezing level) never downgrades.

### Stage 5: Resolution

`tasks/advise.py:_resolve_analyses()` resolves the `convective_method` user preference. **The default is `"nwp"`** (`advisories/engine_methods.py:ENGINE_METHOD_DEFAULTS`), not thermo:
- `"thermo"`: no swap needed (`convective = convective_thermo`)
- `"nwp"` (default): swaps `convective_nwp` into the `convective` slot, falling back to `convective_thermo` when NWP is None (a model with no scheme, or a detection-incomplete explicit hour). Records `convective_method_effective` = `"nwp"` / `"thermo"` (#408) so the fallback is never silent, **and `convective_nwp_fallback = (nwp is None)`** (#568, §26a) — the dedicated marker the §18 cap keys on. The two are not interchangeable: `convective_method_effective == "thermo"` is ambiguous by construction (it also means "the user explicitly asked for thermo"), and `convective_nwp is None` is False under an explicit thermo request because the NWP assessment is always computed and stored, just not swapped in. `_resolve_analyses` is the only layer that knows the *requested* method, so the marker is set there — and on **both** branches, so a stale True cannot survive a `model_copy(update=...)`.

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

**NWP towers** use model boundaries directly — no estimation needed. On the explicit (`nwp_explicit`) track there are no bounds at all: `top_ft` is None by construction. **The two clients diverge here, and always have:** web draws its own depth-unresolved ghost column (`nwp-convective-bg.ts:drawUnresolvedColumn` — a risk-tinted wash with dashed sides and a `?` marker), iOS skips the point entirely (`NwpConvectiveBgLayer.swift`: "drawing full-height columns would be misleading"). Both read `nwpConvectiveBase/TopFt` straight off the point; neither consults the thermo track.

**These two layers are not the advisory highlight scrim.** A third layer (`highlight-layer.ts` / `HighlightLayer.swift`) renders the `tower` / `tower_estimated` / `tower_unresolved` cutouts the *advisory* publishes, and it is the only one of the three that borrows bounds across tracks (#592, Stage 7). Neither convective background layer consults the other's track: each draws its own per-point sounding fields, so a `tower_estimated` cutout can sit over an NWP-layer ghost column on the same point — the scrim is saying "the advisory drew this box from the thermodynamics", not "the NWP layer resolved a tower here".

Both layers share the same color palette (via exports from `thermo-convective-bg.ts`): bgWash, towerFill, hatching, anvil strip, CB labels.

### Stage 7: Advisory Evaluation

**The grading formula does not live in the evaluator.** `advisories/convective_grading.py:grade_convective_model(ctx, model, params)` is the single source of the convective colour (§22); `ConvectiveEvaluator` adds only locale wording and the cross-model aggregate, and `IFRFeasibilityEvaluator` takes `status` from the same call. Before the extraction, `ifr_feasibility` carried its own older derivation (own `convective_min_risk` floor, own 10% red threshold, no tops-below-cruise filter, no #442 awareness) and the two advisories disagreed on the same sounding in both directions. **Any new consumer must come through `grade_convective_model`,** and read its tuning through `resolve_convective_params` / `CONVECTIVE_PARAM_DEFAULTS` (`min_risk` 2 = LOW, `affected_pct_amber` 20, `affected_pct_red` 50, `top_clearance_ft` 2000) so the settings page and the grading cannot drift apart.

It reads `sounding.convective` (the resolved active slot):
- HIGH/EXTREME at any flagged point → RED; else coverage percentages give AMBER vs GREEN, with a MODERATE+-reaching-cruise amber floor
- **NWP-native grade (#442, meteorology-decisions §18) — this REPLACED the old DD floor.** The colour comes from the model's own NWP tier, **not** `max(NWP, DD)`. The old `graded_risk = max(active, convective_thermo)` floor produced the loaded-gun false-alarm REDs and is gone.
- **DD-trigger AMBER cap:** when the NWP tier is below `min_risk` but the cross-check reads `dd_not_corroborated` (DD MODERATE+, scheme quiet), the point is raised to AMBER only — tier capped at MODERATE, `reason_code="dd_trigger"`, counted in `dd_trigger_count` and **excluded from the red-coverage count**. DD alone can never make a red. The colour is bound to the *same* condition as the cross-check note, so colour/note/reason cannot diverge.
- **Absent-track cap (#568, §26a):** the rule above keys on the cross-check, which needs an NWP assessment to compare against — so it never fired for a model with **no** native track, where `sounding.convective` *is* the thermo assessment and already sits at/above `min_risk`. Such a point (`convective_nwp_fallback=True`) is now capped at MODERATE, marked `reason_code="dd_fallback"`, and counted into `dd_trigger_count` so the same red-coverage exclusion applies. Kept separable from `dd_trigger` because "the scheme was quiet" and "there was no scheme" are different facts about the forecast. The cap never touches a model whose own scheme is firing, and never a user who explicitly selected `convective_method="thermo"`.
- The below-cruise altitude filter uses the **deeper** of the active and thermo tops when the DD trigger raised the grade — covering both a missing NWP top and a shallow NWP top below the DD EL. Otherwise `top_ft=None` would bypass the filter (#283 review I1). **This is `filter_top_ft`, and since #592 it is a different variable from the drawn geometry** — see below.
- Evidence geometry (#592): resolved **independently of the filter**. Each missing bound is filled from the thermo track whenever the thermo track has it, ungated — the model saying "convection here" without publishing base/top is the common case, and complete thermodynamic bounds usually sit unread in the same sounding. Fill the gap, never override: a bound the model published is the model's own claim and stands, so only the missing side is borrowed. A borrowed bound makes the cutout `tower_estimated` (dashed outline on both clients) rather than `tower`; when neither track has both bounds the cell stays `tower_unresolved`, which the clients draw as a narrow depth-unknown marker and **never** as a terrain-to-top rectangle. `method_id` still names the *graded* track (`convective_method_effective`), deliberately not compounded when the DD trigger raised the grade; read it together with `kind`, which is what says the drawn bounds came from somewhere else.

**Why geometry and filter are two variables.** They used to be one (`check_top_ft`), so the only way to draw a real box was to widen the value the tops-below-cruise test reads — a meteorological policy change ("use the deeper of the model's and the thermodynamic top when testing whether convection reaches cruise") riding along with a rendering fix. Measured on the eval corpus (179 packs, 6,315 flagged convective points): 2,413 points (38 %) drew as full-column ghosts, 1,666 of them (69 %) with complete thermo bounds available; opening the *shared* variable would also have moved 10 of 691 (pack, model) rows' status and cut the tops-below-cruise filter from 387 points to 270. Splitting the variable takes the 69 % geometry win with a provable zero grade movement (`tests/analysis/advisories/test_convective_geometry.py::TestGradeInvariance`). Opening the filter's gate remains open as its own question — it is defensible and probably an improvement, but it needs a dated entry in `meteorology-decisions.md` with those 10 movers as worked examples, not a rendering PR.

**Orthogonal axis — convective *character* (avoidability).** Separate from this severity evaluator, `advisories/convective_character.py` grades whether route convection is circumnavigable VFR (ISOLATED/SCATTERED→AMBER, WIDESPREAD/ORGANIZED/EMBEDDED→RED), never changing the severity colour. Three #568 changes live there. **The EMBEDDED gate was redefined (§26b):** `_point_embedded` now requires a BKN/OVC layer that *contains* cruise (`base − embed_cruise_buffer_ft ≤ cruise ≤ top + embed_cruise_buffer_ft`) with corroborating bulk cover **in the band containing cruise** — the old test read only the deck's *base*, so a 697–1,942 ft stratocumulus sheet counted as hiding cells from an aircraft at FL180 — and the route-level test is the longest *contiguous* run of realized embedded points ≥ `embed_min_nm` (50 nm) instead of a floorless `embedded / len(conv) ≥ 50 %` fraction that let one 9 nm point red a 582 nm route. **An altitude mitigation was added (§26d):** EMBEDDED only, climb-or-descend to the nearest level whose re-derived band is no longer embedded, promoted to the aggregate only when it clears every EMBEDDED model. And the per-model result now carries `primary_method_id` (§26c) — `build_character_points` returns the effective convective track alongside the points, because `driving_method_id` reads `AdvisoryHighlights`, which this evaluator does not produce. A fallback anywhere on the route wins the badge. Severity is altitude-aware *above* (the overfly filter here). #298 shipped the mirror *below* (`_below_base_geometry`): an **annotate-only** below-base clearance note reporting the model-native cell base vs cruise (`base_clearance_ft`, default 2000 ft, mirroring `top_clearance_ft`). Precedence is safety-first and strictly ordered, so a softer phrase can never mask a worse geometry on the same route: `within_layer` (cruise at/above a cell base) → `deck` (BKN/OVC between cruise and the cells, i.e. not VMC) → `unresolved` (any realized cell whose base is `None` — `nwp_precip` ghost, `nwp_lcl_top` without LCL, CAPE fallback) → `clear` / `marginal` with the tightest margin + a descend hint. Full rationale in `designs/meteorology-decisions.md` §15. **That note stays route-wide, but the altitude-swept *escape* built on the same geometry is now asked per convective cluster (§30/#593):** `convective_clusters` splits the realized cells into contiguous along-track runs and `below_base_escapes` runs the geometry over each, because a route-wide test made "below the cells" mean "below every resolved base on the whole flight" — which a flight crossing two systems never satisfies, so the helper never fired. The terrain floor is that cluster's own (the Alps at departure must not bound a descent over flat ground 300 nm later) and the tip names the miles it applies to; the band half stays route-wide and still gates every cluster.

### Stage 8: DD-vs-Model Cross-Check

`convective.py:convective_cross_check(thermo, nwp)` → `ConvectiveCrossCheck | None`. `grade_convective_model` runs this per route point using `convective_thermo` explicitly (never the resolved slot — it must stay a DD-vs-NWP comparison even if the user picked NWP; falling back to `sounding.convective` would pass the NWP assessment as both sides). It compares the DD (parcel-CAPE) thermo risk against the model's native **firing** signal (#283): convective precip > the gate, cover ≥ 25%, or a tower ≥ FL200. Bare convective-geometry presence is **not** enough — a shallow Cu top would otherwise spuriously read "active". (The model's `risk_level` is not compared directly here; the risk-level comparison that used to live in `dd_nwp_agreement` was removed for convective — this inline cross-check is the single source of truth, see `designs/advisories.md`.)

Fires only on two material divergences (else `None`):
- `dd_not_corroborated` — thermo MODERATE+ but the scheme is quiet (no convective precip, low/no cover, no deep tower)
- `model_active_dd_quiet` — thermo NONE/MARGINAL but the scheme fired (precip / cover ≥ 25% / tower ≥ FL200)

LOW thermo is intentionally in neither band; the gap between the quiet and active bands (e.g. cover 10–25%, tower FL120–200) is also neither. The result never changes the grade *directly* — but `dd_not_corroborated` is the exact condition the DD-trigger AMBER cap keys on (Stage 7), so it does carry a colour. Thresholds are tunable module constants (`_FIRING_PRECIP_MM_H`, `_XCHECK_MODEL_QUIET_COVER_PCT`, `_XCHECK_MODEL_ACTIVE_COVER_PCT`, `_XCHECK_DEEP_TOP_FL`, `_XCHECK_QUIET_TOP_FL`).

**Explicit dispatch:** for a `method="nwp_explicit"` assessment the function delegates to `_explicit_cross_check`, which keys active/quiet on the explicit assessment's *own tier* — the parameterized channels are structurally None there, the explicit tier already folds in reflectivity + corroborators, and it only exists when detection is complete, so its `NONE` is a genuine simulated-radar quiet rather than a missing signal.

**Absent NWP track (#568).** `convective_cross_check` returns `None` immediately when `nwp is None` — correct at that layer, there is nothing to compare — so the absence note is emitted by `_peak_cross_check` instead, keyed on the peak point's `convective_nwp_fallback`: *"No NWP Convective forecast from this model here — graded on Thermo Convective (thermodynamics) alone, which on its own can never grade red"*. Without it the card was silent about the one fact that explained the outlier, and the grade read as meteorological disagreement rather than a missing track.

**Surfacing (#442 follow-up).** The advisory's `cross_check` string is *not* a route-wide dominant-direction scan: `convective_grading._peak_cross_check` anchors it on the **grade-driving (peak) point** and emits a note only when the two tiers there differ by **≥2 levels** (same-or-one-off is normal method spread). Anchoring on the driver stops a minority quiet stretch reading as contradicting a red driven elsewhere. Copy is named after the cross-section toggles ("NWP Convective" / "Thermo Convective") so a pilot can pull up exactly those two overlays.

Note: this is distinct from the unused `cape_raw_vs_calc_divergent` flag (Bugs #3/#5) — that compares raw NWP CAPE vs MetPy CAPE magnitudes; this cross-check compares the thermo *tier* against the model's *convective scheme*.

---

## Bugs Found

Fixed items are kept as one-liners so a future reader doesn't re-open a closed question.

- **1. ICON-EU NWP assessment always returned None** ✅ FIXED — `assess_convective_nwp` used to bail when `convective_cover_pct is None`, wasting ICON's `hbas_con`/`htop_con`. Now the `nwp_hybrid` path.
- **2. `_effective_cape()` ignored ML-CAPE** ✅ FIXED — now `max(SB, MU, ML)`.
- **4. Thermo CAPE ignored raw NWP CAPE** ✅ FIXED — deliberately kept as fallback-only, not pooled into the max. See recommendation #1 for why.
- **6. NWP convective viz vs assessment boundary mismatch** ✅ FIXED by #1 — `nwp-convective-bg.ts` now renders ICON-EU towers.
- **7. Silent NWP→thermo fallback in the resolved slot** ✅ FIXED (#408) — `convective_method_effective` records the track that actually graded, and `grade_convective_model` sources the evidence chip's `method_id` from it. Residual gap: no `method_id` *region* badge like the icing/cloud axes.
- **8. A fallback-graded model bypassed the §18 amber cap** ✅ FIXED (#568) — see Stage 5/Stage 7 above and meteorology-decisions §26a. `convective_nwp_fallback` is the discriminator; the cap and the absence note key on it.
- **9. The embedded gate never checked that the deck reached cruise, and its route test was a floorless fraction** ✅ FIXED (#568) — `_point_embedded` now requires containment of cruise (with a resolution buffer) plus corroborating cover in the band cruise sits in, and the route fires on a contiguous extent ≥ `embed_min_nm` rather than `embedded / len(conv) ≥ 50 %`. meteorology-decisions §26b.
- **11. Embedded convection offered no mitigation** ✅ FIXED (#568) — a climb/descend altitude tip, EMBEDDED only, promoted to the aggregate only when it clears every EMBEDDED model. meteorology-decisions §26d.
- **10. `convective_character` carried no method badge** ✅ FIXED (#568) — `primary_method_id` now set from the effective track, meteorology-decisions §26c. **Still open on iOS:** the client reads neither `primary_method_id` nor the region-level `method_id` for *any* advisory, so the badge is server-side and web-side only until the iOS chip is built.

### 3. Raw NWP CAPE vs MetPy CAPE divergence not used — STILL OPEN

`cape_raw_vs_calc_divergent` is computed in `sounding/__init__.py` (raw vs MetPy SB-CAPE, |Δ| > 200 J/kg or > 100% relative) and stored on `ThermodynamicIndices`. It is still **consumed by nobody** — not the thermo assessment, not any evaluator; it only reaches `web/ts/store/types.ts` as a serialized field.

The divergence is real: models compute CAPE internally over 50–140 vertical levels, MetPy re-derives it from 13–28. On coarse pressure data MetPy can show convective instability where the model's own CAPE is near zero, or vice versa. Options if picked up: prefer raw when raw < computed, surface a "CAPE disagreement" note, or modulate risk confidence. Note the flag's premise weakened since #283 — the NWP track no longer grades on CAPE at all, so the divergence now bears mainly on the DD track's own credibility.

### 5. CAPE type mismatch in cross-model comparison — STILL OPEN

`comparison.py` compares `nwp_cape_jkg` across models (threshold `(200.0, 500.0)`) without regard to `nwp_cape_type`: GFS reports SB, ECMWF MU, ICON ML. GFS SB 200 J/kg vs ECMWF MU 600 J/kg reads as "poor agreement" when the gap is partly methodological. The MetPy comparison is fine (same computation everywhere). Fix: type-aware grouping, or drop raw NWP CAPE from the comparison entirely (recommendation #4).

---

## Inconsistencies

### 1. Asymmetric information between thermo and NWP methods — by design (#283)

The two methods are now *deliberately* independent: thermo risk is parcel-CAPE-driven, NWP risk is model-native (tower top + cover/precip, NOT CAPE). The MetPy severity modifiers are still copied into the NWP output as descriptive text (separate from the native risk tier). These can disagree — high native cover/tower but low MetPy CAPE, or vice versa — and that disagreement is the **product goal**, surfaced via the inline cross-check (Stage 8). The historical "NWP risk is CAPE-driven, cover informational" framing no longer holds (#283).

### 2. CIN suppression in the NWP method (#283)

Both methods apply strong-CIN suppression (CIN < −200 J/kg → one level down) on their *geometry* paths. The NWP method prefers the model's own `ml_cin_jkg` when present (ECMWF `mlcin100`, ICON `cin_ml`), falling back to the DD `cin_surface_jkg`. The realized-convection **firing gate** is the native-side analogue of the cap (a deep-but-dry tower is held down one level), so a capped tower the scheme reports but won't realize is no longer left at full risk.

**Two paths deliberately skip it:** `nwp_precip` and `nwp_explicit`. Both are realized-by-construction (the scheme is precipitating / the model has simulated an echo), so suppressing on surface/ML CIN would be circular. Don't "restore consistency" here.

### 3. MARGINAL risk handling differs between methods

- **Thermo:** MARGINAL = any CAPE > 0 with defined LFC and EL (shallow convection possible)
- **NWP (#283):** MARGINAL = a convective tower present but shallow (< FL120), or scattered cover (15–35%) when no tower top is reported

The thermo MARGINAL is very sensitive — any small positive CAPE with LFC/EL triggers it. The NWP MARGINAL requires the model's convective scheme to actually produce a (shallow) tower or scattered cover. In practice, thermo produces many more MARGINAL points than NWP.

Both the thermo visualization and the NWP visualization skip MARGINAL towers (render only LOW+), so this is mainly an advisory-level difference.

### 4. Tower top estimation only applies to thermo, not needed for NWP

The thermo method has elaborate tower-top estimation logic (`estimateTowerTop()`) because MetPy EL can be unreliably shallow on coarse pressure levels. The NWP method uses model boundaries directly. This is correct behavior (model-native boundaries are authoritative), but means towers from the two methods can look very different for the same point.

### 5. Convective assessment vs altitude advisories use different cloud data

- `grade_convective_model` reads `sounding.convective.risk_level` — the resolved track's tier, filtered by cruise clearance
- Altitude advisories (`advisories.py`) use `nwp_cloud_diagnostics.convective_{base_ft,top_ft,cover_pct}` directly for regime transitions — this is always from GRIB regardless of the user's convective method preference
- Vertical regime labels don't reflect the convective method choice

### 6. `VizPoint.capeSurfaceJkg` always uses MetPy SB-CAPE

The route graph CAPE metric (`capeSurfaceJkg`) and the thermo tooltip show MetPy surface-based CAPE. For models where the NWP provides a different CAPE type (ECMWF MU, ICON ML), the displayed CAPE may underrepresent the model's own convective signal. The NWP raw CAPE is not exposed in the route graph.

---

## Per-Model Convective Pipeline Summary

One row per model: what feeds the DD/thermo track, what feeds the NWP track, and what the NWP track ends up being.

| Model | Open-Meteo | GRIB native | NWP track outcome |
|-------|-----------|-------------|-------------------|
| **GFS / Best Match** | `cape` (SB), CIN, LI | conv cover + base/top, `CAPE`/`CIN` 180-0 mb → ml pair, `CPRAT`→conv precip (#566) | `nwp` — tower-top tier + cover modifier + firing gate |
| **ICON-EU** | `cape` (ML) | `hbas_con`/`htop_con`, `crr`→conv precip, `cape_ml`/`cin_ml` | `nwp_hybrid` — tower-top tier, GRIB bounds, gate/corroboration from `crr` (#421) |
| **ICON-D2** (in-domain) | `cape` (ML) | explicit storm fields only (`dbz_ctmax`, `echotop`, `lpi_max`, `w_ctmax`, `uh_max`); parameterized fields structurally None | `nwp_explicit` (#462) — reflectivity × corroborators; `top_ft` always None |
| **ECMWF** | `cape` (MU); pressure levels replaced by IFS GRIB | `hcct`, `cp`, `kx`/`totalx`, `mlcape100`/`mlcin100` | `nwp_lcl_top` when `hcct` present, else `nwp_precip` when `cp` fires — `hcct` is sparsely delivered, so `cp` is often the only firing signal |
| **HRRR** (`gfs` slot, CONUS) | — (GRIB sounding replacement) | band covers only; `convective_scheme_absent=True`, own ML-CAPE/CIN | `nwp_cape_fallback` (#457) |
| **UKMO** | `cape` (type unknown), CIN | — | None — thermo grades, badged truthfully |
| **Météo-France / GEM** | nothing convective | — | None — MetPy-only, no model-native CAPE to validate against |

The DD/thermo track is identical everywhere: MetPy SB/MU/ML CAPE, CIN, LCL/LFC/EL, shear, K, TT from 13–28 pressure levels. Level count matters — ECMWF's 13 levels can miss thin unstable layers GFS's 28 resolve, which is why `cape_raw_vs_calc_divergent` exists (see bug #3).


---

## Recommendations

### 1. ~~Use NWP raw CAPE when available~~ ✅ IMPLEMENTED (MetPy-primary, NWP fallback)

`effective_cape()` takes `max(SB, MU, ML)` over the **MetPy** variants and uses `nwp_cape_jkg` **only as a last-resort fallback when no MetPy variant is available** — raw NWP CAPE is deliberately *not* folded into the max pool. Rationale (in the docstring): model-native CAPE diverges from sounding-derived values (different parcel selection, virtual-temperature corrections), so pooling it could inflate risk the sounding doesn't support. `test_effective_cape_nwp_raw_fallback_only` pins this.

### 2. ~~Enable ICON-EU NWP convective assessment~~ ✅ IMPLEMENTED

Shipped as the `nwp_hybrid` path — but note the eventual implementation is *not* what was proposed here. The proposal was CAPE-derived risk with GRIB bounds; #283 instead made the whole NWP track model-native (tower-top tier), so ICON's risk comes from `htop_con`, not CAPE.

### 3. ~~Add `convective_method_effective` tracking~~ ✅ IMPLEMENTED (#408)

`_resolve_analyses` records which track actually graded per model after fallback — the sibling of `cloud_method_effective`. Closes the honesty gap where a pilot who asked for NWP convective silently graded on thermo. `grade_convective_model` sources the evidence chip's `method_id` from it; the convective evaluator still does not badge a `method_id` *region* the way the icing/cloud axes do (see [advisories.md](./advisories.md) evidence contract).

### 4. Don't compare `nwp_cape_jkg` across models (Priority: Low — still open)

`comparison.py` still carries `"nwp_cape_jkg": (200.0, 500.0)` in its divergence thresholds. Remove it from the cross-model comparison or add `nwp_cape_type`-aware grouping: comparing GFS SB-CAPE against ECMWF MU-CAPE is not apples-to-apples (bug #5).

### 5. ~~Consider simplifying to a single convective method~~ — resolved the other way (Priority: closed)

The NWP track now covers GFS, ICON-EU, ICON-D2, ECMWF and HRRR, and #283/#442 made it the **default and the grade driver**, with thermo demoted to a DD-trigger amber cap. The "deprecate NWP" option is dead; the "merge into one method" option is explicitly rejected — the two tracks' independence is what makes the cross-check meaningful (meteorology-decisions §4–§5, §18). What remains for UKMO / Météo-France / GEM is an honest thermo fallback, badged by `convective_method_effective`.

## Interpolation Summary

| Axis | What | Method | Notes |
|------|------|--------|-------|
| **Temporal** | Convective geometry (base/top/cover) | Forward-fill from native GRIB hours | `NWP_CLOUD_DIAG_INSTANT_SCALARS` |
| **Temporal** | `convective_precip_mm_h` | **Covering-interval hold, NOT forward-fill** | De-accumulated rate over `(N−w, N]` — forward-filling reads "dry" through a firing window (#485) |
| **Temporal** | `convective_scheme_absent`, `band_definition` | Carried, never lerped | Capability markers; a bool through `_lerp` returns a float |
| **Temporal** | `explicit_convective_diagnostics` | **Registered SKIP** | Already corridor maxima; lerping log-scale dBZ is invalid (#462) |
| **Temporal** | Open-Meteo CAPE/CIN/LI | Hourly interpolation by Open-Meteo | Independent of GRIB timing |
| **Spatial** | All `NWP_CLOUD_DIAG_SCALARS` | Linear between route points | Field list derived from the shared tuple, not hand-written (#485) |
| **Spatial** | `explicit_convective_diagnostics` | **Registered SKIP** | Same reason as above |
| **Spatial** | Open-Meteo CAPE/CIN/LI | Not interpolated | Each route point has its own API response |

Pressure-level count per model (drives MetPy CAPE quality, all models get SB/MU/ML): GFS & Best Match 28, UKMO & GEM 20, ICON-EU & Météo-France 19, ECMWF 13 (but ECMWF's sounding is replaced by the IFS GRIB when enrichment ran).
