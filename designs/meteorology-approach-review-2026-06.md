# Meteorology Approach Review — Advisories & Cross-Section

**Date:** 2026-06-11
**Scope:** External-style review of the meteorological approach in the web app: the
metrics chosen, how they are computed, and how they are interpreted as advisories
for GA decision-making — with focus on the route advisory system and the
cross-section visualization. Follows the convention of the other point-in-time
audit docs: trust the reasoning, re-verify line numbers against current code.

**Sources reviewed:** `analysis.md`, `advisories.md`, `analysis-metrics.md`,
`meteorology-decisions.md`, `visualization.md`; code in
`src/weatherbrief/analysis/sounding/` (icing, sfip, clouds, convective,
vertical_motion, e_shear, advisories), `src/weatherbrief/analysis/advisories/`
(all 14 evaluators + helpers), `web/ts/visualization/` (cross-section layers,
route graph, route map). "14 evaluators" is the June-2026 count — the registry
has grown well past that since, partly from this review's §5.

**Parent doc:** [meteorology-decisions.md](./meteorology-decisions.md) (links
here from §8 and §11); this review is its point-in-time companion, not a
standalone spec. Findings that were acted on became decision-log sections
(§8–§11, §25); this file keeps the original reasoning as the record.

---

## 1. Overall assessment

The meteorological architecture is unusually strong for a GA product:

- **Parallel independent methods** (DD vs NWP clouds, four icing indices, thermo
  vs NWP convective) with the divergence *surfaced* rather than blended is the
  right philosophy for a decision-support tool — model disagreement IS the
  uncertainty signal a pilot needs at D-7..D-1.
- **Icing-gated-by-displayed-cloud** ("icing only where a cloud band is drawn")
  is an excellent consistency invariant — it prevents the classic GRAMET-style
  confusion of icing calls floating in visually clear air.
- The **convective tier** (regime discrimination, realizable ML-CAPE scoped to
  ACTIVE, loaded-gun-on-potential, elevated-convection flag) is meteorologically
  literate and the safety asymmetry is correctly oriented.
- The **decisions log** discipline (meteorology-decisions.md) is rare and
  valuable.

The findings below are therefore mostly calibration, consistency, and
escape-logic issues — plus a small number of genuine bugs.

---

## 2. Likely bugs (high confidence)

> **Status update (verified against code).** Most of the findings below have
> since been fixed — see `meteorology-decisions.md` §8 (dated 2026-06-11,
> "Review-driven fixes"). Per-finding RESOLVED markers added inline; the
> original reasoning is kept verbatim as the record. The single
> `_descend_below_icing` rewrite resolved §2.1 (min→max), §2.2 (terrain floor),
> and §2.3 (warm-nose / freezing-rain guard) together. §2.4 (IENG vapor
> density), §2.5 (E-Shear units), and §2.6 (negative-Ri storage) are also fixed.
> Re-verified 2026-06-20: §2.7's `analysis.md` Ogimet-table drift is also now
> fixed — `analysis.md` reads `< 10 NONE / 10–30 LIGHT`, matching
> `icing.py:53-55`. All §2 findings are resolved. (Line numbers below have
> drifted by a few lines as code moved; the resolutions still hold.)
> Re-verified again 2026-08-15 — every §2 fix is still in place
> (`advisories.py:634` `max(candidates)`, `:663` terrain floor, `:603` FZRA
> guard; `icing.py:561` real `_vapor_density`; `e_shear.py:42-43`
> `_VWS_SCALE`/`_HWS_SCALE`; `vertical_motion.py:163-170` stores negative Ri).
> Line numbers have drifted ~60 lines in `advisories.py`; the reasoning holds.

### 2.1 `_descend_below_icing`: code contradicts its own docstring, and the wrong way

