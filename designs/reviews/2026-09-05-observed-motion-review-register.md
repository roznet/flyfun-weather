# Observed-motion historical review register

This is a historical register of findings raised during the independent task
reviews. It is not a list of current bugs. Fixed findings are retained to show
what was found and how it was dispositioned; the separate
[verification report](2026-09-05-observed-motion-verification.md) is the current
completion/evidence record. Line numbers below refer to the reviewed revision,
not necessarily current `HEAD`.

## Disposition ledger

All Task 1–7, 9 and 10 task gates were approved after the recorded fix/re-review
rounds. The Task 8 test/doc subgate at `08f7e699` is also approved. This archive
is a documentation extraction, not another code review; the final whole-branch
gate remains separate.

### Task 1 — contract and policy (`84c7c5ae`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | `_parse_utc` accepted date-only `Z` values and localized them by host timezone (`src/weatherbrief/models/observed_motion.py:43-55`). | Fixed in the two-round contract work (`dd106ee7..84c7c5ae`); strict re-review approved. |
| Important | CRS validation accepted malformed strings containing `+proj=aeqd` (`:151-168`). | Fixed; pyproj-compatible WGS84 AEQD validation added; re-review approved. |
| Important | Accepted pairs/patches could contradict policy diagnostics, lineage, NCC, IoU, residual and area bounds (`:412-431`, `:842-864`). | Fixed in contract validation round 1; re-review approved. |
| Important | `reference_frame_id` need not be the newest frame (`:711-729`, `:827-833`). | Fixed; exact latest-frame requirement added; re-review approved. |
| Important | Advertised projection times were not required to be absolute five-minute UTC ticks (`:812-817`, `:866-867`). | Fixed; tick validation added; re-review approved. |
| Important | Route/overlap records allowed contradictory relationship, closure, tangent, interval and rounding semantics (`:624-660`). | Fixed in contract validation round 1; re-review approved. |
| Important follow-up (same accepted-diagnostics finding) | A displacement exactly on the maximum search boundary was accepted. | Fixed in round 2 (`a757f27c..84c7c5ae`) by rejecting equality as well as excess; interior control retained; re-review approved. This is a follow-up to the accepted-pair/policy-diagnostics finding above, not a seventh original Task 1 finding. |
| Minor (deferred) | Reason codes were not constrained to lower_snake_case (`:69`). | Deferred for final triage; no policy change recorded. |
| Minor (deferred) | The large envelope validator could be split into named helpers (`:778-1001`). | Deferred for final triage; readability remains acceptable. |

Cross-task caveats were explicit: producer call sites, policy application to
real computation, and real CTTH/geolocation evidence were not established by
the DTO review.

### Task 2 — source history and geometry (`07f61d49`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | OPERA motion time used acquisition end rather than nominal target (`src/weatherbrief/observed/opera.py:166`, `src/weatherbrief/observed/motion/history.py:118`). | Fixed (`878a916a..07f61d49`) with separate `motion_valid_time`; re-review approved while legacy observation time remained unchanged. |
| Important | Valid netCDF variable-length string lightning times lost individual precision (`src/weatherbrief/observed/lightning.py:136`). | Fixed; per-element string decoding/parsing added; re-review approved. |
| Minor (deferred) | Synthetic registration test also set `reviewed=False` and `checks_passed=False`, so it did not isolate the synthetic barrier (`tests/observed/test_motion_geometry.py:88`). | Deferred final triage; production gate remained correct. |
| Minor (deferred) | Adjacent verification retained pre-existing NumPy/netCDF binary and fixture-shape warnings. | Visible environment/fixture cleanup item; not treated as a Task 2 regression. |

The review also recorded that real CTTH registration/readiness and downstream
freshness, omission and completeness consumption were separate gates. The
reported mixed-fixture collection issue was diagnosed in the ledger as pytest
duplicate collector-node behavior, not hidden as green evidence.

### Task 3 — tracking (`35c0fb4d`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | Inclusive 20% fractional lineage threshold could exclude a mathematically boundary-equal overlap through floating-point roundoff (`src/weatherbrief/observed/motion/tracking.py:264`, `:271`). | Fixed (`ac011c46..35c0fb4d`) with a narrowly scaled epsilon allowance, without changing the physical threshold; re-review approved. |
| Minor (deferred) | Adjacent validation carried known ABI/fixture/timezone warnings. | Deferred controller/final triage; focused tracking evidence was pristine. |

