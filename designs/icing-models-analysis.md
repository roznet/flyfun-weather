# Icing Models Analysis

> Per-model input audit, source tracing, interpolation methods, and consistency analysis for all three icing estimation methods.

_Code references verified against the repo on 2026-08-15._

> 📐 The Ogimet icing-zone-width / convective-contribution decision (why Ogimet bands look "wide" vs GRAMET) is documented in [meteorology-decisions.md](./meteorology-decisions.md) §2 — read before re-investigating zone width.

## Overview

WeatherBrief computes icing potential using four independent methods plus SLD detection, all running on every sounding:

| Method | Module | Signal Source | Index Range |
|--------|--------|--------------|-------------|
| **Ogimet-DD** | `analysis/sounding/icing.py` | Dewpoint depression attenuation | 0–100 |
| **Ogimet-NWP** (default) | `analysis/sounding/icing.py` | NWP cloud fraction × glaciation | 0–100 |
| **SFIP** | `analysis/sounding/sfip.py` | Fuzzy-logic membership functions | 0–100 |
| **IENG** | `analysis/sounding/icing.py` | NWP cloud fraction (no glaciation) | 0–100 |
| **SLD** | `analysis/sounding/sld.py` | Warm-nose freezing rain (all models) | moderate/severe |

Shared utilities live in `analysis/sounding/icing_common.py`: cloud-layer gating, NWP cloud altitude lookup, icing type classification, zone grouping, and the supercooled-rain test.

**The single most important gating fact:** Ogimet-NWP and IENG return `[]` outright when the model has no *native* NWP cloud envelope. `clouds.build_nwp_cloud_layers()` returns `None` for models with only Open-Meteo bulk low/mid/high percentages (MétéoFr, UKMO, GEM) — bulk-%-narrowed-by-DD synthesis was deliberately removed, because a hybrid layer isn't a model-native layer and the cross-section can't anchor icing to it. Those three models therefore contribute Ogimet-DD and SFIP-proxy only.

---

## Per-Model Input Matrix

### Data Sources

| Input | Source | GFS | ECMWF | ICON | MétéoFr | UKMO | GEM |
|-------|--------|-----|-------|------|---------|------|-----|
| Temperature | Open-Meteo API | 28 lvls | 13 lvls | 19 lvls | 19 lvls | 20 lvls | 20 lvls |
| Dewpoint | Open-Meteo API | 28 | 13 | 19 | 19 | 20 | 20 |
| RH | Open-Meteo API | 28 | 13 | 19 | 19 | 20 | 20 |
| DD | Derived (T−Td) | all | all | all | all | all | all |
| Wet-bulb | MetPy derived | all | all | all | all | all | all |
| Omega (ω) | Open-Meteo API | yes | yes | **NO** | **NO** | yes | **NO** |
| CLWMR | GRIB2 enrichment | 50hPa lvls | 25 lvls (Europe) | EU only | **NO** | **NO** | **NO** |
| ICMR | GRIB2 enrichment | 50hPa lvls | 25 lvls (Europe) | EU only | **NO** | **NO** | **NO** |
| Per-level cloud fraction (cc/clc) | GRIB2 enrichment | **NO** | yes (Europe) | EU only | **NO** | **NO** | **NO** |
| NWP cloud low/mid/high | Open-Meteo API | yes | yes | yes | yes | yes | yes |
| NWP cloud diagnostics | GRIB2 enrichment | yes (full bulk + base/top) | bulk + cbh + hcct + deg0l (no per-band base/top) | EU only (bulk + ceiling + convective) | **NO** | **NO** | **NO** |
| CAPE | Open-Meteo API | yes (SB) | yes (MU) | yes (ML) | yes | yes | yes |
| Rain water (qr) | GRIB2 enrichment | **NO** | **NO** | ICON-D2 only | **NO** | **NO** | **NO** |

**GRIB slot ≠ Open-Meteo model.** Two slots carry a second GRIB variant that changes what the icing methods see:
- **`gfs` over CONUS** may be sourced from **HRRR** (`grib_sources["gfs"] == "hrrr:noaa"`). HRRR has no band base/top geometry, so its NWP layers come from per-level condensate (`CLMR` + `CIMIXR` → `source="nwp_condensate"`) rather than GRIB bands — see meteorology-decisions §23. HRRR fields are instantaneous, so none of the GFS averaged-window machinery (window-midpoint interp, RH/condensate gate) runs.
- **`icon` may be ICON-D2** rather than ICON-EU (`icon_d2:dwd` vs `icon_eu:dwd`). D2 additionally publishes `qr`/`qs`/`qg`, which is the only source of the supercooled-rain flag today.

