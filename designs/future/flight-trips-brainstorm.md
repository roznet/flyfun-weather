# Flight trips (packages) — brainstorm

> Status: design agreed, nothing built. Grouping flights into a trip whose
> viability is the conjunction of its remaining legs.

## Decisions taken

| Area | Decision |
|---|---|
| Selection bar | Context-sensitive: **Group as trip** when nothing selected is in a trip, **Add to "…"** when exactly one trip is represented |
| Removing a leg | Explicit, and explicitly *unlink* — never a delete |
| Minimum size | A **1-leg trip is valid**; adding legs later is a supported flow, not a repair |
| Past section | Trips group in **future/recent only**; past legs render individually with a trip badge (the past section is server-paginated — see below) |
| Flown legs | Shown, greyed — the trip is also a record |
| Commit-point table | **v2.** v1 ships chain strip + binding callout + leg rows |
| Binding leg | Worst among *gradeable* legs; beyond-horizon legs reported separately, never competing. Expected to need iteration — keep the rule in one tweakable place |
| "Remaining" | Clock time primary; debrief refines the label |
| AI summary gating | **Inherits `llm_digest_enabled`.** Any leg with AI off ⇒ no AI summary for the trip, deterministic only |
| AI summary coverage | Generates with incomplete legs and says so |
| AI guidance presets | None — it is rewriting, not judging |
| Duplicate | Creates a plain new flight. No trip inheritance, no "start a new trip" suggestion |
| Continuity | Soft warning when leg *k*'s destination ≠ leg *k+1*'s origin; never a block |
| Refresh control | **One "Refresh trip" button.** Per-leg refresh is greyed out for member legs |
| Refresh pacing | **One leg in flight at a time, server-driven** — see the finding below |
| Auto-refresh | Trip-level takes over; per-leg auto-refresh greyed as "managed by trip" |
| Donate nudge | Legs count normally. The button simply appearing is acceptable |
| Notifications | One per trip, carrying a per-leg summary |
| Trip id | Short token (like `share_code`), so sharing needs no later migration |
| AI summary storage | On the trip row |
| iOS | Simple trip badge now; full UI later |
| MCP | v2 |

## The premise worth naming first

The obvious framing is "let me group flights and show a combined summary". That
framing is too weak, and if we build it we build the wrong thing.

What is actually being described is a **conjunctive feasibility chain with a
shrinking scope**:

- A trip happens only if **all** its legs work (AND, not OR).
- The set of legs that must work **shrinks as the trip progresses**. Before
  departure, all of them. After leg 1 is flown, only legs 2..n.
- Therefore the question the pilot is asking changes at each commit point, and
  the *consequence of being wrong* changes with it. Getting Friday wrong means a
  cancelled weekend. Getting Sunday wrong means being stranded in Sion with the
  aircraft, a Monday meeting, and no plan.

**The return leg is the binding constraint, and per-flight briefings structurally
hide it.** You open Friday's briefing, it is green, you go. Sunday's leg is a
different page you did not open, and it is red. That gap is the entire product
value of this feature, and everything below follows from it.

Corollary (and it matters, see `feedback_not_go_nogo`): the trip view must never
print a trip-level verdict. It ranks and directs attention. Its headline is
**"which leg decides this trip, and when does that leg become decidable"** — not
a colour for the trip.

## Structure: derive, don't model

Both worked examples:

- Fri `EGTF→LSGS`; Sun `LSGS→LFAT→EGTF`. Out = 1 leg, back = 2.
- `LFQA→LFAT→EGTF` — 2 legs flown back-to-back with a stop.

Resist modelling "outbound" and "return" as first-class. A trip is an **ordered
chain of legs**, ordered by `departure_time`. Everything else derives:

| Derived | Rule |
|---|---|
| `is_round_trip` | last destination ICAO == first origin ICAO |
| *sortie* boundary | consecutive legs with a ground gap below a threshold (~4 h) are one sortie — a fuel/customs stop, not a decision point |
| *night* boundary | a gap spanning local night → separate sortie day, a real decision point |
| "way out" / "way back" | the sortie(s) before vs after the longest gap, on a round trip |

The sortie/night distinction is not cosmetic — it is exactly what tells you where
the **commit points** are, and commit points are the output.

Do **not** store `trip_position`. Sort by `departure_time`. One less invariant to
maintain, and `/move` changes departure times.

## The output: a commit-point table (v2)

