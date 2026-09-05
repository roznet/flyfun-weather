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
| Strict shared producer contract and policy | In progress; no completion/review verdict yet. |
| Cutoff-safe history, bounded source geometry, lightning precision | In progress; no completion/review verdict yet. |
| Independent feature tracking | Pending. |
| Route closure, continuous planned overlap, evidence and bounded payload | Pending. |
| Atomic publication primitive | In progress; no completion/review verdict yet. |
| Full/realtime/legacy/SSE/snapshot/bundle integration | Pending. |
| Web explorer and capability/expiry lifecycle | Pending. |
| Native explorer and raw-cache/capability lifecycle | Pending. |
| Integrated tests and independent final review | Pending. |

Task-level red/green reports and scoped review packages live in the plan-scoped
work ledger during execution. Final commands/counts and actionable review findings
will be recorded here as each gate is actually verified; a worker's partial test
run is not the completion of its task or the feature.

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
