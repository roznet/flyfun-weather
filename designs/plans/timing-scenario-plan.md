# Timing scenario scan — better departure-window discovery

> **Status (2026-06-30): brainstorm / exploration.** Not yet an issue, not
> scheduled. This doc captures the thinking so far so we can keep iterating
> before committing to a build. Implements the `MitigationKind.TIMING` axis that
> was reserved (unimplemented) in the mitigation framework (#330). Several points
> still open — see *Open questions*. Do NOT add to `designs/INDEX.md` (plans are
> not MCP-discoverable by house rule).

## Goal

When a briefing flags a hazard that genuinely varies through the day, tell the
pilot **"a better departure time may exist"** and let them explore it — without
re-running a full multi-model briefing for every candidate hour. GA pilots
commonly have ±a-day flexibility; surfacing a calmer window is high-value.

Posture (non-negotiable, inherits from the mitigation framework): an
**attention-director, never a verdict**. Neutral soft hook (lightbulb, like the
mitigations — never green/red), never auto-switches the plan, never overclaims.

## Why this is hard — the data-cost reality

A timing search means evaluating advisories at valid-times other than the flight
window. The cost is **not uniform across models** (verified against `fetch.md`,
`icing-models-analysis.md`, `convective-analysis.md`):

| Layer | Full-day cost | Fidelity off the flight window |
|---|---|---|
| Open-Meteo base | **free** — whole 24h target day already on disk (`fetch.md:103`) | **degraded** icing/convective |
| **ECMWF** | **decode only, no download** — full GRIB run preloaded (0–90h, 168h at 00/12z) | **full** (CLW/ICMR from a2 GRIB) |
| GFS | moderate (S3 byte-range) | full |
| ICON | **heavy download** (DWD) | full |

The trap: **Open-Meteo alone would mislead** exactly on the axes that matter most.
`CLWMR`/`ICMR` (cloud-water/ice — 0.35 of the icing fuzzy-logic weight) are
GRIB-only and absent for MétéoFr/UKMO/GEM (always synthesized). Convective DD is
OM-computable (CAPE is an OM field) but the NWP-native firing signal + safety
`cross_check` need GRIB. So a cheap OM-only sweep produces *confident-but-wrong*
conclusions on icing and convection. Conversely, a faithful full-multi-model
sweep across 10–12 daylight hours × (1–3 days) is ~10–36 briefing-equivalents —
far too expensive to run eagerly.

## Core idea — ECMWF-anchored coarse-to-fine

Exploit the model-cost asymmetry: ECMWF is **both the best model and the
preloaded one**.

1. **Cheap search (background, ECMWF-only).** After the main briefing, a
   low-priority job sweeps ECMWF across the daylight window — full fidelity,
   decode-only, no download. Ranks candidate windows.
2. **Expensive confirm (on user tap, multi-model).** Only when the user taps a
   candidate do we spend the ICON/GFS download+decode for that one hour. The
   heavy cost is gated on demonstrated user intent.

Bias is in the **safe direction**: ECMWF-only search can *miss* a good window,
but the confirm pass kills any candidate ICON/GFS disagree with — we never
surface a bad time, we only occasionally fail to find every good one.

### Flow — an honesty ladder

```
SCANNING ──► CANDIDATES (ECMWF-only, provisional) ──► CONFIRMED (multi-model)
  bg job          shown on reopen, soft hook            on user tap
```

- **Provisional** — "ECMWF suggests a calmer window at 09:00; other models not
  yet checked." Never claims more than the one model checked.
- **Confirmed** — "all models checked: better" **or** "actually not better — ICON
  sees convection at 09:30." The **downgrade case is a feature** — it shows the
  cross-check working, on-brand for the attention-director stance.

## Gating — reserve the scan for when it makes sense

The scan is expensive, so don't always run it. Enqueue only when **both** hold:

1. **Relevance** — at least one `scan`-class advisory (table below) is RED/AMBER
   at the planned time. All-green, or only `cheap`/`none`-class advisories
   flagged (sun, density altitude, precip) → no scan.
2. **Variance pre-filter** — use the **free local OM 24h data as an honest
   *variance detector***: does the hazard field (CAPE, cloud, RH-icing proxy)
   actually move across the day? OM is too degraded to *grade* icing, but it is
   fair for "flat all day vs swinging." If convection is pinned RED dawn-to-dusk
   (synoptic, not diurnal), skip the scan — timing won't help. (Honest use of
   degraded data: variance, not absolute severity.)

