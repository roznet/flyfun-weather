# Observed-motion implementation and verification record

Status: **backend/web/native software implemented; isolated software suites and
independent whole-branch review passed**. Production prediction remains disabled. This
does not complete live cloud-motion readiness or establish predictive usefulness.
User approved the written specification and explicitly requested implementation.
The [implementation plan](../../docs/superpowers/plans/2026-09-05-observed-motion.md)
and [normative contract](../../docs/superpowers/specs/2026-09-05-observed-motion-contract.md)
govern this increment. Published PR #600 still contains observation corrections
until a later verified update; no prediction deployment is authorized.

## Current integrated verification

At integration commit `08f7e699`, with documentation archive `ff0c4aea`:

- The controller's focused Python run passed **11 tests**, with one known
  Starlette deprecation warning, in 4.03s. The real enabled retained OPERA
  DBZH/RATE test exercises history, matching, payload construction, publication,
  direct-refresh API and disk. A hand-derived eastward speed of approximately
  12.959 kt is checked independently of the producer calculation.
- Shared web and authored native fixture literals pass Python's producer schema.
  The TypeScript parser boundary passed **17 tests / 1 file**. This does not
  execute Swift.
- The final full web unit suite passed **844 tests / 52 files in 938ms**.
- Application TypeScript (`tsc --noEmit`) exited 0 without diagnostics.
- The no-server Chromium harness passed **18 scenarios in 13.5s**, including
  motion entry/selection, expiry, capability revocation, refresh failures and
  narrow layout. Web/type/browser outputs were pristine.
- Full isolated Python: **6,117 passed, 20 skipped, 23 deselected, 854 warnings in
  225.96s**, exit 0. JUnit independently records 6,137 executed, 20 skipped,
  zero errors and zero failures. No task test file was excluded.
- Independent integration test/doc review at `08f7e699`: spec compliant, quality
  approved, no Critical/Important findings. Its single Minor warning-evidence
  finding is resolved by the exact disclosure below. The final whole-branch
  review was completed below; the verified branch and updated description are now
  published to draft PR #600 at the head recorded below.

Full Python command, from the worktree, using a fresh temporary data/SQLite root:

```bash
env -u AIRPORTS_DB -u ECMWF_GRIB_DIR -u ECMWF_PROD_GRIB_DIR \
  -u ECMWF_TEST_GRIB_DIR -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
  PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  WB_OBSERVED_ENABLED=0 WB_OBSERVED_MOTION_ENABLED=0 \
  WB_OBSERVED_LIVE_TESTS=0 WEATHERBRIEF_EVAL_LIVE=0 \
  DATA_DIR=/tmp/flyfun-motion-final.0qT3w7/data \
  DATABASE_URL=sqlite:////tmp/flyfun-motion-final.0qT3w7/test.sqlite \
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  venv/bin/python -m pytest -q -p no:cacheprovider tests --tb=short \
  --junitxml=.superpowers/sdd/2026-09-05-observed-motion/final-python.xml
```

Skipped tests require missing evaluation corpora, ECMWF/HRRR samples or mirrors,
or the navigation DB. No shared data was substituted to make them execute.
The 23 deselected tests follow the existing suite selection; this is not live
provider, real-source replay or native verification.

The 854 warnings remain visible: netCDF/NumPy ABI import and fixture-shape
deprecations, legacy LI timezone conversion, Starlette/HTTPX deprecations,
deliberately short JWT test keys, the srtm escape-sequence warning, MetPy
interpolation/Matplotlib deprecations, and three publication-test warnings about
forking a multithreaded process. No production deadlock was observed. These are
retained for final triage, not a warning-free claim or blanket waiver of future
warnings. The focused integration warning is specifically
`starlette/testclient.py:53`, `DeprecationWarning: The anyio.abc.BlockingPortal
alias is deprecated, use anyio.from_thread.BlockingPortal instead.` It also
appeared in the pre-motion baseline; Task8 changes no production code.

Commands for the full web checks, from `web/`:

```bash
env PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  ./node_modules/.bin/vitest run
env PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  ./node_modules/.bin/tsc --noEmit
env -u NO_COLOR \
  PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  OBSERVED_BROWSER_CHANNEL=chromium \
  ./node_modules/.bin/playwright test -c playwright.observed.config.ts
```

The [historical review register](2026-09-05-observed-motion-review-register.md)
preserves the original findings, fix/re-review closures and deferred minor triage.