This is the artifact the per-flight page can never produce. Deferred to v2 - v1
ships the chain strip, the binding-leg callout and the per-leg rows, which carry
most of the value at a fraction of the novel UI. Recorded here in full because
it is what the data model is shaped to support.

| Commit point | You are deciding | Legs still needed | Binding leg | If it fails you sit at |
|---|---|---|---|---|
| Fri 09:00 EGTF | Go to Sion at all? | 3 | Sun `LFAT→EGTF` — RED, convective | LSGS |
| Sun 10:00 LSGS | Head home? | 2 | Sun `LFAT→EGTF` — RED | LFAT |
| Sun 13:00 LFAT | Last hop? | 1 | Sun `LFAT→EGTF` — RED | LFAT |

Reading down the "if it fails" column is the risk picture. It answers "before the
package all need to be feasible, after the first leg only the last 2" literally
and visually.

Rows for commit points already passed (legs flown/cancelled — we have
`flight_debriefs`) grey out. The table shrinks as the trip is flown, which is the
shrinking-scope semantics made visible with no extra machinery.

## Two aggregations, never one

A trip spans D-0 to D-9. Folding a D-7 AMBER and a D-1 GREEN through `worst()`
produces AMBER, and the D-7 AMBER is mostly *uncertainty*, not weather. Do that
across three legs and every trip more than four days out is amber-or-red, the
signal dies, and pilots stop reading it. This is the main way this feature fails.

So compute two things, and headline the second:

1. **Chain status** — worst status across *remaining* legs, but horizon-aware.
   Within the GRIB horizon use `BriefingPackMeta.assessment`; beyond it the pack
   carries `outlook` (TRENDING_SETTLED / MIXED_SIGNALS / TRENDING_UNSETTLED)
   instead, and those two are **mutually exclusive by design** — never fold an
   outlook into a traffic light. A leg beyond the booking horizon is
   `pending_coverage`, a third state again.
2. **Decision ripeness** — "the binding leg is 5 days out; nothing here is
   decidable until Thursday." At booking time this is the single most useful line
   the page can print, and it is nearly free to compute from `days_out`.

Headline shape: *"Sunday's `LFAT→EGTF` decides this trip. It's 5 days out —
outlook is mixed. Worth re-checking Thursday."*

## Where the numbers come from

