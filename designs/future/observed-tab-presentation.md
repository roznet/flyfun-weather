# Observed tab: from "what you need to know" to the details

Status: direction agreed 2026-10-06. Slice 1 (#689) done: reissue "updated",
`new_sigmets`, SIGMET-after-arrival highlight, tops "unavailable", radar peak
position in the summary, iOS storm count/sort/wording as interim client logic
(`CellsOverlay.routeStorms`, to be replaced by #688's server geometry).
Slice 2 (#688, PR #692) landed: per-storm route geometry in the tick,
`LiveLayer.storms`, storm rows (meteorology-decisions §41), estimates logged
for `score-estimates`; thresholds not yet calibrated on a real-cell replay.
Slice 3 server half (#690): `LiveLayer.glance` / `ribbon` / `focus` built in the
tick, on `/live` and the agent `live` block (`tasks/live_glance.py`, as-built notes in
`live-observation-layer.md`). Slice 4 iOS landed in the same PR (#695, 2026-10-07):
nutshell, the ribbon as a symbolic map (rain/core bands from the cells feed's
outlines, airports either side, motion arrows), tap-to-map, storm sheet; pilot text
says "cell". The Layer 3 cells list still uses the client-side
`CellsOverlay.routeStorms`. Slice 4's web half landed next (#699): the same
nutshell and ribbon as two sections at the head of the briefing page's
Observations group, in the iOS reading order, with tap-to-map and the cell
detail. The ribbon's rules were extracted into a mirrored pure pair
(`ribbon-core.ts` ↔ `RouteRibbonRules.swift`) with tests that assert the same
strings on both sides — as-built notes and the two deliberate palette
divergences are in `live-observation-layer.md`.
Slice 5 server half (#697) landed 2026-10-07: the Haiku-written highlight is
generated after each tick commits and stored on `glance.highlight`, with a
grounding check and a per-flight review log — **written but displayed nowhere**
(owner's call: a dozen live flights a day get reviewed over the following days
to calibrate the prompt before any client shows it, which also makes the #696
dependency moot for now). As-built in `live-observation-layer.md`. It needs
#695 deployed to produce anything: the facts come from `glance` + `ribbon`.
Slice 6 (#697 display, 2026-10-09): the highlight is shown on iOS and web above
the ribbon, alert lines above it, with an experimental caption, its written
time and a 👍/👎 recorded as `highlight_rating` feedback; iOS folds the
headline, other lines and other change rows under "Details" (collapsed,
remembered), web leaves them unfolded; the agent `live` block quotes it.
As-built in `live-observation-layer.md` ("Display and feedback").
Builds on `designs/live-observation-layer.md`.

## 1. Premise

The Observed tab is organised by **data source** (Radar / Cells / METAR/TAF / SIGMET),
and every row answers "what is near the route?". A pilot asks by **phase and time**:

1. Can I leave now?
2. What will be on my track when I get there?
3. What will the destination be at my ETA?
4. Is the briefing verifying, better or worse?

The missing ingredient is the relation of each phenomenon to the aircraft's track and
time: which side, how far off, when I'm abeam, closing or moving away. The data for
that already exists (cell position, motion, trend, lineage; SIGMET movement; the
pack's ETA per point). It just isn't computed against the route.

Product voice is unchanged: attention, not a verdict
(`feedback_not_go_nogo`); progressive depth (simple at the top, drillable).

## 2. Decisions (2026-10-06)

- **The route ribbon lives on Observed; the map stays one tap away.** Anything on the
  ribbon or in a list that the map can show is tappable, and opens the map focused
  on that item with the relevant layers on (see §5).
- **Everything route-relative is computed on the server**, once, in the live tick, and
  shared by iOS, web and the MCP/agent `live` block. Clients render; they don't
  derive.
- **Observed motion is shown** ("moving away at 11 kt", "closing at 8 kt", "3 → 9 NM in
  25 min"): it is an observation.
- **The projection is computed and logged, not shown by default.** "Near the route at
  14:50Z" is computed every tick for every relevant cell and written to
  `live_history.jsonl` so its accuracy can be scored against later frames. It appears
  only in a cell's detail pop-up, labelled **"Estimate at current motion"**, never in
  the nutshell, the ribbon or an alert, until the scoring says it has skill at that
  horizon.

## 3. Layers

**Layer 0: the nutshell.** One line per phase, plus one line comparing with the
briefing. Each line is tappable into its layer. Example (LPPR→LPPT, 2026-10-06 14:29Z):

```
Observed 14:29Z · as briefed, departure improving
DEPARTURE  LPPR VFR · nearest storm 9 NM NE (left of track, behind), moving away 11 kt · no lightning ≤20 NM
EN ROUTE   Storms 25–40 NM east (inland), all moving away ENE · TS SIGMET covers first 150 NM (briefed)
ARRIVAL    LPPT VFR · TAF PROB30 TSRA at ETA · nothing within 30 NM now
```

Rules: one "as of" time for the block, a stale source flagged inline; missing data
says "unavailable", never "clear"; counts are storms, not threshold tiers.

**Layer 1: the route ribbon.** The x-axis is distance along the route, labelled with
ETAs; the y-axis is left/right of track. Lanes:
- stations: METAR category now, TAF at ETA beneath (PROB/TEMPO hatched);
- SIGMET coverage band (with "moving away/toward" from its MOV);
- radar maximum per route segment;
- each storm as one marker at its side offset, arrow coloured closing / moving away,
  size by intensity, a lightning glyph when flashing.
iPad horizontal; iPhone may run it vertically.

**Layer 2: what changed.** The current "Since this briefing" list, plus:
- a reissue of a briefed SIGMET is "updated", not "worse";
- one rule for NEW shared by every surface;
- a "since you last looked" marker.

**Layer 3: details by source.** The current tabs, but:
- cells grouped into storms (core41 inside core35 inside rain20 = one storm) and
  sorted by relevance to the track (nearest distance near my time there, then
  intensity), not by dBZ;
- position relative to the route ("9 NM left of track abeam LPPR"), not "NE of airport";
- one stated corridor, or the distance on every row;
- trend numbers (`d_peak_db`, `area_ratio`, `d_flashes`) one tap down;
- the cell detail pop-up carries the observed track (last 30 min) and the
  "Estimate at current motion" block.

## 4. Server model

Computed in the live tick, written to `live.json`, served by `/live`, overlaid
wherever the live layer already goes. Sketch, names to settle:

- `storms[]`: one per lineage group, with `id`, the dominant cell id per tier, peak
  dBZ, flashes, tops (or unavailable), trend + numbers, motion `{status, toward_deg,
  speed_kt}`, and route geometry from #688: `side`, `offtrack_nm`, `along_nm`,
  `abeam_eta`, `nearest_point`, `relative_motion` (`closing` / `moving_away` /
  `parallel` / `unknown`, with the perpendicular component in kt), and the observed
  off-track history over the last few frames.
- `storms[].estimate` (logged, detail only): closest approach to the 4-D track at
  current motion, `{cpa_nm, cpa_time, at_eta_offtrack_nm, horizon_min}`, computed
  only when `motion.status == "available"`.
- `glance`: the nutshell lines, by phase, each with `focus` (§5) and the source ids it
  summarises, so agents and both clients show identical text.
- `ribbon`: segments along the route with the lane values above, already binned.

## 5. Tap-to-map contract

Every item that can be drawn on the map carries a `focus` the client turns into a
map deep link: `{kind: storm|sigmet|station|segment, id, bbox, layers: [...],
time}`. The map opens framed on the bbox with the named layers on (e.g. a storm →
radar + cells + lightning at that frame, the storm highlighted; a SIGMET → its polygon
and the route; a station → its METAR/TAF marker). One contract for iOS and web.

## 6. Scoring the estimate

- Each tick logs every storm's estimate in `live_history.jsonl` (new row type, e.g.
  `"type":"estimate"`), keyed by lineage id and tick time.
- A `review.py score-estimates` step (prod-briefings-review skill) joins each estimate with the
  same lineage's observed position at `cpa_time` from later display frames, and
  reports the CPA distance error and timing error by horizon (0–30, 30–60, 60+ min),
  and by motion confidence.
- The mini's own self-scoring (motion vs persistence at 30/60 min) is a field-wide
  check; this one is route-relative and is what decides whether the estimate may
  leave the pop-up.

## 7. What the 2026-10-06 test flight showed (LPPR→LPPT)

Screens at 14:29Z against the data:

1. "65 radar cores near route" counts core35 and core41 cells of the same storm
   separately, in the corridor box widened by 50 NM (`CellsOverlay.marginNm`,
   `listedCells`). In fact: one storm within 10 NM (moving away), the rest 25–40+ NM
   inland, all moving away.
2. "1 worse since briefing" was the LECM 4→6 reissue of a briefed SIGMET.
3. Area Hazards badged LECM 6 **NEW** while the change list said "replaces 4".
4. "Cloud tops: clear over the whole corridor" next to a 49 dBZ echo; every cell had
   `top_fl = null`. `_tops_clause` (`observed/summary.py`) says "clear" whenever no
   covered point has a top, without checking radar.
5. Cells sorted by dBZ: the top 5 were 36–75 NM away; the only near storm (9 NM NE of
   LPPR, moving away 3 → 9 NM in 25 min) was under "Show all 65".
6. Counts and scales disagree: 68 vs 65 cells; radar 20 NM, METAR 30 NM, SIGMET 50 NM,
   cells box + 50 NM; every row with its own age.
7. Two radar rows read as contradicting ("no heavy echo within 5 NM" / "very heavy
   echo 49 dBZ within 20 NM of LPPR"), neither saying where or which way.
8. TAF PROB30/40 TSRA CB at 5 of 8 stations, all METARs VFR: the strongest forecast
   signal is only in the table.
9. Wording: "motion withheld: split/merge this frame", "1 flashes", "75.5 dBZ"; the
   radar section's "Within 20 NM of the route" footer reads as the cells' filter.

## 8. Slices

1. **Correctness fixes** (iOS + the tops clause): items 1–5, 7, 9 of §7 (#689).
2. **#688 geometry + estimate logging** (addendum comment on #688): per-storm route geometry in the tick;
   estimates logged; `score-estimates` in the prod-briefings-review skill. Built: `LiveLayer.storms` (§4's
   `storms[]` minus `focus`; names as in `models/live.py::LiveStorm`), storm rows (meteorology §41).
3. **Server `glance` + `ribbon` + `focus`**, and the agent `live` block uses `glance` (#690).
4. **iOS**: nutshell, ribbon, tap-to-map, storm detail pop-up with the estimate (#695).
   Then web: the same, as `observed-glance` + `observed-ribbon` sections over a
   shared pure rules module.
5. **Promote the estimate** out of the pop-up only when §6 shows skill at that
   horizon.
