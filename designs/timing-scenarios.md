# Timing Scenarios (Flexibility)

> "Is there a better departure time?" — an opt-in, per-flight scan of alternative
> departure hours, graded on the full advisory set and ranked by improvement.

Posture is inherited from the mitigation framework: an **attention-director,
never a verdict**. The feature surfaces windows worth considering; it never tells
the pilot to move a flight.

## Intent

The weather along a route is a function of *when* you fly as much as *where*.
A flight that is AMBER for icing at 08:00 may be GREEN at 11:00, and the pilot
has no cheap way to discover that. Timing Scenarios re-grades the same route at
other departure hours and surfaces the ones that materially improve.

Everything downstream follows from one constraint: **re-grading at another hour
is only free where the weather data for that hour has actually been decoded.**

## The Flexibility toggle — user opt-in, per flight

Scanning never happens automatically. Flight create/edit carries a `flexibility`
enum (the only new column; `alt_departure_time` was reused as-is, so no DTO
break):

| Mode | Meaning | Cost class |
|---|---|---|
| **None** (default) | No scenario work — right for local yes/no flights | zero |
| **Alternate time** | Grade one user-picked date/time (the old alt-departure feature, folded in as a single-candidate scenario) | one candidate |
| **Same day** | Scan the full daylight window of the target date | ECMWF day-scan |
| **Previous / Next day** | Scan the adjacent day's daylight window too | day-scan + extra OM fetch |

- **Queued, never blocking.** The briefing pipeline is untouched: the briefing
  renders as normal, then the scan runs as a low-priority background job.
- **Delivery is polling, not SSE.** The refresh stream closes with `complete`
  when the *pipeline* finishes — before the scan does — so there is no live
  channel to push a scan-ready event on. Clients poll `GET …/time-options`.
  See `ios-app-server-api.md` for the endpoint contract and poll backoff.
- **The gate is user intent, not a relevance heuristic.** An earlier design had
  the system decide when a scan was worthwhile; that was dropped. If the user set
  a mode, the scan runs even on an all-green flight — "no better window found,
  your time is already the smoothest" is a useful answer, not waste.
- **±day has a data cost.** On-disk Open-Meteo covers only the target date
  (`fetch_multi_point` windows `start_date = end_date = target day`), so adjacent
  days need an extra OM fetch (`extend_openmeteo_adjacent_day`). Previous-day
  clamps away once in the past; Next-day can cross the ECMWF horizon, and the
  window must **visibly stop where ECMWF fidelity stops** — read the horizon from
  the max step on disk for the run, never from the nominal 90/168 h order figures.

## The decisive constraint: enrichment coverage

The tempting premise — "re-grading at another hour is free, just call
`run_alt_from_pack`" — is **half true, and the wrong half is dangerous**.

`run_alt_from_pack` does generalise: it accepts an arbitrary `alt_departure_time`
and a model subset, and re-runs analysis plus front detection at the shifted
ETAs. The re-grading *machinery* is free.

But it re-grades against the **saved pack's `cross_sections`, not the on-disk
GRIB** — and GRIB enrichment is windowed around the flight. The window differs
per model (ECMWF uses a symmetric ±3 h `ECMWF_FLIGHT_WINDOW_MARGIN`; GFS and ICON
use forward flight-window hours), and the fields that matter most — CLW/ICMR, the
icing fuel, and the GRIB cloud geometry — exist *only* inside each model's
enriched window. Open-Meteo base data spans the day, so a naive off-window
re-grade still returns *numbers*.

That is the trap. `at_time()` picks the closest stored hour and **silently clamps
past the edge**, so an off-window re-grade returns OM-clamped values *labelled
ECMWF*: a confident, wrong, provisional answer — precisely what the posture
forbids.

**Hard invariant: never grade a candidate hour whose fields are not actually
decoded for the model being claimed.** Extend enrichment to cover it, or refuse
the hour.

As built, this is enforced by explicit metadata rather than a defensive kwarg:
`compute_model_coverage` derives per-route-point `(lo, hi)` enriched spans per
GRIB model, and the scan refuses any candidate whose shifted flight window falls
outside them, recording them in **`refused_times`**. A `strict` kwarg on
`at_time` was considered and deliberately *not* added — checking coverage up
front made the backstop unnecessary, so `at_time` still clamps silently for its
~18 other callers. **If you ever bypass `compute_model_coverage`, that silent
clamp is waiting for you.**

One more constraint from the data: ECMWF `tp`/`sf`/`cp` are **step-differenced
across consecutive processed steps**, so a daylight extension must decode a
*contiguous* run of forecast hours. Cherry-picking only promising hours corrupts
the precipitation and snow deltas.

## Core idea — ECMWF-anchored, coarse-to-fine, three tiers

The design exploits a cost asymmetry: ECMWF is both the best model *and* the one
delivered to local disk, so it is the only model that can be swept broadly
without paying for downloads.

1. **Free tier (in-window).** Candidates inside the original enrichment window
   already have *every* model enriched, so they are graded **multi-model
   immediately, at zero cost**. "Leave 2 h earlier" is probably the most common
   useful suggestion, and it is free.
2. **Cheap search (background, ECMWF-only).** The job decodes the daylight ECMWF
   forecast hours — decode-only, no download, ephemeral — and sweeps ECMWF
   advisories across them at full fidelity to rank candidate windows
   (`extend_ecmwf_daylight`).
