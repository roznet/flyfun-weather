# Meteorological Pipeline Review — GRIB Interpretation, Variable Semantics, Advisory Consumption

**Date:** 2026-08-08
**Scope:** Full-pipeline audit: GRIB2 decode per model (GFS / HRRR / ICON-EU / ICON-D2 /
ECMWF), Open-Meteo ingestion and the enrichment merge seam, the sounding/derived-analysis
layer (thermodynamics, four icing methods, cloud methods, convective tiers, CAT/E-Shear,
precipitation), and the 22 advisory evaluators — with a closing comparison against
`meteorology-decisions.md`, `meteorology-approach-review-2026-06.md`,
`cross-cutting-review.md` and the known-issues lists.
**Method:** Four parallel independent code reviews (one per pipeline stage), each reading
code first-hand without treating design docs as ground truth; the highest-impact claims
were then re-verified line-by-line before inclusion. Same convention as the other
point-in-time audits: trust the reasoning, re-verify line numbers against current code.

Verification tags: **VERIFIED** = re-checked directly in code during synthesis;
**REPORTED** = reviewer-verified with quoted code, not independently re-checked;
**SUSPECT** = mechanism confirmed in code, live impact needs a data-level check.

---

## 1. Overall assessment

The core meteorology holds up well — better than most review passes find. Everything
checked in the formula layer is right: MetPy call discipline (argument order, units,
sort order, parcel handling), the fast wet-bulb integrator (exactly MetPy's
pseudoadiabat), hypsometric reconstruction with virtual temperature (§20), the GFS
de-averaging algebra (§3/#481 — verified against live NCEP `.idx` files), omega sign
conventions end-to-end, CIN sign discipline per provider, the de-accumulation state
machines (cadence-aware, gap-safe), HRRR Lambert projection and wind rotation
(verified analytically), the D2 explicit-convection v2 table with LPI-primary
corroborators and the bright-band gate, the §22 single-source convective grade, and the
#442 dd_trigger mechanics. The None-vs-zero-vs-clear discipline, where it is applied,
is applied correctly.

The genuine findings live almost entirely at **seams**: datum conventions (AGL vs MSL,
ISA vs geopotential), unit conventions that differ per provider landing in one shared
field, missing-data semantics in the few evaluators that predate the #391 hardening,
and known fixes applied to one model path but not its siblings. A handful are in the
false-green direction and worth prompt attention; most are consistency debt.

---

## 2. Safety-direction findings (false-green risk)

### 2.1 FIKI advisory: mid-route SEVERE icing (and SLD) at cruise can grade GREEN — VERIFIED

`analysis/advisories/fiki_icing.py:233-243, 278-292, 343-391`. The SLD / SEVERE /
transit-thickness RED logic aggregates only over points within `proximity_nm` (50 nm)
of departure and arrival (`dist <= proximity_nm` / `dist >= total - proximity_nm`).
Mid-route icing reaches the route grade **only** through the clear-cruise percentage,
and with `clear_cruise_amber_pct=80` (default), SEVERE icing sitting *at cruise* over
≤20 % of the route (≈3 of 20 points, ~90 nm on a 600 nm leg) yields **GREEN**. Severe
icing exceeds FIKI certification anywhere, not only in the terminal corridors. For a
FIKI-configured profile this is the only enabled icing-severity advisory (the setup
interview disables `icing_escape`), so the briefing can show no icing concern at all.

### 2.2 Icing *intensity* is priced nowhere in the default advisory chain — REPORTED

- `icing_escape.py:371-383`: escapable icing below 20 % coverage → GREEN regardless of
  LIGHT vs SEVERE (zone `risk` is read only by the advice-only mitigation solver).
- `ifr_feasibility.py:196-201`: the icing axis is pure geometry
  (`min_icing_clearance < buffer`), intensity-blind.
- `fiki_icing.py` is the only evaluator reading `IcingRisk` at all — and only in the
  corridors (2.1).

"Moderate icing" therefore means three different things across the three consumers
(thickness+severity / existence+escape / existence+clearance), and forecast
moderate-to-severe icing at cruise over ~15 % of a route with a viable warm escape can
grade GREEN across the entire default set.

### 2.3 SLD aloft is computed but consumed by nothing — REPORTED (deliberate dormancy, real gap)

`_helpers.py:388-403` documents it: `IcingZone.sld_risk` is populated by no producer,
and `sounding.sld_zones` (the actual warm-nose SLD detector output) is read by no
evaluator. Consequently `fiki_icing`'s `reason="sld"` RED and `icing_escape`'s SLD hard
wall are dead paths on real data. §9 (`freezing_precip`) covers the *surface-phase*
FZRA/PL case; in-cloud SLD aloft — which the 2026-06 review §5.1 called out as the one
hazard the "icing only inside cloud bands" invariant cannot represent — still has no
surfacing path.

### 2.4 ECMWF `hcct` is AGL but is consumed as AMSL by the convective tier — VERIFIED

`fetch/grib/decode.py:177-190, 2940-2947`: `hcct → convective_cloud_top_m` is metres
**above ground**, explicitly not converted ("ceil/cbh/hcct are AGL too and are NOT
converted here"). `analysis/sounding/convective.py:491-529` consumes
`convective_top_ft` as AMSL: `_risk_from_conv_top` divides by 100 to get a **flight
level** against `_CONV_TOP_FL_THRESHOLDS` (FL380/280/200/120), the cross-check
active/quiet bands key on FL200/FL120, the tops-below-cruise filter compares it to
cruise MSL, and the `nwp_lcl_top` envelope pairs an MSL-ish LCL base with an AGL top.
GFS (pressure→ISA≈MSL) and ICON (`htop_con`, MSL per DWD) are consistent; ECMWF is the
live divergence. Over 1,500 m terrain a real FL240 tower reads ≈FL190 → MODERATE
instead of HIGH — **under-warning that grows with terrain elevation**, i.e. worst over
the Alps. §21 deliberately scoped `ceil`/`cbh`/`hcct` out of the deg0l fix on the
grounds that ceiling/cloud-base are "conventionally AGL in aviation" — a sound argument
for ceilings that does not extend to a convective tower top graded on MSL flight-level
thresholds. This is the sharpest new finding of the review.

### 2.5 Mixed ceiling/base datum in `NWPCloudDiagnostics` remains live — VERIFIED (known as #441 f3)

Same root as 2.4: `ceiling_ft` / `low.base_ft` are MSL for GFS/HRRR/ICON and AGL for
ECMWF. The main consumers branch correctly (`_nwp_ceiling_is_agl` in
airport_conditions/airport_consensus), but `tasks/standalone_grib.py:140/230` writes
`diag.ceiling_ft` into `nwp_ceiling_ft` uniformly (verification pipeline), and every
new consumer is one forgotten branch from a full-terrain-elevation offset. The decode
comment acknowledges the follow-up; until it lands, the invariant "one field, one
datum" that §21 established for the freezing level does not hold for the cloud heights.

### 2.6 Convective GREEN-by-absence: missing CAPE is an assessed quiet NONE, and the owner has no coverage guard — VERIFIED (mechanism)

`analysis/sounding/convective.py:119-120`: `if cape is None: return
ConvectiveRisk.NONE` — a model whose CAPE could not be computed produces a real NONE,
indistinguishable from "assessed and quiet". `convective_grading.py` counts
`conv is None` points into `total` and ribbons them GREEN, and `grade_convective_model`
has **no** `below_coverage` check — the thin-coverage guard exists only in the IFR
*consumer* (`ifr_feasibility.py:464-465`), so the convective card can show GREEN while
the IFR composite abstains on identical data. Under MAJORITY, structurally-quiet
models are full votes. This is the residual instance of the 2026-06 review §3.6
("GREEN-by-absence vs UNAVAILABLE is the single most important aggregation fix") that
the #391 hardening did not reach; §22's open item about scheme-less models (UKMO/MF
thermo-fallback REDs) is the same seam from the other direction.

### 2.7 Missing terrain elevation silently becomes 0 ft — VERIFIED

`fetch/elevation.py:116`: `elevation_ft = ... if elev_m is not None else 0`. SRTM voids
cluster on steep terrain — exactly where terrain matters — and if the Open-Meteo
fallback also fails (any network error nulls the whole batch, `elevation.py:60`), the
point is recorded as sea level with no degraded marker. `max_elevation_ft` under-reads
and the §8a descend-escape terrain-feasibility guard (plumbed specifically so escapes
don't go below terrain) can be defeated by the very data gap it was built to survive.

### 2.8 Live-horizon skip compares against midnight of the arrival date — VERIFIED

`tasks/fetch.py:319-350`: `end_date_dt` is `fromisoformat("YYYY-MM-DD")` — 00:00 of
the arrival day, up to ~24 h before the actual window end (which the same function
computes three lines earlier). A model whose `data_end` covers 06:00 but not 14:00
passes the check, fetches structurally-valid nulls for the tail, ships in the pack,
and silently drops out of soundings/divergence at later route points with no
`MODEL_SKIPPED_HORIZON` diagnostic. Fix: compare against
`departure + ceil(duration)` (the ECMWF path's `flight_end`).

### 2.9 E-Shear model set is taken from route point 0 only — VERIFIED

`tasks/analyze.py:408`: `for model in analyses[0].sounding:`. A model whose sounding
failed at the origin alone loses E-Shear for the **entire route**, silently — CAT
absence reading as smooth air, the #391 failure class.

### 2.10 `nwp_cloud_cover_at_altitude` pairs diagnostic geometry with bulk ICAO percentages, and reads missing cover as 0 % — VERIFIED

`analysis/sounding/icing_common.py:120-145`: inside the diagnostics branch the cover
is `bulk_pct or 0.0` — the Open-Meteo ICAO-slab percentage paired with GFS/HRRR NCEP
**pressure-band** geometry (the mismatched-slice pairing §23 records being fixed twice
elsewhere), never `diag_layer.cover_pct`; and an absent bulk percentage becomes 0 %,
after which Ogimet-NWP and IENG drop the level entirely (`icing.py:469,538`). Effect:
icing understated or zones silently missing whenever diagnostics are present but bulk
covers are not, and mis-banded percentages when NCEP bands disagree with ICAO slabs.

### 2.11 Sentinel values can be bilinearly blended before the sentinel guard — SUSPECT

`decode.py`: spatial interpolation runs on raw grids; the 9999-sentinel screen runs on
the **blended** value (`_agl_m`, `decode.py:2979-2983`). A route point whose four
ECMWF corners mix sentinel and real values yields e.g. `0.5·9999 + 0.5·500 ≈ 5250 m` —
passes the ≥9998 guard and becomes a phantom 17,000 ft ceiling/tower. Worst case is
`_normalize_model_cin` (`decode.py:1628-1635`): a blended `mlcin100` of ~5,000 passes
`drop_at_or_above=9998`, is negated to −5,000 J/kg, and trips the CIN-suppression gate
next to real convection (under-warn at the cloud edge). The D2 path shows the correct
pattern (NaN the sentinel **before** reduction, `decode.py:2098-2100`). Impact hinges
on whether ECMWF/DWD encode these as literal values (repo comments say yes: "9999 = no
cloud sentinel", ICON "−999.9") or as bitmap-missing (NaN → safely dropped). One dump
of an a1 `ceil` field over a clear region settles it; a physically-bounded guard
(|CIN| > 1000 → None; heights within a few hundred m of the sentinel → None) closes it
regardless.

---

## 3. Correctness & consistency findings

### 3.1 `round()` ties-to-even drops every other GFS/ICON forecast hour for :30 departures — VERIFIED

`fetch/grib/grib_fetch.py:154-162` (`_snap_to_gfs_grid`) and
`icon_eu_fetch.py` (`_snap_to_icon_eu_grid`) sample `departure + h` and `round()`. For
a :30 departure every offset is X.5 and Python's banker's rounding collapses pairs —
`round(9.5)=10, round(10.5)=10, round(11.5)=12…` — so odd forecast hours are never
fetched and enrichment silently halves to 2-hourly for one of the most common GA
departure slots. The repo **already diagnosed exactly this** and fixed it for HRRR
only: `hrrr_fetch.py:365-371` — "Deliberately NOT the sample-and-round shape the
GFS/ICON helpers use: `round()` is ties-to-even, so a :30 departure … silently drops
every other forecast hour (PR #508 review)." Back-port the contiguous
`floor(dep)..ceil(end)` range. Knock-on: ICON-D2 explicit convection needs file
f(H−1)'s 15-min windows for hour H's echo top, so missing odd hours degrade
`echo_top_complete` across most of the window.

### 3.2 Parcel thermodynamics can launch from below-ground levels; the 2 m surface obs are dead code — VERIFIED (dead fields) / REPORTED (impact)

`analysis/sounding/prepare.py:39-41,147-164`: `PreparedProfile.surface_pressure/
temperature/dewpoint` are populated from `HourlyForecast` and **never read anywhere**.
All parcel quantities launch from `p[0]` — the highest-pressure level of the list —
and nothing clips levels below the model surface. Open-Meteo and GFS/ECMWF/HRRR
pressure-level products deliver extrapolated sub-surface values (1000/975/950 hPa
under 850 hPa terrain), so on elevated terrain SB-CAPE/CIN, LCL/LFC/LI, DD cloud
layers, inversions and icing all operate partly on fictitious sub-terrain levels —
extrapolated sub-ground RH is frequently near-saturated, so phantom below-ground
"cloud" is a plausible output. ICON is largely immune (terrain-following model levels
replace the list); GFS/ECMWF/HRRR are exposed. Either clip at
`hourly.surface_pressure_hpa` or launch the parcel from the stored (currently unused)
surface observation.

### 3.3 Two snow conventions in one field, and a rain fallback that can go negative — VERIFIED

- `decode.py:3128-3130`: ECMWF `sf` (m water-equivalent) × 1000 → `snowfall_cm`, i.e. a
  10:1 snow:water convention; Open-Meteo's `snowfall` is cm of snow at ~7:1. One
  `HourlyForecast.snowfall_cm` series thus mixes conventions across the GRIB window
  boundary, and cross-model snow divergence (thresholds 0.5/2.0 cm) carries a uniform
  ~43 % ECMWF inflation — spurious MODERATE/POOR agreement on snow days.
- `analysis/sounding/precipitation.py:90-92`: the `rain_mm` fallback is
  `precip_mm − snowfall_cm × 10` — treating snow **depth** as liquid equivalent
  (~7× over-subtraction on OM data), unclamped: 2 mm precip + 1 cm snow →
  `rain_mm = −8`.

### 3.4 ISA pressure-altitude and geopotential height are mixed within one analysis — REPORTED

LCL/LFC/EL altitudes are ISA conversions of pressures
(`thermodynamics.py:87-88,104-105,114-115`) while level/cloud/freezing altitudes use
geopotential height when available; the two are compared directly (ceiling LCL floor
`sounding/__init__.py:63-68`, cloud-top uncertainty EL-vs-top `clouds.py:1039-1044`,
convective envelopes). GFS band geometry (`_pa_to_ft`, `decode.py:1471-1475`) and ICON
CLC-derived bands are likewise ISA. In a deep low or cold column the offset is
300–1,000 ft — and the repo itself refuses ISA for D2 echo tops for exactly this
reason (`_echo_top_pa_to_ft`: "a metre-datum column and ISA disagree by hundreds of
feet"). §20 fixed the ICON *anchor* datum; this is the remaining intra-analysis
instance of the same class.

### 3.5 The §8c negative-Ri fix broke the EDR calibration's premise — REPORTED

`edr.py:33-39` asserts "`compute_stability_indicators` only assigns Ri when N² ≥ 0" —
no longer true since §8c deliberately stores negative Ri
(`vertical_motion.py:118-123`). `richardson_to_d` maps every statically-unstable layer
to `1/max(Ri, 0.01) = 100` (the diagnostic ceiling), inflating the lognormal
moment-matching that calibrates `a, b` — on a climatology (Sharman & Pearson) that
excludes the convective regime. §8c's own note said "verify how a negative Ri then
classifies downstream" — this is that unfinished verification. Exclude Ri < 0 from the
EDR path (the CAT classifier already handles it separately).

### 3.6 `icing_severity_enhance` is a silent no-op — VERIFIED

The preference is threaded end-to-end (`api/preferences.py` → `pipeline.py:83` →
`tasks/analyze.py` → `analyze_sounding(icing_severity_enhance=…)`,
`sounding/__init__.py:532`) and **never consumed**; `_enhance_severity`
(`icing.py:184`) has no callers. Users enabling "RH/PW icing severity upgrades" get
identical output. This also connects to the 2026-06 review §3.1: the Ogimet stratiform
index is arithmetically capped at 50 (`raw/2` with layered max 100) against
`_INDEX_SEVERE = 80`, so **pure stratiform icing can never reach SEVERE in any
Ogimet-family method** — and the documented mitigation path (`_enhance_severity`) is
the disconnected function. Either wire the flag or remove it and log the ceiling as a
deliberate decision (per §3.1's request, still unanswered).

### 3.7 Regime classification pairs effective (MU) CAPE with surface-based CIN — REPORTED

`convective.py:411-413`: `classify_regime(_effective_cape(indices),
indices.cin_surface_jkg)`. Elevated MU-CAPE ≥ 800 above a stable nocturnal/marine
boundary layer reads LOADED_GUN off an SB "cap" the elevated parcel never feels;
`most_unstable_cape_cin`'s own CIN is computed and discarded
(`thermodynamics.py:133`). Partially mitigated by the `elevated_convection` flag, but
the ω₇₀₀ cap-erosion narrative is being applied to the wrong parcel's cap.

### 3.8 Smaller consistency items — REPORTED

- **DD gate vs DD attenuation disagree about "in cloud":** attenuation is 0 at
  DD ≥ 2.0 °C (`icing.py:35-44`) while the cloud gate admits DD < 3.0 °C — every SCT
  deck (DD 2–3) passes the gate and zeroes out in Ogimet-DD, while SFIP-proxy still
  scores it. Structural divergence between the headline methods.
- **NWP coverage 50 % → BKN** (`clouds.py:277-282`): METAR convention puts BKN at
  5/8 (62.5 %); 50–62 % decks become ceilings — a systematic IFR-leaning bias (the
  comment "(5-6 oktas)" mislabels its own threshold).
- **SFIP-full vs glaciation floor:** `sfip.py:240-241` hard-gates measured CLW ≤ 0 →
  NONE while `icing_common.glaciation_factor` floors the same case at 0.8 above
  −10 °C because "GFS often reports CLW=0 at grid scale even when sub-grid SLW
  exists" — same input, same physics argument, opposite outcomes. (Also:
  `glaciation_factor` returns 0.0 when CLW=ICMR=0, so its warm floor never applies in
  exactly the case its own comment describes.)
- **vfr_feasibility vs vmc_cruise disagree on the same layer:** 100 % BKN at cruise →
  vfr RED (`imc_pct ≥ 30`) but vmc_cruise AMBER (RED requires OVC ≥ 50 %); and
  cruise IMC below 15 % of route → GREEN "minor clearance issues", which for a
  VFR-only pilot is a hard stop, not minor.
- **altitude_table:** candidate altitudes start at 2,000 ft with no terrain floor
  (`altitude_table.py:151`) — `best_below_cruise` can recommend an altitude below the
  Alps (below-ground air is hazard-free by construction); and UNAVAILABLE rows score
  as well as GREEN in `_find_best`, rewarding data absence.
- **`run_alt_from_pack` drops `ctx.sun`** (`tasks/advise.py:1009-1026`) — the sunset
  check vanishes precisely in the departure-time-shifting flow where it matters most.
- **Wind at cruise snaps to nearest level with no distance guard**
  (`analysis/wind.py:41-49`): ECMWF's Open-Meteo table has nothing between 850 and
  700 hPa, so an 8,000 ft cruise reads ~9,900 ft wind on non-GRIB hours while GFS
  reads 750 hPa — part of the cross-model wind "divergence" at cruise is a level-table
  artifact.
- **Bulk shear endpoints are MSL-referenced** (`thermodynamics.py:250-263`): over
  1,500 m terrain "0–6 km shear" is surface-to-4.5-km-AGL, and `argmin` accepts a
  nearest level arbitrarily far away on truncated profiles.
- **CAT/E-Shear layer geometry offset:** Ri for pair (i, i+1) is stored on i+1 and
  layer base/top use qualifying levels' own altitudes — bands sit ~one level spacing
  high.
- **ICON W on half levels paired with full-level pressure by level number** —
  SUSPECT: w displaced ~half a layer (~100–300 m); confirm against DWD HHL.
- **Spatial interp:** `_lerp_optional`'s one-sided fallback can assemble layer
  geometry that never co-existed (base from one neighbor, top from the other); hour
  pairing is by list index, not timestamp (safe today, undetected if it ever breaks).
- **Forward-fill past the last GRIB anchor** extends to the end of the fetched series
  (up to ~24–30 h past the window on cross-midnight fetches) with no per-hour
  provenance flag; off-window consumers can't tell filled from fresh.
- **Sampling gaps:** `at_time` is unbounded nearest-hour (a shifted departure beyond
  the horizon silently grades against the last stored hour); mountain ridges midway
  between ~20–30 nm route points are never evaluated; LLWS `continue`s an airport with
  no data and can grade GREEN off the other end alone.
- **Tunables:** `severe_is_red` lets a user switch off "severe icing → RED" (not a
  personal-minimum axis); several amber/red threshold pairs have no cross-validation
  (`cloud_top` hard-codes `red_pct=60` under a tunable amber; convective
  `affected_pct_red` can be set below amber) — all fail toward red, but produce
  unexplainable grades.
- **Cache/API hygiene:** GFS cloud-diag cache blob key is unversioned (ICON's was
  bumped to `_V2` for exactly this); the dead `bracket_forecast_hours` helpers return
  nonexistent fhours at cadence boundaries (f120→121); cfgrib `unknown` → ice mixing
  ratio mapping for HRRR has no decode-time parameter assertion.

### 3.9 Doc drift found (docs asserting things the code no longer does) — REPORTED

- `decode.py:2926-2929` claims "GFS fills [freezing_level_ft] from HGT@0degC (MSL)" —
  no such fetch/decode exists; the field is ECMWF-only in this layer. (Incidentally:
  `HGT:0C isotherm` *is* in the live GFS `.idx` — a native GFS freezing level is
  available for free.)
- `time-alignment-audit.md`: the "GFS and ICON-EU share one set of cross-sections"
  and "cross-midnight hours silently skipped" sections are both stale (sections are
  per-model; `run_fetch` extends `end_date` across midnight).
- `fetch.md` step 7 / Key Choices claim GRIB "overrides Open-Meteo cloud cover" —
  contradicted by its own later paragraph and by code (`attach_nwp_diagnostics` never
  touches `cloud_cover_*_pct`).
- `spatial_interpolation.py` header says the time axis forward-fills — stale vs
  fill.py's midpoint/linear modes.
- `registry.py:262-267`, `fronts.py:354-356`, `sun.py:132-135` comments claim
  all-UNAVAILABLE collapses to GREEN; `AdvisoryStatus.worst/majority` correctly return
  UNAVAILABLE — the comments describe a prior bug as current and invite a wrong "fix".
- `cross-cutting-review.md` still lists finding #4 (no `icing/convective
  _method_effective`) as open; #408 implemented both (per `analysis.md`).
- `edr.py:33-39` premise comment (see 3.5).

---

## 4. Verified sound (checked, don't re-check)

- **MetPy usage** across `thermodynamics.py`: sort order, pint units, argument order
  for all 18 calls; parcel Kelvin handling; MetPy's internal virtual-T correction in
  `cape_cin` making an explicit one unnecessary.
- **Wet-bulb fast path** = MetPy pseudoadiabat integrand exactly; Magnus constants
  consistent across all four sites (open_meteo, fill, decode, Bolton in prepare).
- **E-Shear scale factors** exact post-§8b (`_VWS_SCALE = MS_TO_KT·304.8`,
  `_HWS_SCALE = 360000`); head/crosswind trig; circular wind statistics.
- **GRIB conventions:** GFS `.idx` names (`CLMR`/`ICMR`, HRRR `CLMR`+`CIMIXR`)
  verified against the live bucket; averaged-vs-instantaneous pair selection matches
  live idx; GFS window-length formula and the #481 disjoint-window de-averaging
  algebra verified; longitude seam handling (GFS cyclic wrap, D2 0–360→±180
  normalization before the descending-axis flip); HRRR Lambert bilinear-in-projected-
  space (exact on a regular projected grid) and grid→earth wind rotation.
- **Omega/CIN conventions** consistent end-to-end (ICON −ρgw; providers' positive CIN
  negated; HRRR native-negative correctly left alone, big-positive → None).
- **De-accumulation** (ECMWF tp/sf/cp, ICON crr): actual-window differencing,
  predecessor reset on gaps, `max(0,·)`, mm-vs-m distinction, None ≠ 0.0 through the
  firing gate.
- **§21 deg0l normalization** implemented as documented (model orography from the gh
  column, drop-not-passthrough, negative deg0l retained).
- **§19/§23 machinery:** D2 explicit decode (stepRange in minutes, sentinel-NaN before
  reduction, corridor-boundary completeness), the v2 LPI-primary table and bright-band
  gate; the HRRR condensate cloud-envelope builder with NCEP-band provenance gating.
- **Advisory layer:** `worst`/`majority` UNAVAILABLE semantics (never green, ties to
  worst); throwing evaluators become explicit UNAVAILABLE cards; #391 coverage guards
  demote only would-be-GREEN verdicts in eleven evaluators; §22 single-source
  convective + sanctioned deviations exactly as documented; #442 dd_trigger caps,
  red-coverage exclusion and MODERATE+ amber floor; freezing_precip's no-threshold RED
  and PL-proves-FZRA logic; approach_feasibility's blocker×softening cross product,
  circling exclusion from best-case minima, and coverage-gap abstention.
- **Time alignment:** date+hour matching, aware-UTC discipline, ECMWF cadence-
  independent step selection, GFS bracket-anchor guarantee for :XX departures
  (`_snap_to_gfs_grid_floor`), HRRR staged validate-commit atomicity, ICON all-or-
  nothing hours with partial-column protection.

---

## 5. Comparison against the decision log and prior audits

**Decisions verified implemented as written:** §3/#480/#481 (windowing +
de-averaging), §8 (all four review fixes), §9–§11 (the new evaluators), §14/§15/§16
convective phases, §17 (wet-bulb phase bands 0/1.3), §18+§22 (NWP-native grade,
single-source consumption), §19 v2 + #468, §20 (anchor + virtual T), §21 (deg0l), §23
(condensate envelope + geometry-gated cover branch). Nothing in this review
contradicts a *deliberate* decision; re-litigating §2 (Ogimet width), §3.4 (DD
coverage heuristic) or the MAJORITY default was avoided per the log's instructions.

**Known-open items this review confirms are still open:**
- Cross-cutting #2 — altitude advisories computed pre-resolution (still true:
  `compute_altitude_advisories` runs in the analysis stage on DD/Ogimet-DD defaults).
- Cross-cutting #3 — ICON convective transitions gated on `convective_cover_pct`.
- 2026-06 §3.2 — SFIP `_no_vv` weight renormalization (still 0.90 ceiling for MF/GEM).
- 2026-06 §3.6 — GREEN-by-absence: substantially fixed by #391/#389 in most
  evaluators, but the convective owner still has the gap (finding 2.6), and §22's
  scheme-less-model thermo-RED gap remains.
- 2026-06 §3.1 — stratiform-SEVERE ceiling: still undecided, now sharpened by the
  discovery that the suggested mitigation is disconnected (finding 3.6).
- 2026-06 §3.7 (short-route percentage dilution), §3.8 (gust-speed crosswind), §3.11
  (theoretical-top feasibility) — all still as described; 2.1/2.2 above are the
  icing-side instantiation of §3.7's warning.
- `analysis-metrics.md` §5 "Clear (cloud 100 %)" label; ECMWF sparse `ceil`
  (accepted); SLD coalescence disabled (known-issues) — unchanged.

**Where the docs are now wrong about the code:** see 3.9. The decision log itself is
accurate throughout — the drift is in the satellite docs and inline comments.

---

## 6. Priority summary (new items only)

| # | Action | Type | Where | Tag |
|---|--------|------|-------|-----|
| 1 | FIKI: grade SLD/SEVERE/thickness route-wide, not corridor-only (or add an intensity axis to `icing_escape`/IFR) | bug (false green) | `fiki_icing.py` | VERIFIED |
| 2 | Normalize ECMWF `hcct` AGL→MSL (model orography, as deg0l) before the FL-threshold tier | bug (under-warn over terrain) | `decode.py` / `convective.py` | VERIFIED |
| 3 | Back-port the HRRR contiguous-range window fix to GFS/ICON (`round()` ties-to-even) | bug (silent 2-hourly enrichment) | `grib_fetch.py`, `icon_eu_fetch.py` | VERIFIED |
| 4 | Convective: `cape=None` → UNAVAILABLE-style handling + `below_coverage` guard in `grade_convective_model` | aggregation (green-by-absence) | `convective.py`, `convective_grading.py` | VERIFIED |
| 5 | Horizon skip: compare `live_end` vs flight-window end, not arrival midnight | bug (null tails) | `tasks/fetch.py` | VERIFIED |
| 6 | Elevation void → None + diagnostic, never 0 ft | bug (terrain guard defeated) | `fetch/elevation.py` | VERIFIED |
| 7 | E-Shear: union of models across points, not `analyses[0]` | bug (silent CAT loss) | `tasks/analyze.py` | VERIFIED |
| 8 | Wire or remove `icing_severity_enhance`; decide the stratiform-SEVERE ceiling (§3.1) | product/decision | `icing.py`, `sounding/__init__.py` | VERIFIED |
| 9 | Clip profiles at the model surface (or launch parcel from the stored surface obs) | correctness (high terrain) | `prepare.py` | REPORTED |
| 10 | Unify snow conventions (7:1 vs 10:1) and fix/clamp the `rain_mm` fallback | correctness | `decode.py`, `precipitation.py` | VERIFIED |
| 11 | `nwp_cloud_cover_at_altitude`: use `diag_layer.cover_pct`, treat missing as None | correctness (icing understated) | `icing_common.py` | VERIFIED |
| 12 | Sentinel-mask before spatial interpolation (or physically-bounded guards); confirm literal-vs-bitmap with one a1 dump | bug (SUSPECT) | `decode.py` | SUSPECT |
| 13 | Exclude Ri < 0 from `richardson_to_d` / EDR calibration | calibration integrity | `edr.py` | REPORTED |
| 14 | Pair regime CIN with the MU parcel's own CIN | calibration | `convective.py`, `thermodynamics.py` | REPORTED |
| 15 | Terrain floor + UNAVAILABLE penalty in altitude-table best-pick; `sun` on the alt-departure context | advisory | `altitude_table.py`, `advise.py` | REPORTED |
| 16 | Doc-drift sweep (3.9 list) | docs | various | REPORTED |

Items 1–7 are the "new to worry about" core: each is a silent failure in the
safety-relevant direction or a silent data-quality loss on a common flight profile.
