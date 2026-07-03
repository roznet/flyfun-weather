# Timing scenario scan — better departure-window discovery

> **Status (2026-07-02): decisions locked — ready to implement (next:
> `/worktree-init`).** History: brainstorm 2026-07-01 → code review of the reuse
> premise (same day, reshaped around the enrichment window) → full code-verified
> plan review + flow decisions 2026-07-02 (Flexibility toggle, background
> scenario job, all open questions resolved — see *Decisions locked*).
> Implements the `MitigationKind.TIMING` axis reserved in the mitigation
> framework (#330). Do NOT add to `designs/INDEX.md` (plans are not
> MCP-discoverable by house rule).

## Goal

When a pilot has schedule flexibility, let them ask **"is there a better
departure time?"** — without re-running a full multi-model briefing for every
candidate hour. GA pilots commonly have ±a-day flexibility; surfacing a calmer
window is high-value.

Posture (non-negotiable, inherits from the mitigation framework): an
**attention-director, never a verdict**. Neutral soft hook (lightbulb, like the
mitigations — never green/red), never auto-switches the plan, never overclaims.

## The user flow — the Flexibility toggle

Scanning is **user opt-in per flight**, not automatic. Flight create/edit gains
a **Flexibility** setting:

| Mode | Meaning | Cost class |
|---|---|---|
| **None** (default) | No scenario work — right for local yes/no flights | zero |
| **Alternate time** | Grade one user-picked date/time (the old alt-departure feature, now a single-candidate scenario) | one candidate |
| **Same day** | Scan the full daylight window of the target date | ECMWF day-scan |
| **Previous day** / **Next day** | Scan the daylight window of the adjacent day as well | day-scan + extra OM fetch |

- **Queued analysis, never blocking.** The main briefing pipeline is untouched —
  the briefing renders exactly as today, then the scenario job starts as a
  low-priority background job. The briefing page shows **"Scenarios running…"**
  and fills in results when ready.
- **Delivery is polling, not SSE.** The refresh SSE stream (`POST
  /refresh/stream`) closes with `complete` when the pipeline finishes
  (`api/packs.py:1990`) — before the scan does — so there is no live channel for
  a `time_scan_ready` event. The client polls `GET .../time-options` (backed by
  a scan-status field in pack meta) after `briefing_ready`. Same for confirm
  results.
- **User intent gates *whether*; a cheap early-exit may still gate *how much*.**
  The earlier auto relevance gate is **dropped** — if the user set a scan mode,
  the scenario job runs, even on an all-green flight (the honest result "no
  better window found — your time is already the smoothest" is useful, not
  waste). But a **quick "day pinned bad" check survives as step 0 of the scan
  job** (benchmarked, not assumed — decision B): when the hazard is synoptic and
  uniformly bad all day, exiting early with "no better window today — hazard is
  pinned all day" both saves compute and is itself a fast, useful answer.
  Two honest ways to build it, to compare when slice 2 gives real cost numbers:
  - **OM variance sweep** (free data already on disk) — but OM has no icing
    signal (CLW/ICMR are GRIB-only), so it can only ever gate
    convective/cloud/wind days, never icing.
  - **Coarse-ECMWF-first early-exit** — decode the daylight window at 3h cadence
    first (~4–5 fhours), and only refine to 1h if any coarse hour beats the
    baseline. Honest for *all* hazards including icing, and the coarse decode is
    reusable work, not a throwaway check. (Check the accumulated-field
    step-differencing behaves at 3h spacing before relying on precip at the
    coarse pass.)

  `timing_class` survives with a reduced role (below).
- **Hint for `None` flights** (keeps the proactive attention-director goal):
  when a hint-class advisory is RED/AMBER at the planned time, show the soft
  lightbulb — "this hazard varies through the day — set Flexibility to scan for
  a better window." Costs nothing, never auto-scans.
- **±day realities.** Previous/Next day need an **extra OM fetch**: OM data on
  disk covers only the target date — the pipeline windows the fetch with
  `start_date = end_date = target day` (`tasks/fetch.py:275-313`,
  `fetch_multi_point`); the next day is present only when the flight crosses
  midnight. Previous day clamps away when already in the past; Next day can
  cross the ECMWF horizon for far-out flights — the flexibility window must
  **visibly stop where ECMWF fidelity stops**. The horizon is already derived
  from the max step on disk per run (`ecmwf_fetch.py:381`), not hardcoded — use
  that, not the nominal 90/168h order figures.