`sounding/advisories.py:524-576`. Docstring: *"escape altitude =
**max**(freezing_level, lowest_cloud_base)"*. Code: `escape = **min**(candidates)
− 500`.

Meteorologically, **max is correct**: to exit airframe icing it suffices to be
*either* in warm air (below the freezing level, even in cloud) *or* in clear air
(below the lowest icing-bearing cloud base, even sub-zero). The highest altitude
satisfying at least one condition is `max(fz, cloud_base) − margin`. Using
`min()` demands both simultaneously, which:

- needlessly forces the escape thousands of feet lower in winter (cloud base
  3000 ft, FZL 9000 ft → advisory says "descend below 2500 ft" when 8500 ft is
  a valid warm-air escape);
- increases the chance the displayed altitude is **below terrain** (see 2.2).

Note the route-level `IcingEscapeEvaluator` independently uses the freezing
level vs terrain (correct logic); only this per-waypoint altitude advisory is
affected.

> **RESOLVED.** `advisories.py:599` now uses `max(candidates) − margin`; the
> docstring matches. (meteorology-decisions §8a)

### 2.2 Escape altitudes have no terrain floor

`_descend_below_icing` clamps at 0 ft MSL only (`advisories.py:576`); the
elevation profile is available in the pipeline but not consulted. Over the Alps
the advisory can read "descend below 4500 ft to exit icing" where the valley
floor is 6000 ft. The route advisory (IcingEscape) does check terrain, but the
number a pilot sees on the altitude advisory / regimes panel is the unfloored
one. Recommendation: floor at `max_terrain_along_segment + margin` and mark
`feasible=False` when the floor wins — symmetric with how `climb_above_icing`
handles the service ceiling.

> **RESOLVED.** `advisories.py:628-632` now takes `terrain_elevation_ft`, keeps
> the advisory but marks `feasible=False` when escape < terrain +
> `_TERRAIN_CLEARANCE_FT`.

### 2.3 Descend-below-freezing-level escape is unsafe under a warm nose

`freezing_level_ft` is the first 0 °C crossing from the surface. In a
freezing-rain profile (sub-zero surface layer under a warm nose) "descend below
the freezing level" puts the aircraft in sub-zero air beneath supercooled
precipitation — the worst place available. The precipitation module already
detects warm noses and classifies FZRA/ice pellets
(`sounding/precipitation.py`), but the escape advisory never consults it.
Recommendation: when a warm nose / surface sub-zero profile is detected,
suppress the descend advisory (or invert it to "climb into the warm layer /
land short") — and see §5.1 for the missing freezing-precipitation advisory.

> **RESOLVED (guard).** `advisories.py:566-571` now sets the per-model escape to
> `None` when `precipitation.freezing_rain_risk` is set, and returns a "no
> descent escape" advisory when every model is FZRA. The standalone
> `FreezingPrecipEvaluator` proposed in §5.1 has since been built
> (`advisories/freezing_precip.py`, auto-registered) — see §5.1.

### 2.4 IENG convective term: level vapor density hard-coded to 0

`sounding/icing.py:547`:
`conv_index = _compute_convective_index(lv.temperature_c, 0.0, vd_base)`.

Passing `vapor_density=0.0` makes the moisture term
`(vd_base − 0)/ρv_20sat` — the maximum possible moisture differential,
identical for every level. The Ogimet convective formula's moisture term is
supposed to measure the *decrease* of vapor density from cloud base to the
level (condensed water proxy). As written, IENG's convective add-on is a pure
temperature curve scaled by cloud-base moisture, systematically overestimated
relative to the same formula in Ogimet-DD/NWP (`icing.py:392,457` pass the real
per-level `rho_v`). Impact is bounded — it is scaled by
`convective_cover_pct`, which is often null — but it is an inconsistency
between methods that are explicitly compared side-by-side
(meteorology-decisions §2 method-comparison table).

> **RESOLVED.** `icing.py:548` now passes `_vapor_density(lv.dewpoint_c)` (the
> real per-level vapour density), matching the Ogimet-DD/NWP paths.
> (meteorology-decisions §8d)

### 2.5 E-Shear units do not match the formula's calibration

`sounding/e_shear.py:29-31`. The CloudPath formula `E = (5·HWS + VWS² + 42)/4`
is documented (docstring lines 8-9) with VWS in **kt/1000 ft** and HWS in
**kt/100 nm**. The implementation computes shear in SI and scales by `1e3` /
`1e5`, producing **m/s per km** and **m/s per 100 km**:

- VWS: 1 m/s/km ≈ 0.59 kt/1000 ft → the m/s-per-km number is ×1.69 **larger**
  than the same shear expressed in kt/1000 ft; since VWS enters **squared**,
  the VWS contribution to E is **overstated ×2.85** vs a kt-calibrated formula.
- HWS: 1 m/s/100 km ≈ 0.28 kt/100 nm → the HWS contribution is
  **understated ×3.6**.

If the empirical constants (5, 42, thresholds 40/80/160) come from a kt-based
source, the implementation over-weights vertical shear and under-weights
horizontal shear — over-calling E-Shear in VWS-dominated profiles and
under-calling it where horizontal gradients dominate (jet flanks).
Recommendation: convert the scale factors to the documented calibration units
and add a unit test pinning a known shear profile to a known E value.

*(Correction over the first published version of this section, which had the
VWS direction inverted. Fixed in code — see meteorology-decisions §8.)*

> **RESOLVED.** `e_shear.py:35-37` now scales SI shear to the formula's
> calibration units via `_VWS_SCALE` (m/s/m → kt/1000ft) and `_HWS_SCALE`
> (m/s/m → kt/100nm); the old 1e3/1e5 factors are gone. (meteorology-decisions
> §8b. The recommended pinning test now exists — `tests/test_e_shear.py`
> asserts unit-calibrated outcomes, e.g. 20 kt/1000 ft VWS → MODERATE,
> 25 → SEVERE, 10 → NONE, and HWS in kt/100 nm.)

### 2.6 Statically unstable layers are invisible to the Richardson CAT path

`vertical_motion.py:121`: Ri is only stored when `n_sq >= 0`. A layer with
N² < 0 (absolutely unstable — the most turbulent case, Ri < 0 in classical
terms) gets *no* Ri, hence no CAT layer. The convective assessment covers
surface-rooted instability, but an **elevated** unstable layer (e.g. above a
frontal surface) with strong shear is exactly the case that produces moderate+
turbulence in cruise and is currently dropped by both paths (E-Shear may catch
it only if raw shear is large). Recommendation: treat N² < 0 with valid shear
as SEVERE CAT (or at least MODERATE), not as missing data.

> **RESOLVED (storage).** `vertical_motion.py:121-123` now stores Ri whenever
> shear is valid, regardless of N² sign, so a statically-unstable layer is no
> longer dropped as missing. (meteorology-decisions §8c — verify how a *negative*
> Ri then classifies downstream in `_classify_*`.)

### 2.7 Doc drift (minor, but in safety tables)

- ~~`analysis.md` Ogimet risk table says "≤0 NONE / 0–30 LIGHT"; code
  (`icing.py:53-55`) is `<10 NONE / 10–30 LIGHT`.~~ RESOLVED — `analysis.md`
  now reads `< 10 NONE / 10–30 LIGHT`. `analysis-metrics.md` already had the
  correct table.
- ~~`_descend_below_icing` docstring vs code (§2.1).~~ RESOLVED (see §2.1).

---

## 3. Calibration & soundness observations (no bug, but worth a decision-log entry)

> **Status (verified 2026-08-15).** Two items have since landed:
> - **§3.6 GREEN-by-absence — RESOLVED.** `_helpers.below_coverage()` +
>   `EvidenceSample.assessed` (#391/#393) downgrade a would-be-GREEN to
>   UNAVAILABLE when under half the domain was assessable; a flagged verdict
>   always stands. `turbulence.py` now emits UNAVAILABLE per unassessed point
>   and for the whole grade. The *capability-weighted vote* half of §3.6 (icing
>   models on proxy microphysics voting equal to GFS/ECMWF/ICON) is still open.
> - **§3.7 %-of-route on short routes — PARTIALLY RESOLVED for convective.**
>   Decisions §11(a): `FlightCategoryEvaluator` grades convection within
>   `conv_radius_nm` (25 nm) of each end with **no** percentage threshold. Icing
>   and turbulence still have no absolute-extent floor.
>
> Two items have *sharpened* rather than aged out:
> - **§3.3**: decisions §25 (#533) gave Richardson an altitude-dependent
>   resolution correction. E-Shear still has none, so the two CAT methods are
>   now calibrated asymmetrically as well as being non-independent.
> - **§5.7's `cloud_cover_low_pct`**: still only an ICAO-band *fallback* in
>   `sounding/advisories.py:347-353`, not the third ceiling signal §3.4 wants.
>
> §3.1, §3.2, §3.4, §3.5, §3.8–§3.11 are all still open exactly as described
> (`icing.py:140` `raw/2`; `sfip.py:37-42` un-renormalized `_W_*_PROXY`;
> `RunwayWind` has no `crosswind_gust_kt`; `flight_category.py` params are
> still `amber_vis_sm`/`red_vis_sm`; `_climb_above_icing` still computes
> `feasible` against the sounding top).

### 3.1 Pure stratiform Ogimet can never exceed MODERATE

`_compute_icing_index` (`icing.py:124-140`): `raw/2` normalization caps a
layered-only profile (layered_frac = 1.0, layered_index max 100 at −7 °C) at
**50** → MODERATE (SEVERE starts at 80). SEVERE is reachable only via the
convective term, i.e. only when CAPE > 100. Severe stratiform icing does exist
(deep warm-frontal decks, high LWC near −5 °C), and the only paths to SEVERE for
it are the off-by-default `_enhance_severity` or SFIP. If this ceiling is
deliberate ("stratiform transit is escapable, reserve SEVERE for convective"),
record it in meteorology-decisions.md; otherwise consider letting the
`_enhance_severity` RH+NWP corroboration path run by default, since it already
requires cold temps + deep saturation + NWP cloud ≥ 50 %.

### 3.2 SFIP `_no_vv` variants: dead weight = systematic low bias for MF/GEM

`sfip.py:31-40, 253-265`: when omega is unavailable the VV weight (0.10 proxy)
contributes 0 and the index ceiling drops to 0.90. Météo-France and GEM —
already on the weakest (proxy CLW) variant — are thus *additionally* biased low
by 10 %. This matters because per-model results vote in MAJORITY aggregation:
the models with the least icing physics systematically vote "greener", which
can flip an aggregate AMBER→GREEN. Recommendation: renormalize weights when a
membership is structurally absent (0.40/0.25/0.25 → /0.90), which is standard
practice for fuzzy aggregations with missing members.

### 3.3 Two CAT methods are not as independent as presented

Ri (`vertical_motion.py`) and E-Shear (`e_shear.py`) both derive from the same
coarse pressure-level wind/temperature profile. The Ri thresholds were loosened
(0.5/1.0/2.0) explicitly to compensate for resolution-driven positive bias —
correct reasoning — but E-Shear's VWS suffers the same vertical-resolution
smoothing and has no equivalent compensation (and §2.5 pushes it further the
same direction). When both methods read NONE, the honest statement is "no
*resolved* shear layer", not "smooth air"; the UI/digest should keep that
framing (cross-section tooltip currently does not distinguish).

### 3.4 DD coverage classification: vertical saturation as a horizontal proxy

`clouds.py:30-42` maps *mean dewpoint depression* to OVC/BKN/SCT. DD measures
how saturated the column is at the point, not horizontal sky coverage; the okta
language implies the latter. This is a known, documented heuristic, and the NWP
reclassification overlay exists (disabled) — but the okta labels propagate into
ceiling determination (lowest **BKN/OVC** base) and the VMC-cruise evaluator
(BKN/OVC % of route), where they are treated as genuine coverage. A DD of
0.9 °C in a stable moist layer prints OVC and can set an IFR ceiling on its own
(decision log §1 already documents the ECMWF false-IFR case and the pending
`cloud_cover_low_pct` third-signal enhancement — that enhancement is worth
prioritizing, since ceiling is the single most decision-relevant output for GA).

### 3.5 Fixed DD < 3 °C threshold with altitude

A constant DD threshold approximates a constant RH (~75–85 %) across the
troposphere, so it is defensible. Two refinements used in comparable products:
(a) tighten the threshold with height (e.g. 3.0 °C below 750 hPa, 2.5 °C
mid-levels, 2.0 °C above 500 hPa) to reduce mid/high over-detection; (b) at
T < −25 °C assess saturation w.r.t. ice. Low priority: icing indices are
temperature-gated anyway, so the main consumer affected is cloud/ceiling
display of cirrus.

### 3.6 Aggregation default (MAJORITY) and asymmetric model quality

The MAJORITY default with worst-of-tied tie-breaking is reasonable for noise
suppression, and 2 RED + 1 AMBER correctly yields RED. The residual risk is
4-quiet-models vs 2-capable-models scenarios: for icing, only GFS/ECMWF/ICON-EU
carry microphysics; MF/GEM/UKMO run proxies that under-read (§3.2). A
capability-weighted vote, or excluding structurally-blind models from
hazard-specific votes (a model with no omega shouldn't vote on vertical-motion
advisories — it currently returns GREEN-by-absence in `turbulence.py:77-79`
rather than UNAVAILABLE), would make MAJORITY safer. **GREEN-by-absence vs
UNAVAILABLE is the single most important aggregation fix**: absence of data is
currently indistinguishable from a benign forecast in several evaluators.

### 3.7 Percentage-of-route thresholds vs short routes

All %-based evaluators (`pct_above_threshold`) grade on fraction of ~20 points.
On a 60 nm hop, one convective cell = 5 % → GREEN even though it may sit over
the destination. HIGH/EXTREME overrides mitigate the worst case for convective,
but icing/turbulence have no absolute-extent floor. Consider `max(pct_rule,
absolute_nm_rule)` — e.g. any ≥ 10 nm contiguous MODERATE icing ≥ AMBER
regardless of route length.

### 3.8 Airport wind: gusts and crosswind interact

`airport_wind.py`: crosswind (15/25 kt) and gust (25/35 kt) are graded
independently on the **mean** wind. Standard GA practice is to assess crosswind
at the gust speed (a 12 kt mean / 28 kt gust 60° off-axis is a ~24 kt
instantaneous crosswind — above most POH demonstrated values — yet grades
GREEN-crosswind/AMBER-gust today). Recommendation: add
`crosswind_gust_kt = gust × sin(Δ)` to `RunwayWind` and grade the worst of mean
and gust crosswind. Also consider gust *factor* (gust − mean ≥ 10–15 kt) as the
gust criterion instead of absolute gust, which double-counts strong steady
winds.

### 3.9 Visibility thresholds in statute miles for a European product

`flight_category.py` parameters are sm-based (5/3 sm) — US FAA category
boundaries. European VFR minima are metric (5 km / 1500 m / 800 m) and ICAO
flight categories aren't sm-defined. The conversion is handled correctly
internally; this is a presentation/parameter-semantics issue: a French pilot
tuning "amber_vis_sm = 5" is being asked to think in units they never use.
Locale-aware parameter units (the QNH/altimeter route-graph metric already does
this) would fix it.

### 3.10 Convective top-clearance skip and the climb/descent phases

`convective.py:164-169`: convection with tops below `cruise − 2000 ft` is
skipped. For *cruise* exposure that's right, and such cells are weak by
construction — but route advisories are implicitly cruise-phase advisories
everywhere (icing uses `[0, cruise+buffer]`, so it does cover climb/descent;
convective does not). A cell topping 1500 ft below cruise still must be
out-climbed after departure. Low severity in practice (tops below cruise −
2000 ft with cruise ≤ 10k ft implies very shallow convection), but the
asymmetry between the icing and convective altitude windows is worth unifying.

### 3.11 Cloud-top uncertainty is informational where it should be operational

`_climb_above_icing` (`advisories.py:593-662`) computes the escape from the
*sounding-derived* top and only mentions `theoretical_max_top_ft` in the reason
string. For convective regimes the EL-based theoretical top is routinely
5–10 kft above the resolved top; "climb above 12,000 ft (theoretical max
17,500 ft)" with `feasible=True` at a 14,000 ft ceiling invites a trap — VFR on
top with tops rising through the ceiling. Recommendation: when
`theoretical_max_top_ft − top_ft ≥` the existing 2000 ft uncertainty gap,
compute `feasible` against the theoretical top (or mark feasibility
"uncertain"), not just annotate.

---

## 4. Cross-section / visualization findings

> **Status (verified 2026-08-15).** §4.2 is **largely addressed by a different
> mechanism than the one recommended**: instead of a per-layer "no data" hatch,
> `data-extract.ts:getUnavailableLayers` (now ~line 458) grays out the toggle
> for any layer whose source is structurally absent — explicitly distinguishing
> "model says clear sky" (`nwpCloudLayers === []`) from "no NWP enrichment"
> (`null`) — and `cross-section/nwp-fallback.ts` substitutes an available
> method (NWP clouds → DD, NWP/SFIP icing → Ogimet-DD, NWP convective →
> thermodynamic) so a layer never silently renders nothing. That closes the
> "blank reads as clear" ambiguity at the *control* level; the residual gap is
> on-canvas (compare mode still shows an unannotated blank panel). §4.1, §4.3
> and every §4.4 item are unchanged (`estimateTowerTop` still lives in
> `thermo-convective-bg.ts`; `route-map/metrics.ts:59` still SCT=0.3/BKN=0.7).

### 4.1 Zone interpolation can bridge data gaps (icing/CAT bands)

`zone-matching.ts:34-98` matches zones between adjacent route points by any
altitude overlap and tapers unmatched zones to the segment midpoint. Two
consequences: (a) a single-point zone is drawn extending half-way to both
neighbors (~±0.5 segment ≈ up to ~10–15 nm of painted hazard that no data
supports — conservative, acceptable); (b) two *different* decks that merely
overlap in altitude are smoothly merged, hiding a real gap a pilot might use.
With `maxRisk()` applied to matched pairs the result is conservative, but the
smoothness communicates more spatial confidence than ~20-point sampling
warrants. Suggestion: render interpolated spans at reduced opacity or with a
dotted edge between samples — "sampled here, inferred between".

### 4.2 "No data" vs "clear sky" is invisible at the point of use

The backend is rigorous about `None` (no native source) vs `[]` (clear
forecast) for NWP cloud layers, and `data-extract.ts:425-468` propagates it —
but on the canvas both render as *nothing*, and the tooltip simply omits the
row. Same for icing (a model with no CLW shows the same blank as a model
forecasting no icing) and for omega-less models on the CAT/vertical-motion
layers. For a tool whose stated goal is exposing uncertainty, this is the
biggest visualization gap: recommend a per-layer "no data from this model"
hatch/banner (the compare mode would benefit most — a blank ECMWF panel outside
GRIB coverage currently reads as "clear").

### 4.3 Client-side convective tower-top heuristics duplicate backend logic

`thermo-convective-bg.ts:31-56` re-derives tower tops (freezing+2000 ft for
low risk, −20 °C/−10 °C for moderate+, base+4000 fallback) when EL−LFC <
3000 ft. The backend already owns cloud-top uncertainty and EL semantics
(meteorology-decisions §6 carefully restricts when EL may be used as a top!).
A client-side EL fallback hierarchy can disagree with those rules — e.g. §6
decided a CIN-capped EL must *not* be drawn as a realized top, but the client
fallback can still paint a tall tower from temperature levels. Move tower
geometry server-side (it's already computed for NWP convective) so the §6
realized/potential gate applies to what's drawn.

### 4.4 Smaller items

- **Inversion opacity saturates at 3 °C** (`theme.ts:276`): a 6 °C inversion
  (strong wave/rotor and LLWS signal) looks identical to 3 °C. Extend the ramp
  (or label strength in the tooltip).
- **DD (gray) vs NWP (blue) cloud palettes**: deliberate and good for source
  identity, but the *brightness/opacity encodings differ in meaning* (DD: DD
  magnitude; NWP: cover %), so side-by-side visual comparison — the main use of
  the toggle — is apples-to-oranges. Consider a shared opacity=coverage
  convention with hue carrying the source.
- **Route-map `cloudAtAlt` weights** SCT=0.3/BKN=0.7 (`route-map/metrics.ts:59`)
  vs METAR band midpoints (SCT≈0.44, BKN≈0.75) — cosmetic, but trivially
  aligned.
- **Route-graph ceiling cap at 5000 ft AGL**: sensible chart scale, but a
  ceiling that disappears at >5000 reads as "no data" — same missing-vs-benign
  ambiguity as 4.2; an explicit ">5000" affordance would resolve it.
- **Temp-at-level extrapolation at 2 °C/1000 ft** (`route-map/metrics.ts:79-106`)
  — fine (≈ISA); just keep it display-only as it is now.

---

## 5. Suggested additions (value for pilot decision)

Ordered by estimated decision value per implementation effort. The first three
fill genuine hazard gaps; the rest are refinements.

> **Implementation status (2026-06-20, extended 2026-08-15):** §5.1, §5.2, and
> §5.3 have all been built — `advisories/freezing_precip.py`,
> `advisories/llws.py`, and `advisories/density_altitude.py` (all
> auto-registered via the registry's pkgutil module-walk; see
> `registry.ENTRY_ORDER` for where each lands in the catalog).
> **§5.6 is also BUILT** — decisions §11(d) gave `mountain_wind.py`
> wave-signature corroboration (ridge-top inversion in a −1000/+2000 ft band,
> or `VerticalMotionClass.OSCILLATING`) and deliberately *rejected* the
> cross-ridge component this review suggested, as false precision on a
> perpendicular-crossing assumption. Two §5.7 endorsements also landed:
> ECMWF `kx`/`totalx` are decoded (`fetch/grib/decode.py:219-220` →
> `nwp_total_totals`) and Open-Meteo `showers` is consumed as convective-precip
> corroboration (`advisories/convective_character.py`).
> **Still open: §5.4 (carb icing), §5.5 (radiation fog at ETA), and §5.7's
> `cloud_cover_low_pct` third ceiling signal + surface precip-phase strip** —
> no carb/fog code exists and the cross-section has no precip-phase layer.

### 5.1 Freezing precipitation / SLD route advisory (highest value) — BUILT

The deadliest GA icing scenario — FZRA/FZDZ below cloud — is currently
**computed but never advised**: `precipitation.py` detects warm noses, ice
pellets, freezing rain; SLD zones exist (warm-nose mechanism active); ECMWF
delivers `fzra` and `ptype` (unprocessed, per analysis-metrics §1.3). There is
no evaluator that turns any of this into a RED. A `FreezingPrecipEvaluator`
(any FZRA/FZDZ phase along route or at airports → RED; warm-nose-over-subzero
surface profile → AMBER even without active precip) closes the gap and also
fixes §2.3 (the descend advisory can consult the same predicate). Below-cloud
SLD is also the one icing hazard the "icing only inside cloud bands" invariant
structurally cannot represent — worth a dedicated cross-section marker (e.g.
hatched band from cloud base to the sub-zero ground).

### 5.2 LLWS / approach-shear advisory — BUILT (`advisories/llws.py`)

`bulk_shear_0_1km_kt` is computed for every sounding and consumed by nothing.
Combine: 0–1 km shear > 20 kt, gust factor, and a surface-based inversion with
strong flow above (nocturnal LLJ — `inversion_layers` + wind at the first
levels) into a departure/arrival wind-shear advisory. All inputs exist; this is
an evaluator-only change and addresses a top-3 GA approach hazard that nothing
in the current 14 covers.

### 5.3 Density altitude / performance advisory — BUILT (`advisories/density_altitude.py`)

T, dewpoint, QNH, and field elevation are all in `airport_conditions`. Compute
DA at departure/arrival at ETD/ETA; AMBER/RED on user-tunable thresholds
(absolute DA and DA−elevation delta). Summer + mountain strips is a classic GA
accident category; cheap to add and a natural fit for the existing airport
category.

### 5.4 Carburetor icing advisory

From surface T/Td: the standard carb-icing envelope (serious-at-cruise/descent
power for roughly T −5…+25 °C with small DD). Per-aircraft relevance gate (the
aircraft registry exists — piston/carburetted flag). Trivial computation, high
familiarity value for the fleet most exposed.

### 5.5 Radiation fog risk at arrival ETA

For evening/early-morning arrivals: clear/low NWP cloud + surface DD < 2–3 °C
and falling + wind < 5 kt + ETA within a window around/after sunset (the sun
pipeline already provides timing). Visibility forecasts only exist for 3 of 7
models, so a physics proxy materially improves D-1/D-0 evening-arrival calls —
currently FlightCategory just goes quiet for the vis-less models
(GREEN-by-absence, §3.6).

### 5.6 Mountain-wave upgrade for MountainWind — BUILT (decisions §11(d))

`mountain_wind.py` grades wind *speed* near terrain only. The classical wave
criteria are nearly free here: cross-ridge component (route track and terrain
gradient are known), a stable layer/inversion near ridge top
(`inversion_layers`), and the already-computed-but-unused
`VerticalMotionClass.OSCILLATING`. Speed-only misses the stability/direction
discrimination that separates "windy ridge" from "rotor day", and OSCILLATING
currently feeds no advisory at all.

### 5.7 Endorsements of items already on internal lists

- **ECMWF `kx`/`totalx`/`cp` decode** (analysis-metrics §1.3 note) — the
  model-native convective corroboration the cross-check tier wants.
- **Open-Meteo `showers`** as convective-precip corroboration
  (meteorology-decisions §4 deferred list).
- **`cloud_cover_low_pct` as an explicit third ceiling signal**
  (meteorology-decisions §1 enhancement) — prioritize; ceiling is the
  highest-leverage single metric in the product.
- **Surface precip-phase strip on the cross-section** (rain/snow/FZRA bands
  along the distance axis below terrain): the phase assessment exists per
  point; GRAMET users expect it; pairs with 5.1.

### 5.8 Not recommended (for the record)

- Tropopause/jet products: above GA levels for this fleet.
- Ensemble-spread shading on the cross-section: high cost, and the multi-model
  compare mode already communicates spread more honestly than a single-model
  ensemble plume would.

---

## 6. Summary of recommended actions

Status column verified 2026-08-15. "open" means the code still reads as the
review described it — re-check before assuming, but don't re-derive the finding.

| # | Action | Type | Where | Status |
|---|--------|------|-------|--------|
| 1 | Fix `_descend_below_icing` min→max + terrain floor + warm-nose guard | bug | `sounding/advisories.py` | **done** (§2.1–2.3) |
| 2 | Audit E-Shear units vs CloudPath calibration; pin with test | bug | `sounding/e_shear.py` | **done** (+ `tests/test_e_shear.py`) |
| 3 | IENG convective term: pass real level vapor density | bug | `sounding/icing.py:561` | **done** |
| 4 | Treat N²<0 + shear as CAT, not missing | bug | `vertical_motion.py:163` | **done** (storage; classification unreviewed) |
| 5 | UNAVAILABLE (not GREEN) when a model structurally lacks the input | aggregation | `_helpers.below_coverage`, `turbulence.py` | **done** (#391/#393) |
| 6 | Renormalize SFIP weights for `_no_vv` | calibration | `sfip.py:37-42` | open |
| 7 | Decide/document the stratiform-MODERATE Ogimet ceiling | decision-log | `icing.py:140` | open |
| 8 | Gust-speed crosswind grading | advisory | `airport_wind.py`, `RunwayWind` | open |
| 9 | Feasibility of climb-above vs theoretical max top | advisory | `sounding/advisories.py:733` | open |
| 10 | "No data" hatching per layer on cross-section | viz | `web/ts/visualization` | mostly addressed differently (see §4 status) |
| 11 | Server-side convective tower geometry (retire client fallback) | viz | `thermo-convective-bg.ts` | open |
| 12 | New: freezing-precip advisory (+ cross-section marker) | feature | §5.1 | **built** (evaluator; no cross-section marker) |
| 13 | New: LLWS advisory from 0–1 km shear + inversions | feature | §5.2 | **built** |
| 14 | New: density-altitude advisory | feature | §5.3 | **built** |
| 15 | New: carb-icing, fog-at-ETA, wave-criteria upgrades | feature | §5.4–5.6 | wave **built** (§11(d)); carb + fog open |
