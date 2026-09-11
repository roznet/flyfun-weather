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
| `web/ts/helpers/assessment-badges.ts` | `assessmentClass`, `outlookClass` — shared by the flights list and the trip page so their badge colours cannot drift |
| `weatherbrief/storage/trips.py` | `create_trip`, `trip_members`, `set_leg_trip`, `delete_trip`, `prune_empty_trips`, `read_refresh_state`, `write_refresh_state` |
| `weatherbrief/api/trips.py` | the `/api/trips` router, `build_leg_inputs`, `build_trip_summary`, `bulk_trip_refs`, `trip_ref_for`, `ai_summary_key`, `derive_trip_name` |
| `weatherbrief/api/trip_refresh.py` | `start`, `kick`, `status`, `active_run_for_flight`, `record_leg_notice`, `open_scheduler_run`, `note_leg_done`, `run_trip_refresh_resume` |
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

There are four non-gradeable states, not three, and they are reported
separately: `beyond_horizon_leg_ids` (an outlook), `pending_coverage_leg_ids`
(no model reaches the date), `needs_briefing_leg_ids` (never briefed) and
`unavailable_leg_ids` (briefed, but the pack came back ungradeable). The last
two must not be conflated — telling a pilot a leg has no briefing when it has
one that failed to grade is simply false, and the two imply different actions.

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

Two things keep "one leg at a time" actually true, and both are load-bearing:

- **`start()` holds `_state_lock` across the busy check, the write and its
  commit.** A plain check-then-write is not enough: two concurrent presses (a
  double-click, a retry, web and iOS both firing) open independent sessions,
  both read `refresh_id is None`, both pass, and both submit. Committing inside
  the lock is what makes the second caller's read see the first caller's write.
  A single uvicorn worker makes a process-local lock sufficient — the same
  assumption `refresh-durability` already relies on.
- **One gate, `_run_scope(db, trip_id, run_id)`, and every mutator goes through
  it — including `record_leg_notice`, and committing inside it.** It takes `_state_lock`, loads the row, and yields `None` when the
  caller's run is not the live one. This shape is the fix for a *process*, not
  just a bug: fencing used to be something each function opted into, and three
  review rounds running found the function that had not — `open_scheduler_run`,
  then `_finish`. A new mutator that skips the gate is now visible in review
  rather than in production. The test that polices this **discovers** the
  mutators (any module function calling `write_refresh_state`) rather than
  naming them: the first version hard-coded three names and so could not catch
  the fourth, which is precisely the failure it existed to prevent. A test that
  enumerates the list it polices is not a structural guarantee.
- **`_record_result` returns `None` when fenced**, which the caller must not
  conflate with an empty state: an empty `pending` means the chain is done and
  should be closed out, while a fenced-out task must touch nothing.
- **`_finish` is fenced too, and this one is subtle.** `_record_result` commits
  `pending=[]` and releases the lock; at that instant `start()`'s busy check
  sees an idle trip and may open a *new* run. An unfenced `_finish` arriving
  moments later closes that one — dropping the first run's notification and
  killing the second before it claims a leg.
- **Nothing else may refresh a leg the driver owns**, and there are two paths,
  not one. `legs_claimed_by_a_live_run` filters the scheduler's due list;
  `leg_is_claimed` guards the manual per-flight `/packs/refresh` endpoints
  (queued and streaming), which a pilot reaches from an individual leg's
  briefing page. Fixing only the scheduler left the invariant reopened through
  the path a user is most likely to take.
- **The scheduler yields legs the driver already owns.** A leg sitting in a
  run's `pending` has been promised to the driver but is not yet in
  `refresh_registry` — nothing claims it until `_claim_next` picks it up — so the
  scheduler's admission check cannot see it, and running it there under the
  *uncapped* `"scheduler"` trigger would put a second leg of the same trip in
  flight. `legs_claimed_by_a_live_run` filters them out of `_find_due_flights`;
  they come back on a later cycle if still due.
- **`start()` re-reads with `with_for_update=True`.** Prod is MySQL at
  REPEATABLE READ, where a plain `refresh()` can be served from the enclosing
  transaction's snapshot and miss another transaction's just-committed write —
  silently reopening the race in production only. A locking read takes the
  latest committed row and serialises across processes, which the in-process
  lock alone cannot. SQLite ignores `FOR UPDATE`.

### Recovery after a crash

