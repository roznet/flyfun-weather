# iOS App Battery Efficiency

> Energy review of the iOS companion app — findings, what was fixed, and what was deliberately deferred.
>
> **Not listed in `INDEX.md`** — this is a point-in-time review record plus the
> standing energy rules for the app, not a module design doc. Read it before
> touching `FlightTrackingService`, any poll loop, or the foreground-activation
> fan-out in `WeatherBriefApp`.

Review date: 2026-08. Related: [Architecture](./ios-app-architecture.md),
[Sync & Prompting](./ios-app-sync-prompting.md), [Overview](./ios-app-overview.md).

## Why this app is an unusual energy case

For most apps, battery is a background-work problem. Here it is almost entirely
a **foreground** problem, because of how the app is actually used: phone or iPad
mounted in a cockpit, screen on, GPS running, for the duration of a flight, often
with no way to charge. The app is also frequently the pilot's only moving-map
reference in that window, so an energy fix that silently degrades live position
is worse than the energy cost it saves.

That shapes every trade-off below:

- **The GPS is the app's single largest controllable draw.** The screen is
  larger still, but the app doesn't (and must not) control that.
- **Background work is not the problem** — the app does almost none, by design.
  See "Deliberate non-features".
- **Request count matters more than payload size.** Each radio wake carries a
  multi-second high-power tail regardless of how many bytes moved, so ten small
  polls cost far more than one larger fetch.

## Standing rules

1. **Never use `kCLLocationAccuracyBest`.** See finding 1. `NearestTenMeters` is
   the app's tracking budget; anything tighter needs a written justification.
2. **`distanceFilter` is not an energy control.** It gates delegate delivery, not
   the GPS duty cycle. Only `desiredAccuracy` changes what the radio does.
3. **Any auto-stop condition must be reachable without a location fix.** See
   finding 2 — this is the bug class that pinned the GPS on indefinitely.
4. **Every poll loop needs all four of:** backoff, a connectivity gate, a
   wall-clock bound, and a cancellation handle wired to view/scene lifecycle.
   `FlightListViewModel.pollActiveRefreshesLoop` is the reference implementation.
5. **Don't add `UIBackgroundModes` entries** without reading "Deliberate
   non-features" first. Adding `location` there would convert a foreground-only
   cost into a continuous one.
6. **Don't call `isIdleTimerDisabled`.** If pilots ask for a screen-stays-on
   behaviour it must be an explicit, off-by-default setting.

## What the app already did right

Recorded so nobody "optimizes" these away:

- **No background execution.** `SupportingFiles/Info.plist` declares only
  `remote-notification`. No `location` background mode, so location delivery is
  suspended the moment the app backgrounds; no `BGTaskScheduler`; no background
  `URLSession`. There is no idle drain to find.
