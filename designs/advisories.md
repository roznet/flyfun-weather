# Route Advisory System

> Deterministic evaluation of weather hazards along a flight route with per-model severity assessments

## Intent

Provide actionable, severity-graded (GREEN/AMBER/RED) advisories from 21 hazard evaluators (grouped into icing, cloud, precipitation, turbulence, convective, wind, model, airport, feasibility, fronts, and sun categories) along the route. Evaluators analyze existing route analysis data — no additional data fetch. User-tunable parameters allow recalculation without re-running the pipeline. This is a **route-level** system (advisory per route), complementing the per-waypoint `AltitudeAdvisories` in the sounding subpackage.

## Architecture

```
RouteContext (immutable)
  ├── analyses: list[RoutePointAnalysis]   (~20 points along route)
  ├── cross_sections: list[RouteCrossSection]  (per-model forecast grids)
  ├── elevation: ElevationProfile | None
  ├── airport_conditions: AirportConditions | None  (dep + arr weather)
  ├── sun: RouteSunAnalysis | None  ├── route_fronts: RouteFrontsManifest | None
  ├── cruise_speed_ias_kt, flight_duration_hours  (headwind trip-time inputs)
  ├── models, cruise_altitude_ft, flight_ceiling_ft, total_distance_nm, locale
      ↓
Registry → evaluate_all(ctx, enabled_ids?, user_params?, aggregation?)
  ├── @register IcingEscapeEvaluator       # en-route icing
  ├── @register FIKIIcingEvaluator
  ├── @register FreezingPrecipEvaluator
  ├── @register CloudTopEvaluator          # en-route cloud
  ├── @register VMCCruiseEvaluator
  ├── @register EnroutePrecipEvaluator     # precipitation (en-route visibility proxy)
  ├── @register TurbulenceEvaluator        # en-route turbulence
  ├── @register MountainWindEvaluator
  ├── @register ConvectiveEvaluator        # convective (severity)
  ├── @register ConvectiveCharacterEvaluator  # convective (VFR-avoidability, orthogonal to severity)
  ├── @register HeadwindEvaluator          # wind (trip impact)
  ├── @register ModelAgreementEvaluator    # model quality (cross-model)
  ├── @register DDvsNWPAgreementEvaluator  # model quality (within-model)
  ├── @register FlightCategoryEvaluator    # airport conditions (+ terminal convective)
  ├── @register AirportWindEvaluator
  ├── @register DensityAltitudeEvaluator
  ├── @register LLWSEvaluator
  ├── @register VFRFeasibilityEvaluator    # composite go/no-go
  ├── @register IFRFeasibilityEvaluator
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

## The 21 Evaluators

### Icing

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `IcingEscapeEvaluator` | icing | Non-FIKI: can we descend below freezing to escape icing? Checks FZ level vs terrain + margin. Altitude-aware: ignores icing above cruise + buffer. Any no-escape point → amber; `no_escape_pct_red` escalates to red. `icing_coverage_pct_amber` warns when escapable icing covers a significant portion of the route | `terrain_margin_ft`, `tight_margin_ft`, `icing_altitude_buffer_ft`, `icing_coverage_pct_amber` (20%), `no_escape_pct_red` (15%) |
| `FIKIIcingEvaluator` | icing | FIKI-equipped: evaluates icing layer thickness and severity (transit OK, loiter not) | `thickness_amber_ft`, `thickness_red_ft`, `severe_is_red` |
| `FreezingPrecipEvaluator` | icing | Freezing rain / ice pellets: active FZRA/PL surface phase anywhere → RED (exceeds all icing certification, incl. FIKI; below-cloud hazard invisible to in-cloud icing methods). Freezing-rain-shaped profile without active precip (warm nose over sub-zero surface, via `detect_warm_nose`) → AMBER above coverage threshold | `primed_pct_amber` (5%) |

### Cloud

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `CloudTopEvaluator` | cloud | Can we fly above the clouds? Only considers layers pilot would enter (base ≤ ceiling), ignores high cirrus | `margin_ft`, `pct_amber` |
| `VMCCruiseEvaluator` | cloud | Cloud coverage at cruise altitude specifically (BKN/OVC percentage along route) | `bkn_pct_amber`, `ovc_pct_red` |

### Precipitation

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `EnroutePrecipEvaluator` | precipitation | Precipitation along the route as the en-route **visibility proxy** (no model forecasts visibility at altitude; surface phase/intensity from `PrecipitationAssessment` works for all 7 models). Snow is the VFR killer: any snow ≥ amber threshold → AMBER, widespread moderate+ snow → RED. Moderate+ rain → AMBER (capped — rain degrades, rarely prohibits). FZRA/PL count toward extent only; severity owned by `freezing_precip`. Shared classifier (`classify_enroute_precip`) feeds the VFR composite capped at AMBER | `snow_pct_amber` (5%), `snow_moderate_pct_red` (25%), `rain_pct_amber` (30%) |

### Turbulence

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `TurbulenceEvaluator` | turbulence | CAT layers at cruise + strong vertical motion. SEVERE CAT anywhere → RED | `route_pct_amber`, `strong_w_fpm` |
| `MountainWindEvaluator` | turbulence | Wind speed near significant terrain corroborated by wave signatures: an inversion overlapping ridge top (−1000/+4000ft band) or an OSCILLATING vertical-motion classification. Signature present → RED bar drops from `wind_red_kt` to `corroborated_red_kt` ("rotor day" vs "windy ridge"). Cross-ridge direction deliberately NOT assessed (1-D terrain profile — ridge orientation unknown). Only evaluates where terrain > threshold | `terrain_threshold_ft`, `altitude_margin_ft`, `wind_amber_kt`, `wind_red_kt` (40), `corroborated_red_kt` (30) |

### Other

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `ConvectiveEvaluator` | convective | Route points with convective risk ≥ threshold. Altitude-aware: ignores convection whose tops are below `cruise_ft - top_clearance_ft`. HIGH/EXTREME → instant RED. LOW risk capped at AMBER (prevents false alarms for marginal instability). **Grade floors at the DD (thermo) tier** even when the active track is the model-native NWP one (`convective_method` defaults to `"nwp"`) — a quiet NWP never suppresses a DD HIGH (#283; safety asymmetry). Also emits a per-model, **details-only** `cross_check` note via `convective_cross_check` (DD risk vs the model's native *firing* signal — precip/cover/deep-tower, #283): `dd_not_corroborated` (DD MODERATE+ but the scheme is quiet) or `model_active_dd_quiet` (the scheme fired where DD is quiet). The note never affects the grade. **This is the single source of truth for convective DD-vs-NWP divergence** (see `DDvsNWPAgreementEvaluator`). | `min_risk`, `affected_pct_amber`, `affected_pct_red`, `top_clearance_ft` (2000) |
| `ConvectiveCharacterEvaluator` | convective | Second convective axis (#294), **orthogonal to severity** — grades whether route convection is *circumnavigable VFR*, never touches the `convective` grade. Per-model (realized-coverage + K/TT signals are per model; disagreement on avoidability is itself signal). Classifies via `classify_convective_character` (sounding subpackage): NONE/UNKNOWN → GREEN; ISOLATED/SCATTERED (avoidable but committing) → AMBER; WIDESPREAD/ORGANIZED/EMBEDDED (no reliable gaps, frontal band, or cells hidden under a deck) → RED. Only considers points at/above `min_risk`; `_point_embedded` relabels cells under a BKN/OVC deck, `_front_present` promotes a widespread band to ORGANIZED. Altitude-aware (see-and-avoid needs `base_clearance_ft` VMC below cell bases) | `min_risk` (3), `showers_mm` (0.1), `isolated_max_pct` (15), `scattered_max_pct` (40), `organized_shear_kt` (35), `base_clearance_ft` (2000) |
| `HeadwindEvaluator` | wind | Route-average cruise-level headwind + trip-time delta vs still air. TAS is derived from `ctx.cruise_speed_ias_kt` (aircraft/profile cruise IAS via `atmo.resolve_cruise_speed_ias`, converted to TAS at cruise altitude), falling back to `total_distance_nm / flight_duration_hours` — both ride on `RouteContext` so recalc-from-pack still works. Altitude-aware (cross-section winds at the evaluated altitude → the altitude table shows the wind trade per level; falls back to precomputed `wind_components`). Informational bias: AMBER on mean ≥ 20kt, RED only ≥ 40kt; tailwinds report minutes saved | `mean_amber_kt` (20), `mean_red_kt` (40) |
| `ModelAgreementEvaluator` | model | Cross-model divergence (POOR/MODERATE agreement). Evaluated once, not per-model. **Disabled by default** — user must enable via profile | `min_poor_vars` (3), `poor_pct_amber`, `poor_pct_red` |
| `DDvsNWPAgreementEvaluator` | model | Within-model agreement: compares DD (thermodynamic) vs NWP tracks per model at each route point. Checks freezing-level delta and cloud layer Jaccard (only when NWP has real boundaries — `source="nwp_3d"` or `"grib"`, never `"synthesized"` which is circular). Surfaces e.g. mid-level decks GFS sees that DD misses, or ECMWF cirrus ice-supersaturation that `cc` rejects. **Convective divergence is intentionally NOT checked here** — now that the NWP convective track is model-native (#283), DD-vs-NWP convective disagreement is reported by the richer, convective-specific inline `cross_check` on `ConvectiveEvaluator`; duplicating it here would double-count the same divergence. **Disabled by default** — dev/calibration signal, not pilot-facing | `freezing_delta_ft` (2000), `cloud_overlap_min` (30%), `amber_pct` (30), `red_pct` (60) |

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
| `VFRFeasibilityEvaluator` | feasibility | Composite VFR go/no-go combining: airport flight category, en-route cloud clearance (base vs cruise), VMC compliance (BKN/OVC percentage), climb-out/descent corridor decks (BKN/OVC fully below cruise within `terminal_corridor_nm` of either end — OVC→RED, BKN→AMBER, see meteorology-decisions §10), and en-route precipitation (shared `classify_enroute_precip`, capped at AMBER in the composite). Worst of sub-assessments wins | `cloud_clearance_ft`, `imc_pct_amber`, `imc_pct_red`, `terminal_corridor_nm` (5) |
| `IFRFeasibilityEvaluator` | feasibility | Composite IFR go/no-go combining: airport IFR viability (LIFR→amber, below minimums→red), en-route icing exposure (uses shared `icing_zones_in_altitude_range()` helper over `[0, cruise + icing_altitude_buffer_ft]`, aligned with FIKI advisory), convective risk along route | `min_dep_ceiling_ft`, `min_arr_ceiling_ft`, `icing_pct_amber`, `icing_pct_red`, `icing_altitude_buffer_ft` |

### Sun

| Evaluator | Category | Logic | Key Parameters |
|-----------|----------|-------|----------------|
| `SunEvaluator` | sun | Informational, never go/no-go. Thin classifier over the precomputed `RouteSunAnalysis` on `ctx.sun` (built by `analysis/sun.py:compute_route_sun`). Model-independent → single `per_model=["all"]` like `ModelAgreementEvaluator`. AMBER when a low sun sits roughly down the wind-best runway on takeoff/landing (glare, recomputed from stored geometry so params honour recalc), and — for day-VFR profiles — when the leg ends near/after sunset or starts near/before sunrise (gated by `warn_near_sunset`; glare AMBER always applies). Detail text always carries the sun-side seating note. UNAVAILABLE (per-model) on old packs / when `ctx.sun is None` | `glare_azimuth_deg` (30), `glare_elev_max_deg` (15), `warn_near_sunset` (true), `sunset_margin_min` (30) |

**Sun pipeline (issue #227):** the heavy/airport-independent parts (night intervals + sun-side summary) are computed once in `tasks/analyze.py` and stored on `RouteAnalysesManifest.sun` (served to the client for cross-section night shading). The advise stage recomputes the full `RouteSunAnalysis` — adding dep/arr glare from the wind-best runway (shared `select_best_runway` helper in `airport_conditions.py`) — onto `RouteContext.sun`, where `run_advisories_from_pack` also rebuilds it so recalculation works. Solar math lives in the `euro_aip` solar primitive (`euro_aip/utils/solar.py`, astral-backed); azimuths and runway headings are both **true** degrees, so glare needs no magnetic conversion.

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

Mitigations live at two levels: `ModelAdvisoryResult.mitigations` (per-model) and
`RouteAdvisoryResult.aggregate_mitigations`. The aggregate is chosen by
`_aggregate_mitigations` — the **representative-model policy**: the mitigations of
the first per-model result whose status equals the aggregate status (same
representative that sets `aggregate_detail`). Kept as a standalone function so the
policy can later swap to a conservative per-kind merge in one place. Both default
to `[]`, so old packs deserialize cleanly.

**Producers.** Two evaluators emit mitigations, both via the shared
`vertical_profile.py` min-cost path-finder (`solve`/`CostModel`, design in
[future/vertical-profile-solver.md](./future/vertical-profile-solver.md)):
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

## Shared Helpers (`_helpers.py`)

- **`format_extent(affected, total, total_distance_nm)`** → `"30nm/55nm (55%)"` — human-readable spatial extent
- **`build_ribbon(per_point, total_nm)`** → `list[RibbonSegment]` — merge consecutive same-severity route points into runs; boundaries fall midway between adjacent points; tiles `[0, total_nm]` exactly (sorted/non-overlapping/gapless invariants tested). No-sounding points → `UNAVAILABLE` (#373)
- **`build_regions(per_point, total_nm)`** → `list[HighlightRegion]` — merge consecutive same-`kind`/`severity` flagged points (a `FlaggedCell` per flagged point, `None` otherwise) into one cutout using the **envelope** (min `base_ft` / max `top_ft`); all-`None` run stays a full column. Same cell-midpoint x-boundaries as the ribbon (#373)
- **`ribbon_peak(segments)`** → center of the longest RED run, else longest AMBER run, else `None` — generic worst-point for evaluators whose peak is pure ribbon extent (`vmc_cruise`); richer peaks (convective's highest-CAPE) are computed in the evaluator (#373)
- **`status_to_severity(status)`** / **`worst_severity(*sevs)`** → map a sub-axis `AdvisoryStatus` onto the ribbon scale and worst-of merge per-point severities — used by the composites for the multi-axis ribbon and the airport endpoint colouring (#375). `worst_severity` ranks UNAVAILABLE lowest so a flagged verdict overrides a data gap, never the reverse
- **`classify_precip_point(precip)`** / **`precip_point_severity(cls, cap_amber=)`** (in `enroute_precip.py`) → single source of the per-point precip phase/intensity bucketing, shared by `classify_enroute_precip` (grade) and the ribbon builders (standalone + VFR composite with `cap_amber=True`) so geometry cannot drift from the grade (#375)
- **`icing_zones_in_altitude_range(zones, floor_ft, ceiling_ft)`** → filter zones overlapping an altitude band. Called with `[0, cruise + buffer]` by IFR feasibility, icing escape, and FIKI to ignore icing far above cruise altitude
- **`apply_airport_endpoints(ribbon_points, dep_status, arr_status)`** → worst-merge the departure/arrival airport status into the first/last ribbon point, in place. The airport axis of a composite has no en-route extent, so it colours only the endpoints; GREEN/UNAVAILABLE leave the ribbon alone. Shared by both feasibility composites (#375)
- **`min_icing_clearance(zones, cruise_altitude_ft)`** → minimum vertical distance (ft) from cruise to nearest icing zone. Used by FIKI evaluator
- **`pct_above_threshold(affected, total, amber_pct, red_pct)`** → common GREEN/AMBER/RED from percentage
- **`terrain_at_distance(elevation, distance_nm)`** → binary search + linear interpolation for terrain altitude
- **`max_terrain_near_point(elevation, distance_nm, radius_nm=5)`** → peak elevation within radius
- **`wind_at_altitude(cross_sections, model, point_index, target_alt_ft, target_time)`** → wind at the pressure level nearest a target altitude, for the hourly forecast nearest `target_time` (not the first hour — matters on multi-hour legs). Delegates the level pick to `analysis/wind.py:pick_wind_at_pressure`

## User Parameters

Each evaluator declares parameters with `AdvisoryParameterDef` (key, label, type, unit, default, min, max, step). At evaluation time:

```python
params = {**defaults_from_catalog, **user_overrides}
result = evaluator.evaluate(ctx, params)
```

User overrides stored in `flight_profiles.settings_json` under `advisories: {enabled: {id: bool}, params: {id: {key: val}}, aggregation: "worst"|"majority"}`. Recalculation endpoint loads the flight's profile settings (including aggregation mode), re-evaluates without re-fetching weather data.

## Pipeline Integration

In `tasks/advise.py` via `run_advisories()`:
1. **Method resolution**: `_resolve_analyses(rp_analyses, icing_method, cloud_method, convective_method)` returns new objects with the user's preferred icing/cloud/convective method resolved into the active `icing_zones`/`cloud_layers`/`convective` slots via `model_copy()` — originals are never mutated. Returns the original list unchanged when no swap is needed. See [analysis.md](./analysis.md) for method details.
2. **Model filtering**: `advisory_models` preference selects which models to evaluate (default excludes `best_match`)
3. Build `RouteContext` from existing route analyses, cross-sections, elevation, airport conditions
4. Call `evaluate_all(ctx)` → `list[RouteAdvisoryResult]`
5. Save `RouteAdvisoriesManifest` to `route_advisories.json`

Also supports `run_advisories_from_pack()` for re-evaluation from saved artifacts without re-fetching.

**Altitude table** (`analysis/advisories/altitude_table.py` → `compute_altitude_table`, driven by `run_altitude_table_from_pack`): sweeps the *altitude-dependent* evaluators (`get_altitude_dependent_ids()`, derived from each catalog entry's `altitude_dependent` flag) across an altitude range at `step_ft` intervals, returning an `AltitudeTableResult` with per-altitude advisory rows plus `best_below_cruise`/`best_above_cruise` picks. Pure analysis module — does NOT import from `tasks/`. `diff_altitude_rows()` (with `row_for_altitude`) is the canonical altitude-diff primitive — "which advisories improve/worsen at altitude X vs the planned row" — shared by the digest prompt builder (the deterministic `=== ALTITUDE OPTIONS ===` block; see [digest.md](./digest.md)) and mirrored by a client TypeScript twin (`web/ts/helpers/altitude-diff.ts`). The table is **precomputed at briefing time and fed into the digest input** so the LLM phrases the altitude trade-off without inventing numbers (#259).

**Alt departure** (`run_alt_from_pack` + `derive_assessment_from_advisories`): re-runs analysis + advisories against the already-fetched forecast at a flight's `alt_departure_time`, writing `route_advisories_alt.json` and deriving an overall GREEN/AMBER/RED assessment (worst aggregate status) for the alt scenario.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `.../packs/{ts}/advisories` | GET | Return saved advisories JSON |
| `.../packs/{ts}/advisories/recalculate` | POST | Re-evaluate with user prefs (enabled IDs + param overrides) |
| `.../packs/{ts}/advisories/altitude-table` | GET | Return the precomputed altitude table persisted at refresh (#259) — cheap cached path the slider indexes into; 404 on pre-precompute packs (client falls back to POST sweep) |
| `.../packs/{ts}/advisories/altitude-table` | POST | Altitude sweep (`step_ft` 500–5000, default 2000) → `AltitudeTableResult` |
| `.../packs/{ts}/advisories/alt` | GET | Return saved alt-departure advisories JSON |
| `.../packs/{ts}/advisories/alt/compute` | POST | Compute alt-departure advisories on-demand (requires flight `alt_departure_time`) |
| `.../advisories/catalog` | GET | Catalog of all evaluators + parameter defs (on the preferences router) |

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
- Recalculate button triggers POST endpoint and re-renders. The top-level entry point is `renderAdvisories(...)` in `briefing-main.ts`, fed by `getEffectiveAdvisories(state)`

**Info popup** (`components/info-popup.ts`):
- Full description, category, parameter table with values used
- Reuses shared modal infrastructure from metrics info popups

**Store**: `briefingStore.routeAdvisories` + `recalculateAdvisories()` action.

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

- Evaluator exceptions are caught and logged — one failure doesn't break the whole advisory set
- `ModelAgreementEvaluator` has `per_model=["all"]` (not actual model names) since it's cross-model
- `format_extent` falls back to percentage-only if route has too few points for meaningful distance
- `wind_at_altitude` picks the level via `pick_wind_at_pressure` and the hour via `at_time` — don't reintroduce a "first hourly" shortcut, it lags the route point's valid time on long legs

## References

- Data models: [data-models.md](./data-models.md) (RouteAdvisoryResult, AdvisoryCatalogEntry, etc.)
- Analysis layer: [analysis.md](./analysis.md) (sounding analysis that feeds evaluators)
- Architecture: [architecture.md](./architecture.md) (pipeline, API endpoints)
- Per-waypoint advisories: `analysis/sounding/advisories.py` (different system — vertical regimes)
