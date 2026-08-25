# Advisory Extent: Review and Consolidation

> Review — 2026-08-25. Not implemented. Triggered by the turbulence misreport on
> `lfmd_..._egtf-2026-08-27` (D-2 pack `2026-08-25T09:32:55`), and by the #568
> convective-character EMBEDDED fix, which solved one instance of a defect class
> that is still present everywhere else.

## Intent

Every route advisory eventually has to answer *"how much of the flight is
affected?"*. Today 14 evaluators answer it 14 slightly different ways, with **four
incompatible geometry conventions**, **24 parameter names for the same threshold**,
and **no minimum-extent floor on any of them but two**. Three of those differences
are not stylistic — they produce numbers that are wrong by up to 4×, and those
numbers reach the pilot, the LLM prompt, and the MCP/agent API.

This document is the census, the defect classes, and a proposed shared primitive.

---

## Part 1 — Census

### A. Route-extent evaluators (14) — grade keys on a fraction of route points

| Advisory | Denominator | Grade gate | Extent quoted in message | Min-extent floor |
|---|---|---|---|---|
| `cloud_top` | assessed points | `pct_amber=25`, red=60 | `format_extent(above_ceiling)` | — |
| `convective` | assessed points | `affected_pct_amber=20` / `affected_pct_red=50` on **any-risk** | `format_extent(affected_mod)` — **MODERATE+ subset** | — |
| `convective_character` | realized / all points | band by `realized_pct` (`isolated_max_pct=15`, `scattered_max_pct=40`) | `format_extent(realized_count)` | **`embed_min_nm=50`, contiguous** ✅ |
| `dd_nwp_agreement` | assessed points | `amber_pct=30` / `red_pct=60` | `format_extent(disagree_points)` | — |
| `enroute_precip` | assessed points | `snow_pct_amber=5` / `snow_moderate_pct_red=25` / `rain_pct_amber=30` | `format_extent(snow_pts \| sig_rain_pts \| light_pts)` | — |
| `fiki_icing` | cruise points | **`clear_cruise_amber_pct=80`** / `clear_cruise_red_pct=50` — *inverted polarity* | pct only, **no nm** | — |
| `freezing_precip` | assessed points | `primed_pct_amber=5` | `format_extent(active_pts / primed_pts)` | — |
| `icing_escape` | assessed points (**varies per model**: 49 / 61 / 51 on this pack) | `icing_coverage_pct_amber=20`, `no_escape_pct_red=15` | `format_extent(affected)` | — |
| `ifr_feasibility` | `icing_total` (icing axis), `total` (conv axis) | `icing_pct_amber=20` / `icing_pct_red=50` | `format_extent(icing_count, icing_total)` | — |
| `model_agreement` | all points | `poor_pct_amber=25` / `poor_pct_red=50` | `format_extent(poor_count \| moderate_count)` | — |
| `mountain_wind` | **mountain points only** (domain ≠ route) | pct over mountain points | `format_extent(affected, mountain_total, **route_nm**)` ⚠️ | — |
| `turbulence` | assessed points | `route_pct_amber=20`; severe bypass; 50 % significant tier | `format_extent(affected)` with **worst-tier word** ⚠️ | — |
| `vfr_feasibility` | assessed points | `imc_pct_amber=15` / `imc_pct_red=30` | `format_extent(affected)` | `_TERMINAL_DECK_MIN_POINTS=2` **or** `_TERMINAL_DECK_MIN_RUN_NM=15` — *mitigation gate only, not the grade* ⚠️ |
| `vmc_cruise` | assessed points | `bkn_pct_amber=25` / `ovc_pct_red=50` | `format_extent(ovc_count \| affected)` | — |

### B. Endpoint evaluators (5) — departure/arrival only

`airport_wind`, `approach_feasibility`, `density_altitude`, `flight_category`, `llws`.

Denominator is 1–2 airports. Extent is meaningless and correctly absent. **No change
proposed** — they should stay outside the shared primitive rather than be forced into it.

### C. Scalar / event evaluators (3)

| Advisory | Shape |
|---|---|
| `headwind` | Mean component → trip-time delta. Counts `affected` but **does not grade on it**. |
| `sun` | `dominant_side_pct` over the **sunlit** portion — its own denominator, correctly named in the message ("~59 % of the sunlit route"). |
| `fronts` | Crossing events; no extent concept. |

`sun` is worth calling out as the one place the codebase already does the right
thing: it states its denominator in the sentence.

---

## Part 2 — Defect classes

### D1 — The severity word and the extent describe different populations

