# Route Advisory System

> Deterministic evaluation of weather hazards along a flight route with per-model severity assessments

## Intent

Provide actionable, severity-graded (GREEN/AMBER/RED) advisories from 22 hazard evaluators (grouped into icing, cloud, precipitation, turbulence, convective, wind, model, airport, feasibility, fronts, and sun categories) along the route. Evaluators analyze existing route analysis data — no additional data fetch. User-tunable parameters allow recalculation without re-running the pipeline. This is a **route-level** system (advisory per route), complementing the per-waypoint `AltitudeAdvisories` in the sounding subpackage.

## Architecture

```
RouteContext (immutable)
  ├── analyses: list[RoutePointAnalysis]   (~20 points along route)
  ├── cross_sections: list[RouteCrossSection]  (per-model forecast grids)
  ├── elevation: ElevationProfile | None
  ├── airport_conditions: AirportConditions | None  (dep + arr weather)
  ├── arrival_approaches: AirportApproaches | None  (destination IAPs, #509)
  ├── sun: RouteSunAnalysis | None  ├── route_fronts: RouteFrontsManifest | None
  ├── cruise_speed_ias_kt, flight_duration_hours  (headwind trip-time inputs)
  ├── models, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm, locale
      ↓
Registry → evaluate_all(ctx, enabled_ids?, user_params?, aggregation?)
  │   (publishes user_params on ctx.advisory_params so a composite can grade a
  │    sub-axis with the OWNING advisory's parameters — see §22)
  ├── @register IcingEscapeEvaluator       # en-route icing
  ├── @register FIKIIcingEvaluator
  ├── @register FreezingPrecipEvaluator
  ├── @register CloudTopEvaluator          # en-route cloud
  ├── @register VMCCruiseEvaluator
  ├── @register EnroutePrecipEvaluator     # precipitation (en-route visibility proxy)
  ├── @register TurbulenceEvaluator        # en-route turbulence
  ├── @register MountainWindEvaluator
  ├── @register ConvectiveEvaluator        # convective (severity) — grades via convective_grading.py (§22)
  ├── @register ConvectiveCharacterEvaluator  # convective (VFR-avoidability, orthogonal to severity)
  │                                          exposes classify_route_character() for the §22 EMBEDDED escalation
  ├── @register HeadwindEvaluator          # wind (trip impact)
  ├── @register ModelAgreementEvaluator    # model quality (cross-model)
  ├── @register DDvsNWPAgreementEvaluator  # model quality (within-model)
  ├── @register FlightCategoryEvaluator    # airport conditions (+ terminal convective)
  ├── @register AirportWindEvaluator
  ├── @register DensityAltitudeEvaluator
  ├── @register LLWSEvaluator
  ├── @register VFRFeasibilityEvaluator    # composite go/no-go
  ├── @register IFRFeasibilityEvaluator     # convective axis = ConvectiveEvaluator's grade (§22)
  ├── @register ApproachFeasibilityEvaluator  # arrival: approach vs wind vs ceiling
  ├── @register FrontsEvaluator            # fronts (experimental, gated on artifact)
  └── @register SunEvaluator               # sun (glare + night-proximity + seating note)
      ↓
RouteAdvisoriesManifest (advisories + catalog + aggregation mode)
  → route_advisories.json
```

**All files in `src/weatherbrief/analysis/advisories/`.**

## Evaluator Protocol

Every evaluator implements two static methods — no inheritance, no base class:

```python
class MyEvaluator:
    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        # Metadata: id, name, description, category, parameters, default_enabled

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        # Per-model iteration → aggregate
```

The `@register` decorator auto-discovers evaluators at import time. New evaluators: create file, implement protocol, add `@register` — no central config changes needed.

## Three-Level Aggregation

Every evaluator follows the same pattern:

1. **Point level**: Iterate route points, count affected vs total per model
2. **Model level**: `ModelAdvisoryResult.build(status, detail, affected, total, total_distance_nm)` — computes percentage and distance metrics
3. **Route level**: `RouteAdvisoryResult.from_per_model(id, per_model, params, aggregation=)` — aggregates per-model statuses using the chosen mode

Aggregation is controlled by `AdvisoryAggregation` enum (`WORST` or `MAJORITY`). The registry passes the aggregation mode; individual evaluators always produce per-model results without knowing the aggregation strategy.

Detail text comes from the worst-performing model. Shared classmethods on the models eliminated ~115 lines of boilerplate.

**Localized detail text** (`strings.py`): evaluators build detail strings via `adv_t(key, locale, **params)` against the `_STRINGS` catalog (en/fr/de/es), keyed `<evaluator>.<msg>`. Aviation abbreviations (VFR, OVC, BKN, FL, kt, ICAO codes…) are never translated. `ctx.locale` flows in from the flight profile and is honored on recalc.

### Aggregation Modes

- **WORST**: If ANY model shows RED, the aggregate is RED. Conservative — pilots see the worst-case scenario.
- **MAJORITY** (default, changed from WORST): The most common status across models wins. Ties broken by worst status among the tied group. Example: 2 AMBER, 2 GREEN, 1 RED → tie between AMBER and GREEN → worst of tied = AMBER. Changed to default because WORST mode was too noisy — a single outlier model could make the whole route RED.

`AdvisoryStatus.majority(statuses)` implements the majority logic: count each status (ignoring UNAVAILABLE), find max count, return worst among tied leaders. The registry re-aggregates after each evaluator returns if mode isn't WORST, so evaluator code is unchanged.

