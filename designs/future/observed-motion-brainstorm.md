# Observed motion — brainstorm, options and direction

> Brainstorm of 2026-09-05/06 on using the phase-1 radar, lightning and
> satellite frames (#574) beyond display: identify rain and cloud-top objects,
> estimate their motion, relate them to the route and the destination, and
> validate the result. **Nothing here is decided or implemented.** The one
> candidate implementation (draft PR #600, written by an autonomous agent from
> a single prompt) was reviewed and is *not* the base; the parts of it worth
> keeping are listed in §7 as options.
>
> **2026-10-03: §10 supersedes the ordering in §8 and the compute placement in
> §1/§9.** Cell analysis moves to an always-on loop on the Mac mini; the droplet
> only relates the pushed cell catalogue to each flight.  **Slice 1 (#650) is
> built** — the loop, the analysis and self-scoring, as-built in
> `designs/observed-cells.md`.  Correction to §3/§9: the OPERA DBZH composite is
> a **1 km** grid (3800 × 4400), not 2 km; RATE is 2 km.

Companion docs: `current-conditions.md` (phase 1 as built),
`current-conditions-review.md` (decisions D1–D12, especially D2 and the
§4.6 time-alignment fork), `satellite-cloud-top-validation.md`.

---

## 1. Premise and constraints

**The "permanently out" line needs reopening, precisely.** Phase 1 recorded
"nowcasting and a time slider" as permanently out of scope. Read closely, that
decision was about an *animated radar loop* — tiled imagery over time, which is
a different and expensive product. An *object nowcast* is not that: it is a
small amount of structured data (cell outlines, velocities, projected
footprints) drawn on the single frame the map already shows. The decision to
record is: animated loops stay out; object tracking is in scope.

The raw material already exists. The frame store on the droplet retains:

| Source | Cadence | Retention | Frames |
|---|---|---|---|
| `opera_dbzh` | 5 min | 3 h | 36 |
| `opera_rate` | 15 min | 3 h | 12 |
| `eumetsat_li` | 10 min | 3 h | 18 |
| `eumetsat_ctth` | 10 min | 1 h | 6 |

Three constraints carry over unchanged from phase 1 and D2:

- **Annotate-only.** An observation never moves a hazard grade. Motion output
  directs attention; it never says "go" or "no-go".
- **No heavy work in the request path.** Phase 1 allowed observed sampling in
  the ↻ refresh because it costs 42–130 ms. Tracking costs seconds and is
  GIL-bound; it belongs in the collector loop, written next to the frames.
- **Three-state coverage and parallax-before-geometry** apply to every derived
  object exactly as they apply to samples.

---

## 2. The question, stated correctly

"Is the cell heading toward the route?" is the wrong test. A cell that crosses
the route two hours before the aircraft gets there is irrelevant. The honest
formulation is four-dimensional:

> At the time I reach point P, where will this object be?

Every cross-section point already carries an ETA, so the tracker output per
object is a closest approach in *distance* and in *time relative to each
point's ETA*. "Away from the route" falls out of the same computation. The
destination is a special case of P with the TAF beside it.

**Skill horizon bounds where this applies.** Extrapolation nowcasts hold
useful skill for roughly an hour for convective cells and two to three hours
for stratiform frontal bands; beyond that, growth and decay dominate position.
So this is a D-0 product within a couple of hours of departure, and an
in-flight product (Start Flight tracking already exists on iOS). It is not a
pre-departure product for a flight six hours away.

---

## 3. Object identification

Well-trodden ground (TITAN, SCIT, MeteoSwiss TRT). Threshold DBZH, run
connected components on the 2 km grid, drop blobs below a minimum area.

**Use two tiers, not one contour.** A single low contour serves neither
purpose: at ~5 dBZ it sits below meteorological rain, picks up clear-air,
clutter and bright-band residue in the max composite, and in frontal rain
merges everything into one blob so cells are never resolved.

| Tier | Threshold (to calibrate) | Purpose |
|---|---|---|
| Rain area | ~20 dBZ | Extent, frontal bands, onset/clearance timing |
| Convective core | ~35–40 dBZ | Cells, lifecycle, lightning association |

Enrich each object from the other sources, all readable through the existing
sampler primitives:

- **RATE** peak inside the footprint — the only intensity we may claim
  (D8: never our own Z–R conversion).
- **Lightning** flash count and flash-rate trend inside the footprint. First
  flash in a cell is the moment it becomes a thunderstorm. Flashes are
  *reported*, never advected.
- **CTTH** highest top and top temperature inside the parallax-corrected
  footprint.

That yields the classification asked for: rain only, rain with high tops, rain
with lightning. Static identification alone is already a visible step up:
"three cells with lightning within 20 NM of the route, tops FL380, 40 minutes
old".

**CTTH as its own object family** is weaker than it looks. A fixed geometric
height contour (PR #600 used 15,000 ft) is arbitrary — not cruise-relative,
not convective (anvils sit at FL300+) — and mid-level decks have no texture to
track. CTTH earns its place as an *attribute* of radar objects, and as the
*only* object source where radar is blind (§5, idea 6). Its parallax
registration also still needs real-granule validation
(`satellite-cloud-top-validation.md`).

---

## 4. Motion estimation

Two families. The recommendation is the second, with the first as a fallback.

**A. Object matching across frames** (centroid/overlap association, PR #600's
patch-correlation variant). Fragile on splits and merges, which convection does
constantly; each object needs its own identity to survive a frame pair.

**B. Field-based optical flow on the DBZH sequence** (Lucas-Kanade or DARTS as
pySTEPS implements them for OPERA composites). Gives a smooth motion vector
field; each object's velocity is the mean flow over its footprint. Robust to
identity loss. Dependency note: pySTEPS' Lucas-Kanade path needs OpenCV; DARTS
is pure numpy/FFT; a cropped cross-correlation is small enough to write
in-house.

Either way the velocity is combined with the object lineage from §3 so that
lifecycle (below) is available.

**Lifecycle matters more than exact position.** Trend of peak dBZ, area and
flash rate over the last 30 minutes classifies an object as developing,
mature or decaying. A decaying cell an hour away is a different story from a
developing one, and a flash-rate jump is a known precursor of severe cores.

**Projection.** Constant-shape translation `F(t) = F0 + v·(t − t0)` is fine as
version 1, *with the horizon set by measured skill* (§6), not fixed a priori.
PR #600's 15-minute cap from the frame's nominal time, minus 5–10 minutes of
delivery lag, leaves 5–10 minutes of lead — 10–20 NM at 120 kt — which reaches
only an aircraft already next to a cell.

---

## 5. Further ideas (own)

1. **Phase-2 verdicts first.** `echo_match`, `intensity_match` and the
   per-model tops comparison (D8, D9), plus the Parquet verification stream
   already specified. Everything below needs that calibration base, and it
   resolves the ETA-vs-observation-time fork (§4.6) once.
2. **Steering-wind cross-check.** Compare each object's motion with the model
   mean wind in the 850–500 hPa layer (already in the cross-section, per
   model). A free sanity check on the tracker; disagreement flags propagation
   such as back-building, which pure extrapolation misses.
3. **Model phase-error estimate.** The LFMD→EGTF flight of 2026-08-27 showed
   ECMWF displaced the system in space and time rather than getting intensity
   wrong. Comparing the observed object field with each model's current hourly
   precipitation field yields a displacement per model ("ECMWF has this 60 km
   SW and two hours late"). Surfaces the disagreement instead of suppressing
   it; needs no tracking.
4. **Frontal band passage timing.** Elongated components with orientation and
   normal speed give the frontal passage time at each route point. This is
   where extrapolation has real skill, and it cross-checks the Hewson front
   timing directly.
5. **Convective initiation from CTTH cooling.** Cloud-top temperature dropping
   several kelvin per 10–20 minutes signals rapid growth before radar shows an
   echo. Paired with the model CAPE field, it catches new cells a tracker can
   never extrapolate.
6. **Satellite-only objects in the radar hole.** Half the OPERA grid is
   nodata (Bay of Biscay, Alpine shadow). Lightning plus CTTH sees cells there.
   Tracking satellite objects where radar is blind, with coverage state
   carried honestly, fills a gap the layers currently show as hatched.
7. **Destination ETA window versus TAF.** Extrapolated precipitation onset and
   clearance at the destination beside the TAF's TEMPO group: "TAF says TSRA
   14–16Z; radar suggests the cell passes 13:40–14:10".
8. **In-flight own-ship.** Start Flight tracking supplies a live position;
   time-to-closest-approach relative to it is the natural in-flight form.
   Later: it needs the horizon question settled first.

---

## 6. Validation — the part to build first

Ordered by how much each proves.

1. **The tracker validates itself for free.** Advect frame T by 30 and 60
   minutes, score against the real frame at that time with contingency
   statistics (POD, FAR, CSI) at the 20 and 35 dBZ thresholds, and beat the
   persistence baseline. All frames are in the store. This is the standard
   nowcast verification and it *sets* the horizon and the acceptance gates;
   nothing user-visible ships before it exists and has run over retained
   frames for a season of weather.
2. **Route-intersection claims are scored the same way.** Log each "object
   reaches point P at T ± x" claim, check the frame at T. Hit / miss / false
   alarm into the archive stream the phase-2 design already planned.
3. **METAR and SPECI are the right truth for airports only.** Predicted onset
   and end at an airport against present-weather groups (RA, SHRA, TS), with
   SPECI giving the timing resolution METAR lacks. Lightning within 10 NM
   against TS in the METAR is a classic, clean check. METAR says nothing en
   route; radar-derived truth carries the route legs.
4. **Steering wind and pilot debriefs close the loop.** Model-wind agreement
   checks the flow field; the debrief taxonomy and PIREPs give the occasional
   human confirmation.

---

## 7. What PR #600 offers as options

Draft PR #600 was reviewed in full (comment on the PR, 2026-09-06). Its first
three commits are a *corrections-only* state (`95e233b6`: FCI quality-code
reinterpretation, immutable acquisition windows, ft-MSL labelling, map-race
fix, offline realtime-patch persistence on iOS) — those are justified and are
being taken separately. The motion increment is **not the base**: the analysis
runs on the request path, the wire contract is implemented three times (server,
web, iOS), the fencing storage breaks deletion and refresh, and the
meteorological choices were made without the verification loop of §6. What is
worth taking, as options rather than code:

| Option | Where in #600 | Why it is interesting | Caveat |
|---|---|---|---|
| **Masked normalised cross-correlation with a fixed support mask** | `observed/motion/tracking.py` `_fixed_support`, `_patch` | Correct handling of nodata under every tested shift — unknown pixels never influence the displacement, including through a moving coverage edge. Reciprocal (forward/reverse) check is a sound diagnostic. Numerically tested to 0.1 m/s. | Two 31×31 patches per object is the fragile part; keep the masking idea, apply it to a field-based flow or to more patches. |
| **Continuous planned-timing overlap solver** | `observed/motion/route.py` | Intersects the relative aircraft segment `p(t) − v·(t − t0)` with the static contour, preserving holes, multiple intersections and tangent instants; converts segment fractions back to UTC. This is the four-dimensional question of §2 done properly, not sampled at ticks. | Depends on planned timing = departure + duration × distance fraction; live own-ship is a later input. |
| **Closure rate with an "approximately unchanged" band** | `route.py` | Signed distance trend over a centred 60 s interval, ±1 kt as a display-resolution dead band; intersecting → closure not applicable rather than zero. | None. |
| **Explicit reason codes instead of silent absence** | `models/observed_motion.py` envelope | `disabled` / `unavailable` / `available` are all full envelopes with a reason list; "no motion" is never conflated with "no object". Consistent with three-state coverage. | The 1,277-line contract around it re-validates producer policy; keep the shape, not the validators. |
| **Lineage ambiguity → withhold** | `tracking.py` `_plausible` | More than one candidate parent or child above an overlap fraction means split/merge; no velocity is published for that object rather than a wrong one. | Threshold (20 %) was never measured; set it from §6. |
| **Policy version on the wire** | `policy.py`, `policy_version` field | Thresholds, caps and horizon travel with each payload, so a stored result is interpretable after a policy change. | Keep as a single small dataclass; do not import it into the wire model. |
| **Lightning is evidence, never advected** | `association.py` | Flashes are counted inside footprints at their own times; nothing projects them. | The evaluated-zero state could never be emitted in #600; make sure it can. |
| **Route-centred azimuthal-equidistant 2 km analysis grid** | `geometry.py` `build_analysis_grid` | A metric grid shared by radar and satellite objects, independent of source projection. | Reuse `walk_route` for the densification, which #600 duplicated. |

Not worth taking: the request-path placement, the per-pack revision-fencing
storage and its control/lock sidecars, the client-side contract validators and
mini-frameworks (web `observed-motion/`, iOS `ObservedMotion.swift`), the
single 5 dBZ / 15,000 ft contours, the fixed 15-minute horizon, and the
`designs/reviews` / `docs/superpowers` process logs.

---

## 8. Possible direction (not yet a plan) — superseded by §10

1. **Phase-2 verdicts** as designed (D8, D9) plus the Parquet verification
   stream.
2. **Verification harness** (§6.1) running offline over retained frames — no
   UI, no payload. Output: skill vs lead time at two thresholds, by regime.
   This decides the horizon, the contour thresholds and the acceptance gates.
3. **Static object identification** (§3) in the collector loop, written as a
   small JSON sidecar next to each DBZH frame; surfaced as "N cells with
   lightning within R NM, tops FLxxx, age" in the summary and on the map.
4. **Motion** (§4) with lifecycle, projected footprints and the §2 closest
   approach per route point, horizon from step 2, self-verified continuously
   (§6.2).
5. **Steering-wind cross-check and model phase error** (§5.2, §5.3).
6. **Fronts and initiation** (§5.4, §5.5), satellite-only objects (§5.6).

Decisions to take before step 3: compute placement (collector loop — see
§1), the two-tier contour values, and what the pilot sees at each step. The
display stays a single frame: outlines, velocity arrows and projected
footprints are not an animation.

---

## 9. Feasibility notes

- **Compute.** Connected components on the full OPERA grid is a 16 MB integer
  array. Optical flow on the composite is perhaps a couple of hundred
  megabytes transient — on a droplet that peaks near its cgroup limit during
  GRIB decode, so the collector must keep ticking off the decode minute as it
  already does. Cropping to a route bounding box is the cheap alternative.
- **Measured pitfalls from #600** to avoid: per-cell Python polygon tests
  (`np.vectorize(covers)` at 1.4 s per call vs 7 ms for
  `shapely.contains_xy`); polygonising a 49 %-nodata mask (16k parts, 5 s);
  walking full-width CTTH strips column by column (~9 s per frame); hashing
  54 MB granules three times.
- **Dependencies.** scipy is already transitive; shapely 2 would become direct;
  pySTEPS optional and only if family B is chosen.
- **Storage.** A per-frame object sidecar is kilobytes; nothing changes the
  0.5 GB frame budget.

---

## 10. Revised direction (2026-10-03): the mini owns the field, the droplet owns the route

Discussion of 2026-10-03. Two things changed since §8 was written: the
flight-day live layer shipped (#637, a 10-minute per-flight tick that already
raises radar/lightning "appeared" events and logs evidence to
`live_history.jsonl`), and the home compute node (M4 Mac mini) has run the
forecast offload in production since 2026-07-27. Phase-2 verdicts (D8/D9) are
still unbuilt and are no longer a prerequisite.

### The split

Detecting and tracking cells does not depend on any route. Relating a cell to
a route depends on the flight's geometry, ETAs and, in flight, own-ship
position, all of which live on the server. So:

| Where | Does | Keeps |
|---|---|---|
| **Mac mini: a new, always-on loop** (separate from the forecast offload; launchd KeepAlive, not a calendar job) | Fetches OPERA and EUMETSAT itself. Processes every radar and satellite update over all of Europe: cells at two thresholds, attributes (peak, rate, flashes, tops), motion field, lineage and lifecycle, and self-scoring of its own projections against later frames. Pushes one small artifact per update to the droplet. | Full history (frames and catalogues) on the mini/NAS: the dataset for backtesting, refining thresholds and re-running the analysis. What to retain long-term is deferred: start by keeping it, prune once the analysis is trusted. Raw CTTH (~7–8 GB/day) is the first candidate to drop in favour of per-cell attributes. |
| **Droplet** | Live tick reads the newest catalogue and does route geometry only: off-track distance and side, nearest named point, along-track position, closing speed, closest approach against each point's ETA. A few ms per flight. | Only the last few hours of artifacts, purged like the frame store. |

The droplet's per-route radar processing (a crop analysis in the tick) is not
built. The analysis function is still written over an arbitrary grid window.

### The vocabulary this enables, by tier

| Tier | Example | Nature | Gate |
|---|---|---|---|
| 0. Corridor trend | "heavy rain 10–20 NM left of route near LFLY, intensifying over 30 min" | observation | none beyond correctness |
| 1. Static cells | "cell 8 NM right of route abeam LFLY, lightning, tops FL350" | observation | none beyond correctness |
| 2. Observed motion and lifecycle | "moved NE at 25 kt over the last 30 min, growing" / "weakening" | observation (past tense) | none beyond correctness |
| 3. Projection against ETA | "moving toward the route at 25 kt, near LFLY ~14:20Z, about 10 min before your ETA there" | forecast | horizon and acceptance from the mini's self-scoring history |

Tiers 0–2 describe what was measured and can ship once correct; only Tier 3
claims the future and waits on measured skill (§6.1, now running continuously
on the mini instead of as a one-off offline harness).

### Constraints carried over

- Annotate-only (§1): nothing here moves a grade.
- A stale or missing catalogue is reported explicitly ("cell analysis
  unavailable since HH:MMZ"), never as an empty sky (three-state coverage).
- Every artifact carries the analysis version (`policy_version`, §7), and
  the deploy skill's node-drift check covers this loop as it does the forecast
  node.

### Decided

- **Outage policy: dark, stated.** When the mini is down or its catalogue is
  stale, cell analysis is unavailable and every surface says so ("cell analysis
  unavailable since HH:MMZ"). This is a hobby service; the architecture is what
  matters. No droplet crop fallback.
- **Scaling path.** Robustness, if ever needed, comes from a more resilient
  compute node (e.g. a DigitalOcean node running the same loop), which costs
  money but leaves the architecture unchanged. The droplet stays a consumer.
- **Dev collection on the MacBook.** The same loop runs on the dev machine to
  collect frames and iterate on the analysis while the mini loop is built and
  tested; nothing on the dev machine pushes to production.
- **Runs identically on macOS and Linux.** The mini and the MacBook are macOS
  (arm64), and a DigitalOcean node or CI is Linux (x86-64):
  - Keep to libraries with wheels on both and no BLAS-sensitive core:
    `scipy.ndimage` labelling and `scipy.fft` (pocketfft) behave identically
    on both. numpy/scipy link Accelerate on macOS and OpenBLAS on Linux, which
    only matters for linear algebra (last-digit differences).
  - No OpenCV, and nothing that needs a source build on either platform.
  - No process pools in the analysis, or force the `spawn` start method:
    macOS defaults to `spawn` and Linux to `fork`, a classic portability trap.
  - A golden-catalogue test (a few stored frames → expected cells and motion
    within tolerance) runs on the Mac and in Linux CI.

### Still open

1. First surface: agent `live` block and live change events (text only), before
   map outlines and arrows.
2. Core threshold: 41 dBZ to match `RADAR_SIGNIFICANT_DBZ` vs. 35 dBZ for
   earlier detection; the history will settle it.
3. EUMETSAT data-volume quota with three consumers (droplet, mini, dev
   machine): unchecked.
4. Artifact format and push path (inbox rsync as in the forecast offload, or a
   push endpoint), and the droplet's retention window.