## Final whole-branch review and fix wave

The independent whole-branch review of `167f7c56` against the actual upstream
base found **8 Important groups and no Critical findings**. The complete report
is retained at [final-review.md](../../.superpowers/sdd/2026-09-05-observed-motion/final-review.md)
for the plan workspace. Findings covered deleted-pack writer fencing, off-grid
projection ticks, multi-window lightning scope, web malformed-result and
registration gates, native freshness/observed-only rendering, BFCache expiry,
known-disconnect authority and visible evidence/completeness scope.

The combined fix is committed at `57aa53fb`; its detailed I1–I8 dispositions and
focused evidence are in [final-fix-report.md](../../.superpowers/sdd/2026-09-05-observed-motion/final-fix-report.md).
The controller's full checks on the corrected tree are **6,136 Python passed,
20 skipped, 23 deselected, 854 warnings in 226.66s** (JUnit 6,156 executed,
zero failures/errors), **861 web unit tests / 52 files**, clean application
TypeScript, and **22 Chromium scenarios in 16.2s**. Focused fix evidence is 111
Python tests (three known fork warnings), 44 targeted web tests, 12 motion
browser scenarios, and static native-fixture validation. These outputs are not
native compilation or real-source/replay evidence.

The scoped re-review of `167f7c56..57aa53fb` closed all eight findings as
**ADDRESSED**, with no new Critical/Important breakage. Native compilation and
real-source/replay evidence remain explicitly unexecuted; the branch is ready
for the draft-PR update, not for deployment or operational use. PR #600 remains
open and draft after the verified branch update.
Historical stage entries below retain their at-the-time status and are not the
current publication or completion claim.

## Baseline and environment

Implementation base: `79b88b37`; plan/approval record `b67fa07d` and publication-task
split `59e93941`. Existing linked worktree:
`/home/qian/flyfun_weather/observed-corrections`, branch `codex/observed-corrections`.

Baseline, before production motion changes:

```bash
env PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 WB_OBSERVED_LIVE_TESTS=0 \
  venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/observed tests/test_api_observed.py
```

Result: **190 passed, 6 deselected**, 163 warnings in 9.05 seconds. Warnings were
the pre-existing netCDF/NumPy ABI import warning, NumPy 2.5 shape-setting
deprecations in CTTH fixtures, and Starlette/AnyIO deprecation. This is not a
warning-free run and does not verify any new prediction behavior.

From `web/`:

```bash
env PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  npm test -- --run tests/unit/observed-conditions.test.ts \
  tests/unit/observed-map-lifecycle.test.ts
```

Result: **75 passed / 2 files**. Node v22.23.1. Own worktree Python resolves
`src/weatherbrief` in this checkout; SciPy 1.18.1 and Shapely 2.1.2 verified after
the required geometry dependency installation. Direct dependency declarations
belong to the contract task.

Setup correction: one worker mistakenly created an additional environment outside
the worktree. Its use was stopped; no verification from that environment counts.
The confirmed worker-created directory was moved, not deleted, to
`/tmp/flyfun-motion-env.HPeBX8/unused-task1-venv` for recoverable cleanup. All workers
were redirected to this worktree's existing `venv`.

## Implementation gates

| Component | Current state |
|---|---|
| Strict shared producer contract and policy | Six Important findings fixed across two rounds; independent scoped re-reviews approved. |
| Cutoff-safe history, bounded source geometry, lightning precision | Implemented; two Important findings fixed and independently re-reviewed. |
| Independent feature tracking | Implemented; fractional-lineage boundary fixed at `35c0fb4d` and independently re-reviewed. |
| Route closure and continuous planned overlap | Pure solver and payload integration implemented and independently approved. |
| Time-compatible evidence and bounded payload | Implemented and independently approved after fix round 2 at `3ad0088f`; all eight original Important findings closed. |
| Atomic publication primitive | Implemented, equality bug fixed and independently re-reviewed; server integration independently approved. |
| Full/realtime/legacy/SSE/snapshot/bundle integration | Implemented and independently approved after fix round 1 at `95941574`; fresh 274-test regression passes. |
| Web explorer and capability/expiry lifecycle | Implemented and independently approved after fixes at `c7be612c`; final server integration remains separate. |
| Native explorer and raw-cache/capability lifecycle | Implemented and independently statically approved after fix round 1 at `d793698c`. No Mac execution. |
| Integrated tests and independent final review | Real enabled source-to-endpoint/shared fixtures independently approved; full isolated Python/web/type/browser checks passed; whole-branch review and scoped fix re-review clean. |