**A GREEN aggregate may sit above a RED model — deliberately (#578).** That is
what MAJORITY *means*: a lone dissenting model does not move the aggregate. The
consequence is worth stating plainly, because it is what a pilot sees: the
aggregate detail comes from the representative model (the first per-model result
carrying the aggregate status), so the briefing prints "Smooth ride expected"
while one model reads RED over 63% of the route. Replaying 201 staging packs
found 15 such `(pack, advisory)` pairs; a pilot who wants the other behaviour
sets aggregation to WORST.

This is pinned as a choice, not left as an accident:
`TestTheAggregateLayer.test_majority_may_publish_green_over_a_red_model`
asserts it, and `invariants.masked_flagged_models` reports it (never as a
violation) so `rerun_advisories_diff.py --check-invariants` can count it across
real packs. Flooring the aggregate at AMBER whenever any model is RED is the
alternative; it moves grades on live briefings, so it needs its own diff run and
its own decision — not an edited assertion.

**Invariants over the published result** (`analysis/advisories/invariants.py`):
one set of predicates — the #571 extent rules, "flagged ⇒ non-zero coverage", and
the aggregate-layer rules (the aggregate holds a grade some model holds; it names
the representative it speaks for; any extent it prints is that model's) — shared
by the unit tests, a real-corpus test and the replay script, so a new evaluator
is covered without anyone remembering to add it. The coverage rule applies only
to results that publish an extent at all: `fronts` builds its result directly
rather than through `build()` because it grades a *distance to a boundary* in km,
not a span of route, so its extent fields stay zero even on a graded AMBER —
nothing published, nothing to contradict. Publish an extent and it comes back
under the rule with no edit to the invariants.

## The 22 Evaluators

### Icing

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `IcingEscapeEvaluator` | icing | Non-FIKI: can we descend below freezing to escape icing? Checks FZ level vs terrain + margin. Altitude-aware: ignores icing above cruise + buffer. Any no-escape point → amber; `extent_pct_red` escalates to red. `extent_pct_amber` warns when escapable icing covers a significant portion of the route | `terrain_margin_ft`, `tight_margin_ft`, `icing_altitude_buffer_ft`, `extent_pct_amber` (20%), `extent_pct_red` (15%), `extent_min_nm` (30nm) |
| `FIKIIcingEvaluator` | icing | FIKI-equipped: evaluates icing layer thickness and severity (transit OK, loiter not) | `thickness_amber_ft`, `thickness_red_ft`, `extent_pct_amber` (20), `extent_pct_red` (50), `extent_min_nm` (30nm), `severe_is_red` |
| `FreezingPrecipEvaluator` | icing | Freezing rain / ice pellets: active FZRA/PL surface phase anywhere → RED (exceeds all icing certification, incl. FIKI; below-cloud hazard invisible to in-cloud icing methods). Freezing-rain-shaped profile without active precip (warm nose over sub-zero surface, via `detect_warm_nose`) → AMBER above coverage threshold | `extent_pct_amber` (5%), `extent_min_nm` (30nm) |

### Cloud

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `CloudTopEvaluator` | cloud | Can we fly above the clouds? Only considers layers pilot would enter (base ≤ ceiling), ignores high cirrus | `margin_ft`, `extent_pct_amber` (25), `extent_pct_red` (60), `extent_min_nm` (30nm) |
| `VMCCruiseEvaluator` | cloud | Cloud coverage at cruise altitude specifically (BKN/OVC percentage along route) | `extent_pct_amber` (25%, BKN+OVC), `extent_pct_red` (50%, OVC only), `extent_min_nm` (30nm) |

### Precipitation

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `EnroutePrecipEvaluator` | precipitation | Precipitation along the route as the en-route **visibility proxy** (no model forecasts visibility at altitude; surface phase/intensity from `PrecipitationAssessment` works for all 7 models). Snow is the VFR killer: any snow ≥ amber threshold → AMBER, widespread moderate+ snow → RED. Moderate+ rain → AMBER (capped — rain degrades, rarely prohibits). FZRA/PL count toward extent only; severity owned by `freezing_precip`. Shared classifier (`classify_enroute_precip`) feeds the VFR composite capped at AMBER | `extent_pct_amber` (5%, snow), `extent_pct_red` (25%, moderate+ snow), `rain_pct_amber` (30%), `extent_min_nm` (30nm) |

### Turbulence

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `TurbulenceEvaluator` | turbulence | Richardson CAT layers overlapping cruise + strong vertical motion near cruise. **Severe is split by where it sits (#533/#534, meteorology-decisions §25; the Ri tiers feeding these layers are thickness-scaled per §28):** *free-atmosphere* SEVERE at cruise → RED outright; *boundary-layer* SEVERE (`layer.boundary_layer`) does NOT bypass the coverage gate — one low-level shear sheet is not a route-wide hazard — but floors the advisory at AMBER so the grade can't read GREEN under a "SEVERE over …" detail. **RED via coverage needs moderate-or-worse** over 50% of assessed points (`significant_points`, which also counts a strong updraft within 3000 ft of cruise); LIGHT-only chop over most of the route caps at AMBER. Ribbon severity and grade `affected` key on deliberately different predicates (severe-anywhere-in-column vs cruise band) — see the evidence helper | `extent_pct_amber` (20), `extent_pct_red` (50), `extent_min_nm` (30nm), `strong_w_fpm` (200) |
| `MountainWindEvaluator` | turbulence | Wind speed near significant terrain corroborated by wave signatures: an inversion overlapping ridge top (−1000/+4000ft band) or an OSCILLATING vertical-motion classification. Signature present → RED bar drops from `wind_red_kt` to `corroborated_red_kt` ("rotor day" vs "windy ridge"). Cross-ridge direction deliberately NOT assessed (1-D terrain profile — ridge orientation unknown). Only evaluates where terrain > threshold | `terrain_threshold_ft`, `altitude_margin_ft`, `wind_amber_kt`, `wind_red_kt` (40), `corroborated_red_kt` (30) |

### Other

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `ConvectiveEvaluator` | convective | **Grading lives in `convective_grading.grade_convective_model`, not here (§22)** — this evaluator adds only the locale wording and the cross-model aggregate. `ifr_feasibility` consumes the same call, so the two advisories cannot disagree about one sounding.  Route points with convective risk ≥ threshold. Altitude-aware: ignores convection whose tops are below `cruise_ft - top_clearance_ft`. **NWP-native grade (#442, meteorology-decisions §18):** colour comes from the model's own NWP convective tier (`convective_method` defaults to `"nwp"`), **not** `max(NWP, DD)` — a quiet NWP is no longer floored up to a DD tower (that produced loaded-gun false-alarm REDs). RED comes only from the NWP track: a HIGH tower, or its own MODERATE+ coverage ≥ `extent_pct_red`. **DD-trigger AMBER (`reason_code="dd_trigger"`):** a *green* NWP whose DD-vs-scheme `cross_check` reads `dd_not_corroborated` (DD MODERATE+, scheme quiet) is raised to AMBER only — never red, tier capped at MODERATE, and excluded from the red-coverage count. **Absent-track AMBER (`reason_code="dd_fallback"`, #568, meteorology-decisions §26a):** the DD-trigger rule keys on the cross-check, which needs an NWP assessment to compare against — so it never fired for a model with *no* native track, where `sounding.convective` **is** the thermo assessment and already sits at/above `min_risk`, and the DD tier therefore went uncapped to RED (ICON graded on MetPy parcel CAPE beside GFS/ECMWF graded on their own schemes). Such a point — `SoundingAnalysis.convective_nwp_fallback`, set in `_resolve_analyses`, the only layer that knows the *requested* method — is capped at MODERATE and counted into `dd_trigger_count` so the same red-coverage exclusion applies. Neither `convective_nwp is None` nor `convective_method_effective == "thermo"` can stand in for the marker: the first is False under an explicit thermo request, the second is ambiguous between fallback and explicit request. An explicit `convective_method="thermo"` is never capped, and a model whose own scheme is firing is never capped. **MODERATE+ amber floor:** any MODERATE+ point reaching cruise (real or dd_trigger) forces ≥ AMBER (the coverage thresholds were tuned around the old floor; without it isolated MODERATE would read green). Also emits a per-model, **details-only** `cross_check` note (never regrades), **anchored on the grade-driving (peak) point** (#442 f/u): it compares the two cross-section layers there — *NWP Convective* (`convective_nwp`, the model's own scheme) vs *Thermo Convective* (`convective_thermo`, DD) — and surfaces a note **only when they differ by ≥2 tiers** (same-or-one-off is normal method spread). Names the driver: NWP higher → "NWP Convective drives this — Thermo Convective shows only {DD}"; DD higher (the `dd_trigger` case) → "Thermo Convective shows {DD} instability, but the NWP forecast is quiet here"; no NWP track at the driving point at all (#568) → "No NWP Convective forecast from this model here — graded on Thermo Convective (thermodynamics) alone, which on its own can never grade red" (`convective_cross_check` correctly returns None there — nothing to compare — so the absence note is emitted by `_peak_cross_check`). Anchoring on the driver (not a route-wide dominant-direction scan) stops a minority quiet stretch reading as contradicting a red driven elsewhere. The web card surfaces a muted, tappable `ℹ Convective signals disagree` tag when present. Copy is jargon-free and named after the cross-section toggles so a pilot can pull up both overlays. **This is the single source of truth for convective DD-vs-NWP divergence** (see `DDvsNWPAgreementEvaluator`). **Explicit-convection mode (#462):** on a D2-sourced icon slot the NWP track is the `method="nwp_explicit"` assessment (reflectivity-driven, meteorology-decisions §19). Structural consequences here: `top_ft` is always None, so the tops-below-cruise filter never consumes the 18 dBZ echo top (which would err toward "safe to overfly" under a higher anvil) and flagged cells render as `tower_unresolved` ghosts; the cross-check note comes from `_explicit_cross_check` ("ICON-D2 explicitly develops a cell…" / "…explicit convection is quiet"); an unavailable explicit track (`convective_nwp=None`) grades on thermo like any scheme-less model — never a fake quiet. The proposed lone-D2-cell aggregate AMBER floor is NOT implemented — that is #442's call. | `min_risk`, `extent_pct_amber` (20), `extent_pct_red` (50), `extent_min_nm` (30nm), `top_clearance_ft` (2000) |
| `ConvectiveCharacterEvaluator` | convective | Second convective axis (#294), **orthogonal to severity** — grades whether route convection is *circumnavigable VFR*, never touches the `convective` grade. Per-model (realized-coverage + K/TT signals are per model; disagreement on avoidability is itself signal). Classifies via `classify_convective_character` (sounding subpackage): NONE/UNKNOWN → GREEN; ISOLATED/SCATTERED (avoidable but committing) → AMBER; WIDESPREAD/ORGANIZED/EMBEDDED (no reliable gaps, frontal band, or cells hidden under a deck) → RED. Only considers points at/above `min_risk`; `_point_embedded` marks a point whose cruise sits **inside** a BKN/OVC deck, `_front_present` promotes a widespread band to ORGANIZED. **The EMBEDDED gate, redefined (#568, meteorology-decisions §26b):** a point is embedded only when it has a *realized* cell, a BKN/OVC layer *contains* cruise (`base − embed_cruise_buffer_ft ≤ cruise ≤ top + embed_cruise_buffer_ft`) and bulk cover in the **ICAO band containing cruise** reaches `embed_deck_cover_pct` (OVC-only fallback when the model publishes no bulk cover). Route-level, EMBEDDED fires iff the longest **contiguous** run of such points spans ≥ `embed_min_nm`, measured with `route_geometry.cell_edges` so it agrees with the card's own `(Xnm/Ynm)`. All three replace defects that each alone produced a spurious RED: only the deck's *base* was tested (36 of 39 "embedded" ICON points were decks entirely below cruise), `max(low, mid)` cover ignored cruise altitude, and the old `embedded / len(conv) ≥ 50 %` fraction had no floor (live: ECMWF grading a 582 nm route RED off one 9 nm point). Points failing the gate fall through to the realized-coverage band; severity still owns that cell's own colour.

**Altitude mitigation for EMBEDDED (#568 Fix 4, §26d):** the one band where a different cruise level is likely to be the answer now carries the lightbulb. The full band is re-derived at each candidate on the 500 ft `MITIGATION_BIN_STEP_FT` ladder between `mitigation_min_base_agl_ft` above terrain and `ctx.flight_ceiling_ft`; the nearest level that is no longer EMBEDDED is offered as climb (VFR on top — penetrating buildups are visible) or descend (see-and-avoid under the cells), with `mitigated_status` = the band you would actually get there. Advice only — it never moves `status`. EMBEDDED only (altitude cannot fix horizontal extent), and `aggregate_mitigations` is overridden to promote only an altitude clearing **every** model currently grading EMBEDDED. `Mitigation.profile` stays `None`: the contiguous-extent test is route-level and non-additive, so it cannot be a per-point cost in the vertical-profile solver. **Emits `primary_method_id` (#568, §26c):** the character card is the one that renders the EMBEDDED red and badged nothing, so a model graded on thermodynamics for lack of a native track sat beside two graded on their own schemes with no visible difference. `driving_method_id` can't be reused (it reads `AdvisoryHighlights`, which this evaluator doesn't produce), so `build_character_points` returns the effective convective track alongside the points; a fallback anywhere on the route wins the badge. **Zero realized cells → NONE/GREEN (§22)**, checked before the EMBEDDED test — previously `realized_pct = 0` fell into the `<= isolated_max_pct` bucket and rendered "Isolated cells … (0nm/600nm (0%))", an AMBER promise of avoidable cells over an extent of nothing (UKMO/MétéoFrance, which have no model-native convective scheme). The per-model loop is extracted to `build_character_points` / `classify_route_character` (+ `resolve_character_params`) so `ifr_feasibility` reads the same band for its EMBEDDED escalation. Altitude-aware (see-and-avoid needs `base_clearance_ft` VMC below cell bases) | `min_risk` (3), `showers_mm` (0.1), `isolated_max_pct` (15), `scattered_max_pct` (40), `organized_shear_kt` (35), `base_clearance_ft` (2000), `embed_min_nm` (50), `embed_cruise_buffer_ft` (1000), `embed_deck_cover_pct` (60), `mitigation_min_base_agl_ft` (3000 — this advisory's own, deliberately not `vfr_feasibility`'s, §26d) |
| `HeadwindEvaluator` | wind | Route-average cruise-level headwind + trip-time delta vs still air. TAS is derived from `ctx.cruise_speed_ias_kt` (aircraft/profile cruise IAS via `atmo.resolve_cruise_speed_ias`, converted to TAS at cruise altitude), falling back to `total_distance_nm / flight_duration_hours` — both ride on `RouteContext` so recalc-from-pack still works. Altitude-aware (cross-section winds at the evaluated altitude → the altitude table shows the wind trade per level; falls back to precomputed `wind_components`). Informational bias: AMBER on mean ≥ 20kt, RED only ≥ 40kt; tailwinds report minutes saved | `mean_amber_kt` (20), `mean_red_kt` (40) |
| `ModelAgreementEvaluator` | model | Cross-model divergence (POOR/MODERATE agreement). Evaluated once, not per-model. **Disabled by default** — user must enable via profile | `min_poor_vars` (3), `extent_pct_amber` (25), `extent_pct_red` (50), `extent_min_nm` (30nm) |
| `DDvsNWPAgreementEvaluator` | model | Within-model agreement: compares DD (thermodynamic) vs NWP tracks per model at each route point. Checks freezing-level delta and cloud layer Jaccard (only when NWP has real boundaries — `source="nwp_3d"` or `"grib"`, never `"synthesized"` which is circular). Surfaces e.g. mid-level decks GFS sees that DD misses, or ECMWF cirrus ice-supersaturation that `cc` rejects. **Convective divergence is intentionally NOT checked here** — now that the NWP convective track is model-native (#283), DD-vs-NWP convective disagreement is reported by the richer, convective-specific inline `cross_check` on `ConvectiveEvaluator`; duplicating it here would double-count the same divergence. **Disabled by default** — dev/calibration signal, not pilot-facing | `freezing_delta_ft` (2000), `cloud_overlap_min` (30%), `extent_pct_amber` (30), `extent_pct_red` (60), `extent_min_nm` (30nm) |

### Airport

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `FlightCategoryEvaluator` | airport | Ceiling/visibility at departure + arrival (OR logic: either metric below threshold triggers; defaults match MVFR/IFR boundaries) PLUS terminal-area convective risk within `conv_radius_nm` of each end with **no coverage dilution** (a deviation is not an option on climb-out/approach): MODERATE → AMBER, HIGH/EXTREME → RED, no altitude filter | `amber_ceiling_ft` (3000), `amber_vis_sm` (5), `red_ceiling_ft` (1000), `red_vis_sm` (3), `conv_radius_nm` (25) |
| `AirportWindEvaluator` | airport | Crosswind on best runway + gust severity at departure + arrival. Worst of dep/arr becomes the status | `xwind_green_kt`, `xwind_red_kt`, `gust_green_kt`, `gust_red_kt` |
| `DensityAltitudeEvaluator` | airport | Density altitude at departure + arrival from forecast T/QNH at the expected times + field elevation (terrain profile). Triggers on absolute DA OR DA-above-field (hot-day performance loss). UNAVAILABLE without temperature/terrain — never green-by-absence | `da_amber_ft` (5000), `da_red_ft` (8000), `delta_amber_ft` (3000), `delta_red_ft` (5000) |
| `LLWSEvaluator` | airport | Low-level wind shear at departure + arrival: 0–1km bulk shear from the airport-point sounding (catches the nocturnal LLJ AirportWind can't see) OR gust factor (gust − sustained). Surface-based inversion reported alongside significant shear. Worst of dep/arr | `shear_amber_kt` (20), `shear_red_kt` (30), `gust_factor_amber_kt` (15) |

### Feasibility (Composite)

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `VFRFeasibilityEvaluator` | flight_rules | Composite VFR go/no-go combining: airport flight category, en-route cloud clearance (base vs cruise), VMC compliance (BKN/OVC percentage), climb-out/descent corridor decks (BKN/OVC fully below cruise within `terminal_corridor_nm` of either end — OVC→RED, BKN→AMBER, see meteorology-decisions §10), and en-route precipitation (shared `classify_enroute_precip`, capped at AMBER in the composite). Worst of sub-assessments wins | `cloud_clearance_ft`, `extent_pct_amber` (15), `extent_pct_red` (30), `extent_min_nm` (30nm), `terminal_corridor_nm` (5) |
| `IFRFeasibilityEvaluator` | flight_rules | Composite IFR go/no-go combining: airport IFR viability (LIFR→amber, below minimums→red), en-route icing exposure (uses shared `icing_zones_in_altitude_range()` helper over `[0, cruise + icing_altitude_buffer_ft]`, aligned with FIKI advisory), and the convective axis. **The convective axis is not graded here (§22):** it is `ConvectiveEvaluator`'s per-model status verbatim, obtained from the shared `convective_grading.grade_convective_model` — the identical call the convective advisory grades with — using the *convective advisory's* parameters resolved via `resolve_convective_params(ctx)` (`ctx.advisory_params["convective"]` over `CONVECTIVE_PARAM_DEFAULTS`; catalog defaults when that advisory is disabled). It therefore inherits the §18 NWP-native grade, the DD-trigger AMBER cap and the tops-below-cruise filter for free. Its own `convective_min_risk` / `convective_pct_red` were **retired** — they were a second formula that diverged from the first in both directions and, aggregated under MAJORITY-ties-to-worst, inverted a briefing headline from AMBER to RED. Exactly two deviations are sanctioned: *abstention* (the #391 thin-coverage guard may turn a would-be-GREEN convective axis UNAVAILABLE — contributes nothing to `worst`, never a different grade), and the **EMBEDDED escalation** — `convective_character` EMBEDDED for that model bumps the convective axis one step, AMBER→RED only, detail `ifr.conv_embedded`. ISOLATED/SCATTERED are see-and-avoid statements that do not transfer to IMC; WIDESPREAD/ORGANIZED are realized-coverage bands the convective advisory already prices, so escalating on them would double-count. EMBEDDED is orthogonal (the deck *hiding* the cells) and the one band genuinely worse under IFR than VFR. Character is read via `classify_route_character` with `resolve_character_params(ctx)` — that advisory's own tuning, never a duplicate | `min_dep_ceiling_ft`, `min_arr_ceiling_ft`, `extent_pct_amber` (20), `extent_pct_red` (50), `extent_min_nm` (30nm), `icing_altitude_buffer_ft` |
| `ApproachFeasibilityEvaluator` | flight_rules | **The arrival question no other evaluator owns (#509):** *can I get in, on a runway I can also land on?* Joins the destination's ceiling, the runway the wind favours, and which runways actually have a published approach — a joint function of ceiling **and** wind **and** infrastructure. See the dedicated section below | `tailwind_limit_kt` (10), `crosswind_limit_kt` (20), `circling_ceiling_ft` (1000) |

### Sun

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `SunEvaluator` | sun | Informational, never go/no-go. Thin classifier over the precomputed `RouteSunAnalysis` on `ctx.sun` (built by `analysis/sun.py:compute_route_sun`). Model-independent → single `per_model=["all"]` like `ModelAgreementEvaluator`. AMBER when a low sun sits roughly down the wind-best runway on takeoff/landing (glare, recomputed from stored geometry so params honour recalc), and — for day-VFR profiles — when the leg ends near/after sunset or starts near/before sunrise (gated by `warn_near_sunset`; glare AMBER always applies). Detail text always carries the sun-side seating note. UNAVAILABLE (per-model) on old packs / when `ctx.sun is None` | `glare_azimuth_deg` (30), `glare_elev_max_deg` (15), `warn_near_sunset` (true), `sunset_margin_min` (30) |

**Sun pipeline (issue #227):** the heavy/airport-independent parts (night intervals + sun-side summary) are computed once in `tasks/analyze.py` and stored on `RouteAnalysesManifest.sun` (served to the client for cross-section night shading). The advise stage recomputes the full `RouteSunAnalysis` — adding dep/arr glare from the wind-best runway (shared `best_runway_of` helper in `analysis/airport_conditions.py`, reached via `sun.py::_consensus_best_runway`) — onto `RouteContext.sun`, where `run_advisories_from_pack` also rebuilds it so recalculation works. Solar math lives in the `euro_aip` solar primitive (`euro_aip/utils/solar.py`, astral-backed); azimuths and runway headings are both **true** degrees, so glare needs no magnetic conversion.

## Approach feasibility — the arrival intersection (#509)

`enrich_wind` (`analysis/airport_consensus.py`) picks the best runway with
**zero approach awareness** — `min(crosswind_kt, -headwind_kt)` across all ends.
So the briefing could say *"destination IFR, ceiling 600 ft, best runway 24"* at
a field where 24 has no approach and the ILS serves 06. `flight_category` grades
ceiling/vis, `airport_wind` grades wind, `ifr_feasibility` composites
airport+icing+convective — **no evaluator owned the intersection**. This one
does, with its own semantic, its own params and its own detail payload.
`enrich_wind`'s `best_runway` semantics are deliberately unchanged: the pure-wind
answer is the correct one for VFR arrivals and for the forecast map.

**Data plumbing.** `airports.py::get_runway_approaches(icaos, db_path)` mirrors
`get_runway_ends` — the wind side of the same question — and returns
`AirportApproaches` per ICAO (`models/airport_conditions.py`, sibling of
`RunwayEnd`). Each `RunwayApproach` resolves into exactly one of **three**
states, and keeping them apart is the point:

| State | Means |
|-------|-------|
| `runway_id` set | straight-in to that runway END; only set when the procedure's ident joins a **live** (non-closed) end that has a true heading, so it can always be paired with that end's wind components |
| `circling=True` | ICAO letter-suffixed circling-only procedure (`RNP A`, `NDB C`) |
| neither | alignment unresolved — treated as *uncertainty*, never as hard misalignment |

The container adds a fourth, airport-level state: `lookup_failed` (see
UNAVAILABLE below).

Circling detection keys on the single trailing letter, excluding X/Y/Z (those are
straight-in *variant* designators) and any name carrying a digit. That digit
guard is what matters: of the 262 ident-less rows in `nav.db`, only **39** are
real circling procedures — a naive "no runway ident ⇒ circling" rule would be
wrong ~85% of the time (`MIPS: RNP (LNAV) ARINC CODING` and friends).

**Wiring — two points, both required.** `RouteContext.arrival_approaches`
follows the `route_fronts` / `sun` precedent: `None` ⇒ UNAVAILABLE, so old packs
degrade cleanly. Both the briefing run and the recalculate/preview re-run build
it (`tasks/advise.py::_collect_arrival_approaches`), and the API recalc/preview/
alt endpoints now hand `airports_db_path` down — the recalc path has no resolved
`RouteConfig`, so `_arrival_icao` falls back to the recomputed airport
conditions' arrival ICAO. Miss either point and the advisory silently degrades
to a grey card on recalculate.

**Grade map.** The destination's flight category selects which logic applies —
the alignment/circling/tailwind reasoning only earns its keep once the pilot can
no longer be expected to complete the arrival visually.

| Category | Logic |
|----------|-------|
| **VFR** | GREEN, always. Visually reachable; approach infrastructure is irrelevant (GREEN not UNAVAILABLE — a benign assessment is a real assessment) |
| **MVFR** | IAP presence only, capped at AMBER. Any approach → GREEN; none → AMBER. **Never RED.** Alignment/circling/tailwind are not applied: with a 1000–3000 ft ceiling the landing can be completed visually, so a misaligned approach is not a penalty |
| **IFR / LIFR** | Full logic. GREEN = straight-in to an end whose wind is within limits and a ceiling clear of the estimated minima band. AMBER = a compromise (circling required, ceiling *inside* the band, or a tailwind still within limits). RED = a hard fact only |

**UNAVAILABLE** is reserved for "the evaluator could not run": `arrival_approaches`
absent (old pack / no nav.db), no per-model airport condition, or
`AirportApproaches.lookup_failed` — the ICAO was unknown to `nav.db`, or the
procedure query raised. It is *not* used for an airport with no approaches:
**zero procedure rows is treated as "no instrument approach available", full
stop.** There is deliberately no third state distinguishing "genuinely has none"
from "never ingested". Per-country coverage in `nav.db` is cleanly bimodal
(40–100% or exactly 0.0%), and every 0% country is outside the current European
scope, so the rule cannot misfire today. **Caveat for the US-expansion work:** in
a 0%-coverage country this would grade every IFR destination RED. Revisit before
enabling coverage there.

`lookup_failed` exists because "this field has no instrument approach" is the
**most severe input the grade map takes** (RED at IFR/LIFR), and it arrives as
the same empty approach list a parse failure would produce. Only the flag tells
them apart, so every path in `get_runway_approaches` that fails to *determine*
the picture sets it rather than returning an empty list — a data problem must not
be able to manufacture a RED. (The outer failure — the whole lookup raising —
already degrades to `arrival_approaches=None` in
`tasks/advise.py::_collect_arrival_approaches`; this closes the inner one.)

**Two rules keep it honest.**

1. **One minima table.** Decision heights come from
   `analysis/alternate_requirement.py::APPROACH_CLASS_PROXY` via
   `proxy_for_approach` — the same estimates the alternate-requirement card
   shows. A second table answering the same question on the same page is a drift
   bug waiting to happen.
2. **Asymmetric uncertainty.** Those DHs are *estimates*, which is exactly why
   `alternate_requirement` reports Likely/Marginal/Unlikely instead of a traffic
   light. Emitting GREEN/AMBER/RED from the same data makes a soft claim look
   hard, so: **estimate uncertainty may push toward AMBER, never toward RED.**
   RED needs a hard fact — no IAP at all, a ceiling/visibility below even the
   *best case* (`min(dh_lo)` / `min(vis_lo)`), an approach-served end with an
   out-of-limits tailwind *and* a ceiling that will not support circling, or the
   same best-case test applied **per approach** to an end the wind is happy with
   (below). A
   forecast *inside* the band (`[dh_lo, dh_hi]`, `[vis_lo, vis_hi]`) is AMBER —
   the AMBER middle is exactly the width of our uncertainty about the published
   minima. Neither an approach of unresolved alignment nor a served end the wind
   model did not cover can drive RED: both are our gap, not a fact about the
   arrival. An absent ceiling means "no BKN/OVC layer" (clears everything); an
   absent visibility means the model does not publish one (ECMWF visibility is
   GRIB-only) and the axis is skipped, matching `flight_category`, which also
   does not read an absent visibility as a poor one.

**Two failures reach the "no usable straight-in" fallback, and they are not the
same story.** Either every approach-served end is out on wind (genuine
misalignment — "wind favours 24; approaches serve 02"), or an end the wind is
*happy* with is blocked only by its own approach's minima. The second is not
misalignment at all, so it gets its own copy naming the runway and its approach
class. This is why `_GradedPlan` keeps the wind and minima verdicts apart
instead of merging them: the airport-wide best-case gate uses `min(dh_lo)` over
*all* approaches, so a ceiling can clear it (via, say, an ILS on the
wind-unusable end) and still sit below the wind-favoured end's own
non-precision minimum. Merging the two axes made the evaluator announce "wind
favours RWY 20; approaches serve 02, **20**" — naming the served, wind-favoured
runway as if it were unserved (caught in review on #511).

**The fallback verdict is a CROSS PRODUCT, and is built as one.** Four review
rounds on #511 each found a different cell of it rendering the wrong sentence,
because the cells were written out by hand. Two independent axes:

| `_Blocker` — what stops the arrival | `_Softening` — why it is not RED |
|---|---|
| `MINIMA` — a wind-acceptable end blocked only by its own approach's minima | `CIRCLING` — ceiling **and** visibility support circling |
| `MISALIGNED` — every approach-served end is out on wind | `UNRESOLVED` — an approach we could not match to a runway |
| `NO_STRAIGHT_IN` — no straight-in published at all (circling-only, or unresolvable) | `NO_WIND_DATA` — no wind components for the served end(s) |
| | `NONE` — nothing softens it (the only RED) |

`_fallback_detail` composes `"{blocked_<blocker>} — {soften_<softening>}"`, so
every cell has correct text by construction and a new blocker or reason costs
**one** string rather than a row or column. The bugs this replaced: an AMBER
earned by an unrelated unresolved approach kept the hard "will not support
circling" claim; the misalignment branch advised "plan for circling" at a
ceiling the same call had just ruled out; `NO_WIND_DATA` borrowed the
`UNRESOLVED` copy and sent the pilot checking plates for an alignment ambiguity
that did not exist; and a circling-only field rendered "approaches serve **-**".
A test asserts every cell resolves in all four locales and that no non-circling
reason recommends circling.

Two rules the axes encode. **Softening is never dropped to fix copy:** grading
off `circling_supported` alone would harden genuine uncertainty into RED, which
is exactly what rule 2 forbids — an unresolved approach may serve the
wind-favoured end with lower minima. And **circling needs visibility, not just a
ceiling** (`circling_visibility_m`, default 1500 m): a generous or absent
ceiling in fog that clears the straight-in floor but not the circling one used
to soften a genuine "no way in" into AMBER *and recommend circling*. Absent
values stay permissive on both terms, so a data gap never hardens a grade.

**Circling is flagged, never graded.** `nav.db` carries no circling minima, and
"circling not authorised" / night / category restrictions are not in the data at
all. `circling_ceiling_ft` (default 1000) only decides whether a misaligned
arrival softens to AMBER — it is never a circling verdict. The same rule governs
the airport-wide best-case gate: **circling-only procedures are excluded from
`_best_case_minima`**, because `APPROACH_CLASS_PROXY` holds *straight-in* minima
and real circling minima are categorically higher for the same class — blending
a circling ILS in would claim a 200 ft best case the field does not offer, an
optimistic assumption sitting inside a RED gate. A field whose every approach is
circling-only has no straight-in minimum to test against, so the gate is skipped
and the fallback speaks.

**Alignment is brittle**, so the grade is computed **per model** and the
registry's majority aggregation absorbs the disagreement (wind direction can
spread 180° across models at D-3, and alignment is discrete). Near the wind
boundary the verdict degrades to AMBER rather than flipping GREEN↔RED: crossing
the tailwind limit only removes the straight-in option; RED additionally needs
the ceiling to fail circling.

**Known gap (deliberate).** At D-0 this should prefer the TAF over the NWP
consensus so the card cannot contradict the alternates card beside it. Advisories
run at pipeline step 3 and METAR/TAF are fetched at step 3.5, so `RouteContext`
has no observation to read; the NWP consensus in `airport_conditions.arrival` is
what is graded today. Also deferred (from #509): per-candidate approach
feasibility for *alternates*, and an approach-aware "best usable runway"
alongside `best_runway` in `enrich_wind`. #510's user-declared unpublished
approaches shipped — see the next section.

No highlights are emitted — like every airport advisory this is point-in-space,
not route geometry (see the "Not emitting" list under Highlights). Web/iOS/MCP/
digest pick the advisory itself up from the catalog with no per-advisory
registration, but **three id→category tables are hand-maintained and do need an
entry** (all three list both feasibility composites, so a new one is easy to
miss): `ADVISORY_PRIORITY` (`web/ts/helpers/advisory-order.ts`) so it sorts
beside them rather than at the bottom of its band; `ADVISORY_TAG_MAP`
(`debriefs/taxonomy.py`, mirrored in `web/ts/components/debrief-taxonomy.ts` —
iOS reads it from the served catalog) → `IMC`, so a RED/AMBER verdict can be
graded against the pilot's debrief outcome; and `ADVISORY_SITUATION`
(`eval_workbench/situations.py`) → the existing `ifr_marginal` cell, so it
contributes to eval-corpus situation coverage. `ifr_marginal` rather than a new
`SITUATION_VOCAB` entry: this only grades at an IFR/LIFR destination, and a new
vocab cell changes the coverage-matrix denominator every fixture is scored
against.

### User-declared unpublished approaches (#510)

`approach_feasibility` grades a field with no published procedure as **RED** at
IFR/LIFR. That is the right default, and it over-fires in the UK, where a field
can have no AIP-published procedure while pilots routinely fly a self-briefed
**cloud break procedure** or a private/unpublished GNSS approach. `EGTF`
(Fairoaks) has **zero** rows in `procedures`, so for a pilot based there *every*
IFR arrival at their home field graded RED — enough noise to make the advisory
worth ignoring, which is worse than not having it. This is **not a data gap**:
#509 established these absences are genuine *within a surveyed country* (see
the coverage gate below). The published data is right and the
operational reality differs.

### The procedure-coverage gate — "no rows" needs a surveyed country

The #509/#510 reasoning above rests on a premise: that the source dataset
surveys instrument procedures everywhere it lists airports. That held while
`nav.db` was euro_aip's European build, and it is what makes the EGTF case a
genuine "no published approach" rather than ignorance.

It stopped holding when the DB gained **~3,600 US airports with runways and no
procedures at all**. There, zero rows means *unsurveyed*, and grading it as "no
IAP" hands the grade map its single most severe input on the strength of
missing data — observed on the first US routes briefed (#457 HRRR testing):
every US arrival graded Approach Feasibility RED, and on KCLE→KORD that
fabricated RED was driving the whole briefing headline.

The discriminator is **per-country coverage, derived from the data — never
geography**. A country counts as surveyed when any of its airports carries
procedure rows (`airports.py::_procedure_coverage_countries`, built on
euro_aip's own `with_procedures()` query, memoized per process):

| Rows at this airport | Country surveyed | Result |
|---|---|---|
| some | — | approaches exist → grade normally |
| none | **yes** | genuinely no IAP → grade (EGTF, #510 preserved) |
| none | **no** | `lookup_failed=True, coverage_gap=True` → abstain |

Per-airport emptiness proves nothing on its own — euro_aip holds procedures for
128 of 430 French airports and the other 302 genuinely have none — which is why
the gate is country-scoped. Deriving it from the data (rather than a Europe
allow-list) means the day US procedures are ingested, those airports start
grading with **no code change**.

The gate lives in the collection step, not the evaluator, because
`approach_feasibility` is not the only reader: `tasks/alternates.py`
(`has_instrument_approach`), `tasks/alternate_requirement.py` (destination
minima class) and `tasks/map_queries.py` all consume the same `has_iap`, and
would otherwise keep reading "no data" as "no approach" — which for the
alternates card would silently disqualify every US alternate. It resolves to
the existing `lookup_failed` state rather than a fourth grade state, since "we
could not determine the picture" is exactly what a coverage gap means, and
every consumer already honours it. `coverage_gap` is a diagnostic sub-reason
only, so a gap stays distinguishable from a parse failure in logs.

The question really belongs to euro_aip, which already carries an
`airac_country_coverage` table (`country_iso`, `source`, `airports_count`) that
would answer it authoritatively. That table ships **empty** today (0 rows in the
2026-07-28 `nav.db`), so the derived scan is the only signal actually available;
once it is populated, this should become a read of that table rather than a
pass over the airport collection.

**Storage is a user preference, not an advisory param.** `app_prefs_json →
declared_approach_icaos`, read by `api/preferences.py::load_declared_approaches`
(the sibling of `load_user_locale`). It is a fact about the pilot and the
airport ("I'm current on the Fairoaks cloud break"), entered once and applied
everywhere — not a per-profile judgment. It could not have been an
`AdvisoryParameterDef` anyway: `default` is typed `float`, and every param in the
catalog is numeric.

**Resolution happens in the collection step**, not the evaluator:
`get_runway_approaches(icaos, db_path, declared_icaos)` injects a synthetic
`RunwayApproach(source="user_declared")`. Every field that could confer credit
is empty **by construction** — `approach_type` is `None` so no minima class can
be inferred, `runway_id` is `None` so it can never be paired with a runway end's
wind. The type is fixed rather than user-selectable precisely so a pilot cannot
declare "ILS" and be handed a 200 ft decision height on a procedure that has no
plate. It is *not* injected over `lookup_failed`: that state means "we could not
determine the picture", and a declaration does not determine it either.

`AirportApproaches.published` is the split that keeps this honest — **everything
reasoning about minima, alignment or approach class reads it**, never
`approaches`. Miss that and the declaration (no runway, not circling) silently
reads as UNRESOLVED alignment, and the best-case minima gate starts testing
ceilings against a plate that does not exist.

| Destination | Undeclared | Declared |
|---|---|---|
| VFR | GREEN | GREEN |
| MVFR | AMBER (no IAP) | **GREEN** (own copy — never called "published") |
| IFR | RED (no IAP) | **AMBER** |
| LIFR | RED | **RED** — hard cap |

**The IFR amber does *not* fall out of the existing rules**, contrary to the
framing in #510, and this is the one place the issue's stated mechanism was
wrong. The no-straight-in fallback only softens off RED when the weather
supports circling (`circling_ceiling_ft` / `circling_visibility_m`, 1000 ft /
1500 m) — but an IFR ceiling is 500–1000 ft *by definition*, so a declared EGTF
at 700 ft would still have graded RED, leaving exactly the false REDs the
declaration exists to damp. Hence an explicit `_Softening.DECLARED` and a
dedicated no-published branch in `_grade_full`. The issue's **table** is
implemented as specified; only its rationale is corrected here.

**LIFR stays RED as an explicit cap.** With no published procedure there are no
published minima, so no DH can be claimed at all — the estimated-band logic must
not be applied to a procedure that does not exist. The cap gates both the bare
declared field and the `DECLARED` softening on a published field.

Three rules bound what a declaration can do:

1. **Circling outranks it.** When the weather genuinely supports circling,
   "plan for circling" is the more actionable line and holds regardless.
2. **It never softens below the weather** (#510 guardrail 3). A ceiling below
   the best-case DH of a *real* plate stays RED; the declaration removes the
   *infrastructure* penalty only. Ceiling, visibility, wind and crosswind
   grading are untouched.
3. **The basis is always visible** (guardrail 2). Every declared verdict names
   it — *"graded against your declared unpublished approach at EGTF, which has
   no published minima, so check your own"*. **Shared briefings are explicitly
   not handled, by decision:** a pack stores its computed manifest, so `/s/{code}`
   renders the owner's baked-in grades. We do **not** re-grade per viewer — that
   sentence is the only thing that travels with the share.

**Containment is the main hazard, and it is structural.** A self-briefed
approach cannot make a field qualify as a filed alternate under EASA NCO.OP.143
nor relieve the NCO.OP.140 trigger, so it must never reach
`alternate_requirement` or the alternates candidate gate. It cannot today:
`tasks/alternates.py` answers "does this field qualify?" from its own
`ap.procedures_query.approaches()` and never calls `get_runway_approaches`, the
only function that knows about declarations. `tests/test_declared_approach_guardrails.py`
pins that — including an enumeration of every module referencing
`has_declared_approach`, so a new consumer has to be a deliberate act rather
than an accident.

**Wiring — four paths, all required.** `BriefingOptions.declared_approach_icaos`
covers the briefing run; `api/packs.py::_load_advisory_profile` returns it as a
12th element, feeding recalculate, the settings preview, the alt-departure re-run
**and** `tasks/time_scan_runner`. The time-scan is easy to miss and matters: it
grades the planned departure through the same path and asserts shift=0
reproduces the briefing, so omitting it would make the scan contradict the card
beside it. Validation on save is `airports.py::unknown_icaos` against `nav.db`,
and an unknown code **rejects the whole write** with a 400 naming the codes —
dropping it silently would leave the pilot believing their home field is
declared when it is not.

Out of scope, deliberately: declaring approach type, minima or runway alignment;
per-runway declarations (circling-equivalent confers no alignment credit either
way); sharing the list between users; viewer-scoped re-grading of shared
briefings.

## Mitigations (advice only — #328/#330)

An advisory can carry **mitigations**: decisions that would improve a *specific
flagged sub-issue* if applied. A mitigation **never changes the grade** — same
contract as `cross_check`. A RED advisory with a mitigation is still RED.

`Mitigation` (in `models/advisories.py`):

| Field | Meaning |
|-------|---------|
| `kind` | `MitigationKind`: `ALTITUDE` / `ROUTE_POSITION` / `TIMING` (timing reserved) |
| `addresses` | stable English machine token for the sub-issue (e.g. `cruise_imc`, `climb_deck`, `descent_deck`) — NOT localized |
| `detail` | localized human phrasing (via `adv_t`) |
| `mitigated_status` | status **of the addressed sub-issue alone** if applied — NOT the advisory overall (an advisory grades several axes via `worst`; a mitigation on one axis says nothing about the others) |
| `altitude_ft` | set for `ALTITUDE` |
| `distance_nm` + `reference` | set for `ROUTE_POSITION` (`reference` = `"departure"`/`"arrival"` disambiguates the distance) |
| `profile` | optional `MitigationProfile` (`MitigationSegment` bands + `MitigationTransition` climbs/descents, #335) from the shared solver — additive, `None` on old packs and non-solver mitigations. v1 UIs render `detail`; the cross-section overlay consumes this |

Mitigations live at two levels: `ModelAdvisoryResult.mitigations` (per-model) and
`RouteAdvisoryResult.aggregate_mitigations`. The aggregate is chosen by
`_aggregate_mitigations` — the **representative-model policy**: the mitigations of
the first per-model result whose status equals the aggregate status (same
representative that sets `aggregate_detail`). Kept as a standalone function so the
policy can later swap to a conservative per-kind merge in one place. Both default
to `[]`, so old packs deserialize cleanly.

**Producers.** Two evaluators emit mitigations, both via the shared
`vertical_profile.py` min-cost path-finder (`solve`/`CostModel`, design in
[vertical-profile-solver.md](./vertical-profile-solver.md)):
`icing_escape.py` emits an `ALTITUDE` mitigation (`addresses="icing_escape"` —
"climb/descend to an ice-free band") off its own cost grid, and
`vfr_feasibility.py` emits the cloud/corridor tips below. A consumer builds a
hazard→cost `CostModel` and reads the returned profile; the same solver backs both.

**Worked example — `vfr_feasibility.py`.** Two generators:
- `_vertical_mitigation` → `ALTITUDE`, `addresses="cruise_imc"`: scans downward
  from cruise to a terrain floor (`max_terrain + _TERRAIN_CLEARANCE_FT`, 1000 ft),
  re-grading the en-route cloud axis at each step; offers the highest altitude that
  strictly improves it (prefers a fully clear GREEN band over a merely-better AMBER
  one). No mitigation when the only improving band is below the terrain floor — the
  RED is genuine. Only computed for an already-flagged (AMBER/RED) axis.
- `_corridor_mitigation` → `ROUTE_POSITION`, `addresses="climb_deck"`/`"descent_deck"`:
  when a corridor deck blocks climb-out/descent near one end but clears beyond, offers
  "climb to cruise after ~d nm" / "descend before ~d nm". Gated on BOTH a genuine
  clear/blocked split AND flyable VFR room beneath the deck along the blocked stretch
  (`_under_deck_flyable`, `mitigation_min_base_agl_ft` default 3000 ft) — otherwise the
  clear air is unreachable and the grade is genuine.
  - Since #335 both generators are unified in `_solver_mitigations` over one min-cost
    vertical profile; the corridor tips carry two extra guards (#342): a terminal tip is
    emitted only when the forcing deck is *real* — it covers ≥2 route points or ≥15 nm,
    not just the departure/arrival field's own cloud that every climb-out transits
    (`_terminal_deck_span`, **Bug A**) — and only when the break is within
    `mitigation_max_reposition_nm`. The former `<= total/2` half-route split is gone:
    `clean_terminal` already guards interior decks, so the split only produced a
    knife-edge that dropped the correct arrival tip on a fractional-mile overshoot
    (**Bug B**). The `cruise_imc` `altitude_marginal` copy reads "Marginal VMC around
    {alt} ft" (a fly-lower target, parallel to the GREEN "VMC available at {alt} ft").

**Surfacing** (each consumer treats mitigations as a soft hook, never a verdict):
- **Web / iOS**: a neutral "lightbulb" hook on the advisory card → tip detail
  (neutral blue-gray, never green/red).
- **Digest** (`prompt_builder.py`): consolidated into the deterministic
  `=== OPTIONS TO IMPROVE ===` block — `ALTITUDE` mitigations are dropped here
  (the altitude-options sub-block owns that axis and shows the cross-advisory
  trade-off); non-altitude (`ROUTE_POSITION`/`TIMING`) ones list under a Tactical
  sub-block. See [digest.md](./digest.md).
- **MCP / ChatGPT** (`connectors/views.py`): `summarize_advisories` sets a neutral
  `aggregate_mitigations_present` hook (always) and expands the full objects only in
  the same non-green/flagged window as `cross_check`; `advisory_detail` includes
  per-model `mitigations` + top-level `aggregate_mitigations` plus a `MITIGATION_NOTE`
  guardrail (sibling of `CROSS_CHECK_NOTE`). See [chatgpt-connector.md](./chatgpt-connector.md).

## Advisory Highlights (cross-section geometry — #373)

An advisory can carry **highlights**: per-model cross-section geometry locating
*where along the route and at what altitudes* the verdict comes from. Like
`cross_check` / mitigations, highlights **never change the grade** — they locate
it. Two elements, each doing exactly one job (models in `models/advisories.py`,
sibling of `Mitigation`):

| Model | Job | Shape |
|-------|-----|-------|
| `RibbonSegment` | **1-D route verdict** (judgement) | `dist_from_nm`, `dist_to_nm`, `severity` — a gapless partition of `[0, total_nm]` |
| `HighlightRegion` | **2-D scrim cutout** (focus), flagged areas only | `dist_from/to_nm`, `base_ft`/`top_ft` (both `None` = full column), `kind` (stable English token), `severity` (amber/red) |
| `AdvisoryHighlights` | container | `ribbon`, `regions`, optional `peak_dist_nm` (jump-to-worst) |

`ModelAdvisoryResult` gains `highlights: AdvisoryHighlights | None` (+ a `highlights=`
kwarg on `.build`, same pattern as `mitigations`). Old packs deserialize with `None`;
no migration (pure JSON). There is **no** `aggregate_highlights` — the cross-section
is per-model, and the web chip switches to the representative model (the same
policy as `aggregate_detail`) to cover the aggregate view at the UI level.

**Evidence contract (#393).** Three additive, legacy-safe provenance fields make
the chips do more without new geometry: `RouteAdvisoryResult.representative_model`
(the model whose per-model result sources the aggregate view — emitted from the
backend so the web client reads it instead of reimplementing the rule; the TS
`representativeModel()` in `advisory-highlights.ts` is now a one-line read of that
field, keeping a first-per-model fallback only for packs that predate it),
`ModelAdvisoryResult.primary_method_id`
(stable id of the method that controlled *this model's* grade — for a method
badge, not the user's selected method), and `HighlightRegion.reason_code` /
`metric_id` / `method_id` (stable non-localised tokens; `metric_id` lets a chip
jump to the right cross-section layer). Old packs deserialize with all absent.

**Consuming `reason_code` — the ribbon-hover tooltip (#412).** Hovering the
verdict ribbon in the cross-section surfaces *why* the advisory is flagged at the
cursor's x, not just its colour: advisory name + verdict + — when the flagged
region under the cursor carries one — the human phrasing of `reason_code`. Only
`icing_escape` / `fiki_icing` / `convective` emit a code (their colour+shape
genuinely can't disambiguate `no_escape` vs `warm_escape`, `sld` vs
`thick_transit`, `active_track` vs `dd_trigger` vs `dd_fallback`); every other region carries
`None`, so the tooltip shows just the verdict — a reason that restates the
advisory's definition would be noise. The lookup is web-side and geometry-free:
the ribbon (`RibbonSegment`) owns the verdict at a distance, the flagged regions
(`HighlightRegion`) own the reason; `ribbonSeverityAt` / `reasonCodeAt` /
`reasonLabelKey` in `web/ts/visualization/cross-section/advisory-highlights.ts`
resolve both, and a per-code localized label (`advisories.reason.<code>`, en/de/es/fr)
gives the phrasing — an unknown/absent code → verdict only, never invented text.
The tooltip is a mouse-hover interaction handled inline in `interaction.ts` (like
`current-conditions`), so it is web-only for now; an iOS tap-based equivalent is a
follow-up. The optional layer-jump via `metric_id` is deferred (a second pass).

**Populating `method_id` from the EFFECTIVE method (#408).** #393 declared these
fields but no evaluator filled them. #408 joins the two halves: the producer,
`_resolve_analyses` (`tasks/advise.py`), stamps `icing_method_effective` and
`convective_method_effective` alongside the pre-existing `cloud_method_effective`
on each `SoundingAnalysis` — the method it *actually* graded on, at the exact
branch each fallback decision is taken, so a silent fallback is recorded, not
hidden (`convective` NWP→`thermo` when `convective_nwp` is None; `icing`
`ogimet_nwp` left unset when it could not run without a native cloud envelope,
pairing with `active_icing_available=False`). The consumer: the five
method-controlled evaluators (`icing_escape`, `fiki_icing`, `ifr_feasibility` on
the icing axis; `cloud_top`, `vmc_cruise` on the cloud axis) stamp
`method_id = sounding.<axis>_method_effective` on the evidence cells they emit —
`build_regions` already threads `cell.method_id → region.method_id`, so there is
no plumbing — and roll `primary_method_id` up from the **driving region**
(`driving_method_id` in `_helpers.py`: the highest-severity region stamped with a
method, read from the same `highlights` the grade produced so the badge can't
drift from the geometry). It makes **no comparison to the grade's own severity**:
every caller passes a single method-bearing axis's regions, so the badge is simply
the method of the flagged evidence. An earlier version did match severities and
leaked an edge case in each direction — a grade escalating *past* capped-low
regions by extent (`cloud_top` ≥60% AMBER decks → RED), and a grade landing
*below* the only regions present (`vmc_cruise` sub-red OVC → AMBER off
RED-severity regions). Dropping the match removes the whole class. The aggregate view reuses the existing
`representative_model` to pick which per-model `primary_method_id` a chip shows —
no second selection rule. **The rule that matters: `method_id` is the EFFECTIVE
method, never the REQUESTED one** — a region graded on DD under fallback says
`dd`, not the `nwp` the user asked for, and must never be sourced from the
profile (where the post-#407 sparse majority store no key at all). Evaluators
with no engine-method axis (`enroute_precip`, `turbulence`, `mountain_wind`,
`freezing_precip`, `model_agreement`) stamp nothing → `method_id` stays `None`,
exactly the "when one controlled it" space the docstring reserves. Convective's
effective method is *produced* (the honesty fix) but not yet consumed by a
`method_id` axis.

**The no-swap path badges too.** An explicit DD/thermo selection replaces no data,
so `_resolve_analyses` has nothing to swap — but it still stamps `ogimet_dd` /
`dd` / `thermo`. Leaving those unset (as it first did) would make "graded on DD"
indistinguishable from "this advisory has no method axis", which is the same
absence-reads-as-something-else failure #391/#393 exist to kill. The `swap_*`
flags therefore govern only whether the *data* is replaced, never whether
provenance is recorded — which is why there is no early return for the all-DD
case, at the cost of a shallow `model_copy` on that path. `None` on a
method-bearing axis now means exactly one thing: **the method could not run**
(`active_icing_available=False` → UNAVAILABLE).

Design decisions (don't relitigate): **backend owns the geometry** (which zones
fired depends on evaluator thresholds/altitude buffers/user params — re-deriving
client-side would drift and iOS would need a third copy); **distance-space (`nm`)**
like `MitigationSegment`; **ribbon is a full partition** incl. explicit GREEN and
UNAVAILABLE segments (green must never be inferred from silence); highlights
**track params/altitude for free** because the recalc endpoint and altitude slider
re-run `evaluate_all`. The ribbon (per-point verdict) and the card badge
(route-level, extent-thresholded grade) deliberately use **different mappings** — a
GREEN advisory with a short amber ribbon run is correct.

**Emitters** (#373 shipped `vmc_cruise` + `convective`; #375 added the rest).
Every emitter builds the geometry inside the per-point loop it already runs and
builds highlights only when the model has data (`total > 0`); shared
conventions: no sounding → UNAVAILABLE ribbon segment; points skipped by an
evaluator's relevance filters (altitude buffers, terrain thresholds) are
ribbon-GREEN ("not relevant to your flight here" reads as clear); unless noted,
`peak_dist_nm` = `ribbon_peak` (center of the longest red run, else amber).

| Evaluator | Ribbon (per-point verdict) | Region kinds |
|-----------|----------------------------|--------------|
| `vmc_cruise` | OVC→red, BKN→amber, else green | `cruise_imc` — envelope of the layer(s) containing cruise |
| `convective` | HIGH/EXTREME→red, ≥`min_risk`→amber, below floor / tops-below-cruise→green | `tower` only when **both** base and top resolve (evaluator's own `check_top_ft` resolution, thermo fallback), else `tower_unresolved` full-column ghost — never a bounded box implying a base the model lacks. Peak = worst graded risk, ties → highest CAPE (matches the MCP deep-link) |
| `icing_escape` | green = no *relevant* icing (post `icing_zones_in_altitude_range` over `[0, cruise+buffer]` — icing above cruise+buffer is deliberately green) · amber = icing with viable warm-air escape (incl. tight-margin) · red = no escape (fz below terrain+margin, or fz/terrain unknown at an affected point) | `icing_band` — envelope of the relevant zones |
| `fiki_icing` | corridor points grade the transit column exactly as the route grade (SLD / SEVERE-when-`severe_is_red` / thickness ≥ red → red; thickness ≥ amber → amber); everywhere, icing within the cruise buffer → amber; thin transit-able icing away from cruise stays green | `icing_band` — envelope of zones overlapping `[0, cruise+buffer]` |
| `turbulence` | red = SEVERE CAT **anywhere in the column** (the cutout showing where it sits is the ambiguity the highlight resolves — may exceed the badge, which keys on the cruise band) · amber = MODERATE CAT overlapping cruise or strong updraft near cruise · LIGHT stays green | `cat_layer` — envelope of the triggering layers. Strong-w resolves to a single level, not a band → no `strong_updraft` cutout (skip rather than invent geometry) |
| `cloud_top` | binary: amber = can't get on top here, else green (the *amount* of amber tells the story) | `blocking_deck` — reachable layers whose `top + margin > ceiling` |
| `vfr_feasibility` | worst firing sub-axis per x: cruise IMC→red / marginal clearance→amber (ribbon only), corridor deck OVC→red / BKN→amber, en-route precip per shared classifier **capped amber**; airport flight-category colours only the endpoint segments | `cruise_imc` + `climb_deck` / `descent_deck` (deck envelope over its corridor span) — the multi-kind case |
| `ifr_feasibility` | worst per x of icing (amber) / convective (HIGH/EXTREME red, else amber); airport IFR-viability (LIFR amber, below-minimums red) colours the endpoints | `icing_band` (zones within the cruise buffer) + `tower`/`tower_unresolved` (same conventions as `convective`) |
| `mountain_wind` | non-mountain points green; mountain points: no wind data→unavailable, wind ≥ red or (signature + ≥ corroborated red)→red, ≥ amber→amber. Peak = strongest-wind affected point. All-green ribbon on a flat route (GREEN-not-UNAVAILABLE choice), not `None` | `ridge_wind` (terrain → terrain+`altitude_margin_ft`) + `wave_signature` (the corroborating inversion band, red points only — visually explains the dropped RED bar). Envelope-merging absorbs bumpy-terrain noise into per-run rectangles; if a real rotor-day pack still reads noisy, dropping to ribbon-only is a one-line change |
| `freezing_precip` | red = active FZRA/PL surface phase · amber = primed profile (warm nose, no active precip) · green otherwise | `freezing_precip_column` — `base=None` (surface) → warm-nose top from `detect_warm_nose`, falling back to a fixed shallow AGL band (terrain + 3000 ft) when the nose top doesn't resolve |
| `enroute_precip` | mirrors `classify_precip_point` (shared with the VFR composite): moderate+ snow→red, any snow / moderate+ rain / FZRA-PL→amber, light/dry→green | `precip_column` — full column (`base/top = None`) |

Not emitting (deliberate, #375): `headwind` (scalar — route graph is its home),
airport advisories (`flight_category`, `airport_wind`, `density_altitude`,
`llws` — point-in-space), `model_agreement`/`dd_nwp_agreement` (cross-model —
Compare mode), `convective_character`, `fronts` and `sun` (dedicated layers).

iOS rendering is #374; the geometry is data-driven so iOS picks all emitters up
with no further backend work.

## Single-assessment evidence helper (`_helpers.py`, #393)

The nine single-axis evaluators (`vmc_cruise`, `cloud_top`, `turbulence`,
`freezing_precip`, `mountain_wind`, `icing_escape`, `fiki_icing`,
`enroute_precip`, `model_agreement`) emit **one `EvidenceSample` list per model**
inside their per-point loop, and `summarize_evidence` derives *everything* from
it — grade counts, geometry, coverage — so the verdict and the highlight can no
longer come from two loops that drift (the class of bug #391 kept hitting).

- **`EvidenceSample(distance_nm, assessed, severity, affected=None, in_domain=True, region=None)`** —
  one route point's evidence. `severity` is the ribbon verdict; `affected`
  (defaults to `severity in {AMBER,RED}`) is the grade count. They're passed
  *separately* where an evaluator keys ribbon and grade on different predicates —
  turbulence (SEVERE-anywhere ribbon vs cruise-band grade), FIKI (corridor cutout
  vs cruise clear-air grade), enroute_precip (light rain: green ribbon, counts in
  affected). `in_domain=False` drops a point from the coverage denominator
  (mountain_wind measures coverage over mountain points only). `region` is the
  `FlaggedCell` scrim cutout.
- **`summarize_evidence(samples, total_nm, peak_dist_nm=None, speed_kt=None)`** → `EvidenceSummary(affected, assessed, domain, affected_nm, highlights, data_state, domain_nm, samples, total_nm)`.
  `affected_nm` is the **midpoint-owned-cell** distance of the affected points (the
  #391 geometry fix, landed here): each point owns the interval to its neighbours'
  midpoints, and the affected cells are summed — so extent and ribbon share one
  geometry and `affected_nm` can't contradict `affected_pct` (both count the same
  samples). `ModelAdvisoryResult.build` takes an optional `affected_nm=` override
  for exactly this. `data_state` is complete/partial/unavailable; `.below_coverage`
  is the existing `below_coverage(assessed, domain)` predicate, applied **only to a
  would-be-GREEN** verdict — never #389's binary `partial→UNAVAILABLE`, so a
  flagged verdict on thin coverage always stands.
  `.extent` is the affected population as a `RouteExtent` and `.extent_of(pred)`
  reduces any **sub-population** over the same geometry — that is how an evaluator
  whose message names a narrower population than its grade (turbulence's SEVERE
  tier, vmc_cruise's OVC bar, enroute_precip's snow split) gets a real nm for it
  instead of a scaled share of the union's (#571). Tag samples via
  `EvidenceSample.tags` when the population is not derivable from `severity`.

### The extent contract (#571)

Every route advisory answers *"how much of the flight is affected?"*, and there
is exactly **one** way to answer it: build a `RouteExtent` and format it.

- **`RouteExtent(points, domain_points, nm, domain_nm, longest_run_nm, minutes)`**
  with `pct = 100 × nm / domain_nm`. `nm` is the midpoint-owned-cell sum of the
  affected points (`route_geometry.cell_edges`) — the same geometry the ribbon
  and the convective-character contiguity gate use.
- **The percentage is distance-based, never a point ratio.** `interpolate_route`
  fills at a fixed 10 nm but inserts extra points at waypoints, so a point ratio
  and a distance ratio never agree; keying the percentage off the nm the message
  prints makes them consistent by construction.
- **`domain_nm` travels with the extent.** A domain-scoped advisory
  (`mountain_wind`: mountain points only) measures coverage against *its own*
  denominator. There is deliberately no route length available to multiply a
  domain fraction by — that was the ~4x overstatement. Such an advisory must
  also **name** its denominator in the sentence (`format_extent(ext,
  domain_label=…)`, "132nm/190nm of high terrain") and publish
  `affected_domain` so the digest prompt and the MCP views can qualify the
  percentage.
- **One extent per severity tier.** The severity word and the coverage beside it
  describe the same points: "Severe CAT over …" quotes the SEVERE extent, not
  light-and-above coverage. The tier reaches `build()` as `extent_mod=<extent>`,
  never as a bare `nm` float — a lone numerator carries neither its denominator
  nor its `distance_known`, and both omissions shipped as bugs (#571 rounds 6-7).
- **Whatever the sentence names, the object publishes.** The rule above is not
  only about severity words. Wherever a detail describes a *narrower* population
  than `affected` counts — `vfr_feasibility`'s RED, graded off IMC alone but
  counting IMC+marginal; `dd_nwp_agreement` naming its top category out of a
  union; `enroute_precip` suppressing light rain when snow queued a sentence —
  that population rides `extent_mod`/`affected_mod` so the miles a pilot reads
  are miles the API carries. The union stays on `affected_nm`, because the union
  is what the grade keys on. Three evaluators still had the mismatch after the
  primitive landed (#571 review round 8): consolidating the geometry did not by
  itself make the sentence and the field agree.
- **Contiguity is a reducer on the same geometry**, not a separate function:
  `longest_run_nm` for barrier-type hazards ("you cannot get around it") sits on
  the same object as the union `nm`. Both the convective-character EMBEDDED gate
  (`embed_min_nm`) and the VFR terminal-deck corridor tip read it.
- **Coverage may not promote below the minimum-extent floor** (`EXTENT_MIN_NM`,
  30 nm — about three points at `interpolate_route`'s fixed 10 nm spacing,
  expressed in the unit that survives a change of route length). Deliberate
  severe-hazard bypasses live in the evaluators and are unaffected. The floor is
  capped at half the domain so it can never suppress a short route that is
  largely in the hazard.
- **An unassessed point is geometry, not evidence.** Route points a model cannot
  grade are kept in the point list so `cell_edges` tiles the true route — drop
  them and the last covered point's cell swallows every uncovered mile. But that
  makes the list non-empty for a model with *no* data at all, so `if not points`
  no longer means "nothing to grade": ask `any(p.assessed …)`. Getting this
  wrong turns a model with zero soundings into a confident GREEN, which is the
  #391 false-GREEN class wearing the fix's clothes (#571 review round 9).
- **No geometry and no route length still reports the point ratio.** An advisory
  that builds without an `extent` (the airport-scoped ones: a verdict about a
  point, not a span) has `affected_pct` derived from distance — which on a
  zero-length route is `0/0`. It falls back to `affected/total`, the answer it
  gave before the percentage became distance-based. A RED verdict publishing
  `affected_pct: 0.0` reads as "nothing wrong" to the digest and the API.
- **The denominator is what the model could grade** — in-domain *and* assessed.
  Unassessable points are excluded (#391: two snowing points among eight blanks
  read as snow, not as 20% of a route the model never saw), and thinness is
  reported separately by `below_coverage` → UNAVAILABLE.
- **`format_extent` takes the `RouteExtent`**, never counts. This is what makes
  the sentence and the published `affected_nm` one number rather than two
  derivations of it.
- **A zero-length route publishes a percentage and no miles.** Origin == destination
  is a supported flight (pattern work, sightseeing); `route_extent` substitutes
  equal synthetic cells so the *ratio* stays measurable and sets
  `distance_known=False`. That flag is a publishing gate, not a formatting hint:
  `format_extent` prints "75%" with no nm, and `build()` zeroes **every** mile
  field — `affected_nm`, `domain_nm`, `total_nm`, `affected_mod_nm` — while
  taking both percentages from the extents, which are real. Missing it on one
  field put `domain_nm: 400` beside `total_nm: 0` on the MCP surface; missing it
  on the sibling field a round later published synthetic tier mileage beside a
  correctly suppressed primary.
- **Time is a display axis, not a gate.** `RouteExtent.minutes` is `nm` at
  `ctx.cruise_groundspeed_kt` (cruise TAS less the route-average headwind, from
  the wind components the pack already carries), appended to the message as
  "about 8 min in it" and suppressed below three minutes. `grade_extent` accepts
  `min_minutes` but it is **opt-in and unused**: a large share of flights fall
  back to a profile-default speed, so gating on minutes would grade one aircraft
  differently from another for reasons the pilot never set. Promote it only
  after measuring real `cruise_speed_ias_kt` coverage in prod.

### The parameter contract (#571 Stage 3)

Every coverage-driven advisory declares the **same three keys** —
`extent_pct_amber`, `extent_pct_red`, `extent_min_nm` — and **its own defaults**.
The consolidation is of *shape and semantics, not of values*: `vfr_feasibility`
stays at 15/30, `convective` at 20/50, `enroute_precip`'s snow axis at 5. Do not
collapse the defaults; the win is that they became the same parameter, with the
same meaning and the same geometry behind it, so the approach can be tuned
generically in one place.

- **Labels carry the domain word, keys do not**: "% of route in IMC", "% of route
  with poor agreement". Use `extent_min_nm_param()` rather than restating the
  floor's definition twelve times.
- **Everything reads the same direction.** `fiki_icing` used to express a
  percentage of the *clear* cruise compared with `<`; it is now affected
  polarity like everything else.
- **Stored profiles are actively rewritten** by migration `093` —
  `profile_sparsify` cannot help, because it deliberately keeps any key it cannot
  prove is a default, so a renamed-away key would linger forever doing nothing
  while the pilot believed it was live. The rename map lives in
  `analysis/advisories/extent_param_migration.py` with a lockstep TypeScript
  sibling (`renameExtentParams` in `web/ts/helpers/profile-sparsify.ts`), which
  the settings page applies on load so an unmigrated or cached profile still
  shows the pilot their tuning.
- **Two advisories deliberately stay out.** `convective_character`'s
  `isolated_max_pct` / `scattered_max_pct` are three-way *band boundaries*, not an
  amber/red pair, and `embed_min_nm` is a **contiguous-run** floor, not the union
  floor `extent_min_nm` — renaming either would state a false equivalence.
  `mountain_wind` has no coverage gate at all (it grades on wind speed), so it
  has nothing to consolidate.
- **Bonus, for free**: the settings UI pairs amber/red onto one row by base key
  (`settings-main.ts:paramSeverity`). `bkn_pct_amber` / `ovc_pct_red` had
  different bases and did not pair; under the common key they do.

## Shared Helpers (`_helpers.py`)

- **`route_extent(distances, total_nm, affected, in_domain=None, speed_kt=None)`** → `RouteExtent` — reduce per-point flags to an extent over the route's cell edges. The lower-level entry point for evaluators that don't build an `EvidenceSample` list (`convective_grading`, `enroute_precip`, the two feasibility composites)
- **`format_extent(ext, domain_label=None)`** → `"30nm/55nm (55%), about 15 min in it"` — human-readable spatial extent, taken from the `RouteExtent` itself. `domain_label` names a denominator that is not the whole route (`"of high terrain"`); the time clause appears only when the extent carries `minutes` and they exceed three
- **`resolve_cruise_tas(ctx)`** → cruise TAS (kt), always a value: aircraft/profile speed (IAS→TAS at the evaluated altitude) → the flight's own planned speed → a generic light-GA fallback. Shared by the headwind advisory and the extent time axis so both resolve the same speed the same way
- **`build_ribbon(per_point, total_nm)`** → `list[RibbonSegment]` — merge consecutive same-severity route points into runs; boundaries fall midway between adjacent points; tiles `[0, total_nm]` exactly (sorted/non-overlapping/gapless invariants tested). No-sounding points → `UNAVAILABLE` (#373)
- **`build_regions(per_point, total_nm)`** → `list[HighlightRegion]` — merge consecutive same-`kind`/`severity` flagged points (a `FlaggedCell` per flagged point, `None` otherwise) into one cutout using the **envelope** (min `base_ft` / max `top_ft`); all-`None` run stays a full column. Same cell-midpoint x-boundaries as the ribbon (#373)
- **`ribbon_peak(segments)`** → center of the longest RED run, else longest AMBER run, else `None` — generic worst-point for evaluators whose peak is pure ribbon extent (`vmc_cruise`); richer peaks (convective's highest-CAPE) are computed in the evaluator (#373)
- **`status_to_severity(status)`** / **`worst_severity(*sevs)`** → map a sub-axis `AdvisoryStatus` onto the ribbon scale and worst-of merge per-point severities — used by the composites for the multi-axis ribbon and the airport endpoint colouring (#375). `worst_severity` ranks UNAVAILABLE lowest so a flagged verdict overrides a data gap, never the reverse
- **`classify_precip_point(precip)`** / **`precip_point_severity(cls, cap_amber=)`** (in `enroute_precip.py`) → single source of the per-point precip phase/intensity bucketing, shared by `classify_enroute_precip` (grade) and the ribbon builders (standalone + VFR composite with `cap_amber=True`) so geometry cannot drift from the grade (#375)
- **`icing_zones_in_altitude_range(zones, floor_ft, ceiling_ft)`** → filter zones overlapping an altitude band. Called with `[0, cruise + buffer]` by IFR feasibility, icing escape, and FIKI to ignore icing far above cruise altitude
- **`apply_airport_endpoints(ribbon_points, dep_status, arr_status)`** → worst-merge the departure/arrival airport status into the first/last ribbon point, in place. The airport axis of a composite has no en-route extent, so it colours only the endpoints; GREEN/UNAVAILABLE leave the ribbon alone. Shared by both feasibility composites (#375)
- **`min_icing_clearance(zones, cruise_altitude_ft)`** → minimum vertical distance (ft) from cruise to nearest icing zone. Used by FIKI evaluator
- **`grade_extent(ext, amber_pct=, red_pct=None, min_nm=EXTENT_MIN_NM, min_run_nm=None, min_minutes=None)`** → the single coverage gate. Distance-based, and it applies the **minimum-extent floor** (30 nm, capped at half the domain) before coverage may promote anything. `min_run_nm` gates on `longest_run_nm` instead of the union, for barrier-type hazards. Replaced `pct_above_threshold`, which is gone rather than kept as a trap
- **`terrain_at_distance(elevation, distance_nm)`** → binary search + linear interpolation for terrain altitude
- **`max_terrain_near_point(elevation, distance_nm, radius_nm=5)`** → peak elevation within radius
- **`wind_at_altitude(cross_sections, model, point_index, target_alt_ft, target_time)`** → wind at the pressure level nearest a target altitude, for the hourly forecast nearest `target_time` (not the first hour — matters on multi-hour legs). Delegates the level pick to `analysis/wind.py:pick_wind_at_pressure`

## User Parameters

Each evaluator declares parameters with `AdvisoryParameterDef` (key, label, type, unit, default, min, max, step, **audience**). At evaluation time:

```python
params = {**defaults_from_catalog, **user_overrides}
result = evaluator.evaluate(ctx, params)
```

User overrides stored in `flight_profiles.settings_json` under `advisories: {enabled: {id: bool}, params: {id: {key: val}}, aggregation: "worst"|"majority"}`. Recalculation endpoint loads the flight's profile settings (including aggregation mode), re-evaluates without re-fetching weather data.

### Sparse persistence — persist only what differs from the default (#403)

**The rule:** absence means "follow the default", resolved in code at evaluation time — so a default can be changed centrally and reach every user who never deliberately overrode it. Two halves:

- **Engine method defaults.** The three engine methods (icing / cloud / convective) now have one declared default, `analysis/advisories/engine_methods.py::ENGINE_METHOD_DEFAULTS` = `{icing_method: "ogimet_nwp", cloud_source: "nwp", convective_method: "nwp"}`. `_resolve_analyses` resolves `None` through it (absence no longer falls through to DD/DD/thermo). The constant is served on the advisory-catalog endpoint (`engine_method_defaults`) and read by the settings page (`populateProfileForm`/`collectEngineDraft`) so the UI default and the runtime default cannot drift.
  - **#410 — `cloud_source` split from render style.** The cloud grading axis is `cloud_source` (bare `"dd"`/`"nwp"`), split from the render *style* (natural/soft/square) which is client-only (`vizSettings.cloudStyle`, localStorage). The old fused `cloud_method` (`<style>_<source>`) meant the settings "Cloud Style" select wrote a value no renderer read, and the backend threw the style away; a fresh key keeps the profile migration unambiguous (the bare form is what account-level `001_method_defaults_v2` rewrites `dd → square_nwp`). Two migrations cleared the legacy state: `alembic 078` cleared the DD fossil triple off the 873 `vfr_only`/`ifr_conservative` template profiles, then the `#405` sparsify (`alembic 079`) swept the fused `cloud_method` off every remaining profile — NWP forms dropped (absence → default), DD forms rewritten to the bare `cloud_source`. With zero legacy keys left, the read-path fallback `engine_methods.cloud_source_from_settings` was removed and all call sites read `settings.get("cloud_source")` directly (the pure `legacy_cloud_source` reducer stays — `alembic 079` uses it). The **account-level** engine methods (`icing_method`/`cloud_method`/`convective_method` in `app_prefs_json`) were retired entirely in #410 — empty for every user, never written by a client, never read by the pipeline (which grades only off the profile); `PreferencesResponse`/`PreferencesUpdate`/`_parse_service_toggles` no longer carry them.
- **Prune on save + server delete-semantics.** The one-off backfill lives server-side in `analysis/advisories/profile_sparsify.py` (`sparsify_settings` + `SparsifyStats`, driven by `alembic 079`); the ongoing prune is client-side. The settings page drops any advisory param equal to its catalog default (`web/ts/helpers/profile-sparsify.ts::pruneAdvisoryParams`) and any engine method equal to its default (`pruneEngineMethod` → **explicit `null`**, so the draft-preview endpoint — which keys on JSON presence — still grades on the resolved default, and the save deletes the stored key). Enable flags are **deliberately not** sparsified (#402: `fronts` `false` equals the catalog default yet is a meaningful override). `update_profile`'s PUT merges with `model_dump(exclude_unset=True)` + null-means-delete: an omitted key is left untouched (partial-writer safe — MCP/ChatGPT/agent are read-only, iOS/web send complete objects), a `null` deletes, any other value replaces. The `advisories` block is sent complete every save, so replacing it wholesale already prunes its params. Pruning at the default is lossless for grading (`evaluate_all` re-fills catalog defaults; `_resolve_analyses` re-fills engine defaults), so the only grading change in #402 is the engine-default flip for the ~6% of profiles that carried no explicit method keys.

### Audience tiers + catalog ordering (#387)

`AdvisoryParameterDef.audience` (`"pilot" | "advanced"`, default `"advanced"`) is a **curation mechanism** for progressive disclosure on the settings page — not a filter or tag taxonomy, and it never affects evaluation. `"pilot"` params are personal-minimum / aircraft-capability choices rendered **inline** on the advisory row (~19 today: crosswind/gust limits, ceiling/vis minima, DA thresholds, cloud/headwind margins, VFR/IFR feasibility floors, sun near-sunset gating); everything else is `"advanced"` (meteorological calibration) and hides behind a collapsed **Advanced (n)** expander. New params default to `"advanced"` so they never leak into the compact view unasked. A test bounds the pilot-tier count (≤ 25) so the compact page stays compact.

**Ordering is backend-owned** — there is no client-side `CATEGORY_KEYS` copy to drift. `registry.CATEGORY_ORDER` (by pilot-input density: `airport, flight_rules, wind, icing, cloud, convective, precipitation, turbulence, sun, fronts, model`) plus `ENTRY_ORDER` (within-category order) sort `get_catalog()`; `get_category_order()` returns the ordered category list (`{key, diagnostics}`) served alongside the catalog. `model` is flagged `diagnostics` (both its advisories are disabled-by-default dev signals) so the client renders it as a distinct "Diagnostics" group. iOS/MCP inherit the served order for free.

### Setup interview presets (#387)

`analysis/advisories/interview.py:get_interview()` returns a declarative `Interview` (in `models/advisories.py`): ordered `InterviewQuestion`s, each `InterviewOption` a patch `{enabled: {id: bool}, params: {id: {key: val}}}`. It **cannot** be derived from `audience` — one answer spans advisories (FIKI enables `fiki_icing` AND disables `icing_escape`). Invariants relied on by clients: every option of a question declares the **same key set** (re-answering is reversible) and sibling questions declare **disjoint keys** (answers never conflict). Built off the live catalog so the "standard"/default options carry real catalog defaults. v1 questions: flight rules (VFR-only hides `ifr_feasibility`), icing equipage (FIKI pair), minimums style (standard vs conservative personal minima). The client stores chosen answers under `settings_json.interview` for idempotent re-runs.

## Pipeline Integration

In `tasks/advise.py` via `run_advisories()`:
1. **Method resolution**: `_resolve_analyses(rp_analyses, icing_method, cloud_source, convective_method)` returns new objects with the user's preferred icing/cloud/convective method resolved into the active `icing_zones`/`cloud_layers`/`convective` slots via `model_copy()` — originals are never mutated. Returns the original list unchanged when no swap is needed. See [analysis.md](./analysis.md) for method details.
2. **Model filtering**: `advisory_models` preference selects which models to evaluate (default excludes `best_match`)
3. Build `RouteContext` from existing route analyses, cross-sections, elevation, airport conditions
4. Call `evaluate_all(ctx)` → `list[RouteAdvisoryResult]`
5. Save `RouteAdvisoriesManifest` to `route_advisories.json`

Also supports `run_advisories_from_pack()` for re-evaluation from saved artifacts without re-fetching.

**Altitude table** (`analysis/advisories/altitude_table.py` → `compute_altitude_table`, driven by `run_altitude_table_from_pack`): sweeps the *altitude-dependent* evaluators (`get_altitude_dependent_ids()`, derived from each catalog entry's `altitude_dependent` flag) across an altitude range at `step_ft` intervals, returning an `AltitudeTableResult` with per-altitude advisory rows plus `best_below_cruise`/`best_above_cruise` picks. Pure analysis module — does NOT import from `tasks/`. `diff_altitude_rows()` (with `row_for_altitude`) is the canonical altitude-diff primitive — "which advisories improve/worsen at altitude X vs the planned row" — shared by the digest prompt builder (the deterministic `=== ALTITUDE OPTIONS ===` block; see [digest.md](./digest.md)) and mirrored by a client TypeScript twin (`web/ts/helpers/altitude-diff.ts`). The table is **precomputed at briefing time and fed into the digest input** so the LLM phrases the altitude trade-off without inventing numbers (#259).

**Alt departure** (`run_alt_from_pack` + `derive_assessment_from_advisories`): re-runs analysis + advisories against the already-fetched forecast at a flight's `alt_departure_time`, writing `route_advisories_alt.json` and deriving an overall assessment (worst aggregate status) for the alt scenario.

## The flight-level assessment — and its UNAVAILABLE state (#392)

`derive_assessment_from_advisories` folds the manifest into the single traffic
light a pilot sees on the flight list, the briefing banner, the timing grid, the
digest email and the MCP/ChatGPT responses. It has **four** states, not three.

| Value | Means |
|-------|-------|
| `GREEN`/`AMBER`/`RED` | worst aggregate status across the advisories that graded |
| `UNAVAILABLE` | we briefed the flight and **nothing graded** — absent data, not a clear sky |
| `NULL` | no pack / not briefed yet. A *different* state; don't collapse the two |

Before #392 the empty case short-circuited to `GREEN` ("No advisory data
available") — the reason string was honest, the colour was not. That is the
opposite of what `AdvisoryStatus.worst([])` already did one layer down.

**Two producers, one verdict.** The pack's assessment is normally the **LLM
digest's**, not this function's (`api/packs.py::_build_pack_meta` prefers
`result.digest.assessment`; the derived value is the provisional/fallback, and is
always the one used for alt-departure and the time-scan grid). The digest schema
only permits GREEN/AMBER/RED, so an empty manifest gets a *confident* traffic
light out of it. Fixing the derivation alone would therefore have changed nothing
for a normal briefing. Hence:

- **`advisories_ungradeable(manifest)`** (`tasks/advise.py`) is the single test
  behind both consumers, so they cannot disagree. `None` is deliberately **not**
  ungradeable — a stage that never ran is the NULL case, not the UNAVAILABLE one.
- **`pipeline.py` phase 7 skips the digest** when nothing graded. There is nothing
  to narrate and the LLM would charge us to invent a verdict.
- **`api/packs.py` clamps** the assessment to UNAVAILABLE regardless of what the
  digest said — the backstop covering the on-demand `/digest/generate` path (which
  also 422s) and any caller that hands us a digest.
- **`notify/dispatch.py` suppresses** push, email and the badge for UNAVAILABLE.
  It is not weather news and the pilot cannot act on it; the grey badge is there
  when they next open the flight. This holds even for a per-flight "always
  notify" override — there is nothing to notify *about*.

**The trigger is binary** — *zero* graded advisories. Partial coverage still
grades on what graded (one real GREEN among nineteen gaps is a GREEN); the grey
advisory cards carry the gaps. No coverage threshold, deliberately: it would need
calibration and a judgement on which advisories are "core".

**Rendering is nearly free** — web `assessmentClass` already defaults to a grey
badge and iOS's `Assessment` enum already has an `.unavailable` case. Only the
briefing *banner* needed a new CSS class (`.assessment-unavailable`), since it
builds its class by string interpolation.

**Known gap:** the `assessment_reason` contract (`"icing=RED, cloud=AMBER"` —
red/amber only) cannot express a gap, so a *per-advisory* UNAVAILABLE is still
invisible in the timing-grid dots, which fall back to `?? 'GREEN'`. The dot
colour mappings no longer paint unknown statuses green, but closing this properly
means extending the reason format — which changes user-visible email/report copy
for normal packs.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `.../packs/{ts}/advisories` | GET | Return saved advisories JSON |
| `.../packs/{ts}/advisories/{advisory_id}/detail` | GET | Per-advisory drill-down for the iOS "why it's RED" ladder (#285) — reuses the pure shapers in `connectors/views.py` (`advisory_detail`, plus `convective_detail` for the convective advisory) so web/iOS/MCP/ChatGPT read one source |
| `.../packs/{ts}/advisories/recalculate` | POST | Re-evaluate with the flight's **saved** prefs and **persist** the recomputed artifacts into the pack dir (owner-only) |
| `.../packs/{ts}/advisories/preview` | POST | Non-persisting **draft** preview (#387): `{profile_id, enabled, params, aggregation, advisory_models, icing_method, cloud_source, convective_method}` → runs evaluators with `persist=False`, writes nothing (no `route_advisories.json`, no fronts recompute), returns the manifest. Powers the settings-page live diff. Settings resolve against `profile_id` — the profile **being edited**, which is usually *not* the profile bound to the flight supplying the pack — and any axis absent from the body falls back to that profile's saved value, so a body of just `{profile_id}` is the saved-settings baseline. Fallback keys on JSON **presence**, not `None`, so an explicit `advisory_models: null` ("no selection ⇒ all models") is honoured as a draft value. Owner-gates `profile_id` (404) rather than degrading an unowned id to catalog defaults |
| `.../packs/{ts}/advisories/altitude-table` | GET | Return the precomputed altitude table persisted at refresh (#259) — cheap cached path the slider indexes into; 404 on pre-precompute packs (client falls back to POST sweep) |
| `.../packs/{ts}/advisories/altitude-table` | POST | Altitude sweep (`step_ft` 500–5000, default 2000) → `AltitudeTableResult` |
| `.../packs/{ts}/advisories/alt` | GET | Return saved alt-departure advisories JSON |
| `.../packs/{ts}/advisories/alt/compute` | POST | Compute alt-departure advisories on-demand (requires flight `alt_departure_time`) |
| `.../advisories/catalog` | GET | `{advisories: […in display order], categories: [{key, diagnostics}]}` (#387; on the preferences router). Tolerant clients also accept the legacy bare array |
| `.../advisories/interview` | GET | Declarative setup-interview preset structure (#387; on the preferences router) |

Recalculate loads route analyses + elevation + cross-sections from disk, applies user preferences, returns fresh manifest. Recalculate and altitude-table share `_load_advisory_profile()` to resolve enabled IDs, param overrides, aggregation, advisory models, icing/cloud/convective methods, and locale.

## Frontend

**Dashboard** (`managers/advisories-ui.ts`):
- Summary bar: badge counts per severity (e.g., "3 RED 2 AMBER 5 GREEN")
- Advisory cards sorted RED → AMBER → GREEN → UNAVAILABLE
- Each card: aggregate status badge + name + info button + per-model badges + detail text
- **Altitude slider**: re-evaluates advisories at different cruise altitudes without recalculating the full pipeline (altitude-dependent evaluators only)
- **Altitude table button**: opens a popup rendering the `/advisories/altitude-table` sweep — per-altitude advisory grid with best-below/best-above-cruise picks
- **Profile selector** (`ProfileSelectorConfig`, owner-only): dropdown to switch the flight's advisory profile, re-running advisories with that profile's enabled/params/aggregation
- **Alt-time toggle**: switches the displayed advisories between the planned departure and `alt_departure_time` (`routeAdvisories` vs `altAdvisories`)
- **Advisory chips** (`handleAdvisoryChip`): clickable per-advisory chips that cross-link to the relevant map/cross-section context. Since #375 `freezing_precip` (→ `icing` preset + `sld-bands` override) and `enroute_precip` (→ `vfr` preset + precipitation route-graph override) also have chips; the mapping lives in `ADVISORY_TO_PRESET` (`web/ts/visualization/cross-section/advisory-presets.ts`), mirrored in iOS `CrossSectionPresets.swift`
- Recalculate button triggers POST endpoint and re-renders. The top-level entry point is `renderAdvisories(...)` (exported from `advisories-ui.ts`), called from `briefing-main.ts` and fed by `getEffectiveAdvisories(state)`

**Info popup** (`components/info-popup.ts`):
- Full description, category, parameter table with values used
- Reuses shared modal infrastructure from metrics info popups

**Store**: `briefingStore.routeAdvisories` + `recalculateAdvisories()` action.

**Settings page** (`settings-main.ts` `renderAdvisorySettings`, #387): renders categories/entries in the **served** order (no client `CATEGORY_KEYS`). Per advisory row: enable toggle + `audience:"pilot"` params **inline**; `audience:"advanced"` params behind a collapsed **Advanced (n)** `<details>` expander carrying a "k modified" chip (non-default advanced values are never invisibly hidden), a per-advisory **Reset** (scoped to the *advanced* params only — it sits in that expander's summary beside a chip counting advanced modifications, so resetting the inline pilot-tier minimums too would silently wipe the very values the tier exists to protect), and a per-param "· default X" hint on modified values. Amber/red threshold pairs of one quantity render on a single visual row (`renderParamGroup`, pairing only — no new metadata). The `model` category renders as a distinct **Diagnostics** group; engine-level controls (aggregation, advisory-model checkboxes, icing/cloud/convective method selectors, auto front detection) live in a collapsed **Engine settings** `<details>`. Global **Expand all / Collapse all**. A **Setup assistant** button opens the interview modal (`openSetupAssistant`/`applyInterview`) — radio-per-question, pre-selecting `settings_json.interview`, warning before overwriting a manually-modified owned key. A debounced **live preview panel** (`initAdvisoryPreview`/`runAdvisoryPreview`) previews the draft settings against the user's most recent flight+pack via `POST …/advisories/preview` and lists per-advisory `AMBER→GREEN`-style deltas — labelled a preview, persists nothing. Both halves are scoped to the profile being edited (`profile_id`): baseline = `{profile_id}` alone (that profile's saved settings), draft = the live form, engine axes included (`collectEngineDraft`, shared with `handleSave` so preview and save can't grade differently). The baseline is cached and re-fetched only on baseline-changing events (profile switch/new/duplicate/reset, save — all funnelled through `populateProfileForm`); edits debounce and re-evaluate the draft alone.

## Key Choices

- **Protocol over inheritance** — evaluators are peer classes, no hierarchy. Easier to test and extend.
- **Immutable RouteContext** — frozen dataclass prevents accidental mutation across evaluators.
- **Configurable aggregation** — MAJORITY (default, most common status, ties→worst) or WORST (conservative). Set per-profile in `advisories.aggregation`.
- **Lazy evaluation** — advisories evaluated fresh each time, not cached. Enables fast parameter tuning.
- **Altitude-aware icing** — IFR feasibility, icing escape, and FIKI all filter icing by altitude relevance. Icing above `cruise_altitude_ft + buffer` (default 2000ft) is ignored. Prevents false alerts from high-altitude icing a pilot will never encounter. Buffer is tunable per evaluator via `icing_altitude_buffer_ft`.
- **Cloud top filtering** — only considers layers pilot would enter (base ≤ ceiling). High cirrus above ceiling is irrelevant.
- **Mountain wind: GREEN not UNAVAILABLE** — if no mountains on route, "no hazard" is better UX than "N/A".
- **Model agreement evaluated once** — inherently cross-model, returned as single "all" model result.
- **Parameters as JSON, not schema** — no DB migration needed to add new evaluator parameters.

## Gotchas

- Evaluator exceptions are caught and logged — one failure doesn't break the whole advisory set. The failed evaluator is not dropped: the registry appends an explicit UNAVAILABLE `RouteAdvisoryResult` with a localized diagnostic `aggregate_detail` (via `adv_t("evaluation_failed", …)`), so a crash reads as "could not assess", never as a silently-absent "not a concern" (#391)
- `ModelAgreementEvaluator` has `per_model=["all"]` (not actual model names) since it's cross-model
- `format_extent` renders `"0nm"` when the extent has no domain (nothing measurable)
- Every percentage in the system is **distance-based** — `format_extent`'s, `grade_extent`'s and `ModelAdvisoryResult.affected_pct` — and they are the same number by construction. A point ratio anywhere is a bug
- `wind_at_altitude` picks the level via `pick_wind_at_pressure` and the hour via `at_time` — don't reintroduce a "first hourly" shortcut, it lags the route point's valid time on long legs

## References

- Data models: [data-models.md](./data-models.md) (RouteAdvisoryResult, AdvisoryCatalogEntry, etc.)
- Analysis layer: [analysis.md](./analysis.md) (sounding analysis that feeds evaluators)
- Architecture: [architecture.md](./architecture.md) (pipeline, API endpoints)
- Per-waypoint advisories: `analysis/sounding/advisories.py` (different system — vertical regimes)