Each leg is an ordinary durable refresh job, so `tasks/refresh_resume.py`
already resumes the *one* leg that was in flight — but it knows nothing about
trips, so on its own the rest of the chain never runs and every gathered notice
is discarded once the staleness window lapses.
`trip_refresh.run_trip_refresh_resume` is the sibling boot pass, started from
the same lifespan block: any trip with a non-NULL `refresh_id` at boot is by
definition an orphan (single worker). It puts the interrupted `current` leg back
at the head of `pending` — re-queued rather than dropped, because the gate makes
a redundant re-run a cheap no-op while a dropped leg would silently never be
briefed — and either restarts the chain under its original run id or closes it
out so the coalesced notification finally fires.

One more path had to report in: when `try_register` refuses a due leg, the
scheduler loop `continue`s past its `finally`, so the leg must be reported to
`note_leg_done` explicitly. Without that it stays in `pending` forever and the
trip's single notification is lost for the legs that *did* complete.

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

### The trip switch has to count on its own

`_find_due_flights` originally selected on `FlightRow.auto_refresh` alone, and
`_with_trip_mates` returns early on an empty list — so the trip-mate step was
only ever reached through a leg that had its *own* flag on. A flight is created
with `auto_refresh` off (`db/models.py`), and neither `PATCH /trips/{id}` nor
`add_legs` cascades, so a trip with auto-refresh on and every leg untouched had
nothing to hand `_with_trip_mates` and simply never refreshed — while the
briefing page disabled the only control that could have switched a leg on. The
query now admits a leg on **either** flag (`or_`, outer-joined on
`FlightTripRow`).

### On/off is the trip's, the hour is the leg's

The two halves of the control are owned by different things, and the split is
the point:

* **Whether** — the trip's. It refreshes the whole chain or none of it, so
  `renderAutoRefreshBar` shows `flight.trip.auto_refresh` on a member leg,
  disabled, linking to the trip page. Disabled rather than hidden: hiding looks
  like the setting was lost. `TripLegRef.auto_refresh` exists to carry it.
* **When** — the leg's. Whichever leg comes due first pulls in the rest, so the
  *earliest* leg's hour is the trip's effective refresh time. The hour select
  therefore stays editable on a member leg; without it a Fri-out/Sun-back trip
  could only be re-timed by guessing which leg happened to come due first.

A member leg's own `auto_refresh` is carried through the PATCH untouched — it is
not what governs the leg while it is in a trip, but it is what the leg reverts
to on leaving one. `helpers/auto-refresh-control.ts` holds the decision (the
renderer only draws it) so it is unit-testable without a DOM.

`FlightTripRow.auto_refresh_hour` is still read by nothing. It predates this and
would mean "one hour for the whole chain", which the per-leg-min rule makes
redundant; it is left in place rather than migrated away.

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

  **The leg identity is a field, not prose.** `generate()` uses
  `with_structured_output(TripParagraph)`, so the model returns
  `worst_leg_id` alongside its paragraph and the guardrail is an *equality
  check on a flight id*. Getting here took three rounds of the wrong approach
  and the history is the argument: matching the named leg by regex meant
  widening a superlative list (which rejected correct paragraphs), then scoping
  the search to a clause (which let a comma-appositive through), then facing
  negation ("has no problem") and single-waypoint legs. Every patch traded a
  false negative for a false positive, because natural-language matching has no
  fixed point. Asking for the id deletes the parse and the entire class of
  finding with it — `_names_leg`, `_fragments_naming`, the superlative regex and
  the clause splitter are all gone.

  What stays regex-matched is what genuinely is a property of the prose: the
  go/no-go vocabulary and the length cap. That list must be a **superset of what
  the prompt forbids** — the guardrail exists precisely for the case where the
  model ignores the instruction, so a word banned in the prompt but absent here
  (as "avoid", bare "safe" and bare "go" once were) is a guardrail that does not
  guard.
