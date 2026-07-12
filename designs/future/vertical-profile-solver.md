# Vertical-Profile Path-Finder for Altitude-Dependent Advisories

> Shared `(route-distance × altitude)` cost-field solver that replaces the per-axis
> VFR mitigation gates and is reused by icing-escape. Issue #335. Supersedes the
> ad-hoc corridor logic from #328/#330.

## Intent

Today's VFR mitigation logic (`analysis/advisories/vfr_feasibility.py`) reconstructs
path continuity from three proxy axes — `cruise_imc` (vertical scan), `climb_deck`
and `descent_deck` (corridor scans) — each with its own compensating gates:
terminal-corridor width, `under_deck_flyable`, the midpoint scan, a 25 nm reposition
cap, and a "only offer the corridor mitigation when cruise is GREEN" gate. Every gate
exists to stop the decomposition from producing nonsense ("climb to cruise after
10 nm" when cruise is IMC 40 nm later). Those are symptoms of decomposing **one**
question — *is there a continuous flyable vertical profile, and where is it blocked?* —
into three axes that can't see each other.

This doc specifies a single **cost-field shortest-path solver** over a
`(route-point × altitude-bin)` grid. Each advisory contributes only a hazard→cost
mapping; the solver, terrain floor, ceiling cap, transition detection, and
profile/blockage output are shared. Grading stays per-advisory and **unchanged** in
v1 — the solver replaces the *mitigation* layer only.

Two deliberately different first consumers prove the abstraction generalizes:

- **VFR feasibility — hard wall.** You cannot legally occupy a BKN/OVC cell VFR, so
  those cells are `∞`. The path routes *around* decks (under them, or above them below
  ceiling); it never crosses one.
- **Icing escape — soft wall.** You *can* transit a thin/light icing layer at a finite
  cost to reach clear air above or warm air below. This expresses a maneuver the
  current single-transition code cannot: *climb over the ice, cruise on top, descend
  below (clear of terrain) before destination.*

Building it cost-based from the start is exactly what lets a hard-wall and a soft-wall
consumer share one solver.

## Core model

For each route point `i` (≈20–60 along the route) and altitude bin `a`, a **cost**:

- `∞` — hard wall: cannot occupy or cross (BKN/OVC cell for VFR; SLD / freezing-precip
  cell for icing).
- finite — soft wall: occupy/cross at a penalty (thin/light rime × thickness).
- `0` — feasible.

Bounded below by `terrain(i) + margin` (per point) and above by the flight ceiling
(per point). A shortest-path / DP over the grid returns either:

- the **min-cost continuous vertical profile** — a list of distance-bounded altitude
  bands with the climb/descent transitions between them — or
- the **blocking segment** when no feasible path exists ("no VMC between 180–220 nm,
  deck surface→FL80").

The grid is tiny (≈60 × ≈40 bins), so a general DP is effectively free versus a bespoke
connectivity solver, and it handles multi-deck-with-gaps routes that a greedy
"single max clear altitude" scan misjudges.

### Locked decisions (see #335 comment thread)

1. **Cost-based, not connectivity-based.** Hard walls are just `∞`. This is what lets
   cloud (hard) and icing (soft) share one algorithm.
2. **Above cruise is a valid solution** as long as it's below the per-point ceiling.
   Planned cruise is a *preference*, not the upper bound. If the only feasible band is
   above cruise, report it.
3. **Pluggable per-advisory part = the hazard→cost mapping only.** Solver, terrain
   floor, ceiling cap, transition detection, and profile/blockage output are shared.
   Grading (R/A/G) and phrasing stay per-advisory and unchanged in v1.
4. **v1 ignores climb/descent rate.** Leave a `rate_limit` hook in the signature; do
   not implement the gradient constraint first.
5. **Objective is lexicographic**, not a weighted multi-term cost. Order:
   *no hard-wall (`∞`) crossing* → *lowest total finite hazard cost* → *closest to
   preferred cruise* → *fewest vertical transitions*. The finite-hazard-cost tier is
   **essential, not cosmetic**: for a soft-wall consumer every path is "feasible"
   (nothing is `∞`), so without it finite costs do nothing and icing avoidance is a
   no-op — the solver would keep you at cruise in light icing rather than making two
   transitions to escape most of it.
   **Correction during implementation:** *closest-to-cruise (deviation) must precede
   fewest-transitions*, not follow it as the earlier draft (and Codex's note) had it.
   With transitions ahead of deviation, a flat low profile (0 transitions) beats climbing
   back to cruise (1 transition) at equal hazard — i.e. an aircraft forced low by a
   departure deck would never climb back up. Transitions become only a tie-break between
   equally-close-to-cruise paths (still prevents needless oscillation). Codex's icing
   concern is unaffected: it is resolved by the hazard tier, which is unchanged.
   Keeping the objective a strict tier order (not summed weights) still avoids a fragile
   6-weight function. Leave a hook to add preference terms later.
6. **Transitions happen on edges between adjacent route points** (climb-while-
   progressing), not in place. A transition from band `a` at point `i` to band `b` at
   point `i+1` crosses the altitude interval `[a, b]`. **Conservative column
   convention:** the crossing is charged against *both* endpoint columns (`i` and
   `i+1`) over `[a, b]` — it is `∞` (blocked) if **either** column has a hard-wall cell
   in the interval, and the finite crossing cost is the **max** of the two columns'
   summed finite costs over the interval. This is deliberately conservative for the
   "deck ends between points" case: a deck present at `i` but modeled as gone at `i+1`
   still blocks the climb (you would fly through it mid-transition), so the path cannot
   sneak through on the strength of one clear endpoint. Net: VFR cannot climb through a
   deck; icing can climb through a thin layer at a penalty. Tested explicitly.
7. **BKN and OVC are both `∞` for the VFR path.** You cannot legally fly VFR inside
   either, regardless of coverage — so both are impassable cells. The
   `BKN→AMBER` / `OVC→RED` distinction is a *grading* nuance about gap likelihood and
   stays entirely in the grade (unchanged, per decision 3); it does not enter the cost
   field. This reproduces today's behavior: the path routes *under* the deck to where it
   breaks, and never offers "climb through the broken layer" (which isn't VFR-legal and
   the deterministic model can't see the gaps between points anyway).
8. **Icing soft-wall floor.** SLD (`IcingZone.sld_risk`) and freezing precip stay `∞`
   or near-`∞`; only thin/light rime is finite-crossable, with the crossing-cost
   threshold explicit and pilot-facing. The solver must not casually price non-FIKI
   icing transit.
9. **The path is surface-anchored at both ends.** The aircraft departs from and lands
   at the field, so `solve()` takes `allowed_start_bins` / `allowed_end_bins`, and for
   VFR they default to the terrain-floor band at the departure (point 0) and arrival
   (point N) points. Without this the continuity question is **under-specified**: an
   unconstrained solver could "start at cruise" and skip the climb-out deck entirely —
   exactly the `climb_deck` / `descent_deck` problem it is meant to replace. Anchoring at
   the floor forces the climb-through-deck question and recovers those axes as emergent:
   a departure OVC at 500 ft AGL leaves only start bins below the deck, the climb hits
   the `∞` wall, and the solver returns a blockage — today's `climb_deck` RED, derived
   rather than special-cased.

## Interface

As shipped (`vertical_profile.py`), the solver takes the bundled `CostModel` rather than
unpacked axis arrays — the terrain floor and ceiling are baked into `cost_field` as `inf`
cells rather than passed as separate per-point lists, which keeps `solve()` a pure grid
search:

```python
@dataclass(frozen=True)
class CostModel:
    cost_field: list[list[float]]        # [point_i][alt_bin] -> 0 / finite / inf
    distances_nm: list[float]            # x-axis label per point
    bin_altitudes_ft: list[int]          # y-axis label per bin (multiples of 500)
    allowed_start_bins: set[int] | None  # bins the path may begin in at point 0
    allowed_end_bins: set[int] | None    # bins the path may end in at point N

def solve(
    model: CostModel,
    preferred_alt_ft: int,               # planned cruise; a preference, not a bound
    rate_limit: float | None = None,     # hook (decision 4) — accepted, unused in v1
) -> Profile | Blockage: ...
```

- `Profile{ segments: [Segment(dist_from_nm, dist_to_nm, alt_ft)], transitions: [Transition(from_nm, to_nm, from_alt_ft, to_alt_ft)], total_cost }`
- `Blockage{ from_nm, to_nm, reason }`

`solve()` selects the lexicographically-best path (decision 5) that begins in
`allowed_start_bins` and ends in `allowed_end_bins` (decision 9), with edge transitions
charged per the conservative column convention (decision 6). Path cost is a `_Cost`
triple `(hazard, transitions, deviation)`; its ordering key is `(hazard, deviation,
transitions)` — deviation before transitions per the correction below.

Each advisory implements only its per-cell hazard→cost mapping; the shared
`build_cost_model` (in `_helpers.py`, decision 3) does bin construction, terrain-floor /
ceiling walling, and endpoint anchoring once:

```python
def build_cost_model(
    ctx: RouteContext,
    model: str,
    cell_cost: Callable[[SoundingAnalysis, float], float],  # advisory's mapping
    floor_margin_ft: float,                                 # clearance above terrain
) -> CostModel | None: ...   # None when no route point carries this model's sounding
```

### Data sources (grounded in current models)

- Points and distance: `ctx.analyses[i].distance_from_origin_nm`, `ctx.total_distance_nm`.
- Per-point sounding: `ctx.analyses[i].sounding[model]` (`SoundingAnalysis`).
- Cloud cells: `sounding.cloud_layers` (`CloudLayer.base_ft/top_ft/coverage`,
  `CloudCoverage.{BKN,OVC,...}`).
- Icing cells: `sounding.icing_zones` (`IcingZone.base_ft/top_ft/risk/icing_type/
  sld_risk`), plus `sounding.indices.freezing_level_ft` for the warm-air floor.
- Terrain: `ctx.elevation` (`ElevationProfile.points`, `max_elevation_ft`) — reuse the
  existing `_field_elevation_ft` / `max_terrain_near_point` helpers and the current
  `_TERRAIN_CLEARANCE_FT` (1000 ft) so the floor matches today's mitigation floor.
- Ceiling: `ctx.flight_ceiling_ft` per point (flat in v1; per-point hook stays).
- Altitude bins: align resolution to the terrain-clearance floor and
  `flight_ceiling_ft`; do not invent new constants.

## Consumer A — VFR feasibility (hard wall)

Cost field: `∞` inside any BKN/OVC layer plus the cloud-clearance margin
(`cloud_clearance_ft`, default 1000), else `0`. Bounded by terrain floor and ceiling.
Endpoints anchored (decision 9): `allowed_start_bins` / `allowed_end_bins` = the
terrain-floor band at the departure (point 0) and arrival (point N) points, so the path
must actually climb out and descend in rather than materialize at cruise.

- **EDDN→EGSG** (en-route IMC, cruise 10k): the connected VMC band never reaches cruise
  → profile is "fly ~7,000 the whole way", with **no** "climb to cruise" claim. The
  cruise-green gate from #330 becomes unnecessary — it falls out.
- **LFMD→EGTF** (cruise clear, OVC deck at arrival): forward holds cruise, backward from
  arrival is forced low under the deck; they meet at the descent point → "descend before
  ~X nm" is *derived*, not special-cased.

The four #328/#330 gates (terminal corridor, `under_deck_flyable`, midpoint scan, 25 nm
cap, cruise-green gate) are **deleted, not ported** — their behavior is an emergent
property of the path model. That deletion is the success signal for this change.

## Consumer B — Icing escape (soft wall)

Cost field from temperature + moisture + severity: high inside the icing band, **0
below the freezing level** (warm air is feasible even in cloud), finite (crossable) for
thin/light layers, `∞` for SLD/freezing precip (decision 8).

- Today `icing_escape.py` models a single transition ("descend to warm air"). The path
  model expresses the richer maneuver: climb above the icing to on-top, then descend
  below it (clear of terrain) before destination — a two-transition profile — or reports
  that no ice-free/warm continuous band exists.
- Porting `icing_escape` is the validation that the interface isn't VFR-shaped
  (consumer #2), and it upgrades the feature.

## Result model & cross-section

The structured result and the eventual cross-section overlay are the **same
requirement**: the cross-section is a `(route-distance × altitude)` canvas that already
draws the cruise line + flight ceiling (`reference-lines.ts`), i.e. exactly the solver's
coordinate space.

- Extend `Mitigation` (`models/advisories.py`) with an **optional**
  `segments: list[(dist_from_nm, dist_to_nm, alt_band_ft)]` + `transitions`
  (and a `blocked_interval` for the no-path case). **Keep the existing flat
  `altitude_ft` / `distance_nm` fields** so old packs deserialize and digest / MCP /
  iOS / web keep rendering the one-line tip unchanged. Non-breaking.
- The varying-altitude cross-section overlay is a **separate follow-up issue** that
  depends on this result model: a new neutral-colored `mitigation-path` line layer beside
  the reference lines (advice, not verdict — never green/red), a layer toggle, and a
  hover band readout. #335 owns the data shape; the render issue owns the canvas layer.

### Aggregation

Do **not** merge profiles across models with the representative-model "first status
match" policy (`_aggregate_mitigations`). It is acceptable for a one-line tip but
misleading when a full profile is presented as *the* answer while another model says
no-path. Keep profiles **per-model**; where they disagree, surface "found in 2/3 models".
The cross-section is already drawn per selected model, so the visual renders that model's
profile and never needs a cross-model merge — the per-model decision makes the overlay
fall out for free.

## Testing

Synthetic grids (no fixtures — hand-built cost fields), asserting profile / blockage:

- single deck (route under it)
- multi-deck with a gap (greedy max-altitude scan fails; DP must thread the gap)
- deck running to terrain (blockage reported, no false mitigation)
- above-cruise-only feasible band (decision 2)
- no feasible path (blockage with from/to/reason)
- two-transition icing (climb-over-then-descend, decision 6/8)
- **departure-anchored deck** (decision 9): OVC at the departure floor → blockage, not a
  "start at cruise" cheat; and the symmetric arrival case
- **deck ends between points** (decision 6): deck present at point `i`, clear at `i+1` →
  the `i→i+1` climb is still blocked by the conservative column convention
- **soft-wall preference** (decision 5, tier 2): a feasible stay-at-cruise path through
  light icing must lose to a two-transition path that escapes most of the icing —
  guards against the finite-cost tier being a no-op

Regression: port `vfr_feasibility` first as a **pure refactor** that emits the same flat
`Mitigation` objects, and behavior-compare over the eval corpus — the 30+ packs that
carry corridor mitigations are the before/after set. Classify each change as
same / better / removed / new; the solver must be equal-or-strictly-better before the
segment model is grown behind it. Do not change the flat output and the model shape in
the same step, or a solver bug is indistinguishable from a shape change in the diff.

## Sequencing

1. This design doc + solver interface (this commit).
2. `solve()` DP + synthetic-grid tests — pure, no advisory dependency.
3. Port `vfr_feasibility` mitigations onto the solver; validate equal-or-better across
   the corridor eval packs; delete the four #328/#330 gates.
4. Port `icing_escape` (gains climb-over-then-descend); add the two-transition test.
5. Add optional `segments` to the result model; digest / MCP / iOS / web unchanged.

## Out of scope / future

- Further consumers (`turbulence`, `fiki_icing`, `cloud_top`, `vmc_cruise`,
  `headwind`) — validate the interface with two first; don't over-generalize on guessed
  requirements.
- **Combined multi-hazard profile** (sum cost fields → one clear-of-everything
  altitude) — the strategic prize the shared solver unlocks, but not v1. A coherent
  *feasibility grade* only exists once this does, which is another reason grade
  integration (issue decision C) waits past v1.
- Climb/descent-rate constraint (`rate_limit` hook, decision 4).
- Lateral / re-route escapes — the model is a 2-D vertical slice only (same limitation
  as today).
- Letting the path drive the **grade** (RED→AMBER capping) — the A-now / C-later
  decision in the #335 thread keeps grading per-advisory for v1; revisit against the
  eval corpus later.
- Do not force non-path advisories in (`freezing_precip`, `llws`, `density_altitude`,
  `mountain_wind`).

## Implementation outcome (v1)

- **Unified floor = `terrain + mitigation_min_base_agl_ft`** (default 3000 ft) for both
  the "fly lower" (cruise_imc) and "fly under a deck" (corridor) mitigations — the single
  conservative scud-running margin (chosen over keeping two separate floors). Consequence,
  by design: a tight terrain-to-deck gap that leaves under 3000 ft no longer produces a
  marginal "fly lower" tip (it did under the old 1000 ft floor). Covered by
  `test_vertical_tight_terrain_gap_suppresses_mitigation`.
- **Gates deleted (emergent):** the cruise-green mutual-exclusivity, the midpoint scan
  (`_deck_then_clear`), and `_under_deck_flyable` are gone — the single solved profile
  yields the right mitigation and the exclusivity falls out (a profile that can't reach
  cruise has no climb-to-cruise transition to suppress). **Kept as a presentation filter:**
  the `mitigation_max_reposition_nm` cap (a pure distance heuristic on the reported break,
  no floor conflict).
- **Consumers:** `vfr_feasibility` (hard wall) and `icing_escape` (soft wall) both ported;
  icing gains the climb-on-top / climb-over-then-descend maneuver the old single-transition
  code could not express. Both attach the structured `MitigationProfile` for the future
  cross-section overlay. Grades are untouched (advice-only).
- **Icing soft wall is LIGHT+RIME only** (decision 8, tightened in #338 review): the sole
  finite-crossable cell is thin/light *rime*; MODERATE/SEVERE at any type, non-rime light
  (clear/mixed) ice, and SLD are all hard walls (`∞`) — a non-FIKI aircraft routes *around*
  them, never "climbs through at a penalty".
- **Corridor tips require a clean terminal deck** (#338 review): a `climb_deck`/`descent_deck`
  is suppressed when the profile also dips below cruise in the *interior* (a mid-route deck),
  so "climb to cruise after ~X nm" is never offered when the flight has to descend again
  further out — closing the same failure the deleted cruise-green gate guarded against.
- **Shared `build_cost_model`** (`_helpers.py`, #338 round-2 follow-up): bin construction,
  terrain-floor/ceiling walling and floor anchoring live once; each advisory supplies only
  its `cell_cost` mapping + floor margin. Terrain is looked up once (linear interp,
  `terrain_at_distance`) so VFR and icing compute the *same* floor for a point, and
  `MITIGATION_BIN_STEP_FT` is sourced once from `vertical_profile.py`.
- **`cruise_imc` scans for the flat altitude** (#338 round-2 follow-up): the solver gates
  feasibility (Blockage → no tip), then a downward per-step scan picks the highest
  whole-route-improving flat altitude — so a staircasing profile (deck height varies) no
  longer drops an otherwise-valid "fly lower" tip. Regression:
  `test_vertical_staircase_deck_scans_for_flat_altitude`.
- **Tests:** `tests/test_vertical_profile.py` (solver, synthetic grids),
  `tests/test_vfr_mitigation.py` (VFR port), `tests/test_icing_escape_mitigation.py`
  (icing port). Full suite green (3109 passed).
- **Eval-corpus behavior compare (done):** old-code vs new-code mitigations recomputed via
  `eval_workbench.rerun.rerun_manifest` over the 204 staging packs (the committed corpus
  baselines predate the mitigation feature, so the before/after is old-code vs new-code, not
  saved-baseline vs new). Result for VFR `cruise_imc`: **40 removed = blockage** (new solver
  finds a full-column cloud wall → no continuous VMC path, old %-scan's lower tip was
  unflyable), **14 removed = reaches-cruise** (continuity-aware profile reaches cruise, old
  was over-eager), **1 removed = below the new 3000 ft floor** (expected), **1 changed**
  (5000 GREEN → 7500 AMBER: the highest *continuously-reachable* band, more correct), 1 kept.
  Corridor climb/descent tips: a refinement wash (~7 new the solver newly finds, ~7 dropped
  as not continuously flyable). Icing: ~40 new escape tips (the additive upgrade), bounded by
  the flight ceiling (e.g. a FL180/ceiling-FL250 flight climbs 1000 ft over a patch). Net:
  **equal-or-better — removals are overwhelmingly improvements or expected; no regressions
  found** (every removal is a real wall, a genuine reaches-cruise, or the floor change).

## Related

- Issue #335 (this plan), supersedes the corridor logic in #328 / #330.
- `designs/advisories.md` — evaluator protocol and `RouteContext`.
- `designs/visualization.md` — cross-section layers / `reference-lines.ts`.
- `designs/meteorology-decisions.md` — record the BKN/OVC and icing soft-wall calls here
  once implemented.
