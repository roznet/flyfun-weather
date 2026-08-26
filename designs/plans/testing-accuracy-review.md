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

**2026-07-01 — Full independent review (5 parallel section audits vs code+tests).** ~90% of
tracker claims verified exactly; corrections folded into the rows below (stale rows marked
*(corrected 2026-07-01)*). New bugs #9–#17 logged. Meteorological verdicts from the review:
- **Physics confirmed sound:** wet-bulb RK4 integrand (standard pseudoadiabat, MetPy constants),
  N²/Ri forms, crosswind trig + signs, DA formula (27.3 ft/hPa, 118.8 ft/°C), Hewson TFP
  (cos-lat-corrected, K/100 km) modulo the documented θe-for-θw substitution, FAA 91.169 and
  EASA planning-minima values, European CAPE tier calibration.
- **Bug #9 (precipitation phase bands) FIXED this session** — see decisions §17. Boundaries
  moved from −5/0 °C to the Matsuo & Sasyo 0/+1.3 °C melting convention; per-level classifier
  and surface fallback unified; exact-boundary tests added. Full suite **3092 passed**, 0 failed.
- Remaining meteorology items needing a decision (not code errors): okta cutpoints (#5, now
  precisely characterized), MVFR boundary inclusivity (#12), SLD severity proxy (#13),
  Ogimet /2 stratiform cap (#14), dry-θ (not θv) in N² (#15).

**2026-08-15 — sync pass vs code (no new audit; re-checked the open items).** The tracker was
not touched between 2026-07-01 and now, while the analysis package moved a lot. Deltas:
- **Resolved since the review** (fixed as side effects of other work, not by this queue):
  Bug **#11** (DD/NWP Jaccard > 1.0 — `_cloud_overlap_fraction` now merges each model's spans
  before intersecting, commit `75bbb4eb`); Bug **#16** *wind + omega only* (per-level NaN fill
  under #478, pinned by `tests/test_sounding_partial_wind.py` — but the **height** array is still
  all-or-nothing, same silent-GREEN turbulence exposure, see the narrowed #16); the dead
  `tracking._apply_persistence_filter` from Bug **#17** has been deleted.
- **Still open, verified present today:** #3, #5, #6, #8, #10, #12, #13, #14, #15, #16 (height), and the
  `np.roll` / dual-TFP-scaling halves of #17. Stale coordinates refreshed: `_KT_PER_MS = 1.94384`
  is now `fetch/grib/decode.py:2537` (consumed :2616/:3166/:3174), and the bogus turbulence
  param key survives at `tests/analysis/advisories/test_evaluators.py:333` (one site now, not two).
- **Coverage improved without an entry here:** `cloud_top.py` gained AMBER (blocking-deck
  highlights) and UNAVAILABLE (#391) tests — the "every test asserts only GREEN" line is stale,
  though the `red_pct=60` RED path is still unexercised.
- **The tables are now incomplete.** New evaluators landed after the audit and have never been
  scored: `approach_feasibility.py` (#509), `convective_grading.py` (decisions §22 — the single
  convective grade), `vertical_profile.py` (shared path-finder), plus `convective_character.py`
  (#294), which existed at audit time and was missed. Plumbing added since: `interview.py`,
  `engine_methods.py`, `profile_sparsify.py`.
- `meteorology-decisions.md` grew §17 → §25; several rows below now have a decisions-log home
  they didn't have (§18/§22 convective grading, §24 precipitation phase, §25 Richardson CAT
  altitude calibration + mixed-layer gate).

---

## 1. Thermodynamic core (`src/weatherbrief/analysis/sounding/`)

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `wet_bulb.py` | ✅ | ✅ vs MetPy `wet_bulb_temperature` (`MetPy/lib`) — 2,860-pt grid, \|Δ\|<0.001 | ☐ | ☐ | 🟢 | **Gold-standard oracle — the template to replicate.** Hand-rolled RK4 but rigorously checked. |
| `thermodynamics.py` | 🟡 | 🔵 delegated MetPy for indices (`delegated`); 2 helpers now ✅ (A3) | ☐ | ☐ | 🟠 | Every index is `mpcalc.*` (right-by-construction) but **no test asserts a computed value**. `_find_temperature_crossing` + `_compute_bulk_shear` value-tested (A3); `_derive_dewpoint` lives in **prepare.py** *(corrected 2026-07-01)*. Blanket `try/except → None` masks bugs (see Bugs #6 nuance). Also hand-rolled: per-level lapse rate (:307) and 196.85 fpm constant (:400), untested; ISA-altitude fallback silently substitutes for geopotential height in crossings/shear. |
| `vertical_motion.py` | ✅ | ❌ unused (`judgment`/analytic) | ☐ | ☐ | 🟠 | Tests feed hand-set Ri/ω and check bands+sign only; **N²/Richardson computation never value-tested.** Oracle = hand-computed analytic profile. Formula verified correct by inspection (2026-07-01) but N² still uses **dry θ, not θv** — see Bugs #15. *(2026-08-15)* Ri tiers are no longer flat 0.5/1.0/2.0: #533 added an altitude ramp (classical ≤10 kft → 2× at ≥20 kft) plus a θv-parcel mixed-layer cutoff — recorded in decisions §25, still untested against an analytic profile. *(2026-08-26)* #539 replaced the ramp with `clamp(Δz/1,000 ft, 1, 2)` on each Ri's own level pair (decisions §28); the multiplier itself is now unit-tested at floor, slope and cap, but the underlying N²/Ri computation is still not value-tested against an analytic profile. |
| `prepare.py` | 🟡 | ⚪ N/A (`judgment`/plumbing) | ☐ | ☐ | 🟠 | ~~No dedicated test~~ *(2026-08-15)* — `tests/test_sounding_partial_wind.py` (#478) now covers the per-level wind path. ~~Magnus constant discrepancy — Bugs #1.~~ Resolved (B1). Wind/omega all-or-nothing gating resolved (#478); **height gating still all-or-nothing — Bugs #16.** No test for <3 levels → None or descending-pressure sort. |

**TODO (Ext required):**
- [ ] `thermodynamics.py`: oracle test re-computing indices vs `mpcalc` direct on the `_make_levels()` fixture; value-test `_find_temperature_crossing` + `_compute_bulk_shear`; replace blanket `try/except → None` with explicit handling.
- [ ] `vertical_motion.py`: hand-compute N² and Ri for a 2–3 level analytic profile (known θ gradient + shear), assert exact values.
- [ ] `prepare.py`: finish the unit suite (<3 levels → None; descending-pressure sort). *(2026-08-15: partial-wind + Magnus halves done.)*

---

## 2. Bespoke sounding methods (`src/weatherbrief/analysis/sounding/`)

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `icing.py` (Ogimet DD/NWP/IENG) | 🟡 | 📄 Ogimet index + `obs-only` (PIREP) | ☐ | ☐ | 🔴 | "Ogimet" cited but **no primary ref in code** (likely none exists — Ogimet is a website, not a paper). Index thresholds 10/30/80 only edge-tested vs own constants. Zone-width PIREP calibration explicitly deferred (decisions §2). **/2 cap + dead code — see Bugs #14.** |
| `sfip.py` | 🟡 | 📄 Belo-Pereira 2015 & Morcrette 2019 (cited, **never checked against**) | ☐ | ☐ | 🔴 | Best hand-derived coverage of the set — but all vs the module's own formulas. **The cited papers ARE an available oracle, currently unused.** |
| `convective.py` | ✅ | ⚪ `obs-only` (lightning/radar/TS); tiers ESSL/ESTOFEX-calibrated | ☐ | ☐ | 🔴 | Most-documented module (decisions §4/§5/§14/§15/§16). Boundary-rigorous, but anchors are **prod-flight snapshots that record the code's own verdict**. Every §lists "real-world validation needed". |
| `icing_common.py` | 🟡 | ⚪ `obs-only`/literature (accretion physics) | ☐ | ☐ | 🟠 | Icing-type bands (−4/−11 °C) textbook-adjacent but ~1 °C app-shifted; glaciation floors 0.8/0.15 untested at boundaries, undocumented. |
| `sld.py` | 🟡 | ⚪ `obs-only` (FZRA METAR events) | ☐ | ☐ | 🟠 | Warm-nose SEVERE depth (3000 ft) not edge-tested. Collision-coalescence path disabled. **Severity proxy nonstandard (thickness, not nose max temp) — see Bugs #13.** |
| `precipitation.py` | ✅ | ✅ Matsuo & Sasyo 1981 Tw melting convention (0/+1.3 °C), exact-boundary tested; `_compute_ice_fraction` arithmetic oracle | ☐ | ☐ | 🟠 | ~~Wet-bulb bands −5/0 °C uncited~~ **fixed 2026-07-01 (Bug #9, decisions §17)** — per-level classifier + surface fallback unified. Obs validation (wet-snow day vs METAR SN/RASN) still open; FZRA-vs-PL level-count criterion still resolution-dependent (Bugs #13). |
| `clouds.py` | 🟡 | ⚪ `obs-only` (METAR oktas) | ☐ | ☐ | 🟠 | DD→coverage and okta cutpoints app-invented. **BKN=50/SCT=25 % diverge from WMO midpoints — see Bugs #5.** DD coverage bands tested mid-band only, not at edges. |
| `edr.py` | ✅ | ❌ unused — Kim et al. 2020 C1/C2 table + Sharman & Pearson 2017 (`paper`) | ☐ | ☐ | 🟠 | **Oracle one assertion away.** The climatology-looking test is an algebraic identity that passes with *wrong* constants — a transcription typo is uncaught. See Bugs #3. *(Also: calibration accumulator collects nothing in prod — feature inert per #221/PR #230 memory; keep that in mind when prioritizing the pin-test.)* |
| `e_shear.py` | ✅ | 🟡 CloudPath formula (hand-derived from own formula; close to ✅) | ☐ | ☐ | 🟢 | E pinned via kt-unit profiles, but assertions land on the *tier* not the exact E float; scale factors (~592.5 / ~360000) exercised but not independently asserted vs `1.94384×304.8`. |
| `inversions.py` | ✅ | ✅ definitional, hand-arithmetic (`judgment` — no index exists) | ☐ | ☐ | 🟢 | Strength values hand-computed exactly. Adequate as-is. |
| `advisories.py` (sounding aggregation) | ✅ | ⚪ N/A (derived logic) | ☐ | ☐ | 🟢 | Escape/aggregation arithmetic hand-checked — **descend path only** *(corrected 2026-07-01)*: `_climb_above_icing`, `_cat_turbulence_advisory`, `_strong_motion_advisory`, `_cloud_top_uncertainty_advisory` have no direct tests. Inherits upstream risk from icing/convective. |

**TODO (Ext required):**
- [ ] `edr.py`: assert `C1_C2_BY_BAND == Kim et al. 2020 published table values` (cheapest high-value win).
- [ ] `e_shear.py`: add exact-E-float + independent scale-factor assertion (CloudPath is the oracle).
- [ ] `sfip.py`: reproduce Belo-Pereira 2015 / Morcrette 2019 reference soundings.
- [ ] `icing.py`: reproduce published Ogimet index values for sample soundings.
- [ ] **(obs-only / user-test track)** `clouds.py` METAR-okta validation; `icing.py`/`sfip.py`/`sld.py` PIREP calibration; `convective.py` lightning/radar/TS validation of named cases; `precipitation.py` ~~phase-boundary vs climatology~~ *(done — Bug #9/decisions §17)* → wet-snow day validation vs METAR SN/RASN.

---

## 3. Route advisory evaluators (`src/weatherbrief/analysis/advisories/`)

| Evaluator | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `flight_category.py` | ✅ | ✅ FAA MVFR/IFR ceiling+vis table (`reg/std`) | ☐ | ☐ | 🟢 | Cutoffs exactly match published categories; boundary-tested. |
| `vfr_feasibility.py` | ✅ | ✅ partial — FAA cat + VFR cloud-clearance regs (decisions §10) | ☐ | ☐ | 🟢 | ~~Mitigation machinery untested~~ *(corrected 2026-07-01 — `tests/test_vfr_mitigation.py`, 12 tests, exact altitudes/distances)*. |
| `convective.py` | ✅ | ✅ boundary + ESSL/ESTOFEX (decisions §4/§14) | ☐ | ☐ | 🟢 | DD-floor altitude filter, cross-check additive-only, headline boundaries tested. |
| `freezing_precip.py` | ✅ | ✅ boundary + cert reasoning (decisions §9) | ☐ | ☐ | 🟢 | FZRA/PL→RED, warm-nose detection tested. |
| `enroute_precip.py` | ✅ | ✅ boundary (decisions §11b) | ☐ | ☐ | 🟢 | snow≫rain grading boundary-tested. |
| `headwind.py` | ✅ | ✅ boundary + ISA TAS physics (decisions §11c) | ☐ | ☐ | 🟢 | Minor doc drift on TAS param. |
| `mountain_wind.py` | ✅ | ✅ boundary + classical wave theory (decisions §11d) | ☐ | ☐ | 🟢 | Corroborated-RED w/ wave signature tested. |
| `density_altitude.py` | ✅ | ✅ formula vs hand-computed (ISA, 118.8 ft/°C) | ☐ | ☐ | 🟠 | **Formula validated** (`compute(2000,30)≈4253`, ISA SL→0). Cutoffs 5000/8000 ft are judgment, undocumented. |
| `fronts.py` | ✅ | ⚪ realized-gate ESSL anchors (decisions §6/§7/§13); 15000 ft cutoff judgment | ☐ | ☐ | 🟠 | 26 boundary tests. Experimental, advisory default-off. |
| `ifr_feasibility.py` | 🟡 | 🟡 200 ft ≈ ILS CAT-I DH (loose); icing/conv % judgment | ☐ | ☐ | 🟠 | ~~Config bug 20/50 vs 15/30~~ **fixed (B2, guard test in `test_ifr_feasibility_defaults.py`)**. %-boundaries still not pinned (tests use 100 %-icing fixtures vs 30/80, never near 20/50). |
| `fiki_icing.py` | ✅ | ⚪ `judgment` (no published formula) | ☐ | ☐ | 🟠 | Thickness/clear-cruise/buffer boundary-tested but thresholds undocumented. |
| `icing_escape.py` | 🟡 | ⚪ `judgment` | ☐ | ☐ | 🟠 | 15%-RED, tight-margin AMBER, missing-data→no-escape branches **untested**; loose membership asserts only. Note: pack with icing but **no elevation profile → every point counts no-escape → RED** — conservative, but "no terrain data" masquerades as terrain entrapment; consider distinct wording/UNAVAILABLE. |
| `cloud_top.py` | 🟡 | ⚪ `judgment` | ☐ | ☐ | 🟠 | Hardcoded `red_pct=60` (`cloud_top.py:158`), invisible in the catalog. *(2026-08-15)* ~~every test asserts only GREEN~~ — AMBER now covered via the blocking-deck highlight tests and UNAVAILABLE via the #391 subset-coverage test; **the RED path is still unexercised.** |
| `vmc_cruise.py` | 🟡 | ⚪ `judgment` | ☐ | ☐ | 🟠 | `ovc_pct_red=50` RED path tested only via the 60 %-OVC + 40 %-BKN `cloudy_context` fixture *(detail corrected 2026-07-01)*; no AMBER/boundary test. |
| `turbulence.py` | ❌ | ⚪ upstream Ri/EDR (decisions §8c) | ☐ | ☐ | 🟠 | Smoke only; **bogus param key `icing_coverage_pct_amber` in tests — see Bugs #10**; no SEVERE→RED or fpm boundary coverage; `red_pct=50` hardcoded, invisible in catalog. |
| `airport_wind.py` | 🟡 | 🟡 trig oracle-tested; limits `judgment` | ☐ | ☐ | 🟠 | *(corrected 2026-07-01)* The crosswind decomposition **is** numerically verified incl. signs and the 45° oracle (`tests/analysis/test_airport_conditions.py::TestRunwayWinds`); remaining gap is only that the advisory-level tests feed pre-computed `RunwayWind` fixtures, and limits aren't tied to aircraft demonstrated crosswind. |
| `llws.py` | 🟡 | 🟡 shear math oracle-tested (A3); standard tie-in `judgment` | ☐ | ☐ | 🟠 | *(corrected 2026-07-01)* `_compute_bulk_shear` got hand-computed oracles in the Tier-1 commit; advisory tests still feed `bulk_shear_0_1km_kt` directly, and 20/30 kt grades aren't tied to any published LLWS standard. |
| `sun.py` (advisory) | ✅ | 🔵 delegated euro_aip/astral; primitive unchecked | ☐ | ☐ | 🟢 | Glare geometry boundary-tested; solar primitive validation tracked under §5 `analysis/sun.py`. |
| `model_agreement.py` | 🟡 | ⚪ dev/calibration signal | ☐ | ☐ | 🟢 | Disabled by default. |
| `dd_nwp_agreement.py` | ❌ | ⚪ dev/diagnostic signal | ☐ | ☐ | 🟢 | **No dedicated test;** Jaccard helper still untested, but ~~can exceed 1.0 (Bugs #11)~~ **fixed 2026-08-15 sync** — `_cloud_overlap_fraction` merges each side's self-overlapping spans first. Disabled by default. |

**Not yet scored** *(added after the 2026-06-30 audit; each needs its own row)*:
`approach_feasibility.py` (#509 — IAP-vs-wind-runway alignment), `convective_grading.py`
(decisions §22 — now the single source of the convective colour, so its coverage subsumes what
this table credits to `convective.py` and `ifr_feasibility.py`), `convective_character.py`
(#294, decisions §15/§16), `vertical_profile.py` (shared climb/cruise/descent path-finder behind
`vfr_feasibility` + `icing_escape` mitigations — a new load-bearing dependency under two rows
already marked 🟠).

Supporting (no weather thresholds): `registry.py` (aggregation plumbing), `strings.py` (i18n detail strings), `altitude_table.py` (altitude-sweep orchestration), `interview.py` (#387 setup presets), `engine_methods.py` (#403 engine-method defaults), `profile_sparsify.py` (#405 stored-settings sparsify).

**TODO (Ext required):**
- [ ] `ifr_feasibility.py`: fix 20/50-vs-15/30 mismatch; boundary-test icing %.
- [ ] `cloud_top.py`, `vmc_cruise.py`, `icing_escape.py`, `turbulence.py`: add AMBER/RED boundary tests for the untested severity paths (and fix turbulence's bogus `icing_coverage_pct_amber` test key — Bugs #10 — + add SEVERE→RED).
- [ ] `airport_wind.py`, `llws.py`: ~~numerically verify decomposition/shear~~ *(done — trig + bulk-shear oracles exist)*; add advisory-level tests that recompute from raw wind instead of fixtures; consider tying `airport_wind` limits to aircraft demonstrated crosswind and `llws` grades to a published windshear criterion.
- [ ] Document the judgment thresholds (`fiki_icing`, `density_altitude` cutoffs, `cloud_top`, `vmc_cruise`) in `meteorology-decisions.md`.
- [ ] **Exact-boundary pin pattern:** almost no `>=` threshold is tested at its exact `==` value (headwind 20/40, mountain_wind 30/40, enroute_precip 5/25/30, ifr icing 20/50, DD bands 1.0/2.0/3.0 …) — a comparison-direction flip survives nearly every suite. Add a parametrized exact-boundary test per evaluator (fiki's `5000 == 5000 → RED` is the model).

---

## 4. Frontal detection + Hewson (`src/weatherbrief/frontal/`, `src/weatherbrief/hewson/`)

**Status: experimental, advisory gated OFF by default (`auto_front_detection`).** No module reaches
Ext ✅. TFP math is faithful to Hewson 1998, but the method is **app-adapted** (θe substituted for
the paper's θw; cross-front-wind classification replaces the paper's front-motion masking field;
thresholds tuned on a *single* case, 2026-05-31 Channel front). All automated tests use synthetic
analytic fields or round-trip the code's own output.

| Module | Reg | Ext (basis) | UsrT | UsrT+Eval | Risk | Notes |
|---|---|---|---|---|---|---|
| `detect.py` | ✅(synthetic) | 📄 Hewson 1998 TFP (faithful) + `obs-only` (DWD charts) | ☐ | ☐ | 🔴 | TFP `−∇\|∇τ\|·∇̂τ` = published; θe for θw; thresholds T=2.0/θe=4.0/wind=2.0 tuned. *(2026-07-01)* Two TFP implementations with different unit scalings + `np.roll` edge wraparound — see Bugs #17; note the route/contour paths classify warm/cold by **advection sign**, a second distinct substitution for Hewson's front-speed rule. |
| `tracking.py` | 🟡(synthetic) | ⚪ app-invented two-pass anomaly filtering | ☐ | ☐ | 🔴 | *(downgraded 2026-07-01)* Only the clearance-timing helpers are tested; `apply_anomaly_filter` + `build_zone_timeseries` still have **zero coverage** (re-checked 2026-08-15) despite both being live on the CLI paths. ~~`_apply_persistence_filter` is dead code~~ — deleted since. Anomaly 1.0/2.0, floor 2.0/4.0 tuned. |
| `route_sampling.py` | ✅(synthetic) | ⚪ gate stack app-defined; TFP walk faithful | ☐ | ☐ | 🔴 | Gate config 6.0/5.0/2.0 "validated on 1 case". |
| `gates.py` | ✅ | ⚪ `judgment` — thresholds from single 2026-05-31 case | ☐ | ☐ | 🟠 | `gradient_min=6.0` is a midpoint of "significant(>4)/classical(>8)" convention. |
| `contour_fronts.py` | ✅(synthetic) | 📄 closest to literal Hewson line-extraction (TFP=0 contour) | ☐ | ☐ | 🟠 | Synthetic meridional front tested. |
| `zones.py` | ✅(synthetic) | ⚪ bespoke aggregation (not in any paper) | ☐ | ☐ | 🟠 | 8% + 32-pt coverage thresholds tuned. CLI-only path. |
| `grid.py` | ✅ | 🔵 MetPy θe + SRTM terrain (standard) | ☐ | ☐ | 🟢 | Grid shape/bounds/wind conversions tested. |
| `sources.py` | ✅ | ⚪ N/A (data plumbing) | ☐ | ☐ | 🟢 | Snapshot==direct-compute equivalence. |
| `case.py` | ✅ | ⚪ N/A (calibration loader) | ☐ | ☐ | 🟢 | Save/load round-trip. |
| `cache.py` | 🟡 | ⚪ N/A | ☐ | ☐ | 🟢 | Cache-key round-trip only. |
| `cli.py` | 🟡 | ⚪ hosts the only real ground-truth path (`score`/`validate` vs DWD charts) — **untested, baseline deleted** | ☐ | ☐ | 🟠 | POD/FAR/CSI machinery exists but exercised only manually; the two pilot-annotated `expected.yaml` baselines were deleted from git in `9d689a6f`; three local (gitignored) skeletons exist but are unannotated `zones: {}` TODOs *(precision 2026-07-01)*. |
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
| `alternate_requirement.py` | ✅ | ✅ FAA 14 CFR 91.169 + EASA NCO.OP.140/143 (`reg/std`) | ☐ | ☐ | 🟢 | **Oracle-grade vs regulation text** (67 tests); guards the real "conflated 140/143" bug. *Residual:* `APPROACH_CLASS_PROXY` DH ranges are bespoke (no plate-minima oracle) → EASA band width is calibration, conservative-by-design (🟠 for that piece). *(2026-07-01)* Values match the regs; **verify the "NCO.OP.143" citation numbering** for the selection-minima table (may belong under NCO.OP.142/AMC). |
| `airport_conditions.py` | ✅ | ✅ standard US VFR/MVFR/IFR/LIFR table (`reg/std`) | ☐ | ☐ | 🟢 | *(corrected 2026-07-01)* MVFR/VFR edges pinned both sides, but exact 500 ft / 1000 ft / 1 SM / 3 SM edges untested; **exact 3000 ft / 5 SM classify VFR vs the inclusive FAA/AWC convention — see Bugs #12.** |
| `wind.py` | ✅ | ✅ trig identity, hand-computed (`MetPy/lib`-class) | ☐ | ☐ | 🟢 | head=V·cos, cross=V·sin verified incl. sign convention. |
| `spatial_interpolation.py` | ✅ | ✅ hand-computed interpolation on known grid | ☐ | ☐ | 🟢 | CLW/ICMR path oracle-tested; *(corrected 2026-07-01)* the diagnostics path **is** oracle-tested too (`tests/test_grib_fill.py::TestSpatialDiagnostics`, exact midpoints). Interp is 1-D linear in along-route nm (haversine distances — no cos(lat) issue). Minor: `_lerp_optional` one-sided fallback can copy the far neighbor verbatim. |
| `comparison.py` | ✅ | ✅ circular mean/spread trig; thresholds ⚪ spec | ☐ | ☐ | 🟢 | Wraparound (350↔010) tested; GOOD/MODERATE/POOR thresholds are spec values (advisory). |
| `airport_consensus.py` | ✅ | ⚪ bespoke "worst" rules; reuses validated category/wind | ☐ | ☐ | 🟢 | *(corrected 2026-07-01)* Worst/majority modes **are** directly unit-tested via the now-delegating `map_queries._consensus` wrapper (`tests/test_map_queries.py:325+`). Minor: re-declares `_M_PER_SM` locally despite `units.py` claiming single source of truth. |
| `sun.py` | 🟡 | 🔵 delegated euro_aip solar primitive; **never checked vs NOAA/astral** | ☐ | ☐ | 🟠 | All `test_sun.py` assertions qualitative — a silent azimuth/elevation error would pass. |
| `route_geometry.py` | 🟡 | 🔵 delegated euro_aip haversine | ☐ | ☐ | 🟢 | Thin wrapper, exercised indirectly. |

**TODO (Ext required):**
- [ ] `sun.py`: 2–3 fixed (lat, lon, UTC) → assert solar elevation/azimuth within ~0.5° of NOAA / `astral` (pins the euro_aip primitive; current tests tolerate a silent systematic error up to ~±15–20° azimuth).
- [x] ~~`spatial_interpolation.py`: diagnostics-path test~~ — already exists (`tests/test_grib_fill.py::TestSpatialDiagnostics`).
- [ ] `alternate_requirement.py`: add per-constant CFR/NCO citation comments (and verify NCO.OP.143 vs 142 numbering); source real plate-minima for `APPROACH_CLASS_PROXY` DH ranges.
- [x] ~~`airport_consensus.py`: direct worst-mode unit test~~ — exists via the delegating wrapper (`tests/test_map_queries.py:325+`).
- [ ] `airport_conditions.py`: pin the exact 500/1000 ft and 1/3 SM edges; decide Bugs #12 (MVFR inclusivity).

---

## Concrete discrepancies / bugs surfaced this session (act on regardless)

1. ~~**`prepare.py` Magnus constants** `a=17.27, b=237.7` vs doc `17.67 / 243.5`.~~ **RESOLVED (B1)** — now delegates to `mpcalc.dewpoint_from_relative_humidity`.
2. ~~**`ifr_feasibility.py` icing-% mismatch** — catalog 20/50 vs evaluate fallback 15/30.~~ **RESOLVED (B2)** — unified to 20/50 via shared constant + guard test.
3. **`edr.py` C1/C2 not pinned** to the Kim et al. 2020 table — only an algebraic-identity test guards it (passes with wrong constants). **Partially addressed (A1):** `20_45kft` confirmed vs Sharman & Pearson 2017; pin-test deferred until lower bands verified.
4. ~~**`test_nwp_cloud_and_ceiling.py::TestLCLFloor`** re-implements production ceiling logic locally.~~ **RESOLVED (C1)** — extracted `compute_sounding_ceiling_ft`; test calls production.
5. **Cloud okta cutpoints** BKN=50 / SCT=25 % diverge from WMO okta midpoints, with no test noting it.
   *Precision (2026-07-01):* 25/50/87.5 % are the okta-band **starts** (2/4/7 oktas), not the
   category boundaries (FEW/SCT 31.25, SCT/BKN 56.25, BKN/OVC 93.75 %) — every category is
   entered ½–1 okta early, so coverage over-reads (a 50 %-cover layer is METAR SCT but becomes
   BKN, i.e. a ceiling). Over-warn direction, consistent with the safety asymmetry, but it is an
   undocumented calibration choice inflating every DD ceiling and the "embedded" classifier.
   Also the inline comment mislabels BKN as "5–6 oktas" (METAR BKN is 5–7). → document in
   meteorology-decisions.md or move to boundary values.
6. **`thermodynamics.py` blanket `try/except → None`** — a physics bug surfaces as "missing data" rather than an error. *(Nuance 2026-07-01: most sites log at debug with `exc_info`; `_mag` (:30) and `_compute_bulk_shear` (:264) are fully silent.)*
7. ~~**`e_shear.py` truncated knot constants** — `0.51444` (kt→m/s) and `1.94384` (m/s→kt) are not exact reciprocals (~1e-5 inconsistency); `_HWS_SCALE` came out 359999.168 not 360000.~~ **RESOLVED (A2)** — replaced with exact `1852/3600` forms. *Caught by the new scale-factor assertion, not by inspection.*
8. **`fetch/grib/decode.py:2537`** *(line refreshed 2026-08-15)* uses the same truncated `_KT_PER_MS = 1.94384` (consumed at :2616/:3166/:3174 for ECMWF winds) — follow-up, out of Tier-1 scope. ~2 ppm wind-conversion error. Related: the Bolton–Magnus 17.67/243.5 dewpoint formula is hand-rolled in **four** modules (`fetch/open_meteo.py:65`, `fetch/grib/decode.py` :2579/:2596/:2695, `analysis/sounding/icing.py:119`, `storage/sounding_profiles.py` :162/:181) — constants all consistent with MetPy, but duplicated; consolidate when touching. *(sites re-located 2026-08-15)*
9. ~~**`precipitation.py` wet-bulb phase bands physically wrong** (−5..0 → MIXED, ≥0 → RAIN).~~
   **RESOLVED (2026-07-01)** — Matsuo & Sasyo convention (SNOW < 0 / MIXED 0..+1.3 / RAIN > 1.3),
   per-level classifier unified with the surface fallback. Rationale in decisions §17 (§24 since).
10. **`tests/analysis/advisories/test_evaluators.py:333` (turbulence) passes a bogus param
    key** `icing_coverage_pct_amber` (copy-paste from icing_escape; real key is
    `route_pct_amber`) — the override silently never lands and the test passes on the
    defaults. *(2026-08-15: one site left; the sibling call sites were switched to
    `route_pct_amber` in the interim.)* Fix alongside the missing SEVERE→RED / `strong_w_fpm`
    boundary tests (§3 TODO).
11. ~~**`dd_nwp_agreement._cloud_overlap_fraction` "Jaccard" can exceed 1.0** — pairwise
    intersection vs merged union, so self-overlapping layers overstate agreement and the
    disagreement flag under-fires.~~ **RESOLVED** (`75bbb4eb`) — each side's spans are merged
    into disjoint intervals before intersecting. Still has no dedicated test.
12. **VFR/MVFR boundary inclusivity** — exactly 3000 ft / 5 SM classifies **VFR**
    (`airport_conditions.py` strict `<`), but the FAA/AWC convention is inclusive
    (MVFR = 1000–3000 ft and/or 3–5 SM). Deliberately pinned by `test_boundary_vfr_ceiling`,
    so it's a chosen convention — either flip to `<=` or record as a decision.
13. **`sld.py` severity proxy is nonstandard** — FZRA severity graded on warm-nose
    *thickness* (≥3000 ft) only; the usual discriminator includes nose **max temperature**
    (complete melting needs Tmax ≳ +1 °C), so a thin-but-warm nose (classic large-drop FZRA)
    grades MODERATE. Also `warm_levels >= 2` / `cold_depth >= 3` are level *counts*, making
    the FZRA-vs-ice-pellets call resolution-dependent.
14. **`icing.py` /2 normalization silently caps stratiform Ogimet at 50** — layered parabola
    peaks at 100, halved to 50, SEVERE needs ≥80 ⇒ pure stratiform icing can never grade
    SEVERE from this index (only the convective moisture-differential term reaches it).
    Possibly fine by design (SLD owns severe-stratiform) but undocumented and interacts with
    decisions §2. Same file: `_enhance_severity`, `_detect_sld`, `_lwc_to_icing_severity`
    are **dead code** (never called) — their thresholds must not be counted as live logic;
    and `test_ogimet_nwp_scales_by_cloud_pct` asserts zone *count*, not scaling.
15. **`vertical_motion.py` N² uses dry θ, not θv** — overestimates stability in moist layers
    → Ri biased high → CAT under-warned. Partially offset by the deliberately loosened
    0.5/1.0/2.0 Ri thresholds, but that interplay is undocumented. Ratify (one decisions-log
    line) or switch to virtual potential temperature.
16. ~~**`prepare.py` wind gating is all-or-nothing**~~ **wind + omega RESOLVED (#478,
    2026-07-22)** — both NaN-fill per level (`prepare.py:110`/`:133`), pinned by
    `tests/test_sounding_partial_wind.py` against the #391/#393 absence-reads-as-clear failure.
    **Height is still all-or-nothing** (`all(...)` at `prepare.py:127`): one level missing
    geopotential drops height for the whole column → no bulk shear, no Ri, no CAT layers, and
    the same silent-GREEN turbulence outcome the wind fix was written to prevent. Give it the
    same NaN treatment. *(narrowed 2026-08-15)*
17. **`frontal/detect.py` `_tfp_proximity_mask` uses `np.roll`** (`detect.py:44`) — TFP sign
    comparisons wrap around the domain edges, creating spurious "zero-crossings" along the
    boundary rows/columns, then dilated 2 cells inward. Same stack: two TFP implementations
    coexist with different unit scalings (zones path K/km² unscaled vs diagnostics path
    K/(100 km)²) under the same `"tfp"` key — a 10⁴ trap for any future threshold.
    ~~`tracking._apply_persistence_filter` is dead code~~ — deleted since.
    Experimental/default-off, so contained.
18. **Visibility statute-mile conversion is still duplicated despite `units.py` claiming one
    source of truth** — `tasks/scoring.py`, `tasks/route_weather.py`, and
    `analysis/airport_consensus.py` redeclare `_M_PER_SM = 1609.34` instead of importing
    `weatherbrief.units.M_PER_SM`. Low numerical risk (same value today), but a verification
    maintenance trap because scoring, D-0 comparison, airport consensus, and display can drift
    separately if one copy changes. **FIXED** — all three now
    `from weatherbrief.units import M_PER_SM as _M_PER_SM`.

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
- **Matsuo, T. & Sasyo, Y. (1981).** "Non-melting phenomena of snowflakes observed in subsaturated air below freezing level." *J. Meteor. Soc. Japan* — `sounding/precipitation.py` wet-bulb phase boundaries (0/+1.3 °C melting convention). **USED** as of 2026-07-01 (Bug #9 fix).

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
- [x] `precipitation.py` — wet-bulb phase bands realigned to the Matsuo & Sasyo 0/+1.3 °C
  melting convention, exact boundaries pinned (Bug #9, decisions §17). *(2026-07-01)*
- [ ] `fetch/grib/decode.py:1413` — replace truncated `1.94384` with exact `3600/1852` (Bug #8); consolidate the 4 hand-rolled Magnus copies while there.
- [~] Turbulence test bogus key + `dd_nwp_agreement` Jaccard overlap-merge (Bugs #10/#11) — the
  Jaccard half is **done** (`75bbb4eb`); the turbulence key at `test_evaluators.py:333` remains.
- [ ] Score the post-audit evaluators in §3 (`approach_feasibility`, `convective_grading`,
  `convective_character`, `vertical_profile`) — the tables silently claim coverage they never
  assessed. *(2026-08-15)*
- [x] Consolidate remaining `_M_PER_SM = 1609.34` copies to `weatherbrief.units.M_PER_SM`
  (Bug #18).

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