- **Keyed and persisted** on the member `(flight_id, fetch_timestamp,
  debrief_decision)` tuples, so unchanged inputs never pay twice. The debrief is
  in the key because it feeds `_pick_binding_leg`: marking the binding leg
  cancelled changes which leg decides the trip without moving any
  `fetch_timestamp`, and a packs-only key would keep serving the stale paragraph
  as fresh. The key is stored on a *rejected* attempt too, and the cache check
  keys off the key alone rather than the text — otherwise an input set that
  reliably fails the guardrail is regenerated and re-charged on every page open.

  The key also includes each leg's derived **state**. A leg flips
  `remaining` → `flown` from the clock alone, with no debrief and no new pack,
  and only remaining legs can bind — so the binding leg changes identity as a
  departure passes, and a key without it would pair a fresh deterministic
  callout with a cached paragraph naming a leg that no longer matters.

  **The consent gate runs before the cache, and the order is load-bearing.**
  `llm_digest_enabled` is in neither the packs nor the debriefs, so it cannot be
  in the key: turning AI off on a leg without touching its pack leaves the key
  unchanged, and a gate placed after the cache check would never be reached —
  the stored paragraph would keep being served to a pilot who had switched AI
  off. That is a consent property, not a caching one — and it applies to
  *reads* as well: `_trip_to_response` re-checks `legs_allow_ai` before
  returning a stored paragraph, so the API contract holds for any consumer, not
  just for the page that happens to fetch it through the generating endpoint. Generated once per completed trip refresh and
  on demand when the page opens stale. Goes through the
  ledger (`action="trip_summary"`) — an invisible cost line is how a small cost
  becomes an unexplained one — priced by `costs.compute_call_cost` at the trip
  model's own token rate, with the model and token counts in the row's
  metadata. It was first routed through the per-briefing `compute_cost`, which
  adds a droplet/subscription share and margin to every call and so billed a
  sub-cent Haiku paragraph at ~$0.62, more than the briefing it summarises.

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
individually with a trip badge (`badge-trip`, linking to the trip) instead —
without it a flown outbound leg is indistinguishable from an ungrouped flight. The "n of m legs ahead" count comes
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

The checkbox's default is resolved **at click time**, not by nudging it on
hover: a touch tap and a keyboard Tab+Enter never fire `mouseenter`, so a
hover-set default left Duplicate silently inheriting the trip on exactly the
devices most likely to be used in a cockpit. Hover, focus and `pointerdown` all
*display* the pending default so the box never shows something other than what
the action will do.

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
out — **Briefing** and **Edit** — plus a **Refresh leg** button on remaining
legs (see below).

### Refreshing one leg

v1 made refresh trip-only — no per-leg button here, and the Refresh button on a
member leg's briefing page disabled. That was wrong for the commonest trip-day
pattern: the next leg wants several refreshes (D-0 METAR/TAF just before
departure) while Sunday's return wants none, and every trip refresh re-runs and
re-digests each leg that has new model data. So a leg is refreshable on its own
from both its row here and its briefing page.

It needs no new mechanism: a single-leg refresh is an ordinary per-flight
refresh (`triggered_by="user"`, capped as usual, its own per-flight
notification — nothing to coalesce). The only trip-specific rule is the existing
one — while a trip run owns the leg, `leg_is_claimed` refuses it with a 409, and
the row button is disabled for the duration of a run.

The trip summary follows without extra plumbing. The deterministic aggregate is
computed per read, and the AI paragraph is keyed on member `fetch_timestamp`s,
so it regenerates once, the next time the page asks. The page watches its legs'
refreshes (`startLegRefreshPolling`) and re-reads the trip when one *settles*
(`helpers/trip-leg-refresh.ts::settledLegIds`) — so a leg refreshed from
anywhere, not just this page, updates the callout without a reload.

A finished run's readout ("1 of 2 legs had new data; 1 already current") stays
on the trip row indefinitely — `_finish` keeps the results so the last poll can
render them. After a single-leg refresh that line described a run the legs had
moved past, calling a just-re-briefed leg "already current". So
`TripRefreshStatus.finished_at` reports when the run closed, and
`tripRunMessage` shows the line only while a run is live, or afterwards until
any leg has a pack newer than that.

## iOS

Phase 1 was the `trip` field on `FlightResponse` and a "leg 2 of 3" badge on the
flight card (`TripBadge`). The native trip UI (#607) is built to
[`plans/ios-trips.md`](plans/ios-trips.md) M1 + M2: a trip is a header row with
the binding chip followed by its remaining legs as plain sibling rows (never a
`DisclosureGroup` — `List(selection:)` drives the iPad detail pane), and a trip
screen with a vertical timeline behind the header. It reads the same server
answers as the web — binding leg, headline, leg state (`monitoring` is not
remaining), `finished_at` — and re-derives none of them. Membership editing
(M3) is not built yet.

## Deferred (v2)

Commit-point table (the per-decision-point view: what you are deciding, legs
still needed, binding leg, and where you are stranded if it fails); MCP
`get_trip`; joint time optimisation across legs using the per-leg `flexibility`
scans; trip sharing via the reserved `share_code`; iOS membership editing
(group / add / remove from the list, `plans/ios-trips.md` M3). Nested trips: no.

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
