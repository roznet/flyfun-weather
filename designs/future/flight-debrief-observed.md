# Flight debrief: what was observed, what you reported

Status: brainstorm agreed 2026-10-06, split into slices A–C 2026-10-08, slice A reshaped
2026-10-11 (per-tick snapshots, recap on request, replay, web first); nothing built. Replaces the standalone PIREP
direction (`designs/pireps.md` as-built, `future/pirep-plan.md` M2/M3): pilot reports
stop being their own feature and become part of the flight debrief. Builds on
`designs/debrief.md`, `designs/live-observation-layer.md` and
`future/observed-tab-presentation.md`.

## 1. Premise

PIREPs never took off: the UI is hidden behind `PirepFeature.isEnabled = false` and
the community-feed value only appears once many pilots report, which a small user
base never reaches. The incentive was wrong: "report so others benefit" pays nothing
to the reporter on most flights.

The debrief has the opposite incentive, and its design already says so
(`debrief.md` § Intent: the summary exists "to make the feature useful enough that
the pilot fills it in — calibration falls out"). A pilot who likes to go back over
their flights ("which flights did I see icing on?") reports **for their own
logbook**. Another pilot flying nearby at the same time is a bonus, not the goal.

The second reason is that the instruments and the pilot see different things:

| The live layer observes | Only the pilot observes |
|---|---|
| Rain / radar cores, storms, lightning | Icing (intensity, type) |
| Satellite cloud tops | Turbulence |
| Ceiling / visibility **at airports** (METAR) | Ceiling and tops **en route**, in cloud or not |
| SIGMETs in force | Whether the forecast matched |

So the debrief can say: here is what the instruments saw along your flight; add what
only you could see. Reports fill exactly the gaps the observations leave.

## 2. Decisions (proposed, 2026-10-06)

- **The debrief is the home of pilot reports.** The PIREPs tab, the "Add PIREP"
  flight-list action and the long report form go away (§9).
- **The observed recap is automatic** and useful with zero reports: computed on the
  server from per-tick snapshots of the live layer, remapped to the time of the flight
  (§5). It covers cancelled flights too, on the planned times.
- **In-flight reports are one-tap bookmarks**; detail is added afterwards in the
  debrief, on the ground.
- **Reports are private by default.** Sharing with other pilots is an opt-in, later
  phase (§11). Private reports need none of the community machinery (publish gate,
  Europe-only gate, moderation).
- **Calibration is the stated long-term purpose but not the pitch until it is real**
  (§12). The honest pitch today is "see how the forecast compared with what you met".
- **Everything derived is computed server-side** and shared by web, iOS and the
  agent tools, as for the Observed tab.
- **Where the aircraft was comes from `FlightProgress`** (#759): the plan today;
  manual takeoff/landed, app GPS, ADS-B and bookmark fixes inside the object as they
  land. The debrief has no time fields of its own. Avionics logs stay an optional
  later phase (§10).
- **Web first** (2026-10-11): the debrief, recap and replay UI is built and reviewed
  on the web; iOS keeps the in-flight bookmark capture and gets the debrief UI after
  the web one works.

## 3. What exists to reuse

| Piece | Where | Use here |
|---|---|---|
| Per-flight live history, append-only | `live_history.jsonl`, `tasks/live_layer.py::load_live_history` | Raw METAR/TAF/SIGMET once each, change events, radar/lightning evidence, storm `estimate` records with geometry — the recap's main input |
| Route geometry | `analysis/route_geometry.py::RouteTrack.project` → `TrackProjection` | Off-track distance, side, along-route position for observations *and* reports |
| Live-window tick | `tasks/live_tick.py` (dep − 3 h … arr + 1 h), `commit_live_update` | Where the per-tick display snapshot is written (§5.1) |
| Flight progress | `analysis/flight_progress.FlightProgress`, `live_layer.flight_progress`, `progress.at(t)` | The recap's time base; observed sources (#759) plug in inside it |
| Debrief | `flight_debriefs`, `debriefs/taxonomy.py`, `DebriefCard` (iOS Advisory tab), `debrief-form.ts` | Outcomes get pre-filled from the recap; the recap and reports are shown with the debrief |
| Report storage + offline queue | `PirepRow`, `api/pireps.py`, iOS `PirepOfflineStore`, client UUID dedupe | Bookmarks are `PirepRow`s; offline queue unchanged |
| Retention exemption | `tasks/retention.py`: packs linked to a report skip all tiers | The forecast the pilot flew with survives with the report |

## 4. The debrief, in layers

One route timeline (x = distance along route labelled with times): the Observed tab's
route ribbon (`LiveRibbon`, #690) after the fact, with the same renderers, each segment
shown as it was when the aircraft was there. Three rows:

1. **Forecast:** what the briefing the pilot flew on said, at each point and time
   (advisories, cloud bands, icing / turbulence layers at the flown altitude).
2. **Observed:** the recap (§5): departure METAR at departure time; en route per
   segment: radar maximum, storms with their off-track distance, lightning, satellite
   tops, SIGMETs in force; arrival METAR, and TAF at arrival time.
3. **Reported:** the pilot's bookmarks, with their detail once added (§6).

Plus alert markers on the time axis: when the app raised, worsened, improved or cleared
an alert (the history `change` records). Whether it was pushed is the pilot's setting
and not part of the review.

**Two more views over the same snapshots (2026-10-11, web first, #769):**

- **Single-tick replay.** Tap a segment, a route point or a tick chip, and the whole
  Observed section (Haiku highlight, nutshell and alert rows, ribbon, cards, sources)
  re-renders as it stood at that tick, under "Viewing HH:MMZ · Back to live / recap".
  Available during the flight too. The route map's imagery stays live (past frames are
  not kept on the droplet).
- **Distance × time grid (experiment).** The ribbon stacked once per tick, the flight
  drawn as its diagonal: the diagonal is the recap; the rest shows whether it was
  already there, built or moved after the aircraft passed, and what an hour later
  would have met. Behind a "Show over time" toggle, directly on wide screens; kept
  only if it reads well on real flights.

The forecast row comes from a pack the pilot picks in the web sidebar, defaulting to
the newest pack before departure.
Under the timeline:

- **The debrief outcomes, pre-filled from the evidence.** Today every queried category
  defaults to `consistent` (`debrief.md` § Form behaviour, with a known bias). With a
  recap the form proposes a value with its reason, for example "Convective: no radar
  storm within 10 NM during the flight — consistent?", "Icing: you marked icing at
  FL080, forecast was light — worse?". The pilot confirms or flips. The label stored
  is the pilot's; the proposal is recorded beside it, so calibration can tell a
  confirmed proposal from an untouched default.
- **Forecast vs report at the report's point** (§7).

Progressive depth: the top is one line per phase in the
nutshell style, then the timeline, then details per source.

## 5. Observed recap (server)

Revised 2026-10-11 (#711 is the spec; this section is the summary).

### 5.1 Per-tick display snapshot

`live.json` is overwritten every tick, and `live_history.jsonl` keeps raw reports,
change events, evidence and storm estimates, but not the quiet ticks ("no echo along
the route" is a result) nor what the page showed. Each tick appends the blocks the
Observed section renders to `live_snapshots.jsonl.gz`:

- `glance` (with the highlight), `ribbon` (with `weather`), `storms`, `changes`,
  `route_sigmets`;
- the `observed_conditions` summary and source metadata, not the per-point sample
  arrays (those feed only the map and cross-section overlays).

The station lane / METAR table is rebuilt from the history `report` records. Measured:
~6 KB gzipped per tick, ~200 KB per flight; a full `live.json` copy would be ~23 KB per
tick. This replaces the earlier compact `ribbon` record, and keeps storms and the
rain/core bands, so the recap draws the normal ribbon.

### 5.2 Remapping to the flight's time

For each segment the recap picks the snapshot whose **radar frame valid time** is
nearest the time the aircraft was there (not the tick time: they differ by 6–15 min,
more than a segment at cruise speed). The time base is read through `FlightProgress`
only: the plan today; #760 manual takeoff/landed, #761 app GPS, #762 ADS-B and #712
bookmark fixes inside the object as they land. The recap names its base (`planned` /
`partial` / `observed`). A cancelled flight uses the plan and says so.

### 5.3 Computed on request, stored as inputs

- **No frozen recap.** It is computed per request from about 35 snapshot lines, so a
  corrected takeoff or a new fix applies automatically. No `flight_recaps` table.
- **Forecast lane:** each pack writes a per-segment summary when built (each
  advisory's `highlights.ribbon` bands per model, a few KB), so any pack stays
  selectable after T1 strips the pack. `?pack=` picks one, defaulting to the newest
  before departure. Its advisories are at that pack's planned ETAs; the gap is shown,
  and re-evaluating at actual times is later work. What the lane shows is settled on
  real flights (#769).
- **Retention:** snapshots, history and pack summaries are kept 90 days after
  departure for every flight with a live window (cancelled ones included) and forever
  for debriefed, reported or pinned flights. `live.json` and `live_highlights.jsonl`
  (the admin calibration log) are still purged at T1.

### 5.4 Coverage

- A recap exists only for flights that had a live window with a pack. A flight
  created afterwards, or one whose live layer was off, has none: the debrief says so
  rather than showing an empty timeline as "nothing observed".
- Flights flown before snapshots exist are not backfilled; `replay_live_history.py`
  stays a dev tool for investigating an old flight.
- Later (not phase 1): rebuild a missing recap from the archives (radar frames on the
  NAS for 12 months, airport observations in the Parquet archive).
- Outside OPERA / MTG coverage (US flights) the recap is METAR/TAF/SIGMET only; each
  source carries "unavailable", never "clear".

## 6. In-flight reports as bookmarks

### 6.1 Capture

On the Observed tab (flight day) and from the Start Flight view: a row of large
one-tap buttons:

`Icing` · `Bumpy` · `In cloud` · `Tops here` · `Note`

One tap stores time, GPS position, GPS altitude and the kind, queued offline as
today. The GPS fix is also posted to `FlightProgress` as a `fix` event (#759), so a
bookmark helps place the flight on the recap. No severity, no type, no typing in the air. A small "Marked: icing 14:32Z"
confirmation with undo. `Note` may later take dictation; a typed note is fine for v1.

### 6.2 Enrich afterwards

In the debrief (web first; iOS after the web debrief works) each bookmark opens an edit card with the current form's fields
(intensity, icing type, tops value and `tops_basis`, ceiling, wind, temperature,
remarks), pre-filled where the evidence allows (altitude from GPS, tops from the
satellite at that point offered as a suggestion, never filled silently). Reports can
also be added after the flight from the timeline ("I hit icing about here").

### 6.3 Data model changes (`PirepRow`)

All hazard columns are already nullable, so a bookmark fits. Proposed additions
(follow `designs/migrations.md`):

- `kind`: `icing | turbulence | in_cloud | tops | note`, what was tapped; the detail
  columns stay null until enriched. Keeps "icing, intensity not given" distinct from
  "no icing".
- `flight_id`: the iOS client sends no `pack_id` today and the server infers the
  flight from `observed_at`; a bookmark taken from the flight's Observed tab knows its
  flight. Send `flight_id` and `pack_id` (the pack on screen).
- `visibility`: `private | shared`, default `private`.
- `enriched_at`: when detail was added on the ground (calibration should know a value
  was given hours later from memory).
- `source`: add `bookmark`; `postflight` already exists in `PIREP_SOURCES` and finally
  gets an emitter (§6.2).

### 6.4 API and gates

- `PATCH /api/pireps/{id}` (owner only) to enrich. None exists today (POST, batch,
  GET only).
- `pirep_can_publish` gates `shared` only. Private reports need just an account.
- `validate_european_bounds` applies to `shared` only: it protects the community
  provenance claim, which a private logbook entry doesn't make. US pilots can keep a
  private log.
- Rate limits: the burst limiter (1 per 120 s) would block "Bumpy" then "In cloud" a
  minute apart. Apply it to `shared` only; private bookmarks keep the daily cap.

## 7. Forecast vs report at the report's point

For each report, the forecast at its position, altitude and time from the pack the
pilot flew with: icing / turbulence layer and intensity, cloud base / tops, freezing
level, temperature, wind. Computed when the report is enriched (or added after the
flight) and stored compactly on the report, because `cross_section.json` of a debriefed flight is stripped at
T1 (`debrief.md` § Retention coupling). Packs linked to a report keep their full
exemption (`pireps.md`), so a later recompute stays possible, but the debrief must not
depend on it.

This pairing is the calibration sample: a pilot observation, the forecast the pilot
saw, and the instruments' view at the same place and time.

## 8. Personal history

- A "My reports" list across flights, filterable by kind, intensity, season, aircraft
  and forecast agreement ("icing where none was forecast"), each opening its flight's
  debrief.
- Extends the web debrief stats panel with report counts and per-category agreement;
  the iOS stats panel is still missing (`debrief.md` § Out of scope) and would come
  with this.
- This is the reshaped successor of the PIREP list view.

## 9. Removing the PIREP surfaces

- iOS: `BriefingTab.pireps` and its tab, the toolbar "Report PIREP", the flight-list
  "Add PIREP" context menu, `PirepListView` (replaced by §8), `PirepReportingView`
  (replaced by the bookmark row + the debrief edit card), `PirepFeature`.
- Web: the hidden briefing PIREPs panel (`#pireps-wrapper`, `loadFlightPireps`), the
  standalone `pireps.html` page and its nav entry; `pirep-map.ts` is kept for §11.
- Server: the API stays (extended in §6). `list_pireps` gains a "mine" query for §8.
- Release notes: the PIREP UI was never public, so nothing is "removed" from the
  user's point of view.

## 10. Phase 2 (optional): the actual track

What each source adds, best first:

| Source | Track | Wind | Temperature | Turbulence | Notes |
|---|---|---|---|---|---|
| Avionics log via flightlogstats (G1000 CSV) | yes | measured (`WndSpd`/`WndDr`) | `OAT` | objective, from `NormAc` / `LatAc` | User-owned app, already parses these fields and has an upload queue (to FlySto) to extend |
| Avionics log via FlySto | yes | yes | yes | yes | flightlogstats only uploads; no documented read-back API — ask FlySto |
| Phone GPS (Start Flight) | yes | no | no | no | `FlightTrackingService` already tracks live; needs an end-of-flight upload of a thinned track |
| ADS-B by tail (OpenSky) | yes, gaps at low level | no (GA rarely sends Mode S enhanced data) | no | no | Free account, OAuth; `/flights/aircraft` the next day (nightly batch); `/tracks` experimental |

Notes:

- **flightlogstats → flyfun-weather** is the preferred link: a second upload
  destination sending a summary (thinned track, wind, OAT, a normal-acceleration spread
  series), not the raw CSV, authenticated with the shared flyfun account. The
  acceleration spread is a usable turbulence-intensity proxy, which no other source
  gives and the turbulence calibration has never had.
- **adsb.lol** daily trace dumps are open data but each day is one large archive;
  only as an occasional batch job on the Mac mini, not per request.
- **Privacy:** storing a track reverses the current "no GPS tracks stored" line in
  `pireps.md`. Opt-in per user, private, deleted with the flight, stated at opt-in.
- With a track, §5.2 uses it as the time base and the recap samples observations along
  the flown path instead of the planned one.

## 11. Phase 3 (optional): sharing

- A shared report enters the live layer of other flights whose corridor and time
  window it falls in: a `pilot_reports` block in `live.json` (route-projected with
  `RouteTrack.project`), a ribbon lane, a "Pilot reports" section on the Observed tab,
  a map `focus` kind `report`. Highlights only, no alerts, until there is data to
  calibrate an alert on (live alerts are kept scarce by design).
- A "Pilot reports" layer on the web maps Now tab (bbox fetch, the lightning layer as
  template, markers from `pirep-map.ts`), and a toggle on the iOS route map. iOS has
  no Now map; building one is separate work.
- The `pireps.md` presentation rules stay: older than ~90 min flagged, never hidden;
  "no reports" never reads as clear; the situational-awareness disclaimer in the UI.
- `pirep_can_view` is dropped (its own design calls it a beta gate);
  `pirep_can_publish` stays as the kill switch for sharing.

## 12. Calibration and the wording

- What is true from phase 1: the pilot sees forecast vs observed vs reported for their
  own flights. That is the pitch.
- "Helps calibrate the forecasts" is a promise until something consumes the data (the
  debrief's calibration harness is still out of scope). Use it in copy only once an
  export or a calibration run exists: release notes describe only what is live.
- What is stored for calibration from day one: report + `enriched_at`, the forecast at
  the report point, the recap at the same point, the pilot's outcome and whether it
  confirmed a proposal or overrode it.

## 13. Slices

Tracked under umbrella #710; the optional phases get issues only when wanted.
Reorganised 2026-10-11.

- **A1. Observed recap, server** (#711): per-tick snapshots, recap on request with the
  `FlightProgress` time base, forecast lane with per-pack summaries, `/live/ticks`,
  `/live/at`, `/recap?pack=`, agent recap block, 90-day / kept-forever retention.
- **A2. Web** (#769, needs A1): recap ribbon, single-tick replay with back to live,
  alert markers, sidebar pack picker, distance × time grid prototype; a review pass on
  real flights settles the grid, the forecast lane's content and where the debrief
  form lives.
- **B. Bookmarks + reports** (#712, parallel with A): `PirepRow` additions, `PATCH`,
  gates and limits split by visibility, iOS bookmark row, bookmark as progress fix,
  web debrief report list and edit card, "Reported" row on the web recap; remove the
  PIREP surfaces (§9). No actual-time fields (#760 owns takeoff/landed).
- **C. Evidence-backed debrief + My reports** (#713, needs A and B): outcome proposals
  from the recap (observed and forecast lanes) and reports with the proposal recorded
  beside the label, forecast at the report point stored on the report at enrich time
  (§7), "My reports" with filters and the web stats panel additions (§8).
- Later, no issue yet: **iOS debrief / recap / replay UI**, after A2 works on the web.
- Optional, no issue yet: **phase 2, actual track** (§10: flightlogstats upload first;
  phone GPS and ADS-B are now #761/#762 under #759) and **phase 3, sharing** (§11).

## 14. Decisions taken and open questions

Decided 2026-10-08:

- **Name**: keep "Debrief"; in flight the buttons are named by kind (Icing · Bumpy ·
  In cloud · Tops here · Note); "pirep" stays internal.
- **Recap on iOS**: after the live window the Observed tab turns into the recap (the
  place the pilot watched it live); the Advisory-tab `DebriefCard` links to it.
  Deferred 2026-10-11 until the web debrief works.

Decided 2026-10-11 (#711, #769, #712, #713):

- Per-tick display snapshots; recap computed on request; no `flight_recaps` table.
- Time base from `FlightProgress` (#759); no debrief time fields; bookmarks are fixes.
- Cancelled flights get a planned-time recap.
- Single-tick replay, during the flight too; distance × time grid as an experiment.
- Forecast lane in slice A with a sidebar pack picker; alert markers, no push record.
- Retention: 90 days for every live-window flight, forever when debriefed, reported or
  pinned.
- Web first for the debrief UI; no LLM debrief paragraph for now; satellite tops per
  segment deferred.

Open:

- **Opt-in GPS track** (phase 2): yes/no, and whether phone GPS is collected at all
  without avionics.
- ~~Recap storage~~: none, computed on request (2026-10-11).
- **Forecast lane content** (advisories, consensus vs per model, one row or several):
  settled in #769's review pass.
- **Where the web debrief form lives**: flight page or next to the recap (#769).
- **Proposal-vs-label recording**: one field per category (`proposed`, `source`) in
  `outcomes_json`, or a separate column.