- **DB:** one new column — a `flexibility` enum on flights (batch_alter
  migration per house rules). `alt_departure_time` is reused as-is for the
  Alternate-time value — no rename, no DTO break.

## Decisions locked (2026-07-02)

| # | Decision |
|---|---|
| A | Flexibility is opt-in, default None; soft hint on None flights when a hint-class advisory fires |
| B | The toggle gates *whether* to run scenarios; a cheap "day pinned bad" early-exit inside the scan job may still gate *how much* — kept as a benchmarked optimization (OM variance vs coarse-ECMWF-first, see flow section), not a v1 blocker. `timing_class` only drives the hint + ranking emphasis |
| C | `freezing_precip` is a full `scan`-class member (9 scan rows total — severe, and the diurnal T-crossing IS the timing mechanism, so it counts toward the ranking margin, not just the hint; as-built, confirmed at review). Hint set = the 9 scan rows + `flight_category` (`timing_hint=True`); `fronts` excluded (experimental, default-off). Low-stakes and declarative (`timing_class` config) — since every candidate is graded on the full set, the lightbulb trigger can be re-tuned anytime without rework |
| D | Margin/presentation confirmed: surface a candidate only if it improves ≥1 grade and doesn't materially worsen anything; ranked, cap ~3, alternate time pinned (exact thresholds tuned at build time) |
| E | On-tap multi-model confirm is **in v1** |
| F | Previous/Next day modes are **in v1** |
| G | Extended enrichment stays **ephemeral** in the scan job; only `time_options.json` is persisted — no mutation of published pack artifacts |
| H | Scan keyed by **(flight, ECMWF run)**: a refresh on the same run reuses the existing scan; a new run re-scans automatically |

## Why this is hard — the data-cost reality

A timing search means evaluating advisories at valid-times other than the flight
window. The cost is **not uniform across models** (verified against `fetch.md`,
`icing-models-analysis.md`, `convective-analysis.md`, and code 2026-07-02):

| Layer | Full-day cost | Fidelity off the flight window |
|---|---|---|
| Open-Meteo base | **free for the target day** — whole 24h already on disk (`tasks/fetch.py:275-313`); **adjacent days need a new fetch** | **degraded** icing/convective |
| **ECMWF** | **decode only, no download** — full GRIB run on local ECPDS disk (`ECMWF_GRIB_DIR`, horizon from files on disk) | **full** (CLW/ICMR from a2 GRIB) — *but not decoded into the pack off-window; see below* |
| GFS | moderate — per-fhour S3 byte-range (a handful of ranged GETs per fhour, `grib_fetch.py:33`, `gfs_idx.py`) | full |
| ICON | **heavy download** — per fhour ≈ 40 model levels × N variables of bz2 files from DWD (`icon_eu_fetch.py:433-462`) | full |

The trap: **Open-Meteo alone would mislead** exactly on the axes that matter
most. `CLWMR`/`ICMR` (cloud-water/ice — 0.35 of the icing fuzzy-logic weight)
are GRIB-only and absent for MétéoFr/UKMO/GEM (always synthesized). Convective
DD is OM-computable (CAPE is an OM field) but the NWP-native firing signal +
safety `cross_check` need GRIB. So a cheap OM-only sweep produces
*confident-but-wrong* conclusions on icing and convection. Conversely, a
faithful full-multi-model sweep across 10–12 daylight hours × (1–3 days) is
~10–36 briefing-equivalents — far too expensive to run eagerly.

## The enrichment-window reality (decisive finding)

The reuse premise — "just re-grade at another hour with `run_alt_from_pack`,
it's free" — is only **half true**, and getting this wrong would silently
violate the honesty posture. Verified in code:

- `run_alt_from_pack` (`tasks/advise.py:704`) **does** take an arbitrary
  `alt_departure_time: datetime` and an `advisory_models` subset, and re-runs
  analysis + front detection at the shifted ETAs (it also re-writes
  `route_fronts_alt.json` and airport conditions). So the *re-grading machinery*
  generalises to any hour and any model set for free. ✓
- **But it re-grades against the *saved pack* `cross_sections`, not against the
  on-disk GRIB.** GRIB enrichment is windowed around the flight — **the ±3h
  margin is ECMWF-specific** (`margin = timedelta(hours=3)`,
  `fetch/grib/__init__.py:2071-2077`); GFS and ICON use forward flight-window
  hours instead (`grib_fetch.py:176`, `icon_eu_fetch.py:361`), not a symmetric
  bracket. OM base spans the whole target day, but CLW/ICMR/cloud-diagnostics —
  the icing fuel and the GRIB cloud geometry — exist only in each model's
  enriched window. **Coverage must therefore be checked per model, never
  assumed.**
