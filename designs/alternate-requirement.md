# Regulatory Alternate Requirement (FAA 91.169 + EASA Part-NCO)

> For a briefing's destination, answer **"is a filed alternate required?"** two
> ways — **FAA (14 CFR 91.169)** and **EASA Part-NCO** — and, for each
> weather-computed divert candidate (#210), **"does it meet alternate minima?"**.
> Planning-grade and advisory, never a go/no-go verdict.

**Status: Shipped (#249).** Pipeline post-step (4.1) whenever the alternates stage
ran (D-2 inward + `compute_alternates` opt-in), `airports_db_path` is set, and we
are not in `historical_mode`. Wrapped in try/except — a failure warns and leaves
`snapshot.alternates.alternate_requirement` as `None`, so every consumer must
tolerate its absence.

## The core idea: transparent requirement + confidence band

Ceiling and visibility are two independent criteria, each evaluated from **real
forecast data** (TAF or NWP). The forecast values are never proxied. What we
lack is the **published plate minima** (DA/MDA and RVR/visibility) — we only know
the approach *type*.

Rather than hide the missing minima behind one conservative number, we **compute
the actual requirement** (e.g. an ILS alternate needs `ceiling ≥ DH + 200` under
NCO.OP.143), express the unknown plate value as a **plausible range** per approach
class, and report a **confidence band**: **Likely** (clears even the worst-case
plate minima), **Unlikely** (fails even the best case), **Marginal** (inside the
band — genuinely undeterminable without the plate).

**FAA** thresholds are fixed regulatory numbers (2000/3 trigger; 600-2 / 800-2
alternate), so the band collapses (`req_lo == req_hi`) and FAA stays **binary**
(Yes/No, never Marginal). **EASA** derives thresholds from the unknown plate
minima + margins, so it uses the 3-state band. Where a hard yes/no is forced,
**Marginal counts conservatively** (marginal alternate → not qualifying; marginal
trigger → required).

## Architecture

```
analysis/alternate_requirement.py   ← PURE (no I/O, no euro_aip) — all the logic
  APPROACH_CLASS_PROXY              ← estimated plate-minima ranges (the only tuning surface)
  proxy_for_approach()              ← approach_type string → ApproachProxy | None (VFR)
  easa_alt_ceiling_band/easa_alt_vis ← NCO.OP.143 selection minima (DH+200/1500 | DH+400/3000)
  band_qualification/band_trigger   ← verdict primitives (collapsed band ⇒ no Marginal)
  combine_qual/combine_trigger      ← worst-of-criteria
  TrendView / build_window          ← worst-case ceiling/vis over the ETA window
  nwp_window / no_forecast_window   ← fallbacks
  compute_faa_trigger / compute_easa_trigger   ← 91.169 / NCO.OP.140
  compute_faa_qual / compute_easa_qual         ← 91.169 / NCO.OP.143

tasks/alternate_requirement.py      ← WIRING (the only euro_aip / airports-DB touch)
  run_alternate_requirement(snapshot, airports_db_path, *, now=None)
    _build_destination_window()     ← destination TAF (WeatherReport.from_taf +
                                       WeatherAnalyzer.applicable_trends) else NWP
    _collect_candidate_tafs()       ← reuse route_observations TAFs + fetch the gaps
    _fetch_candidate_tafs()         ← RouteWeatherService (corridor=1) for gap ICAOs
    _candidate_taf_window()         ← per-candidate TAF window (reuses _build_destination_window)
    _destination_approach_class()   ← airport.procedures_query.approaches().most_precise()
    → writes snapshot.alternates.alternate_requirement + per-candidate .faa/.easa
      (per-candidate qual from a TAF covering the ETA when available, else NWP)

models/alternate_requirement.py     ← BandVerdict, TriggerVerdict, CriterionAssessment,
                                       RegAlternateTrigger, AlternateQual, AlternateRequirement
```

The euro_aip TAF→`TrendView` extraction (`tasks/…:_taf_instant_trends`) takes an
injectable `applicable_trends_fn`, so the whole windowing path is unit-testable
without euro_aip installed.

## Inputs (no new fetching)

| Input | Source |
|---|---|
| Destination raw TAF (D-0 only) | `snapshot.route_observations.airports[*].taf_raw` (matched on `obs.icao == destination`) |
| Destination NWP fallback (D-2/D-1) | `RouteAlternates.destination_ceiling_ft` / `destination_visibility_m` — the destination's NWP-consensus assessment at ETA, stored by `run_alternates` under the user's advisory aggregation mode (majority = median of the winning-category pool; worst = worst across models). See "Aggregation mode & TAF precedence" below. |
| Destination ETA | `RouteAlternates.eta` (rounded ETA hour) |
| Candidate ceiling/vis | A candidate TAF covering the ETA when available (D-0; reused from `route_observations` or gap-fetched), else `AlternateAirport.ceiling_ft` / `.visibility_m` (NWP-consensus under the same aggregation mode). `AlternateQual.source` records which. |
| Candidate / destination approach class | `best_approach_type` (candidates) / `procedures_query.approaches().most_precise()` (destination) |

The NWP fallback deliberately reuses the alternates stage's own destination
assessment rather than recomputing `AirportConditions.arrival`, so the trigger is
NWP-consistent with the candidate qualifications (same shared code path, same ETA
hour).

## Proxy ranges (the only tuning surface)

Keyed on the values that actually exist in `nav.db` (`procedures.approach_type`).
There is **no LPV/LNAV granularity** in the data — `approach_type` is the ICAO
chart family; minima lines live inside the chart. So the GNSS bucket spans
LPV-best to LNAV-worst and the Marginal band absorbs the ambiguity.

| Class | `approach_type` | DH range (ft) | Vis range (m) | FAA branch |
|---|---|---|---|---|
| Precision | `ILS` | 200–300 | 550–1500 | precision → 600-2 |
| GNSS | `RNP`, `RNAV` | 250–600 | 1000–2000 | non-precision → 800-2 |
| Non-precision | `VOR`, `NDB`, `LOC` | 400–900 | 1500–3000 | non-precision → 800-2 |
| Unknown/other | empty/NULL/TACAN/unmapped | 400–900 | 1500–3000 | non-precision → 800-2 |
| No IAP | `has_instrument_approach=False` | VFR proxy | VFR proxy | VFR proxy |

**Only ILS** is the FAA precision branch (no confirmable LPV). The DH ranges feed
the EASA bands (below); the vis ranges are now **descriptive only** — EASA uses
the fixed NCO.OP.143 visibilities, not `published_vis + margin`.

## EASA: two distinct tests (NCO.OP.140 vs NCO.OP.143)

EASA splits into two separate regulatory tests. (The FAA side hides this because
its trigger and alternate minima are unrelated fixed numbers.) Conflating them was
a real bug — the trigger used the low *selection* minima, so IFR destinations
wrongly read "no alternate required."

### Destination trigger — NCO.OP.140 (is an alternate required?)
An IFR flight needs a destination alternate **unless**, for ETA−1h..ETA+1h:
- ceiling ≥ **DH/MDH + 1000 ft**, **and** visibility ≥ **5000 m**.

No IAP at destination → must be VMC (proxy ceiling **1500 ft** / 5000 m). This is a
far higher bar than selection minima, so an IFR destination requires an alternate.
`EASA_DEST_CEILING_MARGIN_FT=1000`, `EASA_DEST_VIS_M=5000`.

**Candidate forecast source (D-0 TAF, mirroring the destination trigger).**
Per-candidate qualification prefers a **TAF covering the ETA** over the NWP
consensus, through the same `_build_destination_window` / `build_window` machinery
(identical TEMPO/PROB and CAVOK policy). TAFs are sourced **reuse-first,
fetch-the-gaps**: `_collect_candidate_tafs` reuses anything already in
`snapshot.route_observations` (the 30 nm corridor) and fetches the rest via
`RouteWeatherService` with a minimal corridor (`tasks/verification.py`'s
arbitrary-ICAO pattern). The whole TAF path is gated on `route_observations` (D-0
only) — at D-1/D-2 a current TAF wouldn't cover the ETA, so candidates stay on NWP.
The fetch is wrapped so a failure degrades silently to NWP, and a TAF that parses
but yields neither ceiling nor visibility does **not** override a valid NWP
consensus (a degenerate parse must not flip a candidate to a fail).

NB the candidate window is built at the **destination** ETA (`RouteAlternates.eta`),
not a per-candidate divert time — `AlternateAirport` carries no ETA of its own
(`_candidate_taf_window`'s "divert ETA" docstring is aspirational). Fine at GA
divert distances; if a real per-candidate ETA ever lands, thread it through here.

### Alternate selection — NCO.OP.143 (does candidate X qualify?)
Tiered on the approach DH; **visibility is a fixed value per tier**:

| Approach | Ceiling | Visibility |
|---|---|---|
| IAP, DH < 250 ft (ILS) | DH/MDH + 200 ft | 1500 m |
| IAP, DH ≥ 250 ft (RNP / non-precision) | DH/MDH + 400 ft | 3000 m |
| No IAP | 2000 ft (or min-safe IFR altitude) | 5000 m |

We tier by class — ILS is the only confirmable DH<250 class; RNP/non-precision
default to the demanding DH≥250 tier (conservative given the LPV/LNAV gap). The DH
proxy range still yields a ceiling **band** (Likely/Marginal/Unlikely); the
visibility bar is fixed.

## Verdict logic

Per criterion, forecast `F` vs band `[lo, hi]`:
- **Qualification** (higher better): `F ≥ hi` → Likely; `F < lo` → Unlikely; else Marginal.
- **Trigger** (lower forces alternate): `F ≥ hi` → not-forced; `F < lo` → Required; else Marginal.
- Combine: worst wins.
- FAA: `lo == hi` ⇒ only the two extremes.

## Conservative bias

- Proxy ranges bracket reality; `hi` end at/above the class maximum.
- Ambiguous/unmapped `approach_type` → most-demanding non-precision range.
- No procedure data (`approach_filter_relaxed`) → VFR-only treatment + surfaced caveat.
- TAF side (D-0) already worst-case over the ETA window (lowest ceiling/vis across
  prevailing + TEMPO/PROB) — kept. See "Aggregation mode" below for the NWP side.
- **Missing/unparseable forecast value → fail.** Exception: genuine clear sky
  (CAVOK/NSC/SKC) → good. A ceiling of `None` from a *present* forecast means
  "no BKN/OVC layer" (good); the only "no forecast" path is no TAF **and** no NWP
  (`has_forecast=False` → both triggers Required, `source="none"`).
- Hard gates count Marginal conservatively.

### Aggregation mode & TAF precedence

Source precedence is fixed and TAF-first: when a destination TAF covers the ETA
`_build_destination_window` (in `tasks/`) returns the TAF's own worst-case window
**without consulting NWP at all** — the `nwp_ceiling`/`nwp_vis` args are read only
on the `else` branch. TAFs exist only at D-0, so D-0-with-TAF uses the TAF window
(aggregation mode irrelevant) and D-1/D-2 falls back to NWP consensus.

On that NWP fallback the consensus is reduced under the **user's advisory
aggregation preference** (PR #346), not a fixed `mode="worst"`: `majority`
(app default) → median of the winning-category pool; `worst` → worst across
models. **Intentional coupling** so the alternate card and the airport arrival card
show the same category for the same airport. The consequence — a noise-reduction
display preference can, at a borderline destination where models disagree, soften
the NWP-fallback trigger toward "no alternate required" — is accepted because the
whole assessment is planning-grade (the DH is a proxy) and at D-0, when it matters
most, a real TAF supersedes NWP entirely. Pinned by
`test_alternate_requirement.py::TestAggregationModeAffectsTrigger`.

## Window builder

`build_window(instant_trends, source, include_prob30=False)` reduces the trends
applicable at each sample time (ETA−60/−30/0/+30/+60) to the worst-case ceiling
and visibility. At each instant the prevailing line is the base group plus the
latest applicable FM/BECMG (supersession by `validity_start`); TEMPO/PROB groups
can only make it *worse*. PROB policy: TEMPO + PROB40 honoured; **PROB30
disregarded** by default. `triggered_by_tempo` records whether a binding worst
value came from a temporary group.

### Conservative vs. the letter of the rule
We deliberately treat **TEMPO and PROB40 as governing** the verdict (a dip below
minima makes the field fail) and **PROB30 as advisory** (noted, not counted).
Strictly, Part 91 / Part-NCO let a pilot disregard PROB lines and assess a TEMPO by
expected duration and fuel — so our verdict is a notch more conservative than the
legal minimum. The pilot still sees the full picture: the destination popup lists
the steady-state conditions and every TEMPO/PROB group with how it was treated
(`main_body_ceiling_ft`/`_visibility_m` + `conditionals[]`, each tagged
`counted`). Recorded in
[meteorology-decisions.md](./meteorology-decisions.md).

## Surfacing

- **Alternates UI** (`web/ts/managers/briefing-ui.ts:renderRouteAlternates`): a destination
  banner (`Alternate required? — FAA: … · EASA: …`, with reason, `(forecast)` vs
  `(model estimate)`, a TEMPO tag), plus two table columns — **FAA alt** (Yes/No)
  and **EASA alt** (Likely/Marginal/Unlikely, green/amber/grey). Tooltips + the
  candidate popup show the computed requirement vs forecast.
  - **Clickable verdict pills.** Each banner verdict is a button →
    `renderAltReqRegimePopup`: verdict, worked reason, a criteria table (forecast vs
    the threshold that makes an alternate unnecessary, with ceiling-band provenance),
    TAF TEMPO/PROB detail. The **(i)** is an *about & caveats* overview only, not a
    per-verdict explanation. Pills and per-candidate qual badges share `.alt-reg-btn`,
    so the trigger handler (keyed on `data-altreq-regime`) must run first.
  - **Trigger provenance.** `RegAlternateTrigger.ceiling_basis` mirrors the
    per-candidate one with the *trigger* margin — EASA `"{class}: est DH {lo}–{hi} ft
    + 1000 ft margin (NCO.OP.140)"`, FAA `"fixed 2000 ft / 3 SM (14 CFR 91.169)"`.
  - **Per-candidate provenance.** `AlternateQual.source` (`"taf"`|`"nwp"`) renders as
    a "Based on: TAF (forecast)" / "NWP consensus (model estimate)" line in the qual
    popup and a `via TAF` / `via model` tag in the text digest, mirroring the banner
    badge. `AlternateQual.ceiling_basis` is a display-only string under the required
    value: EASA `"{class}: est DH {lo}–{hi} ft + {200|400} ft alternate margin
    (NCO.OP.143)"`, FAA the fixed value (`"ILS (precision): fixed 600-2"`). NB the
    **selection** margin (+200/+400) is *not* the destination **trigger** margin
    (+1000); conflating them is the NCO.OP.140-vs-143 trap.
  - **EASA trigger wording.** FAA stays binary (`Not required`/`Required`); the EASA
    trigger reads as a band (`"Unlikely required"` / `"Possibly required"` /
    `"Required"`). The word **"required" is always attached** so it never flips
    polarity against the per-candidate **EASA alt** column (there `Likely` =
    *qualifies* = good; an un-suffixed `Likely` on the trigger would read as *bad*).
  - **Worked EASA reason** (`_easa_trigger_reason`, surfaced verbatim in banner +
    digest). Rather than "comfortably above minima", it inverts the NCO.OP.140 bar
    (`ceiling ≥ DH + 1000 ft`) into the **break-even DH = ceiling − 1000** and states
    it against the class's typical minima range, e.g. *"ILS: … forecast ceiling
    1597 ft permits DH up to ~597 ft, above typical ILS minima (~200–300 ft); vis
    22840 m ≥ 5000 m"* + a conclusion line ending on the actionable break-even (the
    pilot checks the published plate minimum against it). Two invariants: the
    source badge is never repeated in the string (UI/digest show it separately),
    and the `above/within/below` word is exactly the band edge, so prose and verdict
    can never disagree. `approach_label=` only labels; it never moves the verdict.
- **Plain-text digest** (`digest/text.py:_format_route_alternates`): a trigger
  line + per-candidate `FAA alt yes/no` and `EASA <verdict>` on each row.
- **iOS** (`Views/Briefing/AlternatesView.swift`, DTO `Models/API/AlternatesResponse.swift`):
  deliberately reduced — a banner with an FAA and an EASA verdict chip plus
  per-candidate qual chips. The Swift structs decode a **display subset**
  (`regime`/`status`/`reason` only): no `ceiling_basis`, no `source` badge, no
  `conditionals`, no popup. Widening the UI means widening the DTO first; run the
  `sync-ios-web` skill after touching either side.
- **MCP / agent connector** (`connectors/views.py`): the alternates view emits
  `alternate_required: {faa, easa}` (raw status strings) plus per-candidate regime
  verdicts, so `get_alternates` and the ChatGPT Action see the trigger too.
- **LLM digest / PDF report:** out of scope (matches the alternates decision).

## Mandatory caveats

Surfaced via `AlternateRequirement.caveats`: EASA requirements are computed from
estimated plate minima expressed as a range (band reflects that uncertainty;
forecast inputs are real); per-candidate qualification uses a TAF when one covers
the ETA window (D-0) and NWP consensus otherwise (per-candidate
`AlternateQual.source` records which); `source="nwp"` triggers are model
estimates; planning guidance only.

## Other consumers of the pure logic

Two features import from `analysis/alternate_requirement.py` — deliberate reuse, so
the shared primitives move more than this card.

- **Forecast map** (`tasks/map_queries.py:_alt_required`): per-airport "alternate
  required?" marker flags, via `nwp_window` + `compute_faa_trigger` /
  `compute_easa_trigger` (EASA `MARGINAL` collapses to required). **One deliberate
  divergence:** a *missing visibility alone* is not a fail there (ECMWF vis is
  GRIB-only and not republished to the map), whereas the briefing path keeps
  missing-vis → fail. Don't "unify" that without checking what the map has.
- **`approach_feasibility` advisory** (#509, see [advisories.md](./advisories.md)):
  consumes `APPROACH_CLASS_PROXY` / `proxy_for_approach` for a *different* question
  — "can I get in, on a runway I can also land on?", not "is a filed alternate
  required?". One minima table on purpose, no drift. It adds the
  asymmetric-uncertainty rule: estimate uncertainty may push it to AMBER, never RED.

## Out of scope / follow-ups

- User-entered Field-16 alternate list.
- Real plate minima from a procedures DB (would replace the proxy ranges and
  shrink Marginal toward exact Yes/No).
- Isolated-aerodrome fuel rules.

## References

- Pure logic + tests: `analysis/alternate_requirement.py`, `tests/test_alternate_requirement.py`
- Wiring: `tasks/alternate_requirement.py`; pipeline step 4.1 in `pipeline.py`
- Models: `models/alternate_requirement.py`; fields added to `models/alternates.py`
- Upstream: [alternates.md](./alternates.md) (#210),
  [metar-taf-route-weather.md](./metar-taf-route-weather.md)
- euro_aip: `briefing/weather/models.py` (`WeatherReport.from_taf`),
  `briefing/weather/analysis.py` (`WeatherAnalyzer.applicable_trends`)

### Regulations
- **FAA 14 CFR 91.169** — IFR flight plan: alternate required (2000/3 trigger;
  600-2 / 800-2 alternate minima).
- **EASA NCO.OP.140** — destination alternate aerodromes (when an alternate is
  required: ceiling ≥ DH/MDH + 1000 ft and vis ≥ 5000 m).
  [CAA](https://regulatorylibrary.caa.co.uk/965-2012/Content/Document%20Structure/07%20NCO/2%20Regs/19210_NCOOP140_Destination_alternate_aerodromes_aeroplanes.htm) ·
  [EASA](https://www.easa.europa.eu/en/easy-access-rules/4e4220/ERULES-1963177438-13656)
- **EASA NCO.OP.143** — destination alternate planning minima (whether a field
  qualifies: DH<250 → +200 ft / 1500 m; DH≥250 → +400 ft / 3000 m; no IAP →
  2000 ft / 5000 m).
  [CAA](https://regulatorylibrary.caa.co.uk/965-2012/Content/Document%20Structure/07%20NCO/2%20Regs/19245_NCOOP143_Destination%20alternate%20aerodromes%20planning%20minima%20-%20aeroplanes.htm) ·
  [EASA](https://www.easa.europa.eu/en/easy-access-rules/4e4220/ERULES-1963177438-19090)
