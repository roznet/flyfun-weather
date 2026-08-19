# Timing scenario — iOS client port

> **ARCHIVED 2026-08-17.** The durable endpoint contract (the three
> `/time-options` routes with their 404/409/429 semantics) and the client poll
> contract now live in [`designs/ios-app-server-api.md`](../ios-app-server-api.md);
> the feature design is [`designs/timing-scenarios.md`](../timing-scenarios.md).

> **Status (2026-07-05): IMPLEMENTED (all 4 slices, iOS #357).** Client port
> landed — Flexibility control in the flight editor, DTO decode + poll loop on
> `BriefingViewModel`, the Timing Scenarios panel in the Advisory tab, and the
> confirm / "Check all models" / "Set as alternate" actions. Per-model dot
> *tooltips* (a hover-only web affordance) were intentionally dropped for v1 —
> the three-column dot table (Current / ECMWF-only / All-models) is rendered.
> The feature is fully built on backend + web (see
> `timing-scenario-plan.md`, all 4 slices MERGED in PR #341). iOS has **zero**
> timing code today. This is a pure client port: **no backend work** — every
> endpoint and DTO already exists and is stable. Do NOT add to
> `designs/INDEX.md` (plans are not MCP-discoverable by house rule).

## Goal

Bring the "is there a better departure time?" Timing-Flexibility feature to the
iOS app, at display parity with web. Posture is unchanged and inherited from the
mitigation framework: an **attention-director, never a verdict**.

## Locked decisions

- **Placement:** the Timing Scenarios panel is a **section inside the Advisory
  tab** (`AdvisoryTabView`), mirroring web's inline-in-briefing placement — *not*
  a new `BriefingTab` case.
- **Offline:** **online-only for v1.** Timing data is a separate endpoint, not
  part of the briefing payload, and is deliberately *not* added to the iOS
  offline-download bundle. The panel polls live and shows a placeholder when
  offline (`NetworkMonitor` already exists).
- **Flexibility control home:** the **flight editor** (`AddFlightView`), per
  flight — *not* global `SettingsView` (matches web `flight-detail-ui.ts`).

## Backend surface (already stable — reference only)

Endpoints (all under `/api/flights/{id}/packs/{ts}/…`, in `api/packs.py` — grep
`time-options`; the line numbers this plan originally quoted have all drifted):

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `…/time-options`         | Poll status + scan result. 404 when Flexibility=`none` & never scanned. Owner-only lazy-schedules if flexibility set but no status. |
| `POST` | `…/time-options/rescan`  | Re-queue scan (owner-only). `202`; 409 if Flexibility=`none`. |
| `POST` | `…/time-options/confirm` | On-tap multi-model check of one candidate. `202`; **429** if a confirm already runs for this pack; 409 unknown/already-confirmed. |

Flexibility mode lives on the flight: `CreateFlightRequest.flexibility` in
`api/flights.py` (`none|same_day|prev_day|next_day`), edit/`UpdateFlightRequest`
adds `alternate` (requires `alt_departure_time` else 422). `FlightResponse`
already returns `flexibility`; alternate value reuses `alt_departure_time`.

DTOs to mirror from `models/time_scan.py` (web mirror `web/ts/adapters/api-adapter.ts`):
`TimeScanStatus`, `TimeWindowScan`, `TimeScanBaseline`, `TimeScanWindow`,
`TimeCandidate`, `TimeConfirmation`, `ModelCoverage`.

State ladder: job `status` `pending→running→done|failed|skipped`; per-candidate
`confidence` `confirmed_in_window` (free) / `ecmwf_only` (provisional, shows the
"Check all models" affordance) / `confirmed` (user-tapped, may be a designed
downgrade). No hard rate limit exists yet (`count_user_time_scans` "neither
enforced yet") — the only throttle is the **one-confirm-at-a-time 429**.

## Slices

### Slice 1 — Model + Flexibility control
- Add `flexibility` + `alt_departure_time` to `Models/API/FlightResponse.swift`
  and `CreateFlightRequest.swift` (neither decoded today).
- Flexibility `Picker` section in `AddFlightView` (`none/same_day/prev_day/
  next_day`; `alternate` only in edit + requires the alt time).
- Net-new **alt-departure `DatePicker`** (iOS has no alt-departure concept).
- Decode `time_scan_used` / `time_scan_count` from `GET /usage`; first-time
  explainer sheet gated on `!time_scan_used`, re-openable via an (i) button.
  Copy = web's four beats (`web/ts/components/flexibility-explainer.ts`).

### Slice 2 — DTO decode + polling
- Swift Codable mirrors of the `time_scan.py` DTO family.
- `@Published timeOptions` on `BriefingViewModel` + a repo method hitting
  `GET …/time-options`.
- Poll loop mirroring `web/ts/store/briefing-store.ts`: backoff 3s → ×1.5 → cap
  15s, poll-until-terminal, 404 = no-data, tolerate 3 transient errors; start
  after pack load only when `flexibility ≠ none`; placeholder when offline.
  *As built:* the gate reads `pack?.flexibility ?? flight.effectiveFlexibility`
  (the pack meta carries it too, and the `Flight` object goes stale if
  Flexibility was edited after the briefing opened), and the loop also carries a
  hard iteration cap as a backstop against a job that never reaches a terminal
  status.

### Slice 3 — Timing Scenarios panel (section in Advisory tab)
- State ladder: hidden / running (spinner) / failed / skipped (reason copy) /
  results.
- Result rows: time + day suffix ("· next day"/"· previous day"), assessment
  pill, shift label, "★ your alternate" tag, improves/worsens diff, confidence
  line (`confirmed` up-or-down / `confirm_pending` spinner / `ecmwf_only` +
  button).
- Expandable per-advisory dot-table: Current vs ECMWF-only vs All-models, rows
  sorted worst-status first. (The open question here — does iOS's
  `AdvisoriesResponse` carry the per-advisory **per-model** dots the "Current"
  column needs — resolved yes: `AdvisoriesResponse.perModel`.)
- Footnotes: refused-hours, `past_clipped`, `horizon_clipped`.

### Slice 4 — Confirm / "Check all models" + Set-as-alternate
- `POST …/confirm`; mirror the server **429 one-at-a-time** by disabling all
  confirm buttons while any candidate is `confirm_pending`.
- "Check all models" on `ecmwf_only` rows.
- "Set as alternate" on same-day non-baseline rows → PATCH flight
  (`flexibility=alternate` + `alt_departure_time`) then `POST …/rescan`.

## Where it landed (iOS, `app/flyfun-weather/flyfun-weather/`)

- `Models/API/TimeOptionsResponse.swift` — the whole DTO family
  (`TimeOptionsResponse`, `TimeScanStatusDTO`, `TimeWindowScanDTO`,
  `TimeBaselineDTO`, `TimeWindowDTO`, `TimeCandidateDTO`, `TimeConfirmationDTO`,
  `TimeScanJobStatus`, `TimeConfidence`) plus a lenient candidate decoder so one
  malformed candidate can't nuke the panel.
- `ViewModels/BriefingViewModel.swift` — `timeOptions` / `timeOptionsOffline`,
  `pollTimeOptions`, `anyConfirmPending`, confirm/rescan actions.
- `Views/Briefing/TimingScenariosView.swift` — the panel; hosted from
  `AdvisoryTabView`.
- `Views/Flights/AddFlightView.swift` + `ViewModels/AddFlightViewModel.swift` +
  `Views/Flights/FlexibilityExplainer.swift` — the control and the first-time
  explainer (gated on `usageSummary().timeScanUsed`).
- `Services/BriefingRepository.swift` (+ fixture/caching repos) —
  `timeOptions` / `confirmTimeOption` / `rescanTimeOptions`.
- Tests: `flyfun-weatherTests/TimingScenariosTests.swift`.

## Out of scope for v1
- Offline caching of timing data (revisit if pilots want scenarios in flight).
- Any hard daily rate limit (backend hasn't enforced one either).
- iOS push/live channel — polling only.
