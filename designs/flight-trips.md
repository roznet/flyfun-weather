# Flight trips — a conjunctive feasibility chain

> Status: v1 as built (#602). The brainstorm that produced these decisions,
> including the rejected options, is `designs/future/flight-trips-brainstorm.md`.

## What this is, and what it must never become

A trip groups flights into a **conjunctive feasibility chain with a shrinking
scope**:

- A trip happens only if **all** its legs work (AND, not OR).
- The set of legs that must work **shrinks as the trip progresses**. Before
  departure, all of them; after leg 1 is flown, only legs 2..n.

**The return leg is the binding constraint, and per-flight briefings
structurally hide it.** You open Friday's briefing, it is green, you go. Sunday's
leg is a different page you did not open, and it is red. Closing that gap is the
entire product value of this feature.

The corollary is load-bearing and constrains almost every choice below: **the
trip view never prints a verdict for the trip.** It ranks and directs attention.
Its headline is *"which leg decides this trip, and when does that leg become
decidable"* — never a colour for the trip.

## Key exports

| Where | What |
|---|---|
| `weatherbrief/trips.py` | `summarize_trip`, `TripLegInput`, `TripSummary`, `TripLeg`, `SORTIE_GAP_HOURS` — the pure deterministic summary |
| `weatherbrief/storage/trips.py` | `create_trip`, `trip_members`, `set_leg_trip`, `delete_trip`, `prune_empty_trips`, `read_refresh_state`, `write_refresh_state` |
| `weatherbrief/api/trips.py` | the `/api/trips` router, `build_leg_inputs`, `build_trip_summary`, `bulk_trip_refs`, `trip_ref_for`, `ai_summary_key`, `derive_trip_name` |
| `weatherbrief/api/trip_refresh.py` | `start`, `kick`, `status`, `active_run_for_flight`, `record_leg_notice`, `open_scheduler_run`, `note_leg_done` |
| `weatherbrief/digest/trip_summary.py` | `ensure_trip_ai_summary`, `check_guardrail`, `build_context`, `legs_allow_ai` |
| `web/ts/helpers/trip-selection.ts` | `buildTripSelection` — the selection-bar rule, pure |
| `web/ts/managers/trip-ui.ts`, `web/ts/trip-main.ts` | the `/trip.html` page |

## Data model

`flight_trips` (migration 095) plus a nullable, indexed `flights.trip_id` with
`ON DELETE SET NULL`. Membership is a column, not a join table.

Two things are deliberately **not** stored:

- **Leg position.** Chain order derives from `departure_time`. `/move` changes
  departure times and recreates the row, so a stored position would be an
  invariant to repair on every move; deriving it means a move that reorders the
  chain needs no fixup at all.
- **The deterministic aggregate.** It is stale the moment any leg refreshes, so
  it is computed per read and never persisted. The *AI paragraph* is the
  exception and does live on the trip row — precisely because it costs money.

`SET NULL` rather than cascade: deleting a trip must never delete flights. The
flights own the packs, and the packs cost real money.

One trip per leg is enforced by the shape of the column. Allowing many would
make "which legs are still needed" ambiguous, and the pilot question has no
multi-trip reading.

## Two aggregations, never one

A trip spans D-0 to D-9. Folding a D-7 AMBER and a D-1 GREEN through `worst()`
gives AMBER, and that D-7 AMBER is mostly *uncertainty*, not weather. Do that
across three legs and every trip more than four days out is amber-or-red, the
signal dies, and pilots stop reading it. **This is the main way this feature
fails.**

So `summarize_trip` produces two things and the page headlines the second:

1. `chain_status` — worst traffic light across the **gradeable remaining** legs
   only. A beyond-horizon leg carries an `outlook`, not an assessment, and a
   pending-coverage leg carries neither; both are reported in their own fields
   and never folded in.
2. `decision_ripeness_days` / `decidable_from` — "the binding leg is 5 days out;
   nothing here is decidable until Thursday." Nearly free to compute from
   `days_out`, and the single most useful line at booking time.

### The binding-leg rule lives in exactly one place

`weatherbrief.trips._pick_binding_leg`. The design expects it to need iteration,
so nothing else re-derives it — web, iOS, MCP, the notification coalescer and
the AI guardrail all read the server's answer. Two tiers:

1. The worst **gradeable remaining** leg (a real GREEN/AMBER/RED), ties broken by
   the earliest departure. `UNAVAILABLE` is not a rung on the ladder and is
   skipped.
2. Only when *no* remaining leg is gradeable, the worst remaining **outlook**,
   reported with `binding_basis="outlook"` and **no** `chain_status`. This is
   what lets a trip booked entirely beyond the horizon still name the leg to
   watch instead of saying nothing — an outlook never competes with a traffic
   light, but it is better than silence.

"Remaining" is keyed on clock time; a debrief refines it (a cancelled leg is not
remaining even if it is still in the future).

## Where the numbers come from

Everything the summary needs is already denormalized on `briefing_packs`:
`assessment`, `assessment_reason`, `outlook`, `days_out`, `advisory_summary`. So
a trip summary is **one DB query over the latest pack per member leg** — no
pack-file reads, no pipeline run. `build_leg_inputs` deliberately reuses
`api/flights.py::_get_latest_packs`, the same helper the flights list uses, so
the trip card and the flight card can never disagree.

## Refresh: serial, server-driven, capped

A naive N-leg fan-out **fails outright**, not merely impolitely:

| Constant | Value | Effect on a 3-leg fan-out |
|---|---|---|
| `_RefreshRegistry.MAX_PER_USER` | 2 | leg 3 refused with `UserQueueLimitError` |
| `_RefreshRegistry.MAX_QUEUE_DEPTH` | 5 | global cap across all users |
| `_refresh_executor` | `max_workers=2` | **two refresh slots for the whole process** |

So `api/trip_refresh.py` admits exactly **one leg at a time** and submits the
next only when the previous finishes. That never approaches `MAX_PER_USER`,
always leaves one of the two executor slots free (a trip refresh costs its owner
latency, never anyone else's turn), makes "leg 2 of 3" the natural progress
readout, and keeps every leg an ordinary durable job that the existing
`decide_resume` path recovers after a container restart.

One executor task **per leg**, not per chain, so the slot is released between
legs and other users interleave.

**`triggered_by="trip"` is not in `UNCAPPED_TRIGGERS`, and must not be added.**
That is the tempting one-liner and precisely the change that would let one
user's trip monopolise both slots. The caps are the protection, not the
obstacle.

It is server-side because a closed tab would otherwise strand a trip
half-refreshed. The state — run id, ordered leg list, pending, per-leg outcomes,
per-leg notification notices — is one small JSON document on the trip row, live
only while a run is in flight, with a 3-hour staleness bound so a process killed
mid-chain cannot disable the button forever.

Partial failure completes the rest and reports per-leg. The gate skips legs with
no new model run, and the UI says so ("2 of 3 legs had new data") — without that
line, a trip refresh that legitimately did almost nothing looks like one that
failed.

### Advancement vs coalescing — one seam each

Notification coalescing hangs off `api/packs.py::_notify_refresh_complete`, the
single post-commit notify gate, exactly as designed. *Advancement* runs from the
driver's own per-leg completion handler instead: the notify gate is only reached
by a leg that succeeded, and a chain that stopped dead because leg 2 raised
would be worse than one that reports leg 2 as failed and carries on.

## Auto-refresh needs no new mechanism

Per-leg `auto_refresh_hour` defaults to *that leg's* departure − 1 h, which for
a trip is exactly wrong: Sunday's return would refresh Sunday morning, long after
the decision was made on Friday. `scheduler.py::_with_trip_mates` extends
`_find_due_flights` with a due leg's still-future trip-mates (when the trip has
`auto_refresh` on), and because `process_auto_refreshes` is already a strictly
sequential `for` loop, that is already correctly paced.

That path registers with `triggered_by="scheduler"`, which *is* uncapped — safe
only because the loop serialises it. The manual trip refresh must not reuse that
trigger to get past the caps.

## Notifications

One coalesced push + email per trip refresh, carrying the deterministic
binding-leg sentence plus a per-leg line-up. The per-leg WHEN decision still runs
(and still advances the badge, which counts unopened *flights*) via
`notify_briefing_refresh(..., deliver=False)`; only delivery is deferred. The
trip layer coalesces delivery — it never manufactures a notification the per-leg
gate would have suppressed.

Override precedence, applied in `_notify_refresh_complete`: an explicit
per-flight `notify_override` wins, else the trip's, else the account scope.

## AI trip summary — Haiku, and why that is structural

`claude-haiku-4-5-20251001`, matching the existing `longrange` / `translator`
pins in `digest/llm_config.py`. Haiku is right here not merely because it is
cheap: the task is **rewriting, not analysis**. It runs over already-analysed
conclusions — per-leg assessment/outlook, advisory chips, `days_out`, and the
deterministic binding leg — and never sees a sounding or re-derives meteorology.
There is nothing left for a bigger model to do, and a model that only ever sees
three colours and nine advisory names cannot invent a verdict from weather it
never read.

Three properties are load-bearing:

- **Gating inherits.** If *any* member leg has `llm_digest_enabled` off, the trip
  gets no AI paragraph — deterministic only. A pilot who turned AI off for a leg
  should not find that leg described by an LLM because it was grouped with
  others; all-on is the only unambiguous consent.
- **The guardrail is exact**, because the deterministic layer already knows the
  answer: the named worst leg must match the computed binding leg, and go/no-go
  vocabulary is rejected. On a mismatch we fall back to the deterministic
  sentence, so the LLM can only ever make it *nicer to read*, never different.
- **Keyed and persisted** on the member `(flight_id, fetch_timestamp)` tuples, so
  unchanged inputs never pay twice. Generated once per completed trip refresh and
  on demand when the page opens stale. Goes through `compute_cost` and the
  ledger (`action="trip_summary"`) — an invisible cost line is how a small cost
  becomes an unexplained one.

No prompt-cache breakpoint: the minimum cacheable prefix is 512–4096 tokens
depending on model and this prompt head may sit below it, so a breakpoint could
do nothing while billing 2× for the write. Measure with `count_tokens` before
adding one.

## UI

### Flights list

A trip renders as **one collapsible card**, placed in the section of its
earliest un-flown leg — one rule instead of a rendering special case for the
straddle. Grouping happens *before* sectioning; ungrouped flights keep exactly
today's path, so the trip case is additive rather than a rewrite.

The card holds only its **future + recent** members. The past section is
server-paginated and server-filtered, so a past leg pulled into the card could
vanish or duplicate depending on which page is loaded; past legs keep rendering
individually with their trip badge instead. The "n of m legs ahead" count comes
from the server's leg total, so it stays honest either way, and `/trip.html`
shows the whole chain including flown legs.

Expansion state is a `Set<string>` in `localStorage`, mirroring the existing
`pastExpanded` / `recentExpanded` pattern. **Collapsed by default**: the
collapsed card already carries the binding-leg chip, so auto-expanding a red trip
adds noise rather than information and makes the list jump around as forecasts
change.

Creation reuses the existing multi-select `#selection-bar` — zero new
interaction vocabulary. The rule (`web/ts/helpers/trip-selection.ts`) keys off
how many *distinct* trips the selection touches: none → **Group as trip**,
exactly one → **Add to "…"**, more than one → neither, because merging two trips
is a different decision the bar has no way to ask about. **Remove from trip** is
an explicit unlink, never a delete, and the confirm says so.

### Move vs Duplicate — same control, opposite default

| Action | Endpoint | Trip membership |
|---|---|---|
| **Move** | `POST /api/flights/{id}/move` | **inherits** (`keep_in_trip`, default `true`) |
| **Duplicate** | `POST /api/flights` | **does not inherit** (`trip_id`, opt-in) |

The reasoning is identity. A move *is the same leg, rescheduled* — the Sunday
return slipping to Monday is still this trip's return. A duplicate is a *new*
thing, and the common use ("same route, different weekend") belongs to a
different trip or to none. Both get the same explicit checkbox in the edit panel,
rendered only when the source is in a trip.

`/move` destroys and recreates the row, so `trip_id` is carried explicitly in the
field-merge block — it will not survive on its own. The moved leg loses its
packs (correct: a new date needs a new forecast), and the summary shows it as
*needs a briefing*, never UNAVAILABLE.

`bulk-delete`, single delete and unlink all call `prune_empty_trips`. A **1-leg
trip is valid** and is kept — adding legs later is a normal flow, and it is what
makes "book the outbound now, add the return when it comes into range" work
against the booking cap. Only the genuinely empty container goes.

### `/trip.html`

Chain strip (one tile per leg, flown greyed, ground gaps labelled) → the
deterministic binding-constraint callout → the AI paragraph, visually secondary
→ soft continuity warnings → per-leg rows → trip controls.

Every leg row carries `days_out` beside its badge (a D-7 amber and a D-1 amber
are not the same claim), the advisory chips, freshness, and two explicit links
out — **Briefing** and **Edit**. There is deliberately **no per-leg refresh
button**.

## iOS

Phase 1 is the `trip` field on `FlightResponse` and a "leg 2 of 3" badge on the
flight card (`TripBadge`). It cannot say which leg binds — that computation is
server-side and surfaced on the web trip page — but it says this flight is part
of a chain, which the per-flight card otherwise hides completely. Full trip UI
is v2.

## Deferred (v2)

Commit-point table (the per-decision-point view: what you are deciding, legs
still needed, binding leg, and where you are stranded if it fails); MCP
`get_trip`; joint time optimisation across legs using the per-leg `flexibility`
scans; trip sharing via the reserved `share_code`; full iOS trip UI. Nested
trips: no.

## Still open

- Sortie-gap threshold: fixed ~4 h (`SORTIE_GAP_HOURS`), or derived from
  turnaround/night?
- Should a cancelled leg collapse the rest of the chain, or leave the remainder
  standing as its own sub-trip? (Currently: it drops out of "remaining" and the
  rest stands.)
- Does a trip need its own retention exemption? A half-debriefed trip is a
  calibration case worth keeping whole.
- Booking-cap interaction: a trip whose return is beyond the cap cannot be
  created complete. The 1-leg-trip decision softens this but does not label it.
- Can a leg belong to another user's trip? The one-trip-per-leg rule assumes not;
  confirm before any `share_code` work.
