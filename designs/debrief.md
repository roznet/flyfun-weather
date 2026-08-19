# Flight Debrief

> Pilot post-flight judgement (cancelled / flown), captured against past flights to feed future Hewson advisory calibration (#90, #92).

## Intent

Every past flight is a labelled calibration sample. Pilot says what happened
in two taps; the system stores tags and outcomes against the flight; future
calibration work re-analyses against ERA5 reanalysis. This module owns the
data side. The pilot-facing summary panel exists to make the feature
*useful enough that the pilot fills it in* — calibration falls out.

Phase 1 (this module): data capture + per-user summary stats. Web shipped
first; iOS capture has since landed (form + card on the Advisory tab, rendered
from the served taxonomy — see *iOS surfaces*). Still out: post-ETA prompts,
cross-user aggregation, calibration harness changes. Calibration is a separate
problem that consumes this data later.

## Schema

Sidecar table 1:1 with `flights`, JSON columns for the variable-shape parts:

```
flight_debriefs
  flight_id      PK, FK → flights.id ON DELETE CASCADE
  decision       'cancelled' | 'flown' | 'monitoring'
  reasons_json   ['IMC','WIND',...]                  (cancelled only)
  outcomes_json  {'icing':'worse','cloud':'consistent',...}  (flown only)
  note           free text, ≤ 300 chars
  created_at, updated_at
```

`monitoring` = flight created to watch weather, not intended to fly. It is
neither a go nor a no-go decision, so both `reasons_json` and `outcomes_json`
must be empty (enforced by the Pydantic `_decision_shape` validator). It is
tracked separately in stats and excluded from both the cancellation-reason and
category-accuracy aggregates so it doesn't bias calibration data.

JSON over per-category columns because the taxonomy will evolve and
per-user stats fit comfortably in Python (≤ a few hundred rows). When
cross-user calibration export comes online, a one-shot script flattens
JSON to CSV/Parquet. See discussion in PR conversation for the full
trade-off table.

`outcomes_json` keys = categories that were *queried* (an advisory was
raised on the briefing). Default value `consistent` is stored explicitly
so the per-category accuracy denominator is honest — categories absent
from the dict were not queried and don't count.

## Taxonomy

Single shared vocabulary in `weatherbrief.debriefs.taxonomy`:

| Tag | Cancel reason | Outcome category |
|-----|---|---|
| IMC | ✓ | ✓ |
| ICE | ✓ | ✓ |
| WIND | ✓ | ✓ |
| TS | ✓ | ✓ |
| TURB | ✓ | ✓ |
| FRZ | ✓ | ✓ |
| VIS | ✓ | ✓ |
| OPS | ✓ | — (non-weather, no outcome to grade) |

`Decision`: `cancelled | flown | monitoring`. `OutcomeValue`: `consistent | better | worse`.

Python owns the *display* metadata too — `DECISION_ORDER`, `DECISION_LABELS`,
`TAG_LABELS`, `TAG_DESCRIPTIONS`, `OUTCOME_LABELS`, `NOTE_MAX_LENGTH` and
`ADVISORY_TAG_MAP` (advisory id → tag; per-id because one category can cover
several phenomena, and `model` advisories map to nothing). `build_taxonomy_catalog()`
serves all of it inside `/api/help/catalog` under the `debrief` key, so iOS renders
its whole debrief form from the wire and never hand-copies a Swift third copy
(`DebriefTaxonomy.bundledBaseline` is only the cold-first-launch fallback).

Three copies exist in practice: Python (source), the build-time TS mirror
`web/ts/components/debrief-taxonomy.ts` (hand-maintained — the web build doesn't
read the catalog), and the iOS baseline. A taxonomy edit means touching Python
plus those two mirrors.

`KEYWORD_MAP` maps each tag to phrases that the free-text note matches — used by
the hybrid entry UX to auto-toggle chips as the pilot types. It is deliberately
**not** in the served catalog, so note→chip auto-toggling is a web-only affordance.

## API

| Method | Path | Use |
|---|---|---|
| `GET` | `/api/flights/{id}/debrief` | Load — 404 if absent |
| `PUT` | `/api/flights/{id}/debrief` | Upsert |
| `DELETE` | `/api/flights/{id}/debrief` | Idempotent remove |
| `GET` | `/api/debriefs/stats?window_days=90` | Per-user aggregate |
| `GET` | `/api/help/catalog` | `debrief` key = served taxonomy (labels, advisory map, note cap) |

`GET /api/flights` is extended with two new fields per flight:
- `debrief: DebriefResponse | null` — owned past flights only
- `section: 'future' | 'recent' | 'past'` — server-assigned bucket

### Section assignment

- **future**: the flight has not ended — `departure_time + flight_duration_hours >= now`, via the shared `_flight_has_ended(flight, now)` predicate. Duration-aware since #536: a flight that departed 30 minutes ago on a 3-hour trip is in progress, not past. Zero duration (a real case — the web add-flight flow confirms it) is past the instant it departs, exactly as before. This matches the web's `isFlightPast` and iOS's `FlightResponse.hasEnded(now:)`.
- **recent**: most-recent `RECENT_SECTION_CAP` (= 2) past undebriefed flights whose `departure_time` is within the last `RECENT_SECTION_MAX_AGE_DAYS` (= 30) days. Independent of debrief history — debriefing one flight doesn't pull a third into the slot, but anything older than the window drops to Past so the nudge stays bounded.
- **past**: everything else

`_classify_section` and `_compute_recent_section` must both test the boundary
through `_flight_has_ended`; if they diverge, an airborne flight is `future` to
one and a `recent` candidate to the other.

The cap is hard-coded in `api/flights.py` for Phase 1; revisit if usage
patterns suggest a different ceiling or a per-user setting.

### Section ordering (#536)

Within a section the list order is `departure_time desc`, with one opt-in
account preference — `flight_order` in `app_prefs_json` (no migration; default
`"furthest_first"` = today's behaviour):

- `"furthest_first"` — the flight departing last is at the top. Newly added
  flights, usually the furthest ahead, appear first.
- `"soonest_first"` — only the **future** section flips to ascending, so the
  next departure (or the flight currently in progress) is at the top.

Recent and Past stay most-recent-first under both values. Past pagination is
offset-based over that order, so reordering it would produce duplicate and
skipped rows across pages. The `past_q` route-token filter (#542) narrows the
past page *after* section classification, so a filtered-out flight still counts
towards `_compute_recent_section`.

`load_flight_order(db, user_id)` is read in the *body* of `list_all_flights`,
never as a `Depends()` parameter: `api/agent.py` calls that route directly as a
plain function, where a `Depends` default would arrive as a
`fastapi.params.Depends` instance and silently compare unequal to every valid
value. The agent/MCP surfaces inherit the ordering for free, which is intended —
a chatbot printing the list then matches what the app shows.

iOS applies the same preference in `FlightListView.groupedFlights(_:order:now:)`
and in `FlightResolver.orderedForSuggestions(_:order:now:)` (the Siri/Shortcuts
display list). `FlightResolver.nextFlight` deliberately does **not** follow it —
"my next flight" means the soonest departure however the list is drawn.

## Stats

`compute_stats(flights, debriefs, window_days=90)` is a pure function
returning `DebriefStats`:
- Activity counts (flown / cancelled / monitoring / pending, where pending is
  the in-window remainder with no debrief)
- Cancellation reason histogram
- Per-category accuracy (consistent / better / worse counts)

Window scoping: a flight is in-window when `departure_time` falls within
the last `window_days`. Future flights are excluded. The default is 90;
the API accepts 1..3650 so 6m / 1y / all selectors can be added on the
client without touching the backend.

OPS-only cancellations are counted in `cancelled_count` and
`cancellation_reasons` but excluded from `category_accuracy` (OPS is not
in `OUTCOME_CATEGORIES`).

## Retention coupling

`flight_debriefs` rows exempt **all** of a flight's `briefing_packs` from
T2 retention. T1 still applies — heavy artifacts (`forecasts.json`,
`cross_section.json`, `gramet.*`, `skewt/`) get stripped at the normal
30-day boundary; lightweight `briefing.json` + DB row stay indefinitely.

The exemption keeps every refresh of the briefing the pilot saw, so
retrospective calibration can ask both "what did the system say at time
T?" and "did the prediction get better with newer model runs?". PIREP
exemption (full T1+T2 skip) is unchanged.

## UI layout

Flights list page (`web/index.html`, entry `flights-main.ts`):

```
[ Future flights ]
[ Recent — please debrief ]   ← per-row [Debrief →] button, accordion form
[ Statistics (last 90 days) ]
[ Past flights (collapsible) ]
```

Flight detail page (`web/flight.html`): a single Debrief section appears
under "Latest Assessment" for owner past flights only:
- No debrief: `[ Add debrief ]` button
- Has debrief: read-only summary card + `[ Edit debrief ]` button

Both surfaces share the same `debrief-form.ts` component.

### iOS surfaces

- `DebriefCard` sits on the Advisory tab of the briefing (owner-only), showing
  the stored debrief or an entry prompt; tapping it presents `DebriefFormView`
  as a sheet, driven by `DebriefViewModel`.
- The flights list shows a "Needs debrief" nudge glyph on cards the server put
  in `section == "recent"` (and only when `isEditable` — debrief is owner-only).
  Tapping the row opens the briefing, where the card lives.
- `/api/flights` inlines `debrief` so the list can draw the debriefed state and
  the briefing can seed its card without a second call.
- No stats panel on iOS — `/api/debriefs/stats` is web-only so far.

### Form behaviour

Hybrid entry on the cancel form:
- Chips multi-select (`IMC, ICE, WIND, ...`)
- Free-text note below
- As pilot types, `matchTagsInText` scans the note and auto-activates
  matching chips with a soft visual indicator. Pilot can deselect.

Outcome form (flown):
- Defaults every queried category to `consistent`.
- Pilot only flips the ones that weren't.
- The queried set = `flaggedCategories`, now derived from the briefing's
  non-green advisories through `ADVISORY_TAG_MAP` —
  `flaggedTagsFromAdvisories(manifest)` on web (flights list *and* flight
  detail), `DebriefTaxonomy.flaggedTagIds(fromAdvisories:)` on iOS. It falls
  back to `[]` when no manifest is loaded, which is why an un-briefed flight
  shows an empty outcome list rather than all eight categories.

The "default consistent" choice biases data toward "very inconsistent
only" — accepted trade-off. Phase 2 (post-ETA prompt) will record an
explicit `✓ All consistent` tap as a different signal than no debrief at
all. Stats panel surfaces a copy caveat acknowledging the bias.

## Files

- `src/weatherbrief/debriefs/taxonomy.py` — enums, labels, advisory map, keyword map, matcher, `build_taxonomy_catalog()`
- `src/weatherbrief/api/help.py` — serves the taxonomy under `/api/help/catalog`
- `src/weatherbrief/debriefs/stats.py` — aggregate computation
- `src/weatherbrief/models/storage.py` — `FlightDebrief` Pydantic
- `src/weatherbrief/db/models.py` — `FlightDebriefRow` ORM
- `src/weatherbrief/storage/debriefs.py` — CRUD + bulk fetch
- `src/weatherbrief/api/debriefs.py` — REST endpoints
- `src/weatherbrief/api/flights.py` — section + debrief on list response
- `src/weatherbrief/tasks/retention.py` — T2 exemption
- `alembic/versions/045_flight_debriefs.py` — table create
- `web/ts/components/debrief-{form,summary,stats,taxonomy}.ts`
- `web/ts/adapters/debrief-adapter.ts`
- `app/.../Models/API/DebriefTaxonomy.swift`, `DebriefResponse.swift` — iOS wire types + baseline
- `app/.../ViewModels/DebriefViewModel.swift`, `Views/Briefing/DebriefFormView.swift` — iOS form + card

## Out of scope (deferred)

- **Calibration harness**: separate problem, consumes this data via a
  future export script.
- **cancel.md backfill**: fresh start.
- **Post-ETA prompts** (Phase 2)
- **iOS stats panel** — capture shipped, `/api/debriefs/stats` still web-only
- **Note→chip auto-toggle on iOS** — `KEYWORD_MAP` isn't in the served catalog
- **Cross-user / aggregate stats** (Phase 3)
- **`pack_id` FK on debrief**: exempt all packs of a debriefed flight
  instead, simpler.
- **`decided_at` separate field**: `created_at` covers it for Phase 1.
- **`replaced_by_flight_id`, `package_flight_ids`**: dropped along with `postponed`.
- **`postponed`, `no_show` decision states**: simplified to
  `cancelled | flown` only.
- **Audit history of debrief edits**.
- **PIREP retention change**: separate decision, not in this module's scope.