- `at_time()` picks the **closest** stored hour and **silently clamps** past the
  edge (`models/analysis.py:329-342`, ~18 call sites, one method definition). So
  a naive off-window `run_alt_from_pack` returns OM-clamped values **labelled
  ECMWF** — a confident-but-wrong provisional, the exact failure the posture
  forbids.
- **Accumulated-field constraint:** ECMWF tp/sf/cp are step-differenced across
  consecutive processed steps (`grib/__init__.py:2067,2205`). The daylight
  extension must decode a **contiguous run of fhours** — cherry-picking only
  "promising" hours would corrupt precip/snow deltas.

So there are **two regimes**, and the design treats them separately:

| Regime | Cost | What it needs |
|---|---|---|
| **Inside the enriched window** (per model) | **free** — zero decode, pure re-analysis; **all models** are enriched here, so in-window candidates can be graded **multi-model immediately** | nothing new — `run_alt_from_pack` as-is |
| **Full daylight window** | **decode-only, no download** (ECMWF row above) | **decode the daylight ECMWF fhours** into an extended (ephemeral, per decision G) cross-section set *first*, then re-grade |

**The real v1 primitive is "extend the ECMWF enrichment window across daylight,"
not "reuse `run_alt_from_pack` for free."** You reuse the analysis + advisory
*machinery*; you build the full-day *data layer* fresh (bounded to ECMWF, decode
from local disk). Hard invariant: **never grade a candidate hour whose fields
aren't actually decoded for the model being claimed** — extend enrichment to
cover it, or refuse the hour. No silent `at_time()` clamp. Implementation:
record per-model enriched-hour **coverage as explicit metadata** and check it
*before* grading (refuse up front), with an opt-in `strict` kwarg on `at_time`
as a localized backstop — don't thread strictness through all ~18 call sites.

## Core idea — ECMWF-anchored, native-cadence, coarse-to-fine

Exploit the model-cost asymmetry: ECMWF is **both the best model and the
locally-delivered one**.

1. **Free tier (in-window).** Candidates inside the original enrichment window
   have *all* models enriched already — they surface as **confirmed
   immediately, for free**. ("Leave 2h earlier" is probably the most common
   useful suggestion, and it costs nothing.)
2. **Cheap search (background, ECMWF-only).** The scenario job decodes the
   daylight ECMWF fhours (decode-only, no download, ephemeral) and sweeps
   ECMWF-only advisories across them — full fidelity. Ranks candidate windows.
3. **Expensive confirm (on user tap, multi-model).** Only when the user taps a
   candidate do we spend the ICON/GFS download+decode. **Cost note:** a
   candidate is a `departure_shift`, so the confirm needs ICON/GFS at *all*
   native steps spanning the shifted flight window (typically 2–5 fhours), not
   one step — roughly **one briefing-equivalent** of ICON+GFS fetch. Both
   fetchers are already per-fhour (`icon_eu_fetch.py:433`, `grib_fetch.py:33`),
   so no fetch-layer changes. Expect tens of seconds to a couple of minutes;
   async with polling; result cached on the candidate (invalidated by new runs
   per decision H).

Bias is in the **safe direction**: ECMWF-only search can *miss* a good window,
but the confirm pass kills any candidate ICON/GFS disagree with — we never
surface a bad time, we only occasionally fail to find every good one.

**Snap to native cadence, don't interpolate to clock hours.** The scan grid *is*
the model's native valid-times: 1h where ECMWF publishes hourly, coarse (3h) in
the later window (actual cadence read from files on disk). The confirm pass
grades each candidate at ICON/GFS's nearest native steps.

### Flow — an honesty ladder

```
SCANNING ──► CANDIDATES ──────────────────────► CONFIRMED (multi-model)
  bg job      in-window: confirmed for free        on user tap (off-window)
              off-window: ECMWF-only, provisional
```

- **Provisional** — "ECMWF suggests a calmer window at 09:00; other models not
  yet checked." Never claims more than the one model checked.
- **Confirmed** — "all models checked: better" **or** "actually not better —
  ICON sees convection at 09:30." The **downgrade case is a feature** — it shows
  the cross-check working, on-brand for the attention-director stance.

## `timing_class` — hint trigger + ranking emphasis only