- **Canvas redraw gating (#303).** `StaticCrossSectionScene` is `Equatable` with
  `.equatable()` applied at the call site, so a GPS tick or a scrub drag redraws
  only the O(1) cursor overlay instead of ~400 gradient fills.
  `RouteMapKitView`'s `routeSignature` does the same for map overlays. Both are
  load-bearing — the doc comments say so, and they are right.
- **`pollActiveRefreshesLoop`** already had backoff, a connectivity gate, and
  `scenePhase` cancellation.
- **Auto-download is Wi-Fi-gated** through `NetworkMonitor` (`NWPathMonitor`),
  honouring both `isExpensive` and `isConstrained` (Low Data Mode).
- **Help catalog is ETag/304-conditional.**
- No `repeatForever` animations, no `TimelineView(.animation)`, no
  `CADisplayLink`, no timers outside the poll loops.

## Findings

Ranked by expected energy impact. Findings 1, 2, 3 and 6 are fixed here; the
rest are deferred with reasons.

### 1. GPS ran at maximum accuracy — FIXED

`FlightTrackingService` requested `kCLLocationAccuracyBest`, which drives the GPS
chip in continuous maximum-rate mode. The route projection resolves position to
~0.1nm (185m) and the cross-track threshold is 10nm, so this was roughly an order
of magnitude more precision than any consumer of the data needed.

The `distanceFilter = 200` line carried the comment *"throttles at GPS level"* —
it does not. `distanceFilter` gates **delegate delivery**; the hardware duty
cycle follows `desiredAccuracy` alone. So the filter was saving projection
compute (already throttled to 5s independently) and SwiftUI ticks, but zero
radio energy. This is a common enough misconception that rule 2 above exists.

Fixed: `desiredAccuracy = kCLLocationAccuracyNearestTenMeters`, plus
`activityType = .airborne` so Core Location filters and duty-cycles for flight
rather than the default `.other` profile. The one-shot PIREP fix got the same
treatment — a position report does not need `Best` either.

### 2. Tracking could pin the GPS on indefinitely — FIXED

Both auto-stop conditions (past the flight window; landed near destination) were
evaluated **inside `projectLocation`**, which only runs when a fix is delivered.
With a 200m delivery filter, a parked aircraft delivers no fixes at all — so
after shutdown neither auto-stop could ever fire, `isTracking` stayed true, and
`startUpdatingLocation()` was never balanced by `stop()`. A diversion failed the
same way from the other direction: `distToDest < 5.0` never becomes true at an
alternate, so the landing rule was unreachable there too.

Nothing downstream rescued it: `BriefingContainerView` had no `.onDisappear` or
background `stop()`, so the state survived leaving the briefing screen, and
re-foregrounding resumed an unbounded track.

Fixed with a wall-clock watchdog (`runWatchdog`, 60s cadence) that evaluates the
flight-window rule and a new staleness rule independently of fix delivery. Two
design points worth keeping:

- **Suspension detection.** A watchdog tick that arrives much later than
  scheduled means the process was suspended in between — the app was
  backgrounded, where location delivery is suspended anyway. That silence says
  nothing about the aircraft, so the fix clock is rolled forward instead of being
  read as a landing. Without this, backgrounding mid-flight for a few minutes and
  returning would kill the track.
- **15-minute staleness threshold.** A moving aircraft produces a fix every few
  seconds through a 200m filter; fifteen minutes of silence means it moved less
  than 200m in that time. Accepted trade-off: an aircraft whose GPS loses signal
  for 15 continuous minutes in flight will also be stopped. That is defensible
  because the on-screen position has *already* been frozen for a quarter of an
  hour at that point — the display was lying before the watchdog acted — and
  `isTracking` flipping false restores the Start button. Known residual: a
  session that is repeatedly backgrounded for 60–120s at a stretch can accumulate
  staleness across ticks that each look normal, and eventually stop. Acceptable;
  restarting is one tap.

The near-destination landing rule was deliberately left as-is rather than
loosened to a speed-only test — a speed-only rule would stop a track during a
hold or slow flight. The watchdog covers diversions instead.

### 3. `pollRefreshStatus` was the one undisciplined loop — FIXED

It ran `for _ in 0..<100 { sleep 3s }` — a flat 3-second poll for up to five
minutes, with no backoff, no connectivity gate, and no `scenePhase` gate, firing
on every briefing open where a refresh was active. Up to 100 round trips, each
its own radio wake with its own tail. The correct pattern already existed forty
lines away in `FlightListViewModel`.

Fixed: 3s → ×1.5 → 15s cap backoff, an offline gate (back off to 30s rather than
waking the radio for a call that cannot succeed), a **wall-clock** 10-minute
bound rather than an iteration count (with backoff, iterations no longer map to a
predictable duration), and a stored cancellation handle.

Three consequences that came with it:

- **`checkActiveRefresh` is now safe to call repeatedly** and is called on
  foreground, which re-arms the follow cancelled on the way out *and* picks up a
  refresh started elsewhere while the app was away.
- **`isStreamingRefresh` was added.** A foreground `checkActiveRefresh` must not
  start a second, polled follow while this device already owns an SSE stream for
  the same run. `refreshState.isRefreshing` cannot express this: it is also true
  while merely following someone else's refresh, which is exactly the case
  foregrounding needs to re-arm.
- **The poll is now an unstructured `Task`**, so cancelling the enclosing
  `.task` no longer reaches it. `.onDisappear` cancels it explicitly. This is
  easy to get wrong — the previous code was implicitly cancelled by SwiftUI, and
  moving it to a stored handle silently removed that.

**Not** changed: `pollTimeOptions` is still left running on background. It is
already backoff-capped and hard-bounded, and it has no resume trigger — it is
armed by `loadPackData`, and a foreground `syncLatestPack` no-ops when the pack
is unchanged, so cancelling it would strand a running scan on "Scenarios
running…" forever. Giving it a resume path is a prerequisite for cancelling it.

### 6. Per-fix `.info` logging — FIXED

`projectLocation` logged at `.info` on every projection — about 720 persisted
unified-log writes per flight hour. Demoted to `.debug`. The one-off "First
location" line stays at `.info`: it is the line that says tracking acquired.

### 4. Seven independent requests on every foreground — DEFERRED

Every `.active` transition fires `refreshUserPreferences` →
`reconcilePushAuthorization`, `refreshHelpCatalog`, `pruneStaleCache`,
`reconcileBadge`, `syncPendingPireps` from `WeatherBriefApp`, plus
`FlightListView` adds `loadFlights()` and restarts its poll, plus an open
`BriefingContainerView` adds `syncLatestPack()` (and now `checkActiveRefresh`).
A two-second glance at the app costs all of them.

They run concurrently, so they largely share a single radio wake — that is what
keeps this from being severe, and why it is deferred rather than fixed here. Two
improvements, in value order:

1. **Debounce the bundle** — skip the whole set when it last ran under 30–60s
   ago. App-switching back and forth currently re-runs everything each time.
   Small, local, no server change.
2. **Coalesce server-side** — a single `/api/sync` returning badge + prefs +
   flight summaries turns seven requests into one or two. Biggest structural win
   available, but it is a server change with a client migration.

### 5. `OneShotLocator` fires an unrequested fix — DEFERRED

`ForecastMapView+Support.swift`'s `locationManagerDidChangeAuthorization` calls
`requestLocation()` unconditionally. Core Location invokes that delegate once
immediately after `delegate` is set in `init`, so merely instantiating the
`@State` object — i.e. opening the forecast map — triggers a fix even when the
user never taps Locate. It needs the same `requested` flag that
`FlightTrackingService.oneShotActive` already has.

Deferred because it runs at `kCLLocationAccuracyKilometer`, which is derived from
Wi-Fi/cell rather than GPS and is close to free. Worth fixing next time that file
is open.

## Deliberate non-features

Things that look like battery wins and are not, for this app:

| Not doing | Why |
|---|---|
| `BGTaskScheduler` / background app refresh | Would **cost** battery, not save it. The app has no need to be current while closed — pushes already deep-link the pilot in. If ever added, use `BGAppRefreshTask` over `BGProcessingTask` and set `requiresExternalPower`. |
| Significant-location-change / region monitoring / deferred updates | Route projection needs continuous position. No coarse mode satisfies the feature. |
| `location` in `UIBackgroundModes` | Converts a foreground-only cost into a continuous one. The app has no in-background location feature and should not acquire one casually. |
| `allowsExpensiveNetworkAccess` / `allowsConstrainedNetworkAccess` on the session | Auto-download already gates on `NetworkMonitor.isOnWiFi` in code, which is equivalent and far more legible at the call site. |
| OLED-dark cross-section palette | `CrossSectionThemeID` already offers dark variants, but the cross-section is dense coloured data — near-zero true-black area, so the OLED saving is negligible and legibility would pay for it. |

## Not yet done, worth doing

### Low Power Mode awareness — the biggest remaining easy win

Nothing in the app reads `ProcessInfo.processInfo.isLowPowerModeEnabled` or
observes `NSProcessInfoPowerStateDidChange`. This is the standard iOS lever the
app is missing outright. Under LPM the app should: suspend the 5s active-refresh
poll, skip auto-download, and drop tracking accuracy a further notch.
`NetworkMonitor` is the natural place to hang it — it already publishes
`isConstrained` for Low Data Mode, and an LPM flag fits the same shape.

### MetricKit

`MXMetricManager` would give `MXLocationActivityMetric` — real-user time spent at
each accuracy tier — and `MXCellularConditionMetric`, time on poor signal (the
most expensive radio state). For an app whose users hold the device as their only
nav reference, this turns finding 1 from an estimate into a measurement.

### Background `URLSession` for pack downloads — scoped, not small

Handing pack downloads to `URLSessionConfiguration.background(withIdentifier:)`
with `isDiscretionary = true` would let iOS schedule the transfer when the device
is on Wi-Fi *and* charging — the cheapest possible bytes — and would
simultaneously fix the standing UX limitation that a download dies when the
pilot navigates away from the briefing screen.

It is a genuine win, but it is **not a small change**, because the current
implementation is structurally incompatible with a background session:

1. **Background sessions do not support `dataTask`** — only download and upload
   tasks. `APIClient.requestDataStreaming` accumulates bytes in memory via
   `URLSessionDataDelegate` and returns `Data`. It would have to become a
   download-to-file, with `downloadPack` parsing from disk. That is a rewrite of
   `StreamingDownloadDelegate`, not an edit.
2. **No async/await, no completion handlers.** Everything is delegate-driven, and
   the delegate must be reconstructible at launch: iOS relaunches the app into
   the background to deliver `didFinishEventsForBackgroundURLSession`. That means
   a singleton coordinator created early in launch, plus
   `application(_:handleEventsForBackgroundURLSession:completionHandler:)` in
   `AppDelegate` storing and later firing the handler.
3. **Task→(flight, timestamp) mapping must be persisted.** The process can be
   terminated between task start and completion. `taskDescription` survives and
   covers most of it, but the cache-index write on completion needs the rest of
   the metadata (`flightTitle`, `assessment`, `departureTime`, `packMeta`) that
   `downloadPack` currently closes over.
4. **Progress semantics change.** The banner currently shows *uncompressed* bytes
   from the `X-Uncompressed-Length` header; a download task reports compressed
   transfer bytes. Reconcilable — arguably compressed is the more honest transfer
   bar — but it is a visible UI change, not a silent one.
5. **Two paths, not one.** `isDiscretionary` means iOS may defer for hours, which
   is right for auto-download and wrong for a manual "Download" tap where the
   pilot is waiting. The manual path must stay immediate, so the session choice
   becomes a parameter and both paths need to converge on the same cache-write
   and progress reporting.
6. **Effectively untestable in CI.** The riskiest behaviour — relaunch into the
   background — cannot be exercised in unit tests and is awkward on the
   simulator.

Realistically 300–500 lines across a new coordinator, `AppDelegate` plumbing, a
persisted task map, and a second progress path. **Track as its own issue**, and
if taken, phase it: (a) convert `requestDataStreaming` to a download-task shape
on the existing default session, keeping behaviour identical and provable; then
(b) introduce the background session behind the auto-download path only, leaving
the manual tap on the foreground path.

## Measuring

- **Instruments** — Energy Log and the Location Energy template, on device. The
  simulator reports nothing meaningful for location power.
- **Xcode Organizer → Metrics** — per-release battery and location data from real
  installs. The before/after for findings 1 and 2 should be visible here.
- **MetricKit** — see above; not yet wired up.
