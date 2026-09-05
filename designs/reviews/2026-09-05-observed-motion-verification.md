# Observed-motion implementation and verification record

Status: **implementation in progress, not a completed prediction feature**.
User approved the written specification and explicitly requested implementation.
The [implementation plan](../../docs/superpowers/plans/2026-09-05-observed-motion.md)
and [normative contract](../../docs/superpowers/specs/2026-09-05-observed-motion-contract.md)
govern this increment. Published PR #600 still contains observation corrections
until a later verified update; no prediction deployment is authorized.

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
| Independent feature tracking | Implementation in progress on reviewed inputs/contract. |
| Route closure and continuous planned overlap | Pure solver implemented and independently approved; payload integration pending. |
| Time-compatible evidence and bounded payload | Pending. |
| Atomic publication primitive | Implemented, equality bug fixed and independently re-reviewed; server integration pending. |
| Full/realtime/legacy/SSE/snapshot/bundle integration | Pending. |
| Web explorer and capability/expiry lifecycle | Implementation in progress. |
| Native explorer and raw-cache/capability lifecycle | Implementation in progress; no Mac execution. |
| Integrated tests and independent final review | Pending. |

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

These commits remain local. No server writer, web or native prediction integration
is claimed from these stage tests.

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
