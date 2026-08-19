# iOS App — Features & Vision

> End-state feature set. Build order in [Roadmap](./ios-app-roadmap.md).

> **Status framing — read this before believing any present tense below.** This doc is the *target* feature set, largely written before the app existed, and most of it is still forward-looking. Shipped today: offline pack download + native briefing viewer, APNs refresh-complete push, "Start Flight" GPS tracking, and briefing-anchored PIREP filing with an offline queue. NOT built in the app: the proactive prompting engine, standalone/community PIREP filing and the PIREP map, voice PIREP, and live WebSocket sharing. Corrections are inlined per section. [Overview](./ios-app-overview.md) is the authoritative current status.

## Vision

The WeatherBrief Companion App turns every flight into a two-way weather conversation: before departure, sync your briefing and carry it offline; in flight, mark actual conditions with a few taps; after landing, observations feed back into forecast verification and stream as PIREPs to other pilots.

PIREPs are first-class — you don't need a planned flight to contribute. Just landed after local pattern work? Open the app, file a PIREP for the airport, and every pilot checking conditions there sees it. The community feed builds a real-time picture that complements official reports.

Designed for the cockpit: one-handed operation, large tap targets, GPS-prepopulated fields, voice input via Siri, UI that assumes the pilot is busy. Works fully offline (most GA pilots have no connectivity) but lights up with real-time sharing when Starlink/cellular is available.

On the ground, doubles as a primary briefing viewer — push notifications when briefings auto-refresh, native cross-section rendering, mobile-optimized UI better than the web on phone/tablet.

Long-term, observation data creates a feedback loop: compare forecast vs reality, learn which models are most reliable for which conditions, build a community-sourced real-time picture complementing official PIREPs.

## 1. Briefing Sync & Offline Access

Briefing data splits into two tiers: **lightweight payload** synced for offline use (Phase 2+), **heavy artifacts** on-demand when online.

### Offline payload (synced before departure — Phase 2+)

Cached locally. Everything the app needs to display the full briefing and power the prompting engine without connectivity:
- **Cross-section data** — per-model forecast grids at each route point and pressure level — core dataset for cross-section viz and prompting
- **Route analyses** — per-point results (cloud layers, icing, wind components, convective risk, etc.)
- **Route advisories** — evaluated results (severity + detail per evaluator per model)
- **Elevation profile** — terrain along route
- **Digest summary** — synopsis text (synoptic + trend)
- **Route geometry** — waypoints, route points with coordinates, distances
- **Airport conditions** — departure/arrival weather (category, wind, visibility)

### On-demand via API (online only)

Heavier artifacts derived from full forecast data that don't need to live on device:
- **Skew-T** — *superseded*: the app does not fetch server PNGs. It renders natively (`SkewTDetailView` → RZSkewT) from `GET /api/flights/{id}/packs/{ts}/sounding-profile/{point}/{model}`, and an explicit pack download caches every `(point, model)` profile, so Skew-Ts work fully offline
- **Full sounding analysis** — detailed thermodynamic indices per waypoint
- **Model comparison details** — full multi-metric divergence table (scored variables in `analysis/comparison.py::DIVERGENCE_THRESHOLDS`)
- **LLM digest full text** — complete AI briefing narrative
- **GRAMET cross-section** — Autorouter PDF/PNG

"Tap to load" in UI. If offline: placeholder says "available when online." If viewed while connected (e.g., pre-flight Wi-Fi): cached locally.

### Payload size

Lightweight — derived analysis results, not raw forecasts. Cross-sections, route analyses, advisories, elevation, digest are a few hundred KB total. Multi-MB full sounding and raw forecasts stay on the server. Sync is fast even on cellular.

### Sync behavior

- **Pre-flight sync** — pull latest payload for selected flights (before engine start). *Shipped as* an explicit per-pack download button with byte-level progress: one bundled, gzipped request fetches every endpoint at once
- **Offline storage** — payload cached locally, full briefing viewable without connectivity
- **On-demand caching** — heavy artifacts fetched once and cached
- **Auto-sync** — *shipped narrower than written*: `AppSettingsStore.autoDownloadMode` (off / Wi-Fi only / Wi-Fi+cellular) downloads the latest pack for a today-or-later flight, but only while its briefing screen is open or on foreground. No `BGTaskScheduler`, no lead-time setting, no true background refresh
- **Push notifications** — shipped for refresh-complete deep-links (`Services/PushNotifications.swift`); server-side off by default. See [Briefing Refresh Notifications](./ios-app-briefing-notifications.md)
- **Briefing viewer** — native cross-section, advisories, route graph, digest — optimized for tablet and phone. Shipped