### Interpolation Chain

| Step | What | Method | Notes |
|------|------|--------|-------|
| 1. Spatial CLW/ICMR | `spatial_interpolation.py` | Linear in distance-space between route-point neighbors | Max 100nm gap. Preserves 0.0. Sets `clw_interpolated=True` |
| 2. Vertical CLW/ICMR | `_interpolate_cloud_water()` in `__init__.py` | Linear in pressure-space between 50hPa GRIB levels | Fills 25hPa intermediate levels. Sets `clw_interpolated=True` |
| 3. Volumetric LWC | `_enrich_lwc()` in `__init__.py` | Ideal gas law: `LWC = CLWMR × P/(Rd×T) × 1000` | Requires temperature; skipped if T missing |
| 4. ICON model→pressure | GRIB enrichment | Log-pressure interpolation from ICON-EU model levels 35–74 | To the 28 `EXTENDED_PRESSURE_LEVELS` |
| 5. Vertical qr | `_interpolate_rain_water()` in `__init__.py` | Linear in pressure-space, same bracketing as step 2 | `supercooled_rain` is **re-derived**, never interpolated (see below). Dormant for ICON today — pass 1 always matches |

---

## Per-Method Analysis

### 1. Ogimet-DD (`assess_icing_zones_ogimet_dd`)

**Signal:** `effective_index = ogimet_index(T) × dd_attenuation_factor(DD)`

| Input | Role | Required | All models? |
|-------|------|----------|------------|
| `temperature_c` | Ogimet index formula + icing type | Yes | Yes |
| `dewpoint_c` | Vapor density for convective component | Yes | Yes |
| `dewpoint_depression_c` | DD attenuation factor (cloud signal) | Yes | Yes |
| `wet_bulb_c` | Icing type classification (fallback: T) | Optional | Yes |
| `cape_jkg` | Layered/convective split | Optional | Yes |

**Model consistency:** All 6 models get identical treatment. Most consistent method — no GRIB2 or NWP cloud dependency.

### 2. Ogimet-NWP (`assess_icing_zones_ogimet_nwp`)

**Signal:** `effective = ogimet_index(T) × cloud_fraction(alt) × glaciation(CLW, ICMR)`

| Input | GFS (or HRRR) | ECMWF (Europe) | ICON (EU/D2) | MétéoFr/UKMO/GEM |
|-------|---------------|----------------|--------------|------------------|
| NWP cloud layers (gating) | GFS: GRIB bulk bands (base/top, `source="grib"`). HRRR: from condensate (`source="nwp_condensate"`) | Per-deck from 3D `cc` (`source="nwp_3d"`) | Per-deck from 3D `clc` + convective | **None** — no native envelope |
| CLW/ICMR glaciation | Yes (reduces glaciated cloud) | Yes (CLW/CIW from a2 GRIB) | Yes | **N/A** |
| Method runs at all? | Yes | Yes | Yes | **No — returns `[]`** |

**No degradation path, by design.** `assess_icing_zones_ogimet_nwp()` (and `assess_icing_zones_ieng()`) bail with `[]` when `clouds` is `None` or empty rather than falling back to bulk ICAO percentages: fabricating icing at altitudes the cross-section can't anchor to a drawn cloud band was judged worse than reporting nothing. Do NOT "fix" this by reintroducing synthesized bands — the old bulk-%-narrowed-by-DD synthesis was removed from `clouds.build_nwp_cloud_layers()` for exactly this reason, and the docstring there says so.

Per-level index stored in `icing_index_nwp` (separate from `icing_index` used by DD) to prevent overwrite.

### 3. SFIP (`assess_sfip_zones`)

**Signal:** Weighted fuzzy-logic membership: `Σ(w_i × M_i)` for T, RH, CLW, VV