With the Flexibility toggle as the compute gate, `timing_class` no longer
decides *whether* to scan. Every candidate hour is graded on the **full**
advisory set regardless (otherwise we'd surface a window that fixed icing and
quietly introduced a crosswind), so e.g. fog burn-off shows up in results even
though `flight_category` is not scan-driven. The classification now decides:

1. **The hint** on Flexibility=None flights (which advisories being RED/AMBER
   triggers the lightbulb), and
2. **Ranking emphasis** (what "improves" is worth surfacing first).

Config stays declarative: add a `timing_class` flag to `AdvisoryCatalogEntry`
(`models/advisories.py:144`, sibling of `altitude_dependent` at `:153`), with a
registry helper mirroring `get_altitude_dependent_ids()`
(`analysis/advisories/registry.py:31`), so a new evaluator auto-participates.
Note two evaluators register their IDs via constants (`fronts`, `sun`) — don't
grep for literal `id="..."`.

### Mapping (all 21 evaluators — verified complete against the registry)

**Hint-class** (triggers the None-flight hint; decision C): the 9 `scan` rows +
`flight_category` (via `timing_hint=True` — the hint set is
`get_scan_class_ids() | {timing_hint}` in the registry).

#### `scan` — the timing-sensitive core (9 rows, as-built)
| Advisory | Cat | Why |
|---|---|---|
| `convective` | convective | Diurnal CAPE/firing, GRIB cross-check — canonical case |
| `convective_character` | convective | Same diurnal convection, avoidability axis |
| `icing_escape` | icing | In-cloud icing rides GRIB cloud-water + freezing level, both shift through the day |
| `fiki_icing` | icing | Thickness/severity is GRIB-CLW-driven and evolves; OM under-reads |
| `cloud_top` | cloud | Tops build/burn-off diurnally; GRIB cloud geometry |
| `vmc_cruise` | cloud | BKN/OVC at cruise burns off/builds; GRIB cloud |
| `vfr_feasibility` | feasibility | RED is usually enroute IMC / corridor decks — the burn-off case |
| `ifr_feasibility` | feasibility | Gated by icing + convective, both scan-class |
| `freezing_precip` | icing | Severe, and the diurnal T-crossing-zero IS the timing mechanism — ranking-worthy, not hint-only |

#### resolved `scan?` rows (decision C)
| Advisory | Disposition |
|---|---|
| `flight_category` | **hint-only** (`timing_class="cheap"`, `timing_hint=True`) — fog/ceiling burn-off is the classic timing case but OM/TAF-driven; improvements surface via full-set grading |
| `freezing_precip` | **scan-class** (see table above) |
| `fronts` | **excluded** until the advisory matures (experimental, default-off) |

#### `cheap` — OM-sufficient, never drives the hint
`density_altitude`, `sun`, `airport_wind`, `llws` — their timing sensitivity is
already visible in free OM data; improvements still appear in candidate diffs.

#### `none` — timing not the lever
`headwind`, `turbulence` (altitude is the lever; its thermal part is captured by
the convective scan), `mountain_wind`, `enroute_precip`, `model_agreement`,
`dd_nwp_agreement`.

## Alternate time — the old alt-departure feature, unified

The old `alt_departure_time` feature (single manually-set alternate time, graded
synchronously in-pipeline with a planned↔alt toggle) is **subsumed as
Flexibility=Alternate time — a single-candidate scenario job**:

- **No column change.** `alt_departure_time` (DB `db/models.py:111`, DTOs)
  keeps its name and stores the Alternate-time value.
- **Grading moves out of the synchronous pipeline** into the scenario job
  (`pipeline.py:437-444` alt stage retires). This resolves an ordering
  contradiction: an off-window alternate time graded in-pipeline would hit
  exactly the silent-clamp failure the invariant forbids, before any enrichment
  extension exists. **Behavior change:** alt advisories now arrive a beat
  *after* the briefing ("Scenarios running…") instead of with it — accepted.
- **Artifact compatibility:** the job still writes `route_advisories_alt.json`
  for the alternate time (web already consumes it — `briefing-store.ts:379`,
  `briefing-main.ts:485`), now with an explicit **model-coverage/confidence
  field**, since off-window grades are ECMWF-only until confirmed. The web
  planned↔alt UI must render that label. (iOS has **zero** alt-departure code
  today — nothing to migrate; the reframe is web-only.)
- **Pinned in scan modes:** in Same-day/±day modes, the alternate time (if set)
  is the pinned row in the candidate list and the ranking tiebreaker.

## Implementation sketch (reuse-heavy)