## 2. PIREP Reporting

PIREPs are **first-class** — they exist independently of flights. A pilot doesn't need a planned flight in WeatherBrief to file one. Just landed after pattern work? Open the app, tap "File PIREP", select the airport, report conditions. That PIREP is visible to every other user.

When filed during an active flight session, it's linked to the flight for verification — but the link is optional. The data model links a PIREP to a flight via a nullable `pack_id` (FK to the briefing pack it was filed against, `db/models.py::PirepRow`); standalone PIREPs leave it null. There is no `session_id` — the flight session is purely client-side (`FlightTrackingService`), not persisted on the server.

**Shipped reality (app side):** every filing entry point is anchored to a briefing — the toolbar button, the PIREPs-tab bar, the flight-list context menu. There is no "File PIREP" from a cold start, no airport picker. The app also sends **no** `pack_id`; the server infers the flight link from `observed_at` falling inside the flight window *and* the reporter being the flight owner (`storage/pireps.py`), so an out-of-window or subscriber report silently becomes a standalone community report. Server-side the community surface is complete — `GET /api/pireps` filters by `airport` / `bounds` / `hazard` / `min_severity` / altitude / aircraft type, and `web/pireps.html` consumes it — it is the *client* that hasn't caught up.

### Three ways to file

1. **Proactive prompting** (in-flight, active session) — app watches forecast, tracks position, prompts at transition points with pre-populated observations. Pilot confirms/edits/dismisses. **Not built.**
2. **Pilot-initiated in-flight** — "Report" button during active session. **Built**, but reachable from the briefing at any time (gated on `pirepCanPublish` only, no flight-window gate), and fields are pre-filled from GPS, not from forecast — they start unselected on purpose, to avoid confirmation bias.
3. **Standalone** (no flight required) — anytime, ground or in the pattern. Tap "File PIREP", pick airport or use current GPS location, report conditions. **Not built in the app** (the server accepts it; the web page offers it).

### Proactive Forecast-Driven Prompts (unbuilt — target design)

The app knows what weather is predicted at each route point (from synced cross-section data). As aircraft progresses along the route, current position/altitude is compared against forecast and prompts trigger when conditions are notable:

| Trigger | Prompt | Example |
|---------|--------|---------|
| Entering predicted icing zone | "Forecast shows light icing here at FL065. Confirm?" | Pre-selected: Light. Pilot confirms or adjusts |
| Entering predicted IMC | "Forecast shows BKN at 5500ft. Are you in cloud?" | Pre-selected: IMC, BKN |
| Predicted convective ahead | "Convective forecast 15nm ahead. Seeing anything?" | Pre-selected: TS nearby |
| Altitude change near predicted cloud base | "Climbing through predicted cloud base (6200ft)" | Pre-selected: entering cloud |
| Turbulence zone | "Moderate turbulence forecast this segment" | Pre-selected: Moderate |
| Periodic (no hazard) | "Conditions at FL065 near LFMD?" | Pre-selected: VMC, no icing, smooth |
| Significant wind shear predicted | "Wind shift forecast: 240/25 → 310/15" | Pre-selected: wind different |

**Key principle: prompts come pre-populated from forecast.** Pilot either:
- **Confirms** (single tap — common case if forecast is right)
- **Edits** (tap a different severity — 2 taps)
- **Denies** ("Not present" — single tap, equally valuable data for verification)
- **Dismisses** (swipe away — no observation recorded, pilot is busy)

Even a "confirm" is useful data; a "not present" is a high-value negative observation.

### Flight Session & Prompt Timing

Pilot explicitly starts a session via **"Start Flight"** button. Until started, no GPS tracking runs (planning/viewer mode). *Shipped*: `FlightTrackingService` + a Start/Stop toolbar button in `BriefingContainerView`, shown only inside the in-flight window; it projects live GPS onto the route and draws the aircraft on the cross-section and map. A lighter `requestOneShotLocation()` backs PIREP pre-fill when no track is running.

