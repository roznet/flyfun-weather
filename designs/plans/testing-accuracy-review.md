# Testing & Accuracy Review — Analysis Validation Tracker

**Started:** 2026-06-30
**Purpose:** Track the validation maturity of every meteorological / decision module in the
analysis code, and drive it from "regression-tested only" up to "confirmed against real-world
observation with a saved eval set." This is a living checklist — update the user-test columns as
cases are worked through.

The question this answers: *of the bespoke analysis code, how much is actually validated against
something external (a library, a published method, a regulation, an observation) vs. only asserting
its own output?*

---

## How to read this

Each module carries a four-state maturity ladder (the spine), plus metadata to make it actionable.

### The four states (the ladder)
| State | Meaning |
|---|---|
| **Reg** — regression tested | A test exists that pins current behaviour. Catches *changes*, not *being wrong from the start*. `✅` full · `🟡` partial / smoke · `❌` none |
| **Ext** — external-validated tested | An automated test asserts output against an **independent** oracle (library, published values, regulation, hand-computed). See *basis* below. |
| **UsrT** — confirmed from user test | A human checked the output against reality (a flight, an observation, expert judgement) on ≥1 real case. *(empty for now)* |
| **UsrT+Eval** — confirmed + eval set saved | Same as above, but the case(s) are captured in a durable eval corpus so it can be re-run / regression-guarded. *(empty for now)* |

### Ext basis — what kind of external check applies (the key addition)
Not every module *can* be unit-tested against an oracle. This tag records what's possible, so an
empty Ext cell isn't ambiguous:

| Basis tag | Meaning | Reachable via |
|---|---|---|
| `MetPy/lib` | Delegated to / checkable against a validated library | Ext (automatable now) |
| `paper` | A published method/values exist | Ext (automatable now — reproduce paper cases) |
| `reg/std` | A regulation or published standard defines the numbers | Ext (automatable now) |
| `obs-only` | No formula oracle; only real-world observation validates it (METAR/PIREP/radar) | UsrT / UsrT+Eval |
| `judgment` | App calibration / threshold — no external truth exists; only operational experience validates | UsrT / UsrT+Eval |
| `delegated` | Physics handed to a validated lib (right-by-construction) but no asserting test yet | partial-Ext |

### Ext cell symbols
`✅ vs <oracle>` automated oracle test exists · `🔵 delegated <lib>` right-by-construction, no value test ·
`📄 <ref>` published oracle exists, **ref saved, not yet validated** · `❌ unused` oracle exists but no test (see TODO) ·
`⚪ N/A` no automatable oracle (obs-only / judgment) — track via UsrT columns.

**Risk:** 🔴 high · 🟠 medium · 🟢 low. (Reflects blast radius × external-checkability × current coverage; frontal/Hewson H is tempered by being experimental + default-off.)

---

## Scoreboard (session findings, 2026-06-30)

Counts of the ~50 analysis modules audited this session:
- **Genuinely external-validated (Ext ✅):** the FAA/EASA reg modules, flight-category, wind trig,
  spatial interpolation, density-altitude formula, wet-bulb (MetPy oracle), and the documented
  boundary-tested advisory evaluators.
- **Delegated-but-no-value-test (🔵):** the MetPy thermodynamic core (CAPE/CIN/parcel/ω) and the
  solar primitive.
- **Regression/snapshot only (⚪/📄/❌):** the large majority of bespoke aviation logic — icing×4,
  SFIP, SLD, clouds, convective tiers, EDR, E-shear, and the entire frontal/Hewson stack.
- **UsrT / UsrT+Eval:** empty across the board — to be filled as you work through cases.

> ⚠️ **The snapshot trap:** SFIP, EDR, and E-Shear have *rich* test suites, but their expected
> numbers were **hand-derived from each module's own formula**. That proves code-matches-design,
> not design-matches-reality. They are scored Reg ✅ / Ext ❌|📄, not Ext ✅.

### Progress log