**Artifact** — `time_options.json` on the pack, keyed by ECMWF run (decision H):
```
TimeWindowScan {
  ecmwf_run: str                                       # staleness key (decision H)
  baseline:   { time, ecmwf_assessment }               # planned time, ECMWF view
  window:     { start, end, cadence, daylight_clipped, flexibility_mode }
  candidates: [ TimeCandidate {
     departure_shift, valid_times[],                   # a shift propagates per-point ETAs
     ecmwf_assessment,                                 # GREEN/AMBER/RED
     improves: [advisory_ids], worsens: [...],         # FULL-picture diff vs baseline
     confidence: "confirmed_in_window" | "ecmwf_only" | "confirmed",
     confirmed: TimeConfirmation | null                # filled on tap (or free in-window)
  } ]
}
```
(`departure_shift` not a single `valid_time`: shifting departure moves *every*
route point's ETA — `analyze_all_route_points` re-derives per-point valid-times,
`tasks/analyze.py:345-354`. The diff is vs the **ECMWF-only baseline**, stored —
never apples-to-oranges vs the multi-model headline.)

**Background job** — `run_time_scan`. **Reality check: there is no task queue**
(no Celery/arq; pipeline stages are sequential calls, background work is
scheduler asyncio loops). This is the largest piece of new infrastructure: a
post-refresh background task launched after the pipeline completes, on the
standalone-subprocess substrate (#236 — note its supervisor is currently
scheduler-cycle-specific, needs a per-briefing variant) with decodes at
`DecodePriority.BACKGROUND` (#172) so it never starves a live briefing. Global
scan concurrency = 1; skip when a scan for the same (flight, ECMWF run) exists.
First step is **decode the daylight ECMWF fhours** (contiguous, ephemeral —
decisions above), then re-grade ECMWF-only at each native-cadence step. Daylight
bounds from `RouteSunAnalysis` / night intervals. (The altitude table is *not* a
competing background job — it's computed inside `run_advisories` while
cross-sections are in memory, `tasks/advise.py:375-390`.)

**Endpoints**
- `GET .../packs/{ts}/time-options` → scan result or status ("Scenarios
  running…" while pending; client polls).
- `POST .../packs/{ts}/time-options/{step}/confirm` → on-tap full check
  (ICON+GFS across the shifted window; async; polled; cached on the candidate).

**Reuse, don't rebuild:** re-grading machinery = `run_alt_from_pack` (arbitrary
time + `advisory_models` subset) — reused unchanged. What's *new* is the data
layer (daylight ECMWF decode, bounded copy of the `fetch/grib` window logic),
the coverage metadata + refusal check, and the background-job wiring. Scoring =
`derive_assessment_from_advisories`; comparison UI = the existing planned↔alt
toggle, generalised to candidate selection.

### UX hook (sketch)
```
┌────────────────────────────────────────────────┐
│ 🕐  Scenarios: 2 better windows found       [v] │
├────────────────────────────────────────────────┤
│  08:00 ★ your alternate time · AMBER            │
│  09:00   ● improves: VFR, winds aloft           │
│          ECMWF only · tap to check all models → │
│  10:00   ● improves: VFR · worsens: density alt │
│          ECMWF only · tap to check all models → │
│                                  show full day ⌄ │
└────────────────────────────────────────────────┘
```
While pending: "🕐 Scenarios running…". Rules against noise (decision D):
**suppress entirely** unless a candidate clears the margin; show only improving
candidates (+ the pinned alternate time), **ranked, capped ~3**; **never
auto-switch** the plan.

## Digest & MCP surfacing

**Digest — hint only, never the async numbers.** The scan finishes after the
digest has been generated and shown. The digest gets at most the cheap
synchronous hint ("this hazard swings through the day — see Timing options in
the app") — note the OM diurnal-swing computation behind it is **new code**
(nothing exists today; only observation-climatology diurnal profiles in
`tasks/airport_summary.py`). The full scan block lives in the web UI — a
deterministic table, no LLM needed. `MitigationKind.TIMING` LLM phrasing is a
nice-to-have, not a dependency.

**MCP / ChatGPT — provisional-only, refer to the app.** The connector path is
stateless/synchronous, so no interactive confirm. The MCP surface points the
user to the app rather than exposing half a workflow.

## Honesty guardrails

- **Never grade an hour whose fields aren't decoded for the model claimed.**
  Coverage checked up front from explicit metadata; refuse otherwise. No silent
  `at_time()` clamp (`strict` backstop).
- Off-window candidates are explicitly ECMWF-only-labelled; the claim upgrades
  only on confirm (in-window candidates are honestly labelled confirmed — all
  models really are enriched there).
- The confirm-downgrade ("you tapped a suggestion, it turned out worse") is a
  first-class designed outcome, not an error path.
- The flexibility window visibly stops where ECMWF fidelity stops (horizon from
  max step on disk per run) — no silent degradation to OM.
- Staleness: keyed by ECMWF run (decision H); a new run invalidates the scan and
  all confirmations.

## Validation plan

1. **Invariant test:** scan at `departure_shift = 0` must reproduce the baseline
   ECMWF-only grades exactly.
2. **Coverage-refusal test:** a candidate hour with missing enrichment is
   refused, never clamp-graded.
3. **Replay the eval-corpus convective cases** — Jun 21 LFMD→EGTF and Jun 27
   EGTF→LFAT→LFQA are diurnal-timing cases with known ground truth: "would the
   scan have found the morning window?" is the single best pre-build design
   validation, runnable on the eval-workbench substrate.
4. **Decode benchmark spike (gates the Same-day slice):** wall-time to decode
   ~10–14 extra ECMWF fhours for a typical route at BACKGROUND priority on the
   droplet. Decode is the known GIL/process-pool bottleneck; this number decides
   whether scan-per-refresh is comfortable or the (flight, run) cache is doing
   heavy lifting.

## Implementation slices (all in v1, in order)

1. **Toggle + unified Alternate time + in-window free tier.** Flexibility
   column/UI; alt grading moves to the scenario job; candidates within the
   existing enrichment window surface confirmed-for-free. No new data layer —
   validates the job wiring, polling, UX, and ranking margin cheaply.
2. **ECMWF daylight extension + Same-day scan.** The decode benchmark (above)
   gates this slice. Ephemeral enrichment, coverage metadata, honesty ladder.
3. **On-tap confirm** (ICON+GFS across the shifted window, async, cached).
4. **Previous/Next day** (extra OM fetch, past-day clamp, horizon edge UX).

## Out of scope (v1)

- Cheap analytic timing hints for the `cheap` tier (DA/sun/wind/LLWS) beyond the
  shared hint.
- Multi-day search beyond the ECMWF GRIB horizon.
- Lateral route deviation (2-D) — the route is 1-D; only along-track timing.
- iOS surfacing (alt-departure/scenarios are web-only client-side today).

## Remaining build-time details (no user decision needed)

- Exact margin thresholds for "improves / materially worsens" (shape decided —
  decision D).
- Hint copy + digest hint wording.
- Whether the None-flight hint ships in slice 1 or with slice 2.
- **Early-exit benchmark (decision B):** once slice 2 gives real scan-cost
  numbers, compare OM-variance vs coarse-ECMWF-first on real packs —
  `cost(check)` vs `cost(full scan) × skip_rate` — and keep whichever (if
  either) pays for itself.

## References

- Mitigation framework + `MitigationKind.TIMING` (reserved): [advisories.md](../advisories.md) *Mitigations* section, #328/#330
- Reuse machinery: `run_alt_from_pack` (`tasks/advise.py:704`), `AdvisoryCatalogEntry` (`models/advisories.py:144`, `altitude_dependent` `:153`), registry helper pattern (`analysis/advisories/registry.py:31`)
- Enrichment windows: ECMWF ±3h (`fetch/grib/__init__.py:2071-2077`); GFS forward window (`grib_fetch.py:176`); ICON forward window (`icon_eu_fetch.py:361`); silent clamp (`models/analysis.py:329-342`); accumulated-field step-differencing (`grib/__init__.py:2067,2205`)
- OM fetch windowing: `tasks/fetch.py:275-313` (`fetch_multi_point`, target-day only)
- Per-fhour fetch primitives: ICON (`icon_eu_fetch.py:433-462`), GFS byte-range (`grib_fetch.py:33`, `gfs_idx.py`)
- Data-fidelity basis: [fetch.md](../fetch.md), [icing-models-analysis.md](../icing-models-analysis.md), [convective-analysis.md](../convective-analysis.md)
- Substrate: decode priority dispatcher (#172, `DecodePriority`), standalone subprocess isolation (#236), altitude table inside `run_advisories` (#259, `tasks/advise.py:375-390`), alt artifacts (`tasks/artifacts.py:345-357`), web alt UI (`web/ts/store/briefing-store.ts:379`, `briefing-main.ts:485`)
