# Observed corrections: verification and independent review

This is a local, unsubmitted follow-up to PR #584. Worktree:
`/home/qian/flyfun_weather/observed-corrections`; branch:
`codex/observed-corrections`; base:
`b48d9a8ff5831ab44fc3e43253c808578c215277`.

The original checkout and unrelated worktrees were left untouched. No deployment,
GitHub comments, push or PR submission was performed.

The initial corrections were committed locally as
`359373818d40f6b2031e70f73ae074f08aa642da`. The subsequent non-Mac continuation
adds full-entrypoint browser verification and fixes three more web defects.
Mac/iOS execution is **deferred at the user's request**, not reported as passed.

## Approach and baseline

Used Superpowers brainstorming to keep future motion/association design separate
from the bounded correctness fixes, systematic debugging to reproduce failures,
test-first implementation, parallel independent reviews and verification before
handoff. The task's explicit no-submission constraint overrides publishing steps.

Baseline on the current upstream worktree: 142 targeted Python tests passed,
6 deselected; 68 targeted web tests passed. All runtime verification used this
worktree's own editable-install venv and its own frontend dependencies.

## Test-first evidence

- Backend initial regressions: 23 failed, 4 passed before implementation (quality
  classification, false negative/clear statements, timestamps and method meanings).
- Web initial regressions: 12 expected failures before implementation (frozen and
  invalid/future ages, partial positive evidence, source absence, labels and QM9).
- Imagery initial regression set: 6 failed, 2 passed before implementation
  (warmest/highest confusion, missing winning temperature, centred/projected
  footprints and intersecting off-image centres).
- Added sample-share tooltip regression: failed on "of sampled area", then passed
  after replacing the claim with "of valid retrieval samples".
- Added IR scaling-display regression: failed on inferred percent, then passed
  with raw decoded value and explicit unverified-scale wording.
- Review-round window/batching regressions: 2 failed before implementation,
  then the complete imagery/API selection passed (45 tests).
- Panel DOM-preservation regression failed on a minute tick, then passed after
  updating only timestamp text nodes; saved prose and focused controls stay put.
- The extracted source selector reproduced the explicit-None bug (`opera_dbzh`
  instead of an empty selection). The corrected selector passed all six cases.
- Actual map-renderer lifecycle tests: 1 failed, 2 passed before the legend
  cleanup fix; all three passed afterward. These cover custom-label teardown,
  late image completion after destruction, and flash fade/expiry without image
  reconstruction or weather fetching.
- Full-page browser fallback regression: the map rendered radar while the
  selector's value was None (`''`, expected `opera_dbzh`). Sharing the renderer's
  source resolution with the menu and opacity control fixed the mismatch without
  overwriting the saved preference.
- Full-page refresh regressions: one click sent **2 POSTs**, expected 1, for both
  raster and lightning recovery. Replacing the panel-owned delegated handler on
  each render fixes accumulation; a second refresh still sends only one request.
- Narrow-layout regression: at 390px the opacity readout ended at **394.625px**,
  the legend intruded into the wrapped badge, and the badge intruded into the
  attribution row. A normal-flow legend/badge stack and wrapping controls pass
  with long, dated labels at 1280, 390 and 320px.
- Harness integration regression: inheriting the default runner's localhost base
  URL produced `ERR_HTTP_RESPONSE_CODE_FAILURE` from the fixture's denied request.
  Absolute navigation to the intercepted fixture origin passed the same no-server
  probe; the independent reviewer subsequently passed the full ten-test suite
  with that alternate base URL.
- Swift tests were written before their corresponding changes, but could not be
  run red/green because neither `swift` nor `xcodebuild` exists here. This is an
  explicit limitation, not successful iOS test execution.

## Independent review findings and disposition

Reviewers were separate agents, read-only, with bounded context and access to the
actual diff/callers. The first review attempt was interrupted by a service usage
limit; replacement reviewers completed substantive reviews. Their findings are
retained here rather than reporting only final positive assessments.

