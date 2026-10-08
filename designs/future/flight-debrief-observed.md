# Flight debrief: what was observed, what you reported

Status: brainstorm agreed 2026-10-06, split into slices A–C 2026-10-08; nothing built. Replaces the standalone PIREP
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
- **The observed recap is automatic** and useful with zero reports: built on the
  server from the live layer, remapped to the time of the flight, frozen when the live
  window closes.
- **In-flight reports are one-tap bookmarks**; detail is added afterwards in the
  debrief, on the ground.
- **Reports are private by default.** Sharing with other pilots is an opt-in, later
  phase (§11). Private reports need none of the community machinery (publish gate,
  Europe-only gate, moderation).
- **Calibration is the stated long-term purpose but not the pitch until it is real**
  (§12). The honest pitch today is "see how the forecast compared with what you met".
- **Everything derived is computed server-side** and shared by web, iOS and the
  agent tools, as for the Observed tab.
- **Actual-track sources (avionics logs, phone GPS, ADS-B) are an optional later
  phase** (§10). Phase 1 works on the planned schedule plus the pilot's actual times
  and bookmark positions.

## 3. What exists to reuse

| Piece | Where | Use here |
|---|---|---|
| Per-flight live history, append-only | `live_history.jsonl`, `tasks/live_layer.py::load_live_history` | Raw METAR/TAF/SIGMET once each, change events, radar/lightning evidence, storm `estimate` records with geometry — the recap's main input |
| Route geometry | `analysis/route_geometry.py::RouteTrack.project` → `TrackProjection` | Off-track distance, side, along-route position for observations *and* reports |
| Live-window tick | `tasks/live_tick.py` (dep − 3 h … arr + 1 h) | Where the compact per-tick observed record is written, and where the window-close freeze hooks in |
| Debrief | `flight_debriefs`, `debriefs/taxonomy.py`, `DebriefCard` (iOS Advisory tab), `debrief-form.ts` | Outcomes get pre-filled from the recap; the recap and reports are shown with the debrief |
| Report storage + offline queue | `PirepRow`, `api/pireps.py`, iOS `PirepOfflineStore`, client UUID dedupe | Bookmarks are `PirepRow`s; offline queue unchanged |
| Retention exemption | `tasks/retention.py`: packs linked to a report skip all tiers | The forecast the pilot flew with survives with the report |

## 4. The debrief, in layers