| Model | Variant | Weights | Missing Signals |
|-------|---------|---------|-----------------|
| GFS | full | 0.35T + 0.15RH + 0.35CLW + 0.15VV | None |
| ICON (EU) | full_no_vv | 0.35T + 0.15RH + 0.35CLW + 0.15×0 | **No omega** → VV always 0 |
| ECMWF (Europe) | full | 0.35T + 0.15RH + 0.35CLW + 0.15VV | None (CLW/CIW from ECMWF IFS GRIB a2) |
| MétéoFr | proxy_no_vv | 0.40T + 0.25RH + 0.25proxy + 0.10×0 | No CLW, no ICMR, **no omega** |
| UKMO | proxy | 0.40T + 0.25RH + 0.25proxy + 0.10VV | No CLW, no ICMR |
| GEM | proxy_no_vv | 0.40T + 0.25RH + 0.25proxy + 0.10×0 | No CLW, no ICMR, **no omega** |

**Variant naming:** `full`, `full_no_vv`, `interp`, `interp_no_vv`, `proxy`, `proxy_no_vv`

---

## Shared Utilities (`icing_common.py`)

### `is_in_cloud_layer(level, cloud_layers, margin_ft)` — current gate

The cloud-gating check used by **all four** zone builders today (Ogimet-DD,
Ogimet-NWP, IENG, SFIP). Pure altitude-band test: true when the level's
altitude falls within any supplied cloud layer ±`CLOUD_MARGIN_FT`. It does
**no** per-level DD re-check — the cloud-layer list passed in already encodes
the detection method's decisions (DD-detected+NWP-filtered layers for DD-gated
methods; pure-model NWP layers for NWP-gated methods). The caller chooses which
layer list to pass (`dd_clouds` vs `nwp_clouds`); see the `is_in_cloud_layer`
call sites in `icing.py` and `sfip.py`.

### `is_near_cloud(level, clouds, dd_threshold, skip_sct)` — dead code

The old proximity check that mixed a per-level DD re-check with layer proximity.
Superseded by `is_in_cloud_layer`. The legacy `assess_icing_zones()` it existed
for has since been deleted, so as of this sync it has **zero callers anywhere —
not even tests**, and its `DD_BKN_THRESHOLD`=2.0 / `DD_SCT_THRESHOLD`=3.0
constants are unreferenced too. Its own comments still claim tests use it; they
don't. Do not use for new gating; it is a removal candidate.

### `is_supercooled_rain(rain_water_kg_kg, temperature_c)` (#530)

True when non-trivial rain water (`qr ≥ 1e-6 kg/kg`) sits at `T < 0°C` —
freezing-rain / large-droplet regime, physically distinct from supercooled cloud
droplets. Requires **both** inputs, so a model publishing no `qr` (ICON-EU, GFS,
ECMWF) yields `False` meaning *not detected*, never *no supercooled rain here*.
Threshold rationale and rejected alternatives: meteorology-decisions §24.

