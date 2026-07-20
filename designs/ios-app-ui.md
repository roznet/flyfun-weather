# iOS App UI Design

> Cockpit constraints, screen layouts, report cards

**Status (2026-05): mixed design + as-built.** The cockpit constraints and the
Briefing Viewer layout are the design intent that shipped (in tabbed form — see
below). The "In-Flight Mode", "In-Flight Map", and the two report-card mockups
(Prompted Report Card, Full Report Sheet) are the *original Phase 3 vision* and
do NOT match what was built. The shipped PIREP UI is a single manual reporting
sheet — see [As-built PIREP sheet](#as-built-pirep-reporting-sheet) at the end.
Cross-check `ios-app-overview.md` ("Current Implementation Status") before
treating any mockup here as current code.

## Cockpit Constraints

- **One-handed operation** — all primary actions reachable with right thumb on iPad in landscape
- **Large tap targets** — min 60pt buttons for condition reporting (FAA HIG recommends 44pt; we go larger for turbulence)
- **High contrast** — dark cockpit (night) + bright cockpit (day VFR). Always high contrast, no subtle grays
- **Minimal reading** — icons > text. Color-coded severity. Numbers only where essential
- **No typing required** — all reports tap-only. Free text is optional and supports voice-to-text
- **Non-blocking** — no modals requiring dismissal. Report sheet slides in, auto-dismisses after save. Never steals focus
- **Glanceable** — current conditions / last report always visible in a status bar without interaction

## Briefing Viewer (Phase 1, shipped)

Planning/viewer mode — default when not in an active flight session. What the pilot sees on the ground when reviewing. **Shipped as a `TabView`** (`BriefingContentView` inside `BriefingContainerView`), not the single-screen dashboard drawn below — the mockup shows the conceptual content, the real app puts each block on its own tab. iPad uses `NavigationSplitView` (flight-list sidebar + briefing detail).

```
┌──────────────────────────────────────────────────────────┐
│  ◀ Flights   LFAT → LFMD  ·  Mar 15  ·  06:00 UTC       │  Nav bar
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ Assessment ─────────────────────────────────────┐    │
│  │  🟡 AMBER — Moderate icing forecast FL060-FL080  │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Advisories ─────────────────────────────────────┐    │
│  │  ❄️ Icing         🟡 AMBER  GFS  │  🟡 AMBER  ICON │    │
│  │  ☁️ Cloud Base     🟢 GREEN  GFS  │  🟢 GREEN  ICON │    │
│  │  💨 Turbulence     🟢 GREEN  GFS  │  🟡 AMBER  ICON │    │
│  │  🛬 Crosswind      🟢 GREEN  GFS  │  🟢 GREEN  ICON │    │
│  │  ...expandable per evaluator...                   │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Cross-Section ──────────────────────────────────┐    │
│  │  [Model: GFS ▼]  [Layers: Cloud | Icing | Wind] │    │
│  │                                                   │    │
│  │  FL100 ─┬─────────────────────────────────────── │    │
│  │         │  ░░░░░░▓▓▓▓▓▓▓▓░░░░░░                 │    │
│  │  FL080 ─┤  ░░░▓▓▓▓▓▓▓▓▓▓▓▓░░░░░                │    │
│  │         │  ░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░                 │    │
│  │  FL060 ─┤  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░                │    │
│  │         │  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░                 │    │
│  │  FL040 ─┤  ▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒▒░░                │    │
│  │         │▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲ terrain        │    │
│  │  GND  ──┴────┬────┬────┬────┬──── distance ───── │    │
│  │           LFAT  LFBE  LSGG  LFMD                 │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─ Route Graph ────────────────────────────────────┐    │
│  │  Headwind/Tailwind · Temperature · Humidity       │    │
│  │  ┄┄┄╱╲┄┄┄╱╲╲┄┄┄┄┄╱╲┄┄┄┄┄┄┄  (Swift Charts)    │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
├──────────────────────────────────────────────────────────┤
│ [Advisories] [Cross-Section] [Map] [Digest] [PIREPs]     │  Tab bar
└──────────────────────────────────────────────────────────┘
```

> **Tab reorganization (#310, current).** The mega-scroll "Brief" tab was split
> and the standalone Skew-T tab folded away. Tabs are now
> **Advisory · Discussion · Cross-Section · Map** (+ a gated **PIREPs** tab when
> `userPreferences.pirepCanView`). This is a partial, intentional reversal of the
> §4.1 "one narrative scroll" decision — the single scroll stays the iPhone
> *fallback shape*, but content is regrouped into purpose-built tabs. Settings is
> still reached from the flight-list ellipsis menu; the PIREP reporting sheet is
> a toolbar button (shown whenever `pirepCanPublish`, no flight-window gate), not
> a tab — plus a "Report a PIREP" action on the PIREPs tab and an "Add PIREP"
> flight-list context-menu item.

| Tab | Contents (code) |
|---|---|
| **Advisory** | `AdvisoryTabView` — accented hero (traffic-light + reason) → watch chips → responsive advisory grid (`LazyVGrid` adaptive ≥280pt; AMBER/RED as `AdvisoryCardView`s, all GREEN collapsed into `GreenAdvisoryStrip` pills) → `AirportConditionsView` → `AlternatesView` (D-2 inward only). Per-hazard digest narrative is attached to the matching advisory card, not Discussion. |
| **Discussion** | `DiscussionTabView` — synoptic overview text (`DigestResponse.synopsis`). v1 is synopsis-only; surface-pressure & front charts are a deferred fast-follow. |
| **Cross-Section** | `CrossSectionView` with the Skew-T (`SkewTTabView`, bounded height) folded **below it in one scroll**. The "Sounding ›" deep-link and `FocusIntent.target == .skewT` scroll to the embedded Skew-T instead of switching tabs. |
| **Map** | `RouteMapView` — `MKMapView`-backed (migrated off SwiftUI `Map` in #428) route map with the metric-colored route line, plus an **airport-forecast overlay** (#428): per-airport markers coloured by the same served forecast catalog as the full forecast map, with a control cluster — on/off toggle, independent metric picker, valid-time label, and an "open full forecast map" deep-link. Only a new day/hour slice fetches (`RouteForecastOverlayModel`); model/metric switches recolour client-side. |

**Chrome / space reclaim (#310 item 1).** The old standalone `BriefingHeaderView`
band is gone: route identity moved to `navigationTitle` + `.navigationSubtitle`
(iOS 26), and the freshness chip + pack-history (D-N) picker merged into one
`BriefingPackToolbar` menu in the nav-bar toolbar.

**Tab presentation is a native `TabView`** (`BriefingContentView`) on both idioms:
a bottom tab bar on **compact width (iPhone)**, a top tab bar on **regular width
(iPad)**. Both drive `viewModel.selectedTab`, so all deep-links (watch chips,
advisory detail → cross-section) behave identically. iPad keeps the
`NavigationSplitView` sidebar. (A custom iPad pill band, `BriefingTabBand`, was
removed in #437: pinned as a sibling above the tab content it composited zero
pixels when the reused split-view detail column re-presented — the same bug as
the scroll-spy bar below. The system-hosted `TabView` is immune.)

**Intra-tab scroll-spy (#310 item 5).** Multi-section tabs (Advisory; reusable on
Discussion/Cross-Section) wrap their scroll in `ScrollSpyScroll`, which shows a
`SectionSpyBar` of section pills as a **pinned `Section` header inside the
`ScrollView`** (#436 — a bar pinned *outside* the scroll drew zero pixels on
re-presentation in the reused split-view detail column), highlights the section
nearest the top, and jumps on tap. Sections register with `.spyAnchor(id)` (a
plain `.id`); the active section comes from `onScrollTargetVisibilityChange` and
taps drive `ScrollPosition.scrollTo(id:)` — native iOS 18 scroll APIs that
replaced a `GeometryReader`/`PreferenceKey`/coordinate-space offset reporter
(#437). The bar suppresses itself when there is only one section.

## In-Flight Mode (Phase 3 vision — NOT built as drawn)

Original concept: "Start Flight" → map-dominant UI, status bar with live data, report/timeline panel. **This dedicated full-screen flight-session layout was not built.** What actually shipped instead: GPS tracking (`FlightTrackingService`) runs during the flight window and is surfaced *inside the existing tabs* — a live aircraft icon (heading-rotated, opacity by position confidence) on the **Map** tab, plus a position marker on the cross-section. No separate session screen, no status bar with GS/altitude/timer. See `project_start_flight_feature.md` memory for the v1 scope that was actually implemented.

The mockup below is retained as the original design intent.

```
┌──────────────────────────────────────────────────────────┐
│ [Flight: LFAT→LFMD]  [▲ 6500ft]  [GS: 120kt]  [⏱ 1:23] │  Status bar
├────────────────────────────────┬─────────────────────────┤
│                                │                         │
│                                │   Last report: 3min ago │
│        Route Map               │   VMC                   │
│    (current position,          │   No icing              │
│     observations as pins,      │   Light turb            │
│     other pilots' reports)     │                         │
│                                │   ┌─────────────────┐   │
│                                │   │   NEW REPORT    │   │
│                                │   └─────────────────┘   │
│                                │                         │
│                                │   Timeline (scrollable) │
│                                │   12:34 VMC, no icing   │
│                                │   12:15 Light turb      │
│                                │   12:00 Session start   │
├────────────────────────────────┴─────────────────────────┤
│ [Briefing] [Map] [Timeline]                    [Settings]│  Tab bar
└──────────────────────────────────────────────────────────┘
```

## In-Flight Map (Phase 3 — partially shipped on the Map tab)

> The live overlay below ships on the regular Map tab (`RouteMapView`) when tracking is active; the full-screen "dominates the screen" treatment does not.

Original intent: primary view while flying — dominates the screen. Pilot needs to see position relative to route and weather. Cross-section and briefing tabs are secondary in-flight (but primary on ground during planning). In Phase 1, route map is a planning view (no live position); becomes live in-flight map in Phase 3.

**Offline map tiles** (Phase 2) — cache tiles along route corridor before departure. Min: tiles at zoom levels covering route ±30nm at low-to-medium resolution. Pilot always sees a real map, not a blank grid, even offline. Prepared as part of pre-flight sync. Options: MapKit's `MKTileOverlay` with a custom local tile cache, or Apple's `DownloadedMap` API if available iOS 18+.

**Map layers in flight**:
- Route line with advisory coloring (same as web route map — metric-colored)
- Current position (prominent aircraft icon with heading)
- Observation pins (own + other pilots' if online)
- Forecast hazard zones (icing, convective) as shaded regions derived from cross-section data
- Waypoints with ETA and key conditions (e.g., "LFMD: MVFR, 15kt XW")

## Prompted Report Card (Phase 3b vision — NOT built)

> **Not implemented.** There is no forecast-triggered prompt card, no pre-selection, and no `confirmed/denied/edited/dismissed` response model. `SubmitPirepRequest`/`PirepResponse` carry no `response` field. The shipped sheet deliberately starts every field **unselected** ("no confirmation bias" — see overview doc), the opposite of the pre-selection drawn here. Kept as original design intent.

Original concept: compact — focuses on the specific trigger, doesn't show every field. Shows what forecast predicted, confirms or edits, done.

```
┌─────────────────────────────────────────────┐
│  ❄️ Icing forecast at FL065                  │
│  12:47 UTC · near LFMD · GFS model          │
│                                              │
│  Icing  [None] [Trace] [·Light·] [Mod] [Sev]│
│                          ^^^^^^^^             │
│                       (pre-selected from      │
│                        forecast — highlighted) │
│                                              │
│  [ ✓ Confirm ]   [ ✕ Not present ]   [swipe→]│
└─────────────────────────────────────────────┘
```

- **Confirm** — saves `response = .confirmed`
- **Not present** — saves `response = .denied` (equally valuable)
- **Edit any button** — tapping different severity saves `response = .edited`
- **Swipe to dismiss** — no observation recorded, `response = .dismissed`

## Full Report Sheet (Phase 3a vision — superseded by the as-built sheet)

> The mockup below (flight-rules / visibility / precip / "All correct" shortcut, all pre-populated from forecast) was **not** built as drawn. The shipped sheet is documented in [As-built PIREP reporting sheet](#as-built-pirep-reporting-sheet). Kept for design intent.

Original concept: when pilot taps "Report" manually. All fields pre-populated from forecast at current position:

```
┌──────────────────────────────────────────────────────────┐
│  Report at 12:47 UTC  ·  N45.12 E6.34  ·  FL065         │
│  Forecast source: GFS                                    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  Flight Rules    [·VMC·]  [ MVFR ]  [ IMC ]              │
│                                                          │
│  Icing     [None] [Trace] [·Light·] [Mod]  [Sev]        │
│                                                          │
│  Turbulence [·None·] [Light] [Mod]  [Sev]  [Extr]       │
│                                                          │
│  Cloud     [ CLR ]  [ SCT ]  [·BKN·]  [ OVC ]           │
│                  Base: [  ▼ 4500 ft ▲  ]                 │
│                                                          │
│  Visibility [·>10·]  [ 5-10 ]  [ 1-5 ]  [ <1 ]          │
│                                                          │
│  Precip    [·—·]  [ 🌧 ]  [ 🌨 ]  [ Mix ]  [ ⛈ ]       │
│                                                          │
│  Wind vs forecast  [·✓ OK·]  [ ↑ Stronger ]  [ ↻ Diff ] │
│                                                          │
│  [🎤 Add note...]                                        │
│                                                          │
│          [ ✕ Cancel ]    [ ✓ All correct ]    [ ✓ Save ] │
└──────────────────────────────────────────────────────────┘
```

**"All correct"** button — if forecast is spot-on, one tap confirms everything. Lowest-effort path for the common case.

## As-built PIREP reporting sheet

`PirepReportingView` (driven by `PirepViewModel`), reachable whenever `pirepCanPublish` — from the briefing toolbar, the PIREPs-tab "Report a PIREP" action, or the flight-list "Add PIREP" context menu. **No flight-window gate.** One manual `Form` sheet, severity fields start **unselected**, GPS pre-fills only altitude/position via `FlightTrackingService` — the sheet requests a one-shot fix on appear (`requestOneShotLocation()`) so pre-fill works without an active track. Toolbar cancel button is labelled "Skip". `Form` sections, in order:

- **Altitude** — read-only GPS altitude row + editable "Reported altitude" (ft) text field (pre-filled from GPS on appear)
- **Icing** — severity picker (none/trace/light/moderate/severe); if non-none, an icing-**type** picker appears (rime/clear/mixed)
- **Turbulence** — severity picker (none/light/moderate/severe)
- **Cloud** — just an "In cloud?" Yes/No toggle
- **Optional** — cloud tops (ft MSL) with a tops-**basis** picker (crossed/estimated_above/below_min), ceiling (ft MSL), wind dir (°) + speed (kt), temperature (°C). All free-entry.
- **Remarks** — optional free-text field
- (No flight-rules, visibility, or precip fields; no "All correct" shortcut)
- **Submit** ("Submit Report" button) — state machine: idle/error → loading → loaded ("Saved offline — will sync when connected" when no connectivity) with "Submit Another" (resets form) / "Done". Offline submissions set `queuedOffline` and enqueue to `PirepOfflineStore` to flush on reconnect.

Severity color mapping (`severityPicker`): none→green, trace/light→yellow, moderate→orange, severe→red. This is the real cockpit color coding referenced by the constraints above.

## References

- [Features](./ios-app-features.md) — PIREP modes, voice input, timeline, community feed
- [Sync & Prompting](./ios-app-sync-prompting.md) — prompting engine behavior backing the prompt card
