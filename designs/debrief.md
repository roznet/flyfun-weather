# Flight Debrief

> Pilot post-flight judgement (cancelled / flown), captured against past flights to feed future Hewson advisory calibration (#90, #92).

## Intent

Every past flight is a labelled calibration sample. Pilot says what happened
in two taps; the system stores tags and outcomes against the flight; future
calibration work re-analyses against ERA5 reanalysis. This module owns the
data side. The pilot-facing summary panel exists to make the feature
*useful enough that the pilot fills it in* — calibration falls out.

Phase 1 (this module): web-only data capture + per-user summary stats. No
post-ETA prompts, no cross-user aggregation, no calibration harness changes.
Calibration is a separate problem that consumes this data later.

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

`KEYWORD_MAP` maps each tag to phrases that the free-text note matches —
used by the hybrid entry UX to auto-toggle chips as the pilot types. The
TS mirror in `web/ts/components/debrief-taxonomy.ts` must stay in sync
(small surface, hand-maintained).

## API

| Method | Path | Use |
|---|---|---|
| `GET` | `/api/flights/{id}/debrief` | Load — 404 if absent |
| `PUT` | `/api/flights/{id}/debrief` | Upsert |
| `DELETE` | `/api/flights/{id}/debrief` | Idempotent remove |
| `GET` | `/api/debriefs/stats?window_days=90` | Per-user aggregate |

`GET /api/flights` is extended with two new fields per flight:
- `debrief: DebriefResponse | null` — owned past flights only
- `section: 'future' | 'recent' | 'past'` — server-assigned bucket

### Section assignment

- **future**: `departure_time >= now`
- **recent**: most-recent `RECENT_SECTION_CAP` (= 2) past undebriefed flights whose `departure_time` is within the last `RECENT_SECTION_MAX_AGE_DAYS` (= 30) days. Independent of debrief history — debriefing one flight doesn't pull a third into the slot, but anything older than the window drops to Past so the nudge stays bounded.
- **past**: everything else

The cap is hard-coded in `api/flights.py` for Phase 1; revisit if usage
patterns suggest a different ceiling or a per-user setting.

## Stats

`compute_stats(flights, debriefs, window_days=90)` is a pure function
returning `DebriefStats`:
- Activity counts (flown / cancelled / pending)
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

### Form behaviour

Hybrid entry on the cancel form:
- Chips multi-select (`IMC, ICE, WIND, ...`)
- Free-text note below
- As pilot types, `matchTagsInText` scans the note and auto-activates
  matching chips with a soft visual indicator. Pilot can deselect.

Outcome form (flown):
- Defaults every queried category to `consistent`.
- Pilot only flips the ones that weren't.
- (Phase 1 wires `flaggedCategories=[]` placeholder; the briefing-side
  hookup that derives flagged categories from advisories is a follow-up.)

The "default consistent" choice biases data toward "very inconsistent
only" — accepted trade-off. Phase 2 (post-ETA prompt) will record an
explicit `✓ All consistent` tap as a different signal than no debrief at
all. Stats panel surfaces a copy caveat acknowledging the bias.

## Files

- `src/weatherbrief/debriefs/taxonomy.py` — enums, keyword map, matcher
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

## Out of scope (deferred)

- **Calibration harness**: separate problem, consumes this data via a
  future export script.
- **cancel.md backfill**: fresh start.
- **Post-ETA prompts** (Phase 2)
- **iOS UI** (Phase 2 — same data shape)
- **Cross-user / aggregate stats** (Phase 3)
- **`pack_id` FK on debrief**: exempt all packs of a debriefed flight
  instead, simpler.
- **`decided_at` separate field**: `created_at` covers it for Phase 1.
- **`replaced_by_flight_id`, `package_flight_ids`**: dropped along with `postponed`.
- **`postponed`, `no_show` decision states**: simplified to
  `cancelled | flown` only.
- **Audit history of debrief edits**.
- **PIREP retention change**: separate decision, not in this module's scope.