Everything the trip summary needs is **already denormalized on `briefing_packs`**:
`assessment`, `assessment_reason`, `outlook`, `days_out`, and `advisory_summary`
(#276 — red/amber counts plus a severity-ordered top-3 of `status`+`name` chips).
The flights-list card already renders from exactly these without touching
`route_advisories.json`.

So a trip summary is **one DB query over the latest pack per member leg** — no
pack-file reads, no pipeline run. That is what makes it cheap enough to render on
every card in the flights list.

**Never persist the aggregate.** It is stale the moment any leg refreshes.
Compute at read time.

Put the computation in a pure function — `src/weatherbrief/trips.py`,
`(list[FlightRow], latest BriefingPackRow per flight) -> TripSummary` — with no
DB or FastAPI in it. Web, iOS, MCP, the notification coalescer and the email
digest then all share one definition of "the binding leg", the same way
`analysis/airport_consensus.py` is shared between the forecast map and alternates
for exactly that consistency guarantee.

## Data model

New table `flight_trips`:

```
id           str PK        short token, like flight ids (shareable later)
user_id      FK users CASCADE
name         str           defaults to a derived label
notes        text | null
auto_refresh          bool
auto_refresh_hour     int | null
notify_override       str          default | notify | mute
share_code   str | null unique     mirrors FlightRow.share_code
created_at   TZDateTime
```

Membership: a single nullable **`trip_id` column on `flights`**, `ON DELETE SET
NULL`, indexed — not a join table.

- A leg belongs to at most one trip. Allowing many makes "remaining legs"
  ambiguous, and the pilot question has no multi-trip reading. Enforce one.
- No join on the hot flights-list query.
- `SET NULL` so deleting a trip never deletes flights — the flights own the packs,
  and the packs cost real money.

Migration: one `create_table` plus one `batch_alter_table` add-column on
`flights`. Follow `designs/migrations.md` (batch mode mandatory, named
constraints). `TZDateTime` for `created_at` per `time-alignment-audit.md`.

### Move vs Duplicate — different defaults, both explicit

Both actions live on the same edit panel in `flight-main.ts`, which swaps Save
for Move + Duplicate as soon as a structural field changes
(`web/tests/move-duplicate.spec.ts`). They must treat trip membership
differently:

| Action | Endpoint | Trip membership |
|---|---|---|
| **Move** | `POST /api/flights/{id}/move` | **Inherits** by default |
| **Duplicate** | `POST /api/flights` (original untouched) | **Does not inherit** by default |

The reasoning is identity. A move *is the same leg, rescheduled* — the Sunday
return slipping to Monday is still this trip's return, and dropping it out of the
trip would be a silent data loss the pilot never asked for. A duplicate is a
*new* thing; the overwhelmingly common use is "same route, different weekend",
which belongs to a different trip or to none. Inheriting there would quietly
grow the trip with a leg that is not part of it.

Both get an **explicit checkbox** in the edit panel, rendered only when the
source flight has a `trip_id`, pre-ticked for Move and un-ticked for Duplicate:
*"Keep in trip 'EGTF → LSGS → EGTF'"*. Same control, opposite default — the
default encodes the common case, the checkbox makes the uncommon one one click
away, and neither is a surprise.

Implementation notes:

- **`/move` destroys and recreates the row.** It computes a new id, creates the
  new flight, deletes the old one and cascades its packs, all in one
  transaction. `trip_id` must be carried explicitly in the field-merge block —
  it will not survive on its own. The moved leg loses its packs (correct: a new
  date needs a new forecast), so the trip summary must show it as *"needs a
  briefing"*, never UNAVAILABLE.
- **Position is re-derived, not migrated.** Because order comes from
  `departure_time`, a move that reorders the chain needs no fixup at all. This is
  the payoff for not storing `trip_position`.
- A move *can* break geographic continuity (leg *k*'s destination ≠ leg *k+1*'s
  origin). Don't block it — pilots reposition. Surface it as a soft warning on
  the trip page, since a broken chain is usually a mistake worth seeing.

### Other gotchas found in the code

- **`/api/flights/bulk-delete`** must leave no empty trips behind. Either delete
  a trip when its last member goes, or (better) allow a 1-leg trip and let the
  user delete it — deleting user-named containers as a side effect of a flight
  delete is surprising.
- **`_compute_flight_id`** encodes route+date+params. Two legs of a trip can
  never collide, so no new id pressure.
- `flight_params_hash` (#552) on packs already separates "no new model run" from
  "the flight changed" — a trip-wide refresh gets that for free per leg.

## AI trip summary — Haiku over already-summarized inputs

A short LLM paragraph over the chain: which legs are fine, which are not, and
what the shape of the problem is. Explicitly **not** a go/no-go call.

### Why Haiku is the right call, structurally

The critical property is that this is a **rewriting task, not an analysis task**.
It runs over data that has *already* been analysed: per-leg `assessment` /
`outlook`, `advisory_summary` chips, `days_out`, and the deterministic
binding-leg computation. It never sees a sounding, never sees `route_analyses`,
never re-derives meteorology. Sonnet does the heavy lifting once per flight in
the briefer; this is a second-order pass over its conclusions.

That is exactly what makes Haiku appropriate rather than merely cheap — the task
has no meteorological reasoning left in it, so there is nothing for a bigger
model to do. It also structurally bounds the failure mode: a model that only ever
sees three colours and nine advisory names cannot invent a verdict from weather
it never read.

### It drops straight into the existing config

`DigestConfig` (`digest/llm_config.py`) already carries **per-role
`LLMConfig` blocks** — `llm` (briefer, `claude-sonnet-4-6`), `longrange`
(`claude-haiku-4-5-20251001`), `translator` (same Haiku pin) — and
`create_chat_model(llm_config)` is already the generic factory. So this is one
field:

```python
trip: LLMConfig = LLMConfig(
    provider="anthropic",
    model="claude-haiku-4-5-20251001",  # match the existing pins in this file
    temperature=0.0,
)
```

...plus a `prompts.trip` entry and `create_chat_model(config.trip)`. No new
plumbing, and it stays per-config-overridable like every other role.

### Cost

Input is a few hundred tokens of chain state per trip; output a short paragraph.
At Haiku 4.5 rates ($1 / $5 per MTok) that is a fraction of a cent per trip —
call it three orders of magnitude below a briefing. It still goes through
`compute_cost` / the cost ledger, because an invisible cost line is how a small
cost becomes an unexplained one.

Don't reach for a prompt-cache breakpoint reflexively: the minimum cacheable
prefix is 512–4096 tokens depending on model, and a trip prompt head may sit
below it, in which case the breakpoint does nothing. Measure with
`count_tokens` before adding one — the digest's `cache_locales` comment in
`llm_config.py` is the precedent for making that call from data rather than
guessing.

### When it runs — not per leg

Regenerating on every leg refresh means N generations per trip refresh, for one
output. Instead:

- generate **once when a trip refresh completes** — the same coalescing seam as
  the notification (`trip_refresh_id`);
- generate **on demand** when the trip page opens and the stored summary is stale.

Key it on the tuple of member `(flight_id, fetch_timestamp)`. Store the text plus
that key and a timestamp on the trip row (`ai_summary_text`, `ai_summary_key`,
`ai_summary_at`). Unlike the deterministic aggregate — which must never be
persisted — this one **is** worth persisting, precisely because it costs money;
unchanged inputs must never pay twice.

### Gating: inherits, never its own switch

The trip summary follows the legs' `llm_digest_enabled`. **If any member leg has
AI off, the trip gets no AI summary** - only the deterministic callout.

This is the conservative reading and the right one: a pilot who turned AI off for
a leg should not find that leg described by an LLM because it was grouped with
others. All-on is the only unambiguous consent, and it avoids inventing a
trip-level preference that could disagree with the legs underneath it.

### Guardrail

The deterministic layer already knows the binding leg and every leg's status. So
the guardrail is cheap and exact: **assert the model's named worst leg matches
the computed one**, and reject go/no-go vocabulary. On a mismatch, fall back to
the deterministic sentence rather than showing the generated text. Sibling of the
existing `run_guardrails` in the digest, and it means the LLM can only ever make
the deterministic answer *nicer to read*, never different.

Prompt instruction, roughly: *describe which legs look fine and which don't, and
what kind of problem the difficult one is. Do not recommend, advise, or conclude
whether to fly.*

## UI/UX

### Creating a trip

Reuse what exists. The flights list already has multi-select with a floating
`#selection-bar` carrying Select-all / Select-past / Clear / Delete
(`web/ts/managers/flights-ui.ts:renderSelectionBar`, covered by
`web/tests/multi-delete.spec.ts`). Add one button: **Group as trip**. Zero new
interaction vocabulary.

Default name derived from the chain and dates: *"EGTF → LSGS → EGTF, 20–22 Feb"*.

Second entry point, later: "add a return leg" from the create form. Deferred —
that form is already dense, and the model should prove itself on existing flights
first. Third, much later: paste a multi-leg FPL and split it into legs
(`/parse-fpl` and `/interpret-route` already exist).

### On the flights list

The awkward case is a trip that straddles sections: leg 1 flown (`past`/`recent`),
legs 2–3 ahead (`future`). The bucketing in `renderFlightList` is per-flight.

Recommendation: **a trip renders as one card, placed in the section of its
earliest un-flown leg, expandable to per-leg rows.** Flown legs appear inside the
expansion, struck through, carrying their debrief state. This:

- keeps the trip as the unit of attention, which is the point;
- resolves the straddle with one rule instead of a rendering special case;
- makes the shrinking scope literally visible — the greyed rows are the legs that
  no longer matter.

The collapsed card carries: chain string, date span, the binding-leg chip, and
"2 of 3 legs ahead". Expanded, it lists the member legs as ordinary flight rows.

The alternative (keep per-flight cards, draw a connecting spine) preserves the
current bucketing but scatters a trip across three sections, which defeats the
feature. Not recommended.

#### Collapse / expand

`flights-ui.ts` already has the pattern, twice: the past- and recent-flights
sections keep module-level `pastExpanded` / `recentExpanded` booleans, toggle a
`.collapsed` class on the section wrapper, and re-apply after each render.
Trip cards are the same mechanism with per-trip state — a `Set<string>` of
expanded trip ids rather than a single boolean, persisted to `localStorage` so a
refresh doesn't re-collapse what the pilot opened.

Default **collapsed**. Resist the temptation to auto-expand trips with a red leg:
the collapsed card already shows the binding-leg chip, so auto-expanding adds
noise rather than information, and it makes the list jump around as forecasts
change. Remember the pilot's choice instead.

`renderFlightList`'s bucketing needs one change: group members by `trip_id`
*before* sectioning, then place each group by its earliest un-flown leg. Ungrouped
flights keep exactly today's path — the trip case is additive, not a rewrite.

### The trip page (`/trip.html?id=…`)

Sits alongside `/flight.html?id=` (flight detail) and `/briefing.html?flight=`
(the briefing). Progressive depth (`feedback_progressive_depth`) — simple at the
top, drillable:

1. **Chain strip** — one tile per leg, left to right, coloured by that leg's
   assessment, flown legs greyed, gaps labelled ("2 nights at Sion"). The whole
   trip at a glance.
2. **Binding-constraint callout** — one deterministic sentence.
3. **AI trip summary** — the Haiku paragraph, visually secondary to (2) so the
   deterministic line stays the thing the eye lands on.
4. **Commit-point table** — the section above.
5. **Per-leg detail rows** — the substance of the page. Each row carries:
   - route + date/time (local and UTC), duration, cruise altitude;
   - assessment or outlook badge, with `days_out` beside it so the confidence is
     legible without a click;
   - advisory chips from `advisory_summary` — reuse the card renderer, no new
     rendering code;
   - freshness (model init times). **No per-leg refresh button** - refreshing
     is a trip-level action, and the gate already skips legs with no new data,
     so one button is genuinely sufficient;
   - **two links out**: *Briefing* → `/briefing.html?flight={id}`, *Edit* →
     `/flight.html?id={id}`. Getting from the trip to one leg's full detail is
     the main navigation the page exists to serve, so it is an explicit control
     on every row, not a click-the-card affordance.
   - a continuity warning where leg *k*'s destination ≠ leg *k+1*'s origin.
6. **Alternatives** — see below.
7. **Trip-level controls** — refresh all, auto-refresh toggle + hour, notify
   override, rename, remove a leg, delete trip.

### Alternatives / flexibility

Per-leg `flexibility` scans already exist (`time_options.json`,
`designs/timing-scenarios.md`) and produce "this leg is green if you leave 3 h
earlier". v1: **surface those per leg on the trip page** — free, already computed
for legs that opted in.

v2, and genuinely hard: a **joint** search for a departure combination that works
across the chain, subject to turnaround minima and daylight. Real value (it is
the actual question — "is there *any* way to make this weekend work?") but it is
a constrained search over the per-leg candidate sets, and the honesty invariant
from the timing-scenario work applies with force: never grade an hour whose
fields are not decoded for the model claimed. Out of v1.

## Refresh, scheduling, notifications

This is where cost bites and where the naive version misbehaves.

### Refresh all legs — a naive fan-out does not work

**The finding.** Enqueueing N legs at once is not merely impolite to other
users; it fails outright. The refresh admission path
(`api/packs.py::_RefreshRegistry.try_register`) **rejects** rather than queues:

| Constant | Value | Effect on a 3-leg fan-out |
|---|---|---|
| `MAX_PER_USER` | 2 | Leg 3 is refused with `UserQueueLimitError` |
| `MAX_QUEUE_DEPTH` | 5 | Global cap across all users |
| `_refresh_executor` | `ThreadPoolExecutor(max_workers=2)` | **Two refresh slots for the entire process** |

So a naive fan-out would error on the third leg *and*, for the two that were
admitted, occupy both server-wide slots — blocking every other user for the
duration. Two workers process-wide is the number that matters here: one user's
trip must never be able to claim both.

**The design: one leg in flight at a time, driven server-side.** Admit exactly
one leg into the registry; submit the next only when the previous completes.
This:

- never approaches `MAX_PER_USER`, so no leg is ever refused;
- always leaves one of the two executor slots free, so other users interleave —
  a trip refresh costs its owner latency, never anyone else's turn;
- makes "leg 2 of 3" the natural progress readout;
- keeps every leg an ordinary durable job, so a container restart resumes it
  through the existing `decide_resume` path with no trip-specific recovery.

It must be **server-side**, not client-driven: a closed tab would otherwise
strand a trip half-refreshed. The state is small — `trip_refresh_id` plus the
pending leg list on the trip row — and it advances from the *same*
`_notify_refresh_complete` seam that does notification coalescing. One seam,
both jobs.

**Do not add a `"trip"` trigger to `UNCAPPED_TRIGGERS`.** It is the tempting
one-line shortcut and it is precisely the change that would let one user's trip
monopolise both slots. The caps are the protection, not the obstacle.

Concurrency stays a named constant (1 for now). Raising it adaptively when the
queue is otherwise idle is a legitimate later refinement; starting there is not.

**Tradeoff, stated plainly:** a 3-leg trip refresh takes roughly 3× a single
briefing (~6 min). The refresh gate skips legs with no new model run, so it is
frequently fewer, and the UI should say so ("2 of 3 legs had new data"). Serial
is the correct trade against a two-slot server.

### Auto-refresh — the real improvement, and it needs no new mechanism

Today `auto_refresh_hour` defaults to *that leg's* departure - 1 h. For a trip
that is exactly wrong: Sunday's return would auto-refresh Sunday morning, long
after the decision was actually made on Friday. A trip should refresh **ahead of
its next remaining commit point**.

The good news is that this needs no new machinery.
`scheduler.py::process_auto_refreshes` is already a strictly sequential `for`
loop - `await asyncio.to_thread(_auto_refresh_one, ...)`, one flight at a time.
Extending `_find_due_flights` to pull in a due flight's trip-mates that are still
in the future gets trip auto-refresh **for free**, already correctly paced.

One caution: that path registers with `triggered_by="scheduler"`, which is in
`UNCAPPED_TRIGGERS`. That is safe *only* because the loop serialises it - the
manual trip refresh must not reuse that trigger to get past the caps.

### Notification coalescing

`notify/dispatch.py` is emitted from a **single gate** in
`api/packs.py::_notify_refresh_complete`, which is lucky — one seam. A 3-leg trip
refresh must not fire 3 pushes. Coalesce on `trip_refresh_id`: fire once when the
last leg lands, with a chain summary — *"Trip to Sion: 2 green, Sunday's return
now RED."*

And the actually-valuable notification is not "refresh done" but **"the leg that
decides your trip changed colour"**. `compute_refresh_delta` already computes
worsening for the banner, and the notification prefs already have a *change-only*
scope. A trip-level change-only push is the strongest version of this feature and
should be designed in from the start even if shipped second.

## API surface

Additive, so iOS and MCP can adopt without a redesign:

- `GET/POST /api/trips`, `GET/PATCH/DELETE /api/trips/{id}`
- `POST /api/trips/{id}/legs` / `DELETE /api/trips/{id}/legs/{flight_id}`
- `POST /api/trips/{id}/refresh` → fans out, returns `trip_refresh_id`
- `GET /api/trips/{id}/summary` → the pure `TripSummary`
- `FlightResponse` gains `trip: {id, name, position, total} | null`

That last field alone lets the iOS flight list group without a new screen. Run
the `sync-ios-web` skill when the web half lands.

## v1 scope

**One issue, v1:**

1. `flight_trips` table + `trip_id` on `flights`; move inherits / duplicate does not, with the explicit checkbox.
2. Context-sensitive Group / Add-to on the selection bar; explicit unlink; 1-leg trips valid.
3. Pure `TripSummary` function + `/api/trips`; `trip` field on `FlightResponse`.
4. Collapsible trip card on the list (future/recent only; past legs keep a badge).
5. `/trip.html` — chain strip, binding callout, greyed flown legs, per-leg detail rows linking to Briefing / Edit, continuity warning.
6. One "Refresh trip" button with the serial server-side driver; per-leg refresh and per-leg auto-refresh greyed for members; trip auto-refresh via `_find_due_flights`.
7. One coalesced notification per trip carrying a per-leg summary.
8. Haiku trip summary, inheriting `llm_digest_enabled`, with the binding-leg guardrail.
9. Simple trip badge on iOS.

Items 1-5 are useful on their own if 6-9 slip; item 8 depends on the
deterministic binding leg from item 3 and should land last.

**Follow-up issue, v2:** commit-point table; MCP `get_trip`; joint time
optimisation across legs; trip sharing (`share_code` is reserved —
`FlightSubscriptionRow` gives the pattern); full iOS trip UI. Nested trips: no.

## Still open

- Sortie-gap threshold: fixed 4 h, or derived from turnaround/night?
- Should a cancelled leg (from the debrief) collapse the rest of the chain, or
  leave the remaining legs standing as their own sub-trip?
- Does a trip need its own retention exemption? Debriefed flights are already
  exempt from T2; a half-debriefed trip is a calibration case worth keeping whole.
- Booking-cap interaction: a trip whose return is beyond the cap cannot be
  created complete today. The 1-leg-trip decision softens this (create the
  outbound, add the return when it comes into range) but does not label it.
- Can a leg belong to another user's trip? The one-trip-per-leg rule assumes
  not; confirm before any `share_code` work.
