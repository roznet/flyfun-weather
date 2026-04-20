# iOS App UI Design

> Cockpit constraints, screen layouts, report cards

## Cockpit Constraints

- **One-handed operation** — all primary actions reachable with right thumb on iPad in landscape
- **Large tap targets** — min 60pt buttons for condition reporting (FAA HIG recommends 44pt; we go larger for turbulence)
- **High contrast** — dark cockpit (night) + bright cockpit (day VFR). Always high contrast, no subtle grays
- **Minimal reading** — icons > text. Color-coded severity. Numbers only where essential
- **No typing required** — all reports tap-only. Free text is optional and supports voice-to-text
- **Non-blocking** — no modals requiring dismissal. Report sheet slides in, auto-dismisses after save. Never steals focus
- **Glanceable** — current conditions / last report always visible in a status bar without interaction

## Briefing Viewer (Phase 1, iPad Landscape)

Planning/viewer mode — default when not in an active flight session. What the pilot sees on the ground when reviewing.

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
│ [Advisories] [Cross-Section] [Map] [Digest]  [⚙ Settings]│  Tab bar
└──────────────────────────────────────────────────────────┘
```

On **iPhone**, becomes single-column scrollable view or swipeable tabs rather than full dashboard layout.

## In-Flight Mode (Phase 3, iPad Landscape)

"Start Flight" → map-dominant UI, status bar with live data, report/timeline panel.

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

## In-Flight Map (Phase 3)

Primary view while flying — dominates the screen. Pilot needs to see position relative to route and weather. Cross-section and briefing tabs are secondary in-flight (but primary on ground during planning). In Phase 1, route map is a planning view (no live position); becomes live in-flight map in Phase 3.

**Offline map tiles** (Phase 2) — cache tiles along route corridor before departure. Min: tiles at zoom levels covering route ±30nm at low-to-medium resolution. Pilot always sees a real map, not a blank grid, even offline. Prepared as part of pre-flight sync. Options: MapKit's `MKTileOverlay` with a custom local tile cache, or Apple's `DownloadedMap` API if available iOS 18+.

**Map layers in flight**:
- Route line with advisory coloring (same as web route map — metric-colored)
- Current position (prominent aircraft icon with heading)
- Observation pins (own + other pilots' if online)
- Forecast hazard zones (icing, convective) as shaded regions derived from cross-section data
- Waypoints with ETA and key conditions (e.g., "LFMD: MVFR, 15kt XW")

## Prompted Report Card (Phase 3b — slides in from side, non-modal)

Compact — focuses on the specific trigger, doesn't show every field. Shows what forecast predicted, confirms or edits, done.

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

## Full Report Sheet (Phase 3a — manual, slides up from bottom)

When pilot taps "Report" manually. All fields pre-populated from forecast at current position:

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

## References

- [Features](./ios-app-features.md) — PIREP modes, voice input, timeline, community feed
- [Sync & Prompting](./ios-app-sync-prompting.md) — prompting engine behavior backing the prompt card