One route timeline (x = distance along route labelled with times): the Observed tab's
route ribbon (`LiveRibbon`, #690) after the fact, with the same renderers on iOS and
web, each segment shown as it was when the aircraft was there. Three rows:

1. **Forecast:** what the briefing the pilot flew on said, at each point and time
   (advisories, cloud bands, icing / turbulence layers at the flown altitude).
2. **Observed:** the recap (§5): departure METAR at departure time; en route per
   segment: radar maximum, storms with their off-track distance, lightning, satellite
   tops, SIGMETs in force; arrival METAR, and TAF at arrival time.
3. **Reported:** the pilot's bookmarks, with their detail once added (§6).

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

### 5.1 Inputs, and the one missing record

The history already holds raw reports, change events, evidence and storm estimates.
It does **not** hold what the radar / lightning / tops looked like along the route on
a quiet tick: evidence is written only when a change appears or moves
(`EVIDENCE_PEAK_DBZ` / `EVIDENCE_SPAN_NM`). A recap needs the quiet ticks too ("no
echo along the route" is a result).

The route ribbon (#690, `LiveRibbon`) already computes that summary every tick:
each `RibbonSegment` carries the strongest echo, its `radar_status` (`measured` /
`no_coverage` / `no_sample`, so "nothing detected" stays distinct from "could not
see"), lightning, the SIGMET ids and storm ids abeam it, and `LiveRibbon.weather` the
rain/core bands beside the route. So the missing record is a compact copy of the
ribbon per tick: one `ribbon` history record with `radar_time` and the segments'
lane values (no `focus`, no profiles), plus satellite tops per segment, which the
ribbon doesn't carry yet. Tens of bytes per segment, ~37 ticks per flight.

### 5.2 Remapping to the flight's time

For each segment the recap picks the tick nearest the time the aircraft was there.
Phase 1 time base, best first:

1. The pilot's actual off-block / landing times (two optional fields on the debrief),
   with the planned profile stretched between them.
2. Bookmark positions and times as anchors (a bookmark is a GPS fix with a time).
3. The planned schedule (departure + profile ETAs), as the live layer uses today.

The recap states which base it used. Phase 2 (§10) adds a real track.

### 5.3 Freeze and storage

- **Freeze at live-window close** (arrival + 1 h) in the tick, recomputed when the
  pilot edits the actual times or adds a bookmark.
- **Durable, small storage.** Today `live.json` and `live_history.jsonl` are purged at
  retention T1 (30 days after departure, `retention._purge_live_layer`), even for
  debriefed flights. So the recap is written where retention keeps it: a
  `flight_recaps` row (JSON column), or a `recap.json` beside the packs outside
  `LIVE_FILES`. Proposal: a DB row, like `flight_debriefs`.
- **Keep the history of debriefed / reported flights.** It is tens to ~100 KB per
  flight and is the only input for recomputing a recap under better rules later.
  Exempt `live_history.jsonl` from T1 when the flight has a debrief or a report; still
  purge `live.json`.

### 5.4 Coverage

- A recap exists only for flights that had a live window with a pack (every flight
  with a pack, not only auto-refresh ones). A flight created afterwards, or one whose
  live layer was off, has none: the debrief says so rather than showing an empty
  timeline as "nothing observed".
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
today. No severity, no type, no typing in the air. A small "Marked: icing 14:32Z"
confirmation with undo. `Note` may later take dictation; a typed note is fine for v1.

### 6.2 Enrich afterwards

In the debrief each bookmark opens an edit card with the current form's fields
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
level, temperature, wind. Stored compactly in the recap when the report is enriched or
the recap is frozen, because `cross_section.json` of a debriefed flight is stripped at
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

Three slices, tracked under one umbrella issue; the optional phases get issues only
when wanted.

- **A. Observed recap** (independent, useful with zero reports): `ribbon` history
  record in the tick (+ tops per segment), freeze at window close, `flight_recaps`
  storage, `live_history.jsonl` exempt from T1 for debriefed/reported flights, recap
  served by the API and the agent tools; after the live window the Observed tab shows
  the recap on iOS and web, and the debrief card links to it.
- **B. Bookmarks + reports in the debrief** (parallel with A): `PirepRow` additions,
  `PATCH`, gates and limits split by visibility, bookmark row on the Observed tab,
  edit card and after-the-flight reports in the debrief, actual off-block / landing
  times; remove the PIREP surfaces (§9).
- **C. Evidence-backed debrief + My reports** (needs A and B): outcome proposals from
  the recap and reports with the proposal recorded beside the label, forecast at the
  report point (§7), "My reports" with filters and the stats panel additions (§8).
- Optional, no issue yet: **phase 2, actual track** (§10: flightlogstats upload first,
  then phone GPS, OpenSky last) and **phase 3, sharing** (§11).

## 14. Decisions taken and open questions

Decided 2026-10-08:

- **Name**: keep "Debrief"; in flight the buttons are named by kind (Icing · Bumpy ·
  In cloud · Tops here · Note); "pirep" stays internal.
- **Recap on iOS**: after the live window the Observed tab turns into the recap (the
  place the pilot watched it live); the Advisory-tab `DebriefCard` links to it.

Open:

- **Opt-in GPS track** (phase 2): yes/no, and whether phone GPS is collected at all
  without avionics.
- **Recap storage**: DB row (proposed) vs file beside the packs.
- **Proposal-vs-label recording**: one field per category (`proposed`, `source`) in
  `outcomes_json`, or a separate column.