| ID | Review finding | Corrective action / status |
|---|---|---|
| P1 | Cloud-free state accepted contradictory processing quality | Additional decoder guard and regressions; closed in final Python review |
| P2 | Radar negative prose still implied all-route coverage | Scope absence to covered radar samples with coverage percentage; closed in final Python review |
| P3 | Positive rain rate lacked its own partial-coverage caveat | Add rate-specific qualification; closed in final Python review |
| P4 | Latest raster inherited static/snapshot window | Actual frame window header, sidecar-based status and response-based client label; API/client regressions passed and re-reviewed |
| P5 | Full-strip Nx4 corner temporaries could consume excessive memory | Project in batches of 8192 detections, retain global highest-top order; regression passed |
| P6 | Digest prompt still appended frozen `age_minutes` | Caller now uses immutable UTC frame times; closed in final Python review |
| P7 | Sub-threshold echo comment implied safe routing | Neutralized threshold wording and removed weak-echo/non-convective inference; re-reviewed |
| W1 | Failed lightning request rerendered and retried indefinitely | Failure latched before completion callback; bounded explicit retry; tested and re-reviewed |
| W2 | ABA raster source switching accepted obsolete results and leaked URLs | Generation guards and single-owner URL disposal; tested and re-reviewed |
| W3 | Raster failure stayed permanently latched | Snapshot refresh permits one new attempt; tested and re-reviewed |
| W4 | Minute clock did not expire/fade map flashes | Separate clock-only marker redraw; actual renderer regression passed |
| W5 | Web lacked per-source stale qualification | Informational stale label at 30 minutes, independent per source; tested and re-reviewed |
| W6 | Panel minute update replaced focused DOM controls | Update timestamp nodes only; node identity regression and typecheck passed |
| W7 | Temperature raster badge did not name its quantity | Explicit cloud-top temperature label; re-reviewed |
| W8 | Observed legend survived map destruction | Remove renderer-owned legend alongside badge; actual renderer regression and re-review passed |
| W9 | Flash completion callback could run after hiding map | Clear/invalidate request state at map teardown; generation regression passed, callsite statically re-reviewed |
| W10 | Explicit None selection silently enabled radar | Shared option-based selector preserves None and unavailable-source fallback; six cases passed and callsite re-reviewed |
| W11 | Unavailable saved source showed None in the menu while the map displayed radar | Use the same resolved source for menu, opacity control and renderer without changing the preference; full-page regression and independent review passed |
| W12 | Re-rendering route observations accumulated refresh/popup click handlers | Replace the panel-owned delegated handler; real repeated-click tests verify one POST per click; independently re-reviewed |
| W13 | Narrow layouts overlapped legend, source badge and basemap attribution, and clipped the opacity readout | Renderer-owned normal-flow label stack, wrapping controls and explicit teardown; long-label layout regressions at 1280/390/320px and independent review passed |
| T1 | New focused browser tests inherited an incompatible origin in the default runner | Absolute intercepted-origin navigation; no-server alternate-base RED/GREEN and independent full-suite execution passed |
| I1 | iOS still said histogram percentage was sky area | Readout/model/help/config footer now say valid retrieval samples; stale test comments/names corrected; static re-review closed |
| I2 | IR effective cloudiness still multiplied by 100 | Both clients display decoded numbers, explicitly scale unverified |
| I3 | Lightning still implied full-disc absence | Qualified zero as no flashes reported; corrected docs/help/comments |
| I4 | All non-DBZH intervals called accumulation; no start/end | Source-specific window names and actual UTC start/end added |
| I5 | Siri realtime refresh bypassed cache persistence | `RefreshDriver` uses the same repository seam before completion; real-cache regression added (unexecuted here) |
| I6 | Swift test still expected former 95% cloudiness string | Updated expected decoded value/scale wording |
| I7 | Below-run hatch still suggested shared cloud depth | Removed iOS/web below-run depth geometry and legend swatch; retain coverage/off-scale marks; statically re-reviewed |