**Config is declarative, not a hardcoded list.** Add a `timing_class` flag to
`AdvisoryCatalogEntry` (sibling of `altitude_dependent`); the scan job asks the
registry which IDs are `scan`-class, so a new evaluator auto-participates.

**Scope vs objective stay separate** (keeps honesty): the trigger set decides
*whether to scan* and *what to rank by*, but each candidate hour is graded on the
**full** advisory set — otherwise we'd surface a window that fixed icing and
quietly introduced a crosswind.

### The unifying principle

> **scan-worthy ⟺ GRIB-dependent ⟺ OM-insufficient.** The hazards that need the
> good model are the same hazards worth scanning. The cheap-OM-sufficient
> advisories (DA / wind / sun) don't need a scan — if timing helped, the free OM
> data would already show it. So they get at most a cheap analytic hint, never
> the ECMWF scan.

## `timing_class` mapping (all 21 evaluators)

Trigger threshold = RED or AMBER on a `scan`-class advisory.

### `scan` — worth the expensive ECMWF day-scan
| Advisory | Cat | Why |
|---|---|---|
| `convective` | convective | Diurnal CAPE/firing, GRIB cross-check — canonical case |
| `convective_character` | convective | Same diurnal convection, avoidability axis |
| `icing_escape` | icing | In-cloud icing rides GRIB cloud-water + freezing level, both shift through the day; OM-synthesized misleads |
| `fiki_icing` | icing | Thickness/severity is GRIB-CLW-driven and evolves; OM under-reads |
| `cloud_top` | cloud | Tops build/burn-off diurnally; GRIB cloud geometry |
| `vmc_cruise` | cloud | BKN/OVC at cruise burns off/builds; GRIB cloud |
| `vfr_feasibility` | feasibility | RED is usually enroute IMC / corridor decks (GRIB cloud) — the burn-off case |
| `ifr_feasibility` | feasibility | Gated by icing + convective, both scan-class |

### `scan?` — ambiguous, decide before committing
| Advisory | Cat | Tension |
|---|---|---|
| `flight_category` | airport | Airport IMC/fog burn-off is a strong timing case, but ceiling/vis fidelity is OM/TAF not GRIB — earns the scan, or just a cheap ceiling hint? |
| `freezing_precip` | icing | Severe (RED) and temp-crossing-zero is diurnal, but the T-profile is OM-complete — scan (safety) vs cheap (data sufficiency)? |
| `fronts` | fronts | Front passage is diurnally-timed and GRIB-θe-derived → scan-worthy, but experimental/default-off — leave `none` until the advisory matures? |

### `cheap` — OM-sufficient, free hint at most, never the scan
| Advisory | Cat | Why not scan |
|---|---|---|
| `density_altitude` | airport | Follows the temperature diurnal curve; obvious from local OM temp |
| `sun` | sun | Pure astronomy — glare time computable exactly, no forecast scan at all |
| `airport_wind` | airport | Surface wind is diurnal but OM-complete |
| `llws` | airport | Nocturnal-jet shear is textbook-timing but low-level winds are OM-available |

### `none` — timing not the lever
| Advisory | Cat | Why |
|---|---|---|
| `headwind` | wind | Altitude-mitigated, weakly diurnal, informational |
| `turbulence` | turbulence | Lever is altitude; its diurnal (thermal) part is already captured by the convective scan — separate scan double-counts |
| `mountain_wind` | turbulence | Wind speed is OM; wave-erosion timing too subtle to scan reliably |
| `enroute_precip` | precip | Surface precip is OM-complete and system-movement (spatial) driven, not a diurnal burn |
| `model_agreement` | model | Intrinsic — no time changes model spread |
| `dd_nwp_agreement` | model | Intrinsic dev/calibration signal |

v1 trigger set = the 8 `scan` rows; the 3 `scan?` rows are the first thing to settle.

## Implementation sketch (reuse-heavy)

