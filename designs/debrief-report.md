# Debrief report

> Post-flight report built on the server (#1023, umbrella #710): a headline that judges only what
> the instruments saw, collapsed detail lines, airports at actual times, and one to three one-tap
> questions on what they could not see. Answers are stored one row each and fold into the old
> debrief outcomes. UI on iOS, Android and web is #1024.

## Intent

The debrief is the incentive for pilot feedback: the pilot gets "what happened vs what was
forecast" for free, and the server asks only where its instruments are blind (icing, cloud at
the flown level, turbulence, showers where radar had no coverage). Every answer is a labelled
verification sample (#792, #411 step 5).

Rules were settled on the phase 0 prototype (branch `claude/project-thread-9dvcd6`, reviewed by
the owner on real flights 17-18 Nov 2026). Plan: https://claude.ai/artifact/MXa2nY3mXsz9LH2tgLCbRR

## Structure

| Piece | Where |
|---|---|
| Report rules (pure) and assembly | `debriefs/report.py` (`build_report`, `report_for_flight`) |
| Answers + report rows | `storage/debriefs.py` (`save_answers`, `load_answers`, `bulk_report_flags`) |
| Window-close job and push | `tasks/debrief_close.py` (`run_debrief_close`), run by `scheduler._run_verification_once` after the live tick |
| API | `api/debriefs.py` |
| Replay script | `scripts/ops/debrief_report.py FLIGHT_ID... [--json]` (dev DB, after `import_flight.py`) |

Inputs are all existing: the observed recap (`debrief-recap.md`), the recap's default forecast
pack (`route_advisories.json` highlight regions with base/top per model, `forecast_lane.json`
ribbons), the live history (METAR/TAF at actual times, the app's fixes for the flown level), the
proxy station per field (#767, now also kept in the per-tick snapshots so it survives `live.json`
going at T1), and the route locator for wording.

## Rules (agreed, round 2)

- **Headline** (`build_headline`) judges radar-visible weather only. Heavy showers not forecast,
  or forecast showers that did not show, set `differs_from_forecast`; light rain does not.
- **Airports** enter the headline only when below VFR at your time, worse than the TAF you had,
  fog / low cloud lifting in the 3 h before departure, or a cell / category drop in the hour after
  landing.
- **Icing, cloud and turbulence are never judged**: they are the questions.
- **Details** (`summary.lines`): METAR next to the TAF, the forecast at your level, what radar saw,
  in-flight alerts. No verdict word.
- **Questions** (`build_questions`): one per hazard stretch flagged amber/red by more than half the
  models that ran, at the flown level (within 1,000 ft), at most one per kind, icing first, max 3.
  Showers only where radar could not see. Flown level = median of mid-route airborne fixes, else
  the planned cruise ("You were at FL070 (planned)").
- **Interest** (`build_interest`): forecast hazard at your level, in-flight alert, heavy precip
  observed, airport worse than the TAF, or any question. A cancelled flight never is.

`REPORT_VERSION` is the rules version stored with each answer: bump it when a rules change would
make an old answer read differently. Question ids are `kind-from_nm-to_nm`, so a rules change
that moves a stretch makes old answers "orphans" (still returned under `orphan_answers`).

## Storage

```
flight_debrief_answers          one row per answer; unique (flight_id, question_id); cascade
  question_id, kind, answer, avoided, note (300), answered_at, rules_version
  question_json   text, stretch, at, flown_ft, flown_source   (the question as asked)
  forecast_json   {model: {severity, base_ft, top_ft}}         (the forecast it was asked about)

flight_debrief_reports          one row per flight, written once at window close; cascade
  built_at, rules_version, status, headline, differs_from_forecast, interesting,
  reasons_json, questions_json [{id, kind, text}], push_reason, push_outcome, pushed_at

flight_debriefs.form            'questions' | 'legacy'  (existing rows backfilled 'legacy')
```

**Folded form.** Answering (`save_answers`) upserts `flight_debriefs` as `flown`, form
`questions`, with `outcomes_json` derived from all stored answers (`outcomes_from_answers`):

| Answer | Old outcome |
|---|---|
| Icing: none (in cloud) / light / moderate or more | ICE better / consistent / worse |
| Icing: not in cloud, or any answer with "avoided" | no outcome |
| Cloud: in cloud, in and out | IMC consistent |
| Cloud: on top, below, no cloud | IMC better |
| Turbulence: smooth / light / moderate or more | TURB better / consistent / worse |
| Showers | no outcome (kept in the answers table only) |

`PUT /debrief` with `form: "questions"` is the section's flown / cancelled tap: a flown
flight's outcomes are re-derived from the answers and the sent `outcomes` ignored. Without
`form` (older apps) it stores the old shape as `legacy`. Stats are unchanged; legacy rows stay
as they are, marked, since their "consistent" defaults are biased.

The report itself is never stored whole: `GET /debrief/report` rebuilds it on each call. The
report row keeps only what the list, agents and push need.

## Window close and push

Each 10-min verification cycle, after the live tick: flights with packs whose live window closed
in the last 6 h (`LOOKBACK`) and no report row get one (`find_closed_flights`, `close_flight`).
The window end is `live_window_end` (landing + 15 min grace, else arrival + 1 h). The row is
committed before any push, so a flight is built and pushed at most once and a failed send is
never retried. The first run after a deploy only picks flights closed in the last 6 h.

Push only when: interesting, and `push_text` has a body (a question, or a headline that differs
from the forecast); the flight was started (`start_fix`) and airborne (`takeoff_at`); not
cancelled / monitoring; bell not muted (flight, else trip); `notify_push` on; a device.
`WB_DEBRIEF_PUSH=1` sends (payload `type: "debrief"`, "EGKB → EHRD · debrief" / "Two quick
questions about the icing forecast."); unset logs `DEBRIEF_PUSH_WOULD_SEND` and stores
`push_outcome = shadow`. Turn it on only once #1024 is on both stores.

Flights without Start still get a report row and questions (asked at the planned level): they
show in the app, they just never push.

## API

| Method | Path | Use |
|---|---|---|
| `GET` | `/api/flights/{id}/debrief/report` | Full report, built on request (owner) |
| `PUT` | `/api/flights/{id}/debrief/answers` | `{answers: [{question_id, answer|null, avoided, note}]}`; 404 unknown question, 422 bad option; returns the report |
| `GET` | `/api/flights/{id}/debrief/summary` | `{debrief: {headline, questions_open, questions_answered, built_at} | null}` for agents |

`/api/flights` carries flags only: `debrief_ready` (report row exists) and `questions_open`
(asked minus answered), two queries for the whole list. `get_briefing` (ChatGPT action and MCP)
carries `debrief` from the summary, owner only.

## Gotchas

- Retention: history and snapshots are kept forever only for debriefed flights
  (`retention.kept_flight_ids`). Answering creates the debrief row; a flight never answered
  loses its recap inputs at 90 days, after which `GET /debrief/report` degrades while the stored
  headline stays.
- `report_for_flight` reads the answers from the DB; tests that fake it must too.
- The headline wording carries no verdict word: never "go/no-go" (product principle).