Final Python/iOS reviewer: no remaining Critical or Important issues in the
reviewed correction scope; Python findings closed, iOS assessment static-only.
Final web reviewer: no remaining Critical, Important or Minor findings in the
browser continuation after the responsive/origin re-review; independently ran all
ten browser tests with a different inherited base URL. The earlier correction
review also closed its Critical/Important findings. Ready for **local user
review**, not merge approval.
Both reviewers explicitly retain the validation gates below.

## Verification evidence

- Initial correction commit, full isolated Python suite: **5,782 passed,
  20 skipped, 23 deselected,
  849 warnings**, 188.36 seconds. No shared application data or database used.
- Initial correction commit, final targeted Python observed/API suite:
  **190 passed, 6 deselected,
  163 warnings**, 9.27 seconds.
- Backend code did not change during the browser continuation; those Python
  suites were not rerun for the web-only additions.
- Fresh full web unit suite after the responsive fix: **817 passed in 50 files**.
  The reviewer had also independently run the initial correction's 817-test suite.
- Fresh focused browser suite: **10 passed in 8.1 seconds**, real Chrome
  152.0.7977.64. Independent alternate-base run: **10 passed in 5.7 seconds**.
- Fresh application TypeScript `npx tsc --noEmit`: passed. This previously
  caught an optional dataset-string error that the panel unit test did not;
  the formatter now handles absent values as unknown.
- Both bundled JSON help catalogs parse and match exactly.
- `git diff --check`: passed.

Commands, from the fix worktree unless noted:

```bash
# Full Python suite: temporary, isolated data and SQLite database.
observed_test_tmp=$(mktemp -d /tmp/observed-full-tests.XXXXXX)
PYTHONDONTWRITEBYTECODE=1 DATA_DIR="$observed_test_tmp" \
  DATABASE_URL="sqlite:///$observed_test_tmp/test.db" \
  venv/bin/python -m pytest -q -p no:cacheprovider --tb=short

# Final targeted Python regressions.
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/observed tests/test_api_observed.py --tb=short

# From web/; this workspace requires the installed Node directory on PATH.
env PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin npm test
env PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin npx tsc --noEmit

# Dedicated browser configuration: no server and no provider/network requests.
env PATH=/home/qian/.nvm/versions/node/v22.23.1/bin:/usr/local/bin:/usr/bin:/bin \
  OBSERVED_BROWSER_CHANNEL=chrome \
  npx playwright test --config playwright.observed.config.ts

# From the worktree root.
git diff --check
```

No frontend production build or dev server was run, per repository instructions.
The browser harness bundles the actual `briefing-main.ts` entrypoint with esbuild
**in memory** (`write: false`), leaving `web/dist` untouched. It uses the actual
HTML/CSS, store and Leaflet. All HTTP, including basemap tiles, is intercepted;
the PNG is a valid synthetic 16×16 image whose decoding is checked in Chrome.
The test clock is controlled; URL instrumentation delegates to the real browser
creation/revocation methods. Page errors and unexpectedly omitted requests fail
the tests; intentionally absent auxiliary APIs have an explicit allowlist.

The ten browser cases cover:

1. Full-page focused-node preservation and advancing snapshot/image ages without
   refetching or reconstructing imagery; actual response time/producer displayed.
2. Unavailable-source fallback agreement and explicit None cleanup.
3. One failed raster request, no retry on unrelated controls/clock, one authorized
   retry after the real observations-refresh click; repeated clicks stay singular.
4. Equivalent lightning failure/recovery through the same actual refresh UI.
5. Real Leaflet flash opacity at 58.5 and 59.5 minutes, expiry at 60.5, no fetching.
6. Delayed flashes cannot overwrite a replacement map after hide/reopen.
7. A→B→A imagery race keeps newest provenance, rejects obsolete results and releases
   its real object URL at teardown.
