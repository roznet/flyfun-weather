Review target: $ARGUMENTS

If $ARGUMENTS is a GitHub PR URL or repo/pull/number:

- Use gh to fetch the PR diff and description
- Focus review on changed lines only
- Post the review as a PR comment using gh pr comment

Otherwise:

- Interpret $ARGUMENTS as a description of what to review
- Use git log, git diff, or file reads as appropriate to find the relevant changes

---

## Context loading

Before starting the review, read the following:

- CLAUDE.md at the repo root for coding standards
- Any design documents in /designs/ folder relevant to files being changed - use these for module intent and architecture

---

## Single-pass completeness requirement

**Do one complete pass. Find every issue. Report them all.**

Do not hold issues back for a follow-up round. If there are 10 real issues, report all 10 now. The goal is one complete review that can be acted on in one go, not an iterative dialogue.

Group output by severity:

- **Critical** - bugs, logic errors, data corruption, security issues: must fix before merge
- **Important** - architecture violations, design deviations, correctness problems: should fix
- **Minor** - best practices, missed optimisations, low-effort improvements: fix if low effort

Within each group, order by file/module for readability.

---

## Review criteria

Apply all of the following in the single pass:

**General**
- Bugs and logic errors
- CLAUDE.md violations
- Deviations from documented architecture/design intent in /designs/
- Code and logic duplication, opportunities for consolidation
- Simplicity, maintainability and extensibility

**Swift/iOS**
- Memory management (retain cycles, weak/unowned correctness)
- Concurrency (actor isolation, data races, MainActor usage)
- iOS best practices
- For PRs touching `app/flyfun-weather/**`, apply the deeper **iOS app code** section below.

**TypeScript**
- Type safety, avoid `any`
- async/await correctness

**Python**
- Type hints
- Error handling
- Async patterns if applicable

---

## Weather analysis code

If the PR touches weather analysis code (formulas, thresholds, sounding pipeline, layer building, advisory evaluators, or data model changes in the analysis path), also check:

**Meteorological validity**
Do the formulas, thresholds, and assumptions hold from an aviation weather / thermodynamics standpoint? Are units consistent? Are threshold values reasonable for the NWP resolution used? Flag anything that contradicts standard meteorological practice or could produce misleading results for a pilot.

**Cross-metric impact**
Does the same pattern, formula, or data structure exist in a sibling module or parallel metric? (e.g., Ri-based CAT layers and E-Shear layers share grouping logic; icing has Ogimet, SFIP, and IENG variants.) If the change fixes or improves one, check whether the same fix applies to the others. List sibling modules reviewed and whether they need a matching change.

**Cross-model robustness**
The pipeline processes GFS, ECMWF, ICON, UKMO, and MeteoFrance, which differ in available fields, pressure level spacing, and variable naming. For any change, check:

- Is the logic at the right abstraction level for all models, or is it model-specific when it shouldn't be (or vice versa)?
- If it relies on an input field (omega, cloud water, geopotential), does it fail gracefully when that field is absent for some models?
- Could differences in vertical resolution (e.g., GFS 25hPa vs UKMO 50hPa spacing) cause the same logic to behave very differently across models?
- Are thresholds calibrated for one model's characteristics but applied to all?

---

## Web frontend code

If the PR touches web frontend code (HTML, TypeScript, CSS), also check:

**CSP compliance**
Read deploy/weather.flyfun.aero.caddy for the active Content-Security-Policy. Check whether any new or changed code would be blocked:

- New external script/stylesheet loads (CDNs) - blocked by script-src/style-src
- New fetch()/XHR to external domains - blocked by connect-src
- New images from external domains (not tile servers) - blocked by img-src
- New inline scripts in HTML without 'unsafe-inline' in script-src
- New `<form action="...">` to unlisted domains - blocked by form-action
- New iframes - blocked by frame-ancestors/default-src

If a violation is found, flag it and suggest either updating the CSP or refactoring to stay within the current policy.

---

## iOS app code

If the PR touches iOS code (`app/flyfun-weather/**`), also check:

**DTO decode safety (highest priority)**
The iOS app is an independent client of the same backend as the web app. `Models/API/*Response.swift` Codable types mirror the JSON each endpoint returns — but nothing on the server side fails to compile when that JSON shape changes, so a mismatch surfaces only as a **runtime decode failure on device**. For any changed response model, decoded field, or `CodingKeys`:

- Does the Swift type match the JSON the endpoint actually returns? Cross-reference the Python response model in `src/` and the TS type in `web/ts/types/` for the same endpoint.
- Are fields that the server may omit (new, optional, model-dependent) declared **optional** in Swift, with sensible defaults — so an older client degrades gracefully instead of throwing on the whole payload?
- No force-unwraps (`!`) or `try!` on network/JSON-derived data.

**Concurrency**
- `@MainActor` isolation on ViewModels and any UI-mutating state; no off-main mutation of `@Published`/observable state.
- `Sendable` correctness across `async` boundaries; no shared-mutable-state data races.
- Correct handling of the SSE refresh stream (`APIClient.streamSSE` / `URLSession.bytes` → `AsyncThrowingStream<RefreshEvent, Error>`): cancellation, error propagation, and not leaking the task.

**Memory**
- Retain cycles in escaping/`async` closures and `Task {}` / Combine captures; `weak self` where the closure outlives the owner.

**SwiftUI Canvas correctness**
The cross-section renders in an immediate-mode `SwiftUI.Canvas`, which has **no intrinsic size**. Watch for layout assumptions that depend on intrinsic geometry — e.g. `.aspectRatio(nil, .fit)` silently collapses a Canvas to a sliver (this was the iPhone-landscape bug in commit `9f03fd54`). Verify portrait/landscape and iPhone/iPad sizing paths.

**Auth/storage**
- Bearer tokens go through the FlyFunCommon Keychain helpers (`KeychainBearerTokenStore`, `RollingBearerSession`), not ad-hoc `UserDefaults`/file storage.

---

## Cross-platform parity (web ↔ iOS)

Web and iOS share several **hand-copied** surfaces. If the diff touches any of them on one platform, flag that the counterpart on the other platform likely needs a matching change, and recommend running `/sync-ios-web` to enumerate the divergences:

- **Cross-section preset tables** — `CrossSectionPresets.swift` ↔ `web/ts/visualization/cross-section/{layer-registry,advisory-presets,layers/cloud-bands-factory}.ts` (these carry reciprocal `SYNC` comments).
- **`metrics-catalog.json`** — byte-identical copy in `web/ts/data/` and `app/.../Resources/`.
- **API DTOs** — `Models/API/*Response.swift` ↔ backend response models (`src/`) ↔ `web/ts/types/`.

This is a flag-and-defer check: note the parity risk, don't try to fix the other platform inside this review.

---

## Do NOT flag

- Style issues not covered by CLAUDE.md
- Nits or minor suggestions below the Minor threshold
- Pre-existing issues not touched by this PR

If no issues found, post a brief approval comment.
