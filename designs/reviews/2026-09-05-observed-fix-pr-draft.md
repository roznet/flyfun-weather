# Final local PR package — awaiting user review

Final title: **Fix observed-weather validity, freshness, cache durability and map rendering**

The submission-ready body is
[the final PR description](2026-09-05-observed-fix-pr-final.md), aligned with the
repository's PR template. This file remains the local preparation/checklist record.

Branch: `codex/observed-corrections`

Target: `roznet/flyfun-weather:main`

Base: `b48d9a8ff5831ab44fc3e43253c808578c215277`

Implementation frozen at: `0d4ae47fb63ba596b7e97018278e88404d56002b`

The GitHub `main` head was checked read-only on 2026-09-05 and still matches the
base above. Later commits in this package finalize documentation only. Recheck
upstream immediately before an eventual submission; do not silently rebase or
change the implementation the user reviewed.

Working copy: `/home/qian/flyfun_weather/observed-corrections`

Nothing has been pushed, posted to GitHub, deployed or merged. This file is the
local handoff record, not an existing GitHub pull request. Publication remains
on hold until the user explicitly approves it.

## What & why

Correct the observed radar/lightning/satellite pipeline and its web/iOS displays
following the review of PR #584. Keep existing payload keys and product boundaries.

- Decode FCI cloud-free/failed/unprocessed states using status/quality flags;
  correct method meanings and remove the unsupported QM9 multilayer inference.
- Retain positive radar/rain/top detections under incomplete coverage, while
  qualifying gaps and limiting negative claims to what was actually sampled.
- Use immutable observation times/windows in saved summaries and advancing,
  source-specific display clocks. Do not poll weather as time labels update.
- Persist same-timestamp realtime refresh fields in downloaded iOS snapshots,
  including Siri refreshes, with atomic, field-only updates and failure reporting.
- Distinguish geometric ft MSL from pressure FL. Describe histogram values as
  valid-sample shares, not measured sky area or a vertical cloud stack. Show IR
  effective cloudiness as a decoded value with unverified scale, not visual opacity.
- Render projected satellite footprints centred at corrected positions. Resolve
  all overlaps by geometric top height and carry that top's temperature; batch
  projections to avoid full-strip four-corner temporary arrays.
- Give map imagery the actual response's time, acquisition window and attribution;
  protect request/URL lifecycles, failures and lightning aging. Preserve explicit
  None selection and remove labels/pending callbacks when the map is closed.
- Keep the observed-source menu and opacity control aligned with the rendered
  fallback; prevent duplicate refresh requests from accumulated click handlers.
- Stack map legend and source labels without overlap, keep basemap attribution
  clear, and wrap observed controls on phone-width layouts.
- Save the full correctness review, evidence limits and future visualization/
  motion-estimation proposals; align design docs and both bundled help catalogs.

## Issue linkage

Follow-up to merged PR #584 and observed-conditions work #574. No unrelated issue
is automatically closed. A new tracking issue has not been created.

## Testing / verification

See [verification and independent-review record](2026-09-05-observed-fix-verification.md)
for final commands, counts, reviewer dispositions and outstanding checks.

- Historical full Python, initial correction commit: **5,782 passed**, 20 skipped,
  23 deselected.
- Historical observed/API regressions, initial correction commit: **190 passed**,
  6 deselected. Backend code has not changed since those runs.
- Full web: **817 passed**; TypeScript typecheck passed.
- Full-entrypoint browser checks: **10 passed**, including real Leaflet/PNG
  decoding, refresh retries, races/cleanup and 1280/390/320px layouts. Independent
  alternate-base run also passed all ten tests without starting a server.
- Bundled help JSON matches; diff whitespace check passed.
- Independent Python/iOS and web re-reviews closed all reported Critical/Important
  code findings in this fix scope. The iOS assessment is static-only.

