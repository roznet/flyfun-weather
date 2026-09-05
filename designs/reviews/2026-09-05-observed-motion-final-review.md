# Observed-motion final review and fix disposition

Date: 2026-09-05. Scope: whole branch from the actual upstream base
`b48d9a8ff5831ab44fc3e43253c808578c215277`, reviewed at `167f7c56`, followed by
the combined fix `57aa53fb`. This is the final software review record for the
experimental observed-motion increment. It does not claim native compilation,
real-source registration, forecast skill or operational readiness.

## Findings and dispositions

The independent whole-branch review found eight Important cross-boundary groups
and no Critical findings. The scoped re-review marked every group **ADDRESSED**
and found no new Critical or Important breakage.

| ID | Finding | Fix and verification |
|---|---|---|
| I1 | Full/legacy snapshot wrappers could recreate a deleted pack outside the publication generation fence. | Writers now establish lifecycle ownership before serialization/directory creation and use the locked atomic writer; failed first publication is cleaned up. Publication/deletion/delayed-write regressions pass. |
| I2 | Non-five-minute-aligned cutoffs skipped the immediately following supported UTC tick. | Projection enumeration starts at the strictly next absolute five-minute boundary, including seconds, minute and hour-rollover cases. |
| I3 | Multi-frame lightning evidence combined counts but advertised only the last source window. | Feature evidence merges enclosing compatible windows and frame references while retaining qualification reasons; regional counts remain separate. |
| I4 | Tolerant web parsing could turn malformed positive overlap into an evaluated empty result and expose accepted ground claims without registration/support. | Invalid overlap/route semantics fail closed; ground speed, route and dependent overlap require owning registration/support. Raw JSON and safe observed context remain available. |
| I5 | Native used a 15-minute freshness gate instead of the approved 20-minute current-evidence limit and hid observed-only contours in unavailable envelopes. | Native separates current freshness from the 15-minute projection horizon and renders suitable dated observed-only content without unauthorized projections/ground claims. Swift checks are authored/static only. |
| I6 | Browser BFCache restoration cleared the expiry timer without restarting it. | Expiry clock ownership is idempotent across `pagehide`/`pageshow`; repeated lifecycle browser coverage passes without timer network requests. |
| I7 | Known disconnect did not immediately revoke active prediction styling or fence late authority replies. | Web/native disconnect transitions to Stored analysis, retain dated raw state, fence late replies and require the bounded capability read after reconnect. |
| I8 | Web/native cards omitted absolute evaluated windows and numeric completeness, and conflated complete-empty with unavailable overlap. | Cards show dates/windows, overlap status/reasons, lightning reported/emitted/lower-bound scope and considered/emitted/omitted/unknown completeness. |

## Verification evidence

Controller verification after `57aa53fb`:

- Python: **6,136 passed, 20 skipped, 23 deselected, 854 warnings in 226.66s**;
  JUnit records 6,156 executed, zero failures/errors. The warnings are retained
  dependency/fixture/test-harness warnings; no blanket suppression was added.
- Web unit tests: **861 passed / 52 files**.
- Application TypeScript: `tsc --noEmit` exited 0 without diagnostics.
- Observed Chromium harness: **22 scenarios passed in 16.2s**.
- Final-fix focused evidence: 111 Python tests (three known fork warnings), 44
  targeted web tests, 12 motion browser scenarios, and Python validation of the
  authored native literals. No Swift/Xcode command ran.

All Python checks used the worktree environment, fresh temporary data/SQLite,
disabled dotenv and explicit `WB_OBSERVED_ENABLED=0`,
`WB_OBSERVED_MOTION_ENABLED=0`, `WB_OBSERVED_LIVE_TESTS=0` and
`WEATHERBRIEF_EVAL_LIVE=0`; provider/shared-data overrides were unset. Browser
checks used intercepted fixtures and the no-server harness. No provider request,
shared weather data, credential, dev server or production build was used.

## Remaining boundaries

- CTTH cloud motion, ground projection and quantitative radar/cloud association
  remain source-gated until real product/grid/decoder geolocation evidence is
  reviewed. Synthetic registration fixtures do not authorize production.
- No regional replay has established predictive usefulness, useful lead time,
  false-alarm/miss behavior or a safety threshold. Constant-motion vectors are
  experimental inspection aids, not forecasts, route-clearance decisions,
  thunderstorm diagnoses or vertical-clearance guidance.
- Mac/iOS compilation, Swift unit/UI tests, simulator/device execution, Safari
  validation and live realistic imagery checks remain deferred. Native code is
  statically reviewed only.
- Full raster-history playback, native raw-raster parity, a new route-distance
  time canvas, live-aircraft prediction and provider tracking products remain
  outside this increment.

See [the implementation/verification record](2026-09-05-observed-motion-verification.md),
[the historical review register](2026-09-05-observed-motion-review-register.md),
and [the controller decisions](2026-09-05-observed-motion-decisions.md) for
component gates, deferred minor triage and rationale/consequence decisions.