Task-level red/green reports and scoped review packages live in the plan-scoped
work ledger during execution. Final commands/counts and actionable review findings
will be recorded here as each gate is actually verified; a worker's partial test
run is not the completion of its task or the feature.

Controller-verified local stages (not full-feature verification):

| Commit / stage | Fresh command suffix after the Python environment prefix above | Result |
|---|---|---|
| `dd106ee7` contract/policy | `venv/bin/python -m pytest -q -p no:cacheprovider -W error tests/observed/test_motion_contract.py` | 49 passed in 1.29s, no warnings. |
| `878a916a` source inputs/geometry | `venv/bin/python -m pytest -q -p no:cacheprovider tests/observed/test_motion_history.py tests/observed/test_motion_geometry.py tests/observed/test_motion_readers.py` | 39 passed in 2.05s, one existing NumPy binary warning. |
| `f168274e` publication primitive | `venv/bin/python -m pytest --noconftest -p no:cacheprovider tests/test_observed_motion_publication.py -q` | 59 passed in 0.55s, no warnings. |
| `c733be60` publication equality fix | Same publication-only invocation | 67 passed in 0.61s, no warnings. |
| `07f61d49` nominal/precision fix | Same three focused source files listed above | 46 passed in 2.08s, two warnings. |
| `a757f27c` contract validation fix | Contract-only invocation above, additionally `-p no:asyncio` | 89 passed in 1.39s, warning-strict. |
| `84c7c5ae` exact search-boundary refusal | Same warning-strict contract invocation | 92 passed in 1.28s, no warnings. |
| Current route/geometry integration | `venv/bin/python -m pytest -q -p no:cacheprovider tests/observed/test_motion_route.py tests/observed/test_motion_geometry.py tests/test_route_points.py tests/test_model_region.py --tb=short` | 101 passed in 2.10s, one known warning. |
| `ac011c46` tracking integration | `venv/bin/python -m pytest -q -p no:cacheprovider tests/observed/test_motion_tracking.py tests/observed/test_motion_geometry.py tests/observed/test_motion_history.py tests/observed/test_motion_readers.py tests/observed/test_motion_contract.py tests/observed/test_motion_route.py` | 220 passed in 3.95s, two known reader/environment warnings. |
| `35c0fb4d` fractional-lineage fix | `venv/bin/python -m pytest -q -p no:cacheprovider tests/observed/test_motion_tracking.py` | 55 passed in 3.03s, no warnings. |

These commits remain local. No server writer, web or native prediction integration
is claimed from these stage tests.

Tracking now labels the full eight-connected field before candidate limits, uses
fixed-support two-patch forward/reverse matching and retains full relevant lineage,
including small/unselected competitors. It fits cumulative matched displacement
against actual elapsed time on a clean newest-ending chain, not centroid movement.
Observed-only failure results carry explicit known/unknown count metadata. The
44 focused tracking cases are included in the 220-test integration run above;
independent review remains a separate gate.

The tracking reviewer reproduced an exact-threshold lineage defect: shifting a
cell by `0.8` cells gives a floating-point overlap of `0.199999999999999956`,
which a bare `>= 0.20` comparison excludes. If it is a second parent/child, that
can incorrectly turn an ambiguous match into a unique one. The fix preserves the
physical 20% rule with a numerical allowance of 64 float64 epsilons times the
smaller footprint's cell count. Literal positive/negative fractional shifts at
1/64/512-cell scales, a second small/unselected competitor and genuinely
below-threshold controls cover it. Independent scoped re-review found I1 addressed
with no new breakage.

Fresh controller web verification for `81653e16`, from `web/` with the Node PATH
shown above:

```bash
./node_modules/.bin/vitest run
./node_modules/.bin/tsc --noEmit
env -u NO_COLOR OBSERVED_BROWSER_CHANNEL=chromium \
  ./node_modules/.bin/playwright test -c playwright.observed.config.ts
```

Results: **839 unit tests / 52 files passed** (1.67s), TypeScript exit 0 with no
diagnostics, and **16 real-entrypoint Chromium scenarios passed** (12.9s).
Removing the inherited `NO_COLOR` conflict for that command made the browser
output pristine without changing application settings. Strict Python validation
also accepts `web/tests/fixtures/observed-motion-v1.json` (available revision 1,
one feature, two reciprocal pairs). The web implementation and these checks do
not establish server integration or replace its pending independent review.