8. Desktop (1280px) long dated label/control layout and label-container teardown.
9. Phone (390px) equivalent layout, including readable opacity slider/readout.
10. Narrow phone (320px) equivalent layout and non-overlapping basemap attribution.

The refresh fixture deliberately returns the same source timestamps and bounds,
so failure recovery cannot pass merely by switching to a different request key.
These cases supersede the previous static-only assessment of full-entrypoint
observed wiring **for these paths**, not for every auxiliary briefing feature.
Root inspected the generated layout screenshots; imagery/tiles are solid synthetic
fixtures, so these are label/control checks, not weather-map visual validation.
For screenshot attachments, use Playwright's HTML reporter (`--reporter=line,html`,
`PLAYWRIGHT_HTML_OPEN=never`); the default console reporter does not persist the
in-memory successful-test attachments as PNG files.

An optional standalone strict typecheck of the Playwright harness was attempted
and **did not pass**: the repository does not provide `@types/node`, so `process`,
`Buffer`, `__dirname`, `node:fs` and `node:path` lack types. The normal application
tsconfig covers `ts/**/*`, not browser tests. Browser execution is verified, but
is not claimed as harness typechecking; no dependency was added for this probe.
The attempted command, from `web/`, was:

```bash
npx tsc --noEmit --strict --target es2022 --module commonjs \
  --moduleResolution node --esModuleInterop --skipLibCheck \
  tests/observed-browser.spec.ts playwright.observed.config.ts
```

Warnings: netCDF/numpy binary-size warning and Starlette/AnyIO deprecation existed
in the baseline. New netCDF test-fixture scalar writes trigger NumPy 2.5 shape
deprecations. The broader suite also reports pre-existing JWT test-key-length,
cookie API, interpolation, and Matplotlib deprecation warnings. These are not test
failures, but a passing suite is not presented as a warning-free environment.
Browser runs also emit the Node `NO_COLOR`/`FORCE_COLOR` environment warning.

A synthetic one-million-detection geostationary render at 800 output pixels
completed in **0.76 s**, **146 MiB process peak RSS** after batching on this host.
This includes Python/import/frame memory; it is not an operational server memory
guarantee. The independent review's original unbatched one-million reproduction
reported approximately 309 MB RSS. Inputs/measurement context may differ, so this
is not a controlled percentage-speedup or memory-reduction claim. The regression
also directly bounds each projection input to 32768 corner points and checks
cross-batch highest-top precedence.

## Remaining validation gates

1. **Mac/iOS deferred by user request:** Xcode compilation, unit tests and UI
   tests remain unexecuted. Static review cannot establish Swift compiler,
   SwiftUI layout, simulator or device correctness. Deferral does not block this
   local review package, but does not establish iOS merge/release readiness.
2. Actual-device/cross-browser visual checks and realistic geographic imagery.
   Chrome fixture tests now cover source switching, failures, flash expiry and
   responsive labels. They do not validate Safari/WebKit, real-provider imagery,
   all cross-section partial-coverage/off-scale marks, or backend auth/network.
3. Real-granule scientific validation of FCI status/packing, parallax magnitude
   and LI coverage. No live-provider credentials were used for this task.
4. User approval before any push or GitHub PR creation.

Deferred Xcode unit command for later (select an installed simulator):

```bash
xcodebuild test \
  -project app/flyfun-weather/flyfun-weather.xcodeproj \
  -scheme flyfun-weather \
  -destination 'platform=iOS Simulator,name=iPhone 17,OS=latest' \
  -only-testing:flyfun-weatherTests \
  -resultBundlePath observed-unit.xcresult \
  -skipMacroValidation
```

Check the xcresult for a nonzero executed test count. Run relevant UI journeys
with `-only-testing:flyfun-weatherUITests`; existing CI runs unit tests only.