The prompt-governing rules below are **design, not code** — nothing fires prompts today:
- **Departure/arrival quiet zone** — no prompts within configurable radius (default 15nm) of origin/destination. Pilot is busy with ATC, checklists
- **Non-intrusive** — banner or side-card, never modal, never blocks the view
- **Rate-limited** — max one every 5 min (configurable). Multiple triggers → prioritize by severity
- **Smart suppression** — if pilot just reported icing 3 min ago, don't re-prompt unless conditions changed significantly
- **Dismissal is OK** — a dismissed prompt is not a negative report; pilot was busy. Recorded as "prompt dismissed" (for UX tuning, not sent as observation)
- **Audio/haptic cue** — optional gentle chime/haptic when prompt appears

### Pilot-Initiated PIREPs (Always Available)

Persistent "Report" button — flag something the app didn't prompt about, report unexpected conditions, or share what you're seeing. Full report card with all fields pre-populated from forecast at current position. The PIREP `source` field is a plain string validated server-side against `PIREP_SOURCES = ("manual", "inflight", "postflight")` (`db/models.py`); the active in-flight report card currently submits `"inflight"` (see `PirepViewModel`).

**Auto-populated from device sensors**: position (lat/lon) → nearest waypoint, GPS altitude (with pressure altitude correction option), timestamp, ground speed, track. *Shipped* for lat/lon + altitude (live track or one-shot fix).

**Fields** — the target was forecast pre-population; the shipped form deliberately leaves every hazard field **unselected** so the pilot isn't nudged toward confirming the forecast. Revisit that choice knowingly, not by accident:

| Category | Input | UI |
|----------|-------|----|
| **Flight rules** | VMC / IMC / marginal | 3-button toggle, color-coded |
| **Icing** | None / Trace / Light / Moderate / Severe | 5-button strip |
| **Turbulence** | None / Light / Moderate / Severe / Extreme | 5-button strip |
| **Cloud** | Clear / SCT / BKN / OVC | 4-button toggle |
| **Cloud base** | 100s of ft | Scroll wheel, pre-filled from forecast |
| **Precipitation** | None / Rain / Snow / Mixed / TS | Icon buttons |
| **Visibility** | >10km / 5–10 / 1–5 / <1 | 4-button range |
| **Wind** | As forecast / Stronger / Weaker / Different dir | Quick comparison |
| **Temperature** | OAT from avionics if available | Numeric, optional |
| **Free text** | Short note | Voice-to-text or keyboard, optional |

### Voice PIREP via Siri Shortcut (Phase 3a — unbuilt)

Hands-free, eyes on the sky. Aviation PIREP language is near-ideal for speech-to-structured-data — vocabulary is small, standardized, unambiguous.

**Trigger**: "Hey Siri, FlyFun PIREP", to be registered via `AppShortcutsProvider`. **Careful:** an `AppShortcutsProvider` *does* already exist (`AppIntents/FlyFunShortcuts.swift`), but it is the briefing-navigation surface — CheckBriefing, OpenBriefing, RefreshBriefing, AirportWeather, FlightsOverview ([App Intents](./ios-app-intents.md)). No PIREP intent is registered and the app links no Speech framework, so nothing below exists yet; building it means adding an intent to that provider, not creating one.

**Flow**:
1. Siri activates app in recording mode
2. `SFSpeechRecognizer` (on-device, offline since iOS 17) for real-time transcription
3. Pattern-based parser extracts structured fields
4. Report card appears pre-filled with voice-extracted values (highlighted)
5. Pilot confirms with one tap — or edits before saving

**Example utterances**:

| Spoken | Parsed |
|--------|--------|
| "flight level 120" / "FL120" | Altitude: FL120 |
| "eight thousand feet" / "8000'" | Altitude: 8000 ft |
| "IMC" / "in cloud" | Flight rules: IMC |
| "light icing" / "trace icing" / "no icing" | Icing |
| "moderate turbulence" / "light chop" / "smooth" | Turbulence |
| "tops at 8000" / "cloud tops eight thousand" | Cloud top: 8000 ft |
| "broken" / "overcast" / "scattered" | Cloud coverage |
| "visibility 3 miles" | Visibility: 1-5km |
| "moderate rain" / "snow" | Precipitation |

**Parser approach**: keyword/regex matching, not ML. Finite vocabulary. Examples:

```swift
// Altitude: "flight level 120", "FL065", "8000 feet"
/(?:flight level|FL)\s*(\d{2,3})/i
/(\d{3,5})\s*(?:feet|ft|foot|')/i

// Icing: "light icing", "no icing"
/(no|none|trace|light|moderate|severe)\s*icing/i

// Cloud tops/bases: "tops at 8000", "bases 4500"
/(tops?|bases?)\s*(?:at\s*)?(\d{3,5})/i
```

**Graceful fallback** — anything the parser doesn't extract stays at the forecast default. Pilot always sees the result before confirming. Voice is an input method, not auto-submit.

### Passive Data Collection

Beyond explicit observations, silently record data requiring no pilot input:
- **Track log** — GPS breadcrumbs at regular intervals
- **Altitude profile** — continuous, useful for detecting holds, diversions, altitude changes that may indicate weather avoidance
- **Route deviation** — significant deviation from planned route is itself a weather signal (likely circumnavigating something)
- **Timing** — actual vs planned departure, arrival, segment times

Low-cost to collect, valuable for analysis. A route deviation around a convective area is a strong signal even without explicit report.

**None of this is collected yet.** `FlightTrackingService` publishes the current fix and its route projection for live display only — no breadcrumb history is accumulated, persisted, or uploaded. Adding a track log means adding storage, not just switching something on.

## 3. Observation Timeline (unbuilt as a timeline)

Scrollable timeline of all observations made during the flight, shown on the route:
- In-flight — log of what you've reported, ability to amend
- Post-flight — review of the entire flight's weather experience
- Data source for forecast verification

What exists instead: the briefing's **PIREPs tab** (`PirepListView`), a flat list of that flight's reports with severity bars and hazard icons. No route positioning, no amend.

## 4. Community PIREP Feed (server-side only today)

All shared PIREPs form a community picture visible to every user, regardless of whether they have a flight planned.
- **PIREP map** — map view with recent PIREPs as severity-colored markers. Main screen. Filter by recency, distance, airport
- **Airport PIREPs** — tap any airport for recent PIREPs filed there/nearby. "What are conditions at EGTF right now?"
- **Route-aware** — when viewing a briefing, PIREPs near the route are highlighted automatically (like today's METAR/TAF corridor on the web)

The query layer for all three already exists (`api/pireps.py::query_pireps` — airport, bounds, hazard, severity, altitude, aircraft filters) and `web/pireps.html` renders it. The app consumes only the `flight_id` scoping.

## 5. Online Sharing (Starlink / Cellular)

When connectivity is available during an active flight, observations stream in real-time:
- **Outbound** — your observations pushed to server as created. *Shipped*, as plain POST on submit
- **Inbound** — you receive other pilots' recent observations along/near your route. **Not built**
- **Display** — other pilots' reports as markers on route, severity-coded. **Not built**
- **Graceful degradation** — connection drops → queue locally, sync when reconnected. App never blocks on network. *Shipped*: `PirepOfflineStore` actor (JSON queue in Documents, dedup by `client_uuid`) flushed via `POST /api/pireps/batch` on the next successful online submit

No WebSocket exists — the "real-time stream" is still request/response. Standalone PIREPs would sync immediately if online, or queue.

## 6. Post-Flight: Forecast Verification

After landing, compare briefing forecast vs actual observations:
- Side-by-side — predicted vs reported at each point
- Per-model accuracy — which model was closest to reality for each metric
- Aggregate scoring over multiple flights — personal track record of model reliability
- Highlight surprises — where forecast and reality diverged significantly

Future analysis layer — companion app's job is to **collect data**; verification happens server-side. Still unbuilt as written. Note that a *different* post-flight surface has shipped meanwhile: the pilot **debrief** form (`DebriefFormView`, `DebriefViewModel`, taxonomy from `/api/help/catalog`), which rates how well the briefing read rather than scoring models against observations — see [debrief.md](./debrief.md). Don't conflate the two.

## References

- [Overview](./ios-app-overview.md) — authoritative "what is actually built"
- [UI](./ios-app-ui.md) — screen layouts, report cards
- [Sync & Prompting](./ios-app-sync-prompting.md) — prompting engine spec
- [Roadmap](./ios-app-roadmap.md) — build order
- [App Intents](./ios-app-intents.md) — the shipped Siri/Shortcuts surface
- Key code paths: `app/flyfun-weather/flyfun-weather/{Services,ViewModels}/`, `src/weatherbrief/api/pireps.py`, `src/weatherbrief/db/models.py::PirepRow`