`turbulence.py:198-222` builds `"<worst tier anywhere at cruise>" + "<extent of ANY-risk coverage>"`.

Measured on the trigger pack at FL180:

| model | any-risk @cruise | mod-or-worse | severe (free-atm) | published |
|---|---|---|---|---|
| ecmwf | 0 | 0 | 0 | "Smooth ride expected" ✓ |
| gfs | 18 pts / 164 nm (28 %) | 4 pts / 36 nm (6 %) | 0 | "**MODERATE over 164nm (28 %)**" |
| icon | 16 pts / 146 nm (25 %) | 3 pts / 27 nm (5 %) | **1 pt / 9 nm (2 %)** | "**Severe CAT over 146nm (25 %)**" |

"Severe CAT over 146nm/582nm (25 %)" means: severe at **one point**, moderate at two,
**light at thirteen**. The RED grade itself is correct and deliberate
(`test_free_atmosphere_severe_still_forces_red`) — a single free-atmosphere severe
layer at cruise (ICON, 393.6 nm, FL175–187, Ri = 0.44) is a real hazard. Only the
sentence is wrong.

`convective` already fixed the *phrasing* half of this in #300 — it keeps
`affected` and `affected_mod` and quotes `ext_mod` alongside the peak name, with the
principle stated in-code: *"the severity word (peak) and the coverage (MODERATE+
extent) are never conflated."* But it fixed only the string:

| convective | detail string | `affected_pct` (JSON) | `affected_nm` (JSON) |
|---|---|---|---|
| ecmwf | "MODERATE+ over 264nm (45 %)" | **68.8 %** | **400.1** |
| icon | "MODERATE+ over 91nm (16 %)" | **23.4 %** | **136.4** |

The string quotes the MODERATE+ subset; the structured fields carry the any-risk
population. Both are published, and consumers read the structured one.

### D2 — The printed nm is a point ratio; the geometry-accurate nm is computed and discarded

`summarize_evidence` already computes `affected_nm` correctly — the sum of
midpoint-owned cells (`cell_edges`) of the affected points — and evaluators pass it to
`ModelAdvisoryResult.build`. But the **message** calls `format_extent(affected, total,
total_distance_nm)`, which recomputes `total_nm × affected / total`. Route points are
not evenly spaced (this route: 54 gaps of 10.0 nm, 9 gaps of 1.3–9.8 nm where waypoints
were inserted), so the two never agree:

| advisory / model | string says | `affected_nm` says |
|---|---|---|
| turbulence / gfs | 164 nm | 173.3 |
| vmc_cruise / gfs | 73 nm | 80.0 |
| enroute_precip / ecmwf | 55 nm | 45.5 |
| cloud_top / gfs | 91 nm | 99.9 |
| icing_escape / gfs | 86 nm | 89.8 |

`affected_pct` is likewise a **point** ratio, never a distance ratio — so
`affected_nm / total_nm ≠ affected_pct / 100` (turbulence/gfs: 29.8 % vs 28.1 %). And
`affected_mod_nm` has no geometry-accurate path at all: `build()` always computes it
proportionally.

**Three different answers to one question ship in the same object.**

### D3 — A restricted domain multiplied by the whole route length

`mountain_wind` correctly restricts its domain to mountain points via `in_domain`, then
passes that restricted `total` into `format_extent` **together with the full route
distance**. The percentage is right ("% of mountain points"); multiplying it by 582 nm is not:

| model | string | truth (`affected_nm`) | overstatement |
|---|---|---|---|
| ecmwf | "233nm/582nm (40 %)" | 60.0 nm | **3.9×** |
| gfs | "194nm/582nm (33 %)" | 50.0 nm | **3.9×** |
| icon | "543nm/582nm (93 %)" | 131.8 nm | **4.1×** |

ICON flags 14 of 15 mountain points — a genuine and notable signal — and the advisory
renders it as *543 nm of a 582 nm route*. The real footprint is 131.8 nm, and it is not
even contiguous.

### D4 — No minimum-extent floor

Only two floors exist in the whole system, both added reactively:

- `convective_character` — `embed_min_nm = 50`, **contiguous**, added by #568 for
  exactly this reason. The in-code rationale is the general statement of the problem:
  *"The old fraction had `len(conv)` as its denominator and no population floor, so a
  single flagged point under a deck scored 100 % and called a 582 nm route
  EMBEDDED/RED off 9 nm of it."*
- `vfr_feasibility` — `_TERMINAL_DECK_MIN_POINTS=2 or _TERMINAL_DECK_MIN_RUN_NM=15`,
  module constants, and it gates a **mitigation tip**, not the grade.