**Artifact** — `time_options.json` on the pack, precompute-style like the
altitude table (#259):
```
TimeWindowScan {
  baseline:   { time, ecmwf_assessment }              # planned time, ECMWF view
  window:     { start, end, step, daylight_clipped, day_flex }
  candidates: [ TimeCandidate {
     valid_time, ecmwf_assessment,                     # GREEN/AMBER/RED
     improves: [advisory_ids], worsens: [...],         # FULL-picture diff vs baseline
     confidence: "ecmwf_only",
     confirmed: TimeConfirmation | null                # filled on tap
  } ]
}
```

**Background job** — `run_time_scan`, enqueued after `run_advisories` in the
refresh pipeline (subject to the two gates). Rides existing substrate: standalone
subprocess isolation (#236), behind the decode priority dispatcher (#172) so it
never starves a live briefing. Coarse-to-fine on time (3h scan → 1h refine).
Daylight bounds free from `RouteSunAnalysis` / night intervals. Emits SSE
`time_scan_ready`.

**Endpoints**
- `GET .../packs/{ts}/time-options` → the scan (404 while scanning → "looking…").
- `POST .../packs/{ts}/time-options/{hh:mm}/confirm` → on-tap full check. This is
  the existing `alt/compute` generalized to an arbitrary time. **Not instant** —
  ICON/GFS GRIB at that hour is off-window, so confirm triggers the deferred
  fetch+decode (the cost we gate on user intent). Make it **async (SSE)**; cache
  the result on the candidate.

**Reuse, don't rebuild:** scan = `evaluate_all(advisory_models=["ecmwf"])` on
local GRIB; confirm = the `alt_departure_time` path (`run_alt_from_pack` /
`route_advisories_alt.json`) generalized to any hour, all models; scoring =
`derive_assessment_from_advisories`; delivery = SSE refresh; comparison UI = the
existing planned↔alt toggle.

### UX hook (sketch)
```
┌────────────────────────────────────────────────┐
│ 🕐  A smoother departure may exist               │
│     ECMWF found 2 better windows today      [v] │
├────────────────────────────────────────────────┤
│  09:00   ● improves: VFR, winds aloft           │
│          ECMWF only · tap to check all models → │
│  10:00   ● improves: VFR · worsens: density alt │
│          ECMWF only · tap to check all models → │
│                                  show full day ⌄ │
└────────────────────────────────────────────────┘
```
Three rules against noise: **suppress entirely** unless a candidate clears a
margin; show only improving candidates, **ranked, capped ~3** ("2 windows found",
not "12 hours scanned"); **never auto-switch** the plan.

## Honesty guardrails

- Never surface a time evaluated only on degraded data — provisional candidates
  are explicitly ECMWF-only-labelled; the claim upgrades only on confirm.
- The confirm-downgrade ("you tapped a suggestion, it turned out worse") is a
  first-class designed outcome, not an error path.
- Day-flexibility has a hard fidelity edge: beyond the ECMWF 0–90h (168h at
  00/12z) horizon even the scan drops to degraded OM — the flexibility window
  should visibly stop where ECMWF fidelity stops, not silently degrade.
- Stale on refresh: a new model run invalidates the scan + confirmed candidates.

## Open questions (to nail down before this becomes an issue)

**Blocking prerequisite (verify first):** confirm `run_alt_from_pack` can re-grade
at an **arbitrary same-day hour on local ECMWF GRIB with full icing/convective
fidelity, no re-fetch**. The entire cheap-search premise rests on this; if it
doesn't hold, the design changes shape.

1. **The 3 `scan?` rows** — `flight_category`, `freezing_precip`, `fronts`:
   scan / cheap / none.
2. **Search window** — daylight clip (reuse `RouteSunAnalysis`); cadence
   (coarse-to-fine 3h→1h?); day-flexibility knob UX ("day only / ±prev / ±next")
   and its ECMWF-horizon edge.
3. **"Better" margin** — how much improvement surfaces a candidate; whole-picture
   vs trigger-weighted ranking objective.
4. **On-tap confirm** — async/SSE; result caching; promote a confirmed candidate
   to a saved alt scenario?
5. **Variance pre-filter** — build in v1 or defer as an optimization; what
   "varies enough" threshold.
6. **Execution + budget** — subprocess vs in-process task; caps on
   candidate-hours/scan and scans/day so a fleet of flights can't stampede the
   decode pool.
7. **Cheap tier** — do we ever build the OM analytic hints for DA/sun/wind/LLWS,
   or is the `cheap` class just "not the scan, no treatment"?

## Out of scope (v1)

- Cheap analytic timing hints for the `cheap` tier (DA/sun/wind/LLWS).
- Multi-day search beyond the ECMWF GRIB horizon.
- Lateral route deviation (2-D) — the route is 1-D; only along-track timing.

## References

- Mitigation framework + `MitigationKind.TIMING` (reserved): [advisories.md](../advisories.md) *Mitigations* section, #328/#330
- Data-fidelity basis: [fetch.md](../fetch.md), [icing-models-analysis.md](../icing-models-analysis.md), [convective-analysis.md](../convective-analysis.md)
- Reuse substrate: alt-departure path (advisories.md *Alt departure*), decode priority dispatcher (#172), standalone subprocess isolation (#236), altitude-table precompute (#259)