**2026-06-30 — Tier-1 bundle (committed).** Decisions confirmed by user: B1→delegate to MetPy,
B2→align icing % to 20/50, A1→verify EDR against the paper first.
- **B1** `prepare.py` dewpoint → now delegates to `mpcalc.dewpoint_from_relative_humidity`
  (Bolton 1980). Magnus discrepancy (Bug #1) resolved; Ext: ⚪→🔵 delegated.
- **B2** `ifr_feasibility.py` icing % unified to **20/50** via shared `_ICING_PCT_*_DEFAULT`
  constants + guard test (Bug #2 resolved).
- **A2** `e_shear.py` — exact-E + scale-factor oracle tests added. Ext: 🟡→✅. **The scale-factor
  assertion caught a real defect (Bug #7):** truncated knot constants (`0.51444` vs `1.94384`,
  not exact reciprocals) → replaced with exact `1852/3600` forms. `_HWS_SCALE` now exactly 360000.
- **A3** `thermodynamics.py` — `_find_temperature_crossing` and `_compute_bulk_shear` now
  value-tested against hand-computed oracles. Ext (those two helpers): ❌→✅. The bulk-shear test
  documents the nearest-level approximation (interpolate-to-exact-height logged as a TODO).
- **C1** Bug #4 resolved — extracted `compute_sounding_ceiling_ft`; `TestLCLFloor` now exercises
  production code instead of a copy.
- **A1** EDR — verification only **partial**: `20_45kft (-2.953, 0.602)` confirmed against
  Sharman & Pearson 2017; lower bands unverifiable (sources network-blocked). Test deferred
  pending the paper's Table 1 (see §2 TODO).
- Verified: full suite **3063 passed**, 1 pre-existing unrelated auth/OAuth env failure.

---

## 1. Thermodynamic core (`src/weatherbrief/analysis/sounding/`)

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `wet_bulb.py` | ✅ | ✅ vs MetPy `wet_bulb_temperature` (`MetPy/lib`) — 2,860-pt grid, \|Δ\|<0.001 | ☐ | ☐ | 🟢 | **Gold-standard oracle — the template to replicate.** Hand-rolled RK4 but rigorously checked. |
| `thermodynamics.py` | 🟡 | 🔵 delegated MetPy for indices (`delegated`); 3 hand-rolled helpers `❌ unused` | ☐ | ☐ | 🟠 | Every index is `mpcalc.*` (right-by-construction) but **no test asserts a computed value**. `_find_temperature_crossing`, `_compute_bulk_shear`, `_derive_dewpoint` untested. Blanket `try/except → None` masks bugs as missing data. |
| `vertical_motion.py` | ✅ | ❌ unused (`judgment`/analytic) | ☐ | ☐ | 🟠 | Tests feed hand-set Ri/ω and check bands+sign only; **N²/Richardson computation never value-tested.** Oracle = hand-computed analytic profile. |
| `prepare.py` | ❌ | ⚪ N/A (`judgment`/plumbing) | ☐ | ☐ | 🟠 | No dedicated test (input-construction layer everything trusts). **Magnus constant discrepancy — see Bugs #1.** |

**TODO (Ext required):**
- [ ] `thermodynamics.py`: oracle test re-computing indices vs `mpcalc` direct on the `_make_levels()` fixture; value-test `_find_temperature_crossing` + `_compute_bulk_shear`; replace blanket `try/except → None` with explicit handling.
- [ ] `vertical_motion.py`: hand-compute N² and Ri for a 2–3 level analytic profile (known θ gradient + shear), assert exact values.
- [ ] `prepare.py`: unit suite (<3 levels → None; descending-pressure sort; Magnus dewpoint value-check); reconcile Magnus constants with doc.

---

## 2. Bespoke sounding methods (`src/weatherbrief/analysis/sounding/`)

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `icing.py` (Ogimet DD/NWP/IENG) | 🟡 | 📄 Ogimet index + `obs-only` (PIREP) | ☐ | ☐ | 🔴 | "Ogimet" cited but **no primary ref in code**. Index thresholds 10/30/80 only edge-tested vs own constants. Zone-width PIREP calibration explicitly deferred (decisions §2). |
| `sfip.py` | 🟡 | 📄 Belo-Pereira 2015 & Morcrette 2019 (cited, **never checked against**) | ☐ | ☐ | 🔴 | Best hand-derived coverage of the set — but all vs the module's own formulas. **The cited papers ARE an available oracle, currently unused.** |
| `convective.py` | ✅ | ⚪ `obs-only` (lightning/radar/TS); tiers ESSL/ESTOFEX-calibrated | ☐ | ☐ | 🔴 | Most-documented module (decisions §4/§5/§14/§15/§16). Boundary-rigorous, but anchors are **prod-flight snapshots that record the code's own verdict**. Every §lists "real-world validation needed". |
| `icing_common.py` | 🟡 | ⚪ `obs-only`/literature (accretion physics) | ☐ | ☐ | 🟠 | Icing-type bands (−4/−11 °C) textbook-adjacent but ~1 °C app-shifted; glaciation floors 0.8/0.15 untested at boundaries, undocumented. |
| `sld.py` | 🟡 | ⚪ `obs-only` (FZRA METAR events) | ☐ | ☐ | 🟠 | Warm-nose SEVERE depth (3000 ft) not edge-tested. Collision-coalescence path disabled. |
| `precipitation.py` | 🟡 | 📄/⚪ snow-rain partition climatology (uncited) | ☐ | ☐ | 🟠 | Wet-bulb phase boundaries (−5/0 °C) rule-of-thumb, uncited; `_compute_ice_fraction` is a true arithmetic oracle. |
| `clouds.py` | 🟡 | ⚪ `obs-only` (METAR oktas) | ☐ | ☐ | 🟠 | DD→coverage and okta cutpoints app-invented. **BKN=50/SCT=25 % diverge from WMO midpoints — see Bugs #5.** DD coverage bands tested mid-band only, not at edges. |
| `edr.py` | ✅ | ❌ unused — Kim et al. 2020 C1/C2 table + Sharman & Pearson 2017 (`paper`) | ☐ | ☐ | 🟠 | **Oracle one assertion away.** The climatology-looking test is an algebraic identity that passes with *wrong* constants — a transcription typo is uncaught. See Bugs #3. |
| `e_shear.py` | ✅ | 🟡 CloudPath formula (hand-derived from own formula; close to ✅) | ☐ | ☐ | 🟢 | E pinned via kt-unit profiles, but assertions land on the *tier* not the exact E float; scale factors (~592.5 / ~360000) exercised but not independently asserted vs `1.94384×304.8`. |
| `inversions.py` | ✅ | ✅ definitional, hand-arithmetic (`judgment` — no index exists) | ☐ | ☐ | 🟢 | Strength values hand-computed exactly. Adequate as-is. |
| `advisories.py` (sounding aggregation) | ✅ | ⚪ N/A (derived logic) | ☐ | ☐ | 🟢 | Escape/aggregation arithmetic hand-checked. Inherits upstream risk from icing/convective. |

**TODO (Ext required):**
- [ ] `edr.py`: assert `C1_C2_BY_BAND == Kim et al. 2020 published table values` (cheapest high-value win).
- [ ] `e_shear.py`: add exact-E-float + independent scale-factor assertion (CloudPath is the oracle).
- [ ] `sfip.py`: reproduce Belo-Pereira 2015 / Morcrette 2019 reference soundings.
- [ ] `icing.py`: reproduce published Ogimet index values for sample soundings.
- [ ] **(obs-only / user-test track)** `clouds.py` METAR-okta validation; `icing.py`/`sfip.py`/`sld.py` PIREP calibration; `convective.py` lightning/radar/TS validation of named cases; `precipitation.py` phase-boundary vs published partition climatology.

---

## 3. Route advisory evaluators (`src/weatherbrief/analysis/advisories/`)

| Evaluator | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `flight_category.py` | ✅ | ✅ FAA MVFR/IFR ceiling+vis table (`reg/std`) | ☐ | ☐ | 🟢 | Cutoffs exactly match published categories; boundary-tested. |
| `vfr_feasibility.py` | ✅ | ✅ partial — FAA cat + VFR cloud-clearance regs (decisions §10) | ☐ | ☐ | 🟢 | Mitigation machinery untested (gap). |
| `convective.py` | ✅ | ✅ boundary + ESSL/ESTOFEX (decisions §4/§14) | ☐ | ☐ | 🟢 | DD-floor altitude filter, cross-check additive-only, headline boundaries tested. |
| `freezing_precip.py` | ✅ | ✅ boundary + cert reasoning (decisions §9) | ☐ | ☐ | 🟢 | FZRA/PL→RED, warm-nose detection tested. |
| `enroute_precip.py` | ✅ | ✅ boundary (decisions §11b) | ☐ | ☐ | 🟢 | snow≫rain grading boundary-tested. |
| `headwind.py` | ✅ | ✅ boundary + ISA TAS physics (decisions §11c) | ☐ | ☐ | 🟢 | Minor doc drift on TAS param. |
| `mountain_wind.py` | ✅ | ✅ boundary + classical wave theory (decisions §11d) | ☐ | ☐ | 🟢 | Corroborated-RED w/ wave signature tested. |
| `density_altitude.py` | ✅ | ✅ formula vs hand-computed (ISA, 118.8 ft/°C) | ☐ | ☐ | 🟠 | **Formula validated** (`compute(2000,30)≈4253`, ISA SL→0). Cutoffs 5000/8000 ft are judgment, undocumented. |
| `fronts.py` | ✅ | ⚪ realized-gate ESSL anchors (decisions §6/§7/§13); 15000 ft cutoff judgment | ☐ | ☐ | 🟠 | ~28 boundary tests. Experimental, advisory default-off. |
| `ifr_feasibility.py` | 🟡 | 🟡 200 ft ≈ ILS CAT-I DH (loose); icing/conv % judgment | ☐ | ☐ | 🟠 | **Config bug: icing-% defaults 20/50 (catalog) vs 15/30 (evaluate) — see Bugs #2.** %-boundaries not pinned. |
| `fiki_icing.py` | ✅ | ⚪ `judgment` (no published formula) | ☐ | ☐ | 🟠 | Thickness/clear-cruise/buffer boundary-tested but thresholds undocumented. |
| `icing_escape.py` | 🟡 | ⚪ `judgment` | ☐ | ☐ | 🟠 | 15%-RED, tight-margin AMBER, missing-data→no-escape branches **untested**; loose membership asserts only. |
| `cloud_top.py` | ❌ | ⚪ `judgment` | ☐ | ☐ | 🟠 | Hardcoded `red_pct=60`; **every test asserts only GREEN — RED/AMBER path unexercised.** |
| `vmc_cruise.py` | 🟡 | ⚪ `judgment` | ☐ | ☐ | 🟠 | `ovc_pct_red=50` RED path untested beyond one 100%-OVC case; no AMBER/boundary test. |
| `turbulence.py` | ❌ | ⚪ upstream Ri/EDR (decisions §8c) | ☐ | ☐ | 🟠 | Smoke only; **wrong param key** in a test; no SEVERE→RED or fpm boundary coverage. |
| `airport_wind.py` | 🟡 | ❌ not tied to aircraft demonstrated crosswind (`judgment`) | ☐ | ☐ | 🟠 | Grade boundaries tested BUT **crosswind decomposition fed pre-computed, never numerically verified** — a projection bug mis-grades RED while passing. |
| `llws.py` | 🟡 | ❌ not tied to any LLWS/windshear standard (`judgment`) | ☐ | ☐ | 🟠 | Same shape as airport_wind: **bulk-shear vector math fed in, never recomputed/verified.** |
| `sun.py` (advisory) | ✅ | 🔵 delegated euro_aip/astral; primitive unchecked | ☐ | ☐ | 🟢 | Glare geometry boundary-tested; solar primitive validation tracked under §5 `analysis/sun.py`. |
| `model_agreement.py` | 🟡 | ⚪ dev/calibration signal | ☐ | ☐ | 🟢 | Disabled by default. |
| `dd_nwp_agreement.py` | ❌ | ⚪ dev/diagnostic signal | ☐ | ☐ | 🟢 | **No dedicated test;** Jaccard helper untested. Disabled by default. |

Supporting (no weather thresholds): `registry.py` (aggregation plumbing), `strings.py` (i18n detail strings), `altitude_table.py` (altitude-sweep orchestration).

**TODO (Ext required):**
- [ ] `ifr_feasibility.py`: fix 20/50-vs-15/30 mismatch; boundary-test icing %.
- [ ] `cloud_top.py`, `vmc_cruise.py`, `icing_escape.py`, `turbulence.py`: add AMBER/RED boundary tests for the untested severity paths (and fix turbulence's wrong param key + add SEVERE→RED).
- [ ] `airport_wind.py`, `llws.py`: numerically verify the wind→crosswind decomposition and bulk-shear vector math (currently fixture-fed); consider tying `airport_wind` limits to aircraft demonstrated crosswind.
- [ ] Document the judgment thresholds (`fiki_icing`, `density_altitude` cutoffs, `cloud_top`, `vmc_cruise`) in `meteorology-decisions.md`.

---

## 4. Frontal detection + Hewson (`src/weatherbrief/frontal/`, `src/weatherbrief/hewson/`)

**Status: experimental, advisory gated OFF by default (`auto_front_detection`).** No module reaches
Ext ✅. TFP math is faithful to Hewson 1998, but the method is **app-adapted** (θe substituted for
the paper's θw; cross-front-wind classification replaces the paper's front-motion masking field;
thresholds tuned on a *single* case, 2026-05-31 Channel front). All automated tests use synthetic
analytic fields or round-trip the code's own output.

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `detect.py` | ✅(synthetic) | 📄 Hewson 1998 TFP (faithful) + `obs-only` (DWD charts) | ☐ | ☐ | 🔴 | TFP `−∇\|∇τ\|·∇̂τ` = published; θe for θw; thresholds T=2.0/θe=4.0/wind=2.0 tuned. |
| `tracking.py` | ✅(synthetic) | ⚪ app-invented two-pass anomaly filtering | ☐ | ☐ | 🔴 | Clearance timing on hand-built sequences; anomaly 1.0/2.0, floor 2.0/4.0 tuned. |
| `route_sampling.py` | ✅(synthetic) | ⚪ gate stack app-defined; TFP walk faithful | ☐ | ☐ | 🔴 | Gate config 6.0/5.0/2.0 "validated on 1 case". |
| `gates.py` | ✅ | ⚪ `judgment` — thresholds from single 2026-05-31 case | ☐ | ☐ | 🟠 | `gradient_min=6.0` is a midpoint of "significant(>4)/classical(>8)" convention. |
| `contour_fronts.py` | ✅(synthetic) | 📄 closest to literal Hewson line-extraction (TFP=0 contour) | ☐ | ☐ | 🟠 | Synthetic meridional front tested. |
| `zones.py` | ✅(synthetic) | ⚪ bespoke aggregation (not in any paper) | ☐ | ☐ | 🟠 | 8% + 32-pt coverage thresholds tuned. CLI-only path. |
| `grid.py` | ✅ | 🔵 MetPy θe + SRTM terrain (standard) | ☐ | ☐ | 🟢 | Grid shape/bounds/wind conversions tested. |
| `sources.py` | ✅ | ⚪ N/A (data plumbing) | ☐ | ☐ | 🟢 | Snapshot==direct-compute equivalence. |
| `case.py` | ✅ | ⚪ N/A (calibration loader) | ☐ | ☐ | 🟢 | Save/load round-trip. |
| `cache.py` | 🟡 | ⚪ N/A | ☐ | ☐ | 🟢 | Cache-key round-trip only. |
| `cli.py` | 🟡 | ⚪ hosts the only real ground-truth path (`score`/`validate` vs DWD charts) — **untested, baseline deleted** | ☐ | ☐ | 🟠 | POD/FAR/CSI machinery exists but exercised only manually; last baseline (0.5°, FAR≈77%) cases deleted. |
| `hewson/precompute.py` | ✅(synthetic) | ⚪ diagnostics = `detect.py` | ☐ | ☐ | 🟠 | Tendency/purge/schema tested on synthetic. |
| `hewson/era5_case.py` | ✅(synthetic) | ❌ builds Storm-Ciarán snapshot but **no truth assertion** | ☐ | ☐ | 🟠 | Schema/path tested on a synthetic "ciaran" tmp case. |
| `hewson/cli.py` | ❌ | ⚪ thin wrapper | ☐ | ☐ | 🟢 | No dedicated test. |

**TODO (Ext required) — the deepest open gap:**
- [ ] Revive `frontal cli score` POD/FAR/CSI scoring vs DWD-chart `expected.yaml`; re-establish a baseline at production 0.25° grid.
- [ ] Build an ERA5 retrospective set of synoptically-diverse strong-front cases with front positions hand-digitized from DWD/Met Office surface analyses.
- [ ] Add ≥1 oracle test: run the live Storm Ciarán / May-4 ERA5 case end-to-end and assert the detected front falls within ±50–100 km of the analyzed front.
- [ ] Decide/record whether the θe-for-θw substitution is acceptable vs reverting to Hewson's θw.

---

## 5. Top-level analysis (`src/weatherbrief/analysis/`)

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `alternate_requirement.py` | ✅ | ✅ FAA 14 CFR 91.169 + EASA NCO.OP.140/143 (`reg/std`) | ☐ | ☐ | 🟢 | **Oracle-grade vs regulation text** (~60 tests); guards the real "conflated 140/143" bug. *Residual:* `APPROACH_CLASS_PROXY` DH ranges are bespoke (no plate-minima oracle) → EASA band width is calibration, conservative-by-design (🟠 for that piece). |
| `airport_conditions.py` | ✅ | ✅ standard US VFR/MVFR/IFR/LIFR table (`reg/std`) | ☐ | ☐ | 🟢 | Every category boundary tested. |
| `wind.py` | ✅ | ✅ trig identity, hand-computed (`MetPy/lib`-class) | ☐ | ☐ | 🟢 | head=V·cos, cross=V·sin verified incl. sign convention. |
| `spatial_interpolation.py` | 🟡 | ✅ hand-computed interpolation on known grid | ☐ | ☐ | 🟢 | CLW/ICMR path oracle-tested; `interpolate_diagnostics_spatially`/`_lerp_diagnostics` path **untested**. |
| `comparison.py` | ✅ | ✅ circular mean/spread trig; thresholds ⚪ spec | ☐ | ☐ | 🟢 | Wraparound (350↔010) tested; GOOD/MODERATE/POOR thresholds are spec values (advisory). |
| `airport_consensus.py` | 🟡 | ⚪ bespoke "worst" rules; reuses validated category/wind | ☐ | ☐ | 🟢 | Tested via equivalence to `map_queries._consensus`; no direct worst-mode unit test. |
| `sun.py` | 🟡 | 🔵 delegated euro_aip solar primitive; **never checked vs NOAA/astral** | ☐ | ☐ | 🟠 | All `test_sun.py` assertions qualitative — a silent azimuth/elevation error would pass. |
| `route_geometry.py` | 🟡 | 🔵 delegated euro_aip haversine | ☐ | ☐ | 🟢 | Thin wrapper, exercised indirectly. |

**TODO (Ext required):**
- [ ] `sun.py`: 2–3 fixed (lat, lon, UTC) → assert solar elevation/azimuth within ~0.5° of NOAA / `astral` (pins the euro_aip primitive).
- [ ] `spatial_interpolation.py`: add a test for the `interpolate_diagnostics_spatially` / `_lerp_diagnostics` path.
- [ ] `alternate_requirement.py`: add per-constant CFR/NCO citation comments; source real plate-minima for `APPROACH_CLASS_PROXY` DH ranges.
- [ ] `airport_consensus.py`: direct unit test of `consensus(mode="worst")` xw=max / hw=min picks.

---

## Concrete discrepancies / bugs surfaced this session (act on regardless)

1. ~~**`prepare.py` Magnus constants** `a=17.27, b=237.7` vs doc `17.67 / 243.5`.~~ **RESOLVED (B1)** — now delegates to `mpcalc.dewpoint_from_relative_humidity`.
2. ~~**`ifr_feasibility.py` icing-% mismatch** — catalog 20/50 vs evaluate fallback 15/30.~~ **RESOLVED (B2)** — unified to 20/50 via shared constant + guard test.
3. **`edr.py` C1/C2 not pinned** to the Kim et al. 2020 table — only an algebraic-identity test guards it (passes with wrong constants). **Partially addressed (A1):** `20_45kft` confirmed vs Sharman & Pearson 2017; pin-test deferred until lower bands verified.
4. ~~**`test_nwp_cloud_and_ceiling.py::TestLCLFloor`** re-implements production ceiling logic locally.~~ **RESOLVED (C1)** — extracted `compute_sounding_ceiling_ft`; test calls production.
5. **Cloud okta cutpoints** BKN=50 / SCT=25 % diverge from WMO okta midpoints, with no test noting it.
6. **`thermodynamics.py` blanket `try/except → None`** — a physics bug surfaces as "missing data" rather than an error.
7. ~~**`e_shear.py` truncated knot constants** — `0.51444` (kt→m/s) and `1.94384` (m/s→kt) are not exact reciprocals (~1e-5 inconsistency); `_HWS_SCALE` came out 359999.168 not 360000.~~ **RESOLVED (A2)** — replaced with exact `1852/3600` forms. *Caught by the new scale-factor assertion, not by inspection.*
8. **`grib/decode.py:1413`** uses the same truncated `_KT_PER_MS = 1.94384` — follow-up, out of Tier-1 scope. ~2 ppm wind-conversion error in GRIB decode.

---

## References (oracles — saved for not-yet-validated modules)

> Verify exact citations against each module's docstring before using as an oracle (some are cited
> only informally in code).

- **Hewson, T. D. (1998).** "Objective fronts." *Meteorological Applications*, 5(1), 37–65. — `frontal/*`, `hewson/*` (TFP method).
- **Belo-Pereira, M. (2015).** Icing diagnostic / SFIP family. — `sounding/sfip.py`. *(confirm full cite from module docstring)*
- **Morcrette, C. J. et al. (2019).** Supercooled-liquid / icing index. — `sounding/sfip.py`. *(confirm full cite)*
- **Sharman, R. & Pearson, J. (2017).** "Prediction of energy dissipation rates for aviation turbulence." *J. Appl. Meteor. Climatol.* — `sounding/edr.py`.
- **Kim, J.-H. et al. (2020).** EDR lognormal remap / GTG climatology (C1/C2 table). — `sounding/edr.py`.
- **CloudPath E-shear formula** `E=(5·HWS+VWS²+42)/4`. — `sounding/e_shear.py` (see decisions §8b for the unit-conversion calibration). *(confirm source)*
- **FAA 14 CFR §91.169** (1-2-3 rule, 600-2 / 800-2 alternate minima). — `alternate_requirement.py`.
- **EASA Part-NCO NCO.OP.140 / NCO.OP.143** (alternate selection + planning minima). — `alternate_requirement.py`.
- **WMO okta cloud-cover definitions.** — `sounding/clouds.py` (cutpoint divergence to reconcile).
- **Ogimet icing index** (informal — no primary ref in code). — `sounding/icing.py`. *(find/confirm primary reference)*.
- **Magnus / Alduchov-Eskridge (1996)** dewpoint constants. — `sounding/prepare.py` (constant reconciliation).

---

## Consolidated TODO — external validation required (work queue)

**Tier 1 — quick oracle wins (oracle exists, ~1 assertion each):**
- [~] `edr.py` — pin C1/C2 to Kim 2020 table. **Blocked:** only `20_45kft` externally
  confirmed; lower-band values not in accessible sources + primary PDFs network-blocked. Needs
  the paper's Table 1 (user access or unblocked fetch) before the pin-test is meaningful.
- [x] `e_shear.py` — exact-E-float + scale-factor assertion. *(also fixed Bug #7 + #8 logged)*
- [ ] `analysis/sun.py` — NOAA/astral fixed-case oracle.
- [x] `thermodynamics.py` — value-test the 2 hand-rolled helpers. *(full index-vs-mpcalc oracle
  still open; helpers done)*
- [x] `prepare.py` — dewpoint delegated to MetPy (Magnus reconciled). *(broader unit suite still open)*
- [x] Fix bugs #2 (ifr_feasibility mismatch) and #4 (self-reimplementing test).
- [ ] `grib/decode.py:1413` — replace truncated `1.94384` with exact `3600/1852` (Bug #8).

**Tier 2 — reproduce published reference cases:**
- [ ] `sfip.py` — Belo-Pereira 2015 / Morcrette 2019 reference soundings.
- [ ] `icing.py` — published Ogimet index values.

**Tier 3 — observation ground-truth (build labelled corpus; feeds UsrT+Eval columns):**
- [ ] `clouds.py` — METAR/SYNOP okta validation.
- [ ] `icing.py` / `sfip.py` / `sld.py` — PIREP calibration of zone width / type bands.
- [ ] `convective.py` — lightning/radar/METAR-TS validation of named cases; eval-digest replay.
- [ ] `frontal/*` — revive POD/FAR/CSI vs DWD charts; Storm Ciarán end-to-end oracle.

**Pattern to standardize:** `wet_bulb.py`'s oracle test (independent reference + dense input
envelope + tight tolerance) is the model — replicate that shape wherever an oracle exists.