Everywhere else, one point can carry a grade. `interpolate_route` uses a **fixed
`spacing_nm = 10.0`** regardless of route length, so the point count scales with
distance and the *weight of one point* scales inversely. On this 582 nm route one point
is 1.6 % — harmless against a 20 % gate. On a 120 nm route (~13 points) **one point is
7.7 % and two points are 15 %**, which clears `imc_pct_amber=15` and
`no_escape_pct_red=15` outright (and `ifr_feasibility`, which this user has tuned down
to 15/30 from its 20/50 default). The percentage gate is silently ~5× more sensitive
on a short flight than a long one — and short flights are exactly where a 20 nm band of
weather is most likely to be avoidable.

### D5 — Twenty-four parameter names for one concept

Twenty-four distinct catalog keys across 13 advisories express one idea — "how much of
the route counts as a lot":

`pct_amber` · `affected_pct_amber`/`_red` · `isolated_max_pct`/`scattered_max_pct` ·
`amber_pct`/`red_pct` · `rain_pct_amber` · `snow_pct_amber` · `snow_moderate_pct_red` ·
`clear_cruise_amber_pct`/`clear_cruise_red_pct` · `primed_pct_amber` ·
`icing_coverage_pct_amber` · `no_escape_pct_red` · `icing_pct_amber`/`_red` ·
`poor_pct_amber`/`_red` · `route_pct_amber` · `imc_pct_amber`/`_red` ·
`bkn_pct_amber` · `ovc_pct_red`. Plus `min_route_pct`, an undocumented code-level
fallback in `icing_escape` that is not a catalog key at all.

Note both the generic (`amber_pct`, `pct_amber` — the same two words in either order,
in different advisories) and the inverted: `fiki_icing`'s pair is a percentage of the
*good* thing, so its comparison runs the other way.

### Four geometry conventions in the codebase

| # | Convention | Where | Used for |
|---|---|---|---|
| 1 | `total_nm × affected/total` (proportional) | `format_extent` | **every message** |
| 2 | Σ midpoint-owned cells of affected points | `summarize_evidence.affected_nm` | the JSON `affected_nm` |
| 3 | Midpoint cells over the longest **contiguous** run | `longest_embedded_run_nm` | character EMBEDDED only |
| 4 | `max(d) − min(d)` — raw span **including gaps** | `_terminal_deck_extent` | VFR mitigation gate only |

Plus `affected_pct` as a point ratio, which is a fifth answer.

### Blast radius

`digest/prompt_builder.py:977` feeds `affected_pct` to the LLM as
`"{model} sees {STATUS} ({pct}% affected)"` — beside the detail string, which carries a
different number. Verbatim from this pack's `digest_context.txt`:

```
[AMBER] Mountain Wind: Mountain wave risk (24kt near terrain) over 233nm/582nm (40%) …
  (outlier: icon sees RED (93% affected))
```

The digest then wrote *"the ICON outlier grades this RED at 93 % coverage"* and made it
watch-item #3. It also narrated the day-to-day swing — *"Mountain Wind has collapsed
from 93 % to 13 % route coverage — the most dramatic single change"* — where both
endpoints are fractions of *mountain points* and the denominator itself moves between
runs.

`connectors/views.py:152,378-379` exposes `affected_pct` and `affected_nm` on the
MCP and ChatGPT surfaces, so agents get convention #2 while the text they quote uses
convention #1.

---

## Part 3 — Proposed consolidation

### The primitive

One value object, computed once per (advisory × model × severity tier), replacing the
counts that are threaded through evaluators today:

```python
class RouteExtent(NamedTuple):
    points: int             # affected point count
    domain_points: int      # denominator in points
    nm: float               # Σ midpoint-owned cells of affected points
    domain_nm: float        # the DENOMINATOR's own nm — route nm, or mountain nm
    longest_run_nm: float   # longest contiguous affected run
    minutes: float | None   # nm / groundspeed, when a cruise speed is known

    @property
    def pct(self) -> float:         # distance-based, not point-based
        return 100.0 * self.nm / self.domain_nm if self.domain_nm else 0.0
```

and one gate:

```python
def grade_extent(
    ext: RouteExtent, *,
    amber_pct: float,
    red_pct: float | None = None,
    min_nm: float = EXTENT_MIN_NM,      # default 30 (≈3 points at 10nm spacing)
    min_run_nm: float | None = None,    # contiguity, for barrier-type hazards
    min_minutes: float | None = None,
) -> AdvisoryStatus
```

### Seven decisions this encodes