3. **Expensive confirm (on user tap, multi-model).** Only when the user taps a
   candidate do we spend the ICON/GFS download+decode (`confirm_candidate`).
   Because a candidate is a *departure shift*, the confirm needs ICON/GFS at all
   native steps spanning the shifted flight window — typically 2–5 forecast
   hours, so roughly **one briefing-equivalent** of fetch, not a single step.

The bias is deliberately in the safe direction: an ECMWF-only search can *miss* a
good window, but the confirm pass kills any candidate ICON or GFS disagree with.
We never surface a bad time; we occasionally fail to find every good one.

**Snap to native cadence, don't interpolate to clock hours.** The scan grid *is*
the model's native valid-times — 1 h where ECMWF publishes hourly, 3 h in the
later window, with the actual cadence read from the files on disk.

### The honesty ladder

```
SCANNING ──► CANDIDATES ──────────────────────► CONFIRMED (multi-model)
  bg job      in-window: confirmed for free        on user tap (off-window)
              off-window: ECMWF-only, provisional
```

Each candidate carries a `confidence`:

- **`confirmed_in_window`** — free, all models already enriched.
- **`ecmwf_only`** — provisional. "ECMWF suggests a calmer window at 09:00; other
  models not yet checked." Never claims more than the one model checked; this is
  what shows the "Check all models" affordance.
- **`confirmed`** — user-tapped multi-model result. This may be a **downgrade**
  ("actually not better — ICON sees convection at 09:30"), and **the downgrade
  case is a feature**: it shows the cross-check working, which is on-brand for
  the attention-director stance. Don't "fix" it by hiding confirmations that
  disagree with the scan.

Surface a candidate only if it improves ≥1 grade and doesn't materially worsen
anything; ranked, capped at ~3, with the alternate time pinned.

## `timing_class` — hint trigger and ranking emphasis only

Because the Flexibility toggle is the compute gate, `timing_class` no longer
decides *whether* to scan. **Every candidate hour is graded on the full advisory
set**, always — otherwise the scan could surface a window that fixed icing while
quietly introducing a crosswind. What the classification still decides:

1. **The hint** on Flexibility=None flights — which advisories being RED/AMBER
   trigger the soft lightbulb.
2. **Ranking emphasis** — what counts as an improvement worth surfacing first.

It is declarative: a `timing_class` flag on `AdvisoryCatalogEntry` (sibling of
`altitude_dependent`) with a registry helper, so a new evaluator auto-participates.
Current split across the 22 evaluators: 9 `scan` / 6 `cheap` / 7 `none`. The hint
set is `get_scan_class_ids() | get_timing_hint_ids()` — the 9 scan rows plus
`flight_category` (`timing_hint=True`); `fronts` is excluded as experimental and
default-off.

Two evaluators register their IDs via constants (`fronts`, `sun`) — grepping for
a literal `id="…"` will miss them.

**The None-flight hint is NOT BUILT.** The classification half shipped
(`timing_hint=True`, `get_timing_hint_ids()`) but nothing calls it and no UI
surface exists on either client. This is the main open piece.

## Persistence and staleness

- **Extended enrichment is ephemeral.** The daylight decode lives only inside the
  scan job; published pack artifacts are never mutated. Only `time_options.json`
  is persisted.
- **Scans are keyed by `(flight, ECMWF run)`.** A refresh on the same run reuses
  the existing scan; a new run re-scans automatically. This is why the key is the
  run and not the pack timestamp — re-running the pipeline against unchanged data
  shouldn't burn a fresh day-scan.
- Confirm results are cached on the candidate and invalidated by a new run.
- All graded candidates and their disposition are persisted, not just the winners
  (#434), so the ranking can be audited after the fact.

## Key exports

`compute_model_coverage`, `candidate_valid_times`, `covers`,
`extend_ecmwf_daylight`, `extend_openmeteo_adjacent_day`, `confirm_candidate`,
`compute_daylight_window`, `current_ecmwf_run_ts`, `run_time_scan`
(`tasks/time_scan.py`); `TimeScanStatus`, `TimeWindowScan`, `TimeScanBaseline`,
`TimeScanWindow`, `TimeCandidate`, `TimeConfirmation`, `ModelCoverage`
(`models/time_scan.py`).

## Gotchas

- `at_time()` clamps silently — coverage must be checked *before* grading, not
  discovered afterwards. This is the single most important invariant here.
- ECMWF accumulated fields need contiguous decoded steps; a sparse decode
  corrupts precip/snow.
- Enrichment windows are **per model and asymmetric** — ECMWF brackets the flight,
  GFS/ICON run forward. Never assume one model's coverage implies another's.
- The client must gate polling on `pack?.flexibility ?? flight.flexibility`: the
  pack meta carries it, and the client's flight object goes stale if Flexibility
  was edited after the briefing was opened.
- Timing data is deliberately **excluded from the offline bundle** — it is
  online-only on iOS.

## References

- Advisory framework and the evaluator registry: [advisories.md](./advisories.md)
- Enrichment windows and per-model GRIB behaviour: [fetch.md](./fetch.md)
- Endpoint contract, status codes, poll backoff: [ios-app-server-api.md](./ios-app-server-api.md)
- Original plan (archived): [archive/timing-scenario-plan.md](./archive/timing-scenario-plan.md)
- iOS port record (archived): [archive/timing-scenario-ios-port.md](./archive/timing-scenario-ios-port.md)