The independent web review found three Important gaps: the entrypoint bypassed
its own failure/missing-motion authority predicate; an unused direct adapter
discarded capability; and nested projections lacked owning-feature expiry/time
validation. The fix uses the state predicate for stored/active styling, removes
the uncalled adapter while preserving supported refresh paths, and validates
projection ownership, advertised time, strict cutoff and feature end in both
parser and renderer. Controller verification: **27 focused tests**, TypeScript
exit 0, and **18 Chromium scenarios in 13.5s**, all pristine. Independent scoped
re-review marked all three findings addressed with no new breakage. The worker's
full unit run was 844 tests / 52 files; final integrated checks will run again.

The payload/evidence task's first implementation passed **11 focused tests in
9.78s**, freshly checked by the controller, and is committed at `d854666c` for
independent review. That review found eight Important issues: stale-motion and
source-bounded tick eligibility, full-domain projection support, RATE-time
alignment, radar/cloud comparison geometry/acquisition windows/proximity,
uneven-family capacity fill, nonblocking busy admission and explicit failure
reasons, aggregate limits/counts, and lightning completeness. Each is assigned
to a test-first fix round and scoped independent re-review. The initial passing
tests are not approval of those behaviors.

Payload fix round 1 at `5f0cc743` passes **21 focused tests in 28.36s**, freshly
run by the controller with no warnings. Independent scoped re-review confirms
five original findings addressed. The remaining three require the projection's
one-cell support/domain rim, retention of the expected `invalid_route` refusal
code, and unknown/incomplete counts when route/interval work was refused before
enumeration. Fix round 2 at `3ad0088f` addresses them; fresh controller result:
**23 focused tests passed in 29.04s**, no warnings. Independent scoped re-review
confirms all three addressed without new breakage. All eight original payload
findings are now closed, separately from final integrated verification.

Server integration at `b8d031a2` has a fresh controller result of **273 passed,
21 warnings in 20.70s**. This covers real snapshot/bundle/header/direct/gated/SSE
paths, disabled-builder replacement, outside-D-0 timing, reused writer
preservation, retention fencing and delayed-old/newer-disabled publication.
The ordinary MetPy fixture interpolation warnings (20) and Starlette alias
deprecation (1) remain disclosed. This is not accepted-result end-to-end evidence;
that follows the payload fixes. Command from the worktree:

```bash
env -u AIRPORTS_DB -u ECMWF_GRIB_DIR -u ECMWF_PROD_GRIB_DIR \
  -u ECMWF_TEST_GRIB_DIR -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
  PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 \
  WB_OBSERVED_ENABLED=0 WB_OBSERVED_MOTION_ENABLED=0 \
  WB_OBSERVED_LIVE_TESTS=0 WEATHERBRIEF_EVAL_LIVE=0 \
  DATA_DIR=/tmp/flyfun-motion-server-review.ZvyK2a/data \
  DATABASE_URL=sqlite:////tmp/flyfun-motion-server-review.ZvyK2a/test.sqlite \
  OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/observed/test_briefing_integration.py tests/test_api_observed_motion.py \
  tests/test_api.py tests/test_retention.py tests/test_flights_storage.py \
  tests/test_route_weather.py tests/test_models.py tests/test_sounding_sidecar.py \
  tests/test_briefing_ready.py tests/test_pipeline.py --tb=short
```

Server review found that gated JSON/SSE refresh could swallow a publication
lifecycle error as a successful no-op, and a superseded realtime call returned
current motion alongside its stale local ordinary fields. Fix `95941574` makes
lifecycle errors explicit (JSON 409/SSE error) with capability/no-store headers
and returns the helper's current intended refreshed fields. A strengthened
thread-barrier test distinguishes old/new observation values. Fresh controller
run of the scope above using `/tmp/flyfun-motion-server-fix1.PB5lVW`: **274 passed,
21 known warnings in 20.89s**. Independent scoped re-review confirms both findings
addressed with no new breakage. Real accepted-result integration follows Task4.