Regression coverage includes FCI quality contradictions; incomplete coverage;
source timestamps/windows; temperature overlap including inversions and missing
values; projection/clipping/batching; live clocks; map request failures/races;
and iOS cache reopen, Siri and save-failure paths (Swift tests written, unexecuted).

Mac/iOS testing is **deferred at the user's request**; this Linux workspace cannot
compile or run the app. The local review package can proceed, but does not claim
iOS merge/release readiness. Browser tests use synthetic HTTP fixtures, not a live
backend or provider granules. The application typecheck passes; the optional
standalone harness typecheck is blocked by missing repository Node typings, as
documented in the verification record.

## Compatibility, rollout and limitations

- No database migration, new dependency, advisory logic or weather polling.
- Existing JSON fields and legacy geometric `*_fl` keys remain compatible.
  `X-Observed-Window-Minutes` is additive response metadata for the actual image.
- No nowcasting, motion vector, storm association, animation or route-safety
  judgment is implemented in this corrective PR.
- No speculative changes to parallax scale/sign or IR-cloudiness normalization.
  Rectangular satellite footprints are display approximations, not an
  area-conservative cloud mask.
- Already-saved packs may contain pre-fix decoded states or summary prose. They
  need an explicit realtime refresh/recomputation after deployment to acquire the
  corrected interpretations. The client cannot recover missing quality evidence
  from old aggregate payloads. No existing local/user weather data was deleted.
- Review FCI status names/packing, large low-cloud parallax offsets and LI
  coverage against real current granules before asserting stronger guarantees.
- This is delayed remote situational-awareness data, not tactical storm avoidance
  or an overflight-clearance system.

## Before submission

- [ ] User reviews the final PR description and the branch diff.
- [x] Verify map source switching, failure recovery, lightning expiry and responsive
  labels in real Chrome using synthetic fixtures; independently re-review fixes.
- [ ] iOS unit/UI and on-device checks — deferred by user request, not verified.
- [ ] Actual-device/cross-browser and live-granule validation remain outstanding.
- [x] Confirm upstream main matches the reviewed base at finalization (2026-09-05).
- [ ] Recheck that base and the approved branch head immediately before publishing.
- [ ] Resolve GitHub write access: the current GitHub session reports
  `permissions.push: false` for `roznet/flyfun-weather`.
- [ ] Obtain explicit approval to push and create the GitHub PR.

## Submission preparation — commands not executed

After explicit user approval, from the fix worktree, first confirm that the
working tree and branch are still the reviewed versions:

```bash
git status --short --branch
git log -3 --oneline
gh api repos/roznet/flyfun-weather/commits/main --jq .sha
```

Stop if the branch has unexpected changes or upstream no longer matches the
recorded base. Report the difference before changing the reviewed code.

The following commands publish the branch and create a **draft** GitHub PR;
they are provided for later use, not authorization to run them now. Draft status
preserves the outstanding validation gates and does not enable auto-merge.

The configured `origin` points to a local mirror, not to the requested GitHub
repository. No remote configuration was changed. The command below therefore
names GitHub explicitly; do not substitute `git push origin`. GitHub's repository
metadata confirms the HTTPS URL below. Publishing requires GitHub write access;
the current GitHub session reports `permissions.push: false` (checked read-only
on 2026-09-05). **Submission is blocked until suitable access is provided.**
Do not execute the publication commands until both access and user approval are
resolved. Do not create a fork or choose a different destination without direction.
If authentication or authorization still fails, stop rather than pushing elsewhere.

```bash
git push https://github.com/roznet/flyfun-weather.git codex/observed-corrections
gh pr create \
  --repo roznet/flyfun-weather \
  --base main \
  --head codex/observed-corrections \
  --draft \
  --title 'Fix observed-weather validity, freshness, cache durability and map rendering' \
  --body-file designs/reviews/2026-09-05-observed-fix-pr-final.md
```

Full findings and future options:
[PR #584 observed review](2026-09-05-pr584-observed-review.md).
