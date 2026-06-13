# Regulatory Alternate Requirement (FAA 91.169 + EASA Part-NCO)

> For a briefing's destination, answer **"is a filed alternate required?"** two
> ways — **FAA (14 CFR 91.169)** and **EASA Part-NCO** — and, for each
> weather-computed divert candidate (#210), **"does it meet alternate minima?"**.
> Planning-grade and advisory, never a go/no-go verdict.

**Status: Shipped (#249).** Computed as a pipeline post-step whenever the
alternates stage ran (D-2 inward + `compute_alternates` opt-in).

## The core idea: transparent requirement + confidence band

Ceiling and visibility are two independent criteria, each evaluated from **real
forecast data** (TAF or NWP). The forecast values are never proxied. What we
lack is the **published plate minima** (DA/MDA and RVR/visibility) — we only know
the approach *type*.

Rather than hide the missing minima behind one conservative number, we **compute
the actual requirement** (e.g. EASA needs `ceiling ≥ DH + 200`), express the
unknown plate value as a **plausible range** per approach class, and report a
**confidence band**:

- **Likely** — the forecast clears even the worst-case plate minima.
- **Unlikely** — it fails even the best-case.
- **Marginal** — inside the band; genuinely undeterminable without the plate.

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
  easa_ceiling_band/easa_vis_band   ← DH+200 / vis+1500(floor 1500)
  band_qualification/band_trigger   ← verdict primitives (collapsed band ⇒ no Marginal)
  combine_qual/combine_trigger      ← worst-of-criteria
  TrendView / build_window          ← worst-case ceiling/vis over the ETA window
  nwp_window / no_forecast_window   ← fallbacks
  compute_faa_trigger / compute_easa_trigger
  compute_faa_qual / compute_easa_qual

tasks/alternate_requirement.py      ← WIRING (the only euro_aip / airports-DB touch)
  run_alternate_requirement(snapshot, airports_db_path)
    _build_destination_window()     ← destination TAF (WeatherReport.from_taf +
                                       WeatherAnalyzer.applicable_trends) else NWP
    _destination_approach_class()   ← airport.procedures_query.approaches().most_precise()
    → writes snapshot.alternates.alternate_requirement + per-candidate .faa/.easa

models/alternate_requirement.py     ← BandVerdict, TriggerVerdict, CriterionAssessment,
                                       RegAlternateTrigger, AlternateQual, AlternateRequirement
```

The euro_aip TAF→`TrendView` extraction (`_taf_instant_trends`) takes an
injectable `applicable_trends_fn`, so the whole windowing path is unit-testable
without euro_aip installed.

## Inputs (no new fetching)

| Input | Source |
|---|---|
| Destination raw TAF (D-0 only) | `snapshot.route_observations.airports[*].taf_raw` (matched on `obs.icao == destination`) |
| Destination NWP fallback (D-2/D-1) | `RouteAlternates.destination_ceiling_ft` / `destination_visibility_m` — the destination's NWP-consensus assessment at ETA, stored by `run_alternates` (worst across models) |
| Destination ETA | `RouteAlternates.eta` (rounded ETA hour) |
| Candidate ceiling/vis | `AlternateAirport.ceiling_ft` / `.visibility_m` (NWP-consensus, `mode="worst"`) |
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

EASA ceiling req = `[DH_lo+200, DH_hi+200]`; vis req = `[vis_lo+1500, vis_hi+1500]`
with a 1500 m floor. **Only ILS** is the FAA precision branch (no confirmable LPV).

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
- Forecast side already worst-case (lowest ceiling/vis across models + window) — kept.
- **Missing/unparseable forecast value → fail.** Exception: genuine clear sky
  (CAVOK/NSC/SKC) → good. A ceiling of `None` from a *present* forecast means
  "no BKN/OVC layer" (good); the only "no forecast" path is no TAF **and** no NWP
  (`has_forecast=False` → both triggers Required, `source="none"`).
- Hard gates count Marginal conservatively.

## Window builder

`build_window(instant_trends, source, include_prob30=False)` reduces the trends
applicable at each sample time (ETA−60/−30/0/+30/+60) to the worst-case ceiling
and visibility. At each instant the prevailing line is the base group plus the
latest applicable FM/BECMG (supersession by `validity_start`); TEMPO/PROB groups
can only make it *worse*. PROB policy: TEMPO + PROB40 honoured; **PROB30
disregarded** by default. `triggered_by_tempo` records whether a binding worst
value came from a temporary group.

## Surfacing

- **Alternates UI** (`briefing-ui.ts:renderRouteAlternates`): a destination
  banner (`Alternate required? — FAA: … · EASA: …`, with reason, `(forecast)` vs
  `(model estimate)`, a TEMPO tag, and an info button for the proxy caveat), plus
  two table columns — **FAA alt** (Yes/No) and **EASA alt** (Likely/Marginal/
  Unlikely, green/amber/grey). Tooltips + the candidate popup show the computed
  requirement vs forecast.
- **Plain-text digest** (`digest/text.py:_format_route_alternates`): a trigger
  line + per-candidate `FAA alt yes/no` and `EASA <verdict>` on each row.
- **LLM digest / PDF report:** out of scope (matches the alternates decision).

## Mandatory caveats

Surfaced via `AlternateRequirement.caveats`: EASA requirements are computed from
estimated plate minima expressed as a range (band reflects that uncertainty;
forecast inputs are real); per-candidate qualification uses NWP consensus not a
TAF; `source="nwp"` triggers are model estimates; planning guidance only.

## Out of scope / follow-ups

- User-entered Field-16 alternate list.
- Fetching TAFs for the divert candidates themselves.
- Real plate minima from a procedures DB (would replace the proxy ranges and
  shrink Marginal toward exact Yes/No).
- Route advisory evaluator (GREEN/AMBER/RED), isolated-aerodrome fuel rules.

## References

- Pure logic + tests: `analysis/alternate_requirement.py`, `tests/test_alternate_requirement.py`
- Wiring: `tasks/alternate_requirement.py`; pipeline step 4.1 in `pipeline.py`
- Models: `models/alternate_requirement.py`; fields added to `models/alternates.py`
- Upstream: [alternates.md](./alternates.md) (#210),
  [metar-taf-route-weather.md](./metar-taf-route-weather.md)
- euro_aip: `briefing/weather/models.py` (`WeatherReport.from_taf`),
  `briefing/weather/analysis.py` (`WeatherAnalyzer.applicable_trends`)