Flows through as `DerivedLevel.supercooled_rain` → `IcingZone.supercooled_rain`
(`any()` over the zone's levels). **Deliberately not a severity modifier** —
`_enhance_severity()` ignores it, because the signal has never been checked
against a real winter freezing-rain case and an unvalidated upgrade would move
grades on every ICON-D2 winter briefing at once. Issue #411 gates the upgrade.

### `nwp_cloud_cover_at_altitude(altitude_ft, ...)`

Checks **all** diagnostic layers (low/mid/high) regardless of ICAO band. Returns highest cloud cover for any layer whose base/top range (±500ft margin) contains the altitude. With no band geometry (ECMWF, ICON, HRRR), returns the model's own GRIB band cover via `clouds.nwp_band_cover_pct` — banded by `band_definition` (NCEP pressure bands need `pressure_hpa`) — and Open-Meteo bulk ICAO-band % only when the diagnostics carry no band cover (#844). Ogimet-NWP / IENG then scale by `max(this, the gating deck's mean_cloud_cover_pct)` (`icing._level_nwp_cover_pct`).

**`None` means unknown, never 0 %** (#807). Inside the geometry branch the cover paired with a diagnostic layer is that layer's own `cover_pct` (same model field as the geometry — for GFS the NCEP pressure-band cover), with the bulk ICAO-slab % only as a fallback when the layer has none; this matches `clouds.build_nwp_cloud_layers`. Previously it was `bulk_pct or 0.0` — a mismatched-slice pairing and missing-as-clear. Outside every diagnosed layer it returns 0.0 only when the altitude's band has geometry of its own; a band without geometry returns its band cover per `nwp_band_cover_pct` (or `None`).

What each caller does with unknown:
- **Ogimet-NWP / IENG** — the level already passed the native-NWP-layer gate, so cloud is present and only the fraction is unknown: `icing._cloud_fraction_for_level` uses the gating layer's `mean_cloud_cover_pct`, else an unscaled index (fraction 1.0). A *known* 0 % still skips the level.
- **SFIP proxy** — unknown → 0.0, deliberately: `membership_clw_proxy` takes `max(sounding_score, nwp_score)`, so it falls back to the DD/RH evidence alone. SFIP-full ignores cover.

Used by Ogimet-NWP, IENG and SFIP (previously SFIP had a simpler single-band version that missed cross-band cloud layers).

### `glaciation_factor(clw, icmr, T)` — scope of the warm floor

CLW/(CLW+ICE) floored at 0.8 above −10 °C, tapering to 0.15 at −20 °C. The floor applies to an ice-dominant mix (CLW≈0, ICMR>0). It does **not** apply when CLW = ICMR = 0 (returns 0.0 — the level is dropped even inside an NWP cloud layer). SFIP-full never reaches it for CLW ≤ 0: `compute_sfip_level` hard-gates CLW ≤ 0 → NONE first. So CLW = 0, ICMR > 0 at −5 °C keeps 80 % in Ogimet-NWP but is NONE in SFIP-full. Both are recorded calibration divergences (pipeline-review 2026-08 §3.8), not re-tuned.

### Opt-in severity enhancement (`icing_severity_enhance`)

Wired in #807 (it was threaded to `analyze_sounding` and never read). When on, `_enhance_severity` runs per zone on the zone's own grouped levels for **Ogimet-DD and Ogimet-NWP only**: ≥3 levels RH > 95 % **and** known NWP cover ≥ 50 % somewhere in the zone → LIGHT→MODERATE, MODERATE→SEVERE if mean T ≤ −5 °C; column PW > 25 mm → LIGHT→MODERATE. Upgrade-only; `mean_icing_index` is left as computed. Unknown cover never corroborates. IENG and SFIP are excluded (they reach SEVERE on their own). Not applied by the airport-profile / on-the-fly sounding paths (`api/airport_profile.py`, `storage/sounding_profiles.py`), which call `analyze_sounding` without the flag.

**Known ceiling:** the Ogimet `raw/2` normalisation caps pure-stratiform icing at index 50 (MODERATE; SEVERE = 80). Deliberate and not re-tuned; stratiform SEVERE comes only from the enhancement above, SFIP or IENG.

### DD gate vs DD attenuation (Ogimet-DD)

The DD cloud layers that gate Ogimet-DD come from `clouds.py` (`IN_CLOUD_DD_THRESHOLD = 3.0`, so DD 2–3 °C forms SCT layers), but the Ogimet-DD attenuation taper is 0 at DD ≥ 2.0 °C. SCT-grade and ±500 ft margin levels pass the gate and contribute nothing; SFIP-proxy still scores them. Recorded calibration choice (§3.8), not re-tuned; an unused duplicate `IN_CLOUD_DD_THRESHOLD` was removed from `icing.py`.

### `classify_icing_type(temperature_c, wet_bulb_c)`

Wet-bulb temperature with dry-bulb fallback. Thresholds shifted ~1°C colder than dry-bulb equivalents (accretion surface closer to wet-bulb):
- CLEAR: −4°C to 0°C
- MIXED: −11°C to −4°C
- RIME: < −11°C

Used by all three methods (previously SFIP used dry-bulb only with different thresholds).

### `group_icing_levels(icing_levels, build_zone_fn, *, all_levels=None)`

Shared adjacency grouping. With *all_levels* (the pre-gated input the caller iterated over), two icing entries merge into the same zone iff they are directly consecutive in it: a level filtered out by the caller's gate (e.g. an NWP cloud-band gap, or a measured level that isn't icing) splits the zone. The model's level spacing does not (#835, meteorology-decisions §58): Open-Meteo ECMWF's 850 → 700 hPa (150 hPa) neighbours merge. `ZONE_MAX_NATIVE_GAP_HPA = 200` is only a sanity cap for degraded soundings and must stay ≥ 150.

Levels dropped by `prepare_profile` (no T, or no Td/RH) never reach *all_levels*, so an icing layer bridges across them (within the cap): unknown is not clear.

When *all_levels* is omitted the function falls back to the legacy pressure-only check; this path is preserved for backward compatibility but should not be used by new callers (it's vulnerable to bug #7 below).

Used by all four zone builders: Ogimet-DD, Ogimet-NWP, IENG, and SFIP.

---

## Bugs Found and Fixed

### 1. Inconsistent NWP cloud altitude lookup (SFIP vs Ogimet)

**Problem:** SFIP's `_cloud_cover_for_level` only checked the single ICAO-band layer matching the altitude. A cloud layer extending from low into mid band (e.g., topping at 11,000ft) would be missed at mid-band altitudes (8,000ft).

**Fix:** Both now delegate to `nwp_cloud_cover_at_altitude()` which checks all diagnostic layers.

### 2. SFIP icing type used dry-bulb only

**Problem:** SFIP classified icing type using dry-bulb temperature with thresholds shifted ~1°C warmer than Ogimet's wet-bulb-based thresholds. Created inconsistent type reports between methods for the same level.

**Fix (corrected 2026-06-06 — partial):** SFIP shares the unified cloud-lookup and
zone-grouping utilities, but for icing **type** it **intentionally keeps its own
dry-bulb thresholds** (`sfip.py:_classify_icing_type`, per Belo-Pereira 2015 /
Morcrette et al. 2019) — it does **not** delegate to the wet-bulb
`classify_icing_type()` that the Ogimet-family methods use. So type labels can
still differ between SFIP and Ogimet by design; only Ogimet-DD/NWP/IENG were
unified. (Earlier wording here claimed full unification — that was overstated.)

### 3. `icing_index` overwrite between methods

**Problem:** Both `ogimet_dd` and `ogimet_nwp` wrote to `DerivedLevel.icing_index`. Since they run sequentially on the same levels list, ogimet_nwp (running second) overwrote ogimet_dd's values.

**Fix:** Added `DerivedLevel.icing_index_nwp` field. Ogimet-DD writes to `icing_index`, Ogimet-NWP writes to `icing_index_nwp`.

### 4. Vertically interpolated CLW not flagged

**Problem:** The vertical interpolation pass in `_interpolate_cloud_water()` filled CLW/ICMR for intermediate 25hPa levels but never set `clw_interpolated=True`. SFIP reported `variant="full"` when it should have reported `variant="interp"`.

**Fix:** Vertical interpolation now sets `dl.clw_interpolated = True` on filled levels.

### 5. ICON SFIP "full" variant silently degraded

**Problem:** ICON gets CLW from GRIB2 so SFIP selected "full" variant with 15% weight on VV. But omega is unavailable for ICON, so VV always contributed 0. Max possible SFIP was 85% of GFS, but variant said "full".

**Fix:** Added `_no_vv` variant suffix. ICON now reports `full_no_vv`, MétéoFr/GEM report `proxy_no_vv`. Frontend updated to handle new variant names.

### 6. Duplicated utilities across modules

**Problem:** `_is_near_cloud`, `_classify_icing_type`, `_nwp_cloud_for_altitude`/`_cloud_cover_for_level`, and zone grouping logic were duplicated between `icing.py` and `sfip.py` with subtle differences.

**Fix:** the NWP cloud-altitude lookup and zone grouping were extracted to
`icing_common.py` and both modules use them. Cloud gating was unified too, but
has since moved past the original `_is_near_cloud` extraction: all four builders
now gate on `is_in_cloud_layer` (clean altitude-band check), with the old
`is_near_cloud` left behind as dead code (see Shared Utilities). **Exception:** the
icing-**type** classifier was *not* unified — `icing_common.classify_icing_type()`
(wet-bulb) is used by the Ogimet-family methods only; SFIP retains its own
dry-bulb `_classify_icing_type` by design (see Bug #2).

### 7. Icing zones bridging cloud-band gaps (PR #106)

**Problem:** `group_icing_levels` merged adjacent surviving icing levels using a pressure-gap heuristic alone (`abs(p1 - p2) ≤ 100 hPa`). At typical 25 hPa input spacing, a 3-level NWP cloud-band gap (e.g. GFS lcc top FL114 → mcc base FL157) collapses to exactly 100 hPa between the surviving icing levels on either side — the heuristic let two cloud-gated zones bridge the "no cloud" stretch. Visible on prod flight `kpao_kgcn-2026-05-05-dc1b` GFS pt13: NWP cloud bands `[4088-11477]` and `[15679-20851]` ft, but `icing_ogimet_nwp_zones` produced a single `7257-18442` ft zone bridging the FL114-FL157 gap — rendering as icing over apparent blue sky on the cross-section.

**Fix:** `group_icing_levels` now optionally takes the pre-gated input list (`all_levels=`). When provided, two icing entries merge iff **both** they were directly consecutive in `all_levels` (no level filtered between them) and the pressure gap stays under the legacy threshold. All four zone builders updated. The dual-check guarantees zones never bridge a gating-induced gap regardless of how the underlying pressure spacing aligns with the gap width.

---

## Per-Method Analysis (continued)

### 4. IENG (`assess_icing_zones_ieng`)

**Signal:** `effective = layered_index(T) × cloud_fraction(alt)` — no glaciation correction.

Uses the same Ogimet layered formula (parabola peaking at −7°C) but weights only by NWP cloud coverage fraction. Unlike Ogimet-NWP, does NOT apply the glaciation factor from CLW/ICMR. This makes it simpler and slightly more conservative in glaciated clouds (where Ogimet-NWP would reduce the index).

Matches the approach used by CloudPath. Convective component added when CAPE > 100 J/kg, weighted by NWP convective cloud cover.

**Model consistency:** Same as Ogimet-NWP — requires a *native* NWP cloud envelope and returns `[]` without one. So GFS/HRRR, ECMWF-in-Europe and ICON only; bulk Open-Meteo percentages alone are not enough.

**Key difference from Ogimet-NWP:** No `glaciation_factor(clw, icmr, T)` call. In ice-dominant clouds (high ICMR/CLW ratio), Ogimet-NWP reduces the index while IENG does not.

### 5. SLD (`assess_sld_zones`)

**Signal:** Atmospheric structure — detects supercooled large droplets (>50μm) that can overwhelm de-ice systems. Two physical formation mechanisms implemented; only warm-nose is active.

**Active — Warm-nose freezing rain:** When the precipitation module detects a warm layer (Tw > 0°C) above a subfreezing surface layer with `freezing_rain_risk=True`, the cold layer below the warm nose is an SLD zone. Freezing rain drops are 0.5–5mm — SLD by definition. Risk: MODERATE (normal), SEVERE if warm nose ≥ 3000ft deep.

**Disabled — Collision-coalescence:** Deep clouds (>3000ft) with tops warmer than −12°C where ice nucleation is too slow for Bergeron glaciation. Disabled because it fires on virtually every deep stratiform cloud in European weather — NWP models lack droplet size data to distinguish SLD from normal icing. Code retained in `_coalescence_sld_zones()`. See `designs/known-issues.md` for details and future improvement ideas.

**Model availability:** Works with all models (uses temperature/wet-bulb profile, not GRIB-specific fields). The `mechanism` field on `SldZone` indicates which detection fired ("warm_nose" or "coalescence").

**Cross-section:** Layer `sld-bands`, disabled by default in every preset (experimental), and marked unavailable when the sounding carries no `sld_zones`. Zones expanded to minimum 1000ft thickness for visibility.

**Gotcha — two unrelated SLD channels.** `sounding.sld_zones` (from `sld.py`) is the live one. `IcingZone.sld_risk` is a *separate* field that **nothing populates**: `icing._build_zone_simple` never sets it and `icing._detect_sld()` returns `False` unconditionally. Advisory code in `advisories/_helpers.py`, `fiki_icing.py` and `icing_escape.py` branches on `zone.sld_risk`, so those branches are all currently unreachable — the safe default there must stay "SLD survives a NONE risk" for whenever it is wired. Don't "clean up" the dormant clauses.

---

## Future Considerations

### Weight redistribution for missing omega

MétéoFr and GEM (`proxy_no_vv`) allocate 10% weight to VV that always returns 0. Redistributing this dead weight to other inputs would give fairer cross-model comparison. Suggested: `0.44T + 0.28RH + 0.28proxy` (proportional redistribution).

Similarly, ICON (`full_no_vv`) could use `0.41T + 0.18RH + 0.41CLW` instead of wasting 15% on zero-VV.

### Proxy variant accuracy

The proxy CLW membership (`membership_clw_proxy`) combines DD score and NWP cloud cover. This is a significantly less precise signal than actual CLWMR. For models without GRIB2 enrichment (MétéoFr, UKMO, GEM — and ECMWF outside Europe), SFIP should be interpreted with lower confidence.