Source freshness, registration, Earth-relative conversion, payload limits and
downstream association/projection remained explicitly outside this review.

### Task 4 — payload and associations (`3ad0088f`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | Stale tracks could publish accepted motion and manufactured ticks could exceed source-supported expiry (`src/weatherbrief/observed/motion/payload.py:202-259`, `:512`). | Fixed in round 1; re-review approved. The controller decision records the normative rule: a tick may be supported by any eligible feature and envelope expiry is the maximum accepted end ([Decision 11](2026-09-05-observed-motion-decisions.md#decision-11)). |
| Important | Projected display geometry lacked full-domain/support-rim proof (`src/weatherbrief/observed/motion/payload.py:237`, `src/weatherbrief/observed/motion/geometry.py:128`). | Fixed in round 2; one-cell rim and support checks added; re-review approved. |
| Important | RATE scalar used the newest radar contour rather than the selected RATE observation time (`src/weatherbrief/observed/motion/payload.py:185`, `:278`). | Fixed in round 1; exact bracket-time translation and provenance added; re-review approved. |
| Important | Cross-source association used the wrong historical contour/windows and an overly broad nearby threshold (`src/weatherbrief/observed/motion/association.py:39-45`, `:110`). | Fixed in round 1; left-bracket contour, original acquisition windows and grid-diagonal threshold restored; re-review approved. |
| Important | Per-family 24-item truncation failed to fill unused capacity from the other family (`src/weatherbrief/observed/motion/payload.py:324`). | Fixed in round 1; deterministic fill to the total cap added; re-review approved. |
| Important | No process-wide nonblocking busy admission existed and bounded failure reasons collapsed to `runtime_error` (`src/weatherbrief/observed/motion/payload.py:453`, `:525`). | Fixed in round 1/2; guard and stable refusal classification added, including `invalid_route`; re-review approved. |
| Important | Aggregate route-row/interval/link caps reported refused, unenumerated work as complete zero (`src/weatherbrief/observed/motion/association.py:246`, `src/weatherbrief/observed/motion/payload.py:399`, `:423-425`, `:494`, `:524`). | Fixed in round 2; unknown/incomplete counts retained on early refusal; re-review approved. |
| Important | Lightning completeness summed absent/unevaluated per-feature counts to a known zero (`src/weatherbrief/observed/motion/payload.py:393`, `:424`). | Fixed in round 1; regional LI totals are kept separate from overlapping precise per-feature summaries ([Decision 12](2026-09-05-observed-motion-decisions.md#decision-12)); re-review approved. |
| Minor follow-up | Focused tests initially lacked uneven-family, domain-exit, concurrency, aggregate-cap, UTF-8-limit and null-lightning cases (`tests/observed/test_motion_association.py:134`, `tests/observed/test_motion_payload.py:292`). | Coverage was expanded with the fixes; any remaining breadth is a final-triage/documentation concern, not an open implementation finding. |

The original review suggestion about “earliest-family” tick bounds was corrected
by the controller and not promoted to policy. Regional lightning totals versus
overlapping per-feature summaries were a separate controller clarification, not
an original review recommendation to sum feature counts. The decisions record
above preserves both rationales.

### Task 5 — publication and refresh lifecycle (`95941574`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | Gated JSON/SSE refresh swallowed publication lifecycle errors as successful no-op/complete responses (`src/weatherbrief/api/packs.py:2222-2241`, `:2467-2482`). | Fixed (`b8d031a2..95941574`) with explicit JSON 409/SSE error and capability/no-store headers; re-review approved. |
| Important | Superseded realtime callers received current motion with stale local ordinary refresh fields (`src/weatherbrief/tasks/route_weather.py:861-883`, `src/weatherbrief/storage/observed_motion.py:305`). | Fixed; returned `RealtimeRefreshResult` is built from the current published snapshot; strengthened race coverage and re-review approval. |
| Minor follow-up (fixed) | Race test initially asserted only newer motion revision, not coherence of ordinary payload fields (`tests/test_api_observed_motion.py:303`). | Strengthened coverage was added with the fix; not deferred. |

Real accepted-result source-to-endpoint integration remained a later Task 8
gate, not a claim of this task review.

### Task 6 — web explorer (`c7be612c`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | Entrypoint could render retained ready projection as active after missing motion or refresh failure (`web/ts/briefing-main.ts:1981-1984`, `web/ts/observed-motion/state.ts:166-170`). | Fixed; state authority predicate now forces stored/hidden presentation; re-review approved. |
| Important | Direct `refreshBriefing()` path discarded motion capability (`web/ts/adapters/api-adapter.ts:387-391`). | Fixed by removing the obsolete unsupported raw transport; supported stream path retains capability; re-review approved. |
| Important | Nested projections lacked advertised-time, owning-feature, cutoff, accepted-motion/geolocation and expiry guards (`web/ts/observed-motion/types.ts:348-392`, `web/ts/observed-motion/map-layer.ts:102-110`). | Fixed in parser and renderer; re-review approved. |

Server publication/header behavior was explicitly outside this client review.

### Task 7 — native explorer/cache (`d793698c`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | Native rendering boundary omitted required contour `operator` and true-bearing validation (`Models/ObservedMotion.swift:363-366`, `:640-642`, `:678`). | Fixed; required operator/literal and bearing checks plus authored negative cases; static re-review approved. |
| Important | Un-tokened direct snapshot writer could recreate a deleted pack (`Services/BriefingCacheStore.swift:109`, `:123-133`, `:323`). | Fixed; direct writers now reject deleted packs unless explicitly authorized; static re-review approved. |
| Important | Converting advertised UTC strings through `Date` lost exact spelling (for example `.000Z`). | Fixed; original token retained for identity lookup; static re-review approved. |
| Minor follow-up (fixed) | Raw-boundary tests did not cover every required literal/range case (`app/flyfun-weather/flyfun-weatherTests/ObservedMotionTests.swift:77-184`). | Authored negative coverage was expanded with the fix; not deferred. |

Swift/Xcode/XCTest/XCUITest, simulator and device execution were deliberately
not performed; Python fixture checks are wire-parity evidence only.

### Task 9 — atomic publication primitive (`c733be60`)

| Severity | Historical finding | Disposition |
|---|---|---|
| Important | Same-revision JSON equality conflated booleans and numbers in preserved nested content (`src/weatherbrief/storage/observed_motion.py:319`, `:358`). | Fixed (`f168274e..c733be60`) with JSON-aware recursive comparison; re-review approved. |
| Minor (deferred) | Reader start barrier did not guarantee a read before writer completion (`tests/test_observed_motion_publication.py:210`, `:230`). | Deferred final reviewer triage; no production defect claimed. |

Task 5 retained ownership of auditing every writer/deleter and binding the first
snapshot to reserved route/timing identities.

### Task 10 — route/timing helpers (`ff55b964`)

No Critical, Important or Minor findings requiring change. The task gate was
approved. Registration/eligibility, shared deadline, aggregate caller caps and
real CTTH evidence remained caller/integration responsibilities.

### Task 8 — integrated fixtures and as-built documentation (`08f7e699`)

Spec compliant and quality approved; no Critical/Important findings. The retained
radar test uses a hand-derived approximately 12.959 kt eastward motion and the
real history/tracking/payload/publication/API/disk path. Shared literals are
Python-producer-validated and the TypeScript boundary is executed; native
execution is not claimed.

One Minor warning-evidence finding in the task report was addressed by recording
the exact known `starlette/testclient.py:53` AnyIO `BlockingPortal` alias
deprecation and its pre-motion baseline provenance in the verification record.
Broader implementation claims are supported by the respective component gates;
final suites/review and external validation are separate from this subgate.

## Pending gates and evidence boundaries

- The final whole-branch review remains separate from the approved task gates.
  The enabled source-to-endpoint check and isolated full suites now pass as
  recorded in the verification report.
- Actual CTTH registration with reviewed non-synthetic geolocation evidence and
  forecast-skill replay remain unestablished; synthetic fixtures do not prove
  predictive usefulness.
- Native Swift execution remains unexecuted.
- Swift tests were authored and statically reviewed, never executed here.
- Existing dependency/fixture warnings remain disclosed in the verification
  record; they are not silently reclassified as feature failures.
- The historical archive is included in the final whole-branch review package.