An interim isolated broad Python run, before changing existing server writers,
passed **6,082 tests**, with **20 skipped, 23 deselected and 853 warnings** in
221.94s. It explicitly excluded the in-progress Task4 association/payload and
Task5 API tests. Both master feature flags, both live-test flags and dotenv were
disabled; provider keys and shared-data overrides were unset; data/SQLite lived
under `/tmp/flyfun-motion-regression.9dMC7T`. JUnit confirmed zero errors/failures.
It is not the required final integrated suite. A preceding run was deliberately
interrupted to make the already-default-off master flags explicit; its partial
1,601-pass count is not used as full-suite evidence. Warnings include existing
dependency/fixture warnings and the already-recorded publication fork warning.

Native initial pre-commit parity check: Python's strict producer model rejected the new
DEBUG fixture because its latest receipt is after its cutoff. Static inspection
also finds a two-frame accepted chain and out-of-domain patch indices. These are
fixture/validation issues to correct, not permission to weaken the scientific
contract or call an unexecuted Swift UI test passed. Those fixture issues are now
corrected at `fd625b9c`: fresh Python validation accepts the DEBUG raw literal
and the native available fixture's extracted literal fields, each with three
frames, two matching pairs and three advertised times. Native validator checks
were strengthened, and fictional positive lightning was replaced by explicit
unavailable evidence. The worker also checked the strict disabled-envelope
shape; the controller statically inspected that helper. No Swift compilation,
unit/UI, simulator or device command was run.

Native review then found required contour/bearing validation gaps, a direct
snapshot-writer deleted-pack bypass, and loss of an advertised UTC token's exact
spelling through `Date` conversion. Fix `d793698c` adds the render prerequisites
and deletion guards and retains the original server time token. Authored native
regressions cover invalid/missing operator, bearing 400, both direct writer paths,
and an exact five-minute tick spelled with `.000Z`. Fresh Python producer checks
accept the positive literals/alternate UTC spelling and reject the invalid
variants. Independent scoped static re-review marks all three findings addressed
with no new breakage. These checks do not execute or certify the Swift code.

Worker usage limits interrupted the native final static pass and the initial
Task4/5 dispatches. No Task4/5 production files existed at that interruption;
native changes and task requirements were retained. Available fallback workers
are being checked, with no weakening of test, review or scientific requirements.
Nothing was pushed or changed on GitHub at this checkpoint.

Read-only PR checks confirm the published draft remains at `95e233b6` and its
actual upstream base is `b48d9a8ff5831ab44fc3e43253c808578c215277`. Final whole-branch
review uses that base, not the unrelated stale local `main`; `79b88b37` separately
identifies the prediction-increment start. No remote state was changed.

Independent publication review found that Python dictionary equality treats JSON
`true` and `1` as equal, allowing conflicting unknown content at the same revision
through both publication paths. The fix uses recursive JSON-aware equality and
has passed independent scoped re-review, including both write paths. A separate
minor reader-test scheduling issue is retained for final review. End-to-end server
integration remains a separate gate. Strict DTO fixes are now independently
approved; the second round added exact search-boundary refusal rather than
accepting a capped-speed result.

The source review found a five-minute nominal-versus-acquisition reference error
and dropped genuine netCDF variable-length ISO lightning timestamps. Both were
reproduced with real file formats, corrected and independently re-reviewed.
OPERA now persists a separate nominal motion target without changing ordinary
observation timing. Old sidecars without that target are motion-ineligible.
Precise lightning timestamps remain distinct from the legacy fallback display time.

Current combined primitive verification:

```bash
env PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 WB_OBSERVED_LIVE_TESTS=0 \
  venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/observed/test_motion_contract.py tests/observed/test_motion_history.py \
  tests/observed/test_motion_geometry.py tests/observed/test_motion_readers.py \
  tests/observed/test_motion_route.py tests/test_observed_motion_publication.py \
  tests/test_route_points.py tests/test_model_region.py
```

Result: **290 passed in 3.12s, five warnings**. Warnings comprise the known netCDF
binary-size warning, the legacy LI `datetime64` timezone warning newly exercised by
the vlen fixture, and three warnings about forking a multithreaded test process.
The latter need final test-harness triage; no production deadlock was observed.

A previous mixed explicit-file invocation had two missing-fixture setup errors.
Instrumented pytest 9.1.1 collection showed two different collector objects named
`tests/observed`: fixtures were registered on one but tests belonged to the other.
Its node-identity fixture matching therefore returned no definitions. Keeping all
observed test arguments contiguous executes the same tests successfully (101-pass
run above). No application or global-fixture changes were needed. Full-suite
collection will name `tests` once; this is not evidence that the unimplemented
tracking, payload or UI layers pass.