1. **The percentage denominator becomes distance, not point count.**
   `pct = 100 × nm / domain_nm`. Uneven spacing stops mattering, and `affected_pct` and
   `affected_nm` become consistent by construction rather than by discipline.

2. **`domain_nm` travels with the extent.** `mountain_wind`'s domain is *mountain
   miles*; its percentage is a percentage of mountain miles and its nm is real mountain
   miles. This kills D3 structurally — there is no route length lying around to
   multiply by. The message must then name the denominator:
   *"132 nm of 190 nm of high terrain (69 %)"*, never *"543nm/582nm"*.

3. **One extent per severity tier.** `extent_at(SEVERE)`, `extent_at(MODERATE)`. The
   phrase "SEVERE over X" always quotes the SEVERE extent. This is the turbulence fix
   generalized, and `convective`'s bespoke `affected_mod` / `ext_mod` pair — including
   the `affected_mod_nm` field that `build()` can only compute proportionally —
   collapses into it.

4. **A minimum-extent floor on every coverage-driven promotion.** `min_nm = 30` as the
   shared default (the "3 points" intuition, expressed in the unit that survives a
   change of route length or point spacing). Deliberate severe-hazard bypasses stay —
   turbulence's free-atmosphere severe rule is sound — but a bypassed grade must then
   **describe itself honestly** (*"severe CAT at one point, 9 nm"*) instead of borrowing
   a coverage number that describes a different population.

5. **Contiguity becomes a reducer on the same geometry, not a separate function.**
   `longest_run_nm` sits on the same object as `nm`. Barrier-type hazards (EMBEDDED,
   "can't get around it") gate on `min_run_nm`; everything else gates on the union
   `min_nm`. Conventions #3 and #4 both fold in; `_terminal_deck_extent`'s gap-including
   span is simply dropped as a bug.

6. **Time as a third axis, display-first.** `minutes = nm / groundspeed × 60`, reusing
   `_resolve_cruise_tas(ctx)` (already on `RouteContext` as `cruise_speed_ias_kt`, with
   the `flight_duration_hours` fallback) and the per-point wind components the headwind
   advisory already computes. **Recommendation: ship it as message text only**
   (*"about 8 min in it"*) and do **not** gate on it initially — a large share of
   flights fall back to a profile-default speed, so `min_minutes` would grade one
   aircraft differently from another for reasons the pilot did not set. Promote it to a
   gate only after measuring real `cruise_speed_ias_kt` coverage in prod.

7. **`format_extent` takes the `RouteExtent`.** Not counts. The string and the JSON
   field then come from the same number by construction, which is what makes D2
   unrepeatable rather than merely fixed.

### Parameter naming

Collapse to three keys — `extent_pct_amber`, `extent_pct_red`, `extent_min_nm` — with
per-advisory **labels** (not keys) carrying the domain word ("% of route in IMC",
"% of high terrain with wave risk"). `fiki_icing`'s inverted `clear_pct` flips to
`affected_pct` polarity so every gate reads the same direction.

Persisted user profiles key on the old names, so this needs a read-time alias shim.
Blast radius is limited by #402/#403 sparse defaults (only non-default values persist)
— **verify against prod before assuming it is small.**

### Migration slices

| # | Change | Grades move? |
|---|---|---|
| S1 | `format_extent` takes the summary / extent; pass `affected_nm` everywhere | No — display only |
| S2 | `mountain_wind` domain_nm (D3) | No — message only |
| S3 | Per-tier extents; turbulence + convective quote their own tier (D1) | No — message only |
| S4 | Distance-based percentage replaces point ratio | **Yes, slightly** |
| S5 | `min_nm` floor on coverage promotions (D4) | **Yes** |
| S6 | Parameter rename + alias shim (D5) | No |
| S7 | Time axis, display-only | No |

S1–S3 are pure honesty fixes with no grade movement and should go first — they remove
the numbers that are currently wrong without changing a single colour.

S4 and S5 move grades and **must** be measured with `scripts/rerun_advisories_diff.py`
(recomputes every advisory from a saved pack without re-fetching or overwriting, and
reports which ratings changed). Two caveats: it needs packs that still carry
`cross_section.json` (stripped from prod at T1 ~30 days, so pull fresh), and old-vs-new
must run against the *same* config — dev's thinner wind coverage otherwise overstates
missing-data effects. The LLM eval corpus needs a re-baseline after S4/S5 for the same
reason the wind-format standardisation did.

### Docs to update

- `designs/meteorology-decisions.md` — new § for the extent convention: distance-based
  percentage, domain-scoped denominator, per-tier extent, minimum-extent floor.
- `designs/advisories.md` — the evaluator-authoring contract.