Current observation/API compatibility check (Task3 was still being authored and
explicitly excluded):

```bash
env PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 WB_OBSERVED_LIVE_TESTS=0 \
  venv/bin/python -m pytest -q -p no:cacheprovider tests/observed \
  tests/test_api_observed.py tests/test_route_weather.py \
  --ignore=tests/observed/test_motion_tracking.py --tb=short
```

Result: **400 passed, 6 deselected, 164 warnings in 10.13s**. This is compatibility
evidence for the reviewed source/contract changes and existing displays, not a
passed tracking/payload or full-feature test. The warnings are the recorded
netCDF/NumPy/Starlette deprecations plus the legacy LI timezone conversion.

## Remaining external evidence

- Mac/iOS compilation, unit/UI tests and device execution remain explicitly deferred.
- No live-provider granules, credentials, shared weather data or shared DB are used.
- CTTH ground-registration/parallax validation remains a separate production gate.
- Regional forecast-skill replay and useful-horizon evidence have not been obtained.
- Synthetic software tests do not authorize operational prediction or safe-route claims.

## Implementation decisions

The complete decision/rationale/consequence record is preserved in
[implementation decisions](2026-09-05-observed-motion-decisions.md).

- Independent, disjoint file ownership permits parallel work; commits and review
  gates remain serialized. A conflict would require reconciliation and rerunning
  covering tests, not relaxing a requirement.
- Candidate tracking now takes the projected continuous route explicitly. The
  original proposed function signature omitted information needed by the approved
  route-proximity ranking. This changes an internal interface, not user scope or
  scientific thresholds; callers/tests must use the same geometry.
- Publication integration must use the snapshot route/timing captured for the
  revision, with the single shared route-identity function. Storage separately
  detects raw identity changes; it cannot certify a first envelope against an
  independently changed flight record merely because both carry opaque IDs.
- Pure route/timing work has its own task/review gate before payload integration.
  This permits parallel numerical tests using the agreed grid/track fields; the
  required continuous solver and scientific limits are unchanged. Any interface
  disagreement still requires reconciliation and covering tests.
- The deletion audit found retention and account-delete callers as well as flight
  deletion. Their individual-pack operations join the same publication lock;
  generic ancestor-directory cleanup must remain distinct. Ordinary reusable pack
  identities retain ordering state; a removed account namespace with lost state
  must not be reused. This is planned integration work, not a real-data deletion.
- Tracking returns explicit count/completeness metadata alongside its tracks.
  A bare list loses the distinction between known omissions, unevaluated work and
  evaluated zero. Source loading similarly carries inventory/selection counts.
  These are internal interface changes; consumers and count tests must agree.
- The plan-named briefing CSS source did not exist. The web task creates it and
  imports it through the existing entrypoint/CSS asset path; the no-server browser
  harness must verify the rendered styling. No generated distribution files or
  shared global stylesheet changes are needed.
- An asymmetric identical-image tracking test exposed spurious subcell movement
  from quadratic refinement despite an integer NCC of one. The design now requires
  retaining the integer match at that mathematical upper bound (absolute roundoff
  guard `1e-12`), while separately testing non-perfect fractional movement. This
  clarification is subject to the tracking review; it is not predictive-skill
  evidence. If the guard suppresses a genuinely fractional but numerically
  indistinguishable match, the refinement must be revisited.
- The competing-peak exclusion neighbourhood is now explicitly a Euclidean
  two-cell radius; offset `(2, 2)` is a competitor. The design previously omitted
  the metric. This matches the displacement/separation geometry but may refuse
  more tracks than a square neighbourhood. A low-contrast fractional fixture is
  retained as a refusal case, with separate stronger-texture refinement coverage;
  regional replay must judge usefulness rather than weakening acceptance tests.
- Future ticks follow the normative any-eligible-feature rule, with the maximum
  accepted feature expiry as the envelope bound. The review's suggested earliest
  family expiry would unnecessarily shorten a newer source; each other feature
  still has an explicit unavailable entry outside its own lead. If that ruling
  is wrong, heterogeneous-source projection selection needs rework.
- Regional lightning completeness counts reported detections, not a sum of
  potentially overlapping feature-association summaries. Unknown evaluation
  stays null rather than a fabricated zero. If that interpretation is wrong,
  completeness/card counts and regression coverage need correction; neither
  interpretation permits a no-thunderstorm or complete-coverage claim.
